#!/usr/bin/env python3
"""Validate parity between Python and TypeScript cluster topology definitions.

Reads:
  - ``Infrastructure/shared/cluster_topology.py`` (canonical Python registry)
  - ``Infrastructure/frontend/src/config/clusterTopology.ts`` (frontend mirror)

Extracts room → cluster mappings from both and compares:

  * Room names (set of known rooms)
  * Device cluster per room (always ``"main"`` today)
  * Physical sensor sub-clusters per room (``("front", "back")`` for Flower,
    ``()`` for unsplit rooms)
  * Derived sensor URL slugs (sub-clusters if non-empty, else ``(device_cluster,)``)

Exits **0** when all fields match. Exits **1** with a detailed diff on any
mismatch, printing exactly which rooms/attributes diverge.

Usage::

    python Infrastructure/scripts/validate_cluster_topology.py

No dependencies beyond the stdlib.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import sys

# ── Paths (relative to repo root) ──────────────────────────────────────────

# Topology files live under ``Infrastructure/``, which is two levels up
# from this script at ``Infrastructure/scripts/``.
INFRA_ROOT = Path(__file__).resolve().parent.parent
PYTHON_TOPOLOGY = INFRA_ROOT / "shared" / "cluster_topology.py"
TYPESCRIPT_TOPOLOGY = INFRA_ROOT / "frontend" / "src" / "config" / "clusterTopology.ts"


# ── Python parser (AST-based) ──────────────────────────────────────────────


def _parse_python_topology(filepath: Path) -> dict[str, dict]:
    """Parse the ``_TOPOLOGY`` dict from the Python module via ``ast``.

    Returns ``{room_name: {"device_cluster": str, "sensor_subclusters": tuple[str, ...]}}``.
    """
    source = filepath.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        # ``_TOPOLOGY`` is declared with an annotation, so it's an ``AnnAssign``.
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_TOPOLOGY"
            and node.value is not None
        ):
            return _extract_python_rooms(node.value)
        # Plain ``Assign`` for safety (e.g. test fixtures).
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_TOPOLOGY":
                    return _extract_python_rooms(node.value)

    raise ValueError("Could not find _TOPOLOGY assignment in Python source")


def _extract_python_rooms(dict_node: ast.AST) -> dict[str, dict]:
    """Walk a ``ast.Dict`` literal and extract per-room topology entries."""
    if not isinstance(dict_node, ast.Dict):
        raise TypeError(f"Expected ast.Dict, got {type(dict_node).__name__}")

    rooms: dict[str, dict] = {}
    for key_node, value_node in zip(dict_node.keys, dict_node.values, strict=True):
        room_name = _ast_str(key_node)
        call = value_node
        if not isinstance(call, ast.Call):
            continue  # skip non-call entries

        device_cluster: str = "main"
        sensor_subclusters: tuple[str, ...] = ()

        for kw in call.keywords:
            if kw.arg == "device_cluster":
                device_cluster = _ast_str(kw.value)
            elif kw.arg == "sensor_subclusters" and isinstance(kw.value, ast.Tuple):
                sensor_subclusters = tuple(_ast_str(elt) for elt in kw.value.elts)
                # ast.Name (default empty tuple) → keep ()

        rooms[room_name] = {
            "device_cluster": device_cluster,
            "sensor_subclusters": sensor_subclusters,
        }

    return rooms


def _ast_str(node: ast.AST) -> str:
    """Extract a string literal from an AST node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    raise ValueError(f"Expected string constant, got AST {type(node).__name__}")


# ── TypeScript parser (regex-based) ────────────────────────────────────────


def _parse_typescript_topology(filepath: Path) -> dict[str, dict]:
    """Parse the ``TOPOLOGY`` const from the TypeScript module.

    Returns the same structure as :func:`_parse_python_topology`.
    Uses regex + minimal brace-counting — the file is small and well-structured.
    """
    source = filepath.read_text()

    # Locate the TOPOLOGY assignment's opening brace.
    m = re.search(r"export\s+const\s+TOPOLOGY\s*:\s*[^=]+=\s*(\{)", source)
    if not m:
        raise ValueError("Could not find TOPOLOGY definition in TS source")

    # Find matching closing brace via depth-counting.
    brace_start = m.start(1)
    depth = 0
    topo_end = -1
    for i in range(brace_start, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                topo_end = i + 1
                break

    if topo_end == -1:
        raise ValueError("Could not find closing brace for TOPOLOGY block")

    topo_block = source[brace_start:topo_end]

    # Split top-level entries (handle nested braces/brackets).
    entries = _split_topo_entries(topo_block)

    rooms: dict[str, dict] = {}
    for entry in entries:
        entry = entry.strip()
        if not entry or entry.startswith("//"):
            continue

        # Room key — may be quoted ('Flower Room') or bare (Lab).
        key_m = re.match(r"""['"]?([a-zA-Z0-9_ ]+)['"]?\s*:\s*\{""", entry)
        if not key_m:
            continue
        room_name = key_m.group(1).strip()

        # deviceCluster
        dc_m = re.search(r"deviceCluster\s*:\s*['\"]([^'\"]+)['\"]", entry)
        device_cluster = dc_m.group(1) if dc_m else "main"

        # sensorSubclusters array
        sensor_subclusters: tuple[str, ...] = ()
        ss_m = re.search(r"sensorSubclusters\s*:\s*\[(.*?)\]", entry, re.DOTALL)
        if ss_m:
            inner = ss_m.group(1).strip()
            if inner:
                items = re.findall(r"""['"]([^'"]+)['"]""", inner)
                sensor_subclusters = tuple(items)
            # else empty array → keep ()

        rooms[room_name] = {
            "device_cluster": device_cluster,
            "sensor_subclusters": sensor_subclusters,
        }

    return rooms


def _split_topo_entries(block: str) -> list[str]:
    """Split a top-level TS object literal into ``key: value`` entries.

    Respects nested braces ``{ }`` and brackets ``[ ]`` so commas inside
    sub-objects or arrays don't cause false splits.
    """
    # Strip outer braces.
    block = block.strip()
    if block.startswith("{") and block.endswith("}"):
        block = block[1:-1]

    entries: list[str] = []
    depth_brace = 0
    depth_bracket = 0
    start = 0

    for i, ch in enumerate(block):
        if ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace -= 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket -= 1
        elif ch == "," and depth_brace == 0 and depth_bracket == 0:
            entries.append(block[start:i])
            start = i + 1

    trailing = block[start:].strip()
    if trailing:
        entries.append(trailing)

    return [e for e in entries if e.strip()]


# ── Derived field: sensor URL slugs ───────────────────────────────────────


def _sensor_url_slugs(data: dict) -> tuple[str, ...]:
    """Derive sensor URL slugs per the cluster topology contract.

    * Rooms with physical sub-clusters → the sub-clusters themselves.
    * Unsplit rooms → ``(device_cluster,)`` sentinel.
    """
    sub = data["sensor_subclusters"]
    return sub if sub else (data["device_cluster"],)


# ── Comparison logic ───────────────────────────────────────────────────────


def compare(py: dict[str, dict], ts: dict[str, dict]) -> list[str]:
    """Compare two topology dicts; return list of diff messages (empty = match)."""
    diffs: list[str] = []

    py_rooms = set(py)
    ts_rooms = set(ts)
    common = py_rooms & ts_rooms

    for r in sorted(py_rooms - ts_rooms):
        diffs.append(f"Room {r!r} exists in Python but is MISSING in TypeScript")
    for r in sorted(ts_rooms - py_rooms):
        diffs.append(f"Room {r!r} exists in TypeScript but is MISSING in Python")

    for room in sorted(common):
        p, t = py[room], ts[room]

        if p["device_cluster"] != t["device_cluster"]:
            diffs.append(
                f"Room {room!r}: device_cluster differs — "
                f"Python={p['device_cluster']!r}, TypeScript={t['device_cluster']!r}"
            )

        if p["sensor_subclusters"] != t["sensor_subclusters"]:
            diffs.append(
                f"Room {room!r}: sensor_subclusters differ — "
                f"Python={p['sensor_subclusters']!r}, TypeScript={t['sensor_subclusters']!r}"
            )

        py_slugs = _sensor_url_slugs(p)
        ts_slugs = _sensor_url_slugs(t)
        if py_slugs != ts_slugs:
            diffs.append(
                f"Room {room!r}: sensor URL slugs differ — "
                f"Python={py_slugs!r}, TypeScript={ts_slugs!r}"
            )

    return diffs


# ── CLI ────────────────────────────────────────────────────────────────────


def main() -> int:
    if not PYTHON_TOPOLOGY.is_file():
        print(f"ERROR: {PYTHON_TOPOLOGY} not found", file=sys.stderr)
        return 1
    if not TYPESCRIPT_TOPOLOGY.is_file():
        print(f"ERROR: {TYPESCRIPT_TOPOLOGY} not found", file=sys.stderr)
        return 1

    try:
        py_data = _parse_python_topology(PYTHON_TOPOLOGY)
    except (ValueError, SyntaxError, TypeError) as exc:
        print(f"ERROR parsing Python topology: {exc}", file=sys.stderr)
        return 1

    try:
        ts_data = _parse_typescript_topology(TYPESCRIPT_TOPOLOGY)
    except (ValueError, SyntaxError, TypeError) as exc:
        print(f"ERROR parsing TypeScript topology: {exc}", file=sys.stderr)
        return 1

    diffs = compare(py_data, ts_data)

    if not diffs:
        print("\N{CHECK MARK} Cluster topology parity check PASSED")
        print(f"  Rooms compared: {len(py_data)}")
        for room in sorted(py_data):
            d = py_data[room]
            print(
                f"    {room}: dev={d['device_cluster']!r}, "
                f"subs={d['sensor_subclusters']!r}, "
                f"url_slugs={_sensor_url_slugs(d)!r}"
            )
        return 0

    print(
        f"\N{CROSS MARK} Cluster topology parity check FAILED \u2014 {len(diffs)} difference(s):\n",
        file=sys.stderr,
    )
    for i, diff in enumerate(diffs, 1):
        print(f"  {i}. {diff}", file=sys.stderr)

    return 1


if __name__ == "__main__":
    sys.exit(main())
