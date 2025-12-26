#!/usr/bin/env python3
"""
MCP23017 I2C Relay Driver
Controls MCP23017 16-channel I/O expander for relay control
Supports simulation mode when hardware is not connected
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# MCP23017 Register Addresses
MCP23017_IODIRA = 0x00  # I/O Direction Register A
MCP23017_IODIRB = 0x01  # I/O Direction Register B
MCP23017_GPIOA = 0x12   # GPIO Register A
MCP23017_GPIOB = 0x13   # GPIO Register B
MCP23017_OLATA = 0x14   # Output Latch Register A
MCP23017_OLATB = 0x15   # Output Latch Register B


class MCP23017Driver:
    """
    MCP23017 I2C Relay Driver
    Provides interface to control 16-channel relay board via I2C
    """
    
    def __init__(self, i2c_bus: int = 1, i2c_address: int = 0x20, simulation: bool = False):
        """
        Initialize MCP23017 driver
        
        Args:
            i2c_bus: I2C bus number (usually 1 on Raspberry Pi)
            i2c_address: I2C address of MCP23017 (default 0x20)
            simulation: If True, simulate hardware without actual I2C communication
        """
        self.i2c_bus = i2c_bus
        self.i2c_address = i2c_address
        self.simulation = simulation
        self.bus = None
        self._channel_states = [False] * 16  # Track state of all 16 channels
        
        if not simulation:
            try:
                import smbus2
                self.bus = smbus2.SMBus(i2c_bus)
                self._initialize_hardware()
                logger.info(f"MCP23017 initialized on I2C bus {i2c_bus}, address 0x{i2c_address:02X}")
            except ImportError:
                logger.warning("smbus2 not available, falling back to simulation mode")
                self.simulation = True
            except Exception as e:
                logger.error(f"Failed to initialize MCP23017 hardware: {e}")
                logger.warning("Falling back to simulation mode")
                self.simulation = True
        
        if self.simulation:
            logger.info("MCP23017 running in simulation mode (no hardware connected)")
    
    def _initialize_hardware(self):
        """Initialize MCP23017 hardware - set all pins as outputs"""
        if self.simulation:
            return
        
        try:
            # Set all pins as outputs (0 = output, 1 = input)
            # Port A (channels 0-7)
            self.bus.write_byte_data(self.i2c_address, MCP23017_IODIRA, 0x00)
            # Port B (channels 8-15)
            self.bus.write_byte_data(self.i2c_address, MCP23017_IODIRB, 0x00)
            # Initialize all outputs to LOW (relays OFF)
            self.bus.write_byte_data(self.i2c_address, MCP23017_GPIOA, 0x00)
            self.bus.write_byte_data(self.i2c_address, MCP23017_GPIOB, 0x00)
        except Exception as e:
            logger.error(f"Error initializing MCP23017 hardware: {e}")
            raise
    
    def set_channel(self, channel: int, state: bool) -> bool:
        """
        Set a relay channel on or off
        
        Args:
            channel: Channel number (0-15)
            state: True = ON, False = OFF
        
        Returns:
            True if successful, False otherwise
        """
        if channel < 0 or channel > 15:
            logger.error(f"Invalid channel number: {channel} (must be 0-15)")
            return False
        
        try:
            if self.simulation:
                self._channel_states[channel] = state
                logger.debug(f"Simulation: Channel {channel} set to {'ON' if state else 'OFF'}")
                return True
            
            # Determine which port (A or B) and bit position
            if channel < 8:
                # Port A (channels 0-7)
                port = MCP23017_GPIOA
                bit = channel
            else:
                # Port B (channels 8-15)
                port = MCP23017_GPIOB
                bit = channel - 8
            
            # Read current port state
            current_state = self.bus.read_byte_data(self.i2c_address, port)
            
            # Set or clear the bit
            if state:
                new_state = current_state | (1 << bit)
            else:
                new_state = current_state & ~(1 << bit)
            
            # Write new state
            self.bus.write_byte_data(self.i2c_address, port, new_state)
            self._channel_states[channel] = state
            
            logger.debug(f"Channel {channel} set to {'ON' if state else 'OFF'}")
            return True
            
        except Exception as e:
            logger.error(f"Error setting channel {channel}: {e}")
            return False
    
    def get_channel(self, channel: int) -> Optional[bool]:
        """
        Get current state of a relay channel
        
        Args:
            channel: Channel number (0-15)
        
        Returns:
            True if ON, False if OFF, None on error
        """
        if channel < 0 or channel > 15:
            logger.error(f"Invalid channel number: {channel} (must be 0-15)")
            return None
        
        try:
            if self.simulation:
                return self._channel_states[channel]
            
            # Determine which port (A or B) and bit position
            if channel < 8:
                port = MCP23017_GPIOA
                bit = channel
            else:
                port = MCP23017_GPIOB
                bit = channel - 8
            
            # Read port state
            port_state = self.bus.read_byte_data(self.i2c_address, port)
            
            # Extract bit
            state = bool(port_state & (1 << bit))
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
                # On error, return current tracked state
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
        if self.bus and not self.simulation:
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

