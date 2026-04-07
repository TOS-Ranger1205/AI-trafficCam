"""
Rule Engine for Traffic Violation Detection

This module provides rule-based logic for detecting traffic violations.
Violations are detected based on vehicle behavior, NOT AI inference during playback.

Violation Types:
- RED_LIGHT: Vehicle crosses stop line while traffic light is red
- NO_HELMET: Motorcycle rider without helmet
- WRONG_WAY: Vehicle traveling against traffic flow
- OVERSPEEDING: Vehicle exceeds configured speed limit
- TRIPLE_RIDING: More than 2 people on motorcycle
- LANE_VIOLATION: Vehicle in wrong lane
- ZEBRA_CROSSING: Vehicle on pedestrian crossing with pedestrians present
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import numpy as np


class ViolationSeverity(str, Enum):
    """Severity levels for violations."""
    LOW = "low"           # Minor infractions
    MEDIUM = "medium"     # Standard violations  
    HIGH = "high"         # Serious violations
    CRITICAL = "critical" # Life-threatening violations


@dataclass
class ViolationRule:
    """Configuration for a violation rule."""
    name: str
    violation_type: str
    description: str
    fine_amount: int
    severity: ViolationSeverity
    min_confidence: float = 0.6
    
    # Rule-specific parameters
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleCheckResult:
    """Result of a rule check."""
    triggered: bool
    violation_type: str
    confidence: float
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)


class TrafficRuleEngine:
    """
    Rule-based engine for traffic violation detection.
    
    This engine applies configurable rules to vehicle detection data
    to determine if violations have occurred.
    """
    
    # Default rule configurations (can be overridden via config)
    DEFAULT_RULES = {
        "red_light": ViolationRule(
            name="Red Light Violation",
            violation_type="red_light",
            description="Vehicle crossed stop line while traffic light was red",
            fine_amount=5000,
            severity=ViolationSeverity.HIGH,
            params={
                "min_crossing_distance": 20,  # pixels
                "min_red_duration": 0.5,      # seconds
            }
        ),
        "overspeeding": ViolationRule(
            name="Overspeeding",
            violation_type="speed_violation",
            description="Vehicle exceeded speed limit",
            fine_amount=2000,
            severity=ViolationSeverity.MEDIUM,
            params={
                "default_speed_limit": 60,    # km/h
                "tolerance_percent": 10,       # 10% buffer
            }
        ),
        "no_helmet": ViolationRule(
            name="No Helmet",
            violation_type="no_helmet",
            description="Motorcycle rider not wearing helmet",
            fine_amount=1000,
            severity=ViolationSeverity.MEDIUM,
            params={
                "min_helmet_ratio": 0.20,     # 20% of head region should have helmet
            }
        ),
        "wrong_way": ViolationRule(
            name="Wrong Way",
            violation_type="wrong_way",
            description="Vehicle traveling against traffic flow",
            fine_amount=5000,
            severity=ViolationSeverity.HIGH,
            params={
                "min_movement_pixels": 50,    # minimum movement to detect direction
                "allowed_direction": "forward",
            }
        ),
        "triple_riding": ViolationRule(
            name="Triple Riding",
            violation_type="triple_riding",
            description="More than 2 persons on motorcycle",
            fine_amount=1500,
            severity=ViolationSeverity.MEDIUM,
            params={
                "min_rider_count": 3,
            }
        ),
        "lane_violation": ViolationRule(
            name="Lane Violation",
            violation_type="lane_violation",
            description="Vehicle in wrong lane or improper lane change",
            fine_amount=500,
            severity=ViolationSeverity.LOW,
            params={
                "lane_crossing_threshold": 30,  # pixels
            }
        ),
        "zebra_crossing": ViolationRule(
            name="Zebra Crossing Violation",
            violation_type="zebra_crossing",
            description="Vehicle on pedestrian crossing with pedestrians present",
            fine_amount=500,
            severity=ViolationSeverity.MEDIUM,
            params={
                "min_pedestrian_distance": 150,  # pixels
            }
        ),
        "no_license_plate": ViolationRule(
            name="No License Plate",
            violation_type="no_license_plate",
            description="Vehicle without visible license plate",
            fine_amount=5000,
            severity=ViolationSeverity.HIGH,
            params={
                "max_consecutive_frames": 5,  # frames without plate detection
            }
        ),
    }
    
    def __init__(self, custom_rules: Optional[Dict[str, ViolationRule]] = None):
        """
        Initialize the rule engine.
        
        Args:
            custom_rules: Optional dictionary of custom rules to override defaults
        """
        self.rules = {**self.DEFAULT_RULES}
        if custom_rules:
            self.rules.update(custom_rules)
        
        # Runtime state for multi-frame analysis
        self.traffic_light_history: List[str] = []
        self.vehicle_plate_detections: Dict[int, int] = {}  # vehicle_id -> frames_with_plate
        
    def check_red_light_violation(
        self,
        vehicle_positions: List[Tuple[int, int]],
        traffic_light_state: str,
        stop_line_y: Optional[int] = None
    ) -> RuleCheckResult:
        """
        Check if vehicle violated red light.
        
        Args:
            vehicle_positions: List of (x, y) positions over time
            traffic_light_state: Current traffic light state
            stop_line_y: Y coordinate of stop line (if known)
            
        Returns:
            RuleCheckResult indicating if violation occurred
        """
        rule = self.rules["red_light"]
        
        if traffic_light_state != "red":
            return RuleCheckResult(
                triggered=False,
                violation_type=rule.violation_type,
                confidence=0,
                reason="Traffic light is not red"
            )
        
        if len(vehicle_positions) < 3:
            return RuleCheckResult(
                triggered=False,
                violation_type=rule.violation_type,
                confidence=0,
                reason="Insufficient position data"
            )
        
        # Calculate forward movement during red light
        start_y = vehicle_positions[0][1]
        end_y = vehicle_positions[-1][1]
        forward_movement = start_y - end_y  # Negative Y = forward in most camera views
        
        min_crossing = rule.params.get("min_crossing_distance", 20)
        
        if forward_movement > min_crossing:
            confidence = min(0.95, 0.70 + (forward_movement / 100) * 0.25)
            return RuleCheckResult(
                triggered=True,
                violation_type=rule.violation_type,
                confidence=confidence,
                reason=f"Vehicle moved forward {forward_movement:.0f}px during red light",
                evidence={
                    "forward_movement": forward_movement,
                    "positions_tracked": len(vehicle_positions),
                    "traffic_light_state": traffic_light_state
                }
            )
        
        return RuleCheckResult(
            triggered=False,
            violation_type=rule.violation_type,
            confidence=0,
            reason="No significant forward movement during red light"
        )
    
    def check_overspeeding(
        self,
        estimated_speed: float,
        speed_limit: Optional[float] = None,
        zone_type: str = "urban"
    ) -> RuleCheckResult:
        """
        Check if vehicle is overspeeding.
        
        Args:
            estimated_speed: Estimated vehicle speed in km/h
            speed_limit: Speed limit for the road (default from rule params)
            zone_type: Type of zone (urban, highway, school, etc.)
            
        Returns:
            RuleCheckResult indicating if violation occurred
        """
        rule = self.rules["overspeeding"]
        
        if speed_limit is None:
            speed_limit = rule.params.get("default_speed_limit", 60)
        
        # Adjust speed limit based on zone
        zone_multipliers = {
            "urban": 1.0,
            "highway": 1.5,
            "school": 0.5,
            "residential": 0.7,
        }
        speed_limit *= zone_multipliers.get(zone_type, 1.0)
        
        tolerance = rule.params.get("tolerance_percent", 10) / 100
        effective_limit = speed_limit * (1 + tolerance)
        
        if estimated_speed > effective_limit:
            excess = estimated_speed - speed_limit
            confidence = min(0.95, 0.60 + (excess / 50) * 0.35)
            
            return RuleCheckResult(
                triggered=True,
                violation_type=rule.violation_type,
                confidence=confidence,
                reason=f"Speed {estimated_speed:.0f} km/h exceeds limit {speed_limit:.0f} km/h by {excess:.0f} km/h",
                evidence={
                    "detected_speed": estimated_speed,
                    "speed_limit": speed_limit,
                    "excess_speed": excess,
                    "zone_type": zone_type
                }
            )
        
        return RuleCheckResult(
            triggered=False,
            violation_type=rule.violation_type,
            confidence=0,
            reason=f"Speed {estimated_speed:.0f} km/h is within limit"
        )
    
    def check_wrong_way(
        self,
        vehicle_positions: List[Tuple[int, int]],
        allowed_direction: str = "forward"
    ) -> RuleCheckResult:
        """
        Check if vehicle is traveling wrong way.
        
        Args:
            vehicle_positions: List of (x, y) positions over time
            allowed_direction: Expected traffic direction
            
        Returns:
            RuleCheckResult indicating if violation occurred
        """
        rule = self.rules["wrong_way"]
        
        if len(vehicle_positions) < 5:
            return RuleCheckResult(
                triggered=False,
                violation_type=rule.violation_type,
                confidence=0,
                reason="Insufficient position data"
            )
        
        # Calculate overall movement vector
        start = vehicle_positions[0]
        end = vehicle_positions[-1]
        
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        
        min_movement = rule.params.get("min_movement_pixels", 50)
        
        # In most camera setups: -Y = forward, +Y = backward
        if abs(dy) > min_movement:
            actual_direction = "backward" if dy > 0 else "forward"
            
            if actual_direction != allowed_direction:
                confidence = min(0.90, 0.60 + (abs(dy) / 150) * 0.30)
                return RuleCheckResult(
                    triggered=True,
                    violation_type=rule.violation_type,
                    confidence=confidence,
                    reason=f"Vehicle traveling {actual_direction}, expected {allowed_direction}",
                    evidence={
                        "movement_vector": {"dx": dx, "dy": dy},
                        "detected_direction": actual_direction,
                        "allowed_direction": allowed_direction
                    }
                )
        
        return RuleCheckResult(
            triggered=False,
            violation_type=rule.violation_type,
            confidence=0,
            reason="Vehicle direction matches expected flow"
        )
    
    def check_no_helmet(
        self,
        helmet_detected: bool,
        helmet_confidence: float,
        vehicle_type: str
    ) -> RuleCheckResult:
        """
        Check if motorcycle rider has no helmet.
        
        Args:
            helmet_detected: Whether helmet was detected
            helmet_confidence: Confidence of helmet detection
            vehicle_type: Type of vehicle
            
        Returns:
            RuleCheckResult indicating if violation occurred
        """
        rule = self.rules["no_helmet"]
        
        if vehicle_type not in ["motorcycle", "scooter", "moped"]:
            return RuleCheckResult(
                triggered=False,
                violation_type=rule.violation_type,
                confidence=0,
                reason="Vehicle is not a two-wheeler"
            )
        
        if not helmet_detected or helmet_confidence < rule.params.get("min_helmet_ratio", 0.20):
            confidence = 0.70 + (1 - helmet_confidence) * 0.25
            return RuleCheckResult(
                triggered=True,
                violation_type=rule.violation_type,
                confidence=confidence,
                reason=f"No helmet detected on {vehicle_type} rider",
                evidence={
                    "helmet_detected": helmet_detected,
                    "helmet_confidence": helmet_confidence,
                    "vehicle_type": vehicle_type
                }
            )
        
        return RuleCheckResult(
            triggered=False,
            violation_type=rule.violation_type,
            confidence=0,
            reason="Helmet detected on rider"
        )
    
    def check_triple_riding(
        self,
        rider_count: int,
        vehicle_type: str
    ) -> RuleCheckResult:
        """
        Check for triple riding on motorcycle.
        
        Args:
            rider_count: Number of detected riders
            vehicle_type: Type of vehicle
            
        Returns:
            RuleCheckResult indicating if violation occurred
        """
        rule = self.rules["triple_riding"]
        
        if vehicle_type not in ["motorcycle", "scooter", "moped"]:
            return RuleCheckResult(
                triggered=False,
                violation_type=rule.violation_type,
                confidence=0,
                reason="Vehicle is not a two-wheeler"
            )
        
        min_riders = rule.params.get("min_rider_count", 3)
        
        if rider_count >= min_riders:
            confidence = 0.65 + min(0.30, (rider_count - 2) * 0.15)
            return RuleCheckResult(
                triggered=True,
                violation_type=rule.violation_type,
                confidence=confidence,
                reason=f"Detected {rider_count} riders on {vehicle_type}",
                evidence={
                    "rider_count": rider_count,
                    "vehicle_type": vehicle_type
                }
            )
        
        return RuleCheckResult(
            triggered=False,
            violation_type=rule.violation_type,
            confidence=0,
            reason=f"Rider count {rider_count} is within limit"
        )
    
    def get_fine_amount(self, violation_type: str) -> int:
        """Get fine amount for a violation type."""
        rule = self.rules.get(violation_type)
        if rule:
            return rule.fine_amount
        return 500  # Default fine
    
    def get_rule(self, violation_type: str) -> Optional[ViolationRule]:
        """Get rule configuration for a violation type."""
        return self.rules.get(violation_type)
    
    def update_traffic_light_history(self, state: str):
        """Update traffic light state history."""
        self.traffic_light_history.append(state)
        if len(self.traffic_light_history) > 30:
            self.traffic_light_history.pop(0)
    
    def get_consistent_light_state(self, min_frames: int = 3) -> str:
        """Get consistent traffic light state from history."""
        if len(self.traffic_light_history) < min_frames:
            return "unknown"
        
        recent = self.traffic_light_history[-min_frames:]
        # Return state if consistent in recent frames
        if len(set(recent)) == 1:
            return recent[0]
        return "unknown"


# Singleton instance
rule_engine = TrafficRuleEngine()
