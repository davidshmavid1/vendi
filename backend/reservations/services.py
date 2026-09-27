"""Stall holds: acquire, release, expire, and (for Phase 13) confirm.

Permissions:

    action                                   who
    take a hold, release a hold              vendor business OWNER
    list / view the business's reservations  vendor business OWNER, MEMBER
    confirm a hold                           trusted server code only (no endpoint)

Locking. Every write locks the event date's row first (FOR NO KEY UPDATE,
the same lock the applications and layouts services take), then the
reservation or offer rows it needs. All hold changes for one date therefore
run one at a time and in one order: date, then rows. The same date lock also
serializes holds with organizer edits of the date's layout and offers.

Expiry. A hold stops blocking its stall the moment ``expires_at`` passes:
reads treat it as expired, and every write for a date first switches that
date's lapsed HELD rows to EXPIRED (``_expire_lapsed``) before checking or
inserting, so the database's partial unique indexes only ever see live
holds. The ``expire_holds`` command does the same in bulk for tidiness;
nothing depends on it running.

Idempotency. Taking a hold requires a client ``request_key`` (1-64 of
``A-Z a-z 0-9 _ -``), unique per account and business. Sending the same key
for the same stall returns the original reservation, whatever its state now;
an expired or released hold is never revived. Sending it for another stall
is a conflict. Repeating a request never extends ``expires_at``.
"""

import re
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from accounts.models import User
from applications.models import Application, ApplicationStatus
from core.exceptions import Conflict, InvalidRequest, NotFound, PermissionDenied
from layouts.models import OccurrenceLayout, StallOffer
from layouts.services import public_date_layout
from markets.models import EventOccurrence, MarketStatus, OccurrenceStatus
from moderation.policy import ensure_can_participate
from reservations.models import OCCUPYING, Reservation, ReservationStatus
from vendors.permissions import membership_for, require_owner

REQUEST_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")


def hold_duration() -> timedelta:
    return timedelta(seconds=settings.RESERVATION_HOLD_SECONDS)


def _require_verified(user: User) -> None:
    if not user.is_email_verified:
        raise PermissionDenied(
            "Confirm your email address before doing this.", code="email_not_verified"
        )


def _lock_occurrence(occurrence_id: int) -> EventOccurrence:
    return (
        EventOccurrence.objects.select_for_update(of=("self",), no_key=True)
        .select_related("market")
        .get(pk=occurrence_id)
    )


def _expire_lapsed(occurrence_id: int, now) -> int:
    """Switch the date's lapsed holds to EXPIRED. Call with the date locked.
    ``expired_at`` records when the hold actually lapsed."""
    return (
        Reservation.objects.lapsed(now)
        .filter(occurrence_id=occurrence_id)
        .update(status=ReservationStatus.EXPIRED, expired_at=F("expires_at"), updated_at=now)
    )


def _unavailable(message: str, code: str) -> Conflict:
    return Conflict(message, code=code)


# --- Taking a hold ---------------------------------------------------------------------


@dataclass
class HoldResult:
    reservation: Reservation
    created: bool


def acquire_hold(actor: User, business_id: int, *, offer_id: int, request_key: str) -> HoldResult:
    _require_verified(actor)
    membership = membership_for(actor, business_id)
    require_owner(membership)
    if not REQUEST_KEY.fullmatch(request_key):
        raise InvalidRequest(
            "request_key must be 1-64 letters, digits, '-' or '_'.", code="request_key_invalid"
        )
    business = membership.business
    offer_date = StallOffer.objects.filter(pk=offer_id).values_list("occurrence_id", flat=True)
    occurrence_id = offer_date.first()
    if occurrence_id is None:
        raise NotFound("Stall not found.")
    try:
        with transaction.atomic():
            return _acquire_locked(actor, business, occurrence_id, offer_id, request_key)
    except IntegrityError as error:
        # Only possible when the same key races for two different dates (each
        # date is locked separately). Answer as a replay would.
        replay = _replay(actor, business, request_key, offer_id)
        if replay is not None:
            return HoldResult(replay, created=False)
        raise Conflict(
            "Your hold changed at the same moment. Refresh and try again.", code="hold_conflict"
        ) from error


def _replay(actor, business, request_key: str, offer_id: int) -> Reservation | None:
    existing = Reservation.objects.filter(
        held_by=actor, vendor_business=business, request_key=request_key
    ).first()
    if existing is None:
        return None
    if existing.offer_id != offer_id:
        raise Conflict(
            "This request key was already used for a different stall.",
            code="request_key_reused",
        )
    return existing


def _acquire_locked(actor, business, occurrence_id, offer_id, request_key) -> HoldResult:
    occurrence = _lock_occurrence(occurrence_id)
    now = timezone.now()
    _expire_lapsed(occurrence.pk, now)
    replay = _replay(actor, business, request_key, offer_id)
    if replay is not None:
        return HoldResult(replay, created=False)

    # Re-read everything that decides eligibility under the date lock.
    if occurrence.market.status != MarketStatus.PUBLISHED:
        raise _unavailable("This market isn't open for stall holds.", "stall_not_available")
    if occurrence.status != OccurrenceStatus.SCHEDULED or occurrence.starts_at <= now:
        raise _unavailable(
            "This date is cancelled or has already started.", "occurrence_unavailable"
        )
    offer = (
        StallOffer.objects.select_for_update(of=("self",)).select_related("stall").get(pk=offer_id)
    )
    layout = OccurrenceLayout.objects.filter(occurrence=occurrence).first()
    if (
        layout is None
        or not layout.is_published
        or offer.stall.layout_version_id != layout.layout_version_id
        or not offer.enabled
    ):
        raise _unavailable("This stall isn't offered for this date.", "stall_not_available")
    application = Application.objects.filter(
        occurrence=occurrence, vendor_business=business, status=ApplicationStatus.APPROVED
    ).first()
    if application is None:
        raise PermissionDenied(
            "Your business needs an approved application for this date to hold a stall.",
            code="application_not_approved",
        )
    ensure_can_participate(
        occurrence.market.organization_id, account=actor, vendor_business=business
    )

    current = Reservation.objects.filter(
        vendor_business=business, occurrence=occurrence, status__in=OCCUPYING
    ).first()
    if current is not None:
        raise Conflict(
            "Your business already has a stall for this date. Release it first to choose another.",
            code="hold_exists",
            details=[{"reservation_id": current.pk, "offer_id": current.offer_id}],
        )
    if Reservation.objects.filter(offer=offer, status__in=OCCUPYING).exists():
        raise _unavailable("Someone else is holding this stall right now.", "stall_unavailable")

    reservation = Reservation.objects.create(
        offer=offer,
        occurrence=occurrence,
        application=application,
        vendor_business=business,
        held_by=actor,
        price_minor=offer.price_minor,
        currency=offer.currency,
        request_key=request_key,
        created_at=now,
        expires_at=now + hold_duration(),
    )
    return HoldResult(reservation, created=True)


# --- Release, reads ----------------------------------------------------------------------


def release_hold(actor: User, business_id: int, reservation_id: int) -> Reservation:
    """End a hold early. Idempotent: an expired or released reservation is
    returned unchanged. Restrictions don't prevent releasing."""
    require_owner(membership_for(actor, business_id))
    reservation = Reservation.objects.filter(
        pk=reservation_id, vendor_business_id=business_id
    ).first()
    if reservation is None:
        raise NotFound("Reservation not found.")
    with transaction.atomic():
        _lock_occurrence(reservation.occurrence_id)
        now = timezone.now()
        _expire_lapsed(reservation.occurrence_id, now)
        reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
        if reservation.status == ReservationStatus.CONFIRMED:
            raise Conflict(
                "This stall is confirmed; it can't be released here.",
                code="reservation_confirmed",
            )
        if reservation.status == ReservationStatus.HELD:
            reservation.status = ReservationStatus.RELEASED
            reservation.released_at = now
            reservation.released_by = actor
            reservation.save(update_fields=["status", "released_at", "released_by", "updated_at"])
    return reservation


def list_for_business(actor: User, business_id: int, *, occurrence_id=None, status=None):
    """Any member may read. ``status`` filters on the displayed status, so
    lapsed holds count as EXPIRED."""
    membership_for(actor, business_id)
    queryset = Reservation.objects.filter(vendor_business_id=business_id).select_related(
        "offer__stall", "occurrence__market"
    )
    if occurrence_id:
        queryset = queryset.filter(occurrence_id=occurrence_id)
    now = timezone.now()
    if status == ReservationStatus.HELD:
        queryset = queryset.filter(status=ReservationStatus.HELD, expires_at__gt=now)
    elif status == ReservationStatus.EXPIRED:
        queryset = queryset.filter(
            Q(status=ReservationStatus.EXPIRED)
            | Q(status=ReservationStatus.HELD, expires_at__lte=now)
        )
    elif status:
        queryset = queryset.filter(status=status)
    return queryset


def get_for_business(actor: User, business_id: int, reservation_id: int) -> Reservation:
    membership_for(actor, business_id)
    reservation = (
        Reservation.objects.select_related("offer__stall", "occurrence__market")
        .filter(pk=reservation_id, vendor_business_id=business_id)
        .first()
    )
    if reservation is None:
        raise NotFound("Reservation not found.")
    return reservation


# --- Trusted operations -----------------------------------------------------------------


def confirm_hold(reservation_id: int) -> Reservation:
    """Phase 13 contract: turn a live hold into CONFIRMED, atomically.

    Server code only (e.g. after a verified payment); there is no endpoint.
    Locks the date, then the reservation. Succeeds only if the reservation
    is still HELD and ``expires_at`` hasn't passed; then it keeps its stall
    and price snapshot. Otherwise the lapsed hold is recorded as EXPIRED and
    Conflict is raised (``hold_expired``, or ``hold_not_active`` for a
    released or already confirmed reservation). Callers decide what to do
    with any money taken for a failed confirmation.
    """
    reservation = Reservation.objects.filter(pk=reservation_id).first()
    if reservation is None:
        raise NotFound("Reservation not found.")
    with transaction.atomic():
        _lock_occurrence(reservation.occurrence_id)
        now = timezone.now()
        _expire_lapsed(reservation.occurrence_id, now)
        reservation = Reservation.objects.select_for_update().get(pk=reservation_id)
        if reservation.status == ReservationStatus.HELD:
            reservation.status = ReservationStatus.CONFIRMED
            reservation.confirmed_at = now
            reservation.save(update_fields=["status", "confirmed_at", "updated_at"])
            return reservation
    # Outside the transaction so the EXPIRED switch above is kept.
    if reservation.status == ReservationStatus.EXPIRED:
        raise Conflict("This hold has expired.", code="hold_expired")
    raise Conflict("This reservation isn't an active hold.", code="hold_not_active")


def expire_holds() -> int:
    """Switch every lapsed hold to EXPIRED, one date at a time (each under its
    date lock). Safe to run anytime and repeatedly."""
    now = timezone.now()
    total = 0
    dates = Reservation.objects.lapsed(now).values_list("occurrence_id", flat=True).distinct()
    for occurrence_id in list(dates):
        with transaction.atomic():
            _lock_occurrence(occurrence_id)
            total += _expire_lapsed(occurrence_id, timezone.now())
    return total


# --- Public availability ---------------------------------------------------------------


AVAILABLE = "available"
UNAVAILABLE = "unavailable"
NOT_OFFERED = "not_offered"


def public_availability(occurrence_id: int) -> list[dict]:
    """Per stall of a published layout: available, unavailable (held or
    confirmed by someone) or not_offered. Reveals nothing about who."""
    occurrence_layout, stalls, offers = public_date_layout(occurrence_id)
    now = timezone.now()
    taken = set(
        Reservation.objects.occupying(now)
        .filter(occurrence_id=occurrence_id)
        .values_list("offer_id", flat=True)
    )
    items = []
    for stall in stalls:
        offer = offers.get(stall.pk)
        if offer is None or not offer.enabled:
            status = NOT_OFFERED
        elif offer.pk in taken:
            status = UNAVAILABLE
        else:
            status = AVAILABLE
        items.append(
            {
                "stall_id": stall.pk,
                "offer_id": offer.pk if status != NOT_OFFERED else None,
                "status": status,
            }
        )
    return items
