"""Application middleware and static file handling."""
from shared.logging import get_logger
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

logger = get_logger(__name__)


def setup_cors(app: FastAPI) -> None:
    """Setup CORS middleware."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def setup_static_files(app: FastAPI) -> None:
    """Setup static file serving for frontend."""
    # Frontend is in Infrastructure/frontend/dist (sibling to automation-service)
    # Try multiple path resolution methods
    _base_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    frontend_dist_path = os.path.abspath(os.path.join(_base_path, "frontend", "dist"))

    logger.info(f"Frontend dist path: {frontend_dist_path}, exists: {os.path.exists(frontend_dist_path)}")

    if os.path.exists(frontend_dist_path):
        # Mount static assets (JS, CSS, images) - these are in the assets subdirectory
        app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist_path, "assets")), name="static-assets")

        # Serve logo.png and favicon routes - must be registered before catch-all route
        logo_path = os.path.join(frontend_dist_path, "logo.png")
        # Use absolute path to ensure it works
        logo_path = os.path.abspath(logo_path)
        logger.info(f"Registering favicon routes, path: {logo_path}, exists: {os.path.exists(logo_path)}")

        if os.path.exists(logo_path):
            @app.api_route("/logo.png", methods=["GET", "HEAD"], name="logo")
            async def serve_logo():
                """Serve logo.png favicon."""
                logger.info(f"Serving logo from: {logo_path}")
                return FileResponse(logo_path, media_type="image/png")

            @app.api_route("/favicon.ico", methods=["GET", "HEAD"], name="favicon_ico")
            async def serve_favicon_ico():
                """Serve favicon.ico (browser default request)."""
                # Try to serve actual favicon.ico file first, fallback to logo.png
                favicon_path = os.path.join(frontend_dist_path, "favicon.ico")
                favicon_path = os.path.abspath(favicon_path)
                if os.path.exists(favicon_path):
                    logger.info(f"Serving favicon.ico from: {favicon_path}")
                    response = FileResponse(favicon_path, media_type="image/x-icon")
                else:
                    logger.info(f"Serving favicon.ico from logo: {logo_path}")
                    response = FileResponse(logo_path, media_type="image/x-icon")
                # Add cache headers to help with browser caching
                response.headers["Cache-Control"] = "public, max-age=86400"
                return response

            @app.api_route("/favicon.png", methods=["GET", "HEAD"], name="favicon_png")
            async def serve_favicon_png():
                """Serve favicon.png."""
                logger.info(f"Serving favicon.png from: {logo_path}")
                return FileResponse(logo_path, media_type="image/png")

        else:
            logger.warning(f"Logo file not found at: {logo_path}")

        # Serve index.html for root and all non-API routes (SPA fallback)
        @app.get("/")
        async def serve_frontend():
            """Serve frontend index.html."""
            index_path = os.path.join(frontend_dist_path, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
            return {
                "service": "Automation Service",
                "version": "1.0.0",
                "status": "running",
                "note": "Frontend not built. Run 'npm run build' in Infrastructure/frontend"
            }

        # SPA fallback: serve index.html for all routes that don't match API routes
        # This must be registered last so API routes take precedence
        @app.get("/{path:path}")
        async def serve_frontend_routes(path: str):
            """Serve frontend routes (SPA fallback)."""
            # Serve favicon files immediately if requested (fallback)
            if path in ["logo.png", "favicon.ico", "favicon.png"]:
                logo_file = os.path.join(frontend_dist_path, "logo.png")
                if os.path.exists(logo_file):
                    # Serve PNG file even for .ico requests (browsers handle this fine)
                    return FileResponse(logo_file, media_type="image/png")

            # Don't serve frontend for API routes, WebSocket, or FastAPI docs
            # FastAPI should match API routes first, but this is a safety check
            if path.startswith("api/") or path.startswith("ws") or path in ["docs", "openapi.json", "redoc"]:
                from fastapi.responses import JSONResponse
                return JSONResponse({"error": "Not found"}, status_code=404)

            # Serve other static files from dist root (favicon, etc.)
            if path and '.' in path and not path.startswith('api/') and not path.startswith('ws'):
                static_file_path = os.path.abspath(os.path.join(frontend_dist_path, path))
                if os.path.exists(static_file_path) and os.path.isfile(static_file_path):
                    # Determine media type based on extension
                    if path.endswith('.png'):
                        return FileResponse(static_file_path, media_type="image/png")
                    elif path.endswith('.ico'):
                        return FileResponse(static_file_path, media_type="image/x-icon")
                    elif path.endswith('.svg'):
                        return FileResponse(static_file_path, media_type="image/svg+xml")
                    elif path.endswith('.jpg') or path.endswith('.jpeg'):
                        return FileResponse(static_file_path, media_type="image/jpeg")
                    else:
                        return FileResponse(static_file_path)

            # Serve index.html for SPA routes
            index_path = os.path.join(frontend_dist_path, "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
            return {"error": "Frontend not found"}

    else:
        @app.get("/")
        async def root():
            """Root endpoint."""
            return {
                "service": "Automation Service",
                "version": "1.0.0",
                "status": "running",
                "note": "Frontend not built. Run 'npm run build' in Infrastructure/frontend"
            }