"""Tests für Geo-Placement (Institution > Gazetteer > breiter Centroid),
regeocode und die Score-Anreicherung in map_data."""
from datetime import datetime, timezone

from marktradar import hashtags, ranking


def _dist(a, b):
    return abs(a[0] - b[0]), abs(a[1] - b[1])


# ── _place_post ──────────────────────────────────────────────────────────────
def test_institution_placed_at_exact_seat():
    post = {"url": "https://rki.de/news/1", "author": "RKI", "location_text": "RKI"}
    lat, lon = hashtags._place_post(post, hashtags.DE_CENTER)
    dlat, dlon = _dist((lat, lon), (52.52, 13.40))
    assert dlat <= 0.6 and dlon <= 0.6  # eng um den echten Sitz


def test_gazetteer_hit_placed_near_city():
    post = {"url": "u1", "author": "Lokalzeitung", "location_text": "Hamburg Redaktion"}
    lat, lon = hashtags._place_post(post, hashtags.DE_CENTER)
    dlat, dlon = _dist((lat, lon), (53.55, 9.99))
    assert dlat <= 1.5 and dlon <= 1.5


def test_country_placed_before_language_centroid():
    # US-Post (Englisch) muss nach Nordamerika, NICHT auf den en-Centroid (London)
    post = {"url": "https://example.com/us1", "author": "Local Outlet", "location_text": ""}
    lat, lon = hashtags._place_post(post, (51.5, -0.12), country="US")  # en-Centroid = London
    assert lon < -30, f"US post lon {lon} should be western hemisphere, not Europe"
    dlat, dlon = _dist((lat, lon), hashtags.COUNTRY_CENTROIDS["US"])
    assert dlat <= 4.0 and dlon <= 4.0


def test_brazil_post_lands_in_south_america():
    post = {"url": "u-br", "author": "Folha", "location_text": "", "country": "BR"}
    lat, lon = hashtags._place_post(post, (39.4, -8.2))  # pt-Centroid = Portugal
    assert lat < 0 and lon < -30, f"BR post {lat},{lon} should be South America"


def test_centroid_fallback_spreads_wide():
    centroid = (51.16, 10.45)
    post = {"url": "https://bsky.app/x/9", "author": "rando", "location_text": ""}
    lat, lon = hashtags._place_post(post, centroid)
    dlat, dlon = _dist((lat, lon), centroid)
    assert dlat <= hashtags.CENTROID_SPREAD and dlon <= hashtags.CENTROID_SPREAD
    assert hashtags.CENTROID_SPREAD > 1.5  # breiter als der alte enge Jitter


def _add_post(conn, hid, **kw):
    cols = {"hashtag_id": hid, "source": "news", "url": "u", "author": "", "content": "",
            "location_text": "", "lat": None, "lon": None, "published": "2026-06-01",
            "fetched_at": "2026-06-01", "lang_code": "de", "country": "DE"}
    cols.update(kw)
    conn.execute(
        "INSERT INTO hashtag_posts(hashtag_id,source,url,author,content,location_text,"
        "lat,lon,published,fetched_at,lang_code,country) VALUES "
        "(:hashtag_id,:source,:url,:author,:content,:location_text,:lat,:lon,"
        ":published,:fetched_at,:lang_code,:country)", cols)
    conn.commit()


# ── regeocode ────────────────────────────────────────────────────────────────
def test_regeocode_places_unlocated_posts(conn):
    conn.execute("INSERT INTO hashtags(id,term,color,active) VALUES (1,'Pflege','#fff',1)")
    _add_post(conn, 1, url="https://rki.de/a", author="RKI", location_text="RKI", lat=None, lon=None)
    _add_post(conn, 1, url="https://bsky.app/b", author="x", location_text="", lang_code="ar", lat=None, lon=None)
    res = hashtags.regeocode(conn, only_missing=True)
    assert res["regeocoded"] == 2
    r = conn.execute("SELECT lat,lon FROM hashtag_posts WHERE url='https://rki.de/a'").fetchone()
    assert abs(r["lat"] - 52.52) <= 0.6  # Institution exakt verortet


# ── map_data Anreicherung ────────────────────────────────────────────────────
def test_map_data_points_carry_score_and_sources_sorted(conn):
    conn.execute("INSERT INTO hashtags(id,term,color,active) VALUES (1,'Pflege','#fff',1)")
    _add_post(conn, 1, url="https://rki.de/a", author="RKI", location_text="RKI",
              lang_code="de", lat=52.5, lon=13.4)
    _add_post(conn, 1, url="https://bsky.app/b", author="rando", location_text="",
              source="bluesky", lang_code="ar", lat=25.0, lon=45.0)
    data = hashtags.map_data(conn)
    assert data["points"], "points should be present"
    for pt in data["points"]:
        assert "score" in pt and "trust" in pt and "lang_tier" in pt
    srcs = data["sources"][1]
    # deutsche Institution muss vor fremdsprachigem Social-Post stehen
    assert srcs[0]["url"] == "https://rki.de/a"
    assert srcs[0]["score"] >= srcs[-1]["score"]
    assert srcs[0]["trust"] == "institution"
