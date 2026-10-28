#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f data/server.pid ]] || { echo 'No Inspirator PID file.'; exit 0; }
read -r pid < data/server.pid
[[ "$pid" =~ ^[0-9]+$ ]] || { echo 'Invalid PID file.' >&2; exit 1; }
if ! kill -0 "$pid" 2>/dev/null; then echo 'Inspirator is not running.'; exit 0; fi
command_line="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
[[ "$command_line" == *"$ROOT/scripts/run_server.py"* ]] || { echo 'PID belongs to another process; refusing to stop it.' >&2; exit 1; }
kill -TERM "$pid"
for _ in {1..30}; do
  if ! kill -0 "$pid" 2>/dev/null; then echo 'Inspirator stopped.'; exit 0; fi
  sleep 1
done
echo 'Still stopping an active inference. Check logs; do not start a second GPU worker.' >&2
exit 1
