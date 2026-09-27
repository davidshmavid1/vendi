"""An in-memory Stripe for tests, behind the same interface as
payments.gateway.StripeGateway.

It keeps Stripe's semantics that the payment flow relies on:
- create calls are idempotent per key (same key -> same object; different
  parameters with a reused key -> idempotency error);
- failures can be injected per method: ``"timeout"`` (unknown outcome, the
  call never reached Stripe), ``"lost"`` (Stripe did it, the response was
  lost) or ``"refuse"`` (definitive refusal);
- webhook signatures are checked by the real verification code.
"""

import hashlib
import hmac
import json
import time
from dataclasses import replace
from datetime import UTC, datetime

from payments.gateway import (
    AccountSnapshot,
    IntentSnapshot,
    ProviderError,
    RefundSnapshot,
    SessionSnapshot,
    StripeGateway,
)

WEBHOOK_SECRET = "whsec_test_secret"


class FakeStripe:
    def __init__(self):
        self.sessions: dict[str, SessionSnapshot] = {}
        self.session_params: dict[str, dict] = {}
        self.intents: dict[str, IntentSnapshot] = {}
        self.refunds: dict[str, RefundSnapshot] = {}
        self.by_key: dict[str, tuple[str, dict]] = {}
        self.calls: list[str] = []
        self.failures: dict[str, list[str]] = {}
        self.refund_status = "pending"
        self.accounts = {"acct_test123": AccountSnapshot("acct_test123", True, False)}
        self._verifier = StripeGateway("", WEBHOOK_SECRET)
        self._n = 0

    # -- test controls -------------------------------------------------------------------

    def fail(self, method: str, *modes: str):
        self.failures.setdefault(method, []).extend(modes)

    def _next_failure(self, method):
        queue = self.failures.get(method)
        return queue.pop(0) if queue else None

    def _id(self, prefix):
        self._n += 1
        return f"{prefix}_test_{self._n}"

    def pay(self, session_id: str, **intent_overrides) -> str:
        """The customer pays (Stripe-side)."""
        session = self.sessions[session_id]
        params = self.session_params[session_id]
        intent_data = params["payment_intent_data"]
        intent = IntentSnapshot(
            id=self._id("pi"),
            status="succeeded",
            amount=session.amount_total,
            amount_received=session.amount_total,
            currency=session.currency,
            livemode=False,
            destination=intent_data["transfer_data"]["destination"],
            application_fee_amount=intent_data.get("application_fee_amount"),
        )
        intent = replace(intent, **intent_overrides)
        self.intents[intent.id] = intent
        self.sessions[session_id] = replace(
            session, status="complete", payment_status="paid", payment_intent_id=intent.id
        )
        return intent.id

    def pay_later(self, session_id: str) -> str:
        """The customer completes checkout with a delayed payment method: the
        session completes unpaid and its PaymentIntent is processing."""
        intent_id = self.pay(session_id, status="processing", amount_received=0)
        self.sessions[session_id] = replace(self.sessions[session_id], payment_status="unpaid")
        return intent_id

    def settle(self, session_id: str, *, succeeded: bool):
        """A delayed payment succeeds or fails (Stripe-side)."""
        session = self.sessions[session_id]
        intent = self.intents[session.payment_intent_id]
        if succeeded:
            self.intents[intent.id] = replace(
                intent, status="succeeded", amount_received=intent.amount
            )
            self.sessions[session_id] = replace(session, payment_status="paid")
        else:
            self.intents[intent.id] = replace(intent, status="requires_payment_method")

    def expire(self, session_id: str):
        """The session times out (Stripe-side)."""
        self.sessions[session_id] = replace(self.sessions[session_id], status="expired")

    def only_session(self) -> SessionSnapshot:
        assert len(self.sessions) == 1, self.sessions
        return next(iter(self.sessions.values()))

    def count(self, call: str) -> int:
        return self.calls.count(call)

    # -- gateway interface ---------------------------------------------------------------

    def key_livemode(self) -> bool:
        return False

    def is_configured(self) -> bool:
        return True

    def _around(self, method, do):
        self.calls.append(method)
        mode = self._next_failure(method)
        if mode == "timeout":
            raise ProviderError("stripe_APIConnectionError", definitive=False)
        if mode == "refuse":
            raise ProviderError("stripe_invalid_request", definitive=True)
        result = do()
        if mode == "lost":
            raise ProviderError("stripe_APIConnectionError", definitive=False)
        return result

    def create_checkout_session(self, params, *, idempotency_key):
        def do():
            if idempotency_key in self.by_key:
                session_id, original = self.by_key[idempotency_key]
                if original != params:
                    raise ProviderError("idempotency_error", definitive=False)
                return self.sessions[session_id]
            item = params["line_items"][0]["price_data"]
            session = SessionSnapshot(
                id=self._id("cs"),
                status="open",
                payment_status="unpaid",
                amount_total=sum(
                    li["price_data"]["unit_amount"] * li["quantity"] for li in params["line_items"]
                ),
                currency=item["currency"].upper(),
                livemode=False,
                url=f"https://checkout.stripe.test/{self._n}",
                payment_intent_id=None,
                client_reference_id=params["client_reference_id"],
                expires_at=datetime.fromtimestamp(params["expires_at"], tz=UTC),
            )
            self.sessions[session.id] = session
            self.session_params[session.id] = params
            self.by_key[idempotency_key] = (session.id, params)
            return session

        return self._around("create_session", do)

    def retrieve_session(self, session_id):
        return self._around("retrieve_session", lambda: self.sessions[session_id])

    def expire_session(self, session_id):
        def do():
            session = self.sessions[session_id]
            if session.status != "open":
                raise ProviderError("stripe_checkout_session_not_open", definitive=True)
            self.expire(session_id)
            return self.sessions[session_id]

        return self._around("expire_session", do)

    def retrieve_payment_intent(self, intent_id):
        return self._around("retrieve_intent", lambda: self.intents[intent_id])

    def create_refund(self, *, payment_intent_id, amount, idempotency_key):
        def do():
            if idempotency_key in self.by_key:
                return self.refunds[self.by_key[idempotency_key][0]]
            refund = RefundSnapshot(self._id("re"), self.refund_status, amount, payment_intent_id)
            self.refunds[refund.id] = refund
            self.by_key[idempotency_key] = (refund.id, {})
            return refund

        return self._around("create_refund", do)

    def retrieve_refund(self, refund_id):
        return self._around("retrieve_refund", lambda: self.refunds[refund_id])

    def settle_refund(self, refund_id, status="succeeded"):
        self.refunds[refund_id] = replace(self.refunds[refund_id], status=status)

    def retrieve_account(self, account_id):
        def do():
            if account_id not in self.accounts:
                raise ProviderError("stripe_account_invalid", definitive=True)
            return self.accounts[account_id]

        return self._around("retrieve_account", do)

    def verify_webhook(self, payload, signature):
        return self._verifier.verify_webhook(payload, signature)


def signed_event(event_type: str, object_id: str, *, event_id=None, account=None, secret=None):
    """A webhook body and a valid Stripe-Signature header for it."""
    body = json.dumps(
        {
            "id": event_id or f"evt_{object_id}_{event_type}",
            "object": "event",
            "type": event_type,
            "livemode": False,
            "created": int(time.time()),
            "account": account,
            "data": {"object": {"id": object_id, "object": "checkout.session"}},
        }
    )
    timestamp = int(time.time())
    digest = hmac.new(
        (secret or WEBHOOK_SECRET).encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return body, f"t={timestamp},v1={digest}"
