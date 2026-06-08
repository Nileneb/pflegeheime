"""FastMCP-Server: Pflege-Marktradar. Tools delegieren an getestete Module.

Transport: stdio (default) oder streamable-http (für Langdock-Integration) via
  PFLEGE_MCP_TRANSPORT=streamable-http PFLEGE_MCP_HOST=0.0.0.0 PFLEGE_MCP_PORT=8088

Auth: OAuth-Resource-Server (RFC 9728). Mit konfiguriertem JWT-Public-Key serviert
FastMCP /.well-known/oauth-protected-resource → Langdock entdeckt den Authorization-
Server (app.linn.games) und führt den OAuth-Flow; die Tokens validiert der
TokenVerifier (RS256) hier. Ohne Key → keine Auth (stdio/dev).
"""
import os

from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from marktradar import auth, db, ingest, query

OAUTH_ISSUER = os.getenv("PFLEGE_OAUTH_ISSUER", "https://app.linn.games")
PUBLIC_URL = os.getenv("PFLEGE_PUBLIC_URL", "https://pflege.linn.games")
_HOST = urlparse(PUBLIC_URL).hostname or "pflege.linn.games"

# WHY: hinter nginx (einziger Ingress, fixes Host-Routing) + OAuth/JWT ist der
# DNS-Rebinding-Schutz (Browser-Angriffsmodell) nicht relevant und blockt sonst
# den proxied Host (421 Invalid Host). Per PFLEGE_DNS_REBIND_PROTECT=1 reaktivierbar.
_TRANSPORT_SECURITY = TransportSecuritySettings(
    enable_dns_rebinding_protection=os.getenv("PFLEGE_DNS_REBIND_PROTECT", "0") == "1",
    allowed_hosts=[_HOST, f"{_HOST}:443", "127.0.0.1", "localhost"],
    allowed_origins=[PUBLIC_URL, f"https://{_HOST}"],
)


class _JWTVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        claims = auth.verify(token)
        if claims is None:
            return None
        raw = claims.get("scope") or claims.get("scopes") or []
        scopes = raw.split() if isinstance(raw, str) else list(raw)
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or claims.get("sub") or "unknown"),
            scopes=scopes,
            expires_at=claims.get("exp"),
            resource=auth.AUDIENCE or None,
        )


_auth_kwargs = {}
if auth.PUBLIC_KEY:  # Prod: OAuth-Resource-Server aktiv
    _auth_kwargs = dict(
        token_verifier=_JWTVerifier(),
        auth=AuthSettings(
            issuer_url=OAUTH_ISSUER,
            resource_server_url=PUBLIC_URL,
            required_scopes=[],
        ),
    )

mcp = FastMCP("pflege-marktradar", transport_security=_TRANSPORT_SECURITY, **_auth_kwargs)
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
        import uvicorn

        # FastMCP serviert OAuth-Resource-Metadata + erzwingt Token-Verifikation;
        # der ASGI-Shim hebt nur ?token= in den Authorization-Header.
        app = auth.QueryTokenASGI(mcp.streamable_http_app())
        uvicorn.run(app, host=os.getenv("PFLEGE_MCP_HOST", "127.0.0.1"),
                    port=int(os.getenv("PFLEGE_MCP_PORT", "8088")))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
