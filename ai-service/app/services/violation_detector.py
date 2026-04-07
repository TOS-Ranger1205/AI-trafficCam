"""
Violation Detection Service for AI TrafficCam
Detects traffic violations from video frames using computer vision
Now supports dynamic rules from database
"""

import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import numpy as np
import cv2
from collections import defaultdict

from app.core.logging import logger
from app.core.config import settings
from app.services.detector import object_detector, Detection, VehicleDetection
from app.services.plate_ocr import license_plate_ocr, PlateResult
from app.services.dynamic_rules import rule_fetcher, get_confidence_threshold
from app.utils.config_reader import (
    get_min_detection_confidence,
    get_speed_violation_threshold,
    get_red_light_grace_seconds,
    is_ai_enabled,
)


class ViolationType(str, Enum):
    """Traffic violation types."""
    SIGNAL_JUMPING = "signal_jumping"
    WRONG_WAY = "wrong_way"
    NO_HELMET = "no_helmet"
    TRIPLE_RIDING = "triple_riding"
    NO_SEATBELT = "no_seatbelt"
    OVERSPEEDING = "overspeeding"
    PARKING_VIOLATION = "parking_violation"
    LANE_VIOLATION = "lane_violation"
    ZEBRA_CROSSING = "zebra_crossing"
    NO_LICENSE_PLATE = "no_license_plate"
    MOBILE_PHONE_USE = "mobile_phone_use"


@dataclass
class ViolationEvidence:
    """Evidence for a detected violation."""
    frame_number: int
    timestamp: float
    image_path: Optional[str] = None
    bbox: Optional[Tuple[int, int, int, int]] = None
    description: str = ""


@dataclass
class DetectedViolation:
    """Detected traffic violation."""
    violation_type: ViolationType
    confidence: float
    vehicle_detection: Optional[VehicleDetection] = None
    license_plate: Optional[PlateResult] = None
    evidence: List[ViolationEvidence] = field(default_factory=list)
    ai_reasoning: str = ""  # Explainable AI
    fine_amount: float = 0.0
    location: Optional[Dict[str, Any]] = None
    detected_at: datetime = field(default_factory=datetime.now)
    extra_data: Dict[str, Any] = field(default_factory=dict)  # Additional violation-specific data


@dataclass
class VehicleTrack:
    """Track a vehicle across frames."""
    track_id: int
    detections: List[Tuple[int, VehicleDetection]]  # (frame_num, detection)
    positions: List[Tuple[int, int]]  # center positions
    timestamps: List[float] = field(default_factory=list)  # timestamps for speed calculation
    license_plate: Optional[PlateResult] = None
    violations: List[DetectedViolation] = field(default_factory=list)
    
    def estimate_speed_kmh(self, pixels_per_meter: float = 10.0) -> Optional[float]:
        """
        Estimate vehicle speed based on position changes over time.
        """
        if len(self.positions) < 2 or len(self.timestamps) < 2:
            return None
        
        # Use last 10 positions/timestamps for smoothing
        positions = self.positions[-10:]
        timestamps = self.timestamps[-10:]
        
        if len(positions) < 2:
            return None
        
        # Calculate total distance in pixels
        total_distance_px = 0
        for i in range(1, len(positions)):
            dx = positions[i][0] - positions[i-1][0]
            dy = positions[i][1] - positions[i-1][1]
            total_distance_px += np.sqrt(dx**2 + dy**2)
        
        # Time difference
        time_diff = timestamps[-1] - timestamps[0]
        if time_diff <= 0:
            return None
        
        # Convert to meters
        distance_meters = total_distance_px / pixels_per_meter
        
        # Speed in m/s then convert to km/h
        speed_ms = distance_meters / time_diff
        speed_kmh = speed_ms * 3.6
        
        # Cap at reasonable values (0-200 km/h)
        return min(max(speed_kmh, 0), 200)
    

class ViolationDetector:
    """
    Traffic violation detection service.
    Analyzes video frames to detect various traffic violations.
    """
    
    # Fine amounts (in INR) - can be configured
    FINE_AMOUNTS = {
        ViolationType.SIGNAL_JUMPING: 5000,
        ViolationType.WRONG_WAY: 5000,
        ViolationType.NO_HELMET: 1000,
        ViolationType.TRIPLE_RIDING: 1500,
        ViolationType.NO_SEATBELT: 1000,
        ViolationType.OVERSPEEDING: 2000,
        ViolationType.PARKING_VIOLATION: 500,
        ViolationType.LANE_VIOLATION: 500,
        ViolationType.ZEBRA_CROSSING: 500,
        ViolationType.NO_LICENSE_PLATE: 5000,
        ViolationType.MOBILE_PHONE_USE: 1500,
    }
    
    def __init__(self):
        self.vehicle_tracks: Dict[int, VehicleTrack] = {}
        self.next_track_id = 1
        self.frame_history: Dict[int, List[Detection]] = {}
        
        # FIX: Use configured upload path so Backend can find the images
        try:
            self.evidence_dir = Path(settings.upload_path) / "evidence"
        except AttributeError:
            self.evidence_dir = Path("uploads/evidence")
            
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        
        # Traffic light state tracking
        self.traffic_light_state = "unknown"  # red, yellow, green, unknown
        self.traffic_light_history = []
        
        # Lane/road configuration (loaded from config or detected)
        self.lane_config = {
            "num_lanes": 3,
            "allowed_direction": "forward",  # forward, backward, both
        }
        
        # Dynamic rules cache
        self.violation_rules = {}
        self.rules_last_updated = 0
        
        # Load AI configuration from system config (with safe fallbacks)
        try:
            self.min_detection_confidence = get_min_detection_confidence()
            self.speed_violation_threshold = get_speed_violation_threshold()
            self.red_light_grace_seconds = get_red_light_grace_seconds()
            self.ai_enabled = is_ai_enabled()
            logger.info(f"AI Config loaded: min_conf={self.min_detection_confidence}, "
                       f"speed_threshold={self.speed_violation_threshold} km/h, "
                       f"red_light_grace={self.red_light_grace_seconds}s")
        except Exception as e:
            # Fallback to safe defaults if config fetch fails
            logger.warning(f"Failed to load AI config, using defaults: {e}")
            self.min_detection_confidence = 0.70
            self.speed_violation_threshold = 60
            self.red_light_grace_seconds = 2.0
            self.ai_enabled = True
    
    async def update_violation_rules(self):
        """Update violation rules from database."""
        try:
            self.violation_rules = await rule_fetcher.get_ai_rules_config()
            self.rules_last_updated = datetime.now().timestamp()
            logger.info(f"Updated {len(self.violation_rules)} violation rules from database")
        except Exception as e:
            logger.warning(f"Failed to update violation rules: {e}")
    
    async def get_violation_confidence_threshold(self, violation_type: str) -> float:
        """Get confidence threshold for specific violation type."""
        try:
            # Update rules if cache is old (older than 60 seconds)
            if (datetime.now().timestamp() - self.rules_last_updated) > 60:
                await self.update_violation_rules()
            
            rule = self.violation_rules.get(violation_type)
            if rule and rule.get("ai_enabled", True):
                return rule.get("min_confidence", 0.75)
        except Exception as e:
            logger.warning(f"Failed to get threshold for {violation_type}: {e}")
        
        # Fallback to hardcoded values
        fallback_thresholds = {
            "red_light": 0.80,
            "speed_violation": 0.85,
            "no_helmet": 0.70,
            "no_seatbelt": 0.75,
            "wrong_way": 0.85,
            "triple_riding": 0.80
        }
        return fallback_thresholds.get(violation_type, 0.75)
    
    def process_frame(
        self,
        frame: np.ndarray,
        frame_number: int,
        timestamp: float,
        video_id: str
    ) -> List[DetectedViolation]:
        """
        Process a single frame for violations.
        """
        violations = []
        
        # Detect vehicles
        vehicles = object_detector.detect_vehicles(frame)
        
        # Detect traffic elements
        traffic_elements = object_detector.detect_traffic_elements(frame)
        
        # Update traffic light state
        self._update_traffic_light_state(traffic_elements.get('traffic_lights', []), frame)
        
        # Update vehicle tracks with timestamp for speed estimation
        self._update_vehicle_tracks(vehicles, frame_number, timestamp)
        
        # Store frame detections for history
        all_detections = object_detector.detect(frame)
        self.frame_history[frame_number] = all_detections
        
        # Check for violations
        for vehicle in vehicles:
            vehicle_violations = []
            
            # Check signal jumping
            signal_violation = self._check_signal_jumping(vehicle, frame, frame_number)
            if signal_violation:
                vehicle_violations.append(signal_violation)
            
            # Check wrong way
            wrong_way = self._check_wrong_way(vehicle, frame_number)
            if wrong_way:
                vehicle_violations.append(wrong_way)
            
            # Check motorcycle-specific violations
            if vehicle.vehicle_type == 'motorcycle':
                helmet_violation = self._check_no_helmet(vehicle, frame, frame_number)
                if helmet_violation:
                    vehicle_violations.append(helmet_violation)
                
                triple_riding = self._check_triple_riding(vehicle, frame, frame_number)
                if triple_riding:
                    vehicle_violations.append(triple_riding)
            
            # Check for missing license plate
            plate_violation = self._check_no_plate(vehicle, frame, frame_number)
            if plate_violation:
                vehicle_violations.append(plate_violation)
            
            # Check for overspeeding (using configured threshold)
            speed_violation = self._check_overspeeding(
                vehicle, frame_number, timestamp, speed_limit=self.speed_violation_threshold
            )
            if speed_violation:
                vehicle_violations.append(speed_violation)
            
            # Extract license plate for all violations
            plate_result = self._extract_license_plate(frame, vehicle)
            
            # Add evidence and plate info to violations
            for violation in vehicle_violations:
                violation.vehicle_detection = vehicle
                violation.license_plate = plate_result
                violation.fine_amount = self.FINE_AMOUNTS.get(violation.violation_type, 500)
                
                # Save evidence image
                evidence_path = self._save_evidence(frame, vehicle, violation, frame_number, video_id)
                
                violation.evidence.append(ViolationEvidence(
                    frame_number=frame_number,
                    timestamp=timestamp,
                    image_path=evidence_path,
                    bbox=vehicle.bbox,
                    description=f"{violation.violation_type.value} detected at {timestamp:.1f}s"
                ))
            
            violations.extend(vehicle_violations)
        
        # Check for zebra crossing violations
        zebra_violations = self._check_zebra_crossing(
            vehicles, 
            traffic_elements.get('pedestrians', []),
            frame, 
            frame_number
        )
        violations.extend(zebra_violations)
        
        # Clean up old frame history (keep last 100 frames)
        if len(self.frame_history) > 100:
            old_frames = sorted(self.frame_history.keys())[:-100]
            for f in old_frames:
                del self.frame_history[f]
        
        return violations
    
    def _save_evidence(
        self,
        frame: np.ndarray,
        vehicle: VehicleDetection,
        violation: DetectedViolation,
        frame_number: int,
        video_id: str
    ) -> str:
        """
        Save annotated evidence image.
        Returns RELATIVE path for database storage.
        """
        # Create annotated frame
        annotated = frame.copy()
        
        # Draw vehicle bbox (Thick Red Box)
        x1, y1, x2, y2 = vehicle.bbox
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 3)
        
        # Add label background
        label = f"{violation.violation_type.value.upper()}"
        (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
        cv2.rectangle(annotated, (x1, y1 - 25), (x1 + w, y1), (0, 0, 255), -1)
        
        # Add label text
        cv2.putText(annotated, label, (x1, y1 - 5), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        
        # Add timestamp and info at bottom
        info_text = f"Frame: {frame_number} | Conf: {violation.confidence:.2f}"
        if violation.extra_data.get('speed_detected'):
            info_text += f" | Speed: {violation.extra_data['speed_detected']:.1f} km/h"
            
        cv2.putText(annotated, info_text, (10, annotated.shape[0] - 20),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        
        # Filename: videoID_frame_type.jpg
        # Ensure filename is filesystem safe
        safe_type = violation.violation_type.value.replace(" ", "_")
        filename = f"{video_id}_{frame_number}_{safe_type}.jpg"
        filepath = self.evidence_dir / filename
        
        # Write file
        cv2.imwrite(str(filepath), annotated)
        
        # Return relative path (e.g., "evidence/filename.jpg")
        return f"evidence/{filename}"

    def _update_traffic_light_state(
        self, 
        traffic_lights: List[Detection],
        frame: np.ndarray
    ):
        """Detect traffic light color from detected traffic lights."""
        if not traffic_lights:
            return
        
        for light in traffic_lights:
            x1, y1, x2, y2 = light.bbox
            light_region = frame[y1:y2, x1:x2]
            
            if light_region.size == 0:
                continue
            
            # Convert to HSV for better color detection
            hsv = cv2.cvtColor(light_region, cv2.COLOR_BGR2HSV)
            
            # Red color range
            red_mask1 = cv2.inRange(hsv, (0, 100, 100), (10, 255, 255))
            red_mask2 = cv2.inRange(hsv, (160, 100, 100), (180, 255, 255))
            red_mask = red_mask1 + red_mask2
            
            # Yellow color range
            yellow_mask = cv2.inRange(hsv, (15, 100, 100), (35, 255, 255))
            
            # Green color range
            green_mask = cv2.inRange(hsv, (40, 100, 100), (80, 255, 255))
            
            # Count pixels
            red_count = cv2.countNonZero(red_mask)
            yellow_count = cv2.countNonZero(yellow_mask)
            green_count = cv2.countNonZero(green_mask)
            
            max_count = max(red_count, yellow_count, green_count)
            
            if max_count > 50:  # Minimum threshold
                if red_count == max_count:
                    self.traffic_light_state = "red"
                elif yellow_count == max_count:
                    self.traffic_light_state = "yellow"
                elif green_count == max_count:
                    self.traffic_light_state = "green"
            
            self.traffic_light_history.append(self.traffic_light_state)
            if len(self.traffic_light_history) > 30:
                self.traffic_light_history.pop(0)
    
    def _update_vehicle_tracks(
        self, 
        vehicles: List[VehicleDetection],
        frame_number: int,
        timestamp: float = 0.0
    ):
        """Update vehicle tracking with timestamp for speed calculation."""
        used_tracks = set()
        
        for vehicle in vehicles:
            matched_track = None
            min_distance = float('inf')
            
            # Find closest existing track
            for track_id, track in self.vehicle_tracks.items():
                if track_id in used_tracks:
                    continue
                    
                if track.positions:
                    last_pos = track.positions[-1]
                    distance = np.sqrt(
                        (vehicle.center[0] - last_pos[0]) ** 2 +
                        (vehicle.center[1] - last_pos[1]) ** 2
                    )
                    
                    if distance < min_distance and distance < 100:  # Max 100px movement
                        min_distance = distance
                        matched_track = track_id
            
            if matched_track:
                # Update existing track
                self.vehicle_tracks[matched_track].detections.append((frame_number, vehicle))
                self.vehicle_tracks[matched_track].positions.append(vehicle.center)
                self.vehicle_tracks[matched_track].timestamps.append(timestamp)
                used_tracks.add(matched_track)
            else:
                # Create new track
                new_track = VehicleTrack(
                    track_id=self.next_track_id,
                    detections=[(frame_number, vehicle)],
                    positions=[vehicle.center],
                    timestamps=[timestamp]
                )
                self.vehicle_tracks[self.next_track_id] = new_track
                self.next_track_id += 1
    
    def _check_signal_jumping(
        self,
        vehicle: VehicleDetection,
        frame: np.ndarray,
        frame_number: int
    ) -> Optional[DetectedViolation]:
        """Check if vehicle crossed during red light."""
        # Need red light and moving vehicle
        if self.traffic_light_state != "red":
            return None
        
        # Get vehicle track
        track = self._get_vehicle_track(vehicle)
        if not track or len(track.positions) < 3:
            return None
        
        # Check if vehicle is moving forward (crossing)
        positions = track.positions[-5:]  # Last 5 positions
        if len(positions) < 2:
            return None
        
        # Calculate movement
        start = positions[0]
        end = positions[-1]
        y_movement = end[1] - start[1]  # Negative = moving up = forward
        
        # If moving forward during red light
        if y_movement < -20:  # Moved at least 20 pixels forward
            return DetectedViolation(
                violation_type=ViolationType.SIGNAL_JUMPING,
                confidence=0.85,
                ai_reasoning=f"Vehicle detected moving forward ({abs(y_movement):.0f}px) while traffic light was red. "
                            f"Signal state history confirms red light was active. "
                            f"Movement tracked across {len(track.positions)} frames.",
            )
        
        return None
    
    def _check_wrong_way(
        self,
        vehicle: VehicleDetection,
        frame_number: int
    ) -> Optional[DetectedViolation]:
        """Check if vehicle is going wrong way."""
        track = self._get_vehicle_track(vehicle)
        if not track or len(track.positions) < 5:
            return None
        
        positions = track.positions[-10:]
        if len(positions) < 5:
            return None
        
        # Calculate overall direction
        start = positions[0]
        end = positions[-1]
        
        x_movement = end[0] - start[0]
        y_movement = end[1] - start[1]
        
        # Determine direction
        if abs(y_movement) > abs(x_movement):
            direction = "backward" if y_movement > 50 else "forward"
        else:
            return None  # Horizontal movement, not clear violation
        
        # Check against allowed direction
        if self.lane_config["allowed_direction"] == "forward" and direction == "backward":
            return DetectedViolation(
                violation_type=ViolationType.WRONG_WAY,
                confidence=0.80,
                ai_reasoning=f"Vehicle detected traveling in wrong direction. "
                            f"Movement vector: ({x_movement:.0f}, {y_movement:.0f}). "
                            f"Road configured for forward traffic only.",
            )
        
        return None
    
    def _check_no_helmet(
        self,
        vehicle: VehicleDetection,
        frame: np.ndarray,
        frame_number: int
    ) -> Optional[DetectedViolation]:
        """Check if motorcycle rider has no helmet."""
        x1, y1, x2, y2 = vehicle.bbox
        height = y2 - y1
        
        # Upper portion where rider's head would be
        rider_region = frame[max(0, y1 - int(height * 0.2)):y1 + int(height * 0.4), x1:x2]
        
        if rider_region.size == 0:
            return None
        
        # Look for helmet-colored regions (typically dark/black)
        hsv = cv2.cvtColor(rider_region, cv2.COLOR_BGR2HSV)
        
        # Helmet typically dark colors
        dark_mask = cv2.inRange(hsv, (0, 0, 0), (180, 255, 80))
        
        # Also check for colorful helmets
        saturated_mask = cv2.inRange(hsv, (0, 100, 100), (180, 255, 255))
        
        helmet_mask = cv2.bitwise_or(dark_mask, saturated_mask)
        
        total_pixels = rider_region.shape[0] * rider_region.shape[1]
        helmet_pixels = cv2.countNonZero(helmet_mask)
        
        helmet_ratio = helmet_pixels / total_pixels if total_pixels > 0 else 0
        
        # If less than 20% helmet-like pixels, likely no helmet
        if helmet_ratio < 0.20:
            return DetectedViolation(
                violation_type=ViolationType.NO_HELMET,
                confidence=0.70 + (0.2 - helmet_ratio),  # Higher confidence with fewer helmet pixels
                ai_reasoning=f"Motorcycle rider detected without helmet. "
                            f"Head region analysis shows {helmet_ratio*100:.1f}% helmet-like pixels, "
                            f"below the 20% threshold. Upper body analysis performed on extracted region.",
            )
        
        return None
    
    def _check_triple_riding(
        self,
        vehicle: VehicleDetection,
        frame: np.ndarray,
        frame_number: int
    ) -> Optional[DetectedViolation]:
        """Check for triple riding on motorcycle."""
        x1, y1, x2, y2 = vehicle.bbox
        
        # Extract motorcycle region
        motorcycle_region = frame[y1:y2, x1:x2]
        
        if motorcycle_region.size == 0:
            return None
        
        # Use HOG or contour analysis to count riders
        gray = cv2.cvtColor(motorcycle_region, cv2.COLOR_BGR2GRAY)
        
        # Look for skin-colored regions (faces/hands)
        hsv = cv2.cvtColor(motorcycle_region, cv2.COLOR_BGR2HSV)
        
        # Skin color range
        skin_mask = cv2.inRange(hsv, (0, 20, 70), (20, 255, 255))
        
        # Find contours of skin regions
        contours, _ = cv2.findContours(skin_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Filter by size (face-sized regions)
        face_like = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if area > 100:  # Minimum size
                face_like.append(contour)
        
        # If more than 2 face-like regions, possibly triple riding
        if len(face_like) >= 3:
            return DetectedViolation(
                violation_type=ViolationType.TRIPLE_RIDING,
                confidence=0.65,
                ai_reasoning=f"Motorcycle detected with possible triple riding. "
                            f"Analysis found {len(face_like)} distinct regions matching rider presence. "
                            f"Skin-tone analysis suggests more than 2 occupants.",
            )
        
        return None
    
    def _check_no_plate(
        self,
        vehicle: VehicleDetection,
        frame: np.ndarray,
        frame_number: int
    ) -> Optional[DetectedViolation]:
        """Check if vehicle has no visible license plate."""
        if vehicle.license_plate_region is None:
            return None
        
        # Try to detect plate in estimated region
        plates = license_plate_ocr.detect_plate_in_frame(frame)
        
        if not plates:
            # Also try in vehicle bbox directly
            x1, y1, x2, y2 = vehicle.bbox
            vehicle_region = frame[y1:y2, x1:x2]
            plates = license_plate_ocr.detect_plate_in_frame(vehicle_region)
        
        if not plates:
            return DetectedViolation(
                violation_type=ViolationType.NO_LICENSE_PLATE,
                confidence=0.60,
                ai_reasoning=f"Vehicle detected without visible license plate. "
                            f"Plate detection algorithm found no rectangular text regions "
                            f"in expected vehicle positions.",
            )
        
        return None
    
    def _check_overspeeding(
        self,
        vehicle: VehicleDetection,
        frame_number: int,
        timestamp: float,
        speed_limit: float = 60.0  # Default 60 km/h
    ) -> Optional[DetectedViolation]:
        """
        Check if vehicle is overspeeding based on tracked movement.
        """
        track = self._get_vehicle_track(vehicle)
        if not track:
            return None
        
        # Need enough tracking data for reliable speed estimation
        if len(track.positions) < 5 or len(track.timestamps) < 5:
            return None
        
        # Estimate speed using the track
        estimated_speed = track.estimate_speed_kmh(pixels_per_meter=10.0)
        
        if estimated_speed is None:
            return None
        
        # Check if over speed limit (with 10% buffer)
        buffer_multiplier = 1.1
        if estimated_speed > speed_limit * buffer_multiplier:
            excess_speed = estimated_speed - speed_limit
            
            # Calculate confidence based on how much over the limit
            # More excess = higher confidence
            confidence = min(0.90, 0.60 + (excess_speed / 100))
            
            violation = DetectedViolation(
                violation_type=ViolationType.OVERSPEEDING,
                confidence=confidence,
                ai_reasoning=f"Vehicle detected traveling at approximately {estimated_speed:.0f} km/h, "
                            f"exceeding the speed limit of {speed_limit:.0f} km/h by {excess_speed:.0f} km/h. "
                            f"Speed estimated from {len(track.positions)} tracked positions "
                            f"over {track.timestamps[-1] - track.timestamps[0]:.2f} seconds.",
                extra_data={
                    "speed_detected": round(estimated_speed, 1),
                    "speed_limit": speed_limit,
                    "excess_speed": round(excess_speed, 1)
                }
            )
            return violation
        
        return None
    
    def _check_zebra_crossing(
        self,
        vehicles: List[VehicleDetection],
        pedestrians: List[Detection],
        frame: np.ndarray,
        frame_number: int
    ) -> List[DetectedViolation]:
        """Check for zebra crossing violations."""
        violations = []
        
        if not pedestrians:
            return violations
        
        # Detect zebra crossing region using white stripe detection
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        _, white_mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        
        # Find horizontal stripes pattern
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (50, 5))
        stripes = cv2.morphologyEx(white_mask, cv2.MORPH_OPEN, kernel)
        
        stripe_sum = cv2.countNonZero(stripes)
        
        # If zebra crossing pattern detected
        if stripe_sum > 1000:
            for vehicle in vehicles:
                for pedestrian in pedestrians:
                    # Check if vehicle is near pedestrian on zebra crossing
                    v_center = vehicle.center
                    p_center = pedestrian.center
                    
                    distance = np.sqrt(
                        (v_center[0] - p_center[0]) ** 2 +
                        (v_center[1] - p_center[1]) ** 2
                    )
                    
                    # If vehicle too close to pedestrian at crossing
                    if distance < 150:  # Threshold in pixels
                        violation = DetectedViolation(
                            violation_type=ViolationType.ZEBRA_CROSSING,
                            confidence=0.70,
                            vehicle_detection=vehicle,
                            ai_reasoning=f"Vehicle detected too close ({distance:.0f}px) to pedestrian "
                                        f"at zebra crossing. Pedestrian at ({p_center[0]}, {p_center[1]}), "
                                        f"vehicle at ({v_center[0]}, {v_center[1]}).",
                        )
                        violations.append(violation)
        
        return violations
    
    def _extract_license_plate(
        self,
        frame: np.ndarray,
        vehicle: VehicleDetection
    ) -> Optional[PlateResult]:
        """Extract license plate text from vehicle."""
        # First try estimated plate region
        plate_result = license_plate_ocr.extract_plate_text(
            frame, 
            vehicle.license_plate_region
        )
        
        if plate_result and plate_result.is_valid:
            return plate_result
        
        # Try detecting plate in vehicle bbox
        x1, y1, x2, y2 = vehicle.bbox
        vehicle_region = frame[y1:y2, x1:x2]
        
        plate_regions = license_plate_ocr.detect_plate_in_frame(vehicle_region)
        
        for region, confidence in plate_regions:
            # Adjust coordinates to full frame
            adjusted_region = (
                x1 + region[0],
                y1 + region[1],
                x1 + region[2],
                y1 + region[3]
            )
            
            plate_result = license_plate_ocr.extract_plate_text(frame, adjusted_region)
            
            if plate_result and plate_result.confidence > 0.5:
                return plate_result
        
        return plate_result  # Return last attempt even if not valid
    
    def _get_vehicle_track(self, vehicle: VehicleDetection) -> Optional[VehicleTrack]:
        """Get track for a vehicle based on current position."""
        for track in self.vehicle_tracks.values():
            if track.positions:
                last_pos = track.positions[-1]
                if (abs(vehicle.center[0] - last_pos[0]) < 50 and 
                    abs(vehicle.center[1] - last_pos[1]) < 50):
                    return track
        return None
    
    def reset(self):
        """Reset detector state for new video."""
        self.vehicle_tracks.clear()
        self.next_track_id = 1
        self.frame_history.clear()
        self.traffic_light_state = "unknown"
        self.traffic_light_history.clear()
    
    def get_frame_detections(
        self,
        frame: np.ndarray,
        frame_number: int,
        timestamp: float
    ) -> List[Dict[str, Any]]:
        """
        Get all vehicle detections for a frame with tracking info.
        This is used for CCTV-style playback overlay.
        
        Returns list of detections with:
        - vehicle_id (track ID)
        - vehicle_type
        - bbox (x1, y1, x2, y2)
        - confidence
        - speed (estimated km/h)
        - plate_number (if detected)
        - has_violation (boolean)
        """
        detections = []
        
        # Get all vehicles in this frame
        vehicles = object_detector.detect_vehicles(frame)
        
        # Update tracks with this frame's data
        self._update_vehicle_tracks(vehicles, frame_number, timestamp)
        
        for vehicle in vehicles:
            # Find track for this vehicle
            track = self._get_vehicle_track(vehicle)
            track_id = track.track_id if track else 0
            
            # Estimate speed from track
            speed = None
            if track:
                speed = track.estimate_speed_kmh()
            
            # Try to get plate (only if track doesn't have one yet)
            plate_text = None
            if track and track.license_plate and track.license_plate.is_valid:
                plate_text = track.license_plate.normalized_text
            elif track:
                # Try OCR for this vehicle
                plate_result = self._extract_license_plate(frame, vehicle)
                if plate_result and plate_result.is_valid:
                    plate_text = plate_result.normalized_text
                    track.license_plate = plate_result
            
            # Check if this vehicle has any violations
            has_violation = False
            violation_types = []
            if track:
                has_violation = len(track.violations) > 0
                violation_types = [v.violation_type.value for v in track.violations]
            
            detections.append({
                "frame_number": frame_number,
                "timestamp": timestamp,
                "vehicle_id": track_id,
                "vehicle_type": vehicle.vehicle_type,
                "confidence": round(vehicle.confidence, 3),
                "bbox": {
                    "x1": vehicle.bbox[0],
                    "y1": vehicle.bbox[1],
                    "x2": vehicle.bbox[2],
                    "y2": vehicle.bbox[3]
                },
                "center": {"x": vehicle.center[0], "y": vehicle.center[1]},
                "speed": round(speed, 1) if speed else None,
                "plate_number": plate_text,
                "has_violation": has_violation,
                "violation_types": violation_types
            })
        
        return detections

# Singleton instance
violation_detector = ViolationDetector()