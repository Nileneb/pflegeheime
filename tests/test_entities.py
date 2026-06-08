from marktradar import entities


def _article(conn, title, summary="", relevant=1):
    conn.execute("INSERT OR IGNORE INTO sources(name,type,url) VALUES('s','rss','http://x')")
    cur = conn.execute("INSERT INTO articles(source_id,guid,title,summary,relevant) "
                       "VALUES (1,?,?,?,?)", (title, title, summary, relevant))
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


def test_classify_topics_stores_llm_stance(conn, monkeypatch):
    aid = _article(conn, "Verband kritisiert Pflegereform scharf")

    class R:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": '{"Pflegereform":"kritisch"}'}}
    monkeypatch.setattr(entities.requests, "post", lambda *a, **k: R())
    rep = entities.classify_topics(conn)
    assert rep["classified"] >= 1
    row = conn.execute("SELECT stance FROM article_topics WHERE article_id=? AND topic='Pflegereform'",
                       (aid,)).fetchone()
    assert row["stance"] == "kritisch"


def test_discourse_aggregates_party_stance(conn, monkeypatch):
    from marktradar import query
    entities.seed_entities(conn)
    _article(conn, "CDU fordert mehr Personal in der Pflege")
    entities.tag_articles(conn)

    class R:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": '{"Personal & Fachkräfte":"fordernd"}'}}
    monkeypatch.setattr(entities.requests, "post", lambda *a, **k: R())
    entities.classify_topics(conn)
    d = query.discourse(conn)
    pers = [t for t in d["topics"] if t["topic"] == "Personal & Fachkräfte"][0]
    assert any(n["name"] == "CDU" and n["stance"] == "fordernd" for n in pers["nodes"])
