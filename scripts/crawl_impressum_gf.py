#!/usr/bin/env python3
"""Phase 2 — Geschäftsführer aus dem Impressum, pro Domain.

Geschäftsführung ist eine TRÄGER-Eigenschaft: viele Heime teilen sich eine
Website (korian.de × 58, alloheim.de × 56 …). Wir crawlen jede Domain GENAU
EINMAL und mappen das Ergebnis in Phase 3 auf alle Heime der Domain zurück.

Extraktion: REGEX-FIRST (gf_extract.extract_gf) — die §5-TMG-Angaben sind
kanonisch und werden wörtlich aus dem Text geschnitten (grounded by construction,
keine Halluzination, kein LLM nötig). Nur wenn ein GF-Keyword im Text steht, die
Regex aber keinen Namen fasst, fragt ein kurzer Ollama-Call mit KLEINEM Kontext.

Bounded fetching: max 3 HTTP-Requests/Domain à 8s → keine Hänger.
Resumable über domain_impressum.crawled_at. Unbuffered: mit  python -u  starten.

Usage:
  python -u scripts/crawl_impressum_gf.py --workers 12
  python -u scripts/crawl_impressum_gf.py --limit 30 --workers 12
  python -u scripts/crawl_impressum_gf.py --no-ollama        # nur regex
"""
import argparse
import json
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
from data_cleaner import db_connect, OLLAMA_HOST
from scripts.gf_extract import extract_gf, has_gf_keyword  # noqa: E402

OLLAMA_MODEL = os.getenv("GF_MODEL", "qwen3.5:9b")
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "de-DE,de;q=0.9"}
FETCH_TIMEOUT = 8

DDL = """
CREATE TABLE IF NOT EXISTS domain_impressum (
    domain          TEXT PRIMARY KEY,
    sample_url      TEXT,
    impressum_url   TEXT,
    geschaeftsfuehrung TEXT,
    traeger         TEXT,
    quelle_zitat    TEXT,
    method          TEXT,
    grounded        BOOLEAN,
    crawl_status    TEXT,
    impressum_text  TEXT,
    crawled_at      TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE domain_impressum ADD COLUMN IF NOT EXISTS method TEXT;
ALTER TABLE domain_impressum ADD COLUMN IF NOT EXISTS impressum_text TEXT;
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
        r = sess().get(url, timeout=FETCH_TIMEOUT, allow_redirects=True)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
            return r.text, r.url
    except Exception:
        pass
    return None, None


def page_text(html):
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer"]):
        t.decompose()
    return soup.get_text(" ", strip=True)


def find_impressum_url(base_url, html):
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if "impressum" in href.lower() or "impressum" in (a.get_text() or "").lower():
            return urljoin(base_url, href)
    return None


def get_impressum(sample_url):
    """≤3 requests: sample page → impressum link → fetch; else one /impressum guess."""
    parsed = urlparse(sample_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    html, final = fetch(sample_url)
    if html:
        imp = find_impressum_url(final or sample_url, html)
        if imp:
            ihtml, ifinal = fetch(imp)
            if ihtml:
                return (ifinal or imp), page_text(ihtml)
        if "impressum" in html.lower():        # sample page IS the impressum
            return (final or sample_url), page_text(html)
    ihtml, ifinal = fetch(root + "/impressum")
    if ihtml and "impressum" in ihtml.lower():
        return (ifinal or root + "/impressum"), page_text(ihtml)
    return None, ""


TRAEGER_RE = re.compile(
    r"([A-ZÄÖÜ][\w\.\-&äöüß]+(?:[ \-][A-ZÄÖÜ][\w\.\-&äöüß]+){0,6}\s*"
    r"(?:gGmbH|GmbH(?:\s*&\s*Co\.?\s*KG)?|gAG|AG|e\.?\s?V\.?|KG|GbR|gUG|UG|Stiftung|"
    r"Gesellschaft|Werk|Verband|Verein))",
)


def guess_traeger(text):
    m = TRAEGER_RE.search(text or "")
    return m.group(1).strip() if m else None


SYSTEM = ("Extrahiere aus diesem Impressum-Ausschnitt NUR die Namen der "
          "Geschäftsführer/Vorstände/Inhaber, die WÖRTLICH im Text stehen. "
          'Antworte als JSON: {"gf":"Name1, Name2"} oder {"gf":""} wenn keine genannt. '
          "Keine Halluzination, keine Rollen, nur Personennamen aus dem Text.")


def ollama_fallback(window):
    payload = {"model": OLLAMA_MODEL, "format": "json", "stream": False,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": window[:1500]}],
               "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 100}}
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=90)
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "") or ""
        gf = (json.loads(content).get("gf") or "").strip()
        return gf or None
    except Exception:
        return None


def normalize(s):
    return re.sub(r"[^a-zäöüß ]", " ", (s or "").lower())


def grounded(gf, text):
    nt = normalize(text)
    toks = [t for t in normalize(gf).split() if len(t) >= 4]
    if not toks:
        return False
    return sum(1 for t in toks if t in nt) >= (len(toks) + 1) // 2


def crawl_domain(domain, sample_url, use_ollama):
    imp_url, text = get_impressum(sample_url)
    if not text:
        return dict(domain=domain, sample_url=sample_url, impressum_url=imp_url,
                    geschaeftsfuehrung=None, traeger=None, quelle_zitat=None,
                    method="none", grounded=False, crawl_status="no_impressum",
                    impressum_text=None)
    traeger = guess_traeger(text)
    gf, method = extract_gf(text)
    if not gf and use_ollama and has_gf_keyword(text):
        m = re.search(r".{0,60}(gesch[aä]ftsf[uü]hr|vertretungsberechtigt|vertreten\s+durch|vorstand)", text, re.I)
        window = text[m.start():m.start() + 600] if m else text[:600]
        gf = ollama_fallback(window)
        method = "ollama" if gf else "none"
    if gf and grounded(gf, text):
        status = "ok"
    else:
        gf, status = None, ("no_gf_in_text" if has_gf_keyword(text) else "no_gf_keyword")
    return dict(domain=domain, sample_url=sample_url, impressum_url=imp_url,
                geschaeftsfuehrung=gf, traeger=traeger,
                quelle_zitat=None, method=method, grounded=bool(gf),
                crawl_status=status, impressum_text=text[:8000])


def regdom(u):
    try:
        return urlparse(u).netloc.lower().replace("www.", "")
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-ollama", action="store_true")
    args = ap.parse_args()
    use_ollama = not args.no_ollama

    conn = db_connect()
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT web_api FROM pflegeheime WHERE web_api IS NOT NULL AND web_api<>''")
        urls = [r[0] for r in cur.fetchall()]
    dom_sample = {}
    for u in urls:
        d = regdom(u)
        if not d:
            continue
        if d not in dom_sample or len(urlparse(u).path) > len(urlparse(dom_sample[d]).path):
            dom_sample[d] = u

    with conn.cursor() as cur:
        cur.execute("SELECT domain FROM domain_impressum WHERE crawl_status IS NOT NULL")
        done = {r[0] for r in cur.fetchall()}
    todo = [(d, s) for d, s in sorted(dom_sample.items()) if d not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"Domains gesamt: {len(dom_sample)} | erledigt: {len(done)} | jetzt: {len(todo)} | ollama={use_ollama}", flush=True)

    UPSERT = """
        INSERT INTO domain_impressum
          (domain, sample_url, impressum_url, geschaeftsfuehrung, traeger,
           quelle_zitat, method, grounded, crawl_status, impressum_text, crawled_at)
        VALUES (%(domain)s,%(sample_url)s,%(impressum_url)s,%(geschaeftsfuehrung)s,
                %(traeger)s,%(quelle_zitat)s,%(method)s,%(grounded)s,%(crawl_status)s,
                %(impressum_text)s,NOW())
        ON CONFLICT (domain) DO UPDATE SET
          sample_url=EXCLUDED.sample_url, impressum_url=EXCLUDED.impressum_url,
          geschaeftsfuehrung=EXCLUDED.geschaeftsfuehrung, traeger=EXCLUDED.traeger,
          method=EXCLUDED.method, grounded=EXCLUDED.grounded,
          crawl_status=EXCLUDED.crawl_status, impressum_text=EXCLUDED.impressum_text,
          crawled_at=NOW()
    """
    lock = threading.Lock()
    stats = {}
    done_n = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(crawl_domain, d, s, use_ollama): d for d, s in todo}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = dict(domain=d, sample_url=dom_sample.get(d), impressum_url=None,
                           geschaeftsfuehrung=None, traeger=None, quelle_zitat=None,
                           method="none", grounded=False, crawl_status=f"error:{str(exc)[:30]}")
            with lock:
                with conn.cursor() as cur:
                    cur.execute(UPSERT, res)
                conn.commit()
                done_n += 1
                st = res["crawl_status"]
                stats[st] = stats.get(st, 0) + 1
                if st == "ok":
                    print(f"  ✓ {res['domain']:30} [{res['method']}] {res['geschaeftsfuehrung'][:55]}", flush=True)
                if done_n % 50 == 0 or done_n == len(todo):
                    rate = done_n / max(time.time() - t0, 1e-6)
                    print(f"  --- {done_n}/{len(todo)} ({rate:.1f} dom/s) {stats} ---", flush=True)
    conn.close()
    print(f"\nFertig. {stats}", flush=True)


if __name__ == "__main__":
    main()
