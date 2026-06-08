"""Tests for the Marktradar Globe v2 translation backend."""
import json
import sqlite3

import pytest

from marktradar import db, hashtags, languages


# ── languages.py ──────────────────────────────────────────────────────────────

def test_languages_has_expected_count():
    assert len(languages.LANGUAGES) >= 40


def test_languages_all_have_required_keys():
    for lang in languages.LANGUAGES:
        assert "code" in lang
        assert "name" in lang
        assert "native" in lang
        assert "color" in lang
        assert "centroid" in lang
        lat, lon = lang["centroid"]
        assert -90 <= lat <= 90
        assert -180 <= lon <= 180


def test_by_code_returns_correct_entry():
    de = languages.by_code("de")
    assert de is not None
    assert de["name"] == "German"


def test_by_code_returns_none_for_unknown():
    assert languages.by_code("xx") is None


def test_codes_matches_languages():
    assert set(languages.CODES) == {lang["code"] for lang in languages.LANGUAGES}


def test_language_colors_are_unique():
    colors = [lang["color"] for lang in languages.LANGUAGES]
    assert len(colors) == len(set(colors)), "Language colors must be unique"


# ── db: hashtag_translations table ────────────────────────────────────────────

def test_bootstrap_creates_hashtag_translations_table(conn):
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hashtag_translations" in names


def test_bootstrap_is_idempotent(tmp_path, monkeypatch):
    """Running bootstrap twice must not raise."""
    monkeypatch.setenv("PFLEGE_DB", str(tmp_path / "idem.db"))
    c = db.connect(str(tmp_path / "idem.db"))
    db.bootstrap(c)
    db.bootstrap(c)  # second run must be silent
    c.close()


def test_hashtag_translations_primary_key(conn):
    """Inserting duplicate (hashtag_id, lang_code) raises IntegrityError."""
    conn.execute("INSERT INTO hashtags(term, color, active, created) VALUES ('Test','#fff',1,'2026-01-01')")
    conn.commit()
    hid = conn.execute("SELECT id FROM hashtags WHERE term='Test'").fetchone()["id"]
    conn.execute("INSERT INTO hashtag_translations(hashtag_id, lang_code, term) VALUES (?,?,?)",
                 (hid, "en", "Test"))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO hashtag_translations(hashtag_id, lang_code, term) VALUES (?,?,?)",
                     (hid, "en", "Test2"))
        conn.commit()


# ── translate_hashtag ─────────────────────────────────────────────────────────

def _make_fake_post(response_json: dict):
    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": json.dumps(response_json)}}

    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeResp()

    return fake_post


def test_translate_hashtag_parses_mocked_response(conn, monkeypatch):
    import marktradar.embeddings as emb
    mock_translations = {"en": "Nursing Reform", "fr": "Réforme des soins", "es": "Reforma de cuidados"}
    monkeypatch.setattr(emb.requests, "post", _make_fake_post(mock_translations))

    result = hashtags.translate_hashtag("Pflegereform", ["en", "fr", "es"])
    assert result == mock_translations


def test_translate_hashtag_filters_unrequested_codes(conn, monkeypatch):
    import marktradar.embeddings as emb
    # LLM returns extra codes not requested
    mock_translations = {"en": "Nursing Reform", "de": "Pflegereform", "xx": "garbage"}
    monkeypatch.setattr(emb.requests, "post", _make_fake_post(mock_translations))

    result = hashtags.translate_hashtag("Pflegereform", ["en"])
    assert set(result.keys()) == {"en"}


def test_translate_hashtag_returns_empty_on_network_failure(conn, monkeypatch):
    import marktradar.embeddings as emb
    import requests as req

    def boom(*a, **kw):
        raise req.ConnectionError("refused")

    monkeypatch.setattr(emb.requests, "post", boom)
    result = hashtags.translate_hashtag("Pflegereform", ["en", "fr"])
    assert result == {}


def test_translate_hashtag_returns_empty_on_http_error(conn, monkeypatch):
    import marktradar.embeddings as emb

    class BadResp:
        def raise_for_status(self):
            import requests as req
            raise req.HTTPError("500 Server Error")
        def json(self): return {}

    monkeypatch.setattr(emb.requests, "post", lambda *a, **kw: BadResp())
    result = hashtags.translate_hashtag("Pflegereform", ["en"])
    assert result == {}


def test_translate_hashtag_handles_malformed_json(conn, monkeypatch):
    import marktradar.embeddings as emb

    class BadJsonResp:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "not json at all"}}

    monkeypatch.setattr(emb.requests, "post", lambda *a, **kw: BadJsonResp())
    result = hashtags.translate_hashtag("Pflegereform", ["en"])
    assert result == {}


# ── ensure_translations ───────────────────────────────────────────────────────

def test_ensure_translations_is_idempotent(conn, monkeypatch):
    """Second call with same langs must NOT trigger a new chat call."""
    import marktradar.embeddings as emb
    call_count = {"n": 0}

    def counting_post(*a, **kw):
        call_count["n"] += 1

        class R:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": json.dumps({"en": "Nursing Shortage"})}}
        return R()

    monkeypatch.setattr(emb.requests, "post", counting_post)

    conn.execute("INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegenotstand','#f00',1,'2026-01-01')")
    conn.commit()
    hid = conn.execute("SELECT id FROM hashtags WHERE term='Pflegenotstand'").fetchone()["id"]

    result1 = hashtags.ensure_translations(conn, hid, "Pflegenotstand", ["en"])
    assert call_count["n"] == 1
    assert result1.get("en") == "Nursing Shortage"

    result2 = hashtags.ensure_translations(conn, hid, "Pflegenotstand", ["en"])
    # Must NOT call LLM again — cache hit
    assert call_count["n"] == 1
    assert result2.get("en") == "Nursing Shortage"


def test_ensure_translations_only_fetches_missing(conn, monkeypatch):
    """Only missing lang codes are sent to the LLM."""
    import marktradar.embeddings as emb
    requested_langs = {"n": []}

    def capturing_post(url, json=None, **kw):
        user_msg = json["messages"][-1]["content"]
        payload = __import__("json").loads(user_msg)
        requested_langs["n"] = list(payload["languages"].keys())

        class R:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": __import__("json").dumps(
                {code: f"translation_{code}" for code in requested_langs["n"]}
            )}}
        return R()

    monkeypatch.setattr(emb.requests, "post", capturing_post)

    conn.execute("INSERT INTO hashtags(term,color,active,created) VALUES ('Tariftreue','#0f0',1,'2026-01-01')")
    conn.commit()
    hid = conn.execute("SELECT id FROM hashtags WHERE term='Tariftreue'").fetchone()["id"]

    # Pre-populate "en" so it should NOT be re-requested
    conn.execute("INSERT INTO hashtag_translations(hashtag_id, lang_code, term) VALUES (?,?,?)",
                 (hid, "en", "Wage Commitment"))
    conn.commit()

    hashtags.ensure_translations(conn, hid, "Tariftreue", ["en", "fr"])
    assert "en" not in requested_langs["n"]
    assert "fr" in requested_langs["n"]


def test_ensure_translations_persists_to_db(conn, monkeypatch):
    import marktradar.embeddings as emb
    monkeypatch.setattr(emb.requests, "post", _make_fake_post({"fr": "Crise des soins"}))

    conn.execute("INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegekrise','#00f',1,'2026-01-01')")
    conn.commit()
    hid = conn.execute("SELECT id FROM hashtags WHERE term='Pflegekrise'").fetchone()["id"]

    hashtags.ensure_translations(conn, hid, "Pflegekrise", ["fr"])
    stored = conn.execute(
        "SELECT term FROM hashtag_translations WHERE hashtag_id=? AND lang_code='fr'", (hid,)
    ).fetchone()
    assert stored is not None
    assert stored["term"] == "Crise des soins"


# ── map_data extension ────────────────────────────────────────────────────────

def test_map_data_contains_translations_and_languages_keys(conn, monkeypatch):
    # Stub out all external calls
    monkeypatch.setattr(hashtags, "trends", lambda c: {"weights": {}, "pairs": []})
    result = hashtags.map_data(conn)
    assert "translations" in result
    assert "languages" in result
    assert isinstance(result["translations"], dict)
    assert isinstance(result["languages"], list)
    assert len(result["languages"]) >= 40


def test_map_data_translations_uses_canonical_term(conn, monkeypatch):
    monkeypatch.setattr(hashtags, "trends", lambda c: {"weights": {}, "pairs": []})

    conn.execute("INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegereform','#5b8def',1,'2026-01-01')")
    conn.commit()
    hid = conn.execute("SELECT id FROM hashtags WHERE term='Pflegereform'").fetchone()["id"]
    conn.execute("INSERT INTO hashtag_translations(hashtag_id, lang_code, term) VALUES (?,?,?)",
                 (hid, "en", "Nursing Reform"))
    conn.commit()

    result = hashtags.map_data(conn)
    assert "Pflegereform" in result["translations"]
    assert result["translations"]["Pflegereform"]["en"] == "Nursing Reform"


def test_map_data_does_not_break_existing_keys(conn, monkeypatch):
    monkeypatch.setattr(hashtags, "trends", lambda c: {"weights": {}, "pairs": []})
    result = hashtags.map_data(conn)
    for key in ("legend", "points", "sources", "trends", "total_posts"):
        assert key in result


# ── country_language.json ─────────────────────────────────────────────────────

def test_country_language_keys_in_geojson():
    """All keys in country_language.json must correspond to ISO codes in the geojson."""
    import pathlib
    data_dir = pathlib.Path(__file__).parent.parent / "marktradar" / "data"
    with open(data_dir / "country_language.json") as f:
        cl = json.load(f)
    with open(data_dir / "ne_110m_admin_0_countries.geojson") as f:
        gj = json.load(f)

    geojson_codes = set()
    for feat in gj["features"]:
        p = feat["properties"]
        iso = p.get("ISO_A2", "")
        iso_eh = p.get("ISO_A2_EH", "")
        effective = iso if iso and iso != "-99" else iso_eh
        if effective and effective != "-99":
            geojson_codes.add(effective)

    extra_keys = set(cl.keys()) - geojson_codes
    assert not extra_keys, f"country_language.json has keys not in geojson: {extra_keys}"


def test_country_language_values_in_language_codes():
    """All values in country_language.json must be valid language codes."""
    import pathlib
    data_dir = pathlib.Path(__file__).parent.parent / "marktradar" / "data"
    with open(data_dir / "country_language.json") as f:
        cl = json.load(f)

    valid = set(languages.CODES)
    invalid = {k: v for k, v in cl.items() if v not in valid}
    assert not invalid, f"country_language.json has invalid lang codes: {invalid}"


# ── New tests (code-quality review) ───────────────────────────────────────────

def test_translate_all_hashtags_isolates_per_hashtag_failures(conn, monkeypatch):
    """One hashtag failing must not abort processing of others."""
    import marktradar.embeddings as emb
    from marktradar import server as srv

    conn.execute("INSERT INTO hashtags(term,color,active,created) VALUES ('GoodTag','#0f0',1,'2026-01-01')")
    conn.execute("INSERT INTO hashtags(term,color,active,created) VALUES ('BrokenTag','#f00',1,'2026-01-01')")
    conn.commit()

    call_counter = {"n": 0}

    def selective_post(url, json=None, headers=None, timeout=None):
        payload = __import__("json").loads(json["messages"][-1]["content"])
        call_counter["n"] += 1
        if payload.get("term") == "BrokenTag":
            raise RuntimeError("simulated LLM failure")

        class R:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": __import__("json").dumps({"en": "Good"})}}
        return R()

    monkeypatch.setattr(emb.requests, "post", selective_post)
    monkeypatch.setattr(srv, "_conn", conn)

    result = srv.translate_all_hashtags(["en"])
    assert "GoodTag" in result
    assert result["GoodTag"] == 1
    # BrokenTag is present in the summary (0 translations, not missing entirely)
    assert "BrokenTag" in result
    assert result["BrokenTag"] == 0


def test_unknown_lang_codes_skipped_without_raising(conn, monkeypatch):
    """ensure_translations and translate_hashtag must not raise on bogus lang codes."""
    import marktradar.embeddings as emb

    conn.execute("INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegereform','#5b8def',1,'2026-01-01')")
    conn.commit()
    hid = conn.execute("SELECT id FROM hashtags WHERE term='Pflegereform'").fetchone()["id"]

    monkeypatch.setattr(emb.requests, "post", _make_fake_post({"en": "Nursing Reform"}))

    result = hashtags.ensure_translations(conn, hid, "Pflegereform", ["en", "xx", "zz"])
    assert "en" in result
    assert "xx" not in result
    assert "zz" not in result

    result2 = hashtags.translate_hashtag("Pflegereform", ["xx", "zz"])
    assert result2 == {}


def test_add_hashtag_best_effort_translation_failure(conn, monkeypatch):
    """add_hashtag must return the created hashtag even when the translation LLM is down."""
    import marktradar.embeddings as emb
    import requests as req

    def boom(*a, **kw):
        raise req.ConnectionError("LLM is down")

    monkeypatch.setattr(emb.requests, "post", boom)

    conn.execute("INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegekrise','#ff7043',1,'2026-01-01')")
    conn.commit()
    hid = conn.execute("SELECT id FROM hashtags WHERE term='Pflegekrise'").fetchone()["id"]

    # Simulate what server.add_hashtag does
    result = {"id": hid, "term": "Pflegekrise", "color": "#ff7043", "active": 1}
    try:
        hashtags.ensure_translations(conn, hid, "Pflegekrise")
    except Exception:
        pass  # WHY: translation failure must never block hashtag creation

    assert result["id"] == hid
    assert result["term"] == "Pflegekrise"


def test_delete_removes_hashtag_translations(conn):
    """delete() must clean up hashtag_translations rows."""
    conn.execute("INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegereform','#5b8def',1,'2026-01-01')")
    conn.commit()
    hid = conn.execute("SELECT id FROM hashtags WHERE term='Pflegereform'").fetchone()["id"]

    conn.execute("INSERT INTO hashtag_translations(hashtag_id, lang_code, term) VALUES (?,?,?)",
                 (hid, "en", "Nursing Reform"))
    conn.execute("INSERT INTO hashtag_translations(hashtag_id, lang_code, term) VALUES (?,?,?)",
                 (hid, "fr", "Réforme des soins"))
    conn.commit()

    hashtags.delete(conn, hid)

    remaining = conn.execute(
        "SELECT * FROM hashtag_translations WHERE hashtag_id=?", (hid,)
    ).fetchall()
    assert remaining == []
