-- Migration: Dedicated physical-sensor registry (`sensor_registry`)
--
-- Adds the canonical registry mapping every physical sensor unit (CAN node
-- or RS-485 soil probe) to its Flower-room placement WITHOUT moving any
-- time-series data: `device` / `sensor` / `measurement` stay the metric and
-- history store; Redis stays live state only. `sensor_registry.device_id`
-- links a physical unit to the existing metadata hierarchy
-- (room -> rack -> device -> sensor -> measurement).
--
-- Idempotent: safe to run repeatedly. Seeds are guarded so re-runs never
-- clobber operator assignments made after the first application.
--
-- Assignment shapes enforced by `sensor_registry_legal_state`:
--   * unassigned (any bus):   room_id / rack_id / location_in_room all NULL
--   * assigned CAN:           room_id + location_in_room set, rack_id NULL
--   * assigned RS-485:        rack_id set (a Flower bed), room/location NULL

BEGIN;

-- ============================================
-- Registry table
-- ============================================

CREATE TABLE IF NOT EXISTS sensor_registry (
    registry_id BIGSERIAL PRIMARY KEY,
    bus TEXT NOT NULL CHECK (bus IN ('can', 'rs485')),
    hardware_address INTEGER NOT NULL CHECK (hardware_address > 0),
    device_id INTEGER REFERENCES device(device_id) ON DELETE SET NULL,
    display_name TEXT NOT NULL CHECK (length(btrim(display_name)) > 0),
    room_id INTEGER REFERENCES room(room_id) ON DELETE RESTRICT,
    rack_id INTEGER REFERENCES rack(rack_id) ON DELETE RESTRICT,
    location_in_room TEXT CHECK (location_in_room IN ('front', 'back', 'main')),
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT sensor_registry_bus_address_unique UNIQUE (bus, hardware_address),
    CONSTRAINT sensor_registry_device_unique UNIQUE (device_id),
    CONSTRAINT sensor_registry_legal_state CHECK (
        (room_id IS NULL AND rack_id IS NULL AND location_in_room IS NULL)
        OR (
            bus = 'can'
            AND room_id IS NOT NULL
            AND location_in_room IS NOT NULL
            AND rack_id IS NULL
        )
        OR (
            bus = 'rs485'
            AND rack_id IS NOT NULL
            AND room_id IS NULL
            AND location_in_room IS NULL
        )
    )
);

-- A CAN room position holds at most one physical node.
CREATE UNIQUE INDEX IF NOT EXISTS sensor_registry_can_position_unique
    ON sensor_registry (room_id, location_in_room)
WHERE bus = 'can'
  AND room_id IS NOT NULL
  AND location_in_room IS NOT NULL;

COMMENT ON TABLE sensor_registry IS
    'Physical sensor units (CAN nodes, RS-485 probes) with commissioned room/bed placement; links to device rows for time-series data';

-- ============================================
-- Canonical Flower rooms / beds (idempotent)
-- ============================================

INSERT INTO room (name)
SELECT 'Flower Room'
WHERE NOT EXISTS (
    SELECT 1 FROM room WHERE name = 'Flower Room'
);

INSERT INTO rack (room_id, name)
SELECT f.room_id, b.bed_name
FROM room f
CROSS JOIN (VALUES ('Front Bed'), ('Back Bed')) AS b(bed_name)
WHERE f.name = 'Flower Room'
  AND NOT EXISTS (
    SELECT 1 FROM rack r
    WHERE r.room_id = f.room_id AND r.name = b.bed_name
);

-- ============================================
-- Seed CAN nodes 1-3 with their legacy hard-coded mappings
-- (1 Flower/back, 2 Flower/front, 3 Veg/main) so a migrated
-- deployment keeps publishing located live state unchanged.
-- ============================================

WITH wanted(bus, hardware_address, room_name, location_in_room) AS (
    VALUES
        ('can'::text, 1, 'Flower Room', 'back'::text),
        ('can'::text, 2, 'Flower Room', 'front'::text),
        ('can'::text, 3, 'Veg Room', 'main'::text)
),
resolved AS (
    SELECT
        w.bus,
        w.hardware_address,
        w.location_in_room,
        r.room_id,
        d.device_id,
        d.created_at
    FROM wanted w
    LEFT JOIN room r ON r.name = w.room_name
    LEFT JOIN LATERAL (
        SELECT dev.device_id, dev.created_at
        FROM device dev
        WHERE dev.name = 'Node ' || w.hardware_address
        ORDER BY dev.created_at, dev.device_id
        LIMIT 1
    ) d ON TRUE
)
INSERT INTO sensor_registry (
    bus, hardware_address, device_id, display_name,
    room_id, rack_id, location_in_room, first_seen
)
SELECT
    bus,
    hardware_address,
    device_id,
    'Node ' || hardware_address,
    room_id,
    NULL,
    location_in_room,
    COALESCE(created_at, NOW())
FROM resolved
ON CONFLICT (bus, hardware_address) DO NOTHING;

-- ============================================
-- Seed RS-485 probes from existing devices whose serial_number
-- is MODBUS-<positive integer>. Devices parked on a Flower bed keep
-- that bed; at most four probes per bed (oldest first by
-- device.created_at, device_id), the remainder stays unassigned so
-- the four-slot bed capacity is never violated by the seed.
-- ============================================

WITH candidates AS (
    SELECT
        d.device_id,
        d.rack_id,
        d.name,
        d.created_at,
        substring(d.serial_number FROM '^MODBUS-([1-9][0-9]*)$')::integer AS hardware_address
    FROM device d
    WHERE d.serial_number ~ '^MODBUS-[1-9][0-9]*$'
),
deduplicated AS (
    SELECT DISTINCT ON (hardware_address)
        device_id,
        name,
        created_at,
        hardware_address,
        rack_id
    FROM candidates
    ORDER BY hardware_address, created_at, device_id
),
flower_beds AS (
    SELECT r.rack_id
    FROM rack r
    JOIN room f ON f.room_id = r.room_id
    WHERE f.name = 'Flower Room'
      AND r.name IN ('Front Bed', 'Back Bed')
),
resolved AS (
    SELECT
        dd.device_id,
        dd.name,
        dd.created_at,
        dd.hardware_address,
        CASE WHEN fb.rack_id IS NOT NULL THEN fb.rack_id END AS bed_rack_id,
        ROW_NUMBER() OVER (
            PARTITION BY fb.rack_id
            ORDER BY dd.created_at, dd.device_id
        ) AS bed_rank
    FROM deduplicated dd
    LEFT JOIN flower_beds fb ON fb.rack_id = dd.rack_id
)
INSERT INTO sensor_registry (
    bus, hardware_address, device_id, display_name,
    room_id, rack_id, location_in_room, first_seen
)
SELECT
    'rs485',
    hardware_address,
    device_id,
    name,
    NULL,
    CASE WHEN bed_rank <= 4 THEN bed_rack_id END,
    NULL,
    COALESCE(created_at, NOW())
FROM resolved
ON CONFLICT (bus, hardware_address) DO NOTHING;

COMMIT;

-- Production services connect as cea_user; the new table must be owned and
-- accessible by the service role the way every other metadata table is.
-- Disposable verification databases have no cea_user role, so the grant
-- step is applied only where that role exists (idempotent either way).
DO $grant$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cea_user')
       AND NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
        -- Applied by the service-role owner itself: ownership already correct.
        RETURN;
    END IF;
    ALTER TABLE sensor_registry OWNER TO cea_user;
    GRANT ALL PRIVILEGES ON TABLE sensor_registry TO cea_user;
    GRANT USAGE, SELECT ON SEQUENCE sensor_registry_registry_id_seq TO cea_user;
EXCEPTION
    WHEN insufficient_privilege THEN
        -- Disposable verification databases run under a non-member role;
        -- skip the service-role alignment there (production applies it).
        NULL;
END
$grant$;
