"""
API Endpoints for AI TrafficCam Service
"""

import time
import base64
from pathlib import Path
from typing import Optional
from datetime import datetime

import numpy as np
import cv2
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.logging import logger
from app.core.config import settings
from app.core.security import verify_api_key
from app.api.schemas import (
    VideoAnalysisRequest, VideoAnalysisResponse,
    FrameAnalysisRequest, FrameAnalysisResponse,
    LicensePlateRequest, LicensePlateResponse,
    DisputeAnalysisRequest, DisputeAnalysisResponse,
    HealthResponse, ErrorResponse,
    ViolationResponse, VehicleDetectionResponse, BoundingBox,
    ProcessingStatus, ViolationType
)
from app.services import (
    video_processor,
    object_detector,
    license_plate_ocr,
    violation_detector,
    dispute_analyzer
)

router = APIRouter()

# Track server start time for uptime
START_TIME = time.time()


# ============== Health & Status ==============

@router.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Check service health and model status."""
    models_loaded = {
        "yolo": object_detector.model is not None,
        "easyocr": license_plate_ocr.reader is not None,
    }
    
    return HealthResponse(
        status="healthy",
        version=settings.version,
        models_loaded=models_loaded,
        uptime_seconds=time.time() - START_TIME
    )


@router.get("/ready", tags=["Health"])
async def readiness_check():
    """Check if service is ready to accept requests."""
    # Try to load models if not loaded
    yolo_ready = object_detector.load_model()
    ocr_ready = license_plate_ocr.load_reader()
    
    if not yolo_ready and not settings.allow_mock_detection:
        raise HTTPException(status_code=503, detail="YOLO model not ready")
    
    return {"status": "ready", "yolo": yolo_ready, "ocr": ocr_ready}


# ============== Video Analysis ==============

@router.post(
    "/analyze/video",
    response_model=VideoAnalysisResponse,
    tags=["Analysis"],
    dependencies=[Depends(verify_api_key)]
)
async def analyze_video(request: VideoAnalysisRequest):
    """
    Analyze a traffic video for violations.
    
    This endpoint processes the entire video and returns all detected violations
    with evidence and AI explanations.
    """
    start_time = time.time()
    
    try:
        video_path = Path(request.video_url)
        
        if not video_path.exists():
            raise HTTPException(status_code=404, detail=f"Video not found: {request.video_url}")
        
        # Get video metadata
        metadata = video_processor.get_video_metadata(str(video_path))
        
        if metadata is None:
            raise HTTPException(status_code=400, detail="Could not read video file")
        
        # Reset violation detector for new video
        violation_detector.reset()
        
        violations = []
        frames_analyzed = 0
        
        # Process frames
        for frame in video_processor.extract_frames(str(video_path), request.sample_rate):
            frames_analyzed += 1
            
            # Detect violations in frame
            frame_violations = violation_detector.process_frame(
                frame.image,
                frame.frame_number,
                frame.timestamp,
                request.video_id
            )
            
            # Convert to response format
            for v in frame_violations:
                violation_resp = ViolationResponse(
                    violation_type=ViolationType(v.violation_type.value),
                    confidence=v.confidence,
                    fine_amount=v.fine_amount,
                    vehicle=_convert_vehicle_detection(v.vehicle_detection) if v.vehicle_detection else None,
                    license_plate=v.license_plate.normalized_text if v.license_plate else None,
                    license_plate_valid=v.license_plate.is_valid if v.license_plate else False,
                    evidence_path=v.evidence[0].image_path if v.evidence else None,
                    frame_number=frame.frame_number,
                    timestamp=frame.timestamp,
                    ai_reasoning=v.ai_reasoning
                )
                violations.append(violation_resp)
        
        processing_time = time.time() - start_time
        
        return VideoAnalysisResponse(
            video_id=request.video_id,
            status=ProcessingStatus.COMPLETED,
            duration_seconds=metadata.duration,
            frames_analyzed=frames_analyzed,
            violations_detected=len(violations),
            violations=violations,
            processing_time_seconds=processing_time,
            metadata={
                "width": metadata.width,
                "height": metadata.height,
                "fps": metadata.fps,
                "location": request.location,
                "camera_id": request.camera_id
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Video analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/analyze/video/async",
    tags=["Analysis"],
    dependencies=[Depends(verify_api_key)]
)
async def analyze_video_async(
    request: VideoAnalysisRequest,
    background_tasks: BackgroundTasks
):
    """
    Start async video analysis.
    
    Returns immediately with a task ID. Poll /analyze/status/{task_id} for results.
    """
    import uuid
    task_id = str(uuid.uuid4())
    
    # In production, this would use Celery or similar
    # For now, return task_id and process in background
    background_tasks.add_task(
        _process_video_background,
        task_id,
        request
    )
    
    return {
        "task_id": task_id,
        "status": "processing",
        "message": "Video analysis started. Poll /analyze/status/{task_id} for results."
    }


async def _process_video_background(task_id: str, request: VideoAnalysisRequest):
    """Background video processing task."""
    # In production, store results in Redis/database
    logger.info(f"Background processing started for task: {task_id}")
    # Process video...
    logger.info(f"Background processing completed for task: {task_id}")


# ============== Frame Analysis ==============

@router.post(
    "/analyze/frame",
    response_model=FrameAnalysisResponse,
    tags=["Analysis"],
    dependencies=[Depends(verify_api_key)]
)
async def analyze_frame(request: FrameAnalysisRequest):
    """
    Analyze a single frame for vehicles and violations.
    """
    start_time = time.time()
    
    try:
        # Get image from request
        image = None
        
        if request.image_base64:
            image = _decode_base64_image(request.image_base64)
        elif request.image_url:
            image = cv2.imread(request.image_url)
        
        if image is None:
            raise HTTPException(status_code=400, detail="Could not load image")
        
        # Detect vehicles
        vehicles = object_detector.detect_vehicles(image)
        traffic_elements = object_detector.detect_traffic_elements(image)
        
        # Convert detections
        detection_responses = []
        for v in vehicles:
            # Try to get license plate
            plate = license_plate_ocr.extract_plate_text(image, v.license_plate_region)
            
            detection_responses.append(VehicleDetectionResponse(
                vehicle_type=v.vehicle_type,
                confidence=v.confidence,
                bbox=BoundingBox(x1=v.bbox[0], y1=v.bbox[1], x2=v.bbox[2], y2=v.bbox[3]),
                license_plate=plate.normalized_text if plate else None,
                license_plate_confidence=plate.confidence if plate else None
            ))
        
        # Check for violations
        violation_detector.reset()
        frame_violations = violation_detector.process_frame(
            image, 0, 0, request.frame_id
        )
        
        violations = []
        for v in frame_violations:
            violations.append(ViolationResponse(
                violation_type=ViolationType(v.violation_type.value),
                confidence=v.confidence,
                fine_amount=v.fine_amount,
                vehicle=_convert_vehicle_detection(v.vehicle_detection) if v.vehicle_detection else None,
                license_plate=v.license_plate.normalized_text if v.license_plate else None,
                license_plate_valid=v.license_plate.is_valid if v.license_plate else False,
                evidence_path=None,
                frame_number=0,
                timestamp=0,
                ai_reasoning=v.ai_reasoning
            ))
        
        processing_time = (time.time() - start_time) * 1000  # Convert to ms
        
        return FrameAnalysisResponse(
            frame_id=request.frame_id,
            detections=detection_responses,
            violations=violations,
            traffic_light_state=violation_detector.traffic_light_state,
            pedestrians_count=len(traffic_elements.get('pedestrians', [])),
            processing_time_ms=processing_time
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Frame analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============== License Plate ==============

@router.post(
    "/extract/plate",
    response_model=LicensePlateResponse,
    tags=["Analysis"],
    dependencies=[Depends(verify_api_key)]
)
async def extract_license_plate(request: LicensePlateRequest):
    """
    Extract license plate text from image.
    """
    try:
        image = None
        
        if request.image_base64:
            image = _decode_base64_image(request.image_base64)
        elif request.image_url:
            image = cv2.imread(request.image_url)
        
        if image is None:
            raise HTTPException(status_code=400, detail="Could not load image")
        
        # Get region of interest
        roi = None
        if request.region_of_interest and len(request.region_of_interest) == 4:
            roi = tuple(request.region_of_interest)
        
        # Extract plate
        result = license_plate_ocr.extract_plate_text(image, roi)
        
        if result is None:
            raise HTTPException(status_code=404, detail="No license plate detected")
        
        return LicensePlateResponse(
            text=result.text,
            normalized_text=result.normalized_text,
            confidence=result.confidence,
            is_valid=result.is_valid,
            bbox=BoundingBox(
                x1=result.bbox[0], y1=result.bbox[1],
                x2=result.bbox[2], y2=result.bbox[3]
            ) if result.bbox else None
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Plate extraction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============== Dispute Analysis ==============

@router.post(
    "/analyze/dispute",
    response_model=DisputeAnalysisResponse,
    tags=["Dispute"],
    dependencies=[Depends(verify_api_key)]
)
async def analyze_dispute(request: DisputeAnalysisRequest):
    """
    Analyze a violation dispute and provide AI recommendation.
    
    This endpoint analyzes the dispute statement, evidence, and violation data
    to provide an AI-powered recommendation.
    """
    try:
        result = dispute_analyzer.analyze_dispute(
            dispute_id=request.dispute_id,
            user_statement=request.user_statement,
            violation_data=request.violation_data,
            evidence_files=request.evidence_files,
            user_history=request.user_history
        )
        
        return DisputeAnalysisResponse(
            dispute_id=result.dispute_id,
            category=result.category.value,
            recommendation=result.recommendation,
            confidence=result.confidence,
            reasoning=result.reasoning,
            evidence_analysis=result.evidence_analysis,
            factors=result.factors,
            suggested_action=result.suggested_action,
            human_review_required=result.human_review_required,
            analyzed_at=result.analyzed_at
        )
        
    except Exception as e:
        logger.error(f"Dispute analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/analyze-dispute",
    tags=["Dispute"],
    dependencies=[Depends(verify_api_key)]
)
async def analyze_dispute_for_backend(request: dict):
    """
    Analyze dispute for backend integration.
    
    This endpoint is called by the backend service for dispute analysis.
    """
    try:
        dispute_id = request.get('dispute_id')
        violation_id = request.get('violation_id')
        dispute_reason = request.get('dispute_reason', '')
        detailed_explanation = request.get('detailed_explanation', '')
        violation_details = request.get('violation_details', {})
        supporting_documents = request.get('supporting_documents', [])
        
        # Combine dispute reason and explanation
        user_statement = f"{dispute_reason}. {detailed_explanation}"
        
        result = dispute_analyzer.analyze_dispute(
            dispute_id=dispute_id,
            user_statement=user_statement,
            violation_data=violation_details,
            evidence_files=supporting_documents,
            user_history=None
        )
        
        return {
            "success": True,
            "recommendation": result.recommendation.value,
            "confidence": result.confidence,
            "reason": result.reasoning,
            "analysis": {
                "category": result.category.value,
                "factors": result.factors,
                "evidence_analysis": result.evidence_analysis,
                "suggested_action": result.suggested_action,
                "human_review_required": result.human_review_required
            }
        }
        
    except Exception as e:
        logger.error(f"Dispute analysis error: {e}")
        return {
            "success": False,
            "recommendation": "manual_review",
            "confidence": 0,
            "reason": f"AI analysis error: {str(e)}",
            "analysis": None
        }


# ============== File Upload ==============

@router.post(
    "/upload/video",
    tags=["Upload"],
    dependencies=[Depends(verify_api_key)]
)
async def upload_video(
    file: UploadFile = File(...),
    video_id: str = Form(...),
    camera_id: Optional[str] = Form(None),
    location: Optional[str] = Form(None)
):
    """
    Upload a video file for analysis.
    """
    # Validate file type
    allowed_types = ['video/mp4', 'video/avi', 'video/quicktime', 'video/x-msvideo']
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid file type. Allowed: {allowed_types}"
        )
    
    # Create upload directory
    upload_dir = Path(settings.upload_path)
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Save file
    file_path = upload_dir / f"{video_id}_{file.filename}"
    
    try:
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Get video info
        metadata = video_processor.get_video_metadata(str(file_path))
        
        return {
            "video_id": video_id,
            "file_path": str(file_path),
            "file_size_mb": len(content) / (1024 * 1024),
            "duration_seconds": metadata.duration if metadata else None,
            "resolution": f"{metadata.width}x{metadata.height}" if metadata else None,
            "status": "uploaded"
        }
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/upload/evidence",
    tags=["Upload"],
    dependencies=[Depends(verify_api_key)]
)
async def upload_evidence(
    file: UploadFile = File(...),
    dispute_id: str = Form(...)
):
    """
    Upload evidence file for dispute.
    """
    # Validate file type
    allowed_types = [
        'image/jpeg', 'image/png', 'image/jpg',
        'application/pdf',
        'video/mp4'
    ]
    
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {allowed_types}"
        )
    
    # Create upload directory
    upload_dir = Path(settings.upload_path) / "evidence" / dispute_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    # Save file
    file_path = upload_dir / file.filename
    
    try:
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        return {
            "dispute_id": dispute_id,
            "file_path": str(file_path),
            "file_name": file.filename,
            "file_size_kb": len(content) / 1024,
            "status": "uploaded"
        }
        
    except Exception as e:
        logger.error(f"Evidence upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============== Helper Functions ==============

def _decode_base64_image(base64_str: str) -> np.ndarray:
    """Decode base64 string to OpenCV image."""
    try:
        # Remove data URL prefix if present
        if ',' in base64_str:
            base64_str = base64_str.split(',')[1]
        
        img_bytes = base64.b64decode(base64_str)
        nparr = np.frombuffer(img_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        return image
    except Exception as e:
        logger.error(f"Base64 decode error: {e}")
        return None


def _convert_vehicle_detection(detection) -> VehicleDetectionResponse:
    """Convert internal vehicle detection to response model."""
    return VehicleDetectionResponse(
        vehicle_type=detection.vehicle_type,
        confidence=detection.confidence,
        bbox=BoundingBox(
            x1=detection.bbox[0],
            y1=detection.bbox[1],
            x2=detection.bbox[2],
            y2=detection.bbox[3]
        ),
        license_plate=None,
        license_plate_confidence=None
    )


# ============== Backend Integration Endpoint ==============

@router.post(
    "/process-video",
    tags=["Analysis"],
    dependencies=[Depends(verify_api_key)]
)
async def process_video_for_backend(request: dict):
    """
    Process video for backend integration.
    
    This endpoint is called by the backend service to process uploaded videos.
    Returns violations in the format expected by the backend.
    
    CRITICAL: This endpoint ALWAYS returns a JSON response with:
    - success: True/False
    - video_id: The video ID
    - frames_processed: Number of frames analyzed
    - violations: Array of detected violations (empty if none)
    - error: Error message if success=False
    """
    start_time = time.time()
    video_id = request.get('video_id', 'unknown')
    
    # Build base response - ensures we ALWAYS have a valid response
    base_response = {
        "success": False,
        "video_id": video_id,
        "frames_processed": 0,
        "violations": [],
        "processing_time": 0,
        "metadata": {},
        "error": None
    }
    
    try:
        video_path = request.get('video_path')
        metadata = request.get('metadata', {})
        
        # Validate required fields
        if not video_id or video_id == 'unknown':
            base_response["error"] = "video_id is required"
            logger.error(f"[process-video] Missing video_id")
            return JSONResponse(content=base_response, status_code=200)
        
        if not video_path:
            base_response["error"] = "video_path is required"
            logger.error(f"[process-video] Missing video_path for {video_id}")
            return JSONResponse(content=base_response, status_code=200)
        
        path = Path(video_path)
        
        if not path.exists():
            base_response["error"] = f"Video file not found: {video_path}"
            logger.error(f"[process-video] Video not found: {video_path}")
            return JSONResponse(content=base_response, status_code=200)
        
        # Get video metadata with error handling
        try:
            video_meta = video_processor.get_video_metadata(str(path))
        except Exception as meta_err:
            logger.error(f"[process-video] Metadata extraction failed for {video_id}: {meta_err}")
            base_response["error"] = f"Could not read video metadata: {str(meta_err)}"
            base_response["processing_time"] = time.time() - start_time
            return JSONResponse(content=base_response, status_code=200)
        
        if video_meta is None:
            base_response["error"] = "Could not read video file - file may be corrupted"
            base_response["processing_time"] = time.time() - start_time
            logger.error(f"[process-video] Video metadata is None for {video_id}")
            return JSONResponse(content=base_response, status_code=200)
        
        # Reset violation detector for new video
        try:
            violation_detector.reset()
        except Exception as reset_err:
            logger.warning(f"[process-video] Violation detector reset warning: {reset_err}")
            # Continue processing - reset failure shouldn't stop video processing
        
        violations = []
        all_detections = []  # Store ALL detections for CCTV playback overlay
        frames_processed = 0
        frame_errors = 0
        max_frame_errors = 10  # Stop if too many frames fail
        
        # Calculate sample rate: analyze ~3-5 frames per second for good tracking
        # For a 30 FPS video, sample every 6-10 frames (5-3 FPS analysis)
        # For a 10 FPS video, sample every 2-3 frames
        video_fps = video_meta.fps or 30
        target_analysis_fps = 5  # We want to analyze ~5 frames per second
        sample_rate = max(1, int(video_fps / target_analysis_fps))
        
        logger.info(f"[process-video] Processing video: {video_id}, duration: {video_meta.duration:.1f}s, " +
                   f"fps: {video_meta.fps}, sample_rate: every {sample_rate} frames ({video_fps/sample_rate:.1f} FPS analysis)")
        
        try:
            for frame in video_processor.extract_frames(str(path), sample_rate):
                frames_processed += 1
                
                # Get ALL vehicle detections for this frame (for CCTV overlay)
                try:
                    frame_detections = violation_detector.get_frame_detections(
                        frame.image,
                        frame.frame_number,
                        frame.timestamp
                    )
                    all_detections.extend(frame_detections)
                except Exception as det_err:
                    logger.warning(f"[process-video] Frame {frame.frame_number} detection error: {det_err}")
                
                # Detect violations in frame with error handling
                try:
                    frame_violations = violation_detector.process_frame(
                        frame.image,
                        frame.frame_number,
                        frame.timestamp,
                        video_id
                    )
                    
                    # Convert to backend format
                    for v in frame_violations:
                        try:
                            violation_data = {
                                "type": _map_violation_type_to_backend(v.violation_type.value),
                                "confidence": int(v.confidence * 100),  # Convert to percentage
                                "frame_number": frame.frame_number,
                                "frame_timestamp": frame.timestamp,
                                "timestamp": datetime.now().isoformat(),
                                "bounding_box": {
                                    "x1": v.vehicle_detection.bbox[0] if v.vehicle_detection else 0,
                                    "y1": v.vehicle_detection.bbox[1] if v.vehicle_detection else 0,
                                    "x2": v.vehicle_detection.bbox[2] if v.vehicle_detection else 0,
                                    "y2": v.vehicle_detection.bbox[3] if v.vehicle_detection else 0
                                },
                                "vehicle_number": v.license_plate.normalized_text if v.license_plate and v.license_plate.is_valid else None,
                                "vehicle_type": v.vehicle_detection.vehicle_type if v.vehicle_detection else "unknown",
                                "evidence_frame_path": v.evidence[0].image_path if v.evidence else None,
                                "detected_objects": [v.vehicle_detection.vehicle_type] if v.vehicle_detection else [],
                                "signal_state": violation_detector.traffic_light_state if hasattr(violation_detector, 'traffic_light_state') else "unknown",
                                "speed_detected": v.extra_data.get('speed_detected') if hasattr(v, 'extra_data') and v.extra_data else None,
                                "speed_limit": v.extra_data.get('speed_limit') if hasattr(v, 'extra_data') and v.extra_data else None,
                                "lane_details": v.extra_data.get('lane_details') if hasattr(v, 'extra_data') and v.extra_data else None
                            }
                            violations.append(violation_data)
                        except Exception as v_err:
                            logger.warning(f"[process-video] Error converting violation: {v_err}")
                            continue
                            
                except Exception as frame_err:
                    frame_errors += 1
                    logger.warning(f"[process-video] Frame {frame.frame_number} processing error: {frame_err}")
                    if frame_errors >= max_frame_errors:
                        logger.error(f"[process-video] Too many frame errors ({frame_errors}), stopping")
                        break
                    continue
                    
        except Exception as extraction_err:
            logger.error(f"[process-video] Frame extraction error for {video_id}: {extraction_err}")
            # Continue with what we have - don't fail completely
        
        processing_time = time.time() - start_time
        
        # ALWAYS return success if we processed any frames
        # Even if 0 violations, that's a valid result ("No violations detected")
        logger.info(f"[process-video] Video {video_id} completed: {frames_processed} frames, {len(violations)} violations, {len(all_detections)} detections in {processing_time:.2f}s")
        
        return JSONResponse(content={
            "success": True,
            "video_id": video_id,
            "frames_processed": frames_processed,
            "violations": violations,
            "detections": all_detections,  # All vehicle detections for CCTV overlay
            "processing_time": processing_time,
            "metadata": {
                "duration": video_meta.duration,
                "width": video_meta.width,
                "height": video_meta.height,
                "fps": video_meta.fps,
                "frame_errors": frame_errors,
                "total_detections": len(all_detections)
            }
        }, status_code=200)
        
    except Exception as e:
        logger.error(f"[process-video] Unexpected error for {video_id}: {e}", exc_info=True)
        base_response["error"] = str(e)
        base_response["processing_time"] = time.time() - start_time
        # ALWAYS return a valid JSON response, never raise/crash
        return JSONResponse(content=base_response, status_code=200)


def _map_violation_type_to_backend(violation_type: str) -> str:
    """Map internal violation types to backend expected types."""
    mapping = {
        "signal_jumping": "red_light",
        "wrong_way": "wrong_way",
        "no_helmet": "no_helmet",
        "triple_riding": "triple_riding",
        "no_seatbelt": "no_seatbelt",
        "overspeeding": "speed_violation",
        "speed_violation": "speed_violation",
        "parking_violation": "parking_violation",
        "lane_violation": "lane_violation",
        "zebra_crossing": "zebra_crossing",
        "no_license_plate": "no_license_plate",
        "mobile_phone_use": "mobile_phone_use",
        "red_light": "red_light"
    }
    return mapping.get(violation_type, violation_type)


# ============== Production Pipeline Endpoint ==============

@router.post(
    "/process-video/v2",
    tags=["Analysis"],
    dependencies=[Depends(verify_api_key)]
)
async def process_video_production(request: dict):
    """
    Production-grade video processing endpoint.
    
    Uses the new production pipeline with:
    - Adaptive keyframe sampling
    - ByteTrack vehicle tracking
    - Rule-based violation detection
    - Confidence-based legal flow
    - Evidence generation
    
    Returns:
    - success: True/False
    - video_id: The video ID
    - frames_processed: Number of frames analyzed
    - violations: Array of detected violations with legal decision
    - detections: Array for CCTV overlay rendering
    - metadata: Video and processing metadata
    """
    from app.services.production_pipeline import production_pipeline
    
    start_time = time.time()
    video_id = request.get('video_id', 'unknown')
    
    # Base response structure
    base_response = {
        "success": False,
        "video_id": video_id,
        "frames_processed": 0,
        "violations": [],
        "detections": [],
        "processing_time": 0,
        "metadata": {},
        "error": None
    }
    
    try:
        video_path = request.get('video_path')
        metadata = request.get('metadata', {})
        
        # Validate inputs
        if not video_id or video_id == 'unknown':
            base_response["error"] = "video_id is required"
            logger.error(f"[process-video-v2] Missing video_id")
            return JSONResponse(content=base_response, status_code=200)
        
        if not video_path:
            base_response["error"] = "video_path is required"
            logger.error(f"[process-video-v2] Missing video_path for {video_id}")
            return JSONResponse(content=base_response, status_code=200)
        
        if not Path(video_path).exists():
            base_response["error"] = f"Video file not found: {video_path}"
            logger.error(f"[process-video-v2] Video not found: {video_path}")
            return JSONResponse(content=base_response, status_code=200)
        
        logger.info(f"[process-video-v2] Starting production pipeline for {video_id}")
        
        # Run production pipeline
        result = production_pipeline.process_video(
            video_path=video_path,
            video_id=video_id,
            metadata=metadata
        )
        
        if not result.success:
            base_response["error"] = result.error
            base_response["processing_time"] = result.processing_time
            logger.error(f"[process-video-v2] Pipeline failed for {video_id}: {result.error}")
            return JSONResponse(content=base_response, status_code=200)
        
        # Convert violations to backend format
        backend_violations = []
        for v in result.violations:
            # Extract evidence path - the violation dict has nested evidence object
            evidence_path = None
            if 'evidence' in v and v['evidence']:
                evidence_path = v['evidence'].get('annotated_path') or v['evidence'].get('snapshot_path')
            elif 'evidencePath' in v:
                evidence_path = v['evidencePath']
            
            backend_violations.append({
                "id": v.get("id"),
                "type": _map_violation_type_to_backend(v.get("violation_type", "")),
                "confidence": int(v.get("confidence", 0) * 100),
                "decision": v.get("decision"),  # auto_challan, police_review, manual_review
                "fineAmount": v.get("fine_amount", 500),
                "frame_number": v.get("frame_number", 0),
                "frame_timestamp": v.get("timestamp", 0),  # THIS is the video timestamp in seconds
                "timestamp": datetime.now().isoformat(),
                "vehicle_number": v.get("license_plate"),
                "vehicle_type": v.get("vehicle_type", "unknown"),
                "vehicle_id": v.get("vehicle_id"),
                "evidence_frame_path": evidence_path,
                "ai_reasoning": v.get("ai_reasoning"),
                "speed_detected": v.get("detected_speed"),
                "speed_limit": v.get("speed_limit"),
                "bounding_box": None  # Will be populated from detections
            })
        
        logger.info(f"[process-video-v2] Video {video_id} completed: "
                   f"{result.frames_processed} frames, "
                   f"{len(backend_violations)} violations, "
                   f"{len(result.detections)} detections in {result.processing_time:.2f}s")
        
        return JSONResponse(content={
            "success": True,
            "video_id": video_id,
            "frames_processed": result.frames_processed,
            "vehicles_tracked": result.vehicles_tracked,
            "violations_detected": result.violations_detected,
            "violations": backend_violations,
            "detections": result.detections,
            "processing_time": result.processing_time,
            "metadata": result.metadata
        }, status_code=200)
        
    except Exception as e:
        logger.error(f"[process-video-v2] Unexpected error for {video_id}: {e}", exc_info=True)
        base_response["error"] = str(e)
        base_response["processing_time"] = time.time() - start_time
        return JSONResponse(content=base_response, status_code=200)
