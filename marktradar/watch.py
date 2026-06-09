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
