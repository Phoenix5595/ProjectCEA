"""Shared fakes for sensor-registry backend tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class FakeConnection:
    """Minimal asyncpg-connection-shaped fake.

    Matchers are permanent (first-match wins in registration order): each
    registered ``(predicate, handler)`` answers every matching statement by
    calling ``handler(query, *args)``. Statements are recorded for
    assertions so tests can verify the exact mutation sequence.
    """

    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self._matchers: list[tuple[Callable[[str], bool], Callable[..., Any]]] = []

    def respond_to(self, predicate: Callable[[str], bool], handler: Callable[..., Any]) -> None:
        self._matchers.append((predicate, handler))

    def _result(self, query: str, *args: Any) -> Any:
        for predicate, handler in self._matchers:
            if predicate(query):
                return handler(query, *args)
        raise AssertionError(f"Unexpected statement: {query!r} with {args!r}")

    async def fetch(self, query: str, *args: Any) -> Any:
        self.statements.append((query, args))
        return self._result(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> Any:
        self.statements.append((query, args))
        return self._result(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.statements.append((query, args))
        return self._result(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        self.statements.append((query, args))
        self._result(query, *args)
        return "OK"

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()


class FakePool:
    """Pool-shaped fake whose connections share one scripted FakeConnection."""

    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection
        self.acquire_calls: int = 0

    def acquire(self) -> Any:
        self.acquire_calls += 1
        connection = self._connection

        class _Acquire:
            async def __aenter__(self) -> FakeConnection:
                return connection

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                return None

        return _Acquire()
