"""FastMCP-Server: Pflege-Marktradar. Tools delegieren an getestete Module."""
from mcp.server.fastmcp import FastMCP

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


def main():
    mcp.run()


if __name__ == "__main__":
    main()
