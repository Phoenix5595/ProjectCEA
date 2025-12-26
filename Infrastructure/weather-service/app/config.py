"""Configuration loader for YAML config files."""
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads and parses YAML configuration files for weather service."""
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize config loader.
        
        Args:
            config_path: Path to weather_config.yaml. If None, searches in common locations.
        """
        if config_path is None:
            # Try common locations
            possible_paths = [
                Path(__file__).parent.parent / "weather_config.yaml",
                Path("/home/antoine/Project CEA/Infrastructure/weather-service/weather_config.yaml"),
            ]
            for path in possible_paths:
                if path.exists():
                    config_path = str(path)
                    break
        
        if config_path is None or not Path(config_path).exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        self.config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self.load()
    
    def load(self) -> None:
        """Load configuration from YAML file."""
        with open(self.config_path, 'r') as f:
            self._config = yaml.safe_load(f) or {}
        
        logger.info(f"Loaded config from {self.config_path}")
    
    def get_weather_config(self) -> Dict[str, Any]:
        """Get weather API configuration."""
        return self._config.get('weather', {
            'station_icao': 'CYUL',
            'poll_interval': 900,
            'api_url': 'https://aviationweather.gov/api/data/metar'
        })
    
    def get_database_config(self) -> Dict[str, Any]:
        """Get database configuration."""
        return self._config.get('database', {
            'host': 'localhost',
            'database': 'cea_sensors',
            'user': 'cea_user',
            'password': 'Lenin1917',
            'port': 5432
        })
    
    def get_room_config(self) -> Dict[str, Any]:
        """Get room and device configuration."""
        return self._config.get('room', {
            'name': 'Outside',
            'device_name': 'Weather Station YUL'
        })
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., 'weather.station_icao')."""
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












