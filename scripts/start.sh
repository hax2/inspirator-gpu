#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -x .venv/bin/python && -f .env ]] || { echo 'Run bash scripts/install.sh first.' >&2; exit 1; }
mkdir -p data logs
if [[ -f data/server.pid ]]; then
  read -r pid < data/server.pid
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    command_line="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    if [[ "$command_line" == *"$ROOT/scripts/run_server.py"* ]]; then
      echo "Inspirator is already running (PID $pid)."
      exit 0
    fi
  fi
fi
nohup "$ROOT/.venv/bin/python" "$ROOT/scripts/run_server.py" >> "$ROOT/logs/server.log" 2>&1 < /dev/null &
pid=$!
printf '%s\n' "$pid" > data/server.pid
sleep 1
if ! kill -0 "$pid" 2>/dev/null; then
  echo 'Startup failed. Check logs/server.log.' >&2
  exit 1
fi
echo "Started Inspirator (PID $pid). Model loading may take a few minutes."
echo 'Readiness: curl http://127.0.0.1:8010/health'
echo 'Logs:      tail -f logs/server.log'
