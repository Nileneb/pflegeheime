#!/usr/bin/env python3
"""Phase 2c — Recover the no_impressum domains with a more robust fetcher.

The first pass failed on ~190 domains for two reasons we can fix:
  • SSL errors        → retry with verify=False (expired/mismatched certs are
                        common on small Träger sites) and follow cross-domain
                        redirects (cellitinnen.de → cellitinnenhaeuser.de).
  • odd Impressum path → scan the resolved homepage for the Impressum link AND
                        try a larger brute-force path list on the final host.

Only touches domain_impressum rows with crawl_status='no_impressum'. Stores text
and re-extracts (regex + Ollama for low-confidence). Re-runnable.

Usage:  python -u scripts/recover_impressum.py --workers 6
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect, OLLAMA_HOST
from scripts.gf_extract import extract_gf, has_gf_keyword, looks_like_person
from scripts.reextract_gf import (regex_is_high_conf, ollama_extract, clean_llm,
                                   window_around_gf)

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "de-DE,de;q=0.9"}
PATHS = ["/impressum", "/impressum/", "/impressum.html", "/impressum.php",
         "/de/impressum", "/kontakt/impressum", "/service/impressum",
         "/footer-menue-deutsch/service/impressum.html", "/rechtliches/impressum",
         "/ueber-uns/impressum", "/index.php/impressum", "/impressum-datenschutz"]
_local = threading.local()


def sess():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session(); s.headers.update(HEADERS)
        _local.s = s
    return s


def get(url):
    """Robust GET: try normal, then verify=False on SSL error."""
    for verify in (True, False):
        try:
            r = sess().get(url, timeout=12, allow_redirects=True, verify=verify)
            if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
                return r.text, r.url
            return None, None
        except requests.exceptions.SSLError:
            continue
        except Exception:
            return None, None
    return None, None


def text_of(html):
    s = BeautifulSoup(html, "lxml")
    for t in s(["script", "style", "nav", "header", "footer"]):
        t.decompose()
    return s.get_text(" ", strip=True)


def imp_link(base, html):
    s = BeautifulSoup(html, "lxml")
    for a in s.find_all("a", href=True):
        if "impressum" in a["href"].lower() or "impressum" in (a.get_text() or "").lower():
            return urljoin(base, a["href"])
    return None


def recover(domain, web_api):
    parsed = urlparse(web_api or f"https://{domain}")
    host = parsed.netloc or domain
    candidates = [web_api] if web_api else []
    candidates += [f"https://www.{domain}", f"http://www.{domain}",
                   f"https://{domain}", f"http://{domain}"]
    homepage = final = None
    for c in candidates:
        if not c:
            continue
        html, fin = get(c)
        if html:
            homepage, final = html, fin
            break
    if not homepage:
        return domain, None, None, "none", "no_impressum_retry"

    # 1) link on resolved homepage
    imp = imp_link(final, homepage)
    txt = None
    if imp:
        ihtml, ifin = get(imp)
        if ihtml:
            txt, final = text_of(ihtml), ifin
    # 2) homepage itself is impressum
    if not txt and "impressum" in homepage.lower() and "geschäftsf" in homepage.lower():
        txt = text_of(homepage)
    # 3) brute paths on resolved host
    if not txt:
        root = f"{urlparse(final).scheme}://{urlparse(final).netloc}"
        for p in PATHS:
            ihtml, ifin = get(root + p)
            if ihtml and "impressum" in ihtml.lower():
                txt = text_of(ihtml)
                final = ifin
                break
    if not txt:
        return domain, None, None, "none", "no_impressum_retry"

    gf, method = extract_gf(txt)
    if not (gf and regex_is_high_conf(gf)) and has_gf_keyword(txt):
        llm = clean_llm(ollama_extract(window_around_gf(txt)), txt)
        if llm:
            gf, method = llm, "ollama"
    if gf and not all(looks_like_person(p.strip()) for p in gf.split(",")):
        gf = None
    status = "ok" if gf else ("no_gf_in_text" if has_gf_keyword(txt) else "no_gf_keyword")
    return domain, gf, txt[:8000], (method if gf else "none"), status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    conn = db_connect(); cur = conn.cursor()
    # representative web_api per domain
    cur.execute("""SELECT DISTINCT ON (lower(regexp_replace(split_part(split_part(web_api,'/',3),':',1),'^www\\.','')))
                          web_api FROM pflegeheime
                   WHERE web_api IS NOT NULL AND web_api<>''""")
    # simpler: build map in python
    cur.execute("SELECT web_api FROM pflegeheime WHERE web_api IS NOT NULL AND web_api<>''")
    dom_url = {}
    for (u,) in cur.fetchall():
        try:
            d = urlparse(u).netloc.lower().replace("www.", "")
        except Exception:
            continue
        if d and (d not in dom_url or len(urlparse(u).path) > len(urlparse(dom_url[d]).path)):
            dom_url[d] = u
    cur.execute("SELECT domain FROM domain_impressum WHERE crawl_status IN ('no_impressum','no_impressum_retry')")
    todo = [d for (d,) in cur.fetchall()]
    print(f"Recovery für {len(todo)} no_impressum-Domains (workers={args.workers})", flush=True)

    UP = ("UPDATE domain_impressum SET geschaeftsfuehrung=%s, impressum_text=COALESCE(%s,impressum_text), "
          "method=%s, grounded=%s, crawl_status=%s WHERE domain=%s")
    lock = threading.Lock(); stats = {}; done = 0; t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(recover, d, dom_url.get(d)): d for d in todo}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                domain, gf, txt, method, status = fut.result()
            except Exception as exc:  # noqa: BLE001
                domain, gf, txt, method, status = d, None, None, "none", "no_impressum_retry"
            with lock:
                cur.execute(UP, (gf, txt, method, bool(gf), status, domain))
                conn.commit()
                done += 1
                stats[status] = stats.get(status, 0) + 1
                if status == "ok":
                    print(f"  ✓ {domain:32} [{method}] {gf[:50]}", flush=True)
                if done % 50 == 0 or done == len(todo):
                    print(f"  --- {done}/{len(todo)} ({done/max(time.time()-t0,1e-6):.1f}/s) {stats} ---", flush=True)
    conn.close()
    print(f"\nFertig. {stats}", flush=True)


if __name__ == "__main__":
    main()
