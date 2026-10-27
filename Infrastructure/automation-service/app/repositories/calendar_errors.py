"""Calendar repository exceptions shared by the repository, its sync mixin, routes and the mode scheduler."""

from __future__ import annotations

from typing import Any


class CalendarMutationError(Exception):
    """A deterministic Calendar/lifecycle mutation rejection."""

    def __init__(self, code: str, status_code: int = 409, detail: Any = None) -> None:
        self.code = code
        self.status_code = status_code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


class GrowPlanCurrentRunAmbiguousError(Exception):
    """Multiple canonical grow-plan groups span today in the same room."""
