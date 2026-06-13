from marktradar import embeddings


def test_embed_returns_configured_dim(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"embedding": [0.5] * embeddings.EMBED_DIM}

    def fake_post(url, json, timeout, headers=None):
        captured["url"] = url
        captured["model"] = json["model"]
        return FakeResp()

    monkeypatch.setattr(embeddings.requests, "post", fake_post)
    vec = embeddings.embed("Korian eröffnet neues Pflegeheim")
    assert len(vec) == embeddings.EMBED_DIM == 1024
    assert captured["model"] == "bge-m3"
    assert captured["url"].endswith("/api/embeddings")


def test_embed_raises_on_ollama_down(monkeypatch):
    def boom(*a, **k): raise embeddings.requests.ConnectionError("refused")
    monkeypatch.setattr(embeddings.requests, "post", boom)
    import pytest
    with pytest.raises(embeddings.requests.ConnectionError):
        embeddings.embed("x")


class _ThinkingResp:
    def __init__(self, content, thinking):
        self._c, self._t = content, thinking
    def raise_for_status(self): pass
    def json(self): return {"message": {"content": self._c, "thinking": self._t}}


def test_chat_json_falls_back_to_thinking_channel(monkeypatch):
    # Regression: gpt-oss:20b (Reasoning) lässt content leer → in Prod wurde
    # JEDER Artikel still relevant=0 (Feed statisch seit Deploy 2026-06-08).
    monkeypatch.setattr(embeddings.requests, "post", lambda *a, **k: _ThinkingResp(
        "", 'Der Nutzer will JSON. Ich antworte {"relevant": true, "kategorie": "News", "grund": "echt"} fertig.'))
    d = embeddings.chat_json("sys", "user", empty={})
    assert d == {"relevant": True, "kategorie": "News", "grund": "echt"}


def test_chat_json_content_wins_over_thinking(monkeypatch):
    monkeypatch.setattr(embeddings.requests, "post", lambda *a, **k: _ThinkingResp(
        '{"relevant": false}', '{"relevant": true}'))
    assert embeddings.chat_json("s", "u") == {"relevant": False}


def test_chat_json_empty_both_returns_empty_default(monkeypatch):
    monkeypatch.setattr(embeddings.requests, "post",
                        lambda *a, **k: _ThinkingResp("", "nur prosa ohne json"))
    assert embeddings.chat_json("s", "u", empty=[]) == []
