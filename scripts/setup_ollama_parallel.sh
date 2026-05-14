#!/usr/bin/env bash
# Setup parallel inference for the local Ollama service.
#
# What this does:
#   - Drops a systemd override at /etc/systemd/system/ollama.service.d/parallel.conf
#   - Sets OLLAMA_NUM_PARALLEL=2  → 2 concurrent inference slots in one model instance
#   - Sets OLLAMA_MAX_LOADED_MODELS=1  → only one model in VRAM at a time
#   - Sets OLLAMA_KEEP_ALIVE=2h  → model stays warm between requests for 2h
#   - daemon-reloads systemd and restarts ollama
#   - Waits for ollama to come back, prints the active env
#
# Why num_parallel=2 (not 4):
#   8 GB VRAM (RTX 5060 Laptop). Mistral 7B Q4 weights ~4.4 GB + 2× KV-cache @ 2k ctx
#   ≈ 6.5 GB total. Leaves headroom. Two slots is the sweet spot.
#
# Idempotent: safe to re-run.
# Run: sudo bash scripts/setup_ollama_parallel.sh

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Diese Datei muss mit sudo laufen:  sudo bash $0"
  exit 1
fi

OVERRIDE_DIR=/etc/systemd/system/ollama.service.d
OVERRIDE_FILE="$OVERRIDE_DIR/parallel.conf"

mkdir -p "$OVERRIDE_DIR"

cat > "$OVERRIDE_FILE" <<'EOF'
[Service]
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=2h"
EOF

echo "Override geschrieben → $OVERRIDE_FILE"
echo "----------------------------------------"
cat "$OVERRIDE_FILE"
echo "----------------------------------------"

systemctl daemon-reload
systemctl restart ollama
echo "ollama service neu gestartet."

# Warte bis Ollama wieder antwortet
for i in {1..30}; do
  if curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "Ollama ist wieder erreichbar (nach ${i}s)."
    break
  fi
  sleep 1
done

# Verify environment hit the runner
PID=$(pgrep -x ollama | head -1)
if [[ -n "$PID" ]]; then
  echo "----------------------------------------"
  echo "Aktive Ollama-Env (PID $PID):"
  tr '\0' '\n' < "/proc/$PID/environ" | grep -E "OLLAMA_NUM_PARALLEL|OLLAMA_MAX_LOADED_MODELS|OLLAMA_KEEP_ALIVE" || \
    echo "  (kein Ollama-Env-Var gefunden — Service noch am Hochfahren?)"
fi

echo
echo "Fertig. Du kannst diesen Tab jetzt schließen — der Cleaner-Run im anderen Tab"
echo "wird ein paar in-flight Requests verloren haben (ollama-restart) und dann"
echo "automatisch mit dem doppelten Tempo weitermachen."
