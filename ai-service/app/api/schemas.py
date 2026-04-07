"""
Pydantic schemas for API request/response models
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum


# ============== Enums ==============

class ViolationType(str, Enum):
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


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DisputeRecommendation(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    REVIEW = "review"
    PARTIAL = "partial"


# ============== Request Schemas ==============

class VideoAnalysisRequest(BaseModel):
    """Request for video analysis."""
    video_url: str = Field(..., description="URL or path to video file")
    video_id: str = Field(..., description="Unique video identifier")
    location: Optional[Dict[str, Any]] = Field(None, description="Location metadata")
    camera_id: Optional[str] = Field(None, description="Camera identifier")
    timestamp: Optional[datetime] = Field(None, description="Video timestamp")
    sample_rate: float = Field(1.0, ge=0.1, le=10.0, description="Frames per second to analyze")
    
    class Config:
        json_schema_extra = {
            "example": {
                "video_url": "/data/videos/cam001_2024.mp4",
                "video_id": "vid_abc123",
                "location": {"lat": 19.0760, "lng": 72.8777, "name": "Mumbai Junction"},
                "camera_id": "CAM_MUM_001",
                "timestamp": "2024-01-15T10:30:00Z",
                "sample_rate": 2.0
            }
        }


class FrameAnalysisRequest(BaseModel):
    """Request for single frame analysis."""
    image_base64: Optional[str] = Field(None, description="Base64 encoded image")
    image_url: Optional[str] = Field(None, description="URL to image file")
    frame_id: str = Field(..., description="Unique frame identifier")
    timestamp: Optional[float] = Field(None, description="Frame timestamp in video")


class LicensePlateRequest(BaseModel):
    """Request for license plate extraction."""
    image_base64: Optional[str] = Field(None, description="Base64 encoded image")
    image_url: Optional[str] = Field(None, description="URL to image file")
    region_of_interest: Optional[List[int]] = Field(
        None, 
        description="[x1, y1, x2, y2] bounding box"
    )


class DisputeAnalysisRequest(BaseModel):
    """Request for dispute analysis."""
    dispute_id: str = Field(..., description="Unique dispute identifier")
    violation_id: str = Field(..., description="Related violation ID")
    user_statement: str = Field(..., min_length=10, description="User's dispute statement")
    violation_data: Dict[str, Any] = Field(..., description="Original violation details")
    evidence_files: Optional[List[str]] = Field(None, description="Paths to evidence files")
    user_history: Optional[Dict[str, Any]] = Field(None, description="User's dispute history")
    
    class Config:
        json_schema_extra = {
            "example": {
                "dispute_id": "disp_123",
                "violation_id": "viol_456",
                "user_statement": "I was not driving the vehicle at that time. The car was stolen and I have filed a police report.",
                "violation_data": {
                    "type": "signal_jumping",
                    "confidence": 0.85,
                    "fine_amount": 5000,
                    "license_plate": "MH12AB1234"
                },
                "evidence_files": ["/uploads/police_report.pdf"],
                "user_history": {
                    "total_disputes": 2,
                    "accepted_disputes": 1
                }
            }
        }


# ============== Response Schemas ==============

class BoundingBox(BaseModel):
    """Bounding box coordinates."""
    x1: int
    y1: int
    x2: int
    y2: int


class VehicleDetectionResponse(BaseModel):
    """Vehicle detection result."""
    vehicle_type: str
    confidence: float
    bbox: BoundingBox
    license_plate: Optional[str] = None
    license_plate_confidence: Optional[float] = None


class ViolationResponse(BaseModel):
    """Single violation response."""
    violation_type: ViolationType
    confidence: float
    fine_amount: float
    vehicle: Optional[VehicleDetectionResponse] = None
    license_plate: Optional[str] = None
    license_plate_valid: bool = False
    evidence_path: Optional[str] = None
    frame_number: int
    timestamp: float
    ai_reasoning: str


class VideoAnalysisResponse(BaseModel):
    """Response for video analysis."""
    video_id: str
    status: ProcessingStatus
    duration_seconds: float
    frames_analyzed: int
    violations_detected: int
    violations: List[ViolationResponse]
    processing_time_seconds: float
    metadata: Optional[Dict[str, Any]] = None


class FrameAnalysisResponse(BaseModel):
    """Response for frame analysis."""
    frame_id: str
    detections: List[VehicleDetectionResponse]
    violations: List[ViolationResponse]
    traffic_light_state: Optional[str] = None
    pedestrians_count: int = 0
    processing_time_ms: float


class LicensePlateResponse(BaseModel):
    """Response for license plate extraction."""
    text: str
    normalized_text: str
    confidence: float
    is_valid: bool
    bbox: Optional[BoundingBox] = None


class DisputeAnalysisResponse(BaseModel):
    """Response for dispute analysis."""
    dispute_id: str
    category: str
    recommendation: DisputeRecommendation
    confidence: float
    reasoning: str
    evidence_analysis: List[Dict[str, Any]]
    factors: Dict[str, float]
    suggested_action: str
    human_review_required: bool
    analyzed_at: datetime


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    models_loaded: Dict[str, bool]
    uptime_seconds: float


class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    detail: Optional[str] = None
    code: str
