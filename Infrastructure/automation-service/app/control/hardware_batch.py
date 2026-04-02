"""Hardware Batch Executor for sequential I2C operations.

This module provides sequential execution of I2C hardware operations with
critical sequencing constraints within each device's operation chain.

Key Design Principles:
- Sequential execution across all devices (deterministic, safe)
- Sequence within device chains (relay ON before dimmer for light ON, etc.)
- 500ms timeout per operation chain to prevent control loop stalls
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)

# Timeout for each device operation chain (500ms)
CHAIN_TIMEOUT_SECONDS = 0.5


@dataclass
class RelayOperation:
    """Represents a relay (on/off) operation."""

    location: str
    cluster: str
    device_name: str
    state: int  # 0 = OFF, 1 = ON
    relay_manager: Any  # RelayManager - using Any to avoid import issues
    mode: str = "auto"
    check_interlock: bool = True


@dataclass
class DimmerOperation:
    """Represents a dimmer (0-10V) operation."""

    board_id: str
    channel: int
    intensity: int  # 0-100 percent
    dfr0971_manager: Any  # DFR0971Manager - using Any to avoid import issues


@dataclass
class DeviceOperationChain:
    """A chain of operations for a single device.

    Operations within a chain are executed sequentially (order matters).
    """

    device_key: str  # "location/cluster/device_name"
    operations: list[RelayOperation | DimmerOperation] = field(default_factory=list)

    def add_relay(self, op: RelayOperation) -> None:
        """Add a relay operation to the chain."""
        self.operations.append(op)

    def add_dimmer(self, op: DimmerOperation) -> None:
        """Add a dimmer operation to the chain."""
        self.operations.append(op)


@dataclass
class BatchResult:
    """Result of a batch execution."""

    success_count: int = 0
    failure_count: int = 0
    results: dict[str, bool] = field(default_factory=dict)  # device_key -> success
    errors: dict[str, str] = field(default_factory=dict)  # device_key -> error message
    # device_key -> light intent (location/cluster/device_name, board/channel, intensity percent)
    light_intents: dict[str, dict[str, Any]] = field(default_factory=dict)


class HardwareBatchExecutor:
    """Batches and executes I2C hardware operations sequentially across devices.

    Usage:
        executor = HardwareBatchExecutor()
        executor.queue_light_on("Flower Room", "main", "grow_light_1", 75, ...)
        executor.queue_light_off("Flower Room", "main", "grow_light_2", ...)
        executor.queue_binary_device("Flower Room", "main", "exhaust_fan", 1, ...)
        result = await executor.execute()

    Sequencing Rules (CRITICAL):
        - Light ON: relay ON first, THEN dimmer set (power before signal)
        - Light OFF: dimmer 0 first, THEN relay OFF (signal before power)
        - Binary device: single relay operation
    """

    def __init__(self) -> None:
        """Initialize the batch executor."""
        self._chains: dict[str, DeviceOperationChain] = {}
        # Track the *intended* final state for dimmable lights so callers can persist it
        # (e.g., Redis/UI) after a successful batch run.
        self._light_intents: dict[str, dict[str, Any]] = {}

    def _get_or_create_chain(
        self, location: str, cluster: str, device_name: str
    ) -> DeviceOperationChain:
        """Get existing chain or create new one for a device."""
        device_key = f"{location}/{cluster}/{device_name}"
        if device_key not in self._chains:
            self._chains[device_key] = DeviceOperationChain(device_key=device_key)
        return self._chains[device_key]

    def queue_light_on(
        self,
        location: str,
        cluster: str,
        device_name: str,
        intensity: int,
        relay_manager: Any,
        dfr0971_manager: Any,
        board_id: str,
        dimming_channel: int,
        relay_channel: int | None = None,
    ) -> None:
        """Queue a light ON operation.

        Sequence: relay ON -> dimmer set (power before signal)

        Args:
            location: Room location (e.g., "Flower Room")
            cluster: Cluster name (e.g., "main")
            device_name: Device name (e.g., "grow_light_1")
            intensity: Target intensity 0-100 percent
            relay_manager: RelayManager instance
            dfr0971_manager: DFR0971Manager instance
            board_id: DFR0971 board ID
            dimming_channel: DFR0971 channel (0-1)
            relay_channel: Optional relay channel (for power control)
        """
        chain = self._get_or_create_chain(location, cluster, device_name)
        device_key = chain.device_key

        self._light_intents[device_key] = {
            "location": location,
            "cluster": cluster,
            "device_name": device_name,
            "board_id": board_id,
            "channel": dimming_channel,
            "intensity_percent": int(intensity),
        }

        # Step 1: Relay ON (power before signal)
        if relay_channel is not None and relay_manager is not None:
            chain.add_relay(
                RelayOperation(
                    location=location,
                    cluster=cluster,
                    device_name=device_name,
                    state=1,  # ON
                    relay_manager=relay_manager,
                )
            )

        # Step 2: Set dimmer intensity
        if dfr0971_manager is not None:
            chain.add_dimmer(
                DimmerOperation(
                    board_id=board_id,
                    channel=dimming_channel,
                    intensity=intensity,
                    dfr0971_manager=dfr0971_manager,
                )
            )

        logger.debug(f"Queued light ON: {location}/{cluster}/{device_name} @ {intensity}%")

    def queue_light_off(
        self,
        location: str,
        cluster: str,
        device_name: str,
        relay_manager: Any,
        dfr0971_manager: Any,
        board_id: str,
        dimming_channel: int,
        relay_channel: int | None = None,
    ) -> None:
        """Queue a light OFF operation.

        Sequence: dimmer 0 -> relay OFF (signal before power)

        Args:
            location: Room location
            cluster: Cluster name
            device_name: Device name
            relay_manager: RelayManager instance
            dfr0971_manager: DFR0971Manager instance
            board_id: DFR0971 board ID
            dimming_channel: DFR0971 channel (0-1)
            relay_channel: Optional relay channel (for power control)
        """
        chain = self._get_or_create_chain(location, cluster, device_name)
        device_key = chain.device_key

        self._light_intents[device_key] = {
            "location": location,
            "cluster": cluster,
            "device_name": device_name,
            "board_id": board_id,
            "channel": dimming_channel,
            "intensity_percent": 0,
        }

        # Step 1: Set dimmer to 0 (signal before power off)
        if dfr0971_manager is not None:
            chain.add_dimmer(
                DimmerOperation(
                    board_id=board_id,
                    channel=dimming_channel,
                    intensity=0,
                    dfr0971_manager=dfr0971_manager,
                )
            )

        # Step 2: Relay OFF
        if relay_channel is not None and relay_manager is not None:
            chain.add_relay(
                RelayOperation(
                    location=location,
                    cluster=cluster,
                    device_name=device_name,
                    state=0,  # OFF
                    relay_manager=relay_manager,
                )
            )

        logger.debug(f"Queued light OFF: {location}/{cluster}/{device_name}")

    def queue_binary_device(
        self,
        location: str,
        cluster: str,
        device_name: str,
        state: int,
        relay_manager: Any,
    ) -> None:
        """Queue a binary (on/off) device operation.

        Args:
            location: Room location
            cluster: Cluster name
            device_name: Device name
            state: Target state (0=OFF, 1=ON)
            relay_manager: RelayManager instance
        """
        chain = self._get_or_create_chain(location, cluster, device_name)

        chain.add_relay(
            RelayOperation(
                location=location,
                cluster=cluster,
                device_name=device_name,
                state=state,
                relay_manager=relay_manager,
            )
        )

        logger.debug(f"Queued binary device: {location}/{cluster}/{device_name} -> {state}")

    async def _execute_operation(
        self, op: RelayOperation | DimmerOperation
    ) -> tuple[bool, str | None]:
        """Execute a single operation (wrapped for async).

        Returns:
            Tuple of (success, error_message)
        """
        try:
            if isinstance(op, RelayOperation):
                # Relay operations are synchronous, wrap in to_thread
                success, reason = await asyncio.to_thread(
                    op.relay_manager.set_device_state,
                    op.location,
                    op.cluster,
                    op.device_name,
                    op.state,
                    op.mode,
                    op.check_interlock,
                )
                return success, reason
            elif isinstance(op, DimmerOperation):
                # Dimmer operations are synchronous, wrap in to_thread
                success = await asyncio.to_thread(
                    op.dfr0971_manager.set_intensity,
                    op.board_id,
                    op.channel,
                    op.intensity,
                )
                return success, None if success else "Dimmer set failed"
            else:
                return False, f"Unknown operation type: {type(op)}"
        except Exception as e:
            logger.error(f"Operation failed: {e}")
            return False, str(e)

    async def _execute_chain(self, chain: DeviceOperationChain) -> tuple[bool, str | None]:
        """Execute all operations in a chain sequentially.

        Operations within a chain MUST be sequential to maintain proper
        power/signal sequencing for dimmable lights.

        Returns:
            Tuple of (all_success, first_error_message)
        """
        for op in chain.operations:
            try:
                success, error = await asyncio.wait_for(
                    self._execute_operation(op),
                    timeout=CHAIN_TIMEOUT_SECONDS,
                )
                if not success:
                    return False, error or "Operation failed"
            except TimeoutError:
                error_msg = f"Operation timed out after {CHAIN_TIMEOUT_SECONDS}s"
                logger.warning(f"{chain.device_key}: {error_msg}")
                return False, error_msg
            except Exception as e:
                logger.error(f"{chain.device_key}: Operation exception: {e}")
                return False, str(e)

        return True, None

    async def execute(self) -> BatchResult:
        """Execute all queued operations sequentially.

        Returns:
            BatchResult with success/failure counts and per-device results
        """
        result = BatchResult()

        if not self._chains:
            logger.debug("No operations queued, nothing to execute")
            return result

        chain_list = list(self._chains.values())

        logger.debug(f"Executing {len(chain_list)} device chains sequentially")

        # Sequential execution (safe mode)
        for chain in chain_list:
            try:
                success, error = await self._execute_chain(chain)
                if success:
                    result.success_count += 1
                    result.results[chain.device_key] = True
                else:
                    result.failure_count += 1
                    result.results[chain.device_key] = False
                    result.errors[chain.device_key] = error or "Unknown error"
            except Exception as e:
                result.failure_count += 1
                result.results[chain.device_key] = False
                result.errors[chain.device_key] = str(e)

        logger.info(
            f"Batch execution complete: {result.success_count} success, "
            f"{result.failure_count} failures"
        )

        # Attach intents before clearing internal queues.
        result.light_intents = dict(self._light_intents)

        # Clear chains after execution
        self._chains.clear()
        self._light_intents.clear()

        return result

    def clear(self) -> None:
        """Clear all queued operations without executing."""
        self._chains.clear()
        self._light_intents.clear()

    @property
    def pending_count(self) -> int:
        """Get the number of pending device chains."""
        return len(self._chains)

    @property
    def pending_operations(self) -> int:
        """Get the total number of pending operations across all chains."""
        return sum(len(chain.operations) for chain in self._chains.values())
