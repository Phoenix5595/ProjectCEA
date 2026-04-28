"""CAN bus reader - reads messages from CAN interface."""

from __future__ import annotations

import subprocess
import time

import can

from shared.infra_logging import get_logger

logger = get_logger(__name__)


def check_can_interface(channel: str = "can0") -> tuple[bool, str | None]:
    """Check if CAN interface exists and is up.

    Args:
        channel: CAN interface name (default: can0)

    Returns:
        Tuple of (is_ok, error_message)
    """
    try:
        # Check if interface exists
        result = subprocess.run(
            ["ip", "link", "show", channel], capture_output=True, text=True, timeout=2
        )
        if result.returncode != 0:
            return False, f"CAN interface '{channel}' does not exist"

        # Check if interface is UP
        if "state UP" not in result.stdout and "state UNKNOWN" not in result.stdout:
            return False, f"CAN interface '{channel}' is DOWN. Try: sudo ip link set {channel} up"

        return True, None
    except subprocess.TimeoutExpired:
        return False, f"Timeout checking CAN interface '{channel}'"
    except FileNotFoundError:
        # ip command not found, skip check
        return True, None
    except Exception as e:
        return False, f"Error checking CAN interface: {e}"


class CANReader:
    """Reads CAN messages from CAN bus interface."""

    def __init__(self, channel: str = "can0", bustype: str = "socketcan"):
        """Initialize CAN reader.

        Args:
            channel: CAN interface name (default: can0)
            bustype: CAN bus type (default: socketcan)
        """
        self.channel = channel
        self.bustype = bustype
        self.bus: can.interface.Bus | None = None
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0
        self._next_reconnect_at = 0.0

    def connect(self) -> bool:
        """Connect to CAN bus.

        Returns:
            True if successful, False otherwise
        """
        # Check interface first
        interface_ok, error_msg = check_can_interface(self.channel)
        if not interface_ok:
            logger.error(f"CAN interface check failed: {error_msg}")
            return False

        try:
            self.bus = can.interface.Bus(channel=self.channel, bustype=self.bustype)  # type: ignore
            logger.info(f"Connected to CAN bus: {self.channel}")
            self._reconnect_delay = 1.0
            self._next_reconnect_at = 0.0
            return True
        except Exception as e:
            logger.error(f"Failed to connect to CAN bus '{self.channel}': {e}")
            return False

    def read_message(self, timeout: float = 1.0):
        """Read a message from CAN bus.

        Args:
            timeout: Timeout in seconds (default: 1.0)

        Returns:
            CAN message object or None if timeout
        """
        if not self.bus:
            self._reconnect_if_due()
            return None

        try:
            return self.bus.recv(timeout=timeout)
        except Exception as e:
            error_msg = str(e)
            if "Network is down" in error_msg or "100" in error_msg:
                logger.error(f"CAN interface '{self.channel}' went down: {error_msg}")
            else:
                logger.warning(f"CAN bus error: {error_msg}")
            self._mark_disconnected()
            self._reconnect_if_due()
            return None

    def _mark_disconnected(self) -> None:
        """Close the current bus handle before a reconnect attempt."""
        if self.bus is not None:
            try:
                self.bus.shutdown()
            except Exception as e:
                logger.debug(f"Error shutting down CAN bus after read failure: {e}")
            self.bus = None

    def _reconnect_if_due(self) -> None:
        """Bring CAN interface up and reconnect with capped exponential backoff."""
        now = time.monotonic()
        if now < self._next_reconnect_at:
            return

        logger.info(
            "Attempting CAN reconnect for %s (next delay %.1fs)",
            self.channel,
            self._reconnect_delay,
        )
        try:
            subprocess.run(["ip", "link", "set", self.channel, "up"], timeout=3, check=False)
        except FileNotFoundError:
            logger.debug("ip command not found while reconnecting CAN; trying socketcan directly")
        except Exception as e:
            logger.warning(f"Failed to bring CAN interface '{self.channel}' up: {e}")

        if self.connect():
            return

        self._next_reconnect_at = now + self._reconnect_delay
        self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)

    def close(self):
        """Close CAN bus connection."""
        if self.bus:
            try:
                self.bus.shutdown()
            except Exception as e:
                logger.debug(f"Error shutting down CAN bus: {e}")
            self.bus = None
