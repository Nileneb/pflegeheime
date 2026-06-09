"""Account-Watching: kuratierte Akteur-Accounts beobachten und ihre Posts ingesten.
Reine CRUD + Fetch/Parse/Geo/Refresh. Auth optional über marktradar.credentials."""
from datetime import datetime, timezone

from marktradar import hashtags  # _strip_html, geocode, _get


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
