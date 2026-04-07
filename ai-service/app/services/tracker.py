"""
ByteTrack-style Vehicle Tracker for AI TrafficCam

Production-grade multi-object tracker for vehicle tracking across frames.
Uses IOU-based association with Kalman filter for motion prediction.

Key Features:
- Persistent vehicle IDs across frames
- Trajectory tracking for speed/direction estimation
- Handles occlusion and temporary disappearance
- Memory-efficient for long videos
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any
from collections import deque
from scipy.optimize import linear_sum_assignment
from filterpy.kalman import KalmanFilter

from app.core.logging import logger


@dataclass
class BoundingBox:
    """Bounding box representation."""
    x1: int
    y1: int
    x2: int
    y2: int
    
    @property
    def center(self) -> Tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)
    
    @property
    def width(self) -> int:
        return self.x2 - self.x1
    
    @property
    def height(self) -> int:
        return self.y2 - self.y1
    
    @property
    def area(self) -> int:
        return self.width * self.height
    
    def to_xyxy(self) -> Tuple[int, int, int, int]:
        return (self.x1, self.y1, self.x2, self.y2)
    
    def to_xywh(self) -> Tuple[float, float, float, float]:
        return (self.center[0], self.center[1], self.width, self.height)


@dataclass
class Detection:
    """Single detection from a frame."""
    bbox: BoundingBox
    confidence: float
    class_id: int
    class_name: str
    frame_number: int
    timestamp: float


@dataclass 
class TrackState:
    """State of a tracked object."""
    track_id: int
    bbox: BoundingBox
    confidence: float
    class_id: int
    class_name: str
    age: int = 0  # Frames since creation
    hits: int = 1  # Total successful matches
    time_since_update: int = 0  # Frames since last match
    
    # Trajectory data
    positions: List[Tuple[float, float]] = field(default_factory=list)
    timestamps: List[float] = field(default_factory=list)
    velocities: List[Tuple[float, float]] = field(default_factory=list)
    
    # Best detection for OCR/evidence
    best_detection_frame: int = 0
    best_detection_confidence: float = 0.0
    best_bbox: Optional[BoundingBox] = None


class KalmanBoxTracker:
    """
    Kalman filter for bounding box tracking.
    
    State: [x, y, s, r, vx, vy, vs]
    - (x, y): center position
    - s: scale (area)
    - r: aspect ratio (constant)
    - (vx, vy, vs): velocities
    """
    
    count = 0
    
    def __init__(self, bbox: BoundingBox, class_id: int, class_name: str, confidence: float):
        """Initialize tracker with bounding box."""
        # State: [x, y, s, r, vx, vy, vs]
        self.kf = KalmanFilter(dim_x=7, dim_z=4)
        
        # Transition matrix
        self.kf.F = np.array([
            [1, 0, 0, 0, 1, 0, 0],
            [0, 1, 0, 0, 0, 1, 0],
            [0, 0, 1, 0, 0, 0, 1],
            [0, 0, 0, 1, 0, 0, 0],
            [0, 0, 0, 0, 1, 0, 0],
            [0, 0, 0, 0, 0, 1, 0],
            [0, 0, 0, 0, 0, 0, 1]
        ])
        
        # Measurement matrix
        self.kf.H = np.array([
            [1, 0, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0, 0],
            [0, 0, 1, 0, 0, 0, 0],
            [0, 0, 0, 1, 0, 0, 0]
        ])
        
        # Measurement noise
        self.kf.R[2:, 2:] *= 10.0
        
        # Covariance matrix
        self.kf.P[4:, 4:] *= 1000.0
        self.kf.P *= 10.0
        
        # Process noise
        self.kf.Q[-1, -1] *= 0.01
        self.kf.Q[4:, 4:] *= 0.01
        
        # Initialize state
        x, y = bbox.center
        s = bbox.area
        r = bbox.width / float(bbox.height) if bbox.height > 0 else 1.0
        
        self.kf.x[:4] = np.array([[x], [y], [s], [r]])
        
        # Track metadata
        self.id = KalmanBoxTracker.count
        KalmanBoxTracker.count += 1
        
        self.class_id = class_id
        self.class_name = class_name
        self.confidence = confidence
        self.time_since_update = 0
        self.hits = 1
        self.age = 0
        
        # Trajectory
        self.positions = [(x, y)]
        self.timestamps = []
        self.velocities = []
        
        # Best detection
        self.best_bbox = bbox
        self.best_confidence = confidence
        self.best_frame = 0
        
    def predict(self) -> BoundingBox:
        """Predict next position."""
        if self.kf.x[6] + self.kf.x[2] <= 0:
            self.kf.x[6] = 0
            
        self.kf.predict()
        self.age += 1
        
        if self.time_since_update > 0:
            self.hits = 0
        self.time_since_update += 1
        
        return self._get_bbox()
    
    def update(self, detection: Detection, timestamp: float = 0.0):
        """Update tracker with matched detection."""
        bbox = detection.bbox
        x, y = bbox.center
        s = bbox.area
        r = bbox.width / float(bbox.height) if bbox.height > 0 else 1.0
        
        self.kf.update(np.array([[x], [y], [s], [r]]))
        
        self.time_since_update = 0
        self.hits += 1
        self.confidence = detection.confidence
        
        # Update trajectory
        self.positions.append((x, y))
        self.timestamps.append(timestamp)
        
        # Calculate velocity
        if len(self.positions) >= 2 and len(self.timestamps) >= 2:
            dt = self.timestamps[-1] - self.timestamps[-2]
            if dt > 0:
                vx = (self.positions[-1][0] - self.positions[-2][0]) / dt
                vy = (self.positions[-1][1] - self.positions[-2][1]) / dt
                self.velocities.append((vx, vy))
        
        # Track best detection
        if detection.confidence > self.best_confidence:
            self.best_confidence = detection.confidence
            self.best_bbox = bbox
            self.best_frame = detection.frame_number
        
        # Limit trajectory history (memory efficiency)
        max_history = 100
        if len(self.positions) > max_history:
            self.positions = self.positions[-max_history:]
            self.timestamps = self.timestamps[-max_history:]
            self.velocities = self.velocities[-max_history:]
    
    def _get_bbox(self) -> BoundingBox:
        """Convert state to bounding box."""
        x, y, s, r = self.kf.x[:4].flatten()
        
        w = np.sqrt(s * r)
        h = s / w if w > 0 else 0
        
        x1 = int(x - w / 2)
        y1 = int(y - h / 2)
        x2 = int(x + w / 2)
        y2 = int(y + h / 2)
        
        return BoundingBox(x1, y1, x2, y2)
    
    def get_state(self) -> TrackState:
        """Get current track state."""
        bbox = self._get_bbox()
        return TrackState(
            track_id=self.id,
            bbox=bbox,
            confidence=self.confidence,
            class_id=self.class_id,
            class_name=self.class_name,
            age=self.age,
            hits=self.hits,
            time_since_update=self.time_since_update,
            positions=list(self.positions),
            timestamps=list(self.timestamps),
            velocities=list(self.velocities),
            best_detection_frame=self.best_frame,
            best_detection_confidence=self.best_confidence,
            best_bbox=self.best_bbox
        )


def iou_batch(bb_det: np.ndarray, bb_trk: np.ndarray) -> np.ndarray:
    """
    Compute IOU between all detection-tracker pairs.
    
    Args:
        bb_det: (N, 4) array of detection boxes [x1, y1, x2, y2]
        bb_trk: (M, 4) array of tracker boxes [x1, y1, x2, y2]
        
    Returns:
        (N, M) IOU matrix
    """
    bb_det = np.expand_dims(bb_det, 1)  # (N, 1, 4)
    bb_trk = np.expand_dims(bb_trk, 0)  # (1, M, 4)
    
    # Intersection
    xx1 = np.maximum(bb_det[..., 0], bb_trk[..., 0])
    yy1 = np.maximum(bb_det[..., 1], bb_trk[..., 1])
    xx2 = np.minimum(bb_det[..., 2], bb_trk[..., 2])
    yy2 = np.minimum(bb_det[..., 3], bb_trk[..., 3])
    
    w = np.maximum(0.0, xx2 - xx1)
    h = np.maximum(0.0, yy2 - yy1)
    
    intersection = w * h
    
    # Areas
    area_det = (bb_det[..., 2] - bb_det[..., 0]) * (bb_det[..., 3] - bb_det[..., 1])
    area_trk = (bb_trk[..., 2] - bb_trk[..., 0]) * (bb_trk[..., 3] - bb_trk[..., 1])
    
    union = area_det + area_trk - intersection
    
    iou = intersection / np.maximum(union, 1e-7)
    
    return iou


class ByteTracker:
    """
    ByteTrack-style multi-object tracker.
    
    Key improvements over simple IOU tracking:
    - Two-stage association (high + low confidence)
    - Kalman filter prediction for motion
    - Handles temporary occlusion
    - Track lifecycle management
    """
    
    def __init__(
        self,
        track_thresh: float = 0.5,      # High confidence threshold
        track_buffer: int = 30,         # Frames to keep lost tracks
        match_thresh: float = 0.8,      # IOU threshold for matching
        low_thresh: float = 0.1,        # Low confidence threshold
        min_hits: int = 3               # Minimum hits before confirmed
    ):
        """
        Initialize tracker.
        
        Args:
            track_thresh: Confidence threshold for high-confidence detections
            track_buffer: Number of frames to keep lost tracks
            match_thresh: IOU threshold for matching
            low_thresh: Minimum confidence to consider detection
            min_hits: Minimum hits before track is confirmed
        """
        self.track_thresh = track_thresh
        self.track_buffer = track_buffer
        self.match_thresh = match_thresh
        self.low_thresh = low_thresh
        self.min_hits = min_hits
        
        self.trackers: List[KalmanBoxTracker] = []
        self.frame_count = 0
        
        # Reset Kalman counter
        KalmanBoxTracker.count = 0
    
    def update(
        self,
        detections: List[Detection],
        frame_number: int,
        timestamp: float = 0.0
    ) -> List[TrackState]:
        """
        Update tracker with new detections.
        
        Args:
            detections: List of detections from current frame
            frame_number: Current frame number
            timestamp: Current timestamp in seconds
            
        Returns:
            List of confirmed track states
        """
        self.frame_count = frame_number
        
        # Predict new locations of existing trackers
        for trk in self.trackers:
            trk.predict()
        
        # Split detections by confidence
        high_dets = [d for d in detections if d.confidence >= self.track_thresh]
        low_dets = [d for d in detections if self.low_thresh <= d.confidence < self.track_thresh]
        
        # Get tracker predictions
        if len(self.trackers) > 0:
            trk_boxes = np.array([trk._get_bbox().to_xyxy() for trk in self.trackers])
        else:
            trk_boxes = np.empty((0, 4))
        
        # Stage 1: Match high confidence detections to trackers
        unmatched_dets = []
        unmatched_trks = list(range(len(self.trackers)))
        
        if len(high_dets) > 0 and len(self.trackers) > 0:
            det_boxes = np.array([d.bbox.to_xyxy() for d in high_dets])
            iou_matrix = iou_batch(det_boxes, trk_boxes)
            
            # Hungarian algorithm
            matched_indices = self._linear_assignment(-iou_matrix)
            
            for d_idx, t_idx in matched_indices:
                if iou_matrix[d_idx, t_idx] >= self.match_thresh:
                    self.trackers[t_idx].update(high_dets[d_idx], timestamp)
                    if t_idx in unmatched_trks:
                        unmatched_trks.remove(t_idx)
                else:
                    unmatched_dets.append(high_dets[d_idx])
            
            # Unmatched detections
            matched_det_indices = set(m[0] for m in matched_indices if iou_matrix[m[0], m[1]] >= self.match_thresh)
            unmatched_dets.extend([high_dets[i] for i in range(len(high_dets)) if i not in matched_det_indices])
        else:
            unmatched_dets = list(high_dets)
        
        # Stage 2: Match low confidence detections to remaining trackers
        if len(low_dets) > 0 and len(unmatched_trks) > 0:
            remaining_trk_boxes = np.array([self.trackers[i]._get_bbox().to_xyxy() for i in unmatched_trks])
            low_det_boxes = np.array([d.bbox.to_xyxy() for d in low_dets])
            
            iou_matrix = iou_batch(low_det_boxes, remaining_trk_boxes)
            
            matched_indices = self._linear_assignment(-iou_matrix)
            
            for d_idx, t_idx in matched_indices:
                if iou_matrix[d_idx, t_idx] >= 0.5:  # Lower threshold for low conf
                    self.trackers[unmatched_trks[t_idx]].update(low_dets[d_idx], timestamp)
                    unmatched_trks[t_idx] = -1  # Mark as matched
            
            unmatched_trks = [t for t in unmatched_trks if t >= 0]
        
        # Create new trackers for unmatched high-confidence detections
        for det in unmatched_dets:
            if det.confidence >= self.track_thresh:
                trk = KalmanBoxTracker(
                    det.bbox,
                    det.class_id,
                    det.class_name,
                    det.confidence
                )
                trk.timestamps.append(timestamp)
                trk.best_frame = det.frame_number
                self.trackers.append(trk)
        
        # Remove dead trackers
        self.trackers = [
            trk for trk in self.trackers
            if trk.time_since_update <= self.track_buffer
        ]
        
        # Return confirmed tracks
        confirmed_tracks = [
            trk.get_state() 
            for trk in self.trackers
            if trk.hits >= self.min_hits and trk.time_since_update == 0
        ]
        
        return confirmed_tracks
    
    def _linear_assignment(self, cost_matrix: np.ndarray) -> List[Tuple[int, int]]:
        """Solve linear assignment problem."""
        if cost_matrix.size == 0:
            return []
        
        row_indices, col_indices = linear_sum_assignment(cost_matrix)
        return list(zip(row_indices, col_indices))
    
    def get_all_tracks(self) -> List[TrackState]:
        """Get all current tracks (including unconfirmed)."""
        return [trk.get_state() for trk in self.trackers]
    
    def reset(self):
        """Reset tracker state."""
        self.trackers = []
        self.frame_count = 0
        KalmanBoxTracker.count = 0


# Module-level tracker instance
vehicle_tracker = ByteTracker(
    track_thresh=0.5,
    track_buffer=30,
    match_thresh=0.7,
    low_thresh=0.1,
    min_hits=2
)


def estimate_speed(
    positions: List[Tuple[float, float]],
    timestamps: List[float],
    pixels_per_meter: float = 10.0
) -> Optional[float]:
    """
    Estimate vehicle speed from trajectory.
    
    Args:
        positions: List of (x, y) center positions
        timestamps: List of timestamps in seconds
        pixels_per_meter: Calibration factor
        
    Returns:
        Estimated speed in km/h or None if insufficient data
    """
    if len(positions) < 3 or len(timestamps) < 3:
        return None
    
    # Use recent positions
    positions = positions[-10:]
    timestamps = timestamps[-10:]
    
    # Calculate distance
    total_distance = 0.0
    for i in range(1, len(positions)):
        dx = positions[i][0] - positions[i-1][0]
        dy = positions[i][1] - positions[i-1][1]
        total_distance += np.sqrt(dx**2 + dy**2)
    
    # Time elapsed
    time_elapsed = timestamps[-1] - timestamps[0]
    if time_elapsed <= 0:
        return None
    
    # Speed in pixels per second
    speed_pps = total_distance / time_elapsed
    
    # Convert to km/h
    speed_mps = speed_pps / pixels_per_meter
    speed_kmh = speed_mps * 3.6
    
    # Sanity check (0-200 km/h)
    return min(max(speed_kmh, 0), 200)


def get_movement_direction(
    positions: List[Tuple[float, float]]
) -> Optional[str]:
    """
    Determine vehicle movement direction.
    
    Returns:
        'forward', 'backward', 'left', 'right', or None
    """
    if len(positions) < 3:
        return None
    
    # Net displacement
    dx = positions[-1][0] - positions[0][0]
    dy = positions[-1][1] - positions[0][1]
    
    # Determine primary direction
    if abs(dx) > abs(dy):
        return 'right' if dx > 0 else 'left'
    else:
        return 'backward' if dy > 0 else 'forward'  # Y increases downward
