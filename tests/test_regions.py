"""Tests für Multi-Region-Fetch (Amerika/Afrika-Abdeckung)."""
from marktradar import hashtags, languages


def test_every_news_region_has_a_centroid():
    missing = {gl for regions in languages.NEWS_REGIONS.values() for gl in regions
               if gl not in hashtags.COUNTRY_CENTROIDS}
    assert not missing, f"NEWS_REGIONS countries ohne COUNTRY_CENTROID: {missing}"


def test_news_region_singular_is_first_of_list():
    for lang, regions in languages.NEWS_REGIONS.items():
        assert languages.NEWS_REGION[lang] == regions[0]


def test_african_and_american_regions_present():
    flat = {gl for r in languages.NEWS_REGIONS.values() for gl in r}
    for cc in ("NG", "KE", "ZA", "SN", "EG", "US", "MX", "BR", "AR"):
        assert cc in flat, f"{cc} fehlt in den Fetch-Regionen"


def test_refresh_fetches_multiple_news_regions(conn, monkeypatch):
    conn.execute("INSERT INTO hashtags(term,color,active,created) "
                 "VALUES ('Pflegereform','#5b8def',1,'2026-01-01')")
    conn.commit()
    monkeypatch.setattr(hashtags, "ensure_translations", lambda *a, **k: {"en": "care reform"})
    seen_gl = []

    def fake_news(term, limit=20, hl="de", gl="DE", ceid="DE:de"):
        seen_gl.append(gl)
        return [{"source": "news", "url": f"http://x/{hl}/{gl}", "author": "O",
                 "content": "c", "location_text": "", "published": None}]

    monkeypatch.setattr(hashtags, "fetch_news", fake_news)
    monkeypatch.setattr(hashtags, "fetch_bluesky", lambda *a, **k: [])
    monkeypatch.setattr(hashtags, "fetch_mastodon", lambda *a, **k: [])
    monkeypatch.setattr(hashtags.time, "sleep", lambda _: None)

    hashtags.refresh(conn, sources=("news",), limit=3)

    # en muss mehrere Länder gefetcht haben (US + afrikanische/amerikanische)
    assert "US" in seen_gl and "NG" in seen_gl and "KE" in seen_gl
    # Posts landen mit dem echten country in der DB
    countries = {r["country"] for r in conn.execute(
        "SELECT DISTINCT country FROM hashtag_posts").fetchall()}
    assert {"US", "NG", "ZA"} <= countries


def test_posts_land_in_correct_hemisphere(conn, monkeypatch):
    conn.execute("INSERT INTO hashtags(term,color,active,created) "
                 "VALUES ('Pflege','#5b8def',1,'2026-01-01')")
    conn.commit()
    monkeypatch.setattr(hashtags, "ensure_translations", lambda *a, **k: {"en": "care"})
    monkeypatch.setattr(hashtags, "fetch_news",
                        lambda term, limit=20, hl="de", gl="DE", ceid="DE:de":
                        [{"source": "news", "url": f"http://x/{gl}", "author": "O",
                          "content": "c", "location_text": "", "published": None}])
    monkeypatch.setattr(hashtags, "fetch_bluesky", lambda *a, **k: [])
    monkeypatch.setattr(hashtags, "fetch_mastodon", lambda *a, **k: [])
    monkeypatch.setattr(hashtags.time, "sleep", lambda _: None)
    hashtags.refresh(conn, sources=("news",), limit=2)
    # US-Post muss westliche Hemisphäre sein
    row = conn.execute("SELECT lat,lon FROM hashtag_posts WHERE country='US'").fetchone()
    assert row["lon"] < -30
