"""
Config utility for AI service to read system configurations from backend API
"""
import requests
import os
from typing import Optional, Dict, Any
from functools import lru_cache
import logging

logger = logging.getLogger(__name__)

# Backend API URL (can be overridden by environment variable)
BACKEND_URL = os.getenv('BACKEND_URL', 'http://backend:5001')
AI_CONFIG_ENDPOINT = f'{BACKEND_URL}/api/v1/admin/config/ai'

# Cache TTL in seconds
CACHE_TTL = 60


class AIConfigManager:
    """Manages AI configuration fetching and caching"""
    
    def __init__(self):
        self._config_cache: Optional[Dict[str, Any]] = None
        self._cache_timestamp = 0
        
    def get_config(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Fetch AI configuration from backend
        
        Args:
            force_refresh: Force fetch from API even if cache is valid
            
        Returns:
            Dictionary with AI configuration values
        """
        import time
        
        current_time = time.time()
        
        # Return cached config if valid
        if not force_refresh and self._config_cache and (current_time - self._cache_timestamp) < CACHE_TTL:
            return self._config_cache
            
        try:
            response = requests.get(AI_CONFIG_ENDPOINT, timeout=5)
            response.raise_for_status()
            
            data = response.json()
            if data.get('success'):
                self._config_cache = data.get('data', {})
                self._cache_timestamp = current_time
                logger.info("AI config fetched successfully from backend")
                return self._config_cache
            else:
                logger.error(f"Failed to fetch AI config: {data}")
                return self._get_default_config()
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching AI config from backend: {e}")
            # Return cached config if available, otherwise defaults
            return self._config_cache if self._config_cache else self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Return safe default configuration values"""
        return {
            'aiEnabled': True,
            'minDetectionConfidence': 0.70,
            'detectionConfidenceThreshold': 0.75,
            'speedViolationThreshold': 60,
            'redLightGraceSeconds': 2.0,
            'realTimeProcessing': False,
            'maxConcurrentProcessing': 5,
            'plateOcrEnabled': True,
            'autoGenerateChallan': False,
        }
    
    def get_value(self, key: str, default: Any = None) -> Any:
        """Get a specific config value by key"""
        config = self.get_config()
        return config.get(key, default)
    
    def clear_cache(self):
        """Clear the config cache"""
        self._config_cache = None
        self._cache_timestamp = 0
        logger.info("AI config cache cleared")


# Global instance
_config_manager = AIConfigManager()


def get_ai_config(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Get AI configuration values
    
    Args:
        force_refresh: Force refresh from backend API
        
    Returns:
        Dictionary with AI configuration
    """
    return _config_manager.get_config(force_refresh)


def get_config_value(key: str, default: Any = None) -> Any:
    """
    Get a specific config value
    
    Args:
        key: Configuration key
        default: Default value if key not found
        
    Returns:
        Configuration value
    """
    return _config_manager.get_value(key, default)


def clear_config_cache():
    """Clear the configuration cache"""
    _config_manager.clear_cache()


# Convenience getters for specific config values
def get_min_detection_confidence() -> float:
    """Get minimum detection confidence threshold (0.0 - 1.0)"""
    return get_config_value('minDetectionConfidence', 0.70)


def get_speed_violation_threshold() -> int:
    """Get speed violation threshold in km/h"""
    return get_config_value('speedViolationThreshold', 60)


def get_red_light_grace_seconds() -> float:
    """Get red light grace period in seconds"""
    return get_config_value('redLightGraceSeconds', 2.0)


def is_ai_enabled() -> bool:
    """Check if AI detection is enabled"""
    return get_config_value('aiEnabled', True)


def is_plate_ocr_enabled() -> bool:
    """Check if plate OCR is enabled"""
    return get_config_value('plateOcrEnabled', True)


if __name__ == '__main__':
    # Test the config manager
    logging.basicConfig(level=logging.INFO)
    
    print("Fetching AI config...")
    config = get_ai_config()
    print(f"AI Config: {config}")
    
    print(f"\nMin Detection Confidence: {get_min_detection_confidence()}")
    print(f"Speed Violation Threshold: {get_speed_violation_threshold()} km/h")
    print(f"Red Light Grace Seconds: {get_red_light_grace_seconds()}s")
    print(f"AI Enabled: {is_ai_enabled()}")
    print(f"Plate OCR Enabled: {is_plate_ocr_enabled()}")
