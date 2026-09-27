"""Stall layout operations.

    action                               OWNER   ADMIN   STAFF
    view an occurrence's layout          yes     yes     yes
    save (create/update), publish,       yes     yes     no
    unpublish

Every write locks the occurrence (FOR NO KEY UPDATE, the same order as the
applications app) and then the layout row, re-checks ``expected_revision``,
validates the *whole* submitted layout, and only then writes, all in one
transaction: an invalid stall leaves the saved layout untouched.

Rules:
- A published layout must be unpublished before it can be edited.
- A save lists every existing stall by id. Stalls are never deleted (disable
  them instead); stall ids from other layouts are rejected, so stalls can't
  be moved between occurrences or organizations.
- Publishing needs a scheduled date that hasn't ended, a market that isn't
  archived, and at least one enabled stall.
"""

from django.db import transaction
from django.utils import timezone

from accounts.models import User
from core.exceptions import Conflict, InvalidRequest, NotFound
from layouts.geometry import TEMP_LABEL_PREFIX, check_layout
from layouts.models import Stall, StallLayout
from markets.models import EventOccurrence, MarketStatus, OccurrenceStatus
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
    "enabled",
    "price_minor",
)


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


def _check_revision(layout: StallLayout | None, expected: int | None) -> None:
    current = layout.revision if layout else None
    if expected != current:
        raise Conflict(
            "Someone else changed this layout since you loaded it. Reload to see their changes.",
            code="stale_revision",
            details=[{"current_revision": current}],
        )


def get_layout(actor: User, organization_id: int, market_id: int, occurrence_id: int):
    """(occurrence, layout, stalls) for any organization member."""
    membership_for(actor, organization_id)
    occurrence = _occurrence(organization_id, market_id, occurrence_id)
    layout = StallLayout.objects.filter(occurrence=occurrence).first()
    if layout is None:
        raise _layout_not_found()
    return occurrence, layout, list(layout.stalls.order_by("pk"))


def save_layout(
    actor: User,
    organization_id: int,
    market_id: int,
    occurrence_id: int,
    *,
    expected_revision: int | None,
    canvas_width: int,
    canvas_height: int,
    currency: str,
    stalls: list[dict],
):
    """Create the layout (``expected_revision`` None) or replace its contents."""
    stalls = [
        {**stall, "label": stall["label"].strip(), "description": stall["description"].strip()}
        for stall in stalls
    ]
    with transaction.atomic():
        require_role(membership_for(actor, organization_id), Role.OWNER, Role.ADMIN)
        occurrence = _occurrence(organization_id, market_id, occurrence_id, lock=True)
        if occurrence.market.status == MarketStatus.ARCHIVED:
            raise Conflict("Archived markets can't be changed.", code="market_archived")
        layout = StallLayout.objects.select_for_update().filter(occurrence=occurrence).first()
        _check_revision(layout, expected_revision)
        if layout is not None and layout.is_published:
            raise Conflict("Unpublish this layout before editing it.", code="layout_published")
        existing = {s.pk: s for s in layout.stalls.all()} if layout else {}
        _check_stall_ids(stalls, existing)
        currency = check_layout(canvas_width, canvas_height, currency, stalls)

        if layout is None:
            layout = StallLayout(occurrence=occurrence, created_by=actor, revision=1)
        else:
            layout.revision += 1
        layout.canvas_width = canvas_width
        layout.canvas_height = canvas_height
        layout.currency = currency
        layout.updated_by = actor
        layout.save()
        _write_stalls(layout, stalls, existing)
    return occurrence, layout, list(layout.stalls.order_by("pk"))


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
                "message": (
                    "Every existing stall must be included. Disable a stall instead of removing it."
                ),
            }
        )
    if problems:
        raise InvalidRequest(
            "The stall list doesn't match this layout.", code="stall_mismatch", details=problems
        )


def _write_stalls(layout: StallLayout, stalls: list[dict], existing: dict[int, Stall]) -> None:
    saved_labels = {pk: row.label for pk, row in existing.items()}
    updates, creates = [], []
    for stall in stalls:
        values = {f: stall[f] for f in STALL_FIELDS}
        values["physical_unit"] = values["physical_unit"] or ""
        if stall.get("id") is None:
            creates.append(Stall(layout=layout, **values))
        else:
            row = existing[stall["id"]]
            for field, value in values.items():
                setattr(row, field, value)
            updates.append(row)
    # Labels are unique per layout and a save may swap them between stalls
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
        require_role(membership_for(actor, organization_id), Role.OWNER, Role.ADMIN)
        occurrence = _occurrence(organization_id, market_id, occurrence_id, lock=True)
        layout = StallLayout.objects.select_for_update().filter(occurrence=occurrence).first()
        if layout is None:
            raise _layout_not_found()
        if publish:
            _check_revision(layout, expected_revision)
            _check_publishable(occurrence, layout)
            if not layout.is_published:
                layout.published_at = timezone.now()
        else:
            layout.published_at = None
        layout.updated_by = actor
        layout.save(update_fields=["published_at", "updated_by", "updated_at"])
    return occurrence, layout, list(layout.stalls.order_by("pk"))


def _check_publishable(occurrence: EventOccurrence, layout: StallLayout) -> None:
    if occurrence.market.status == MarketStatus.ARCHIVED:
        raise Conflict("Archived markets can't be changed.", code="market_archived")
    if occurrence.status != OccurrenceStatus.SCHEDULED or occurrence.ends_at <= timezone.now():
        raise Conflict(
            "Only a scheduled date that hasn't ended can have a published layout.",
            code="occurrence_unavailable",
        )
    if not layout.stalls.filter(enabled=True).exists():
        raise InvalidRequest(
            "Add at least one enabled stall before publishing.", code="layout_empty"
        )


def publish_layout(actor, organization_id, market_id, occurrence_id, *, expected_revision: int):
    return _set_published(
        actor,
        organization_id,
        market_id,
        occurrence_id,
        publish=True,
        expected_revision=expected_revision,
    )


def unpublish_layout(actor, organization_id, market_id, occurrence_id):
    return _set_published(actor, organization_id, market_id, occurrence_id, publish=False)


# --- Public -------------------------------------------------------------------------------


def public_layout(occurrence_id: int):
    """A published layout of a published market's scheduled date that hasn't
    ended; otherwise not found (drafts, archived markets, cancelled or past
    dates, unpublished layouts)."""
    layout = (
        StallLayout.objects.select_related("occurrence__market")
        .filter(
            occurrence_id=occurrence_id,
            published_at__isnull=False,
            occurrence__status=OccurrenceStatus.SCHEDULED,
            occurrence__ends_at__gt=timezone.now(),
            occurrence__market__status=MarketStatus.PUBLISHED,
        )
        .first()
    )
    if layout is None:
        raise _layout_not_found()
    return layout, list(layout.stalls.order_by("pk"))
