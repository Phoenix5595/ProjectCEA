#!/usr/bin/env python3
"""Single entrypoint for reading Infrastructure/services.yaml.

Used by bash callers (deploy.sh, rollback-deploy.sh, one-off admin scripts)
and by Python tooling. Bash callers invoke this with one of the ``--list-*``
flags; every invocation prints one value per line so shell ``while read`` or
``$(...)`` capture stays trivial.

CLI modes:
    --list-units                : one unit name per line, in start_order
    --list-deploy-managed-units : same, filtered to services deploy.sh touches
    --list-health               : "<unit>\\t<url>" for every service with a url
    --list-hardware-facing      : units where hardware_facing: true
    --list-repo-unit-paths      : "<unit>\\t<repo_path>" for services whose
                                  unit file is tracked in this repo

If no flag is provided, the whole structure is dumped as JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "ERROR: PyYAML is required. Install with: apt install python3-yaml",
        file=sys.stderr,
    )
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVICES_YAML = REPO_ROOT / "Infrastructure" / "services.yaml"


def _load() -> list[dict]:
    if not SERVICES_YAML.exists():
        print(f"ERROR: {SERVICES_YAML} not found", file=sys.stderr)
        sys.exit(2)
    raw = yaml.safe_load(SERVICES_YAML.read_text()) or {}
    services = raw.get("services") or []
    if not isinstance(services, list):
        print("ERROR: services.yaml 'services' key must be a list", file=sys.stderr)
        sys.exit(2)

    services.sort(key=lambda s: (s.get("start_order", 99), s.get("name", "")))
    return services


def list_units(deploy_managed_only: bool) -> None:
    for svc in _load():
        if deploy_managed_only and svc.get("deploy_managed") is False:
            continue
        print(svc["unit"])


def list_health_urls() -> None:
    for svc in _load():
        url = svc.get("health_url")
        if url:
            print(f"{svc['unit']}\t{url}")


def list_hardware_facing() -> None:
    for svc in _load():
        if svc.get("hardware_facing"):
            print(svc["unit"])


def list_repo_unit_paths() -> None:
    for svc in _load():
        repo_unit = svc.get("repo_unit")
        if repo_unit:
            print(f"{svc['unit']}\t{repo_unit}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list-units", action="store_true")
    group.add_argument("--list-deploy-managed-units", action="store_true")
    group.add_argument("--list-health", action="store_true")
    group.add_argument("--list-hardware-facing", action="store_true")
    group.add_argument("--list-repo-unit-paths", action="store_true")
    args = parser.parse_args()

    if args.list_units:
        list_units(deploy_managed_only=False)
    elif args.list_deploy_managed_units:
        list_units(deploy_managed_only=True)
    elif args.list_health:
        list_health_urls()
    elif args.list_hardware_facing:
        list_hardware_facing()
    elif args.list_repo_unit_paths:
        list_repo_unit_paths()
    else:
        json.dump(_load(), sys.stdout, indent=2, default=str)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
