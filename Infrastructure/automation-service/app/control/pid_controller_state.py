"""PID controller lifecycle and state methods."""

from __future__ import annotations

from typing import Any

from app.state.pid import PIDParams
from shared.infra_logging import get_logger

logger = get_logger("app.control.pid_controller_manager")


class PIDControllerStateMixin:
    async def get_pid_controller(
        self, location: str, cluster: str, device_name: str, device_type: str
    ) -> Any | None:
        """Get or create a PID controller for a device.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_type: Device type

        Returns:
            PIDController instance or None if not applicable
        """
        key = (location, cluster, device_name, device_type)

        if key not in self._pid_controllers:
            controller = await self._create_pid_controller(
                location, cluster, device_name, device_type
            )
            if controller:
                self._pid_controllers[key] = controller
        controller = self._pid_controllers.get(key)
        if controller is not None and key in self._settings_default_controllers:
            pid_params = await self._get_cached_pid_parameters(location, cluster, device_type)
            if pid_params and pid_params.get("source") != "settings_default":
                self._settings_default_controllers.discard(key)
                controller.update_parameters(
                    pid_params.get("kp", 1.0),
                    pid_params.get("ki", 0.0),
                    pid_params.get("kd", 0.0),
                )
            else:
                self._refresh_settings_default_controller(controller, device_type)
        return controller

    def _service_pid_defaults(self, device_type: str) -> dict[str, float] | None:
        get_defaults = getattr(self.config, "get_pid_params_for_device", None)
        if not callable(get_defaults):
            return None
        return get_defaults(device_type)

    def _apply_service_pid_defaults(
        self, pid_params: dict[str, Any], device_type: str
    ) -> dict[str, Any]:
        source = pid_params.get("source")
        if source != "settings_default" and not (
            source == "default" and pid_params.get("updated_at") is None
        ):
            return pid_params
        defaults = self._service_pid_defaults(device_type)
        if defaults is None:
            return pid_params
        return {**pid_params, **defaults, "source": "settings_default"}

    def _refresh_settings_default_controller(self, controller: Any, device_type: str) -> None:
        defaults = self._service_pid_defaults(device_type)
        if defaults is not None:
            controller.update_parameters(defaults["kp"], defaults["ki"], defaults["kd"])

    # Control mode integration
    async def get_control_mode_info(
        self, location: str, cluster: str, device_type: str
    ) -> dict[str, Any]:
        """Get control mode info for a device type with caching."""
        try:
            mode_info = await self.database.pid_repo.get_pid_control_mode(
                location, cluster, device_type
            )
            if mode_info:
                return mode_info
        except Exception as e:
            logger.warning(f"Failed to get control mode for {device_type}: {e}")
        return {"control_mode": "pid", "hysteresis_high": 1.0, "hysteresis_low": 0.5}

    async def _create_pid_controller(
        self, location: str, cluster: str, device_name: str, device_type: str
    ) -> Any | None:
        """Create a PID controller for a device if applicable.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_type: Device type

        Returns:
            PIDController instance or None
        """
        # Import here to avoid circular imports
        from app.control.pid_controller import PIDController

        # Check if device type supports PID control
        pid_capable_types = ["heating", "cooling", "co2"]

        if device_type not in pid_capable_types:
            return None

        try:
            # Get PID parameters with caching
            pid_params = await self._get_cached_pid_parameters(location, cluster, device_type)
            if not pid_params:
                logger.debug(f"No PID parameters found for device_type {device_type}")
                return None
            if pid_params.get("source") == "settings_default":
                self._settings_default_controllers.add(
                    (location, cluster, device_name, device_type)
                )

            kp = pid_params.get("kp", 1.0)
            ki = pid_params.get("ki", 0.0)
            kd = pid_params.get("kd", 0.0)

            # Create PID controller with optimized parameters
            controller = PIDController(kp=kp, ki=ki, kd=kd)
            logger.debug(
                f"Created PID controller for {device_name} ({device_type}): Kp={kp}, Ki={ki}, Kd={kd}"
            )

            return controller

        except Exception as e:
            logger.error(f"Failed to create PID controller for {device_name} ({device_type}): {e}")
            return None

    async def _get_cached_pid_parameters(
        self, location: str, cluster: str, device_type: str
    ) -> PIDParams | None:
        """Get PID parameters from StateManager cache with database fallback.

        Uses StateManager for fast in-memory access (<1ms reads).
        Falls back to database if not in cache.
        """
        # Try StateManager cache first (fast path)
        pid_params = await self._state.get_pid_params(location, cluster, device_type)
        if pid_params:
            logger.debug(
                f"StateManager cache hit for PID params: {location}/{cluster}/{device_type}"
            )
            return self._apply_service_pid_defaults(dict(pid_params), device_type)

        # Cache miss - fetch from database
        try:
            pid_params = await self.database.pid_repo.get_pid_parameters(
                location, cluster, device_type
            )
            if pid_params:
                pid_params = self._apply_service_pid_defaults(pid_params, device_type)
                await self._state.set_pid_params(
                    location,
                    cluster,
                    device_type,
                    pid_params.get("kp", 1.0),
                    pid_params.get("ki", 0.0),
                    pid_params.get("kd", 0.0),
                    binary_hysteresis=pid_params.get("binary_hysteresis"),
                    source=pid_params.get("source", "database"),
                )
                logger.debug(
                    f"Populated StateManager cache for PID params: {location}/{cluster}/{device_type}"
                )
            return pid_params
        except Exception as e:
            logger.warning(
                f"Failed to fetch PID parameters for {location}/{cluster}/{device_type}: {e}"
            )
            return None

    async def reload_pid_parameters(self, location: str, cluster: str, device_type: str) -> bool:
        """Reload PID parameters for all controllers of a device type.

        Args:
            location: Location name
            cluster: Cluster name
            device_type: Device type to reload

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get updated PID parameters
            pid_params = await self.database.pid_repo.get_pid_parameters(
                location, cluster, device_type
            )
            if not pid_params:
                logger.warning(f"No PID parameters found for device_type {device_type}")
                return False

            kp = pid_params.get("kp", 1.0)
            ki = pid_params.get("ki", 0.0)
            kd = pid_params.get("kd", 0.0)

            # Update all controllers of this type
            updated_count = 0
            for key, controller in self._pid_controllers.items():
                if key[3] == device_type:  # device_type is the 4th element in key
                    old_kp, old_ki, old_kd = controller.kp, controller.ki, controller.kd
                    controller.update_parameters(kp, ki, kd)
                    updated_count += 1
                    logger.info(
                        f"PID parameters reloaded for {key[2]} ({device_type}): "
                        f"Kp={old_kp}->{kp}, Ki={old_ki}->{ki}, Kd={old_kd}->{kd}"
                    )

            logger.info(
                f"PID parameters updated for {updated_count} controllers of type {device_type}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to reload PID parameters for {device_type}: {e}")
            return False

    def get_pid_status(self) -> dict[str, dict[str, Any]]:
        """Get status of all PID controllers."""
        status = {}
        for key, controller in self._pid_controllers.items():
            location, cluster, device_name, device_type = key
            duty = controller.get_duty_cycle() if hasattr(controller, "get_duty_cycle") else 0.0
            status[f"{location}/{cluster}/{device_name}"] = {
                "device_type": device_type,
                "kp": controller.kp,
                "ki": controller.ki,
                "kd": controller.kd,
                "integral": getattr(controller, "integral", 0),
                "previous_error": getattr(controller, "previous_error", 0),
                "load_percent": round(float(duty), 1),
            }
        return status

    async def clear_caches(self) -> None:
        """Clear all caches for fresh data."""
        # Clear StateManager cache (async)
        await self._state.clear()
        logger.debug("PID parameter cache cleared via StateManager")

    async def get_performance_stats(self) -> dict[str, Any]:
        """Get performance statistics."""
        state_stats = await self._state.get_stats()
        return {
            "active_controllers": len(self._pid_controllers),
            "state_manager_stats": state_stats,
        }
