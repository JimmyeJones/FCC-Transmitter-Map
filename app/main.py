"""FastAPI application entry point."""

import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings, setup_logging
from app.database import engine
from app.grid_cache import ensure_grid_cache
from app.routes import api, web
from app.scheduler import get_scheduler

# Initialize logging
setup_logging()

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title=settings.app_title,
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)

# Add CORS middleware if needed
if settings.allowed_hosts:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_hosts,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log HTTP requests and responses."""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    
    # Log only non-static requests to reduce noise
    if not request.url.path.startswith("/static"):
        logger.debug(
            f"{request.method} {request.url.path} - Status: {response.status_code} - Duration: {process_time:.3f}s"
        )
    
    return response

# Static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Templates
templates_dir = Path(__file__).parent / "templates"
templates_dir.mkdir(parents=True, exist_ok=True)
templates = Jinja2Templates(directory=str(templates_dir))

# Include routers
app.include_router(api.router, prefix="/api", tags=["api"])
app.include_router(web.router, tags=["web"])


# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize scheduler and other resources on startup."""
    logger.info("FCC Radio License Map starting up...")
    
    try:
        scheduler = get_scheduler()
        await scheduler.start()
    except Exception as e:
        logger.error(f"Failed to start scheduler: {e}", exc_info=True)
        # Don't fail startup if scheduler fails - app can still work

    # Pre-compute the grid cache table so map queries are instant
    try:
        await ensure_grid_cache(engine)
    except Exception as e:
        logger.error(f"Failed to populate grid cache: {e}", exc_info=True)


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown."""
    logger.info("FCC Radio License Map shutting down...")
    
    try:
        scheduler = get_scheduler()
        await scheduler.stop()
    except Exception as e:
        logger.error(f"Error during shutdown: {e}", exc_info=True)
