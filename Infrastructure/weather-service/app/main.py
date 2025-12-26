"""Main FastAPI application for weather service."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.weather_client import WeatherClient
from app.background_tasks import BackgroundTasks

from app.routes import status, weather

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global instances (will be initialized in lifespan)
config: ConfigLoader = None
database: DatabaseManager = None
weather_client: WeatherClient = None
background_tasks: BackgroundTasks = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global config, database, weather_client, background_tasks
    
    # Startup
    logger.info("Starting weather service...")
    
    try:
        # 1. Load configuration
        logger.info("Loading configuration...")
        config = ConfigLoader()
        
        # 2. Initialize database
        logger.info("Initializing database...")
        db_config = config.get_database_config()
        database = DatabaseManager(db_config)
        await database.initialize()
        
        # 3. Initialize weather client
        logger.info("Initializing weather client...")
        weather_config = config.get_weather_config()
        weather_client = WeatherClient(
            api_url=weather_config['api_url'],
            station_icao=weather_config['station_icao']
        )
        
        # 4. Initialize background tasks
        logger.info("Initializing background tasks...")
        background_tasks = BackgroundTasks(config, database, weather_client)
        await background_tasks.start()
        
        logger.info("Weather service started successfully")
        
        yield
        
    except Exception as e:
        logger.error(f"Failed to start weather service: {e}", exc_info=True)
        raise
    finally:
        # Shutdown
        logger.info("Shutting down weather service...")
        
        if background_tasks:
            await background_tasks.stop()
        
        if weather_client:
            await weather_client.close()
        
        if database:
            await database.close()
        
        logger.info("Weather service stopped")


# Create FastAPI app
app = FastAPI(
    title="Weather Service",
    description="Weather data service for CEA system - YUL Airport",
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


def get_weather_client() -> WeatherClient:
    """Get weather client."""
    return weather_client


# Override route dependencies
app.dependency_overrides[status.get_database] = get_database
app.dependency_overrides[weather.get_database] = get_database
app.dependency_overrides[weather.get_weather_client] = get_weather_client

# Register routes
app.include_router(status.router)
app.include_router(weather.router, prefix="/weather", tags=["weather"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "Weather Service",
        "version": "1.0.0",
        "status": "running"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)












