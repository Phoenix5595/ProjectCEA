"""CAN frame decoder - decodes CAN messages from CAN bus."""
import struct
from typing import Dict, Any, Optional

# CAN IDs for FullV5 (Node 1, 2, 3)
CAN_ID_PT100_BASE = 0x101
CAN_ID_BME280_BASE = 0x102
CAN_ID_SCD30_BASE = 0x103
CAN_ID_VL53_BASE = 0x104
CAN_ID_HEARTBEAT_BASE = 0x105

# Invalid temperature marker
INVALID_TEMP = 0x7FFF


def get_node_id(can_id: int) -> Optional[int]:
    """Get node ID from CAN ID.
    
    Node ID is determined by bits 8-11 (0xF00):
    - 0x100: Node 1 (Flower Room, back)
    - 0x200: Node 2 (Flower Room, front)
    - 0x300: Node 3 (Veg Room, main)
    """
    base = can_id & 0xF00
    if base == 0x100:
        return 1
    elif base == 0x200:
        return 2
    elif base == 0x300:
        return 3
    return None


def get_message_type(can_id: int) -> str:
    """Get message type from CAN ID.
    
    Message type is determined by the last byte (0xFF) of the CAN ID:
    - 0x01: PT100
    - 0x02: BME280
    - 0x03: SCD30
    - 0x04: VL53
    - 0x05: Heartbeat
    """
    msg_type_id = can_id & 0xFF
    if msg_type_id == 0x01:
        return 'PT100'
    elif msg_type_id == 0x02:
        return 'BME280'
    elif msg_type_id == 0x03:
        return 'SCD30'
    elif msg_type_id == 0x04:
        return 'VL53'
    elif msg_type_id == 0x05:
        return 'Heartbeat'
    return 'Unknown'


def decode_message_data(msg) -> Dict[str, Any]:
    """Decode CAN message and return structured data dictionary.
    
    Args:
        msg: CAN message object with arbitration_id, data, and dlc attributes
    
    Returns:
        Decoded data dictionary
    """
    can_id = msg.arbitration_id
    data = msg.data
    node_id = get_node_id(can_id)
    msg_type = get_message_type(can_id)
    
    decoded = {
        'node_id': node_id,
        'message_type': msg_type,
        'can_id': can_id,
        'dlc': msg.dlc
    }
    
    # Decode based on message type
    if msg_type == 'PT100' and len(data) >= 6:
        t_dry_raw = struct.unpack('>h', bytes(data[0:2]))[0]
        t_wet_raw = struct.unpack('>h', bytes(data[2:4]))[0]
        msg_count = struct.unpack('<H', bytes(data[4:6]))[0]
        
        decoded['temp_dry_c'] = t_dry_raw / 100.0 if t_dry_raw != INVALID_TEMP else None
        decoded['temp_wet_c'] = t_wet_raw / 100.0 if t_wet_raw != INVALID_TEMP else None
        decoded['message_count'] = msg_count
    
    elif msg_type == 'BME280' and len(data) >= 6:
        decoded['temperature_c'] = struct.unpack('>h', bytes(data[0:2]))[0] / 100.0
        decoded['humidity_percent'] = struct.unpack('>H', bytes(data[2:4]))[0] / 100.0
        decoded['pressure_hpa'] = struct.unpack('>H', bytes(data[4:6]))[0] / 10.0
    
    elif msg_type == 'SCD30' and len(data) >= 6:
        decoded['co2_ppm'] = struct.unpack('>H', bytes(data[0:2]))[0]
        decoded['temperature_c'] = struct.unpack('>h', bytes(data[2:4]))[0] / 100.0
        decoded['humidity_percent'] = struct.unpack('>H', bytes(data[4:6]))[0] / 100.0
    
    elif msg_type == 'VL53' and len(data) >= 6:
        decoded['distance_mm'] = struct.unpack('>H', bytes(data[0:2]))[0]
        decoded['ambient'] = struct.unpack('>H', bytes(data[2:4]))[0]
        decoded['signal'] = struct.unpack('>H', bytes(data[4:6]))[0]
    
    elif msg_type == 'Heartbeat' and len(data) >= 6:
        uptime_ms = struct.unpack('>I', bytes(data[2:6]))[0]
        decoded['uptime_ms'] = uptime_ms
        decoded['uptime_sec'] = uptime_ms / 1000.0
        decoded['uptime_min'] = uptime_ms / 60000.0
        decoded['uptime_hr'] = uptime_ms / 3600000.0
    
    return decoded

