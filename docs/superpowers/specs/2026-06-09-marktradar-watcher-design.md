# Marktradar Social-Watcher — Design

**Datum:** 2026-06-09
**Status:** Entwurf — User-Review ausstehend
**Repo:** `nileneb/pflegeheime` (`marktradar/`, master). Secrets via app.linn.games GitHub→`.env`.
**Verbunden mit:** [[project_marktradar_globe_v2]] (Globus, Ranking, Multi-Region, Akteur×Thema).

## Problem / Ziel

Der Marktradar beobachtet Pflege-Diskurs heute **öffentlich** (Mastodon/Bluesky/Google-News,
ohne Auth). Der User will **Beobachtung vertiefen** (nicht publishen): mehr Volumen + gezielt
Akteure verfolgen. Zwei Hebel, eine Vertikale:

1. **Mehr Volumen authentifiziert (#1):** mit eigenen App-Credentials höhere Rate-Limits +
   authentifizierte Suche → mehr Posts/Geopunkte (füllt u.a. Afrika/Amerika weiter).
2. **Account-Watching (#2):** kuratierte Liste von Akteuren (Ministerien, Träger, Journalisten)
   als Live-Post-Strom, verknüpft mit `entities` → die Akteur×Thema-Sicht bekommt echte Posts.

**Engagement-Metriken (#4) und Publishing sind explizit NICHT in diesem Schnitt** (später).

## Scope

**In:**
- Optionale Authentifizierung für Mastodon + Bluesky (gratis, Auth-optional).
- `credentials`-Abstraktion (single-tenant App-Credentials heute; per-User-OAuth später andockbar).
- `watched_accounts`-Tabelle + `watch.py`-Ingestion + `watched_posts`-Tabelle.
- Surfacing: watched-Posts auf dem Globus (eigener Marker-Typ) + Live-Strom im Akteur×Thema-Panel.

**Out (YAGNI / später):**
- X/Twitter (bezahlter API-Tier ~$100/Mo) — Seam vorhanden, nicht implementiert.
- Engagement/Reichweite (#4).
- Publishing/Posten (ursprüngliche SMM-Idee).
- Per-User-OAuth-Flow (Ansatz B) — Datenmodell-Seam gelegt, Flow nicht gebaut.
- Google-Rank/SEO-Tool — eigenes Subsystem, eigene Spec.

## Architektur

Single-tenant, alles im Marktradar. Der B-Pfad ("später per-User-OAuth") bleibt offen durch
**eine** Abstraktion: Ingestion-Code ruft `credentials.get(platform)` statt verstreuter
`os.getenv`. Heute liest das die App-Credentials aus dem Secret; später kann dieselbe Funktion
ein per-User-Token aus einer `social_connections`-Tabelle ziehen — ohne Bruch am Aufrufer.

```
Daemon/refresh ─► credentials.get(platform)
                     │
        ┌────────────┴─────────────┐
        ▼                          ▼
  Auth-Hashtag-Suche         watch.refresh(conn)
  (höhere Limits)            (Account-Timelines)
        │                          │
        ▼                          ▼
   hashtag_posts              watched_posts (entity_id)
        └──────────┬───────────────┘
                   ▼
        Globus (Union) + Akteur×Thema-Panel
```

## Komponenten

### 1. `marktradar/credentials.py` (neu) — Credential-Provider
- `get(platform: str) -> dict | None`: liefert `{"token": ...}` (Mastodon), `{"handle":...,
  "app_password":...}` (Bluesky), o.ä. Heute aus env-Secrets:
  `MASTODON_INSTANCE`, `MASTODON_TOKEN`, `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD`.
- Fehlt ein Credential → `None` (graceful: Fetcher fällt auf öffentliches Verhalten zurück).
- **B-Seam:** Signatur nimmt optional `user_id=None`; heute ignoriert. Später Override-Hook,
  der per-User-Token zieht. Dokumentiert, aber NICHT verdrahtet.

### 2. Auth in `hashtags.py`-Fetchern (#1)
- `fetch_mastodon(...)`: wenn `credentials.get("mastodon")` ein Token liefert →
  `Authorization: Bearer <token>` + Instanz aus Secret. Höhere Limits, authentifizierte Suche.
- `fetch_bluesky(...)`: wenn App-Passwort vorhanden → einmalig Session-JWT erzeugen
  (`com.atproto.server.createSession`), gecacht je Prozess; sonst öffentlich wie heute.
- **Invariante:** ohne Credential identisches öffentliches Verhalten (kein Regressions-Bruch;
  bestehende Tests bleiben grün).

### 3. Account-Watching (#2)
- **Tabelle `watched_accounts`:** `id, platform, handle, account_id (nullable),
  entity_id (nullable FK→entities), label, active, added`. UNIQUE(platform, handle).
- **Tabelle `watched_posts`:** `id, account_id FK→watched_accounts, entity_id (nullable),
  url, author, content, lat, lon, published, fetched_at`. UNIQUE(account_id, url).
- **`marktradar/watch.py`:**
  - `add(conn, platform, handle, *, entity_id=None, label=None)` / `list`/`remove`/`set_active`.
  - `fetch_account_posts(platform, handle)`: Mastodon `/api/v1/accounts/:id/statuses`
    (Handle→id via `/accounts/lookup`), Bluesky `app.bsky.feed.getAuthorFeed`. Öffentlich;
    nutzt Auth wenn vorhanden (höhere Limits).
  - `refresh(conn)`: je aktivem Account fetchen, `INSERT OR IGNORE` in `watched_posts`,
    Geo bestimmen, `entity_id` durchreichen. Idempotent. Fehler je Account isoliert + gemeldet
    (NICHT verschluckt).
  - **Geo:** verknüpfte Entität ist Institution (`ranking.match_institution(name)`) → deren Sitz;
    sonst Profil-Ort via `geocode(location_text)`; sonst kein Punkt (Post erscheint nur im Panel).

### 4. Surfacing (`hashtags.map_data` + `viewer.py`)
- `map_data` ergänzt `watched` (Punkte aus `watched_posts` mit lat/lon, Marker-Typ `watch`,
  Farbe nach Entitäts-Typ via `entities.ACTOR_COLORS`) und reichert `actors` je Hashtag um den
  jüngsten watched-Post je Akteur an (Live-Strom-Vorschau).
- `viewer.py`: watched-Marker als eigener Typ (z.B. Ring/Raute statt Kugel); im Akteur-Chip-Panel
  zeigt ein Klick auf einen Akteur dessen letzte beobachtete Posts (klickbare Quell-URLs).
- **Manage-UI:** `watched_accounts`-CRUD analog zum bestehenden Hashtag-CRUD im Viewer
  (Add/Remove/Toggle, optional Entität verknüpfen). Stellt sicher, dass das Feature einen
  echten UI-Eintrittspunkt hat (kein „gebaut aber nie genutzt").

## Error Handling
- Fehlendes Credential → graceful Public-Fallback, kein Fehler.
- Auth-Fehler (abgelaufenes Token) → WARNING + Public-Fallback + `watched_accounts.status` bzw.
  Report-Eintrag; NIE stummschalten.
- Account nicht gefunden / Plattform down → Fehler je Account isoliert, andere laufen weiter,
  Fehler im Refresh-Report (Muster wie `hashtags.refresh`).

## Testing
- `credentials.get` env-Fallback + None-Verhalten.
- `watch.add/list/remove/set_active` (CRUD, UNIQUE).
- Account-Post-Parsing mit Fixtures (Mastodon-/Bluesky-Response-JSON) → normalisierte Posts.
- Geo der watched-Posts (Institution-Sitz vs Profil-Ort vs kein Punkt).
- `map_data` enthält `watched` + angereicherte `actors`.
- Netz-Fetcher via Monkeypatch (Muster wie `test_multilingual`/`test_regions`).
- Invariante: Fetcher ohne Credential = unverändertes öffentliches Verhalten (Bestandstests grün).

## Deployment
- Secrets (`MASTODON_*`, `BLUESKY_*`) zentral via app.linn.games GitHub-vars/secrets →
  `.env.mayring`/pflege-env (Muster [[feedback_env_single_source_github]]), NICHT manueller
  u-server-Edit. In pflegeheime nur `.env.example`-Einträge.
- pflege-Image: manuell save|load + recreate (kein CI fürs Image), Migrationen via `db.bootstrap`
  ALTER-Pfad (auf existierender Prod-DB testen).

## B-Pfad (offen, nicht gebaut)
Per-User-OAuth dockt später an, indem (a) `credentials.get(platform, user_id)` einen
Per-User-Override bekommt, der Token aus einer neuen `social_connections`-Tabelle (Muster
`GithubConnection`, `access_token` `'encrypted'`-Cast) zieht, und (b) Socialite-Connect-
Controller in app.linn.games den OAuth-Flow fahren. `watched_accounts` bleibt user-agnostisch
(single-tenant) bis dahin. Kein Code dafür in diesem Schnitt.
