#!/usr/bin/env python3
"""
Diagnostic script to test DFR0971 board at address 0x59
This script will help identify why board 0x59 is not working
"""

import sys
import time
import logging
from pathlib import Path

# Add the automation service to the path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "Infrastructure" / "automation-service"))

from app.hardware.dfr0971 import DFR0971Driver

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_board_0x59():
    """Test board at address 0x59"""
    logger.info("=" * 60)
    logger.info("Testing DFR0971 Board at Address 0x59")
    logger.info("=" * 60)
    
    # Test 1: Check if board is detected on I2C bus
    logger.info("\n[TEST 1] Checking I2C bus detection...")
    try:
        import subprocess
        result = subprocess.run(['i2cdetect', '-y', '1'], capture_output=True, text=True)
        if '59' in result.stdout:
            logger.info("✓ Board 0x59 detected on I2C bus")
        else:
            logger.error("✗ Board 0x59 NOT detected on I2C bus!")
            logger.error("I2C scan output:")
            logger.error(result.stdout)
            return False
    except Exception as e:
        logger.error(f"✗ Failed to scan I2C bus: {e}")
        return False
    
    # Test 2: Initialize driver
    logger.info("\n[TEST 2] Initializing DFR0971 driver...")
    try:
        driver = DFR0971Driver(
            i2c_bus=1,
            i2c_address=0x59,
            simulation=False
        )
        
        if driver.simulation:
            logger.error("✗ Driver fell back to simulation mode!")
            logger.error("This means hardware initialization failed")
            return False
        else:
            logger.info("✓ Driver initialized successfully (NOT in simulation mode)")
    except Exception as e:
        logger.error(f"✗ Failed to initialize driver: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 3: Check if range was set
    logger.info("\n[TEST 3] Checking output range...")
    if driver._range_set:
        logger.info("✓ Output range is set")
    else:
        logger.warning("⚠ Output range is NOT set - this could be a problem")
    
    # Test 4: Try to set output range explicitly
    logger.info("\n[TEST 4] Setting output range to 10V...")
    try:
        driver._set_output_range(0x11)  # 10V range
        logger.info("✓ Output range set successfully")
    except Exception as e:
        logger.error(f"✗ Failed to set output range: {e}")
        return False
    
    # Test 5: Store settings to EEPROM
    logger.info("\n[TEST 5] Storing settings to EEPROM...")
    try:
        success = driver.store_settings()
        if success:
            logger.info("✓ Settings stored to EEPROM")
        else:
            logger.warning("⚠ Failed to store settings to EEPROM")
    except Exception as e:
        logger.error(f"✗ Error storing settings: {e}")
    
    # Test 6: Test channel 0
    logger.info("\n[TEST 6] Testing Channel 0...")
    try:
        logger.info("Setting channel 0 to 5.0V (50% intensity)...")
        success = driver.set_voltage(5.0, channel=0)
        if success:
            logger.info("✓ Channel 0 command sent successfully")
            time.sleep(1)
            voltage = driver.get_voltage(0)
            logger.info(f"  Reported voltage: {voltage}V")
        else:
            logger.error("✗ Failed to set channel 0 voltage")
            return False
    except Exception as e:
        logger.error(f"✗ Error testing channel 0: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 7: Test channel 1
    logger.info("\n[TEST 7] Testing Channel 1...")
    try:
        logger.info("Setting channel 1 to 5.0V (50% intensity)...")
        success = driver.set_voltage(5.0, channel=1)
        if success:
            logger.info("✓ Channel 1 command sent successfully")
            time.sleep(1)
            voltage = driver.get_voltage(1)
            logger.info(f"  Reported voltage: {voltage}V")
        else:
            logger.error("✗ Failed to set channel 1 voltage")
            return False
    except Exception as e:
        logger.error(f"✗ Error testing channel 1: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 8: Test with different voltages
    logger.info("\n[TEST 8] Testing voltage ramp...")
    test_voltages = [0.0, 2.5, 5.0, 7.5, 10.0, 5.0, 0.0]
    for voltage in test_voltages:
        try:
            logger.info(f"Setting both channels to {voltage}V...")
            success0 = driver.set_voltage(voltage, channel=0)
            success1 = driver.set_voltage(voltage, channel=1)
            if success0 and success1:
                logger.info(f"  ✓ Both channels set to {voltage}V")
            else:
                logger.warning(f"  ⚠ Some channels failed: ch0={success0}, ch1={success1}")
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"  ✗ Error setting voltage {voltage}V: {e}")
    
    # Test 9: Check for I2C errors
    logger.info("\n[TEST 9] Monitoring for I2C errors...")
    logger.info("Sending 10 rapid commands to check for intermittent errors...")
    error_count = 0
    for i in range(10):
        try:
            driver.set_voltage(5.0, channel=0)
            driver.set_voltage(5.0, channel=1)
            time.sleep(0.1)
        except Exception as e:
            error_count += 1
            logger.warning(f"  I2C error on attempt {i+1}: {e}")
    
    if error_count == 0:
        logger.info("✓ No I2C errors detected")
    else:
        logger.warning(f"⚠ Detected {error_count} I2C errors out of 10 attempts")
        logger.warning("This indicates intermittent I2C communication issues")
    
    logger.info("\n" + "=" * 60)
    logger.info("Test Summary")
    logger.info("=" * 60)
    logger.info("If all tests passed but the lights still don't work:")
    logger.info("  1. Check physical connections (SDA, SCL, GND, VCC)")
    logger.info("  2. Check power supply to the board")
    logger.info("  3. Check I2C address jumpers (should be set for 0x59)")
    logger.info("  4. Check if the board's output stage is working")
    logger.info("  5. Try swapping the board with one that works to isolate the issue")
    logger.info("=" * 60)
    
    return True

if __name__ == "__main__":
    try:
        success = test_board_0x59()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
