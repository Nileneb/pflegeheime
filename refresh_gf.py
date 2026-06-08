"""Re-extract Geschäftsführung + Einrichtungsleitung from each Heim's Impressum.

Impressum-first:
  1. Use the row's `impressum_url` if present; otherwise try the website's
     /impressum (and a few common variants), or follow an <a> link whose
     visible text contains 'Impressum'.
  2. Parse the page text for German legal-disclosure keywords:
       Geschäftsführer, Vorstand, Vertretungsberechtigt(e), Vertreten durch,
       Heimleitung, Einrichtungsleitung, Hausleitung, Pflegedienstleitung
  3. Pick the cleanest names; skip placeholders ('Sehr geehrte', 'Name folgt' …).

LLM-fallback (qwen2.5:7b-instruct or mistral:7b-instruct) only when the
Impressum yielded nothing AND the row already has a website that returned 200.

This script does NOT touch any other columns; only writes:
  geschaeftsfuehrung_clean, einrichtungsleitung_clean,
  clean_notes (appended), gf_source (= 'impressum'|'llm'|'none')

Run incrementally — safe to re-run; rows already with gf_source='impressum'
are skipped.
"""

from __future__ import annotations

import os
import re
import json
import time
import requests
from data_cleaner import db_connect
from urllib.parse import urlparse, urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

ALTER_SQL = "ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS gf_source TEXT;"

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = "mistral:7b-instruct"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (PflegeheimDataCleaner/0.2)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en;q=0.7",
}

IMPRESSUM_PATHS = ("/impressum", "/impressum/", "/impressum.html",
                   "/impressum.php", "/de/impressum", "/kontakt/impressum",
                   "/ueber-uns/impressum", "/legal/impressum")

# Regex patterns to extract GF/EL block. Tolerant to <strong>, line breaks, etc.
GF_LABEL_RE = re.compile(
    r"(?:Geschäftsführ(?:er|ung|erin)|"
    r"Vertretungsberechtigt(?:e[rn]?)?|Vertreten\s+durch|"
    r"Inhaber(?:in)?|Vorstand|Stiftungsvorstand|Heimträger)"
    r"\s*[:\-]?\s*",
    re.IGNORECASE,
)
EL_LABEL_RE = re.compile(
    r"(?:Einrichtungsleit(?:er|ung|erin)|Heimleit(?:er|ung|erin)|"
    r"Hausleit(?:er|ung|erin)|Pflegedienstleit(?:er|ung|erin)|PDL)"
    r"\s*[:\-]?\s*",
    re.IGNORECASE,
)

# Reject obviously-junk capture results
BAD_CAPTURE_RE = re.compile(
    r"sehr\s+geehrt|name\s+folgt|wird\s+(noch\s+)?(bekannt|ergänzt|"
    r"angegeben|veröffentlicht)|n\.?n\.?$|noch\s+nicht\s+bekannt",
    re.IGNORECASE,
)

# Plausible German name pattern: 'Vorname Nachname' or 'Dr. Vorname Nachname'
NAME_RE = re.compile(
    r"((?:Dr\.|Prof\.|Pfr\.|Pater|Schwester)?\s*"
    r"[A-ZÄÖÜ][a-zäöüß]+(?:[\-\s][A-ZÄÖÜ][a-zäöüß]+){1,4})"
)

# Reject candidates whose tokens hit street/address/section vocabulary.
# Last token gets trimmed if matches; if nothing usable remains, capture is junk.
STREET_SUFFIX = {
    "weg", "straße", "strasse", "str", "str.", "platz", "ring", "allee",
    "gasse", "ufer", "damm", "stieg", "pfad", "bogen", "hof", "markt",
}
SECTION_WORDS = {
    "telefon", "tel", "tel.", "fax", "e-mail", "email", "anschrift", "adresse",
    "sitz", "internet", "www", "homepage", "ust-id", "ust", "umsatzsteuer",
    "registergericht", "handelsregister", "amtsgericht", "hrb", "hra",
    "verantwortlich", "vertretungsberechtigt", "geschäftsführung",
    "geschäftsführer", "geschäftsführerin", "einrichtungsleitung",
    "heimleitung", "hausleitung", "pflegedienstleitung", "pdl",
    "vorstand", "stiftungsvorstand", "kuratorium", "aufsichtsrat",
    "kontakt", "impressum", "datenschutz", "agb", "haftung",
}

# Section markers that should TRUNCATE the search window (page layout boundary)
SECTION_BOUNDARY_RE = re.compile(
    r"\b(Telefon|Tel\.?|Fax|E-?Mail|Anschrift|Sitz|Internet|Homepage|"
    r"Registergericht|Handelsregister|USt-ID|Umsatzsteuer|"
    r"Verantwortlich|Datenschutz|Impressum|Kontakt|"
    r"Geschäftsführer|Geschäftsführung|Vorstand|"
    r"Einrichtungsleit|Heimleit|Hausleit|Pflegedienstleit)\b",
    re.IGNORECASE,
)


def discover_impressum(session: requests.Session, website: str,
                       impressum_url: str | None) -> str | None:
    """Return a usable impressum URL or None."""
    if impressum_url and impressum_url.startswith(("http://", "https://")):
        return impressum_url
    if not website:
        return None
    base = website if website.startswith(("http://", "https://")) else "https://" + website

    # 1) Try common path conventions
    for p in IMPRESSUM_PATHS:
        url = base.rstrip("/") + p
        try:
            r = session.head(url, allow_redirects=True, timeout=8)
            if r.status_code == 200 and "html" in r.headers.get("Content-Type", "").lower():
                return r.url
        except requests.RequestException:
            continue

    # 2) Fetch homepage and look for link containing 'impressum'
    try:
        r = session.get(base, timeout=10)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            txt = (a.get_text() or "").strip().lower()
            if "impressum" in txt or "impressum" in a["href"].lower():
                return urljoin(r.url, a["href"])
    except requests.RequestException:
        return None
    return None


def fetch_text(session: requests.Session, url: str) -> str:
    """Return the visible text of a page. Empty string on error."""
    try:
        r = session.get(url, timeout=12)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text("\n")
        # Collapse whitespace but keep line breaks
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text
    except requests.RequestException:
        return ""


def _clean_capture(s: str) -> str:
    """Trim and reject junk: street names, section labels, placeholders."""
    if not isinstance(s, str):
        return ""
    s = re.sub(r"\s+", " ", s).strip(" :,-—–")
    if not s or len(s) < 4 or len(s) > 120:
        return ""
    if BAD_CAPTURE_RE.search(s):
        return ""

    # Trim trailing tokens that are street/section vocabulary.
    tokens = s.split()
    while tokens and tokens[-1].lower().rstrip(".,") in STREET_SUFFIX | SECTION_WORDS:
        tokens.pop()
    s = " ".join(tokens).strip(" :,-—–")

    # Need at least 2 word tokens to look like a person.
    if len(tokens) < 2:
        return ""

    # If ANY token equals a street suffix in the middle (e.g. 'Müllerstraße Ecke')
    # it's a place, not a person.
    for t in tokens:
        if t.lower().rstrip(".,") in STREET_SUFFIX:
            return ""

    return s


def extract_after(text: str, label_re: re.Pattern) -> str | None:
    """Find label, return the next plausible person-name (max 200 chars window)."""
    for m in label_re.finditer(text):
        tail = text[m.end():m.end() + 200]
        tail = tail.split("\n\n", 1)[0]
        tail = re.sub(r"\s*\n\s*", " ", tail)

        # Truncate at the next section boundary so we don't pick up a name
        # that actually belongs to the *following* field.
        sec = SECTION_BOUNDARY_RE.search(tail)
        if sec and sec.start() > 2:   # leave at least the immediate name
            tail = tail[:sec.start()]

        nm = NAME_RE.search(tail)
        if nm:
            cleaned = _clean_capture(nm.group(1))
            if cleaned:
                return cleaned
    return None


def parse_impressum(text: str) -> tuple[str | None, str | None]:
    """Return (geschaeftsfuehrung, einrichtungsleitung) or Nones."""
    if not text:
        return None, None
    gf = extract_after(text, GF_LABEL_RE)
    el = extract_after(text, EL_LABEL_RE)
    return gf, el


# ── LLM fallback ───────────────────────────────────────────────────────────

LLM_SYSTEM = (
    "Du extrahierst Personen-Namen für Pflegeheime aus dem mitgegebenen Web-Text. "
    "Antworte AUSSCHLIESSLICH als JSON: "
    '{"geschaeftsfuehrung": "...", "einrichtungsleitung": "..."}. '
    "Wenn nichts klar erkennbar ist: 'No clear Data'. "
    "Keine Halluzinationen, keine Boilerplate-Texte."
)


def llm_extract(name: str, ort: str, web_text: str) -> tuple[str | None, str | None]:
    """Use LLM as last resort. Returns (gf, el) or (None, None) on failure."""
    if not web_text:
        return None, None
    payload = {
        "model": OLLAMA_MODEL,
        "format": "json",
        "stream": False,
        "keep_alive": "30m",
        "messages": [
            {"role": "system", "content": LLM_SYSTEM},
            {"role": "user",
             "content": f"Pflegeheim: {name} ({ort})\n\n=== Webtext ===\n{web_text[:3500]}"},
        ],
        "options": {"temperature": 0.1, "num_predict": 200},
    }
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=180)
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "") or ""
        data = json.loads(content)
        # Defensive: LLM occasionally returns a list of names → join.
        def _coerce(v):
            if v is None:
                return ""
            if isinstance(v, list):
                return ", ".join(str(x) for x in v if x)
            return str(v)
        gf = _coerce(data.get("geschaeftsfuehrung")).strip()
        el = _coerce(data.get("einrichtungsleitung")).strip()
        gf = gf if gf and gf.lower() not in {"no clear data", "n/a"} else None
        el = el if el and el.lower() not in {"no clear data", "n/a"} else None
        if gf:
            gf = _clean_capture(gf)
        if el:
            el = _clean_capture(el)
        return gf or None, el or None
    except (requests.RequestException, json.JSONDecodeError, ValueError):
        return None, None


# ── Worker / Main ─────────────────────────────────────────────────────────

def process_row(row: dict) -> dict:
    """Returns dict with api_id + new fields. Stateless, thread-safe."""
    out = {"api_id": row["api_id"], "gf": None, "el": None,
           "source": "none", "note": ""}
    sess = requests.Session()
    sess.headers.update(HEADERS)

    impressum_url = discover_impressum(sess, row.get("website") or "",
                                        row.get("impressum_url") or "")
    text = fetch_text(sess, impressum_url) if impressum_url else ""
    gf, el = parse_impressum(text)
    if gf or el:
        out["gf"], out["el"] = gf, el
        out["source"] = "impressum"
        out["note"] = f"GF/EL aus Impressum: {impressum_url}"
        return out

    # LLM fallback only if we got page text but couldn't parse,
    # OR if no impressum but the homepage returned text.
    if text:
        web_text = text
    elif row.get("website"):
        web_text = fetch_text(sess, row["website"])
    else:
        web_text = ""

    if web_text:
        gf, el = llm_extract(row["name"] or "", row["ort"] or "", web_text)
        if gf or el:
            out["gf"], out["el"] = gf, el
            out["source"] = "llm"
            out["note"] = "GF/EL via LLM-Fallback aus Webseite"

    return out


def main(workers: int = 6, limit: int | None = None) -> None:
    conn = db_connect()
    with conn.cursor() as cur:
        cur.execute(ALTER_SQL)
    conn.commit()

    cur = conn.cursor()
    cur.execute(
        """
        SELECT api_id, name, ort, website, impressum_url
        FROM pflegeheime
        WHERE cleaner IS NOT NULL
          AND (gf_source IS NULL OR gf_source = 'none')
          AND (website IS NOT NULL AND website <> '')
        ORDER BY api_id
        """
        + (f" LIMIT {limit}" if limit else "")
    )
    rows = cur.fetchall()
    print(f"refreshing GF/EL on {len(rows)} rows ({workers} workers)…\n")

    upd = conn.cursor()
    counts = {"impressum": 0, "llm": 0, "none": 0}
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_row, dict(r)): r for r in rows}
        for fut in as_completed(futures):
            try:
                res = fut.result()
            except Exception as exc:
                print(f"  worker err: {exc}")
                continue
            counts[res["source"]] += 1

            updates = ["gf_source = %s"]
            params: list[str] = [res["source"]]
            if res["gf"]:
                updates.append("geschaeftsfuehrung_clean = %s")
                params.append(res["gf"])
            if res["el"]:
                updates.append("einrichtungsleitung_clean = %s")
                params.append(res["el"])
            if res["note"]:
                updates.append("clean_notes = COALESCE(clean_notes,'') || %s")
                params.append(" | " + res["note"])
            params.append(res["api_id"])
            upd.execute(
                f"UPDATE pflegeheime SET {', '.join(updates)} WHERE api_id = %s",
                params,
            )
            completed += 1
            if completed % 50 == 0:
                conn.commit()
                print(f"  {completed}/{len(rows)} | impressum={counts['impressum']} "
                      f"llm={counts['llm']} none={counts['none']}")

    conn.commit()
    print(f"\nfinal: {counts}")

    upd.execute(
        """SELECT gf_source, COUNT(*) FROM pflegeheime
           WHERE cleaner IS NOT NULL GROUP BY gf_source ORDER BY COUNT(*) DESC"""
    )
    print("\ngf_source overall:")
    for src, n in upd.fetchall():
        print(f"  {src or '<null/no-website>':<14} {n}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    main(workers=args.workers, limit=args.limit)
