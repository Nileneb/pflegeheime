"""Tests für den Social-Watcher."""


def test_watched_tables_exist(conn):
    cols_a = {r["name"] for r in conn.execute("PRAGMA table_info(watched_accounts)")}
    assert {"id", "platform", "handle", "account_id", "entity_id", "label", "active", "added"} <= cols_a
    cols_p = {r["name"] for r in conn.execute("PRAGMA table_info(watched_posts)")}
    assert {"id", "account_id", "entity_id", "url", "author", "content", "lat", "lon", "published", "fetched_at"} <= cols_p
