"""Hashtag-Beobachtung à la Taggbox: aggregiert echte Posts/Treffer je Hashtag
aus öffentlichen Quellen (Mastodon, Bluesky, Google-News-RSS), geokodiert sie grob
über die Autor-/Outlet-Location und liefert Geo-Punkte für den 3D-Globus.

Jeder Punkt hängt an einer ECHTEN, klickbaren Quell-URL ("immer über die Quelle").
Hashtags sind CRUD-fähig (Tabelle `hashtags`), Posts liegen in `hashtag_posts`.
"""
import html as _html
import json
import logging
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from marktradar import languages as _langs, stupid

_log = logging.getLogger(__name__)

UA = "pflege-marktradar/1.0 (+https://pflege.linn.games)"
TIMEOUT = 12

# WHY: env-override lets CI/tests set 0 to skip rate-limit staggering entirely.
_FETCH_DELAY = float(os.getenv("PFLEGE_FETCH_DELAY", "0.3"))

# Hashtag-Seed aus dem bisherigen DISCOURSE_TERMS-Set, mit festen Farben.
SEED = [
    ("Pflegereform", "#5b8def"), ("Pflegenotstand", "#ff4d4d"), ("Tariftreue", "#2ecc71"),
    ("Pflegekräfte", "#f0a830"), ("Personalmangel", "#9b6dff"), ("Pflegekrise", "#ff7043"),
    ("Pflegeversicherung", "#26c6da"), ("Altenpflege", "#67d98b"), ("Pflegepolitik", "#c792ea"),
    ("Insolvenz", "#e84393"),
]

# Kompakter Gazetteer: Großstädte (DE) + Länder → (lat, lon). Substring-Match auf
# der Autor-/Outlet-Location. Bewusst klein gehalten (offline, kein Geocoding-API).
GAZ = {
    "berlin": (52.52, 13.405), "hamburg": (53.55, 9.99), "münchen": (48.137, 11.575),
    "munich": (48.137, 11.575), "köln": (50.938, 6.96), "cologne": (50.938, 6.96),
    "frankfurt": (50.11, 8.682), "stuttgart": (48.775, 9.182), "düsseldorf": (51.227, 6.773),
    "dortmund": (51.514, 7.466), "essen": (51.456, 7.012), "leipzig": (51.34, 12.375),
    "bremen": (53.079, 8.802), "dresden": (51.05, 13.738), "hannover": (52.376, 9.718),
    "nürnberg": (49.452, 11.077), "duisburg": (51.434, 6.762), "bochum": (51.481, 7.216),
    "wuppertal": (51.256, 7.15), "bonn": (50.737, 7.098), "münster": (51.96, 7.626),
    "karlsruhe": (49.007, 8.404), "mannheim": (49.488, 8.466), "augsburg": (48.37, 10.898),
    "wiesbaden": (50.082, 8.24), "kiel": (54.323, 10.122), "mainz": (49.992, 8.247),
    "freiburg": (47.999, 7.842), "aachen": (50.776, 6.084), "remscheid": (51.178, 7.19),
    "wülfrath": (51.28, 7.04), "nrw": (51.43, 7.55), "bayern": (48.79, 11.5),
    "bavaria": (48.79, 11.5), "sachsen": (51.0, 13.3), "hessen": (50.6, 9.0),
    "niedersachsen": (52.8, 9.1), "baden-württemberg": (48.6, 9.0),
    "deutschland": (51.16, 10.45), "germany": (51.16, 10.45), "österreich": (47.6, 14.1),
    "austria": (47.6, 14.1), "wien": (48.21, 16.37), "vienna": (48.21, 16.37),
    "schweiz": (46.8, 8.2), "switzerland": (46.8, 8.2), "zürich": (47.37, 8.54),
    "uk": (51.5, -0.12), "london": (51.5, -0.12), "usa": (38.9, -77.04),
    "new york": (40.71, -74.0), "france": (48.85, 2.35), "paris": (48.85, 2.35),
    "netherlands": (52.37, 4.9), "amsterdam": (52.37, 4.9), "spain": (40.42, -3.7),
    "italy": (41.9, 12.5), "europe": (50.1, 9.0), "eu": (50.85, 4.35),
    # global (für nicht-DE Mastodon/Bluesky-Autoren — Weltkugel statt nur DE)
    "rome": (41.9, 12.5), "madrid": (40.42, -3.7), "barcelona": (41.39, 2.17),
    "lisbon": (38.72, -9.14), "portugal": (39.4, -8.2), "brussels": (50.85, 4.35),
    "belgium": (50.5, 4.5), "dublin": (53.35, -6.26), "ireland": (53.4, -8.0),
    "copenhagen": (55.68, 12.57), "denmark": (56.0, 10.0), "stockholm": (59.33, 18.06),
    "sweden": (60.0, 18.0), "oslo": (59.91, 10.75), "norway": (60.5, 8.5),
    "helsinki": (60.17, 24.94), "finland": (62.0, 26.0), "warsaw": (52.23, 21.0),
    "poland": (52.0, 19.0), "prague": (50.08, 14.44), "budapest": (47.5, 19.04),
    "athens": (37.98, 23.73), "greece": (39.0, 22.0), "istanbul": (41.0, 28.98),
    "turkey": (39.0, 35.0), "moscow": (55.75, 37.62), "tokyo": (35.68, 139.69),
    "japan": (36.2, 138.25), "beijing": (39.9, 116.4), "china": (35.0, 104.0),
    "delhi": (28.61, 77.21), "india": (22.0, 79.0), "sydney": (-33.87, 151.21),
    "australia": (-25.0, 133.0), "toronto": (43.65, -79.38), "canada": (56.0, -106.0),
    "los angeles": (34.05, -118.24), "san francisco": (37.77, -122.42),
    "chicago": (41.88, -87.63), "washington": (38.9, -77.04), "boston": (42.36, -71.06),
    "brazil": (-14.2, -51.9), "são paulo": (-23.55, -46.63), "mexico": (23.6, -102.5),
    "south africa": (-30.0, 25.0), "egypt": (26.8, 30.8), "nigeria": (9.1, 8.7),
    "singapore": (1.35, 103.82), "dubai": (25.2, 55.27), "uae": (24.0, 54.0),
}
DE_CENTER = tuple(_langs.by_code("de")["centroid"])  # WHY: unified constant — avoids divergence with languages.LANGUAGES["de"]["centroid"]


def _get(url, as_json=True, headers=None):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return json.loads(raw) if as_json else raw


def _strip_html(s):
    return re.sub(r"<[^>]+>", " ", _html.unescape(s or "")).strip()


def geocode(text):
    """Grobe Geokodierung: erstes Gazetteer-Match im Freitext → (lat, lon) | None."""
    if not text:
        return None
    low = text.lower()
    for key, ll in GAZ.items():
        if key in low:
            return ll
    return None


def _jitter(seed_str, base, spread=1.5):
    """Kleiner pseudo-zufälliger Versatz (±`spread`°) um `base`, damit gleiche
    Koordinaten nicht exakt überlappen. `spread` steuert die Streuung: eng (≈0.6)
    für exakt verortete Institutionen, breit (CENTROID_SPREAD) für den groben
    Sprach-Centroid-Fallback — sonst kollabieren alle Fallback-Posts einer Sprache
    auf einen Punkt (= die Zentrierung im Globus)."""
    h = abs(hash(seed_str))
    dlat = ((h % 1000) / 1000 - 0.5) * 2 * spread
    dlon = (((h // 1000) % 1000) / 1000 - 0.5) * 2 * spread
    return (base[0] + dlat, base[1] + dlon)


# WHY: Sprach-Centroid-Fallback breit streuen (Land-groß statt Punkt) → entzerrt
# den Globus. env-justierbar; 0 = exakt auf den Centroid (alte Enge).
CENTROID_SPREAD = float(os.getenv("PFLEGE_CENTROID_SPREAD", "6.0"))

# Land-Zentroide (ISO-3166 alpha-2). WHY: Posts tragen `country` (aus NEWS_REGION),
# aber bisher landeten sie am SPRACH-Centroid (en→London, pt→Lissabon) → US/BR-Posts
# klumpten auf Europa. Country VOR Sprache verorten füllt Amerika/Afrika korrekt.
COUNTRY_CENTROIDS = {
    "DE": (51.16, 10.45), "AT": (47.6, 14.1), "CH": (46.8, 8.2), "GB": (54.0, -2.0),
    "US": (39.5, -98.35), "CA": (56.0, -106.0), "BR": (-10.0, -52.0), "MX": (23.6, -102.5),
    "AR": (-38.0, -63.0), "ES": (40.0, -3.7), "FR": (46.2, 2.2), "IT": (42.5, 12.5),
    "PT": (39.5, -8.0), "NL": (52.2, 5.3), "BE": (50.5, 4.5), "PL": (52.0, 19.0),
    "RU": (61.5, 105.0), "UA": (48.4, 31.2), "TR": (39.0, 35.0), "SA": (24.0, 45.0),
    "EG": (26.8, 30.8), "ZA": (-29.0, 24.0), "NG": (9.1, 8.7), "KE": (-0.0, 37.9),
    "ET": (9.1, 40.5), "JP": (36.2, 138.25), "CN": (35.0, 104.0), "IN": (22.0, 79.0),
    "ID": (-2.5, 118.0), "AU": (-25.0, 133.0), "SE": (62.0, 15.0), "NO": (62.0, 10.0),
    "DK": (56.0, 10.0), "FI": (64.0, 26.0), "GR": (39.0, 22.0), "CZ": (49.8, 15.5),
    "CO": (4.6, -74.1), "SN": (14.5, -14.5), "CI": (7.5, -5.5), "CD": (-2.9, 23.6),
    "AO": (-11.2, 17.9), "MZ": (-18.7, 35.5), "MA": (31.8, -7.1),
}


def _place_post(post, lang_centroid, country=None):
    """Verortet einen Post → (lat, lon). Reihenfolge (genau→grob): Institution mit
    bekanntem Sitz → Gazetteer-Treffer (Stadt) → Land-Centroid (`country`) → Sprach-
    Centroid. Seed = URL (oder id), damit ein Post stabil am selben Ort bleibt."""
    from marktradar import ranking
    seed = post.get("url") or str(post.get("id") or post.get("location_text") or "")
    inst = ranking.match_institution(
        f"{post.get('author', '')} {post.get('location_text', '')} {post.get('url', '')}")
    if inst:
        return _jitter(seed, (inst["lat"], inst["lon"]), spread=0.6)
    ll = geocode(post.get("location_text"))
    if ll:
        return _jitter(seed, ll, spread=1.5)
    cc = COUNTRY_CENTROIDS.get((country or post.get("country") or "").upper())
    if cc:
        return _jitter(seed, cc, spread=4.0)  # Land-groß, aber enger als der Sprach-Fallback
    return _jitter(seed, lang_centroid, spread=CENTROID_SPREAD)


def regeocode(conn, only_missing=False):
    """Rechnet lat/lon bestehender Posts mit der aktuellen Placement-Logik neu
    (Institution-Sitz → Gazetteer → Land-Centroid → Sprach-Centroid) → verteilt den
    vorhandenen Globus sofort korrekt (US/BR ans echte Land), ohne neu zu fetchen."""
    where = " WHERE lat IS NULL" if only_missing else ""
    rows = conn.execute(
        f"SELECT id, url, author, location_text, lang_code, country FROM hashtag_posts{where}").fetchall()
    n = 0
    for r in rows:
        entry = _langs.by_code(r["lang_code"] or "de")
        centroid = tuple(entry["centroid"]) if entry else DE_CENTER
        lat, lon = _place_post(
            {"id": r["id"], "url": r["url"], "author": r["author"],
             "location_text": r["location_text"]}, centroid, country=r["country"])
        conn.execute("UPDATE hashtag_posts SET lat=?, lon=? WHERE id=?", (lat, lon, r["id"]))
        n += 1
    conn.commit()
    return {"regeocoded": n}


# ── Fetcher: liefern Liste {source, url, author, content, location_text, published} ──
def fetch_mastodon(term, limit=20, instance="mastodon.social"):
    from marktradar import credentials
    cred = credentials.get("mastodon")
    headers = None
    if cred:
        instance = cred["instance"]
        headers = {"Authorization": f"Bearer {cred['token']}"}
    tag = urllib.parse.quote(term.lstrip("#"))
    data = _get(f"https://{instance}/api/v1/timelines/tag/{tag}?limit={limit}", headers=headers)
    out = []
    for s in data or []:
        acc = s.get("account") or {}
        loc = ""
        for f in acc.get("fields") or []:
            if re.search(r"(location|ort|stadt|standort|city|wohnort)", (f.get("name") or ""), re.I):
                loc = _strip_html(f.get("value"))
        out.append({
            "source": "mastodon", "url": s.get("url"),
            "author": acc.get("display_name") or acc.get("acct"),
            "content": _strip_html(s.get("content"))[:280],
            "location_text": loc or _strip_html(acc.get("note"))[:60],
            "published": s.get("created_at"),
        })
    return out


def fetch_bluesky(term, limit=20, lang=None):
    from marktradar import credentials
    jwt = credentials.bluesky_session()
    headers = {"Authorization": f"Bearer {jwt}"} if jwt else None
    host = "bsky.social" if jwt else "api.bsky.app"
    q = urllib.parse.quote("#" + term.lstrip("#"))
    url = f"https://{host}/xrpc/app.bsky.feed.searchPosts?q={q}&limit={limit}"
    if lang:
        url += f"&lang={urllib.parse.quote(lang)}"
    data = _get(url, headers=headers)
    out = []
    for p in (data or {}).get("posts", []):
        au = p.get("author") or {}
        rec = p.get("record") or {}
        handle = au.get("handle", "")
        rkey = (p.get("uri") or "").rsplit("/", 1)[-1]
        post_url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else None
        out.append({
            "source": "bluesky", "url": post_url,
            "author": au.get("displayName") or handle,
            "content": (rec.get("text") or "")[:280],
            "location_text": (au.get("description") or "")[:60],
            "published": rec.get("createdAt"),
        })
    return out


def fetch_news(term, limit=20, hl="de", gl="DE", ceid="DE:de"):
    q = urllib.parse.quote(f'"{term.lstrip("#")}"')
    url = f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"
    raw = _get(url, as_json=False)
    root = ET.fromstring(raw)
    out = []
    for item in list(root.iterfind(".//item"))[:limit]:
        src_el = item.find("source")
        out.append({
            "source": "news",
            "url": (item.findtext("link") or "").strip(),
            "author": (src_el.text if src_el is not None else "") or "News",
            "content": _strip_html(item.findtext("title"))[:280],
            "location_text": (src_el.text if src_el is not None else "") or "",
            "published": (item.findtext("pubDate") or "").strip(),
        })
    return out



# ── CRUD ──
def seed(conn):
    if conn.execute("SELECT 1 FROM hashtags LIMIT 1").fetchone():
        return 0
    now = datetime.now(timezone.utc).isoformat()
    for term, color in SEED:
        conn.execute("INSERT OR IGNORE INTO hashtags(term,color,active,created) VALUES (?,?,1,?)",
                     (term, color, now))
    conn.commit()
    return len(SEED)


def list_hashtags(conn):
    rows = conn.execute(
        "SELECT h.id,h.term,h.color,h.active,"
        " (SELECT count(*) FROM hashtag_posts p WHERE p.hashtag_id=h.id) posts,"
        " (SELECT count(*) FROM hashtag_posts p WHERE p.hashtag_id=h.id AND p.lat IS NOT NULL) geo "
        "FROM hashtags h ORDER BY posts DESC, h.term").fetchall()
    return [dict(r) for r in rows]


def add(conn, term, color="#5b8def"):
    term = term.lstrip("#").strip()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute("INSERT OR IGNORE INTO hashtags(term,color,active,created) VALUES (?,?,1,?)",
                       (term, color, now))
    conn.commit()
    row = conn.execute("SELECT id,term,color,active FROM hashtags WHERE term=?", (term,)).fetchone()
    return dict(row) if row else {"id": cur.lastrowid, "term": term, "color": color}


def update(conn, hashtag_id, term=None, color=None, active=None):
    sets, vals = [], []
    if term is not None:
        sets.append("term=?"); vals.append(term.lstrip("#").strip())
    if color is not None:
        sets.append("color=?"); vals.append(color)
    if active is not None:
        sets.append("active=?"); vals.append(1 if active else 0)
    if not sets:
        return {"updated": 0}
    vals.append(hashtag_id)
    conn.execute(f"UPDATE hashtags SET {','.join(sets)} WHERE id=?", vals)
    conn.commit()
    r = conn.execute("SELECT id,term,color,active FROM hashtags WHERE id=?", (hashtag_id,)).fetchone()
    return dict(r) if r else {"updated": 0}


def delete(conn, hashtag_id):
    conn.execute("DELETE FROM hashtag_posts WHERE hashtag_id=?", (hashtag_id,))
    conn.execute("DELETE FROM article_hashtags WHERE hashtag_id=?", (hashtag_id,))
    conn.execute("DELETE FROM hashtag_translations WHERE hashtag_id=?", (hashtag_id,))
    conn.execute("DELETE FROM hashtags WHERE id=?", (hashtag_id,))
    conn.commit()
    return {"deleted": hashtag_id}


_PALETTE = ["#5b8def", "#2ecc71", "#f0a830", "#9b6dff", "#26c6da", "#ff7043",
            "#67d98b", "#c792ea", "#e84393", "#1abc9c", "#ff4d4d"]


def _autocolor(term):
    return _PALETTE[abs(hash(term)) % len(_PALETTE)]


_EXTRACT_SYS = (
    "Du extrahierst aus einer deutschen Pflege-/Gesundheits-/Sozial-Meldung max. 2 prägnante "
    "Themen-Schlagworte (je EIN Wort, deutsch, ohne #, z.B. Pflegereform, Personalmangel, "
    "Heimaufsicht, Tariftreue). Nur Sachthemen — KEINE Eigennamen, Orte, Personen, Firmen. "
    "Wenn nichts Pflege-/Sozialrelevantes: leeres Array. Antworte NUR als JSON-Array von "
    'Strings, z.B. ["Pflegereform","Tariftreue"].')


_JSON_ARR = re.compile(r"\[.*?\]", re.S)


def _parse_terms(raw):
    """Robust: zieht ein JSON-Array aus content ODER thinking (gpt-oss schreibt die
    Antwort in den thinking-Channel, wenn content leer bleibt)."""
    if not raw:
        return []
    try:
        out = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        m = _JSON_ARR.search(raw)  # JSON-Array irgendwo im Freitext (reasoning-Channel)
        if not m:
            return []
        try:
            out = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(out, dict):
        out = out.get("hashtags") or out.get("themen") or list(out.values())
    return [str(x).lstrip("#").strip() for x in (out or [])
            if isinstance(x, str) and 2 < len(str(x)) < 30][:2]


def _extract_hashtags(title, summary):
    """LLM extrahiert 1-2 Themen-Hashtags aus einer Meldung → Liste[str].
    WHY: gpt-oss:20b (ollama.com) ist ein Reasoning-Modell — mit knappem num_predict
    landet die Antwort im thinking-Channel statt in content; daher beide auswerten
    und num_predict großzügig, damit nach dem Reasoning noch content folgt."""
    from marktradar import embeddings
    try:
        msg = embeddings.chat_raw(
            _EXTRACT_SYS, f"Titel: {title}\nText: {summary or ''}"[:1200],
            temperature=0.1, num_predict=400)
        content = msg.get("content")
        # WHY: ein nicht-leeres content ist autoritativ — auch "[]" (= bewusst keine
        # Themen). Nur wenn der content-Channel KOMPLETT leer ist (Reasoning-Modell
        # ohne Budget), auf thinking ausweichen; sonst fischt der Regex das im
        # thinking zitierte System-Prompt-Beispiel und erfindet falsche Hashtags.
        if content and content.strip():
            return _parse_terms(content)
        return _parse_terms(msg.get("thinking"))
    except Exception:
        return []


def debug_extract(conn, n=3):
    """Diagnose: zeigt für die ersten n relevanten, ungetaggten Artikel die rohe
    LLM-Antwort (content + thinking) und die geparsten Terme — KEIN Error-Swallow."""
    from marktradar import embeddings
    rows = conn.execute(
        "SELECT id,title,summary,relevant FROM articles "
        "WHERE id NOT IN (SELECT article_id FROM article_hashtags) "
        "ORDER BY relevant DESC, published DESC LIMIT ?", (n,)).fetchall()
    out = {"host": embeddings.CHAT_HOST, "model": embeddings.CHAT_MODEL, "items": []}
    for a in rows:
        item = {"id": a["id"], "title": a["title"], "relevant": a["relevant"]}
        try:
            msg = embeddings.chat_raw(
                _EXTRACT_SYS, f"Titel: {a['title']}\nText: {a['summary'] or ''}"[:1200],
                temperature=0.1, num_predict=400)
            item["status"] = 200  # chat_raw wirft bei non-2xx
            content = msg.get("content")
            item["content"] = (content or "")[:400]
            item["thinking"] = (msg.get("thinking") or "")[:400]
            item["terms"] = (_parse_terms(content) if content and content.strip()
                             else _parse_terms(msg.get("thinking")))
        except Exception as e:
            item["error"] = f"{type(e).__name__}: {e}"
        out["items"].append(item)
    return out


def tag_articles(conn, article_ids=None, auto_create=False, limit=400):
    """Verknüpft Artikel ↔ Hashtags (article_hashtags). Matcht aktive Hashtags (Wortgrenze);
    mit auto_create bekommen relevante Artikel OHNE Treffer ein neues Hashtag per LLM
    („Event wird Hashtag"). Gibt {linked, created, articles}."""
    tags = [dict(r) for r in conn.execute("SELECT id,term FROM hashtags WHERE active=1").fetchall()]
    pats = [(t["id"], re.compile(r"\b" + re.escape(t["term"]) + r"\w*", re.I)) for t in tags]
    if article_ids is None:
        # relevante zuerst (auto_create lohnt nur dort), dann neueste
        rows = conn.execute(
            "SELECT id,title,summary,relevant FROM articles "
            "WHERE id NOT IN (SELECT article_id FROM article_hashtags) "
            "ORDER BY relevant DESC, published DESC LIMIT ?", (limit,)).fetchall()
    else:
        if not article_ids:
            return {"linked": 0, "created": 0, "articles": 0}
        ph = ",".join("?" * len(article_ids))
        rows = conn.execute(
            f"SELECT id,title,summary,relevant FROM articles WHERE id IN ({ph})",
            list(article_ids)).fetchall()
    linked = created = 0
    for a in rows:
        text = f"{a['title'] or ''} {a['summary'] or ''}"
        hits = [hid for (hid, p) in pats if p.search(text)]
        if not hits and auto_create and a["relevant"]:
            for term in _extract_hashtags(a["title"], a["summary"]):
                existed = conn.execute("SELECT id FROM hashtags WHERE term=?", (term,)).fetchone()
                row = add(conn, term, _autocolor(term))
                if row.get("id"):
                    if not existed:
                        created += 1
                        pats.append((row["id"], re.compile(r"\b" + re.escape(term) + r"\w*", re.I)))
                    hits.append(row["id"])
        for hid in hits:
            cur = conn.execute(
                "INSERT OR IGNORE INTO article_hashtags(article_id,hashtag_id) VALUES (?,?)",
                (a["id"], hid))
            if cur.rowcount:
                linked += 1
    conn.commit()
    return {"linked": linked, "created": created, "articles": len(rows)}


# ── Refresh: je aktivem Hashtag aus allen Quellen holen, geokodieren, upserten ──
def refresh(conn, sources=("mastodon", "bluesky", "news"), limit=20, only_id=None):
    now = datetime.now(timezone.utc).isoformat()
    if only_id is not None:
        tags = conn.execute("SELECT id,term FROM hashtags WHERE id=?", (only_id,)).fetchall()
    else:
        tags = conn.execute("SELECT id,term FROM hashtags WHERE active=1").fetchall()
    added, errors, per_source = 0, [], {s: 0 for s in sources}
    for ht in tags:
        tr = {}
        try:
            tr = ensure_translations(conn, ht["id"], ht["term"], _langs.FETCH_LANGS)
        except Exception as e:  # WHY: translation failure must not abort fetching
            errors.append(f"translations:{ht['term']}: {type(e).__name__}: {e}")

        # Always include the German source term; append translated terms for other langs.
        lang_terms = [("de", ht["term"])] + [
            (code, tr[code]) for code in _langs.FETCH_LANGS if code in tr
        ]

        for lang, term in lang_terms:
            primary = _langs.NEWS_REGION.get(lang, "DE")
            # News deckt mehrere Länder je Sprache ab (Amerika/Afrika); Bluesky/Mastodon
            # liefern keinen Länder-Parameter → einmal mit der Primär-Region.
            news_regions = _langs.NEWS_REGIONS.get(lang, [primary])
            lang_entry = _langs.by_code(lang)
            lang_centroid = tuple(lang_entry["centroid"]) if lang_entry else DE_CENTER
            for src in sources:
                regions = news_regions if src == "news" else [primary]
                for gl in regions:
                    try:
                        if src == "news":
                            posts = fetch_news(term, limit, hl=lang, gl=gl, ceid=f"{gl}:{lang}")
                        elif src == "bluesky":
                            posts = fetch_bluesky(term, limit, lang=lang)
                        else:
                            # WHY: mastodon kennt kein Land → country = Primär-Region (Näherung).
                            posts = fetch_mastodon(term, limit)
                    except Exception as e:  # WHY: eine Quelle/Sprache/Region down → andere weiter
                        errors.append(f"{src}:{lang}:{gl}:{ht['term']}: {type(e).__name__}: {e}")
                        continue

                    for p in posts:
                        if not p.get("url"):
                            continue
                        # Institution → Gazetteer → Land-Centroid (gl) → Sprach-Centroid.
                        lat, lon = _place_post(p, lang_centroid, country=gl)
                        # WHY: UNIQUE(hashtag_id,url) + INSERT OR IGNORE dedupt URL-Dubletten
                        # über Sprachen/Regionen — der erste Treffer behält seine Attribution.
                        cur = conn.execute(
                            "INSERT OR IGNORE INTO hashtag_posts"
                            "(hashtag_id,source,url,author,content,"
                            "location_text,lat,lon,published,fetched_at,lang_code,country)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (ht["id"], p["source"], p["url"], p.get("author"), p.get("content"),
                             p.get("location_text"), lat, lon, p.get("published"), now,
                             lang, gl))
                        if cur.rowcount:
                            added += 1
                            per_source[src] = per_source.get(src, 0) + 1

            # WHY: fire once per language (not per source) — ~13× more langs than before;
            # staggering per-source would add ~3× unnecessary sleep (~21 min for 109 hashtags).
            if _FETCH_DELAY:
                time.sleep(_FETCH_DELAY)

    conn.commit()
    return {"added": added, "per_source": per_source, "tags": len(tags), "errors": errors}


# ── Trends: News gegen Hashtags matchen → Häufigkeit + Ko-Vorkommen → Gewicht ──
def trends(conn):
    """Jeder News-Artikel = Event; welche aktiven Hashtags drin vorkommen wird gezählt,
    und gemeinsames Vorkommen zweier Hashtags (Kombination) erhöht das Gewicht. Anzahl +
    Kombination bestimmen die Trend-Stärke (→ Pulsieren)."""
    tags = [dict(r) for r in conn.execute(
        "SELECT id,term,color FROM hashtags WHERE active=1").fetchall()]
    pats = [(t, re.compile(r"\b" + re.escape(t["term"]) + r"\w*", re.I)) for t in tags]
    counts = {t["id"]: 0 for t in tags}
    pairs = {}
    for a in conn.execute("SELECT title,summary FROM articles ORDER BY published DESC LIMIT 2500").fetchall():
        text = f"{a['title'] or ''} {a['summary'] or ''}"
        hit = [t["id"] for (t, p) in pats if p.search(text)]
        for hid in hit:
            counts[hid] += 1
        for i in range(len(hit)):
            for j in range(i + 1, len(hit)):
                k = (hit[i], hit[j]) if hit[i] < hit[j] else (hit[j], hit[i])
                pairs[k] = pairs.get(k, 0) + 1
    cooc = {t["id"]: 0 for t in tags}
    for (a, b), n in pairs.items():
        cooc[a] += n
        cooc[b] += n
    tm = {t["id"]: t for t in tags}
    weights = {tid: round(counts[tid] + 0.6 * cooc[tid], 1) for tid in counts}
    top = sorted(pairs.items(), key=lambda x: -x[1])[:14]
    return {
        "weights": {tid: {"term": tm[tid]["term"], "color": tm[tid]["color"],
                          "count": counts[tid], "cooc": cooc[tid], "weight": weights[tid]}
                    for tid in counts},
        "pairs": [{"a": tm[a]["term"], "b": tm[b]["term"], "ca": tm[a]["color"],
                   "cb": tm[b]["color"], "n": n} for (a, b), n in top if n > 0],
    }


# ── Cross-language connection net: arcs spanning regions for the globe ──
CONNECTIONS_CAP = 200  # total arcs returned by connections(); sorted by n desc, rest dropped
_CLUSTER_PAIR_MIN = 2  # co-occurrence count for a `pair` to seed a 2-hashtag cluster
_MAX_CLUSTERS = 40     # cap distinct clusters considered (strongest pairs first)
_MAX_REGIONS_PER = 8   # cap regions per concept / cluster → bounds arcs per item


def _region_key(post):
    """Region bucket for a geolocated post: country, else lang_code, else a coarse
    10°-geo-bucket. Stable string key so the same place groups together."""
    if post["country"]:
        return f"c:{post['country']}"
    if post["lang_code"]:
        return f"l:{post['lang_code']}"
    # Coarse fallback so posts with neither tag still cluster by rough location.
    return f"g:{int(post['lat'] // 10)},{int(post['lon'] // 10)}"


def _centroid(latlons):
    """Antimeridian-safe centroid via unit-vector mean (mirrors the frontend's
    approach so server arcs land on the same point the client draws)."""
    n = len(latlons)
    x = y = z = 0.0
    for lat, lon in latlons:
        rlat, rlon = math.radians(lat), math.radians(lon)
        cl = math.cos(rlat)
        x += cl * math.cos(rlon)
        y += cl * math.sin(rlon)
        z += math.sin(rlat)
    x /= n; y /= n; z /= n
    if math.hypot(x, y, z) < 1e-9:  # WHY: near-antipodal points leave float residuals ~4e-17, == 0.0 never triggers
        return (sum(p[0] for p in latlons) / n, sum(p[1] for p in latlons) / n)
    lon = math.degrees(math.atan2(y, x))
    lat = math.degrees(math.atan2(z, math.hypot(x, y)))
    return (lat, lon)


def _blend_colors(hexes):
    """Average a list of #rrggbb colors → a single #rrggbb (cluster tint)."""
    rgb = [0, 0, 0]
    valid = [h for h in hexes if isinstance(h, str) and len(h) == 7 and h.startswith("#")]
    if not valid:
        return "#9aa6c0"
    for h in valid:
        for i in range(3):
            rgb[i] += int(h[1 + 2 * i:3 + 2 * i], 16)
    rgb = [round(c / len(valid)) for c in rgb]
    return "#%02x%02x%02x" % tuple(rgb)


def _regions_for(conn, hashtag_ids):
    """Group geolocated posts of the given hashtag(s) by region → {region: {centroid, n}}.
    hashtag_ids: iterable of ids whose posts jointly define the regions (a single id for a
    concept, a cluster's members for a cluster)."""
    ids = list(hashtag_ids)
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT lat, lon, lang_code, country FROM hashtag_posts "
        f"WHERE hashtag_id IN ({placeholders}) AND lat IS NOT NULL AND lon IS NOT NULL",
        ids,
    ).fetchall()
    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(_region_key(dict(r)), []).append((r["lat"], r["lon"]))
    return {k: {"centroid": _centroid(v), "n": len(v)} for k, v in buckets.items()}


def connections(conn, cap=CONNECTIONS_CAP, trends_result=None):
    """Explicit-endpoint arcs for the globe net.

    kind="concept": one hashtag present in ≥2 regions → hub-and-spoke from its densest
    region to every other region (bounded, O(R) not O(R²)). color = hashtag color.

    kind="cluster": a set of co-occurring hashtags (strong `trends` pairs as 2-clusters)
    present in ≥2 regions → arcs from the cluster's densest region to the others.
    color = blend of the cluster members' colors.

    trends_result: optional precomputed result of trends(conn). When provided, the
    internal trends() call is skipped (avoids a redundant ~200-300ms scan in map_data).

    Returns {"arcs": [...], "truncated": <dropped>}. Arcs are capped (sorted by n desc).
    Empty/small output is honest: if data lives in one region there is simply no net.
    """
    tags = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id,term,color FROM hashtags WHERE active=1").fetchall()}
    arcs = []

    # ── Part 1: concept-across-regions ──
    for tid, t in tags.items():
        regions = _regions_for(conn, [tid])
        if len(regions) < 2:
            continue  # same concept must appear in ≥2 places to be a cross-region arc
        items = sorted(regions.items(), key=lambda kv: -kv[1]["n"])[:_MAX_REGIONS_PER]
        _, hub = items[0]
        for _key, reg in items[1:]:
            arcs.append({
                "a_lat": hub["centroid"][0], "a_lon": hub["centroid"][1],
                "b_lat": reg["centroid"][0], "b_lon": reg["centroid"][1],
                "color": t["color"], "n": min(hub["n"], reg["n"]), "kind": "concept",
            })

    # ── Part 2: cluster-set-matching ──
    # WHY: simplest defensible cluster def — each strong co-occurrence `pair` from trends()
    # is a 2-hashtag cluster. Avoids fragile component-merging when the co-occurrence
    # signal is sparse (only German articles feed it today).
    tr = trends_result if trends_result is not None else trends(conn)
    term_to_id = {t["term"]: tid for tid, t in tags.items()}
    seen_clusters = set()
    pair_signals = sorted(tr["pairs"], key=lambda p: -p["n"])
    for pair in pair_signals[:_MAX_CLUSTERS]:
        if pair["n"] < _CLUSTER_PAIR_MIN:
            continue
        a_id, b_id = term_to_id.get(pair["a"]), term_to_id.get(pair["b"])
        if a_id is None or b_id is None:
            continue
        members = tuple(sorted((a_id, b_id)))
        if members in seen_clusters:
            continue
        seen_clusters.add(members)
        regions = _regions_for(conn, members)
        if len(regions) < 2:
            continue  # the cluster must be co-present in ≥2 regions to span the globe
        tint = _blend_colors([tags[a_id]["color"], tags[b_id]["color"]])
        items = sorted(regions.items(), key=lambda kv: -kv[1]["n"])[:_MAX_REGIONS_PER]
        _, hub = items[0]
        for _key, reg in items[1:]:
            arcs.append({
                "a_lat": hub["centroid"][0], "a_lon": hub["centroid"][1],
                "b_lat": reg["centroid"][0], "b_lon": reg["centroid"][1],
                "color": tint, "n": min(hub["n"], reg["n"]), "kind": "cluster",
            })

    arcs.sort(key=lambda a: -a["n"])
    dropped = max(0, len(arcs) - cap)
    if dropped:
        _log.info("connections: %d arcs over cap %d → dropped %d", len(arcs), cap, dropped)
    return {"arcs": arcs[:cap], "truncated": dropped}


# ── Translation service ──

_TRANSLATE_SYS = (
    "You are a professional translator. Translate the given German word or short phrase "
    "into ALL target languages listed in the JSON input. Return ONLY a strict JSON object "
    "mapping each language code to the translated term. Use the same casing style as the "
    "input (e.g., capitalise if input is capitalised). No explanations, no extra keys."
)

_JSON_OBJ = re.compile(r"\{[^{}]*\}", re.S)


def _parse_translation_json(raw: str) -> dict[str, str]:
    """Extract {lang_code: term} from LLM response, tolerating surrounding text."""
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return {str(k): str(v) for k, v in obj.items() if v}
    except (json.JSONDecodeError, ValueError):
        pass
    m = _JSON_OBJ.search(raw)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return {str(k): str(v) for k, v in obj.items() if v}
    except (json.JSONDecodeError, ValueError):
        return {}


def translate_hashtag(term: str, target_langs: list[str]) -> dict[str, str]:
    """Batch-translate a German hashtag term into all target_langs via Ollama chat.

    Unknown lang codes (not in languages.LANGUAGES) are silently skipped before the
    request is built — callers may pass user-supplied codes without risk of a TypeError.
    Returns {lang_code: translated_term}. On any error returns {} and logs a warning
    — never silently swallows, never returns partial garbage from an exception path.
    """
    from marktradar import embeddings
    # WHY: user-supplied codes may not exist in the language table; filter them early
    # to avoid a TypeError on _langs.by_code(code)["name"] for unknown codes.
    known_langs = [code for code in target_langs if _langs.by_code(code) is not None]
    if not known_langs:
        return {}
    payload = {"term": term, "languages": {code: _langs.by_code(code)["name"] for code in known_langs}}
    try:
        msg = embeddings.chat_raw(
            _TRANSLATE_SYS, json.dumps(payload, ensure_ascii=False),
            temperature=0.1, num_ctx=4096, num_predict=2048, timeout=120)
        parsed = _parse_translation_json(msg.get("content") or "")
        # Keep only codes that were actually requested and have non-empty values
        return {k: v for k, v in parsed.items() if k in known_langs and v and v.strip()}
    except Exception as exc:
        _log.warning("translate_hashtag(%r) failed: %s: %s", term, type(exc).__name__, exc)
        return {}


def ensure_translations(conn, hashtag_id: int, term: str,
                        target_langs: list[str] | None = None) -> dict[str, str]:
    """Cache-first translation: reads existing rows, translates only missing langs, persists.

    Unknown lang codes (not in languages.LANGUAGES) are filtered out early so callers
    can pass user-supplied code lists without risk of a TypeError downstream.
    Returns the full {lang_code: term} dict for all requested langs that are available.
    Idempotent: safe to call repeatedly.
    """
    if target_langs is None:
        target_langs = _langs.CODES

    # WHY: user-supplied codes may include unknowns; filter before any DB/LLM access
    target_langs = [code for code in target_langs if _langs.by_code(code) is not None]
    if not target_langs:
        return {}

    existing = {
        r["lang_code"]: r["term"]
        for r in conn.execute(
            "SELECT lang_code, term FROM hashtag_translations WHERE hashtag_id=?",
            (hashtag_id,),
        ).fetchall()
    }
    missing = [code for code in target_langs if code not in existing]
    if missing:
        new_translations = translate_hashtag(term, missing)
        if new_translations:
            conn.executemany(
                "INSERT OR REPLACE INTO hashtag_translations(hashtag_id, lang_code, term) "
                "VALUES (?, ?, ?)",
                [(hashtag_id, code, translated) for code, translated in new_translations.items()],
            )
            conn.commit()
            existing.update(new_translations)

    return {code: existing[code] for code in target_langs if code in existing}


# ── Map-Daten für den Globus ──
_MAP_POINTS_DEFAULT = int(os.getenv("PFLEGE_MAP_POINTS", "25000"))


def map_data(conn, max_points=None):
    if max_points is None:
        max_points = _MAP_POINTS_DEFAULT
    tags = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id,term,color,active FROM hashtags").fetchall()}
    counts = {}
    for r in conn.execute(
            "SELECT hashtag_id, count(*) n, sum(CASE WHEN lat IS NOT NULL THEN 1 ELSE 0 END) geo "
            "FROM hashtag_posts GROUP BY hashtag_id").fetchall():
        counts[r["hashtag_id"]] = (r["n"], r["geo"])
    tr = trends(conn)
    W = tr["weights"]
    maxw = max([w["weight"] for w in W.values()] + [1.0])
    legend = []
    for tid, t in tags.items():
        n, geo = counts.get(tid, (0, 0))
        w = W.get(tid, {})
        legend.append({**t, "count": n, "geo": geo, "news": w.get("count", 0),
                       "cooc": w.get("cooc", 0), "weight": w.get("weight", 0)})
    legend.sort(key=lambda x: -(x["weight"] + x["count"]))
    from marktradar import ranking
    # WHY: Punkt-Payload schlank halten — der Globus liest pro Punkt nur
    # lat/lon/color/term/url/weight/published. content/author/score_post sind hier toter
    # Ballast (score_post = 25k Gazetteer-Lookups). Die reiche QC-Liste unten bleibt davon
    # unberührt. Slim-Payload macht 25k Punkte erst tragbar (~2-3× kleineres JSON).
    rows = conn.execute(
        "SELECT p.hashtag_id,p.url,p.lat,p.lon,p.published,p.author "
        "FROM hashtag_posts p WHERE p.lat IS NOT NULL "
        "ORDER BY p.published DESC LIMIT ?", (max_points,)).fetchall()
    points = []
    for r in rows:
        # WHY(stupid-liste 2026-06-10): Quarantäne-Quellen tauchen auf der
        # Karte NIE auf (author nur fürs Gate selektiert, bleibt aus dem Payload).
        if stupid.is_stupid_post({"url": r["url"], "author": r["author"]}):
            continue
        t = tags.get(r["hashtag_id"], {})
        wn = (W.get(r["hashtag_id"], {}).get("weight", 0) / maxw) if maxw else 0
        points.append({
            "lat": r["lat"], "lon": r["lon"], "color": t.get("color", "#5b8def"),
            "term": t.get("term", "?"), "url": r["url"],
            "published": r["published"],
            "weight": round(wn, 3),  # 0..1 Trend-Stärke → Punktgröße
        })
    # QC-Quellenliste je Hashtag: lesbare + vertrauenswürdige Posts nach oben (Score),
    # damit der Leser oben anfangen kann — fremdsprachig/anonym bleibt erfasst, rutscht runter.
    by_tag: dict = {}
    for r in conn.execute(
            "SELECT hashtag_id,source,url,author,content,published,lang_code,location_text "
            "FROM hashtag_posts").fetchall():
        by_tag.setdefault(r["hashtag_id"], []).append(dict(r))
    sources = {}
    for tid, lst in by_tag.items():
        lst = [p for p in lst if not stupid.is_stupid_post(p)]
        ranked = ranking.rank_posts(lst)[:12]
        sources[tid] = [{
            "source": p["source"], "url": p["url"], "author": p["author"],
            "content": p["content"], "published": p["published"],
            "score": p["score"], "trust": p["trust"], "lang_tier": p["lang_tier"],
        } for p in ranked]

    # Build translations map keyed by canonical hashtag term (active only)
    translations: dict[str, dict[str, str]] = {}
    active_terms = {t["id"]: t["term"] for t in tags.values() if t.get("active")}
    if active_terms:
        rows_t = conn.execute(
            "SELECT ht.hashtag_id, ht.lang_code, ht.term "
            "FROM hashtag_translations ht "
            f"WHERE ht.hashtag_id IN ({','.join('?' * len(active_terms))})",
            list(active_terms.keys()),
        ).fetchall()
        for r in rows_t:
            canonical = active_terms.get(r["hashtag_id"])
            if canonical:
                translations.setdefault(canonical, {})[r["lang_code"]] = r["term"]

    from marktradar import entities
    watched = []
    for r in conn.execute(
            "SELECT wp.url, wp.author, wp.content, wp.lat, wp.lon, wp.published, "
            "e.name AS entity, e.type AS etype "
            "FROM watched_posts wp "
            "LEFT JOIN entities e ON e.id = wp.entity_id "
            "WHERE wp.lat IS NOT NULL "
            "ORDER BY wp.published DESC LIMIT 500").fetchall():
        if stupid.is_stupid_post({"url": r["url"], "author": r["author"]}):
            continue
        watched.append({
            "kind": "watch", "url": r["url"], "author": r["author"],
            "content": r["content"], "lat": r["lat"], "lon": r["lon"],
            "published": r["published"], "entity": r["entity"],
            "color": entities.ACTOR_COLORS.get(r["etype"], "#9fb0c8"),
        })
    conns = connections(conn, trends_result=tr)
    return {
        "legend": legend,
        "points": points,
        "sources": sources,
        "actors": entities.actors_for_hashtags(conn),  # Akteur×Thema je Hashtag
        "actor_colors": entities.ACTOR_COLORS,
        "watched": watched,
        "trends": tr["pairs"],
        "connections": conns["arcs"],
        "connections_truncated": conns["truncated"],
        "total_posts": sum(c[0] for c in counts.values()),
        "translations": translations,
        "languages": _langs.LANGUAGES,
    }
