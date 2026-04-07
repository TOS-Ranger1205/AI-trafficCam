"""
Adaptive Frame Sampler for AI TrafficCam

Production-grade frame sampling that:
- Extracts keyframes (I-frames) for best quality
- Uses motion-adaptive sampling 
- Limits to ~1 FPS effective rate
- Handles large videos efficiently via chunking

Key Design:
- NEVER process full FPS (30fps video → ~1fps sampling = 97% reduction)
- Keyframes give best image quality for detection
- Scene change detection boosts sampling rate temporarily
"""

import cv2
import subprocess
import json
import os
import tempfile
from pathlib import Path
from typing import Generator, List, Tuple, Optional, Dict, Any
from dataclasses import dataclass
import numpy as np

from app.core.logging import logger
from app.core.config import settings


@dataclass
class SampledFrame:
    """A sampled frame ready for processing."""
    frame_number: int
    timestamp: float
    image: np.ndarray
    is_keyframe: bool = False
    motion_score: float = 0.0


@dataclass
class VideoInfo:
    """Video metadata extracted via ffprobe."""
    width: int
    height: int
    fps: float
    total_frames: int
    duration: float
    codec: str
    has_audio: bool
    keyframe_count: int = 0


class AdaptiveFrameSampler:
    """
    Intelligent frame sampler that balances accuracy vs performance.
    
    Sampling Strategies:
    1. Keyframe extraction - Best quality frames from video codec
    2. Fixed interval - Every Nth frame (e.g., every 30th for 1 FPS from 30 FPS video)
    3. Motion-adaptive - More frames during high motion, fewer during static scenes
    4. Hybrid - Keyframes + interval filling
    
    For traffic violation detection, Hybrid strategy is recommended.
    """
    
    def __init__(
        self,
        target_fps: float = 1.0,           # Target effective FPS
        min_fps: float = 0.5,              # Minimum during static scenes
        max_fps: float = 3.0,              # Maximum during high motion
        motion_threshold: float = 0.02,    # Threshold for motion detection
        use_keyframes: bool = True,        # Prefer keyframes when available
        chunk_duration: int = 300,         # Process in 5-minute chunks
        visualization_mode: bool = False   # High-FPS mode for CCTV display
    ):
        """
        Initialize sampler.
        
        Args:
            target_fps: Target effective frames per second
            min_fps: Minimum FPS during static scenes
            max_fps: Maximum FPS during high motion
            motion_threshold: Threshold for detecting significant motion
            use_keyframes: Whether to prefer keyframes
            chunk_duration: Duration in seconds for chunking large videos
            visualization_mode: If True, samples at high FPS for continuous bounding box display
        """
        self.target_fps = target_fps
        self.min_fps = min_fps
        self.max_fps = max_fps
        self.motion_threshold = motion_threshold
        self.use_keyframes = use_keyframes
        self.chunk_duration = chunk_duration
        self.visualization_mode = visualization_mode
        
        # In visualization mode, use high FPS for smooth tracking display
        if visualization_mode:
            self.target_fps = 15.0  # 15 FPS for smooth visualization
            self.min_fps = 10.0
            self.max_fps = 30.0
            logger.info("[FrameSampler] VISUALIZATION MODE enabled - using 15 FPS for smooth tracking")
        
        # State for motion detection
        self.prev_frame_gray = None
        self.motion_history = []
    
    def get_video_info(self, video_path: str) -> Optional[VideoInfo]:
        """
        Extract video metadata using ffprobe.
        
        Args:
            video_path: Path to video file
            
        Returns:
            VideoInfo or None if extraction fails
        """
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                video_path
            ]
            
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                logger.warning(f"ffprobe failed: {result.stderr}")
                return self._fallback_video_info(video_path)
            
            data = json.loads(result.stdout)
            
            # Find video stream
            video_stream = None
            has_audio = False
            for stream in data.get('streams', []):
                if stream.get('codec_type') == 'video':
                    video_stream = stream
                elif stream.get('codec_type') == 'audio':
                    has_audio = True
            
            if not video_stream:
                return self._fallback_video_info(video_path)
            
            # Parse FPS (can be in format "30/1" or "29.97")
            fps_str = video_stream.get('r_frame_rate', '30/1')
            if '/' in fps_str:
                num, den = fps_str.split('/')
                fps = float(num) / float(den) if float(den) != 0 else 30.0
            else:
                fps = float(fps_str)
            
            # Duration
            duration = float(data.get('format', {}).get('duration', 0))
            if duration == 0:
                duration = float(video_stream.get('duration', 0))
            
            # Total frames
            total_frames = int(video_stream.get('nb_frames', fps * duration))
            
            return VideoInfo(
                width=int(video_stream.get('width', 0)),
                height=int(video_stream.get('height', 0)),
                fps=fps,
                total_frames=total_frames,
                duration=duration,
                codec=video_stream.get('codec_name', 'unknown'),
                has_audio=has_audio,
                keyframe_count=0  # Will be populated separately if needed
            )
            
        except Exception as e:
            logger.error(f"Error extracting video info: {e}")
            return self._fallback_video_info(video_path)
    
    def _fallback_video_info(self, video_path: str) -> Optional[VideoInfo]:
        """Fallback using OpenCV if ffprobe fails."""
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return None
            
            return VideoInfo(
                width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                fps=cap.get(cv2.CAP_PROP_FPS) or 30.0,
                total_frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
                duration=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) / (cap.get(cv2.CAP_PROP_FPS) or 30.0),
                codec='unknown',
                has_audio=False,
                keyframe_count=0
            )
        except:
            return None
        finally:
            if 'cap' in locals():
                cap.release()
    
    def get_keyframe_timestamps(self, video_path: str) -> List[float]:
        """
        Extract keyframe (I-frame) timestamps using ffprobe.
        
        Args:
            video_path: Path to video file
            
        Returns:
            List of keyframe timestamps in seconds
        """
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-select_streams', 'v:0',
                '-show_frames',
                '-show_entries', 'frame=pkt_pts_time,key_frame',
                '-of', 'json',
                video_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120  # 2 minutes timeout
            )
            
            if result.returncode != 0:
                return []
            
            data = json.loads(result.stdout)
            
            keyframes = []
            for frame in data.get('frames', []):
                if frame.get('key_frame') == 1:
                    pts = frame.get('pkt_pts_time')
                    if pts:
                        keyframes.append(float(pts))
            
            logger.info(f"Found {len(keyframes)} keyframes in video")
            return keyframes
            
        except Exception as e:
            logger.warning(f"Could not extract keyframes: {e}")
            return []
    
    def calculate_motion_score(self, frame: np.ndarray) -> float:
        """
        Calculate motion score between current and previous frame.
        
        Args:
            frame: Current frame (BGR)
            
        Returns:
            Motion score (0.0 to 1.0)
        """
        # Convert to grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Resize for faster processing
        gray = cv2.resize(gray, (320, 180))
        
        if self.prev_frame_gray is None:
            self.prev_frame_gray = gray
            return 0.0
        
        # Calculate absolute difference
        diff = cv2.absdiff(gray, self.prev_frame_gray)
        
        # Threshold to remove noise
        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        
        # Calculate percentage of changed pixels
        motion_score = np.sum(thresh > 0) / thresh.size
        
        self.prev_frame_gray = gray
        
        return motion_score
    
    def get_adaptive_interval(self, motion_score: float, base_fps: float) -> int:
        """
        Calculate frame interval based on motion.
        
        Args:
            motion_score: Current motion score (0-1)
            base_fps: Video's native FPS
            
        Returns:
            Frame interval (sample every N frames)
        """
        # Update motion history
        self.motion_history.append(motion_score)
        if len(self.motion_history) > 30:
            self.motion_history = self.motion_history[-30:]
        
        # Use smoothed motion
        avg_motion = np.mean(self.motion_history)
        
        # Calculate target FPS based on motion
        if avg_motion > self.motion_threshold * 2:
            target = self.max_fps  # High motion
        elif avg_motion > self.motion_threshold:
            target = self.target_fps  # Normal motion
        else:
            target = self.min_fps  # Static scene
        
        # Convert to interval
        interval = max(1, int(base_fps / target))
        
        return interval
    
    def sample_frames(
        self,
        video_path: str,
        strategy: str = 'hybrid',
        max_frames: Optional[int] = None,
        start_time: float = 0.0,
        end_time: Optional[float] = None,
        progress_callback: Optional[callable] = None
    ) -> Generator[SampledFrame, None, None]:
        """
        Sample frames from video using specified strategy.
        
        Args:
            video_path: Path to video file
            strategy: 'keyframe', 'interval', 'motion', 'hybrid', or 'visualization'
            max_frames: Maximum frames to sample
            start_time: Start time in seconds
            end_time: End time in seconds
            progress_callback: Callback for progress updates
            
        Yields:
            SampledFrame objects
        """
        # Auto-select visualization strategy if in visualization mode
        if self.visualization_mode and strategy != 'visualization':
            strategy = 'visualization'
            logger.info("[FrameSampler] Auto-selecting 'visualization' strategy for smooth tracking")
        # Get video info
        info = self.get_video_info(video_path)
        if not info:
            logger.error(f"Could not read video: {video_path}")
            return
        
        logger.info(f"Sampling video: {info.width}x{info.height}, {info.fps:.1f} FPS, {info.duration:.1f}s")
        
        # Reset state
        self.prev_frame_gray = None
        self.motion_history = []
        
        # Calculate frame range
        start_frame = int(start_time * info.fps)
        end_frame = int((end_time or info.duration) * info.fps)
        end_frame = min(end_frame, info.total_frames)
        
        # Get keyframe timestamps if using keyframe/hybrid strategy
        keyframe_times = set()
        if strategy in ['keyframe', 'hybrid'] and self.use_keyframes:
            keyframes = self.get_keyframe_timestamps(video_path)
            keyframe_times = set(keyframes)
        
        # Open video
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error(f"Could not open video: {video_path}")
            return
        
        # Calculate base interval
        base_interval = max(1, int(info.fps / self.target_fps))
        
        try:
            frame_count = 0
            sampled_count = 0
            current_interval = base_interval
            last_sampled_frame = -base_interval  # Allow first frame
            
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
            
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                
                current_frame = start_frame + frame_count
                timestamp = current_frame / info.fps
                
                if current_frame >= end_frame:
                    break
                
                # Check max frames
                if max_frames and sampled_count >= max_frames:
                    break
                
                # Determine if we should sample this frame
                should_sample = False
                is_keyframe = False
                motion_score = 0.0
                
                if strategy == 'keyframe':
                    # Only sample keyframes
                    should_sample = timestamp in keyframe_times
                    is_keyframe = timestamp in keyframe_times
                    
                elif strategy == 'interval':
                    # Fixed interval sampling
                    should_sample = (frame_count % base_interval == 0)
                    
                elif strategy == 'motion':
                    # Motion-adaptive sampling
                    motion_score = self.calculate_motion_score(frame)
                    current_interval = self.get_adaptive_interval(motion_score, info.fps)
                    should_sample = (current_frame - last_sampled_frame) >= current_interval
                    
                elif strategy == 'hybrid':
                    # Hybrid: keyframes + motion-adaptive interval
                    motion_score = self.calculate_motion_score(frame)
                    
                    # Always sample keyframes
                    if timestamp in keyframe_times:
                        should_sample = True
                        is_keyframe = True
                    else:
                        # Motion-adaptive for non-keyframes
                        current_interval = self.get_adaptive_interval(motion_score, info.fps)
                        should_sample = (current_frame - last_sampled_frame) >= current_interval
                
                elif strategy == 'visualization':
                    # High-frequency sampling for CCTV-style display
                    # Sample every 2nd frame (or use target_fps to control)
                    vis_interval = max(1, int(info.fps / self.target_fps))
                    should_sample = (frame_count % vis_interval == 0)
                    motion_score = 0.0  # Skip motion calculation for speed
                
                if should_sample:
                    yield SampledFrame(
                        frame_number=current_frame,
                        timestamp=timestamp,
                        image=frame,
                        is_keyframe=is_keyframe,
                        motion_score=motion_score
                    )
                    
                    sampled_count += 1
                    last_sampled_frame = current_frame
                    
                    # Progress callback
                    if progress_callback:
                        progress = (current_frame - start_frame) / (end_frame - start_frame) * 100
                        progress_callback(progress, sampled_count)
                
                frame_count += 1
                
        finally:
            cap.release()
        
        logger.info(f"Sampled {sampled_count} frames from {frame_count} total frames")
    
    def sample_in_chunks(
        self,
        video_path: str,
        strategy: str = 'hybrid',
        progress_callback: Optional[callable] = None
    ) -> Generator[Tuple[int, SampledFrame], None, None]:
        """
        Sample frames in chunks for large videos.
        
        Yields tuples of (chunk_index, SampledFrame).
        Memory-efficient for long videos.
        
        Args:
            video_path: Path to video file
            strategy: Sampling strategy
            progress_callback: Callback for progress updates
            
        Yields:
            (chunk_index, SampledFrame) tuples
        """
        info = self.get_video_info(video_path)
        if not info:
            return
        
        duration = info.duration
        num_chunks = max(1, int(np.ceil(duration / self.chunk_duration)))
        
        logger.info(f"Processing video in {num_chunks} chunks")
        
        for chunk_idx in range(num_chunks):
            start_time = chunk_idx * self.chunk_duration
            end_time = min((chunk_idx + 1) * self.chunk_duration, duration)
            
            logger.info(f"Processing chunk {chunk_idx + 1}/{num_chunks}: {start_time:.1f}s - {end_time:.1f}s")
            
            for frame in self.sample_frames(
                video_path,
                strategy=strategy,
                start_time=start_time,
                end_time=end_time,
                progress_callback=progress_callback
            ):
                yield (chunk_idx, frame)


# Check for visualization mode from config
from app.core.config import settings
VISUALIZATION_MODE = settings.visualization_mode

# Module-level instance for PRODUCTION mode (default)
frame_sampler = AdaptiveFrameSampler(
    target_fps=1.0,
    min_fps=0.5,
    max_fps=2.0,
    motion_threshold=0.02,
    use_keyframes=True,
    chunk_duration=300,  # 5-minute chunks
    visualization_mode=VISUALIZATION_MODE  # Controlled by config
)

# Convenience function to create visualization-mode sampler
def create_visualization_sampler() -> AdaptiveFrameSampler:
    """Create a high-FPS sampler for CCTV-style visualization."""
    return AdaptiveFrameSampler(
        target_fps=15.0,  # 15 FPS for smooth tracking
        min_fps=10.0,
        max_fps=30.0,
        motion_threshold=0.02,
        use_keyframes=False,  # Don't wait for keyframes
        chunk_duration=300,
        visualization_mode=True
    )
