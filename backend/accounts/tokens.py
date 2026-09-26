"""Signed, expiring tokens for account emails. No custom cryptography.

- Email verification uses django.core.signing (HMAC with SECRET_KEY, its own
  salt and max age). The payload is the user id and the email being verified;
  it is single-use because ``verify_email`` only accepts it while that email
  is still unverified.
- Password reset uses Django's built-in PasswordResetTokenGenerator, which
  expires after PASSWORD_RESET_TIMEOUT and becomes invalid once the password
  (or last login) changes.
"""

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

EMAIL_VERIFICATION_SALT = "vendi.accounts.email-verification"


class TokenExpired(Exception):
    pass


class TokenInvalid(Exception):
    pass


def make_email_verification_token(user) -> str:
    return signing.dumps({"uid": user.pk, "email": user.email}, salt=EMAIL_VERIFICATION_SALT)


def read_email_verification_token(token: str) -> tuple[int, str]:
    try:
        payload = signing.loads(
            token, salt=EMAIL_VERIFICATION_SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE
        )
    except signing.SignatureExpired:
        raise TokenExpired from None
    except signing.BadSignature:
        raise TokenInvalid from None
    if not isinstance(payload, dict) or not isinstance(payload.get("uid"), int):
        raise TokenInvalid
    return payload["uid"], str(payload.get("email", ""))


def make_password_reset_token(user) -> tuple[str, str]:
    """Return (uid, token) for a reset link."""
    return urlsafe_base64_encode(force_bytes(user.pk)), default_token_generator.make_token(user)


def decode_password_reset_uid(uid: str) -> int:
    try:
        return int(force_str(urlsafe_base64_decode(uid)))
    except (TypeError, ValueError, OverflowError):
        raise TokenInvalid from None


def check_password_reset_token(user, token: str) -> bool:
    return default_token_generator.check_token(user, token)
