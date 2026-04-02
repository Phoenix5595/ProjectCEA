#!/usr/bin/env python3
"""Emit one deploy log JSON object (no ts) from env: DLOG_*, RELEASE_ID, TARGET, PREVIOUS_RELEASE."""

from __future__ import annotations

import json
import os


def main() -> None:
    print(
        json.dumps(
            {
                "event": os.environ["DLOG_EVENT"],
                "release_id": os.environ.get("RELEASE_ID", ""),
                "release_path": os.environ.get("TARGET", ""),
                "previous_release_path": os.environ.get("PREVIOUS_RELEASE") or None,
                "detail": os.environ.get("DLOG_DETAIL", ""),
                "service": os.environ.get("DLOG_SERVICE", ""),
                "http_code": os.environ.get("DLOG_HTTP", ""),
            }
        )
    )


if __name__ == "__main__":
    main()
