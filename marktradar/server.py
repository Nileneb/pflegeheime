"""FastMCP-Server: Pflege-Marktradar. Tools delegieren an getestete Module.

Transport: stdio (default) oder streamable-http (für Langdock-Integration) via
  PFLEGE_MCP_TRANSPORT=streamable-http PFLEGE_MCP_HOST=0.0.0.0 PFLEGE_MCP_PORT=8088

Auth: OAuth-Resource-Server (RFC 9728). Mit konfiguriertem JWT-Public-Key serviert
FastMCP /.well-known/oauth-protected-resource → Langdock entdeckt den Authorization-
Server (app.linn.games) und führt den OAuth-Flow; die Tokens validiert der
TokenVerifier (RS256) hier. Ohne Key → keine Auth (stdio/dev).

Auto-refresh background thread (v2.1-T4):
  PFLEGE_AUTO_REFRESH=1     — enable periodic hashtag refresh (default: 0/off)
  PFLEGE_REFRESH_INTERVAL=1800  — seconds between refresh cycles (default: 1800 = 30 min)
  PFLEGE_REFRESH_LIMIT=15   — per-hashtag post limit per cycle (default: 15)
  PFLEGE_REFRESH_INITIAL_DELAY=60 — seconds to wait before first cycle (default: 60)
Thread runs ONLY in this MCP-server entrypoint (mcp-pflege container), never in viewer.py.
"""
import logging
import os
import threading
import time

from urllib.parse import urlparse

_log = logging.getLogger(__name__)

from mcp.server.fastmcp import FastMCP, Image
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from marktradar import auth, db, hashtags, ingest, organigram, query

INSTRUCTIONS = (
    "Pflege-Marktradar — Markt-Intelligence für den deutschen Pflegemarkt "
    "(News, Diskurs-Positionen, Entitäten, Organigramm). "
    "TOOL-WAHL: Fragen nach dem ORGANIGRAMM / der Organisationsstruktur / dem Aufbau / "
    "den Abteilungen, Bereichen, Einrichtungen oder Personen eines TRÄGERS "
    "(z.B. „Bergische Diakonie“) beantwortest du mit `org_tree` / `org_stats` / "
    "`org_persons` — NICHT mit get_entity oder search_news (die liefern nur News/Entitäten, "
    "kein Org-Diagramm). Träger-Name als `traeger`-Parameter übergeben. "
    "QUELLENANGABE (wichtig): Jede Meldung/Position trägt ein `link`-Feld mit der "
    "ORIGINAL-URL und `source_domain`. Zitiere als Quelle IMMER diese `link`-URL "
    "(bzw. source_domain) — NIEMALS den Tool-Call-Namen oder eine call_*.json-Referenz."
)

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
        if claims and os.getenv("PFLEGE_LOG_CLAIMS") == "1":
            import sys
            print(f"[auth] OK token iss={claims.get('iss')} aud={claims.get('aud')} "
                  f"sub={claims.get('sub')} azp={claims.get('azp')} "
                  f"scope={claims.get('scope')} keys={list(claims.keys())}",
                  file=sys.stderr, flush=True)
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

# WHY: stateless_http → jede Request self-contained, keine Mcp-Session-Id-Affinität.
# Hinter Proxies (Synology), die Session-/Auth-Header strippen, sonst „Created new
# transport" pro Call → Session verloren → Langdock bricht ab. Tools sind zustandslos.
_STATELESS = os.getenv("PFLEGE_STATELESS", "1") == "1"
mcp = FastMCP("pflege-marktradar", instructions=INSTRUCTIONS,
              transport_security=_TRANSPORT_SECURITY,
              stateless_http=_STATELESS, **_auth_kwargs)
_conn = db.connect()
db.bootstrap(_conn)
organigram.seed(_conn)  # Bergische-Diakonie-Organigramm (idempotent)
hashtags.seed(_conn)    # Hashtag-Set (idempotent)


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
    """Rendert die Diskurs-Positionen eines Themas als PNG (x=Zeit, y=pro/contra,
    Farbe=Position) — server-seitig (SVG→PNG via cairosvg, kein Browser, schnell,
    zuverlässig). Themen via discourse_topics()."""
    svg = query.render_topic_svg(_conn, topic, width=1200)
    try:
        import cairosvg
        png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=1200)
        return Image(data=png, format="png")
    except Exception:  # WHY: ohne cairo (dev) → SVG-Fallback, Tool bleibt nutzbar
        return Image(data=svg.encode("utf-8"), format="svg+xml")


@mcp.tool()
def org_tree(traeger: str = "Bergische Diakonie") -> list[dict]:
    """ORGANIGRAMM / Organisationsstruktur / Aufbau eines Trägers (Pflege-Unternehmen,
    z.B. „Bergische Diakonie“): verschachtelter Baum Sektoren → Bereiche → Einrichtungen,
    mit Icon/Farbe je Einheit. Nutze DIESES Tool für jede Frage nach Abteilungen,
    Bereichen, Einrichtungen oder dem Aufbau eines Trägers — nicht get_entity/search_news."""
    return organigram.tree(_conn, traeger)


@mcp.tool()
def org_persons(unit_id: int | None = None, traeger: str = "Bergische Diakonie") -> list[dict]:
    """Personen eines Trägers; mit unit_id rekursiv inkl. aller Untereinheiten."""
    return organigram.persons(_conn, unit_id, traeger)


@mcp.tool()
def org_stats(traeger: str = "Bergische Diakonie") -> dict:
    """Kennzahlen der Org-Struktur: Einheiten, Einrichtungen, Personen."""
    return organigram.stats(_conn, traeger)


@mcp.tool()
def add_org_unit(name: str, parent_id: int | None = None, type: str = "bereich",
                 traeger: str = "Bergische Diakonie", short_name: str | None = None,
                 icon: str | None = None, color: str | None = None) -> dict:
    """Legt eine neue Org-Einheit unter parent_id an (level wird abgeleitet).
    icon = Emoji (z.B. "🏠"), color = Hex (z.B. "#2E8B57"). Ohne color erbt die
    Einheit die Farbe des Parents."""
    return organigram.add_unit(_conn, name, parent_id, type, traeger, short_name, icon, color)


@mcp.tool()
def add_org_person(first_name: str, last_name: str, role: str | None = None,
                   unit_id: int | None = None, email: str | None = None,
                   traeger: str = "Bergische Diakonie") -> dict:
    """Fügt eine Person einer Org-Einheit hinzu."""
    return organigram.add_person(_conn, first_name, last_name, role, unit_id, email,
                                 traeger=traeger)


@mcp.tool()
def list_hashtags() -> list[dict]:
    """Beobachtete Hashtags mit Post-Anzahl + geokodierten Treffern."""
    return hashtags.list_hashtags(_conn)


@mcp.tool()
def add_hashtag(term: str, color: str = "#5b8def") -> dict:
    """Neuen Hashtag zur Beobachtung aufnehmen (CRUD). color = Hex für die Karten-Punkte."""
    result = hashtags.add(_conn, term, color)
    # Best-effort translation at creation time — must not break add if LLM is down.
    try:
        hashtag_id = result.get("id")
        if hashtag_id:
            hashtags.ensure_translations(_conn, hashtag_id, result.get("term", term))
    except Exception:
        pass  # WHY: translation failure must never block hashtag creation
    return result


@mcp.tool()
def translate_all_hashtags(langs: list[str] | None = None) -> dict:
    """Übersetzt alle aktiven Hashtags in die angegebenen (oder alle ~40) Sprachen.
    Nutzt Cache-first: bereits übersetzte Terme werden nicht neu angefragt.
    Unbekannte Sprachcodes werden stillschweigend übersprungen (kein Absturz).
    Gibt {hashtag_term: count_translated} zurück."""
    from marktradar import languages as _langs
    if langs is not None:
        # WHY: user-supplied codes may include unknowns; skip them instead of crashing
        langs = [c for c in langs if _langs.by_code(c) is not None]
    active = _conn.execute(
        "SELECT id, term FROM hashtags WHERE active=1"
    ).fetchall()
    summary: dict[str, int] = {}
    for row in active:
        try:
            translations = hashtags.ensure_translations(
                _conn, row["id"], row["term"], langs
            )
            summary[row["term"]] = len(translations)
        except Exception as exc:  # WHY: one hashtag failing must not abort the whole batch (matches refresh() pattern)
            _log.warning("translate_all_hashtags: skipping %r: %s: %s",
                         row["term"], type(exc).__name__, exc)
    return summary


@mcp.tool()
def update_hashtag(hashtag_id: int, term: str | None = None, color: str | None = None,
                   active: bool | None = None) -> dict:
    """Hashtag ändern: Begriff, Farbe oder aktiv/inaktiv schalten."""
    return hashtags.update(_conn, hashtag_id, term, color, active)


@mcp.tool()
def delete_hashtag(hashtag_id: int) -> dict:
    """Hashtag (samt aggregierter Posts) löschen."""
    return hashtags.delete(_conn, hashtag_id)


@mcp.tool()
def refresh_hashtags(sources: list[str] | None = None, limit: int = 20) -> dict:
    """Aggregiert echte Posts/Treffer je aktivem Hashtag aus öffentlichen Quellen
    (mastodon, bluesky, news) und geokodiert sie für den Globus. Jeder Treffer trägt
    seine ORIGINAL-URL. sources=None → alle drei."""
    src = tuple(sources) if sources else ("mastodon", "bluesky", "news")
    return hashtags.refresh(_conn, src, limit)


@mcp.tool()
def hashtag_map() -> dict:
    """Geo-Punkte + Legende für die Hashtag-Weltkarte: pro Punkt lat/lon, Farbe,
    Hashtag, Quelle und klickbare URL; je Hashtag Aktivität + echte Quell-Links.
    Punkte tragen score/trust/lang_tier; QC-Quellen je Hashtag sind nach Score sortiert."""
    return hashtags.map_data(_conn)


@mcp.tool()
def regeocode_posts(only_missing: bool = False) -> dict:
    """Verortet bestehende Hashtag-Posts mit der aktuellen Placement-Logik neu
    (Institution mit exaktem Sitz → Gazetteer → breit gestreuter Sprach-Centroid)
    → entzerrt den Globus sofort, ohne neu zu fetchen. only_missing=True nur für
    bisher unverortete Posts."""
    return hashtags.regeocode(_conn, only_missing=only_missing)


@mcp.tool()
def watch_add(platform: str, handle: str, entity_id: int | None = None,
              label: str | None = None) -> dict:
    """Beobachte einen Akteur-Account (mastodon/bluesky). Optional mit entity_id
    verknüpfen (Akteur×Thema) und Label. Idempotent (platform+handle UNIQUE)."""
    from marktradar import watch
    return watch.add(_conn, platform, handle, entity_id=entity_id, label=label)


@mcp.tool()
def watch_refresh() -> dict:
    """Holt die letzten Posts aller aktiven beobachteten Accounts in watched_posts
    (idempotent). Fehler je Account isoliert im Report."""
    from marktradar import watch
    return watch.refresh(_conn)


@mcp.tool()
def tag_news_hashtags(auto_create: bool = False, limit: int = 400) -> dict:
    """Verknüpft News-Artikel mit Hashtags (matcht aktive Hashtags). auto_create=True:
    relevante Artikel ohne Treffer bekommen per LLM ein neues Themen-Hashtag (Event wird
    Hashtag). Speist Trends/Diskurs/Globus. Gibt {linked, created, articles}."""
    return hashtags.tag_articles(_conn, None, auto_create=auto_create, limit=limit)


@mcp.tool()
def geocode_org(traeger: str = "Bergische Diakonie") -> dict:
    """Geokodiert die Einrichtungen eines Trägers (Nominatim) → lat/lon für die
    begehbare 3D-OSM-Szene. Einmalig nötig; Nominatim-Policy max 1 req/s."""
    from marktradar import geo
    return geo.geocode_units(_conn, traeger)


_ALL_SOURCES = ("mastodon", "bluesky", "news")


def _auto_refresh_loop(interval: int, limit: int, initial_delay: int) -> None:
    """Daemon thread: periodically calls hashtags.refresh across all sources/languages.

    Opens a fresh SQLite connection per cycle — never shares the module-level _conn
    across threads (SQLite threading model: one connection per thread).
    One failure per cycle is caught and logged as WARNING; the loop always continues.
    """
    time.sleep(initial_delay)
    while True:
        conn = None
        try:
            conn = db.connect()
            result = hashtags.refresh(conn, sources=_ALL_SOURCES, limit=limit)
            added = result.get("added", 0)
            errors = result.get("errors", [])
            _log.info(
                "auto-refresh: added=%d errors=%d tags=%d",
                added, len(errors), result.get("tags", 0),
            )
            if errors:
                _log.warning("auto-refresh errors: %s", errors[:5])
        except Exception as exc:  # WHY: one cycle failure (network, db lock) must never kill the loop
            _log.warning("auto-refresh cycle failed: %s: %s", type(exc).__name__, exc)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        time.sleep(interval)


def _start_auto_refresh() -> None:
    """Start the background auto-refresh daemon thread if PFLEGE_AUTO_REFRESH is truthy."""
    if os.getenv("PFLEGE_AUTO_REFRESH", "0").lower() not in ("1", "true", "yes"):
        _log.info("auto-refresh disabled (set PFLEGE_AUTO_REFRESH=1 to enable)")
        return

    interval = int(os.getenv("PFLEGE_REFRESH_INTERVAL", "1800"))
    limit = int(os.getenv("PFLEGE_REFRESH_LIMIT", "15"))
    initial_delay = int(os.getenv("PFLEGE_REFRESH_INITIAL_DELAY", "60"))

    _log.info(
        "auto-refresh enabled: interval=%ds limit=%d initial_delay=%ds",
        interval, limit, initial_delay,
    )
    t = threading.Thread(
        target=_auto_refresh_loop,
        args=(interval, limit, initial_delay),
        daemon=True,
        name="pflege-auto-refresh",
    )
    t.start()


def main():
    _start_auto_refresh()
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
