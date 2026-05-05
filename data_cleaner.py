#!/usr/bin/env python3
"""
Pflegeheim Data Cleaner — one row per run
1. Ensures psql table exists (imports CSV on first run)
2. Picks first row with cleaned=FALSE
3. Web search via ddgs for fresh contact data
4. Sends to local Ollama for extraction/validation
5. Writes cleaned fields back to psql

Usage:
  python data_cleaner.py                        # one row
  python data_cleaner.py --loop                 # until all cleaned
  python data_cleaner.py --import-only          # just import CSV, no cleaning

Env vars (or .env):
  PGHOST / PGPORT / PGDATABASE / PGUSER / PGPASSWORD
  OLLAMA_HOST  (default: http://localhost:11434)
  OLLAMA_MODEL (default: qwen3.5:9b)
  CSV_PATH     (default: data/pflegeheime_nrw.csv)
"""

import argparse
import csv
import json
import os
import re
import time
import logging
from datetime import timezone
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
CSV_PATH = os.getenv("CSV_PATH", "data/pflegeheime_nrw.csv")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "de-DE,de;q=0.9",
}

DDG_SNIPPET_LIMIT = 6

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS pflegeheime (
    api_id              TEXT PRIMARY KEY,
    name                TEXT,
    ort                 TEXT,
    kreis               TEXT,
    lat                 DOUBLE PRECISION,
    lon                 DOUBLE PRECISION,
    website             TEXT,
    impressum_url       TEXT,
    telefon             TEXT,
    email               TEXT,
    adresse             TEXT,
    geschaeftsfuehrung  TEXT,
    einrichtungsleitung TEXT,
    status              TEXT,
    error_msg           TEXT,
    cleaned             BOOLEAN DEFAULT FALSE,
    cleaned_at          TIMESTAMPTZ,
    telefon_clean       TEXT,
    email_clean         TEXT,
    adresse_clean       TEXT,
    geschaeftsfuehrung_clean    TEXT,
    einrichtungsleitung_clean   TEXT,
    clean_notes         TEXT
);
"""

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def db_connect():
    import psycopg2
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "pflegeheime"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", ""),
    )


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
    conn.commit()


def table_empty(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pflegeheime LIMIT 1")
        return cur.fetchone() is None


def import_csv(conn, csv_path: str):
    """Bulk-import CSV into psql (skips existing api_ids)."""
    imported = 0
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        with conn.cursor() as cur:
            for row in reader:
                cur.execute(
                    """
                    INSERT INTO pflegeheime
                        (api_id, name, ort, kreis, lat, lon, website, impressum_url,
                         telefon, email, adresse, geschaeftsfuehrung, einrichtungsleitung,
                         status, error_msg)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (api_id) DO NOTHING
                    """,
                    (
                        row.get("api_id"), row.get("name"), row.get("ort"),
                        row.get("kreis"),
                        float(row["lat"]) if row.get("lat") else None,
                        float(row["lon"]) if row.get("lon") else None,
                        row.get("website"), row.get("impressum_url"),
                        row.get("telefon"), row.get("email"), row.get("adresse"),
                        row.get("geschaeftsfuehrung"), row.get("einrichtungsleitung"),
                        row.get("status"), row.get("error_msg"),
                    ),
                )
                imported += 1
    conn.commit()
    log.info(f"Imported {imported} rows from {csv_path}")


def get_next_uncleaned(conn) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT api_id, name, ort, kreis, website, impressum_url,
                   telefon, email, adresse, geschaeftsfuehrung, einrichtungsleitung
            FROM pflegeheime
            WHERE cleaned = FALSE
            ORDER BY api_id
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = ["api_id", "name", "ort", "kreis", "website", "impressum_url",
                "telefon", "email", "adresse", "geschaeftsfuehrung", "einrichtungsleitung"]
        return dict(zip(cols, row))


def save_cleaned(conn, api_id: str, data: dict):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE pflegeheime SET
                cleaned = TRUE,
                cleaned_at = NOW(),
                telefon_clean = %s,
                email_clean = %s,
                adresse_clean = %s,
                geschaeftsfuehrung_clean = %s,
                einrichtungsleitung_clean = %s,
                clean_notes = %s
            WHERE api_id = %s
            """,
            (
                data.get("telefon"), data.get("email"), data.get("adresse"),
                data.get("geschaeftsfuehrung"), data.get("einrichtungsleitung"),
                data.get("notes"), api_id,
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Search context
# ---------------------------------------------------------------------------
def _fetch_page_text(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)[:3000]
    except Exception:
        return ""


def gather_context(row: dict) -> str:
    name = row["name"]
    ort = row["ort"]
    parts = []

    # Existing scraped data as baseline
    parts.append(f"=== Scraped data ===")
    for field in ["website", "telefon", "email", "adresse", "geschaeftsfuehrung", "einrichtungsleitung"]:
        if row.get(field):
            parts.append(f"{field}: {row[field]}")

    # Fresh web search
    query = f"{name} {ort} Impressum Kontakt Telefon Email"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=DDG_SNIPPET_LIMIT, region="de-de"))
        parts.append(f"\n=== Web search: {query} ===")
        for r in results:
            parts.append(f"[{r['title']}] {r['href']}\n{r['body']}")
    except Exception as e:
        log.warning(f"Search failed: {e}")

    # Fetch existing Impressum page if available
    impressum_url = row.get("impressum_url") or row.get("website")
    if impressum_url:
        text = _fetch_page_text(impressum_url)
        if text:
            parts.append(f"\n=== Impressum page ({impressum_url}) ===\n{text}")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """Du bist ein Data-Cleaner-Agent für Pflegeheime in NRW.
Du erhältst bereits gescrapte Daten und frische Suchergebnisse aus dem Web.
Extrahiere die korrekten, verifizierten Kontaktdaten für genau diese Einrichtung.

Antworte AUSSCHLIESSLICH als valides JSON-Objekt mit diesen Feldern:
{
  "telefon": "...",
  "email": "...",
  "adresse": "...",
  "geschaeftsfuehrung": "...",
  "einrichtungsleitung": "...",
  "notes": "..."
}

Regeln:
- Nur Daten aus den vorliegenden Quellen — KEINE HALLUZINATIONEN
- Wenn keine verlässlichen Daten vorhanden: "No clear Data"
- Telefon im Format: 0XXX XXXXXXX oder +49 XXX XXXXXXX
- Email vollständig (user@domain.de)
- Adresse: Straße Nr, PLZ Ort
- notes: kurze Anmerkung falls Datenqualität unklar"""


def ollama_clean(row: dict, context: str) -> dict:
    user_msg = (
        f"Einrichtung: {row['name']}, {row['ort']} ({row.get('kreis','')})\n\n"
        f"{context}"
    )
    payload = {
        "model": OLLAMA_MODEL,
        "format": "json",
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "options": {"temperature": 0.1},
    }
    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/chat", json=payload, timeout=120
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        return json.loads(content)
    except json.JSONDecodeError:
        log.warning("Ollama returned non-JSON — extracting from text")
        m = re.search(r"\{.*\}", content, re.DOTALL)
        return json.loads(m.group(0)) if m else {}
    except Exception as e:
        log.error(f"Ollama call failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def process_one(conn) -> bool:
    """Process one row. Returns True if a row was found, False if all done."""
    row = get_next_uncleaned(conn)
    if not row:
        log.info("All rows cleaned.")
        return False

    log.info(f"Cleaning: {row['name']} ({row['ort']})")

    context = gather_context(row)
    cleaned = ollama_clean(row, context)

    if not cleaned:
        cleaned = {k: "No clear Data" for k in
                   ["telefon", "email", "adresse", "geschaeftsfuehrung", "einrichtungsleitung", "notes"]}
        cleaned["notes"] = "Ollama returned no data"

    save_cleaned(conn, row["api_id"], cleaned)
    log.info(
        f"[OK] {row['name']}: "
        f"Tel={cleaned.get('telefon','')}, "
        f"Email={cleaned.get('email','')}, "
        f"EL={cleaned.get('einrichtungsleitung','')}"
    )
    return True


def main():
    global OLLAMA_MODEL
    parser = argparse.ArgumentParser(description="Pflegeheim Data Cleaner (one row per run)")
    parser.add_argument("--csv", default=CSV_PATH, help="Path to CSV file")
    parser.add_argument("--import-only", action="store_true", help="Only import CSV, skip cleaning")
    parser.add_argument("--loop", action="store_true", help="Run until all rows cleaned")
    parser.add_argument("--model", default=OLLAMA_MODEL, help="Ollama model")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between rows in --loop mode")
    args = parser.parse_args()

    OLLAMA_MODEL = args.model

    conn = db_connect()
    ensure_schema(conn)

    if table_empty(conn):
        log.info("Table is empty — importing CSV first")
        import_csv(conn, args.csv)

    if args.import_only:
        conn.close()
        return

    if args.loop:
        while process_one(conn):
            time.sleep(args.delay)
    else:
        process_one(conn)

    conn.close()


if __name__ == "__main__":
    main()
