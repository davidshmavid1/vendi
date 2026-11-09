from datetime import datetime, timedelta

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from bookings import cancellations
from bookings.models import (
    Booking,
    BookingCancellation,
    CancellationItemStatus,
    OccurrenceCancellationItem,
)
from core.exceptions import Conflict
from layouts.models import StallOffer
from markets.models import Market
from moderation import services as moderation_services
from organizations.models import OrganizationMembership, Role
from payments.models import AttemptStatus, Fulfillment, PaymentAttempt, Refund, RefundStatus
from reservations import services as reservation_services
from reservations.models import Reservation, ReservationStatus
from tests.test_payments import _webhook, paid, stripe  # noqa: F401 (fixtures)
from tests.test_reservations import _approve, at, world  # noqa: F401 (fixtures)
from vendors import services as vendor_services

CUTOFF = 48


@pytest.fixture
def cw(paid, make_user, as_user):  # noqa: F811 (fixture)
    """The payments world with a 48-hour vendor cutoff and a STAFF member."""
    Market.objects.filter(pk=paid.market.pk).update(vendor_cancellation_cutoff_hours=CUTOFF)
    staff = make_user("staff@example.com")
    OrganizationMembership.objects.create(organization=paid.org, user=staff, role=Role.STAFF)
    admin = make_user("admin@example.com")
    OrganizationMembership.objects.create(organization=paid.org, user=admin, role=Role.ADMIN)
    paid.client["STAFF"] = as_user(staff)
    paid.client["ADMIN"] = as_user(admin)
    return paid


def _hold(w, offer=0, who="a", key="k1"):
    vendor, business = (w.vendor_a, w.business_a) if who == "a" else (w.vendor_b, w.business_b)
    return reservation_services.acquire_hold(
        vendor, business.pk, offer_id=w.offers[offer].pk, request_key=key
    ).reservation


def _book(w, api, offer=0, who="a", key="k1") -> Booking:
    reservation = _hold(w, offer, who, key)
    business = w.business_a if who == "a" else w.business_b
    client = "A" if who == "a" else "B"
    w.client[client].post(f"/vendors/{business.pk}/reservations/{reservation.pk}/checkout")
    attempt = PaymentAttempt.objects.get(reservation=reservation)
    w.stripe.pay(attempt.checkout_session_id)
    _webhook(api, "checkout.session.completed", attempt.checkout_session_id)
    return Booking.objects.get(reservation=reservation)


def _free_booking(w, offer=1, key="free") -> Booking:
    StallOffer.objects.filter(pk=w.offers[offer].pk).update(price_minor=0)
    reservation = _hold(w, offer, key=key)
    w.client["A"].post(f"/vendors/{w.business_a.pk}/reservations/{reservation.pk}/confirm-free")
    return Booking.objects.get(reservation=reservation)


def _vendor_url(w, booking, suffix):
    return f"/vendors/{w.business_a.pk}/bookings/{booking.pk}/{suffix}"


def _org_url(w, booking, suffix):
    return f"/organizations/{w.org.pk}/bookings/{booking.pk}/{suffix}"


def _vendor_cancel(w, booking, client="A", **body):
    payload = {"expected_refund_minor": 2500, "currency": "USD", **body}
    return w.client[client].post(_vendor_url(w, booking, "cancel"), payload)


def _org_cancel(w, booking, client="ORG_OWNER", **body):
    payload = {"expected_refund_minor": 2500, "currency": "USD", "reason": "Rain", **body}
    return w.client[client].post(_org_url(w, booking, "cancel"), payload)


def _occupied(w, offer=0) -> bool:
    return Reservation.objects.occupying(timezone.now()).filter(offer=w.offers[offer]).exists()


# --- Policy snapshots ------------------------------------------------------------------------


def test_policy_is_snapshotted_and_later_edits_do_not_change_bookings(cw, api):
    booking = _book(cw, api)
    assert booking.policy_vendor_cutoff_hours == CUTOFF and booking.policy_captured_at
    response = cw.client["ORG_OWNER"].put(
        f"/organizations/{cw.org.pk}/markets/{cw.market.pk}/cancellation-policy",
        {"vendor_cancellation_cutoff_hours": 2},
    )
    assert response.status_code == 200
    assert response.json()["vendor_cancellation_cutoff_hours"] == 2
    booking.refresh_from_db()
    assert booking.policy_vendor_cutoff_hours == CUTOFF
    preview = cw.client["A"].get(_vendor_url(cw, booking, "cancellation")).json()
    deadline = cw.occurrence.starts_at - timedelta(hours=CUTOFF)
    assert datetime.fromisoformat(preview["vendor_deadline"]) == deadline
    assert _hold(cw, offer=1, who="b", key="b1").policy_vendor_cutoff_hours == 2


def test_policy_endpoint_permissions_and_bounds(cw):
    url = f"/organizations/{cw.org.pk}/markets/{cw.market.pk}/cancellation-policy"
    assert cw.client["STAFF"].put(url, {"vendor_cancellation_cutoff_hours": 1}).status_code == 403
    assert cw.client["A"].put(url, {"vendor_cancellation_cutoff_hours": 1}).status_code == 404
    assert (
        cw.client["ADMIN"].put(url, {"vendor_cancellation_cutoff_hours": 24 * 366}).status_code
        == 422
    )
    cleared = cw.client["ADMIN"].put(url, {"vendor_cancellation_cutoff_hours": None})
    assert cleared.json()["vendor_cancellation_cutoff_hours"] is None


def test_market_without_a_cutoff_offers_no_vendor_cancellation(cw, api):
    Market.objects.filter(pk=cw.market.pk).update(vendor_cancellation_cutoff_hours=None)
    booking = _book(cw, api)
    preview = cw.client["A"].get(_vendor_url(cw, booking, "cancellation")).json()
    assert (preview["permitted"], preview["problem"]) == (False, "vendor_cancellation_not_offered")
    assert _vendor_cancel(cw, booking).json()["error"]["code"] == "vendor_cancellation_not_offered"
    # The organizer can still cancel it.
    assert _org_cancel(cw, booking).status_code == 200


def test_bookings_from_before_policies_go_to_manual_review(cw, api):
    booking = _book(cw, api)
    Booking.objects.filter(pk=booking.pk).update(
        policy_captured_at=None, policy_vendor_cutoff_hours=None
    )
    response = _vendor_cancel(cw, booking)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "manual_review")
    assert Booking.objects.get(pk=booking.pk).status == "CONFIRMED"
    organizer = _org_cancel(cw, booking)
    assert organizer.json()["refund"]["amount_minor"] == 2500


# --- Cutoff boundary -------------------------------------------------------------------------


def test_cutoff_boundary_is_exclusive_and_in_absolute_hours(cw, api):
    booking = _book(cw, api)
    deadline = cw.occurrence.starts_at - timedelta(hours=CUTOFF)
    with at(deadline):
        at_boundary = _vendor_cancel(cw, booking)
    assert at_boundary.json()["error"]["code"] == "cutoff_passed"
    with at(deadline - timedelta(seconds=1)):
        just_before = _vendor_cancel(cw, booking)
    assert just_before.status_code == 200
    assert just_before.json()["status"] == "CANCELLED"


def test_deadline_follows_the_dates_authoritative_start(cw, api):
    booking = _book(cw, api)
    moved = cw.occurrence.starts_at + timedelta(days=2)
    cw.occurrence.__class__.objects.filter(pk=cw.occurrence.pk).update(
        starts_at=moved, ends_at=moved + timedelta(hours=4)
    )
    with at(cw.occurrence.starts_at - timedelta(hours=CUTOFF)):  # past the old deadline
        assert _vendor_cancel(cw, booking).status_code == 200


# --- Vendor cancellation -------------------------------------------------------------------


def test_vendor_cancels_before_the_cutoff(cw, api):
    booking = _book(cw, api)
    preview = cw.client["A"].get(_vendor_url(cw, booking, "cancellation")).json()
    assert preview["permitted"] and preview["can_act"]
    assert (preview["paid_minor"], preview["fee_retained_minor"], preview["refund_minor"]) == (
        2525,
        25,
        2500,
    )
    assert preview["releases_stall"]
    response = _vendor_cancel(cw, booking, reason="Sick")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED"
    assert body["cancellation"]["kind"] == "VENDOR" and body["cancellation"]["reason"] == "Sick"
    # Cancelled, but the refund isn't done until Stripe says so.
    assert body["refund"]["status"] == "PENDING" and body["refund"]["amount_minor"] == 2500
    refund = Refund.objects.get(reason="CANCELLATION")
    params = cw.stripe.refund_params[refund.stripe_refund_id]
    assert params == {
        "payment_intent": booking.payment_attempt.payment_intent_id,
        "amount": 2500,
        "refund_application_fee": False,  # Vendi keeps its fee
        "reason": "cancellation",
    }
    reservation = Reservation.objects.get(pk=booking.reservation_id)
    assert (reservation.status, reservation.confirmed_at is not None) == ("CANCELLED", True)
    # The stall is free again for others.
    assert _hold(cw, offer=0, who="b", key="b1").status == ReservationStatus.HELD
    cw.stripe.settle_refund(refund.stripe_refund_id)
    _webhook(api, "refund.updated", refund.stripe_refund_id)
    assert Refund.objects.get(pk=refund.pk).status == RefundStatus.SUCCEEDED


def test_stale_preview_is_rejected_without_changes(cw, api):
    booking = _book(cw, api)
    response = _vendor_cancel(cw, booking, expected_refund_minor=2525)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "stale_preview")
    assert response.json()["error"]["details"] == [{"refund_minor": 2500, "currency": "USD"}]
    assert _vendor_cancel(cw, booking, currency="EUR").json()["error"]["code"] == "stale_preview"
    assert Booking.objects.get(pk=booking.pk).status == "CONFIRMED"
    assert _occupied(cw)
    # A preview taken before the cutoff can't be used after it.
    with at(cw.occurrence.starts_at - timedelta(hours=CUTOFF) + timedelta(minutes=1)):
        assert _vendor_cancel(cw, booking).json()["error"]["code"] == "cutoff_passed"


def test_vendor_permissions_privacy_and_csrf(cw, api):
    booking = _book(cw, api)
    member = cw.client["A_MEMBER"]
    preview = member.get(_vendor_url(cw, booking, "cancellation")).json()
    assert preview["permitted"] and not preview["can_act"]
    assert _vendor_cancel(cw, booking, client="A_MEMBER").status_code == 403
    other = cw.client["B"].post(_vendor_url(cw, booking, "cancel"), {})
    assert other.status_code in (404, 422)
    assert cw.client["B"].get(_vendor_url(cw, booking, "cancellation")).status_code == 404
    assert api.post(_vendor_url(cw, booking, "cancel"), {}).status_code == 401
    no_csrf = cw.client["A"].post(
        _vendor_url(cw, booking, "cancel"),
        {"expected_refund_minor": 2500, "currency": "USD"},
        csrf=False,
    )
    assert no_csrf.status_code == 403
    assert Booking.objects.get(pk=booking.pk).status == "CONFIRMED"


def test_restrictions_do_not_block_cancelling(cw, api):
    booking = _book(cw, api)
    moderation_services.create_restriction(
        cw.owner, cw.org.pk, vendor_business_id=cw.business_a.pk, reason="internal"
    )
    assert _vendor_cancel(cw, booking).status_code == 200


def test_repeated_cancellation_returns_the_first_outcome(cw, api):
    booking = _book(cw, api)
    first = _vendor_cancel(cw, booking).json()
    second = _vendor_cancel(cw, booking).json()
    organizer = _org_cancel(cw, booking).json()
    assert first == second
    assert organizer["cancellation"]["kind"] == "VENDOR"
    assert BookingCancellation.objects.count() == 1
    assert Refund.objects.filter(reason="CANCELLATION").count() == 1
    assert cw.stripe.count("create_refund") == 1


def test_free_booking_cancels_with_no_refund(cw, api):
    booking = _free_booking(cw)
    preview = cw.client["A"].get(_vendor_url(cw, booking, "cancellation")).json()
    assert (preview["refund_rule"], preview["refund_minor"]) == ("NO_PAYMENT", 0)
    response = _vendor_cancel(cw, booking, expected_refund_minor=0)
    assert response.status_code == 200
    assert response.json()["refund"] is None
    assert cw.stripe.calls == [] or "create_refund" not in cw.stripe.calls
    assert not _occupied(cw, offer=1)


def test_releasing_twice_is_a_no_op_and_one_cancellation_per_booking(cw, api):
    booking = _book(cw, api)
    _vendor_cancel(cw, booking)
    with transaction.atomic():
        _occ, reservation = reservation_services.lock_reservation(booking.reservation_id)
        assert reservation_services.release_for_cancellation(reservation, timezone.now()) is False
    cancellation = BookingCancellation.objects.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        BookingCancellation.objects.create(
            booking=booking,
            kind="ORGANIZER",
            requested_by=cw.owner,
            requested_as="org:OWNER",
            requested_at=timezone.now(),
            completed_at=timezone.now(),
            refund_rule="PAID_MINUS_FEE",
            refund_entitlement_minor=1,
            currency="USD",
        )
    assert cancellation.refund_entitlement_minor == 2500


# --- Organizer cancellation ----------------------------------------------------------------


def test_organizer_roles_reason_and_private_note(cw, api):
    booking = _book(cw, api)
    staff_preview = cw.client["STAFF"].get(_org_url(cw, booking, "cancellation")).json()
    assert staff_preview["permitted"] and not staff_preview["can_act"]
    assert _org_cancel(cw, booking, client="STAFF").status_code == 403
    assert _org_cancel(cw, booking, client="ADMIN", reason="").status_code == 422
    response = _org_cancel(cw, booking, client="ADMIN", internal_note="Vendor no-show history")
    assert response.status_code == 200
    assert response.json()["cancellation"]["internal_note"] == "Vendor no-show history"
    vendor_view = cw.client["A"].get(f"/vendors/{cw.business_a.pk}/bookings/{booking.pk}").json()
    assert vendor_view["cancellation"]["internal_note"] is None
    assert vendor_view["cancellation"]["reason"] == "Rain"
    org_list = cw.client["STAFF"].get(f"/organizations/{cw.org.pk}/bookings").json()
    assert org_list["items"][0]["cancellation"]["internal_note"] == "Vendor no-show history"


def test_organizers_cannot_reach_other_organizations_bookings(cw, api, make_user, as_user):
    from organizations import services as org_services

    booking = _book(cw, api)
    other_owner = make_user("other@example.com")
    other = org_services.create_organization(other_owner, name="Elsewhere").organization
    client = as_user(other_owner)
    url = f"/organizations/{other.pk}/bookings/{booking.pk}/cancel"
    response = client.post(url, {"expected_refund_minor": 2500, "currency": "USD", "reason": "x"})
    assert response.status_code == 404
    assert (
        client.get(f"/organizations/{cw.org.pk}/bookings/{booking.pk}/cancellation").status_code
        == 404
    )


# --- Refund safety ---------------------------------------------------------------------------


def test_refund_timeout_and_lost_response_never_refund_twice(cw, api):
    booking = _book(cw, api)
    cw.stripe.fail("create_refund", "lost")
    body = _vendor_cancel(cw, booking).json()
    assert body["status"] == "CANCELLED" and body["refund"]["status"] == "REQUESTED"
    refund = Refund.objects.get(reason="CANCELLATION")
    assert refund.last_error
    call_command("reconcile_payments")
    call_command("reconcile_payments")
    assert Refund.objects.get(pk=refund.pk).status == RefundStatus.PENDING
    assert len(cw.stripe.refunds) == 1


def test_external_refund_is_recorded_and_blocks_automatic_refund(cw, api):
    booking = _book(cw, api)
    intent = booking.payment_attempt.payment_intent_id
    external = cw.stripe.external_refund(intent, 1000)
    _webhook(api, "refund.created", external)
    recorded = Refund.objects.get(stripe_refund_id=external)
    assert (recorded.reason, recorded.status, recorded.amount_minor) == (
        "EXTERNAL",
        "SUCCEEDED",
        1000,
    )
    # Recording it didn't cancel the booking.
    assert Booking.objects.get(pk=booking.pk).status == "CONFIRMED"
    body = _org_cancel(cw, booking).json()
    assert body["status"] == "CANCELLED" and body["refund"]["status"] == "REVIEW"
    assert "create_refund" not in cw.stripe.calls


def test_unrecorded_external_refund_or_dispute_goes_to_review(cw, api):
    first = _book(cw, api)
    cw.stripe.external_refund(first.payment_attempt.payment_intent_id, 500)  # webhook lost
    assert _org_cancel(cw, first).json()["refund"]["status"] == "REVIEW"
    assert Refund.objects.get(reason="CANCELLATION").last_error == "refunded_externally"
    second = _book(cw, api, offer=1, who="b", key="b1")
    cw.stripe.disputed[second.payment_attempt.payment_intent_id] = True
    url = f"/organizations/{cw.org.pk}/bookings/{second.pk}/cancel"
    cw.client["ORG_OWNER"].post(
        url, {"expected_refund_minor": 2500, "currency": "USD", "reason": "x"}
    )
    review = Refund.objects.get(attempt=second.payment_attempt, reason="CANCELLATION")
    assert (review.status, review.last_error) == ("REVIEW", "disputed")
    assert "create_refund" not in cw.stripe.calls
    counts = cancellations_needing_operator()
    assert counts >= 2


def cancellations_needing_operator() -> int:
    return Refund.objects.filter(status__in=("FAILED", "CANCELED", "REVIEW")).count()


def test_refunds_can_never_exceed_what_was_paid(cw, api):
    booking = _book(cw, api)
    attempt = booking.payment_attempt
    Refund.objects.create(
        attempt=attempt,
        reason="UNFULFILLED",
        amount_minor=2000,
        currency="USD",
        idempotency_key="prior",
        status="SUCCEEDED",
    )
    _org_cancel(cw, booking)
    refund = Refund.objects.get(reason="CANCELLATION")
    assert (refund.status, refund.last_error) == ("REVIEW", "exceeds_refundable")


def test_refund_webhooks_duplicate_and_out_of_order(cw, api):
    booking = _book(cw, api)
    _vendor_cancel(cw, booking)
    refund = Refund.objects.get(reason="CANCELLATION")
    cw.stripe.settle_refund(refund.stripe_refund_id)
    _webhook(api, "refund.updated", refund.stripe_refund_id, event_id="evt_1")
    _webhook(api, "refund.updated", refund.stripe_refund_id, event_id="evt_1")
    # An older "pending" snapshot delivered late never reverses success.
    cw.stripe.settle_refund(refund.stripe_refund_id, status="pending")
    _webhook(api, "refund.updated", refund.stripe_refund_id, event_id="evt_0")
    assert Refund.objects.get(pk=refund.pk).status == RefundStatus.SUCCEEDED


# --- Date cancellation ----------------------------------------------------------------------


def _third_vendor(cw, make_user):
    vendor = make_user("c@example.com")
    business = vendor_services.create_business(
        vendor, name="Candles", category="CRAFTS", contact_email="c@x.example"
    ).business
    _approve(cw.owner, cw.org, cw.market, cw.occurrence, vendor, business)
    return vendor, business


def _cancel_date(cw):
    url = f"/organizations/{cw.org.pk}/markets/{cw.market.pk}/occurrences/{cw.occurrence.pk}/cancel"
    return cw.client["ORG_OWNER"].post(url, {"message": "Storm warning"})


def _progress(cw, client="STAFF"):
    url = (
        f"/organizations/{cw.org.pk}/markets/{cw.market.pk}/occurrences/"
        f"{cw.occurrence.pk}/cancellation"
    )
    return cw.client[client].get(url).json()


def test_date_cancellation_with_mixed_payment_states(cw, api, make_user):
    paid_booking = _book(cw, api)  # A: paid booking
    checkout = _hold(cw, offer=1, who="b", key="b1")  # B: checkout open
    cw.client["B"].post(f"/vendors/{cw.business_b.pk}/reservations/{checkout.pk}/checkout")
    vendor_c, business_c = _third_vendor(cw, make_user)
    unpaid = reservation_services.acquire_hold(
        vendor_c, business_c.pk, offer_id=cw.offers[2].pk, request_key="c1"
    ).reservation  # C: unpaid hold

    assert _cancel_date(cw).status_code == 200
    cw.occurrence.refresh_from_db()
    assert cw.occurrence.status == "CANCELLED"
    # The unpaid hold was released in the same transaction.
    assert Reservation.objects.get(pk=unpaid.pk).status == "CANCELLED"
    progress = _progress(cw)
    assert progress["cancelled"] and progress["pending"]
    assert progress["items"]["HOLD"] == {"DONE": 1}
    assert progress["items"]["BOOKING"] == {"PENDING": 1}
    # Nothing on the date can be held or booked any more.
    with pytest.raises(Conflict) as denied:
        reservation_services.acquire_hold(
            vendor_c, business_c.pk, offer_id=cw.offers[2].pk, request_key="c2"
        )
    assert denied.value.code == "occurrence_unavailable"

    # Right after the request (on commit): database work only.
    cancellations.process_occurrence_cancellations(provider=False)
    booking = Booking.objects.get(pk=paid_booking.pk)
    assert booking.status == "CANCELLED"
    assert booking.cancellation.kind == "EVENT" and booking.cancellation.reason == "Storm warning"
    assert booking.cancellation.refund.status == RefundStatus.REQUESTED
    assert "create_refund" not in cw.stripe.calls

    # Then the recovery command: expire the checkout, send the refund.
    call_command("reconcile_payments")
    attempt_b = PaymentAttempt.objects.get(reservation=checkout)
    assert attempt_b.status == AttemptStatus.CANCELED
    assert Reservation.objects.get(pk=checkout.pk).status == "CANCELLED"
    assert Refund.objects.get(pk=booking.cancellation.refund.pk).status == RefundStatus.PENDING
    final = _progress(cw)
    assert final["completed_at"] and final["items"]["CHECKOUT"] == {"DONE": 1}
    # Idempotent: running again creates nothing and sends nothing new.
    call_command("reconcile_payments")
    assert cw.stripe.count("create_refund") == 1
    assert cw.stripe.count("expire_session") == 1
    assert BookingCancellation.objects.count() == 1


def test_payment_during_date_cancellation_is_refunded_not_booked(cw, api):
    reservation = _hold(cw, offer=1, who="b", key="b1")
    cw.client["B"].post(f"/vendors/{cw.business_b.pk}/reservations/{reservation.pk}/checkout")
    attempt = PaymentAttempt.objects.get(reservation=reservation)
    _cancel_date(cw)
    cw.stripe.pay(attempt.checkout_session_id)  # the vendor pays before we expire it
    call_command("reconcile_payments")
    attempt.refresh_from_db()
    assert (attempt.status, attempt.fulfillment) == ("SUCCEEDED", Fulfillment.UNFULFILLED)
    assert not Booking.objects.filter(reservation=reservation).exists()
    compensation = Refund.objects.get(attempt=attempt)
    assert (compensation.reason, compensation.amount_minor) == ("UNFULFILLED", 2525)
    item = OccurrenceCancellationItem.objects.get(reservation=reservation)
    assert item.status == CancellationItemStatus.DONE


def test_delayed_payment_waits_and_cannot_revive_a_cancelled_date(cw, api):
    reservation = _hold(cw, offer=1, who="b", key="b1")
    cw.client["B"].post(f"/vendors/{cw.business_b.pk}/reservations/{reservation.pk}/checkout")
    attempt = PaymentAttempt.objects.get(reservation=reservation)
    cw.stripe.pay_later(attempt.checkout_session_id)
    _webhook(api, "checkout.session.completed", attempt.checkout_session_id)
    _cancel_date(cw)
    call_command("reconcile_payments")
    item = OccurrenceCancellationItem.objects.get(reservation=reservation)
    assert (item.status, item.last_error) == ("PENDING", "payment_outcome_pending")
    assert _progress(cw)["pending"]
    cw.stripe.settle(attempt.checkout_session_id, succeeded=True)
    _webhook(api, "checkout.session.async_payment_succeeded", attempt.checkout_session_id)
    call_command("reconcile_payments")
    attempt.refresh_from_db()
    assert attempt.fulfillment == Fulfillment.UNFULFILLED
    assert not Booking.objects.filter(reservation=reservation).exists()
    assert OccurrenceCancellationItem.objects.get(pk=item.pk).status == "DONE"


def test_cancelled_date_stays_unbookable_after_its_stalls_are_released(cw, api):
    _book(cw, api)
    _cancel_date(cw)
    cancellations.process_occurrence_cancellations(provider=False)
    assert not _occupied(cw)
    public = cw.client["A"].get(f"/public/occurrences/{cw.occurrence.pk}/stall-availability")
    assert public.status_code == 404
    hold = cw.client["B"].post(cw.url_b, {"offer_id": cw.offers[0].pk, "request_key": "late"})
    assert hold.json()["error"]["code"] in ("occurrence_unavailable", "stall_not_available")


def test_vendor_cancellation_on_a_cancelled_date_is_left_to_the_event_flow(cw, api):
    booking = _book(cw, api)
    _cancel_date(cw)
    response = _vendor_cancel(cw, booking)
    assert response.json()["error"]["code"] == "occurrence_cancelled"


def test_only_managers_can_retry_date_cancellation(cw, api):
    _book(cw, api)
    _cancel_date(cw)
    url = (
        f"/organizations/{cw.org.pk}/markets/{cw.market.pk}/occurrences/"
        f"{cw.occurrence.pk}/cancellation/process"
    )
    assert cw.client["STAFF"].post(url).status_code == 403
    body = cw.client["ADMIN"].post(url).json()
    assert body["items"]["BOOKING"] == {"DONE": 1}
    assert body["refunds"] == {"PENDING": 1}


def test_refund_that_fails_after_succeeding_goes_to_an_operator(cw, api):
    booking = _book(cw, api)
    _vendor_cancel(cw, booking)
    refund = Refund.objects.get(reason="CANCELLATION")
    cw.stripe.settle_refund(refund.stripe_refund_id)
    _webhook(api, "refund.updated", refund.stripe_refund_id, event_id="evt_ok")
    assert Refund.objects.get(pk=refund.pk).status == RefundStatus.SUCCEEDED
    # Weeks later the bank returns it (Stripe sends refund.failed).
    cw.stripe.settle_refund(refund.stripe_refund_id, status="failed")
    _webhook(api, "refund.failed", refund.stripe_refund_id, event_id="evt_failed")
    failed = Refund.objects.get(pk=refund.pk)
    assert (failed.status, failed.last_error) == ("FAILED", "refund_failed_after_success")
    assert Booking.objects.get(pk=booking.pk).status == "CANCELLED"  # booking unaffected
    out = cw.client["A"].get(f"/vendors/{cw.business_a.pk}/bookings/{booking.pk}").json()
    assert out["refund"]["status"] == "FAILED"
