#!/usr/bin/env python3
"""
Generate a realistic test video with simulated vehicles for testing
the CCTV-style playback system.

This creates a video that looks like traffic camera footage with:
- Multiple vehicles moving across the frame
- Simulated license plates
- Different vehicle types (cars, motorcycles, trucks)
- Speed variations

Usage: python generate_traffic_video.py
Output: data/test/traffic_test.mp4
"""

import cv2
import numpy as np
from pathlib import Path
import random
import string


class SimulatedVehicle:
    """A simulated vehicle for the test video."""
    
    COLORS = {
        'car': [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (255, 255, 255), (50, 50, 50)],
        'motorcycle': [(128, 0, 0), (0, 128, 0), (0, 0, 128), (128, 128, 128)],
        'truck': [(100, 100, 100), (150, 100, 50), (80, 80, 150)],
        'bus': [(255, 200, 0), (200, 50, 50), (50, 150, 50)]
    }
    
    SIZES = {
        'car': (100, 60),
        'motorcycle': (50, 30),
        'truck': (140, 80),
        'bus': (160, 70)
    }
    
    def __init__(self, frame_width, frame_height, vehicle_type=None, lane=None):
        self.frame_width = frame_width
        self.frame_height = frame_height
        
        # Random vehicle type
        self.vehicle_type = vehicle_type or random.choice(['car', 'car', 'car', 'motorcycle', 'truck', 'bus'])
        
        # Size
        base_w, base_h = self.SIZES[self.vehicle_type]
        scale = random.uniform(0.8, 1.2)
        self.width = int(base_w * scale)
        self.height = int(base_h * scale)
        
        # Color
        self.color = random.choice(self.COLORS[self.vehicle_type])
        
        # Lane (determines x position range)
        if lane is None:
            lane = random.randint(0, 3)
        self.lane = lane
        lane_width = frame_width // 4
        self.x = lane * lane_width + lane_width // 2 + random.randint(-30, 30)
        
        # Start from top or bottom
        self.direction = random.choice(['down', 'up'])
        if self.direction == 'down':
            self.y = -self.height
            self.speed = random.uniform(3, 8)  # pixels per frame
        else:
            self.y = frame_height + self.height
            self.speed = -random.uniform(3, 8)
        
        # Horizontal drift
        self.x_speed = random.uniform(-0.5, 0.5)
        
        # Generate plate
        self.plate = self._generate_plate()
        
        # Track if still visible
        self.active = True
        
    def _generate_plate(self):
        """Generate an Indian-style license plate."""
        states = ['KA', 'MH', 'TN', 'DL', 'GJ', 'RJ', 'UP', 'MP', 'AP']
        state = random.choice(states)
        num1 = str(random.randint(1, 99)).zfill(2)
        letters = ''.join(random.choices(string.ascii_uppercase, k=2))
        num2 = str(random.randint(1, 9999)).zfill(4)
        return f"{state}{num1}{letters}{num2}"
    
    def update(self):
        """Update vehicle position."""
        self.y += self.speed
        self.x += self.x_speed
        
        # Keep in bounds horizontally
        self.x = max(self.width // 2, min(self.frame_width - self.width // 2, self.x))
        
        # Check if still visible
        if self.direction == 'down' and self.y > self.frame_height + self.height:
            self.active = False
        elif self.direction == 'up' and self.y < -self.height:
            self.active = False
    
    def draw(self, frame):
        """Draw vehicle on frame."""
        x1 = int(self.x - self.width // 2)
        y1 = int(self.y - self.height // 2)
        x2 = x1 + self.width
        y2 = y1 + self.height
        
        # Vehicle body
        cv2.rectangle(frame, (x1, y1), (x2, y2), self.color, -1)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 0), 2)
        
        # Windows (for cars/buses)
        if self.vehicle_type in ['car', 'bus']:
            win_h = self.height // 3
            win_y = y1 + 5
            cv2.rectangle(frame, (x1 + 5, win_y), (x2 - 5, win_y + win_h), (200, 200, 200), -1)
        
        # License plate (bottom of vehicle)
        plate_w = min(70, self.width - 10)
        plate_h = 15
        plate_x = int(self.x - plate_w // 2)
        plate_y = y2 - plate_h - 5
        
        # White plate background
        cv2.rectangle(frame, (plate_x, plate_y), (plate_x + plate_w, plate_y + plate_h), (255, 255, 255), -1)
        cv2.rectangle(frame, (plate_x, plate_y), (plate_x + plate_w, plate_y + plate_h), (0, 0, 0), 1)
        
        # Plate text
        font_scale = 0.35
        cv2.putText(frame, self.plate, (plate_x + 2, plate_y + 11), 
                   cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), 1)
    
    def get_bbox(self):
        """Get bounding box."""
        x1 = int(self.x - self.width // 2)
        y1 = int(self.y - self.height // 2)
        return (x1, y1, x1 + self.width, y1 + self.height)
    
    def get_speed_kmh(self, fps=30):
        """Estimate speed in km/h (simulated)."""
        # Convert pixel speed to simulated km/h
        # Assume 10 pixels = 1 meter, then convert m/s to km/h
        pixels_per_meter = 10
        meters_per_second = abs(self.speed * fps) / pixels_per_meter
        return meters_per_second * 3.6


def draw_road(frame, width, height):
    """Draw road background with lane markings."""
    # Gray road
    cv2.rectangle(frame, (0, 0), (width, height), (80, 80, 80), -1)
    
    # Lane dividers
    lane_width = width // 4
    for i in range(1, 4):
        x = i * lane_width
        for y in range(0, height, 40):
            cv2.line(frame, (x, y), (x, y + 20), (255, 255, 255), 3)
    
    # Road edges
    cv2.line(frame, (10, 0), (10, height), (255, 255, 255), 5)
    cv2.line(frame, (width - 10, 0), (width - 10, height), (255, 255, 255), 5)


def draw_hud(frame, timestamp, frame_num, vehicle_count, width, height):
    """Draw HUD overlay like a real traffic camera."""
    # Semi-transparent overlay bar at top
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (width, 40), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
    
    # Timestamp
    time_str = f"{int(timestamp // 60):02d}:{int(timestamp % 60):02d}.{int((timestamp % 1) * 100):02d}"
    cv2.putText(frame, f"CAM-001 | {time_str} | Frame: {frame_num}", 
               (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    
    # Vehicle count
    cv2.putText(frame, f"Vehicles: {vehicle_count}", 
               (width - 150, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)


def generate_traffic_video(output_path: str, duration: int = 30, fps: int = 30):
    """
    Generate a test traffic video.
    
    Args:
        output_path: Path to output video file
        duration: Duration in seconds
        fps: Frames per second
    """
    width, height = 1280, 720
    total_frames = duration * fps
    
    # Create output directory
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    # Video writer
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))
    
    vehicles = []
    vehicle_id_counter = 0
    
    print(f"Generating {duration}s traffic video at {fps} FPS...")
    
    for frame_num in range(total_frames):
        # Create frame with road
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        draw_road(frame, width, height)
        
        # Randomly spawn new vehicles
        if random.random() < 0.03:  # ~1 vehicle per second on average
            vehicle = SimulatedVehicle(width, height)
            vehicle.id = vehicle_id_counter
            vehicle_id_counter += 1
            vehicles.append(vehicle)
        
        # Update and draw vehicles
        active_vehicles = []
        for vehicle in vehicles:
            vehicle.update()
            if vehicle.active:
                vehicle.draw(frame)
                active_vehicles.append(vehicle)
        vehicles = active_vehicles
        
        # Draw HUD
        timestamp = frame_num / fps
        draw_hud(frame, timestamp, frame_num, len(vehicles), width, height)
        
        # Write frame
        out.write(frame)
        
        # Progress
        if frame_num % (fps * 5) == 0:
            print(f"  Progress: {frame_num / total_frames * 100:.0f}%")
    
    out.release()
    print(f"Video saved to: {output_path}")
    
    # Also save metadata
    metadata = {
        "duration": duration,
        "fps": fps,
        "width": width,
        "height": height,
        "frames": total_frames,
        "description": "Simulated traffic camera footage for testing"
    }
    
    return output_path, metadata


if __name__ == "__main__":
    output = "data/test/traffic_test.mp4"
    generate_traffic_video(output, duration=30, fps=30)
    print(f"\nTest video created: {output}")
    print("You can now upload this video to test the CCTV playback system.")
