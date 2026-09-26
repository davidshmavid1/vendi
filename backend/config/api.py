"""Composition root for the versioned HTTP API, mounted at /api/v1/.

Each domain contributes a Router; add new ones here with ``api.add_router``.
"""

from django.conf import settings
from ninja import NinjaAPI

from core.api import router as health_router
from core.errors import install_exception_handlers

api = NinjaAPI(
    title="Vendi API",
    version="1.0.0",
    urls_namespace="api-v1",
    docs_url="/docs" if settings.API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if settings.API_DOCS_ENABLED else None,
)
install_exception_handlers(api)

api.add_router("/health", health_router)
