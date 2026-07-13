"""Shared types for the state package.

CacheEntry is defined here to avoid circular imports between
StateManager core and the alarm/failsafe mixin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class CacheEntry(Generic[T]):
    """Cache entry with value and expiration time.

    Attributes:
        value: The cached value
        expires_at: Unix timestamp when this entry expires
        created_at: Unix timestamp when this entry was created
    """

    value: T
    expires_at: float
    created_at: float = field(default_factory=time.time)
