#!/usr/bin/env python3
"""Feasibility-Scan: welche Domains haben RSS-Feed / "Aktuelles"-Seite?

Vorstufe zum Newsletter-Ingest. Für jede ERREICHBARE Domain (domain_social
ok/no_social) holen wir die Startseite und prüfen:
  - RSS/Atom-Autodiscovery (<link rel=alternate type=application/rss+xml>)
  - Feed-Links im <a href> (/feed, /rss, .xml)
  - "Aktuelles"/News/Presse/Blog-Navigationslink

Nur Lesen + Reporting. Ergebnis in Tabelle domain_feeds. Mit  python -u  starten.

  python -u scripts/scan_feeds.py --workers 14
"""
import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "de-DE,de;q=0.9"}
TIMEOUT = 8

NEWS_WORDS = ("aktuelles", "aktuell", "neuigkeiten", "news", "presse",
              "blog", "meldungen", "veranstaltungen", "termine", "magazin")

DDL = """
CREATE TABLE IF NOT EXISTS domain_feeds (
    domain     TEXT PRIMARY KEY,
    rss_url    TEXT,
    feed_count INTEGER DEFAULT 0,
    news_url   TEXT,
    has_feed   INTEGER DEFAULT 0,
    has_news   INTEGER DEFAULT 0,
    status     TEXT,
    scanned_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_local = threading.local()


def sess():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session(); s.headers.update(HEADERS)
        _local.s = s
    return s


def fetch(url):
    try:
        r = sess().get(url, timeout=TIMEOUT, allow_redirects=True, verify=False)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
            return r.text, r.url
    except Exception:
        pass
    return None, None


def is_feed_href(href: str) -> bool:
    h = href.lower()
    if "feedback" in h:
        return False
    return ("/feed" in h or "rss" in h or h.endswith(".xml")
            or "atom" in h or "format=feed" in h)


def find_feeds(soup, base):
    feeds = []
    for ln in soup.find_all("link", attrs={"rel": True}):
        rel = " ".join(ln.get("rel") or []).lower()
        typ = (ln.get("type") or "").lower()
        if "alternate" in rel and ("rss" in typ or "atom" in typ) and ln.get("href"):
            feeds.append(urljoin(base, ln["href"]))
    for a in soup.find_all("a", href=True):
        if is_feed_href(a["href"]):
            feeds.append(urljoin(base, a["href"]))
    # dedupe
    seen, out = set(), []
    for f in feeds:
        if f not in seen:
            seen.add(f); out.append(f)
    return out


def find_news(soup, base):
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        txt = (a.get_text() or "").strip().lower()
        hl = href.lower()
        if href.lower().startswith(("mailto:", "javascript:", "#")):
            continue
        if any(w == txt or w in txt for w in NEWS_WORDS) or any(("/" + w) in hl for w in NEWS_WORDS):
            return urljoin(base, href)
    return None


def process(domain, url):
    html, final = fetch(url)
    if not html:
        return dict(domain=domain, rss_url=None, feed_count=0, news_url=None,
                    has_feed=False, has_news=False, status="fetch_failed")
    soup = BeautifulSoup(html, "lxml")
    feeds = find_feeds(soup, final or url)
    news = find_news(soup, final or url)
    return dict(domain=domain, rss_url=(feeds[0] if feeds else None),
                feed_count=len(feeds), news_url=news,
                has_feed=bool(feeds), has_news=bool(news), status="ok")


UPSERT = """
INSERT INTO domain_feeds (domain, rss_url, feed_count, news_url, has_feed, has_news, status, scanned_at)
VALUES (%(domain)s,%(rss_url)s,%(feed_count)s,%(news_url)s,%(has_feed)s,%(has_news)s,%(status)s,NOW())
ON CONFLICT (domain) DO UPDATE SET
  rss_url=EXCLUDED.rss_url, feed_count=EXCLUDED.feed_count, news_url=EXCLUDED.news_url,
  has_feed=EXCLUDED.has_feed, has_news=EXCLUDED.has_news, status=EXCLUDED.status, scanned_at=NOW()
"""


def main():
    import urllib3
    urllib3.disable_warnings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=14)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    conn = db_connect()
    with conn.cursor() as cur:
        cur.execute(DDL)
        # erreichbare Domains aus dem Social-Crawl (haben verifizierte source_url)
        cur.execute("""SELECT domain, source_url FROM domain_social
                       WHERE status IN ('ok','no_social') AND source_url IS NOT NULL""")
        reachable = cur.fetchall()
        cur.execute("SELECT domain FROM domain_feeds WHERE status IS NOT NULL")
        done = {r[0] for r in cur.fetchall()}
    conn.commit()
    todo = [(d, u) for d, u in reachable if d not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"Erreichbare Domains: {len(reachable)} | erledigt: {len(done)} | jetzt: {len(todo)}", flush=True)

    lock = threading.Lock(); stats = {}; done_n = 0; t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, d, u): d for d, u in todo}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = dict(domain=d, rss_url=None, feed_count=0, news_url=None,
                           has_feed=False, has_news=False, status=f"error:{str(exc)[:25]}")
            with lock:
                with conn.cursor() as cur:
                    cur.execute(UPSERT, res)
                conn.commit()
                done_n += 1
                key = "feed+news" if res["has_feed"] and res["has_news"] else \
                      "feed" if res["has_feed"] else \
                      "news" if res["has_news"] else res["status"]
                stats[key] = stats.get(key, 0) + 1
                if done_n % 100 == 0 or done_n == len(todo):
                    rate = done_n / max(time.time() - t0, 1e-6)
                    print(f"  --- {done_n}/{len(todo)} ({rate:.1f}/s) {stats} ---", flush=True)
    conn.close()
    print(f"\nFertig. {stats}", flush=True)


if __name__ == "__main__":
    main()
