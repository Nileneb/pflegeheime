#!/usr/bin/env python3
"""Stufe 1 — Feed-Ingest + Validierung + Ollama-Relevanz für den Newsletter.

Wie der MayringCoder-Ingest: jede aktuelle Feed-Version holen, pro Artikel-Item
diffen (kein roher Seiten-Hash), neue Items mit dem lokalen Ollama auf Relevanz
prüfen. Quelle = die RSS/Atom-Feeds aus `domain_feeds` (scan_feeds.py).

Drei Phasen (einzeln schaltbar):
  A INGEST     Feed laden, validieren, Artikel upserten (ON CONFLICT DO NOTHING
               → nur echte Neuzugänge). WP-Müll (sample-page/comment) wird verworfen.
  B RELEVANZ   neue Items der letzten --since Tage via qwen3.5:9b (think:false)
               → JSON {relevant, kategorie, grund}. Bounded: nur frische Items.
  C EXPORT     out/newsletter_digest.md (nach Datum) + out/newsletter_kandidaten.csv

Resumable: Diff über UNIQUE(domain,guid); Relevanz nur wo relevant IS NULL.

  python -u scripts/ingest_feeds.py                 # alle Phasen
  python -u scripts/ingest_feeds.py --no-relevance  # nur ingest
  python -u scripts/ingest_feeds.py --only-export
  python -u scripts/ingest_feeds.py --since 14 --limit 30
"""
import argparse
import csv
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import feedparser
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect, OLLAMA_HOST

OLLAMA_MODEL = os.getenv("GF_MODEL", "qwen3.5:9b")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "de-DE,de;q=0.9"}
TIMEOUT = 12

DDL = """
ALTER TABLE domain_feeds ADD COLUMN IF NOT EXISTS feed_valid BOOLEAN;
ALTER TABLE domain_feeds ADD COLUMN IF NOT EXISTS feed_items INT;
ALTER TABLE domain_feeds ADD COLUMN IF NOT EXISTS feed_latest TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS feed_articles (
    id          BIGSERIAL PRIMARY KEY,
    domain      TEXT NOT NULL,
    feed_url    TEXT,
    guid        TEXT NOT NULL,
    link        TEXT,
    title       TEXT,
    summary     TEXT,
    published   TIMESTAMPTZ,
    relevant    BOOLEAN,
    kategorie   TEXT,
    grund       TEXT,
    source      TEXT DEFAULT 'rss',
    first_seen  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (domain, guid)
);
ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'rss';
CREATE INDEX IF NOT EXISTS feed_articles_pub_idx ON feed_articles (published DESC);
"""

JUNK_FEED = re.compile(r"/comments/feed|/sample-page/|comment-feed|/feed/feed", re.I)
_local = threading.local()


def sess():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session(); s.headers.update(HEADERS)
        _local.s = s
    return s


def clean_text(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()


def to_dt(struct):
    if not struct:
        return None
    try:
        return datetime(*struct[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def parse_feed(feed_url):
    """Return (valid, items[list of dict], latest_dt)."""
    if JUNK_FEED.search(feed_url):
        return False, [], None
    try:
        r = sess().get(feed_url, timeout=TIMEOUT, verify=False)
        if r.status_code != 200 or not r.content:
            return False, [], None
        fp = feedparser.parse(r.content)
    except Exception:
        return False, [], None
    items, latest = [], None
    for e in fp.entries:
        title = clean_text(getattr(e, "title", ""))
        if not title:
            continue
        link = getattr(e, "link", "") or ""
        guid = getattr(e, "id", "") or link or title
        pub = to_dt(getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None))
        summary = clean_text(getattr(e, "summary", ""))[:1200]
        if pub and (latest is None or pub > latest):
            latest = pub
        items.append(dict(guid=guid[:500], link=link[:1000], title=title[:500],
                          summary=summary, published=pub))
    return (len(items) >= 1), items, latest


UPSERT_ART = """
INSERT INTO feed_articles (domain, feed_url, guid, link, title, summary, published)
VALUES (%(domain)s,%(feed_url)s,%(guid)s,%(link)s,%(title)s,%(summary)s,%(published)s)
ON CONFLICT (domain, guid) DO NOTHING
"""


def ingest(conn, todo, workers):
    new_total, valid_n = 0, 0
    lock = threading.Lock(); done_n = 0; t0 = time.time()

    def work(domain, feed_url):
        valid, items, latest = parse_feed(feed_url)
        return domain, feed_url, valid, items, latest

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, d, u): d for d, u in todo}
        for fut in as_completed(futs):
            try:
                domain, feed_url, valid, items, latest = fut.result()
            except Exception:
                continue
            with lock:
                cur = conn.cursor()
                ins = 0
                if valid:
                    valid_n += 1
                    for it in items:
                        it = dict(it, domain=domain, feed_url=feed_url)
                        cur.execute(UPSERT_ART, it)
                        ins += cur.rowcount
                cur.execute("""UPDATE domain_feeds SET feed_valid=%s, feed_items=%s,
                               feed_latest=%s WHERE domain=%s""",
                            (valid, len(items), latest, domain))
                conn.commit(); cur.close()
                new_total += ins; done_n += 1
                if ins:
                    print(f"  + {ins:3} neu  {domain}", flush=True)
                if done_n % 100 == 0 or done_n == len(todo):
                    print(f"  --- {done_n}/{len(todo)} ({done_n/max(time.time()-t0,1e-6):.1f}/s) "
                          f"valid={valid_n} neue Artikel={new_total} ---", flush=True)
    return new_total, valid_n


SYSTEM = (
    "Du bewertest, ob eine Meldung von der Website eines Pflegeheims/Trägers "
    "für einen Branchen-Newsletter relevant ist. RELEVANT = echte Neuigkeit oder "
    "Artikel: Veranstaltung, Eröffnung/Umbau, Auszeichnung, Projekt, Personalie, "
    "neues Angebot, Bericht, Aktion. NICHT RELEVANT = Navigation, Cookie-/Daten"
    "schutz-/Impressum-Text, leere Platzhalter, reine Rechtstexte, reine Sammel-"
    "Übersichtsseiten. Antworte NUR als JSON: "
    '{"relevant": true/false, "kategorie": "<1-2 Worte>", "grund": "<kurz>"}'
)


# Stellenanzeigen deterministisch erkennen (verlässlicher als das LLM) und als
# eigene Kategorie führen — sie sollen erhalten bleiben, aber separat.
JOB_RE = re.compile(
    r"\(\s*m\s*/?\s*w\s*/?\s*d\s*\)|\bm\s*/\s*w\s*/\s*d\b|\(\s*w\s*/?\s*m\s*/?\s*d\s*\)|"
    r"stellenangebot|stellenanzeige|stellenausschreibung|\bgesucht\b|wir suchen|"
    r"freie\s+stelle|ausbildungsplatz|\bbewerbung\b|jobangebot|\bm/w\b",
    re.I,
)


def is_job(title, summary=""):
    return bool(JOB_RE.search(f"{title} {summary}"))


def classify(title, summary):
    if is_job(title, summary):
        return True, "Stellenanzeige", "Stellenausschreibung (regelbasiert erkannt)"
    payload = {"model": OLLAMA_MODEL, "format": "json", "stream": False, "think": False,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": f"Titel: {title}\nText: {summary}"[:1500]}],
               "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 120}}
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=90)
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "") or "{}"
        d = json.loads(content)
        return bool(d.get("relevant")), (d.get("kategorie") or "")[:60], (d.get("grund") or "")[:200]
    except Exception:
        return None, None, None


def retag_jobs(conn):
    """Bestehende Items, die Stellenanzeigen sind, deterministisch umkategorisieren."""
    cur = conn.cursor()
    cur.execute("SELECT id, title, summary FROM feed_articles")
    upd = [(aid,) for aid, t, s in cur.fetchall() if is_job(t or "", s or "")]
    for (aid,) in upd:
        cur.execute("UPDATE feed_articles SET relevant=TRUE, kategorie='Stellenanzeige', "
                    "grund='Stellenausschreibung (regelbasiert erkannt)' WHERE id=%s", (aid,))
    conn.commit(); cur.close()
    print(f"Job-Retag: {len(upd)} Items als Stellenanzeige markiert.", flush=True)


def relevance(conn, since_days, workers):
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    cur = conn.cursor()
    cur.execute("""SELECT id, title, summary FROM feed_articles
                   WHERE relevant IS NULL AND published IS NOT NULL
                     AND published >= %s AND published <= NOW()
                   ORDER BY published DESC""", (cutoff,))
    rows = cur.fetchall(); cur.close()
    print(f"Relevanz-Check: {len(rows)} frische Items (<= {since_days} Tage)", flush=True)
    lock = threading.Lock(); done_n = 0; rel_n = 0; t0 = time.time()

    def work(aid, title, summary):
        return aid, classify(title, summary)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, aid, t, s) for aid, t, s in rows]
        for fut in as_completed(futs):
            aid, (rel, kat, grund) = fut.result()
            if rel is None:
                continue
            with lock:
                cur = conn.cursor()
                cur.execute("UPDATE feed_articles SET relevant=%s, kategorie=%s, grund=%s WHERE id=%s",
                            (rel, kat, grund, aid))
                conn.commit(); cur.close()
                done_n += 1; rel_n += 1 if rel else 0
                if done_n % 50 == 0 or done_n == len(rows):
                    print(f"  --- {done_n}/{len(rows)} ({done_n/max(time.time()-t0,1e-6):.1f}/s) "
                          f"relevant={rel_n} ---", flush=True)
    return rel_n


def export(conn, since_days, md_path, csv_path):
    cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
    cur = conn.cursor()
    cur.execute("""SELECT DISTINCT ON (a.domain, a.link)
                          a.domain, a.title, a.link, a.published, a.kategorie, a.grund
                   FROM feed_articles a
                   WHERE a.relevant IS TRUE AND a.published >= %s AND a.published <= NOW()
                   ORDER BY a.domain, a.link, a.published DESC""", (cutoff,))
    rows = cur.fetchall(); cur.close()
    # nach Datum sortieren (DISTINCT ON erzwingt domain/link-Sortierung)
    rows.sort(key=lambda r: r[3] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    jobs = [r for r in rows if (r[4] or "") == "Stellenanzeige"]
    news = [r for r in rows if (r[4] or "") != "Stellenanzeige"]
    os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Datum", "Quelle (Domain)", "Titel", "Kategorie", "Grund", "Link"])
        for dom, title, link, pub, kat, grund in rows:
            w.writerow([pub.strftime("%Y-%m-%d") if pub else "", dom, title, kat, grund, link])
    lines = [f"# Pflegeheim-Newsletter — Kandidaten (letzte {since_days} Tage)",
             f"_Erstellt {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
             f"{len(news)} Meldungen + {len(jobs)} Stellenangebote_\n",
             "\n# Meldungen\n"]
    cur_day = None
    for dom, title, link, pub, kat, grund in news:
        day = pub.strftime("%Y-%m-%d") if pub else "ohne Datum"
        if day != cur_day:
            lines.append(f"\n## {day}\n"); cur_day = day
        tag = f" _({kat})_" if kat else ""
        lines.append(f"- **{title}** — {dom}{tag}  \n  {link}")
    lines.append(f"\n\n# Stellenangebote ({len(jobs)})\n")
    cur_day = None
    for dom, title, link, pub, kat, grund in jobs:
        day = pub.strftime("%Y-%m-%d") if pub else "ohne Datum"
        if day != cur_day:
            lines.append(f"\n## {day}\n"); cur_day = day
        lines.append(f"- **{title}** — {dom}  \n  {link}")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nExport: {md_path} + {csv_path}  "
          f"({len(news)} Meldungen + {len(jobs)} Stellenangebote)", flush=True)
    return len(rows)


def main():
    import urllib3
    urllib3.disable_warnings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--rel-workers", type=int, default=2)
    ap.add_argument("--since", type=int, default=30)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-relevance", action="store_true")
    ap.add_argument("--only-export", action="store_true")
    ap.add_argument("--only-relevance-export", action="store_true",
                    help="Feed-Ingest überspringen (z.B. nach scrape_news.py), nur Relevanz + Export")
    ap.add_argument("--md", default="out/newsletter_digest.md")
    ap.add_argument("--csv", default="out/newsletter_kandidaten.csv")
    args = ap.parse_args()

    conn = db_connect()
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

    do_ingest = not (args.only_export or args.only_relevance_export)
    do_relevance = not (args.only_export or args.no_relevance)

    if do_ingest:
        with conn.cursor() as cur:
            cur.execute("""SELECT domain, rss_url FROM domain_feeds
                           WHERE has_feed AND rss_url IS NOT NULL""")
            todo = cur.fetchall()
        if args.limit:
            todo = todo[:args.limit]
        print(f"Feed-Domains: {len(todo)}", flush=True)
        new_total, valid_n = ingest(conn, todo, args.workers)
        print(f"Ingest fertig: {valid_n} gültige Feeds, {new_total} neue Artikel.\n", flush=True)

    if do_relevance:
        relevance(conn, args.since, args.rel_workers)

    retag_jobs(conn)
    export(conn, args.since, args.md, args.csv)
    conn.close()


if __name__ == "__main__":
    main()
