"""Operational endpoints used by load balancers, orchestrators and uptime checks."""

import logging

from django.db import DatabaseError, connection
from ninja import Router, Status

from core.schemas import LivenessOut, ReadinessOut

logger = logging.getLogger(__name__)

router = Router(tags=["health"])


@router.get("/live", response={200: LivenessOut}, summary="Liveness")
def live(request):
    """The process is up and can answer requests. Never touches the database."""
    return Status(200, {"status": "ok"})


@router.get("/ready", response={200: ReadinessOut, 503: ReadinessOut}, summary="Readiness")
def ready(request):
    """The app can serve traffic: the database accepts a trivial query."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError as exc:
        # Log only the exception type: driver messages can include host/user.
        logger.warning("Readiness check failed: database unavailable (%s)", type(exc).__name__)
        return Status(503, {"status": "unavailable", "checks": {"database": "unavailable"}})
    return Status(200, {"status": "ok", "checks": {"database": "ok"}})
