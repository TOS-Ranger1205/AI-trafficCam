#!/usr/bin/env python3
"""Create a simple test video for AI service testing."""

import cv2
import numpy as np
import os

def create_test_video():
    output_path = 'data/test/sample_traffic.mp4'
    os.makedirs('data/test', exist_ok=True)
    
    width, height = 640, 480
    fps = 30
    duration = 3  # seconds

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    for frame_num in range(duration * fps):
        # Create frame with road-like background
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (40, 40, 40)  # Dark gray road
        
        # Draw lane lines
        cv2.line(frame, (width//4, 0), (width//4, height), (255, 255, 255), 2)
        cv2.line(frame, (3*width//4, 0), (3*width//4, height), (255, 255, 255), 2)
        
        # Draw a moving rectangle (simulating a car)
        car_y = 100 + (frame_num * 5) % (height - 150)
        cv2.rectangle(frame, (250, car_y), (350, car_y + 80), (0, 0, 200), -1)
        
        # Add traffic light in corner
        cv2.rectangle(frame, (20, 20), (70, 90), (50, 50, 50), -1)
        if frame_num % 60 < 30:
            cv2.circle(frame, (45, 40), 10, (0, 0, 255), -1)  # Red
        else:
            cv2.circle(frame, (45, 70), 10, (0, 255, 0), -1)  # Green
        
        out.write(frame)

    out.release()
    print(f'Created test video: {output_path}')
    print(f'Size: {os.path.getsize(output_path)} bytes')
    return output_path

if __name__ == '__main__':
    create_test_video()
