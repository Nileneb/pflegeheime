"""RSS-Ingest: holen, parsen, diffen, embedden, klassifizieren (SQLite-Pfad)."""
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
    d = embeddings.chat_json(SYSTEM, f"Titel: {title}\nText: {summary}"[:1500],
                             num_predict=120, empty={}) or {}
    return bool(d.get("relevant")), (d.get("kategorie") or "")[:60], (d.get("grund") or "")[:200]


def _domain(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url).netloc or "").replace("www.", "")


def refresh(conn, source_filter: str | None = None, since_days: int = 14,
            limit: int | None = None, extract_ner: bool = False) -> dict:
    """Holt enabled Quellen, diff't über UNIQUE(source_id,guid), embedded + klassifiziert
    neue Items. Fehler je Quelle landen in sources.last_status UND im Report (nicht geschluckt)."""
    q = "SELECT id, name, url FROM sources WHERE enabled=1 AND type='rss'"
    params: list = []
    if source_filter:
        q += " AND (name LIKE ? OR url LIKE ?)"
        params += [f"%{source_filter}%", f"%{source_filter}%"]
    srcs = conn.execute(q, params).fetchall()
    # Güte-Reihenfolge: stabil relevante Quellen zuerst (zählt bei limit/Abbruch).
    from marktradar import sources as _sources
    rank = {s["id"]: i for i, s in enumerate(_sources.stats(conn))}
    srcs = sorted(srcs, key=lambda s: rank.get(s["id"], 10**6))

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

    # Embedden + klassifizieren: NEUE Items + Heilungs-Pass über Altbestand mit
    # relevant IS NULL (z. B. weil Ollama beim letzten Lauf ausfiel). Fehler je
    # Artikel isoliert — ein toter Embed-/Chat-Host darf den Ingest nicht killen
    # (Artikel sind dann gespeichert, relevant bleibt NULL → nächster Lauf heilt).
    heal_ids = [r["id"] for r in conn.execute(
        "SELECT id FROM articles WHERE relevant IS NULL AND id NOT IN "
        f"({','.join('?' * len(new_ids)) or '0'}) ORDER BY fetched_at DESC LIMIT 30",
        list(new_ids)).fetchall()]
    pipeline_failed = []
    for aid in new_ids + heal_ids:
        row = conn.execute("SELECT title, summary FROM articles WHERE id=?", (aid,)).fetchone()
        text = f"{row['title']} {row['summary'] or ''}".strip()
        try:
            vec = embeddings.embed(text)
            conn.execute("INSERT OR REPLACE INTO article_vec(article_id, embedding) VALUES (?,?)",
                         (aid, serialize_float32(vec)))
            rel, kat, grund = classify(row["title"], row["summary"] or "")
            conn.execute("UPDATE articles SET relevant=?, kategorie=?, grund=? WHERE id=?",
                         (None if rel is None else int(rel), kat, grund, aid))
        except Exception as e:
            pipeline_failed.append({"article_id": aid, "error": f"{type(e).__name__}: {e}"})
    conn.commit()
    # Entity-Tagging + Event-Klassifikation der neuen UND geheilten Items
    # (geheilte stammen aus abgebrochenen Läufen — deren Tagging fehlt ebenso)
    from marktradar import entities, hashtags
    pipeline_ids = new_ids + heal_ids
    tagged = entities.tag_articles(conn, pipeline_ids)
    entities.classify_events(conn, pipeline_ids)
    stance = entities.classify_topics(conn, pipeline_ids)
    # Hashtag-Bildung: matchen + bei Bedarf neues Hashtag aus dem Event (LLM)
    ht = hashtags.tag_articles(conn, pipeline_ids, auto_create=True)
    report = {"new": new_total, "embedded": len(new_ids) + len(heal_ids) - len(pipeline_failed),
              "healed": len(heal_ids), "pipeline_failed": pipeline_failed,
              "tagged": tagged, "stance": stance, "hashtags": ht,
              "errors": errors, "sources": len(srcs)}
    if extract_ner:  # opt-in: LLM-NER nur auf Wunsch, Ingest-Tempo bleibt unverändert
        from marktradar import ner
        report["ner"] = ner.extract_entities(conn, article_ids=new_ids)
    return report


def archive_document(conn, title: str, content: str,
                     source_name: str = "Internes Archiv",
                     published: str | None = None) -> dict:
    """Legt ein internes Dokument als Artikel ab und läuft durch volle Pipeline:
    classify → embed → entity-tag → hashtag-tag. Immer relevant=1."""
    import hashlib
    slug = re.sub(r"[^a-z0-9]+", "-", source_name.lower()).strip("-")
    src_url = f"archive://{slug}"

    conn.execute(
        "INSERT OR IGNORE INTO sources(name,type,url,tier,region,enabled) VALUES (?,?,?,?,?,1)",
        (source_name, "archive", src_url, 1, "DE"))
    conn.commit()
    source_id = conn.execute("SELECT id FROM sources WHERE url=?", (src_url,)).fetchone()["id"]

    art_hash = hashlib.md5(title.encode()).hexdigest()[:8]
    art_url = f"archive://{slug}/{art_hash}"
    now = datetime.now(timezone.utc).isoformat()
    pub = published or now

    cur = conn.execute(
        "INSERT OR IGNORE INTO articles"
        "(source_id,source_domain,guid,link,title,summary,published,fetched_at,relevant)"
        " VALUES (?,?,?,?,?,?,?,?,1)",
        (source_id, source_name, art_url, art_url,
         title[:500], content[:1200], pub, now))
    conn.commit()

    if not cur.rowcount:
        return {"id": None, "title": title, "source": source_name, "new": False}

    aid = cur.lastrowid

    text = f"{title} {content}".strip()
    vec = embeddings.embed(text)
    conn.execute("INSERT OR REPLACE INTO article_vec(article_id, embedding) VALUES (?,?)",
                 (aid, serialize_float32(vec)))

    _rel, kat, grund = classify(title, content[:1200])
    conn.execute("UPDATE articles SET relevant=1, kategorie=?, grund=? WHERE id=?",
                 (kat, grund, aid))
    conn.commit()

    from marktradar import entities, hashtags
    entities.tag_articles(conn, [aid])
    entities.classify_events(conn, [aid])
    entities.classify_topics(conn, [aid])
    hashtags.tag_articles(conn, [aid], auto_create=True)

    return {"id": aid, "title": title, "source": source_name, "new": True}


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
