"""MODBUS-RTU protocol implementation for RS485 communication."""
import serial
import struct
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ModbusRTU:
    """Modbus RTU protocol implementation for RS485 communication"""
    
    def __init__(self, port: str, baudrate: int = 9600, timeout: float = 1.0):
        """
        Initialize Modbus RTU communication
        
        Args:
            port: Serial port path (e.g., '/dev/ttyUSB0', '/dev/serial0')
            baudrate: Serial baudrate (default 9600)
            timeout: Serial read timeout in seconds
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        
    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.disconnect()
        
    def connect(self):
        """Open serial connection"""
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            logger.info(f"Connected to {self.port} at {self.baudrate} baud")
        except serial.SerialException as e:
            logger.error(f"Failed to open serial port {self.port}: {e}")
            raise
            
    def disconnect(self):
        """Close serial connection"""
        if self.ser and self.ser.is_open:
            self.ser.close()
            logger.info("Serial connection closed")
            
    def _calculate_crc16(self, data: bytes) -> int:
        """Calculate Modbus RTU CRC16 checksum"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc
    
    def _add_crc(self, data: bytes) -> bytes:
        """Add CRC16 checksum to data"""
        crc = self._calculate_crc16(data)
        return data + struct.pack('<H', crc)
    
    def _verify_crc(self, data: bytes) -> bool:
        """Verify CRC16 checksum of response"""
        if len(data) < 3:
            return False
        received_crc = struct.unpack('<H', data[-2:])[0]
        calculated_crc = self._calculate_crc16(data[:-2])
        return received_crc == calculated_crc
    
    def read_holding_registers(self, slave_id: int, start_address: int, 
                               quantity: int) -> Optional[list]:
        """
        Read holding registers using Modbus RTU
        
        Args:
            slave_id: Modbus slave/device ID
            start_address: Starting register address
            quantity: Number of registers to read
            
        Returns:
            List of register values or None if error
        """
        if not self.ser or not self.ser.is_open:
            logger.error("Serial port not open")
            return None
            
        # Modbus function code 0x03: Read Holding Registers
        request = struct.pack('>BBHH', slave_id, 0x03, start_address, quantity)
        request = self._add_crc(request)
        
        try:
            # Clear any pending data
            self.ser.reset_input_buffer()
            
            # Send request
            self.ser.write(request)
            self.ser.flush()
            
            # Read response
            # Response format: [Slave ID][Function][Byte Count][Data...][CRC]
            response = self.ser.read(3)  # Read header (ID, function, byte count)
            if len(response) < 3:
                logger.error("Incomplete response header")
                return None
                
            slave_id_resp, function, byte_count = struct.unpack('>BBB', response)
            
            if slave_id_resp != slave_id:
                logger.error(f"Slave ID mismatch: expected {slave_id}, got {slave_id_resp}")
                return None
                
            if function != 0x03:
                if function == (0x03 | 0x80):  # Error response
                    error_code = self.ser.read(1)
                    logger.error(f"Modbus error code: {error_code.hex()}")
                    return None
                logger.error(f"Unexpected function code: {function}")
                return None
            
            # Read data and CRC
            data = self.ser.read(byte_count + 2)
            if len(data) < byte_count + 2:
                logger.error("Incomplete response data")
                return None
                
            response = response + data
            
            # Verify CRC
            if not self._verify_crc(response):
                logger.error("CRC verification failed")
                return None
            
            # Extract register values
            register_data = data[:-2]  # Exclude CRC
            registers = []
            for i in range(0, len(register_data), 2):
                value = struct.unpack('>H', register_data[i:i+2])[0]
                registers.append(value)
                
            return registers
            
        except serial.SerialException as e:
            logger.error(f"Serial communication error: {e}")
            return None
        except Exception as e:
            logger.error(f"Error reading registers: {e}")
            return None

