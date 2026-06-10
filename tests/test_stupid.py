"""Stupid-Liste: Quarantäne für Desinfo-Quellen (AfD-nah).

Invarianten (User-Entscheidung 2026-06-10):
1. Quarantäne-Quellen erscheinen NIE in normalen Outputs (Feed, Karte, Suche).
2. Nur bei EXPLIZITER Nennung des Akteurs in der Anfrage kommen sie mit —
   dann immer mit `warnung`-Feld.
3. Quarantänisiert wird die QUELLE, nicht die Erwähnung: seriöse Presse, die
   ÜBER den Akteur berichtet, bleibt sichtbar.
"""
from marktradar import feeds, hashtags, query, stupid


def _seed_articles(conn):
    conn.execute("INSERT INTO sources(id,name,type,url,tier,region,enabled) "
                 "VALUES (1,'Tagesschau','rss','https://tagesschau.de/feed',1,'DE',1)")
    conn.execute("INSERT INTO sources(id,name,type,url,tier,region,enabled) "
                 "VALUES (2,'AfD','rss','https://afd.de/feed',3,'DE',1)")
    rows = [
        # (id, source_id, domain, link, title, relevant)
        (1, 1, "tagesschau.de", "https://tagesschau.de/pflege-1",
         "Pflegereform beschlossen", 1),
        (2, 2, "afd.de", "https://afd.de/gesundheit-quatsch",
         "AfD Kompakt: mRNA verursacht angeblich Krebs", 1),
        (3, 1, "tagesschau.de", "https://tagesschau.de/afd-bericht",
         "Bericht ÜBER AfD-Falschmeldungen zu Gesundheit", 1),
    ]
    for aid, sid, dom, link, title, rel in rows:
        conn.execute(
            "INSERT INTO articles(id,source_id,source_domain,guid,link,title,summary,published,relevant) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (aid, sid, dom, f"g{aid}", link, title, "s", "2026-06-08T10:00:00+00:00", rel))
    conn.commit()


# ── Registry / Matching ────────────────────────────────────────────────

def test_stupid_matching_domain_handle_entity():
    assert stupid.is_stupid_domain("afd.de") is True
    assert stupid.is_stupid_domain("afdbundestag.de") is True
    assert stupid.is_stupid_domain("tagesschau.de") is False
    assert stupid.is_stupid_handle("@afd@mastodon.social") is True
    assert stupid.is_stupid_handle("afd_fraktion_xy") is True
    assert stupid.is_stupid_handle("pflegekammer_nrw") is False
    assert stupid.is_stupid_entity("AfD") is True
    assert stupid.is_stupid_entity("CDU") is False
    # Quelle vs. Erwähnung: tagesschau-URL ÜBER die AfD ist KEINE Stupid-Quelle
    assert stupid.is_stupid_post({"url": "https://tagesschau.de/afd-bericht"}) is False
    assert stupid.is_stupid_post({"url": "https://afd.de/x"}) is True


def test_explicit_ask_detection():
    assert stupid.explicit_ask("Welche Falschmeldungen hat die AfD zu Gesundheit verbreitet?") is not None
    assert stupid.explicit_ask("alternative für deutschland impfungen") is not None
    # 'afd' nur als Substring (z. B. 'kraftfahrt') zählt NICHT
    assert stupid.explicit_ask("kraftfahrtbundesamt pflege") is None
    assert stupid.explicit_ask("Pflegereform 2026") is None
    assert stupid.explicit_ask(None) is None


# ── Feed: NIE ──────────────────────────────────────────────────────────

def test_feed_never_contains_quarantined_sources(conn):
    _seed_articles(conn)
    xml = feeds.rss(conn)
    assert "tagesschau.de/pflege-1" in xml
    assert "tagesschau.de/afd-bericht" in xml      # Bericht ÜBER bleibt
    assert "afd.de/gesundheit-quatsch" not in xml  # Quelle fliegt IMMER


# ── Suche: nur bei expliziter Nachfrage + Warnung ──────────────────────

def test_search_excludes_quarantined_by_default(conn, monkeypatch):
    _seed_articles(conn)
    monkeypatch.setattr(query.embeddings, "embed", lambda q: [0.0] * 1024)
    # 'mRNA' trifft den Quarantäne-Artikel über den Keyword-Fallback — er darf
    # ohne explizite Akteur-Nennung trotzdem NICHT ausgespielt werden.
    rows = query.search_news(conn, "mRNA")
    links = [r["link"] for r in rows]
    assert "https://afd.de/gesundheit-quatsch" not in links


def test_search_includes_with_warning_on_explicit_ask(conn, monkeypatch):
    _seed_articles(conn)
    monkeypatch.setattr(query.embeddings, "embed", lambda q: [0.0] * 1024)
    rows = query.search_news(conn, "AfD")
    hit = next((r for r in rows if r["link"] == "https://afd.de/gesundheit-quatsch"), None)
    assert hit is not None, "explizite AfD-Nachfrage muss den Treffer liefern"
    assert hit.get("warnung") == stupid.WARNUNG
    # Nicht-Quarantäne-Treffer tragen KEINE Warnung
    clean = next((r for r in rows if "tagesschau" in (r["link"] or "")), None)
    if clean:
        assert "warnung" not in clean


# ── Karte: NIE ─────────────────────────────────────────────────────────

def test_map_data_excludes_quarantined_posts(conn):
    conn.execute("INSERT INTO hashtags(id,term,color,active) VALUES (1,'pflege','#fff',1)")
    conn.execute(
        "INSERT INTO hashtag_posts(hashtag_id,source,url,author,content,lat,lon,published) "
        "VALUES (1,'news','https://afd.de/post-1','AfD Kompakt','quatsch',52.5,13.4,'2026-06-08T10:00:00+00:00')")
    conn.execute(
        "INSERT INTO hashtag_posts(hashtag_id,source,url,author,content,lat,lon,published) "
        "VALUES (1,'mastodon','https://m.social/@afd/2','@afd@m.social','quatsch',52.5,13.4,'2026-06-08T11:00:00+00:00')")
    conn.execute(
        "INSERT INTO hashtag_posts(hashtag_id,source,url,author,content,lat,lon,published) "
        "VALUES (1,'news','https://rki.de/post-3','RKI','seriös',52.5,13.4,'2026-06-08T12:00:00+00:00')")
    conn.commit()

    data = hashtags.map_data(conn)

    point_urls = {p["url"] for p in data["points"]}
    assert "https://rki.de/post-3" in point_urls
    assert "https://afd.de/post-1" not in point_urls
    assert "https://m.social/@afd/2" not in point_urls
    qc_urls = {p["url"] for lst in data["sources"].values() for p in lst}
    assert "https://afd.de/post-1" not in qc_urls
    # author bleibt aus dem Punkt-Payload draußen (Slim-Payload-Invariante)
    assert all("author" not in p for p in data["points"])


# ── Entity-Lookup: direkter Akteur-Lookup = explizit → Warnung ─────────

def test_entity_lookup_of_quarantined_actor_carries_warning(conn):
    _seed_articles(conn)
    conn.execute("INSERT INTO entities(id,name,type,aliases,source) "
                 "VALUES (1,'AfD','partei','[]','seed')")
    conn.execute("INSERT INTO article_entities(article_id,entity_id) VALUES (2,1)")
    conn.execute("INSERT INTO article_entities(article_id,entity_id) VALUES (3,1)")
    conn.commit()

    e = query.get_entity(conn, "AfD")
    assert e is not None
    assert e["warnung"] == stupid.WARNUNG
    by_link = {a["link"]: a for a in e["recent"]}
    assert "warnung" in by_link["https://afd.de/gesundheit-quatsch"]
    assert "warnung" not in by_link["https://tagesschau.de/afd-bericht"]

    tl = query.timeline(conn, "AfD")
    assert any(r.get("warnung") for r in tl if "afd.de" in r["link"])


def test_entity_lookup_of_other_actor_filters_quarantined_sources(conn):
    _seed_articles(conn)
    conn.execute("INSERT INTO entities(id,name,type,aliases,source) "
                 "VALUES (2,'RKI','behoerde','[]','seed')")
    # RKI fälschlich auch am afd.de-Artikel getaggt — Quelle muss trotzdem raus
    conn.execute("INSERT INTO article_entities(article_id,entity_id) VALUES (1,2)")
    conn.execute("INSERT INTO article_entities(article_id,entity_id) VALUES (2,2)")
    conn.commit()

    e = query.get_entity(conn, "RKI")
    links = [a["link"] for a in e["recent"]]
    assert "https://afd.de/gesundheit-quatsch" not in links
    assert "https://tagesschau.de/pflege-1" in links
    assert "warnung" not in e
