# Marktradar Social-Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Den Marktradar von rein öffentlicher Beobachtung zu authentifizierter, gezielter Beobachtung erweitern — höhere Rate-Limits (#1) plus Account-Watching kuratierter Akteure (#2), single-tenant, mit offenem per-User-OAuth-Pfad.

**Architecture:** Alles im `marktradar/`-Paket (Python, SQLite). Eine `credentials.get(platform, user_id=None)`-Abstraktion kapselt die Token-Herkunft (heute env-Secrets, später per-User). Auth ist überall optional/graceful → ohne Credential identisches öffentliches Verhalten. Account-Watching liegt in `watch.py` mit zwei neuen Tabellen; Surfacing über `hashtags.map_data` + `viewer.py`.

**Tech Stack:** Python stdlib (`urllib`, `http.server`), `sqlite3`, vorhandene Module `hashtags`, `ranking`, `entities`. Tests: pytest mit `conn`-Fixture + Monkeypatch (Muster `tests/test_regions.py`).

---

## File Structure

- **Create** `marktradar/credentials.py` — Credential-Provider (env heute, `user_id`-Seam für B).
- **Create** `marktradar/watch.py` — Account-Watching: CRUD, Fetch, Parse, Geo, Refresh.
- **Modify** `marktradar/db.py` — `watched_accounts` + `watched_posts` ins SCHEMA.
- **Modify** `marktradar/hashtags.py` — optionales Auth in `fetch_mastodon`/`fetch_bluesky` + `_get(headers=...)`; `map_data` um `watched` + actors-Anreicherung.
- **Modify** `marktradar/viewer.py` — `watched_accounts`-CRUD-Endpoints + Manage-UI + watched-Marker.
- **Modify** `marktradar/server.py` — MCP-Tools `watch_refresh` + `watch_add`.
- **Create** Tests: `tests/test_credentials.py`, `tests/test_watch.py`, `tests/test_watch_auth.py`.
- **Modify** `.env.example` — neue Secret-Keys dokumentieren.

---

## Task 1: Neue Tabellen `watched_accounts` + `watched_posts`

**Files:**
- Modify: `marktradar/db.py` (SCHEMA-String)
- Test: `tests/test_watch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watch.py
def test_watched_tables_exist(conn):
    cols_a = {r["name"] for r in conn.execute("PRAGMA table_info(watched_accounts)")}
    assert {"id", "platform", "handle", "account_id", "entity_id", "label", "active", "added"} <= cols_a
    cols_p = {r["name"] for r in conn.execute("PRAGMA table_info(watched_posts)")}
    assert {"id", "account_id", "entity_id", "url", "author", "content", "lat", "lon", "published", "fetched_at"} <= cols_p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_watch.py::test_watched_tables_exist -q`
Expected: FAIL (no such table: watched_accounts)

- [ ] **Step 3: Add the tables to the SCHEMA string**

In `marktradar/db.py`, find the `building_photos` CREATE TABLE inside the `SCHEMA` string and add directly after it:

```sql
CREATE TABLE IF NOT EXISTS watched_accounts (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    account_id TEXT,
    entity_id INTEGER REFERENCES entities(id),
    label TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    added TEXT,
    UNIQUE(platform, handle)
);
CREATE TABLE IF NOT EXISTS watched_posts (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES watched_accounts(id),
    entity_id INTEGER REFERENCES entities(id),
    url TEXT NOT NULL,
    author TEXT, content TEXT,
    lat REAL, lon REAL, published TEXT, fetched_at TEXT,
    UNIQUE(account_id, url)
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_watch.py::test_watched_tables_exist -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marktradar/db.py tests/test_watch.py
git commit -m "feat(watch): watched_accounts + watched_posts tables"
```

---

## Task 2: `credentials.py` — Provider mit env + B-Seam

**Files:**
- Create: `marktradar/credentials.py`
- Test: `tests/test_credentials.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_credentials.py
from marktradar import credentials


def test_mastodon_credential_from_env(monkeypatch):
    monkeypatch.setenv("MASTODON_TOKEN", "tok123")
    monkeypatch.setenv("MASTODON_INSTANCE", "pflege.social")
    c = credentials.get("mastodon")
    assert c == {"instance": "pflege.social", "token": "tok123"}


def test_mastodon_instance_defaults(monkeypatch):
    monkeypatch.setenv("MASTODON_TOKEN", "tok123")
    monkeypatch.delenv("MASTODON_INSTANCE", raising=False)
    assert credentials.get("mastodon")["instance"] == "mastodon.social"


def test_missing_credential_returns_none(monkeypatch):
    monkeypatch.delenv("MASTODON_TOKEN", raising=False)
    monkeypatch.delenv("BLUESKY_HANDLE", raising=False)
    assert credentials.get("mastodon") is None
    assert credentials.get("bluesky") is None
    assert credentials.get("unknown") is None


def test_bluesky_needs_both_handle_and_password(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "me.bsky.social")
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)
    assert credentials.get("bluesky") is None
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pw")
    assert credentials.get("bluesky") == {"handle": "me.bsky.social", "app_password": "pw"}


def test_user_id_param_accepted_but_ignored(monkeypatch):
    # B-Seam: Signatur akzeptiert user_id, heute ohne Wirkung.
    monkeypatch.setenv("MASTODON_TOKEN", "tok123")
    assert credentials.get("mastodon", user_id=42) == credentials.get("mastodon")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_credentials.py -q`
Expected: FAIL (No module named 'marktradar.credentials')

- [ ] **Step 3: Implement `credentials.py`**

```python
# marktradar/credentials.py
"""Credential-Provider für authentifizierte Beobachtung. Single-tenant: liest die
App-Credentials aus env-Secrets (GitHub→.env). Der `user_id`-Parameter ist der
Andockpunkt für späteren per-User-OAuth (Ansatz B) — heute ignoriert."""
import os


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_credentials.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add marktradar/credentials.py tests/test_credentials.py
git commit -m "feat(watch): credentials provider (env today, user_id seam for B)"
```

---

## Task 3: `watch.py` — Account-CRUD

**Files:**
- Create: `marktradar/watch.py`
- Test: `tests/test_watch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watch.py  (append)
from marktradar import watch


def test_add_list_toggle_remove(conn):
    conn.execute("INSERT INTO entities(id,name,type) VALUES (1,'BMG','behoerde')")
    a = watch.add(conn, "mastodon", "@bmg_bund", entity_id=1, label="Ministerium")
    assert a["platform"] == "mastodon" and a["handle"] == "bmg_bund" and a["active"] == 1
    assert a["entity_id"] == 1
    # idempotent (UNIQUE platform+handle)
    watch.add(conn, "mastodon", "bmg_bund")
    assert len(watch.list_accounts(conn)) == 1
    watch.set_active(conn, a["id"], False)
    assert watch.list_accounts(conn)[0]["active"] == 0
    watch.remove(conn, a["id"])
    assert watch.list_accounts(conn) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_watch.py::test_add_list_toggle_remove -q`
Expected: FAIL (No module named 'marktradar.watch')

- [ ] **Step 3: Implement CRUD in `watch.py`**

```python
# marktradar/watch.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_watch.py::test_add_list_toggle_remove -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marktradar/watch.py tests/test_watch.py
git commit -m "feat(watch): watched_accounts CRUD"
```

---

## Task 4: `watch.py` — Post-Parsing (pure, fixture-getestet)

**Files:**
- Modify: `marktradar/watch.py`
- Test: `tests/test_watch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watch.py  (append)
def test_parse_mastodon_statuses():
    data = [{"url": "https://m.social/@x/1", "content": "<p>Hallo &amp; Welt</p>",
             "created_at": "2026-06-09T10:00:00Z"}]
    out = watch.parse_mastodon_statuses(data, "bmg_bund")
    assert out == [{"source": "mastodon", "url": "https://m.social/@x/1",
                    "author": "bmg_bund", "content": "Hallo & Welt",
                    "location_text": "", "published": "2026-06-09T10:00:00Z"}]


def test_parse_bluesky_feed():
    data = {"feed": [{"post": {
        "uri": "at://did:plc:abc/app.bsky.feed.post/xyz",
        "author": {"handle": "bmg.bsky.social", "displayName": "BMG"},
        "record": {"text": "Pflege news", "createdAt": "2026-06-09T11:00:00Z"}}}]}
    out = watch.parse_bluesky_feed(data, "bmg.bsky.social")
    assert out[0]["source"] == "bluesky"
    assert out[0]["url"] == "https://bsky.app/profile/bmg.bsky.social/post/xyz"
    assert out[0]["content"] == "Pflege news"
    assert out[0]["published"] == "2026-06-09T11:00:00Z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_watch.py -k parse -q`
Expected: FAIL (module 'marktradar.watch' has no attribute 'parse_mastodon_statuses')

- [ ] **Step 3: Implement the parsers in `watch.py`**

```python
# marktradar/watch.py  (append)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_watch.py -k parse -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marktradar/watch.py tests/test_watch.py
git commit -m "feat(watch): mastodon/bluesky author-feed parsers"
```

---

## Task 5: `watch.py` — Geo-Placement + `refresh`

**Files:**
- Modify: `marktradar/watch.py`
- Test: `tests/test_watch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watch.py  (append)
def test_place_watched_post_uses_entity_institution(conn):
    conn.execute("INSERT INTO entities(id,name,type) VALUES (1,'RKI','behoerde')")
    acc = watch.add(conn, "mastodon", "rki", entity_id=1)
    lat, lon = watch._place_watched_post(conn, acc, {"location_text": ""})
    assert abs(lat - 52.52) < 0.01 and abs(lon - 13.40) < 0.01  # RKI-Sitz aus ranking


def test_place_watched_post_falls_back_to_profile_then_none(conn):
    acc = watch.add(conn, "bluesky", "rando")
    lat, lon = watch._place_watched_post(conn, acc, {"location_text": "Hamburg Redaktion"})
    assert abs(lat - 53.55) < 0.01  # Gazetteer-Treffer
    lat2, lon2 = watch._place_watched_post(conn, acc, {"location_text": "nirgendwo-xyz"})
    assert lat2 is None and lon2 is None


def test_refresh_ingests_account_posts(conn, monkeypatch):
    conn.execute("INSERT INTO entities(id,name,type) VALUES (1,'RKI','behoerde')")
    watch.add(conn, "mastodon", "rki", entity_id=1)
    monkeypatch.setattr(watch, "fetch_account_posts", lambda platform, handle: [
        {"source": "mastodon", "url": "https://m/rki/1", "author": "rki",
         "content": "c", "location_text": "", "published": "2026-06-09T10:00:00Z"}])
    res = watch.refresh(conn)
    assert res["added"] == 1 and res["accounts"] == 1 and res["errors"] == []
    row = conn.execute("SELECT entity_id,lat FROM watched_posts WHERE url='https://m/rki/1'").fetchone()
    assert row["entity_id"] == 1 and abs(row["lat"] - 52.52) < 0.01
    # idempotent
    assert watch.refresh(conn)["added"] == 0


def test_refresh_isolates_account_errors(conn, monkeypatch):
    watch.add(conn, "mastodon", "a")
    watch.add(conn, "mastodon", "b")

    def flaky(platform, handle):
        if handle == "a":
            raise ConnectionError("down")
        return [{"source": "mastodon", "url": "https://m/b/1", "author": "b",
                 "content": "c", "location_text": "", "published": None}]

    monkeypatch.setattr(watch, "fetch_account_posts", flaky)
    res = watch.refresh(conn)
    assert res["added"] == 1
    assert any("a" in e for e in res["errors"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_watch.py -k "place or refresh" -q`
Expected: FAIL (no attribute '_place_watched_post')

- [ ] **Step 3: Implement geo + refresh in `watch.py`**

```python
# marktradar/watch.py  (append)
def _place_watched_post(conn, account, post):
    """Geo: verknüpfte Institution (Sitz) → Profil-Ort (Gazetteer) → kein Punkt."""
    from marktradar import ranking
    if account.get("entity_id"):
        ent = conn.execute("SELECT name FROM entities WHERE id=?",
                           (account["entity_id"],)).fetchone()
        if ent:
            inst = ranking.match_institution(ent["name"])
            if inst:
                return (inst["lat"], inst["lon"])
    ll = hashtags.geocode(post.get("location_text"))
    if ll:
        return (ll[0], ll[1])
    return (None, None)


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_watch.py -k "place or refresh" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marktradar/watch.py tests/test_watch.py
git commit -m "feat(watch): geo placement + idempotent refresh"
```

---

## Task 6: Netz-Fetcher `fetch_account_posts` + optionales Auth in `hashtags`

**Files:**
- Modify: `marktradar/hashtags.py` (`_get` headers; auth in `fetch_mastodon`/`fetch_bluesky`)
- Modify: `marktradar/watch.py` (`fetch_account_posts`, `_bluesky_session`)
- Test: `tests/test_watch_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watch_auth.py
from marktradar import hashtags, watch, credentials


def test_get_accepts_extra_headers(monkeypatch):
    seen = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"

    def fake_urlopen(req, timeout=None):
        seen["auth"] = req.headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(hashtags.urllib.request, "urlopen", fake_urlopen)
    hashtags._get("https://x/y", headers={"Authorization": "Bearer T"})
    assert seen["auth"] == "Bearer T"


def test_fetch_mastodon_uses_auth_when_present(monkeypatch):
    monkeypatch.setenv("MASTODON_TOKEN", "T")
    monkeypatch.setenv("MASTODON_INSTANCE", "pflege.social")
    captured = {}

    def fake_get(url, as_json=True, headers=None):
        captured["url"], captured["headers"] = url, headers
        return []

    monkeypatch.setattr(hashtags, "_get", fake_get)
    hashtags.fetch_mastodon("Pflege", limit=5)
    assert "pflege.social" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer T"


def test_fetch_mastodon_public_without_token(monkeypatch):
    monkeypatch.delenv("MASTODON_TOKEN", raising=False)
    captured = {}
    monkeypatch.setattr(hashtags, "_get",
                        lambda url, as_json=True, headers=None: captured.update(headers=headers) or [])
    hashtags.fetch_mastodon("Pflege", limit=5)
    assert captured["headers"] is None  # unverändertes öffentliches Verhalten


def test_fetch_account_posts_mastodon(monkeypatch):
    # lookup → id, dann statuses
    calls = []

    def fake_get(url, as_json=True, headers=None):
        calls.append(url)
        if "lookup" in url:
            return {"id": "42"}
        return [{"url": "https://m/x/1", "content": "<p>hi</p>", "created_at": "2026-06-09T10:00:00Z"}]

    monkeypatch.setattr(hashtags, "_get", fake_get)
    out = watch.fetch_account_posts("mastodon", "rki")
    assert out[0]["url"] == "https://m/x/1" and out[0]["author"] == "rki"
    assert any("lookup" in u for u in calls) and any("/statuses" in u for u in calls)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_watch_auth.py -q`
Expected: FAIL (`_get()` got unexpected keyword 'headers' / no attribute 'fetch_account_posts')

- [ ] **Step 3a: Add `headers` to `hashtags._get`**

In `marktradar/hashtags.py` replace `_get`:

```python
def _get(url, as_json=True, headers=None):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read()
    return json.loads(raw) if as_json else raw
```

- [ ] **Step 3b: Auth in `fetch_mastodon`**

Replace `fetch_mastodon` in `marktradar/hashtags.py`:

```python
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
```

- [ ] **Step 3c: Implement `fetch_account_posts` + Bluesky session in `watch.py`**

```python
# marktradar/watch.py  (append)
import urllib.parse
from marktradar import credentials

_BSKY_SESSION = {}  # process-cache: {jwt, handle}


def _bluesky_session():
    """App-Passwort → Session-JWT (gecacht je Prozess) | None (öffentlich)."""
    cred = credentials.get("bluesky")
    if not cred:
        return None
    if _BSKY_SESSION.get("handle") == cred["handle"] and _BSKY_SESSION.get("jwt"):
        return _BSKY_SESSION["jwt"]
    import json as _json
    import urllib.request
    body = _json.dumps({"identifier": cred["handle"], "password": cred["app_password"]}).encode()
    req = urllib.request.Request(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=hashtags.TIMEOUT) as r:
        jwt = _json.loads(r.read()).get("accessJwt")
    _BSKY_SESSION.update(handle=cred["handle"], jwt=jwt)
    return jwt


def fetch_account_posts(platform, handle):
    """Letzte Posts eines beobachteten Accounts → normalisierte Posts."""
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
        jwt = _bluesky_session()
        headers = {"Authorization": f"Bearer {jwt}"} if jwt else None
        actor = urllib.parse.quote(handle)
        host = "bsky.social" if jwt else "public.api.bsky.app"
        data = hashtags._get(
            f"https://{host}/xrpc/app.bsky.feed.getAuthorFeed?actor={actor}&limit=20",
            headers=headers)
        return parse_bluesky_feed(data, handle)
    raise ValueError(f"unsupported platform: {platform}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_watch_auth.py -q`
Expected: PASS

- [ ] **Step 4b: Run the full suite to confirm no regression in existing fetchers**

Run: `python3 -m pytest tests/test_multilingual.py tests/test_regions.py -q`
Expected: PASS (existing public-fetch behavior unchanged)

- [ ] **Step 5: Commit**

```bash
git add marktradar/hashtags.py marktradar/watch.py tests/test_watch_auth.py
git commit -m "feat(watch): optional auth + fetch_account_posts (mastodon/bluesky)"
```

---

## Task 7: Surfacing in `map_data` — `watched` + actors-Anreicherung

**Files:**
- Modify: `marktradar/hashtags.py` (`map_data`)
- Test: `tests/test_watch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watch.py  (append)
def test_map_data_includes_watched_points_and_actor_stream(conn):
    conn.execute("INSERT INTO hashtags(id,term,color,active) VALUES (1,'Pflege','#fff',1)")
    conn.execute("INSERT INTO entities(id,name,type) VALUES (1,'RKI','behoerde')")
    acc = watch.add(conn, "mastodon", "rki", entity_id=1)
    conn.execute(
        "INSERT INTO watched_posts(account_id,entity_id,url,author,content,lat,lon,published,fetched_at)"
        " VALUES (?,1,'https://m/rki/1','rki','news',52.5,13.4,'2026-06-09T10:00:00Z','x')",
        (acc["id"],))
    conn.commit()
    d = hashtags.map_data(conn)
    assert any(p["url"] == "https://m/rki/1" and p["kind"] == "watch" for p in d["watched"])
    pt = next(p for p in d["watched"] if p["url"] == "https://m/rki/1")
    assert pt["entity"] == "RKI" and pt["color"] == d["actor_colors"]["behoerde"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_watch.py::test_map_data_includes_watched_points_and_actor_stream -q`
Expected: FAIL (KeyError: 'watched')

- [ ] **Step 3: Add `watched` to `map_data`**

In `marktradar/hashtags.py` `map_data`, in the final return dict (where `actors`/`actor_colors` were added), insert a `watched` key. Directly before `conns = connections(...)` add:

```python
    from marktradar import entities as _ent
    watched = []
    for r in conn.execute(
            "SELECT wp.url, wp.author, wp.content, wp.lat, wp.lon, wp.published, "
            "e.name AS entity, e.type AS etype "
            "FROM watched_posts wp "
            "LEFT JOIN entities e ON e.id = wp.entity_id "
            "WHERE wp.lat IS NOT NULL "
            "ORDER BY wp.published DESC LIMIT 500").fetchall():
        watched.append({
            "kind": "watch", "url": r["url"], "author": r["author"],
            "content": r["content"], "lat": r["lat"], "lon": r["lon"],
            "published": r["published"], "entity": r["entity"],
            "color": _ent.ACTOR_COLORS.get(r["etype"], "#9fb0c8"),
        })
```

Then add `"watched": watched,` to the returned dict (next to `"actors"`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_watch.py::test_map_data_includes_watched_points_and_actor_stream -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marktradar/hashtags.py tests/test_watch.py
git commit -m "feat(watch): surface watched posts in map_data"
```

---

## Task 8: MCP-Tools `watch_add` + `watch_refresh`

**Files:**
- Modify: `marktradar/server.py`
- Test: `tests/test_watch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_watch.py  (append)
def test_server_exposes_watch_tools():
    import marktradar.server as s
    assert hasattr(s, "watch_add") and hasattr(s, "watch_refresh")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_watch.py::test_server_exposes_watch_tools -q`
Expected: FAIL (no attribute 'watch_add')

- [ ] **Step 3: Add the MCP tools in `server.py`**

After the `regeocode_posts` tool in `marktradar/server.py` add:

```python
@mcp.tool()
def watch_add(platform: str, handle: str, entity_id: int | None = None,
              label: str | None = None) -> dict:
    """Beobachte einen Akteur-Account (mastodon/bluesky). Optional mit entity_id
    verknüpfen (Akteur×Thema) und Label. Idempotent (platform+handle UNIQUE)."""
    from marktradar import watch
    return watch.add(_conn, platform, handle, entity_id=entity_id, label=label)


@mcp.tool()
def watch_refresh() -> dict:
    """Holt die letzten Posts aller aktiven beobachteten Accounts in watched_posts
    (idempotent). Fehler je Account isoliert im Report."""
    from marktradar import watch
    return watch.refresh(_conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_watch.py::test_server_exposes_watch_tools -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marktradar/server.py tests/test_watch.py
git commit -m "feat(watch): MCP tools watch_add + watch_refresh"
```

---

## Task 9: Viewer — Manage-UI (CRUD-Endpoints) + watched-Marker

**Files:**
- Modify: `marktradar/viewer.py` (GET `/api/watch`, POST `/api/watch/add|remove|toggle|refresh`; JS Manage-Panel + watched-Marker)
- Test: manuell via Playwright-Screenshot (Pest/pytest prüft Logik nicht Layout — siehe [[feedback_validate_frontend_screenshots]])

- [ ] **Step 1: Add the GET route**

In `marktradar/viewer.py` `do_GET`, after the `/api/hashtags` branch, add:

```python
            elif u.path == "/api/watch":
                from marktradar import watch
                _json(self, {"accounts": watch.list_accounts(conn)})
```

- [ ] **Step 2: Add the POST routes**

In `do_POST`, before the final `else: _json(self, {"error": "not found"}, 404)`, add:

```python
            elif path == "/api/watch/add":
                from marktradar import watch
                b = self._body()
                _json(self, watch.add(conn, b["platform"], b["handle"],
                                      entity_id=b.get("entity_id"), label=b.get("label")))
            elif path == "/api/watch/remove":
                from marktradar import watch
                _json(self, watch.remove(conn, int(self._body()["id"])))
            elif path == "/api/watch/toggle":
                from marktradar import watch
                b = self._body()
                _json(self, watch.set_active(conn, int(b["id"]), bool(b.get("active"))))
            elif path == "/api/watch/refresh":
                from marktradar import watch
                _json(self, watch.refresh(conn))
```

- [ ] **Step 3: Render watched-Marker on the globe**

In `marktradar/viewer.py` `initGlobe(points)`, after the existing points loop that builds source markers, add a loop over `HTDATA.watched` that adds a distinct marker (Octahedron, not Sphere) per watched point:

```javascript
  const wgeo=new THREE.OctahedronGeometry(1,0);
  ((HTDATA&&HTDATA.watched)||[]).forEach(w=>{
    if(w.lat==null||w.lon==null)return;
    const pos=ll2v(w.lat,w.lon,1.012);
    const m=new THREE.Mesh(wgeo,new THREE.MeshBasicMaterial({color:w.color||'#9fb0c8'}));
    m.position.copy(pos);m.scale.setScalar(0.022);
    m.userData={url:w.url,term:w.entity||'',watch:true,lat:w.lat,lon:w.lon};
    group.add(m);
  });
```

- [ ] **Step 4: Add the Manage-Panel JS**

Add a `loadWatch()`/`renderWatch()` pair mirroring `renderHtLegend`, plus `addWatch()`/`delWatch()`/`toggleWatch()`/`refreshWatch()` mirroring the hashtag-CRUD functions (`addHt`/`delHt`/`toggleHt`), wired to the `/api/watch/*` endpoints. Add a `<div id=watchlist>` + add-form (`platform` select mastodon/bluesky, `handle` input, optional `entity_id`) into the sidebar HTML next to the hashtag panel. Call `loadWatch()` inside `loadGlobe()`.

- [ ] **Step 5: Validate via screenshot**

Run the viewer against a populated DB and screenshot the manage panel + a watched marker:

```bash
PFLEGE_DB=/tmp/pflege_test.db PFLEGE_VIEWER_PORT=8799 python3 -m marktradar.viewer &
# Playwright: load /, call loadGlobe(), addWatch demo, screenshot #watchlist
```

Confirm: panel lists accounts with toggle/remove, add-form works, octahedron markers render. Fix layout until correct.

- [ ] **Step 6: Commit**

```bash
git add marktradar/viewer.py
git commit -m "feat(watch): viewer manage-UI + watched markers"
```

---

## Task 10: Env-Doku + Smoke

**Files:**
- Modify: `.env.example`
- Modify: `marktradar/BLOSM_PIPELINE.md` is unrelated — instead append a short watcher section to the README or a new `marktradar/WATCHER.md`.

- [ ] **Step 1: Document the secrets**

Append to `.env.example`:

```bash
# Social-Watcher (optional; ohne diese läuft die Beobachtung öffentlich):
# MASTODON_INSTANCE=mastodon.social
# MASTODON_TOKEN=
# BLUESKY_HANDLE=
# BLUESKY_APP_PASSWORD=
```

- [ ] **Step 2: Write `marktradar/WATCHER.md`**

```markdown
# Social-Watcher

Beobachtet kuratierte Akteur-Accounts (Mastodon/Bluesky) und ingestet ihre Posts
in `watched_posts`, verknüpft mit `entities` (Akteur×Thema). Authentifiziert (höhere
Rate-Limits) wenn Secrets gesetzt, sonst öffentlich.

- Accounts verwalten: Viewer-Panel oder MCP `watch_add(platform, handle, entity_id?)`.
- Refresh: MCP `watch_refresh()` oder der hourly Daemon (wenn verdrahtet).
- Secrets: `MASTODON_TOKEN`/`MASTODON_INSTANCE`, `BLUESKY_HANDLE`/`BLUESKY_APP_PASSWORD`
  zentral via app.linn.games GitHub→.env.
```

- [ ] **Step 3: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS (alle, inkl. der neuen watch/credentials-Tests)

- [ ] **Step 4: Commit**

```bash
git add .env.example marktradar/WATCHER.md
git commit -m "docs(watch): env keys + WATCHER.md"
```

---

## Deployment (nach allen Tasks)

1. Secrets `MASTODON_*`/`BLUESKY_*` als app.linn.games GitHub-vars/secrets setzen (kein u-server-Edit).
2. pflege-Image: `docker build` → `docker save | ssh u-server docker load` → `docker compose up -d --force-recreate --no-deps pflege-viewer mcp-pflege`.
3. Migration: `db.bootstrap` legt die neuen Tabellen via `CREATE TABLE IF NOT EXISTS` auf der Prod-DB an (kein Daten-Risiko).
4. Ein paar Accounts via `watch_add` anlegen, `watch_refresh` triggern (explizite Prod-Write-Freigabe einholen), Globus prüfen.
