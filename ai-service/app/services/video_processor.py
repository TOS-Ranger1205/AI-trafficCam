"""
Video Processing Service for AI TrafficCam
Handles frame extraction and video metadata analysis
"""

import cv2
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple, Generator
from dataclasses import dataclass
import numpy as np

from app.core.logging import logger
from app.core.config import settings


@dataclass
class VideoMetadata:
    """Video metadata container."""
    width: int
    height: int
    fps: float
    total_frames: int
    duration: float
    codec: str


@dataclass
class Frame:
    """Extracted frame container."""
    frame_number: int
    timestamp: float
    image: np.ndarray


class VideoProcessor:
    """
    Video processing service for frame extraction and analysis.
    Handles traffic camera video files.
    """
    
    def __init__(self):
        self.temp_dir = Path(settings.temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.frame_sample_rate = settings.frame_sample_rate
        
    def get_video_metadata(self, video_path: str) -> VideoMetadata:
        """
        Extract metadata from video file.
        
        Args:
            video_path: Path to video file
            
        Returns:
            VideoMetadata object with video properties
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        cap = cv2.VideoCapture(video_path)
        
        if not cap.isOpened():
            raise ValueError(f"Cannot open video file: {video_path}")
        
        try:
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = total_frames / fps if fps > 0 else 0
            codec_int = int(cap.get(cv2.CAP_PROP_FOURCC))
            codec = "".join([chr((codec_int >> 8 * i) & 0xFF) for i in range(4)])
            
            return VideoMetadata(
                width=width,
                height=height,
                fps=fps,
                total_frames=total_frames,
                duration=duration,
                codec=codec
            )
        finally:
            cap.release()
    
    def extract_frames(
        self, 
        video_path: str, 
        sample_rate: int = None,
        max_frames: int = None
    ) -> Generator[Frame, None, None]:
        """
        Extract frames from video at specified sample rate.
        
        Args:
            video_path: Path to video file
            sample_rate: Extract every Nth frame (default from settings)
            max_frames: Maximum number of frames to extract
            
        Yields:
            Frame objects containing frame data
        """
        sample_rate = sample_rate or self.frame_sample_rate
        
        cap = cv2.VideoCapture(video_path)
        
        if not cap.isOpened():
            raise ValueError(f"Cannot open video file: {video_path}")
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = 0
        extracted_count = 0
        
        try:
            while True:
                ret, frame = cap.read()
                
                if not ret:
                    break
                
                # Sample frames based on rate
                if frame_count % sample_rate == 0:
                    timestamp = frame_count / fps if fps > 0 else 0
                    
                    yield Frame(
                        frame_number=frame_count,
                        timestamp=timestamp,
                        image=frame
                    )
                    
                    extracted_count += 1
                    
                    if max_frames and extracted_count >= max_frames:
                        break
                
                frame_count += 1
                
        finally:
            cap.release()
            
        logger.info(f"Extracted {extracted_count} frames from {frame_count} total frames")
    
    def save_frame(
        self, 
        frame: Frame, 
        output_dir: str, 
        prefix: str = "frame"
    ) -> str:
        """
        Save a frame to disk.
        
        Args:
            frame: Frame object to save
            output_dir: Directory to save frame
            prefix: Filename prefix
            
        Returns:
            Path to saved frame
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        filename = f"{prefix}_{frame.frame_number:06d}.jpg"
        filepath = output_path / filename
        
        cv2.imwrite(str(filepath), frame.image)
        
        return str(filepath)
    
    def extract_region(
        self, 
        image: np.ndarray, 
        bbox: Tuple[int, int, int, int]
    ) -> np.ndarray:
        """
        Extract a region from an image.
        
        Args:
            image: Source image
            bbox: Bounding box (x1, y1, x2, y2)
            
        Returns:
            Cropped image region
        """
        x1, y1, x2, y2 = bbox
        return image[y1:y2, x1:x2].copy()
    
    def resize_frame(
        self, 
        image: np.ndarray, 
        target_size: Tuple[int, int]
    ) -> np.ndarray:
        """
        Resize frame to target size while maintaining aspect ratio.
        
        Args:
            image: Source image
            target_size: Target (width, height)
            
        Returns:
            Resized image
        """
        return cv2.resize(image, target_size, interpolation=cv2.INTER_LINEAR)
    
    def enhance_frame(self, image: np.ndarray) -> np.ndarray:
        """
        Enhance frame for better detection.
        
        Args:
            image: Source image
            
        Returns:
            Enhanced image
        """
        # Convert to LAB color space
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        
        # Apply CLAHE to L channel
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        
        # Convert back to BGR
        enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        
        return enhanced
    
    def detect_motion_regions(
        self, 
        prev_frame: np.ndarray, 
        curr_frame: np.ndarray,
        threshold: int = 30,
        min_area: int = 500
    ) -> List[Tuple[int, int, int, int]]:
        """
        Detect regions with motion between frames.
        
        Args:
            prev_frame: Previous frame
            curr_frame: Current frame
            threshold: Difference threshold
            min_area: Minimum contour area
            
        Returns:
            List of bounding boxes for motion regions
        """
        # Convert to grayscale
        gray1 = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)
        
        # Blur to reduce noise
        gray1 = cv2.GaussianBlur(gray1, (21, 21), 0)
        gray2 = cv2.GaussianBlur(gray2, (21, 21), 0)
        
        # Compute difference
        diff = cv2.absdiff(gray1, gray2)
        
        # Threshold
        _, thresh = cv2.threshold(diff, threshold, 255, cv2.THRESH_BINARY)
        
        # Dilate to fill gaps
        thresh = cv2.dilate(thresh, None, iterations=2)
        
        # Find contours
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        
        # Get bounding boxes
        regions = []
        for contour in contours:
            if cv2.contourArea(contour) >= min_area:
                x, y, w, h = cv2.boundingRect(contour)
                regions.append((x, y, x + w, y + h))
        
        return regions


# Singleton instance
video_processor = VideoProcessor()
