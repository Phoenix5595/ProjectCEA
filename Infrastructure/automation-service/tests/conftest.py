"""Shared test fixtures and path setup for automation-service tests.

Adds the service root and the Infrastructure/ parent to ``sys.path`` so
tests can import ``app.*`` (this service) and ``shared.*`` (cross-service
library at ``Infrastructure/shared/``) without per-test boilerplate.
"""

import asyncio
from pathlib import Path
import sys

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_INFRA_ROOT = _SERVICE_ROOT.parent
for _p in (str(_SERVICE_ROOT), str(_INFRA_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture
def event_loop():
    """Create a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
