"""Credential-Provider für authentifizierte Beobachtung. Single-tenant: liest die
App-Credentials aus env-Secrets (GitHub→.env). Der `user_id`-Parameter ist der
Andockpunkt für späteren per-User-OAuth (Ansatz B) — heute ignoriert.

Enthält auch den Bluesky-Session-Tausch (App-Passwort → JWT, prozess-gecacht), damit
sowohl die Hashtag-Suche (hashtags.fetch_bluesky) als auch das Account-Watching
(watch.fetch_account_posts) ihn nutzen, ohne Import-Zyklus."""
import os
import urllib.request

_BSKY_SESSION: dict = {}  # process-cache: {handle, jwt}


def get(platform: str, user_id=None) -> dict | None:
    """App-Credential für eine Plattform | None (graceful → Fetcher bleibt öffentlich).
    `user_id` ist reserviert für per-User-Token (B-Pfad) und heute ohne Wirkung."""
    if platform == "mastodon":
        token = os.getenv("MASTODON_TOKEN")
        if token:
            return {"instance": os.getenv("MASTODON_INSTANCE", "mastodon.social"), "token": token}
        return None
    if platform == "bluesky":
        handle, pw = os.getenv("BLUESKY_HANDLE"), os.getenv("BLUESKY_APP_PASSWORD")
        if handle and pw:
            return {"handle": handle, "app_password": pw}
        return None
    return None


def bluesky_session(user_id=None) -> str | None:
    """App-Passwort → Session-JWT (prozess-gecacht je Handle) | None (öffentlich).
    WHY: Bluesky-Auth hebt Rate-Limits; ohne Credential bleibt der Aufrufer öffentlich."""
    cred = get("bluesky", user_id)
    if not cred:
        return None
    if _BSKY_SESSION.get("handle") == cred["handle"] and _BSKY_SESSION.get("jwt"):
        return _BSKY_SESSION["jwt"]
    import json
    body = json.dumps({"identifier": cred["handle"], "password": cred["app_password"]}).encode()
    req = urllib.request.Request(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=12) as r:
        jwt = json.loads(r.read()).get("accessJwt")
    _BSKY_SESSION.update(handle=cred["handle"], jwt=jwt)
    return jwt
