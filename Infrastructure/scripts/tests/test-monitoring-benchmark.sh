#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT
LOG_PATH="$TMPDIR/requests.log"
PORT_PATH="$TMPDIR/port"
MAX_PATH="$TMPDIR/max-concurrency"

python3 - "$LOG_PATH" "$PORT_PATH" "$MAX_PATH" <<'PY' &
import json
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log_path = pathlib.Path(sys.argv[1])
port_path = pathlib.Path(sys.argv[2])
max_path = pathlib.Path(sys.argv[3])
lock = threading.Lock()
active = 0
maximum = 0


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            max_path.write_text(str(maximum), encoding="utf-8")
        try:
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(f"GET {self.path}\n")
            if "/projection" in self.path:
                time.sleep(0.20)
            elif "/range/" in self.path:
                time.sleep(0.15)
            else:
                time.sleep(0.02)
            if "/range/" in self.path:
                body = b'{"payload":"' + (b"x" * 1010) + b'"}'
            elif "/stats/" in self.path:
                body = b"not-json"
            else:
                body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass
        finally:
            with lock:
                active -= 1

    def do_POST(self):
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"POST {self.path}\n")
        self.send_response(405)
        self.end_headers()

    def log_message(self, _format, *_args):
        return


server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
port_path.write_text(str(server.server_port), encoding="utf-8")
threading.Thread(target=server.serve_forever, daemon=True).start()
while True:
    time.sleep(1)
PY
FIXTURE_PID=$!
trap 'kill "$FIXTURE_PID" 2>/dev/null || true; rm -rf "$TMPDIR"' EXIT

for _ in $(seq 1 100); do
  [[ -s "$PORT_PATH" ]] && break
  sleep 0.01
done
[[ -s "$PORT_PATH" ]]
BASE_URL="http://127.0.0.1:$(<"$PORT_PATH")"
OUTPUT="$TMPDIR/result.json"

python3 - "$ROOT" "$BASE_URL" "$OUTPUT" "$LOG_PATH" "$MAX_PATH" "${MONITORING_BENCHMARK_EVIDENCE:-}" <<'PY'
import importlib.util
import json
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
base_url = sys.argv[2]
output = pathlib.Path(sys.argv[3])
log_path = pathlib.Path(sys.argv[4])
max_path = pathlib.Path(sys.argv[5])
evidence_path = sys.argv[6]
script = root / "Infrastructure/scripts/monitoring_benchmark.py"
spec = importlib.util.spec_from_file_location("monitoring_benchmark", script)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)

scenarios = []

# Given fixed arrays, when nearest-rank percentiles are requested, then rank is 1-based.
assert module.nearest_rank_percentile([9], 95) == 9
assert module.nearest_rank_percentile(list(range(1, 41)), 95) == 38
assert module.nearest_rank_percentile(list(range(1, 41)), 99) == 40
scenarios.append({"scenario": "nearest-rank fixed arrays including n=1", "result": "PASS", "line": "PASS: nearest-rank fixed arrays including n=1"})

# Given rejected arguments, when validation runs before any benchmark, then the fixture log is empty.
for rejected in (
    ["--method", "POST"],
    ["--header", "Authorization: secret"],
    ["--base-url", "http://example.com"],
):
    completed = subprocess.run([
        sys.executable, str(script), "--base-url", base_url, "--location", "Flower",
        "--target", "range", "--samples", "1", "--warmup", "0", "--output", str(output), *rejected,
    ], check=False, capture_output=True, text=True)
    assert completed.returncode != 0
assert not log_path.exists() or log_path.read_text(encoding="utf-8") == ""
scenarios.append({"scenario": "GET-only host and secret guards have zero requests", "result": "PASS", "line": "PASS: GET-only host and secret guards have zero requests"})

# Given a local range response, when measured after a warmup, then payload bytes and samples match.
command = [
    sys.executable, str(script), "--base-url", base_url, "--location", "Flower",
    "--target", "range", "--samples", "4", "--warmup", "1", "--output", str(output),
]
completed = subprocess.run(command, check=False, capture_output=True, text=True)
assert completed.returncode == 0, completed.stderr
data = json.loads(output.read_text(encoding="utf-8"))
result = data["results"][0]
assert result["status_counts"] == {"200": 4}
assert result["bytes_min"] == 1024
assert result["bytes_max"] == 1024
assert result["bytes_avg"] == 1024.0
assert result["latency_ms"]["samples"] == 4
scenarios.append({"scenario": "payload accounting and measured samples", "result": "PASS", "line": "PASS: payload accounting and measured samples"})

# Given an invalid JSON endpoint, when sampled, then parse errors remain recorded rather than crashing.
parse_output = output.with_name("parse.json")
completed = subprocess.run([
    sys.executable, str(script), "--base-url", base_url, "--location", "Flower",
    "--target", "stats", "--samples", "1", "--warmup", "0", "--output", str(parse_output),
], check=False, capture_output=True, text=True)
assert completed.returncode == 0, completed.stderr
assert json.loads(parse_output.read_text())["results"][0]["error_counts"] == {"parse": 1}
scenarios.append({"scenario": "parse classification", "result": "PASS", "line": "PASS: parse classification"})

# Given a slower local endpoint than its configured timeout, when sampled, then timeout is classified.
timeout_output = output.with_name("timeout.json")
completed = subprocess.run([
    sys.executable, str(script), "--base-url", base_url, "--location", "Flower",
    "--target", "projection", "--samples", "1", "--warmup", "0", "--timeout", "0.05",
    "--output", str(timeout_output),
], check=False, capture_output=True, text=True)
assert completed.returncode == 0, completed.stderr
assert json.loads(timeout_output.read_text())["results"][0]["error_counts"] == {"timeout": 1}
scenarios.append({"scenario": "timeout classification", "result": "PASS", "line": "PASS: timeout classification"})

# Given eight viewers, when a short soak starts, then the fixture observes all concurrent sessions.
soak_output = output.with_name("soak.json")
completed = subprocess.run([
    sys.executable, str(script), "--base-url", base_url, "--location", "Flower",
    "--target", "range", "--soak", "--soak-viewers", "8", "--soak-seconds", "1.25",
    "--output", str(soak_output),
], check=False, capture_output=True, text=True)
assert completed.returncode == 0, completed.stderr
soak = json.loads(soak_output.read_text())["soak"]
assert soak["viewers"] == 8
assert soak["aggregate"]["max_concurrent_in_flight"] == 8
assert int(max_path.read_text(encoding="utf-8")) == 8
scenarios.append({"scenario": "eight independent concurrent sessions", "result": "PASS", "line": "PASS: eight independent concurrent sessions"})

if evidence_path:
    data["fixture_scenarios"] = scenarios
    pathlib.Path(evidence_path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"PASS: {len(scenarios)} monitoring benchmark fixture scenarios")
PY
