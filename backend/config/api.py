"""Composition root for the versioned HTTP API, mounted at /api/v1/.

Each domain contributes a Router; add new ones here with ``api.add_router``.
"""

from django.conf import settings
from ninja import NinjaAPI

from accounts.api import router as auth_router
from core.api import router as health_router
from core.errors import install_exception_handlers
from markets.api import public_router as public_markets_router
from markets.api import router as markets_router
from moderation.api import router as moderation_router
from organizations.api import invitations_router
from organizations.api import router as organizations_router
from vendors.api import invitations_router as vendor_invitations_router
from vendors.api import router as vendors_router

api = NinjaAPI(
    title="Vendi API",
    version="1.0.0",
    urls_namespace="api-v1",
    docs_url="/docs" if settings.API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if settings.API_DOCS_ENABLED else None,
)
install_exception_handlers(api)

api.add_router("/health", health_router)
api.add_router("/auth", auth_router)
api.add_router("/organizations", organizations_router)
api.add_router("/organizations", moderation_router)
api.add_router("/organizations", markets_router)
api.add_router("/public", public_markets_router)
api.add_router("/invitations", invitations_router)
api.add_router("/vendors", vendors_router)
api.add_router("/vendor-invitations", vendor_invitations_router)
