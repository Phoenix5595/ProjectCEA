"""CLI tool for managing automation service configuration in PostgreSQL.

This tool provides a safe, validated interface for editing runtime configuration
(setpoints, schedules, PID parameters, safety limits) stored in PostgreSQL.

The CLI never writes to Redis or interacts with actuators - it only modifies
the PostgreSQL Config Store. All changes are logged to config_versions table
for audit trail.

Usage:
    config-cli setpoint get <location> <cluster>
    config-cli setpoint set <location> <cluster> --heating-setpoint <value> [--cooling-setpoint <value>] --humidity <value> --co2 <value> [--dry-run]
    config-cli pid get <device_type>
    config-cli pid set <device_type> --kp <value> --ki <value> --kd <value> [--dry-run]
    config-cli schedule list [--location <loc>] [--cluster <clust>]
    config-cli schedule create <name> <location> <cluster> <device> <start> <end> [--mode <mode>] [--dry-run]
    config-cli schedule update <id> [--name <name>] [--start <time>] [--end <time>] [--mode <mode>] [--enabled <bool>] [--dry-run]
    config-cli schedule delete <id> [--dry-run]
    config-cli config show <location> <cluster>
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.database import DatabaseManager
from cli.pid import cmd_pid_get, cmd_pid_set
from cli.schedules import (
    cmd_schedule_create,
    cmd_schedule_delete,
    cmd_schedule_list,
)
from cli.schedules_update import cmd_schedule_update
from cli.setpoints import cmd_config_show


async def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CLI tool for managing automation service configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument("--author", help="Author name for config version logging")
    parser.add_argument("--dry-run", action="store_true", help="Validate but do not apply changes")

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # PID commands
    pid_parser = subparsers.add_parser("pid", help="Manage PID parameters")
    pid_subparsers = pid_parser.add_subparsers(dest="pid_cmd")

    pid_get = pid_subparsers.add_parser("get", help="Get PID parameters")
    pid_get.add_argument("device_type", help="Device type (heater, co2, etc.)")
    pid_get.add_argument(
        "--location", default="Flower Room", help="Location name (default: Flower Room)"
    )
    pid_get.add_argument("--cluster", default="main", help="Cluster name (default: main)")

    pid_set = pid_subparsers.add_parser("set", help="Set PID parameters")
    pid_set.add_argument("device_type", help="Device type (heater, co2, etc.)")
    pid_set.add_argument("--kp", type=float, required=True, help="Proportional gain")
    pid_set.add_argument("--ki", type=float, required=True, help="Integral gain")
    pid_set.add_argument("--kd", type=float, required=True, help="Derivative gain")
    pid_set.add_argument(
        "--location", default="Flower Room", help="Location name (default: Flower Room)"
    )
    pid_set.add_argument("--cluster", default="main", help="Cluster name (default: main)")

    # Schedule commands
    sched_parser = subparsers.add_parser("schedule", help="Manage schedules")
    sched_subparsers = sched_parser.add_subparsers(dest="schedule_cmd")

    sched_list = sched_subparsers.add_parser("list", help="List schedules")
    sched_list.add_argument("--location", help="Filter by location")
    sched_list.add_argument("--cluster", help="Filter by cluster")

    sched_create = sched_subparsers.add_parser("create", help="Create schedule")
    sched_create.add_argument("name", help="Schedule name")
    sched_create.add_argument("location", help="Location name")
    sched_create.add_argument("cluster", help="Cluster name")
    sched_create.add_argument("device", help="Device name")
    sched_create.add_argument("start", help="Start time (HH:MM)")
    sched_create.add_argument("end", help="End time (HH:MM)")
    sched_create.add_argument("--mode", help="Mode (DAY, NIGHT, TRANSITION)")
    sched_create.add_argument("--day-of-week", type=int, help="Day of week (0-6, None for daily)")
    sched_create.add_argument(
        "--enabled", action="store_true", default=True, help="Enable schedule"
    )
    sched_create.add_argument("--disabled", action="store_true", help="Disable schedule")
    sched_create.add_argument(
        "--target-intensity", type=float, help="Target light intensity (0-100%) for ramp schedules"
    )
    sched_create.add_argument(
        "--ramp-up-duration", type=int, help="Ramp up duration in minutes (0 = instant)"
    )
    sched_create.add_argument(
        "--ramp-down-duration", type=int, help="Ramp down duration in minutes (0 = instant)"
    )

    sched_update = sched_subparsers.add_parser("update", help="Update schedule")
    sched_update.add_argument("id", type=int, help="Schedule ID")
    sched_update.add_argument("--name", help="New name")
    sched_update.add_argument("--start", help="New start time (HH:MM)")
    sched_update.add_argument("--end", help="New end time (HH:MM)")
    sched_update.add_argument("--mode", help="New mode (DAY, NIGHT, TRANSITION)")
    sched_update.add_argument("--day-of-week", type=int, help="New day of week (0-6)")
    sched_update.add_argument("--enabled", action="store_true", help="Enable schedule")
    sched_update.add_argument("--disabled", action="store_true", help="Disable schedule")
    sched_update.add_argument(
        "--target-intensity", type=float, help="New target light intensity (0-100%)"
    )
    sched_update.add_argument(
        "--ramp-up-duration", type=int, help="New ramp up duration in minutes (0 = instant)"
    )
    sched_update.add_argument(
        "--ramp-down-duration", type=int, help="New ramp down duration in minutes (0 = instant)"
    )

    sched_delete = sched_subparsers.add_parser("delete", help="Delete schedule")
    sched_delete.add_argument("id", type=int, help="Schedule ID")

    # Config show command
    config_parser = subparsers.add_parser("config", help="Show configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_cmd")
    config_show = config_subparsers.add_parser("show", help="Show effective config")
    config_show.add_argument("location", help="Location name")
    config_show.add_argument("cluster", help="Cluster name")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Initialize database
    db = DatabaseManager()
    try:
        success = await db.initialize()
        if not success:
            print("Error: Failed to initialize database connection")
            sys.exit(1)
    except Exception as e:
        print(f"Error connecting to database: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    try:
        # Route to appropriate command
        if args.command == "pid":
            if args.pid_cmd == "get":
                await cmd_pid_get(db, args.device_type, args.location, args.cluster)
            elif args.pid_cmd == "set":
                await cmd_pid_set(
                    db,
                    args.device_type,
                    args.kp,
                    args.ki,
                    args.kd,
                    args.dry_run,
                    args.author,
                    args.location,
                    args.cluster,
                )

        elif args.command == "schedule":
            if args.schedule_cmd == "list":
                await cmd_schedule_list(db, args.location, args.cluster)
            elif args.schedule_cmd == "create":
                enabled = not args.disabled if hasattr(args, "disabled") else args.enabled
                await cmd_schedule_create(
                    db,
                    args.name,
                    args.location,
                    args.cluster,
                    args.device,
                    args.start,
                    args.end,
                    args.mode,
                    args.day_of_week,
                    enabled,
                    getattr(args, "target_intensity", None),
                    getattr(args, "ramp_up_duration", None),
                    getattr(args, "ramp_down_duration", None),
                    args.dry_run,
                    args.author,
                )
            elif args.schedule_cmd == "update":
                enabled = None
                if hasattr(args, "enabled") and args.enabled:
                    enabled = True
                elif hasattr(args, "disabled") and args.disabled:
                    enabled = False
                await cmd_schedule_update(
                    db,
                    args.id,
                    args.name,
                    args.start,
                    args.end,
                    args.mode,
                    args.day_of_week,
                    enabled,
                    getattr(args, "target_intensity", None),
                    getattr(args, "ramp_up_duration", None),
                    getattr(args, "ramp_down_duration", None),
                    args.dry_run,
                    args.author,
                )
            elif args.schedule_cmd == "delete":
                await cmd_schedule_delete(db, args.id, args.dry_run, args.author)

        elif args.command == "config":
            if args.config_cmd == "show":
                await cmd_config_show(db, args.location, args.cluster)

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
