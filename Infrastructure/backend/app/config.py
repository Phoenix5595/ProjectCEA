"""Configuration loader for YAML config file."""
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional


class ConfigLoader:
    """Loads and parses YAML configuration file."""
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize config loader.
        
        Args:
            config_path: Path to config.yaml. If None, searches in common locations.
        """
        if config_path is None:
            # Try common locations
            possible_paths = [
                Path(__file__).parent.parent.parent.parent / "config.yaml",
                Path(__file__).parent.parent.parent / "config.yaml",
                Path("/home/antoine/Project CEA/Test Scripts/GUI/config.yaml"),
            ]
            for path in possible_paths:
                if path.exists():
                    config_path = str(path)
                    break
        
        if config_path is None or not Path(config_path).exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        self.config_path = config_path
        self._config: Dict[str, Any] = {}
        self.load()
    
    def load(self) -> None:
        """Load configuration from YAML file."""
        with open(self.config_path, 'r') as f:
            self._config = yaml.safe_load(f) or {}
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., 'dashboard.refresh_interval')."""
        keys = key.split('.')
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        return value
    
    def get_locations(self) -> List[str]:
        """Get list of available locations."""
        sensors = self._config.get('sensors', {})
        return sensors.get('locations', [])
    
    def get_sensors_for_location(self, location: str) -> Dict[str, Any]:
        """Get sensor configuration for a specific location."""
        sensors = self._config.get('sensors', {})
        
        # Map location name to config key
        location_map = {
            "Flower Room": "flower_room",
            "Veg Room": "veg_room",
            "Lab": "lab",
            "Outside": "outside"
        }
        
        config_key = location_map.get(location, location.lower().replace(" ", "_"))
        location_config = sensors.get(config_key, {})
        
        return location_config.get('clusters', {})

