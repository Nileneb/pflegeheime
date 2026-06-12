import pytest
from marktradar import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.bootstrap(c)
    yield c
    c.close()


class FakeLlmResp:
    def __init__(self, content): self._content = content
    def raise_for_status(self): pass
    def json(self): return {"message": {"content": self._content}}


@pytest.fixture
def fake_llm():
    """Factory: fake_llm('<json>') → requests.post-Ersatz, der diese Antwort liefert.
    fake_llm(router=fn) → fn(system_msg) entscheidet pro Aufruf."""
    def make(content=None, router=None):
        def post(*a, **k):
            if router is not None:
                return FakeLlmResp(router(k["json"]["messages"][0]["content"]))
            return FakeLlmResp(content)
        return post
    return make
