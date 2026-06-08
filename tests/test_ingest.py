from pathlib import Path
from marktradar import ingest

FIXTURE = Path(__file__).parent / "fixtures" / "sample_feed.xml"


def test_parse_feed_extracts_items():
    items = ingest.parse_feed(FIXTURE.read_bytes())
    assert len(items) == 2
    first = items[0]
    assert first["title"].startswith("Caritas eröffnet")
    assert first["guid"] == "https://example.org/news/caritas-aachen"
    assert first["published"].startswith("2026-06-02")  # ISO string


def test_classify_detects_job_deterministically():
    rel, kat, grund = ingest.classify("Pflegefachkraft (m/w/d) gesucht", "Wir suchen")
    assert rel is True
    assert kat == "Stellenanzeige"
