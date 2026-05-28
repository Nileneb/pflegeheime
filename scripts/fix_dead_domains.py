#!/usr/bin/env python3
"""Triagiere + repariere tote Website-URLs (domain_social.status=fetch_failed).

Ein totes web_api bedeutet: kein Impressum gecrawlt, kein Social, oft ein
Tippfehler in der NRW-Quelle (ww.korian.de, johaneswerk.de, domicil-grupp.ede).
Wir erzeugen Kandidaten-URLs (lowercase, IDN-Encoding für Umlaute, ww.→www.,
www-→'', wwww→www, Pfad→Root, plus manuelle Typo-Map) und prüfen, welche
WIRKLICH lädt. Nur verifizierte (HTTP 200, HTML) URLs werden übernommen.

Dry-run (Default) zeigt nur die Triage. Mit --apply wird web_api korrigiert
(Original nach web_api_original gesichert).

  python -u scripts/fix_dead_domains.py
  python -u scripts/fix_dead_domains.py --apply
"""
import argparse
import os
import re
import sys
import time
from urllib.parse import urlparse

import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "de-DE,de;q=0.9",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
TIMEOUT = 20

# regdom (netloc ohne www.) -> korrekte Domain. Nur belegte Tippfehler.
MANUAL = {
    "domicil-grupp.ede": "domicil-gruppe.de",
    "johaneswerk.de": "johanneswerk.de",
    "johanneswerk": "johanneswerk.de",
    "lamberrtus-hueckelhoven.de": "lambertus-hueckelhoven.de",
    "pflegehem-fabianek-gmbh.de": "pflegeheim-fabianek-gmbh.de",
    "stellavitails.de": "stellavitalis.de",
    "ww.korian.de": "korian.de",
    "www-doreafamilie-koeln.de": "doreafamilie-koeln.de",
    "www-drk-seniorenhaus-steinbach.de": "drk-seniorenhaus-steinbach.de",
    "wseniorenstift-anderhaard.de": "seniorenstift-anderhaard.de",
    "drk-diesseldorf.de": "drk-duesseldorf.de",
}


def idna_host(host):
    try:
        return host.encode("idna").decode("ascii")
    except Exception:
        return host


def candidates(regdom, stored_url):
    p = urlparse(stored_url)
    host = (p.netloc or "").lower()
    bare = host[4:] if host.startswith("www.") else host
    cands = []

    def add(h):
        h = h.strip().strip(".")
        if not h:
            return
        for variant in (h, "www." + h if not h.startswith("www.") else h[4:]):
            cands.append(variant)
            ih = idna_host(variant)
            if ih != variant:
                cands.append(ih)

    if regdom in MANUAL:
        add(MANUAL[regdom])
    add(bare)
    # mechanische Korrekturen
    if host.startswith("wwww."):
        add(host[5:])
    if host.startswith("ww.") and not host.startswith("www."):
        add(host[3:])
    if bare.startswith("www-"):
        add(bare[4:])
    # dedupe, Reihenfolge erhalten
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c); out.append(c)
    return out


def verify(host):
    """Return working root URL (str) or None."""
    for scheme in ("https", "http"):
        url = f"{scheme}://{host}/"
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT,
                             allow_redirects=True, verify=False)
            if r.status_code == 200 and "html" in r.headers.get("content-type", "html").lower():
                return r.url
        except Exception:
            continue
    return None


def main():
    import urllib3
    urllib3.disable_warnings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = db_connect(); cur = conn.cursor()
    cur.execute("SELECT domain FROM domain_social WHERE status='fetch_failed' OR status LIKE 'error%'")
    dead = sorted(r[0] for r in cur.fetchall())

    cur.execute("SELECT api_id, web_api FROM pflegeheime WHERE web_api<>''")
    rows = cur.fetchall()
    by_dom = {}
    for aid, u in rows:
        d = urlparse(u).netloc.lower().replace("www.", "")
        by_dom.setdefault(d, []).append((aid, u))

    if args.apply:
        cur.execute("ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS web_api_original TEXT")
        conn.commit()

    fixed, dead_final = [], []
    for d in dead:
        sample = by_dom.get(d, [(None, f"https://{d}")])[0][1]
        working = None
        for host in candidates(d, sample):
            working = verify(host)
            if working:
                break
        if working:
            fixed.append((d, working))
            print(f"  FIX  {d:36} -> {working}", flush=True)
        else:
            dead_final.append(d)
            print(f"  DEAD {d:36} (kein Kandidat erreichbar)", flush=True)
        time.sleep(0.2)

    print(f"\nfixbar: {len(fixed)} | wirklich tot: {len(dead_final)}")

    if args.apply and fixed:
        n = 0
        for d, working in fixed:
            for aid, old in by_dom.get(d, []):
                cur.execute("""UPDATE pflegeheime
                               SET web_api_original = COALESCE(web_api_original, web_api),
                                   web_api = %s
                               WHERE api_id = %s""", (working, aid))
                n += 1
        conn.commit()
        print(f"web_api korrigiert für {n} Heime ({len(fixed)} Domains).")
    conn.close()


if __name__ == "__main__":
    main()
