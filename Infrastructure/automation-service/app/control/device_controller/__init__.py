"""Device Controller - Handles device state control and validation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from shared.infra_logging import LoggingContext, get_logger

from .binary_device import BinaryDeviceMixin
from .dimmable_light import DimmableLightMixin
from .logging import LoggingMixin
from .rules import RulesMixin
from .vpd import VPDCalculatorMixin

if TYPE_CHECKING:
    from ..database import DatabaseManager
    from ..hardware.dfr0971 import DFR0971Manager
    from .hardware_batch import HardwareBatchExecutor
    from .relay_manager import RelayManager

logger = get_logger(__name__)


class DeviceController(
    VPDCalculatorMixin,
    RulesMixin,
    DimmableLightMixin,
    BinaryDeviceMixin,
    LoggingMixin,
):
    """Handles device control operations and state management."""

    def __init__(
        self,
        relay_manager: RelayManager,
        database_manager: DatabaseManager,
        dfr0971_manager: DFR0971Manager | None = None,
        binary_hysteresis: float = 0.1,
    ) -> None:
        """Initialize device controller.

        Args:
            relay_manager: Relay manager for device control
            database_manager: Database manager for state persistence
            dfr0971_manager: Optional DFR0971 manager for dimmable lights
            binary_hysteresis: Half-width of the neutral zone around the 0.5
                binary decision threshold. A binary device that is currently
                OFF only transitions ON when output > 0.5 + band, and a device
                that is currently ON only transitions OFF when output < 0.5 - band.
                In the band, the prior state is preserved (no hardware write).
                Per-device override is read from device_info["binary_hysteresis"].
        """
        self.relay_manager: RelayManager = relay_manager
        self.database: DatabaseManager = database_manager
        self.dfr0971_manager: DFR0971Manager | None = dfr0971_manager
        self.binary_hysteresis: float = binary_hysteresis
        self._last_light_command: dict[tuple[str, str, str], int] = {}
        self._last_applied_light: dict[tuple[str, str, str], int] = {}
        # Per-(location, cluster, device_name) last applied binary state, used to
        # decide ON/OFF transitions under hysteresis. Missing key => uninitialized
        # (treated as OFF when applying the band).
        self._last_binary_state: dict[tuple[str, str, str], int] = {}

    async def process_device(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        sensor_values: Mapping[str, float | None],
        current_time: datetime,
        context: dict[str, Any],
        batch_executor: HardwareBatchExecutor | None = None,
    ) -> None:
        """Process control for a single device.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_info: Device configuration
            sensor_values: Available sensor values
            current_time: Current time
            context: Automation context dict
            batch_executor: Optional batch executor for parallel I2C operations
        """
        with LoggingContext(operation="process_device"):
            if device_info.get("dimming_enabled") and device_info.get("dimming_type") == "dfr0971":
                logger.debug(f"Processing device {device_name} ({location}/{cluster})")

            # Determine control mode and setpoint
            control_mode, setpoint = self._determine_control_mode(device_name, device_info, context)

            if control_mode == "manual":
                # Skip automated control for manual devices
                logger.info(f"Skipping {device_name} ({location}/{cluster}) - manual mode")
                return

            # Calculate control output
            control_output = await self._calculate_control_output(
                location,
                cluster,
                device_name,
                device_info,
                sensor_values,
                control_mode,
                setpoint,
                context,
            )

            if control_output is not None:
                # Apply the control output
                await self._apply_control_output(
                    location,
                    cluster,
                    device_name,
                    device_info,
                    control_output,
                    current_time,
                    context,
                    batch_executor,
                )

    def _determine_control_mode(
        self, device_name: str, device_info: dict[str, Any], context: dict[str, Any]
    ) -> tuple[str, float | None]:
        """Determine the control mode and setpoint for a device.

        Returns:
            Tuple of (control_mode, setpoint)
        """
        # Check if device is in failsafe mode
        failsafe_active = context.get("failsafe_active", False)
        if failsafe_active:
            return "failsafe", None

        # Check device-specific control mode
        device_mode = device_info.get("control_mode", "auto")

        if device_mode == "manual":
            return "manual", None

        # Get setpoint based on device type
        device_type = device_info.get("device_type", "")
        setpoint = self._get_setpoint_for_device_type(device_type, context)

        return "auto", setpoint

    def _get_setpoint_for_device_type(
        self, device_type: str, context: dict[str, Any]
    ) -> float | None:
        """Get the appropriate setpoint for a device type.

        VPD is king: humidifier and dehumidifier use effective_vpd_setpoint only.
        """
        setpoint_mapping = {
            "heating": context.get("effective_heating_setpoint"),
            "cooling": context.get("effective_cooling_setpoint"),
            "humidifier": context.get("effective_vpd_setpoint"),
            "dehumidifier": context.get("effective_vpd_setpoint"),
            "co2": context.get("effective_co2_setpoint"),
            "light": context.get("light_intensity"),  # For dimmable lights
        }

        return setpoint_mapping.get(device_type)

    async def _calculate_control_output(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        sensor_values: Mapping[str, float | None],
        control_mode: str,
        setpoint: float | None,
        context: dict[str, Any],
    ) -> float | None:
        """Calculate the control output for a device.

        Args:
            location, cluster, device_name: Device identifiers
            device_info: Device configuration
            sensor_values: Available sensor values
            control_mode: Control mode (auto, manual, failsafe)
            setpoint: Target setpoint value
            context: Automation context

        Returns:
            Control output value (0.0-1.0) or None
        """
        device_type = device_info.get("device_type", "")

        # Handle different control modes
        if control_mode == "failsafe":
            return await self._calculate_failsafe_output(device_type)
        elif control_mode == "auto":
            # Dimmable lights: use sun/moon intensity from context (0 = moon off, >0 = sun)
            if device_info.get("dimming_enabled") and device_info.get("dimming_type") == "dfr0971":
                light_intensity = context.get("light_intensity")
                if light_intensity is not None:
                    return light_intensity
                return None

            # Use PID control or rule-based control for other devices
            pid_output = context.get("pid_output")
            if pid_output is not None:
                return pid_output

            # VPD-only control for humidifier and dehumidifier (no humidity setpoint/sensor)
            if device_type in ["humidifier", "dehumidifier"]:
                return self._calculate_vpd_based_output(device_type, context)

            # Rule-based control for non-PID devices (heating, cooling, co2)
            return await self._calculate_rule_based_output(
                location, cluster, device_name, device_info, sensor_values, setpoint
            )

        return None

    async def _apply_control_output(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        control_output: float,
        current_time: datetime,
        context: dict[str, Any] | None = None,
        batch_executor: HardwareBatchExecutor | None = None,
    ) -> None:
        """Apply the calculated control output to the device.

        Args:
            location, cluster, device_name: Device identifiers
            device_info: Device configuration
            control_output: Control output value (0.0-1.0)
            current_time: Current timestamp
            context: Automation context (for log reason and load_percent)
        """
        device_type = device_info.get("device_type", "")
        relay_channel = device_info.get("channel")
        is_dfr0971_light = bool(
            device_info.get("dimming_enabled") and device_info.get("dimming_type") == "dfr0971"
        )

        # Binary actuators require a relay channel. DFR0971 lights may omit relay (dimmer-only);
        # _control_dimmable_light already handles relay_channel is None.
        if not is_dfr0971_light and relay_channel is None:
            logger.warning(f"No channel configured for {device_name}")
            return

        # control_history.channel: relay index when present; else DFR0971 dimming_channel (no relay).
        history_channel: int
        if is_dfr0971_light:
            dim_ch = device_info.get("dimming_channel")
            if relay_channel is not None:
                history_channel = int(relay_channel)
            elif dim_ch is not None:
                history_channel = int(dim_ch)
            else:
                history_channel = -1
        else:
            history_channel = int(relay_channel)  # guarded above

        # Capture old state before applying (for control history log)
        old_state: int | None = None
        if self.relay_manager:
            old_state = self.relay_manager.get_device_state(location, cluster, device_name)
            if old_state is None:
                old_state = 0

        try:
            # Handle different device types
            if is_dfr0971_light:
                # Dimmable light control
                await self._control_dimmable_light(
                    location, cluster, device_name, device_info, control_output, batch_executor
                )
            else:
                # Binary relay control
                await self._control_binary_device(
                    location,
                    cluster,
                    device_name,
                    device_type,
                    history_channel,
                    control_output,
                    batch_executor,
                    device_info,
                )

            # Log the control action
            await self._log_control_action(
                location,
                cluster,
                device_name,
                device_type,
                history_channel,
                control_output,
                current_time,
                old_state=old_state,
                context=context or {},
            )

        except Exception as e:
            logger.error(f"Failed to control {device_name} ({location}/{cluster}): {e}")
