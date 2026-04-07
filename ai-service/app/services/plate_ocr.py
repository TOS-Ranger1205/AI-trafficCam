"""
License Plate OCR Service for AI TrafficCam
Uses EasyOCR for license plate text extraction
FINAL CORRECTED VERSION
"""

import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import numpy as np
import cv2
from cachetools import TTLCache
import logging

# Logger setup
try:
    from app.core.logging import logger
except ImportError:
    logger = logging.getLogger(__name__)

# Settings setup
try:
    from app.core.config import settings
    MODEL_PATH = Path(settings.model_path) / "easyocr"
except (ImportError, AttributeError):
    MODEL_PATH = Path("./models") / "easyocr"

# EasyOCR Import
try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    EASYOCR_AVAILABLE = False
    logger.warning("EasyOCR not installed. OCR functionality limited.")


@dataclass
class PlateResult:
    """License plate OCR result."""
    text: str
    confidence: float
    bbox: Tuple[int, int, int, int]
    normalized_text: str  # Cleaned/standardized text
    is_valid: bool  # Matches expected plate format


class LicensePlateOCR:
    """
    License Plate OCR service using EasyOCR.
    Handles Indian license plate formats.
    """
    
    # Indian license plate regex patterns
    INDIAN_PLATE_PATTERNS = [
        r'^[A-Z]{2}\s*\d{1,2}\s*[A-Z]{1,3}\s*\d{1,4}$',  # Standard: MH12AB1234
        r'^[A-Z]{2}\s*\d{2}\s*\d{4}$',  # Old format: MH121234
        r'^[A-Z]{3}\s*\d{4}$',  # Temporary: TMP1234
        r'^\d{2}\s*BH\s*\d{4}\s*[A-Z]{1,2}$',  # Bharat Series: 22BH1234AB
    ]
    
    def __init__(self):
        self.reader = None
        self._reader_cache = TTLCache(maxsize=1, ttl=3600)
        self.model_path = MODEL_PATH
        self.model_path.mkdir(parents=True, exist_ok=True)
        self._load_attempted = False
        self._load_failed = False
    
    def load_reader(self) -> bool:
        """Load EasyOCR reader."""
        if self._load_failed:
            return False
            
        if not EASYOCR_AVAILABLE:
            logger.warning("EasyOCR not available")
            self._load_failed = True
            return False
            
        if 'reader' in self._reader_cache:
            self.reader = self._reader_cache['reader']
            return True
        
        self._load_attempted = True
        
        try:
            logger.info("Loading EasyOCR reader...")
            self.reader = easyocr.Reader(
                ['en'],
                model_storage_directory=str(self.model_path),
                gpu=False
            )
            self._reader_cache['reader'] = self.reader
            logger.info("EasyOCR reader loaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load EasyOCR: {e}")
            self._load_failed = True
            return False

    def extract_license_plate(self, image: np.ndarray) -> Optional[PlateResult]:
        """
        CRITICAL WRAPPER: Matches the function name expected by production_pipeline.py
        Returns a PlateResult OBJECT, NOT a string.
        """
        return self.extract_plate_text(image)

    def extract_plate_text(
        self,
        image: np.ndarray,
        plate_region: Optional[Tuple[int, int, int, int]] = None
    ) -> Optional[PlateResult]:
        """
        Extract license plate text from image (Internal Logic).
        """
        if image is None or image.size == 0:
            return None

        # Crop to plate region if provided
        if plate_region:
            x1, y1, x2, y2 = plate_region
            h, w = image.shape[:2]
            padding = 10
            x1 = max(0, x1 - padding)
            y1 = max(0, y1 - padding)
            x2 = min(w, x2 + padding)
            y2 = min(h, y2 + padding)
            image = image[y1:y2, x1:x2]
        
        # Preprocess
        processed = self._preprocess_plate_image(image)
        
        if self.reader is None:
            if not self.load_reader():
                return self._fallback_ocr(processed, plate_region)
        
        try:
            results = self.reader.readtext(processed)
            if not results:
                return None
            
            texts = []
            total_confidence = 0
            bbox = None
            
            for detection in results:
                box, text, confidence = detection
                texts.append(text)
                total_confidence += confidence
                if bbox is None:
                    bbox = self._get_bbox_from_points(box)
                else:
                    new_bbox = self._get_bbox_from_points(box)
                    bbox = self._merge_bboxes(bbox, new_bbox)
            
            combined_text = ' '.join(texts)
            avg_confidence = total_confidence / len(results) if results else 0
            normalized = self._normalize_plate_text(combined_text)
            is_valid = self._validate_plate_format(normalized)
            
            return PlateResult(
                text=combined_text,
                confidence=avg_confidence,
                bbox=bbox or plate_region or (0, 0, 0, 0),
                normalized_text=normalized,
                is_valid=is_valid
            )
            
        except Exception as e:
            logger.error(f"OCR error: {e}")
            return None
    
    def _preprocess_plate_image(self, image: np.ndarray) -> np.ndarray:
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()
        
        h, w = gray.shape
        if w < 200 and w > 0:
            scale = 200 / w
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        
        denoised = cv2.fastNlMeansDenoising(gray, h=10)
        thresh = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        cleaned = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
        return cleaned
    
    def _normalize_plate_text(self, text: str) -> str:
        # Remove spaces and special chars
        normalized = re.sub(r'[^a-zA-Z0-9]', '', text.upper())
        
        # OCR Corrections map
        corrections = {'O': '0', 'I': '1', 'S': '5', 'B': '8', 'G': '6', 'Z': '2', 'Q': '0'}
        
        result = []
        for i, char in enumerate(normalized):
            if char in corrections:
                if i < 2: # Should be letters
                    pass 
                elif (i >= 2 and i <= 3) or (i >= len(normalized) - 4): # Should be numbers
                    char = corrections.get(char, char)
            result.append(char)
        
        return ''.join(result)
    
    def _validate_plate_format(self, text: str) -> bool:
        for pattern in self.INDIAN_PLATE_PATTERNS:
            if re.match(pattern, text):
                return True
        return False
    
    def _get_bbox_from_points(self, points: List) -> Tuple[int, int, int, int]:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (min(xs), min(ys), max(xs), max(ys))
    
    def _merge_bboxes(self, bbox1, bbox2):
        return (min(bbox1[0], bbox2[0]), min(bbox1[1], bbox2[1]), max(bbox1[2], bbox2[2]), max(bbox1[3], bbox2[3]))
    
    def _fallback_ocr(self, image, region):
        h, w = image.shape[:2]
        # Return a safe dummy object if fallback is needed
        return PlateResult(
            text="UNKNOWN",
            confidence=0.0,
            bbox=region or (0, 0, w, h),
            normalized_text="UNKNOWN",
            is_valid=False
        )

# Singleton instance
license_plate_ocr = LicensePlateOCR()