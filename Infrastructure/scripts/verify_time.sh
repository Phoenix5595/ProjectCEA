#!/usr/bin/env bash
# verify_time.sh — fail if NTP/time sync is unhealthy on this host.
# Rationale: time_bucket boundaries, CAGG refresh windows, schedule start/stop,
# replication timestamps, and Redis TTL comparisons all rely on clock alignment.
# Run as a deploy.sh preflight and from verify_iskra.sh on the replica host.

set -euo pipefail

NTP_SYNC=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo "unknown")
if [[ "$NTP_SYNC" != "yes" ]]; then
  echo "verify_time: FAIL — NTPSynchronized=$NTP_SYNC (expected yes)" >&2
  exit 1
fi

# systemd-timesyncd path (no chrony on this Pi). Pull offset from journalctl if available.
OFFSET_MS=""
if command -v chronyc >/dev/null 2>&1; then
  OFFSET_MS=$(chronyc tracking 2>/dev/null | awk -F': *' '/System time/ {print $2}' | awk '{print $1 * 1000}')
elif command -v timedatectl >/dev/null 2>&1; then
  # systemd-timesyncd shows "Poll interval" etc. No direct drift metric here;
  # rely on NTPSynchronized=yes as the gate and fall through silently.
  :
fi

if [[ -n "$OFFSET_MS" ]]; then
  # Threshold: 100 ms per plan's NTP invariant.
  if awk -v o="$OFFSET_MS" 'BEGIN { exit !(o < 0 ? -o : o < 100) }'; then
    echo "verify_time: OK — NTP synced, offset ${OFFSET_MS}ms"
    exit 0
  else
    echo "verify_time: FAIL — NTP offset ${OFFSET_MS}ms > 100ms" >&2
    exit 1
  fi
fi

echo "verify_time: OK — NTPSynchronized=yes (no drift metric available)"
exit 0
