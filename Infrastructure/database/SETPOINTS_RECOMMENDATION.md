# Setpoints Table Recommendation

## Recommendation: Use ONLY the Automation-Service Structure

**You do NOT need both table structures. Use only the automation-service structure.**

## Comparison

### ❌ Old Structure (room_id, variable, target)
**Status**: NOT USED - Dead code

**Problems**:
- Tied to `room_id` foreign key (inflexible)
- One row per variable (requires multiple rows for one setpoint configuration)
- No mode support (can't have DAY/NIGHT setpoints)
- No VPD setpoint support
- Not used by any service

**Example**:
```sql
-- Would need 4 rows for one room:
INSERT INTO setpoints (room_id, variable, target) VALUES (1, 'temperature', 24.0);
INSERT INTO setpoints (room_id, variable, target) VALUES (1, 'humidity', 60.0);
INSERT INTO setpoints (room_id, variable, target) VALUES (1, 'co2', 1000.0);
INSERT INTO setpoints (room_id, variable, target) VALUES (1, 'vpd', 1.2);
```

### ✅ Automation-Service Structure (location, cluster, temperature, humidity, co2, vpd, mode)
**Status**: ACTIVELY USED - This is what exists in your database

**Advantages**:
- ✅ All setpoints in one row (atomic updates)
- ✅ Mode support (DAY/NIGHT/TRANSITION)
- ✅ VPD setpoint support
- ✅ Uses location/cluster strings (flexible, not tied to room_id)
- ✅ Already integrated with automation-service
- ✅ Used by frontend API
- ✅ Used by control system

**Example**:
```sql
-- One row for all setpoints:
INSERT INTO setpoints (location, cluster, temperature, humidity, co2, vpd, mode)
VALUES ('Flower Room', 'back', 24.0, 60.0, 1000.0, 1.2, 'DAY');
```

## Action Items

1. ✅ **Keep**: Automation-service structure (location/cluster based)
2. ❌ **Remove**: Old structure definition from `cea_schema.sql` (already commented out)
3. ✅ **Document**: The automation-service structure is the official one

## Why Both Exist

- **Original schema** (`cea_schema.sql`): Designed early in the project, never actually implemented
- **Automation-service**: Created later with a better design that fits actual needs

The automation-service creates its own table when it initializes, so it uses the location/cluster structure regardless of what's in `cea_schema.sql`.

## Conclusion

**Use only the automation-service structure.** The old one is dead code and should be removed from documentation to avoid confusion.

