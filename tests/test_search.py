from sqlite_vec import serialize_float32
from marktradar import query


def _seed_articles(conn):
    conn.execute("INSERT INTO sources(name,type,url) VALUES('s','rss','http://x')")
    data = [("Korian Insolvenz Gerücht", [0.9] * 1024),
            ("Tag der offenen Tür im Seniorenheim", [0.1] * 1024)]
    for i, (title, vec) in enumerate(data, 1):
        conn.execute("INSERT INTO articles(source_id,guid,title,published,relevant) "
                     "VALUES (1,?,?,?,1)", (f"g{i}", title, "2026-06-0%d" % i))
        conn.execute("INSERT INTO article_vec(article_id,embedding) VALUES (?,?)",
                     (i, serialize_float32(vec)))
    conn.commit()


def test_search_news_ranks_by_vector(conn, monkeypatch):
    _seed_articles(conn)
    monkeypatch.setattr(query.embeddings, "embed", lambda t: [0.9] * 1024)
    hits = query.search_news(conn, "Korian Pleite", limit=2)
    assert hits[0]["title"] == "Korian Insolvenz Gerücht"


def test_search_news_respects_only_relevant(conn, monkeypatch):
    _seed_articles(conn)
    conn.execute("UPDATE articles SET relevant=0 WHERE title LIKE 'Korian%'")
    conn.commit()
    monkeypatch.setattr(query.embeddings, "embed", lambda t: [0.9] * 1024)
    hits = query.search_news(conn, "Korian", limit=5, only_relevant=True)
    assert all(h["title"] != "Korian Insolvenz Gerücht" for h in hits)


def test_search_news_finds_unembedded_article_via_keyword(conn, monkeypatch):
    # Migrierte Altartikel haben keinen article_vec-Eintrag → reine Vektorsuche würde
    # sie NIE finden. Keyword-Hybrid muss sie über title/summary trotzdem liefern.
    conn.execute("INSERT INTO sources(name,type,url) VALUES('s','rss','http://x')")
    conn.execute("INSERT INTO articles(source_id,guid,title,relevant) "
                 "VALUES (1,'g9','Korian Pleite Hamburg',1)")
    conn.commit()
    monkeypatch.setattr(query.embeddings, "embed", lambda t: [0.5] * 1024)
    hits = query.search_news(conn, "Korian", limit=5)
    assert any("Korian" in h["title"] for h in hits)


def test_backfill_embeddings_covers_unembedded(conn, monkeypatch):
    from marktradar import ingest
    conn.execute("INSERT INTO sources(name,type,url) VALUES('s','rss','http://x')")
    conn.execute("INSERT INTO articles(source_id,guid,title) VALUES (1,'a','Titel ohne Vektor')")
    conn.commit()
    monkeypatch.setattr(ingest.embeddings, "embed", lambda t: [0.2] * 1024)
    n = ingest.backfill_embeddings(conn)
    assert n == 1
    assert conn.execute("SELECT count(*) c FROM article_vec").fetchone()["c"] == 1
    assert ingest.backfill_embeddings(conn) == 0  # idempotent
