"""Tests für Akteur×Thema (Entitäten × Hashtags über den articles-Join)."""
from marktradar import entities, hashtags


def _seed(conn):
    conn.execute("INSERT INTO sources(id,name,type,url,enabled) VALUES (1,'Q','rss','http://q/feed',1)")
    conn.execute("INSERT INTO hashtags(id,term,color,active) VALUES (1,'Pflegereform','#fff',1)")
    conn.execute("INSERT INTO hashtags(id,term,color,active) VALUES (2,'Altenpflege','#fff',1)")
    conn.execute("INSERT INTO entities(id,name,type) VALUES (1,'SPD','partei')")
    conn.execute("INSERT INTO entities(id,name,type) VALUES (2,'Caritas','traeger')")
    for aid in (10, 11, 12):
        conn.execute("INSERT INTO articles(id,source_id,guid,title) VALUES (?,1,?,?)",
                     (aid, f"g{aid}", f"t{aid}"))
    # SPD in 10,11 unter #Pflegereform; Caritas in 12 unter #Altenpflege
    conn.execute("INSERT INTO article_entities(article_id,entity_id) VALUES (10,1),(11,1),(12,2)")
    conn.execute("INSERT INTO article_hashtags(article_id,hashtag_id) VALUES (10,1),(11,1),(12,2)")
    conn.commit()


def test_actors_per_hashtag(conn):
    _seed(conn)
    res = entities.actors_for_hashtags(conn)
    assert res[1][0]["name"] == "SPD" and res[1][0]["type"] == "partei" and res[1][0]["n"] == 2
    assert res[2][0]["name"] == "Caritas" and res[2][0]["n"] == 1


def test_actors_respects_per_limit(conn):
    _seed(conn)
    res = entities.actors_for_hashtags(conn, per=1)
    assert all(len(v) <= 1 for v in res.values())


def test_map_data_includes_actors(conn):
    _seed(conn)
    d = hashtags.map_data(conn)
    assert 1 in d["actors"] and d["actors"][1][0]["name"] == "SPD"
    assert d["actor_colors"]["partei"] == entities.ACTOR_COLORS["partei"]
