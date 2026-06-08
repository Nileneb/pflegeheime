"""RS256-Token-Verifikation für den OAuth-Resource-Server (server.py konfiguriert
FastMCP damit). Verifiziert die von app.linn.games ausgestellten JWTs gegen den
RS256-Public-Key. Ohne konfigurierten Public-Key → keine Prüfung (stdio/dev).

Zusätzlich ein ASGI-Shim, der ?token= in den Authorization-Header hebt (falls ein
Proxy Authorization strippt), bevor FastMCPs Auth läuft."""
import base64
import os
from urllib.parse import parse_qs

ISSUER = os.getenv("PFLEGE_JWT_ISSUER", "https://app.linn.games")
AUDIENCE = os.getenv("PFLEGE_JWT_AUDIENCE", "pflege-marktradar")


def _load_public_key() -> str | None:
    raw = os.getenv("PFLEGE_JWT_PUBLIC_KEY")
    if not raw:
        path = os.getenv("PFLEGE_JWT_PUBLIC_KEY_PATH")
        if path and os.path.exists(path):
            raw = open(path, encoding="utf-8").read()
    if not raw:
        return None
    raw = raw.strip()
    if "BEGIN" not in raw:  # base64-kodiertes PEM (wie JWT_PUBLIC_KEY in .env)
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:  # WHY: kaputtes b64 → Klartext belassen, decode unten wirft sauber
            pass
    return raw


PUBLIC_KEY = _load_public_key()


def verify(token: str) -> dict | None:
    """dict bei gültigem Token; None bei ungültig; {} wenn Auth deaktiviert (kein Key).
    Issuer/Audience werden nur geprüft, wenn PFLEGE_JWT_ISSUER/AUDIENCE gesetzt sind
    (sonst nur Signatur + Ablauf). Bei Fehler: diagnostisches Log mit echtem iss/aud."""
    if not PUBLIC_KEY:
        return {}
    import sys

    import jwt
    kwargs = {"algorithms": ["RS256"], "options": {"verify_aud": bool(AUDIENCE)}}
    if AUDIENCE:
        kwargs["audience"] = AUDIENCE
    if ISSUER:
        kwargs["issuer"] = ISSUER
    try:
        return jwt.decode(token, PUBLIC_KEY, **kwargs)
    except Exception as e:
        try:
            unv = jwt.decode(token, options={"verify_signature": False})
            head = jwt.get_unverified_header(token)
        except Exception:
            unv, head = None, None
        print(f"[auth] verify FAILED: {e!r} | unverified iss="
              f"{unv.get('iss') if unv else 'NOT_A_JWT'} aud="
              f"{unv.get('aud') if unv else '?'} claims="
              f"{list(unv.keys()) if unv else '?'} header={head}",
              file=sys.stderr, flush=True)
        return None


class QueryTokenASGI:
    """ASGI-Wrapper: hebt ?token=… in den Authorization-Header, falls keiner da ist
    (Proxy/Synology strippt Authorization). Läuft VOR FastMCPs OAuth-Auth."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = scope.get("headers") or []
            if not any(k == b"authorization" for k, _ in headers):
                qs = parse_qs(scope.get("query_string", b"").decode())
                tok = (qs.get("token") or [None])[0]
                if tok:
                    scope = dict(scope)
                    scope["headers"] = list(headers) + [
                        (b"authorization", f"Bearer {tok}".encode())]
        await self.app(scope, receive, send)
