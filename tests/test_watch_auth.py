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
    assert captured["headers"] is None


def test_fetch_bluesky_uses_auth_when_session(monkeypatch):
    monkeypatch.setattr(credentials, "bluesky_session", lambda user_id=None: "JWT123")
    captured = {}

    def fake_get(url, as_json=True, headers=None):
        captured["url"], captured["headers"] = url, headers
        return {"posts": []}

    monkeypatch.setattr(hashtags, "_get", fake_get)
    hashtags.fetch_bluesky("Pflege", limit=5)
    assert "bsky.social" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer JWT123"


def test_fetch_bluesky_public_without_session(monkeypatch):
    monkeypatch.setattr(credentials, "bluesky_session", lambda user_id=None: None)
    captured = {}

    def fake_get(url, as_json=True, headers=None):
        captured["url"], captured["headers"] = url, headers
        return {"posts": []}

    monkeypatch.setattr(hashtags, "_get", fake_get)
    hashtags.fetch_bluesky("Pflege", limit=5)
    assert "api.bsky.app" in captured["url"] and captured["headers"] is None


def test_fetch_account_posts_mastodon(monkeypatch):
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


def test_bluesky_401_invalidates_session_and_retries(monkeypatch):
    import urllib.error
    from marktradar import credentials
    sessions = iter(["OLD", "NEW"])
    monkeypatch.setattr(credentials, "bluesky_session", lambda user_id=None: next(sessions))
    credentials._BSKY_SESSION.update(handle="h", jwt="OLD")
    calls = []

    def fake_get(url, as_json=True, headers=None):
        calls.append(headers.get("Authorization") if headers else None)
        if len(calls) == 1:
            raise urllib.error.HTTPError(url, 401, "unauth", {}, None)
        return {"feed": []}

    monkeypatch.setattr(hashtags, "_get", fake_get)
    out = watch.fetch_account_posts("bluesky", "rki")
    assert out == []
    assert calls == ["Bearer OLD", "Bearer NEW"]  # 1× retry mit frischem Token
    assert credentials._BSKY_SESSION == {}        # Cache wurde bei 401 geleert
