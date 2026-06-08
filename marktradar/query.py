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
    if not knn:
        return []
    ids = [r["article_id"] for r in knn]
    dist = {r["article_id"]: r["distance"] for r in knn}
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
