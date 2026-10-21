#!/usr/bin/env bash
# verify_time.sh — fail unless NTP time sync is healthy on this host.
# deploy.sh runs this as a preflight: CAGG bucket boundaries, schedule
# start/stop and Redis TTL comparisons all rely on clock alignment.
set -euo pipefail

MAX_OFFSET_MS=100

sync_state="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo unknown)"
if [[ "$sync_state" != "yes" ]]; then
  echo "verify_time: FAIL — NTPSynchronized=$sync_state (expected yes)" >&2
  exit 1
fi

if command -v chronyc >/dev/null 2>&1; then
  # "System time     : 0.000012345 seconds fast of NTP time"
  offset_ms="$(chronyc tracking 2>/dev/null | awk -F': *' '/^System time/ { split($2, f, " "); printf "%.3f", f[1] * 1000; exit }' || true)"
else
  # systemd-timesyncd: "       Offset: +716us" (unit us, ms, s or min)
  offset_ms="$(timedatectl timesync-status 2>/dev/null | awk '
    $1 == "Offset:" {
      v = $2; sign = 1
      if (substr(v, 1, 1) == "-") sign = -1
      sub(/^[+-]/, "", v)
      if (v ~ /us$/) { sub(/us$/, "", v); ms = v / 1000 }
      else if (v ~ /ms$/) { sub(/ms$/, "", v); ms = v + 0 }
      else if (v ~ /min$/) { sub(/min$/, "", v); ms = v * 60000 }
      else if (v ~ /s$/) { sub(/s$/, "", v); ms = v * 1000 }
      else exit
      printf "%.3f", sign * ms
      exit
    }' || true)"
fi

if [[ -z "$offset_ms" ]]; then
  echo "verify_time: FAIL — NTP offset unavailable (chronyc tracking / timedatectl timesync-status)" >&2
  exit 1
fi

if awk -v o="$offset_ms" -v max="$MAX_OFFSET_MS" 'BEGIN { a = (o < 0) ? -o : o; exit !(a < max) }'; then
  echo "verify_time: OK — NTP synced, offset ${offset_ms}ms"
else
  echo "verify_time: FAIL — NTP offset ${offset_ms}ms exceeds ${MAX_OFFSET_MS}ms" >&2
  exit 1
fi
