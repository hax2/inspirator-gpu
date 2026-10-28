#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ "$(uname -s)" != Linux ]]; then
  echo 'This installer targets Linux with an assigned NVIDIA GPU.' >&2
  exit 1
fi
command -v nvidia-smi >/dev/null || { echo 'nvidia-smi is missing. Ask your server provider to expose the GPU.' >&2; exit 1; }
PYTHON="${PYTHON_BIN:-python3}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON" -m venv .venv || { echo 'Install the matching python3-venv package, then rerun this installer.' >&2; exit 1; }
fi
.venv/bin/python -m pip install --upgrade pip
# Bundled CUDA runtime; no driver, MIG, system CUDA or other virtualenv changes.
.venv/bin/python -m pip install 'torch==2.14.1' --index-url https://download.pytorch.org/whl/cu126
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
if [[ ! -f .env ]]; then
  .venv/bin/python - <<'PY'
from pathlib import Path
import secrets
template = Path('.env.example').read_text()
Path('.env').write_text(template.replace('IMAGE_API_KEY=', 'IMAGE_API_KEY=' + secrets.token_urlsafe(48), 1))
Path('.env').chmod(0o600)
PY
fi
mkdir -p data logs
.venv/bin/python -m service.preflight --download
.venv/bin/python -m pip freeze > data/installed-versions.txt
echo 'Installed. The API key is in .env; it was not printed.'
echo 'Start: bash scripts/start.sh'
echo 'Logs:  tail -f logs/server.log'
