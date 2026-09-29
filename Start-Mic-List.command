#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3.11 or newer is required. Install Python from python.org, then run this file again.'
  read -r -p 'Press Return to close.' _
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip install -r requirements.txt
if [ -z "${ADMIN_PASSWORD:-}" ]; then
  read -r -s -p 'Choose your admin password (at least 15 characters): ' ADMIN_PASSWORD
  echo ''
fi
if [ "${#ADMIN_PASSWORD}" -lt 15 ]; then
  echo 'The admin password must contain at least 15 characters.'
  exit 1
fi
export ADMIN_PASSWORD
export LOCAL_DEV=1
export DATABASE_PATH="${DATABASE_PATH:-$PWD/data/miclist.sqlite3}"
export SCHEDULER_ENABLED="${SCHEDULER_ENABLED:-1}"
echo ''
echo 'MIC LIST: http://127.0.0.1:8000'
echo 'Administration: http://127.0.0.1:8000/admin'
echo 'Sign in with the admin password you supplied.'
echo 'Leave this window running for scheduled source checks. Press Control-C to stop.'
echo ''
if command -v open >/dev/null 2>&1; then (sleep 2; open http://127.0.0.1:8000) & fi
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
