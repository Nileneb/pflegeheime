#!/usr/bin/env python3
"""Phase 2b — Re-extract Geschäftsführer from CACHED Impressum text.

Works offline on domain_impressum.impressum_text (no re-crawl). Strategy:

  1. Regex (gf_extract) → candidate names, grounded by construction.
  2. Confidence:
       HIGH  = regex hit, every part a clean person name, AND (a title present
               OR ≥2 comma-separated names) → trust regex, no LLM.
       LOW   = regex empty-but-keyword-present, OR single untitled name, OR any
               borderline part → ask local Ollama on a small GF window.
  3. Ollama output is ground-checked (tokens must appear in text) and re-filtered
     through looks_like_person → no hallucinated or noise names survive.

Re-runnable. Updates geschaeftsfuehrung/method/grounded/crawl_status in place.

Usage:
  python -u scripts/reextract_gf.py --ollama lowconf --workers 2   # default
  python -u scripts/reextract_gf.py --ollama none                  # regex only
  python -u scripts/reextract_gf.py --ollama all                   # validate everything
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect, OLLAMA_HOST
from scripts.gf_extract import extract_gf, has_gf_keyword, looks_like_person

OLLAMA_MODEL = os.getenv("GF_MODEL", "qwen3.5:9b")
TITLE_HINT = re.compile(r"\b(dr|prof|dipl|pfarrer|pastor|diakon|schwester|pater)\b", re.I)


def regex_is_high_conf(gf: str) -> bool:
    if not gf:
        return False
    parts = [p.strip() for p in gf.split(",") if p.strip()]
    if not all(looks_like_person(p) for p in parts):
        return False
    return TITLE_HINT.search(gf) is not None or len(parts) >= 2


SYSTEM = ("Du bekommst einen Ausschnitt aus einem deutschen Impressum. Gib die "
          "Namen der GESCHÄFTSFÜHRER / VORSTÄNDE / INHABER zurück, die WÖRTLICH im "
          "Text stehen — nur echte Personennamen, keine Firmen, Orte, Abteilungen "
          'oder Rollen. JSON: {"gf":"Vorname Nachname, Vorname Nachname"} oder '
          '{"gf":""} wenn keine Person genannt ist. Erfinde nichts.')


def ollama_extract(window: str):
    payload = {"model": OLLAMA_MODEL, "format": "json", "stream": False,
               "think": False,  # qwen3.5 is a reasoning model — without this it
                                 # burns the token budget on hidden <think> and
                                 # returns empty content (done_reason=length).
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": window[:1800]}],
               "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 200}}
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=120)
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "") or ""
        return (json.loads(content).get("gf") or "").strip()
    except Exception:
        return ""


def normalize(s):
    return re.sub(r"[^a-zäöüß ]", " ", (s or "").lower())


def grounded(gf, text):
    nt = normalize(text)
    toks = [t for t in normalize(gf).split() if len(t) >= 4]
    return bool(toks) and sum(1 for t in toks if t in nt) >= (len(toks) + 1) // 2


def clean_llm(gf, text):
    """Keep only grounded person-name parts from an LLM answer."""
    parts = [p.strip() for p in re.split(r"[,;/]| und ", gf) if p.strip()]
    out = []
    for p in parts:
        if looks_like_person(p) and grounded(p, text):
            out.append(p)
    return ", ".join(out[:8])


def window_around_gf(text):
    m = re.search(r".{0,80}(gesch[aä]ftsf[uü]hr|vertretungsberechtigt|vertreten\s+durch|vorstand|inhaber)",
                  text, re.I)
    return text[m.start():m.start() + 700] if m else text[:700]


def process(row, mode):
    domain, text, cur_gf = row
    if not text:
        return domain, None, "none", "no_impressum"
    gf_regex, _ = extract_gf(text)
    high = regex_is_high_conf(gf_regex)

    use_llm = (mode == "all") or (mode == "lowconf" and not high)
    if gf_regex and high:
        return domain, gf_regex, "regex", "ok"

    if use_llm and has_gf_keyword(text):
        llm = ollama_extract(window_around_gf(text))
        llm = clean_llm(llm, text)
        if llm:
            return domain, llm, "ollama", "ok"

    # fall back to (low-conf) regex if it at least looks like a person
    if gf_regex and all(looks_like_person(p.strip()) for p in gf_regex.split(",")):
        return domain, gf_regex, "regex-lowconf", "ok"
    if has_gf_keyword(text):
        return domain, None, "none", "no_gf_in_text"
    return domain, None, "none", "no_gf_keyword"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ollama", choices=["none", "lowconf", "all"], default="lowconf")
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT domain, impressum_text, geschaeftsfuehrung FROM domain_impressum")
    rows = cur.fetchall()
    print(f"Domains: {len(rows)} | ollama-mode={args.ollama} | workers={args.workers}", flush=True)

    lock = threading.Lock()
    stats = {}
    meth = {}
    done = 0
    t0 = time.time()
    UP = ("UPDATE domain_impressum SET geschaeftsfuehrung=%s, method=%s, grounded=%s, "
          "crawl_status=%s WHERE domain=%s")

    def work(r):
        return process(r, args.ollama)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, r) for r in rows]
        for fut in as_completed(futs):
            domain, gf, method, status = fut.result()
            with lock:
                cur.execute(UP, (gf, method, bool(gf), status, domain))
                conn.commit()
                done += 1
                stats[status] = stats.get(status, 0) + 1
                meth[method] = meth.get(method, 0) + 1
                if done % 100 == 0 or done == len(rows):
                    rate = done / max(time.time() - t0, 1e-6)
                    print(f"  {done}/{len(rows)} ({rate:.1f}/s) status={stats} method={meth}", flush=True)
    conn.close()
    print(f"\nFertig. status={stats}\nmethoden={meth}", flush=True)


if __name__ == "__main__":
    main()
