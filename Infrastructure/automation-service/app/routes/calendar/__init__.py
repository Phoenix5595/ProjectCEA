"""Grow calendar API under /api/calendar: core endpoints, then Nextcloud sync endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.routes.calendar.core import router as core_router
from app.routes.calendar.sync import router as sync_router

router = APIRouter(prefix="/api/calendar", tags=["calendar"])
# Include order is route order: every core endpoint, then the sync endpoints.
router.include_router(core_router)
router.include_router(sync_router)
