"""RS256-JWT-Auth fürs streamable-http MCP (Hausmuster wie paper-search).

Verifiziert Bearer-Tokens gegen den RS256-Public-Key (app.linn.games-JWTs).
Token kommt aus Authorization-Header ODER ?token= (Proxy/Synology strippt
Authorization). Ohne konfigurierten Public-Key → keine Prüfung (stdio/dev)."""
import base64
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

ISSUER = os.getenv("PFLEGE_JWT_ISSUER", "https://app.linn.games")
AUDIENCE = os.getenv("PFLEGE_JWT_AUDIENCE", "pflege-marktradar")
_OPEN_PATHS = {"/health", "/.well-known/oauth-authorization-server"}


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
    """dict bei gültigem Token; None bei ungültig; {} wenn Auth deaktiviert (kein Key)."""
    if not PUBLIC_KEY:
        return {}
    import jwt
    try:
        return jwt.decode(token, PUBLIC_KEY, algorithms=["RS256"],
                          issuer=ISSUER, audience=AUDIENCE)
    except Exception:
        return None


def _token(request) -> str | None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return request.query_params.get("token")


class JWTAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if PUBLIC_KEY is None or request.url.path in _OPEN_PATHS:
            return await call_next(request)
        tok = _token(request)
        if not tok or verify(tok) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
