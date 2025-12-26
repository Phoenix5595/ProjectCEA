"""Configuration loader for YAML config files."""
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads and parses YAML configuration files."""
    
    def __init__(self, config_path: Optional[str] = None):
        """Initialize config loader.
        
        Args:
            config_path: Path to automation_config.yaml. If None, searches in common locations.
        """
        if config_path is None:
            # Try common locations
            possible_paths = [
                Path(__file__).parent.parent / "automation_config.yaml",
                Path("/home/antoine/Project CEA/Infrastructure/automation-service/automation_config.yaml"),
            ]
            for path in possible_paths:
                if path.exists():
                    config_path = str(path)
                    break
        
        if config_path is None or not Path(config_path).exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        
        self.config_path = Path(config_path)
        self.schedules_path = self.config_path.parent / "schedules.yaml"
        self.rules_path = self.config_path.parent / "rules.yaml"
        self._config: Dict[str, Any] = {}
        self._schedules: List[Dict[str, Any]] = []
        self._rules: List[Dict[str, Any]] = []
        self.load()
    
    def load(self) -> None:
        """Load configuration from YAML files."""
        # Load main config
        with open(self.config_path, 'r') as f:
            self._config = yaml.safe_load(f) or {}
        
        # Load schedules if exists
        if self.schedules_path.exists():
            with open(self.schedules_path, 'r') as f:
                schedules_data = yaml.safe_load(f) or {}
                self._schedules = schedules_data.get('schedules', [])
        else:
            # Check if schedules are in main config
            if 'schedules' in self._config:
                self._schedules = self._config['schedules']
        
        # Load rules if exists
        if self.rules_path.exists():
            with open(self.rules_path, 'r') as f:
                rules_data = yaml.safe_load(f) or {}
                self._rules = rules_data.get('rules', [])
        else:
            # Check if rules are in main config
            if 'rules' in self._config:
                self._rules = self._config['rules']
        
        logger.info(f"Loaded config from {self.config_path}")
        if self._schedules:
            logger.info(f"Loaded {len(self._schedules)} schedules")
        if self._rules:
            logger.info(f"Loaded {len(self._rules)} rules")
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value using dot notation (e.g., 'hardware.i2c_bus')."""
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
    
    def get_hardware_config(self) -> Dict[str, Any]:
        """Get hardware configuration."""
        return self._config.get('hardware', {})
    
    def get_devices(self) -> Dict[str, Any]:
        """Get device configuration."""
        return self._config.get('devices', {})
    
    def get_control_config(self) -> Dict[str, Any]:
        """Get control configuration."""
        return self._config.get('control', {})
    
    def get_pid_config(self) -> Dict[str, Any]:
        """Get PID configuration."""
        return self._config.get('control', {}).get('pid', {})
    
    def get_safety_limits(self) -> Dict[str, float]:
        """Get safety limits."""
        return self._config.get('control', {}).get('safety_limits', {})
    
    def get_default_setpoints(self) -> Dict[str, Any]:
        """Get default setpoints."""
        return self._config.get('control', {}).get('default_setpoints', {})
    
    def get_sensor_mapping(self) -> Dict[str, Any]:
        """Get sensor mapping."""
        return self._config.get('sensors', {})
    
    def get_update_interval(self) -> int:
        """Get control loop update interval in seconds."""
        return self._config.get('control', {}).get('update_interval', 1)
    
    def get_schedules(self) -> List[Dict[str, Any]]:
        """Get schedules."""
        return self._schedules
    
    def get_rules(self) -> List[Dict[str, Any]]:
        """Get rules."""
        return self._rules
    
    def get_interlocks(self) -> List[Dict[str, Any]]:
        """Get interlock rules."""
        return self._config.get('interlocks', [])
    
    def get_pid_params_for_device(self, device_type: str) -> Dict[str, float]:
        """Get PID parameters for a device type.
        
        Args:
            device_type: Device type (e.g., 'heater', 'co2')
        
        Returns:
            Dict with 'kp', 'ki', 'kd' values
        """
        pid_config = self.get_pid_config()
        
        # Try device-specific params first
        kp_key = f"{device_type}_kp"
        ki_key = f"{device_type}_ki"
        kd_key = f"{device_type}_kd"
        
        kp = pid_config.get(kp_key, pid_config.get('default_kp', 10.0))
        ki = pid_config.get(ki_key, pid_config.get('default_ki', 0.01))
        kd = pid_config.get(kd_key, pid_config.get('default_kd', 0.0))
        
        return {'kp': kp, 'ki': ki, 'kd': kd}
    
    def reload(self) -> None:
        """Reload configuration from files (incremental reload)."""
        old_config = self._config.copy()
        self.load()
        logger.info("Configuration reloaded")
        # Note: Incremental reload - changes applied as loaded, not atomic

