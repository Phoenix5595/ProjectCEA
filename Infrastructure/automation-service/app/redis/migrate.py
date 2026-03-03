#!/usr/bin/env python3
"""
Redis migration utility for ProjectCEA.

Features:
- Scan existing keys and map to new schema using MIGRATION_MAP / NEW_KEY_PATTERNS
- Dry-run report (no changes) and actual migrate with safety checks
- Backup before migration and rollback support
- Logging for all operations

Dependencies (assumed to exist in the repo):
- app/redis/schema.py -> MIGRATION_MAP, NEW_KEY_PATTERNS
- app/redis/ttl.py -> get_ttl_by_key_type
"""

import argparse
from datetime import datetime
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("RedisMigration")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    ch.setFormatter(formatter)
    logger.addHandler(ch)


class RedisMigration:
    def __init__(
        self, redis_client: Any | None = None, backup_path: str | None = None
    ) -> None:
        # Lazy Redis client; prefer provided client, otherwise create a simple one
        self.redis = redis_client or self._default_redis_client()
        # Backup storage path
        self.backup_path = backup_path or os.path.join(
            os.path.dirname(__file__), "migrate_backup.json"
        )
        self.scan_results: list[dict[str, Any]] = []
        self._migration_map = self._load_migration_map()
        logger.info("RedisMigration initialized (backup=%s)", self.backup_path)

    # ========== Redis helper ==========
    def _default_redis_client(self) -> Any | None:
        try:
            import redis  # type: ignore

            return redis.Redis(host="localhost", port=6379, decode_responses=True)
        except Exception as e:
            logger.warning("Redis client not available: %s", e)
            return None

    # ========== Migration map loading (old→new) ==========
    def _load_migration_map(self):
        mm = None
        # Try relative import first (valid Python module path in this repo)
        try:
            from .schema import MIGRATION_MAP  # type: ignore

            mm = MIGRATION_MAP  # type: ignore
        except Exception:
            # Fallback: default to empty map if import fails
            mm = {}
        logger.debug("Migration map loaded: %s", type(mm))
        return mm

    # ========== Backup/restore ==============
    def _backup_plan(self, plan: list[dict[str, Any]]) -> None:
        payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "plan": plan,
        }
        try:
            with open(self.backup_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            logger.info("Backup saved to %s (%d entries)", self.backup_path, len(plan))
        except Exception as e:
            logger.exception("Failed to write backup: %s", e)

    def _load_backup(self) -> dict[str, Any] | None:
        if not os.path.exists(self.backup_path):
            return None
        try:
            with open(self.backup_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.exception("Failed to load backup: %s", e)
            return None

    def _collect_patterns_from_migration_map(self, mm) -> list[str]:
        patterns: list[str] = []
        if isinstance(mm, dict):
            patterns = list(mm.keys())
        elif isinstance(mm, (list, tuple)):
            for entry in mm:
                if isinstance(entry, dict):
                    p = entry.get("old_pattern")
                    if isinstance(p, str):
                        patterns.append(p)
        return patterns

    # ========== Pattern matching helpers ============
    def _pattern_match(self, pattern: str, key: str) -> bool:
        if "*" not in pattern:
            return key == pattern
        # convert glob-like pattern to regex
        regex = re.compile("^" + re.escape(pattern).replace("\\*", ".*") + "$")
        return regex.match(key) is not None

    def _resolve_new_key(self, old_key: str) -> str | None:
        mm = self._migration_map
        if not mm:
            return None
        # If map is a dict: {old_pat: new_pat, ...}
        if isinstance(mm, dict):
            for old_pat, new_pat in mm.items():
                if self._pattern_match(old_pat, old_key):
                    return self._apply_pattern(old_pat, new_pat, old_key)
        # If map is a list of dict entries: {old_pattern, new_pattern}
        if isinstance(mm, (list, tuple)):
            for entry in list(mm):
                if not isinstance(entry, dict):
                    continue
                old_pat = entry.get("old_pattern")
                new_pat = entry.get("new_pattern")
                if old_pat and new_pat and self._pattern_match(old_pat, old_key):
                    return self._apply_pattern(old_pat, new_pat, old_key)
        return None

    def _apply_pattern(self, old_pat: str, new_pat: str, old_key: str) -> str:
        if "*" in old_pat and "*" in new_pat:
            import re as _re

            regex = _re.compile("^" + _re.escape(old_pat).replace("\\*", "(.*)") + "$")
            m = regex.match(old_key)
            if m:
                grp = m.group(1)
                return new_pat.replace("*", grp)
        # fallback: direct replacement if patterns align
        return new_pat

    # ========== Core operations ==============
    def scan_existing_keys(self, batch_size: int = 1000) -> list[dict[str, Any]]:
        if self.redis is None:
            logger.error("Redis client not initialized; cannot scan keys")
            return []
        patterns: list[str] = []
        mm = self._migration_map
        patterns = self._collect_patterns_from_migration_map(mm)
        if not patterns:
            patterns = ["*"]

        results: list[dict[str, Any]] = []
        seen: set = set()
        for pat in patterns:
            try:
                for key in self.redis.scan_iter(match=pat, count=batch_size):
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append({"old_key": key, "matched_pattern": pat})
            except Exception as e:
                logger.exception("Error scanning with pattern %s: %s", pat, e)
        self.scan_results = results
        logger.info("Scan complete: %d keys found across patterns %s", len(results), patterns)
        return results

    def dry_run_report(self) -> list[dict[str, Any]]:
        if not self.scan_results:
            self.scan_existing_keys()
        plan: list[dict[str, Any]] = []
        for item in self.scan_results:
            old_key = item.get("old_key")
            new_key = self._resolve_new_key(old_key) if old_key else None
            item["new_key"] = new_key
            if new_key:
                plan.append({**item})
        logger.info("Dry run plan: %d migrations", len(plan))
        return plan

    def migrate_keys(self, dry_run: bool = True) -> dict[str, Any]:
        # Build plan from dry run
        self.scan_existing_keys()
        plan = self.dry_run_report()
        if not plan:
            logger.info("No keys to migrate based on current migration map.")
            return {"migrated": 0, "planned": 0}
        if dry_run:
            logger.info("Dry-run migration: %d keys would be migrated", len(plan))
            return {"migrated": 0, "planned": len(plan)}

        # Backup before applying migrations
        self._backup_plan(plan)

        migrated = 0
        for item in plan:
            if self.redis is None:
                logger.error("Redis client unavailable during migration; aborting migrations.")
                break
            old_key = item.get("old_key")
            new_key = item.get("new_key")
            if not old_key or not new_key:
                continue
            try:
                if self.redis.exists(new_key):
                    logger.warning(
                        "Target key exists, skipping migration: %s -> %s", old_key, new_key
                    )
                    continue
                # Rename key atomically
                self.redis.rename(old_key, new_key)
                # Preserve TTL if possible
                ttl = None
                try:
                    from .ttl import get_ttl_by_key_type  # type: ignore
                except Exception:
                    try:
                        from app.redis.ttl import get_ttl_by_key_type  # type: ignore
                    except Exception:
                        get_ttl_by_key_type = None  # type: ignore
                if callable(get_ttl_by_key_type):
                    try:
                        ttl = get_ttl_by_key_type(old_key)  # type: ignore
                    except Exception:
                        ttl = None
                if ttl:
                    try:
                        self.redis.expire(new_key, int(ttl))
                    except Exception as e:
                        logger.warning("Failed to set TTL on %s: %s", new_key, e)
                migrated += 1
            except Exception as e:
                logger.exception("Error migrating %s -> %s: %s", old_key, new_key, e)
        logger.info("Migration finished: %d migrated", migrated)
        return {"migrated": migrated, "planned": len(plan)}

    def rollback_migration(self) -> dict[str, Any]:
        backup = self._load_backup()
        if not backup:
            logger.info("No backup found. Nothing to rollback.")
            return {"rolled_back": 0}
        plan = backup.get("plan", [])
        rolled_back = 0
        if not plan:
            logger.info("Backup exists but contains no plan.")
            return {"rolled_back": 0}
        for item in plan:
            if self.redis is None:
                logger.error("Redis client unavailable during rollback; aborting.")
                break
            old_key = item.get("old_key")
            new_key = item.get("new_key")
            if not old_key or not new_key:
                continue
            try:
                if self.redis.exists(new_key):
                    self.redis.rename(new_key, old_key)
                    rolled_back += 1
            except Exception as e:
                logger.exception("Rollback failed for %s -> %s: %s", new_key, old_key, e)
        logger.info("Rollback complete: %d keys restored", rolled_back)
        return {"rolled_back": rolled_back}


def _ensure_redis_cli_available():
    # Quick helper to hint users when Redis is not available in the environment
    try:

        return True
    except Exception:
        logger.warning("redis package not available; ensure Redis client is supplied or installed.")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Redis migration utility for ProjectCEA")
    sub = parser.add_subparsers(dest="cmd", help="sub-command help")

    # dry-run / plan only (default)
    sub.run = None  # type: ignore
    parser_run = sub.add_parser(
        "run", help="Run migration (requires --apply flag) or default dry-run if not provided"
    )
    parser_run.add_argument(
        "--apply", action="store_true", help="Apply the migration (not allowed by default)"
    )
    parser_run.add_argument(
        "--rollback", action="store_true", help="Rollback last migration using backup"
    )

    # Also provide a convenience alias to just dry-run with no flags
    args = parser.parse_args()

    # If no subcommand is provided, behave as dry-run by default
    mode_apply = False
    do_rollback = False
    if args.cmd == "run":
        mode_apply = bool(getattr(args, "apply", False))
        do_rollback = bool(getattr(args, "rollback", False))
    else:
        mode_apply = False
        do_rollback = False

    mig = RedisMigration()
    if do_rollback:
        result = mig.rollback_migration()
        logger.info("Rollback result: %s", result)
        print(result)
        return 0

    if mode_apply:
        result = mig.migrate_keys(dry_run=False)
        logger.info("Migration result: %s", result)
        print(result)
        return 0

    # Default: dry-run report
    dry = mig.migrate_keys(dry_run=True)
    report = mig.dry_run_report()
    logger.info("Dry-run report: %d planned migrations", len(report))
    print({"dry_run": dry, "plan": report})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover
        logger.exception("Unhandled error: %s", exc)
        raise
