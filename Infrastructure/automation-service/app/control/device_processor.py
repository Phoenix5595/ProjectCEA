"""Device processor component for automation control."""
from shared.logging import get_logger
from datetime import datetime
from typing import Dict, Optional, Any
from app.control.device_controller import DeviceController
from app.control.scheduler import Scheduler
from app.database import DatabaseManager
from app.hardware.dfr0971 import DFR0971Manager

logger = get_logger(__name__)


class DeviceProcessor:
    """Handles processing of devices in the automation loop."""

    def __init__(
        self,
        device_controller: DeviceController,
        database: DatabaseManager,
        dfr0971_manager: Optional[DFR0971Manager] = None,
        scheduler: Optional[Scheduler] = None,
    ):
        """Initialize device processor.

        Args:
            device_controller: Device controller instance
            database: Database manager instance
            dfr0971_manager: Optional DFR0971 manager for light intensity
            scheduler: Optional scheduler for light intensity calculations
        """
        self.device_controller = device_controller
        self.database = database
        self.dfr0971_manager = dfr0971_manager
        self.scheduler = scheduler

    async def process_devices(
        self,
        location: str,
        cluster: str,
        cluster_devices: Dict[str, Dict[str, Any]],
        sensor_values: Dict[str, Optional[float]],
        current_time: datetime,
        effective_data: Optional[Dict[str, Any]],
        current_mode: Optional[str],
    ) -> None:
        """Process all devices for a location/cluster.

        Args:
            location: Location name
            cluster: Cluster name
            cluster_devices: Dict of device_name -> device_info
            sensor_values: Current sensor values
            current_time: Current timestamp
            effective_data: Effective setpoint data
            current_mode: Current climate mode
        """
        # Process each device
        for device_name, device_info in cluster_devices.items():
            # Build context for device processing
            if effective_data:
                context = {
                    'effective_heating_setpoint': effective_data.get('effective_heating_setpoint'),
                    'effective_cooling_setpoint': effective_data.get('effective_cooling_setpoint'),
                    'effective_humidity_setpoint': effective_data.get('effective_humidity_setpoint'),
                    'effective_co2_setpoint': effective_data.get('effective_co2_setpoint'),
                    'effective_vpd_setpoint': effective_data.get('effective_vpd_setpoint'),
                    'failsafe_active': False,  # TODO: implement failsafe logic
                    'current_mode': current_mode
                }
            else:
                context = {
                    'effective_heating_setpoint': None,
                    'effective_cooling_setpoint': None,
                    'effective_humidity_setpoint': None,
                    'effective_co2_setpoint': None,
                    'effective_vpd_setpoint': None,
                    'failsafe_active': False,
                    'current_mode': current_mode
                }
            
            # Add light intensity for dimmable lights
            if device_info.get('dimming_enabled') and device_info.get('dimming_type') == 'dfr0971':
                if self.scheduler:
                    intensity_details = self.scheduler.get_light_intensity_details(
                        location, cluster, device_name, current_time
                    )
                    if intensity_details:
                        # Convert percentage (0-100) to ratio (0.0-1.0) for control output
                        context['light_intensity'] = intensity_details['effective_intensity'] / 100.0
                        logger.debug(f"Light {device_name} scheduled intensity: {intensity_details['effective_intensity']}%")

            await self.device_controller.process_device(
                location, cluster, device_name, device_info,
                sensor_values, current_time, context
            )
        
        # Note: Light intensity logging is handled by ControlEngine.run_control_loop()
        # to ensure scheduler access for proper ramp calculations