"""
AI TrafficCam - Intelligent Traffic Violation Detection Service

FastAPI application for traffic video analysis and violation detection.
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import logger
from app.api import router as api_router
from app.services import object_detector, license_plate_ocr
from app.services.dynamic_rules import rule_fetcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("=" * 60)
    logger.info("AI TrafficCam Service Starting...")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"Version: {settings.version}")
    logger.info("=" * 60)
    
    # Pre-load models
    logger.info("Loading AI models...")
    
    yolo_loaded = object_detector.load_model()
    if yolo_loaded:
        logger.info("✓ YOLO model loaded successfully")
    else:
        logger.warning("✗ YOLO model not loaded - running in demo mode")
    
    ocr_loaded = license_plate_ocr.load_reader()
    if ocr_loaded:
        logger.info("✓ EasyOCR reader loaded successfully")
    else:
        logger.warning("✗ EasyOCR reader not loaded")
    
    # Load violation rules from database
    logger.info("Loading violation rules from database...")
    try:
        rules = await rule_fetcher.get_active_rules()
        logger.info(f"✓ Loaded {len(rules)} violation rules from database")
    except Exception as e:
        logger.warning(f"✗ Failed to load violation rules: {e}")
        logger.warning("AI service will use fallback default rules")
    
    logger.info("=" * 60)
    logger.info("AI TrafficCam Service Ready!")
    logger.info(f"API Docs: http://localhost:{settings.port}/docs")
    logger.info("=" * 60)
    
    yield
    
    # Shutdown
    logger.info("AI TrafficCam Service Shutting Down...")
    
    # Cleanup rule fetcher
    try:
        await rule_fetcher.close()
        logger.info("✓ Rule fetcher cleaned up")
    except Exception as e:
        logger.warning(f"✗ Error cleaning up rule fetcher: {e}")
        
    logger.info("AI TrafficCam Service Stopped")


# Create FastAPI application
app = FastAPI(
    title="AI TrafficCam Service",
    description="""
    Intelligent Traffic Violation Detection API
    
    This service provides AI-powered traffic violation detection capabilities:
    
    * **Video Analysis**: Process traffic camera footage for violations
    * **Frame Analysis**: Analyze single frames for vehicles and violations
    * **License Plate OCR**: Extract license plate text from images
    * **Dispute Analysis**: AI-powered dispute resolution assistance
    
    ## Authentication
    
    All endpoints (except health checks) require API key authentication.
    Pass the API key in the `X-API-Key` header.
    
    ## Rate Limiting
    
    API calls are rate-limited. Check response headers for remaining quota.
    """,
    version=settings.version,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
    lifespan=lifespan
)


# ============== Middleware ==============

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gzip compression
app.add_middleware(GZipMiddleware, minimum_size=1000)


# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Add processing time to response headers."""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log incoming requests."""
    # Skip health check logs
    if request.url.path not in ["/health", "/ready", "/favicon.ico"]:
        logger.info(f"Request: {request.method} {request.url.path}")
    
    response = await call_next(request)
    
    # Log response status for non-success
    if response.status_code >= 400:
        logger.warning(f"Response: {response.status_code} for {request.url.path}")
    
    return response


# ============== Exception Handlers ==============

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc) if settings.environment != "production" else None,
            "code": "INTERNAL_ERROR"
        }
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle value errors."""
    return JSONResponse(
        status_code=400,
        content={
            "error": "Invalid input",
            "detail": str(exc),
            "code": "VALIDATION_ERROR"
        }
    )


# ============== Routes ==============

# Include API router
app.include_router(api_router, prefix="/api/v1")


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with service information."""
    return {
        "service": "AI TrafficCam",
        "version": settings.version,
        "description": "Intelligent Traffic Violation Detection Service",
        "docs": "/docs",
        "health": "/api/v1/health"
    }


# Health check at root level too
@app.get("/health", tags=["Health"])
async def health():
    """Root-level health check."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.environment == "development",
        log_level="info"
    )
