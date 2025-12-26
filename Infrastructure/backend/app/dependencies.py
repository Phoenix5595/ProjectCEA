"""Shared dependencies for FastAPI routes."""
from app.database import DatabaseManager
from app.config import ConfigLoader
from typing import Optional

# Global instances (lazy initialization)
_db_manager: Optional[DatabaseManager] = None
_config_loader: Optional[ConfigLoader] = None

def get_db_manager() -> DatabaseManager:
    """Get or create database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager

def get_config_loader() -> ConfigLoader:
    """Get or create config loader instance."""
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
    return _config_loader

