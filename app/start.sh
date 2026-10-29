#!/bin/sh
# Relocation Camper Watch. Runs in the stock python:3.12-slim image: venv in /venv (reinstalled only when
# requirements change), database migration, then the web app on port 80 with the scheduler inside it.
set -e
VENV=/venv
[ -x "$VENV/bin/python" ] || python -m venv "$VENV"
HASH=$(cat /app/requirements.txt | sha256sum | cut -d' ' -f1)
if [ "$(cat "$VENV/.req-hash" 2>/dev/null)" != "$HASH" ]; then
  "$VENV/bin/pip" install --no-cache-dir --disable-pip-version-check -q -r /app/requirements.txt
  echo "$HASH" > "$VENV/.req-hash"
fi
cd /app
"$VENV/bin/python" -m rcw migrate
exec "$VENV/bin/python" -m uvicorn rcw.web:app --host 0.0.0.0 --port 80 --no-access-log
