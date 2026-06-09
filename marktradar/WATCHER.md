# Social-Watcher

Beobachtet kuratierte Akteur-Accounts (Mastodon/Bluesky) und ingestet ihre Posts
in `watched_posts`, verknüpft mit `entities` (Akteur×Thema). Authentifiziert (höhere
Rate-Limits) wenn Secrets gesetzt, sonst öffentlich.

## Nutzung
- Accounts verwalten: Viewer-Panel „WATCHER" oder MCP `watch_add(platform, handle, entity_id?)`.
- Refresh: MCP `watch_refresh()` oder das Viewer-Panel („↻ Posts holen").
- Geo der Posts: verknüpfte Institution (Sitz) → Profil-Ort (Gazetteer) → kein Punkt.

## Secrets (zentral via app.linn.games GitHub→.env, NICHT manueller u-server-Edit)
- `MASTODON_INSTANCE` (default mastodon.social) + `MASTODON_TOKEN`
- `BLUESKY_HANDLE` + `BLUESKY_APP_PASSWORD`

Ohne Credentials bleibt das Verhalten öffentlich (graceful). X/Twitter, Engagement-
Metriken und per-User-OAuth sind bewusst nicht enthalten (eigene Specs).

## Komponenten
- `marktradar/credentials.py` — App-Credential-Provider + Bluesky-Session-JWT (env; user_id-Seam für B).
- `marktradar/watch.py` — CRUD, Parsing, Geo, `refresh`.
- `marktradar/hashtags.py` — `fetch_mastodon`/`fetch_bluesky` nutzen Auth optional; `map_data` liefert `watched`.
