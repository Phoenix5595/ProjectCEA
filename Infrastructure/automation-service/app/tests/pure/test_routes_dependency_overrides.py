from __future__ import annotations

from fastapi import FastAPI

from app.events.mutation_dependencies import get_mutation_event_sink
from app.routes import operational_events, routes


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
        self._operational_event_sink = _FakeService()
        self._operational_event_reader = _FakeService()

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

    def get_device_command_service(self) -> _FakeService:
        return _FakeService()

    def get_control_snapshot_service(self) -> _FakeService:
        return _FakeService()

    def get_interlock_manager(self) -> _FakeService:
        return _FakeService()

    def get_relay_board_state_manager(self) -> _FakeService:
        return _FakeService()

    def get_automation_redis(self) -> _FakeService:
        return _FakeService()

    def get_pid_controller_manager(self) -> _FakeService:
        return _FakeService()

    def get_monitoring_publication_workers(self) -> None:
        return None

    def get_control_engine(self) -> _FakeService:
        return _FakeService()

    def get_photoperiod_history_logger(self) -> _FakeService:
        return _FakeService()

    def get_operational_event_sink(self) -> _FakeService:
        return self._operational_event_sink

    def get_operational_event_reader(self) -> _FakeService:
        return self._operational_event_reader


def test_setup_dependency_overrides_does_not_crash() -> None:
    app = FastAPI()

    routes.setup_dependency_overrides(app, _FakeContainer())


def test_central_registration_binds_operational_event_routes_and_dependencies() -> None:
    # Given: the sole runtime container and an empty application.
    container = _FakeContainer()
    app = FastAPI()

    # When: route lanes are centrally registered and their dependencies are bound.
    routes.register_routes(app)
    routes.setup_dependency_overrides(app, container)

    # Then: event reads and mutation producers resolve only to runtime-owned resources.
    assert any(route.path == "/api/events/history" for route in app.routes)
    assert (
        app.dependency_overrides[get_mutation_event_sink]()
        is container.get_operational_event_sink()
    )
    assert (
        app.dependency_overrides[operational_events.get_operational_event_reader]()
        is container.get_operational_event_reader()
    )
