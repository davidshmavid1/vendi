"""Layout versions (plans) and per-date offers (pricing).

    action                                         OWNER   ADMIN   STAFF
    view layout versions, a date's layout/offers   yes     yes     yes
    create/copy/edit versions, set a date's        yes     yes     no
    version and offers, publish, unpublish

Rules:
- A version is editable until an event date uses it; then it is locked
  (``locked_at``) for good. To change a plan in use, copy it into a new
  version and point the dates you want at the copy. Editing therefore never
  changes a date that uses another version, and never changes a plan a date
  already uses.
- A save sends every existing stall of the version by id. Stalls are never
  deleted; stall ids from other versions are rejected.
- A date's offers must cover exactly the stalls of its selected version, in
  one supported currency. Only stalls of that version are accepted.
- A published date's layout must be unpublished before its version or
  offers change. Publishing needs a scheduled date that hasn't ended, a
  market that isn't archived, and at least one enabled offer.

Every write re-checks ``expected_revision`` and validates the whole payload
before writing, in one transaction. Locks are taken in the order market,
occurrence (FOR NO KEY UPDATE, as in the applications app), occurrence
layout, layout version, so concurrent operations can't deadlock.
"""

from django.db import transaction
from django.db.models import Count, Max
from django.utils import timezone

from accounts.models import User
from core.exceptions import Conflict, InvalidRequest, NotFound
from layouts.geometry import TEMP_LABEL_PREFIX, check_offers, check_plan
from layouts.models import LayoutVersion, OccurrenceLayout, Stall, StallOffer
from markets.models import EventOccurrence, Market, MarketStatus, OccurrenceStatus
from organizations.models import Role
from organizations.permissions import membership_for, require_role

STALL_FIELDS = (
    "label",
    "description",
    "x",
    "y",
    "width",
    "height",
    "physical_width",
    "physical_depth",
    "physical_unit",
)


def _manager(actor: User, organization_id: int) -> None:
    require_role(membership_for(actor, organization_id), Role.OWNER, Role.ADMIN)


def _market(organization_id: int, market_id: int, *, lock=False) -> Market:
    queryset = Market.objects.filter(pk=market_id, organization_id=organization_id)
    market = (queryset.select_for_update() if lock else queryset).first()
    if market is None:
        raise NotFound("Market not found.")
    return market


def _require_not_archived(market: Market) -> None:
    if market.status == MarketStatus.ARCHIVED:
        raise Conflict("Archived markets can't be changed.", code="market_archived")


def _check_revision(current: int | None, expected: int | None, what: str) -> None:
    if expected != current:
        raise Conflict(
            f"Someone else changed this {what} since you loaded it. Reload to see their changes.",
            code="stale_revision",
            details=[{"current_revision": current}],
        )


def _clean_stalls(stalls: list[dict]) -> list[dict]:
    return [
        {**s, "label": s["label"].strip(), "description": s["description"].strip()} for s in stalls
    ]


# --- Layout versions ------------------------------------------------------------------


def list_versions(actor: User, organization_id: int, market_id: int):
    membership_for(actor, organization_id)
    market = _market(organization_id, market_id)
    return market.layout_versions.annotate(
        stall_count=Count("stalls", distinct=True),
        date_count=Count("occurrence_layouts", distinct=True),
    ).order_by("number")


def _version(market: Market, version_id: int, *, lock=False) -> LayoutVersion:
    queryset = LayoutVersion.objects.filter(pk=version_id, market=market)
    version = (queryset.select_for_update() if lock else queryset).first()
    if version is None:
        raise NotFound("Layout not found.", code="layout_version_not_found")
    return version


def get_version(actor: User, organization_id: int, market_id: int, version_id: int):
    membership_for(actor, organization_id)
    version = _version(_market(organization_id, market_id), version_id)
    return version, list(version.stalls.order_by("pk"))


def create_version(
    actor: User,
    organization_id: int,
    market_id: int,
    *,
    canvas_width: int,
    canvas_height: int,
    stalls: list[dict],
    copy_of: int | None = None,
):
    """A new, editable version: from the payload, or a copy of ``copy_of``
    (its canvas and stalls; the payload's canvas and stalls are ignored)."""
    with transaction.atomic():
        _manager(actor, organization_id)
        # The market lock serializes version numbering.
        market = _market(organization_id, market_id, lock=True)
        _require_not_archived(market)
        source = None
        if copy_of is not None:
            source = _version(market, copy_of)
            canvas_width, canvas_height = source.canvas_width, source.canvas_height
            stalls = [
                {f: getattr(s, f) for f in STALL_FIELDS} for s in source.stalls.order_by("pk")
            ]
        else:
            stalls = _clean_stalls(stalls)
            check_plan(canvas_width, canvas_height, stalls)
        number = (market.layout_versions.aggregate(n=Max("number"))["n"] or 0) + 1
        version = LayoutVersion.objects.create(
            market=market,
            number=number,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            based_on=source,
            created_by=actor,
            updated_by=actor,
        )
        Stall.objects.bulk_create(
            [
                Stall(
                    layout_version=version,
                    **{f: stall.get(f) for f in STALL_FIELDS},
                )
                for stall in _normalize(stalls)
            ]
        )
    return version, list(version.stalls.order_by("pk"))


def _normalize(stalls: list[dict]) -> list[dict]:
    return [{**s, "physical_unit": s.get("physical_unit") or ""} for s in stalls]


def save_version(
    actor: User,
    organization_id: int,
    market_id: int,
    version_id: int,
    *,
    expected_revision: int,
    canvas_width: int,
    canvas_height: int,
    stalls: list[dict],
):
    """Replace a draft version's canvas and stalls atomically."""
    stalls = _clean_stalls(stalls)
    with transaction.atomic():
        _manager(actor, organization_id)
        market = _market(organization_id, market_id)
        _require_not_archived(market)
        version = _version(market, version_id, lock=True)
        if version.is_locked:
            raise Conflict(
                "This layout is used by an event date, so it can't change. "
                "Make a copy and edit the copy instead.",
                code="layout_version_locked",
            )
        _check_revision(version.revision, expected_revision, "layout")
        existing = {s.pk: s for s in version.stalls.all()}
        _check_stall_ids(stalls, existing)
        check_plan(canvas_width, canvas_height, stalls)
        version.revision += 1
        version.canvas_width = canvas_width
        version.canvas_height = canvas_height
        version.updated_by = actor
        version.save()
        _write_stalls(version, _normalize(stalls), existing)
    return version, list(version.stalls.order_by("pk"))


def _check_stall_ids(stalls: list[dict], existing: dict[int, Stall]) -> None:
    problems = []
    seen = set()
    for index, stall in enumerate(stalls):
        stall_id = stall.get("id")
        if stall_id is None:
            continue
        if stall_id not in existing:
            problems.append(
                {"index": index, "stall_id": stall_id, "message": "Unknown stall for this layout."}
            )
        elif stall_id in seen:
            problems.append({"index": index, "stall_id": stall_id, "message": "Listed twice."})
        seen.add(stall_id)
    for stall_id in sorted(existing.keys() - seen):
        problems.append(
            {
                "stall_id": stall_id,
                "message": "Every existing stall must be included; stalls can't be removed.",
            }
        )
    if problems:
        raise InvalidRequest(
            "The stall list doesn't match this layout.", code="stall_mismatch", details=problems
        )


def _write_stalls(version: LayoutVersion, stalls: list[dict], existing: dict[int, Stall]) -> None:
    saved_labels = {pk: row.label for pk, row in existing.items()}
    updates, creates = [], []
    for stall in stalls:
        values = {f: stall[f] for f in STALL_FIELDS}
        if stall.get("id") is None:
            creates.append(Stall(layout_version=version, **values))
        else:
            row = existing[stall["id"]]
            for field, value in values.items():
                setattr(row, field, value)
            updates.append(row)
    # Labels are unique per version and a save may swap them between stalls
    # (or give a new stall an existing stall's old label): park every
    # renamed stall on a temporary label first. Temporary labels start with
    # an invisible character that real labels can't contain (geometry.py).
    renamed = [row for row in updates if row.label.casefold() != saved_labels[row.pk].casefold()]
    if renamed:
        Stall.objects.bulk_update(
            [Stall(pk=row.pk, label=f"{TEMP_LABEL_PREFIX}{row.pk}") for row in renamed], ["label"]
        )
    if updates:
        now = timezone.now()
        for row in updates:
            row.updated_at = now
        Stall.objects.bulk_update(updates, [*STALL_FIELDS, "updated_at"])
    if creates:
        Stall.objects.bulk_create(creates)


# --- A date's layout and offers ---------------------------------------------------------


def _occurrence(organization_id: int, market_id: int, occurrence_id: int, *, lock=False):
    queryset = EventOccurrence.objects.select_related("market").filter(
        pk=occurrence_id, market_id=market_id, market__organization_id=organization_id
    )
    if lock:
        queryset = queryset.select_for_update(of=("self",), no_key=True)
    occurrence = queryset.first()
    if occurrence is None:
        raise NotFound("Event date not found.")
    return occurrence


def _layout_not_found() -> NotFound:
    return NotFound("This date has no stall layout yet.", code="layout_not_found")


def current_offers(occurrence_layout: OccurrenceLayout) -> list[StallOffer]:
    """Offers for the stalls of the date's selected version."""
    return list(
        StallOffer.objects.filter(
            occurrence_id=occurrence_layout.occurrence_id,
            stall__layout_version_id=occurrence_layout.layout_version_id,
        ).order_by("stall_id")
    )


def _date_result(occurrence, occurrence_layout):
    version = occurrence_layout.layout_version
    return (
        occurrence,
        occurrence_layout,
        list(version.stalls.order_by("pk")),
        current_offers(occurrence_layout),
    )


def get_date_layout(actor: User, organization_id: int, market_id: int, occurrence_id: int):
    """(occurrence, occurrence layout, stalls, offers) for any member."""
    membership_for(actor, organization_id)
    occurrence = _occurrence(organization_id, market_id, occurrence_id)
    occurrence_layout = (
        OccurrenceLayout.objects.select_related("layout_version")
        .filter(occurrence=occurrence)
        .first()
    )
    if occurrence_layout is None:
        raise _layout_not_found()
    return _date_result(occurrence, occurrence_layout)


def save_date_layout(
    actor: User,
    organization_id: int,
    market_id: int,
    occurrence_id: int,
    *,
    expected_revision: int | None,
    layout_version_id: int,
    currency: str,
    offers: list[dict],
):
    """Select the date's layout version and set its offers, atomically.
    ``expected_revision`` is null when the date has no layout yet."""
    with transaction.atomic():
        _manager(actor, organization_id)
        occurrence = _occurrence(organization_id, market_id, occurrence_id, lock=True)
        _require_not_archived(occurrence.market)
        occurrence_layout = (
            OccurrenceLayout.objects.select_for_update().filter(occurrence=occurrence).first()
        )
        _check_revision(
            occurrence_layout.revision if occurrence_layout else None,
            expected_revision,
            "date's layout",
        )
        if occurrence_layout is not None and occurrence_layout.is_published:
            raise Conflict(
                "Unpublish this date's layout before changing it.", code="layout_published"
            )
        version = (
            LayoutVersion.objects.select_for_update()
            .filter(pk=layout_version_id, market_id=occurrence.market_id)
            .first()
        )
        if version is None:
            raise InvalidRequest("Choose a layout of this market.", code="layout_version_invalid")
        stall_ids = set(version.stalls.values_list("pk", flat=True))
        if not stall_ids:
            raise InvalidRequest("This layout has no stalls yet.", code="layout_version_invalid")
        currency = check_offers(currency, offers, stall_ids)

        if occurrence_layout is None:
            occurrence_layout = OccurrenceLayout(
                occurrence=occurrence, created_by=actor, revision=1
            )
        else:
            occurrence_layout.revision += 1
        occurrence_layout.layout_version = version
        occurrence_layout.updated_by = actor
        occurrence_layout.save()
        if not version.is_locked:
            # From now on a date depends on this plan: it can't change.
            version.locked_at = timezone.now()
            version.save(update_fields=["locked_at", "updated_at"])
        _write_offers(occurrence, currency, offers)
    return _date_result(occurrence, occurrence_layout)


def _write_offers(occurrence: EventOccurrence, currency: str, offers: list[dict]) -> None:
    existing = {
        o.stall_id: o
        for o in StallOffer.objects.filter(
            occurrence=occurrence, stall_id__in=[o["stall_id"] for o in offers]
        )
    }
    updates, creates = [], []
    now = timezone.now()
    for offer in offers:
        row = existing.get(offer["stall_id"])
        values = {
            "price_minor": offer["price_minor"],
            "currency": currency,
            "enabled": offer["enabled"],
        }
        if row is None:
            creates.append(StallOffer(occurrence=occurrence, stall_id=offer["stall_id"], **values))
        else:
            for field, value in values.items():
                setattr(row, field, value)
            row.updated_at = now
            updates.append(row)
    if updates:
        StallOffer.objects.bulk_update(
            updates, ["price_minor", "currency", "enabled", "updated_at"]
        )
    if creates:
        StallOffer.objects.bulk_create(creates)


def _set_published(
    actor: User,
    organization_id: int,
    market_id: int,
    occurrence_id: int,
    *,
    publish: bool,
    expected_revision: int | None = None,
):
    with transaction.atomic():
        _manager(actor, organization_id)
        occurrence = _occurrence(organization_id, market_id, occurrence_id, lock=True)
        occurrence_layout = (
            OccurrenceLayout.objects.select_for_update()
            .select_related("layout_version")
            .filter(occurrence=occurrence)
            .first()
        )
        if occurrence_layout is None:
            raise _layout_not_found()
        if publish:
            _check_revision(occurrence_layout.revision, expected_revision, "date's layout")
            _check_publishable(occurrence, occurrence_layout)
            if not occurrence_layout.is_published:
                occurrence_layout.published_at = timezone.now()
        else:
            occurrence_layout.published_at = None
        occurrence_layout.updated_by = actor
        occurrence_layout.save(update_fields=["published_at", "updated_by", "updated_at"])
    return _date_result(occurrence, occurrence_layout)


def _check_publishable(occurrence: EventOccurrence, occurrence_layout: OccurrenceLayout) -> None:
    _require_not_archived(occurrence.market)
    if occurrence.status != OccurrenceStatus.SCHEDULED or occurrence.ends_at <= timezone.now():
        raise Conflict(
            "Only a scheduled date that hasn't ended can have a published layout.",
            code="occurrence_unavailable",
        )
    if not any(o.enabled for o in current_offers(occurrence_layout)):
        raise InvalidRequest("Offer at least one stall before publishing.", code="layout_empty")


def publish_date_layout(actor, organization_id, market_id, occurrence_id, *, expected_revision):
    return _set_published(
        actor,
        organization_id,
        market_id,
        occurrence_id,
        publish=True,
        expected_revision=expected_revision,
    )


def unpublish_date_layout(actor, organization_id, market_id, occurrence_id):
    return _set_published(actor, organization_id, market_id, occurrence_id, publish=False)


# --- Public ---------------------------------------------------------------------------------


def public_date_layout(occurrence_id: int):
    """(occurrence layout, stalls, offers by stall id) for a published layout
    of a published market's scheduled date that hasn't ended; otherwise not
    found (drafts, archived markets, cancelled or past dates, unpublished)."""
    occurrence_layout = (
        OccurrenceLayout.objects.select_related("layout_version", "occurrence")
        .filter(
            occurrence_id=occurrence_id,
            published_at__isnull=False,
            occurrence__status=OccurrenceStatus.SCHEDULED,
            occurrence__ends_at__gt=timezone.now(),
            occurrence__market__status=MarketStatus.PUBLISHED,
        )
        .first()
    )
    if occurrence_layout is None:
        raise _layout_not_found()
    offers = {o.stall_id: o for o in current_offers(occurrence_layout)}
    stalls = list(occurrence_layout.layout_version.stalls.order_by("pk"))
    return occurrence_layout, stalls, offers
