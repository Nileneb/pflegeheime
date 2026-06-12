"""Geo-Layer für den 3D-OSM-Burner: geokodiert Org-Einheiten (Nominatim) und holt
echte OSM-Gebäude (Overpass) rund um eine Einrichtung. Server-seitig, damit der
Client kein CORS/Rate-Limit-Problem hat. Reine stdlib (urllib)."""
import json
import time
import urllib.parse
import urllib.request

from marktradar import organigram

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


def _match_heim(conn, name):
    """Fuzzy-Match einer Org-Einrichtung gegen die pflegeheime-Liste → (adresse, ort).
    Nutzt die ECHTEN Adressen aus der Liste statt nur des Namens fürs Geocoding."""
    base = name
    for pre in ("Tagespflege ", "Service Wohnen "):
        if base.startswith(pre):
            base = base[len(pre):]
    cands = [name, base, name.split("/")[0].strip(),
             base.replace("Haus ", "").replace("Diakoniezentrum ", "")]
    for c in dict.fromkeys(x.strip() for x in cands):  # dedupe, Reihenfolge erhalten
        if len(c) < 5:
            continue
        r = conn.execute(
            "SELECT adresse, ort FROM pflegeheime WHERE name LIKE ? "
            "AND adresse IS NOT NULL AND adresse!='' ORDER BY length(name) LIMIT 1",
            (f"%{c}%",)).fetchone()
        if r:
            return r["adresse"], r["ort"]
    return None


def geocode_units(conn, traeger=organigram.DEFAULT_TRAEGER, limit=None):
    """Geokodiert noch nicht verortete Einrichtungen (type='einrichtung') des Trägers.
    Reihenfolge: echte Adresse aus pflegeheime-Liste → Name+Träger → Träger-Zentrum.
    Nominatim-Policy: max 1 req/s."""
    rows = conn.execute(
        "SELECT id,name FROM org_units WHERE traeger=? AND type='einrichtung' "
        "AND (lat IS NULL OR lon IS NULL) AND active=1", (traeger,)).fetchall()
    if limit:
        rows = rows[:limit]
    center = TRAEGER_CENTER.get(traeger)
    done, from_list, fell_back = 0, 0, 0
    for r in rows:
        ll, addr = None, None
        heim = _match_heim(conn, r["name"])
        if heim:  # echte Adresse aus der Liste
            try:
                ll = geocode(f"{heim[0]}, {heim[1] or ''}, Deutschland")
                addr = f"{heim[0]}, {heim[1] or ''}".strip(", ")
                if ll:
                    from_list += 1
            except Exception:
                ll = None
            time.sleep(1.05)
        if ll is None:  # Fallback: Name + Träger
            try:
                ll = geocode(f"{r['name']}, {traeger}, Deutschland")
                if ll:
                    addr = addr or ll[2]
            except Exception:
                ll = None
            time.sleep(1.05)
        if ll is None and center:  # letzter Fallback: Träger-Zentrum, gestreut
            h = abs(hash(r["name"]))
            ll = (center[0] + ((h % 100) / 100 - 0.5) * 0.03,
                  center[1] + (((h // 100) % 100) / 100 - 0.5) * 0.05, None)
            fell_back += 1
        if ll:
            conn.execute("UPDATE org_units SET lat=?, lon=?, address=COALESCE(?,address) WHERE id=?",
                         (ll[0], ll[1], addr, r["id"]))
            done += 1
    conn.commit()
    return {"geocoded": done, "from_list": from_list, "fallback": fell_back,
            "remaining": len(rows) - done}


def _num(v):
    try:
        return float(str(v).replace("m", "").strip())
    except (ValueError, TypeError):
        return None


def _height(tags):
    h = _num(tags.get("height")) or _num(tags.get("building:height"))
    if h:
        return h
    lv = _num(tags.get("building:levels"))
    if lv:
        return lv * 3.2
    return 9.0


def _roof(tags):
    shape = (tags.get("roof:shape") or "").lower()
    rh = _num(tags.get("roof:height"))
    if rh is None:
        rl = _num(tags.get("roof:levels"))
        rh = rl * 2.6 if rl else None
    # pitched-Dach getaggt aber ohne Höhe → sinnvoller Default
    if shape and shape != "flat" and rh is None:
        rh = 3.0
    return shape or None, rh or 0.0, tags.get("roof:colour")


def buildings(lat, lon, radius=320):
    """Echte OSM-Gebäude (inkl. building:part) im Umkreis, mit Höhe/Dach/Farbe."""
    ql = (f"[out:json][timeout:25];("
          f"way[\"building\"](around:{radius},{lat},{lon});"
          f"way[\"building:part\"](around:{radius},{lat},{lon}););out geom;")
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
        shape, rh, rcol = _roof(tags)
        out.append({
            "coords": coords, "height": _height(tags),
            "min_height": _num(tags.get("min_height")) or 0.0,
            "roof_shape": shape, "roof_height": rh, "roof_colour": rcol,
            "colour": tags.get("building:colour") or tags.get("colour"),
            "name": tags.get("name"), "amenity": tags.get("amenity"),
            "part": "building:part" in tags,
        })
    return out


def scene(conn, unit_id, radius=320, refresh=False):
    """3D-Szenen-Daten für eine Einrichtung: Zentrum + OSM-Gebäude ringsum.
    Gebäude werden je Einrichtung GECACHT (org_units.buildings_json) — Overpass ist
    flaky+langsam, und es sind nur ~20-30 Einrichtungen. refresh=True erzwingt neu."""
    import json
    u = conn.execute("SELECT id,name,lat,lon,address,buildings_json "
                     "FROM org_units WHERE id=?", (unit_id,)).fetchone()
    if not u:
        return {"error": "unit not found"}
    if u["lat"] is None or u["lon"] is None:
        return {"error": "not geocoded", "name": u["name"]}
    bld, cached = None, False
    if u["buildings_json"] and not refresh:
        try:
            bld = json.loads(u["buildings_json"])
            cached = True
        except (ValueError, TypeError):
            bld = None
    if bld is None:
        bld = buildings(u["lat"], u["lon"], radius)  # Overpass; wirft bei Totalausfall
        conn.execute("UPDATE org_units SET buildings_json=? WHERE id=?",
                     (json.dumps(bld), unit_id))
        conn.commit()
    return {"name": u["name"], "center": {"lat": u["lat"], "lon": u["lon"]},
            "address": u["address"],
            "radius": radius, "buildings": bld, "cached": cached}
