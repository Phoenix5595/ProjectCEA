#!/usr/bin/env python3
"""CAN Processor - Unified service for reading CAN bus and processing messages.

Reads CAN messages directly from CAN bus, decodes once, processes, and writes to:
- Redis Stream (sensor:raw) - recent history buffer
- TimescaleDB (measurement table) - full history
- Redis state keys (sensor:*) - live values for frontend
"""
import signal
import sys
import logging
import os
import argparse
from datetime import datetime
from typing import Optional

from app.can_reader import CANReader
from app.decoder import decode_message_data
from app.processor import validate_decoded_data, extract_sensor_values, get_location_from_node
from app.writer import DataWriter

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

running = True
can_reader: Optional[CANReader] = None
data_writer: Optional[DataWriter] = None

# Statistics
processed_count = 0
error_count = 0
stream_write_count = 0
db_write_count = 0
redis_write_count = 0

# Display mode
display_messages = False


def signal_handler(sig, frame):
    """Handle shutdown signals."""
    global running
    logger.info("Received shutdown signal, stopping...")
    running = False


def format_message_display(msg, decoded, sensors, location, cluster):
    """Format a CAN message for display (similar to old scanner).
    
    Args:
        msg: CAN message object
        decoded: Decoded message data
        sensors: List of (sensor_name, value, unit) tuples
        location: Location name
        cluster: Cluster name
    
    Returns:
        Formatted string for display
    """
    msg_type = decoded.get('message_type', 'Unknown')
    node_id = decoded.get('node_id', '?')
    raw_data = ' '.join(f'{b:02X}' for b in msg.data)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    
    # Build display string
    lines = []
    lines.append(f"[{timestamp}] CAN ID: 0x{msg.arbitration_id:03X} | Node: {node_id} | Type: {msg_type}")
    lines.append(f"  Raw Data: {raw_data}")
    
    # Add decoded sensor values
    if sensors:
        sensor_lines = []
        for sensor_name, value, unit in sensors:
            if value is not None:
                sensor_lines.append(f"  {sensor_name}: {value:.2f} {unit}")
        if sensor_lines:
            lines.append("  Sensors:")
            lines.extend(sensor_lines)
    
    # Add location info
    if location and cluster:
        lines.append(f"  Location: {location}/{cluster}")
    
    return '\n'.join(lines)


def process_can_message(msg):
    """Process a single CAN message.
    
    Args:
        msg: CAN message object
    
    Returns:
        True if successful, False otherwise
    """
    global processed_count, error_count, stream_write_count, db_write_count, redis_write_count
    
    try:
        # Decode CAN frame
        decoded = decode_message_data(msg)
        if not decoded:
            if display_messages:
                raw_data = ' '.join(f'{b:02X}' for b in msg.data)
                logger.warning(f"Failed to decode CAN message - ID: 0x{msg.arbitration_id:03X}, Data: {raw_data}")
            else:
                logger.debug("Failed to decode CAN message")
            error_count += 1
            return False
        
        # Validate decoded data
        if not validate_decoded_data(decoded):
            if display_messages:
                raw_data = ' '.join(f'{b:02X}' for b in msg.data)
                logger.warning(f"Invalid decoded data - ID: 0x{msg.arbitration_id:03X}, Type: {decoded.get('message_type')}, Data: {raw_data}")
            else:
                logger.debug(f"Invalid decoded data: {decoded.get('message_type')}")
            error_count += 1
            return False
        
        # Get raw data
        raw_data = ' '.join(f'{b:02X}' for b in msg.data)
        
        # Get timestamp
        timestamp = datetime.now()
        timestamp_ms = int(timestamp.timestamp() * 1000)
        
        # Extract sensor values (this also calculates RH/VPD if PT100 message)
        node_id = decoded.get('node_id')
        location, cluster = get_location_from_node(node_id)
        sensors = extract_sensor_values(decoded, location, cluster)
        
        # Display message if enabled
        if display_messages:
            display_str = format_message_display(msg, decoded, sensors, location, cluster)
            print(display_str)
            logger.info(display_str)
        
        # Add calculated RH/VPD to decoded data for database storage
        # NOTE: Only add calculated RH/VPD from PT100, NOT secondary_rh from SCD30
        for sensor_name, value, unit in sensors:
            # Match calculated RH (rh_b, rh_f, rh_v) but NOT secondary_rh
            if (sensor_name.startswith('rh_') or sensor_name == 'rh') and not sensor_name.startswith('secondary_rh'):
                decoded['rh_percent'] = value
            # Match calculated VPD (vpd_b, vpd_f, vpd_v)
            elif sensor_name.startswith('vpd_') or sensor_name == 'vpd':
                decoded['vpd_kpa'] = value
            # Store pressure for climate readings (from BME280 or default)
            elif sensor_name.startswith('pressure_') or sensor_name == 'pressure':
                decoded['pressure_hpa'] = value
        
        # Write to all three destinations: Stream, DB, and Redis state
        result = data_writer.write(
            msg=msg,
            decoded=decoded,
            raw_data=raw_data,
            sensors=sensors,
            timestamp=timestamp,
            timestamp_ms=timestamp_ms
        )
        
        # Update statistics
        if result['stream']:
            stream_write_count += 1
        if result['db']:
            db_write_count += 1
        if result['redis']:
            redis_write_count += 1
        
        processed_count += 1
        
        # Log periodically (only if not displaying every message)
        if not display_messages and processed_count % 100 == 0:
            logger.info(f"Processed {processed_count} messages "
                       f"(Stream: {stream_write_count}, DB: {db_write_count}, "
                       f"Redis: {redis_write_count}, Errors: {error_count})")
        
        return result['db']  # Return DB success as primary indicator
    
    except Exception as e:
        logger.error(f"Error processing CAN message: {e}", exc_info=True)
        error_count += 1
        return False


def main():
    """Main processing loop."""
    global running, can_reader, data_writer
    global processed_count, error_count, stream_write_count, db_write_count, redis_write_count
    global display_messages
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='CAN Processor - Unified CAN bus service')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Display each CAN message as it is processed (like old scanner)')
    parser.add_argument('--display', action='store_true',
                       help='Alias for --verbose')
    args = parser.parse_args()
    
    # Enable message display if requested
    display_messages = args.verbose or args.display or os.getenv('CAN_PROCESSOR_DISPLAY', '').lower() in ('1', 'true', 'yes')
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("=" * 60)
    logger.info("CAN Processor - Starting")
    if display_messages:
        logger.info("Message display: ENABLED (showing all messages)")
    else:
        logger.info("Message display: DISABLED (use --verbose to enable)")
    logger.info("=" * 60)
    
    # Initialize CAN reader
    can_reader = CANReader(channel='can0')
    if not can_reader.connect():
        logger.error("Failed to connect to CAN bus. Exiting.")
        sys.exit(1)
    
    # Initialize data writer
    data_writer = DataWriter(redis_ttl=10, stream_name="sensor:raw")
    
    if not data_writer.connect_db():
        logger.error("Failed to connect to TimescaleDB. Exiting.")
        sys.exit(1)
    
    # Connect to Redis (for Stream and state writes)
    data_writer.connect_redis()
    
    logger.info("Connected to CAN bus: can0")
    logger.info("Connected to TimescaleDB")
    logger.info(f"Connected to Redis Stream: sensor:raw")
    if data_writer.redis_enabled:
        logger.info("Redis state updates enabled")
    else:
        logger.warning("Redis state updates disabled")
    
    logger.info("Starting to process CAN frames...")
    logger.info("Press Ctrl+C to stop")
    logger.info("-" * 60)
    
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    try:
        while running:
            try:
                # Read message from CAN bus
                msg = can_reader.read_message(timeout=1.0)
                consecutive_errors = 0  # Reset error counter on success
                
                if msg:
                    # Process message
                    process_can_message(msg)
            
            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Error in main loop: {e}", exc_info=True)
                error_count += 1
                
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many consecutive errors ({consecutive_errors}), stopping")
                    break
                
                # Continue processing despite errors
    
    finally:
        logger.info("-" * 60)
        logger.info("Shutting down...")
        logger.info(f"Statistics:")
        logger.info(f"  Processed: {processed_count}")
        logger.info(f"  Stream writes: {stream_write_count}")
        logger.info(f"  DB writes: {db_write_count}")
        logger.info(f"  Redis writes: {redis_write_count}")
        logger.info(f"  Errors: {error_count}")
        
        if can_reader:
            can_reader.close()
        
        if data_writer:
            data_writer.close()
        
        logger.info("CAN Processor stopped")


if __name__ == "__main__":
    main()

