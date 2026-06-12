"""HTTP-Smoke der neuen Viewer-Endpoints (Discovery/Event-Typen/NER) gegen
einen echten ThreadingHTTPServer auf Ephemeral-Port + tmp-DB."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from marktradar import db, entities, viewer


@pytest.fixture
def server(tmp_path, monkeypatch):
    db_path = str(tmp_path / "viewer.db")
    c = db.connect(db_path)
    db.bootstrap(c)
    entities.seed_event_types(c)
    entities.seed_topics(c)
    c.execute("INSERT INTO pflegeheime(name,traeger,kreis) VALUES ('Haus Test','Caritas','SR X')")
    c.execute("INSERT INTO sources(name,type,url,enabled,discovered,discovered_from) "
              "VALUES ('kandidat','rss','http://kandidat.de/feed',0,1,'kandidat.de')")
    c.commit()
    c.close()
    monkeypatch.setattr(viewer, "DB_PATH", db_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), viewer.Handler)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(base, path):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
        return r.status, json.loads(r.read())


def _post(base, path, payload):
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_index_renders_new_ui(server):
    with urllib.request.urlopen(f"{server}/", timeout=5) as r:
        assert r.status == 200
        html = r.read().decode()
    for marker in ("ENTDECKT", "qdiscbtn", "ettable", "nertable", "mirrorheime"):
        assert marker in html


def test_event_types_endpoint(server):
    code, d = _get(server, "/api/event_types")
    assert code == 200
    assert {e["name"] for e in d["event_types"]} >= {"insolvenz", "politik"}
    assert len(d["topics"]) == len(entities.SEED_TOPICS)


def test_event_types_add_endpoint(server):
    code, d = _post(server, "/api/event_types/add",
                    {"name": "datenleck", "pattern": "ransomware|datenleck"})
    assert code == 200 and d.get("ok")
    _, d = _get(server, "/api/event_types")
    assert any(e["name"] == "datenleck" for e in d["event_types"])
    _, d = _post(server, "/api/event_types/add", {"name": "kaputt", "pattern": "(["})
    assert "error" in d


def test_sources_include_discovered_flag(server):
    _, d = _get(server, "/api/sources")
    cand = next(s for s in d["sources"] if s["name"] == "kandidat")
    assert cand["discovered"] == 1 and cand["enabled"] == 0
    assert cand["discovered_from"] == "kandidat.de"


def test_ner_pending_and_mirror_endpoints(server):
    code, d = _get(server, "/api/entities/pending")
    assert code == 200 and d["pending"] == []
    code, d = _post(server, "/api/entities/mirror_heime", {})
    assert code == 200 and d["mirrored"] == 1
    code, d = _post(server, "/api/entities/mirror_heime", {})
    assert d["mirrored"] == 0  # idempotent
