# AI TrafficCam - Copilot Instructions

## Project Overview
Intelligent traffic violation detection system with AI-powered video analysis. Three-tier architecture: React frontend (Vite), Node.js backend (Express + Sequelize), Python AI service (FastAPI + YOLOv8).

## Architecture & Data Flow

### Service Boundaries
- **Frontend** (port 5173): React 18 + Vite, TailStack Query for server state, role-based routing
- **Backend** (port 5001): Express.js REST API, Sequelize ORM, BullMQ job queue (optional Redis)
- **AI Service** (port 8000): FastAPI, YOLOv8 object detection, EasyOCR license plate recognition
- **Database**: PostgreSQL (Neon Cloud hosted), connection pooling (max=20, min=5)

### Critical Async Pattern: Video Processing Pipeline
```
1. Police uploads video → Backend saves metadata (TrafficVideo table, status='pending')
2. Backend adds job to BullMQ queue (if Redis enabled) OR processes synchronously
3. Worker calls AI Service: POST /api/v1/process-video/v2
4. AI Service: frame sampling → YOLO detection → violation logic → OCR → evidence generation
5. Backend receives violations → creates Violation records (status='pending_review')
6. Police manually reviews & approves → status changes to 'issued' (challan generated)
```

**Key Insight**: Two-stage workflow - AI detection then human approval. Never auto-issue challans.

## Authentication & Authorization

### JWT Flow (backend/src/middleware/auth.js)
- `authenticate` middleware: Accepts token from `Authorization: Bearer <token>` OR `?token=` query param (for video streaming)
- `authorize(...roles)` middleware: RBAC enforcement
- Token refresh: 401 errors trigger automatic refresh via frontend interceptor (frontend/src/services/api.js)
- User roles: `citizen` (default), `police`, `admin` - stored in User.role, checked on every protected route

### Route Protection Example
```javascript
router.post('/violations/:id/review', 
  authenticate,                     // Verify JWT
  authorize('police', 'admin'),     // Only police/admin
  violationController.reviewViolation
);
```

## Database Models & Relationships (backend/src/models/)

### Core Entities
- **User** → hasMany **Vehicle** (userId FK)
- **User** (police) → hasMany **TrafficVideo** (uploadedBy FK)
- **TrafficVideo** → hasMany **Violation** (videoId FK)
- **Violation** → belongsTo **Vehicle** (vehicleId FK, nullable if plate unrecognized)
- **Violation** → hasMany **Dispute** (violationId FK)
- **Violation** → hasOne **Payment** (violationId FK)

### Status Enums (Critical)
- **Violation.status**: `pending_review` → `issued` (after police approval) → `paid`/`disputed`/`dismissed`
- **TrafficVideo.processingStatus**: `pending` → `processing` → `completed`/`failed`
- **Dispute.status**: `pending` → `under_review` → `approved`/`rejected`

## AI Service Integration

### Production Pipeline (ai-service/app/services/production_pipeline.py)
- **Timeout**: 600 seconds (10 min) to handle slow machines
- **Frame Sampling**: Adaptive based on video duration (frame_sampler.py)
- **Tracking**: ByteTrack for multi-object tracking across frames
- **Violation Rules**: Fetched dynamically from backend's ViolationRule table
- **OCR Handling**: Wrapped in try-except, failures don't break pipeline (partial data acceptable)

### Evidence Generation Pattern
Each violation creates:
1. Annotated frame (bounding boxes drawn)
2. 5-second video clip (if possible)
3. Cloudinary upload (if enabled via SystemConfig.enableCloudinary)
4. URLs stored in Violation.evidenceFramePath, evidenceVideoClipPath

## Frontend State Management

### TanStack Query Patterns (frontend/src/pages/)
```javascript
// Data fetching
const { data, isLoading, error } = useQuery({
  queryKey: ['violations', filters],
  queryFn: () => violationAPI.getMyViolations(filters)
})

// Mutations with cache invalidation
const mutation = useMutation({
  mutationFn: violationAPI.payViolation,
  onSuccess: () => {
    queryClient.invalidateQueries(['violations'])
    queryClient.invalidateQueries(['dashboard'])
  }
})
```

### Auth Context (frontend/src/contexts/AuthContext.jsx)
- Global user state, `isAuthenticated`, `isLoading`
- Syncs with localStorage: `accessToken`, `refreshToken`, `user` JSON
- Auto-redirects on 401 after failed token refresh

## Development Workflows

### Local Development (without Docker)
```bash
# Terminal 1: Backend
cd backend
npm install
npm run dev  # Nodemon on port 5001

# Terminal 2: AI Service
cd ai-service
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000

# Terminal 3: Frontend
cd frontend
npm install
npm run dev  # Vite on port 5173
```

### Database Migrations
- Manual: `npm run migrate` (backend/src/config/migrate.js)
- Sequelize auto-sync: `syncDatabase({ alter: true })` in production mode
- Seeding: `npm run seed` (creates test users, violation rules)

### Redis Optional
- If `ENABLE_REDIS=true` → BullMQ queue for video processing
- If Redis unavailable → Falls back to synchronous processing (videoQueue.js line 94-118)

## Project-Specific Conventions

### Error Handling
- Backend: Custom `AppError` class with status codes (middleware/errorHandler.js)
- Always wrap async route handlers: `asyncHandler(async (req, res) => { ... })`
- Frontend: Toast notifications via `react-hot-toast`, error objects from API interceptors

### File Uploads (backend/src/middleware/fileUpload.js)
- Videos: 500MB max, mp4/avi/mov/webm
- Images: 5MB max, jpg/jpeg/png
- Cloudinary integration: Two-stage (local disk → optional cloud upload)
- CORS headers for video streaming: `Cross-Origin-Resource-Policy: cross-origin`, `exposedHeaders: ['Content-Range', 'Accept-Ranges']`

### Logging (Winston + custom logger)
- Backend: `logger.info()`, `logger.error()` (utils/logger.js)
- AI Service: `logger.info()` via Loguru (app/core/logging.py)
- Audit trails: AuditLog table for evidence access, user actions (services/auditService.js)

### Violation Type Normalization (backend/src/queues/videoQueue.js)
```javascript
// AI returns various formats ('speeding', 'helmet_violation', 'red_light_violation')
// Backend normalizes to DB enum: 'speed_violation', 'no_helmet', 'red_light'
// CRITICAL: Unknown types persist as-is, NOT defaulted to 'other'
```

## Testing & Debugging

### Health Checks
```bash
curl http://localhost:5001/api/v1/health  # Backend
curl http://localhost:8000/health         # AI Service
```

### Common Issues
1. **Video upload fails**: Check `MIN_VIDEO_SIZE` (1MB), ffprobe availability (videoService.js line 49-98)
2. **AI timeout**: Increase `GLOBAL_TIMEOUT_SECONDS` (production_pipeline.py line 34)
3. **Token expired loop**: Clear localStorage if refresh token invalid
4. **Violation not showing**: Likely missing vehicle registration (Vehicle.registrationNumber must match plate)

## Key Files Reference
- **Data models**: `backend/src/models/index.js` (all associations)
- **API routes**: `backend/src/routes/*.js` (grouped by resource)
- **Video processing**: `backend/src/queues/videoQueue.js`, `ai-service/app/services/production_pipeline.py`
- **Auth logic**: `backend/src/middleware/auth.js`, `frontend/src/contexts/AuthContext.jsx`
- **AI core**: `ai-service/app/services/detector.py` (YOLO), `plate_ocr.py` (EasyOCR)

## Configuration Files
- Backend: `.env` + `backend/src/config/index.js` (centralizes all env vars)
- AI Service: `.env` + `ai-service/app/core/config.py` (Pydantic settings)
- Frontend: `.env` (VITE_API_URL)
- Docker: `docker-compose.yml` (full stack orchestration)

## Non-Obvious Patterns
- **Video streaming requires query token**: HTML5 `<video>` can't send auth headers, use `?token=${accessToken}` (frontend video players)
- **Sequelize sync on startup**: Auto-creates tables, use `alter: true` to migrate safely (database.js line 61)
- **Dynamic violation rules**: AI service fetches from backend's ViolationRule table on startup (main.py line 48-54)
- **Cloudinary conditional**: Enabled per-request via SystemConfig, not env var (getStorageConfig helper)
