#!/bin/sh
set -e

# Simple start script. Use first arg to choose mode:
#   api     -> run API (gunicorn + uvicorn workers)
#   fetcher -> run fetcher.py (long-running worker)
#   both (default) -> run fetcher in background and API in foreground

MODE="$1"
if [ -z "$MODE" ]; then
  MODE=both
fi

export PYTHONUNBUFFERED=1

if [ "$MODE" = "fetcher" ]; then
  echo "Starting fetcher..."
  exec python packages/backend/fetcher.py

elif [ "$MODE" = "api" ]; then
  echo "Starting API (gunicorn + uvicorn workers)..."
  exec gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} --bind 0.0.0.0:8000 packages.backend.api:app

else
  echo "Starting fetcher (background) and API (foreground)..."
  # start fetcher in background
  python packages/backend/fetcher.py &
  # start api in foreground
  exec gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} --bind 0.0.0.0:8000 packages.backend.api:app
fi
