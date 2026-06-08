"""Geo-Layer für den 3D-OSM-Burner: geokodiert Org-Einheiten (Nominatim) und holt
echte OSM-Gebäude (Overpass) rund um eine Einrichtung. Server-seitig, damit der
Client kein CORS/Rate-Limit-Problem hat. Reine stdlib (urllib)."""
import json
import time
import urllib.parse
import urllib.request

UA = "pflege-marktradar/1.0 (+https://pflege.linn.games)"
TIMEOUT = 30
NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Overpass-Mirror der Reihe nach; Hauptinstanz zuerst (zuverlässigste Erreichbarkeit),
# gibt unter Last aber gelegentlich 504 → pro Endpoint 2 Versuche.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Fallback-Zentren je Träger (wenn Nominatim die Einrichtung nicht findet).
TRAEGER_CENTER = {"Bergische Diakonie": (51.2806, 7.0386)}  # Wülfrath


def _get_json(url, data=None):
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def geocode(query):
    """Nominatim-Suche → (lat, lon, display_name) | None."""
    q = urllib.parse.urlencode({"q": query, "format": "json", "limit": 1,
                                "countrycodes": "de,at,ch"})
    res = _get_json(f"{NOMINATIM}?{q}")
    if res:
        r = res[0]
        return float(r["lat"]), float(r["lon"]), r.get("display_name", "")
    return None


def geocode_units(conn, traeger="Bergische Diakonie", limit=None):
    """Geokodiert noch nicht verortete Einrichtungen (type='einrichtung') des Trägers.
    Nominatim-Policy: max 1 req/s. Fallback = Träger-Zentrum mit Streuung."""
    rows = conn.execute(
        "SELECT id,name FROM org_units WHERE traeger=? AND type='einrichtung' "
        "AND (lat IS NULL OR lon IS NULL) AND active=1", (traeger,)).fetchall()
    if limit:
        rows = rows[:limit]
    center = TRAEGER_CENTER.get(traeger)
    done, fell_back = 0, 0
    for i, r in enumerate(rows):
        ll = None
        try:
            ll = geocode(f"{r['name']}, {traeger}, Deutschland")
        except Exception:
            ll = None
        if ll is None and center:
            # gestreut um das Träger-Zentrum, damit die Häuser nicht exakt überlappen
            h = abs(hash(r["name"]))
            lat = center[0] + ((h % 100) / 100 - 0.5) * 0.03
            lon = center[1] + (((h // 100) % 100) / 100 - 0.5) * 0.05
            ll = (lat, lon, None)
            fell_back += 1
        if ll:
            conn.execute("UPDATE org_units SET lat=?, lon=?, address=COALESCE(address,?) WHERE id=?",
                         (ll[0], ll[1], ll[2], r["id"]))
            done += 1
        time.sleep(1.05)  # Nominatim rate-limit
    conn.commit()
    return {"geocoded": done, "fallback": fell_back, "remaining": len(rows) - done}


def _height(tags):
    for k in ("height", "building:height"):
        v = tags.get(k)
        if v:
            try:
                return float(str(v).replace("m", "").strip())
            except ValueError:
                pass
    lv = tags.get("building:levels")
    if lv:
        try:
            return float(lv) * 3.2
        except ValueError:
            pass
    return 9.0


def buildings(lat, lon, radius=320):
    """Echte OSM-Gebäude im Umkreis: Liste {coords:[[lat,lon],...], height, tags}."""
    ql = (f"[out:json][timeout:25];(way[\"building\"](around:{radius},{lat},{lon}););"
          "out geom;")
    data = urllib.parse.urlencode({"data": ql}).encode()
    res, last = None, None
    for ep in OVERPASS_ENDPOINTS:
        for _ in range(2):  # 504 ist oft transient → ein Retry pro Mirror
            try:
                res = _get_json(ep, data=data)
                break
            except Exception as e:  # WHY: Mirror überlastet → Retry/nächster, Fehler nicht verschlucken
                last = e
        if res is not None:
            break
    if res is None:
        raise RuntimeError(f"alle Overpass-Mirror fehlgeschlagen: {last}")
    out = []
    for el in res.get("elements", []):
        geom = el.get("geometry") or []
        if len(geom) < 3:
            continue
        coords = [[g["lat"], g["lon"]] for g in geom]
        tags = el.get("tags", {})
        out.append({"coords": coords, "height": _height(tags),
                    "name": tags.get("name"), "amenity": tags.get("amenity")})
    return out


def scene(conn, unit_id, radius=320):
    """3D-Szenen-Daten für eine Einrichtung: Zentrum + OSM-Gebäude ringsum."""
    u = conn.execute("SELECT id,name,lat,lon,address,meshy_url FROM org_units WHERE id=?",
                     (unit_id,)).fetchone()
    if not u:
        return {"error": "unit not found"}
    if u["lat"] is None or u["lon"] is None:
        return {"error": "not geocoded", "name": u["name"]}
    bld = buildings(u["lat"], u["lon"], radius)
    return {"name": u["name"], "center": {"lat": u["lat"], "lon": u["lon"]},
            "address": u["address"], "meshy_url": u["meshy_url"],
            "radius": radius, "buildings": bld}
