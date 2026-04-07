"""
Object Detection Service for AI TrafficCam
Uses YOLO for vehicle and traffic element detection
"""

import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import numpy as np
from cachetools import TTLCache

from app.core.logging import logger
from app.core.config import settings
from app.utils.config_reader import get_min_detection_confidence

# Try to import ultralytics for YOLO
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    logger.warning("ultralytics not installed. Using mock detection.")


@dataclass
class Detection:
    """Detection result container."""
    class_id: int
    class_name: str
    confidence: float
    bbox: Tuple[int, int, int, int]  # x1, y1, x2, y2
    center: Tuple[int, int]
    area: int


@dataclass
class VehicleDetection(Detection):
    """Vehicle-specific detection."""
    vehicle_type: str
    license_plate_region: Optional[Tuple[int, int, int, int]] = None


class ObjectDetector:
    """
    Object detection service using YOLO.
    Detects vehicles, traffic signals, pedestrians, etc.
    """
    
    # COCO class mappings for vehicles
    VEHICLE_CLASSES = {
        2: 'car',
        3: 'motorcycle',
        5: 'bus',
        7: 'truck',
        1: 'bicycle'
    }
    
    # Traffic-related classes
    TRAFFIC_CLASSES = {
        0: 'person',
        9: 'traffic_light',
        11: 'stop_sign'
    }
    
    def __init__(self):
        self.model = None
        
        # Safe path handling
        try:
            self.model_path = Path(settings.model_path)
        except AttributeError:
            self.model_path = Path("models")
            
        # Load min detection confidence from system config (with fallback)
        try:
            self.confidence_threshold = get_min_detection_confidence()
            logger.info(f"Detector using configured confidence threshold: {self.confidence_threshold}")
        except Exception as e:
            logger.warning(f"Failed to load min_detection_confidence from config, using default: {e}")
            try:
                self.confidence_threshold = settings.confidence_threshold
            except AttributeError:
                self.confidence_threshold = 0.5
            
        self._model_cache = TTLCache(maxsize=1, ttl=3600)  # Cache model for 1 hour
        
        # Ensure model directory exists
        self.model_path.mkdir(parents=True, exist_ok=True)
        
    def load_model(self) -> bool:
        """
        Load YOLO model.
        Returns: True if model loaded successfully
        """
        if not YOLO_AVAILABLE:
            logger.warning("YOLO not available. Running in demo mode.")
            return False
            
        if 'model' in self._model_cache:
            self.model = self._model_cache['model']
            return True
            
        try:
            # Handle config variable safely
            model_name = getattr(settings, 'yolo_model', 'yolov8n.pt')
            model_file = self.model_path / model_name
            
            logger.info(f"Loading YOLO model from: {model_file}")
            
            if model_file.exists():
                self.model = YOLO(str(model_file))
            else:
                # Download default model
                logger.info(f"Downloading YOLO model: {model_name}")
                self.model = YOLO(model_name)
                # Save for future use
                self.model.save(str(model_file))
            
            self._model_cache['model'] = self.model
            logger.info("YOLO model loaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")
            return False
    
    def detect(
        self, 
        image: np.ndarray,
        confidence_threshold: float = None
    ) -> List[Detection]:
        """
        Detect objects in image.
        """
        confidence_threshold = confidence_threshold or self.confidence_threshold
        
        if self.model is None:
            if not self.load_model():
                return self._mock_detection(image)
        
        try:
            # Run inference
            results = self.model(image, verbose=False)[0]
            
            detections = []
            for box in results.boxes:
                # Safe conversion from tensors to python types
                try:
                    class_id = int(box.cls[0].item())
                    confidence = float(box.conf[0].item())
                    
                    if confidence < confidence_threshold:
                        continue
                    
                    coords = box.xyxy[0].tolist()
                    x1, y1, x2, y2 = map(int, coords)
                    
                    center = ((x1 + x2) // 2, (y1 + y2) // 2)
                    area = (x2 - x1) * (y2 - y1)
                    
                    class_name = self.model.names.get(class_id, f"class_{class_id}")
                    
                    detections.append(Detection(
                        class_id=class_id,
                        class_name=class_name,
                        confidence=confidence,
                        bbox=(x1, y1, x2, y2),
                        center=center,
                        area=area
                    ))
                except Exception as box_err:
                    logger.warning(f"Error parsing detection box: {box_err}")
                    continue
            
            return detections
            
        except Exception as e:
            logger.error(f"Detection error: {e}")
            return self._mock_detection(image) # Fallback to mock on error
    
    def detect_vehicles(
        self, 
        image: np.ndarray,
        confidence_threshold: float = None
    ) -> List[VehicleDetection]:
        """
        Detect vehicles in image.
        """
        detections = self.detect(image, confidence_threshold)
        
        vehicles = []
        for det in detections:
            if det.class_id in self.VEHICLE_CLASSES:
                vehicle = VehicleDetection(
                    class_id=det.class_id,
                    class_name=det.class_name,
                    confidence=det.confidence,
                    bbox=det.bbox,
                    center=det.center,
                    area=det.area,
                    vehicle_type=self.VEHICLE_CLASSES[det.class_id],
                    license_plate_region=self._estimate_plate_region(det.bbox, det.class_id)
                )
                vehicles.append(vehicle)
        
        return vehicles
    
    def detect_traffic_elements(
        self, 
        image: np.ndarray,
        confidence_threshold: float = None
    ) -> Dict[str, List[Detection]]:
        """
        Detect traffic-related elements.
        """
        detections = self.detect(image, confidence_threshold)
        
        result = {
            'traffic_lights': [],
            'stop_signs': [],
            'pedestrians': []
        }
        
        for det in detections:
            if det.class_id == 9:  # traffic_light
                result['traffic_lights'].append(det)
            elif det.class_id == 11:  # stop_sign
                result['stop_signs'].append(det)
            elif det.class_id == 0:  # person
                result['pedestrians'].append(det)
        
        return result
    
    def _estimate_plate_region(
        self, 
        vehicle_bbox: Tuple[int, int, int, int],
        vehicle_class: int
    ) -> Tuple[int, int, int, int]:
        """
        Estimate license plate region based on vehicle bbox.
        """
        x1, y1, x2, y2 = vehicle_bbox
        width = x2 - x1
        height = y2 - y1
        
        # Different estimation for different vehicle types
        if vehicle_class == 3:  # motorcycle
            # Plate usually at the back, lower portion
            plate_x1 = x1 + int(width * 0.3)
            plate_x2 = x1 + int(width * 0.7)
            plate_y1 = y1 + int(height * 0.7)
            plate_y2 = y2
        else:  # cars, trucks, buses
            # Plate usually at front/back, lower portion
            plate_x1 = x1 + int(width * 0.2)
            plate_x2 = x1 + int(width * 0.8)
            plate_y1 = y1 + int(height * 0.75)
            plate_y2 = y2
        
        return (plate_x1, plate_y1, plate_x2, plate_y2)
    
    def _mock_detection(self, image: np.ndarray) -> List[Detection]:
        """
        Generate mock detections for testing without model.
        """
        import cv2
        
        detections = []
        try:
            height, width = image.shape[:2]
            
            # Use edge detection to find potential objects
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 50, 150)
            contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                area = cv2.contourArea(contour)
                # Filter by reasonable vehicle-sized regions
                if area > (width * height * 0.01) and area < (width * height * 0.5):
                    x, y, w, h = cv2.boundingRect(contour)
                    aspect_ratio = w / h if h > 0 else 0
                    
                    # Vehicle-like aspect ratio
                    if 0.5 < aspect_ratio < 4.0:
                        # Estimate class based on size
                        relative_size = area / (width * height)
                        if relative_size > 0.1:
                            class_id, class_name = 7, 'truck'  # Large = truck
                        elif relative_size > 0.05:
                            class_id, class_name = 2, 'car'  # Medium = car
                        else:
                            class_id, class_name = 3, 'motorcycle'  # Small = motorcycle
                        
                        detections.append(Detection(
                            class_id=class_id,
                            class_name=class_name,
                            confidence=0.7 + (0.25 * relative_size),
                            bbox=(x, y, x + w, y + h),
                            center=(x + w // 2, y + h // 2),
                            area=area
                        ))
            return detections[:10]  # Limit to top 10
        except Exception:
            return []

# Singleton instance
object_detector = ObjectDetector()