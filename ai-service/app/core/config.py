import os
from functools import lru_cache
from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    workers: int = 4
    environment: str = "development"
    version: str = "1.0.0"
    
    # API Security
    # Default key if .env is missing. Make sure .env has "secret-traffic-key" if that's what you use.
    api_key: str = "secret-traffic-key" 
    allowed_hosts: str = "localhost,127.0.0.1"
    allowed_origins: str = "http://localhost:5173,http://localhost:5000"
    
    # Model Configuration
    model_path: str = "./models"
    yolo_model: str = "yolov8n.pt"
    confidence_threshold: float = 0.5
    allow_mock_detection: bool = True  
    
    # Processing Configuration
    # Reduced sample rate to 5 to check more frames for better accuracy, or keep 30 for speed.
    frame_sample_rate: int = 30  
    max_video_duration: int = 3600  
    
    # CRITICAL FIX: Increased timeout to 10 minutes (600s)
    processing_timeout: int = 600   
    
    temp_dir: str = "./temp"
    upload_path: str = "./uploads"
    visualization_mode: bool = True  
    
    # Backend API (updated to port 5001)
    backend_url: str = "http://localhost:5001/api/v1"
    backend_api_key: str = "backend-internal-key"
    
    # Logging
    log_level: str = "INFO"
    log_file: str = "./logs/ai-service.log"
    
    @property
    def allowed_hosts_list(self) -> List[str]:
        return [h.strip() for h in self.allowed_hosts.split(",")]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()