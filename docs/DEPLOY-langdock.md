# Deploy: Pflege-Marktradar MCP → Langdock (pflege.linn.games)

## Architektur
- **Image** `nileneb/linn-pflege-marktradar:latest` — **code-only, keine PII**.
- **Daten** liegen im Volume `pflege-data` (`/data/pflege.db`), out-of-band geseedet.
- **Embeddings** (bge-m3) → lokaler GPU-Host `192.168.178.11:11434` (resident, kein Swap).
- **qwen-Chat** (ingest/classify/synthesize, Batch) → **Ollama Cloud** (`OLLAMA_CLOUD_API_KEY`).
  Interaktiver Query-Pfad (search/render/discourse/positions) braucht **kein** qwen.
- **Auth**: RS256-JWT, im MCP validiert (`PFLEGE_JWT_*`), Audience **`pflege-marktradar`**.
  Token aus `Authorization: Bearer` **oder** `?token=` (Proxy strippt Header).

## Status
- ✅ Image gebaut + gepusht (PII-frei, JWT-Auth, streamable-http auf :8088).
- ✅ `app.linn.games`: `mcp-pflege`-Service + `docker/common/nginx/pflege.conf` + `pflege-data`-Volume committet → Deploy.
- ✅ CI-Gate (`.github/workflows/ci.yml`): pytest + docker-build.

## Offene Schritte (Betrieb)
1. **DNS + TLS**: `pflege.linn.games` → nginx-Host (wie paper-search).
2. **Deploy-Env** muss liefern: `JWT_PUBLIC_KEY` (base64-PEM, vorhanden), `OLLAMA_CLOUD_API_KEY`
   (GH-Secret), `OLLAMA_URL` (GPU-Host). Optional `PFLEGE_CHAT_MODEL` (Default `qwen3.5:9b` —
   exakten Ollama-Cloud-Tag prüfen, ggf. anpassen).
3. **Volume seeden** mit der reichen DB (sonst startet der MCP mit leerem Schema):
   ```bash
   # pflege.db (lokal gebaut, ~11 MB) auf den Deploy-Host kopieren (LAN), dann:
   docker compose stop mcp-pflege
   docker run --rm -v pflege-data:/data -v "$PWD":/src alpine \
     cp /src/pflege.db /data/pflege.db
   docker compose start mcp-pflege
   ```
4. **Langdock-Integration**: MCP (Streamable-HTTP) auf `https://pflege.linn.games/mcp`,
   Bearer = app.linn.games-JWT mit **Audience `pflege-marktradar`**.
5. **Verify**:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" -X POST https://pflege.linn.games/mcp \
     -H "Authorization: Bearer $JWT" -H "Accept: application/json, text/event-stream" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'   # → 200 + Tool-Liste
   ```

## Frische halten (Batch, kein interaktives qwen)
`refresh_news` + `classify_topics` + `synthesize_positions` sind qwen-Last → als geplanten
Batch laufen lassen (cron/job), nicht pro Langdock-Abfrage. Beispiel im Container:
```bash
docker compose exec mcp-pflege python -c \
  "from marktradar import db,ingest,entities; c=db.connect(); \
   print(ingest.refresh(c)); entities.classify_topics(c)"
```
