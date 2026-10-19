"""Pure behavior tests for deploy-time migration verification."""

from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

import pytest

from app import migrations


class _FakeConfig:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeScriptDirectory:
    @classmethod
    def from_config(cls, config: _FakeConfig) -> _FakeScriptDirectory:
        assert Path(config.path) == Path(migrations.__file__).parent.parent / "alembic.ini"
        return cls()

    def get_heads(self) -> list[str]:
        return ["z-head", "a-head"]


class _FakeConnection:
    async def fetch(self, query: str) -> list[dict[str, str]]:
        assert query == "SELECT version_num FROM alembic_version"
        return [{"version_num": "z-current"}, {"version_num": "a-current"}]


class _FakeAcquire:
    async def __aenter__(self) -> _FakeConnection:
        return _FakeConnection()

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakePool:
    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire()


@pytest.mark.asyncio
async def test_verify_alembic_head_logs_mismatch_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: deployed revisions and code heads differ, with unordered results.
    alembic_package = ModuleType("alembic")
    alembic_package.__path__ = []  # type: ignore[attr-defined]
    config_module = ModuleType("alembic.config")
    config_module.Config = _FakeConfig  # type: ignore[attr-defined]
    script_module = ModuleType("alembic.script")
    script_module.ScriptDirectory = _FakeScriptDirectory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "alembic", alembic_package)
    monkeypatch.setitem(sys.modules, "alembic.config", config_module)
    monkeypatch.setitem(sys.modules, "alembic.script", script_module)

    errors: list[str] = []
    monkeypatch.setattr(migrations.logger, "error", errors.append)

    # When: startup verifies heads using a fake database connection.
    await migrations.verify_alembic_head(_FakePool())

    # Then: it logs sorted revision sets and does not raise.
    assert errors == [
        "alembic_revision_mismatch current=['a-current', 'z-current'] heads=['a-head', 'z-head']"
    ]
