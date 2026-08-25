"""PID state management mixin for StateManager.

Provides get/set operations for PID controller parameters and autotune state,
stored in Redis with structured keys.
"""

from __future__ import annotations

import json
import time
from typing import Any, TypedDict

from app.redis.schema import pid_autotune_key, pid_key_with_location
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class PIDParams(TypedDict):
    """PID controller parameters stored in Redis."""

    kp: float
    ki: float
    kd: float
    updated_at: str | None


class PIDMixin:
    """Mixin adding PID parameter and autotune state methods to StateManager."""

    # ------------------------------------------------------------------
    # PID Parameter API (migrated from Redis mixin)
    # ------------------------------------------------------------------
    async def get_pid_params(
        self, location: str, cluster: str, device_type: str
    ) -> PIDParams | None:
        """Get PID parameters for a given location/cluster/device_type from cache/Redis.

        Args:
            location: Location name (e.g., 'Flower Room')
            cluster: Cluster name (e.g., 'main')
            device_type: Device type (e.g., 'heater', 'co2')

        Returns:
            Dict containing kp/ki/kd and metadata, or None if not found
        """
        pid_key = pid_key_with_location(location, cluster, device_type)
        data = await self.get(pid_key)
        if data is None:
            return None

        if isinstance(data, (bytes, bytearray)):
            data_str = data.decode()
        elif isinstance(data, str):
            data_str = data
        else:
            return data  # Already a dict-like object

        try:
            return json.loads(data_str)
        except Exception:
            return {"raw": data_str}  # pyright: ignore[reportReturnType]

    async def set_pid_params(
        self,
        location: str,
        cluster: str,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        binary_hysteresis: float | None = None,
        source: str = "api",
        updated_at: int | None = None,
    ) -> bool:
        """Set PID parameters for a given location/cluster/device_type with a 300s TTL.

        Args:
            location: Location name
            cluster: Cluster name
            device_type: Device type
            kp: Proportional gain
            ki: Integral gain
            kd: Derivative gain
            binary_hysteresis: Optional binary hysteresis value
            source: Source of parameters ('api', 'config')
            updated_at: Optional timestamp in milliseconds

        Returns:
            True if written successfully, False otherwise
        """
        pid_key = pid_key_with_location(location, cluster, device_type)
        timestamp_ms = updated_at or int(time.time() * 1000)
        payload: dict[str, Any] = {
            "kp": kp,
            "ki": ki,
            "kd": kd,
            "source": source,
            "updated_at": timestamp_ms,
        }
        if binary_hysteresis is not None:
            payload["binary_hysteresis"] = binary_hysteresis
        try:
            await self.set(pid_key, json.dumps(payload), ttl=300)
            return True
        except Exception as e:
            logger.warning(
                f"StateManager: Failed to set PID params for {location}/{cluster}/{device_type}: {e}"
            )
            return False

    # Autotune state handling for PID controllers
    async def get_autotune_state(self, device_type: str) -> dict[str, Any] | None:
        """Get autotune state for a device's PID controller."""
        key = pid_autotune_key(device_type)
        data = await self.get(key)
        if data is None:
            return None
        if isinstance(data, (bytes, bytearray)):
            data_str = data.decode()
        elif isinstance(data, str):
            data_str = data
        else:
            return data  # dict-like
        try:
            return json.loads(data_str)
        except Exception:
            return {"raw": data_str}

    async def set_autotune_state(
        self, device_type: str, state: dict[str, Any], ttl: int = 300
    ) -> None:
        """Set autotune state for a device's PID controller with a TTL."""
        key = pid_autotune_key(device_type)
        try:
            await self.set(key, json.dumps(state), ttl=ttl)
        except Exception as e:
            logger.warning(f"StateManager: Failed to set autotune state for {device_type}: {e}")
