"""FastAPI application entry point."""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
import os
import uvicorn
import asyncio
import signal
import sys
import traceback
import logging
from contextlib import asynccontextmanager
from app.routes import sensors, statistics, config, measurement, live
from app.websocket import websocket_manager
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global state for shutdown tracking
shutdown_event = asyncio.Event()
shutdown_reason = "unknown"
background_task = None


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    def signal_handler(signum, frame):
        """Handle shutdown signals."""
        global shutdown_reason
        signal_names = {
            signal.SIGINT: "SIGINT (Ctrl+C)",
            signal.SIGTERM: "SIGTERM",
            signal.SIGHUP: "SIGHUP",
        }
        shutdown_reason = signal_names.get(signum, f"Signal {signum}")
        logger.warning(f"⚠️  Received {shutdown_reason}, initiating graceful shutdown...")
        # Set the shutdown event instead of calling sys.exit
        shutdown_event.set()
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, signal_handler)


def handle_exception(exc_type, exc_value, exc_traceback):
    """Handle unhandled exceptions."""
    global shutdown_reason
    if issubclass(exc_type, KeyboardInterrupt):
        shutdown_reason = "KeyboardInterrupt (unhandled)"
        logger.warning("⚠️  Unhandled KeyboardInterrupt")
        return
    
    shutdown_reason = f"Unhandled exception: {exc_type.__name__}"
    logger.error(
        f"❌ Unhandled exception: {exc_type.__name__}: {exc_value}",
        exc_info=(exc_type, exc_value, exc_traceback)
    )


# Set up unhandled exception handler
sys.excepthook = handle_exception


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    global shutdown_reason, background_task
    
    # Setup signal handlers in the lifespan context
    setup_signal_handlers()
    
    # Startup: Start background tasks
    try:
        from app.background_tasks import broadcast_latest_sensor_data
        
        background_task = asyncio.create_task(broadcast_latest_sensor_data())
        logger.info("✅ Background broadcast task started")
        
        # Monitor shutdown event
        async def monitor_shutdown():
            """Monitor for shutdown signals."""
            await shutdown_event.wait()
            logger.info(f"🛑 Shutdown event triggered (reason: {shutdown_reason})")
        
        shutdown_monitor = asyncio.create_task(monitor_shutdown())
        
    except Exception as e:
        logger.error(f"❌ Error starting background tasks: {e}", exc_info=True)
        shutdown_reason = f"Startup error: {type(e).__name__}"
        raise
    
    try:
        yield
    except Exception as e:
        shutdown_reason = f"Lifespan error: {type(e).__name__}: {str(e)}"
        logger.error(f"❌ Error in lifespan context: {e}", exc_info=True)
        raise
    finally:
        # Shutdown: Cancel background tasks
        logger.info(f"🛑 Shutting down (reason: {shutdown_reason})")
        logger.info(f"📊 Shutdown context: background_task={background_task is not None}, "
                   f"task_done={background_task.done() if background_task else 'N/A'}")
        
        # Cancel background broadcast task
        if background_task:
            try:
                if not background_task.done():
                    logger.info("🔄 Cancelling background task...")
                    background_task.cancel()
                    try:
                        await asyncio.wait_for(background_task, timeout=5.0)
                        logger.info("✅ Background task cancelled successfully")
                    except asyncio.TimeoutError:
                        logger.warning("⚠️  Background task did not cancel within 5 second timeout")
                    except asyncio.CancelledError:
                        logger.info("✅ Background task cancellation confirmed")
                    except Exception as e:
                        logger.error(f"❌ Unexpected error while waiting for background task cancellation: {e}", exc_info=True)
                else:
                    logger.info("ℹ️  Background task already completed")
            except Exception as e:
                logger.error(f"❌ Error during background task shutdown: {e}", exc_info=True)
        
        if 'shutdown_monitor' in locals():
            try:
                shutdown_monitor.cancel()
                logger.debug("Shutdown monitor cancelled")
            except Exception as e:
                logger.warning(f"⚠️  Error cancelling shutdown monitor: {e}")
        
        logger.info(f"✅ Shutdown complete (final reason: {shutdown_reason})")


app = FastAPI(title="CEA Dashboard v8 API", version="1.0.0", lifespan=lifespan)

# Add exception handler for HTTP exceptions (but not all exceptions to avoid catching too much)
from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle unhandled exceptions in request handlers."""
    # Don't log or handle asyncio.CancelledError - these are normal during shutdown
    if isinstance(exc, asyncio.CancelledError):
        raise
    
    logger.error(
        f"❌ Unhandled exception in {request.method} {request.url.path}: {exc}",
        exc_info=True
    )
    # Don't set shutdown_reason for request exceptions - these shouldn't crash the server
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc) if logger.level <= logging.DEBUG else "An error occurred"
        }
    )

# Add CORS middleware (API-only; restrict to frontend origins)
default_frontend_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "http://localhost:8000",      # allow same-origin when frontend is served by backend (optional)
    "http://127.0.0.1:8000",
]
env_origins = os.environ.get("FRONTEND_ORIGINS")
allow_origins = (
    [o.strip() for o in env_origins.split(",") if o.strip()]
    if env_origins
    else default_frontend_origins
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(sensors.router)
app.include_router(statistics.router)
app.include_router(config.router)
app.include_router(measurement.router)
app.include_router(live.router)


@app.get("/")
async def read_root():
    """API-only root endpoint."""
    return {
        "message": "CEA Dashboard v8 API",
        "frontend": "served separately",
        "status": "ok",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    global background_task
    health_status = {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "background_task": "running" if background_task and not background_task.done() else "stopped"
    }
    return health_status


@app.websocket("/ws/{location}")
async def websocket_endpoint(websocket: WebSocket, location: str):
    """WebSocket endpoint for real-time sensor updates."""
    await websocket_manager.connect(websocket, location)
    try:
        while True:
            # Keep connection alive and handle incoming messages
            data = await websocket.receive_text()
            # Could handle client messages here if needed
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket, location)


# Serve favicon BEFORE mounting static files (so it takes precedence)
@app.get("/favicon.png")
async def favicon():
    """Serve favicon."""
    # Use absolute paths
    backend_dir = Path(__file__).parent.parent.absolute()
    static_path = backend_dir / "static"
    favicon_path = static_path / "favicon.png"
    logger.info(f"Favicon request: checking {favicon_path} (exists: {favicon_path.exists()})")
    if favicon_path.exists():
        logger.info(f"Serving favicon from: {favicon_path}")
        return FileResponse(str(favicon_path), media_type="image/png")
    # Fallback: try frontend public directory
    frontend_favicon = backend_dir.parent / "frontend" / "public" / "favicon.png"
    logger.info(f"Favicon fallback: checking {frontend_favicon} (exists: {frontend_favicon.exists()})")
    if frontend_favicon.exists():
        logger.info(f"Serving favicon from: {frontend_favicon}")
        return FileResponse(str(frontend_favicon), media_type="image/png")
    logger.warning(f"Favicon not found at either location")
    from fastapi.responses import Response
    return Response(status_code=404)

# Also handle /favicon.ico (browser fallback)
@app.get("/favicon.ico")
async def favicon_ico():
    """Serve favicon as .ico (browser fallback)."""
    return await favicon()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )

