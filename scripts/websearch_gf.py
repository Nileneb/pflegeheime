#!/usr/bin/env python3
"""Phase 2d — Geschäftsführer für die Rest-Lücken via FREIE Websuche + Ollama.

Zielgruppe: Heime mit geschaeftsfuehrung_final='Nicht ermittelt' (kein/totes
Impressum, oder Impressum ohne GF). Pro Heim:

  1. DuckDuckGo-Suche  "{name} {ort} Impressum Geschäftsführer".
  2. Tier 1 (beste Quelle): erste nicht-Aggregator-Trefferseite → Impressum
     holen (reuse get_impressum). Domain schon in domain_impressum mit GF? →
     direkt übernehmen (cache). Regex/Ollama-Extraktion, grounded im Seitentext.
  3. Tier 2 (Fallback): die Such-SNIPPETS selbst an Ollama (think=false) — viele
     Aggregator-Snippets nennen den GF/Inhaber wörtlich. Grounded im Snippet.

Jeder Treffer bekommt Quelle-URL + method (websearch-impressum | websearch-cache
| websearch-snippet). Harter Grounding-Check + looks_like_person → keine
Halluzination. Resumable über Tabelle websearch_gf. Sanftes Rate-Limit (global
min. Abstand zwischen DDG-Calls) + Backoff.

Usage:
  python -u scripts/websearch_gf.py --workers 3 --limit 15   # Test
  python -u scripts/websearch_gf.py --workers 3              # alle Lücken
"""
import argparse
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# ddgs/httpx loggen jede Backend-Anfrage auf INFO — das übertönt unseren Fortschritt.
logging.getLogger().setLevel(logging.WARNING)
for noisy in ("ddgs", "httpx", "httpcore", "urllib3", "primp"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect
from scripts.gf_extract import extract_gf, has_gf_keyword, looks_like_person
from scripts.reextract_gf import regex_is_high_conf, clean_llm, OLLAMA_MODEL
from scripts.crawl_impressum_gf import get_impressum, guess_traeger
import requests

from ddgs import DDGS

from data_cleaner import OLLAMA_HOST

AGG = (
    # Verzeichnisse / Aggregatoren
    "wohnen-im-alter", "seniorenportal", "pflege.de", "pflegehilfe",
    "pflegemarktplatz", "gelbeseiten", "11880", "dasoertliche", "meinestadt",
    "yelp", "kliniken.de", "weisse-liste", "pflegenoten", "pflegeleichter",
    "heimverzeichnis", "altenheim-magazin", "wohnen-im-alter",
    # Social / Suchmaschinen / Wikis
    "facebook", "instagram", "linkedin", "youtube", "twitter", "x.com", "tiktok",
    "wikipedia", "wikiwand", "grokipedia", "google", "bing", "yandex", "yahoo",
    "mojeek", "brave.com", "duckduckgo",
    # Wirtschaftsauskunft
    "northdata", "companyhouse", "firmenwissen", "unternehmensregister",
    "unternehmensauskunft", "wer-zu-wem", "moneyhouse", "dnb.com", "deutschebiz",
    "handelsregister", "bundesanzeiger", "online-handelsregister",
    # Presse / Medien (deren Impressum nennt die GF der ZEITUNG, nicht des Heims!)
    "faz.net", "wa.de", "rp-online", "ksta.de", "express.de", "ruhrnachrichten",
    "wp.de", "derwesten", "wn.de", "lokalkompass", "wochenblatt", "zeitung",
    "anzeiger", "nachrichten", "tagesschau", "spiegel", "focus.de", "welt.de",
    "bild.de", "t-online", "web.de", "gmx.", "stern.de", "ntv.de", "wdr.de",
    "radio", "lokalplus", "come-on.de", "ikz-online", "soester-anzeiger",
    # Beratung / Verbände / Behörden (kein Heim-Betreiber)
    "biva.de", "verbraucherzentrale", "bgw-online", "vdab.de", "bpa.de",
    "unser-quartier", ".bund.de", ".nrw.de", "kreis-", "stadt-", ".gov",
    "diakonie.de", "caritas.de",  # Dachverbände (Konzern-GF, nicht Heim)
    # Branchenbücher / Firmenauskunft / Jobs / weitere Presse / Literatur
    "creditreform", "oeffnungszeitenbuch", "firmeneintrag", "branchenbuch",
    "cylex", "kennstdueinen", "gewerbe", "jobs.", "job.", "karriere.", "stepstone",
    "indeed", "meinestadt", "stellenanzeigen", "kimeta",
    "nw.de", "nrz.de", "waz.de", "rga.de", "lz.de", "hellweger", "come-on",
    "schriftstellen", "abmahnung", "literatur", "autoren", "dichter", "lyrik",
)

# Heim-Betreiber-Signalwörter: kommt KEINES davon in Domain oder Quelltext vor,
# ist die Quelle wahrscheinlich keine Pflegeeinrichtung → GF verwerfen.
CARE_WORDS = ("pflege", "senioren", "alten", "heim", "diakon", "caritas", "hospital",
              "gesundheit", "betreu", "sozial", "malteser", "johanniter", "stift",
              "residenz", "wohnen", "awo", "drk", "asb", "cellitinnen", "vivat",
              "kloster", "orden", "hospiz", "domizil", "quartier", "lebenshilfe",
              "behindert", "teilhabe", "haus", "zentrum", "gGmbH".lower(), "wohlfahrt")


def care_related(*texts):
    blob = " ".join(t for t in texts if t).lower()
    return any(w in blob for w in CARE_WORDS)

_rl_lock = threading.Lock()
_last_search = [0.0]
MIN_GAP = 1.2  # Sekunden zwischen DDG-Calls (global)


def ddg(query, max_results=7):
    for attempt in range(4):
        with _rl_lock:
            wait = MIN_GAP - (time.time() - _last_search[0])
            if wait > 0:
                time.sleep(wait)
            _last_search[0] = time.time()
        try:
            with DDGS() as d:
                return list(d.text(query, max_results=max_results, region="de-de"))
        except Exception:
            time.sleep(2.0 * (attempt + 1))
    return []


def is_agg(url):
    net = (urlparse(url).netloc or "").lower()
    return any(a in net for a in AGG)


# generische Namensbestandteile, die KEINE distinktiven Tokens sind
_GENERIC = {"haus", "hauses", "senioren", "seniorenzentrum", "seniorenheim", "altenheim",
            "seniorenresidenz", "seniorenstift", "pflege", "pflegeheim", "pflegezentrum",
            "altenzentrum", "altenpflegeheim", "wohnstift", "residenz", "zentrum", "stift",
            "alten", "altenhilfe", "wohnpark", "wohnen", "betreuung", "diakonie", "caritas",
            "evangelisch", "katholisch", "gmbh", "ggmbh", "und", "der", "die", "das", "für",
            "am", "an", "im", "in", "haus", "sankt", "sanktes", "gemeinnützige"}


def name_tokens(name):
    return [t for t in re.split(r"[^a-zäöüß0-9]+", (name or "").lower())
            if len(t) >= 4 and t not in _GENERIC]


def relevant(text, name, ort):
    """True wenn der Quelltext das Heim plausibel betrifft: Ort ODER ein
    distinktives Namens-Token kommt vor. Verhindert, dass z.B. das faz.net-
    Impressum als GF eines Pflegeheims übernommen wird."""
    if not text:
        return False
    tl = text.lower()
    if ort and len(ort) >= 3 and ort.lower() in tl:
        return True
    return any(tok in tl for tok in name_tokens(name))


def regdom(u):
    try:
        return urlparse(u).netloc.lower().replace("www.", "")
    except Exception:
        return None


SYS_SNIPPET = ("Aus den folgenden Web-Suchergebnissen zu EINEM Pflegeheim: nenne die "
               "Geschäftsführer / Vorstände / Inhaber des Betreibers, NUR wenn ein "
               "Personenname WÖRTLICH in den Ergebnissen steht. Keine Orte, Firmen, "
               'Rollen, Einrichtungsleitung-nur-wenn-nichts-anderes. '
               'JSON: {"gf":"Vorname Nachname, ..."} oder {"gf":""}. Erfinde nichts.')


def ollama(system, content):
    payload = {"model": OLLAMA_MODEL, "format": "json", "stream": False, "think": False,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": content[:2500]}],
               "options": {"temperature": 0.0, "num_ctx": 3072, "num_predict": 120}}
    import json as _j
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=120)
        r.raise_for_status()
        return (_j.loads(r.json().get("message", {}).get("content", "") or "").get("gf") or "").strip()
    except Exception:
        return ""


def window(text):
    m = re.search(r".{0,80}(gesch[aä]ftsf[uü]hr|vertretungsberechtigt|vertreten\s+durch|vorstand|inhaber)",
                  text, re.I)
    return text[m.start():m.start() + 800] if m else text[:1200]


# domain_impressum cache (domain -> grounded GF), loaded once
_DOM_GF = {}


def from_cache(domain):
    return _DOM_GF.get(domain)


def process(row):
    api_id, name, ort, kreis, web_api = row
    results = ddg(f"{name} {ort} Impressum Geschäftsführer")
    if not results:
        results = ddg(f"{name} {ort} Träger Geschäftsführer")
    snippets = "\n".join(f"[{r.get('title','')}] {r.get('href','')}\n{r.get('body','')}"
                         for r in results)

    # --- Tier 1: fetch best operator Impressum ---
    tried = set()
    for r in results:
        href = r.get("href") or ""
        dom = regdom(href)
        if not dom or dom in tried or is_agg(href):
            continue
        tried.add(dom)
        cached = from_cache(dom)
        if cached:
            return api_id, cached, f"https://{dom}", "websearch-cache", "ok", None
        imp_url, text = get_impressum(href)
        # Doppel-Gate: (1) Quelle betrifft das Heim (Ort/Name-Token), UND (2) die
        # Quelle ist überhaupt ein Pflege-/Sozial-Betreiber (care_related) — sonst
        # greifen wir die GF einer Zeitung/Stadt/Firmenauskunft ab.
        if text and relevant(text, name, ort) and care_related(dom, text):
            gf, _ = extract_gf(text)
            if gf and regex_is_high_conf(gf):
                return api_id, gf, (imp_url or href), "websearch-impressum", "ok", text[:4000]
            if has_gf_keyword(text):
                llm = clean_llm(ollama(SYS_SNIPPET, "IMPRESSUM:\n" + window(text)), text)
                if llm:
                    return api_id, llm, (imp_url or href), "websearch-impressum", "ok", text[:4000]
        if len(tried) >= 3:
            break

    # --- Tier 2: snippets (nur wenn Ort UND ein Care-Wort im Snippet vorkommen) ---
    if snippets.strip() and (not ort or ort.lower() in snippets.lower()) and care_related(snippets):
        llm = ollama(SYS_SNIPPET, f"Einrichtung: {name}, {ort}\n\nSUCHERGEBNISSE:\n{snippets}")
        llm = clean_llm(llm, snippets)
        if llm:
            src = next((r.get("href") for r in results if not is_agg(r.get("href") or "")), "websearch")
            return api_id, llm, src, "websearch-snippet", "ok", snippets[:4000]
    return api_id, None, None, "none", "not_found", None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS websearch_gf (
                     api_id TEXT PRIMARY KEY, gf TEXT, source_url TEXT,
                     method TEXT, status TEXT, quelltext TEXT, done_at TIMESTAMPTZ DEFAULT NOW())""")
    cur.execute("ALTER TABLE websearch_gf ADD COLUMN IF NOT EXISTS quelltext TEXT")
    conn.commit()

    # load domain cache
    cur.execute("SELECT domain, geschaeftsfuehrung FROM domain_impressum WHERE geschaeftsfuehrung IS NOT NULL")
    for d, g in cur.fetchall():
        _DOM_GF[d] = g

    cur.execute("""SELECT p.api_id, p.name, p.ort, p.kreis, p.web_api
                   FROM pflegeheime p
                   LEFT JOIN websearch_gf w ON w.api_id = p.api_id
                   WHERE p.geschaeftsfuehrung_final = 'Nicht ermittelt'
                     AND w.api_id IS NULL
                   ORDER BY p.api_id""")
    todo = cur.fetchall()
    if args.limit:
        todo = todo[:args.limit]
    print(f"Web-Suche für {len(todo)} Heime ohne GF (workers={args.workers})", flush=True)

    UP = ("INSERT INTO websearch_gf (api_id,gf,source_url,method,status,quelltext,done_at) "
          "VALUES (%s,%s,%s,%s,%s,%s,NOW()) ON CONFLICT (api_id) DO UPDATE SET "
          "gf=EXCLUDED.gf, source_url=EXCLUDED.source_url, method=EXCLUDED.method, "
          "status=EXCLUDED.status, quelltext=EXCLUDED.quelltext, done_at=NOW()")
    lock = threading.Lock()
    stats = {}
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, r): r[0] for r in todo}
        for fut in as_completed(futs):
            try:
                api_id, gf, src, method, status, qtext = fut.result()
            except Exception as exc:  # noqa: BLE001
                api_id, gf, src, method, status, qtext = futs[fut], None, None, "none", f"err:{str(exc)[:30]}", None
            with lock:
                cur.execute(UP, (api_id, gf, src, method, status, qtext))
                conn.commit()
                done += 1
                stats[method] = stats.get(method, 0) + 1
                if gf:
                    print(f"  ✓ [{method}] {gf[:48]:48} <- {(src or '')[:40]}", flush=True)
                if done % 25 == 0 or done == len(todo):
                    rate = done / max(time.time() - t0, 1e-6)
                    print(f"  --- {done}/{len(todo)} ({rate:.2f}/s) {stats} ---", flush=True)
    conn.close()
    print(f"\nFertig. {stats}", flush=True)


if __name__ == "__main__":
    main()
