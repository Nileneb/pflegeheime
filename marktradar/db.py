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
    return conn


def bootstrap(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(VEC_SCHEMA)
    conn.executescript(LEGACY_SCHEMA)
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
        # Multi-statement DDL (no params, multiple semicolons) → executescript.
        # Individual ALTER TABLE ADD COLUMN errors (duplicate column) are suppressed.
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
