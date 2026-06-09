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
    b.singleObject = False  # WHY: Einzelgebäude-Objekte → render_orbit kann auf die Einrichtung framen
    b.relativeToInitialImport = True
    if terrain is not None:
        b.terrainObject = terrain.name
    try:
        bpy.ops.blosm.import_data()
    except RuntimeError as e:  # WHY: Overpass/Netz-Ausfall sichtbar machen, nicht verschlucken
        raise RuntimeError(f"Blosm OSM-Import fehlgeschlagen (bbox={bbox}): {e}") from e


def _setup_world():
    """Sonne (Tageswinkel) + gedämpfter Himmel + AgX-Filmic-Tonemapping. WHY: neutrale graue
    World @ Stärke 1.0 + harte Sonne floutet die hellen Default-Materialien aus (kein Kontrast)."""
    import bpy
    import math as _m
    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = 2.0
    sun = bpy.data.objects.new("Sun", sun_data)
    bpy.context.collection.objects.link(sun)
    sun.rotation_euler = (_m.radians(55), 0.0, _m.radians(40))
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.16, 0.22, 0.30, 1.0)  # gedämpfter blauer Himmel als Fill
        bg.inputs[1].default_value = 0.35
    vs = bpy.context.scene.view_settings
    try:
        vs.view_transform = "AgX"  # Filmic-Rolloff gegen Ausbrennen
        vs.look = "AgX - Medium High Contrast"
    except TypeError:  # WHY: älterer Build ohne AgX → Filmic als Fallback, nicht hart fehlschlagen
        vs.view_transform = "Filmic"


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
