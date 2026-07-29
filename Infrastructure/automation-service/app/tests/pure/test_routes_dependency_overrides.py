from __future__ import annotations

from fastapi import FastAPI

from app.routes import routes


class _FakeService:
    pass


class _FakeDeviceRepo:
    pass


class _FakeDatabase:
    def __init__(self) -> None:
        self.device_repo = _FakeDeviceRepo()


class _FakeContainer:
    def __init__(self) -> None:
        self._database = _FakeDatabase()

    def get_database(self) -> _FakeDatabase:
        return self._database

    def get_scheduler(self) -> _FakeService:
        return _FakeService()

    def get_config(self) -> _FakeService:
        return _FakeService()

    def get_dfr0971_manager(self) -> _FakeService:
        return _FakeService()

    def get_relay_manager(self) -> _FakeService:
        return _FakeService()

    def get_interlock_manager(self) -> _FakeService:
        return _FakeService()

    def get_relay_board_state_manager(self) -> _FakeService:
        return _FakeService()

    def get_automation_redis(self) -> _FakeService:
        return _FakeService()

    def get_pid_controller_manager(self) -> _FakeService:
        return _FakeService()

    def get_control_engine(self) -> _FakeService:
        return _FakeService()


def test_setup_dependency_overrides_does_not_crash() -> None:
    app = FastAPI()

    routes.setup_dependency_overrides(app, _FakeContainer())
