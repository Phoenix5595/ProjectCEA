#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.infra_logging import get_logger

"""
DFR0971 2-Channel I2C 0-10V DAC Module Driver
Controls DFR0971 modules for HLG320B light dimming
Supports multiple boards with different I2C addresses
Supports simulation mode when hardware is not connected
"""

logger = get_logger(__name__)

# GP8403 I2C Commands (DFR0971 uses GP8403 chip)
DFR0971_CMD_SET_RANGE = 0x01
DFR0971_CMD_SET_VOLTAGE_CH0 = 0x02
DFR0971_CMD_SET_VOLTAGE_CH1 = 0x04
DFR0971_CMD_STORE = 0x03

# Output range values
DFR0971_RANGE_5V = 0x00
DFR0971_RANGE_10V = 0x11

# Default I2C address
DFR0971_DEFAULT_ADDRESS = 0x58


@dataclass
class DFR0971Board:
    """Represents a DFR0971 board configuration."""

    board_id: int
    i2c_address: int
    name: str | None = None


class DFR0971Driver:
    """
    DFR0971 2-Channel 0-10V DAC Driver
    Handles one DFR0971 board (one I2C address, 2 channels)
    """

    def __init__(
        self, i2c_bus: int = 1, i2c_address: int = DFR0971_DEFAULT_ADDRESS, simulation: bool = False
    ):
        """
        Initialize DFR0971 driver

        Args:
            i2c_bus: I2C bus number (usually 1 on Raspberry Pi)
            i2c_address: I2C address of DFR0971 (default 0x58)
            simulation: If True, simulate hardware without actual I2C communication
        """
        self.i2c_bus = i2c_bus
        self.i2c_address = i2c_address
        self.simulation = simulation
        self.bus: Any = None
        self._channel_states = [0.0, 0.0]  # Track voltage for channels 0 and 1
        self._range_set = False
        self._voltage_range = 10000  # 10V range in millivolts

        if not simulation:
            try:
                import smbus2

                self.bus = smbus2.SMBus(i2c_bus)
                self._initialize_hardware()
                logger.info(
                    f"DFR0971 initialized on I2C bus {i2c_bus}, address 0x{i2c_address:02X}"
                )
            except ImportError:
                logger.warning("smbus2 not available, falling back to simulation mode")
                self.simulation = True
            except Exception as e:
                logger.error(f"Failed to initialize DFR0971 hardware: {e}")
                logger.warning("Falling back to simulation mode")
                self.simulation = True

        if self.simulation:
            logger.info(f"DFR0971 running in simulation mode (address 0x{i2c_address:02X})")

    def _initialize_hardware(self):
        """Initialize DFR0971 hardware - set output range to 10V"""
        if self.simulation:
            return

        try:
            import time

            logger.debug(
                f"Initializing DFR0971 at 0x{self.i2c_address:02X} - setting output range to 10V"
            )

            self._set_output_range(DFR0971_RANGE_10V)
            time.sleep(0.05)

            # Store settings to EEPROM
            try:
                self.store_settings()
                time.sleep(0.1)
            except Exception as e:
                logger.warning(f"Could not store range to EEPROM during init: {e}")

            self._range_set = True
            logger.debug("DFR0971 output range set to 10V and stored")
        except Exception as e:
            logger.error(f"Error initializing DFR0971 hardware: {e}")
            raise

    def _set_output_range(self, range_value: int):
        """Set output range (5V or 10V)"""
        if self.simulation:
            return

        import time

        try:
            assert self.bus is not None, "I2C bus must be initialized when not in simulation mode"
            self.bus.write_word_data(self.i2c_address, DFR0971_CMD_SET_RANGE, range_value)
            time.sleep(0.02)

            logger.debug(
                f"Output range set to {'10V' if range_value == DFR0971_RANGE_10V else '5V'} (value: 0x{range_value:02X})"
            )
        except OSError as e:
            logger.error(f"I2C error setting output range for board 0x{self.i2c_address:02X}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error setting output range: {e}")
            raise

    def set_voltage(self, voltage: float, channel: int = 0, store_to_eeprom: bool = False) -> bool:
        """
        Set output voltage (0-10V)

        Args:
            voltage: Output voltage in volts (0.0 - 10.0)
            channel: Channel number (0 or 1)
            store_to_eeprom: If True, store settings to EEPROM after setting voltage

        Returns:
            True if successful, False otherwise
        """
        if channel not in [0, 1]:
            logger.error(f"Invalid channel number: {channel} (must be 0 or 1)")
            return False

        # Clamp voltage to valid range
        voltage = max(0.0, min(10.0, voltage))

        try:
            if self.simulation:
                self._channel_states[channel] = voltage
                logger.debug(f"Simulation: Channel {channel} set to {voltage:.2f}V")
                return True

            # Ensure output range is set before setting voltage
            if not self._range_set:
                try:
                    self._set_output_range(DFR0971_RANGE_10V)
                    self._range_set = True
                except Exception as e:
                    logger.warning(f"Failed to set output range before voltage write: {e}")

            # Convert voltage (0.0-10.0) to DAC value
            # Formula: (voltage / 10.0) * 4095 << 4
            dac_12bit = int((voltage / 10.0) * 4095)
            dac_12bit = max(0, min(4095, dac_12bit))
            dac_value = dac_12bit << 4

            reg_addr = DFR0971_CMD_SET_VOLTAGE_CH0 if channel == 0 else DFR0971_CMD_SET_VOLTAGE_CH1

            logger.debug(
                f"Setting voltage: {voltage:.2f}V, channel={channel}, "
                f"DAC_12bit={dac_12bit}, DAC_16bit={dac_value} (0x{dac_value:04X})"
            )

            import time

            assert self.bus is not None, "I2C bus must be initialized when not in simulation mode"
            self.bus.write_word_data(self.i2c_address, reg_addr, dac_value)
            time.sleep(0.05)

            self._channel_states[channel] = voltage
            logger.info(
                f"Channel {channel} set to {voltage:.2f}V (intensity: {(voltage / 10.0) * 100:.1f}%, DAC: 0x{dac_value:04X})"
            )

            if store_to_eeprom:
                try:
                    self.store_settings()
                    logger.debug(f"Settings stored to EEPROM for channel {channel}")
                except Exception as e:
                    logger.warning(f"Could not store settings to EEPROM: {e}")

            return True

        except Exception as e:
            logger.error(
                f"Error setting voltage on channel {channel} (address 0x{self.i2c_address:02X}): {e}"
            )
            return False

    def set_intensity(
        self, intensity: float, channel: int = 0, store_to_eeprom: bool = False
    ) -> bool:
        """
        Set dimming intensity as percentage (0-100%)

        Args:
            intensity: Dimming percentage (0.0 - 100.0)
            channel: Channel number (0 or 1)
            store_to_eeprom: If True, store settings to EEPROM after setting intensity

        Returns:
            True if successful, False otherwise
        """
        return self.set_voltage((intensity / 100.0) * 10.0, channel, store_to_eeprom)

    def get_voltage(self, channel: int = 0) -> float | None:
        """
        Get current output voltage

        Args:
            channel: Channel number (0 or 1)

        Returns:
            Current voltage in volts, or None on error
        """
        if channel not in [0, 1]:
            logger.error(f"Invalid channel number: {channel} (must be 0 or 1)")
            return None

        return self._channel_states[channel]

    def get_intensity(self, channel: int = 0) -> float | None:
        """
        Get current dimming intensity as percentage

        Args:
            channel: Channel number (0 or 1)

        Returns:
            Current intensity percentage (0-100), or None on error
        """
        voltage = self.get_voltage(channel)
        if voltage is None:
            return None
        return (voltage / 10.0) * 100.0

    def store_settings(self) -> bool:
        """
        Store current settings to EEPROM (persists after power cycle)

        Returns:
            True if successful, False otherwise
        """
        if self.simulation:
            logger.debug("Simulation: Settings stored")
            return True

        import time

        try:
            assert self.bus is not None, "I2C bus must be initialized when not in simulation mode"
            self.bus.write_byte(self.i2c_address, DFR0971_CMD_STORE)
            time.sleep(0.02)
            logger.debug("DFR0971 settings stored to EEPROM")
            return True
        except OSError as e:
            logger.error(f"I2C error storing settings for board 0x{self.i2c_address:02X}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error storing settings: {e}")
            return False

    def close(self):
        """Close I2C connection and cleanup"""
        if self.bus and not self.simulation:
            try:
                self.bus.close()
                logger.info(f"DFR0971 I2C connection closed (address 0x{self.i2c_address:02X})")
            except Exception as e:
                logger.error(f"Error closing I2C connection: {e}")

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()


class DFR0971Manager:
    """
    Manager for multiple DFR0971 boards
    Provides unified interface to control multiple boards
    """

    def __init__(self, i2c_bus: int = 1, simulation: bool = False):
        """
        Initialize DFR0971 manager

        Args:
            i2c_bus: I2C bus number (usually 1 on Raspberry Pi)
            simulation: If True, simulate hardware without actual I2C communication
        """
        self.i2c_bus = i2c_bus
        self.simulation = simulation
        self._boards: dict[int, DFR0971Driver] = {}
        self._board_configs: dict[int, DFR0971Board] = {}
        self._i2c_to_board: dict[int, int] = {}

    def add_board(self, board_id: int, i2c_address: int, name: str | None = None) -> bool:
        """
        Add a DFR0971 board to the manager

        Args:
            board_id: Board identifier (0, 1, 2, etc.)
            i2c_address: I2C address of the board
            name: Optional name for the board

        Returns:
            True if successful, False otherwise
        """
        if board_id in self._boards:
            logger.warning(f"Board {board_id} already exists, replacing it")

        try:
            driver = DFR0971Driver(
                i2c_bus=self.i2c_bus, i2c_address=i2c_address, simulation=self.simulation
            )

            if not self.simulation:
                try:
                    driver.store_settings()
                    logger.info(
                        f"DFR0971 board {board_id} initialized at address 0x{i2c_address:02X} (range set to 10V)"
                    )
                except Exception as e:
                    logger.warning(f"Could not store settings for board {board_id}: {e}")

            self._boards[board_id] = driver
            self._board_configs[board_id] = DFR0971Board(
                board_id=board_id, i2c_address=i2c_address, name=name
            )
            self._i2c_to_board[i2c_address] = board_id

            logger.info(
                f"Added DFR0971 board {board_id} at address 0x{i2c_address:02X} ({name or 'unnamed'})"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to add DFR0971 board {board_id} at address 0x{i2c_address:02X}: {e}"
            )
            return False

    def get_board(self, board_id: int) -> DFR0971Driver | None:
        """Get driver for a specific board"""
        return self._boards.get(board_id)

    def get_board_by_i2c(self, i2c_address: int) -> DFR0971Driver | None:
        """Get driver for a board by I2C address"""
        board_id = self._i2c_to_board.get(i2c_address)
        if board_id is None:
            return None
        return self._boards.get(board_id)

    def set_intensity(
        self, board_id: int, channel: int, intensity: float, store_to_eeprom: bool = False
    ) -> bool:
        """
        Set dimming intensity for a specific board/channel

        Args:
            board_id: Board identifier
            channel: Channel number (0 or 1)
            intensity: Dimming percentage (0-100%)
            store_to_eeprom: If True, store settings to EEPROM after setting intensity

        Returns:
            True if successful, False otherwise
        """
        driver = self.get_board(board_id)
        if driver is None:
            logger.error(f"Board {board_id} not found")
            return False

        return driver.set_intensity(intensity, channel, store_to_eeprom)

    def set_voltage(
        self, board_id: int, channel: int, voltage: float, store_to_eeprom: bool = False
    ) -> bool:
        """
        Set voltage for a specific board/channel

        Args:
            board_id: Board identifier
            channel: Channel number (0 or 1)
            voltage: Output voltage (0-10V)
            store_to_eeprom: If True, store settings to EEPROM after setting voltage

        Returns:
            True if successful, False otherwise
        """
        driver = self.get_board(board_id)
        if driver is None:
            logger.error(f"Board {board_id} not found")
            return False

        return driver.set_voltage(voltage, channel, store_to_eeprom)

    def set_safety_level(self, board_id: int, channel: int, intensity: float) -> bool:
        """
        Set and save safety level to EEPROM for a specific board/channel.

        The safety level is the default intensity that will be restored on power-up
        before the service can restore from database/Redis. This should be a safe,
        low intensity value (e.g., 0-20%).

        Args:
            board_id: Board identifier
            channel: Channel number (0 or 1)
            intensity: Safety intensity level (0-100%)

        Returns:
            True if successful, False otherwise
        """
        driver = self.get_board(board_id)
        if driver is None:
            logger.error(f"Board {board_id} not found")
            return False

        success = driver.set_intensity(intensity, channel, store_to_eeprom=True)
        if success:
            logger.info(
                f"Safety level set to {intensity:.1f}% for board {board_id}, channel {channel} (saved to EEPROM)"
            )
        return success

    def get_intensity(self, board_id: int, channel: int) -> float | None:
        """Get current intensity for a specific board/channel"""
        driver = self.get_board(board_id)
        if driver is None:
            return None

        return driver.get_intensity(channel)

    def get_voltage(self, board_id: int, channel: int) -> float | None:
        """Get current voltage for a specific board/channel"""
        driver = self.get_board(board_id)
        if driver is None:
            return None

        return driver.get_voltage(channel)

    def list_boards(self) -> list[dict[str, Any]]:
        """
        List all configured boards

        Returns:
            List of board configurations
        """
        boards = []
        for board_id, config in self._board_configs.items():
            driver = self._boards.get(board_id)
            boards.append(
                {
                    "board_id": board_id,
                    "i2c_address": f"0x{config.i2c_address:02X}",
                    "name": config.name,
                    "available": driver is not None,
                }
            )
        return boards

    def close_all(self):
        """Close all board connections"""
        for driver in self._boards.values():
            driver.close()
        self._boards.clear()
        self._board_configs.clear()
        self._i2c_to_board.clear()

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close_all()
