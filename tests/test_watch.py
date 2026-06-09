"""Tests für den Social-Watcher."""

from marktradar import hashtags


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


def test_place_watched_post_uses_entity_institution(conn):
    conn.execute("INSERT INTO entities(id,name,type) VALUES (1,'RKI','behoerde')")
    acc = watch.add(conn, "mastodon", "rki", entity_id=1)
    lat, lon = watch._place_watched_post(conn, acc, {"location_text": ""})
    assert abs(lat - 52.52) < 0.01 and abs(lon - 13.40) < 0.01


def test_place_watched_post_falls_back_to_profile_then_none(conn):
    acc = watch.add(conn, "bluesky", "rando")
    lat, lon = watch._place_watched_post(conn, acc, {"location_text": "Hamburg Redaktion"})
    assert abs(lat - 53.55) < 0.01
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
    assert watch.refresh(conn)["added"] == 0  # idempotent


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
