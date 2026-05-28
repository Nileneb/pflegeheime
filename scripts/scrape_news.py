#!/usr/bin/env python3
"""Stufe 2 — HTML-Scrape der "Aktuelles"-Seiten (Domains OHNE gültigen RSS-Feed).

Stufe 1 (ingest_feeds.py) deckt die ~173 echten Feeds ab. Hier holen wir die
News-/Aktuelles-Seite der übrigen erreichbaren Domains und extrahieren die
Artikel-Liste (Titel + Link + Datum) heuristisch (article-Tags, dann Überschrift-
verankerte Links). Item-Diff über dieselbe Tabelle `feed_articles` (source='html',
UNIQUE domain+guid). Nur DATIERTE Items werden später vom Relevanz-Schritt
aufgegriffen — so flutet der Erstlauf nicht mit undatiertem Navigations-Müll.

Danach:  python -u scripts/ingest_feeds.py --only-relevance-export
(oder ingest_feeds.py normal — re-ingestet Feeds billig + klassifiziert die neuen
HTML-Items + Export).

  python -u scripts/scrape_news.py --workers 12
"""
import argparse
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "de-DE,de;q=0.9"}
TIMEOUT = 10

NAV = {"startseite", "kontakt", "impressum", "datenschutz", "aktuelles", "aktuell",
       "news", "mehr", "weiterlesen", "weiter", "zurück", "anmelden", "login",
       "suche", "menü", "navigation", "karriere", "jobs", "stellenangebote",
       "presse", "downloads", "spenden", "newsletter", "über uns", "leistungen",
       "angebote", "veranstaltungen", "termine", "blog", "home", "alle anzeigen",
       "mehr erfahren", "details", "read more"}

MONTHS = {"januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5,
          "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
          "november": 11, "dezember": 12, "jan": 1, "feb": 2, "mär": 3, "apr": 4,
          "jun": 6, "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dez": 12}

RE_DMY = re.compile(r"\b(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b")
RE_TEXT = re.compile(r"\b(\d{1,2})\.\s*([A-Za-zäöü]+)\s+(\d{4})\b")
RE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

_local = threading.local()


def sess():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session(); s.headers.update(HEADERS)
        _local.s = s
    return s


def clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def parse_date(text):
    if not text:
        return None
    m = RE_ISO.search(text)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
        except Exception:
            pass
    m = RE_DMY.search(text)
    if m:
        try:
            return datetime(int(m[3]), int(m[2]), int(m[1]), tzinfo=timezone.utc)
        except Exception:
            pass
    m = RE_TEXT.search(text)
    if m:
        mo = MONTHS.get(m[2].lower())
        if mo:
            try:
                return datetime(int(m[3]), mo, int(m[1]), tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def good_title(t):
    if not t or not (15 <= len(t) <= 180):
        return False
    if t.lower() in NAV:
        return False
    if len(t.split()) < 3:
        return False
    return True


def same_domain(link, domain):
    try:
        h = urlparse(link).netloc.lower().replace("www.", "")
        return h == domain or h.endswith("." + domain) or domain.endswith("." + h)
    except Exception:
        return False


def find_date(container):
    """Datum aus einem Artikel-Container: HTML5 <time> zuerst, dann Text-Muster."""
    if container is None:
        return None
    for tm in container.find_all("time"):
        d = parse_date(tm.get("datetime") or "") or parse_date(tm.get_text())
        if d:
            return d
    # <meta>/Attribute mit Datum (z.B. data-date)
    d = parse_date(container.get_text(" ", strip=True))
    if d:
        return d
    par = container.parent
    return find_date_shallow(par) if par else None


def find_date_shallow(el):
    for tm in el.find_all("time"):
        d = parse_date(tm.get("datetime") or "") or parse_date(tm.get_text())
        if d:
            return d
    return None


def extract_items(html, base, domain):
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style"]):
        t.decompose()
    found = {}

    def consider(title, link, container):
        title = clean(title)
        if not good_title(title) or not link:
            return
        link = urljoin(base, link.strip())
        if not link.lower().startswith("http") or not same_domain(link, domain):
            return
        link = link.split("#")[0]
        if link.rstrip("/") == base.rstrip("/"):
            return
        date = find_date(container)
        prev = found.get(link)
        # bevorzuge Eintrag mit Datum / längerem Titel
        if prev is None or (date and not prev[1]) or (len(title) > len(prev[0])):
            found[link] = (title, date)

    # 1) <article>-Blöcke
    for art in soup.find_all("article"):
        h = art.find(["h1", "h2", "h3", "h4"])
        a = (h.find("a", href=True) if h else None) or art.find("a", href=True)
        title = (h.get_text() if h else (a.get_text() if a else ""))
        if a:
            consider(title, a["href"], art)

    # 2) Überschrift-verankerte Links (Fallback / Ergänzung)
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        a = h.find("a", href=True) or h.find_next("a", href=True)
        if not a:
            continue
        container = h.find_parent(["article", "li"]) or h.find_parent("div") or h.parent
        consider(h.get_text(), a["href"], container)

    return [(lnk, t, d) for lnk, (t, d) in found.items()]


def fetch(url):
    try:
        r = sess().get(url, timeout=TIMEOUT, allow_redirects=True, verify=False)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
            return r.text, r.url
    except Exception:
        pass
    return None, None


def process(domain, news_url):
    html, final = fetch(news_url)
    if not html:
        return domain, news_url, None
    return domain, news_url, extract_items(html, final or news_url, domain)


UPSERT = """
INSERT INTO feed_articles (domain, feed_url, guid, link, title, summary, published, source)
VALUES (%s,%s,%s,%s,%s,'',%s,'html')
ON CONFLICT (domain, guid) DO NOTHING
"""


def main():
    import urllib3
    urllib3.disable_warnings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    conn = db_connect(); cur = conn.cursor()
    cur.execute("ALTER TABLE feed_articles ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'rss'")
    conn.commit()
    cur.execute("""SELECT domain, news_url FROM domain_feeds
                   WHERE status='ok' AND has_news AND news_url IS NOT NULL
                     AND (feed_valid IS NOT TRUE)""")
    todo = cur.fetchall()
    if args.limit:
        todo = todo[:args.limit]
    print(f"News-Seiten ohne gültigen Feed: {len(todo)}", flush=True)

    lock = threading.Lock(); done_n = 0; new_total = 0; with_items = 0; t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, d, u): d for d, u in todo}
        for fut in as_completed(futs):
            try:
                domain, news_url, items = fut.result()
            except Exception:
                items = None; domain = futs[fut]
            with lock:
                ins = 0
                if items:
                    with_items += 1
                    for link, title, date in items:
                        cur.execute(UPSERT, (domain, news_url, link, link, title, date))
                        ins += cur.rowcount
                    conn.commit()
                done_n += 1; new_total += ins
                if ins:
                    dated = sum(1 for _, _, d in items if d)
                    print(f"  + {ins:3} ({dated} datiert)  {domain}", flush=True)
                if done_n % 100 == 0 or done_n == len(todo):
                    print(f"  --- {done_n}/{len(todo)} ({done_n/max(time.time()-t0,1e-6):.1f}/s) "
                          f"Seiten mit Items={with_items} neue Items={new_total} ---", flush=True)
    conn.close()
    print(f"\nFertig. {new_total} neue HTML-Items aus {with_items} Seiten.", flush=True)


if __name__ == "__main__":
    main()
