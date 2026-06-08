# Pflege-Marktradar MCP (Slice 0+1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Postgres durch SQLite+sqlite-vec ersetzen und einen FastMCP-Server bereitstellen, der einen frischen, semantisch durchsuchbaren Pflege-Newsstrom liefert.

**Architecture:** Neues `marktradar/`-Paket kapselt Storage (`db.py`), Embeddings (`embeddings.py`, Ollama bge-m3), Feed-Ingest (`ingest.py`), Quellen-Registry (`sources.py`), CSV-Migration (`migrate.py`) und MCP-Server (`server.py`). Bau-/Testreihenfolge ist `refresh_news` zuerst (erzeugt Daten), dann `search_news`. Zum Schluss werden die Postgres-Altskripte auf `marktradar.db` umgehängt und Postgres stillgelegt.

**Tech Stack:** Python 3, `fastmcp`/`mcp`, `sqlite-vec`, `feedparser`, `requests`, lokales Ollama (`bge-m3` Embeddings, `qwen3.5:9b` Relevanz), `pytest`.

**Referenz-Spec:** `docs/superpowers/specs/2026-06-08-pflege-marktradar-mcp-design.md`

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `marktradar/__init__.py` | Paket-Marker |
| `marktradar/db.py` | SQLite-Connect (sqlite-vec laden), Schema-Bootstrap, `db_connect`-Kompat-Shim für Altskripte |
| `marktradar/embeddings.py` | `embed`/`embed_batch` via Ollama bge-m3 (1024d) |
| `marktradar/ingest.py` | `fetch`/`parse_feed`/`classify`/`refresh` — RSS holen, diffen, embedden, klassifizieren |
| `marktradar/sources.py` | Tier-1-Seed-Liste + `seed(conn)` |
| `marktradar/migrate.py` | `pflegeheime_final.csv` + `newsletter_kandidaten.csv` → SQLite |
| `marktradar/server.py` | FastMCP-Server + 6 Tools |
| `tests/conftest.py` | pytest-Fixtures (temp-DB, fixture-Feed) |
| `tests/test_db.py`, `tests/test_embeddings.py`, `tests/test_ingest.py`, `tests/test_migrate.py`, `tests/test_search.py` | TDD-Tests |
| `tests/fixtures/sample_feed.xml` | Minimaler RSS-Feed für Ingest-Tests |
| `requirements.txt` | `sqlite-vec`, `mcp`, `pytest` rein; `psycopg2-binary` raus |
| `docker-compose.yml` | gelöscht (Postgres stilllegen) |
| `.gitignore` | `pflege.db*` ergänzt |

Legacy-Repoint (Task 11): `data_cleaner.py`, `scripts/{scan_feeds,social_newsletter,crawl_impressum_gf,websearch_gf}.py`.

---

## Task 1: Projekt-Setup & Dependencies

**Files:**
- Create: `marktradar/__init__.py`
- Modify: `requirements.txt`
- Modify: `.gitignore`

- [ ] **Step 1: requirements.txt anpassen**

Ersetze den Inhalt von `requirements.txt` durch:

```
requests>=2.31
beautifulsoup4>=4.12
lxml>=5.0
feedparser>=6.0
openpyxl>=3.1
dnspython>=2.4
sqlite-vec>=0.1.6
mcp>=1.2
pytest>=8.0
```

- [ ] **Step 2: Dependencies installieren**

Run: `pip install -r requirements.txt`
Expected: `sqlite-vec`, `mcp`, `pytest`, `feedparser` installiert, kein Fehler.

- [ ] **Step 3: Paket-Marker + .gitignore**

Create `marktradar/__init__.py` (leer).
Hänge an `.gitignore` an:

```
pflege.db
pflege.db-wal
pflege.db-shm
.pytest_cache/
__pycache__/
```

- [ ] **Step 4: sqlite-vec lädt verifizieren**

Run: `python -c "import sqlite3, sqlite_vec; c=sqlite3.connect(':memory:'); c.enable_load_extension(True); sqlite_vec.load(c); print(c.execute('select vec_version()').fetchone())"`
Expected: druckt eine Versions-Tupel, z. B. `('v0.1.6',)`.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .gitignore marktradar/__init__.py
git commit -m "chore: marktradar package scaffold, swap psycopg2 for sqlite-vec/mcp"
```

---

## Task 2: Storage-Layer (`db.py`)

**Files:**
- Create: `marktradar/db.py`
- Create: `tests/conftest.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: pytest-Fixture für temp-DB**

Create `tests/conftest.py`:

```python
import pytest
from marktradar import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.bootstrap(c)
    yield c
    c.close()
```

- [ ] **Step 2: Failing test schreiben**

Create `tests/test_db.py`:

```python
def test_bootstrap_creates_all_tables(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','virtual')")}
    assert {"pflegeheime", "articles", "sources", "article_vec"} <= names


def test_article_vec_accepts_1024_dim(conn):
    from sqlite_vec import serialize_float32
    conn.execute("INSERT INTO sources(name,type,url) VALUES('t','rss','http://x')")
    conn.execute("INSERT INTO articles(source_id,guid,title) VALUES(1,'g','t')")
    aid = conn.execute("SELECT id FROM articles").fetchone()["id"]
    conn.execute("INSERT INTO article_vec(article_id, embedding) VALUES (?,?)",
                 (aid, serialize_float32([0.1] * 1024)))
    conn.commit()
    assert conn.execute("SELECT count(*) c FROM article_vec").fetchone()["c"] == 1


def test_guid_unique_per_source(conn):
    import sqlite3
    conn.execute("INSERT INTO sources(name,type,url) VALUES('t','rss','http://x')")
    conn.execute("INSERT INTO articles(source_id,guid,title) VALUES(1,'dup','a')")
    conn.commit()
    try:
        conn.execute("INSERT INTO articles(source_id,guid,title) VALUES(1,'dup','b')")
        conn.commit()
        assert False, "expected UNIQUE violation"
    except sqlite3.IntegrityError:
        pass
```

- [ ] **Step 3: Test laufen lassen — muss fehlschlagen**

Run: `pytest tests/test_db.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'marktradar.db'`.

- [ ] **Step 4: `db.py` implementieren**

Create `marktradar/db.py`:

```python
"""SQLite-Storage für den Pflege-Marktradar (ersetzt den Postgres-Pfad)."""
import os
import sqlite3

import sqlite_vec

DEFAULT_DB = os.getenv("PFLEGE_DB", "pflege.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pflegeheime (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, traeger TEXT, ort TEXT, kreis TEXT,
    adresse TEXT, telefon TEXT, email TEXT, website TEXT, geschaeftsfuehrung TEXT,
    einrichtungsleitung TEXT, social_json TEXT, newsletter TEXT, quality TEXT, quellen TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE, tier INTEGER, region TEXT, enabled INTEGER DEFAULT 1,
    last_fetched TEXT, last_status TEXT, config_json TEXT
);
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES sources(id), source_domain TEXT,
    guid TEXT NOT NULL, link TEXT, title TEXT, summary TEXT, content TEXT,
    published TEXT, fetched_at TEXT, relevant INTEGER, kategorie TEXT, grund TEXT,
    event_type TEXT, UNIQUE(source_id, guid)
);
CREATE INDEX IF NOT EXISTS articles_pub_idx ON articles(published DESC);
"""

VEC_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS article_vec USING vec0("
    "article_id INTEGER PRIMARY KEY, embedding FLOAT[1024])"
)


def connect(path: str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def bootstrap(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(VEC_SCHEMA)
    conn.commit()
```

- [ ] **Step 5: Test laufen lassen — muss bestehen**

Run: `pytest tests/test_db.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add marktradar/db.py tests/conftest.py tests/test_db.py
git commit -m "feat(db): sqlite + sqlite-vec storage layer with schema bootstrap"
```

---

## Task 3: Embeddings-Layer (`embeddings.py`)

**Files:**
- Create: `marktradar/embeddings.py`
- Test: `tests/test_embeddings.py`

- [ ] **Step 1: Failing test schreiben**

Create `tests/test_embeddings.py`:

```python
from marktradar import embeddings


def test_embed_returns_configured_dim(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"embedding": [0.5] * embeddings.EMBED_DIM}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["model"] = json["model"]
        return FakeResp()

    monkeypatch.setattr(embeddings.requests, "post", fake_post)
    vec = embeddings.embed("Korian eröffnet neues Pflegeheim")
    assert len(vec) == embeddings.EMBED_DIM == 1024
    assert captured["model"] == "bge-m3"
    assert captured["url"].endswith("/api/embeddings")


def test_embed_raises_on_ollama_down(monkeypatch):
    def boom(*a, **k): raise embeddings.requests.ConnectionError("refused")
    monkeypatch.setattr(embeddings.requests, "post", boom)
    import pytest
    with pytest.raises(embeddings.requests.ConnectionError):
        embeddings.embed("x")
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

Run: `pytest tests/test_embeddings.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'marktradar.embeddings'`.

- [ ] **Step 3: `embeddings.py` implementieren**

Create `marktradar/embeddings.py`:

```python
"""Ollama-Embeddings (bge-m3, 1024d) — lokal, localhost:11434 (Dev-GPU)."""
import os

import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))


def embed(text: str) -> list[float]:
    r = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    r.raise_for_status()
    vec = r.json().get("embedding")
    if not vec or len(vec) != EMBED_DIM:
        raise ValueError(f"bge-m3 lieferte dim={len(vec) if vec else 0}, erwartet {EMBED_DIM}")
    return vec


def embed_batch(texts: list[str]) -> list[list[float]]:
    return [embed(t) for t in texts]
```

- [ ] **Step 4: Test laufen lassen — muss bestehen**

Run: `pytest tests/test_embeddings.py -v`
Expected: 2 passed.

- [ ] **Step 5: bge-m3 lokal verifizieren (Setup-Check, keine Annahme)**

Run: `ollama list | grep -i bge-m3 || echo "FEHLT: ollama pull bge-m3"`
Expected: zeigt `bge-m3` Zeile. Falls `FEHLT`: `ollama pull bge-m3` ausführen.

- [ ] **Step 6: Commit**

```bash
git add marktradar/embeddings.py tests/test_embeddings.py
git commit -m "feat(embeddings): ollama bge-m3 1024d embed + embed_batch"
```

---

## Task 4: Feed-Parsing (`ingest.py` — Teil 1: pure Funktionen)

**Files:**
- Create: `marktradar/ingest.py`
- Create: `tests/fixtures/sample_feed.xml`
- Test: `tests/test_ingest.py`

- [ ] **Step 1: Fixture-Feed anlegen**

Create `tests/fixtures/sample_feed.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test-Pflegeblog</title>
  <item>
    <title>Caritas eröffnet neues Pflegeheim in Aachen</title>
    <link>https://example.org/news/caritas-aachen</link>
    <guid>https://example.org/news/caritas-aachen</guid>
    <pubDate>Mon, 02 Jun 2026 08:00:00 +0000</pubDate>
    <description>Die Caritas eröffnet eine neue stationäre Einrichtung.</description>
  </item>
  <item>
    <title>Pflegefachkraft (m/w/d) gesucht</title>
    <link>https://example.org/jobs/pfk</link>
    <guid>https://example.org/jobs/pfk</guid>
    <pubDate>Sun, 01 Jun 2026 08:00:00 +0000</pubDate>
    <description>Wir suchen zum nächstmöglichen Zeitpunkt.</description>
  </item>
</channel></rss>
```

- [ ] **Step 2: Failing test für parse_feed + classify(job)**

Create `tests/test_ingest.py`:

```python
from pathlib import Path
from marktradar import ingest

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


def test_parse_feed_extracts_items():
    items = ingest.parse_feed(FIXTURE.read_bytes())
    assert len(items) == 2
    first = items[0]
    assert first["title"].startswith("Caritas eröffnet")
    assert first["guid"] == "https://example.org/news/caritas-aachen"
    assert first["published"].startswith("2026-06-02")  # ISO string


def test_classify_detects_job_deterministically():
    rel, kat, grund = ingest.classify("Pflegefachkraft (m/w/d) gesucht", "Wir suchen")
    assert rel is True
    assert kat == "Stellenanzeige"
```

- [ ] **Step 3: Test laufen lassen — muss fehlschlagen**

Run: `pytest tests/test_ingest.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'marktradar.ingest'`.

- [ ] **Step 4: `ingest.py` Teil 1 implementieren (fetch/parse/classify)**

Create `marktradar/ingest.py`:

```python
"""RSS-Ingest: holen, parsen, diffen, embedden, klassifizieren (SQLite-Pfad)."""
import json
import re
from datetime import datetime, timezone

import feedparser
import requests

from marktradar import db, embeddings
from sqlite_vec import serialize_float32

OLLAMA_HOST = embeddings.OLLAMA_HOST
RELEVANCE_MODEL = "qwen3.5:9b"
HEADERS = {"User-Agent": "pflege-marktradar/0.1 (+research; contact: benedikt.linn@code.berlin)",
           "Accept-Language": "de-DE,de;q=0.9"}
TIMEOUT = 12

JUNK_FEED = re.compile(r"/comments/feed|/sample-page/|comment-feed|/feed/feed", re.I)
JOB_RE = re.compile(
    r"\(\s*m\s*/?\s*w\s*/?\s*d\s*\)|\bm\s*/\s*w\s*/\s*d\b|\(\s*w\s*/?\s*m\s*/?\s*d\s*\)|"
    r"stellenangebot|stellenanzeige|stellenausschreibung|\bgesucht\b|wir suchen|"
    r"freie\s+stelle|ausbildungsplatz|\bbewerbung\b|jobangebot|\bm/w\b", re.I)

SYSTEM = (
    "Du bewertest, ob eine Meldung von der Website eines Pflegeheims/Trägers "
    "für einen Branchen-Newsletter relevant ist. RELEVANT = echte Neuigkeit: "
    "Veranstaltung, Eröffnung/Umbau, Auszeichnung, Projekt, Personalie, neues "
    "Angebot, Bericht, Aktion. NICHT RELEVANT = Navigation, Cookie-/Datenschutz-/"
    "Impressum-Text, Platzhalter, reine Rechtstexte, Sammel-Übersichtsseiten. "
    'Antworte NUR als JSON: {"relevant": true/false, "kategorie": "<1-2 Worte>", "grund": "<kurz>"}')


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def fetch(url: str) -> bytes | None:
    if JUNK_FEED.search(url):
        return None
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, verify=False)
    if r.status_code != 200 or not r.content:
        return None
    return r.content


def parse_feed(content: bytes) -> list[dict]:
    fp = feedparser.parse(content)
    items = []
    for e in fp.entries:
        title = _clean(getattr(e, "title", ""))
        if not title:
            continue
        link = (getattr(e, "link", "") or "")[:1000]
        guid = (getattr(e, "id", "") or link or title)[:500]
        struct = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        pub = None
        if struct:
            try:
                pub = datetime(*struct[:6], tzinfo=timezone.utc).isoformat()
            except (ValueError, TypeError):
                pub = None
        items.append(dict(guid=guid, link=link, title=title[:500],
                          summary=_clean(getattr(e, "summary", ""))[:1200], published=pub))
    return items


def is_job(title: str, summary: str = "") -> bool:
    return bool(JOB_RE.search(f"{title} {summary}"))


def classify(title: str, summary: str) -> tuple:
    if is_job(title, summary):
        return True, "Stellenanzeige", "Stellenausschreibung (regelbasiert erkannt)"
    payload = {"model": RELEVANCE_MODEL, "format": "json", "stream": False, "think": False,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": f"Titel: {title}\nText: {summary}"[:1500]}],
               "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 120}}
    r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=90)
    r.raise_for_status()
    d = json.loads(r.json().get("message", {}).get("content", "") or "{}")
    return bool(d.get("relevant")), (d.get("kategorie") or "")[:60], (d.get("grund") or "")[:200]
```

- [ ] **Step 5: Test laufen lassen — muss bestehen**

Run: `pytest tests/test_ingest.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add marktradar/ingest.py tests/fixtures/sample_feed.xml tests/test_ingest.py
git commit -m "feat(ingest): feed fetch/parse + deterministic job classify"
```

---

## Task 5: Ingest-Orchestrierung (`ingest.refresh`)

**Files:**
- Modify: `marktradar/ingest.py`
- Test: `tests/test_ingest.py`

- [ ] **Step 1: Failing test für refresh (Ollama+Netz gemockt)**

Hänge an `tests/test_ingest.py` an:

```python
def test_refresh_inserts_embeds_and_dedups(conn, monkeypatch):
    conn.execute("INSERT INTO sources(name,type,url,enabled) "
                 "VALUES('Test','rss','https://example.org/feed',1)")
    conn.commit()
    monkeypatch.setattr(ingest, "fetch", lambda url: FIXTURE.read_bytes())
    monkeypatch.setattr(ingest.embeddings, "embed", lambda t: [0.1] * 1024)
    monkeypatch.setattr(ingest, "classify", lambda t, s: (True, "News", "ok"))

    rep = ingest.refresh(conn)
    assert rep["new"] == 2
    assert conn.execute("SELECT count(*) c FROM articles").fetchone()["c"] == 2
    assert conn.execute("SELECT count(*) c FROM article_vec").fetchone()["c"] == 2

    rep2 = ingest.refresh(conn)  # idempotent: keine Duplikate
    assert rep2["new"] == 0
    assert conn.execute("SELECT count(*) c FROM articles").fetchone()["c"] == 2


def test_refresh_records_fetch_error(conn, monkeypatch):
    import requests as _r
    conn.execute("INSERT INTO sources(name,type,url) VALUES('Bad','rss','https://bad/feed')")
    conn.commit()
    monkeypatch.setattr(ingest, "fetch",
                        lambda url: (_ for _ in ()).throw(_r.Timeout("timeout")))
    rep = ingest.refresh(conn)
    assert rep["errors"]
    status = conn.execute("SELECT last_status FROM sources WHERE name='Bad'").fetchone()["last_status"]
    assert "error" in status.lower()
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

Run: `pytest tests/test_ingest.py::test_refresh_inserts_embeds_and_dedups -v`
Expected: FAIL mit `AttributeError: module 'marktradar.ingest' has no attribute 'refresh'`.

- [ ] **Step 3: `refresh` implementieren**

Hänge an `marktradar/ingest.py` an:

```python
def _domain(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).netloc or "").replace("www.", "")


def refresh(conn, source_filter: str | None = None, since_days: int = 14,
            limit: int | None = None) -> dict:
    """Holt enabled Quellen, diff't über UNIQUE(source_id,guid), embedded + klassifiziert
    neue Items. Fehler je Quelle landen in sources.last_status UND im Report (nicht geschluckt)."""
    q = "SELECT id, name, url FROM sources WHERE enabled=1 AND type='rss'"
    params: list = []
    if source_filter:
        q += " AND (name LIKE ? OR url LIKE ?)"
        params += [f"%{source_filter}%", f"%{source_filter}%"]
    srcs = conn.execute(q, params).fetchall()

    new_total, errors, new_ids = 0, [], []
    now = datetime.now(timezone.utc).isoformat()
    for s in srcs:
        try:
            content = fetch(s["url"])
            if content is None:
                conn.execute("UPDATE sources SET last_fetched=?, last_status=? WHERE id=?",
                             (now, "error: kein Feed/Non-200", s["id"]))
                errors.append({"source": s["name"], "error": "kein Feed/Non-200"})
                conn.commit()
                continue
            items = parse_feed(content)
            if limit:
                items = items[:limit]
            ins = 0
            for it in items:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO articles"
                    "(source_id,source_domain,guid,link,title,summary,published,fetched_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (s["id"], _domain(s["url"]), it["guid"], it["link"], it["title"],
                     it["summary"], it["published"], now))
                if cur.rowcount:
                    new_ids.append(cur.lastrowid)
                    ins += 1
            conn.execute("UPDATE sources SET last_fetched=?, last_status=? WHERE id=?",
                         (now, "ok", s["id"]))
            conn.commit()
            new_total += ins
        except Exception as e:  # WHY: Fehler je Quelle isolieren, aber NICHT verschlucken
            conn.execute("UPDATE sources SET last_fetched=?, last_status=? WHERE id=?",
                         (now, f"error: {e}", s["id"]))
            conn.commit()
            errors.append({"source": s["name"], "error": str(e)})

    # Embedden + klassifizieren der frisch eingefügten Items
    for aid in new_ids:
        row = conn.execute("SELECT title, summary FROM articles WHERE id=?", (aid,)).fetchone()
        text = f"{row['title']} {row['summary'] or ''}".strip()
        vec = embeddings.embed(text)
        conn.execute("INSERT OR REPLACE INTO article_vec(article_id, embedding) VALUES (?,?)",
                     (aid, serialize_float32(vec)))
        rel, kat, grund = classify(row["title"], row["summary"] or "")
        conn.execute("UPDATE articles SET relevant=?, kategorie=?, grund=? WHERE id=?",
                     (None if rel is None else int(rel), kat, grund, aid))
    conn.commit()
    return {"new": new_total, "embedded": len(new_ids), "errors": errors,
            "sources": len(srcs)}
```

- [ ] **Step 4: Tests laufen lassen — müssen bestehen**

Run: `pytest tests/test_ingest.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add marktradar/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): refresh orchestration — diff, embed, classify, error report"
```

---

## Task 6: CSV-Migration (`migrate.py`)

**Files:**
- Create: `marktradar/migrate.py`
- Test: `tests/test_migrate.py`

- [ ] **Step 1: Failing test schreiben**

Create `tests/test_migrate.py`:

```python
from marktradar import migrate


def test_migrate_heime_counts(conn, tmp_path):
    csv = tmp_path / "h.csv"
    csv.write_text(
        "﻿ID;Name;Träger;Ort;Kreis;Adresse;Telefon;E-Mail;Website;"
        "Geschäftsführung;GF-Quelle;Einrichtungsleitung;EL-Quelle;Facebook;Instagram;"
        "LinkedIn;YouTube;Social Media (alle);Newsletter;Qualität;Quellen/Hinweise\n"
        "1;Haus Sonne;Caritas;Aachen;SR Aachen;Str 1;0241 1;a@b.de;http://x;"
        "Max M;web;Erika B;web;;;;;;;OK;notes\n", encoding="utf-8")
    n = migrate.migrate_heime(conn, str(csv))
    assert n == 1
    row = conn.execute("SELECT name, traeger, geschaeftsfuehrung FROM pflegeheime").fetchone()
    assert row["name"] == "Haus Sonne"
    assert row["traeger"] == "Caritas"
    assert row["geschaeftsfuehrung"] == "Max M"


def test_migrate_articles_counts(conn, tmp_path):
    csv = tmp_path / "n.csv"
    csv.write_text(
        "﻿Datum;Quelle (Domain);Titel;Kategorie;Grund;Link\n"
        "2026-06-01;example.org;Eröffnung XY;News;echt;http://x/1\n", encoding="utf-8")
    n = migrate.migrate_articles(conn, str(csv))
    assert n == 1
    row = conn.execute("SELECT title, relevant, source_domain FROM articles").fetchone()
    assert row["title"] == "Eröffnung XY"
    assert row["relevant"] == 1
    assert row["source_domain"] == "example.org"
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

Run: `pytest tests/test_migrate.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'marktradar.migrate'`.

- [ ] **Step 3: `migrate.py` implementieren**

Create `marktradar/migrate.py`:

```python
"""Einmal-Migration: CSV-Bestände → SQLite. Idempotent (re-runnable)."""
import csv
import json
import sys

from marktradar import db


def migrate_heime(conn, csv_path: str) -> int:
    n = 0
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            social = {k: row.get(k) for k in ("Facebook", "Instagram", "LinkedIn", "YouTube")
                      if row.get(k)}
            conn.execute(
                "INSERT INTO pflegeheime(name,traeger,ort,kreis,adresse,telefon,email,website,"
                "geschaeftsfuehrung,einrichtungsleitung,social_json,newsletter,quality,quellen)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row.get("Name"), row.get("Träger"), row.get("Ort"), row.get("Kreis"),
                 row.get("Adresse"), row.get("Telefon"), row.get("E-Mail"), row.get("Website"),
                 row.get("Geschäftsführung"), row.get("Einrichtungsleitung"),
                 json.dumps(social, ensure_ascii=False) if social else None,
                 row.get("Newsletter"), row.get("Qualität"), row.get("Quellen/Hinweise")))
            n += 1
    conn.commit()
    return n


def migrate_articles(conn, csv_path: str) -> int:
    n = 0
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            dom = row.get("Quelle (Domain)") or ""
            guid = row.get("Link") or f"{dom}:{row.get('Titel')}"
            cur = conn.execute(
                "INSERT OR IGNORE INTO articles"
                "(source_id,source_domain,guid,link,title,published,kategorie,grund,relevant)"
                " VALUES (NULL,?,?,?,?,?,?,?,1)",
                (dom, guid, row.get("Link"), row.get("Titel"), row.get("Datum") or None,
                 row.get("Kategorie"), row.get("Grund")))
            n += cur.rowcount
    conn.commit()
    return n


def main():
    conn = db.connect()
    db.bootstrap(conn)
    h = migrate_heime(conn, "data/pflegeheime_final.csv")
    a = migrate_articles(conn, "out/newsletter_kandidaten.csv")
    print(f"Migriert: {h} Heime, {a} Artikel → {db.DEFAULT_DB}", file=sys.stderr)
    conn.close()


if __name__ == "__main__":
    main()
```

> Hinweis zur UNIQUE-Constraint: `articles` hat `UNIQUE(source_id, guid)`. Bei migrierten
> Artikeln ist `source_id = NULL`; SQLite behandelt NULL in UNIQUE als distinct, daher kein
> Dedup zwischen Migrations-Artikeln — für den historischen Einmal-Import akzeptabel
> (`INSERT OR IGNORE` greift erst bei späteren echten source_ids).

- [ ] **Step 4: Test laufen lassen — muss bestehen**

Run: `pytest tests/test_migrate.py -v`
Expected: 2 passed.

- [ ] **Step 5: Echte Migration ausführen**

Run: `python -m marktradar.migrate`
Expected (stderr): `Migriert: ~2387 Heime, ~486 Artikel → pflege.db`.

- [ ] **Step 6: Commit**

```bash
git add marktradar/migrate.py tests/test_migrate.py
git commit -m "feat(migrate): CSV pflegeheime + newsletter candidates into SQLite"
```

---

## Task 7: Quellen-Registry & Tier-1-Seed (`sources.py`)

**Files:**
- Create: `marktradar/sources.py`
- Test: `tests/test_db.py` (Ergänzung)

- [ ] **Step 1: Failing test für seed-Idempotenz**

Hänge an `tests/test_db.py` an:

```python
def test_seed_is_idempotent(conn):
    from marktradar import sources
    n1 = sources.seed(conn)
    n2 = sources.seed(conn)
    assert n1 == len(sources.TIER1)
    assert n2 == 0  # zweiter Lauf fügt nichts hinzu (UNIQUE url)
    assert conn.execute("SELECT count(*) c FROM sources").fetchone()["c"] == len(sources.TIER1)
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

Run: `pytest tests/test_db.py::test_seed_is_idempotent -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'marktradar.sources'`.

- [ ] **Step 3: `sources.py` implementieren (Tier-1-Kandidaten)**

Create `marktradar/sources.py`:

```python
"""Tier-1-Quellen-Seed. URLs sind Kandidaten — `verify()` prunt tote Feeds."""

TIER1 = [
    {"name": "presseportal Gesundheit/Soziales", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.presseportal.de/rss/themen/gesundheit.rss2"},
    {"name": "BMG Presse", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.bundesgesundheitsministerium.de/rss/presse.xml"},
    {"name": "pflegen-online (Vincentz)", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.pflegen-online.de/rss"},
    {"name": "Altenheim (Vincentz)", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.altenheim.net/rss"},
    {"name": "Ärzte Zeitung Pflege", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.aerztezeitung.de/rss/pflege.rss"},
    {"name": "GKV-Spitzenverband Presse", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.gkv-spitzenverband.de/service/presse/pressemitteilungen/pressemitteilungen.rss"},
    {"name": "MAGS NRW Presse", "type": "rss", "tier": 1, "region": "NRW",
     "url": "https://www.mags.nrw/rss.xml"},
]


def seed(conn) -> int:
    """Fügt Tier-1-Quellen ein (UNIQUE url → idempotent). Gibt Anzahl NEUER Zeilen zurück."""
    n = 0
    for s in TIER1:
        cur = conn.execute(
            "INSERT OR IGNORE INTO sources(name,type,url,tier,region,enabled) "
            "VALUES (?,?,?,?,?,1)", (s["name"], s["type"], s["url"], s["tier"], s["region"]))
        n += cur.rowcount
    conn.commit()
    return n


def verify(conn) -> list[dict]:
    """GETtet jede Quelle, parst als Feed; tote/leere Quellen werden disabled.
    Gibt Statusliste zurück. Nur bei echtem Setup laufen lassen (Netz nötig)."""
    from marktradar import ingest
    out = []
    for row in conn.execute("SELECT id, name, url FROM sources").fetchall():
        try:
            content = ingest.fetch(row["url"])
            ok = bool(content) and len(ingest.parse_feed(content)) > 0
        except Exception:
            ok = False
        conn.execute("UPDATE sources SET enabled=?, last_status=? WHERE id=?",
                     (1 if ok else 0, "ok" if ok else "verify: kein gültiger Feed", row["id"]))
        out.append({"name": row["name"], "ok": ok})
    conn.commit()
    return out
```

- [ ] **Step 4: Test laufen lassen — muss bestehen**

Run: `pytest tests/test_db.py::test_seed_is_idempotent -v`
Expected: PASS.

- [ ] **Step 5: Seed + Live-Verifikation gegen echte DB**

Run:
```bash
python -c "from marktradar import db, sources; c=db.connect(); db.bootstrap(c); print('neu:', sources.seed(c)); [print(x) for x in sources.verify(c)]"
```
Expected: Liste mit `{'name': ..., 'ok': True/False}`. Tote Feeds stehen jetzt auf `enabled=0`
mit `last_status='verify: kein gültiger Feed'` — **nicht raten, sondern Realität abbilden.**
Für jede `ok=False`-Quelle im Plan-Execution-Log notieren (für späteres Nachbessern/Ersatz).

- [ ] **Step 6: Commit**

```bash
git add marktradar/sources.py tests/test_db.py
git commit -m "feat(sources): tier-1 registry seed + live feed verification"
```

---

## Task 8: Hybrid-Suche (`search` in `ingest`/neues `query.py`)

**Files:**
- Create: `marktradar/query.py`
- Test: `tests/test_search.py`

- [ ] **Step 1: Failing test für search_news (Embedding gemockt)**

Create `tests/test_search.py`:

```python
from sqlite_vec import serialize_float32
from marktradar import query


def _seed_articles(conn):
    conn.execute("INSERT INTO sources(name,type,url) VALUES('s','rss','http://x')")
    data = [("Korian Insolvenz Gerücht", [0.9] * 1024),
            ("Tag der offenen Tür im Seniorenheim", [0.1] * 1024)]
    for i, (title, vec) in enumerate(data, 1):
        conn.execute("INSERT INTO articles(source_id,guid,title,published,relevant) "
                     "VALUES (1,?,?,?,1)", (f"g{i}", title, "2026-06-0%d" % i))
        conn.execute("INSERT INTO article_vec(article_id,embedding) VALUES (?,?)",
                     (i, serialize_float32(vec)))
    conn.commit()


def test_search_news_ranks_by_vector(conn, monkeypatch):
    _seed_articles(conn)
    monkeypatch.setattr(query.embeddings, "embed", lambda t: [0.9] * 1024)
    hits = query.search_news(conn, "Korian Pleite", limit=2)
    assert hits[0]["title"] == "Korian Insolvenz Gerücht"


def test_search_news_respects_only_relevant(conn, monkeypatch):
    _seed_articles(conn)
    conn.execute("UPDATE articles SET relevant=0 WHERE title LIKE 'Korian%'")
    conn.commit()
    monkeypatch.setattr(query.embeddings, "embed", lambda t: [0.9] * 1024)
    hits = query.search_news(conn, "Korian", limit=5, only_relevant=True)
    assert all(h["title"] != "Korian Insolvenz Gerücht" for h in hits)
```

- [ ] **Step 2: Test laufen lassen — muss fehlschlagen**

Run: `pytest tests/test_search.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'marktradar.query'`.

- [ ] **Step 3: `query.py` implementieren**

Create `marktradar/query.py`:

```python
"""Lese-Queries für den MCP: Hybrid-News-Suche + Heim-Suche + Stats."""
from sqlite_vec import serialize_float32

from marktradar import embeddings


def search_news(conn, query: str, limit: int = 20, since_days: int | None = None,
                kategorie: str | None = None, only_relevant: bool = True) -> list[dict]:
    qvec = serialize_float32(embeddings.embed(query))
    knn = conn.execute(
        "SELECT article_id, distance FROM article_vec "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (qvec, max(limit * 4, 20))).fetchall()
    if not knn:
        return []
    ids = [r["article_id"] for r in knn]
    dist = {r["article_id"]: r["distance"] for r in knn}
    placeholders = ",".join("?" * len(ids))
    sql = (f"SELECT id,title,summary,link,published,kategorie,grund,source_domain,relevant "
           f"FROM articles WHERE id IN ({placeholders})")
    params = list(ids)
    if only_relevant:
        sql += " AND relevant=1"
    if kategorie:
        sql += " AND kategorie LIKE ?"; params.append(f"%{kategorie}%")
    if since_days is not None:
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
        sql += " AND published >= ?"; params.append(cutoff)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    rows.sort(key=lambda r: dist.get(r["id"], 1e9))
    return rows[:limit]


def search_heime(conn, query: str, limit: int = 20) -> list[dict]:
    like = f"%{query}%"
    rows = conn.execute(
        "SELECT id,name,traeger,ort,kreis,website,geschaeftsfuehrung "
        "FROM pflegeheime WHERE name LIKE ? OR traeger LIKE ? OR ort LIKE ? LIMIT ?",
        (like, like, like, limit)).fetchall()
    return [dict(r) for r in rows]


def db_stats(conn) -> dict:
    one = lambda q: conn.execute(q).fetchone()[0]
    return {
        "heime": one("SELECT count(*) FROM pflegeheime"),
        "artikel": one("SELECT count(*) FROM articles"),
        "artikel_relevant": one("SELECT count(*) FROM articles WHERE relevant=1"),
        "artikel_embedded": one("SELECT count(*) FROM article_vec"),
        "quellen": one("SELECT count(*) FROM sources"),
        "quellen_aktiv": one("SELECT count(*) FROM sources WHERE enabled=1"),
    }
```

- [ ] **Step 4: Test laufen lassen — muss bestehen**

Run: `pytest tests/test_search.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add marktradar/query.py tests/test_search.py
git commit -m "feat(query): hybrid vector news search, heim search, db stats"
```

---

## Task 9: MCP-Server (`server.py`)

**Files:**
- Create: `marktradar/server.py`
- Test: manueller Smoke (MCP-Server schwer unit-testbar; Logik liegt in getesteten Modulen)

- [ ] **Step 1: `server.py` implementieren**

Create `marktradar/server.py`:

```python
"""FastMCP-Server: Pflege-Marktradar. Tools delegieren an getestete Module."""
from mcp.server.fastmcp import FastMCP

from marktradar import db, ingest, query

mcp = FastMCP("pflege-marktradar")
_conn = db.connect()
db.bootstrap(_conn)


@mcp.tool()
def refresh_news(source_filter: str | None = None, since_days: int = 14,
                 limit: int | None = None) -> dict:
    """Holt frische Artikel aus allen aktiven RSS-Quellen, embedded + klassifiziert sie.
    Gibt Report zurück: {new, embedded, sources, errors}."""
    return ingest.refresh(_conn, source_filter, since_days, limit)


@mcp.tool()
def search_news(query_text: str, limit: int = 20, since_days: int | None = None,
                kategorie: str | None = None, only_relevant: bool = True) -> list[dict]:
    """Semantische + gefilterte Suche über den Newsstrom (Vektor-KNN, bge-m3)."""
    return query.search_news(_conn, query_text, limit, since_days, kategorie, only_relevant)


@mcp.tool()
def list_sources() -> list[dict]:
    """Listet die Quellen-Registry inkl. last_fetched/last_status/enabled."""
    return [dict(r) for r in _conn.execute(
        "SELECT id,name,type,url,tier,region,enabled,last_fetched,last_status "
        "FROM sources ORDER BY tier, name").fetchall()]


@mcp.tool()
def add_source(name: str, url: str, type: str = "rss", tier: int = 1,
               region: str = "DE") -> dict:
    """Registriert eine neue Quelle ohne Code-Änderung."""
    cur = _conn.execute(
        "INSERT OR IGNORE INTO sources(name,type,url,tier,region,enabled) VALUES (?,?,?,?,?,1)",
        (name, type, url, tier, region))
    _conn.commit()
    return {"added": bool(cur.rowcount), "name": name, "url": url}


@mcp.tool()
def search_heime(query_text: str, limit: int = 20) -> list[dict]:
    """Durchsucht die Heim-/Träger-Basis (Name/Träger/Ort)."""
    return query.search_heime(_conn, query_text, limit)


@mcp.tool()
def db_stats() -> dict:
    """Zählungen: Heime, Artikel, relevant, embedded, Quellen."""
    return query.db_stats(_conn)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Server-Import + Tool-Registrierung smoke-testen**

Run: `python -c "from marktradar import server; print(sorted(t.name for t in server.mcp._tool_manager.list_tools()))"`
Expected: `['add_source', 'db_stats', 'list_sources', 'refresh_news', 'search_heime', 'search_news']`
(Falls die FastMCP-API `list_tools()` async ist: `python -c "import asyncio; from marktradar import server; print([t.name for t in asyncio.run(server.mcp.list_tools())])"`.)

- [ ] **Step 3: db_stats gegen die migrierte DB**

Run: `python -c "from marktradar import db, query; c=db.connect(); print(query.db_stats(c))"`
Expected: `{'heime': ~2387, 'artikel': ~486, ...}` — Zahlen > 0.

- [ ] **Step 4: Commit**

```bash
git add marktradar/server.py
git commit -m "feat(server): FastMCP server with 6 marktradar tools"
```

---

## Task 10: End-to-End Live-Lauf (echter Ingest + Suche)

**Files:** keine (Verifikation gegen echte Quellen/Ollama)

- [ ] **Step 1: Voraussetzungen prüfen**

Run: `ollama list | grep -E "bge-m3|qwen3.5" && curl -s http://localhost:11434/api/tags >/dev/null && echo OLLAMA_OK`
Expected: beide Modelle gelistet + `OLLAMA_OK`.

- [ ] **Step 2: Quellen seed + verify (falls Task 7 Step 5 übersprungen)**

Run: `python -c "from marktradar import db, sources; c=db.connect(); db.bootstrap(c); sources.seed(c); print(sources.verify(c))"`
Expected: Statusliste; mind. eine Quelle `ok=True`.

- [ ] **Step 3: Echten Ingest fahren (klein anfangen)**

Run: `python -c "from marktradar import db, ingest; c=db.connect(); print(ingest.refresh(c, since_days=30, limit=20))"`
Expected: `{'new': N>0, 'embedded': N, 'errors': [...], 'sources': M}`. Fehler je Quelle stehen in `errors` — nicht ignorieren, im Log festhalten.

- [ ] **Step 4: Semantische Suche gegen frische Daten**

Run: `python -c "from marktradar import db, query; c=db.connect(); [print(h['published'], h['title']) for h in query.search_news(c, 'Eröffnung neues Pflegeheim', limit=5, only_relevant=False)]"`
Expected: 1–5 thematisch passende Treffer, nach Vektor-Distanz sortiert.

- [ ] **Step 5: Gesamte Testsuite grün**

Run: `pytest -v`
Expected: alle Tests passed (db, embeddings, ingest, migrate, search).

- [ ] **Step 6: Commit (falls Fixes nötig waren)**

```bash
git add -A
git commit -m "test: end-to-end live ingest + semantic search verified" || echo "nichts zu committen"
```

---

## Task 11: Postgres stilllegen & Altskripte umhängen

**Files:**
- Delete: `docker-compose.yml`
- Modify: `data_cleaner.py` (db_connect → SQLite-Shim)
- Modify: `scripts/scan_feeds.py`, `scripts/social_newsletter.py`, `scripts/crawl_impressum_gf.py`, `scripts/websearch_gf.py`
- Modify: `marktradar/db.py` (Kompat-Shim + Legacy-Tabellen)

- [ ] **Step 1: Legacy-Tabellen + paramstyle-Shim in `db.py` ergänzen**

Hänge an `marktradar/db.py` an (vor/nach `bootstrap` — Legacy-Tabellen in den Bootstrap aufnehmen):

```python
LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS domain_feeds (
    domain TEXT PRIMARY KEY, rss_url TEXT, news_url TEXT, has_feed INTEGER,
    feed_valid INTEGER, feed_items INTEGER, feed_latest TEXT
);
CREATE TABLE IF NOT EXISTS domain_social (
    domain TEXT PRIMARY KEY, status TEXT, facebook TEXT, instagram TEXT,
    linkedin TEXT, youtube TEXT, newsletter TEXT
);
CREATE TABLE IF NOT EXISTS domain_impressum (
    domain TEXT PRIMARY KEY, impressum_url TEXT, geschaeftsfuehrung TEXT, raw TEXT
);
CREATE TABLE IF NOT EXISTS websearch_gf (
    api_id TEXT PRIMARY KEY, geschaeftsfuehrung TEXT, quelle TEXT, status TEXT
);
"""


class _ParamShim:
    """WHY(pg-migration): Altskripte nutzen psycopg2-paramstyle (%s / %(name)s).
    sqlite3 kann nur ?/:name. Dieser Wrapper übersetzt, damit der Legacy-SQL
    minimal-invasiv weiterläuft, statt jede execute()-Zeile umzuschreiben."""
    def __init__(self, conn): self._c = conn
    def cursor(self): return _CursorShim(self._c.cursor())
    def commit(self): self._c.commit()
    def close(self): self._c.close()
    def execute(self, sql, params=None): return self._c.execute(*_tr(sql, params))


class _CursorShim:
    def __init__(self, cur): self._cur = cur
    def execute(self, sql, params=None): self._cur.execute(*_tr(sql, params)); return self
    def fetchone(self): return self._cur.fetchone()
    def fetchall(self): return self._cur.fetchall()
    @property
    def rowcount(self): return self._cur.rowcount
    @property
    def lastrowid(self): return self._cur.lastrowid
    def close(self): self._cur.close()


def _tr(sql, params):
    import re as _re
    if isinstance(params, dict):
        sql = _re.sub(r"%\((\w+)\)s", r":\1", sql)
    else:
        sql = sql.replace("%s", "?")
    sql = sql.replace("NOW()", "CURRENT_TIMESTAMP").replace("TRUE", "1").replace("FALSE", "0")
    return (sql, params if params is not None else [])


def db_connect():
    """Kompat-Entry für Altskripte: liefert SQLite-Connection mit paramstyle-Shim."""
    conn = connect()
    bootstrap(conn)
    conn.executescript(LEGACY_SCHEMA)
    conn.commit()
    return _ParamShim(conn)
```

Ergänze in `bootstrap()` am Ende: `conn.executescript(LEGACY_SCHEMA)` — damit auch der MCP-Pfad die Legacy-Tabellen kennt. Definiere `LEGACY_SCHEMA` dafür oberhalb von `bootstrap`.

- [ ] **Step 2: Shim-Test**

Hänge an `tests/test_db.py` an:

```python
def test_param_shim_translates_pyformat(tmp_path):
    from marktradar import db
    conn = db.db_connect()
    cur = conn.cursor()
    cur.execute("INSERT INTO domain_feeds(domain, has_feed) VALUES (%s, %s)", ("x.de", 1))
    conn.commit()
    cur.execute("SELECT has_feed FROM domain_feeds WHERE domain=%(d)s", {"d": "x.de"})
    assert cur.fetchone()[0] == 1
    conn.close()
```

Run: `pytest tests/test_db.py::test_param_shim_translates_pyformat -v`
Expected: PASS.

- [ ] **Step 3: `data_cleaner.py` umhängen**

In `data_cleaner.py` die `db_connect`-Funktion (Zeilen ~129-137) ersetzen durch:

```python
def db_connect():
    from marktradar.db import db_connect as _c
    return _c()
```

Entferne `import psycopg2` (falls top-level) — der Import lebt jetzt nur noch im SQLite-Pfad.
PG-spezifisches DDL in `CREATE_TABLE_SQL`/`ALTER_TABLE_SQL` ist harmlos, da die `pflegeheime`-
Tabelle bereits via `marktradar.db` existiert; `ensure_schema` darf no-op-fehlertolerant bleiben,
muss aber `DOUBLE PRECISION`→`REAL`, `TIMESTAMPTZ`→`TEXT`, `BOOLEAN`→`INTEGER` nicht mehr selbst
anlegen. Setze `ensure_schema` auf:

```python
def ensure_schema(conn):
    from marktradar.db import bootstrap
    bootstrap(conn._c if hasattr(conn, "_c") else conn)
```

- [ ] **Step 4: scripts/* umhängen (je Datei)**

Für `scripts/scan_feeds.py`, `scripts/social_newsletter.py`, `scripts/crawl_impressum_gf.py`,
`scripts/websearch_gf.py`:
- Der Import `from data_cleaner import db_connect` bleibt — er liefert jetzt den Shim.
- Entferne lokale `CREATE TABLE`-DDL-Ausführung **nicht** (Shim toleriert sie via SQLite-CREATE);
  ersetze nur PG-only-Syntax in diesen DDLs: `TIMESTAMPTZ`→`TEXT`, `BOOLEAN`→`INTEGER`,
  `BIGSERIAL PRIMARY KEY`→`INTEGER PRIMARY KEY`, `DEFAULT NOW()`→`DEFAULT CURRENT_TIMESTAMP`,
  `ON CONFLICT (...) DO NOTHING`→`OR IGNORE` ist nicht nötig (ON CONFLICT DO NOTHING kennt SQLite).
- `DISTINCT ON (...)`-Queries (nur in `ingest_feeds.py`/exports) → durch `GROUP BY` ersetzen,
  falls das Skript noch genutzt wird; `ingest_feeds.py` wird durch `marktradar/ingest.py`
  abgelöst (nächster Step).

- [ ] **Step 5: `scripts/ingest_feeds.py` als abgelöst markieren**

Ersetze den Inhalt von `scripts/ingest_feeds.py` durch einen Hinweis-Stub:

```python
"""DEPRECATED: abgelöst durch marktradar/ingest.py (SQLite-Pfad).
Nutze:  python -c "from marktradar import db, ingest; ingest.refresh(db.connect())"
oder den MCP-Server (marktradar/server.py, Tool refresh_news)."""
import sys
sys.exit("ingest_feeds.py ist abgelöst — siehe marktradar/ingest.py")
```

- [ ] **Step 6: Postgres stilllegen + Verifikation „kein PG-Rest"**

```bash
git rm docker-compose.yml
grep -rl "psycopg2" *.py scripts/ || echo "KEIN psycopg2-IMPORT MEHR"
grep -rl "TIMESTAMPTZ\|BIGSERIAL\|DOUBLE PRECISION" *.py scripts/ || echo "KEIN PG-DDL MEHR"
```
Expected: beide grep-Zeilen drucken die „KEIN …"-Meldung (keine Treffer). Treffer = Aufgabe
nicht erledigt, jeweilige Datei nachziehen.

- [ ] **Step 7: Volle Testsuite + Altskript-Smoke**

Run: `pytest -v && python -c "from data_cleaner import db_connect; c=db_connect(); print('legacy db_connect OK'); c.close()"`
Expected: alle Tests passed + `legacy db_connect OK`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: decommission Postgres, repoint legacy scripts to SQLite shim"
```

---

## Self-Review-Ergebnis (Plan-Autor)

- **Spec-Coverage:** db.py→T2 · embeddings bge-m3 1024→T3 · ingest/refresh→T4/T5 · sources-Registry+Tier1→T7 · migrate CSV→T6 · MCP 6 Tools (refresh zuerst)→T9 · Hybrid-Suche→T8 · PG-Abbau+alle Altskripte→T11 · Fehler nie geschluckt→T5/T11 (last_status + Report) · End-to-End-Outcome→T10. Slice-5-Viewer bewusst ausgeklammert (Spec).
- **Type-Konsistenz:** `connect`/`bootstrap`/`db_connect`, `embed`/`EMBED_DIM`, `fetch`/`parse_feed`/`classify`/`refresh`, `seed`/`verify`/`TIER1`, `search_news`/`search_heime`/`db_stats` über alle Tasks identisch benannt.
- **Offen für Execution (kein Platzhalter, sondern Realitäts-Check):** Tier-1-Feed-URLs werden in T7/T10 live verifiziert und tote disabled — nicht geraten.
```
