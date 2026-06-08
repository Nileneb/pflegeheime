# Marktradar Globe v2 — Verknüpfungs-Blink + Sprach-Overlay + Hashtag-Übersetzung

**Datum:** 2026-06-08 · **Repo:** `nileneb/pflegeheime` (`marktradar/`) · **Status:** Design, freigegeben

## Context

Der Marktradar-Globe (`marktradar/viewer.py`, Single-File Vanilla-JS + Three.js r128
WebGL-Globe, SQLite-Backend) zeigt geokodierte Hashtag-Posts als pulsierende Punkte.
Drei gewünschte Änderungen (User, 2026-06-08), Ziel „cool UND nützlich":

1. **Quellen sollen nicht mehr blinken — die Verknüpfungen sollen aufblinken.**
   Verbindungslinien existieren auf dem Globus heute gar nicht (nur `trends.pairs`
   Ko-Vorkommen als Text-Tabelle) → müssen neu gebaut werden.
2. **~30–40 wichtigste Sprachen je eine Farbe**, Hashtags **synchron** in alle
   übersetzen.
3. **Sprachen als halbtransparente Flächen über die Erde** — fließende „Sprachgrenzen"
   statt starrer Landesgrenzen; Vermischung explizit ok.

Heutiger Stand: keine Sprach-Spalte, kein Übersetzungssystem, keine Geo/Sprache-Daten —
aber Ollama-Chat (Cloud, `CHAT_HOST`) ist bereits angebunden.

## Entscheidungen (vom User bestätigt)

- Verknüpfung = **Ko-Vorkommen-Arcs** (aus `trends.pairs`), Glow-Puls wandert entlang.
- Sprach-Overlay = **hybrid weich verblendet** (echte Länder-GeoJSON, geblurrt → fließend).
- Übersetzung = **speichern + anzeigen**; Label-Anzeige = **Hover/Klick-Tooltip**.

## A. Globe-Rendering: Quellen ruhig, Arcs blinken (`marktradar/viewer.py`)

- **Quell-Marker entpulsen** (`viewer.py:776-791`): konstante Größe/Helligkeit nach
  `weight`, dezenter Dauer-Glow, kein Discovery-Sweep, kein Scale-Pulsing.
- **Ko-Vorkommen-Arcs:** Aus der API-Antwort `trends.pairs` (`a, b, ca, cb, n`). Pro
  Hashtag den **Geo-Centroid** seiner Punkte berechnen (clientseitig aus `HTDATA.points`,
  gruppiert nach `term`). Pro Paar einen Großkreis-Arc zwischen den beiden Centroiden:
  `THREE.QuadraticBezierCurve3` mit angehobenem Mittelpunkt (Bogen über der Kugel),
  `TubeGeometry`, additives Material. Arc-Farbe = Mix(`ca`,`cb`).
- **Wander-Glow:** kleines additives Sprite läuft pro Frame entlang `curve.getPoint(t)`,
  `t` von 0→1, Geschwindigkeit + Glow-Intensität ∝ `n`; Phasen pro Arc gestaffelt
  (`idx/N`), plus sanfter Opacity-Puls des Tubes. Ergebnis: schimmerndes Netz.
- Performance-Cap: max. ~120 stärkste Paare rendern (nach `n` sortiert), Rest weglassen
  und im Trends-Panel `log`-artig vermerken (kein lautloses Droppen).
- **Keine neuen Dependencies** (alles r128-CDN, das schon geladen ist).

## B. Sprach-Overlay: weich verblendete Länder (`marktradar/viewer.py` + Daten)

- **Daten ins Repo:** `marktradar/data/ne_110m_admin_0_countries.geojson` (Natural Earth,
  klein) + `marktradar/data/country_language.json` (ISO_A2 → `lang_code`, kuratiert für
  die ~40 Sprachen). Beide statisch, im Image gebündelt.
- **Sprach-Konstanten:** `marktradar/languages.py` — `LANGUAGES = [{code,name,color,
  centroid:[lat,lon]}]` (~40 Einträge). Farbpalette: 40 unterscheidbare, **entsättigte**
  Töne (Overlay liegt hinten, darf nicht mit den kräftigen Hashtag-Farben konkurrieren).
- **Rendering-Trick (fließend statt starr):** Offscreen-**Equirect-Canvas (2048×1024)**.
  Jedes Land-Polygon (lon/lat → x/y äquirektangular) in der **Sprachfarbe** seines
  `lang_code` füllen. Dann **Gaußblur** (`ctx.filter='blur(Npx)'`, N≈18) → Ländergrenzen
  lösen sich auf, benachbarte/überlappende Sprachen mischen additiv. Canvas als
  `THREE.CanvasTexture` auf eine **Overlay-Sphäre** (r≈1.003, `transparent`, opacity
  ~0.22, `depthWrite=false`, normal blend). Toggle-Button „Sprachen".
- Eine kleine **Legende** (Sprache → Farbe) als ein-/ausklappbares Panel.

## C. Hashtag-Übersetzung (`db.py`, `hashtags.py`, `server.py`)

- **Schema:** neue Tabelle
  `hashtag_translations(hashtag_id INT, lang_code TEXT, term TEXT, PRIMARY KEY(hashtag_id,lang_code))`.
  `hashtags.term` (DE) bleibt kanonisch; `hashtags.color` bleibt die **Hashtag**-Farbe
  (getrennt von den Sprachfarben).
- **Service:** `hashtags.translate_hashtag(term, target_langs) -> dict[lang,term]` —
  EIN Batch-Prompt an den vorhandenen Ollama-**Cloud**-Chat (`CHAT_HOST`/`OLLAMA_CLOUD_API_KEY`),
  `format=json`, Ergebnis nach `hashtag_translations` cachen. Idempotent (nur fehlende
  Sprachen nachübersetzen). Läuft bei `add_hashtag` + neues MCP-Tool
  `translate_all_hashtags(langs=None)` (Backfill) in `server.py`.
- **Sprach-Liste** = `languages.LANGUAGES` (Single Source of Truth, geteilt mit Overlay).
- **API:** `map_data()` (`hashtags.py:442-482`) liefert zusätzlich `translations`
  (hashtag→{lang:term}) und `languages` (code,name,color,centroid) mit.
- **Anzeige (Tooltip):** Beim Hover/Klick auf einen Marker zeigt der Tooltip den Hashtag
  in der **Sprache der darunterliegenden Region** (lat/lon → `country_language` → lang_code
  → `translations`), Fallback DE/EN.

## Module / Isolation

- `marktradar/languages.py` — neue Single-Source-of-Truth (Sprachen+Farben+Centroiden).
- `marktradar/data/*.json` — statische Geo/Sprache-Daten.
- `marktradar/hashtags.py` — `translate_hashtag()` + `map_data()`-Erweiterung.
- `marktradar/db.py` — `hashtag_translations`-Tabelle (idempotent CREATE).
- `marktradar/server.py` — MCP-Tool `translate_all_hashtags`.
- `marktradar/viewer.py` — Arcs, Entpulsen, Overlay-Canvas, Legende, Tooltip-i18n.

## Tests / Verifikation

- **Python (pytest):** `translate_hashtag` (gemockter Chat → JSON-Parse, idempotenter
  Cache, fehlende-Sprachen-Nachzug), `map_data` enthält `translations`+`languages`,
  `country_language`-Lookup (lat/lon→lang_code) für ein paar bekannte Punkte.
- **Frontend (visuell, PFLICHT):** Viewer lokal starten (`python -m marktradar.viewer`),
  Screenshot des Globus → prüfen: Quellen ruhig, Arcs mit Wander-Glow, Sprach-Overlay
  weich/fließend, Tooltip lokalisiert. Siehe [[feedback_validate_frontend_screenshots]].
- **Deploy:** `nileneb/pflegeheime` master → Image `nileneb/linn-pflege-marktradar`
  bauen → app.linn.games-Compose (`pflege-viewer`+`mcp-pflege`) → `pflege.linn.games`
  /`/recherche/marktradar` verifizieren.

## Risiken / Nicht-Scope

- Übersetzungs-LLM-Last → **Cloud-Chat**, gecacht, idempotent (nicht die lokale GPU).
- Land→Sprache ist kuratierte Näherung (Vermischung explizit ok; mehrsprachige Länder
  bekommen ihre dominante Sprache, Blur kaschiert Kanten).
- Kein echtes ethnolinguistisches Sprachareal-Dataset (wäre Overkill) — Länder-GeoJSON
  + Blur ist die bewusste „cool+nützlich"-Näherung.
- Arc-Cap 120 — sehr dichte Netze werden begrenzt (geloggt, nicht lautlos).
