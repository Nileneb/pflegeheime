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
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, type TEXT NOT NULL,
    aliases TEXT, region TEXT, source TEXT
);
CREATE TABLE IF NOT EXISTS article_entities (
    article_id INTEGER REFERENCES articles(id),
    entity_id INTEGER REFERENCES entities(id),
    method TEXT,
    PRIMARY KEY (article_id, entity_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS org_units (
    id INTEGER PRIMARY KEY, traeger TEXT, name TEXT NOT NULL, short_name TEXT,
    parent_id INTEGER, level INTEGER, type TEXT, color TEXT, icon TEXT,
    sort_order INTEGER DEFAULT 0, website_url TEXT, description TEXT, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS org_persons (
    id INTEGER PRIMARY KEY, traeger TEXT, first_name TEXT, last_name TEXT, email TEXT,
    role TEXT, unit_id INTEGER REFERENCES org_units(id), type TEXT, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS article_topics (
    article_id INTEGER REFERENCES articles(id),
    topic TEXT NOT NULL,
    stance TEXT,
    valence TEXT,
    position TEXT,
    PRIMARY KEY (article_id, topic)
);
CREATE TABLE IF NOT EXISTS topic_positions (
    topic TEXT NOT NULL,
    label TEXT NOT NULL,
    valence TEXT,
    color TEXT,
    ord INTEGER,
    PRIMARY KEY (topic, label)
);
CREATE TABLE IF NOT EXISTS hashtags (
    id INTEGER PRIMARY KEY, term TEXT NOT NULL UNIQUE, color TEXT,
    active INTEGER DEFAULT 1, created TEXT
);
CREATE TABLE IF NOT EXISTS hashtag_posts (
    id INTEGER PRIMARY KEY,
    hashtag_id INTEGER REFERENCES hashtags(id),
    source TEXT, url TEXT, author TEXT, content TEXT,
    location_text TEXT, lat REAL, lon REAL, published TEXT, fetched_at TEXT,
    UNIQUE(hashtag_id, url)
);
"""

VEC_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS article_vec USING vec0("
    "article_id INTEGER PRIMARY KEY, embedding FLOAT[1024])"
)

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


def connect(path: str | None = None) -> sqlite3.Connection:
    if path is None:
        path = os.getenv("PFLEGE_DB", DEFAULT_DB)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=8000")  # WHY: paralleler Ingest + Viewer + Backfill
    return conn


def bootstrap(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(VEC_SCHEMA)
    conn.executescript(LEGACY_SCHEMA)
    # WHY: idempotente Migration für bestehende DBs (CREATE TABLE IF NOT EXISTS
    # ergänzt keine neuen Spalten auf existierender article_topics-Tabelle).
    for col in ("valence", "position"):
        try:
            conn.execute(f"ALTER TABLE article_topics ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    # Geo-Koordinaten + Adresse je Org-Einheit (für die 3D-Szene / OSM-Burner) und
    # Meshy-Modell-URL (Phase 2: AI-reskinnte Gebäude).
    for col in ("lat REAL", "lon REAL", "address TEXT", "meshy_url TEXT"):
        try:
            conn.execute(f"ALTER TABLE org_units ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def _tr(sql, params):
    """WHY(pg-migration): Altskripte nutzen psycopg2-paramstyle (%s / %(name)s) und
    PG-only-Syntax. sqlite3 kann nur ?/:name. Übersetzen statt jede execute()-Zeile
    umzuschreiben."""
    import re as _re
    if isinstance(params, dict):
        sql = _re.sub(r"%\((\w+)\)s", r":\1", sql)
    else:
        sql = sql.replace("%s", "?")
    sql = sql.replace("NOW()", "CURRENT_TIMESTAMP")
    # WHY(pg-migration): SQLite 3.37+ added ADD COLUMN IF NOT EXISTS but many builds
    # lack it; strip 'IF NOT EXISTS' from ALTER TABLE ADD COLUMN statements so SQLite
    # accepts the syntax, then swallow 'duplicate column name' errors at call site.
    sql = _re.sub(
        r'(ALTER\s+TABLE\s+\w+\s+ADD\s+COLUMN\s+)IF\s+NOT\s+EXISTS\s+',
        r'\1',
        sql,
        flags=_re.IGNORECASE,
    )
    return (sql, params if params is not None else [])


class _CursorShim:
    def __init__(self, cur): self._cur = cur

    def execute(self, sql, params=None):
        sql_t, params_t = _tr(sql, params)
        # WHY(pg-migration): Altskripte schicken Mehrfach-DDL (CREATE…;CREATE…;) in EINEM
        # execute() — sqlite3 erlaubt nur ein Statement, daher hier am ';' splitten. Greift
        # NUR im param-losen DDL-Fund (Daten-INSERTs tragen params → else-Zweig). Einschränkung:
        # ';' in String-Literalen würde zerschnitten — in der Legacy-DDL kommt das nicht vor.
        # Pro-Statement: 'duplicate column' (= ADD COLUMN IF NOT EXISTS-Emulation) wird
        # geschluckt, jeder andere OperationalError propagiert.
        stripped = sql_t.strip().rstrip(";")
        if not params_t and ";" in stripped:
            for stmt in stripped.split(";"):
                stmt = stmt.strip()
                if not stmt:
                    continue
                try:
                    self._cur.execute(stmt)
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise
        else:
            try:
                self._cur.execute(sql_t, params_t)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        return self
    def executemany(self, sql, seq):
        s, _ = _tr(sql, None); self._cur.executemany(s, seq); return self
    def fetchone(self): return self._cur.fetchone()
    def fetchall(self): return self._cur.fetchall()
    def __iter__(self): return iter(self._cur)
    @property
    def rowcount(self): return self._cur.rowcount
    @property
    def lastrowid(self): return self._cur.lastrowid
    def close(self): self._cur.close()
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


class _ParamShim:
    def __init__(self, conn): self._c = conn
    def cursor(self, cursor_factory=None): return _CursorShim(self._c.cursor())
    def commit(self): self._c.commit()
    def rollback(self): self._c.rollback()
    def close(self): self._c.close()
    def execute(self, sql, params=None): return self._c.execute(*_tr(sql, params))
    def __enter__(self): return self
    def __exit__(self, exc_type, *a):
        if exc_type is None: self._c.commit()
        else: self._c.rollback()


def db_connect():
    """Kompat-Entry für Altskripte: SQLite-Connection mit psycopg2-paramstyle-Shim."""
    conn = connect(os.getenv("PFLEGE_DB", DEFAULT_DB))
    bootstrap(conn)
    return _ParamShim(conn)
