#!/usr/bin/env sh
set -eu

DATA_DIR="${PAGELAB_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR/reports" "$DATA_DIR/logs"
chmod 0700 "$DATA_DIR"

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8099 \
  --no-proxy-headers \
  --no-access-log
