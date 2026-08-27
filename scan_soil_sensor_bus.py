#!/usr/bin/env python3
"""Soil Sensor RS485 Bus Scanner

Scans the RS485 Modbus bus to find and monitor DFRobot RS485 4-in-1 soil sensors.
Supports both single scan mode and continuous monitoring mode.
"""

import argparse
from datetime import datetime
import struct
import sys
import time

import serial

# Colors for terminal output
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
RED = "\033[0;31m"
CYAN = "\033[0;36m"
BLUE = "\033[0;34m"
MAGENTA = "\033[0;35m"
NC = "\033[0m"  # No Color


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
                timeout=self.timeout,
            )
        except serial.SerialException as e:
            print(f"{RED}Error: Failed to open serial port {self.port}: {e}{NC}")
            raise

    def disconnect(self):
        """Close serial connection"""
        if self.ser and self.ser.is_open:
            self.ser.close()

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
        return data + struct.pack("<H", crc)

    def _verify_crc(self, data: bytes) -> bool:
        """Verify CRC16 checksum of response"""
        if len(data) < 3:
            return False
        received_crc = struct.unpack("<H", data[-2:])[0]
        calculated_crc = self._calculate_crc16(data[:-2])
        return received_crc == calculated_crc

    def read_holding_registers(
        self, slave_id: int, start_address: int, quantity: int
    ) -> list[int] | None:
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
            return None

        # Modbus function code 0x03: Read Holding Registers
        request = struct.pack(">BBHH", slave_id, 0x03, start_address, quantity)
        request = self._add_crc(request)

        try:
            # Clear any pending data
            self.ser.reset_input_buffer()

            # Send request
            self.ser.write(request)
            self.ser.flush()

            # Small delay to allow response
            time.sleep(0.05)

            # Read response
            # Response format: [Slave ID][Function][Byte Count][Data...][CRC]
            response = self.ser.read(3)  # Read header (ID, function, byte count)
            if len(response) < 3:
                return None

            slave_id_resp, function, byte_count = struct.unpack(">BBB", response)

            if slave_id_resp != slave_id:
                return None

            if function != 0x03:
                if function == (0x03 | 0x80):  # Error response
                    return None
                return None

            # Read data and CRC
            data = self.ser.read(byte_count + 2)
            if len(data) < byte_count + 2:
                return None

            response = response + data

            # Verify CRC
            if not self._verify_crc(response):
                return None

            # Extract register values
            register_data = data[:-2]  # Exclude CRC
            registers = []
            for i in range(0, len(register_data), 2):
                value = struct.unpack(">H", register_data[i : i + 2])[0]
                registers.append(value)

            return registers

        except serial.SerialException:
            return None
        except Exception:
            return None


class SoilSensorScanner:
    """Scanner for DFRobot RS485 4-in-1 soil sensors"""

    # Register addresses (from DFRobot SEN0604 documentation)
    REGISTERS = {"temperature": 0x0000, "humidity": 0x0001, "ec": 0x0002, "ph": 0x0003}

    # Scaling factors (verified from DFRobot documentation)
    SCALING = {
        "temperature": 0.1,  # Register value * 0.1 = temperature in °C
        "humidity": 0.1,  # Register value * 0.1 = humidity in %
        "ec": 1.0,  # Register value = EC in µS/cm
        "ph": 0.1,  # Register value * 0.1 = pH value
    }

    def __init__(self, port: str = "/dev/serial0", baudrate: int = 9600):
        """Initialize scanner"""
        self.port = port
        self.baudrate = baudrate
        self.modbus = None

    def test_serial_port(self) -> bool:
        """Test if serial port can be opened and basic communication works."""
        print(f"{CYAN}Testing serial port connection...{NC}")
        print(f"  Port: {self.port}")
        print(f"  Baudrate: {self.baudrate}\n")

        try:
            # Try to open port
            test_modbus = ModbusRTU(self.port, self.baudrate, timeout=0.5)
            test_modbus.connect()
            print(f"{GREEN}✓ Serial port opened successfully{NC}")

            # Test if port is readable/writable
            if test_modbus.ser and test_modbus.ser.is_open:
                print(f"{GREEN}✓ Port is open and ready{NC}")
                print(f"  Port settings: {test_modbus.ser.get_settings()}")

                # Try to flush buffers
                test_modbus.ser.reset_input_buffer()
                test_modbus.ser.reset_output_buffer()
                print(f"{GREEN}✓ Buffers flushed{NC}")

                test_modbus.disconnect()
                return True
            else:
                print(f"{RED}✗ Port opened but not ready{NC}")
                test_modbus.disconnect()
                return False

        except serial.SerialException as e:
            print(f"{RED}✗ Failed to open serial port: {e}{NC}")
            print(f"\n{YELLOW}Troubleshooting:{NC}")
            print(f"  1. Check if port exists: ls -l {self.port}")
            print("  2. Check permissions: groups (should include 'dialout')")
            print(f"  3. Check if another process is using the port: lsof {self.port}")
            print("  4. Try different port: /dev/ttyUSB0, /dev/ttyAMA0, /dev/ttyS0")
            return False
        except Exception as e:
            print(f"{RED}✗ Unexpected error: {e}{NC}")
            return False

    def scan_bus(self, start_id: int = 1, end_id: int = 254, verbose: bool = False) -> list[int]:
        """
        Scan Modbus bus for active sensors

        Args:
            start_id: Starting Modbus ID to scan
            end_id: Ending Modbus ID to scan
            verbose: Show detailed information for each attempt

        Returns:
            List of found Modbus IDs
        """
        found_sensors = []

        print(f"{CYAN}Scanning RS485 Modbus bus (IDs {start_id}-{end_id})...{NC}")
        print(f"{CYAN}Port: {self.port}, Baudrate: {self.baudrate}{NC}\n")

        try:
            with ModbusRTU(self.port, self.baudrate, timeout=0.5) as modbus:
                for slave_id in range(start_id, end_id + 1):
                    # Try to read first register (temperature) to detect sensor
                    registers = modbus.read_holding_registers(slave_id, 0x0000, 1)
                    if registers is not None:
                        found_sensors.append(slave_id)
                        print(
                            f"{GREEN}✓ Found sensor at Modbus ID {slave_id} (value: {registers[0]}){NC}"
                        )
                    else:
                        if verbose and slave_id <= 5:  # Show first few attempts in verbose mode
                            print(f"{YELLOW}  ID {slave_id}: No response{NC}")
                        # Show progress for every 10 IDs
                        if slave_id % 10 == 0:
                            print(f"{YELLOW}Scanning... ID {slave_id}/{end_id}{NC}", end="\r")

        except serial.SerialException as e:
            print(f"{RED}Error: Failed to open serial port: {e}{NC}")
            print(
                f"{YELLOW}Make sure the port exists and you have permissions (check dialout group){NC}"
            )
            return []
        except KeyboardInterrupt:
            print(f"\n{YELLOW}Scan interrupted by user{NC}")
            return found_sensors

        print(f"\n{CYAN}Scan complete. Found {len(found_sensors)} sensor(s): {found_sensors}{NC}\n")
        return found_sensors

    def read_sensor(self, slave_id: int) -> dict[str, float] | None:
        """
        Read all parameters from a sensor

        Args:
            slave_id: Modbus slave ID

        Returns:
            Dictionary with sensor readings or None if error
        """
        if self.modbus is None:
            return None

        # Read all 4 registers in one request
        registers = self.modbus.read_holding_registers(slave_id, 0x0000, 4)

        if registers is None or len(registers) < 4:
            return None

        # Convert register values to actual measurements
        readings = {
            "temperature": registers[0] * self.SCALING["temperature"],
            "humidity": registers[1] * self.SCALING["humidity"],
            "ec": registers[2] * self.SCALING["ec"],
            "ph": registers[3] * self.SCALING["ph"],
        }

        return readings

    def display_reading(self, slave_id: int, readings: dict[str, float]):
        """Display sensor reading in formatted output"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(
            f"{BLUE}[{timestamp}]{NC} {CYAN}Modbus ID {slave_id}:{NC} "
            f"{GREEN}T={readings['temperature']:.1f}°C{NC} "
            f"{CYAN}H={readings['humidity']:.1f}%{NC} "
            f"{YELLOW}EC={readings['ec']:.0f}µS/cm{NC} "
            f"{MAGENTA}pH={readings['ph']:.2f}{NC}"
        )

    def monitor_sensors(self, sensor_ids: list[int], interval: float = 5.0):
        """
        Continuously monitor sensors

        Args:
            sensor_ids: List of Modbus IDs to monitor
            interval: Update interval in seconds
        """
        if not sensor_ids:
            print(f"{RED}No sensors to monitor{NC}")
            return

        print(f"{CYAN}Monitoring {len(sensor_ids)} sensor(s) every {interval} seconds...{NC}")
        print(f"{CYAN}Press Ctrl+C to stop{NC}\n")

        try:
            with ModbusRTU(self.port, self.baudrate, timeout=1.0) as modbus:
                self.modbus = modbus

                while True:
                    for slave_id in sensor_ids:
                        readings = self.read_sensor(slave_id)
                        if readings:
                            self.display_reading(slave_id, readings)
                        else:
                            print(
                                f"{RED}[{datetime.now().strftime('%H:%M:%S')}] "
                                f"Modbus ID {slave_id}: {RED}Read failed{NC}"
                            )

                    if len(sensor_ids) > 1:
                        print()  # Blank line between cycles

                    time.sleep(interval)

        except serial.SerialException as e:
            print(f"{RED}Error: Serial communication failed: {e}{NC}")
        except KeyboardInterrupt:
            print(f"\n{YELLOW}Monitoring stopped{NC}")


def parse_scan_range(range_str: str) -> tuple[int, int]:
    """Parse scan range string (e.g., '1-4' or '1-254')"""
    try:
        parts = range_str.split("-")
        if len(parts) != 2:
            raise ValueError
        start = int(parts[0])
        end = int(parts[1])
        if start < 1 or start > 254 or end < 1 or end > 254 or start > end:
            raise ValueError
        return start, end
    except (ValueError, IndexError):
        print(f"{RED}Error: Invalid scan range '{range_str}'. Use format 'start-end' (1-254){NC}")
        sys.exit(1)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Scan and monitor DFRobot RS485 4-in-1 soil sensors on Modbus bus",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run diagnostic tests (check hardware/connection)
  %(prog)s --test

  # Continuous monitoring (default - just run the script)
  %(prog)s

  # Single scan to find all sensors and exit
  %(prog)s --single

  # Scan specific Modbus ID range with verbose output
  %(prog)s --scan-range 1-4 --verbose

  # Use different serial port
  %(prog)s --port /dev/ttyUSB0

  # Custom update interval (default is 5 seconds)
  %(prog)s --interval 10
        """,
    )

    parser.add_argument(
        "--port",
        type=str,
        default="/dev/serial0",
        help="Serial port path (default: /dev/serial0 for GPIO UART)",
    )

    parser.add_argument(
        "--baudrate", type=int, default=9600, help="Serial baudrate (default: 9600)"
    )

    parser.add_argument(
        "--scan-range", type=str, default="1-254", help="Modbus ID range to scan (default: 1-254)"
    )

    parser.add_argument(
        "--single",
        action="store_true",
        help="Single scan mode - find all sensors and exit (default is continuous monitoring)",
    )

    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Update interval in seconds for continuous mode (default: 5.0)",
    )

    parser.add_argument(
        "--test",
        action="store_true",
        help="Run diagnostic tests (serial port, hardware connection)",
    )

    parser.add_argument(
        "--verbose", action="store_true", help="Verbose output (show detailed scan information)"
    )

    args = parser.parse_args()

    # Parse scan range
    start_id, end_id = parse_scan_range(args.scan_range)

    # Create scanner
    scanner = SoilSensorScanner(port=args.port, baudrate=args.baudrate)

    # Run diagnostic tests if requested
    if args.test:
        print(f"{CYAN}{'=' * 60}{NC}")
        print(f"{CYAN}Soil Sensor Diagnostic Tests{NC}")
        print(f"{CYAN}{'=' * 60}{NC}\n")

        # Test 1: Serial port
        port_ok = scanner.test_serial_port()
        print()

        if not port_ok:
            print(f"{RED}Serial port test failed. Fix this before continuing.{NC}\n")
            sys.exit(1)

        # Test 2: Try to read from common Modbus IDs
        print(f"{CYAN}Testing communication with common Modbus IDs...{NC}")
        test_ids = [1, 2, 3, 4]
        found_any = False

        try:
            with ModbusRTU(args.port, args.baudrate, timeout=1.0) as modbus:
                for test_id in test_ids:
                    print(f"  Testing Modbus ID {test_id}...", end=" ")
                    registers = modbus.read_holding_registers(test_id, 0x0000, 1)
                    if registers is not None:
                        print(f"{GREEN}✓ Response received (value: {registers[0]}){NC}")
                        found_any = True
                    else:
                        print(f"{YELLOW}✗ No response{NC}")
                    time.sleep(0.2)  # Small delay between attempts
        except Exception as e:
            print(f"{RED}Error during communication test: {e}{NC}")

        print()

        if not found_any:
            print(f"{YELLOW}No sensors responded. Possible issues:{NC}")
            print(
                f"  1. {YELLOW}Voltage:{NC} Check sensor power supply (should be 5V or 12V depending on sensor)"
            )
            print(
                f"  2. {YELLOW}Wiring:{NC} Verify A/B wires are connected correctly (not swapped)"
            )
            print(
                f"  3. {YELLOW}Modbus ID:{NC} Sensor might have different ID (try scanning full range)"
            )
            print(
                f"  4. {YELLOW}Baudrate:{NC} Sensor might use different baudrate (try 2400, 4800, 9600, 19200)"
            )
            print(
                f"  5. {YELLOW}Board:{NC} Test with multimeter - check if MAX13487 is getting power"
            )
            print(f"  6. {YELLOW}Termination:{NC} Ensure 120Ω termination resistors at bus ends")
            print(f"  7. {YELLOW}Ground:{NC} Check common ground between Pi and sensor")
            print()
            print(f"{CYAN}Next steps:{NC}")
            print(f"  - Run full scan: {sys.argv[0]} --scan-range 1-254")
            print("  - Check sensor documentation for default Modbus ID")
            print("  - Verify sensor LED/power indicator is on")
        else:
            print(f"{GREEN}Communication test successful! Sensor(s) detected.{NC}")

        print()
        sys.exit(0)

    # Keep scanning until sensors are found
    found_sensors = []
    scan_count = 0

    print(f"{CYAN}Scanning for sensors... (will retry until sensors are found){NC}")
    print(f"{CYAN}Press Ctrl+C to stop{NC}\n")

    while not found_sensors:
        scan_count += 1
        if scan_count > 1:
            print(f"{YELLOW}No sensors found. Retrying scan #{scan_count}...{NC}\n")
            time.sleep(2)  # Brief pause between scans

        found_sensors = scanner.scan_bus(start_id, end_id, verbose=args.verbose)

        if not found_sensors:
            print(f"{YELLOW}No sensors found on the bus.{NC}")
            print(f"{YELLOW}Check:{NC}")
            print(f"  - Serial port: {args.port}")
            print(f"  - Baudrate: {args.baudrate}")
            print("  - Wiring connections")
            print("  - Sensor power")
            print(f"  - Modbus IDs in scan range ({start_id}-{end_id})")
            print(f"{CYAN}Waiting 5 seconds before next scan...{NC}\n")
            time.sleep(5)

    # Sensors found! Now proceed with reading
    if args.single:
        # Single scan mode - read each sensor once and exit
        print(f"{CYAN}Reading sensor data...{NC}\n")
        try:
            with ModbusRTU(args.port, args.baudrate, timeout=1.0) as modbus:
                scanner.modbus = modbus
                for slave_id in found_sensors:
                    readings = scanner.read_sensor(slave_id)
                    if readings:
                        scanner.display_reading(slave_id, readings)
                    else:
                        print(f"{RED}Modbus ID {slave_id}: Read failed{NC}")
        except serial.SerialException as e:
            print(f"{RED}Error: Serial communication failed: {e}{NC}")
            sys.exit(1)
    else:
        # Continuous monitoring mode (default)
        scanner.monitor_sensors(found_sensors, interval=args.interval)


if __name__ == "__main__":
    main()
