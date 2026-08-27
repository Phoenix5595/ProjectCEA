# Setpoints Table Structure Explanation

## Why Two Different Structures?

There are two different `setpoints` table definitions in the codebase:

### 1. Original Schema (`cea_schema.sql`)
```sql
CREATE TABLE IF NOT EXISTS setpoints (
    setpoint_id SERIAL PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES room(room_id) ON DELETE CASCADE,
    variable TEXT NOT NULL,
    target REAL NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(room_id, variable)
);
```

**Purpose**: Designed for the normalized room hierarchy structure
**Status**: **NOT USED** - This table structure is defined but never actually used by any service

### 2. Automation-Service Schema (`automation-service/app/database.py`)
```sql
CREATE TABLE IF NOT EXISTS setpoints (
    id BIGSERIAL PRIMARY KEY,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    temperature REAL,
    humidity REAL,
    co2 REAL,
    vpd REAL,
    mode TEXT CHECK (mode IS NULL OR mode IN ('DAY', 'NIGHT', 'TRANSITION')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(location, cluster, mode)
);
```

**Purpose**: Used by the automation-service for runtime configuration
**Status**: **ACTUALLY USED** - This is the table structure that exists in the database

## Why the Difference?

1. **Original schema** was designed early in the project for a normalized structure tied to the `room` table
2. **Automation-service** was developed later and needed:
   - String-based `location`/`cluster` (not tied to room_id)
   - All setpoints in one row (denormalized for easier updates)
   - Mode support (DAY/NIGHT/TRANSITION)
   - VPD setpoint support

3. The automation-service creates its own table structure when it initializes, so it uses the location/cluster structure

## Which One to Use?

**Always use the automation-service structure** (`location`, `cluster`, `temperature`, `humidity`, `co2`, `vpd`, `mode`).

The original schema definition in `cea_schema.sql` should be considered **deprecated/outdated** for setpoints.

## Recommendation

1. **Update `cea_schema.sql`** to remove or comment out the old setpoints table definition
2. **Document** that setpoints are managed by the automation-service
3. **Use the automation-service structure** for all queries (Grafana, APIs, etc.)

## Current Usage

- ✅ **Automation-service**: Creates and uses the location/cluster structure
- ✅ **Frontend**: Queries automation-service API which uses location/cluster structure  
- ✅ **Grafana queries**: Should use location/cluster structure (as in the panel JSON)
- ❌ **Original schema**: Not used by any service

