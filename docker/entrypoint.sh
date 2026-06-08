#!/bin/sh
set -e
: "${PFLEGE_DB:=/data/pflege.db}"
mkdir -p "$(dirname "$PFLEGE_DB")"
if [ ! -f "$PFLEGE_DB" ]; then
  if [ -f /seed/pflege.db ]; then
    echo "[entrypoint] seeding $PFLEGE_DB from baked snapshot"
    cp /seed/pflege.db "$PFLEGE_DB"
  else
    echo "[entrypoint] cold start → migrate CSV + seed sources"
    python -m marktradar.migrate || true
    python - <<'PY'
from marktradar import db, sources
c = db.connect(); db.bootstrap(c); sources.seed(c)
PY
  fi
fi
exec python -m marktradar.server
