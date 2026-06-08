def test_bootstrap_creates_all_tables(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','virtual')")}
    assert {"pflegeheime", "articles", "sources", "article_vec"} <= names


def test_article_vec_accepts_1024_dim(conn):
    from sqlite_vec import serialize_float32
    conn.execute("INSERT INTO sources(name,type,url) VALUES('t','rss','http://x')")
    conn.execute("INSERT INTO articles(source_id,guid,title) VALUES(1,'g','t')")
    aid = conn.execute("SELECT id FROM articles").fetchone()["id"]
    conn.execute("INSERT INTO article_vec(article_id, embedding) VALUES (?,?)",
                 (aid, serialize_float32([0.1] * 1024)))
    conn.commit()
    assert conn.execute("SELECT count(*) c FROM article_vec").fetchone()["c"] == 1


def test_guid_unique_per_source(conn):
    import sqlite3
    conn.execute("INSERT INTO sources(name,type,url) VALUES('t','rss','http://x')")
    conn.execute("INSERT INTO articles(source_id,guid,title) VALUES(1,'dup','a')")
    conn.commit()
    try:
        conn.execute("INSERT INTO articles(source_id,guid,title) VALUES(1,'dup','b')")
        conn.commit()
        assert False, "expected UNIQUE violation"
    except sqlite3.IntegrityError:
        pass


def test_seed_is_idempotent(conn):
    from marktradar import sources
    n1 = sources.seed(conn)
    n2 = sources.seed(conn)
    assert n1 == len(sources.TIER1)
    assert n2 == 0  # zweiter Lauf fügt nichts hinzu (UNIQUE url)
    assert conn.execute("SELECT count(*) c FROM sources").fetchone()["c"] == len(sources.TIER1)
