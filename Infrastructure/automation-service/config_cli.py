#!/usr/bin/env python3
"""Thin wrapper for the CLI tool — delegates to cli.config_cli."""

from __future__ import annotations

import os
import sys

# Add parent directory to path to import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli.config_cli import main

if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
