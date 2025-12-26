"""Configuration loader for YAML config files."""
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads and parses YAML configuration files for soil sensor service."""
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize config loader.
        
        Args:
            config_path: Path to soil_sensor_config.yaml. If None, searches in common locations.
        """
        if config_path is None:
            # Try common locations
            possible_paths = [
                Path(__file__).parent.parent / "soil_sensor_config.yaml",
                Path("/home/antoine/Project CEA/Infrastructure/soil-sensor-service/soil_sensor_config.yaml"),
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
    
    def get_rs485_config(self) -> Dict[str, Any]:
        """Get RS485 serial port configuration."""
        return self._config.get('rs485', {
            'port': '/dev/serial0',
            'baudrate': 9600,
            'timeout': 1.0
        })
    
    def get_polling_config(self) -> Dict[str, Any]:
        """Get polling interval configuration."""
        return self._config.get('polling', {
            'interval_seconds': 5
        })
    
    def get_sensors(self) -> List[Dict[str, Any]]:
        """Get list of sensor configurations."""
        return self._config.get('sensors', [])
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., 'rs485.port')."""
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

