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
    import bpy
    import math as _m
    import datetime as _dt
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
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    _main(argv)
