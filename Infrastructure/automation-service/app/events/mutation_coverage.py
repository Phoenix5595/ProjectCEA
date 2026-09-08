"""Marker and OpenAPI route coverage enforcement for persisted mutations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from inspect import getsourcefile
from typing import Final, ParamSpec, TypeVar

from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.events.mutation_exclusions import (
    MUTATION_ROUTE_EXCLUSIONS,
    MutationHttpMethod,
    MutationRouteExclusion,
)

HandlerParameters = ParamSpec("HandlerParameters")
HandlerResult = TypeVar("HandlerResult")
_MARKER_ATTRIBUTE: Final = "__emits_operational_mutation__"
_MUTATING_METHODS: Final[frozenset[str]] = frozenset(method.value for method in MutationHttpMethod)


@dataclass(frozen=True, slots=True)
class MissingMutationRouteCoverage:
    """One OpenAPI mutation operation that requires an event marker or review."""

    operation_id: str
    source_file: str


@dataclass(frozen=True, slots=True)
class MutationCoverageError(Exception):
    """Raised when a mutating route lacks an auditable event coverage decision."""

    missing: tuple[MissingMutationRouteCoverage, ...]

    def __str__(self) -> str:
        entries = ", ".join(f"{entry.operation_id} ({entry.source_file})" for entry in self.missing)
        return f"uncovered persisted mutation operations: {entries}"


def emits_operational_mutation(
    handler: Callable[HandlerParameters, HandlerResult],
) -> Callable[HandlerParameters, HandlerResult]:
    """Mark a route whose successful persistence calls the mutation emitter."""
    setattr(handler, _MARKER_ATTRIBUTE, True)
    return handler


def assert_mutation_route_coverage(
    app: FastAPI,
    exclusions: Iterable[MutationRouteExclusion] = MUTATION_ROUTE_EXCLUSIONS,
) -> None:
    """Fail unless every OpenAPI write route is marked or explicitly reviewed."""
    missing = tuple(_uncovered_routes(app, tuple(exclusions)))
    if missing:
        raise MutationCoverageError(missing=missing)


def _uncovered_routes(
    app: FastAPI, exclusions: tuple[MutationRouteExclusion, ...]
) -> Iterable[MissingMutationRouteCoverage]:
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if method not in _MUTATING_METHODS:
                continue
            if _is_covered(route, method, exclusions):
                continue
            yield MissingMutationRouteCoverage(
                operation_id=route.operation_id or route.name,
                source_file=getsourcefile(route.endpoint) or "<unknown>",
            )


def _is_covered(
    route: APIRoute, method: str, exclusions: tuple[MutationRouteExclusion, ...]
) -> bool:
    if getattr(route.endpoint, _MARKER_ATTRIBUTE, False) is True:
        return True
    return any(
        exclusion.method.value == method and exclusion.path == route.path
        for exclusion in exclusions
    )


__all__ = [
    "MissingMutationRouteCoverage",
    "MutationCoverageError",
    "assert_mutation_route_coverage",
    "emits_operational_mutation",
]
