# Blosm 3D-Gebäude-Pipeline

Lädt eine Pflegeheim-Einrichtung per **Blosm** als 3D-Szene in Blender (Terrain mit
Höhendaten + Häuser nach OSM-Höhe + Straßen + Wald/Vegetation + Wasser), rendert sie aus
8 Winkeln im klaren **Workbench-Solid-Look** und legt die Fotos + ein `photos.json`-Manifest
auf Disk ab. Lokales Authoring-Tool (Blender 5.1 + Blosm-Addon mit ArcGIS-Token) — kein
Prod-Service.

## Module
- `marktradar/blender_geo.py` — Blosm-Treiber: `build_scene(lat, lon, radius_m)` + DB-Lookup.
- `marktradar/blender_render.py` — `render_orbit(out_dir, n_views)` + `run(facility, …)`-Driver.

lat/lon kommen aus `org_units` (übers Organigramm geokodiert). `run()` nimmt eine
`org_units`-id (int) oder einen Namens-Teilstring.

## Nutzung

**Headless:**
```bash
blender --background --python marktradar/blender_render.py -- --facility 12 --radius 320 --views 8
# oder per Name:  --facility "Haus Otto Ohl"
```

**Live über blender-mcp** (laufendes Blender, Socket 9876) — für Sichtprüfung per Viewport:
```python
import sys; sys.path.insert(0, "<repo>")
import marktradar.blender_render as r
out, manifest, stats = r.run(12, radius_m=320, n_views=8, db_path="<repo>/pflege.db")
```

## Output
```
out/buildings/<traeger>/<einrichtung>/
  view_00.png … view_07.png   # Orbit aus 8 Winkeln
  photos.json                  # Manifest (unit, view, angle_deg, path, source, created)
```
`out/buildings/` ist gitignored. Das `photos.json` ist die Disk-„Tabelle", die die geplante
Foto-Kuratier-UI (Folge-Schritt) liest — dort werden manuell ergänzte/gesuchte Fotos
neben den Blender-Renders gesammelt.

## Parameter (Defaults)
- `radius_m=320` — geladener Umkreis (Terrain/Straßen/Grün-Kontext).
- `focus_m=35` — Gebäude innerhalb dieses Radius um den **Fokuspunkt** (Terrain-Mittelpunkt =
  bbox-Zentrum = Einrichtung) zählen fürs Framing. NICHT um (0,0): Blosm `relativeToInitialImport`
  referenziert alle Importe auf den ersten der Session, Häuser liegen also fern vom Ursprung.
- `min_frame_m=50` — Mindest-Fenster; ein einzelnes großes Gebäude füllt sonst formfüllend als
  Wand das Bild. `dist_factor=1.5` — Kamera-Abstand als Vielfaches der Subjekt-Größe.
- `n_views=8`, `resolution=(1280, 960)`, `elevation_deg=20`.

## Batch
`run_traeger("Bergische Diakonie")` rendert alle geokodierten Einrichtungen (ein Ordner +
photos.json je Adresse), `delay_s`/`retries` gegen Overpass-429/504. CLI: `--traeger "<Name>"`.
Bei langem Live-Lauf via blender-mcp bricht der Socket-Client evtl. ab („No data received"),
Blender rendert aber im Hintergrund durch → Fortschritt über die `photos.json`-mtimes pollen.

## Schritt 2: Kuratieren (`curate.py`)
Lokales Tool, das die Renders aus `out/buildings/` in die `building_photos`-Tabelle einliest
und je Einrichtung eine Drag+Drop-Tabelle zeigt — sortierbar nach **Perspektive** (N/O/S/W,
aus dem Orbit-Winkel abgeleitet) und **Style/Typ** (Blender-Render / echtes Foto / Luftbild /
Zeichnung), mit „gut"-Flag und Reihenfolge-per-Drag. Eigene Fotos per Drag-Upload ergänzen.
```bash
python -m marktradar.curate          # → http://127.0.0.1:8766
```
„⟳ Renders einlesen" scannt `out/buildings/` (idempotent, mappt Ordner-Slugs auf `org_units`).
Hochgeladene Fotos landen unter `out/buildings/<traeger>/<unit>/manual/`. Das gewählte Set
(`chosen=1`) ist das Deliverable für den Prod-Viewer. Env: `PFLEGE_OUT_ROOT`, `PFLEGE_DB`,
`PFLEGE_CURATE_PORT` (8766).

## Bekannte Grenzen
- OSM/Blosm liefert keine 3D-Verkehrsschilder.
- `singleObject=False` (für Einzelgebäude-Framing) verschmilzt Straßen/Wald/Wasser in
  generische `element`-Objekte → Layer nur über Blosms Material-Farben unterscheidbar,
  nicht per Objektname.
- Gebäude-Erkennung fürs Framing ist geometrisch (extrudiert > 2 m, Footprint < 120 m),
  da Einzelgebäude generisch benannt sind.
