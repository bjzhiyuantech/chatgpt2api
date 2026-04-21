#!/bin/sh
# Start uvicorn in background
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --access-log &

# Start nginx in foreground
nginx -g 'daemon off;'
