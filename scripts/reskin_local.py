"""Lokaler Reskin-Workflow: Bilder einer Einrichtung beschaffen → QUALITÄT PRÜFEN →
erst auf Freigabe an Meshy schicken (kein Geld für graue/blanke Kästen). Läuft auf dem
Dev-Rechner, nicht auf Prod.

Bildquellen:
  --images a.jpg b.jpg ...    lokale Fotos (tokenlos — beste Kontrolle)
  --unit-id 13                Mapillary um die geokodierte Einrichtung (braucht MAPILLARY_TOKEN)
  --lat .. --lon ..           Mapillary um Koordinaten

Ablauf:
  1. Bilder holen + Qualitäts-Gate (marktradar.mapillary.quality) → nur gute bleiben.
  2. Gute Bilder werden nach /tmp/reskin/<name>/ gespeichert → erst SICHTEN.
  3. Mit --send (+ MESHY_AI_KEY in env) → multi-image-to-3d. Ohne Key: nur Report.

    MAPILLARY_TOKEN=... python -m scripts.reskin_local --unit-id 13
    python -m scripts.reskin_local --images foto1.jpg foto2.jpg --send
"""
import argparse
import base64
import json
import os
import pathlib
import urllib.request

from marktradar import db, mapillary

MESHY_BASE = "https://api.meshy.ai/openapi/v1"
OUT = pathlib.Path("/tmp/reskin")


def _load(path):
    return pathlib.Path(path).read_bytes()


def _fetch_url(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.read()


def gather(args):
    """Gibt Liste (label, bytes, quality_dict) für brauchbare Bilder zurück."""
    name = "lokal"
    items = []
    if args.images:
        name = "manuell"
        for p in args.images:
            b = _load(p)
            items.append((os.path.basename(p), b, mapillary.quality(b)))
    else:
        lat, lon = args.lat, args.lon
        if args.unit_id:
            conn = db.connect(args.db)
            row = conn.execute("SELECT name,lat,lon FROM org_units WHERE id=?",
                               (args.unit_id,)).fetchone()
            if not row or row["lat"] is None:
                raise SystemExit("Einrichtung nicht geokodiert — erst geo.geocode_units laufen lassen")
            name, lat, lon = row["name"], row["lat"], row["lon"]
        if lat is None:
            raise SystemExit("Bitte --images ODER --unit-id ODER --lat/--lon angeben")
        cand = mapillary.candidates(lat, lon, want=args.count, token=os.getenv("MAPILLARY_TOKEN"))
        print(f"Mapillary: {cand['total']} gefunden, {cand['passed']} bestehen das Gate")
        for c in cand["rejected"]:
            print(f"  ✗ {c.get('id')}: {c.get('reason')}")
        for c in cand["best"]:
            items.append((str(c["id"]), _fetch_url(c["url"]), c))
    return name, items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", nargs="*")
    ap.add_argument("--unit-id", type=int)
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--db", default="./pflege.db")
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--prompt", default="detailed realistic building, sci-fi cargo-plane pilot school")
    ap.add_argument("--send", action="store_true", help="an Meshy schicken (braucht MESHY_AI_KEY)")
    args = ap.parse_args()

    name, items = gather(args)
    good = [(l, b, q) for (l, b, q) in items if q.get("ok")]
    folder = OUT / name.replace("/", "_")
    folder.mkdir(parents=True, exist_ok=True)
    print(f"\n=== {name}: {len(good)}/{len(items)} brauchbar ===")
    for l, b, q in items:
        mark = "✓" if q.get("ok") else "✗"
        if q.get("ok"):
            (folder / f"{l}.jpg").write_bytes(b)
        print(f"  {mark} {l}: score={q.get('score')} {q.get('w')}x{q.get('h')} "
              f"std={q.get('std')} → {q.get('reason')}")
    print(f"\nGute Bilder gespeichert in: {folder}  — bitte SICHTEN bevor --send")

    if not args.send:
        print("\n(kein --send → nichts an Meshy geschickt)")
        return
    key = os.getenv("MESHY_AI_KEY")
    if not key:
        raise SystemExit("MESHY_AI_KEY fehlt — nicht an Meshy geschickt")
    if len(good) < 1:
        raise SystemExit("keine brauchbaren Bilder — Abbruch (kein Meshy-Spend)")

    imgs = [f"data:image/jpeg;base64,{base64.b64encode(b).decode()}" for _, b, _ in good[:4]]
    payload = {"image_urls": imgs, "ai_model": "latest", "target_formats": ["glb"],
               "should_texture": True, "texture_prompt": args.prompt[:600]}
    ep = "/multi-image-to-3d" if len(imgs) > 1 else "/image-to-3d"
    if len(imgs) == 1:
        payload = {"image_url": imgs[0], **{k: v for k, v in payload.items() if k != "image_urls"}}
    req = urllib.request.Request(MESHY_BASE + ep, data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read())
    print(f"\n→ Meshy-Task gestartet: {res.get('result')}  ({len(imgs)} Bilder, {ep})")
    print(f"  Status: GET {MESHY_BASE}{ep}/{res.get('result')}")


if __name__ == "__main__":
    main()
