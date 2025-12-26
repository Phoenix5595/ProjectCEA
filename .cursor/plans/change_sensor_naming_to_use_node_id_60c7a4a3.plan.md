---
name: Change Sensor Naming to Use Node ID
overview: Change sensor naming from location/cluster-based suffixes (co2_b, dry_bulb_f) to node ID-based naming (co2_1, dry_bulb_2) throughout the database manager.
todos:
  - id: update_sensor_suffix
    content: Change _get_sensor_suffix to return node ID string instead of location-based suffix
    status: completed
  - id: update_rh_vpd_naming
    content: Update RH and VPD key generation to use node ID in get_all_sensors_for_location
    status: completed
    dependencies:
      - update_sensor_suffix
  - id: verify_all_sensors
    content: Verify all sensor types (PT100, SCD30, BME280, VL53) use node ID naming correctly
    status: completed
    dependencies:
      - update_sensor_suffix
---

# Change Sensor Naming to Use Node ID

## Problem

Sensors are currently named using location/cluster suffixes:

- `co2_b`, `dry_bulb_b` for Flower Room back
- `co2_f`, `dry_bulb_f` for Flower Room front  
- `co2_v`, `dry_bulb_v` for Veg Room

User wants them named by node ID instead:

- `co2_1`, `dry_bulb_1` for node 1 (Flower Room, back)
- `co2_2`, `dry_bulb_2` for node 2 (Flower Room, front)
- `co2_3`, `dry_bulb_3` for node 3 (Veg Room, main)

## Solution

### 1. Update `_get_sensor_suffix` method

- Change it to return node ID as string instead of location-based suffix
- Use `_get_node_id` to get the node ID and return it as string
- Keep special handling for Lab location if needed

### 2. Update all sensor naming in `_extract_sensors`

- All sensor keys already use `suffix` variable, so changing `_get_sensor_suffix` will automatically update them
- Verify all sensor types: PT100, SCD30, BME280, VL53

### 3. Update RH and VPD calculation

- In `get_all_sensors_for_location`, RH and VPD keys also use suffix
- Change to use node ID instead

## Files to Modify

1. **[backend/app/database.py](backend/app/database.py)**

- Modify `_get_sensor_suffix` method to return node ID string
- Update RH/VPD key generation to use node ID
- Ensure all sensor naming consistently uses node ID

## Implementation Details

- Node ID mapping:
- Node 1: Flower Room, back
- Node 2: Flower Room, front
- Node 3: Veg Room, main
- Node 4: Lab, main
- Node 5: Outside, main

- Sensor names will change from:
- `co2_b` → `co2_1`
- `co2_f` → `co2_2`
- `dry_bulb_b` → `dry_bulb_1`
- `rh_b` → `rh_1`
- `vpd_b` → `vpd_1`
- etc.

- Special cases:
- Lab location might need special handling (currently returns empty string)
- Keep `lab_temp` and `water_temp` as special names if they exist