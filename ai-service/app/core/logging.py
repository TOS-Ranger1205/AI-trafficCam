import sys
import os
from loguru import logger
from app.core.config import settings

# Ensure log directory exists
log_dir = os.path.dirname(settings.log_file)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir, exist_ok=True)

# Remove default logger
logger.remove()

# Console logging format
console_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

# File logging format
file_format = (
    "{time:YYYY-MM-DD HH:mm:ss} | "
    "{level: <8} | "
    "{name}:{function}:{line} | "
    "{message}"
)

# Add console handler
logger.add(
    sys.stdout,
    format=console_format,
    level=settings.log_level,
    colorize=True
)

# Add file handler
logger.add(
    settings.log_file,
    format=file_format,
    level=settings.log_level,
    rotation="10 MB",
    retention="7 days",
    compression="zip"
)

# Export logger
__all__ = ["logger"]
