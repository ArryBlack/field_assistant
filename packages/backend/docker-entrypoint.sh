#!/bin/bash
# packages/backend/docker-entrypoint.sh

# Exit immediately if a command exits with a non-zero status.
set -e

# 1. Ensure the media directory exists
mkdir -p /app/media

# 2. Fix the permissions of the mounted volume
# Since this script starts as root, it has the power to do this.
echo "Fixing permissions for /app/media..."
chown -R appuser:appuser /app/media

# 3. Drop privileges and execute the original command
# "$@" represents the command passed from docker-compose (e.g., "/usr/local/bin/start.sh fetcher")
echo "Dropping privileges to appuser and starting service..."
exec gosu appuser "$@"