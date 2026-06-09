"""Tests für den RSS-2.0-Feed (Grundlage Social/SEO)."""
import xml.etree.ElementTree as ET

from marktradar import feeds


def _seed(conn):
    conn.execute("INSERT INTO sources(id,name,type,url,tier,region,enabled) "
                 "VALUES (1,'Quelle','rss','https://q.example/feed',1,'DE',1)")
    conn.execute("INSERT INTO hashtags(id,term,color,active) VALUES (1,'Pflegereform','#fff',1)")
    rows = [
        (1, "Reform & <Wandel> in der Pflege", "Zusammenfassung A", "https://a.example/1", "2026-06-08T10:00:00+00:00", 1),
        (2, "Irrelevanter Cookie-Hinweis", "nav", "https://a.example/2", "2026-06-07T10:00:00+00:00", 0),
        (3, "Neues Heim eröffnet", "Eröffnung B", "https://a.example/3", "2026-06-06T10:00:00+00:00", 1),
    ]
    for aid, title, summ, link, pub, rel in rows:
        conn.execute(
            "INSERT INTO articles(id,source_id,guid,link,title,summary,published,relevant) "
            "VALUES (?,?,?,?,?,?,?,?)", (aid, 1, f"g{aid}", link, title, summ, pub, rel))
    conn.execute("INSERT INTO article_hashtags(article_id,hashtag_id) VALUES (1,1)")
    conn.commit()


def test_feed_is_valid_rss_xml(conn):
    _seed(conn)
    root = ET.fromstring(feeds.rss(conn))  # wirft bei invalidem XML
    assert root.tag == "rss" and root.get("version") == "2.0"
    chan = root.find("channel")
    assert chan.findtext("title") == "Pflege-Marktradar"
    assert chan.findtext("language") == "de-DE"


def test_only_relevant_articles_included(conn):
    _seed(conn)
    root = ET.fromstring(feeds.rss(conn))
    links = [it.findtext("link") for it in root.iter("item")]
    assert "https://a.example/1" in links and "https://a.example/3" in links
    assert "https://a.example/2" not in links  # relevant=0 → raus


def test_items_have_pubdate_and_escaped_title(conn):
    _seed(conn)
    root = ET.fromstring(feeds.rss(conn))
    first = next(root.iter("item"))
    assert first.findtext("title") == "Reform & <Wandel> in der Pflege"  # korrekt entschärft
    assert first.findtext("pubDate")  # RFC-822 vorhanden
    assert first.find("guid").get("isPermaLink") == "true"


def test_tag_filter_returns_only_tagged(conn):
    _seed(conn)
    root = ET.fromstring(feeds.rss(conn, tag="Pflegereform"))
    links = [it.findtext("link") for it in root.iter("item")]
    assert links == ["https://a.example/1"]  # nur der getaggte relevante Artikel
    cats = [c.text for c in next(root.iter("item")).iter("category")]
    assert "Pflegereform" in cats


def test_limit_is_capped(conn):
    _seed(conn)
    assert feeds.rss(conn, limit=99999).count("<item>") == 2  # nur 2 relevante da
    assert "<item>" not in feeds.rss(conn, limit=0) or feeds.rss(conn, limit=0).count("<item>") <= 1
