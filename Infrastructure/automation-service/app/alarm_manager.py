"""Alarm manager for tracking alarms and enforcing failsafe."""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from app.redis_client import AutomationRedisClient
from app.database import DatabaseManager

logger = logging.getLogger(__name__)


class AlarmManager:
    """Manages alarms and failsafe enforcement."""
    
    def __init__(
        self,
        redis_client: AutomationRedisClient,
        database: Optional[DatabaseManager] = None
    ):
        """Initialize alarm manager.
        
        Args:
            redis_client: AutomationRedisClient instance
            database: Optional DatabaseManager for logging alarms
        """
        self.redis_client = redis_client
        self.database = database
        self._active_alarms: Dict[str, Dict[str, Any]] = {}  # Cache of active alarms
    
    def raise_alarm(
        self,
        location: str,
        cluster: str,
        alarm_name: str,
        severity: str,
        message: str
    ) -> bool:
        """Raise an alarm.
        
        Args:
            location: Location name
            cluster: Cluster name
            alarm_name: Alarm identifier
            severity: Alarm severity ('info', 'warning', 'critical')
            message: Alarm message
        
        Returns:
            True if successful, False otherwise
        """
        # Write to Redis
        success = self.redis_client.write_alarm(location, cluster, alarm_name, severity, message)
        
        if success:
            # Cache alarm
            key = f"{location}:{cluster}:{alarm_name}"
            self._active_alarms[key] = {
                'location': location,
                'cluster': cluster,
                'alarm_name': alarm_name,
                'severity': severity,
                'message': message,
                'active': True
            }
            
            # If critical, trigger failsafe
            if severity == 'critical':
                self._trigger_failsafe(location, cluster, 'critical_alarm', alarm_name)
            
            # Log to database if available
            if self.database:
                # Could add alarm logging table if needed
                pass
        
        return success
    
    def clear_alarm(
        self,
        location: str,
        cluster: str,
        alarm_name: str
    ) -> bool:
        """Clear an alarm (set active=False).
        
        Args:
            location: Location name
            cluster: Cluster name
            alarm_name: Alarm identifier
        
        Returns:
            True if successful, False otherwise
        """
        success = self.redis_client.clear_alarm(location, cluster, alarm_name)
        
        if success:
            # Remove from cache
            key = f"{location}:{cluster}:{alarm_name}"
            self._active_alarms.pop(key, None)
        
        return success
    
    def acknowledge_alarm(
        self,
        location: str,
        cluster: str,
        alarm_name: str
    ) -> bool:
        """Acknowledge an alarm.
        
        Args:
            location: Location name
            cluster: Cluster name
            alarm_name: Alarm identifier
        
        Returns:
            True if successful, False otherwise
        """
        return self.redis_client.acknowledge_alarm(location, cluster, alarm_name)
    
    def get_alarms(
        self,
        location: Optional[str] = None,
        cluster: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get all active alarms.
        
        Args:
            location: Optional location filter
            cluster: Optional cluster filter
        
        Returns:
            Dict mapping alarm key to alarm data
        """
        if location and cluster:
            return self.redis_client.read_alarms(location, cluster)
        
        # Get all alarms (would need to scan all locations/clusters)
        # For now, return cached alarms
        return self._active_alarms.copy()
    
    def check_critical_alarms(
        self,
        location: str,
        cluster: str
    ) -> bool:
        """Check if there are any critical alarms for a location/cluster.
        
        Args:
            location: Location name
            cluster: Cluster name
        
        Returns:
            True if critical alarms exist, False otherwise
        """
        alarms = self.redis_client.read_alarms(location, cluster)
        for alarm_name, alarm_data in alarms.items():
            if alarm_data.get('severity') == 'critical' and alarm_data.get('active', False):
                return True
        return False
    
    def _trigger_failsafe(
        self,
        location: str,
        cluster: str,
        reason: str,
        triggered_by: str
    ) -> None:
        """Trigger failsafe mode for a location/cluster.
        
        Args:
            location: Location name
            cluster: Cluster name
            reason: Failsafe reason
            triggered_by: What triggered the failsafe
        """
        # Set mode to failsafe
        self.redis_client.write_mode(location, cluster, 'failsafe', source='system')
        
        # Write failsafe details
        self.redis_client.write_failsafe(location, cluster, reason, triggered_by)
        
        logger.critical(f"FAILSAFE TRIGGERED: {location}/{cluster} - {reason} (triggered by: {triggered_by})")
    
    def clear_failsafe(
        self,
        location: str,
        cluster: str
    ) -> bool:
        """Clear failsafe mode if conditions are met.
        
        Args:
            location: Location name
            cluster: Cluster name
        
        Returns:
            True if failsafe cleared, False if conditions not met
        """
        # Check if critical alarms still exist
        if self.check_critical_alarms(location, cluster):
            logger.warning(f"Cannot clear failsafe for {location}/{cluster}: critical alarms still active")
            return False
        
        # Clear failsafe state
        success = self.redis_client.clear_failsafe(location, cluster)
        
        if success:
            # Set mode back to auto
            self.redis_client.write_mode(location, cluster, 'auto', source='system')
            logger.info(f"Failsafe cleared for {location}/{cluster}")
        
        return success
    
    def update_alarm_cache(self) -> None:
        """Update internal alarm cache from Redis.
        
        This should be called periodically to keep cache in sync.
        """
        # For now, alarms are read directly from Redis
        # Cache update could be implemented if needed for performance
        pass

