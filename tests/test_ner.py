from marktradar import embeddings, entities, ner


def _article(conn, title, summary="", relevant=1):
    conn.execute("INSERT OR IGNORE INTO sources(name,type,url) VALUES('s','rss','http://x')")
    cur = conn.execute(
        "INSERT INTO articles(source_id,guid,title,summary,relevant,published,fetched_at) "
        "VALUES (1,?,?,?,?,'2026-06-01T08:00:00+00:00',datetime('now'))",
        (title, title, summary, relevant))
    conn.commit()
    return cur.lastrowid


def test_extract_creates_new_entity_in_quarantine(conn, monkeypatch, fake_llm):
    aid = _article(conn, "Novacare GmbH übernimmt drei Standorte")
    monkeypatch.setattr(embeddings.requests, "post",
                        fake_llm('[{"name":"Novacare GmbH","kind":"traeger","confidence":0.92}]'))
    rep = ner.extract_entities(conn, article_ids=[aid])
    assert rep["created"] == 1 and rep["linked"] == 1 and rep["failed"] == 0
    row = conn.execute("SELECT type, source, confidence, review FROM entities "
                       "WHERE name='Novacare GmbH'").fetchone()
    assert row["type"] == "traeger" and row["source"] == "ner"
    assert row["review"] == 1 and row["confidence"] == 0.92
    link = conn.execute("SELECT method FROM article_entities WHERE article_id=?",
                        (aid,)).fetchone()
    assert link["method"] == "ner"


def test_extract_dedups_against_alias(conn, monkeypatch, fake_llm):
    entities.seed_entities(conn)
    aid = _article(conn, "Curanum baut Pflegeplätze aus")
    monkeypatch.setattr(embeddings.requests, "post",
                        fake_llm('[{"name":"Curanum","kind":"traeger","confidence":0.95}]'))
    rep = ner.extract_entities(conn, article_ids=[aid])
    # Curanum ist Alias von Korian → KEINE neue Entität, nur Link
    assert rep["created"] == 0 and rep["linked"] == 1
    korian = conn.execute("SELECT id FROM entities WHERE name='Korian'").fetchone()["id"]
    assert conn.execute("SELECT 1 FROM article_entities WHERE article_id=? AND entity_id=? "
                        "AND method='ner'", (aid, korian)).fetchone()
    assert conn.execute("SELECT count(*) FROM entities WHERE name='Curanum'").fetchone()[0] == 0


def test_extract_confidence_threshold(conn, monkeypatch, fake_llm):
    aid = _article(conn, "Irgendein Verein macht irgendwas")
    monkeypatch.setattr(embeddings.requests, "post",
                        fake_llm('[{"name":"Unsicherer Verein","kind":"sonstig","confidence":0.3}]'))
    rep = ner.extract_entities(conn, article_ids=[aid], min_confidence=0.7)
    assert rep["created"] == 0 and rep["skipped"] == 1
    assert conn.execute("SELECT count(*) FROM entities").fetchone()[0] == 0


def test_extract_isolates_llm_failure(conn, monkeypatch):
    a1 = _article(conn, "Artikel eins")
    def boom(*a, **k):
        raise ConnectionError("ollama down")
    monkeypatch.setattr(embeddings.requests, "post", boom)
    rep = ner.extract_entities(conn, article_ids=[a1])
    assert rep["failed"] == 1 and rep["created"] == 0


def test_refresh_skips_ner_by_default(conn, monkeypatch):
    from marktradar import ingest
    called = []
    monkeypatch.setattr(ner, "extract_entities",
                        lambda *a, **k: called.append(1) or {})
    monkeypatch.setattr(ingest, "fetch", lambda url: None)
    rep = ingest.refresh(conn)
    assert "ner" not in rep and not called


def test_refresh_opt_in_calls_ner(conn, monkeypatch):
    from marktradar import ingest
    monkeypatch.setattr(ner, "extract_entities",
                        lambda conn, article_ids=None, **k: {"articles": 0, "created": 0})
    monkeypatch.setattr(ingest, "fetch", lambda url: None)
    rep = ingest.refresh(conn, extract_ner=True)
    assert rep["ner"] == {"articles": 0, "created": 0}


def test_review_accept_and_reject(conn, monkeypatch, fake_llm):
    aid = _article(conn, "Novacare GmbH übernimmt Standorte")
    monkeypatch.setattr(embeddings.requests, "post",
                        fake_llm('[{"name":"Novacare GmbH","kind":"traeger","confidence":0.9}]'))
    ner.extract_entities(conn, article_ids=[aid])
    eid = conn.execute("SELECT id FROM entities WHERE name='Novacare GmbH'").fetchone()["id"]
    assert [p["id"] for p in ner.pending_review(conn)] == [eid]
    assert ner.review_entity(conn, eid, accept=True)["accepted"]
    assert conn.execute("SELECT review FROM entities WHERE id=?", (eid,)).fetchone()["review"] == 0
    assert ner.pending_review(conn) == []
    res = ner.review_entity(conn, eid, accept=False)
    assert res["deleted"]
    assert conn.execute("SELECT count(*) FROM entities WHERE id=?", (eid,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM article_entities WHERE entity_id=?",
                        (eid,)).fetchone()[0] == 0
    assert "error" in ner.review_entity(conn, 99999, accept=True)


def test_pending_review_only_quarantined(conn, monkeypatch, fake_llm):
    entities.seed_entities(conn)
    assert ner.pending_review(conn) == []
