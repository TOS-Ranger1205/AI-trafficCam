"""
Services module initialization

Production Traffic Violation Detection Pipeline:
- video_processor: Frame extraction and video metadata
- object_detector: YOLOv8-nano object detection
- tracker: ByteTrack vehicle tracking
- frame_sampler: Adaptive keyframe sampling
- rule_engine: Deterministic violation rules
- plate_ocr: License plate OCR (EasyOCR)
- violation_detector: Violation detection coordinator
- production_pipeline: Full production AI pipeline
- dispute_analyzer: AI-powered dispute analysis
"""

from app.services.video_processor import video_processor
from app.services.detector import object_detector
from app.services.plate_ocr import license_plate_ocr
from app.services.violation_detector import violation_detector
from app.services.dispute_analyzer import dispute_analyzer
from app.services.tracker import vehicle_tracker, ByteTracker
from app.services.frame_sampler import frame_sampler
from app.services.rule_engine import rule_engine
from app.services.production_pipeline import production_pipeline

__all__ = [
    'video_processor',
    'object_detector', 
    'license_plate_ocr',
    'violation_detector',
    'dispute_analyzer',
    'vehicle_tracker',
    'ByteTracker',
    'frame_sampler',
    'rule_engine',
    'production_pipeline'
]
