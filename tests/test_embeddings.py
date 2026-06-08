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
