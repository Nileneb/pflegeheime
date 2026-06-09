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
