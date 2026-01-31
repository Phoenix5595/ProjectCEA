#!/usr/bin/env python3
"""
Fix script for board 0x59 - forces re-initialization and tests output
"""

import logging
import sys
import time
from pathlib import Path

# Add the automation service to the path
sys.path.insert(
    0, str(Path(__file__).parent.parent.parent / "Infrastructure" / "automation-service")
)

from app.hardware.dfr0971 import DFR0971Driver

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def fix_board_0x59():
    """Force re-initialize and test board 0x59"""
    logger.info("=" * 60)
    logger.info("Fixing Board 0x59 - Force Re-initialization")
    logger.info("=" * 60)

    # Create driver
    logger.info("\n[STEP 1] Creating driver for board 0x59...")
    try:
        driver = DFR0971Driver(i2c_bus=1, i2c_address=0x59, simulation=False)

        if driver.simulation:
            logger.error("✗ Driver is in simulation mode - hardware not detected!")
            return False

        logger.info("✓ Driver created successfully")
    except Exception as e:
        logger.error(f"✗ Failed to create driver: {e}")
        import traceback

        traceback.print_exc()
        return False

    # Force re-initialize
    logger.info("\n[STEP 2] Force re-initializing board...")
    try:
        success = driver.force_reinitialize()
        if success:
            logger.info("✓ Board re-initialized successfully")
        else:
            logger.error("✗ Re-initialization failed")
            return False
    except Exception as e:
        logger.error(f"✗ Error during re-initialization: {e}")
        import traceback

        traceback.print_exc()
        return False

    # Test channel 0
    logger.info("\n[STEP 3] Testing Channel 0...")
    test_voltages = [0.0, 2.5, 5.0, 7.5, 10.0, 5.0, 0.0]
    for voltage in test_voltages:
        try:
            logger.info(f"  Setting channel 0 to {voltage}V...")
            success = driver.set_voltage(voltage, channel=0)
            if success:
                logger.info("    ✓ Command sent successfully")
            else:
                logger.error("    ✗ Failed to set voltage")
                return False
            time.sleep(0.5)
            logger.info(f"    → MEASURE WITH MULTIMETER: Should read {voltage}V")
            if voltage != 0.0:
                input(f"    Press Enter after measuring (should be ~{voltage}V)...")
        except Exception as e:
            logger.error(f"    ✗ Error: {e}")
            return False

    # Test channel 1
    logger.info("\n[STEP 4] Testing Channel 1...")
    for voltage in test_voltages:
        try:
            logger.info(f"  Setting channel 1 to {voltage}V...")
            success = driver.set_voltage(voltage, channel=1)
            if success:
                logger.info("    ✓ Command sent successfully")
            else:
                logger.error("    ✗ Failed to set voltage")
                return False
            time.sleep(0.5)
            logger.info(f"    → MEASURE WITH MULTIMETER: Should read {voltage}V")
            if voltage != 0.0:
                input(f"    Press Enter after measuring (should be ~{voltage}V)...")
        except Exception as e:
            logger.error(f"    ✗ Error: {e}")
            return False

    # Final test - set both channels to 5V
    logger.info("\n[STEP 5] Final test - both channels to 5V...")
    try:
        driver.set_voltage(5.0, channel=0)
        driver.set_voltage(5.0, channel=1)
        time.sleep(0.5)
        logger.info("✓ Both channels set to 5V")
        logger.info("→ MEASURE BOTH OUTPUTS WITH MULTIMETER")
        logger.info("→ Channel 0 should read ~5.0V")
        logger.info("→ Channel 1 should read ~5.0V")
        input("Press Enter after measuring...")
    except Exception as e:
        logger.error(f"✗ Error: {e}")
        return False

    logger.info("\n" + "=" * 60)
    logger.info("Fix Complete!")
    logger.info("=" * 60)
    logger.info("\nIf voltages are still not correct:")
    logger.info("1. Check power supply to the board (should be 3.3V or 5V)")
    logger.info("2. Check for short circuits on output lines")
    logger.info("3. Try power cycling the board")
    logger.info("4. The board may have a hardware fault - consider replacement")
    logger.info("=" * 60)

    return True


if __name__ == "__main__":
    try:
        success = fix_board_0x59()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\nFix interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
