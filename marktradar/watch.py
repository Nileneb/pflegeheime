"""Account-Watching: kuratierte Akteur-Accounts beobachten und ihre Posts ingesten.
Reine CRUD + Fetch/Parse/Geo/Refresh. Auth optional über marktradar.credentials."""
import urllib.error
import urllib.parse
from datetime import datetime, timezone

from marktradar import credentials, hashtags  # _strip_html, geocode, _get


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _account(conn, platform, handle):
    r = conn.execute("SELECT * FROM watched_accounts WHERE platform=? AND handle=?",
                     (platform, handle)).fetchone()
    return dict(r) if r else None


def add(conn, platform, handle, *, entity_id=None, label=None, account_id=None) -> dict:
    handle = handle.lstrip("@").strip()
    conn.execute(
        "INSERT OR IGNORE INTO watched_accounts"
        "(platform,handle,account_id,entity_id,label,active,added) VALUES (?,?,?,?,?,1,?)",
        (platform, handle, account_id, entity_id, label, _now()))
    conn.commit()
    return _account(conn, platform, handle)


def list_accounts(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM watched_accounts ORDER BY active DESC, platform, handle").fetchall()]


def set_active(conn, account_id, active) -> dict:
    conn.execute("UPDATE watched_accounts SET active=? WHERE id=?",
                 (1 if active else 0, account_id))
    conn.commit()
    r = conn.execute("SELECT * FROM watched_accounts WHERE id=?", (account_id,)).fetchone()
    return dict(r) if r else {"removed": account_id}


def remove(conn, account_id) -> dict:
    conn.execute("DELETE FROM watched_posts WHERE account_id=?", (account_id,))
    conn.execute("DELETE FROM watched_accounts WHERE id=?", (account_id,))
    conn.commit()
    return {"removed": account_id}


def parse_mastodon_statuses(data, handle) -> list[dict]:
    """Mastodon /accounts/:id/statuses → normalisierte Posts."""
    out = []
    for s in data or []:
        if not s.get("url"):
            continue
        out.append({
            "source": "mastodon", "url": s.get("url"), "author": handle,
            "content": hashtags._strip_html(s.get("content"))[:280],
            "location_text": "", "published": s.get("created_at"),
        })
    return out


def parse_bluesky_feed(data, handle) -> list[dict]:
    """Bluesky app.bsky.feed.getAuthorFeed → normalisierte Posts."""
    out = []
    for item in (data or {}).get("feed", []):
        p = item.get("post") or {}
        rec = p.get("record") or {}
        au = p.get("author") or {}
        rkey = (p.get("uri") or "").rsplit("/", 1)[-1]
        h = au.get("handle") or handle
        url = f"https://bsky.app/profile/{h}/post/{rkey}" if rkey else None
        if not url:
            continue
        out.append({
            "source": "bluesky", "url": url,
            "author": au.get("displayName") or h,
            "content": (rec.get("text") or "")[:280],
            "location_text": (au.get("description") or "")[:60],
            "published": rec.get("createdAt"),
        })
    return out


def fetch_account_posts(platform: str, handle: str) -> list[dict]:
    """Letzte Posts eines beobachteten Accounts → normalisierte Posts. Auth optional."""
    if platform == "mastodon":
        cred = credentials.get("mastodon")
        instance = cred["instance"] if cred else "mastodon.social"
        headers = {"Authorization": f"Bearer {cred['token']}"} if cred else None
        acct = urllib.parse.quote(handle)
        info = hashtags._get(
            f"https://{instance}/api/v1/accounts/lookup?acct={acct}", headers=headers)
        aid = (info or {}).get("id")
        if not aid:
            return []
        data = hashtags._get(
            f"https://{instance}/api/v1/accounts/{aid}/statuses?limit=20", headers=headers)
        return parse_mastodon_statuses(data, handle)
    if platform == "bluesky":
        actor = urllib.parse.quote(handle)

        def _feed(jwt):
            headers = {"Authorization": f"Bearer {jwt}"} if jwt else None
            # WHY: api.bsky.app liefert die unauth. AppView-Suche aus (public.api.bsky.app
            # → 403 Cloudflare); identisch zu hashtags.fetch_bluesky.
            host = "bsky.social" if jwt else "api.bsky.app"
            return hashtags._get(
                f"https://{host}/xrpc/app.bsky.feed.getAuthorFeed?actor={actor}&limit=20",
                headers=headers)

        jwt = credentials.bluesky_session()
        try:
            data = _feed(jwt)
        except urllib.error.HTTPError as e:
            # WHY: Bluesky-JWT läuft nach ~2h ab → Cache leeren + EINMAL neu, sonst
            # bleiben alle Bluesky-Accounts bis Prozess-Neustart tot.
            if e.code == 401 and jwt:
                credentials._BSKY_SESSION.clear()
                data = _feed(credentials.bluesky_session())
            else:
                raise
        return parse_bluesky_feed(data, handle)
    raise ValueError(f"unsupported platform: {platform}")


# Bekannte Akteur-Sitze, die KEINE Trust-Institution sind (v.a. Medien) — nur für Geo.
# Schlüssel = lowercased Label/Handle-Präfix (vor '@').
ACTOR_LOCATIONS = {
    "tagesschau": (53.55, 9.99), "ard": (53.55, 9.99), "ndr": (53.55, 9.99),
    "zeit": (53.55, 9.99), "spiegel": (53.55, 9.99), "zdf": (50.0, 8.27),
    "sueddeutsche": (48.14, 11.58), "faz": (50.11, 8.68),
}


def _institution_for(text):
    """Institution für einen Akteur-Text: exakter INSTITUTIONS-Name zuerst (robust für
    kanonische Namen wie 'RKI', die der Substring-Marker '​rki ' nicht trifft), dann
    Marker-Substring-Suche."""
    from marktradar import ranking
    if not text:
        return None
    low = text.lower()
    exact = next((i for i in ranking.INSTITUTIONS if i["name"].lower() == low), None)
    return exact or ranking.match_institution(text) or ranking.match_institution(text + " ")


def _place_watched_post(conn, account, post):
    """Geo eines beobachteten Posts: über den AKTEUR (verknüpfte Entität → Label →
    Handle, je als Institutionssitz oder Gazetteer-Ort) → Post-Ort → kein Punkt.
    WHY: institutionelle Account-Posts tragen selbst keinen Orts-Text — der Ort steckt
    im Akteur (RKI→Berlin, tagesschau→Hamburg)."""
    texts = []
    if account.get("entity_id"):
        ent = conn.execute("SELECT name FROM entities WHERE id=?",
                           (account["entity_id"],)).fetchone()
        if ent:
            texts.append(ent["name"])
    texts += [account.get("label"), account.get("handle")]
    for t in texts:
        inst = _institution_for(t)
        if inst:
            return (inst["lat"], inst["lon"])
        loc = ACTOR_LOCATIONS.get((t or "").lower().split("@")[0].strip())
        if loc:
            return loc
        ll = hashtags.geocode(t)
        if ll:
            return (ll[0], ll[1])
    ll = hashtags.geocode(post.get("location_text"))
    if ll:
        return (ll[0], ll[1])
    return (None, None)


def regeocode(conn) -> dict:
    """Verortet bestehende watched_posts neu mit der aktuellen Placement-Logik (nach
    Registry-/Label-Änderungen), ohne neu zu fetchen. Idempotent."""
    accounts = {a["id"]: a for a in (dict(r) for r in
                conn.execute("SELECT * FROM watched_accounts").fetchall())}
    n = 0
    for r in conn.execute("SELECT id, account_id, url, author, content FROM watched_posts").fetchall():
        acc = accounts.get(r["account_id"])
        if not acc:
            continue
        lat, lon = _place_watched_post(conn, acc, {"location_text": ""})
        conn.execute("UPDATE watched_posts SET lat=?, lon=? WHERE id=?", (lat, lon, r["id"]))
        n += 1
    conn.commit()
    return {"regeocoded": n}


def refresh(conn) -> dict:
    """Je aktivem Account die letzten Posts holen, idempotent in watched_posts.
    Fehler je Account isoliert + im Report (NICHT verschluckt)."""
    rows = conn.execute("SELECT * FROM watched_accounts WHERE active=1").fetchall()
    added, errors, now = 0, [], _now()
    for r in rows:
        acc = dict(r)
        try:
            posts = fetch_account_posts(acc["platform"], acc["handle"])
        except Exception as e:  # WHY: ein Account down → andere laufen weiter
            errors.append(f"{acc['platform']}:{acc['handle']}: {type(e).__name__}: {e}")
            continue
        for p in posts:
            if not p.get("url"):
                continue
            lat, lon = _place_watched_post(conn, acc, p)
            cur = conn.execute(
                "INSERT OR IGNORE INTO watched_posts"
                "(account_id,entity_id,url,author,content,lat,lon,published,fetched_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (acc["id"], acc["entity_id"], p["url"], p.get("author"),
                 p.get("content"), lat, lon, p.get("published"), now))
            if cur.rowcount:
                added += 1
    conn.commit()
    return {"added": added, "accounts": len(rows), "errors": errors}
