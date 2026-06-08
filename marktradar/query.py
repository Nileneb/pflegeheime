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


# ── Viewer-API (Live-Dashboard) ──────────────────────────────────────────────
def get_meta(conn, key: str, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()


def overview(conn) -> dict:
    """Kennzahlen + Änderungs-Deltas + Event-/Quellen-Status fürs Dashboard."""
    one = lambda q, p=(): conn.execute(q, p).fetchone()[0]
    last_seen = get_meta(conn, "last_seen", "1970-01-01")
    from datetime import datetime, timezone, timedelta
    d24 = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    return {
        "stats": db_stats(conn),
        "neu_seit_zuletzt": one("SELECT count(*) FROM articles WHERE fetched_at > ?", (last_seen,)),
        "neu_24h": one("SELECT count(*) FROM articles WHERE fetched_at > ?", (d24,)),
        "last_seen": last_seen,
        "events": [dict(r) for r in conn.execute(
            "SELECT event_type, count(*) n FROM articles WHERE event_type IS NOT NULL "
            "GROUP BY event_type ORDER BY n DESC").fetchall()],
        "sources": [dict(r) for r in conn.execute(
            "SELECT name,region,enabled,last_status,last_fetched FROM sources "
            "ORDER BY enabled DESC, name").fetchall()],
        "entities": list_entities(conn, limit=12),
    }


def feed(conn, limit: int = 40, since_ts: str | None = None) -> list[dict]:
    """Neueste Meldungen für den Feed; markiert, welche seit `since_ts` neu sind."""
    rows = conn.execute(
        "SELECT a.id,a.title,a.published,a.fetched_at,a.event_type,a.kategorie,"
        "a.link,a.source_domain,"
        "(SELECT group_concat(e.name,', ') FROM article_entities ae "
        " JOIN entities e ON e.id=ae.entity_id WHERE ae.article_id=a.id) AS entities "
        "FROM articles a ORDER BY a.published DESC NULLS LAST, a.id DESC LIMIT ?",
        (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_new"] = bool(since_ts and r["fetched_at"] and r["fetched_at"] > since_ts)
        out.append(d)
    return out


def graph_data(conn, limit: int = 12) -> dict:
    """Knoten (Entitäten nach Nennungen) + Hub fürs Netzwerk-Tab."""
    nodes = [e for e in list_entities(conn, limit=limit) if e["articles"] > 0]
    total = conn.execute("SELECT count(*) FROM articles").fetchone()[0]
    return {"hub": {"label": "MARKT", "count": total}, "nodes": nodes}


# ── Diskurs/Positions-Radar ──────────────────────────────────────────────────
# WHY: Stance ist v1 heuristisch (Keyword) — bewusst erklärbar/kostenlos; eine
# LLM-Verfeinerung (qwen Stance-Detection) ist als nächste Stufe vorgesehen.
import re as _re
DISCOURSE_TERMS = ["Pflegereform", "Tariftreue", "Personal", "Insolvenz", "Beitrag",
                   "Demenz", "Bürokratie", "Digitalisierung", "Prävention"]


def discourse(conn) -> dict:
    """Positionen je Thema aus LLM-Stance (article_topics), je Entität aggregiert
    (dominante Haltung), + Themen/Hashtag-Radar."""
    from marktradar import entities
    links = conn.execute(
        "SELECT e.name ename, a.title, a.summary, a.published, a.source_domain "
        "FROM article_entities ae JOIN entities e ON e.id=ae.entity_id "
        "JOIN articles a ON a.id=ae.article_id").fetchall()
    arts = conn.execute("SELECT title, summary, published, source_domain FROM articles").fetchall()

    bytopic = {}
    for r in conn.execute(
            "SELECT at.topic, e.name ename, at.stance, count(*) n "
            "FROM article_topics at "
            "JOIN article_entities ae ON ae.article_id = at.article_id "
            "JOIN entities e ON e.id = ae.entity_id "
            "WHERE at.stance IS NOT NULL "
            "GROUP BY at.topic, e.name, at.stance").fetchall():
        d = bytopic.setdefault(r["topic"], {}).setdefault(r["ename"], {})
        d[r["stance"]] = d.get(r["stance"], 0) + r["n"]
    topics = []
    for topic in entities.TOPIC_PREFILTER:
        agg = bytopic.get(topic, {})
        nodes = [{"name": name, "stance": max(cnt, key=cnt.get),
                  "count": sum(cnt.values()), "breakdown": cnt}
                 for name, cnt in sorted(agg.items(), key=lambda x: -sum(x[1].values()))[:10]]
        topics.append({"topic": topic, "nodes": nodes})

    terms = []
    for t in DISCOURSE_TERMS:
        tr = _re.compile(_re.escape(t), _re.I)
        hits = [a for a in arts if tr.search(f"{a['title']} {a['summary'] or ''}")]
        if not hits:
            continue
        doms, ents, weeks = {}, {}, {}
        for a in hits:
            if a["source_domain"]:
                doms[a["source_domain"]] = doms.get(a["source_domain"], 0) + 1
            wk = (a["published"] or "")[:7]
            if wk:
                weeks[wk] = weeks.get(wk, 0) + 1
        for r in links:
            if tr.search(f"{r['title']} {r['summary'] or ''}"):
                ents[r["ename"]] = ents.get(r["ename"], 0) + 1
        terms.append({
            "term": t, "count": len(hits),
            "sources": sorted(doms.items(), key=lambda x: -x[1])[:3],
            "entities": [e for e, _ in sorted(ents.items(), key=lambda x: -x[1])[:4]],
            "trend": [n for _, n in sorted(weeks.items())][-8:],
        })
    terms.sort(key=lambda x: -x["count"])
    return {"topics": topics, "terms": terms,
            "note": "Stance via qwen-LLM pro Artikel (Thema-gegated)"}
