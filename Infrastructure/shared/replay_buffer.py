"""Bounded in-memory FIFO that holds rows while the database is unavailable.

Producers ``offer`` rows; the writer ``peek``s the oldest rows, writes them and
``consume``s them only after a successful commit. When full, the newest rows
are dropped (the oldest buffered history is kept contiguous) and one ERROR is
logged per overflow episode. An episode ends when the buffer drains below 90 %
of capacity.

Single-event-loop use only (no locking); thread-bound producers use
``shared.db_batch_writer.BatchQueue`` instead.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
import logging
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ReplayBuffer(Generic[T]):
    def __init__(self, capacity: int, *, name: str) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._name = name
        self._items: deque[T] = deque()
        self._dropped = 0
        self._overflowing = False

    def offer(self, items: Sequence[T]) -> int:
        """Append up to capacity; drop the rest (newest). Returns accepted count."""
        free = self._capacity - len(self._items)
        accepted = max(0, min(free, len(items)))
        self._items.extend(items[:accepted])
        rejected = len(items) - accepted
        if rejected:
            self._dropped += rejected
            if not self._overflowing:
                self._overflowing = True
                logger.error("replay buffer %s full: dropped %d newest rows", self._name, rejected)
        return accepted

    def peek(self, limit: int) -> list[T]:
        """Return up to ``limit`` oldest rows without removing them."""
        count = min(limit, len(self._items))
        return [self._items[index] for index in range(count)]

    def consume(self, count: int) -> None:
        """Remove ``count`` rows from the head (after they were written)."""
        for _ in range(min(count, len(self._items))):
            self._items.popleft()
        if self._overflowing and len(self._items) < self._capacity * 0.9:
            self._overflowing = False

    def __len__(self) -> int:
        return len(self._items)

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def capacity(self) -> int:
        return self._capacity
