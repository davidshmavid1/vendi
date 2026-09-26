"""Account operations. Business rules and transactions live here; HTTP,
sessions and cookies stay in accounts/api.py."""

from dataclasses import dataclass

from django.contrib.auth import authenticate, password_validation
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts import emails, tokens
from accounts.models import User, normalize_email
from core.exceptions import Conflict, InvalidRequest, PermissionDenied


class InvalidCredentials(InvalidRequest):
    default_code = "invalid_credentials"


@dataclass(frozen=True)
class Registration:
    user: User
    verification_email_sent: bool


def _validated_email(raw: str) -> str:
    email = normalize_email(raw)
    try:
        validate_email(email)
    except ValidationError:
        raise InvalidRequest("Enter a valid email address.", code="email_invalid") from None
    return email


def _validate_password(password: str, user: User) -> None:
    try:
        password_validation.validate_password(password, user=user)
    except ValidationError as exc:
        raise InvalidRequest(
            "Choose a stronger password.",
            code="password_invalid",
            details=[{"msg": message} for message in exc.messages],
        ) from None


def register(email: str, password: str) -> Registration:
    """Create an unverified account and send the verification email.

    The account is committed before the email is sent, so a send failure
    leaves a recoverable account: the person can request another email.
    """
    email = _validated_email(email)
    _validate_password(password, User(email=email))
    try:
        with transaction.atomic():
            user = User.objects.create_user(email=email, password=password)
    except IntegrityError:
        # The unique index is the final word, including concurrent sign-ups.
        raise Conflict("An account with this email already exists.", code="email_taken") from None
    return Registration(user=user, verification_email_sent=emails.send_verification_email(user))


def verify_email(token: str) -> User:
    try:
        user_id, email = tokens.read_email_verification_token(token)
    except tokens.TokenExpired:
        raise InvalidRequest(
            "This verification link has expired. Request a new one.",
            code="verification_token_expired",
        ) from None
    except tokens.TokenInvalid:
        raise _invalid_verification_token() from None

    with transaction.atomic():
        # Lock the row so two uses of the same link cannot both succeed.
        user = User.objects.select_for_update().filter(pk=user_id, is_active=True).first()
        if user is None or user.email != email or user.is_email_verified:
            raise _invalid_verification_token()
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
    return user


def _invalid_verification_token() -> InvalidRequest:
    return InvalidRequest(
        "This verification link is invalid or has already been used.",
        code="verification_token_invalid",
    )


def resend_verification(email: str) -> None:
    """Send a fresh link if an active, unverified account uses this email.
    Says nothing about whether it does (the API response is always the same)."""
    user = User.objects.filter(
        email=normalize_email(email), is_active=True, email_verified_at__isnull=True
    ).first()
    if user is not None:
        emails.send_verification_email(user)


def authenticate_credentials(email: str, password: str) -> User:
    """Return the user for valid credentials of an active, verified account.

    Wrong email, wrong password and deactivated accounts all produce the same
    error. The unverified message is only shown with the correct password.
    """
    user = authenticate(None, username=normalize_email(email), password=password)
    if user is None:
        raise InvalidCredentials("Email or password is incorrect.")
    if not user.is_email_verified:
        raise PermissionDenied(
            "Confirm your email address before logging in.", code="email_not_verified"
        )
    return user


def request_password_reset(email: str) -> None:
    """Email a reset link to an active account with this email, if any."""
    user = User.objects.filter(email=normalize_email(email), is_active=True).first()
    if user is not None:
        emails.send_password_reset_email(user)


def reset_password(uid: str, token: str, new_password: str) -> User:
    """Set a new password from a reset link. Changing the password invalidates
    the token and every existing session of the account."""
    invalid = InvalidRequest(
        "This reset link is invalid or has expired. Request a new one.",
        code="reset_token_invalid",
    )
    try:
        user_id = tokens.decode_password_reset_uid(uid)
    except tokens.TokenInvalid:
        raise invalid from None

    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=user_id, is_active=True).first()
        if user is None or not tokens.check_password_reset_token(user, token):
            raise invalid
        _validate_password(new_password, user)
        user.set_password(new_password)
        user.save(update_fields=["password"])
    return user


def change_password(user: User, current_password: str, new_password: str) -> None:
    if not user.check_password(current_password):
        raise InvalidRequest("Current password is incorrect.", code="current_password_incorrect")
    _validate_password(new_password, user)
    user.set_password(new_password)
    user.save(update_fields=["password"])
