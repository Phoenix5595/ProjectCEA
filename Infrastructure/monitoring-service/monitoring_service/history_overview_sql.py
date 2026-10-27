"""Bounded all-history overview SQL statements with in-statement coverage guards.

Every statement below is one read-only SELECT whose own CTEs verify the
safe-coverage state of the exact material that serves the request, so a
source and its guards always share one snapshot:

* ``monitoring_overview_coverage`` verified-contiguous bounds per tier,
* ``monitoring_history_cagg_watermark`` native materialization watermarks, including
  the measurement 1min/1hour ancestor chain, as an upper bound plus pending
  invalidation overlap per tier,
* below-coverage regions proven raw-empty inside the same statement,
* relay support state through ``monitoring_relay_overview_hourly_ready`` and
  one request observation watermark.

Rows are emitted in two kinds: each statement returns one leading ``guard``
row (checked first by the repository) and then ``data`` rows. A ``false``
guard value means the request would need stale or unverified material and
reports 503 with ``GUARD_UNAVAILABLE_MESSAGE``; nothing is then modeled.

Per output bucket the served contributions merge (never prorated, never
double counted):

1. daily CAGG rows whose whole UTC day lies inside the bucket and inside the
   verified daily coverage window including the native watermark;
2. hourly CAGG rows whose whole UTC hour lies inside the bucket and inside
   the verified hourly coverage window, excluding hours already served by a
   used daily row;
3. raw rows only inside the outer partial-hour fragment at the requested
   start and a recent tail bounded to the last 24 hours of the request.

Every remaining span must be covered by a verified window or lie in the
below-coverage region proven raw-empty in the same statement; anything else
fails the guard. Relay durations merge ``monitoring_relay_duration_1hour``
and ``monitoring_relay_duration_1day`` rows plus exact
``monitoring_relay_duration_slices`` intervals for the uncovered recent
piece span under one request watermark; buckets without recorded owner time
report unknown elapsed microseconds, never fabricated state.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Final

GUARD_UNAVAILABLE_MESSAGE: Final[str] = "history overview aggregate coverage is unavailable"

GUARD_ROW_KIND: Final[str] = "guard"
DATA_ROW_KIND: Final[str] = "data"


def bucket_points_json(bounds: Sequence[tuple[datetime, datetime, bool]]) -> str:
    """Serialize ``(clipped_start, clipped_end, partial)`` bucket bounds."""
    items: list[dict[str, object]] = []
    for index, (start, end, partial) in enumerate(bounds):
        items.append(
            {
                "x": index,
                "b": start.isoformat(),
                "e": end.isoformat(),
                "p": "t" if partial else "f",
            }
        )
    return json.dumps(items, separators=(",", ":"))


# ---------------------------------------------------------------- shared SQL
# Piece engine for the aggregate statements: marks are the clipped window
# edges (NULL windows degrade harmlessly to bucket edges) and consecutive
# distinct marks partition every output bucket into disjoint sub-spans whose
# union is exactly the bucket. A sub-span is valid only when it fits one of
# the allowed windows: verified hourly/daily coverage, the probe-verified
# empty below-coverage region, or an allowed raw span (start partial-hour
# fragment or the 24 hour tail).
_ANGLES: Final[str] = """
request AS (
    SELECT $2::timestamptz AS range_start, $3::timestamptz AS range_end
),
buckets AS (
    SELECT
        (entry ->> 'x')::INTEGER AS idx,
        (entry ->> 'b')::TIMESTAMPTZ AS b0,
        (entry ->> 'e')::TIMESTAMPTZ AS b1,
        COALESCE(entry ->> 'p', 'f')::BOOLEAN AS partial
    FROM jsonb_array_elements($4::jsonb) AS entry
)
"""
