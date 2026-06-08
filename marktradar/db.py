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
