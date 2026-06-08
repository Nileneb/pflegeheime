"""Mapillary-Straßenfotos als Meshy-Input (statt grauer OSM-Klötze). Offene,
crowdsourced Bilder mit freier Lizenz — ToS-sauber für AI-Nutzung (kein Street-View).

WICHTIG: `candidates()` holt + prüft die Bildqualität und gibt NUR brauchbare Fotos
zurück — es ruft NIEMALS Meshy auf. Der teure Meshy-Call passiert erst nach Sichtung.
Braucht MAPILLARY_TOKEN (kostenlos: mapillary.com → Developers → Access Token).
"""
import io
import json
import math
import os
import urllib.parse
import urllib.request

TOKEN = os.getenv("MAPILLARY_TOKEN", "")
GRAPH = "https://graph.mapillary.com/images"
UA = "pflege-marktradar/1.0 (+https://pflege.linn.games)"

# Qualitäts-Schwellen, damit kein Müll (zu klein, blank, zu dunkel/hell) zu Meshy geht.
MIN_W, MIN_H = 640, 480
MIN_STD = 28          # Kontrast/Detail — blanke/uniforme Bilder fallen raus
BRIGHT_LO, BRIGHT_HI = 28, 232


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def fetch_near(lat, lon, radius_m=60, limit=16, token=None):
    """Bilder im Umkreis (bbox) → Liste {id, url, compass, lat, lon, captured_at}."""
    token = token or TOKEN
    if not token:
        raise RuntimeError("MAPILLARY_TOKEN fehlt")
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(0.1, abs(math.cos(math.radians(lat)))))
    bbox = f"{lon-dlon},{lat-dlat},{lon+dlon},{lat+dlat}"
    q = urllib.parse.urlencode({
        "access_token": token, "bbox": bbox, "limit": limit,
        "fields": "id,thumb_2048_url,computed_geometry,compass_angle,captured_at",
    })
    data = json.loads(_get(f"{GRAPH}?{q}"))
    out = []
    for it in data.get("data", []):
        g = (it.get("computed_geometry") or {}).get("coordinates") or [None, None]
        out.append({"id": it.get("id"), "url": it.get("thumb_2048_url"),
                    "compass": it.get("compass_angle"), "lon": g[0], "lat": g[1],
                    "captured_at": it.get("captured_at")})
    return [o for o in out if o["url"]]


def quality(img_bytes):
    """PIL-basierte Qualitätsprüfung → {ok, score, w, h, std, brightness, reason}."""
    from PIL import Image, ImageStat
    try:
        im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception as e:
        return {"ok": False, "reason": f"nicht lesbar: {e}", "score": 0}
    w, h = im.size
    g = im.convert("L")
    st = ImageStat.Stat(g)
    std = st.stddev[0]
    bright = st.mean[0]
    reasons = []
    if w < MIN_W or h < MIN_H:
        reasons.append(f"zu klein {w}x{h}")
    if std < MIN_STD:
        reasons.append(f"zu wenig Detail (std={std:.0f})")
    if not (BRIGHT_LO < bright < BRIGHT_HI):
        reasons.append(f"Belichtung {bright:.0f}")
    ok = not reasons
    # Score: Detail × Auflösung, normiert ~0..1
    score = round(min(1.0, (std / 80) * min(1.0, (w * h) / (1280 * 960))), 3)
    return {"ok": ok, "score": score, "w": w, "h": h, "std": round(std, 1),
            "brightness": round(bright, 1), "reason": "; ".join(reasons) or "ok"}


def candidates(lat, lon, want=4, radius_m=70, token=None):
    """Holt Fotos, prüft Qualität, gibt die besten `want` BRAUCHBAREN zurück.
    Ruft KEIN Meshy auf — nur Sichtungs-Kandidaten + Qualitäts-Report."""
    imgs = fetch_near(lat, lon, radius_m, limit=20, token=token)
    scored = []
    for im in imgs:
        try:
            q = quality(_get(im["url"]))
        except Exception as e:
            q = {"ok": False, "reason": str(e), "score": 0}
        scored.append({**im, **q})
    scored.sort(key=lambda x: -x["score"])
    passed = [s for s in scored if s["ok"]]
    return {"total": len(scored), "passed": len(passed),
            "best": passed[:want], "rejected": [s for s in scored if not s["ok"]][:6],
            "ready_for_meshy": len(passed) >= 2}
