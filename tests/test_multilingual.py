"""Tests for T1 multilingual source fetching."""
import json
import sqlite3

import pytest

from marktradar import db, hashtags, languages


# ── languages.py new constants ────────────────────────────────────────────────

def test_fetch_langs_are_subset_of_codes():
    valid = set(languages.CODES)
    for code in languages.FETCH_LANGS:
        assert code in valid, f"FETCH_LANGS contains unknown code: {code!r}"


def test_fetch_langs_has_expected_entries():
    assert "en" in languages.FETCH_LANGS
    assert "ja" in languages.FETCH_LANGS
    assert len(languages.FETCH_LANGS) == 12


def test_news_region_keys_match_fetch_langs():
    assert set(languages.NEWS_REGION.keys()) == set(languages.FETCH_LANGS)


def test_news_region_values_are_two_char_uppercase():
    for lang, region in languages.NEWS_REGION.items():
        assert len(region) == 2 and region.isupper(), (
            f"NEWS_REGION[{lang!r}] = {region!r} is not a 2-char uppercase code"
        )


# ── db.py migration — lang_code + country columns ────────────────────────────

def test_migration_adds_lang_code_and_country(tmp_path, monkeypatch):
    """Bootstrap on a fresh DB must create lang_code and country columns."""
    monkeypatch.setenv("PFLEGE_DB", str(tmp_path / "m.db"))
    conn = db.connect(str(tmp_path / "m.db"))
    db.bootstrap(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(hashtag_posts)")}
    assert "lang_code" in cols
    assert "country" in cols
    conn.close()


def test_migration_is_idempotent(tmp_path, monkeypatch):
    """Running bootstrap twice must not raise on the new columns."""
    monkeypatch.setenv("PFLEGE_DB", str(tmp_path / "idem2.db"))
    conn = db.connect(str(tmp_path / "idem2.db"))
    db.bootstrap(conn)
    db.bootstrap(conn)  # second call must be silent
    conn.close()


def test_migration_on_existing_table_without_columns(tmp_path):
    """Simulate a pre-existing hashtag_posts table that lacks lang_code/country."""
    path = str(tmp_path / "old.db")
    # Create the table WITHOUT the new columns, as prod DB might look.
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE hashtag_posts (
            id INTEGER PRIMARY KEY,
            hashtag_id INTEGER,
            source TEXT, url TEXT, author TEXT, content TEXT,
            location_text TEXT, lat REAL, lon REAL, published TEXT, fetched_at TEXT,
            UNIQUE(hashtag_id, url)
        )
    """)
    conn.commit()
    conn.close()

    # Now bootstrap should add the missing columns without error.
    conn = db.connect(path)
    db.bootstrap(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(hashtag_posts)")}
    assert "lang_code" in cols
    assert "country" in cols

    # Running it again must be safe.
    db.bootstrap(conn)
    conn.close()


# ── fetch_news URL construction ───────────────────────────────────────────────

def test_fetch_news_builds_correct_url_for_german(monkeypatch):
    """Default params must produce the original DE URL."""
    captured = {}

    def fake_get(url, as_json=True):
        captured["url"] = url
        raise RuntimeError("stop after URL capture")

    monkeypatch.setattr(hashtags, "_get", fake_get)
    try:
        hashtags.fetch_news("Pflegereform")
    except RuntimeError:
        pass
    assert "hl=de" in captured["url"]
    assert "gl=DE" in captured["url"]
    assert "ceid=DE:de" in captured["url"]


def test_fetch_news_builds_correct_url_for_english(monkeypatch):
    captured = {}

    def fake_get(url, as_json=True):
        captured["url"] = url
        raise RuntimeError("stop after URL capture")

    monkeypatch.setattr(hashtags, "_get", fake_get)
    try:
        hashtags.fetch_news("nursing reform", hl="en", gl="US", ceid="US:en")
    except RuntimeError:
        pass
    assert "hl=en" in captured["url"]
    assert "gl=US" in captured["url"]
    assert "ceid=US%3Aen" in captured["url"] or "ceid=US:en" in captured["url"]


def test_fetch_news_builds_correct_url_for_japanese(monkeypatch):
    captured = {}

    def fake_get(url, as_json=True):
        captured["url"] = url
        raise RuntimeError("stop")

    monkeypatch.setattr(hashtags, "_get", fake_get)
    try:
        hashtags.fetch_news("介護改革", hl="ja", gl="JP", ceid="JP:ja")
    except RuntimeError:
        pass
    assert "hl=ja" in captured["url"]
    assert "gl=JP" in captured["url"]


# ── fetch_bluesky lang param ──────────────────────────────────────────────────

def test_fetch_bluesky_adds_lang_param_when_provided(monkeypatch):
    captured = {}

    def fake_get(url, as_json=True):
        captured["url"] = url
        return {"posts": []}

    monkeypatch.setattr(hashtags, "_get", fake_get)
    hashtags.fetch_bluesky("Pflegereform", limit=5, lang="fr")
    assert "lang=fr" in captured["url"]


def test_fetch_bluesky_no_lang_param_when_omitted(monkeypatch):
    captured = {}

    def fake_get(url, as_json=True):
        captured["url"] = url
        return {"posts": []}

    monkeypatch.setattr(hashtags, "_get", fake_get)
    hashtags.fetch_bluesky("Pflegereform", limit=5)
    assert "lang=" not in captured["url"]


# ── refresh multilingual behaviour ───────────────────────────────────────────

def _make_post(url, location_text=""):
    return {
        "source": "news", "url": url, "author": "TestNews",
        "content": "test", "location_text": location_text, "published": "2026-01-01",
    }


def test_refresh_fetches_translated_terms(conn, monkeypatch):
    """refresh must call fetchers with translated terms for FETCH_LANGS."""
    conn.execute(
        "INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegereform','#5b8def',1,'2026-01-01')"
    )
    conn.commit()
    hid = conn.execute("SELECT id FROM hashtags WHERE term='Pflegereform'").fetchone()["id"]

    translations = {"en": "Nursing Reform", "fr": "Réforme des soins"}
    called_terms = []

    def fake_ensure(conn_, hashtag_id, term, target_langs=None):
        return translations

    def fake_news(term, limit=20, hl="de", gl="DE", ceid="DE:de"):
        called_terms.append((term, hl, gl))
        return [_make_post(f"http://example.com/{term}/{hl}")]

    def fake_bluesky(term, limit=20, lang=None):
        return []

    def fake_mastodon(term, limit=20, instance="mastodon.social"):
        return []

    monkeypatch.setattr(hashtags, "ensure_translations", fake_ensure)
    monkeypatch.setattr(hashtags, "fetch_news", fake_news)
    monkeypatch.setattr(hashtags, "fetch_bluesky", fake_bluesky)
    monkeypatch.setattr(hashtags, "fetch_mastodon", fake_mastodon)
    monkeypatch.setattr(hashtags.time, "sleep", lambda _: None)

    result = hashtags.refresh(conn, sources=("news",), limit=5)
    assert result["added"] >= 1

    fetched_term_langs = [(t, hl) for t, hl, gl in called_terms]
    # German base term must be fetched
    assert ("Pflegereform", "de") in fetched_term_langs
    # Translated terms must also be fetched
    assert ("Nursing Reform", "en") in fetched_term_langs
    assert ("Réforme des soins", "fr") in fetched_term_langs


def test_refresh_stores_lang_code_and_country(conn, monkeypatch):
    """Posts stored by refresh must have lang_code and country set."""
    conn.execute(
        "INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegenotstand','#ff4d4d',1,'2026-01-01')"
    )
    conn.commit()

    def fake_ensure(conn_, hashtag_id, term, target_langs=None):
        return {"en": "Nursing Shortage"}

    def fake_news(term, limit=20, hl="de", gl="DE", ceid="DE:de"):
        return [_make_post(f"http://example.com/{hl}/story")]

    monkeypatch.setattr(hashtags, "ensure_translations", fake_ensure)
    monkeypatch.setattr(hashtags, "fetch_news", fake_news)
    monkeypatch.setattr(hashtags, "fetch_bluesky", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags, "fetch_mastodon", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags.time, "sleep", lambda _: None)

    hashtags.refresh(conn, sources=("news",), limit=5)

    rows = conn.execute(
        "SELECT lang_code, country FROM hashtag_posts"
    ).fetchall()
    lang_codes = {r["lang_code"] for r in rows}
    countries = {r["country"] for r in rows}

    assert "de" in lang_codes
    assert "en" in lang_codes
    assert "DE" in countries
    assert "US" in countries  # NEWS_REGION["en"]


def test_refresh_partial_translations_still_fetches_german(conn, monkeypatch):
    """If ensure_translations returns empty/partial, German term still gets fetched."""
    conn.execute(
        "INSERT INTO hashtags(term,color,active,created) VALUES ('Insolvenz','#e84393',1,'2026-01-01')"
    )
    conn.commit()

    def fake_ensure(conn_, hashtag_id, term, target_langs=None):
        return {}  # translation completely failed

    called = []

    def fake_news(term, limit=20, hl="de", gl="DE", ceid="DE:de"):
        called.append(term)
        return [_make_post(f"http://example.com/{term}")]

    monkeypatch.setattr(hashtags, "ensure_translations", fake_ensure)
    monkeypatch.setattr(hashtags, "fetch_news", fake_news)
    monkeypatch.setattr(hashtags, "fetch_bluesky", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags, "fetch_mastodon", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags.time, "sleep", lambda _: None)

    result = hashtags.refresh(conn, sources=("news",), limit=5)
    assert "Insolvenz" in called
    assert result["added"] >= 1


def test_refresh_collects_errors_without_aborting(conn, monkeypatch):
    """A failing source for one language must not abort other language/source combos."""
    conn.execute(
        "INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegekrise','#ff7043',1,'2026-01-01')"
    )
    conn.commit()

    def fake_ensure(conn_, hashtag_id, term, target_langs=None):
        return {"en": "Care Crisis"}

    call_n = {"n": 0}

    def selective_news(term, limit=20, hl="de", gl="DE", ceid="DE:de"):
        call_n["n"] += 1
        if hl == "en":
            raise ConnectionError("EN news is down")
        return [_make_post(f"http://example.com/{hl}")]

    monkeypatch.setattr(hashtags, "ensure_translations", fake_ensure)
    monkeypatch.setattr(hashtags, "fetch_news", selective_news)
    monkeypatch.setattr(hashtags, "fetch_bluesky", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags, "fetch_mastodon", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags.time, "sleep", lambda _: None)

    result = hashtags.refresh(conn, sources=("news",), limit=5)

    # German pass must still have added a post
    assert result["added"] >= 1
    # The EN error must be collected, not raised
    assert any("en" in e for e in result["errors"])
    # DE = 1 Region; EN = 6 Regionen (alle werfen) → 1 + 6 Calls, EN-Fehler je Region gesammelt
    assert call_n["n"] == 1 + len(languages.NEWS_REGIONS["en"])


# ── Geolocation fallback uses language centroid ───────────────────────────────

def test_refresh_uses_language_centroid_as_fallback(conn, monkeypatch):
    """When geocode returns None, lat/lon should be near the fetch-language centroid."""
    conn.execute(
        "INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegereform','#5b8def',1,'2026-01-01')"
    )
    conn.commit()

    def fake_ensure(conn_, hashtag_id, term, target_langs=None):
        return {"fr": "Réforme des soins"}

    def fake_news(term, limit=20, hl="de", gl="DE", ceid="DE:de"):
        # location_text deliberately unresolvable by gazetteer
        return [_make_post(f"http://example.com/{hl}", location_text="nowhere-xyz")]

    monkeypatch.setattr(hashtags, "ensure_translations", fake_ensure)
    monkeypatch.setattr(hashtags, "fetch_news", fake_news)
    monkeypatch.setattr(hashtags, "fetch_bluesky", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags, "fetch_mastodon", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags.time, "sleep", lambda _: None)

    hashtags.refresh(conn, sources=("news",), limit=5)

    rows = conn.execute(
        "SELECT lat, lon, lang_code FROM hashtag_posts WHERE lat IS NOT NULL"
    ).fetchall()
    assert rows, "Expected at least one geolocated post"

    fr_centroid = languages.by_code("fr")["centroid"]
    de_centroid = languages.by_code("de")["centroid"]

    tol = hashtags.CENTROID_SPREAD + 0.01  # Fallback streut bewusst land-breit um den Centroid
    for row in rows:
        if row["lang_code"] == "fr":
            assert abs(row["lat"] - fr_centroid[0]) <= tol, (
                f"FR post lat {row['lat']} too far from FR centroid {fr_centroid[0]}"
            )
            assert abs(row["lon"] - fr_centroid[1]) <= tol, (
                f"FR post lon {row['lon']} too far from FR centroid {fr_centroid[1]}"
            )
        elif row["lang_code"] == "de":
            assert abs(row["lat"] - de_centroid[0]) <= tol
            assert abs(row["lon"] - de_centroid[1]) <= tol


def test_de_fallback_still_uses_de_center(conn, monkeypatch):
    """German posts with no geocodable location must fall back to DE_CENTER area."""
    conn.execute(
        "INSERT INTO hashtags(term,color,active,created) VALUES ('Tariftreue','#2ecc71',1,'2026-01-01')"
    )
    conn.commit()

    monkeypatch.setattr(hashtags, "ensure_translations", lambda *a, **kw: {})

    def fake_news(term, limit=20, hl="de", gl="DE", ceid="DE:de"):
        return [_make_post("http://example.com/de", location_text="somewhere-unknown-xyz")]

    monkeypatch.setattr(hashtags, "fetch_news", fake_news)
    monkeypatch.setattr(hashtags, "fetch_bluesky", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags, "fetch_mastodon", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags.time, "sleep", lambda _: None)

    hashtags.refresh(conn, sources=("news",), limit=5)

    row = conn.execute(
        "SELECT lat, lon FROM hashtag_posts WHERE lang_code='de' LIMIT 1"
    ).fetchone()
    assert row is not None
    de_lat, de_lon = hashtags.DE_CENTER
    tol = hashtags.CENTROID_SPREAD + 0.01  # bewusst breitere Streuung als der alte enge Jitter
    assert abs(row["lat"] - de_lat) <= tol
    assert abs(row["lon"] - de_lon) <= tol


# ── sleep placement: once per language, not per source ───────────────────────

def test_sleep_fires_once_per_language_not_per_source(conn, monkeypatch):
    """time.sleep must be called exactly len(lang_terms) times — once per language,
    not once per (language × source)."""
    conn.execute(
        "INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegereform','#5b8def',1,'2026-01-01')"
    )
    conn.commit()

    # Two translated languages → lang_terms = [("de", ...), ("en", ...), ("fr", ...)] = 3
    translations = {"en": "Nursing Reform", "fr": "Réforme des soins"}
    monkeypatch.setattr(hashtags, "ensure_translations", lambda *a, **kw: translations)
    monkeypatch.setattr(hashtags, "fetch_news", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags, "fetch_bluesky", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags, "fetch_mastodon", lambda *a, **kw: [])

    sleep_calls = []
    monkeypatch.setattr(hashtags.time, "sleep", lambda s: sleep_calls.append(s))
    # Ensure _FETCH_DELAY is non-zero so the sleep branch is entered
    monkeypatch.setattr(hashtags, "_FETCH_DELAY", 0.3)

    hashtags.refresh(conn, sources=("mastodon", "bluesky", "news"), limit=5)

    # 1 hashtag × 3 languages (de + en + fr) = 3 sleep calls
    expected_lang_count = 1 + len(translations)  # de + translated
    assert len(sleep_calls) == expected_lang_count, (
        f"Expected {expected_lang_count} sleep calls (once per language), "
        f"got {len(sleep_calls)} — sleep may still be inside the sources loop"
    )


def test_fetch_delay_zero_disables_sleep(conn, monkeypatch):
    """PFLEGE_FETCH_DELAY=0 must result in zero sleep calls."""
    conn.execute(
        "INSERT INTO hashtags(term,color,active,created) VALUES ('Pflegereform','#5b8def',1,'2026-01-01')"
    )
    conn.commit()

    monkeypatch.setattr(hashtags, "ensure_translations",
                        lambda *a, **kw: {"en": "Nursing Reform"})
    monkeypatch.setattr(hashtags, "fetch_news", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags, "fetch_bluesky", lambda *a, **kw: [])
    monkeypatch.setattr(hashtags, "fetch_mastodon", lambda *a, **kw: [])

    sleep_calls = []
    monkeypatch.setattr(hashtags.time, "sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr(hashtags, "_FETCH_DELAY", 0.0)

    hashtags.refresh(conn, sources=("news",), limit=5)

    assert sleep_calls == [], (
        f"Expected no sleep calls when _FETCH_DELAY=0, got {sleep_calls}"
    )
