#!/bin/bash
# Domino App entry point: rebuilds the frontend when sources are newer than dist, then serves API + static files.
set -e
cd "$(dirname "$0")"
if [ -f package.json ] && command -v npm >/dev/null 2>&1; then
  need=0
  if [ ! -f frontend/dist/index.html ] || [ "${ACT_REBUILD:-0}" = "1" ]; then
    need=1
  elif [ -n "$(find frontend vite.config.js package.json -path frontend/dist -prune -o -type f -newer frontend/dist/index.html -print -quit)" ]; then
    need=1
  fi
  if [ "$need" = "1" ]; then
    [ -d node_modules ] || npm install --no-audit --no-fund
    npm run build
  fi
fi
exec python3 -m backend.main
