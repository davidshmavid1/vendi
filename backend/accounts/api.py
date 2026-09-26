"""Account endpoints under /api/v1/auth/. Thin: check CSRF, call one
operation in accounts/services.py, manage the session, return a schema."""

from django.contrib.auth import login, logout, update_session_auth_hash
from django.middleware.csrf import get_token
from ninja import Router, Status

from accounts import services
from accounts.schemas import (
    AccountOut,
    CsrfOut,
    EmailIn,
    LoginIn,
    MessageOut,
    PasswordChangeIn,
    PasswordResetConfirmIn,
    RegisterIn,
    RegisterOut,
    VerifyEmailIn,
)
from accounts.throttles import ClientIPThrottle, EmailThrottle
from core.auth import enforce_csrf, session_auth
from core.schemas import ErrorOut

router = Router(tags=["auth"])

_ERRORS = {400: ErrorOut, 403: ErrorOut, 422: ErrorOut, 429: ErrorOut}
_GENERIC_EMAIL_SENT = (
    "If an account matches that email, we've sent a message to it. Check your inbox."
)


@router.get("/csrf", response={200: CsrfOut})
def csrf(request):
    """Issue a CSRF token (and set the CSRF cookie). Send it back in the
    X-CSRFToken header on every POST. Fetch a new one after login/logout."""
    return {"csrf_token": get_token(request)}


@router.post(
    "/register",
    response={201: RegisterOut, 409: ErrorOut, **_ERRORS},
    throttle=[ClientIPThrottle("register_ip")],
)
def register(request, payload: RegisterIn):
    """Create an unverified account and email a verification link. Does not log in."""
    enforce_csrf(request)
    result = services.register(payload.email, payload.password)
    return Status(
        201,
        {"account": result.user, "verification_email_sent": result.verification_email_sent},
    )


@router.post("/verify-email", response={200: AccountOut, **_ERRORS})
def verify_email(request, payload: VerifyEmailIn):
    """Mark the email verified. Does not log in; the person logs in next."""
    enforce_csrf(request)
    return services.verify_email(payload.token)


@router.post(
    "/resend-verification",
    response={202: MessageOut, **_ERRORS},
    throttle=[
        ClientIPThrottle("resend_verification_ip"),
        EmailThrottle("resend_verification_email"),
    ],
)
def resend_verification(request, payload: EmailIn):
    enforce_csrf(request)
    services.resend_verification(payload.email)
    return Status(202, {"message": _GENERIC_EMAIL_SENT})


@router.post(
    "/login",
    response={200: AccountOut, **_ERRORS},
    throttle=[ClientIPThrottle("login_ip"), EmailThrottle("login_email")],
)
def login_view(request, payload: LoginIn):
    """Start a session. Rotates the session id and the CSRF token."""
    enforce_csrf(request)
    user = services.authenticate_credentials(payload.email, payload.password)
    login(request, user)
    return user


@router.get("/me", response={200: AccountOut, 401: ErrorOut}, auth=session_auth)
def me(request):
    return request.auth


@router.post("/logout", response={204: None, 403: ErrorOut})
def logout_view(request):
    """End the session (idempotent). Deletes the server-side session data."""
    enforce_csrf(request)
    logout(request)
    return Status(204, None)


@router.post(
    "/password-reset/request",
    response={202: MessageOut, **_ERRORS},
    throttle=[ClientIPThrottle("password_reset_ip"), EmailThrottle("password_reset_email")],
)
def password_reset_request(request, payload: EmailIn):
    enforce_csrf(request)
    services.request_password_reset(payload.email)
    return Status(202, {"message": _GENERIC_EMAIL_SENT})


@router.post("/password-reset/confirm", response={204: None, **_ERRORS})
def password_reset_confirm(request, payload: PasswordResetConfirmIn):
    """Set a new password. Logs out every session; does not log in."""
    enforce_csrf(request)
    services.reset_password(payload.uid, payload.token, payload.new_password)
    return Status(204, None)


@router.post(
    "/password/change",
    response={204: None, 401: ErrorOut, **_ERRORS},
    auth=session_auth,
)
def password_change(request, payload: PasswordChangeIn):
    """Change the password. Keeps this session; other sessions are logged out."""
    services.change_password(request.auth, payload.current_password, payload.new_password)
    update_session_auth_hash(request, request.auth)
    return Status(204, None)
