"""Export the shared frontend Automation/Archive schema without service lifespan."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def main() -> int:
    """Write canonical JSON OpenAPI to the requested output path."""
    if len(sys.argv) != 2:
        print("usage: export_openapi.py OUTPUT_PATH", file=sys.stderr)
        return 2
    service_root = Path(__file__).resolve().parents[1]
    infrastructure_root = service_root.parent
    sys.path.insert(0, str(service_root))
    sys.path.insert(0, str(infrastructure_root))
    sys.path.insert(0, str(infrastructure_root / "grow-archive-service"))
    sys.path.insert(0, str(infrastructure_root / "monitoring-service"))
    from grow_archive_service.archive_routes import router as archive_router

    from app.main import app

    # Schema composition only: the running Archive service still owns port 8006.
    app.include_router(archive_router)

    output_path = Path(sys.argv[1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    paths = schema.get("paths", {})
    for path, operations in paths.items():
        for method, operation in operations.items():
            if isinstance(operation, dict):
                operation["operationId"] = f"{method}_{path.strip('/').replace('/', '_')}"
    payload = json.dumps(schema, sort_keys=True, separators=(",", ":")) + "\n"
    output_path.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
