"""Async retry helper with exponential backoff and jitter.

A deliberately small implementation so we don't drag in `tenacity` for one
or two call sites. Use `retry_async()` directly, or the `@retry_async_call`
decorator for convenience.

Design rules:
- Only retry exceptions the caller explicitly opts into via ``retry_on=``.
  Everything else propagates immediately (so a 4xx never gets retried just
  because a 5xx might).
- Exponential backoff with full jitter ("AWS-style"): the wait before
  attempt N is a uniform random in [0, base * 2**(N-1)], capped at
  ``max_delay``. This avoids thundering-herd on a recovering upstream.
- Each retry is logged at WARNING with attempt number, the exception type,
  and the chosen sleep. The final failure is logged at ERROR by the
  caller (we re-raise; we don't swallow).
- Cooperative cancellation: if the caller's task is cancelled while we're
  asleep between attempts, the CancelledError propagates without being
  retried.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import random
from typing import TypeVar

from .infra_logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    retry_on: tuple[type[BaseException], ...],
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    label: str = "",
) -> T:
    """Run ``fn`` up to ``max_attempts`` times, retrying on listed exceptions.

    Args:
        fn: A zero-argument async callable. Wrap your real call in a
            ``lambda: client.get(url)`` if it takes args.
        retry_on: Tuple of exception types that should trigger a retry.
            Anything not in this tuple propagates on the first occurrence.
        max_attempts: Total attempts including the first one. Must be >= 1.
        base_delay: Seconds. The first retry waits in [0, base_delay].
        max_delay: Seconds. Upper bound on any single sleep.
        label: Optional human-readable name for log lines (e.g. ``"weather METAR"``).

    Returns:
        Whatever ``fn`` returns on its first successful attempt.

    Raises:
        Whatever ``fn`` raises on its final attempt, or any exception not in
        ``retry_on`` from any attempt.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    if not retry_on:
        # Defensive: an empty tuple silently turns this into "no retry",
        # which is almost certainly a caller bug.
        raise ValueError("retry_on must list at least one exception type")

    tag = f"[{label}] " if label else ""
    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except retry_on as e:
            last_exc = e
            if attempt >= max_attempts:
                # Final failure - re-raise so caller decides how to log/handle.
                raise
            # Full-jitter exponential backoff: random in [0, base * 2**(attempt-1)].
            cap = min(max_delay, base_delay * (2 ** (attempt - 1)))
            sleep_for = random.uniform(0, cap)
            logger.warning(
                "%sattempt %d/%d failed (%s: %s); retrying in %.2fs",
                tag,
                attempt,
                max_attempts,
                type(e).__name__,
                e,
                sleep_for,
            )
            await asyncio.sleep(sleep_for)

    # Unreachable - the loop either returns or raises - but mypy/ruff want it.
    assert last_exc is not None
    raise last_exc


__all__ = ["retry_async"]
