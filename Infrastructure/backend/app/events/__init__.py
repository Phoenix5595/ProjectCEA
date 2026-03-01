"""Backend event consumption for cross-service config propagation."""

from __future__ import annotations

from app.events.consumer import ConfigEventConsumer

__all__ = ["ConfigEventConsumer"]
