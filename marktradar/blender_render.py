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


def _frame_objects():
    """Objekte, auf die der Orbit framen soll: gebäudeartig (extrudiert, Z-Höhe > 2m) statt
    flacher Layer (Straßen/Wald/Wasser). Blosm benennt Einzelgebäude generisch ('element.NNN'),
    daher geometrische statt namensbasierte Klassifikation. Terrain wird als Boden ignoriert."""
    import bpy
    from mathutils import Vector
    out = []
    for o in bpy.data.objects:
        if o.type != "MESH" or o.name.startswith("Terrain"):
            continue
        zs = [(o.matrix_world @ Vector(c)).z for c in o.bound_box]
        # extrudiert (Häuser) UND kein Riesen-Footprint (Parkplatz/Landuse-Outline mit Mini-Höhe)
        if max(zs) - min(zs) > 2.0 and max(o.dimensions.x, o.dimensions.y) < 120.0:
            out.append(o)
    return out


def _world_bbox_center(o):
    from mathutils import Vector
    cs = [o.matrix_world @ Vector(c) for c in o.bound_box]
    return sum(cs, Vector()) / 8.0


def _apply_solid_look():
    """Workbench-Solid-Darstellung (= der klare Solid-Viewport-Look statt ausgebranntem EEVEE):
    Studio-Licht + Cavity + Schatten + Blosms eigene Material-Farben (grüner Wald, Lehm-Gebäude,
    graue Wege). WHY: EEVEE brennt die Default-Materialien aus; Workbench/MATERIAL liest klar."""
    import bpy
    sh = bpy.context.scene.display.shading
    sh.light = "STUDIO"
    sh.color_type = "MATERIAL"  # Blosms kuratierte Feature-Farben nutzen
    sh.show_cavity = True
    sh.cavity_type = "BOTH"
    sh.show_shadows = True
    bpy.context.scene.view_settings.view_transform = "Standard"  # Solid-Look ohne Filmic-Mute


def _focus_point():
    """Welt-Position der Einrichtung = Zentrum der geladenen bbox = Terrain-Mittelpunkt. WHY:
    Blosm relativeToInitialImport referenziert alle Importe auf den ERSTEN der Session, also
    liegen Häuser fern von (0,0); der Terrain-Mittelpunkt ist je Haus das echte bbox-Zentrum."""
    import bpy
    from mathutils import Vector
    terr = bpy.data.objects.get("Terrain")
    return _world_bbox_center(terr) if terr else Vector((0, 0, 0))


def _scene_center_and_size(focus_m=28.0):
    """Frame auf die Einrichtung: gebäudeartige Objekte innerhalb focus_m vom Fokuspunkt
    (bbox-Zentrum/Terrain-Mitte = Einrichtung). So bleibt das Gebäude groß im Bild, auch wenn
    drumherum ein weites Viertel geladen ist. Welt-Koordinaten."""
    from mathutils import Vector
    fp = _focus_point()
    blds = _frame_objects()
    near = [o for o in blds if (_world_bbox_center(o) - fp).xy.length <= focus_m] or blds
    pts = []
    for o in near:
        for c in o.bound_box:
            pts.append(o.matrix_world @ Vector(c))
    if not pts:
        return Vector((0, 0, 0)), 50.0
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) / 2, max((hi - lo).x, (hi - lo).y, (hi - lo).z, 10.0)


def render_orbit(out_dir, *, n_views=8, resolution=(1280, 960), elevation_deg=20,
                 focus_m=28.0, dist_factor=1.2):
    """Kamera-Orbit um das Einrichtungs-Gebäude, n_views Renders → view_NN.png. Manifest-Rows
    zurück. focus_m = Radius um das bbox-Zentrum, in dem Gebäude fürs Framing zählen (enger =
    Einrichtung füllt das Bild). dist_factor = Kamera-Abstand als Vielfaches der Subjekt-Größe
    (kleiner = näher dran). WHY: zu weites Fenster/Abstand lässt die Häuser zu klein wirken."""
    import bpy
    import math as _m
    import datetime as _dt
    from mathutils import Vector
    os.makedirs(out_dir, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.image_settings.file_format = "PNG"
    _apply_solid_look()

    center, size = _scene_center_and_size(focus_m)
    dist = size * dist_factor
    z = center.z + dist * _m.sin(_m.radians(elevation_deg))
    flat = dist * _m.cos(_m.radians(elevation_deg))

    target = bpy.data.objects.new("OrbitTarget", None)
    bpy.context.collection.objects.link(target)
    target.location = center
    cam_data = bpy.data.cameras.new("OrbitCam")
    cam_data.clip_end = 100000.0  # Terrain liegt auf realer Höhe → großzügige Clip-Distanz
    cam = bpy.data.objects.new("OrbitCam", cam_data)
    bpy.context.collection.objects.link(cam)
    con = cam.constraints.new("TRACK_TO")
    con.target = target
    con.track_axis = "TRACK_NEGATIVE_Z"
    con.up_axis = "UP_Y"
    scene.camera = cam

    rows = []
    now = _dt.datetime.now().isoformat(timespec="seconds")
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


def run_traeger(traeger="Bergische Diakonie", *, radius_m=320, n_views=8,
                out_root="out/buildings", db_path="pflege.db", only_ids=None,
                delay_s=5.0, retries=2):
    """Alle geokodierten Einrichtungen eines Trägers nacheinander rendern (hausweise: ein Ordner
    + photos.json je Adresse). delay_s Pause zwischen Einrichtungen + Retry mit Backoff gegen
    Overpass-429/504 (Rate-Limit). Fehler je Einrichtung werden gesammelt und reported — flaky
    Antworten dürfen den Batch nicht killen, werden aber NICHT verschluckt. only_ids = Chunking."""
    import sqlite3
    import time
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        units = conn.execute(
            "SELECT id, name FROM org_units WHERE traeger=? AND lat IS NOT NULL "
            "AND lon IS NOT NULL AND active=1 ORDER BY name", (traeger,)).fetchall()
    finally:
        conn.close()
    if only_ids is not None:
        wanted = set(only_ids)
        units = [u for u in units if u["id"] in wanted]
    done, errors = [], []
    for idx, u in enumerate(units):
        if idx:
            time.sleep(delay_s)  # WHY: Overpass 429/504 bei zu schnellen aufeinanderfolgenden Requests
        last = None
        for attempt in range(retries + 1):
            try:
                out_dir, _, stats = run(u["id"], radius_m=radius_m, n_views=n_views,
                                        out_root=out_root, db_path=db_path)
                done.append({"id": u["id"], "name": u["name"], "out": out_dir,
                             "object_count": stats["object_count"]})
                print(f"OK   {u['name']} -> {out_dir}")
                break
            except Exception as e:
                last = e
                if attempt < retries:
                    time.sleep(delay_s * (attempt + 2))  # Backoff für transiente Overpass-Fehler
                    print(f"retry {u['name']} ({attempt + 1}/{retries})")
        else:
            errors.append({"id": u["id"], "name": u["name"], "error": str(last)})
            print(f"FAIL {u['name']}: {last}")
    return {"done": len(done), "failed": len(errors), "results": done, "errors": errors}


def _main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="Blosm geo-import + orbit render")
    ap.add_argument("--facility", help="org_units id (int) oder Name-Teilstring (Einzel-Lauf)")
    ap.add_argument("--traeger", help="alle geokodierten Einrichtungen dieses Trägers (Batch)")
    ap.add_argument("--radius", type=int, default=320)
    ap.add_argument("--views", type=int, default=8)
    ap.add_argument("--out-root", default="out/buildings")
    ap.add_argument("--db", default="pflege.db")
    a = ap.parse_args(argv)
    if a.traeger:
        report = run_traeger(a.traeger, radius_m=a.radius, n_views=a.views,
                             out_root=a.out_root, db_path=a.db)
        print(f"BATCH done={report['done']} failed={report['failed']}")
        for e in report["errors"]:
            print(f"  FAIL {e['name']}: {e['error']}")
    elif a.facility:
        facility = int(a.facility) if a.facility.isdigit() else a.facility
        out_dir, manifest, stats = run(facility, radius_m=a.radius, n_views=a.views,
                                       out_root=a.out_root, db_path=a.db)
        print(f"OK out_dir={out_dir} manifest={manifest} stats={stats}")
    else:
        ap.error("either --facility or --traeger required")


if __name__ == "__main__":
    import sys
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    _main(argv)
