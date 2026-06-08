"""Lese-Queries für den MCP: Hybrid-News-Suche + Heim-Suche + Stats."""
from sqlite_vec import serialize_float32

from marktradar import embeddings


def search_news(conn, query: str, limit: int = 20, since_days: int | None = None,
                kategorie: str | None = None, only_relevant: bool = True) -> list[dict]:
    qvec = serialize_float32(embeddings.embed(query))
    knn = conn.execute(
        "SELECT article_id, distance FROM article_vec "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (qvec, max(limit * 4, 20))).fetchall()
    dist = {r["article_id"]: r["distance"] for r in knn}
    # Keyword-Recall: exakte Term-Treffer in title/summary, die der Vektor verfehlt
    # (Eigennamen wie Träger-/Ortsnamen) ODER Artikel ohne Embedding (migrierte Altdaten).
    like = f"%{query}%"
    kw = conn.execute("SELECT id FROM articles WHERE title LIKE ? OR summary LIKE ?",
                      (like, like)).fetchall()
    ids = list(dict.fromkeys([r["article_id"] for r in knn] + [r["id"] for r in kw]))
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    sql = (f"SELECT id,title,summary,link,published,kategorie,grund,source_domain,relevant "
           f"FROM articles WHERE id IN ({placeholders})")
    params = list(ids)
    if only_relevant:
        sql += " AND relevant=1"
    if kategorie:
        sql += " AND kategorie LIKE ?"; params.append(f"%{kategorie}%")
    if since_days is not None:
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
        sql += " AND published >= ?"; params.append(cutoff)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    rows.sort(key=lambda r: dist.get(r["id"], 1e9))
    return rows[:limit]


def search_heime(conn, query: str, limit: int = 20) -> list[dict]:
    like = f"%{query}%"
    rows = conn.execute(
        "SELECT id,name,traeger,ort,kreis,website,geschaeftsfuehrung "
        "FROM pflegeheime WHERE name LIKE ? OR traeger LIKE ? OR ort LIKE ? LIMIT ?",
        (like, like, like, limit)).fetchall()
    return [dict(r) for r in rows]


def db_stats(conn) -> dict:
    one = lambda q: conn.execute(q).fetchone()[0]
    return {
        "heime": one("SELECT count(*) FROM pflegeheime"),
        "artikel": one("SELECT count(*) FROM articles"),
        "artikel_relevant": one("SELECT count(*) FROM articles WHERE relevant=1"),
        "artikel_embedded": one("SELECT count(*) FROM article_vec"),
        "quellen": one("SELECT count(*) FROM sources"),
        "quellen_aktiv": one("SELECT count(*) FROM sources WHERE enabled=1"),
    }


def _resolve_entity(conn, name: str):
    q = f"%{name}%"
    return conn.execute(
        "SELECT id,name,type,aliases,region,source FROM entities "
        "WHERE name = ? COLLATE NOCASE OR name LIKE ? OR aliases LIKE ? "
        "ORDER BY (name = ? COLLATE NOCASE) DESC LIMIT 1",
        (name, q, q, name)).fetchone()


def get_entity(conn, name: str, limit: int = 10) -> dict | None:
    """Entitäts-Profil + jüngste verknüpfte Artikel. None wenn unbekannt."""
    e = _resolve_entity(conn, name)
    if e is None:
        return None
    import json
    cnt = conn.execute(
        "SELECT count(*) FROM article_entities WHERE entity_id=?", (e["id"],)).fetchone()[0]
    arts = [dict(r) for r in conn.execute(
        "SELECT a.title,a.published,a.event_type,a.link,a.source_domain "
        "FROM articles a JOIN article_entities ae ON ae.article_id=a.id "
        "WHERE ae.entity_id=? ORDER BY a.published DESC LIMIT ?", (e["id"], limit)).fetchall()]
    return {"id": e["id"], "name": e["name"], "type": e["type"],
            "aliases": json.loads(e["aliases"]) if e["aliases"] else [],
            "region": e["region"], "source": e["source"],
            "article_count": cnt, "recent": arts}


def timeline(conn, name: str, limit: int = 30, event_type: str | None = None) -> list[dict]:
    """Chronologische verknüpfte Meldungen einer Entität (optional nach event_type)."""
    e = _resolve_entity(conn, name)
    if e is None:
        return []
    sql = ("SELECT a.title,a.published,a.event_type,a.kategorie,a.link,a.source_domain "
           "FROM articles a JOIN article_entities ae ON ae.article_id=a.id "
           "WHERE ae.entity_id=?")
    params = [e["id"]]
    if event_type:
        sql += " AND a.event_type=?"; params.append(event_type)
    sql += " ORDER BY a.published DESC LIMIT ?"; params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_entities(conn, type: str | None = None, limit: int = 50) -> list[dict]:
    """Entitäten mit Artikel-Anzahl, meistgenannte zuerst."""
    sql = ("SELECT e.id,e.name,e.type,e.source,count(ae.article_id) AS articles "
           "FROM entities e LEFT JOIN article_entities ae ON ae.entity_id=e.id ")
    params = []
    if type:
        sql += "WHERE e.type=? "; params.append(type)
    sql += "GROUP BY e.id ORDER BY articles DESC, e.name LIMIT ?"; params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]
