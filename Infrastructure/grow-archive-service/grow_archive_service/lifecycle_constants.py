from __future__ import annotations

from datetime import timedelta
from typing import Final


FLOWER_ROOM: Final = "Flower Room"
VEG_ROOM: Final = "Veg Room"
IDENTITY_STREAMS: Final = frozenset({"measurement"})

#: Forward expiry window and bounded delete slices.
EXPIRY_DELETE_SLICE: Final = timedelta(hours=1)
EXPIRY_WINDOWS_PER_PAIR: Final = 2
EXPIRY_SLICES_PER_PAIR: Final = 4
EXPIRY_TICK_ROW_BUDGET: Final = 80_000
PURGE_BATCH_ROWS: Final = 4096
PURGE_BATCH_BYTES: Final = 8 * 1024 * 1024
ZIP_DUE_MONTHS: Final = 9
CURSOR_RELATION: Final = "public.archive_expiry_cursor"
LIFECYCLE_TICK_SECONDS: Final = 60
DISPATCH_POLL_SECONDS: Final = 1
MAX_WINDOW_BOUNDARIES: Final = 128
