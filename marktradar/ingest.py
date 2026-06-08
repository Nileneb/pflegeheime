"""RSS-Ingest: holen, parsen, diffen, embedden, klassifizieren (SQLite-Pfad)."""
import json
import re
from datetime import datetime, timezone

import feedparser
import requests

from marktradar import db, embeddings
from sqlite_vec import serialize_float32

OLLAMA_HOST = embeddings.OLLAMA_HOST
RELEVANCE_MODEL = "qwen3.5:9b"
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
    payload = {"model": RELEVANCE_MODEL, "format": "json", "stream": False, "think": False,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": f"Titel: {title}\nText: {summary}"[:1500]}],
               "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 120}}
    r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=90)
    r.raise_for_status()
    d = json.loads(r.json().get("message", {}).get("content", "") or "{}")
    return bool(d.get("relevant")), (d.get("kategorie") or "")[:60], (d.get("grund") or "")[:200]
