from datetime import timedelta

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.utils import timezone

from bookings.models import Booking
from layouts.models import StallOffer
from moderation import services as moderation_services
from organizations import services as org_services
from payments import services
from payments.gateway import AccountSnapshot, StripeGateway
from payments.models import (
    AttemptStatus,
    Fulfillment,
    PaymentAccount,
    PaymentAttempt,
    Refund,
    RefundStatus,
    StripeEvent,
)
from reservations import services as reservation_services
from reservations.models import Reservation, ReservationStatus
from tests.fake_stripe import FakeStripe, signed_event
from tests.test_reservations import HOLD, at, world  # noqa: F401 (fixture)

SESSION = timedelta(seconds=settings.CHECKOUT_SESSION_SECONDS)


@pytest.fixture
def stripe():
    fake = FakeStripe()
    previous = services.set_gateway(fake)
    yield fake
    services.set_gateway(previous)


@pytest.fixture
def paid(world, stripe):  # noqa: F811 (fixture)
    """The world, with the organizer's Stripe account linked."""
    PaymentAccount.objects.create(
        organization=world.org,
        stripe_account_id="acct_test123",
        livemode=False,
        charges_enabled=True,
        application_fee_bps=100,
        verified_at=timezone.now(),
    )
    world.stripe = stripe
    return world


def _hold(w, offer=0, who="a", key="k1"):
    vendor, business = (w.vendor_a, w.business_a) if who == "a" else (w.vendor_b, w.business_b)
    return reservation_services.acquire_hold(
        vendor, business.pk, offer_id=w.offers[offer].pk, request_key=key
    ).reservation


def _path(w, reservation, suffix="", business=None):
    business = business or w.business_a
    return f"/vendors/{business.pk}/reservations/{reservation.pk}{suffix}"


def _checkout(w, reservation, client="A"):
    return w.client[client].post(_path(w, reservation, "/checkout"))


def _webhook(api, event_type, object_id, **kwargs):
    body, signature = signed_event(event_type, object_id, **kwargs)
    return api.http.post(
        "/api/v1/payments/stripe/webhook",
        body,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=signature,
    )


def _attempt(reservation) -> PaymentAttempt:
    return PaymentAttempt.objects.get(reservation=reservation)


def _state(w, reservation, client="A"):
    return w.client[client].get(_path(w, reservation, "/payment")).json()


# --- Checkout creation -----------------------------------------------------------------------


def test_checkout_uses_the_reservation_snapshot_and_the_approved_funds_flow(paid):
    reservation = _hold(paid)
    StallOffer.objects.filter(pk=paid.offers[0].pk).update(price_minor=9999)  # later price edit
    response = _checkout(paid, reservation)
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["state"] == "CHECKOUT_OPEN"
    assert body["payment"]["checkout_url"].startswith("https://checkout.stripe.test/")
    session = paid.stripe.only_session()
    params = paid.stripe.session_params[session.id]
    # The stall price (snapshot, not 9999) plus Vendi's 1% fee on top.
    lines = [
        (li["price_data"]["product_data"]["name"], li["price_data"]["unit_amount"])
        for li in params["line_items"]
    ]
    assert lines[0][1] == 2500 and lines[1] == ("Vendi service fee", 25)
    assert params["line_items"][0]["price_data"]["currency"] == "usd"
    # Dynamic payment methods: no allowlist; Dashboard settings decide.
    assert "payment_method_types" not in params
    assert params["integration_identifier"] == services.CHECKOUT_INTEGRATION_ID
    assert params["payment_intent_data"]["transfer_data"] == {"destination": "acct_test123"}
    assert params["payment_intent_data"]["application_fee_amount"] == 25  # 1%
    # Return URLs come from configuration, never from the client.
    assert params["success_url"].startswith("http://frontend.test/vendor/businesses/")
    assert params["cancel_url"].endswith("?checkout=cancelled")
    attempt = _attempt(reservation)
    assert (attempt.amount_minor, attempt.application_fee_minor, attempt.status) == (
        2525,
        25,
        "OPEN",
    )
    assert body["payment"]["stall_price_minor"] == 2500 and body["payment"]["fee_minor"] == 25
    assert attempt.checkout_session_id == session.id


def test_checkout_extends_the_hold_to_the_session_and_blocks_expiry(paid):
    start = timezone.now()
    with at(start):
        reservation = _hold(paid)
        assert _checkout(paid, reservation).status_code == 200
    reservation.refresh_from_db()
    assert reservation.payment_pending
    assert reservation.expires_at == start + SESSION
    # Long after both the hold and the session would have ended, the stall
    # stays taken until Stripe says what happened.
    with at(start + SESSION + timedelta(hours=1)):
        assert reservation_services.expire_holds() == 0
        response = paid.client["B"].post(
            paid.url_b, {"offer_id": paid.offers[0].pk, "request_key": "b1"}
        )
        assert response.json()["error"]["code"] == "stall_unavailable"
        release = paid.client["A"].post(_path(paid, reservation, "/release"))
        assert release.json()["error"]["code"] == "payment_in_progress"


def test_repeated_checkout_reuses_the_open_session(paid):
    reservation = _hold(paid)
    first = _checkout(paid, reservation).json()
    second = _checkout(paid, reservation).json()
    assert first["payment"]["id"] == second["payment"]["id"]
    assert first["payment"]["checkout_url"] == second["payment"]["checkout_url"]
    assert paid.stripe.count("create_session") == 1
    assert PaymentAttempt.objects.count() == 1


def test_checkout_permissions_and_tenant_isolation(paid):
    reservation = _hold(paid)
    member = _checkout(paid, reservation, client="A_MEMBER")
    assert member.status_code == 403
    other = paid.client["B"].post(_path(paid, reservation, "/checkout"))
    assert other.status_code == 404  # B isn't a member of A's business
    wrong_business = paid.client["B"].post(
        _path(paid, reservation, "/checkout", business=paid.business_b)
    )
    assert wrong_business.status_code == 404  # reservation isn't B's
    assert PaymentAttempt.objects.count() == 0
    # Members can read the status, but never get the payable link.
    _checkout(paid, reservation)
    assert _state(paid, reservation, "A_MEMBER")["payment"]["checkout_url"] is None
    assert _state(paid, reservation)["payment"]["checkout_url"]
    assert paid.client["B"].get(_path(paid, reservation, "/payment")).status_code == 404


def test_checkout_requires_session_and_csrf(paid, api):
    reservation = _hold(paid)
    assert api.post(_path(paid, reservation, "/checkout")).status_code == 401
    no_csrf = paid.client["A"].post(_path(paid, reservation, "/checkout"), csrf=False)
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "csrf_failed"
    assert PaymentAttempt.objects.count() == 0


def test_checkout_needs_a_linked_account_that_can_take_charges(paid):
    PaymentAccount.objects.update(charges_enabled=False)
    reservation = _hold(paid)
    response = _checkout(paid, reservation)
    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "payments_unavailable",
    )
    assert not Reservation.objects.get(pk=reservation.pk).payment_pending


def test_checkout_is_unavailable_without_a_configured_provider(paid):
    # A linked account but no STRIPE_SECRET_KEY: refuse up front instead of
    # starting an attempt that can never reach Stripe.
    previous = services.set_gateway(StripeGateway("", "whsec_x"))
    try:
        reservation = _hold(paid)
        response = _checkout(paid, reservation)
    finally:
        services.set_gateway(previous)
    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "payments_unavailable",
    )
    assert PaymentAttempt.objects.count() == 0
    assert not Reservation.objects.get(pk=reservation.pk).payment_pending


def test_creation_that_never_gets_an_answer_is_abandoned_after_the_session_expiry(paid):
    start = timezone.now()
    with at(start):
        reservation = _hold(paid)
        paid.stripe.fail("create_session", *["timeout"] * 5)
        assert _checkout(paid, reservation).status_code == 503
    # Before any session could have expired, the stall stays pending.
    with at(start + SESSION):
        services.reconcile()
        assert _attempt(reservation).status == AttemptStatus.CREATING
        assert Reservation.objects.get(pk=reservation.pk).payment_pending
    # Well after: any session is expired and was never shown, so give up.
    with at(start + SESSION + services.ABANDON_CREATING_AFTER):
        services.reconcile()
        attempt = _attempt(reservation)
        assert attempt.status == AttemptStatus.FAILED
        assert attempt.last_error.startswith("abandoned_")
        reservation.refresh_from_db()
        assert not reservation.payment_pending
        response = paid.client["B"].post(
            paid.url_b, {"offer_id": paid.offers[0].pk, "request_key": "b1"}
        )
        assert response.status_code == 201, response.json()


def test_checkout_rechecks_eligibility(paid):
    reservation = _hold(paid)
    moderation_services.create_restriction(
        paid.owner, paid.org.pk, vendor_business_id=paid.business_a.pk, reason="x"
    )
    response = _checkout(paid, reservation)
    assert response.status_code == 403
    assert paid.stripe.count("create_session") == 0


def test_expired_hold_cannot_start_checkout(paid):
    start = timezone.now()
    with at(start):
        reservation = _hold(paid)
    with at(start + HOLD):
        response = _checkout(paid, reservation)
    assert response.json()["error"]["code"] == "hold_expired"
    assert PaymentAttempt.objects.count() == 0


# --- Unknown and failed provider outcomes ---------------------------------------------------


def test_timeout_keeps_the_stall_and_the_retry_uses_the_same_key(paid):
    reservation = _hold(paid)
    paid.stripe.fail("create_session", "timeout")
    response = _checkout(paid, reservation)
    assert (response.status_code, response.json()["error"]["code"]) == (503, "checkout_pending")
    attempt = _attempt(reservation)
    assert attempt.status == AttemptStatus.CREATING
    assert attempt.last_error == "stripe_APIConnectionError"
    assert Reservation.objects.get(pk=reservation.pk).payment_pending
    assert _state(paid, reservation)["state"] == "PROCESSING"
    retry = _checkout(paid, reservation)
    assert retry.status_code == 200
    assert _attempt(reservation).status == AttemptStatus.OPEN
    assert len(paid.stripe.sessions) == 1


def test_lost_response_after_stripe_created_the_session(paid):
    reservation = _hold(paid)
    paid.stripe.fail("create_session", "lost")
    assert _checkout(paid, reservation).status_code == 503
    assert len(paid.stripe.sessions) == 1  # Stripe did create it
    assert _checkout(paid, reservation).status_code == 200
    assert len(paid.stripe.sessions) == 1  # and the retry got the same one back
    assert _attempt(reservation).checkout_session_id == paid.stripe.only_session().id


def test_local_write_failure_after_stripe_success_is_recovered(paid, monkeypatch):
    reservation = _hold(paid)
    original = PaymentAttempt.save

    def broken_save(self, *args, **kwargs):
        if self.status == AttemptStatus.OPEN:
            raise IntegrityError("simulated database failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PaymentAttempt, "save", broken_save)
    with pytest.raises(IntegrityError):
        services.start_checkout(paid.vendor_a, paid.business_a.pk, reservation.pk)
    monkeypatch.setattr(PaymentAttempt, "save", original)
    assert _attempt(reservation).status == AttemptStatus.CREATING
    call_command("reconcile_payments")  # a create may still be in flight: left alone
    assert _attempt(reservation).status == AttemptStatus.CREATING
    PaymentAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=1))
    call_command("reconcile_payments")
    assert _attempt(reservation).status == AttemptStatus.OPEN
    assert len(paid.stripe.sessions) == 1


def test_definitive_refusal_frees_the_hold_for_a_bounded_number_of_retries(paid, settings):
    settings.CHECKOUT_MAX_ATTEMPTS = 2
    reservation = _hold(paid)
    paid.stripe.fail("create_session", "refuse", "refuse")
    first = _checkout(paid, reservation)
    assert (first.status_code, first.json()["error"]["code"]) == (409, "checkout_failed")
    assert _attempt(reservation).status == AttemptStatus.FAILED
    assert not Reservation.objects.get(pk=reservation.pk).payment_pending
    assert _checkout(paid, reservation).json()["error"]["code"] == "checkout_failed"
    limit = _checkout(paid, reservation)
    assert limit.json()["error"]["code"] == "checkout_limit"
    assert PaymentAttempt.objects.filter(reservation=reservation).count() == 2


# --- Webhooks -------------------------------------------------------------------------------


def test_paid_webhook_books_the_stall_once(paid, api):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    session = paid.stripe.only_session()
    paid.stripe.pay(session.id)
    assert _state(paid, reservation)["state"] == "CHECKOUT_OPEN"  # redirect isn't proof
    response = _webhook(api, "checkout.session.completed", session.id)
    assert response.status_code == 200
    booking = Booking.objects.get()
    assert (booking.reservation_id, booking.price_minor, booking.currency) == (
        reservation.pk,
        2500,
        "USD",
    )
    assert booking.payment_required and booking.payment_attempt == _attempt(reservation)
    reservation.refresh_from_db()
    assert reservation.status == ReservationStatus.CONFIRMED and not reservation.payment_pending
    attempt = _attempt(reservation)
    assert (attempt.status, attempt.fulfillment) == ("SUCCEEDED", Fulfillment.FULFILLED)
    # Duplicate deliveries (same event, or another event for it) change nothing.
    assert _webhook(api, "checkout.session.completed", session.id).status_code == 200
    _webhook(api, "checkout.session.completed", session.id, event_id="evt_other")
    assert Booking.objects.count() == 1
    state = _state(paid, reservation)
    assert state["state"] == "BOOKED" and state["booking"]["id"] == booking.pk


def test_older_expired_event_never_overwrites_a_payment(paid, api):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    session = paid.stripe.only_session()
    paid.stripe.pay(session.id)
    _webhook(api, "checkout.session.completed", session.id)
    _webhook(api, "checkout.session.expired", session.id)
    assert _attempt(reservation).status == AttemptStatus.SUCCEEDED
    assert Booking.objects.count() == 1


def test_event_payload_is_never_trusted(paid, api):
    """A "completed" event for a session Stripe says is still open does nothing."""
    reservation = _hold(paid)
    _checkout(paid, reservation)
    session = paid.stripe.only_session()
    _webhook(api, "checkout.session.completed", session.id)
    assert _attempt(reservation).status == AttemptStatus.OPEN
    assert Booking.objects.count() == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount_received": 100},
        {"currency": "EUR"},
        {"destination": "acct_someone_else"},
        {"application_fee_amount": 2525},
        {"livemode": True},
    ],
)
def test_payment_that_does_not_match_the_attempt_is_refunded_not_booked(paid, api, overrides):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    session = paid.stripe.only_session()
    paid.stripe.pay(session.id, **overrides)
    _webhook(api, "checkout.session.completed", session.id)
    attempt = _attempt(reservation)
    assert (attempt.status, attempt.fulfillment) == ("SUCCEEDED", Fulfillment.UNFULFILLED)
    assert attempt.last_error.startswith("verification_failed")
    assert Booking.objects.count() == 0
    refund = Refund.objects.get()
    # Never booked: everything paid, fee included, goes back.
    assert (refund.amount_minor, refund.status) == (2525, RefundStatus.PENDING)
    assert Reservation.objects.get(pk=reservation.pk).status == ReservationStatus.EXPIRED


def test_invalid_signatures_are_rejected_before_anything_is_recorded(paid, api):
    body, _signature = signed_event("checkout.session.completed", "cs_x")
    for signature in ("t=1,v1=bad", None):
        headers = {"HTTP_STRIPE_SIGNATURE": signature} if signature else {}
        response = api.http.post(
            "/api/v1/payments/stripe/webhook", body, content_type="application/json", **headers
        )
        assert (response.status_code, response.json()["error"]["code"]) == (
            400,
            "invalid_signature",
        )
    wrong_secret = _webhook(api, "checkout.session.completed", "cs_x", secret="whsec_other")
    assert wrong_secret.status_code == 400
    assert StripeEvent.objects.count() == 0


def test_webhook_for_unknown_or_connected_account_objects_is_ignored(paid, api):
    assert _webhook(api, "checkout.session.completed", "cs_not_ours").status_code == 200
    _webhook(api, "checkout.session.completed", "cs_x", event_id="evt_c", account="acct_x")
    notes = set(StripeEvent.objects.values_list("last_error", flat=True))
    assert notes == {"ignored_unknown_session", "ignored_connected_account"}
    assert StripeEvent.objects.filter(processed_at__isnull=True).count() == 0


def test_webhook_processing_failure_stays_retryable(paid, api):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    session = paid.stripe.only_session()
    paid.stripe.pay(session.id)
    paid.stripe.fail("retrieve_session", "timeout")
    response = _webhook(api, "checkout.session.completed", session.id)
    assert (response.status_code, response.json()["error"]["code"]) == (503, "event_retry")
    event = StripeEvent.objects.get()
    assert event.processed_at is None and event.attempts == 1
    assert Booking.objects.count() == 0
    # Stripe redelivers the same event.
    assert _webhook(api, "checkout.session.completed", session.id).status_code == 200
    assert StripeEvent.objects.get().processed_at is not None
    assert Booking.objects.count() == 1


# --- Expiry, cancellation and late payment --------------------------------------------------


def test_expired_session_frees_the_stall(paid, api):
    start = timezone.now()
    with at(start):
        reservation = _hold(paid)
        _checkout(paid, reservation)
    session = paid.stripe.only_session()
    paid.stripe.expire(session.id)
    with at(start + SESSION + timedelta(seconds=1)):
        _webhook(api, "checkout.session.expired", session.id)
        reservation.refresh_from_db()
        assert _attempt(reservation).status == AttemptStatus.EXPIRED
        assert (reservation.status, reservation.payment_pending) == ("EXPIRED", False)
        taken = _hold(paid, who="b", key="b1")
        assert taken.status == ReservationStatus.HELD


def test_delayed_payment_keeps_the_stall_until_it_succeeds(paid, api):
    start = timezone.now()
    with at(start):
        reservation = _hold(paid)
        _checkout(paid, reservation)
    session = paid.stripe.only_session()
    paid.stripe.pay_later(session.id)
    _webhook(api, "checkout.session.completed", session.id)
    attempt = _attempt(reservation)
    assert attempt.status == AttemptStatus.OPEN and attempt.payment_intent_id
    state = _state(paid, reservation)
    assert state["state"] == "PROCESSING" and state["payment"]["checkout_url"] is None
    assert Booking.objects.count() == 0
    # Days later: the hold outlived its session, but the stall is still held.
    with at(start + timedelta(days=3)):
        call_command("reconcile_payments")
        reservation.refresh_from_db()
        assert (reservation.status, reservation.payment_pending) == ("HELD", True)
        paid.stripe.settle(session.id, succeeded=True)
        _webhook(api, "checkout.session.async_payment_succeeded", session.id)
        assert _state(paid, reservation)["state"] == "BOOKED"
    assert Booking.objects.count() == 1


def test_failed_delayed_payment_frees_the_stall(paid, api):
    start = timezone.now()
    with at(start):
        reservation = _hold(paid)
        _checkout(paid, reservation)
    session = paid.stripe.only_session()
    paid.stripe.pay_later(session.id)
    _webhook(api, "checkout.session.completed", session.id)
    with at(start + timedelta(days=3)):
        paid.stripe.settle(session.id, succeeded=False)
        _webhook(api, "checkout.session.async_payment_failed", session.id)
        attempt = _attempt(reservation)
        assert (attempt.status, attempt.last_error) == ("FAILED", "async_payment_failed")
        reservation.refresh_from_db()
        assert not reservation.payment_pending
        assert _state(paid, reservation)["state"] == "EXPIRED"
        assert _hold(paid, who="b", key="b1").status == ReservationStatus.HELD
    assert Booking.objects.count() == 0


def test_late_payment_after_the_stall_went_to_someone_else_is_refunded(paid, api):
    start = timezone.now()
    with at(start):
        reservation = _hold(paid)
        _checkout(paid, reservation)
    session = paid.stripe.only_session()
    paid.stripe.expire(session.id)
    with at(start + SESSION + timedelta(seconds=1)):
        _webhook(api, "checkout.session.expired", session.id)
        other = _hold(paid, who="b", key="b1")
        # Stripe later reports the session as paid (e.g. a race at expiry).
        paid.stripe.sessions[session.id] = paid.stripe.sessions[session.id].__class__(
            **{**paid.stripe.sessions[session.id].__dict__, "status": "open"}
        )
        paid.stripe.pay(session.id)
        _webhook(api, "checkout.session.completed", session.id, event_id="evt_late")
    attempt = _attempt(reservation)
    assert (attempt.status, attempt.fulfillment) == ("SUCCEEDED", Fulfillment.UNFULFILLED)
    assert attempt.last_error == "attempt_closed"
    assert Refund.objects.get().status == RefundStatus.PENDING
    assert Reservation.objects.get(pk=other.pk).status == ReservationStatus.HELD
    assert Booking.objects.count() == 0
    assert _state(paid, reservation)["state"] == "REFUND_PENDING"


def test_payment_after_eligibility_was_lost_is_refunded(paid, api):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    moderation_services.create_restriction(
        paid.owner, paid.org.pk, vendor_business_id=paid.business_a.pk, reason="x"
    )
    session = paid.stripe.only_session()
    paid.stripe.pay(session.id)
    _webhook(api, "checkout.session.completed", session.id)
    attempt = _attempt(reservation)
    assert (attempt.fulfillment, attempt.last_error) == (
        Fulfillment.UNFULFILLED,
        "participation_restricted",
    )
    assert Reservation.objects.get(pk=reservation.pk).status == ReservationStatus.EXPIRED
    refund = Refund.objects.get()
    assert paid.stripe.refunds[refund.stripe_refund_id].amount == 2525


def test_cancel_checkout_expires_the_session_then_releases(paid):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    response = paid.client["A"].post(_path(paid, reservation, "/checkout/cancel"))
    assert response.status_code == 200
    assert response.json()["state"] == "RELEASED"
    assert paid.stripe.only_session().status == "expired"
    assert _attempt(reservation).status == AttemptStatus.CANCELED


def test_cancel_after_the_customer_paid_keeps_the_booking(paid):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    paid.stripe.pay(paid.stripe.only_session().id)
    response = paid.client["A"].post(_path(paid, reservation, "/checkout/cancel"))
    assert response.json()["state"] == "BOOKED"
    assert Booking.objects.count() == 1


def test_cancel_with_unknown_outcome_does_not_release(paid):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    paid.stripe.fail("expire_session", "timeout")
    response = paid.client["A"].post(_path(paid, reservation, "/checkout/cancel"))
    assert response.status_code == 503
    assert Reservation.objects.get(pk=reservation.pk).status == ReservationStatus.HELD


def test_return_page_status_never_confirms_from_the_url(paid):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    paid.stripe.pay(paid.stripe.only_session().id)
    url = _path(paid, reservation, "/payment") + "?checkout=returned&paid=true"
    assert paid.client["A"].get(url).json()["state"] == "CHECKOUT_OPEN"
    assert Booking.objects.count() == 0
    # The explicit "check again" asks Stripe and books it.
    check = paid.client["A"].post(_path(paid, reservation, "/payment/check"))
    assert check.json()["state"] == "BOOKED"


# --- Refund recovery ------------------------------------------------------------------------


def _unfulfilled(paid, api):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    session = paid.stripe.only_session()
    paid.stripe.pay(session.id, amount_received=1)
    return reservation, session


def test_refund_timeout_is_retried_with_the_same_key(paid, api):
    reservation, session = _unfulfilled(paid, api)
    paid.stripe.fail("create_refund", "lost")
    _webhook(api, "checkout.session.completed", session.id)
    refund = Refund.objects.get()
    assert refund.status == RefundStatus.REQUESTED and refund.last_error
    call_command("reconcile_payments")
    call_command("reconcile_payments")
    refund.refresh_from_db()
    assert refund.status == RefundStatus.PENDING
    assert len(paid.stripe.refunds) == 1  # the retry got the lost refund back
    paid.stripe.settle_refund(refund.stripe_refund_id)
    _webhook(api, "refund.updated", refund.stripe_refund_id)
    refund.refresh_from_db()
    assert refund.status == RefundStatus.SUCCEEDED
    assert _state(paid, reservation)["state"] == "REFUNDED"
    call_command("reconcile_payments")
    # Created once (response lost), recovered once with the same key; after
    # that reconcile only reads it, and a finished refund is left alone.
    assert paid.stripe.count("create_refund") == 2


def test_refused_refund_needs_an_operator(paid, api, capsys):
    reservation, session = _unfulfilled(paid, api)
    paid.stripe.fail("create_refund", "refuse")
    _webhook(api, "checkout.session.completed", session.id)
    assert Refund.objects.get().status == RefundStatus.FAILED
    assert _state(paid, reservation)["state"] == "REFUND_FAILED"
    call_command("reconcile_payments")
    assert "need an operator" in capsys.readouterr().err
    assert paid.stripe.count("create_refund") == 1


def test_one_compensating_refund_per_payment(paid, api):
    _reservation, session = _unfulfilled(paid, api)
    _webhook(api, "checkout.session.completed", session.id)
    attempt = PaymentAttempt.objects.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        Refund.objects.create(
            attempt=attempt,
            reason="UNFULFILLED",
            amount_minor=1,
            currency="USD",
            idempotency_key="x",
        )


# --- Reconciliation -------------------------------------------------------------------------


def test_reconcile_recovers_a_lost_webhook_and_is_idempotent(paid):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    paid.stripe.pay(paid.stripe.only_session().id)
    PaymentAttempt.objects.update(last_synced_at=timezone.now() - timedelta(minutes=10))
    call_command("reconcile_payments")
    call_command("reconcile_payments")
    assert Booking.objects.count() == 1
    assert _attempt(reservation).fulfillment == Fulfillment.FULFILLED


def test_reconcile_closes_expired_sessions(paid):
    start = timezone.now()
    with at(start):
        reservation = _hold(paid)
        _checkout(paid, reservation)
    paid.stripe.expire(paid.stripe.only_session().id)
    with at(start + SESSION + timedelta(minutes=1)):
        call_command("reconcile_payments")
        assert _attempt(reservation).status == AttemptStatus.EXPIRED
        assert Reservation.objects.get(pk=reservation.pk).status == ReservationStatus.EXPIRED


def test_reconcile_finishes_interrupted_checkout_creation(paid):
    reservation = _hold(paid)
    paid.stripe.fail("create_session", "timeout")
    _checkout(paid, reservation)
    PaymentAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=1))
    call_command("reconcile_payments")
    assert _attempt(reservation).status == AttemptStatus.OPEN


def test_reconcile_retries_unprocessed_events(paid, api):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    session = paid.stripe.only_session()
    paid.stripe.pay(session.id)
    paid.stripe.fail("retrieve_session", "timeout")
    _webhook(api, "checkout.session.completed", session.id)
    StripeEvent.objects.update(received_at=timezone.now() - timedelta(minutes=5))
    PaymentAttempt.objects.update(last_synced_at=timezone.now())
    call_command("reconcile_payments")
    assert StripeEvent.objects.get().processed_at is not None
    assert Booking.objects.count() == 1


# --- Free stalls ----------------------------------------------------------------------------


def test_free_stall_is_booked_without_stripe_idempotently(paid):
    StallOffer.objects.filter(pk=paid.offers[1].pk).update(price_minor=0)
    reservation = _hold(paid, offer=1)
    url = _path(paid, reservation, "/confirm-free")
    assert paid.client["A_MEMBER"].post(url).status_code == 403
    first = paid.client["A"].post(url)
    second = paid.client["A"].post(url)
    assert first.status_code == second.status_code == 200
    assert first.json()["state"] == "BOOKED" and first.json()["payment"] is None
    booking = Booking.objects.get()
    assert (booking.payment_required, booking.payment_attempt, booking.price_minor) == (
        False,
        None,
        0,
    )
    assert paid.stripe.calls == []
    assert _checkout(paid, reservation).json()["error"]["code"] == "already_booked"


def test_free_confirmation_is_refused_for_paid_or_expired_holds(paid):
    reservation = _hold(paid)
    response = paid.client["A"].post(_path(paid, reservation, "/confirm-free"))
    assert response.json()["error"]["code"] == "payment_required"
    StallOffer.objects.filter(pk=paid.offers[1].pk).update(price_minor=0)
    reservation_services.release_hold(paid.vendor_a, paid.business_a.pk, reservation.pk)
    free = _hold(paid, offer=1, key="k2")
    with at(timezone.now() + HOLD):
        response = paid.client["A"].post(_path(paid, free, "/confirm-free"))
        assert response.json()["error"]["code"] == "hold_expired"
        again = _hold(paid, offer=1, key="k3")
        assert _checkout(paid, again).json()["error"]["code"] == "payment_not_required"


# --- Bookings reads -------------------------------------------------------------------------


def _book(paid, api):
    reservation = _hold(paid)
    _checkout(paid, reservation)
    session = paid.stripe.only_session()
    paid.stripe.pay(session.id)
    _webhook(api, "checkout.session.completed", session.id)
    return Booking.objects.get()


def test_vendor_and_organizer_booking_access(paid, api, make_user, as_user):
    booking = _book(paid, api)
    mine = paid.client["A_MEMBER"].get(f"/vendors/{paid.business_a.pk}/bookings").json()
    assert [b["id"] for b in mine["items"]] == [booking.pk]
    assert mine["items"][0]["paid_at"] is not None
    assert paid.client["B"].get(f"/vendors/{paid.business_b.pk}/bookings").json()["items"] == []
    assert (
        paid.client["B"].get(f"/vendors/{paid.business_a.pk}/bookings/{booking.pk}").status_code
        == 404
    )
    org = paid.client["ORG_OWNER"].get(
        f"/organizations/{paid.org.pk}/bookings?occurrence_id={paid.occurrence.pk}"
    )
    assert [b["vendor_business"]["name"] for b in org.json()["items"]] == ["Bees"]
    other_owner = make_user("other-org@example.com")
    other_org = org_services.create_organization(other_owner, name="Elsewhere").organization
    elsewhere = as_user(other_owner)
    assert elsewhere.get(f"/organizations/{other_org.pk}/bookings").json()["items"] == []
    assert elsewhere.get(f"/organizations/{paid.org.pk}/bookings").status_code == 404
    assert paid.client["A"].get(f"/organizations/{paid.org.pk}/bookings").status_code == 404


def test_booking_must_match_its_reservation(paid, api):
    booking = _book(paid, api)
    with pytest.raises(IntegrityError), transaction.atomic():
        Booking.objects.filter(pk=booking.pk).update(offer=paid.offers[2])
    with pytest.raises(IntegrityError), transaction.atomic():
        Booking.objects.create(
            reservation=booking.reservation,
            offer=booking.offer,
            occurrence=booking.occurrence,
            application=booking.application,
            vendor_business=booking.vendor_business,
            price_minor=2500,
            currency="USD",
            payment_required=True,
            payment_attempt=booking.payment_attempt,
            created_at=timezone.now(),
        )


def test_link_stripe_account_command_checks_with_stripe(world, stripe):  # noqa: F811
    call_command("link_stripe_account", world.org.pk, "acct_test123", "--fee-bps", "250")
    account = PaymentAccount.objects.get()
    assert (account.charges_enabled, account.application_fee_bps, account.livemode) == (
        True,
        250,
        False,
    )
    with pytest.raises(Exception, match="didn't confirm"):
        call_command("link_stripe_account", world.org.pk, "acct_missing")


def test_link_stripe_account_requires_the_transfers_capability(world, stripe):  # noqa: F811
    stripe.accounts["acct_pending"] = AccountSnapshot("acct_pending", False, False)
    call_command("link_stripe_account", world.org.pk, "acct_pending")
    assert PaymentAccount.objects.get().charges_enabled is False


def test_reconcile_rechecks_linked_accounts(paid):
    later = timezone.now() + services.ACCOUNT_RECHECK_AFTER
    paid.stripe.accounts["acct_test123"] = AccountSnapshot("acct_test123", False, False)
    with at(later):
        assert services.reconcile()["accounts"] == 1
    assert PaymentAccount.objects.get().charges_enabled is False
    reservation = _hold(paid, key="after-recheck")
    assert _checkout(paid, reservation).json()["error"]["code"] == "payments_unavailable"
    # Recently checked accounts aren't asked again.
    calls = paid.stripe.count("retrieve_account")
    services.reconcile()
    assert paid.stripe.count("retrieve_account") == calls
    # Transfers active again: checkout comes back.
    paid.stripe.accounts["acct_test123"] = AccountSnapshot("acct_test123", True, False)
    with at(later + services.ACCOUNT_RECHECK_AFTER * 2):
        services.reconcile()
    assert PaymentAccount.objects.get().charges_enabled is True


def test_holding_shows_the_fee_added_on_top_before_checkout(paid):
    reservation = _hold(paid)
    quote = _state(paid, reservation)["quote"]
    assert (quote["stall_price_minor"], quote["fee_minor"], quote["total_minor"]) == (
        2500,
        25,
        2525,
    )
    PaymentAccount.objects.update(charges_enabled=False)
    assert _state(paid, reservation)["quote"] is None


def test_attempts_from_before_the_fee_change_keep_their_parameters(paid):
    """A CREATING attempt saved before the change must resend exactly what it
    sent then (one line at the stall price), or its idempotent retry fails."""
    reservation = _hold(paid)
    paid.stripe.fail("create_session", "timeout")
    _checkout(paid, reservation)
    PaymentAttempt.objects.update(fee_on_top=False, amount_minor=2500)
    attempt = _attempt(reservation)
    params = services.session_params(attempt)
    assert [li["price_data"]["unit_amount"] for li in params["line_items"]] == [2500]
    assert attempt.stall_price_minor == 2500
