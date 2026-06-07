# Pflege-Marktradar MCP — Design (Slice 0 + 1)

**Datum:** 2026-06-08
**Status:** Entwurf zur Review
**Kontext:** Bestehendes NRW-Scraper-Projekt (`data/pflegeheime_final.csv`, 2.388 Zeilen;
Postgres + Feed-Ingest-Pipeline) wird zu einem **Markt-Intelligence-MCP** umgebaut.

## Leitidee

Der **Newsstrom ist das Produkt**, Entitäten (Träger, Hersteller, Behörden, Heime) sind der
Kontext, an dem Signale verankert werden. Prinzip **„Signal-first, Entity-lazy"**: laufender,
durchsuchbarer Newsstrom + MCP zuerst; Entitäten materialisieren sich aus dem, was die News
erwähnen. Kein erschöpfendes Vorab-Register.

Primärer Konsum: **interaktiv via MCP, on demand**. Kein Scheduler in dieser Stufe.

## Scope dieses Specs

Dieser Spec deckt **Slice 0 (Fundament)** + **Slice 1 (Ingest-Repoint)** ab. Slices 2–4 sind
eigene Specs:

- **Slice 2 — Entity-Layer:** `entities`-Tabelle, Embedding-Tagging Artikel→Entität,
  Event-Klassifikation, Tools `get_entity`/`timeline`.
- **Slice 3 — Signal-Goldminen:** Insolvenzbekanntmachungen, Bundesanzeiger, Ämter/Parteien.
- **Slice 4 — Register-Backfill:** Pflegelotse/BKK/AOK bundesweit.

Nicht in Scope: Digest-Automatik, Alerting, Postgres beibehalten.

---

## Architektur

```
                 ┌─────────────────────────────────────────┐
                 │           MCP-Server (FastMCP)           │
                 │  search_news · refresh_news ·            │
                 │  list_sources · add_source ·             │
                 │  search_heime · db_stats                 │
                 └───────────────┬─────────────────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                         │
   ┌────▼─────┐           ┌──────▼──────┐          ┌───────▼───────┐
   │ ingest.py│           │   db.py     │          │ embeddings.py │
   │ RSS→diff │──insert──▶│ pflege.db   │◀──vec────│ Ollama        │
   │ →embed   │           │ (SQLite +   │          │ nomic-embed   │
   │ →classify│           │  sqlite-vec)│          │ localhost:11434│
   └────┬─────┘           └─────────────┘          └───────────────┘
        │
   ┌────▼──────────────┐
   │ sources-Registry  │  Tier-1-Feeds (presseportal, BMG, Fachpresse …)
   └───────────────────┘
```

Datenfluss `refresh_news`: enabled sources aus Registry → RSS holen → diff über
`UNIQUE(source_id, guid)` → neue Artikel inserten → Embedding (Ollama) → Relevanz-Klassifikation
(Ollama, bestehender Prompt) → speichern. `last_fetched`/`last_status` je Quelle aktualisiert.

Datenfluss `search_news`: Query embedden → `sqlite-vec` KNN über `article_vec` → plus
Keyword-Filter (LIKE auf title/summary) als Fallback/Verstärkung → gerankte Treffer.

---

## Komponenten (Units mit klarer Verantwortung)

### `db.py` — Storage
- `connect()` → sqlite3-Connection, lädt `sqlite-vec`-Extension, setzt PRAGMAs (WAL).
- `bootstrap(conn)` → CREATE TABLE/VIRTUAL TABLE idempotent.
- Ersetzt `data_cleaner.db_connect()` (psycopg2). **Postgres-Pfad wird entfernt**, nicht
  parallel gehalten.
- Abhängigkeit: `sqlite-vec` (pip wheel, kein Compile).

**Schema:**

```sql
-- Entitäts-Basis (migriert aus pflegeheime_final.csv)
CREATE TABLE IF NOT EXISTS pflegeheime (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    traeger       TEXT,
    ort           TEXT,
    kreis         TEXT,
    adresse       TEXT,
    telefon       TEXT,
    email         TEXT,
    website       TEXT,
    geschaeftsfuehrung   TEXT,
    einrichtungsleitung  TEXT,
    social_json   TEXT,        -- FB/IG/LinkedIn/YouTube zusammengefasst
    newsletter    TEXT,
    quality       TEXT,
    quellen       TEXT
);

-- Newsstrom
CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER REFERENCES sources(id),
    source_domain TEXT,
    guid          TEXT NOT NULL,
    link          TEXT,
    title         TEXT,
    summary       TEXT,
    content       TEXT,
    published     TEXT,         -- ISO 8601
    fetched_at    TEXT,
    relevant      INTEGER,      -- NULL=ungeprüft, 0/1
    kategorie     TEXT,
    grund         TEXT,
    event_type    TEXT,         -- Slice 2; jetzt immer NULL
    UNIQUE(source_id, guid)
);

-- Quellen-Registry (statt hartcodierter Listen)
CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,    -- 'rss' | 'press_page' | 'api'
    url           TEXT NOT NULL UNIQUE,
    tier          INTEGER,          -- 1..4
    region        TEXT,             -- 'DE' | 'NRW' | …
    enabled       INTEGER DEFAULT 1,
    last_fetched  TEXT,
    last_status   TEXT,             -- 'ok' | 'error: …' | 'http 404'
    config_json   TEXT              -- per-Quelle-Parameter (z. B. Scrape-Selektoren)
);

-- Vektor-Index (sqlite-vec). dim = nomic-embed-text = 768.
CREATE VIRTUAL TABLE IF NOT EXISTS article_vec USING vec0(
    article_id INTEGER PRIMARY KEY,
    embedding  FLOAT[768]
);
```

### `embeddings.py` — Vektorisierung
- `embed(text) -> list[float]` über Ollama `POST /api/embeddings`, Model `nomic-embed-text`
  (768 dim), Host `localhost:11434` (Laptop/Dev-Invariante).
- `embed_batch(texts)` für Bulk beim Ingest.
- Dim + Model über env (`EMBED_MODEL`, `EMBED_DIM`) konfigurierbar, Default fix.
- Ollama nicht erreichbar → **expliziter Fehler** (kein Fake-Vektor, kein stilles Skippen).

### `ingest.py` — Feed-Ingest (Slice 1)
- Recycelt Logik aus `scripts/ingest_feeds.py` (feedparser, WP-Müll-Filter, Relevanz-Prompt
  qwen3.5), aber gegen `db.py` statt Postgres.
- `refresh(source_filter=None, since_days=14, limit=None)`:
  1. enabled sources (optional gefiltert) laden
  2. je Quelle Feed holen → Items diffen über `UNIQUE(source_id, guid)`
  3. neue Items inserten, embedden, Relevanz klassifizieren
  4. `last_fetched`/`last_status` schreiben
- Fehler je Quelle isoliert in `last_status` protokolliert; Lauf bricht nicht ab, aber Fehler
  werden **nicht verschluckt** (Rückgabe enthält Fehlerliste).

### `migrate.py` — Einmal-Migration
- `pflegeheime_final.csv` (Semikolon, UTF-8-BOM, deutsche Header) → `pflegeheime`-Tabelle.
- `out/newsletter_kandidaten.csv` → `articles` (als historischer Bestand, `relevant=1`).
- Idempotent (re-runnable); zählt importierte Zeilen und gibt sie aus.

### `server.py` — MCP (FastMCP)
Tools:

| Tool | Signatur | Zweck |
|---|---|---|
| `search_news` | `(query, limit=20, since_days=None, kategorie=None, only_relevant=True)` | Semantische (vec KNN) + Keyword-Suche über `articles` |
| `refresh_news` | `(source_filter=None, since_days=14, limit=None)` | Ingest anstoßen, Report zurück (neu/Fehler je Quelle) |
| `list_sources` | `()` | Registry + `last_fetched`/`last_status`/`enabled` |
| `add_source` | `(name, url, type='rss', tier=1, region='DE')` | Neue Quelle ohne Code-Änderung registrieren |
| `search_heime` | `(query, limit=20)` | Bestehende Entity-Basis (Name/Träger/Ort) durchsuchen |
| `db_stats` | `()` | Zählungen: Heime, Artikel, Quellen, embedded, relevant |

---

## Tier-1-Quellen (Seed für `sources`)

RSS, sofort, klar legal (öffentliche Presse/Open Data):

- **presseportal.de** — news aktuell (dpa-Tochter), Branchen-/Themen-Feeds. Realistischer
  kostenloser „dpa-Endpoint" (echter dpa-Newswire ist kostenpflichtig).
- **BMG** (bundesgesundheitsministerium.de) — Presse-RSS.
- **GKV-Spitzenverband** — Presse.
- **Fachpresse:** Vincentz (*Altenheim*, *CAREkonkret*, *Häusliche Pflege*), *pflegen-online*,
  Ärzte Zeitung (Pflege).
- Länder-Sozialministerien — Presse (zunächst NRW, passend zur Datenbasis).

Konkrete Feed-URLs werden im Implementierungsplan verifiziert (HTTP 200 + valides RSS), nicht
blind hartcodiert. Quelle erreichbar-aber-kein-Feed → als `type='press_page'` markiert,
Scraping erst in Slice 3.

**Legalität:** read-only, robots.txt + Rate-Limits respektieren, ehrlicher User-Agent, kein
Bulk-Redistribute.

---

## Fehlerbehandlung (CLAUDE.md: keine stillen Errors)

- `sqlite-vec`-Load schlägt fehl → **harter Startfehler** im MCP, kein Fallback-ohne-Vektor.
- Ollama-Embeddings nicht erreichbar → Tool gibt expliziten Fehler zurück.
- Feed-Fetch-Fehler → in `sources.last_status` + in der `refresh_news`-Antwort, nicht geschluckt.
- Migration mit fehlender CSV → klare Exception, kein leerer Erfolg.

## Tests

- **Unit:** `bootstrap()` legt alle Tabellen/vec0 an; Migration-Zeilenzahl == CSV-Zeilen;
  guid-Dedup (zweiter Insert gleicher guid → kein Duplikat); Embedding-Dim == `EMBED_DIM`.
- **Integration:** `refresh_news` gegen Fixture-RSS (lokale Datei) → Artikel landen in DB,
  bekommen Embedding; `search_news` mit passender Query findet den Fixture-Artikel über vec.
- **MCP-Smoke:** Server startet, `db_stats` liefert Zählungen > 0 nach Migration.

## Abhängigkeiten (requirements)

Neu: `sqlite-vec`, `fastmcp` (bzw. `mcp`). Raus: `psycopg2-binary`.
Bleibt: `requests`, `feedparser`, `beautifulsoup4`, `lxml`, `openpyxl`.

## Migrationspfad / Aufräumen

- `docker-compose.yml` (Postgres) entfernen.
- `data_cleaner.db_connect()` + alle `import psycopg2` durch `db.py` ersetzen; Skripte unter
  `scripts/`, die Postgres-Tabellen anlegen (`scan_feeds`, `social_newsletter`,
  `crawl_impressum_gf`, `websearch_gf`, `ingest_feeds`), werden in Slice 1/2 schrittweise
  umgehängt — in diesem Slice nur der Newsstrom-Pfad.
- `pflege.db` in `.gitignore` (rebuildbar via `migrate.py`).

## Offene Punkte für den Implementierungsplan

- Exakte Tier-1-Feed-URLs verifizieren (HTTP 200 + valides RSS).
- Embedding-Granularität: `title + summary` vs. Volltext (Default: `title + summary`).
- `nomic-embed-text` lokal verfügbar? Sonst `ollama pull` als Setup-Schritt dokumentieren.
