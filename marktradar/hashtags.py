"""Hashtag-Beobachtung à la Taggbox: aggregiert echte Posts/Treffer je Hashtag
aus öffentlichen Quellen (Mastodon, Bluesky, Google-News-RSS), geokodiert sie grob
über die Autor-/Outlet-Location und liefert Geo-Punkte für den 3D-Globus.

Jeder Punkt hängt an einer ECHTEN, klickbaren Quell-URL ("immer über die Quelle").
Hashtags sind CRUD-fähig (Tabelle `hashtags`), Posts liegen in `hashtag_posts`.
"""
import html as _html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

UA = "pflege-marktradar/1.0 (+https://pflege.linn.games)"
TIMEOUT = 12

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
DE_CENTER = (51.16, 10.45)


def _get(url, as_json=True):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
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


def _jitter(seed_str, base):
    """Deterministischer kleiner Versatz (±~1.5°) damit gleiche Koordinaten nicht
    exakt überlappen — Stadt-Cluster bleiben sichtbar getrennt."""
    h = abs(hash(seed_str))
    dlat = ((h % 1000) / 1000 - 0.5) * 3.0
    dlon = (((h // 1000) % 1000) / 1000 - 0.5) * 3.0
    return (base[0] + dlat, base[1] + dlon)


# ── Fetcher: liefern Liste {source, url, author, content, location_text, published} ──
def fetch_mastodon(term, limit=20, instance="mastodon.social"):
    tag = urllib.parse.quote(term.lstrip("#"))
    data = _get(f"https://{instance}/api/v1/timelines/tag/{tag}?limit={limit}")
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


def fetch_bluesky(term, limit=20):
    q = urllib.parse.quote("#" + term.lstrip("#"))
    # WHY: public.api.bsky.app antwortet 403 (Cloudflare); api.bsky.app liefert die
    # unauth. AppView-Suche aus.
    data = _get(f"https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={q}&limit={limit}")
    out = []
    for p in (data or {}).get("posts", []):
        au = p.get("author") or {}
        rec = p.get("record") or {}
        handle = au.get("handle", "")
        rkey = (p.get("uri") or "").rsplit("/", 1)[-1]
        url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else None
        out.append({
            "source": "bluesky", "url": url,
            "author": au.get("displayName") or handle,
            "content": (rec.get("text") or "")[:280],
            "location_text": (au.get("description") or "")[:60],
            "published": rec.get("createdAt"),
        })
    return out


def fetch_news(term, limit=20):
    q = urllib.parse.quote(f'"{term.lstrip("#")}"')
    url = f"https://news.google.com/rss/search?q={q}&hl=de&gl=DE&ceid=DE:de"
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


FETCHERS = {"mastodon": fetch_mastodon, "bluesky": fetch_bluesky, "news": fetch_news}


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
    model = os.getenv("CHAT_MODEL", "qwen3.5:9b")
    try:
        r = requests.post(
            f"{embeddings.CHAT_HOST}/api/chat", headers=embeddings.chat_headers(),
            json={"model": model, "format": "json", "stream": False, "think": False,
                  "messages": [{"role": "system", "content": _EXTRACT_SYS},
                               {"role": "user", "content": f"Titel: {title}\nText: {summary or ''}"[:1200]}],
                  "options": {"temperature": 0.1, "num_ctx": 2048, "num_predict": 400}}, timeout=90)
        r.raise_for_status()
        msg = r.json().get("message", {}) or {}
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
    model = os.getenv("CHAT_MODEL", "qwen3.5:9b")
    out = {"host": embeddings.CHAT_HOST, "model": model, "items": []}
    for a in rows:
        item = {"id": a["id"], "title": a["title"], "relevant": a["relevant"]}
        try:
            r = requests.post(
                f"{embeddings.CHAT_HOST}/api/chat", headers=embeddings.chat_headers(),
                json={"model": model, "format": "json", "stream": False, "think": False,
                      "messages": [{"role": "system", "content": _EXTRACT_SYS},
                                   {"role": "user", "content": f"Titel: {a['title']}\nText: {a['summary'] or ''}"[:1200]}],
                      "options": {"temperature": 0.1, "num_ctx": 2048, "num_predict": 400}}, timeout=90)
            item["status"] = r.status_code
            msg = (r.json().get("message") or {})
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
        for src in sources:
            try:
                posts = FETCHERS[src](ht["term"], limit)
            except Exception as e:  # WHY: eine Quelle/ein Tag down → andere weiter, Fehler gemeldet (nicht stumm)
                errors.append(f"{src}:{ht['term']}: {type(e).__name__}: {e}")
                continue
            for p in posts:
                if not p.get("url"):
                    continue
                ll = geocode(p.get("location_text"))
                if ll is None and src == "news":
                    ll = _jitter(p["url"], DE_CENTER)  # News = DE, gestreut um Zentrum
                elif ll is not None:
                    ll = _jitter(p["url"], ll)
                lat, lon = (ll if ll else (None, None))
                cur = conn.execute(
                    "INSERT OR IGNORE INTO hashtag_posts(hashtag_id,source,url,author,content,"
                    "location_text,lat,lon,published,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (ht["id"], p["source"], p["url"], p.get("author"), p.get("content"),
                     p.get("location_text"), lat, lon, p.get("published"), now))
                if cur.rowcount:
                    added += 1
                    per_source[src] = per_source.get(src, 0) + 1
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


# ── Map-Daten für den Globus ──
def map_data(conn, max_points=600):
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
    rows = conn.execute(
        "SELECT p.hashtag_id,p.source,p.url,p.author,p.content,p.lat,p.lon,p.published "
        "FROM hashtag_posts p WHERE p.lat IS NOT NULL "
        "ORDER BY p.published DESC LIMIT ?", (max_points,)).fetchall()
    points = []
    for r in rows:
        t = tags.get(r["hashtag_id"], {})
        wn = (W.get(r["hashtag_id"], {}).get("weight", 0) / maxw) if maxw else 0
        points.append({
            "lat": r["lat"], "lon": r["lon"], "color": t.get("color", "#5b8def"),
            "term": t.get("term", "?"), "source": r["source"], "url": r["url"],
            "author": r["author"], "content": r["content"], "published": r["published"],
            "weight": round(wn, 3),  # 0..1 Trend-Stärke → Pulsieren
        })
    sources = {}
    for r in conn.execute(
            "SELECT hashtag_id,source,url,author,content,published FROM hashtag_posts "
            "ORDER BY published DESC").fetchall():
        sources.setdefault(r["hashtag_id"], [])
        if len(sources[r["hashtag_id"]]) < 12:
            sources[r["hashtag_id"]].append(dict(r))
    return {"legend": legend, "points": points, "sources": sources, "trends": tr["pairs"],
            "total_posts": sum(c[0] for c in counts.values())}
