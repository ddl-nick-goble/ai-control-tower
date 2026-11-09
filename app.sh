#!/bin/bash
# Domino App entry point: builds the frontend (if needed) and serves API + static files.
set -e
cd "$(dirname "$0")"
if [ -f package.json ] && command -v npm >/dev/null 2>&1; then
  if [ ! -d frontend/dist ] || [ "${ACT_REBUILD:-0}" = "1" ]; then
    [ -d node_modules ] || npm install --no-audit --no-fund
    npm run build
  fi
fi
exec python3 -m backend.main
