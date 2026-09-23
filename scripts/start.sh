#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo '请先执行 python3 -m venv .venv && .venv/bin/pip install -r requirements.lock'
  exit 1
fi
exec .venv/bin/python -m uvicorn data_agent.api:app --host 127.0.0.1 --port "${PORT:-8000}"
