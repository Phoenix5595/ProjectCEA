"""Main FastAPI application for soil sensor service."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.redis_client import RedisClient
from app.background_tasks import BackgroundTasks

from app.routes import status, sensors

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global instances (will be initialized in lifespan)
config: ConfigLoader = None
database: DatabaseManager = None
redis_client: RedisClient = None
background_tasks: BackgroundTasks = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global config, database, redis_client, background_tasks
    
    # Startup
    logger.info("Starting soil sensor service...")
    
    try:
        # 1. Load configuration
        logger.info("Loading configuration...")
        config = ConfigLoader()
        
        # 2. Initialize database
        logger.info("Initializing database...")
        database = DatabaseManager()
        await database.initialize()
        
        # 3. Initialize Redis client
        logger.info("Initializing Redis client...")
        redis_client = RedisClient()
        await redis_client.connect()
        
        # 4. Initialize background tasks
        logger.info("Initializing background tasks...")
        background_tasks = BackgroundTasks(config, database, redis_client)
        await background_tasks.start()
        
        logger.info("Soil sensor service started successfully")
        
        yield
        
    except Exception as e:
        logger.error(f"Failed to start soil sensor service: {e}", exc_info=True)
        raise
    finally:
        # Shutdown
        logger.info("Shutting down soil sensor service...")
        
        if background_tasks:
            await background_tasks.stop()
        
        if redis_client:
            await redis_client.close()
        
        if database:
            await database.close()
        
        logger.info("Soil sensor service stopped")


# Create FastAPI app
app = FastAPI(
    title="Soil Sensor Service",
    description="RS485 soil sensor monitoring service for CEA system",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Dependency injection functions
def get_config() -> ConfigLoader:
    """Get config loader."""
    return config


def get_database() -> DatabaseManager:
    """Get database manager."""
    return database


def get_redis_client() -> RedisClient:
    """Get Redis client."""
    return redis_client


# Override route dependencies
app.dependency_overrides[status.get_database] = get_database
app.dependency_overrides[sensors.get_database] = get_database

# Register routes
app.include_router(status.router)
app.include_router(sensors.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Soil Sensor Service",
        "version": "1.0.0",
        "status": "running"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)

