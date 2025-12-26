"""Soil sensor reader for DFRobot RS485 4-in-1 sensor."""
import logging
from typing import Dict, Optional
from .modbus_rtu import ModbusRTU

logger = logging.getLogger(__name__)


class SoilSensorReader:
    """Main class for reading DFRobot RS485 4-in-1 soil sensor data"""
    
    def __init__(self, port: str, slave_id: int = 1, baudrate: int = 9600):
        """
        Initialize soil sensor reader
        
        Args:
            port: Serial port path for RS485 connection
            slave_id: Modbus slave ID of the soil sensor
            baudrate: Serial baudrate
        """
        self.port = port
        self.slave_id = slave_id
        self.baudrate = baudrate
        self.modbus: Optional[ModbusRTU] = None
        
        # DFRobot SEN0604 register addresses (from documentation)
        # These may need adjustment based on actual sensor documentation
        self.REGISTERS = {
            'temperature': 0x0000,  # Temperature register
            'humidity': 0x0001,     # Humidity register
            'ec': 0x0002,           # EC register
            'ph': 0x0003             # pH register
        }
        
        # Scaling factors for converting register values to actual measurements
        # These may need adjustment based on actual sensor documentation
        self.SCALING = {
            'temperature': 0.1,   # Register value * 0.1 = temperature in °C
            'humidity': 0.1,      # Register value * 0.1 = humidity in %
            'ec': 1.0,            # Register value = EC in µS/cm (or may need scaling)
            'ph': 0.01            # Register value * 0.01 = pH value
        }
        
    def connect(self):
        """Connect to soil sensor via RS485"""
        if self.modbus is None:
            self.modbus = ModbusRTU(self.port, self.baudrate)
        self.modbus.connect()
        
    def disconnect(self):
        """Disconnect from soil sensor"""
        if self.modbus:
            self.modbus.disconnect()
            
    def read_all_parameters(self) -> Optional[Dict[str, float]]:
        """
        Read all soil parameters (temperature, humidity, EC, pH)
        
        Returns:
            Dictionary with sensor readings or None if error
        """
        if not self.modbus or not self.modbus.ser or not self.modbus.ser.is_open:
            logger.error("Not connected to soil sensor")
            return None
            
        readings = {}
        
        # Read all registers in one request if possible
        # Reading 4 consecutive registers starting from temperature
        registers = self.modbus.read_holding_registers(
            self.slave_id,
            self.REGISTERS['temperature'],
            4
        )
        
        if registers is None or len(registers) < 4:
            logger.error("Failed to read soil sensor registers")
            return None
            
        # Convert register values to actual measurements
        readings['temperature'] = registers[0] * self.SCALING['temperature']
        readings['humidity'] = registers[1] * self.SCALING['humidity']
        readings['ec'] = registers[2] * self.SCALING['ec']
        readings['ph'] = registers[3] * self.SCALING['ph']
        
        return readings
    
    def read_temperature(self) -> Optional[float]:
        """Read only temperature"""
        if not self.modbus or not self.modbus.ser or not self.modbus.ser.is_open:
            return None
        registers = self.modbus.read_holding_registers(
            self.slave_id,
            self.REGISTERS['temperature'],
            1
        )
        return registers[0] * self.SCALING['temperature'] if registers else None
    
    def read_humidity(self) -> Optional[float]:
        """Read only humidity"""
        if not self.modbus or not self.modbus.ser or not self.modbus.ser.is_open:
            return None
        registers = self.modbus.read_holding_registers(
            self.slave_id,
            self.REGISTERS['humidity'],
            1
        )
        return registers[0] * self.SCALING['humidity'] if registers else None
    
    def read_ec(self) -> Optional[float]:
        """Read only EC"""
        if not self.modbus or not self.modbus.ser or not self.modbus.ser.is_open:
            return None
        registers = self.modbus.read_holding_registers(
            self.slave_id,
            self.REGISTERS['ec'],
            1
        )
        return registers[0] * self.SCALING['ec'] if registers else None
    
    def read_ph(self) -> Optional[float]:
        """Read only pH"""
        if not self.modbus or not self.modbus.ser or not self.modbus.ser.is_open:
            return None
        registers = self.modbus.read_holding_registers(
            self.slave_id,
            self.REGISTERS['ph'],
            1
        )
        return registers[0] * self.SCALING['ph'] if registers else None

