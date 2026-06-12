from marktradar import db, migrate


def _cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_bootstrap_idempotent(conn):
    db.bootstrap(conn)
    db.bootstrap(conn)
    assert _cols(conn, "event_types") >= {"name", "pattern", "enabled", "created_by"}
    assert _cols(conn, "topics") >= {"name", "prefilter", "domain", "enabled"}


def test_bootstrap_migrates_old_schema(tmp_path):
    # Simuliert die prod-Volume-DB: Tabellen existieren bereits OHNE die neuen Spalten.
    c = db.connect(str(tmp_path / "old.db"))
    c.executescript(
        "CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
        "type TEXT NOT NULL, aliases TEXT, region TEXT, source TEXT);"
        "CREATE TABLE sources (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
        "type TEXT NOT NULL, url TEXT NOT NULL UNIQUE, tier INTEGER, region TEXT, "
        "enabled INTEGER DEFAULT 1, last_fetched TEXT, last_status TEXT, config_json TEXT);")
    c.execute("INSERT INTO entities(name,type) VALUES ('Korian','traeger')")
    db.bootstrap(c)
    assert _cols(c, "entities") >= {"confidence", "review", "ref_table", "ref_id"}
    assert _cols(c, "sources") >= {"discovered", "discovered_from"}
    row = c.execute("SELECT name, review FROM entities").fetchone()
    assert row["name"] == "Korian"
    assert row["review"] in (0, None)
    c.close()


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
