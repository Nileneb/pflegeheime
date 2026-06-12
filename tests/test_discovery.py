from pathlib import Path

from marktradar import discovery, ingest

HOMEPAGE = (Path(__file__).parent / "fixtures" / "sample_homepage.html").read_bytes()
FEED = (Path(__file__).parent / "fixtures" / "sample_feed.xml").read_bytes()


def _article(conn, link, n=1):
    conn.execute("INSERT OR IGNORE INTO sources(name,type,url) VALUES('s','rss','http://reg.de/f')")
    for i in range(n):
        conn.execute(
            "INSERT INTO articles(source_id,guid,title,link,source_domain) "
            "VALUES (1,?,?,?,'reg.de')", (f"{link}#{i}", "t", link))
    conn.commit()


def test_find_feeds_autodiscovery_and_heuristic():
    feeds = discovery.find_feeds(HOMEPAGE, "https://example.org/")
    assert "https://example.org/news/feed.xml" in feeds
    assert "https://example.org/rss/presse" in feeds
    # JUNK_FEED + 'feedback' gefiltert
    assert not any("comments/feed" in f or "feedback" in f for f in feeds)


def test_candidate_domains_excludes_known_and_stupid(conn):
    _article(conn, "https://www.neuequelle.de/artikel/1", n=3)
    _article(conn, "https://reg.de/eigener-artikel")          # registrierte Quelle
    _article(conn, "https://www.afd.de/propaganda")           # stupid-Liste
    cands = discovery.candidate_domains(conn)
    assert cands == ["neuequelle.de"]


def test_discover_sources_quarantines_candidates(conn, monkeypatch):
    _article(conn, "https://neuequelle.de/artikel/1")

    def fake_fetch(url):
        return HOMEPAGE if url == "https://neuequelle.de/" else FEED
    monkeypatch.setattr(ingest, "fetch", fake_fetch)
    rep = discovery.discover_sources(conn)
    assert len(rep["found"]) == 1
    row = conn.execute("SELECT enabled, discovered, discovered_from, type FROM sources "
                       "WHERE url=?", (rep["found"][0]["url"],)).fetchone()
    assert row["enabled"] == 0 and row["discovered"] == 1
    assert row["discovered_from"] == "neuequelle.de" and row["type"] == "rss"
    # idempotent: zweiter Lauf findet nichts Neues (UNIQUE url)
    assert discovery.discover_sources(conn, ["neuequelle.de"])["found"] == []


def test_discover_sources_rejects_stupid_domain(conn, monkeypatch):
    monkeypatch.setattr(ingest, "fetch", lambda url: HOMEPAGE)
    rep = discovery.discover_sources(conn, ["afd.de"])
    assert rep["found"] == [] and "quarantänisiert" in rep["errors"][0]["error"]


def test_discover_sources_skips_invalid_feed(conn, monkeypatch):
    def fake_fetch(url):
        return HOMEPAGE if url.endswith("/") else b"<html>kein feed</html>"
    monkeypatch.setattr(ingest, "fetch", fake_fetch)
    rep = discovery.discover_sources(conn, ["neuequelle.de"])
    assert rep["found"] == []


def test_refresh_ignores_discovered_sources(conn, monkeypatch):
    conn.execute("INSERT INTO sources(name,type,url,enabled,discovered) "
                 "VALUES ('kandidat','rss','http://kandidat.de/feed',0,1)")
    conn.commit()
    fetched = []
    monkeypatch.setattr(ingest, "fetch", lambda url: fetched.append(url) or None)
    ingest.refresh(conn)
    assert "http://kandidat.de/feed" not in fetched


def test_approve_source_verifies_then_enables(conn, monkeypatch):
    conn.execute("INSERT INTO sources(name,type,url,enabled,discovered) "
                 "VALUES ('kandidat','rss','http://kandidat.de/feed',0,1)")
    conn.commit()
    sid = conn.execute("SELECT id FROM sources WHERE name='kandidat'").fetchone()["id"]
    monkeypatch.setattr(ingest, "fetch", lambda url: None)
    res = discovery.approve_source(conn, sid)
    assert "error" in res
    assert conn.execute("SELECT enabled FROM sources WHERE id=?", (sid,)).fetchone()["enabled"] == 0
    monkeypatch.setattr(ingest, "fetch", lambda url: FEED)
    res = discovery.approve_source(conn, sid)
    assert res.get("ok")
    row = conn.execute("SELECT enabled, last_status FROM sources WHERE id=?", (sid,)).fetchone()
    assert row["enabled"] == 1 and row["last_status"] == "approved"
    assert "error" in discovery.approve_source(conn, 99999)
