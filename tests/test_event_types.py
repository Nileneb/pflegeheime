from marktradar import embeddings, entities


def _article(conn, title, relevant=1):
    conn.execute("INSERT OR IGNORE INTO sources(name,type,url) VALUES('s','rss','http://x')")
    cur = conn.execute(
        "INSERT INTO articles(source_id,guid,title,relevant,published) "
        "VALUES (1,?,?,?,'2026-06-01T08:00:00+00:00')", (title, title, relevant))
    conn.commit()
    return cur.lastrowid


def test_seed_event_types_idempotent(conn):
    n = entities.seed_event_types(conn)
    assert n == len(entities.SEED_EVENT_RULES)
    assert entities.seed_event_types(conn) == 0
    rows = entities.list_event_types(conn)
    assert {r["name"] for r in rows} == {t for t, _ in entities.SEED_EVENT_RULES}
    assert all(r["created_by"] == "seed" for r in rows)


def test_classify_events_lazy_seeds_and_matches(conn):
    aid = _article(conn, "Träger meldet Insolvenz an")
    assert entities.classify_events(conn) >= 1
    row = conn.execute("SELECT event_type FROM articles WHERE id=?", (aid,)).fetchone()
    assert row["event_type"] == "insolvenz"
    assert conn.execute("SELECT count(*) FROM event_types").fetchone()[0] > 0


def test_add_event_type_applied_on_next_classify(conn):
    entities.seed_event_types(conn)
    res = entities.add_event_type(conn, "datenleck", r"datenleck|cyberangriff|ransomware",
                                  "IT-Sicherheitsvorfälle")
    assert res.get("ok")
    aid = _article(conn, "Ransomware legt Verwaltung lahm")
    entities.classify_events(conn)
    row = conn.execute("SELECT event_type FROM articles WHERE id=?", (aid,)).fetchone()
    assert row["event_type"] == "datenleck"


def test_add_event_type_rejects_invalid_regex_and_dupes(conn):
    entities.seed_event_types(conn)
    assert "error" in entities.add_event_type(conn, "kaputt", r"([unclosed")
    assert "error" in entities.add_event_type(conn, "insolvenz", r"egal")
    assert "error" in entities.add_event_type(conn, "", r"x")


def test_invalid_pattern_in_db_gets_disabled_not_crash(conn):
    entities.seed_event_types(conn)
    conn.execute("INSERT INTO event_types(name,pattern,created_by) "
                 "VALUES ('broken','([','manual')")
    conn.commit()
    aid = _article(conn, "Träger meldet Insolvenz an")
    entities.classify_events(conn)
    row = conn.execute("SELECT event_type FROM articles WHERE id=?", (aid,)).fetchone()
    assert row["event_type"] == "insolvenz"
    broken = conn.execute("SELECT enabled FROM event_types WHERE name='broken'").fetchone()
    assert broken["enabled"] == 0


def test_suggest_event_types_quarantined(conn, monkeypatch, fake_llm):
    entities.seed_event_types(conn)
    for i in range(25):
        _article(conn, f"Tarifstreik in Einrichtung {i}")
    monkeypatch.setattr(
        embeddings.requests, "post",
        fake_llm('[{"name":"streik","pattern":"streik|tarifkonflikt|warnstreik",'
                 '"description":"Arbeitskämpfe"}]'))
    res = entities.suggest_event_types(conn, sample=30)
    assert res["suggested"] == [{"name": "streik", "pattern": "streik|tarifkonflikt|warnstreik"}]
    row = conn.execute("SELECT enabled, created_by FROM event_types WHERE name='streik'").fetchone()
    assert row["enabled"] == 0 and row["created_by"] == "auto"
    # Quarantäne: disabled Typ klassifiziert NICHT
    aid = _article(conn, "Warnstreik beim Träger")
    entities.classify_events(conn)
    assert conn.execute("SELECT event_type FROM articles WHERE id=?",
                        (aid,)).fetchone()["event_type"] is None


def test_suggest_event_types_skips_below_min(conn):
    entities.seed_event_types(conn)
    res = entities.suggest_event_types(conn)
    assert res["suggested"] == [] and "skipped" in res
