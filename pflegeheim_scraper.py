#!/usr/bin/env python3
"""
Pflegeheim NRW Scraper
======================
1. Fetcht alle Pflegeheime aus der offiziellen Heimfinder NRW API
2. Sucht per Google/DuckDuckGo die Website jeder Einrichtung
3. Fetcht die Impressum-Seite
4. Extrahiert: Telefon, E-Mail, Geschäftsführung, Einrichtungsleitung, Adresse
5. Exportiert nach CSV + JSON
"""

import argparse
import random
import requests
import re
import json
import csv
import time
import logging
from dataclasses import dataclass, field, asdict
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from pathlib import Path
from ddgs import DDGS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("scraper.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HEIMFINDER_API = "https://pfadwtg.mags.nrw/api/heimfinder/einrichtungen"
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

AGGREGATOR_DOMAINS = [
    "wohnen-im-alter.de", "seniorenportal.de", "altenheime.de",
    "pflegehilfe.org", "pflege.de", "pflegemarktplatz.de",
    "heimfinder.nrw.de", "duckduckgo.com", "google.com",
    "facebook.com", "instagram.com", "twitter.com", "youtube.com",
    "linkedin.com", "wikipedia.org", "gelbeseiten.de",
    "seniorenplatz-vermittlung.de", "pflegeguide.de",
    "weisse-liste.de", "bing.com", "yahoo.com",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
}

# Rate limiting
REQUEST_DELAY = 1.5  # seconds between web requests
MAX_WORKERS = 3  # parallel threads for impressum fetching


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class PflegeheimData:
    # From API
    api_id: str = ""
    name: str = ""
    ort: str = ""
    kreis: str = ""
    lat: float = 0.0
    lon: float = 0.0

    # From Impressum
    website: str = ""
    impressum_url: str = ""
    telefon: str = ""
    email: str = ""
    adresse: str = ""
    geschaeftsfuehrung: str = ""
    einrichtungsleitung: str = ""

    # Meta
    status: str = "pending"  # pending | found | no_website | search_failed | error
    error_msg: str = ""


# ---------------------------------------------------------------------------
# Step 1: Fetch all facilities from Heimfinder API
# ---------------------------------------------------------------------------
def fetch_einrichtungen() -> list[PflegeheimData]:
    """Fetch all nursing homes from the official NRW Heimfinder API."""
    log.info("Fetching facilities from Heimfinder API...")
    resp = requests.get(HEIMFINDER_API, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("_items", data) if isinstance(data, dict) else data
    results = []

    for item in items:
        einrichtung = item.get("einrichtung", {})
        adresse = item.get("adresse", {})
        location = item.get("geo_location_epsg_4326", item.get("location", {}))

        ph = PflegeheimData(
            api_id=str(item.get("id", "")),
            name=einrichtung.get("name", "").strip(),
            ort=adresse.get("ort", ""),
            kreis=item.get("kreis", ""),
            lat=location.get("lat", 0.0),
            lon=location.get("lon", 0.0),
        )
        if ph.name:
            results.append(ph)

    log.info(f"Got {len(results)} facilities from API")
    return results


# ---------------------------------------------------------------------------
# Step 2: Find website via search
# ---------------------------------------------------------------------------
def _is_aggregator(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return any(agg in domain for agg in AGGREGATOR_DOMAINS)


def _search_bing(query: str) -> str | None:
    try:
        resp = requests.get(
            "https://www.bing.com/search",
            params={"q": query},
            headers={**HEADERS, "Accept": "text/html"},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("li.b_algo h2 a[href], li.b_algo a.tilk[href]"):
            href = a.get("href", "")
            if href.startswith("http") and not _is_aggregator(href):
                return href
    except Exception as e:
        log.warning(f"Bing fallback failed: {e}")
    return None


def search_website(name: str, ort: str) -> str | None:
    """Search for the facility's website — ddgs primary, Bing HTML fallback."""
    query = f"{name} {ort} Impressum"

    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=8, region="de-de"):
                href = r.get("href", "")
                if href.startswith("http") and not _is_aggregator(href):
                    return href
    except Exception as e:
        log.warning(f"DDGS failed for '{name}': {e}")

    return _search_bing(query)


# ---------------------------------------------------------------------------
# Step 3: Find & fetch Impressum page
# ---------------------------------------------------------------------------
def find_impressum_url(base_url: str, html: str) -> str | None:
    """Find the Impressum link on the homepage."""
    soup = BeautifulSoup(html, "html.parser")

    # Look for links containing "impressum"
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        text = a.get_text(strip=True).lower()
        if "impressum" in href or "impressum" in text:
            full_url = urljoin(base_url, a["href"])
            return full_url

    # Fallback: try common paths
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    common_paths = ["/impressum", "/impressum.html", "/impressum/", "/de/impressum"]
    for path in common_paths:
        try:
            resp = requests.head(
                base + path, headers=HEADERS, timeout=10, allow_redirects=True
            )
            if resp.status_code == 200:
                return resp.url
        except Exception:
            continue

    return None


def fetch_impressum(url: str) -> str | None:
    """Fetch the Impressum page content."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        log.warning(f"Failed to fetch Impressum at {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Step 4: Extract data from Impressum HTML
# ---------------------------------------------------------------------------
def extract_impressum_data(html: str) -> dict:
    """Extract structured data from Impressum page."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove script/style noise
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # Normalize whitespace per line
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    text_block = "\n".join(line for line in lines if line)

    result = {
        "telefon": "",
        "email": "",
        "adresse": "",
        "geschaeftsfuehrung": "",
        "einrichtungsleitung": "",
    }

    # --- Telefon ---
    phone_patterns = [
        r"(?:Tel(?:efon)?|Fon|Phone|Ruf)[\s.:]*([+\d\s\-/()]{7,25})",
        r"(\+49[\s\-/()]*\d[\d\s\-/()]{6,20})",
        r"(0\d{2,4}[\s\-/]\d{3,}[\s\-/]?\d*)",
    ]
    for pat in phone_patterns:
        m = re.search(pat, text_block, re.IGNORECASE)
        if m:
            result["telefon"] = re.sub(r"\s+", " ", m.group(1)).strip()
            break

    # --- Email ---
    # First try mailto links
    mailto = soup.find("a", href=re.compile(r"^mailto:", re.I))
    if mailto:
        result["email"] = mailto["href"].replace("mailto:", "").split("?")[0].strip()
    else:
        email_match = re.search(
            r"[\w.+-]+@[\w-]+\.[\w.-]+", text_block
        )
        if email_match:
            result["email"] = email_match.group(0)

    # --- Adresse ---
    # Match German street + house number + PLZ + Ort
    addr_pattern = r"((?:[A-ZÄÖÜ][a-zäöüß]+[\s\-]*(?:straße|str\.|weg|allee|platz|ring|gasse|damm|ufer|chaussee|pfad))\s*\d+[a-zA-Z]?\s*[\n,]*\s*\d{5}\s+[A-ZÄÖÜ][a-zäöüß\-\s]+)"
    addr_match = re.search(addr_pattern, text_block, re.IGNORECASE)
    if addr_match:
        result["adresse"] = re.sub(r"\s+", " ", addr_match.group(1)).strip()
    else:
        # Fallback: PLZ + Ort pattern
        plz_match = re.search(
            r"(\d{5}\s+[A-ZÄÖÜ][a-zäöüß\-]+(?:\s+[a-zäöüß]+)*)", text_block
        )
        if plz_match:
            # Look backward for street
            pos = text_block.find(plz_match.group(0))
            before = text_block[max(0, pos - 100) : pos].strip()
            street_lines = before.split("\n")
            street = street_lines[-1].strip() if street_lines else ""
            if street and re.search(r"\d", street):  # has house number
                result["adresse"] = f"{street}, {plz_match.group(1)}"
            else:
                result["adresse"] = plz_match.group(1)

    # --- Geschäftsführung ---
    gf_patterns = [
        r"(?:Geschäftsführ(?:ung|er|erin))[\s.:]*([^\n]{3,80})",
        r"(?:Geschäftsleitung)[\s.:]*([^\n]{3,80})",
        r"(?:Vorstand|Vorstandsvorsitzende[r]?)[\s.:]*([^\n]{3,80})",
        r"(?:Vertretungsberechtig(?:t|te|ter))[\s.:]*([^\n]{3,80})",
    ]
    for pat in gf_patterns:
        m = re.search(pat, text_block, re.IGNORECASE)
        if m:
            val = m.group(1).strip().rstrip(",;.")
            # Clean out common suffixes
            val = re.sub(r"\s*(Telefon|Tel|Fon|Fax|E-Mail|Mail|Mobil).*", "", val, flags=re.IGNORECASE)
            if len(val) > 2:
                result["geschaeftsfuehrung"] = val
                break

    # --- Einrichtungsleitung ---
    el_patterns = [
        r"(?:Einrichtungsleitung|Einrichtungsleiter(?:in)?|Heimleitung|Heimleiter(?:in)?)[\s.:]*([^\n]{3,80})",
        r"(?:Pflegedienstleitung|PDL)[\s.:]*([^\n]{3,80})",
        r"(?:Leitung|Leiterin|Leiter)\s+(?:der\s+)?(?:Einrichtung|des\s+Hauses)[\s.:]*([^\n]{3,80})",
    ]
    for pat in el_patterns:
        m = re.search(pat, text_block, re.IGNORECASE)
        if m:
            val = m.group(1).strip().rstrip(",;.")
            val = re.sub(r"\s*(Telefon|Tel|Fon|Fax|E-Mail|Mail|Mobil).*", "", val, flags=re.IGNORECASE)
            if len(val) > 2:
                result["einrichtungsleitung"] = val
                break

    return result


# ---------------------------------------------------------------------------
# Pipeline: process single facility
# ---------------------------------------------------------------------------
def process_facility(ph: PflegeheimData) -> PflegeheimData:
    """Full pipeline for one facility: search → fetch homepage → find impressum → extract."""
    time.sleep(REQUEST_DELAY + random.uniform(0, 1.0))

    # Search for website
    website_url = search_website(ph.name, ph.ort)
    if not website_url:
        # Retry once after a longer pause (rate-limit recovery)
        log.debug(f"Retry search after backoff: {ph.name}")
        time.sleep(random.uniform(5.0, 9.0))
        website_url = search_website(ph.name, ph.ort)

    if not website_url:
        ph.status = "search_failed"
        log.info(f"[SKIP] No website found: {ph.name} ({ph.ort})")
        return ph

    ph.website = website_url

    # Fetch homepage
    try:
        resp = requests.get(website_url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        homepage_html = resp.text
        final_url = resp.url
    except Exception as e:
        ph.status = "error"
        ph.error_msg = f"Homepage fetch failed: {e}"
        log.warning(f"[ERR] {ph.name}: {e}")
        return ph

    # Validate: city name must appear somewhere on the homepage
    page_text = BeautifulSoup(homepage_html, "html.parser").get_text(separator=" ").lower()
    if ph.ort.lower() not in page_text:
        log.info(f"[SKIP] City '{ph.ort}' not on page {website_url} — wrong website")
        ph.status = "search_failed"
        ph.website = ""
        return ph

    # Find Impressum
    impressum_url = find_impressum_url(final_url, homepage_html)
    if not impressum_url:
        # Maybe it's already the impressum or inline
        ph.status = "no_impressum"
        log.info(f"[SKIP] No Impressum link: {ph.name}")
        # Try extracting from homepage anyway
        data = extract_impressum_data(homepage_html)
        for k, v in data.items():
            if v:
                setattr(ph, k, v)
        return ph

    ph.impressum_url = impressum_url

    time.sleep(REQUEST_DELAY)

    # Fetch Impressum
    impressum_html = fetch_impressum(impressum_url)
    if not impressum_html:
        log.info(f"[WARN] Impressum fetch failed, extracting from homepage: {ph.name}")
        data = extract_impressum_data(homepage_html)
        for k, v in data.items():
            if v:
                setattr(ph, k, v)
        ph.status = "found"
        return ph

    # Extract data
    data = extract_impressum_data(impressum_html)
    for k, v in data.items():
        if v:
            setattr(ph, k, v)

    ph.status = "found"
    log.info(
        f"[OK] {ph.name}: Tel={ph.telefon}, Email={ph.email}, "
        f"GF={ph.geschaeftsfuehrung}, EL={ph.einrichtungsleitung}"
    )
    return ph


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def export_results(results: list[PflegeheimData]):
    """Export to CSV (data/) and JSON (output/)."""
    # JSON backup
    json_path = OUTPUT_DIR / "pflegeheime_nrw.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=2)
    log.info(f"JSON exported: {json_path}")

    # CSV in data/
    csv_path = DATA_DIR / "pflegeheime_nrw.csv"
    fieldnames = [
        "api_id", "name", "ort", "kreis", "lat", "lon", "website", "impressum_url",
        "telefon", "email", "adresse", "geschaeftsfuehrung",
        "einrichtungsleitung", "status", "error_msg",
    ]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        for r in results:
            writer.writerow(asdict(r))
    log.info(f"CSV exported: {csv_path}")

    # Stats
    total = len(results)
    found = sum(1 for r in results if r.status == "found")
    no_web = sum(1 for r in results if r.status == "no_website")
    search_failed = sum(1 for r in results if r.status == "search_failed")
    errors = sum(1 for r in results if r.status == "error")
    log.info(
        f"Stats: {total} total, {found} found, {no_web} no website, "
        f"{search_failed} search_failed (retry with --retry-failed), {errors} errors"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def load_from_seed(path: str) -> list[PflegeheimData]:
    """Load facility list from a previously saved JSON seed file."""
    log.info(f"Loading from seed file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Support both raw API format and our export format
    results = []
    items = data.get("_items", data) if isinstance(data, dict) else data
    for item in items:
        if "api_id" in item:
            # Our export format
            results.append(PflegeheimData(**item))
        else:
            # Raw API format
            einrichtung = item.get("einrichtung", {})
            adresse = item.get("adresse", {})
            location = item.get("geo_location_epsg_4326", item.get("location", {}))
            ph = PflegeheimData(
                api_id=str(item.get("id", "")),
                name=einrichtung.get("name", "").strip(),
                ort=adresse.get("ort", ""),
                kreis=item.get("kreis", ""),
                lat=location.get("lat", 0.0),
                lon=location.get("lon", 0.0),
            )
            if ph.name:
                results.append(ph)

    log.info(f"Loaded {len(results)} facilities from seed")
    return results


def main():
    parser = argparse.ArgumentParser(description="Pflegeheim NRW Impressum Scraper")
    parser.add_argument(
        "--seed", type=str, default=None,
        help="Path to seed JSON (skip API fetch). Use 'fetch' to save API response as seed.",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Limit number of facilities to process (0 = all)",
    )
    parser.add_argument(
        "--resume", type=str, default=None,
        help="Path to previous results JSON to resume from (skip already processed)",
    )
    parser.add_argument(
        "--delay", type=float, default=1.5,
        help="Delay between requests in seconds (default: 1.5)",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="When used with --resume, also retry facilities with status 'search_failed'",
    )
    args = parser.parse_args()

    global REQUEST_DELAY
    REQUEST_DELAY = args.delay

    # Step 1: Get facility list
    if args.seed == "fetch":
        # Just fetch and save the API data
        log.info("Fetching API data and saving as seed...")
        resp = requests.get(HEIMFINDER_API, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        seed_path = OUTPUT_DIR / "heimfinder_seed.json"
        with open(seed_path, "w", encoding="utf-8") as f:
            f.write(resp.text)
        log.info(f"Seed saved to {seed_path}")
        data = resp.json()
        items = data.get("_items", data) if isinstance(data, dict) else data
        log.info(f"Contains {len(items)} facilities. Run again with --seed {seed_path}")
        return

    if args.seed:
        facilities = load_from_seed(args.seed)
    else:
        facilities = fetch_einrichtungen()
        # Auto-save seed
        seed_path = OUTPUT_DIR / "heimfinder_seed.json"
        resp = requests.get(HEIMFINDER_API, headers=HEADERS, timeout=30)
        with open(seed_path, "w", encoding="utf-8") as f:
            f.write(resp.text)
        log.info(f"Seed auto-saved to {seed_path}")

    # Limit
    if args.limit > 0:
        facilities = facilities[: args.limit]

    # Resume: skip already processed
    already_done = set()
    results = []
    if args.resume:
        prev = load_from_seed(args.resume)
        skip_statuses = {"pending"} if args.retry_failed else {"pending", "search_failed"}
        for ph in prev:
            if ph.status not in skip_statuses:
                already_done.add(ph.api_id)
                results.append(ph)
        log.info(f"Resuming: {len(already_done)} already processed, skipping them")

    log.info(f"Processing {len(facilities)} facilities...")

    for i, ph in enumerate(facilities):
        if ph.api_id in already_done:
            continue

        log.info(f"[{i+1}/{len(facilities)}] {ph.name} ({ph.ort})")
        result = process_facility(ph)
        results.append(result)

        # Checkpoint every 50
        if (i + 1) % 50 == 0:
            export_results(results)
            log.info(f"Checkpoint at {i+1}")

    export_results(results)
    log.info("Done.")


if __name__ == "__main__":
    main()
