"""Alarm management endpoints."""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from app.database import DatabaseManager
from app.alarm_manager import AlarmManager

router = APIRouter()


class AlarmAcknowledgeRequest(BaseModel):
    """Request to acknowledge an alarm."""
    pass  # No additional fields needed


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    from app.main import get_database as _get_database
    return _get_database()


def get_alarm_manager() -> Optional[AlarmManager]:
    """Get alarm manager."""
    from app.main import alarm_manager
    return alarm_manager


@router.get("/api/alarms/{location}/{cluster}")
async def get_alarms(
    location: str,
    cluster: str,
    alarm_manager: Optional[AlarmManager] = Depends(get_alarm_manager)
) -> Dict[str, Any]:
    """Get all alarms for a location/cluster.
    
    Returns:
        Dict mapping alarm_name to alarm data
    """
    if not alarm_manager:
        raise HTTPException(status_code=503, detail="Alarm manager not available")
    
    alarms = alarm_manager.get_alarms(location, cluster)
    
    return {
        "location": location,
        "cluster": cluster,
        "alarms": alarms
    }


@router.get("/api/alarms")
async def get_all_alarms(
    alarm_manager: Optional[AlarmManager] = Depends(get_alarm_manager)
) -> Dict[str, Dict[str, Any]]:
    """Get all alarms for all locations/clusters.
    
    Returns:
        Dict mapping "location:cluster" to alarms dict
    """
    if not alarm_manager:
        raise HTTPException(status_code=503, detail="Alarm manager not available")
    
    # Get all alarms (would need to scan all locations/clusters)
    # For now, return cached alarms
    all_alarms = alarm_manager.get_alarms()
    
    # Group by location:cluster
    grouped = {}
    for key, alarm_data in all_alarms.items():
        loc = alarm_data.get('location', '')
        clust = alarm_data.get('cluster', '')
        alarm_name = alarm_data.get('alarm_name', '')
        
        group_key = f"{loc}:{clust}"
        if group_key not in grouped:
            grouped[group_key] = {
                "location": loc,
                "cluster": clust,
                "alarms": {}
            }
        
        grouped[group_key]["alarms"][alarm_name] = alarm_data
    
    return grouped


@router.post("/api/alarms/{location}/{cluster}/{alarm_name}/acknowledge")
async def acknowledge_alarm(
    location: str,
    cluster: str,
    alarm_name: str,
    alarm_manager: Optional[AlarmManager] = Depends(get_alarm_manager)
) -> Dict[str, Any]:
    """Acknowledge an alarm.
    
    Args:
        location: Location name
        cluster: Cluster name
        alarm_name: Alarm identifier
    
    Returns:
        Success status
    """
    if not alarm_manager:
        raise HTTPException(status_code=503, detail="Alarm manager not available")
    
    success = alarm_manager.acknowledge_alarm(location, cluster, alarm_name)
    
    if not success:
        raise HTTPException(status_code=404, detail="Alarm not found")
    
    return {
        "location": location,
        "cluster": cluster,
        "alarm_name": alarm_name,
        "acknowledged": True,
        "success": True
    }

