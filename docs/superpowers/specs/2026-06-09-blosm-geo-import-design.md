# Blosm-Geo-Import + Blender-Multiview-Render → Fotos auf Disk

**Datum:** 2026-06-09 · **Repo:** `nileneb/pflegeheime` (master) · **Modul:** `marktradar/`
**Status:** ✅ Approved (User „ja", 2026-06-09) — bereit für writing-plans
**Verbunden:** [[project_pflegeheime_blosm_meshy_pipeline]], [[project_applinngames_php_dockerized]],
`marktradar/geo.py` (OSM-Gebäude/Geocoding), `marktradar/viewer.py` (Viewer + Meshy-Reskin, wird Folge-Session ersetzt).

## Problem & Ausrichtung

Für die 3D-Sicht der Pflegeheim-Gebäude (Träger Bergische Diakonie) wurde bisher eine
**Meshy-Reskin-Automatik** verfolgt (Browser-Multiview-Capture + Foto-Finder mapillary/ddg →
Meshy-Service → GLB → `org_units.meshy_url`). Das ist überautomatisiert, kostet pro Gebäude,
ist flaky und liefert nie die Qualität eines sauberen 3D-Loads.

**Neue Linie (User 2026-06-09):** *„Lieber einmal richtig."* Die Gebäude **einmal** sehr gut
per **Blosm in Blender** laden (Häuser nach Höhe + Straßen + Bäume + Terrain mit Höhendaten),
**Fotos rendern**, und in einer **Tabelle** sammeln, die der User manuell mit gesuchten/
beauftragten Fotos ergänzt. Die Meshy-Pipeline wird **komplett entfernt** (inkl. mapillary/ddg).

## Scope — Staffelung

Der User hat **gestaffelt** entschieden. Diese Spec deckt **Schritt 1** voll ab und
notiert Schritt 2 als geplanten Endzustand (eigene Spec/Session).

### Schritt 1 (DIESE Session — in Scope)
Blosm-Geo-Load + Blender-Multiview-Render → **Fotos auf Disk** + Disk-Manifest. Live
verifiziert (Viewport-Screenshot + echte PNGs).

### Schritt 2 (Folge-Session — NICHT in Scope, nur dokumentiert)
- DB-Tabelle `building_photos` (unit_id, source, path/url, quality, chosen, created).
- Viewer-Panel: Fotos je Einrichtung sortiert + manuelles Ergänzen (Upload/URL) + „gut"-Flag.
  Ersetzt das bestehende Reskin-Panel (Reuse).
- **Meshy + mapillary.py + ddg_images.py entfernen:** `viewer.py` `send_to_meshy()` (Z.57-91),
  `/api/org/meshy_done` (Z.295-300), Reskin-Panel (Z.628-638), Browser-Capture-JS (Z.1232-1290);
  `MESHY_GEN_URL` aus viewer + docker-compose; `meshy_url` aus `geo.py:179,198` + `db.py:140-141`.

## Umgebung (Ist-Stand, verifiziert)

- **Blender 5.1.2**, Addon **Blosm aktiv & konfiguriert** (`addon_utils.check('blosm') == (True, True)`).
  Prefs: `arcgisAccessToken` gesetzt (Terrain/Höhendaten ✓), `dataDir=/home/nileneb/Pictures/3dBae/BergischeDiakonie/`,
  `osmServer='overpass-api.de'`. **Kein** `googleMapsApiKey`/`mapboxAccessToken` → kein Google-3D-Tiles
  (für EU eh tot seit Juli 2025). Quelle: OSM-Geometrie + ArcGIS-Terrain.
- **Blender-MCP** lokal auf dem Laptop (Socket 9876). Das Setup-Script ist ein **lokales
  Authoring-Tool**, kein containerisierter Prod-Service. Diese Session live über MCP
  (`execute_blender_code`/`get_viewport_screenshot`); zusätzlich headless lauffähig.

### Blosm-Scripting-API (maßgeblich aus laufendem Blender ausgelesen)
- `bpy.context.scene.blosm.*` hält alle Import-Properties; `bpy.ops.blosm.import_data()` führt aus.
- **Ein Import pro `dataType`** (`'terrain'`, `'osm'`, `'overlay'`).
- OSM-Toggles: `buildings, highways, railways, water, forests, vegetation` (+`treeDensity=500`).
- Häuser extrudiert nach OSM-Höhe / `levelHeight=3.0`; `defaultRoofShape='gabled'`.
- `terrainObject` + `relativeToInitialImport=True` → georeferenziert ausgerichtet: **erst Terrain,
  dann OSM** drapiert sich aufs Terrain. `mode='3Dsimple'` (saubere extrudierte Geometrie).
- `overlayType='arcgis-satellite'` nutzbar (Token gesetzt) — diese Session **default aus**
  (saubere, neutrale Basis; Overlay optional per Flag).
- **„Schilder" gibt es nicht** — OSM/Blosm liefert keine 3D-Verkehrsschilder. Häuser, Straßen,
  Wald/Vegetation, Wasser, Bahn, Terrain: ja.

## Architektur

Zwei neue, fokussierte Module in `marktradar/`. `bpy` wird **lazy** in den bpy-nutzenden
Funktionen importiert, damit pure Helfer ohne Blender (pytest) laufen.

### `marktradar/blender_geo.py` — Blosm-Treiber
| Funktion | Zweck | Abhängigkeit |
|---|---|---|
| `_bbox(lat, lon, radius_m) -> (minLat,maxLat,minLon,maxLon)` | Meter→Grad mit cos-Breitenkorrektur | pure |
| `_facility_from_db(conn, name_or_id) -> (lat,lon,name)` | lat/lon aus `org_units` (geokodiert übers Organigramm) | sqlite3 |
| `_reset_scene()` | frische Szene, alte Blosm-Objekte weg — idempotent re-runnbar | bpy |
| `_import_terrain(bbox) -> terrain_obj` | `dataType='terrain'`, `import_data()`, setzt `terrainObject` | bpy + Blosm |
| `_import_osm(bbox, terrain, *, mode='3Dsimple')` | alle Geo-Layer an, `import_data()` → Häuser+Straßen+Grün drapiert | bpy + Blosm |
| `_setup_world(*, overlay=False)` | Sonne (Tageswinkel), neutrale World, optional ArcGIS-Satellit-Overlay | bpy |
| `build_scene(lat, lon, radius_m=320, *, terrain=True, overlay=False) -> stats` | orchestriert 1–6, gibt Stats (#Gebäude grob, bbox, terrain-ja/nein, object_count) | bpy + Blosm |

### `marktradar/blender_render.py` — Orbit-Multiview
| Funktion | Zweck | Abhängigkeit |
|---|---|---|
| `_slug(name) -> str` | dateisystemsicherer Einrichtungs-Slug | pure |
| `render_orbit(out_dir, *, n_views=8, resolution=(1280,960), elevation_deg=20) -> list[row]` | Kamera-Orbit um Szenen-Mitte, N Renders → `view_NN.png`; gibt Manifest-Rows zurück | bpy |
| `write_manifest(out_dir, rows) -> path` | `photos.json` schreiben (die Disk-„Tabelle") | pure (json) |

Manifest-Row-Schema: `{"unit": <slug>, "view": <int>, "angle_deg": <float>, "path": "view_NN.png",
"source": "blender_render", "created": <iso>}`. Pfade **relativ** zum out_dir (portabel).

### Driver `run(...)`
Ein `run(facility, *, radius_m=320, n_views=8, out_root="out/buildings", db_path="pflege.db")`
(in `blender_render.py` oder kleines `blender_pipeline.py`):
pflege.db öffnen → `_facility_from_db` → `build_scene` → `render_orbit` → `write_manifest`.
Output-Layout: `<out_root>/<traeger_slug>/<unit_slug>/{view_NN.png, photos.json}`.
Headless: `blender --background --python marktradar/blender_render.py -- --facility "<name>" [--radius 320] [--views 8]`.

## Datenfluss

```
org_units (lat/lon, geokodiert übers Organigramm)
   │  _facility_from_db
   ▼
build_scene(lat,lon,radius)
   ├─ _import_terrain  (ArcGIS-Höhendaten)
   ├─ _import_osm      (Häuser-nach-Höhe + Straßen + Wald/Veg + Wasser + Bahn, aufs Terrain)
   └─ _setup_world     (Sonne, neutrale World)
   ▼
render_orbit(out_dir, n_views)  →  view_00.png … view_NN.png
   ▼
write_manifest  →  photos.json   (Disk-Tabelle, Folge-Session-UI liest sie)
```

## Fehlerbehandlung (kein stilles Schlucken)

- Blosm-`import_data()` schlägt bei Netz/Overpass/ArcGIS-Ausfall fehl → **`RuntimeError` mit
  Layer-Kontext** (welcher `dataType`), nicht verschluckt.
- `_facility_from_db` ohne lat/lon → klarer Fehler: „Einrichtung nicht geokodiert — erst
  `geo.geocode_units` laufen lassen."
- `terrain=False` (ArcGIS-Token fehlt/Fehler) → flache Ebene, **explizit geloggt** (WARN), nicht still.

## Verifikation (diese Session, echt)

1. **Live gegen reale Einrichtung:** eine geokodierte Bergische-Diakonie-`org_units`-Zeile wählen
   (ggf. vorher `geocode_units` für eine mit Adresse), `build_scene` über blender-mcp →
   `get_viewport_screenshot` zeigt **extrudierte Häuser auf Terrain + Straßen/Grün**. Stats geprüft
   (`object_count`>1, bbox plausibel).
2. **Render:** `render_orbit` → `out/buildings/<…>/view_00..07.png` existieren, nicht-leer, zeigen
   das Gebäude aus 8 Winkeln. `photos.json` mit 8 Rows.
3. **Pure-Helfer-Tests (pytest, ohne bpy):** `_bbox` (bekannte lat/lon → erwartete Grad-Spanne),
   `_slug`, `write_manifest`/Schema, `_facility_from_db` gegen Temp-SQLite mit/ohne lat/lon.
4. **Headless-Smoke (optional):** `blender --background --python marktradar/blender_render.py -- --facility "<name>"`
   endet rc=0, erzeugt PNGs.

## YAGNI / bewusst nicht im Scope (diese Session)

- DB-Tabelle `building_photos`, Viewer-Foto-Panel, manuelles Ergänzen → Schritt 2.
- Meshy-/mapillary-/ddg-Removal → Schritt 2.
- AI-Diffusion (photoreal) der Renders → später, separat.
- ArcGIS-Satellit-Overlay default aus (Flag vorhanden, aber neutrale Basis bevorzugt).
- Realismus `3Drealistic`/Asset-Packages → `3Dsimple` reicht als saubere Basis.
