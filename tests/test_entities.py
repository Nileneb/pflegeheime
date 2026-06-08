from marktradar import entities


def _article(conn, title, summary="", relevant=1, published="2026-06-01T08:00:00+00:00"):
    conn.execute("INSERT OR IGNORE INTO sources(name,type,url) VALUES('s','rss','http://x')")
    cur = conn.execute("INSERT INTO articles(source_id,guid,title,summary,relevant,published) "
                       "VALUES (1,?,?,?,?,?)", (title, title, summary, relevant, published))
    conn.commit()
    return cur.lastrowid


def test_seed_entities_curated_and_from_heime(conn):
    conn.execute("INSERT INTO pflegeheime(name,traeger) VALUES ('H1','Stiftung Hephata')")
    conn.execute("INSERT INTO pflegeheime(name,traeger) VALUES ('H2','No clear Data')")
    conn.commit()
    n = entities.seed_entities(conn)
    names = {r["name"] for r in conn.execute("SELECT name FROM entities").fetchall()}
    assert "Korian" in names                 # curated Träger
    assert "Paul Hartmann" in names          # curated Hersteller
    assert "Stiftung Hephata" in names       # aus Heimen
    assert "No clear Data" not in names      # Junk gefiltert
    assert entities.seed_entities(conn) == 0  # idempotent


def test_tag_articles_word_boundary(conn):
    entities.seed_entities(conn)
    aid = _article(conn, "Korian eröffnet Haus in Köln")
    other = _article(conn, "Curanummer 5 vergeben")   # darf NICHT Korian/Curanum matchen
    entities.tag_articles(conn)
    korian = conn.execute("SELECT id FROM entities WHERE name='Korian'").fetchone()["id"]
    linked = {r["article_id"] for r in conn.execute(
        "SELECT article_id FROM article_entities WHERE entity_id=?", (korian,)).fetchall()}
    assert aid in linked
    assert other not in linked


def test_event_type_taxonomy():
    assert entities.event_type("Träger meldet Insolvenz an") == "insolvenz"
    assert entities.event_type("Bundestag beschließt Pflegereform") == "politik"
    assert entities.event_type("AWO eröffnet neuen Standort") == "expansion"
    assert entities.event_type("Sommerfest im Seniorenheim") is None


def test_classify_events_backfill(conn):
    aid = _article(conn, "Großer Träger meldet Insolvenz")
    n = entities.classify_events(conn)
    assert n >= 1
    row = conn.execute("SELECT event_type FROM articles WHERE id=?", (aid,)).fetchone()
    assert row["event_type"] == "insolvenz"


class _Resp:
    def __init__(self, c): self._c = c
    def raise_for_status(self): pass
    def json(self): return {"message": {"content": self._c}}


def _fake_llm(*a, **k):
    """Mock: synth-Prompt → Positions-Liste; classify-Prompt → {topic:{position,valence}}."""
    sysmsg = k["json"]["messages"][0]["content"]
    if "destillierst" in sysmsg:
        return _Resp('[{"label":"Mehr Personal","valence":"pro"}]')
    return _Resp('{"Personal & Fachkräfte":{"position":"Mehr Personal","valence":"pro"}}')


def test_classify_topics_stores_position_and_valence(conn, monkeypatch):
    aid = _article(conn, "Verband fordert mehr Personal in der Pflege")
    monkeypatch.setattr(entities.requests, "post", _fake_llm)
    entities.synthesize_positions(conn, min_hits=1)
    rep = entities.classify_topics(conn)
    assert rep["classified"] >= 1
    row = conn.execute("SELECT position, valence FROM article_topics "
                       "WHERE article_id=? AND topic='Personal & Fachkräfte'", (aid,)).fetchone()
    assert row["position"] == "Mehr Personal"
    assert row["valence"] == "pro"


def test_discourse_topic_legend_and_items(conn, monkeypatch):
    from marktradar import query
    entities.seed_entities(conn)
    _article(conn, "CDU fordert mehr Personal in der Pflege")
    entities.tag_articles(conn)
    monkeypatch.setattr(entities.requests, "post", _fake_llm)
    entities.synthesize_positions(conn, min_hits=1)
    entities.classify_topics(conn)
    d = query.discourse_topic(conn, "Personal & Fachkräfte")
    assert any(p["label"] == "Mehr Personal" for p in d["legend"])
    assert any(it["entity"] == "CDU" and it["valence"] == "pro" and
               it["position"] == "Mehr Personal" for it in d["items"])


def test_render_topic_svg(conn):
    from marktradar import query
    conn.execute("INSERT INTO sources(name,type,url) VALUES('s','rss','http://x')")
    conn.execute("INSERT INTO entities(name,type) VALUES('CDU','partei')")
    eid = conn.execute("SELECT id FROM entities WHERE name='CDU'").fetchone()["id"]
    conn.execute("INSERT INTO articles(source_id,guid,title,link,published) "
                 "VALUES(1,'g','Reform-Titel','http://x/1','2026-06-01T08:00:00+00:00')")
    aid = conn.execute("SELECT id FROM articles").fetchone()["id"]
    conn.execute("INSERT INTO topic_positions(topic,label,valence,color,ord) "
                 "VALUES('Pflegereform','Mehr Personal','pro','#5b8def',0)")
    conn.execute("INSERT INTO article_topics(article_id,topic,stance,valence,position) "
                 "VALUES(?,?,?,?,?)", (aid, 'Pflegereform', 'pro', 'pro', 'Mehr Personal'))
    conn.execute("INSERT INTO article_entities(article_id,entity_id,method) "
                 "VALUES(?,?,'alias')", (aid, eid))
    conn.commit()
    svg = query.render_topic_svg(conn, 'Pflegereform')
    assert svg.startswith('<svg') and '</svg>' in svg
    assert 'CDU' in svg and '<circle' in svg and 'Mehr Personal' in svg


def test_render_topic_svg_empty(conn):
    from marktradar import query
    svg = query.render_topic_svg(conn, 'Pflegereform')
    assert '<svg' in svg and 'noch keine Positionen' in svg
