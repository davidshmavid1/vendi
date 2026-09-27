"""The integration boundary with Stripe. Nothing else imports ``stripe``.

Every call returns a small snapshot of the fields Vendi uses, and every
failure becomes a ``ProviderError`` that says whether the outcome is known:

- ``definitive=True``: Stripe answered and refused (e.g. invalid request);
  nothing was created.
- ``definitive=False``: timeout, connection or server error, rate limit, a
  concurrent request with the same idempotency key, or bad credentials. The
  operation may or may not have happened; retry it with the same
  idempotency key.

Tests replace the gateway with a fake (``payments.services.set_gateway``).
Live keys are refused unless ``STRIPE_ALLOW_LIVE`` is set, so this code can't
move real money by accident.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from django.conf import settings

from core.exceptions import Conflict


class ProviderError(Exception):
    def __init__(self, code: str, *, definitive: bool):
        super().__init__(code)
        self.code = code
        self.definitive = definitive


def payments_unavailable() -> Conflict:
    return Conflict("Online payments aren't available right now.", code="payments_unavailable")


@dataclass(frozen=True)
class SessionSnapshot:
    id: str
    status: str  # open, complete, expired
    payment_status: str  # paid, unpaid, no_payment_required
    amount_total: int | None
    currency: str | None  # upper case
    livemode: bool
    url: str | None
    payment_intent_id: str | None
    client_reference_id: str | None
    expires_at: datetime | None


@dataclass(frozen=True)
class IntentSnapshot:
    id: str
    status: str
    amount: int
    amount_received: int
    currency: str  # upper case
    livemode: bool
    destination: str | None
    application_fee_amount: int | None


@dataclass(frozen=True)
class RefundSnapshot:
    id: str
    status: str  # pending, requires_action, succeeded, failed, canceled
    amount: int
    payment_intent_id: str | None


@dataclass(frozen=True)
class EventSnapshot:
    id: str
    type: str
    livemode: bool
    account: str | None
    object_id: str | None
    created: datetime


@dataclass(frozen=True)
class AccountSnapshot:
    id: str
    # Destination charges need the connected account's ``transfers``
    # capability; ``charges_enabled`` is about charging on that account.
    transfers_active: bool
    livemode: bool | None


def _time(value) -> datetime | None:
    return datetime.fromtimestamp(value, tz=UTC) if value else None


def _plain(value) -> dict:
    """SDK objects (stripe-python 15+) aren't dicts: convert at the boundary."""
    return value.to_dict() if hasattr(value, "to_dict") else value


def _id(value) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return value.get("id")


class StripeGateway:
    def __init__(self, secret_key: str, webhook_secret: str):
        import stripe

        self._stripe = stripe
        self._webhook_secret = webhook_secret
        self._client = (
            stripe.StripeClient(
                secret_key,
                max_network_retries=2,
                http_client=stripe.RequestsClient(timeout=20),
            )
            if secret_key
            else None
        )

    def is_configured(self) -> bool:
        return self._client is not None

    # -- helpers -------------------------------------------------------------------------

    def _call(self, fn, *args, **kwargs):
        if self._client is None:
            raise ProviderError("provider_not_configured", definitive=False)
        stripe = self._stripe
        try:
            return fn(*args, **kwargs)
        except stripe.IdempotencyError as error:
            raise ProviderError("idempotency_error", definitive=False) from error
        except (stripe.InvalidRequestError, stripe.CardError, stripe.PermissionError) as error:
            code = getattr(error, "code", None) or type(error).__name__
            raise ProviderError(f"stripe_{code}"[:100], definitive=True) from error
        except stripe.AuthenticationError as error:
            raise ProviderError("provider_auth_failed", definitive=False) from error
        except stripe.StripeError as error:  # connection, API (5xx), rate limit, ...
            raise ProviderError(f"stripe_{type(error).__name__}", definitive=False) from error

    @staticmethod
    def _session(s) -> SessionSnapshot:
        s = _plain(s)
        return SessionSnapshot(
            id=s["id"],
            status=s.get("status"),
            payment_status=s.get("payment_status"),
            amount_total=s.get("amount_total"),
            currency=(s.get("currency") or "").upper() or None,
            livemode=bool(s.get("livemode")),
            url=s.get("url"),
            payment_intent_id=_id(s.get("payment_intent")),
            client_reference_id=s.get("client_reference_id"),
            expires_at=_time(s.get("expires_at")),
        )

    @staticmethod
    def _refund(r) -> RefundSnapshot:
        r = _plain(r)
        return RefundSnapshot(
            id=r["id"],
            status=r.get("status"),
            amount=r.get("amount"),
            payment_intent_id=_id(r.get("payment_intent")),
        )

    # -- Checkout ------------------------------------------------------------------------

    def create_checkout_session(self, params: dict, *, idempotency_key: str) -> SessionSnapshot:
        session = self._call(
            lambda: self._client.v1.checkout.sessions.create(
                params=params, options={"idempotency_key": idempotency_key}
            )
        )
        return self._session(session)

    def retrieve_session(self, session_id: str) -> SessionSnapshot:
        return self._session(
            self._call(lambda: self._client.v1.checkout.sessions.retrieve(session_id))
        )

    def expire_session(self, session_id: str) -> SessionSnapshot:
        return self._session(
            self._call(lambda: self._client.v1.checkout.sessions.expire(session_id))
        )

    def retrieve_payment_intent(self, intent_id: str) -> IntentSnapshot:
        pi = _plain(self._call(lambda: self._client.v1.payment_intents.retrieve(intent_id)))
        transfer = pi.get("transfer_data") or {}
        return IntentSnapshot(
            id=pi["id"],
            status=pi.get("status"),
            amount=pi.get("amount"),
            amount_received=pi.get("amount_received") or 0,
            currency=(pi.get("currency") or "").upper(),
            livemode=bool(pi.get("livemode")),
            destination=_id(transfer.get("destination")),
            application_fee_amount=pi.get("application_fee_amount"),
        )

    # -- Refunds -------------------------------------------------------------------------

    def create_refund(
        self, *, payment_intent_id: str, amount: int, idempotency_key: str
    ) -> RefundSnapshot:
        # Destination charge: take the money back from the organizer's
        # account and return the platform fee too, so nobody keeps any of it.
        params = {
            "payment_intent": payment_intent_id,
            "amount": amount,
            "reverse_transfer": True,
            "refund_application_fee": True,
            "metadata": {"vendi_reason": "unfulfilled"},
        }
        refund = self._call(
            lambda: self._client.v1.refunds.create(
                params=params, options={"idempotency_key": idempotency_key}
            )
        )
        return self._refund(refund)

    def retrieve_refund(self, refund_id: str) -> RefundSnapshot:
        return self._refund(self._call(lambda: self._client.v1.refunds.retrieve(refund_id)))

    # -- Accounts, webhooks --------------------------------------------------------------

    def retrieve_account(self, account_id: str) -> AccountSnapshot:
        account = _plain(self._call(lambda: self._client.v1.accounts.retrieve(account_id)))
        capabilities = account.get("capabilities") or {}
        return AccountSnapshot(
            id=account["id"],
            transfers_active=capabilities.get("transfers") == "active",
            livemode=account.get("livemode"),
        )

    def key_livemode(self) -> bool:
        return settings.STRIPE_SECRET_KEY.startswith(("sk_live_", "rk_live_"))

    def verify_webhook(self, payload: bytes, signature: str | None) -> EventSnapshot:
        """Check the Stripe-Signature header against the raw body with the
        endpoint's signing secret. Raises ProviderError("invalid_signature")."""
        if not self._webhook_secret:
            raise ProviderError("webhook_not_configured", definitive=True)
        stripe = self._stripe
        try:
            event = stripe.Webhook.construct_event(payload, signature, self._webhook_secret)
        except (ValueError, stripe.SignatureVerificationError) as error:
            raise ProviderError("invalid_signature", definitive=True) from error
        event = _plain(event)
        obj = event["data"]["object"]
        return EventSnapshot(
            id=event["id"],
            type=event["type"],
            livemode=bool(event.get("livemode")),
            account=event.get("account"),
            object_id=obj.get("id") if isinstance(obj, dict) else None,
            created=_time(event["created"]),
        )


def build_gateway() -> StripeGateway:
    key = settings.STRIPE_SECRET_KEY
    if key.startswith(("sk_live_", "rk_live_")) and not settings.STRIPE_ALLOW_LIVE:
        # Never move real money unless a deployment opts in explicitly.
        return StripeGateway("", settings.STRIPE_WEBHOOK_SECRET)
    return StripeGateway(key, settings.STRIPE_WEBHOOK_SECRET)
