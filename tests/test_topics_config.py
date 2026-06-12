from marktradar import entities


def test_seed_topics_idempotent(conn):
    n = entities.seed_topics(conn)
    assert n == len(entities.SEED_TOPICS)
    assert entities.seed_topics(conn) == 0
    assert {t["name"] for t in entities.list_topics(conn)} == set(entities.SEED_TOPICS)


def test_load_topic_prefilters_lazy_seeds(conn):
    pf = entities._load_topic_prefilters(conn)
    assert set(pf) == set(entities.SEED_TOPICS)
    assert pf["Pflegereform"].search("Neue Pflegereform beschlossen")


def test_add_topic_multi_domain(conn):
    res = entities.add_topic(conn, "Lieferketten", r"lieferkette|zulieferer|logistik",
                             domain="industrie")
    assert res.get("ok")
    pf = entities._load_topic_prefilters(conn)
    assert "Lieferketten" in pf
    row = conn.execute("SELECT domain, created_by FROM topics WHERE name='Lieferketten'").fetchone()
    assert row["domain"] == "industrie" and row["created_by"] == "manual"


def test_add_topic_rejects_invalid(conn):
    assert "error" in entities.add_topic(conn, "Kaputt", r"([")
    entities.seed_topics(conn)
    assert "error" in entities.add_topic(conn, "Pflegereform", r"egal")


def test_invalid_topic_prefilter_disabled_not_crash(conn):
    entities.seed_topics(conn)
    conn.execute("INSERT INTO topics(name,prefilter) VALUES ('broken','([')")
    conn.commit()
    pf = entities._load_topic_prefilters(conn)
    assert "broken" not in pf and "Pflegereform" in pf
    assert conn.execute("SELECT enabled FROM topics WHERE name='broken'").fetchone()["enabled"] == 0


def test_mirror_heime_idempotent_and_queryable(conn):
    from marktradar import query
    conn.execute("INSERT INTO pflegeheime(name,traeger,ort,kreis) "
                 "VALUES ('Haus Abendsonne','Caritas','Aachen','SR Aachen')")
    conn.execute("INSERT INTO pflegeheime(name,traeger,ort,kreis) "
                 "VALUES ('x','-','-','-')")  # zu kurz → übersprungen
    conn.commit()
    n = entities.mirror_heime(conn)
    assert n == 1
    assert entities.mirror_heime(conn) == 0
    e = query.get_entity(conn, "Haus Abendsonne")
    assert e and e["type"] == "einrichtung" and e["source"] == "register"
    row = conn.execute("SELECT ref_table, ref_id FROM entities WHERE name='Haus Abendsonne'").fetchone()
    assert row["ref_table"] == "pflegeheime" and row["ref_id"] is not None
    lst = query.list_entities(conn, type="einrichtung")
    assert any(it["name"] == "Haus Abendsonne" for it in lst)


def test_positions_no_namerror(conn):
    # Regression: positions() crashte mit NameError (e undefiniert, Commit 99e2928)
    from marktradar import query
    conn.execute("INSERT INTO sources(name,type,url) VALUES('s','rss','http://x')")
    conn.execute("INSERT INTO entities(name,type) VALUES('CDU','partei')")
    conn.execute("INSERT INTO articles(source_id,guid,title,link,published,source_domain) "
                 "VALUES(1,'g','Reform','http://x/1','2026-06-01T08:00:00+00:00','example.org')")
    aid = conn.execute("SELECT id FROM articles").fetchone()["id"]
    eid = conn.execute("SELECT id FROM entities").fetchone()["id"]
    conn.execute("INSERT INTO article_topics(article_id,topic,stance,valence,position) "
                 "VALUES(?,?,?,?,?)", (aid, 'Pflegereform', 'pro', 'pro', 'Mehr Personal'))
    conn.execute("INSERT INTO article_entities(article_id,entity_id,method) "
                 "VALUES(?,?,'alias')", (aid, eid))
    conn.commit()
    rows = query.positions(conn)
    assert len(rows) == 1 and rows[0]["entity"] == "CDU"
