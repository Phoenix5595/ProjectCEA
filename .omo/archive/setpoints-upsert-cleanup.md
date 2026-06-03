# Plan: Setpoints Table UPSERT & Cleanup

## Problem

The `set_setpoint()` method in `database.py` INSERTs a new row on every save instead of updating. This creates duplicate rows:

```
mode    | heating_setpoint | updated_at
--------+------------------+------------
DAY     | 24               | 2026-01-10  <- Used (latest)
DAY     | 27               | 2026-01-09  <- Duplicate
DAY     | 26               | 2026-01-08  <- Duplicate
```

The code works (uses ORDER BY updated_at DESC LIMIT 1) but the table grows with junk.

## Solution

1. Clean up existing duplicates
2. Add unique constraint on (location, cluster, mode)
3. Change INSERT to UPSERT (ON CONFLICT DO UPDATE)

---

## Phase 1: Analyze Current State

### Task 1.1: Count duplicates
```sql
SELECT location, cluster, mode, COUNT(*) as count
FROM setpoints
GROUP BY location, cluster, mode
HAVING COUNT(*) > 1
ORDER BY count DESC;
```

### Task 1.2: Verify no NULL mode conflicts
```sql
SELECT location, cluster, COUNT(*) 
FROM setpoints 
WHERE mode IS NULL 
GROUP BY location, cluster;
```

---

## Phase 2: Cleanup Duplicates

### Task 2.1: Create backup
```sql
CREATE TABLE setpoints_backup AS SELECT * FROM setpoints;
```

### Task 2.2: Delete duplicates, keep only latest per (location, cluster, mode)
```sql
DELETE FROM setpoints a
USING setpoints b
WHERE a.id < b.id
  AND a.location = b.location
  AND a.cluster = b.cluster
  AND a.mode IS NOT DISTINCT FROM b.mode;
```

### Task 2.3: Verify cleanup
```sql
SELECT location, cluster, mode, COUNT(*) 
FROM setpoints 
GROUP BY location, cluster, mode 
HAVING COUNT(*) > 1;
-- Should return 0 rows
```

---

## Phase 3: Add Unique Constraint

### Task 3.1: Add constraint
```sql
ALTER TABLE setpoints 
ADD CONSTRAINT setpoints_location_cluster_mode_unique 
UNIQUE (location, cluster, mode);
```

Note: If mode can be NULL, PostgreSQL treats each NULL as unique. Use NULLS NOT DISTINCT if on PostgreSQL 15+:
```sql
ALTER TABLE setpoints 
ADD CONSTRAINT setpoints_location_cluster_mode_unique 
UNIQUE NULLS NOT DISTINCT (location, cluster, mode);
```

---

## Phase 4: Update set_setpoint() Method

### Task 4.1: Modify database.py

**File:** `/home/antoine/ProjectCEA/Infrastructure/automation-service/app/database.py`

**Current pattern (around line 1680-1720):**
```python
# Inserts new row every time
query = """
    INSERT INTO setpoints (location, cluster, mode, heating_setpoint, ...)
    VALUES ($1, $2, $3, $4, ...)
    RETURNING id
"""
```

**New pattern (UPSERT):**
```python
query = """
    INSERT INTO setpoints (
        location, cluster, mode, 
        heating_setpoint, cooling_setpoint, humidity, co2, vpd,
        ramp_in_duration, updated_at
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
    ON CONFLICT (location, cluster, mode) 
    DO UPDATE SET
        heating_setpoint = COALESCE(EXCLUDED.heating_setpoint, setpoints.heating_setpoint),
        cooling_setpoint = COALESCE(EXCLUDED.cooling_setpoint, setpoints.cooling_setpoint),
        humidity = COALESCE(EXCLUDED.humidity, setpoints.humidity),
        co2 = COALESCE(EXCLUDED.co2, setpoints.co2),
        vpd = COALESCE(EXCLUDED.vpd, setpoints.vpd),
        ramp_in_duration = COALESCE(EXCLUDED.ramp_in_duration, setpoints.ramp_in_duration),
        updated_at = NOW()
    RETURNING id
"""
```

### Task 4.2: Remove merge logic
The current code fetches existing row and merges values before INSERT. With UPSERT using COALESCE, this is no longer needed - simplify the method.

---

## Phase 5: Verification

### Task 5.1: Test via frontend
1. Change a DAY setpoint value
2. Verify only 1 row exists for that location/cluster/mode
3. Change it again
4. Verify still only 1 row (updated, not new)

### Task 5.2: Check database
```sql
SELECT location, cluster, mode, heating_setpoint, updated_at
FROM setpoints
WHERE location = Flower Room
ORDER BY mode, updated_at DESC;
```

---

## Rollback Plan

If something breaks:
1. Drop the constraint:
   ```sql
   ALTER TABLE setpoints DROP CONSTRAINT setpoints_location_cluster_mode_unique;
   ```
2. Restore from backup:
   ```sql
   INSERT INTO setpoints SELECT * FROM setpoints_backup 
   ON CONFLICT DO NOTHING;
   ```
3. Revert database.py to use INSERT (git checkout or rollback.sh)

---

## Files to Modify

| File | Change |
|------|--------|
| database.py | Change set_setpoint() to UPSERT pattern |
| SQL migration | Add unique constraint |

## Estimated Time

- Phase 1-3 (SQL): 10 minutes
- Phase 4 (Code): 15 minutes
- Phase 5 (Test): 10 minutes
- Total: ~35 minutes

## Priority

Low - functional workaround exists (ORDER BY updated_at DESC LIMIT 1)

## Dependencies

- PostgreSQL 15+ recommended for NULLS NOT DISTINCT
- Or ensure mode is never NULL (use empty string instead)
