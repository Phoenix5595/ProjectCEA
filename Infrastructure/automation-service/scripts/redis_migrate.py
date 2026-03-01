#!/usr/bin/env python3
"""Standalone Redis key migration script.

This script migrates Redis keys from old schema to new cea:* schema.
Can be run directly without complex module imports.
"""

import argparse
import os

import redis

# Migration mapping: old pattern -> new pattern template
MIGRATION_MAP = {
    "sensor:*": "cea:sensor:{location}:{cluster}:{sensor_type}",
    "sensor:*:ts": "cea:sensor:{location}:{cluster}:{sensor_type}_ts",
    "setpoint:*": "cea:setpoint:{location}:{cluster}:{device}",
    "effective_setpoint:*": "cea:effective_setpoint:{location}:{cluster}:{device}",
    "schedule:*": "cea:schedule:{location}:{cluster}",
    "schedule:state:*": "cea:schedule:state:{location}:{cluster}",
    "mode:*": "cea:mode:{location}:{cluster}",
    "alarm:*": "cea:alarm:{location}:{cluster}:{alarm_type}",
    "ramp:*": "cea:ramp:{location}:{cluster}:{device}",
    "pid:*": "cea:pid:{device_type}",
    "heartbeat:*": "cea:heartbeat:{service_name}",
    "light:*": "cea:light:{location}:{cluster}:{device}",
    "stream:*": "cea:stream:{stream_name}",
    "flag:*": "cea:flag:{flag_name}",
}


def parse_key(key: str) -> tuple[str, dict]:
    """Parse old key into components for new key construction."""
    parts = key.split(":")

    if key.startswith("sensor:"):
        if len(parts) >= 3 and parts[-1] == "ts":
            sensor_type = ":".join(parts[1:-1])
            return "sensor:*:ts", {
                "location": "global",
                "cluster": "main",
                "sensor_type": sensor_type,
            }
        sensor_type = parts[1] if len(parts) >= 2 else "unknown"
        return "sensor:*", {"location": "global", "cluster": "main", "sensor_type": sensor_type}

    if key.startswith("effective_setpoint:"):
        device = ":".join(parts[2:]) if len(parts) > 2 else parts[-1]
        return "effective_setpoint:*", {"location": parts[1], "cluster": "main", "device": device}

    if key.startswith("setpoint:"):
        device = ":".join(parts[2:]) if len(parts) > 2 else parts[-1]
        return "setpoint:*", {"location": parts[1], "cluster": "main", "device": device}

    if key.startswith("schedule:state:"):
        return "schedule:state:*", {"location": parts[2], "cluster": "main"}

    if key.startswith("schedule:"):
        return "schedule:*", {"location": parts[1], "cluster": "main"}

    if key.startswith("mode:"):
        return "mode:*", {"location": parts[1], "cluster": "main"}

    if key.startswith("alarm:"):
        return "alarm:*", {"location": parts[1], "cluster": "main", "alarm_type": "unknown"}

    if key.startswith("ramp:"):
        return "ramp:*", {"location": parts[1], "cluster": "main", "device": parts[-1]}

    if key.startswith("pid:"):
        return "pid:*", {"device_type": parts[1]}

    if key.startswith("heartbeat:"):
        return "heartbeat:*", {"service_name": parts[1]}

    if key.startswith("light:"):
        return "light:*", {"location": parts[1], "cluster": "main", "device": parts[-1]}

    if key.startswith("stream:"):
        return "stream:*", {"stream_name": parts[1]}

    if key.startswith("flag:"):
        return "flag:*", {"flag_name": parts[1]}

    return None, {}


def build_new_key(old_key: str) -> str:
    """Convert old key to new cea:* format."""
    pattern, params = parse_key(old_key)

    if pattern is None:
        return old_key  # No mapping, keep as-is

    template = MIGRATION_MAP.get(pattern, old_key)

    try:
        new_key = template.format(**params)
        return new_key
    except KeyError:
        return old_key  # Keep original if formatting fails


def scan_keys(client: redis.Redis) -> dict:
    """Scan all keys and categorize by migration status."""
    keys = client.keys("*")

    result = {"to_migrate": [], "already_cea": [], "total": len(keys)}

    for key in keys:
        key = key.decode() if isinstance(key, bytes) else key
        if key.startswith("cea:"):
            result["already_cea"].append(key)
        else:
            new_key = build_new_key(key)
            result["to_migrate"].append({"old": key, "new": new_key, "changed": key != new_key})

    return result


def migrate_keys(client: redis.Redis, dry_run: bool = True) -> dict:
    """Migrate keys from old schema to new cea:* schema."""
    scan_result = scan_keys(client)

    to_migrate = scan_result["to_migrate"]
    changed_count = sum(1 for k in to_migrate if k["changed"])

    result = {
        "dry_run": dry_run,
        "scanned": scan_result["total"],
        "to_migrate": len(to_migrate),
        "would_change": changed_count,
        "migrated": 0,
        "errors": [],
    }

    if dry_run:
        return result

    # Actual migration
    for item in to_migrate:
        if not item["changed"]:
            continue

        old_key = item["old"]
        new_key = item["new"]

        try:
            # Get value and TTL
            value = client.get(old_key)
            ttl = client.ttl(old_key)

            if value is None:
                continue

            # Set new key
            if ttl > 0:
                client.setex(new_key, ttl, value)
            else:
                client.set(new_key, value)

            result["migrated"] += 1

        except Exception as e:
            result["errors"].append({"key": old_key, "error": str(e)})

    return result


def main():
    parser = argparse.ArgumentParser(description="Redis key migration tool")
    parser.add_argument(
        "--dry-run", action="store_true", default=True, help="Preview migration (default)"
    )
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false", help="Execute migration"
    )
    parser.add_argument("--host", default=os.environ.get("REDIS_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("REDIS_PORT", 6379)))
    parser.add_argument("--db", type=int, default=0)

    args = parser.parse_args()

    client = redis.Redis(host=args.host, port=args.port, db=args.db, decode_responses=False)

    print("=== Redis Key Migration ===")
    print(f"Host: {args.host}:{args.port}/{args.db}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'LIVE MIGRATION'}")
    print()

    # Scan first
    scan = scan_keys(client)
    print(f"Total keys: {scan['total']}")
    print(f"Already using cea:* : {len(scan['already_cea'])}")
    print(f"Keys to migrate: {len(scan['to_migrate'])}")
    print()

    # Show sample migrations
    print("Sample migrations (first 10):")
    for item in scan["to_migrate"][:10]:
        if item["changed"]:
            print(f"  {item['old']} -> {item['new']}")
        else:
            print(f"  {item['old']} (unchanged)")
    print()

    # Execute if not dry-run
    result = migrate_keys(client, dry_run=args.dry_run)

    if args.dry_run:
        print(f"DRY-RUN: Would migrate {result['would_change']} keys")
        print("Run with --no-dry-run to execute")
    else:
        print(f"MIGRATED: {result['migrated']} keys")
        if result["errors"]:
            print(f"ERRORS: {len(result['errors'])}")
            for err in result["errors"][:5]:
                print(f"  {err['key']}: {err['error']}")


if __name__ == "__main__":
    main()
