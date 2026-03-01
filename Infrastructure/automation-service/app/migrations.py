"""Database migration utilities."""

import os
from typing import TYPE_CHECKING

from shared.infra_logging import get_logger

if TYPE_CHECKING:
    from asyncpg import Pool

logger = get_logger(__name__)


def run_alembic_migrations() -> None:
    """Run database migrations using Alembic if available."""
    try:
        from alembic import command
        from alembic.config import Config

        alembic_ini = os.path.join(os.path.dirname(os.path.dirname(__file__)), "alembic.ini")
        if os.path.exists(alembic_ini):
            alembic_cfg = Config(alembic_ini)
            command.upgrade(alembic_cfg, "head")
            logger.info("Alembic migrations applied")
    except ImportError:
        logger.debug("Alembic not installed, skipping migrations (schema already exists)")
    except Exception as e:
        logger.warning(f"Alembic migration skipped: {e}")


async def create_room_modes_tables(pool: "Pool") -> None:
    """Create room modes tables for the new UI."""
    async with pool.acquire() as conn:
        # Room modes table (Veg, Flower, Drying, Sleep)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS room_modes (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                photoperiod_hours INTEGER CHECK (photoperiod_hours >= 0 AND photoperiod_hours <= 24),
                is_constant BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Insert default modes if not exist
        await conn.execute("""
            INSERT INTO room_modes (name, description, photoperiod_hours, is_constant)
            VALUES
                ('veg', 'Vegetative growth - 18/6 photoperiod', 18, FALSE),
                ('flower', 'Flowering - 12/12 photoperiod', 12, FALSE),
                ('drying', 'Drying - 24h constant conditions', 0, TRUE),
                ('sleep', 'Sleep mode - minimal energy', 0, TRUE)
            ON CONFLICT (name) DO NOTHING
        """)

        # Flower submodes table (Stretch, Bulk, Ripen)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS flower_submodes (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                week_start INTEGER CHECK (week_start >= 1),
                week_end INTEGER CHECK (week_end >= 1),
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Insert default flower submodes
        await conn.execute("""
            INSERT INTO flower_submodes (name, description, week_start, week_end)
            VALUES
                ('stretch', 'Stretch phase - weeks 1-3', 1, 3),
                ('bulk', 'Bulk phase - weeks 4-6', 4, 6),
                ('ripen', 'Ripen phase - weeks 7-9', 7, 9)
            ON CONFLICT (name) DO NOTHING
        """)

        # Room active mode table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS room_active_mode (
                id SERIAL PRIMARY KEY,
                location TEXT NOT NULL,
                cluster TEXT NOT NULL,
                mode_id INTEGER REFERENCES room_modes(id),
                submode_id INTEGER REFERENCES flower_submodes(id),
                activated_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(location, cluster)
            )
        """)

        # Light presets per mode
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS light_presets (
                id SERIAL PRIMARY KEY,
                mode_id INTEGER REFERENCES room_modes(id),
                submode_id INTEGER REFERENCES flower_submodes(id),
                lights_on_hour INTEGER CHECK (lights_on_hour >= 0 AND lights_on_hour < 24),
                lights_off_hour INTEGER CHECK (lights_off_hour >= 0 AND lights_off_hour < 24),
                intensity_day INTEGER CHECK (intensity_day >= 0 AND intensity_day <= 100),
                intensity_night INTEGER CHECK (intensity_night >= 0 AND intensity_night <= 100),
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Mode parameters table - stores ALL parameters per room/mode/submode combination
        # Each mode/submode has its own saved parameters that persist through mode switches
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS mode_parameters (
                id SERIAL PRIMARY KEY,
                location TEXT NOT NULL,
                cluster TEXT NOT NULL,
                mode_id INTEGER REFERENCES room_modes(id) NOT NULL,
                submode_id INTEGER REFERENCES flower_submodes(id),  -- NULL for non-Flower modes

                -- Schedule parameters
                day_start_time TIME NOT NULL DEFAULT '17:00',
                night_start_time TIME NOT NULL DEFAULT '11:00',
                ramp_up_minutes INTEGER NOT NULL DEFAULT 30,
                ramp_down_minutes INTEGER NOT NULL DEFAULT 30,
                pre_day_minutes INTEGER NOT NULL DEFAULT 30,
                pre_night_minutes INTEGER NOT NULL DEFAULT 30,
                light_ramp_up_minutes INTEGER NOT NULL DEFAULT 15,
                light_ramp_down_minutes INTEGER NOT NULL DEFAULT 15,

                -- Pre-Day setpoints
                pre_day_heat_temp REAL NOT NULL DEFAULT 22.0,
                pre_day_cool_temp REAL NOT NULL DEFAULT 26.0,
                pre_day_vpd REAL NOT NULL DEFAULT 0.9,
                pre_day_co2 INTEGER NOT NULL DEFAULT 700,

                -- Day setpoints
                day_heat_temp REAL NOT NULL DEFAULT 24.0,
                day_cool_temp REAL NOT NULL DEFAULT 28.0,
                day_vpd REAL NOT NULL DEFAULT 1.0,
                day_co2 INTEGER NOT NULL DEFAULT 800,
                day_leaf_delta REAL NOT NULL DEFAULT -2.0,

                -- Pre-Night setpoints
                pre_night_heat_temp REAL NOT NULL DEFAULT 22.0,
                pre_night_cool_temp REAL NOT NULL DEFAULT 26.0,
                pre_night_vpd REAL NOT NULL DEFAULT 0.9,
                pre_night_co2 INTEGER NOT NULL DEFAULT 700,

                -- Night setpoints
                night_heat_temp REAL NOT NULL DEFAULT 20.0,
                night_cool_temp REAL NOT NULL DEFAULT 24.0,
                night_vpd REAL NOT NULL DEFAULT 0.8,
                night_co2 INTEGER NOT NULL DEFAULT 600,
                night_leaf_delta REAL NOT NULL DEFAULT -1.0,

                -- Light intensity (percentage)
                main_light_intensity INTEGER NOT NULL DEFAULT 100,
                supplemental_light_intensity INTEGER NOT NULL DEFAULT 0,

                -- Timestamps
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW(),

                -- Unique constraint: one parameter set per room/mode/submode
                UNIQUE(location, cluster, mode_id, submode_id)
            )
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mode_parameters_lookup
            ON mode_parameters(location, cluster, mode_id, submode_id)
        """)

        logger.info("Room modes tables created/verified")
