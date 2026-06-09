"""Tests für den Social-Watcher."""


def test_watched_tables_exist(conn):
    cols_a = {r["name"] for r in conn.execute("PRAGMA table_info(watched_accounts)")}
    assert {"id", "platform", "handle", "account_id", "entity_id", "label", "active", "added"} <= cols_a
    cols_p = {r["name"] for r in conn.execute("PRAGMA table_info(watched_posts)")}
    assert {"id", "account_id", "entity_id", "url", "author", "content", "lat", "lon", "published", "fetched_at"} <= cols_p


from marktradar import watch


def test_add_list_toggle_remove(conn):
    conn.execute("INSERT INTO entities(id,name,type) VALUES (1,'BMG','behoerde')")
    a = watch.add(conn, "mastodon", "@bmg_bund", entity_id=1, label="Ministerium")
    assert a["platform"] == "mastodon" and a["handle"] == "bmg_bund" and a["active"] == 1
    assert a["entity_id"] == 1
    watch.add(conn, "mastodon", "bmg_bund")  # idempotent (UNIQUE platform+handle)
    assert len(watch.list_accounts(conn)) == 1
    watch.set_active(conn, a["id"], False)
    assert watch.list_accounts(conn)[0]["active"] == 0
    watch.remove(conn, a["id"])
    assert watch.list_accounts(conn) == []


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
