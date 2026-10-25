"""Control-loop tick methods for the control engine."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json

from app.control.light_effective_setpoint_logging import log_light_effective_intensities_for_cluster
from app.control.performance_monitor import get_performance_monitor
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.control.scheduler import LOCAL_TZ
from app.redis.schema import relay_raw_override_key
from app.schemas.monitoring_models import Phase
from shared.infra_logging import get_logger
from shared.monitoring_contracts import PhotoperiodPhase
from shared.room_light_authority import is_moon_authority_mode

logger = get_logger("app.control.control_engine")


class TickMixin:
    async def run_control_loop(self) -> None:
        """Run one iteration of the control loop with performance profiling."""
        get_binary_hysteresis = getattr(self.config, "get_binary_hysteresis", None)
        if callable(get_binary_hysteresis):
            self.device_controller.binary_hysteresis = float(get_binary_hysteresis())
        snapshot = self.runtime_device_registry.snapshot
        snapshot_token = self.relay_manager.bind_snapshot(snapshot)
        scheduler_snapshot_token = self.scheduler.bind_snapshot(snapshot)
        self._tick_effective_setpoints.clear()
        self._photoperiod_phases.clear()
        try:
            await self.relay_manager.retry_unresolved()
            await self._run_control_loop_with_snapshot(snapshot)
            self._mark_removed_rooms_unavailable(snapshot)
        except Exception:
            # Before the existing failure handling, every snapshot room loses its
            # resolved-phase offer, so history records the outage explicitly.
            self._mark_snapshot_rooms_unavailable(snapshot)
            raise
        finally:
            self._tick_effective_setpoints.clear()
            self._photoperiod_phases.clear()
            if self.relay_board_state_manager is not None:
                await self.relay_board_state_manager.sample()
                if self.alarm_manager is not None:
                    await self.relay_manager.evaluate_observation(
                        self.alarm_manager, self.device_command_service
                    )
            self.scheduler.release_snapshot(scheduler_snapshot_token)
            self.relay_manager.release_snapshot(snapshot_token)

    async def _run_control_loop_with_snapshot(self, snapshot: RuntimeDeviceSnapshot) -> None:
        """Run one tick against the snapshot reference captured at entry."""
        self._tick_effective_setpoints.clear()
        self._photoperiod_phases.clear()

        # Expire manual overrides before processing devices
        try:
            await self._expire_manual_overrides()
        except Exception as e:
            logger.error(f"Failed to expire manual overrides: {e}")

        # Expire raw channel overrides before processing devices
        try:
            await self._expire_raw_channel_overrides()
        except Exception as e:
            logger.error(f"Failed to expire raw channel overrides: {e}")

        loop_start_time = datetime.now() if self._profiling_enabled else None

        current_time = datetime.now(tz=LOCAL_TZ)

        # This hierarchy is fixed for the entire tick; no registry DB read occurs here.
        devices = snapshot.hierarchy
        sensor_mapping = self._config_cache.get_sensor_mapping(self.config.get_sensor_mapping)

        # Debug logging removed

        device_processing_start = datetime.now() if self._profiling_enabled else None

        rooms = [
            (location, cluster) for location, clusters in devices.items() for cluster in clusters
        ]
        sensor_start = datetime.now() if self._profiling_enabled else None
        sensor_values_by_room = await self.sensor_reader.read_sensors_for_rooms(
            rooms, sensor_mapping
        )
        if self._profiling_enabled and sensor_start:
            sensor_time = (datetime.now() - sensor_start).total_seconds() * 1000
            self._record_performance_stat("sensor_reading_time", sensor_time)

        # Process each location/cluster

        for location, clusters in devices.items():
            for cluster, cluster_devices in clusters.items():
                if location in ["Veg Room", "Flower Room"]:
                    logger.debug(
                        f"Control loop processing {location}/{cluster} with {len(cluster_devices)} devices"
                    )

                # Pipeline Step 1: Read sensor values
                sensor_values = sensor_values_by_room[(location, cluster)]

                # Pipeline Step 2: Resolve climate period
                active_profile = self._get_active_room_mode(location, cluster, snapshot)
                period_data = await self.climate_resolver.resolve_period(
                    location,
                    cluster,
                    current_time,
                    self.database,
                    active_profile=active_profile,
                )

                active_period = period_data["active_period"]
                current_period_name = period_data["current_period_name"]
                setpoint_data = period_data["setpoint_data"]

                # Detect period transition for logging
                period_key = (location, cluster)
                previous_period = self._current_period_name.get(period_key)
                if active_period:
                    self._current_period_name[period_key] = current_period_name
                    if previous_period and previous_period != current_period_name:
                        logger.info(
                            f"PERIOD CHANGE: {location}/{cluster} {previous_period} -> {current_period_name}"
                        )
                else:
                    logger.warning(
                        f"No climate period found for {location}/{cluster} at {period_data['time_str']}. "
                        + "Cannot compute setpoints."
                    )

                # Pipeline Step 3: Calculate effective setpoints
                effective_data = None
                if setpoint_data:
                    effective_data = await self.setpoint_calculator.calculate_setpoints(
                        location,
                        cluster,
                        current_time,
                        current_period_name,
                        previous_period,
                        setpoint_data,
                        sensor_values,
                        self.setpoint_manager,
                    )

                    if effective_data:
                        # Add current VPD for humidifier/dehumidifier control
                        effective_data = self.setpoint_calculator.add_current_vpd(
                            effective_data, location, cluster, sensor_values, sensor_mapping
                        )

                        # Store in context
                        self._effective_setpoints[(location, cluster)] = effective_data
                        self._tick_effective_setpoints[(location, cluster)] = effective_data

                        # Log to database
                        await self._log_effective_setpoints(
                            location, cluster, current_period_name, effective_data, current_time
                        )

                # Derive current_mode and previous_mode for device_processor compatibility
                current_mode = current_period_name if active_period else "NO_PERIOD"
                climate_mode_key = (location, cluster)
                previous_mode = self._current_climate_mode.get(climate_mode_key)
                self._current_climate_mode[climate_mode_key] = current_mode

                # Calculate is_sun for light control
                is_sun = self.climate_resolver.calculate_is_sun(current_time, location, cluster)
                room_key = (location, cluster)
                active_mode = self._get_active_room_mode(location, cluster, snapshot)
                moon_authority_active = is_moon_authority_mode((active_mode or {}).get("mode_name"))
                was_moon_authority_active = room_key in self._moon_authority_forced_moon
                if moon_authority_active:
                    if room_key not in self._moon_authority_forced_moon:
                        logger.info(
                            "Moon-authority room mode active for %s/%s: forcing scheduled light authority to MOON",
                            location,
                            cluster,
                        )
                    self._moon_authority_forced_moon.add(room_key)
                    is_sun = False
                elif was_moon_authority_active:
                    self._moon_authority_forced_moon.remove(room_key)

                self._photoperiod_phases[room_key] = (
                    PhotoperiodPhase.SUN if is_sun else PhotoperiodPhase.MOON
                )

                # Every resolved room phase is offered to history after the
                # Sleep/Drying authority override, outside the moon-authority
                # conditional. The sink deduplicates unchanged phases itself.
                self._enqueue_final_photoperiod_phase(
                    location=location,
                    cluster=cluster,
                    active_mode=active_mode,
                    phase=Phase.SUN if is_sun else Phase.MOON,
                    snapshot=snapshot,
                    observed_at=current_time,
                )

                # 6. Process devices (using extracted component)
                await self.device_processor.process_devices(
                    location,
                    cluster,
                    cluster_devices,
                    sensor_values,
                    current_time,
                    effective_data,
                    current_mode,
                    is_sun=is_sun,
                    previous_climate_mode=previous_mode,
                    log_context=self._automation_log_context,
                )

                await log_light_effective_intensities_for_cluster(
                    location=location,
                    cluster=cluster,
                    cluster_devices=cluster_devices,
                    current_time=current_time,
                    is_sun=is_sun,
                    scheduler=self.scheduler,
                    database=self.database,
                    last_light_effective_log=self._last_light_effective_log,
                    interval_sec=float(self._light_effective_log_interval_sec),
                    redis_client=getattr(self.database, "_automation_redis", None),
                    last_sun_schedule_gap_error=self._last_light_sun_schedule_gap_error,
                    sun_schedule_gap_error_interval_sec=60.0,
                )

        if self._pending_db_writes:
            _ = await asyncio.gather(*self._pending_db_writes, return_exceptions=True)
            self._pending_db_writes.clear()

        # Log automation state for all devices
        await self._log_automation_state(snapshot)
        self._offer_current_snapshot(snapshot, current_time.astimezone(UTC))

        # Record performance statistics
        if self._profiling_enabled and loop_start_time:
            total_time = (datetime.now() - loop_start_time).total_seconds() * 1000
            self._record_performance_stat("total_loop_time", total_time)
            get_performance_monitor().record_operation("total_loop_time", total_time / 1000.0)

            if device_processing_start:
                device_time = (datetime.now() - device_processing_start).total_seconds() * 1000
                self._record_performance_stat("device_processing_time", device_time)
                get_performance_monitor().record_operation(
                    "device_processing_time", device_time / 1000.0
                )

    async def _expire_manual_overrides(self) -> None:
        """Restore elapsed in-process timed commands without querying history."""
        if self.device_command_service is not None:
            await self.device_command_service.expire_commands()

    async def _expire_raw_channel_overrides(self) -> None:
        """Sweep and expire raw channel overrides whose timer has passed.

        Checks each channel 0-15 for an expired Redis override record
        (cea:relay:manual_override:{channel}) and turns the channel OFF
        plus deletes the key.
        """
        automation_redis = self.database._automation_redis
        if automation_redis is None or automation_redis.redis_client is None:
            return

        redis_client = automation_redis.redis_client

        raws = await asyncio.to_thread(
            redis_client.mget,
            [relay_raw_override_key(channel) for channel in range(16)],
        )
        for channel, raw in zip(range(16), raws, strict=False):
            if raw is None:
                continue

            payload = json.loads(str(raw))
            expires_at = datetime.fromisoformat(payload["expires_at"])
            if expires_at <= datetime.now(UTC):
                await self.relay_manager.set_channel_state(channel, 0)
                key = relay_raw_override_key(channel)
                await asyncio.to_thread(redis_client.delete, key)
                logger.info(f"Raw channel {channel} manual override expired, turned OFF")
