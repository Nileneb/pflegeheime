# Blosm Geo-Import + Blender Multiview-Render Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein lokales Blender-Authoring-Tool, das per Blosm die Gebäude einer Pflegeheim-Einrichtung (lat/lon aus `org_units`) als 3D-Szene lädt (Terrain + Häuser-nach-Höhe + Straßen + Grün), aus 8 Winkeln rendert und die Fotos + ein `photos.json`-Manifest auf Disk schreibt.

**Architecture:** Zwei fokussierte Module in `marktradar/`. Pure Helfer (Math, Slug, Manifest, DB-Lookup) stehen auf Modul-Top-Level und sind ohne Blender per pytest testbar; `bpy` wird **lazy in den Funktionen** importiert, die es brauchen. Der Blosm-/Render-Pfad wird live über blender-mcp (`execute_blender_code` + `get_viewport_screenshot`) verifiziert, nicht per pytest.

**Tech Stack:** Python 3, Blender 5.1.2 `bpy`, Blosm-Addon (`bpy.context.scene.blosm` + `bpy.ops.blosm.import_data()`), stdlib `sqlite3`/`json`/`math`/`unicodedata`/`argparse`. EEVEE-Next-Render-Engine.

---

## File Structure

| Datei | Verantwortung |
|---|---|
| `marktradar/blender_geo.py` | Blosm-Treiber: bbox-Math, DB-Lookup (pure) + Szenen-Aufbau (bpy, lazy) |
| `marktradar/blender_render.py` | Slug + Manifest (pure) + Orbit-Render (bpy, lazy) + `run()`-Driver + `__main__` headless |
| `tests/test_blender_geo.py` | pytest für `_bbox`, `_facility_from_db` |
| `tests/test_blender_render.py` | pytest für `_slug`, `write_manifest` |

`out/buildings/<traeger_slug>/<unit_slug>/{view_NN.png, photos.json}` — Disk-Output (gitignored via vorhandenem `out/`-Eintrag prüfen).

---

### Task 1: bbox-Helfer (pure)

**Files:**
- Create: `marktradar/blender_geo.py`
- Test: `tests/test_blender_geo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_blender_geo.py
import math
from marktradar.blender_geo import _bbox


def test_bbox_centers_on_point_and_scales_with_radius():
    lat, lon, r = 51.2806, 7.0386, 320.0
    min_lat, max_lat, min_lon, max_lon = _bbox(lat, lon, r)
    # zentriert
    assert math.isclose((min_lat + max_lat) / 2, lat, abs_tol=1e-9)
    assert math.isclose((min_lon + max_lon) / 2, lon, abs_tol=1e-9)
    # 320 m → ~0.00287° lat-Halbspanne (320/111320)
    assert math.isclose(max_lat - lat, r / 111320.0, rel_tol=1e-6)
    # lon-Spanne > lat-Spanne wegen cos-Korrektur bei 51°
    assert (max_lon - lon) > (max_lat - lat)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/nileneb/Desktop/WebDev/pflegeheime && python -m pytest tests/test_blender_geo.py -v`
Expected: FAIL (`ModuleNotFoundError`/`ImportError: cannot import name '_bbox'`)

- [ ] **Step 3: Write minimal implementation**

```python
# marktradar/blender_geo.py
"""Blosm-Treiber: lädt eine Pflegeheim-Einrichtung als 3D-Szene (Terrain + OSM-Gebäude
nach Höhe + Straßen + Grün) in Blender. Pure Helfer (bbox, DB-Lookup) stehen oben und sind
ohne Blender testbar; bpy wird lazy in den Szenen-Funktionen importiert (läuft NUR in Blender)."""
import math

_M_PER_DEG_LAT = 111320.0


def _bbox(lat, lon, radius_m):
    """(minLat, maxLat, minLon, maxLon) für ein radius_m-Quadrat um (lat, lon).
    lon-Grad schrumpfen mit cos(Breite)."""
    dlat = radius_m / _M_PER_DEG_LAT
    dlon = radius_m / (_M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return (lat - dlat, lat + dlat, lon - dlon, lon + dlon)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_blender_geo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marktradar/blender_geo.py tests/test_blender_geo.py
git commit -m "feat(marktradar): _bbox helper for Blosm geo-import"
```

---

### Task 2: DB-Lookup `_facility_from_db` (pure, sqlite)

**Files:**
- Modify: `marktradar/blender_geo.py`
- Test: `tests/test_blender_geo.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_blender_geo.py
import sqlite3
import pytest
from marktradar.blender_geo import _facility_from_db


def _db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE org_units (id INTEGER PRIMARY KEY, name TEXT, traeger TEXT, "
              "lat REAL, lon REAL)")
    c.execute("INSERT INTO org_units (id,name,traeger,lat,lon) VALUES "
              "(1,'Haus Wülfrath','Bergische Diakonie',51.2806,7.0386)")
    c.execute("INSERT INTO org_units (id,name,traeger,lat,lon) VALUES "
              "(2,'Haus Ohne Geo','Bergische Diakonie',NULL,NULL)")
    return c


def test_facility_from_db_by_id_and_name():
    c = _db()
    assert _facility_from_db(c, 1) == (51.2806, 7.0386, "Haus Wülfrath", "Bergische Diakonie")
    # Name-Teilstring matcht
    lat, lon, name, traeger = _facility_from_db(c, "Wülfrath")
    assert name == "Haus Wülfrath" and lat == 51.2806


def test_facility_from_db_raises_without_geo():
    c = _db()
    with pytest.raises(ValueError, match="nicht geokodiert"):
        _facility_from_db(c, 2)


def test_facility_from_db_raises_when_missing():
    c = _db()
    with pytest.raises(ValueError, match="nicht gefunden"):
        _facility_from_db(c, 999)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_blender_geo.py -k facility -v`
Expected: FAIL (`ImportError: cannot import name '_facility_from_db'`)

- [ ] **Step 3: Write minimal implementation**

```python
# append to marktradar/blender_geo.py

def _facility_from_db(conn, name_or_id):
    """(lat, lon, name, traeger) aus org_units. int → id, str → name-Teilstring.
    Wirft ValueError wenn nicht gefunden oder nicht geokodiert."""
    if isinstance(name_or_id, int):
        row = conn.execute(
            "SELECT name, traeger, lat, lon FROM org_units WHERE id=?", (name_or_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT name, traeger, lat, lon FROM org_units WHERE name LIKE ? "
            "ORDER BY length(name) LIMIT 1", (f"%{name_or_id}%",)
        ).fetchone()
    if row is None:
        raise ValueError(f"Einrichtung nicht gefunden: {name_or_id!r}")
    if row["lat"] is None or row["lon"] is None:
        raise ValueError(
            f"Einrichtung {row['name']!r} nicht geokodiert — erst geo.geocode_units laufen lassen.")
    return float(row["lat"]), float(row["lon"]), row["name"], row["traeger"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_blender_geo.py -v`
Expected: PASS (alle)

- [ ] **Step 5: Commit**

```bash
git add marktradar/blender_geo.py tests/test_blender_geo.py
git commit -m "feat(marktradar): _facility_from_db lookup with geo guard"
```

---

### Task 3: Slug + Manifest (pure)

**Files:**
- Create: `marktradar/blender_render.py`
- Test: `tests/test_blender_render.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_blender_render.py
import json
from marktradar.blender_render import _slug, write_manifest


def test_slug_transliterates_and_lowercases():
    assert _slug("Haus Wülfrath / Süd") == "haus-wulfrath-sud"
    assert _slug("  A..B  ") == "a-b"


def test_write_manifest_roundtrip(tmp_path):
    rows = [{"unit": "haus-x", "view": 0, "angle_deg": 0.0,
             "path": "view_00.png", "source": "blender_render", "created": "2026-06-09T00:00:00"}]
    p = write_manifest(str(tmp_path), rows)
    assert p.endswith("photos.json")
    data = json.loads(open(p).read())
    assert data[0]["path"] == "view_00.png" and data[0]["source"] == "blender_render"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_blender_render.py -v`
Expected: FAIL (`ModuleNotFoundError: marktradar.blender_render`)

- [ ] **Step 3: Write minimal implementation**

```python
# marktradar/blender_render.py
"""Orbit-Multiview-Render einer Blender-Szene → Fotos + photos.json-Manifest auf Disk.
Pure Helfer (slug, manifest) oben; bpy lazy in render_orbit/run (läuft NUR in Blender)."""
import json
import os
import re
import unicodedata


def _slug(name):
    """Dateisystemsicherer Slug: Akzente strippen, lower, nicht-alnum → '-'."""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "-", n).strip("-").lower()
    return n


def write_manifest(out_dir, rows):
    """Schreibt rows als photos.json in out_dir, gibt den Pfad zurück."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "photos.json")
    with open(path, "w") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_blender_render.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add marktradar/blender_render.py tests/test_blender_render.py
git commit -m "feat(marktradar): _slug + write_manifest helpers"
```

---

### Task 4: Blosm-Szenen-Aufbau (bpy — live über blender-mcp verifiziert)

**Files:**
- Modify: `marktradar/blender_geo.py`

> **Hinweis an den Implementer:** Dieser Code nutzt `bpy` und läuft NICHT in pytest. Verifikation = den Code über das **blender-mcp**-Tool `execute_blender_code` ins laufende Blender (Socket 9876) schicken und mit `get_viewport_screenshot` ansehen. `import bpy` MUSS innerhalb der Funktionen stehen, damit `tests/test_blender_geo.py` weiter ohne Blender importierbar bleibt.

- [ ] **Step 1: Implementierung schreiben**

```python
# append to marktradar/blender_geo.py

def _reset_scene():
    import bpy
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def _new_objects(before):
    import bpy
    return [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in before]


def _import_terrain(bbox):
    """ArcGIS-Höhendaten als Terrain-Objekt. Gibt das (erste) neue Objekt zurück oder None."""
    import bpy
    b = bpy.context.scene.blosm
    before = set(bpy.data.objects.keys())
    b.dataType = "terrain"
    b.minLat, b.maxLat, b.minLon, b.maxLon = bbox
    bpy.ops.blosm.import_data()
    created = _new_objects(before)
    return created[0] if created else None


def _import_osm(bbox, terrain, *, mode="3Dsimple"):
    """OSM: Gebäude (nach Höhe) + Straßen + Wald/Vegetation + Wasser + Bahn. Drapiert sich
    auf terrain, wenn gesetzt. Wirft RuntimeError bei Import-Fehler (kein stilles Schlucken)."""
    import bpy
    b = bpy.context.scene.blosm
    b.dataType = "osm"
    b.mode = mode
    b.minLat, b.maxLat, b.minLon, b.maxLon = bbox
    b.buildings = True
    b.highways = True
    b.railways = True
    b.water = True
    b.forests = True
    b.vegetation = True
    b.relativeToInitialImport = True
    if terrain is not None:
        b.terrainObject = terrain.name
    try:
        bpy.ops.blosm.import_data()
    except RuntimeError as e:  # WHY: Overpass/Netz-Ausfall sichtbar machen, nicht verschlucken
        raise RuntimeError(f"Blosm OSM-Import fehlgeschlagen (bbox={bbox}): {e}") from e


def _setup_world():
    """Sonne (Tageswinkel) + neutrale graue World für klare, neutrale Render-Basis."""
    import bpy
    import math as _m
    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = 3.0
    sun = bpy.data.objects.new("Sun", sun_data)
    bpy.context.collection.objects.link(sun)
    sun.rotation_euler = (_m.radians(50), 0.0, _m.radians(35))
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.6, 0.62, 0.65, 1.0)
        bg.inputs[1].default_value = 1.0


def build_scene(lat, lon, radius_m=320, *, terrain=True):
    """Orchestriert Reset → Terrain → OSM → World. Gibt Stats zurück."""
    import bpy
    _reset_scene()
    bbox = _bbox(lat, lon, radius_m)
    terr = _import_terrain(bbox) if terrain else None
    if terrain and terr is None:
        print("WARN: Terrain-Import lieferte kein Objekt — fahre ohne Terrain fort (flach).")
    _import_osm(bbox, terr)
    _setup_world()
    return {"bbox": bbox, "terrain": terr is not None,
            "object_count": len(bpy.data.objects),
            "objects": list(bpy.data.objects.keys())[:20]}
```

- [ ] **Step 2: Live-Verifikation über blender-mcp**

Sende den Modulinhalt + einen Aufruf gegen eine reale Bergische-Diakonie-lat/lon ins Blender:

```python
# via mcp__blender__execute_blender_code (Auszug — Pfad an Repo anpassen)
import sys; sys.path.insert(0, "/home/nileneb/Desktop/WebDev/pflegeheime")
import importlib, marktradar.blender_geo as g; importlib.reload(g)
stats = g.build_scene(51.2806, 7.0386, 320)  # Wülfrath-Zentrum als Smoke
print(stats)
```

Dann `mcp__blender__get_viewport_screenshot` (max_size 1000).
Expected: `object_count` > 1, Screenshot zeigt **extrudierte Häuser** (+ Terrain/Straßen/Grün).
Falls Overpass 504: erneut versuchen (Mirror transient) — kein Code-Workaround.

- [ ] **Step 3: pytest-Regression (Import bleibt bpy-frei)**

Run: `python -m pytest tests/test_blender_geo.py -v`
Expected: PASS (Modul importiert weiter ohne Blender, da bpy lazy)

- [ ] **Step 4: Commit**

```bash
git add marktradar/blender_geo.py
git commit -m "feat(marktradar): Blosm scene builder (terrain + OSM buildings/roads/green)"
```

---

### Task 5: Orbit-Multiview-Render (bpy — live verifiziert)

**Files:**
- Modify: `marktradar/blender_render.py`

> **Hinweis:** bpy-Code, live über blender-mcp verifiziert. `import bpy` innerhalb der Funktion.

- [ ] **Step 1: Implementierung schreiben**

```python
# append to marktradar/blender_render.py

def _scene_center_and_size():
    """Mittelpunkt + größte Ausdehnung aller Mesh-Objekte (Welt-Koordinaten)."""
    import bpy
    from mathutils import Vector
    pts = []
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        for c in o.bound_box:
            pts.append(o.matrix_world @ Vector(c))
    if not pts:
        return Vector((0, 0, 0)), 50.0
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) / 2, max((hi - lo).x, (hi - lo).y, (hi - lo).z, 10.0)


def render_orbit(out_dir, *, n_views=8, resolution=(1280, 960), elevation_deg=20):
    """Kamera-Orbit um die Szenen-Mitte, n_views Renders → view_NN.png. Manifest-Rows zurück."""
    import bpy, math as _m, datetime as _dt
    from mathutils import Vector
    os.makedirs(out_dir, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.image_settings.file_format = "PNG"

    center, size = _scene_center_and_size()
    dist = size * 1.6
    z = center.z + dist * _m.sin(_m.radians(elevation_deg))
    flat = dist * _m.cos(_m.radians(elevation_deg))

    target = bpy.data.objects.new("OrbitTarget", None)
    bpy.context.collection.objects.link(target)
    target.location = center
    cam_data = bpy.data.cameras.new("OrbitCam")
    cam = bpy.data.objects.new("OrbitCam", cam_data)
    bpy.context.collection.objects.link(cam)
    con = cam.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    scene.camera = cam

    rows, now = [], _dt.datetime.now().isoformat(timespec="seconds")
    for i in range(n_views):
        ang = 2 * _m.pi * i / n_views
        cam.location = Vector((center.x + flat * _m.cos(ang),
                               center.y + flat * _m.sin(ang), z))
        path = os.path.join(out_dir, f"view_{i:02d}.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        rows.append({"unit": os.path.basename(out_dir.rstrip("/")), "view": i,
                     "angle_deg": round(_m.degrees(ang), 1), "path": f"view_{i:02d}.png",
                     "source": "blender_render", "created": now})
    return rows
```

- [ ] **Step 2: Live-Verifikation über blender-mcp**

Nach Task-4-`build_scene` im selben Blender:

```python
import importlib, marktradar.blender_render as r; importlib.reload(r)
rows = r.render_orbit("/tmp/blosm_smoke", n_views=8)
p = r.write_manifest("/tmp/blosm_smoke", rows)
print(len(rows), p)
```

Expected: 8 Rows; `/tmp/blosm_smoke/view_00.png`…`view_07.png` existieren, nicht-leer.
Prüfe per Bash: `ls -la /tmp/blosm_smoke/ && python -c "import json;print(len(json.load(open('/tmp/blosm_smoke/photos.json'))))"` → 8.
Lade 1-2 PNGs mit dem Read-Tool und prüfe, dass das Gebäude sichtbar ist.

- [ ] **Step 3: pytest-Regression**

Run: `python -m pytest tests/test_blender_render.py -v`
Expected: PASS (Modul weiter bpy-frei importierbar)

- [ ] **Step 4: Commit**

```bash
git add marktradar/blender_render.py
git commit -m "feat(marktradar): orbit multiview render to disk"
```

---

### Task 6: Driver `run()` + headless `__main__`

**Files:**
- Modify: `marktradar/blender_render.py`

- [ ] **Step 1: Implementierung schreiben**

```python
# append to marktradar/blender_render.py

def run(facility, *, radius_m=320, n_views=8, out_root="out/buildings", db_path="pflege.db"):
    """End-to-end: org_units → build_scene → render_orbit → photos.json.
    Gibt (out_dir, manifest_path, stats) zurück. Läuft NUR in Blender (bpy)."""
    import sqlite3
    from marktradar.blender_geo import _facility_from_db, build_scene
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        lat, lon, name, traeger = _facility_from_db(conn, facility)
    finally:
        conn.close()
    out_dir = os.path.join(out_root, _slug(traeger or "unbekannt"), _slug(name))
    stats = build_scene(lat, lon, radius_m)
    rows = render_orbit(out_dir, n_views=n_views)
    manifest = write_manifest(out_dir, rows)
    return out_dir, manifest, stats


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Blosm geo-import + orbit render")
    ap.add_argument("--facility", required=True, help="org_units id (int) oder Name-Teilstring")
    ap.add_argument("--radius", type=int, default=320)
    ap.add_argument("--views", type=int, default=8)
    ap.add_argument("--out-root", default="out/buildings")
    ap.add_argument("--db", default="pflege.db")
    a = ap.parse_args(argv)
    facility = int(a.facility) if a.facility.isdigit() else a.facility
    out_dir, manifest, stats = run(facility, radius_m=a.radius, n_views=a.views,
                                   out_root=a.out_root, db_path=a.db)
    print(f"OK out_dir={out_dir} manifest={manifest} stats={stats}")


if __name__ == "__main__":
    import sys
    # Blender headless: Argumente nach '--'
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    _main(argv)
```

- [ ] **Step 2: pytest-Regression**

Run: `python -m pytest tests/ -v`
Expected: PASS (alle pure-Tests; Modul-Import bpy-frei)

- [ ] **Step 3: Commit**

```bash
git add marktradar/blender_render.py
git commit -m "feat(marktradar): run() driver + headless CLI for blosm pipeline"
```

---

### Task 7: End-to-End-Integration gegen reale Einrichtung + Doku

**Files:**
- Modify: `marktradar/blender_render.py` (nur falls Integration Bugs zeigt)
- Modify: `marktradar/README` oder `docs/` (kurzer Nutzungs-Absatz)

- [ ] **Step 1: Reale Einrichtung sicherstellen (geokodiert)**

Prüfe per Bash, dass `pflege.db` mindestens eine geokodierte Bergische-Diakonie-Einrichtung hat:

```bash
cd /home/nileneb/Desktop/WebDev/pflegeheime
python -c "import sqlite3;c=sqlite3.connect('pflege.db');print(c.execute(\"SELECT id,name,lat,lon FROM org_units WHERE traeger LIKE '%Bergische%' AND lat IS NOT NULL LIMIT 5\").fetchall())"
```

Falls leer: `python -c "from marktradar import db,geo;c=db.connect();print(geo.geocode_units(c, limit=5))"` laufen lassen (Nominatim 1 req/s), dann erneut prüfen.

- [ ] **Step 2: Vollen Lauf über blender-mcp**

```python
# via mcp__blender__execute_blender_code
import sys; sys.path.insert(0, "/home/nileneb/Desktop/WebDev/pflegeheime")
import os; os.chdir("/home/nileneb/Desktop/WebDev/pflegeheime")
import importlib, marktradar.blender_render as r; importlib.reload(r)
out, man, stats = r.run(<ECHTE_ID>, radius_m=320, n_views=8)
print(out, man, stats)
```

Expected: `out/buildings/bergische-diakonie/<slug>/view_00..07.png` + `photos.json` (8 Rows),
`stats["object_count"]>1`. `get_viewport_screenshot` zeigt die Einrichtung; Read auf 2 PNGs.

- [ ] **Step 3: Nutzungs-Doku ergänzen**

Kurzer Absatz (README/docs): wie man den Lauf startet (live via MCP **oder** headless
`blender --background --python marktradar/blender_render.py -- --facility "<name>"`), Output-Layout,
und der Hinweis, dass `photos.json` die Disk-Tabelle für die Folge-Session-UI ist.

- [ ] **Step 4: Commit**

```bash
git add marktradar/blender_render.py docs/ marktradar/README* 2>/dev/null
git commit -m "docs(marktradar): blosm pipeline usage + verified end-to-end run"
```

---

## Self-Review

**Spec-Coverage:**
- Blosm-Load (Terrain+Häuser-nach-Höhe+Straßen+Grün) → Task 4 ✓
- lat/lon aus org_units (Organigramm-Geocoding) → Task 2 + Task 6 ✓
- Multiview-Render → Fotos auf Disk → Task 5 ✓
- `photos.json`-Manifest (Disk-Tabelle) → Task 3 + Task 5 ✓
- Headless lauffähig → Task 6 ✓
- Fehler kein stilles Schlucken (RuntimeError mit Layer-Kontext, Geo-Guard, Terrain-WARN) → Task 4/2 ✓
- pure-Helfer-pytest ohne bpy → Tasks 1-3 ✓
- Live-Verifikation (Viewport + PNGs) → Tasks 4,5,7 ✓
- Schritt-2-Themen (building_photos, UI, Meshy/mapillary/ddg-Removal) bewusst NICHT im Plan ✓

**Platzhalter:** `<ECHTE_ID>` in Task 7 ist bewusst (echte DB-ID aus Step 1 einsetzen) — sonst keine.

**Typ-Konsistenz:** `_bbox`→`build_scene`→`render_orbit`→`write_manifest`→`run` Signaturen konsistent;
Manifest-Row-Schema identisch in Task 3 (Test), 5 (Erzeugung). `_facility_from_db` gibt 4-Tupel
(lat,lon,name,traeger) in Task 2 + Task 6-Nutzung übereinstimmend.
