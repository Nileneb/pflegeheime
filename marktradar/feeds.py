"""RSS-2.0-Feed des Marktradars: die als relevant klassifizierten Pflege-/Gesundheits-/
Sozialpolitik-News als konsumierbarer Feed — Grundlage fürs Social-/SEO-Projekt
(Feed-Reader, Auto-Posting, Content-Syndication). Read-only, keine externen Calls.

Quelle = `articles` (relevant=1, mit Link). Je Item Kategorien aus den verknüpften
Hashtags. Optionaler `tag`-Filter liefert einen themenspezifischen Feed.
"""
import html
from datetime import datetime, timezone
from email.utils import format_datetime

SITE = "https://pflege.linn.games"
FEED_TITLE = "Pflege-Marktradar"
FEED_DESC = "Kuratierte, als relevant klassifizierte Pflege-, Gesundheits- und Sozialpolitik-News."


def _rfc822(iso: str | None) -> str | None:
    """ISO-8601 → RFC-822 (RSS-pubDate). None bei fehlend/unparsebar."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return format_datetime(dt)
    except (ValueError, TypeError):
        return None


def _esc(s: str | None) -> str:
    return html.escape(s or "", quote=True)


def rss(conn, limit: int = 50, tag: str | None = None, base_url: str = SITE) -> str:
    """RSS-2.0-XML der neuesten relevanten Artikel. `tag` filtert auf ein Hashtag."""
    limit = max(1, min(int(limit), 200))
    params: list = []
    join = ""
    if tag:
        join = ("JOIN article_hashtags ah ON ah.article_id=a.id "
                "JOIN hashtags h ON h.id=ah.hashtag_id AND h.term=?")
        params.append(tag.lstrip("#"))
    rows = conn.execute(
        f"SELECT a.id,a.title,a.summary,a.link,a.published,a.kategorie,a.source_domain "
        f"FROM articles a {join} "
        f"WHERE a.relevant=1 AND a.link IS NOT NULL AND a.link!='' "
        f"ORDER BY a.published DESC LIMIT ?", (*params, limit)).fetchall()

    # Kategorien (Hashtags) je Artikel in einem Schwung holen.
    ids = [r["id"] for r in rows]
    cats: dict = {}
    if ids:
        qm = ",".join("?" * len(ids))
        for cr in conn.execute(
                f"SELECT ah.article_id, h.term FROM article_hashtags ah "
                f"JOIN hashtags h ON h.id=ah.hashtag_id WHERE ah.article_id IN ({qm})", ids):
            cats.setdefault(cr["article_id"], []).append(cr["term"])

    self_url = f"{base_url}/feed.xml" + (f"?tag={html.escape(tag, quote=True)}" if tag else "")
    title = FEED_TITLE + (f" · #{tag}" if tag else "")
    items = []
    for r in rows:
        pub = _rfc822(r["published"])
        cat_xml = "".join(f"<category>{_esc(c)}</category>" for c in cats.get(r["id"], []))
        items.append(
            "<item>"
            f"<title>{_esc(r['title'])}</title>"
            f"<link>{_esc(r['link'])}</link>"
            f'<guid isPermaLink="true">{_esc(r["link"])}</guid>'
            + (f"<pubDate>{pub}</pubDate>" if pub else "")
            + f"<description>{_esc(r['summary'] or r['kategorie'] or '')}</description>"
            + cat_xml
            + "</item>")

    now = format_datetime(datetime.now(timezone.utc))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"><channel>'
        f"<title>{_esc(title)}</title>"
        f"<link>{_esc(base_url)}</link>"
        f'<atom:link href="{_esc(self_url)}" rel="self" type="application/rss+xml"/>'
        f"<description>{_esc(FEED_DESC)}</description>"
        "<language>de-DE</language>"
        f"<lastBuildDate>{now}</lastBuildDate>"
        + "".join(items)
        + "</channel></rss>")
