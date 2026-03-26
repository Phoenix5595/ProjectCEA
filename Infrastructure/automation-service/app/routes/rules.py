"""Rules management endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.database import DatabaseManager
from app.schemas.rules import RuleCreate, RuleToggle, RuleUpdate

router = APIRouter()


# This will be overridden by main app
def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


@router.get("/api/rules")
async def get_rules(database: DatabaseManager = Depends(get_database)) -> list[dict[str, Any]]:
    """List all rules."""
    # This would query rules from database
    # For now, return empty list (full implementation would query database)
    return []


@router.post("/api/rules")
async def create_rule(
    rule: RuleCreate, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Create a new rule."""
    # This would insert into rules table
    # For now, return success (full implementation would insert into database)
    return {"id": 1, "success": True, **rule.dict()}


@router.put("/api/rules/{rule_id}")
async def update_rule(
    rule_id: int, rule: RuleUpdate, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Update a rule."""
    # This would update rules table
    # For now, return success (full implementation would update database)
    return {"id": rule_id, "success": True}


@router.delete("/api/rules/{rule_id}")
async def delete_rule(
    rule_id: int, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Delete a rule."""
    # This would delete from rules table
    # For now, return success (full implementation would delete from database)
    return {"id": rule_id, "success": True}


@router.post("/api/rules/{rule_id}/toggle")
async def toggle_rule(
    rule_id: int, toggle: RuleToggle, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Enable/disable a rule."""
    # This would update rule enabled status
    # For now, return success (full implementation would update database)
    return {"id": rule_id, "enabled": toggle.enabled, "success": True}
