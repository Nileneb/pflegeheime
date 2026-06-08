#!/bin/sh
set -e
: "${PFLEGE_DB:=/data/pflege.db}"
mkdir -p "$(dirname "$PFLEGE_DB")"
if [ ! -f "$PFLEGE_DB" ]; then
  if [ -f /seed/pflege.db ]; then
    echo "[entrypoint] seeding $PFLEGE_DB from baked snapshot"
    cp /seed/pflege.db "$PFLEGE_DB"
  else
    # WHY: Image enthält bewusst KEINE PII-Daten. Die Prod-DB wird ins Volume
    # geseedet (out-of-band, LAN). Hier nur leeres Schema + Quellen-Registry,
    # damit der Server bootet, bis das Volume befüllt ist / Ingest gelaufen ist.
    echo "[entrypoint] no DB → empty schema + source registry (seed volume out-of-band)"
    python - <<'PY'
from marktradar import db, sources
c = db.connect(); db.bootstrap(c); sources.seed(c)
PY
  fi
fi
exec python -m marktradar.server
