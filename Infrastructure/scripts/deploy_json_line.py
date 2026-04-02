#!/usr/bin/env python3
"""Read one JSON object from stdin; ensure ts (UTC ISO8601); print single NDJSON line."""

from __future__ import annotations

import datetime
import json
import sys


def main() -> None:
    data = json.load(sys.stdin)
    if not isinstance(data, dict):
        raise SystemExit("deploy_json_line: JSON root must be an object")
    if "ts" not in data:
        data["ts"] = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    main()
