"""FastMCP-Server: Pflege-Marktradar. Tools delegieren an getestete Module.

Transport: stdio (default) oder streamable-http (für Langdock-Integration) via
  PFLEGE_MCP_TRANSPORT=streamable-http PFLEGE_MCP_HOST=0.0.0.0 PFLEGE_MCP_PORT=8766
"""
import os

from mcp.server.fastmcp import FastMCP, Image

from marktradar import db, ingest, query

mcp = FastMCP("pflege-marktradar")
_conn = db.connect()
db.bootstrap(_conn)


@mcp.tool()
def refresh_news(source_filter: str | None = None, since_days: int = 14,
                 limit: int | None = None) -> dict:
    """Holt frische Artikel aus allen aktiven RSS-Quellen, embedded + klassifiziert sie.
    Gibt Report zurück: {new, embedded, sources, errors}."""
    return ingest.refresh(_conn, source_filter, since_days, limit)


@mcp.tool()
def search_news(query_text: str, limit: int = 20, since_days: int | None = None,
                kategorie: str | None = None, only_relevant: bool = True) -> list[dict]:
    """Semantische + gefilterte Suche über den Newsstrom (Vektor-KNN, bge-m3)."""
    return query.search_news(_conn, query_text, limit, since_days, kategorie, only_relevant)


@mcp.tool()
def list_sources() -> list[dict]:
    """Listet die Quellen-Registry inkl. last_fetched/last_status/enabled."""
    return [dict(r) for r in _conn.execute(
        "SELECT id,name,type,url,tier,region,enabled,last_fetched,last_status "
        "FROM sources ORDER BY tier, name").fetchall()]


@mcp.tool()
def add_source(name: str, url: str, type: str = "rss", tier: int = 1,
               region: str = "DE") -> dict:
    """Registriert eine neue Quelle ohne Code-Änderung."""
    cur = _conn.execute(
        "INSERT OR IGNORE INTO sources(name,type,url,tier,region,enabled) VALUES (?,?,?,?,?,1)",
        (name, type, url, tier, region))
    _conn.commit()
    return {"added": bool(cur.rowcount), "name": name, "url": url}


@mcp.tool()
def search_heime(query_text: str, limit: int = 20) -> list[dict]:
    """Durchsucht die Heim-/Träger-Basis (Name/Träger/Ort)."""
    return query.search_heime(_conn, query_text, limit)


@mcp.tool()
def db_stats() -> dict:
    """Zählungen: Heime, Artikel, relevant, embedded, Quellen."""
    return query.db_stats(_conn)


@mcp.tool()
def get_entity(name: str, limit: int = 10) -> dict | None:
    """Profil einer Entität (Träger/Hersteller/Heim) + jüngste verknüpfte Meldungen."""
    return query.get_entity(_conn, name, limit)


@mcp.tool()
def timeline(name: str, limit: int = 30, event_type: str | None = None) -> list[dict]:
    """Chronologische Meldungs-Timeline einer Entität, optional nach event_type
    (insolvenz/expansion/politik/personalie/produkt/auszeichnung/schliessung)."""
    return query.timeline(_conn, name, limit, event_type)


@mcp.tool()
def list_entities(type: str | None = None, limit: int = 50) -> list[dict]:
    """Entitäten mit Meldungs-Anzahl (meistgenannte zuerst). type-Filter optional."""
    return query.list_entities(_conn, type, limit)


@mcp.tool()
def discourse_topics() -> list[str]:
    """Themen mit synthetisierten Positionen (Eingabe für render_chart)."""
    return query.discourse_topics(_conn)


@mcp.tool()
def render_chart(topic: str) -> Image:
    """Rendert die Diskurs-Positionen eines Themas als SVG (x=Zeit, y=pro/contra,
    Farbe=Position) — server-seitig, kein Browser. Themen via discourse_topics()."""
    svg = query.render_topic_svg(_conn, topic)
    return Image(data=svg.encode("utf-8"), format="svg+xml")


def main():
    transport = os.getenv("PFLEGE_MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.settings.host = os.getenv("PFLEGE_MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.getenv("PFLEGE_MCP_PORT", "8766"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
