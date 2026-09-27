"""Rate limits for authentication endpoints, built on Django Ninja's
SimpleRateThrottle (sliding window stored in Django's cache).

Rates come from settings.AUTH_RATE_LIMITS. Exceeding one returns 429 with a
Retry-After header. Limits expire on their own; there is no permanent lockout.
"""

import hashlib
import json

from django.conf import settings
from ninja.throttling import SimpleRateThrottle

from accounts.models import normalize_email


class ClientIPThrottle(SimpleRateThrottle):
    """Per client IP (REMOTE_ADDR, or X-Forwarded-For per NINJA_NUM_PROXIES)."""

    def __init__(self, scope: str):
        self.scope = scope
        super().__init__(settings.AUTH_RATE_LIMITS[scope])

    def get_cache_key(self, request):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class EmailThrottle(ClientIPThrottle):
    """Per normalized email in the JSON body, so one account's attempts are
    limited even when spread across many IPs. The cache key stores a hash, not
    the address."""

    def get_cache_key(self, request):
        try:
            email = json.loads(request.body).get("email")
        except (ValueError, AttributeError):
            return None
        if not isinstance(email, str) or not email.strip():
            return None
        digest = hashlib.sha256(normalize_email(email).encode()).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class UserThrottle(ClientIPThrottle):
    """Per authenticated user (Ninja runs authentication before throttles)."""

    def __init__(self, scope: str, rates: dict):
        self.scope = scope
        SimpleRateThrottle.__init__(self, rates[scope])

    def get_cache_key(self, request):
        user = getattr(request, "auth", None)
        if user is None or not getattr(user, "pk", None):
            return None
        return self.cache_format % {"scope": self.scope, "ident": f"user-{user.pk}"}
