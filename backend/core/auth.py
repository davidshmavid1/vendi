"""Browser session authentication and CSRF enforcement for the API.

Django Ninja marks every API view csrf_exempt, so Django's CsrfViewMiddleware
does not protect Ninja routes. CSRF is checked only (a) by Ninja's cookie
auth classes, for authenticated endpoints, and (b) by ``enforce_csrf``,
which every state-changing endpoint without session auth (login, register,
password reset, ...) must call first.
"""

from ninja.security import SessionAuth
from ninja.utils import check_csrf

from core.exceptions import PermissionDenied

CSRF_FAILED = "csrf_failed"
_CSRF_MESSAGE = "CSRF check failed. Fetch a token from /api/v1/auth/csrf and resend."


def enforce_csrf(request) -> None:
    """Reject unsafe requests without a valid CSRF token (no-op for GET/HEAD)."""
    if check_csrf(request) is not None:
        raise PermissionDenied(_CSRF_MESSAGE, code=CSRF_FAILED)


class SessionUserAuth(SessionAuth):
    """Django session auth with CSRF checked on unsafe methods.

    Resolves to ``request.user`` only for an active, logged-in user: Django's
    AuthenticationMiddleware already drops sessions of deactivated users and
    sessions invalidated by a password change.
    """

    def _get_key(self, request):
        enforce_csrf(request)
        return request.COOKIES.get(self.param_name)


session_auth = SessionUserAuth()
