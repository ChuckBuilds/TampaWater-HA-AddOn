#!/usr/bin/env bash
# Read add-on options (Supervisor writes them to /data/options.json) and start.
set -euo pipefail

OPTS=/data/options.json
get() { python3 -c "import json;print(json.load(open('$OPTS')).get('$1', '$2'))" 2>/dev/null || echo "$2"; }

export TAMPA_USER="$(get tampa_user '')"
export TAMPA_PASS="$(get tampa_pass '')"
export SIDECAR_TOKEN="$(get auth_token '')"
export COMPARE_ENTITIES="$(get compare_entities '')"
export BACKFILL_BILLS="$(get backfill_bills 24)"
export POLL_INTERVAL_HOURS="$(get poll_interval_hours 12)"
export SETUP_WATER_DASHBOARD="$(get setup_water_dashboard True | sed 's/True/1/;s/False/0/')"
export LOG_LEVEL="$(get log_level info)"
export CACHE_DIR="/data/cache"          # persistent + never purged
# SUPERVISOR_TOKEN is injected by Supervisor (homeassistant_api: true)

mkdir -p "$CACHE_DIR"

if [ -z "$TAMPA_USER" ] || [ -z "$TAMPA_PASS" ]; then
  echo "[tampa-water] WARNING: set your City of Tampa portal username/password on the Configuration tab."
fi

echo "[tampa-water] starting (backfill=$BACKFILL_BILLS bills, poll=${POLL_INTERVAL_HOURS}h)"
exec uvicorn server:app --host 0.0.0.0 --port 8099
