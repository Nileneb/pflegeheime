"""Tests für die Ranking-Rails (Sprache / Trust / Geo-Präzision)."""
from marktradar import ranking


# ── Rail 1: Sprach-Tiers ─────────────────────────────────────────────────────
def test_german_is_top_tier():
    assert ranking.lang_tier("de") == 0
    assert ranking.readability("de") == 1.0


def test_european_languages_are_middle_tier():
    for code in ("en", "fr", "nl", "pl", "uk"):
        assert ranking.lang_tier(code) == 1, code
    assert 0.0 < ranking.readability("en") < 1.0


def test_far_languages_fall_to_rest_tier():
    for code in ("ar", "ja", "zh", "hi", None, "xx"):
        assert ranking.lang_tier(code) == ranking.REST_TIER, code
    assert ranking.readability("ja") < ranking.readability("en")


# ── Rail 2+3: Institutionen ──────────────────────────────────────────────────
def test_match_institution_returns_coords():
    inst = ranking.match_institution("Pressemitteilung des RKI.de heute")
    assert inst is not None and inst["name"] == "RKI"
    assert inst["lat"] == 52.52 and inst["country"] == "DE"


def test_match_institution_none_for_plain_text():
    assert ranking.match_institution("Karl-Ludwig aus der Kneipe") is None
    assert ranking.match_institution("") is None
    assert ranking.match_institution(None) is None


def test_generic_marker_grants_institution_trust_without_coords():
    post = {"source": "news", "author": "Ärztekammer Westfalen-Lippe", "url": "x", "content": "y"}
    label, weight = ranking.classify_source(post)
    assert label == "institution" and weight == ranking.TRUST_WEIGHT["institution"]
    # generischer Marker hat keinen Sitz → nicht zwingend exakt verortet …
    assert ranking.match_institution(ranking._identity_text(post)) is None


def test_news_outlet_is_press():
    post = {"source": "news", "author": "Tagesschau", "url": "https://tagesschau.de/x"}
    assert ranking.classify_source(post)[0] == "press"


def test_social_post_is_low_trust():
    post = {"source": "mastodon", "author": "randoperson", "url": "https://mastodon.social/@x/1"}
    label, weight = ranking.classify_source(post)
    assert label == "social" and weight == ranking.TRUST_WEIGHT["social"]


# ── Scoring + Ranking ────────────────────────────────────────────────────────
def test_german_institution_scores_higher_than_foreign_social():
    inst_de = {"lang_code": "de", "source": "news", "author": "RKI", "url": "https://rki.de/a",
               "location_text": "RKI", "published": "2026-06-01"}
    social_far = {"lang_code": "ar", "source": "bluesky", "author": "x", "url": "https://bsky.app/x",
                  "location_text": "", "published": "2026-06-02"}
    s_inst = ranking.score_post(inst_de)
    s_soc = ranking.score_post(social_far)
    assert s_inst["score"] > s_soc["score"]
    assert s_inst["trust"] == "institution" and s_inst["geo_precise"] is True
    assert s_soc["trust"] == "social" and s_soc["lang_tier"] == ranking.REST_TIER


def test_score_is_bounded_0_1():
    for p in ({"lang_code": "de", "source": "news", "author": "RKI", "url": "rki.de",
               "location_text": "RKI"},
              {"lang_code": "zh", "source": "mastodon", "author": "", "url": "u", "location_text": ""}):
        assert 0.0 <= ranking.score_post(p)["score"] <= 1.0


def test_source_trust_institution_vs_unverified():
    assert ranking.source_trust("RKI aktuell", "https://www.rki.de/x.xml", "DE") == "institution"
    assert ranking.source_trust("BMG Pressemitteilungen", "https://bundesgesundheitsministerium.de/p.xml") == "institution"
    assert ranking.source_trust("Karl-Ludwigs Pflegeblog", "https://random-blog.example/feed") == "unverified"


def test_source_gate_default_deny():
    # nur verifizierte Institutionen passieren das Gate automatisch
    assert ranking.passes_source_gate("WHO News", "https://www.who.int/feed.xml", "INT") is True
    assert ranking.passes_source_gate("EU-Kommission Presscorner", "https://ec.europa.eu/rss") is True
    assert ranking.passes_source_gate("Irgendein Newsblog", "https://blog.example/rss") is False
    assert ranking.passes_source_gate("", "", "") is False


def test_rank_posts_sorts_readable_trusted_first():
    posts = [
        {"lang_code": "ar", "source": "bluesky", "author": "a", "url": "u1", "published": "2026-06-09"},
        {"lang_code": "de", "source": "news", "author": "BMG Bundesgesundheitsministerium",
         "url": "u2", "location_text": "BMG", "published": "2026-06-01"},
        {"lang_code": "en", "source": "news", "author": "BBC", "url": "u3", "published": "2026-06-05"},
    ]
    ranked = ranking.rank_posts(posts)
    assert ranked[0]["url"] == "u2"          # de + institution → top trotz ältestem Datum
    assert ranked[-1]["url"] == "u1"         # fern + social → unten
    assert ranked[0]["score"] >= ranked[1]["score"] >= ranked[2]["score"]
    # rank_posts liefert Kopien, Original unverändert
    assert "score" not in posts[0]
