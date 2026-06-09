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
    monkeypatch.setenv("MASTODON_TOKEN", "tok123")
    assert credentials.get("mastodon", user_id=42) == credentials.get("mastodon")


def test_bluesky_session_none_without_credential(monkeypatch):
    monkeypatch.delenv("BLUESKY_HANDLE", raising=False)
    credentials._BSKY_SESSION.clear()
    assert credentials.bluesky_session() is None


def test_bluesky_session_creates_and_caches_jwt(monkeypatch):
    monkeypatch.setenv("BLUESKY_HANDLE", "me.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "pw")
    credentials._BSKY_SESSION.clear()
    calls = {"n": 0}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"accessJwt": "JWT123"}'

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr(credentials.urllib.request, "urlopen", fake_urlopen)
    assert credentials.bluesky_session() == "JWT123"
    assert credentials.bluesky_session() == "JWT123"
    assert calls["n"] == 1
