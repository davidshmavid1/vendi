"""Account emails, sent through Django's configured EMAIL_BACKEND.

Links are built from settings.FRONTEND_BASE_URL (trusted configuration),
never from request headers or client-supplied URLs. Sending is synchronous
and best-effort: callers learn whether the backend accepted the message, not
whether it was delivered. Failures are logged without the message body, so
tokens never reach the logs.
"""

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import send_mail

from accounts import tokens

logger = logging.getLogger(__name__)


def _link(path: str, **params: str) -> str:
    return f"{settings.FRONTEND_BASE_URL.rstrip('/')}{path}?{urlencode(params)}"


def _send(user, *, kind: str, subject: str, body: str) -> bool:
    try:
        send_mail(subject, body, None, [user.email], fail_silently=False)
    except Exception as exc:  # noqa: BLE001 - any backend failure is reported, not raised
        logger.error("Failed to send %s email to user %s (%s)", kind, user.pk, type(exc).__name__)
        return False
    return True


def send_verification_email(user) -> bool:
    link = _link("/verify-email", token=tokens.make_email_verification_token(user))
    hours = settings.EMAIL_VERIFICATION_MAX_AGE // 3600
    body = (
        "Welcome to Vendi.\n\n"
        f"Confirm your email address by opening this link within {hours} hours:\n\n"
        f"{link}\n\n"
        "If you did not create an account, you can ignore this email."
    )
    return _send(user, kind="verification", subject="Confirm your Vendi email", body=body)


def send_password_reset_email(user) -> bool:
    uid, token = tokens.make_password_reset_token(user)
    link = _link("/reset-password", uid=uid, token=token)
    minutes = settings.PASSWORD_RESET_TIMEOUT // 60
    body = (
        "Someone asked to reset the password for your Vendi account.\n\n"
        f"Choose a new password within {minutes} minutes:\n\n"
        f"{link}\n\n"
        "If this wasn't you, ignore this email; your password is unchanged."
    )
    return _send(user, kind="password reset", subject="Reset your Vendi password", body=body)
