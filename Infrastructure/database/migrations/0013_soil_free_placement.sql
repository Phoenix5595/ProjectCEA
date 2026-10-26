-- migration: downtime=false
-- R9 Soil RS-485: free placement of soil probes in the 4x4 ft Flower beds,
-- room-level Veg placement and bed dimensions for the Soil-page heatmap.
BEGIN;

ALTER TABLE public.soil_bed_profile
    ADD COLUMN width_ft numeric(5,2) NOT NULL DEFAULT 4.00
        CONSTRAINT soil_bed_profile_width_ft_check CHECK (width_ft > 0),
    ADD COLUMN length_ft numeric(5,2) NOT NULL DEFAULT 4.00
        CONSTRAINT soil_bed_profile_length_ft_check CHECK (length_ft > 0);

-- One row per placement interval. x_frac runs across the bed width (left to
-- right as drawn), y_frac along its length (top to bottom). Veg rows are
-- room-level: rack_id, x_frac and y_frac are NULL.
CREATE TABLE public.soil_probe_placement (
    placement_id bigserial PRIMARY KEY,
    registry_id bigint NOT NULL REFERENCES public.sensor_registry(registry_id) ON DELETE CASCADE,
    room_id integer NOT NULL REFERENCES public.room(room_id) ON DELETE RESTRICT,
    rack_id integer REFERENCES public.rack(rack_id) ON DELETE RESTRICT,
    x_frac numeric(4,3) CHECK (x_frac BETWEEN 0 AND 1),
    y_frac numeric(4,3) CHECK (y_frac BETWEEN 0 AND 1),
    placed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    removed_at timestamptz,
    closed_reason text CHECK (closed_reason IN ('moved', 'unassigned', 'disconnected')),
    CONSTRAINT soil_probe_placement_open_ck CHECK ((removed_at IS NULL) = (closed_reason IS NULL)),
    CONSTRAINT soil_probe_placement_order_ck CHECK (removed_at IS NULL OR removed_at >= placed_at),
    CONSTRAINT soil_probe_placement_spot_ck CHECK (
        (rack_id IS NULL) = (x_frac IS NULL) AND (rack_id IS NULL) = (y_frac IS NULL))
);
CREATE UNIQUE INDEX soil_probe_placement_open_uq
    ON public.soil_probe_placement (registry_id) WHERE removed_at IS NULL;
CREATE INDEX soil_probe_placement_rack_idx
    ON public.soil_probe_placement (rack_id, removed_at);

ALTER TABLE public.sensor_registry
    DROP CONSTRAINT sensor_registry_legal_state,
    ADD CONSTRAINT sensor_registry_legal_state CHECK (
        (room_id IS NULL AND rack_id IS NULL AND location_in_room IS NULL)
        OR (bus = 'can' AND room_id IS NOT NULL AND location_in_room IS NOT NULL AND rack_id IS NULL)
        OR (bus = 'rs485' AND rack_id IS NOT NULL AND room_id IS NULL AND location_in_room IS NULL)
        OR (bus = 'rs485' AND room_id IS NOT NULL AND rack_id IS NULL AND location_in_room IS NULL)
    );

-- Backfill: sensor_registry.room_id/rack_id always mirrors the open placement.
INSERT INTO public.soil_probe_placement (registry_id, room_id, rack_id, x_frac, y_frac, placed_at)
SELECT sr.registry_id, r.room_id, sr.rack_id, 0.5, 0.5, sr.updated_at
FROM public.sensor_registry sr
JOIN public.rack r ON r.rack_id = sr.rack_id
WHERE sr.bus = 'rs485' AND sr.rack_id IS NOT NULL;

COMMIT;
