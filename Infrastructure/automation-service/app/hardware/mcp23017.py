#!/usr/bin/env python3
from __future__ import annotations

import inspect
from typing import Any

from shared.infra_logging import get_logger

"""
MCP23017 I2C Relay Driver
Controls MCP23017 16-channel I/O expander for relay control
"""


logger = get_logger(__name__)

# MCP23017 Register Addresses
MCP23017_IODIRA = 0x00  # I/O Direction Register A
MCP23017_IODIRB = 0x01  # I/O Direction Register B
MCP23017_GPIOA = 0x12  # GPIO Register A
MCP23017_GPIOB = 0x13  # GPIO Register B
MCP23017_OLATA = 0x14  # Output Latch Register A
MCP23017_OLATB = 0x15  # Output Latch Register B


class MCP23017Driver:
    """
    MCP23017 I2C Relay Driver
    Provides interface to control 16-channel relay board via I2C
    """

    def __init__(
        self,
        i2c_bus: int = 1,
        i2c_address: int = 0x20,
        active_low: bool = True,
    ):
        """
        Initialize MCP23017 driver

        Args:
            i2c_bus: I2C bus number (usually 1 on Raspberry Pi)
            i2c_address: I2C address of MCP23017 (default 0x20)
            active_low: SainSmart 16-channel relay boards are active-LOW
                ("Low Level Trigger"): a LOW bit on MCP23017 energizes the
                relay. Default ``True`` keeps the safe SainSmart default;
                set ``False`` for active-HIGH boards. With ``active_low=True``,
                a physical HIGH bit means logical OFF, and a physical LOW bit
                means logical ON. All read and write paths use XOR inversion
                so polarity is owned in exactly one place.
        """
        self.i2c_bus = i2c_bus
        self.i2c_address = i2c_address
        self.active_low = active_low
        self.bus: Any = None
        self._channel_states = [False] * 16  # Track state of all 16 channels
        self._probe_ok: bool | None = None  # Cache probe result

        import smbus2

        self.bus = smbus2.SMBus(i2c_bus)
        self._initialize_hardware()
        logger.info(
            f"MCP23017 initialized on I2C bus {i2c_bus}, address 0x{i2c_address:02X} "
            f"(active_low={self.active_low})"
        )

    def _initialize_hardware(self):
        """Initialize MCP23017 hardware - set all pins as outputs and all OFF.

        For active-LOW boards (SainSmart default), all-OFF means writing 0xFF
        to GPIOA/GPIOB (physical HIGH -> relay de-energized). For
        active-HIGH boards, all-OFF means writing 0x00 (physical LOW).
        """
        if self.bus is None:
            logger.warning("MCP23017 hardware initialization skipped: bus is None")
            return

        # Direction registers: both ports set to OUTPUT (0x00).
        self.bus.write_byte_data(self.i2c_address, MCP23017_IODIRA, 0x00)
        self.bus.write_byte_data(self.i2c_address, MCP23017_IODIRB, 0x00)
        # All-OFF safe value: 0xFF for active-LOW, 0x00 for active-HIGH.
        safe_off = 0xFF if self.active_low else 0x00
        try:
            self.bus.write_byte_data(self.i2c_address, MCP23017_GPIOA, safe_off)
            self.bus.write_byte_data(self.i2c_address, MCP23017_GPIOB, safe_off)
        except Exception as e:
            logger.error(f"Error initializing MCP23017 hardware: {e}")
            raise

    def probe(self) -> bool:
        """Probe I2C connectivity to the MCP23017 without changing output state.

        Reads a safe register (IODIRA). Use at startup or for health checks.

        Returns:
            True if I2C read succeeded, False on I2C error.
        """
        if self.bus is None:
            return False
        try:
            self.bus.read_byte_data(self.i2c_address, MCP23017_IODIRA)
            self._probe_ok = True
            return True
        except Exception as e:
            logger.debug(f"MCP23017 probe failed: {e}")
            self._probe_ok = False
            return False

    def is_connected(self) -> bool:
        """Return whether real hardware is connected and last probe succeeded.

        Returns:
            Cached probe result or run probe() once.
        """
        if self._probe_ok is None:
            self.probe()
        return self._probe_ok is True

    def set_channel(self, channel: int, state: bool) -> bool:
        """
        Set a relay channel on or off

        Args:
            channel: Channel number (0-15)
            state: True = ON, False = OFF (logical, caller-intent state)

        Returns:
            True if successful, False otherwise

        Polarity handling:
            The physical bit written to MCP23017 is the logical state XOR
            ``active_low``. With active_low=True (SainSmart default), a
            logical ON writes a LOW bit (relay energizes) and a logical OFF
            writes a HIGH bit (relay de-energizes). With active_low=False
            (active-HIGH boards), the logical state is written verbatim.
        """
        if channel < 0 or channel > 15:
            logger.error(f"Invalid channel number: {channel} (must be 0-15)")
            return False

        # TEMP DEBUG (relay-mcp-bugfix Task 8): capture the immediate caller
        # so ch11 cycling can be attributed from journalctl. Uses
        # inspect.currentframe() (single-frame, lightweight) rather than
        # inspect.stack() (full-stack walk). Removed by Task 7 once R12 is
        # observed steady.
        try:
            _caller_frame = inspect.currentframe()
            _caller = (
                _caller_frame.f_back.f_code.co_name
                if _caller_frame is not None and _caller_frame.f_back is not None
                else "<unknown>"
            )
        except Exception:
            _caller = "<unknown>"
        logger.info(
            f"MCP23017Driver.set_channel called: channel={channel}, state={state}, caller={_caller}"
        )

        try:
            if self.bus is None:
                logger.error("I2C bus not initialized")
                return False

            if channel < 8:
                port = MCP23017_GPIOA
                bit = channel
            else:
                port = MCP23017_GPIOB
                bit = channel - 8

            current_state = self.bus.read_byte_data(self.i2c_address, port)

            # XOR inversion: with active_low=True, state=True -> physical LOW
            # (bit cleared), state=False -> physical HIGH (bit set). With
            # active_low=False, the logical state passes through unchanged.
            physical_bit = bool(state) ^ self.active_low
            if physical_bit:
                new_state = current_state | (1 << bit)
            else:
                new_state = current_state & ~(1 << bit)

            self.bus.write_byte_data(self.i2c_address, port, new_state)
            self._channel_states[channel] = state

            logger.debug(f"Channel {channel} set to {'ON' if state else 'OFF'}")
            return True

        except Exception as e:
            logger.error(f"Error setting channel {channel}: {e}")
            return False

    def get_channel(self, channel: int) -> bool | None:
        """
        Get current state of a relay channel

        Args:
            channel: Channel number (0-15)

        Returns:
            True if ON, False if OFF, None on error

        Polarity handling:
            The physical bit read from MCP23017 is interpreted via the
            same XOR rule used on writes, so a write-then-read round trip
            always returns the logical state the caller set. With
            active_low=True, a physical HIGH bit is logical OFF and a
            physical LOW bit is logical ON.
        """
        if channel < 0 or channel > 15:
            logger.error(f"Invalid channel number: {channel} (must be 0-15)")
            return None

        try:
            if self.bus is None:
                logger.error("I2C bus not initialized")
                return None

            if channel < 8:
                port = MCP23017_GPIOA
                bit = channel
            else:
                port = MCP23017_GPIOB
                bit = channel - 8

            port_state = self.bus.read_byte_data(self.i2c_address, port)

            physical_bit = bool(port_state & (1 << bit))
            # XOR inversion: physical_bit ^ active_low flips the meaning
            # back to logical state.
            state = physical_bit ^ self.active_low
            self._channel_states[channel] = state

            return state

        except Exception as e:
            logger.error(f"Error reading channel {channel}: {e}")
            return None

    def get_all_channels(self) -> list:
        """
        Get state of all 16 channels

        Returns:
            List of 16 boolean values (True=ON, False=OFF)
        """
        states = []
        for channel in range(16):
            state = self.get_channel(channel)
            if state is None:
                state = self._channel_states[channel]
            states.append(state)
        return states

    def set_all_channels(self, states: list) -> bool:
        """
        Set all channels at once

        Args:
            states: List of 16 boolean values (True=ON, False=OFF)

        Returns:
            True if successful, False otherwise
        """
        if len(states) != 16:
            logger.error(f"Invalid states list length: {len(states)} (must be 16)")
            return False

        success = True
        for channel, state in enumerate(states):
            if not self.set_channel(channel, state):
                success = False

        return success

    def all_off(self) -> bool:
        """Turn off all channels"""
        return self.set_all_channels([False] * 16)

    def close(self):
        """Close I2C connection and cleanup"""
        if self.bus:
            try:
                self.bus.close()
                logger.info("MCP23017 I2C connection closed")
            except Exception as e:
                logger.error(f"Error closing I2C connection: {e}")

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
