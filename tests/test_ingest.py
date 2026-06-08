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


def test_refresh_dedups_same_link_across_sources(conn, monkeypatch):
    # Zwei Quellen liefern denselben Artikel (gleicher Link) → nur EIN Artikel je Link.
    conn.execute("INSERT INTO sources(name,type,url,enabled) VALUES('A','rss','https://a/feed',1)")
    conn.execute("INSERT INTO sources(name,type,url,enabled) VALUES('B','rss','https://b/feed',1)")
    conn.commit()
    monkeypatch.setattr(ingest, "fetch", lambda url: FIXTURE.read_bytes())
    monkeypatch.setattr(ingest.embeddings, "embed", lambda t: [0.1] * 1024)
    monkeypatch.setattr(ingest, "classify", lambda t, s: (True, "News", "ok"))
    ingest.refresh(conn)
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
