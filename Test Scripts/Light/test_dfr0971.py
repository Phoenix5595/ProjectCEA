#!/usr/bin/env python3
"""
DFR0971 Test Script
Test script for the DFR0971 2-Channel I2C 0-10V DAC Module

Features:
- Basic voltage and intensity tests
- Ramp test (gradual intensity changes)
- Interactive mode for manual control
"""

import sys
import os
import argparse
import time
import yaml
import logging
from pathlib import Path

# Add the automation service path to sys.path to import DFR0971 driver
project_root = Path(__file__).resolve().parent.parent.parent
automation_service_path = project_root / "Infrastructure" / "automation-service"
sys.path.insert(0, str(automation_service_path))

try:
    from app.hardware.dfr0971 import DFR0971Driver
except ImportError as e:
    print(f"Error importing DFR0971Driver: {e}")
    print("Make sure the automation service path is correct.")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DFR0971Tester:
    """Test harness for DFR0971 DAC module"""
    
    def __init__(self, config_path: str = None):
        """Initialize tester with configuration"""
        self.config = self.load_config(config_path)
        self.driver = None
        
    def load_config(self, config_path: str = None) -> dict:
        """Load configuration from YAML file"""
        if config_path is None:
            config_path = Path(__file__).parent / "dfr0971_config.yaml"
        else:
            config_path = Path(config_path)
        
        if not config_path.exists():
            logger.error(f"Configuration file not found: {config_path}")
            sys.exit(1)
        
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            logger.info(f"Configuration loaded from {config_path}")
            return config
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            sys.exit(1)
    
    def initialize_driver(self):
        """Initialize the DFR0971 driver"""
        hw_config = self.config.get('hardware', {})
        i2c_bus = hw_config.get('i2c_bus', 1)
        i2c_address = hw_config.get('i2c_address', 0x58)
        simulation = hw_config.get('simulation', False)
        
        # Convert hex string to int if needed
        if isinstance(i2c_address, str):
            i2c_address = int(i2c_address, 16)
        
        logger.info(f"Initializing DFR0971 driver...")
        logger.info(f"  I2C Bus: {i2c_bus}")
        logger.info(f"  I2C Address: 0x{i2c_address:02X}")
        logger.info(f"  Simulation Mode: {simulation}")
        
        try:
            self.driver = DFR0971Driver(
                i2c_bus=i2c_bus,
                i2c_address=i2c_address,
                simulation=simulation
            )
            logger.info("DFR0971 driver initialized successfully")
            
            # Force output range to 10V and store settings
            if not simulation:
                logger.info("Ensuring output range is set to 10V...")
                # Re-initialize hardware to ensure range is set
                try:
                    self.driver._initialize_hardware()
                    # Store settings to EEPROM
                    self.driver.store_settings()
                    logger.info("Output range set to 10V and stored to EEPROM")
                except Exception as e:
                    logger.warning(f"Could not verify/store range settings: {e}")
            
            return True
        except Exception as e:
            logger.error(f"Failed to initialize DFR0971 driver: {e}")
            return False
    
    def test_voltage(self, channel: int, voltage: float) -> bool:
        """Test setting voltage on a channel"""
        logger.info(f"Testing voltage on channel {channel}: {voltage}V")
        
        # Calculate expected DAC value for debugging
        dac_value = int((voltage / 10.0) * 10000)
        logger.debug(f"  Expected DAC value: {dac_value} (0x{dac_value:04X})")
        
        success = self.driver.set_voltage(voltage, channel)
        if success:
            actual_voltage = self.driver.get_voltage(channel)
            logger.info(f"  ✓ Channel {channel} set to {actual_voltage:.2f}V (expected {voltage:.2f}V)")
            if abs(actual_voltage - voltage) > 0.1:
                logger.warning(f"  ⚠ Voltage mismatch! Expected {voltage:.2f}V, got {actual_voltage:.2f}V")
                logger.warning(f"  Check: Output range should be 10V, multimeter should read ~{voltage:.2f}V")
        else:
            logger.error(f"  ✗ Failed to set voltage on channel {channel}")
        return success
    
    def test_intensity(self, channel: int, intensity: float) -> bool:
        """Test setting intensity on a channel"""
        logger.info(f"Testing intensity on channel {channel}: {intensity}%")
        success = self.driver.set_intensity(intensity, channel)
        if success:
            actual_intensity = self.driver.get_intensity(channel)
            logger.info(f"  ✓ Channel {channel} set to {actual_intensity:.2f}%")
        else:
            logger.error(f"  ✗ Failed to set intensity on channel {channel}")
        return success
    
    def test_range_command(self):
        """Test different methods of setting the output range"""
        logger.info("=" * 60)
        logger.info("Testing Range Command Methods")
        logger.info("=" * 60)
        
        if self.driver.simulation:
            logger.warning("Cannot test range commands in simulation mode")
            return
        
        import smbus2
        bus = smbus2.SMBus(self.driver.i2c_bus)
        addr = self.driver.i2c_address
        
        # Method 1: Current method (block data)
        logger.info("\nMethod 1: write_i2c_block_data (current)")
        try:
            bus.write_i2c_block_data(addr, 0x01, [0x01])  # Set to 10V
            time.sleep(0.1)
            logger.info("  ✓ Command sent")
        except Exception as e:
            logger.error(f"  ✗ Failed: {e}")
        
        # Method 2: Byte data
        logger.info("\nMethod 2: write_byte_data")
        try:
            bus.write_byte_data(addr, 0x01, 0x01)  # Set to 10V
            time.sleep(0.1)
            logger.info("  ✓ Command sent")
        except Exception as e:
            logger.error(f"  ✗ Failed: {e}")
        
        # Method 3: Write byte to register
        logger.info("\nMethod 3: write_byte")
        try:
            bus.write_byte(addr, 0x01)  # Just the command
            time.sleep(0.1)
            logger.info("  ✓ Command sent")
        except Exception as e:
            logger.error(f"  ✗ Failed: {e}")
        
        # Now test voltage after each method
        logger.info("\nTesting voltage output after range commands...")
        test_voltage = 5.0  # Should give 5V if range is 10V
        self.driver.set_voltage(test_voltage, 0)
        logger.info(f"Set voltage to {test_voltage}V - please measure with multimeter")
        logger.info("Expected: ~5.0V if range is 10V, ~2.5V if range is 5V")
        input("Press Enter after measuring...")
        
        logger.info("=" * 60)
    
    def run_diagnostic_test(self):
        """Run diagnostic test to identify voltage output issues"""
        logger.info("=" * 60)
        logger.info("Running Diagnostic Test")
        logger.info("=" * 60)
        logger.info("This test will help identify voltage output issues")
        logger.info("")
        
        # Test various voltage values
        test_voltages = [0.0, 1.0, 2.5, 5.0, 7.5, 10.0]
        
        logger.info("Testing voltage outputs on Channel 0:")
        logger.info("Expected | Commanded | Multimeter Reading")
        logger.info("-" * 60)
        
        for voltage in test_voltages:
            # Calculate DAC value
            dac_value = int((voltage / 10.0) * 10000)
            intensity = (voltage / 10.0) * 100.0
            
            logger.info(f"\nSetting: {voltage:.2f}V ({intensity:.1f}%)")
            logger.info(f"  DAC value: {dac_value} (0x{dac_value:04X})")
            logger.info(f"  High byte: 0x{(dac_value >> 8) & 0xFF:02X}, Low byte: 0x{dac_value & 0xFF:02X}")
            
            success = self.driver.set_voltage(voltage, 0)
            if success:
                # Store settings after each set
                self.driver.store_settings()
                logger.info(f"  ✓ Command sent successfully")
                logger.info(f"  → Please measure with multimeter and note the reading")
                logger.info(f"  → Expected reading: {voltage:.2f}V")
                input("  Press Enter after measuring...")
            else:
                logger.error(f"  ✗ Failed to set voltage")
        
        logger.info("\n" + "=" * 60)
        logger.info("Diagnostic test complete")
        logger.info("=" * 60)
        logger.info("\nIf readings don't match expected values:")
        logger.info("1. Check that output range is set to 10V (not 5V)")
        logger.info("2. Verify I2C address is correct (use: i2cdetect -y 1)")
        logger.info("3. Check wiring (SDA, SCL, GND, VCC)")
        logger.info("4. Verify power supply is stable (3.3V or 5V)")
        logger.info("5. Check if multimeter is set to DC voltage mode")
        logger.info("=" * 60)
    
    def run_basic_tests(self):
        """Run basic functionality tests"""
        logger.info("=" * 60)
        logger.info("Running Basic Tests")
        logger.info("=" * 60)
        
        test_config = self.config.get('test', {})
        test_voltage = test_config.get('test_voltage', 5.0)
        test_intensity = test_config.get('test_intensity', 50.0)
        
        results = []
        
        # Test channel 0 - voltage
        logger.info("\n--- Testing Channel 0 (Voltage) ---")
        results.append(("Channel 0 Voltage", self.test_voltage(0, test_voltage)))
        time.sleep(0.5)
        
        # Test channel 0 - intensity
        logger.info("\n--- Testing Channel 0 (Intensity) ---")
        results.append(("Channel 0 Intensity", self.test_intensity(0, test_intensity)))
        time.sleep(0.5)
        
        # Test channel 1 - voltage
        logger.info("\n--- Testing Channel 1 (Voltage) ---")
        results.append(("Channel 1 Voltage", self.test_voltage(1, test_voltage)))
        time.sleep(0.5)
        
        # Test channel 1 - intensity
        logger.info("\n--- Testing Channel 1 (Intensity) ---")
        results.append(("Channel 1 Intensity", self.test_intensity(1, test_intensity)))
        time.sleep(0.5)
        
        # Test channel independence
        logger.info("\n--- Testing Channel Independence ---")
        self.driver.set_intensity(0, 0)
        self.driver.set_intensity(100, 1)
        time.sleep(0.5)
        ch0_intensity = self.driver.get_intensity(0)
        ch1_intensity = self.driver.get_intensity(1)
        independence_ok = (ch0_intensity == 0.0 and ch1_intensity == 100.0)
        results.append(("Channel Independence", independence_ok))
        if independence_ok:
            logger.info(f"  ✓ Channels are independent (Ch0: {ch0_intensity}%, Ch1: {ch1_intensity}%)")
        else:
            logger.error(f"  ✗ Channel independence test failed (Ch0: {ch0_intensity}%, Ch1: {ch1_intensity}%)")
        
        # Reset channels
        self.driver.set_intensity(0, 0)
        self.driver.set_intensity(0, 1)
        
        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("Basic Tests Summary")
        logger.info("=" * 60)
        for test_name, result in results:
            status = "✓ PASS" if result else "✗ FAIL"
            logger.info(f"  {test_name}: {status}")
        
        passed = sum(1 for _, result in results if result)
        total = len(results)
        logger.info(f"\n  Total: {passed}/{total} tests passed")
        logger.info("=" * 60)
        
        return passed == total
    
    def run_ramp_test(self, channel: int = 0):
        """Run ramp test - gradually increase then decrease intensity"""
        logger.info("=" * 60)
        logger.info(f"Running Ramp Test on Channel {channel}")
        logger.info("=" * 60)
        
        ramp_config = self.config.get('test', {}).get('ramp', {})
        step_size = ramp_config.get('step_size', 5.0)
        delay = ramp_config.get('delay', 0.2)
        start_intensity = ramp_config.get('start_intensity', 0.0)
        end_intensity = ramp_config.get('end_intensity', 100.0)
        
        logger.info(f"  Step size: {step_size}%")
        logger.info(f"  Delay: {delay}s")
        logger.info(f"  Range: {start_intensity}% to {end_intensity}%")
        
        # Ramp up
        logger.info("\n--- Ramping Up ---")
        current = start_intensity
        while current <= end_intensity:
            self.driver.set_intensity(current, channel)
            actual = self.driver.get_intensity(channel)
            logger.info(f"  Setting: {current:.1f}% → Actual: {actual:.1f}%")
            time.sleep(delay)
            current += step_size
        
        # Ensure we end at 100%
        self.driver.set_intensity(end_intensity, channel)
        time.sleep(delay)
        
        # Ramp down
        logger.info("\n--- Ramping Down ---")
        current = end_intensity
        while current >= start_intensity:
            self.driver.set_intensity(current, channel)
            actual = self.driver.get_intensity(channel)
            logger.info(f"  Setting: {current:.1f}% → Actual: {actual:.1f}%")
            time.sleep(delay)
            current -= step_size
        
        # Ensure we end at 0%
        self.driver.set_intensity(start_intensity, channel)
        logger.info("\n  ✓ Ramp test completed")
        logger.info("=" * 60)
    
    def interactive_mode(self):
        """Interactive command-line interface"""
        logger.info("=" * 60)
        logger.info("Interactive Mode")
        logger.info("=" * 60)
        logger.info("Commands:")
        logger.info("  voltage <channel> <voltage>  - Set voltage (0-10V)")
        logger.info("  intensity <channel> <percent> - Set intensity (0-100%)")
        logger.info("  status [channel]             - Get status (all channels if omitted)")
        logger.info("  ramp <channel> <start> <end> <steps> - Ramp test")
        logger.info("  store                        - Store current settings to EEPROM")
        logger.info("  help                         - Show this help")
        logger.info("  quit, exit                   - Exit interactive mode")
        logger.info("=" * 60)
        
        while True:
            try:
                command = input("\n> ").strip().split()
                if not command:
                    continue
                
                cmd = command[0].lower()
                
                if cmd in ['quit', 'exit', 'q']:
                    logger.info("Exiting interactive mode...")
                    break
                
                elif cmd == 'help' or cmd == 'h':
                    logger.info("\nCommands:")
                    logger.info("  voltage <channel> <voltage>  - Set voltage (0-10V)")
                    logger.info("  intensity <channel> <percent> - Set intensity (0-100%)")
                    logger.info("  status [channel]             - Get status (all channels if omitted)")
                    logger.info("  ramp <channel> <start> <end> <steps> - Ramp test")
                    logger.info("  store                        - Store current settings to EEPROM")
                    logger.info("  help                         - Show this help")
                    logger.info("  quit, exit                   - Exit interactive mode")
                
                elif cmd == 'voltage' or cmd == 'v':
                    if len(command) < 3:
                        logger.error("Usage: voltage <channel> <voltage>")
                        continue
                    try:
                        channel = int(command[1])
                        voltage = float(command[2])
                        if channel not in [0, 1]:
                            logger.error("Channel must be 0 or 1")
                            continue
                        if voltage < 0 or voltage > 10:
                            logger.error("Voltage must be between 0 and 10V")
                            continue
                        success = self.driver.set_voltage(voltage, channel)
                        if success:
                            actual = self.driver.get_voltage(channel)
                            logger.info(f"Channel {channel} set to {actual:.2f}V")
                        else:
                            logger.error("Failed to set voltage")
                    except ValueError as e:
                        logger.error(f"Invalid value: {e}")
                
                elif cmd == 'intensity' or cmd == 'i':
                    if len(command) < 3:
                        logger.error("Usage: intensity <channel> <percent>")
                        continue
                    try:
                        channel = int(command[1])
                        intensity = float(command[2])
                        if channel not in [0, 1]:
                            logger.error("Channel must be 0 or 1")
                            continue
                        if intensity < 0 or intensity > 100:
                            logger.error("Intensity must be between 0 and 100%")
                            continue
                        success = self.driver.set_intensity(intensity, channel)
                        if success:
                            actual = self.driver.get_intensity(channel)
                            logger.info(f"Channel {channel} set to {actual:.2f}%")
                        else:
                            logger.error("Failed to set intensity")
                    except ValueError as e:
                        logger.error(f"Invalid value: {e}")
                
                elif cmd == 'status' or cmd == 's':
                    if len(command) > 1:
                        try:
                            channel = int(command[1])
                            if channel not in [0, 1]:
                                logger.error("Channel must be 0 or 1")
                                continue
                            voltage = self.driver.get_voltage(channel)
                            intensity = self.driver.get_intensity(channel)
                            logger.info(f"Channel {channel}:")
                            logger.info(f"  Voltage: {voltage:.2f}V")
                            logger.info(f"  Intensity: {intensity:.2f}%")
                            logger.info(f"  Expected multimeter reading: ~{voltage:.2f}V")
                        except ValueError:
                            logger.error("Invalid channel number")
                    else:
                        for ch in [0, 1]:
                            voltage = self.driver.get_voltage(ch)
                            intensity = self.driver.get_intensity(ch)
                            logger.info(f"Channel {ch}: {voltage:.2f}V ({intensity:.2f}%) - Multimeter should read ~{voltage:.2f}V")
                
                elif cmd == 'store' or cmd == 'save':
                    logger.info("Storing settings to EEPROM...")
                    success = self.driver.store_settings()
                    if success:
                        logger.info("  ✓ Settings stored successfully")
                    else:
                        logger.error("  ✗ Failed to store settings")
                
                elif cmd == 'ramp' or cmd == 'r':
                    if len(command) < 5:
                        logger.error("Usage: ramp <channel> <start> <end> <steps>")
                        continue
                    try:
                        channel = int(command[1])
                        start = float(command[2])
                        end = float(command[3])
                        steps = int(command[4])
                        if channel not in [0, 1]:
                            logger.error("Channel must be 0 or 1")
                            continue
                        if steps < 1:
                            logger.error("Steps must be at least 1")
                            continue
                        
                        step_size = (end - start) / steps
                        delay = 0.2
                        
                        logger.info(f"Ramping channel {channel} from {start}% to {end}% in {steps} steps...")
                        current = start
                        for i in range(steps + 1):
                            self.driver.set_intensity(current, channel)
                            actual = self.driver.get_intensity(channel)
                            logger.info(f"  Step {i+1}/{steps+1}: {current:.1f}% → {actual:.1f}%")
                            time.sleep(delay)
                            current += step_size
                        logger.info("Ramp completed")
                    except ValueError as e:
                        logger.error(f"Invalid value: {e}")
                
                else:
                    logger.error(f"Unknown command: {cmd}. Type 'help' for available commands.")
            
            except KeyboardInterrupt:
                logger.info("\nExiting interactive mode...")
                break
            except Exception as e:
                logger.error(f"Error: {e}")
    
    def cleanup(self):
        """Cleanup resources"""
        if self.driver:
            logger.info("Cleaning up...")
            # Set all channels to 0 before closing
            self.driver.set_intensity(0, 0)
            self.driver.set_intensity(0, 1)
            self.driver.close()
            logger.info("Cleanup complete")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Test script for DFR0971 2-Channel I2C 0-10V DAC Module'
    )
    parser.add_argument(
        '--config',
        type=str,
        default=None,
        help='Path to configuration YAML file (default: dfr0971_config.yaml in script directory)'
    )
    parser.add_argument(
        '--simulation',
        action='store_true',
        help='Force simulation mode (overrides config)'
    )
    parser.add_argument(
        '--basic',
        action='store_true',
        help='Run basic tests only'
    )
    parser.add_argument(
        '--ramp',
        action='store_true',
        help='Run ramp test only'
    )
    parser.add_argument(
        '--interactive',
        action='store_true',
        help='Start interactive mode only'
    )
    parser.add_argument(
        '--channel',
        type=int,
        default=0,
        choices=[0, 1],
        help='Channel for ramp test (default: 0)'
    )
    parser.add_argument(
        '--diagnostic',
        action='store_true',
        help='Run diagnostic test to identify voltage issues'
    )
    parser.add_argument(
        '--test-range',
        action='store_true',
        help='Test different range command methods'
    )
    
    args = parser.parse_args()
    
    # Initialize tester
    tester = DFR0971Tester(args.config)
    
    # Override simulation mode if requested
    if args.simulation:
        tester.config['hardware']['simulation'] = True
    
    # Initialize driver
    if not tester.initialize_driver():
        logger.error("Failed to initialize driver. Exiting.")
        sys.exit(1)
    
    try:
        # Run requested tests
        if args.test_range:
            tester.test_range_command()
        elif args.diagnostic:
            tester.run_diagnostic_test()
        elif args.interactive:
            tester.interactive_mode()
        elif args.ramp:
            tester.run_ramp_test(args.channel)
        elif args.basic:
            tester.run_basic_tests()
        else:
            # Run all tests by default
            tester.run_basic_tests()
            logger.info("\n")
            tester.run_ramp_test(0)
            logger.info("\n")
            logger.info("Entering interactive mode...")
            logger.info("(Press Ctrl+C to skip)")
            try:
                tester.interactive_mode()
            except KeyboardInterrupt:
                logger.info("\nSkipping interactive mode")
    
    finally:
        tester.cleanup()


if __name__ == '__main__':
    main()

