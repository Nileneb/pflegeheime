"""RSS-Ingest: holen, parsen, diffen, embedden, klassifizieren (SQLite-Pfad)."""
import json
import os
import re
from datetime import datetime, timezone

import feedparser
import requests
import urllib3

from marktradar import db, embeddings
from sqlite_vec import serialize_float32

# WHY: fetch() nutzt bewusst verify=False (kaputte TLS-Ketten auf vielen Heim-Sites);
# nur DIESE eine Warnung dämpfen, nicht alle.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OLLAMA_HOST = embeddings.OLLAMA_HOST
RELEVANCE_MODEL = os.getenv("CHAT_MODEL", "qwen3.5:9b")
HEADERS = {"User-Agent": "pflege-marktradar/0.1 (+research; contact: benedikt.linn@code.berlin)",
           "Accept-Language": "de-DE,de;q=0.9"}
TIMEOUT = 12

JUNK_FEED = re.compile(r"/comments/feed|/sample-page/|comment-feed|/feed/feed", re.I)
JOB_RE = re.compile(
    r"\(\s*m\s*/?\s*w\s*/?\s*d\s*\)|\bm\s*/\s*w\s*/\s*d\b|\(\s*w\s*/?\s*m\s*/?\s*d\s*\)|"
    r"stellenangebot|stellenanzeige|stellenausschreibung|\bgesucht\b|wir suchen|"
    r"freie\s+stelle|ausbildungsplatz|\bbewerbung\b|jobangebot|\bm/w\b", re.I)

SYSTEM = (
    "Du bewertest, ob eine Meldung von der Website eines Pflegeheims/Trägers "
    "für einen Branchen-Newsletter relevant ist. RELEVANT = echte Neuigkeit: "
    "Veranstaltung, Eröffnung/Umbau, Auszeichnung, Projekt, Personalie, neues "
    "Angebot, Bericht, Aktion. NICHT RELEVANT = Navigation, Cookie-/Datenschutz-/"
    "Impressum-Text, Platzhalter, reine Rechtstexte, Sammel-Übersichtsseiten. "
    'Antworte NUR als JSON: {"relevant": true/false, "kategorie": "<1-2 Worte>", "grund": "<kurz>"}')


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def fetch(url: str) -> bytes | None:
    if JUNK_FEED.search(url):
        return None
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, verify=False)
    if r.status_code != 200 or not r.content:
        return None
    return r.content


def parse_feed(content: bytes) -> list[dict]:
    fp = feedparser.parse(content)
    items = []
    for e in fp.entries:
        title = _clean(getattr(e, "title", ""))
        if not title:
            continue
        link = (getattr(e, "link", "") or "")[:1000]
        guid = (getattr(e, "id", "") or link or title)[:500]
        struct = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        pub = None
        if struct:
            try:
                pub = datetime(*struct[:6], tzinfo=timezone.utc).isoformat()
            except (ValueError, TypeError):
                pub = None
        items.append(dict(guid=guid, link=link, title=title[:500],
                          summary=_clean(getattr(e, "summary", ""))[:1200], published=pub))
    return items


def is_job(title: str, summary: str = "") -> bool:
    return bool(JOB_RE.search(f"{title} {summary}"))


def classify(title: str, summary: str) -> tuple:
    if is_job(title, summary):
        return True, "Stellenanzeige", "Stellenausschreibung (regelbasiert erkannt)"
    payload = {"model": RELEVANCE_MODEL, "format": "json", "stream": False, "think": False,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": f"Titel: {title}\nText: {summary}"[:1500]}],
               "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 120}}
    r = requests.post(f"{embeddings.CHAT_HOST}/api/chat", json=payload,
                      headers=embeddings.chat_headers(), timeout=90)
    r.raise_for_status()
    d = json.loads(r.json().get("message", {}).get("content", "") or "{}")
    return bool(d.get("relevant")), (d.get("kategorie") or "")[:60], (d.get("grund") or "")[:200]


def _domain(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).netloc or "").replace("www.", "")


def refresh(conn, source_filter: str | None = None, since_days: int = 14,
            limit: int | None = None) -> dict:
    """Holt enabled Quellen, diff't über UNIQUE(source_id,guid), embedded + klassifiziert
    neue Items. Fehler je Quelle landen in sources.last_status UND im Report (nicht geschluckt)."""
    q = "SELECT id, name, url FROM sources WHERE enabled=1 AND type='rss'"
    params: list = []
    if source_filter:
        q += " AND (name LIKE ? OR url LIKE ?)"
        params += [f"%{source_filter}%", f"%{source_filter}%"]
    srcs = conn.execute(q, params).fetchall()

    new_total, errors, new_ids = 0, [], []
    now = datetime.now(timezone.utc).isoformat()
    for s in srcs:
        try:
            content = fetch(s["url"])
            if content is None:
                conn.execute("UPDATE sources SET last_fetched=?, last_status=? WHERE id=?",
                             (now, "error: kein Feed/Non-200", s["id"]))
                errors.append({"source": s["name"], "error": "kein Feed/Non-200"})
                conn.commit()
                continue
            items = parse_feed(content)
            if limit is not None:
                items = items[:limit]
            ins = 0
            for it in items:
                # WHY: presseportal-Themenfeeds teilen denselben Artikel → quellen-
                # übergreifend nach Link deduppen, sonst Dubletten je Themen-Feed.
                if it["link"] and conn.execute(
                        "SELECT 1 FROM articles WHERE link=? LIMIT 1", (it["link"],)).fetchone():
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO articles"
                    "(source_id,source_domain,guid,link,title,summary,published,fetched_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (s["id"], _domain(s["url"]), it["guid"], it["link"], it["title"],
                     it["summary"], it["published"], now))
                if cur.rowcount:
                    new_ids.append(cur.lastrowid)
                    ins += 1
            conn.execute("UPDATE sources SET last_fetched=?, last_status=? WHERE id=?",
                         (now, "ok", s["id"]))
            conn.commit()
            new_total += ins
        except Exception as e:  # WHY: Fehler je Quelle isolieren, aber NICHT verschlucken
            conn.execute("UPDATE sources SET last_fetched=?, last_status=? WHERE id=?",
                         (now, f"error: {e}", s["id"]))
            conn.commit()
            errors.append({"source": s["name"], "error": str(e)})

    # Embedden + klassifizieren der frisch eingefügten Items
    for aid in new_ids:
        row = conn.execute("SELECT title, summary FROM articles WHERE id=?", (aid,)).fetchone()
        text = f"{row['title']} {row['summary'] or ''}".strip()
        vec = embeddings.embed(text)
        conn.execute("INSERT OR REPLACE INTO article_vec(article_id, embedding) VALUES (?,?)",
                     (aid, serialize_float32(vec)))
        rel, kat, grund = classify(row["title"], row["summary"] or "")
        conn.execute("UPDATE articles SET relevant=?, kategorie=?, grund=? WHERE id=?",
                     (None if rel is None else int(rel), kat, grund, aid))
    conn.commit()
    # Entity-Tagging + Event-Klassifikation der neuen Items (deterministisch)
    from marktradar import entities
    tagged = entities.tag_articles(conn, new_ids)
    entities.classify_events(conn, new_ids)
    stance = entities.classify_topics(conn, new_ids)
    return {"new": new_total, "embedded": len(new_ids), "tagged": tagged,
            "stance": stance, "errors": errors, "sources": len(srcs)}


def backfill_embeddings(conn, limit: int | None = None) -> int:
    """Embeddet Artikel ohne article_vec-Eintrag (z. B. migrierte Altartikel). Idempotent.
    Gibt Anzahl neu embeddeter zurück."""
    q = ("SELECT a.id, a.title, a.summary FROM articles a "
         "LEFT JOIN article_vec v ON v.article_id = a.id WHERE v.article_id IS NULL")
    if limit is not None:
        q += f" LIMIT {int(limit)}"
    n = 0
    for r in conn.execute(q).fetchall():
        text = f"{r['title'] or ''} {r['summary'] or ''}".strip()
        if not text:
            continue
        vec = embeddings.embed(text)
        conn.execute("INSERT OR REPLACE INTO article_vec(article_id, embedding) VALUES (?,?)",
                     (r["id"], serialize_float32(vec)))
        n += 1
    conn.commit()
    return n
