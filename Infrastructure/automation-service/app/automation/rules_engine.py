"""Rules engine for if-then automation rules."""
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class RulesEngine:
    """Evaluates automation rules based on sensor conditions."""
    
    def __init__(self, rules: List[Dict[str, any]], scheduler):
        """Initialize rules engine.
        
        Args:
            rules: List of rule dictionaries from database or config
            scheduler: Scheduler instance to check if rule's schedule is active
        """
        self.rules = rules
        self.scheduler = scheduler
        logger.info(f"Initialized rules engine with {len(rules)} rules")
    
    def evaluate(
        self,
        location: str,
        cluster: str,
        sensor_values: Dict[str, Optional[float]],
        current_time: Optional[datetime] = None
    ) -> Optional[Tuple[str, int, int]]:
        """Evaluate rules for a location/cluster.
        
        Args:
            location: Location name
            cluster: Cluster name
            sensor_values: Dict mapping sensor names to values
            current_time: Current time (default: now)
        
        Returns:
            Tuple of (device_name, action_state, rule_id) if rule matches, None otherwise
            Returns highest priority matching rule
        """
        if current_time is None:
            current_time = datetime.now()
        
        matching_rules = []
        
        for rule in self.rules:
            if not rule.get('enabled', True):
                continue
            
            if (rule.get('location') != location or
                rule.get('cluster') != cluster):
                continue
            
            # Check if rule's schedule is active
            schedule_id = rule.get('schedule_id')
            if schedule_id is not None:
                # Rule is constrained by schedule - check if schedule is active
                # Check if the specific schedule is active
                # We need to find the schedule by ID and check if it's active
                # For now, we'll check if any schedule for the action device is active
                # This is a simplification - ideally we'd check the specific schedule_id
                device_name = rule.get('action_device')
                is_active, active_schedule_id = self.scheduler.is_schedule_active(
                    location, cluster, device_name, current_time
                )
                if not is_active or active_schedule_id != schedule_id:
                    continue  # Rule's schedule not active, skip
            
            # Evaluate condition
            condition_sensor = rule.get('condition_sensor')
            condition_operator = rule.get('condition_operator')
            condition_value = rule.get('condition_value')
            
            if condition_sensor not in sensor_values:
                continue
            
            sensor_value = sensor_values.get(condition_sensor)
            if sensor_value is None:
                continue  # Skip if sensor value is missing
            
            # Evaluate condition
            condition_met = self._evaluate_condition(
                sensor_value, condition_operator, condition_value
            )
            
            if condition_met:
                matching_rules.append({
                    'rule': rule,
                    'priority': rule.get('priority', 0),
                    'device': rule.get('action_device'),
                    'state': rule.get('action_state')
                })
        
        # Return highest priority rule
        if matching_rules:
            # Sort by priority (higher priority first)
            matching_rules.sort(key=lambda x: x['priority'], reverse=True)
            best_rule = matching_rules[0]
            return (
                best_rule['device'],
                best_rule['state'],
                best_rule['rule'].get('id')
            )
        
        return None
    
    def _evaluate_condition(
        self, 
        sensor_value: float, 
        operator: str, 
        threshold: float
    ) -> bool:
        """Evaluate a condition.
        
        Args:
            sensor_value: Current sensor value
            operator: Comparison operator ('<', '>', '<=', '>=', '==')
            threshold: Threshold value
        
        Returns:
            True if condition is met, False otherwise
        """
        if operator == '<':
            return sensor_value < threshold
        elif operator == '>':
            return sensor_value > threshold
        elif operator == '<=':
            return sensor_value <= threshold
        elif operator == '>=':
            return sensor_value >= threshold
        elif operator == '==':
            return abs(sensor_value - threshold) < 0.01  # Float comparison
        else:
            logger.warning(f"Unknown operator: {operator}")
            return False
    
    def update_rules(self, rules: List[Dict[str, any]]):
        """Update rules list."""
        self.rules = rules
        logger.info(f"Updated rules: {len(rules)} rules")

