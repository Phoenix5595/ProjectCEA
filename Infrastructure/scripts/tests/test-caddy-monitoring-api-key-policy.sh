#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIRECTORY="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_CADDYFILE="$SCRIPT_DIRECTORY/../../caddy/Caddyfile"
readonly SANDBOX="$(mktemp -d)"
readonly DUMMY_API_KEY="caddy-monitoring-test-key"

CADDY_PID=""
UPSTREAM_PID=""

cleanup() {
  if [[ -n "$CADDY_PID" ]]; then
    kill "$CADDY_PID" 2>/dev/null || true
    wait "$CADDY_PID" 2>/dev/null || true
  fi
  if [[ -n "$UPSTREAM_PID" ]]; then
    kill "$UPSTREAM_PID" 2>/dev/null || true
    wait "$UPSTREAM_PID" 2>/dev/null || true
  fi
  unset CEA_API_KEY
  rm -rf "$SANDBOX"
}
trap cleanup EXIT

cat > "$SANDBOX/upstream.py" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path
from socketserver import BaseRequestHandler, ThreadingTCPServer


class RecordingHandler(BaseRequestHandler):
    def handle(self) -> None:
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            request += chunk
        lines = request.split(b"\r\n")
        method, target_and_protocol = lines[0].split(b" ", 1)
        target, _protocol = target_and_protocol.rsplit(b" HTTP/", 1)
        api_keys = [
            line.split(b":", 1)[1].strip().decode("latin-1")
            for line in lines[1:]
            if line.lower().startswith(b"x-api-key:")
        ]
        record = {
            "method": method.decode("ascii"),
            "path": target.decode("latin-1"),
            "api_keys": api_keys,
        }
        with Path(sys.argv[1]).open("a", encoding="utf-8") as log:
            log.write(json.dumps(record) + "\n")
        self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")


server = ThreadingTCPServer(("127.0.0.1", 0), RecordingHandler)
Path(sys.argv[2]).write_text(str(server.server_address[1]), encoding="utf-8")
server.serve_forever()
PY

python3 "$SANDBOX/upstream.py" "$SANDBOX/upstream.jsonl" "$SANDBOX/upstream.port" &
UPSTREAM_PID=$!

while [[ ! -s "$SANDBOX/upstream.port" ]]; do
  if ! kill -0 "$UPSTREAM_PID" 2>/dev/null; then
    printf 'dummy upstream exited before publishing its port\n' >&2
    exit 1
  fi
done

readonly UPSTREAM_PORT="$(<"$SANDBOX/upstream.port")"
if (( UPSTREAM_PORT > 10000 )); then
  readonly PROXY_PORT="$((UPSTREAM_PORT - 10000))"
else
  readonly PROXY_PORT="$((UPSTREAM_PORT + 10000))"
fi

python3 - "$SOURCE_CADDYFILE" "$SANDBOX/Caddyfile" "$PROXY_PORT" "$UPSTREAM_PORT" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

source, destination, proxy_port, upstream_port = sys.argv[1:]
content = Path(source).read_text(encoding="utf-8")
content = re.sub(r"\n\tlog default \{.*?\n\t\}\n", "\n", content, flags=re.DOTALL)
content = re.sub(r"\n\tlog \{.*?\n\t\}\n", "\n", content, count=1, flags=re.DOTALL)
content = content.replace(":8080 {", f"http://127.0.0.1:{proxy_port} {{", 1)
content = content.replace("127.0.0.1:8000", f"127.0.0.1:{upstream_port}")
content = content.replace("127.0.0.1:8001", f"127.0.0.1:{upstream_port}")
content = content.replace("127.0.0.1:8005", f"127.0.0.1:{upstream_port}")
content = content.replace("{env.CEA_API_KEY}", "caddy-monitoring-test-key")
Path(destination).write_text(content, encoding="utf-8")
PY

caddy run --config "$SANDBOX/Caddyfile" --adapter caddyfile >"$SANDBOX/caddy.log" 2>&1 &
CADDY_PID=$!

request() {
  local method="$1"
  local path="$2"
  curl --silent --retry 20 --retry-connrefused --retry-delay 0 \
    --request "$method" \
    --header 'X-API-Key: attacker-key' \
    --output /dev/null \
    --write-out '%{http_code}' \
    "http://127.0.0.1:$PROXY_PORT$path" \
    || true
}

request_selected() {
  local status
  status="$(request "$1" "$2")"
  [[ "$status" == "200" ]] || {
    printf 'approved monitoring request returned HTTP %s: %s %s\n' "$status" "$1" "$2" >&2
    exit 1
  }
}

# Given: a browser-supplied attacker header and a future room name.
# When: every approved GET-only monitoring endpoint is requested.
request_selected GET '/api/sensors/monitoring/range/Future-Room'
request_selected GET '/api/sensors/monitoring/stats/Future-Room'
request_selected GET '/api/sensors/monitoring/live/Future-Room/front'
request_selected GET '/api/sensors/monitoring/live/Future-Room/back'
request_selected GET '/api/monitoring/control/Future-Room/history'
request_selected GET '/api/monitoring/control/Future-Room/current'
request_selected GET '/api/monitoring/control/Future-Room/projection'

# When: path, encoding, and method boundaries are crossed.
readonly ENCODED_SLASH_STATUS="$(request GET '/api/sensors/monitoring/range/Future%2FRoom')"
readonly DOUBLE_ENCODED_SLASH_STATUS="$(request GET '/api/sensors/monitoring/range/Future%252FRoom')"
readonly SUFFIX_STATUS="$(request GET '/api/sensors/monitoring/range/Future-Room/extra')"
readonly SENSOR_POST_STATUS="$(request POST '/api/sensors/monitoring/range/Future-Room')"
readonly CONTROL_PUT_STATUS="$(request PUT '/api/monitoring/control/Future-Room/current')"
readonly ARBITRARY_PATH_STATUS="$(request GET '/api/controls/open')"

for status in "$ENCODED_SLASH_STATUS" "$DOUBLE_ENCODED_SLASH_STATUS" "$SUFFIX_STATUS" "$SENSOR_POST_STATUS" "$CONTROL_PUT_STATUS" "$ARBITRARY_PATH_STATUS"; do
  [[ "$status" == "200" ]] || {
    printf 'dummy upstream request did not complete: HTTP %s\n' "$status" >&2
    exit 1
  }
done

# Then: only the approved reads replace the attacker key with one server key.
python3 - "$SANDBOX/upstream.jsonl" "$DUMMY_API_KEY" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

records = [json.loads(line) for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()]
expected_key = sys.argv[2]
selected = records[:7]
unselected = records[7:]

if len(selected) != 7 or len(unselected) != 6:
    raise SystemExit(f"unexpected upstream request count: {len(records)}")
if any(record["api_keys"] != [expected_key] for record in selected):
    raise SystemExit(f"approved monitoring request did not receive exactly one injected key: {selected}")
if any(record["api_keys"] != ["attacker-key"] for record in unselected):
    raise SystemExit(f"unapproved request received an injected or altered key: {unselected}")
PY

printf 'caddy monitoring API-key policy sandbox test passed\n'
