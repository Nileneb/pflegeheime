"""LLM-NER: materialisiert Entitäten aus dem Newsstrom — die selbst-erweiternde
Stufe des Entity-Layers (entities.source='ner', article_entities.method='ner').
Neue Entitäten landen in Review-Quarantäne (review=1) statt ungeprüft im Graph."""
import json
import os
import re

import requests

from marktradar import embeddings

NER_MODEL = os.getenv("CHAT_MODEL", "qwen3.5:9b")
KINDS = ("traeger", "hersteller", "behoerde", "partei", "person", "sonstig")

_NER_SYS = (
    "Du extrahierst aus einer Meldung die genannten ORGANISATIONEN und PERSONEN "
    "des Marktes (Betreiber/Träger, Hersteller/Zulieferer, Behörden/Verbände, "
    "Parteien, Schlüsselpersonen). KEINE Orte, keine Produkte, keine generischen "
    "Begriffe. kind ∈ traeger|hersteller|behoerde|partei|person|sonstig. "
    "confidence 0..1 = wie sicher der Name eine eigenständige Markt-Entität ist. "
    'Antworte NUR als JSON-Liste: [{"name":"...","kind":"...","confidence":0.9}].')


def _known_names(conn) -> dict:
    """{lowercase_name_or_alias: entity_id} über Namen UND Aliase."""
    out = {}
    for r in conn.execute("SELECT id, name, aliases FROM entities").fetchall():
        out[r["name"].strip().lower()] = r["id"]
        if r["aliases"]:
            for a in json.loads(r["aliases"]):
                if a:
                    out[a.strip().lower()] = r["id"]
    return out


def _candidates(conn, since_days: int, limit: int, article_ids=None) -> list:
    if article_ids is not None:
        if not article_ids:
            return []
        ph = ",".join("?" * len(article_ids))
        return conn.execute(
            f"SELECT id, title, summary FROM articles WHERE id IN ({ph})",
            list(article_ids)).fetchall()
    return conn.execute(
        "SELECT id, title, summary FROM articles WHERE relevant=1 "
        "AND fetched_at >= datetime('now', ?) "
        "AND id NOT IN (SELECT article_id FROM article_entities WHERE method='ner') "
        "ORDER BY published DESC LIMIT ?", (f"-{int(since_days)} days", limit)).fetchall()


def extract_entities(conn, since_days: int = 7, limit: int = 50,
                     min_confidence: float = 0.7, article_ids=None) -> dict:
    """LLM-NER über relevante Artikel: bekannte Namen/Aliase → nur Link
    (method='ner'), Neulinge ≥ min_confidence → entities(source='ner', review=1).
    Fehler je Artikel isoliert. Gibt {articles, linked, created, skipped, failed}."""
    rows = _candidates(conn, since_days, limit, article_ids)
    known = _known_names(conn)
    linked = created = skipped = failed = 0
    for i, a in enumerate(rows):
        text = f"Titel: {a['title']}\nText: {a['summary'] or ''}"[:1800]
        try:
            payload = {"model": NER_MODEL, "format": "json", "stream": False,
                       "think": False, "messages": [
                           {"role": "system", "content": _NER_SYS},
                           {"role": "user", "content": text}],
                       "options": {"temperature": 0.0, "num_ctx": 2048,
                                   "num_predict": 300}}
            r = requests.post(f"{embeddings.CHAT_HOST}/api/chat", json=payload,
                              headers=embeddings.chat_headers(), timeout=90)
            r.raise_for_status()
            data = json.loads(r.json().get("message", {}).get("content", "") or "[]")
        except Exception:
            failed += 1
            continue
        items = data if isinstance(data, list) else data.get("entities") or []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = re.sub(r"\s+", " ", str(it.get("name", ""))).strip()
            if len(name) < 3:
                continue
            kind = str(it.get("kind", "sonstig")).strip().lower()
            if kind not in KINDS:
                kind = "sonstig"
            try:
                conf = float(it.get("confidence", 0))
            except (TypeError, ValueError):
                conf = 0.0
            eid = known.get(name.lower())
            if eid is None:
                if conf < min_confidence:
                    skipped += 1
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO entities"
                    "(name,type,source,confidence,review) VALUES (?,?,'ner',?,1)",
                    (name, kind, conf))
                if cur.rowcount:
                    eid = cur.lastrowid
                    created += 1
                else:  # Name-Kollision (UNIQUE) → bestehende Entität verlinken
                    eid = conn.execute("SELECT id FROM entities WHERE name=?",
                                       (name,)).fetchone()["id"]
                known[name.lower()] = eid
            cur = conn.execute(
                "INSERT OR IGNORE INTO article_entities(article_id,entity_id,method) "
                "VALUES (?,?,'ner')", (a["id"], eid))
            linked += cur.rowcount
        if (i + 1) % 15 == 0:
            conn.commit()
    conn.commit()
    return {"articles": len(rows), "linked": linked, "created": created,
            "skipped": skipped, "failed": failed}


def review_entity(conn, entity_id: int, accept: bool) -> dict:
    """Review-Entscheid für eine NER-Entität: accept → review=0 (bestätigt);
    reject → Entität + ihre Artikel-Links löschen."""
    row = conn.execute("SELECT id, name, review FROM entities WHERE id=?",
                       (entity_id,)).fetchone()
    if row is None:
        return {"error": f"entity {entity_id} nicht gefunden"}
    if accept:
        conn.execute("UPDATE entities SET review=0 WHERE id=?", (entity_id,))
        conn.commit()
        return {"ok": True, "name": row["name"], "accepted": True}
    conn.execute("DELETE FROM article_entities WHERE entity_id=?", (entity_id,))
    conn.execute("DELETE FROM entities WHERE id=?", (entity_id,))
    conn.commit()
    return {"ok": True, "name": row["name"], "accepted": False, "deleted": True}


def pending_review(conn, limit: int = 50) -> list[dict]:
    """NER-Entitäten in Quarantäne, meistverlinkte zuerst."""
    return [dict(r) for r in conn.execute(
        "SELECT e.id, e.name, e.type, e.confidence, count(ae.article_id) AS articles "
        "FROM entities e LEFT JOIN article_entities ae ON ae.entity_id=e.id "
        "WHERE e.review=1 GROUP BY e.id ORDER BY articles DESC, e.confidence DESC "
        "LIMIT ?", (limit,)).fetchall()]
