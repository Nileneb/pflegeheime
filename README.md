# Pflegeheim NRW Impressum Scraper

## Datenquelle
Offizielle **Heimfinder NRW API** (Open Data, Land NRW)
- API: `https://pfadwtg.mags.nrw/api/heimfinder/einrichtungen`
- Swagger: `https://pfadwtg.mags.nrw/heimfinder-swagger/`
- ~2.500 stationäre Pflegeeinrichtungen in NRW

## Setup
```bash
pip install -r requirements.txt
```

## Usage

### 1. Seed holen (API-Daten speichern)
```bash
python pflegeheim_scraper.py --seed fetch
```
→ Speichert `output/heimfinder_seed.json`

### 2. Scraping starten (erst 10 testen)
```bash
python pflegeheim_scraper.py --seed output/heimfinder_seed.json --limit 10
```

### 3. Alle scrapen
```bash
python pflegeheim_scraper.py --seed output/heimfinder_seed.json
```

### 4. Fortsetzen nach Abbruch
```bash
python pflegeheim_scraper.py --seed output/heimfinder_seed.json --resume output/pflegeheime_nrw.json
```

### 5. Delay anpassen (Standard: 1.5s)
```bash
python pflegeheim_scraper.py --seed output/heimfinder_seed.json --delay 2.0
```

## Output
- `output/pflegeheime_nrw.csv` — Excel-kompatibel (UTF-8-BOM, Semikolon-Trenner)
- `output/pflegeheime_nrw.json` — Maschinen-Format
- `scraper.log` — Vollständiges Log

## Extrahierte Felder
| Feld | Quelle |
|---|---|
| api_id, name, ort, lat, lon | Heimfinder API |
| website, impressum_url | DuckDuckGo-Suche |
| telefon | Regex auf Impressum |
| email | mailto-Link oder Regex |
| adresse | Straße + PLZ + Ort Pattern |
| geschaeftsfuehrung | Regex: Geschäftsführ*, Vorstand |
| einrichtungsleitung | Regex: Einrichtungsleitung, Heimleitung |

## Pipeline pro Einrichtung
1. DuckDuckGo: `"{Name} {Ort} Impressum"` → erste relevante URL
2. Homepage fetchen → Impressum-Link finden
3. Impressum fetchen → Regex-Extraktion
4. Fallback: Wenn kein Impressum-Link → Homepage parsen

## Laufzeit
~2.500 Einrichtungen × ~3s/Einrichtung = **~2 Stunden**
(mit 1.5s Delay zwischen Requests)

## Hinweise
- DuckDuckGo hat Rate-Limits. Bei 429-Fehlern Delay erhöhen.
- Aggregator-Seiten (wohnen-im-alter.de, seniorenportal.de etc.) werden automatisch übersprungen.
- Nicht jedes Pflegeheim hat eine eigene Website. Status `no_website` ist normal.
- Geschäftsführung/Einrichtungsleitung sind oft nur auf der "Über uns"-Seite, nicht im Impressum.
