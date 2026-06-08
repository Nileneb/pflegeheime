"""Tests for v2.1-T2 cross-language connection net (hashtags.connections)."""
import pytest
from marktradar import hashtags


def _add_hashtag(conn, term, color):
    conn.execute(
        "INSERT INTO hashtags(term,color,active,created) VALUES (?,?,1,'2026-01-01')",
        (term, color),
    )
    conn.commit()
    return conn.execute("SELECT id FROM hashtags WHERE term=?", (term,)).fetchone()["id"]


def _add_post(conn, hid, lat, lon, country, lang, url):
    conn.execute(
        "INSERT INTO hashtag_posts(hashtag_id,source,url,lat,lon,country,lang_code,published) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (hid, "news", url, lat, lon, country, lang, "2026-01-01"),
    )
    conn.commit()


# ── Part 1: concept-across-regions ───────────────────────────────────────────

def test_concept_arc_between_two_countries(conn):
    hid = _add_hashtag(conn, "Pflegereform", "#5b8def")
    # Germany cluster (3 posts) and USA cluster (2 posts)
    _add_post(conn, hid, 52.5, 13.4, "DE", "de", "u1")
    _add_post(conn, hid, 51.0, 10.0, "DE", "de", "u2")
    _add_post(conn, hid, 50.0, 9.0, "DE", "de", "u3")
    _add_post(conn, hid, 40.7, -74.0, "US", "en", "u4")
    _add_post(conn, hid, 38.9, -77.0, "US", "en", "u5")

    res = hashtags.connections(conn)
    concept = [a for a in res["arcs"] if a["kind"] == "concept"]
    assert len(concept) == 1
    arc = concept[0]
    assert arc["color"] == "#5b8def"
    # n = min(hub=DE(3), spoke=US(2)) = 2
    assert arc["n"] == 2
    # Hub is the densest region (DE). Endpoints near the two regional centroids.
    assert abs(arc["a_lat"] - 51.0) < 3  # DE centroid
    assert abs(arc["b_lat"] - 39.5) < 3  # US centroid


def test_single_region_concept_emits_no_arc(conn):
    hid = _add_hashtag(conn, "Tariftreue", "#2ecc71")
    _add_post(conn, hid, 52.5, 13.4, "DE", "de", "s1")
    _add_post(conn, hid, 51.0, 10.0, "DE", "de", "s2")

    res = hashtags.connections(conn)
    assert [a for a in res["arcs"] if a["kind"] == "concept"] == []


def test_posts_without_geo_are_ignored(conn):
    hid = _add_hashtag(conn, "Pflegenotstand", "#ff4d4d")
    _add_post(conn, hid, 52.5, 13.4, "DE", "de", "g1")
    # second "region" only via a null-geo post → must not count
    conn.execute(
        "INSERT INTO hashtag_posts(hashtag_id,source,url,lat,lon,country,lang_code) "
        "VALUES (?,?,?,?,?,?,?)",
        (hid, "news", "g2", None, None, "US", "en"),
    )
    conn.commit()
    res = hashtags.connections(conn)
    assert [a for a in res["arcs"] if a["kind"] == "concept"] == []


def test_region_fallback_to_lang_code(conn):
    """Posts with NULL country but distinct lang_code still form two regions."""
    hid = _add_hashtag(conn, "Personalmangel", "#9b6dff")
    _add_post(conn, hid, 51.0, 10.0, None, "de", "f1")
    _add_post(conn, hid, 35.0, 135.0, None, "ja", "f2")
    res = hashtags.connections(conn)
    assert len([a for a in res["arcs"] if a["kind"] == "concept"]) == 1


# ── Part 2: cluster-set-matching ─────────────────────────────────────────────

def test_cluster_arc_for_cooccurring_hashtags(conn, monkeypatch):
    a_id = _add_hashtag(conn, "Pflegereform", "#5b8def")
    b_id = _add_hashtag(conn, "Pflegenotstand", "#ff4d4d")
    # Both hashtags present in DE and JP → cluster co-present in 2 regions.
    _add_post(conn, a_id, 51.0, 10.0, "DE", "de", "ca1")
    _add_post(conn, b_id, 52.0, 13.0, "DE", "de", "cb1")
    _add_post(conn, a_id, 35.0, 135.0, "JP", "ja", "ca2")
    _add_post(conn, b_id, 36.0, 138.0, "JP", "ja", "cb2")

    # Force a strong co-occurrence pair without needing German articles.
    monkeypatch.setattr(hashtags, "trends", lambda c: {
        "pairs": [{"a": "Pflegereform", "b": "Pflegenotstand",
                   "ca": "#5b8def", "cb": "#ff4d4d", "n": 5}],
    })

    res = hashtags.connections(conn)
    cluster = [a for a in res["arcs"] if a["kind"] == "cluster"]
    assert len(cluster) == 1
    arc = cluster[0]
    # color is a blend of the two member colors (not equal to either member)
    assert arc["color"] not in ("#5b8def", "#ff4d4d")
    assert arc["n"] >= 1


def test_cluster_in_single_region_emits_no_cluster_arc(conn, monkeypatch):
    a_id = _add_hashtag(conn, "Pflegereform", "#5b8def")
    b_id = _add_hashtag(conn, "Pflegenotstand", "#ff4d4d")
    _add_post(conn, a_id, 51.0, 10.0, "DE", "de", "x1")
    _add_post(conn, b_id, 52.0, 13.0, "DE", "de", "x2")
    monkeypatch.setattr(hashtags, "trends", lambda c: {
        "pairs": [{"a": "Pflegereform", "b": "Pflegenotstand",
                   "ca": "#5b8def", "cb": "#ff4d4d", "n": 5}],
    })
    res = hashtags.connections(conn)
    assert [a for a in res["arcs"] if a["kind"] == "cluster"] == []


# ── Caps & truncation ────────────────────────────────────────────────────────

def test_cap_and_truncation_signalled(conn, monkeypatch):
    # Many hashtags, each spanning 2 regions → many concept arcs, exceed a tiny cap.
    for i in range(20):
        hid = _add_hashtag(conn, f"Tag{i}", "#5b8def")
        _add_post(conn, hid, 51.0, 10.0, "DE", "de", f"a{i}")
        _add_post(conn, hid, 40.0, -74.0, "US", "en", f"b{i}")
    monkeypatch.setattr(hashtags, "trends", lambda c: {"pairs": []})

    res = hashtags.connections(conn, cap=5)
    assert len(res["arcs"]) <= 5
    assert res["truncated"] == 20 - 5  # 20 concept arcs, 5 kept


def test_sorted_by_n_descending(conn, monkeypatch):
    hid = _add_hashtag(conn, "Big", "#5b8def")
    for i in range(5):
        _add_post(conn, hid, 51.0, 10.0, "DE", "de", f"de{i}")
    _add_post(conn, hid, 40.0, -74.0, "US", "en", "us1")
    hid2 = _add_hashtag(conn, "Small", "#ff4d4d")
    _add_post(conn, hid2, 51.0, 10.0, "DE", "de", "sde1")
    _add_post(conn, hid2, 40.0, -74.0, "US", "en", "sus1")
    monkeypatch.setattr(hashtags, "trends", lambda c: {"pairs": []})

    res = hashtags.connections(conn)
    ns = [a["n"] for a in res["arcs"]]
    assert ns == sorted(ns, reverse=True)


# ── map_data integration ─────────────────────────────────────────────────────

def test_map_data_includes_connections_and_prior_keys(conn, monkeypatch):
    hid = _add_hashtag(conn, "Pflegereform", "#5b8def")
    _add_post(conn, hid, 51.0, 10.0, "DE", "de", "m1")
    _add_post(conn, hid, 40.0, -74.0, "US", "en", "m2")
    monkeypatch.setattr(hashtags, "trends", lambda c: {
        "weights": {hid: {"term": "Pflegereform", "color": "#5b8def",
                          "count": 0, "cooc": 0, "weight": 0}},
        "pairs": [],
    })

    data = hashtags.map_data(conn)
    for key in ("legend", "points", "sources", "trends", "translations",
                "languages", "total_posts", "connections", "connections_truncated"):
        assert key in data, f"map_data missing key {key!r}"
    assert any(a["kind"] == "concept" for a in data["connections"])


# ── Geo-bucket fallback region ────────────────────────────────────────────────

def test_geo_bucket_fallback_emits_concept_arc(conn, monkeypatch):
    """Posts with NULL country AND NULL lang_code fall through to the 10°-geo-bucket.
    Two distinct buckets for the same hashtag → a concept arc must be emitted."""
    monkeypatch.setattr(hashtags, "trends", lambda c: {"pairs": []})
    hid = _add_hashtag(conn, "Geopflegung", "#aabbcc")
    # bucket g:5,1  (lat=51, lon=10 → int(51//10)=5, int(10//10)=1)
    conn.execute(
        "INSERT INTO hashtag_posts(hashtag_id,source,url,lat,lon,country,lang_code,published) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (hid, "news", "geo1", 51.0, 10.0, None, None, "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO hashtag_posts(hashtag_id,source,url,lat,lon,country,lang_code,published) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (hid, "news", "geo2", 53.0, 12.0, None, None, "2026-01-01"),
    )
    # bucket g:3,-8  (lat=35, lon=-74 → int(35//10)=3, int(-74//10)=-8)
    conn.execute(
        "INSERT INTO hashtag_posts(hashtag_id,source,url,lat,lon,country,lang_code,published) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (hid, "news", "geo3", 35.0, -74.0, None, None, "2026-01-01"),
    )
    conn.commit()

    res = hashtags.connections(conn)
    concept = [a for a in res["arcs"] if a["kind"] == "concept"]
    assert len(concept) == 1, f"expected 1 concept arc via geo-bucket, got {concept}"


# ── trends_result bypasses internal trends() call ─────────────────────────────

def test_connections_skips_trends_when_precomputed(conn, monkeypatch):
    """connections(conn, trends_result=tr) must not call trends() internally."""
    call_count = {"n": 0}

    def _spy_trends(c):
        call_count["n"] += 1
        raise AssertionError("trends() called internally despite trends_result being supplied")

    monkeypatch.setattr(hashtags, "trends", _spy_trends)

    precomputed = {"pairs": []}
    # Should not raise even though monkeypatched trends() would raise.
    result = hashtags.connections(conn, trends_result=precomputed)
    assert call_count["n"] == 0
    assert "arcs" in result
