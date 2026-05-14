"""Round 2 cleanup: edge cases the first pass didn't catch.

Phone:
  - em-dash separator (–, U+2013)
  - parens around Vorwahl
  - JSON-array-as-string  ["...", "..."]
  - boilerplate "keine ... gefunden" → No clear Data

Email:
  - (at) and [at] obfuscation → @
  - reversed-string anti-scrape (ed.dlefeleib(ta)X → reverse + decode)
  - base64-encoded email
  - boilerplate "keine Email angegeben" / "Nachricht senden" → No clear Data

Address (PLZ missing):
  - reverse-geocode via Nominatim (1 req/s) using row's lat/lon
"""

import os
import re
import json
import time
import base64
import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

from data_cleaner import (
    PHONE_RE, EMAIL_RE, PLZ_RE, MUNICH_PHONE_RE,
    validate_cleaned,
)
from fix_suspects import normalize_ort

load_dotenv()

# Em-dash (U+2013) plus normal hyphen + JSON array detection
EM_DASH = "–"

JUNK_PHONE_RE = re.compile(
    r"\b(keine?\s+verl.ssliche?n?\s+telefon|keine?\s+telefon|"
    r"telefon\s+unklar|nicht\s+gefunden|no\s+phone\s+found)\b",
    re.IGNORECASE,
)

JUNK_EMAIL_RE = re.compile(
    r"\b(keine?\s+verl.ssliche?n?\s+email|keine?\s+email\s+(angegeben|gefunden|verf.gbar)|"
    r"nachricht\s+senden|kontakt\s+formular|no\s+email\s+found|"
    r"keine?\s+verl.ssliche?n?\s+daten)\b",
    re.IGNORECASE,
)

EMAIL_FIND_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
BASE64_LIKELY_RE = re.compile(r"^[A-Za-z0-9+/]{16,}={0,2}$")

NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "PflegeheimDataCleaner/0.1 (benedikt.linn@code.berlin)"


def normalize_phone(s: str) -> str:
    """Strip parens, normalize em-dash to '-', collapse spaces."""
    s = s.replace("(", "").replace(")", "")
    s = s.replace(EM_DASH, "-")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fix_phone(tel: str) -> tuple[str, list[str]]:
    """Return (fixed, leftovers). Handles JSON-array, em-dash, parens, boilerplate."""
    if not tel:
        return tel, []
    if PHONE_RE.match(tel):
        return tel, []

    if JUNK_PHONE_RE.search(tel):
        return "No clear Data", []

    candidates: list[str] = []

    # JSON-array case: '["...", "..."]'
    s = tel.strip()
    if s.startswith("[") and s.endswith("]"):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                candidates = [str(x).strip() for x in arr if x]
        except Exception:
            pass

    # Otherwise split on common separators (incl. em-dash as separator only when
    # surrounded by spaces — em-dash within a number is part of the number).
    if not candidates:
        candidates = re.split(r"\s*(?:,|;|\|| / | oder | or | und )\s*", s, flags=re.IGNORECASE)

    # Normalize each candidate (strip parens, em-dash inside number → hyphen)
    candidates = [normalize_phone(c) for c in candidates if c]
    valid = [c for c in candidates if PHONE_RE.match(c)]
    if not valid:
        return tel, []
    return valid[0], valid[1:]


def fix_email(email: str) -> tuple[str, list[str]]:
    """Handle (at)/[at] obfuscation, reversed-strings, base64, junk."""
    if not email:
        return email, []
    s = email.strip()
    if EMAIL_RE.match(s):
        return s, []
    if JUNK_EMAIL_RE.search(s):
        return "No clear Data", []

    # 1) (at) / [at] → @  AND  (dot)/[dot] → .
    obf = s
    obf = re.sub(r"\s*[\(\[]\s*at\s*[\)\]]\s*", "@", obf, flags=re.IGNORECASE)
    obf = re.sub(r"\s*[\(\[]\s*dot\s*[\)\]]\s*", ".", obf, flags=re.IGNORECASE)
    if EMAIL_RE.match(obf):
        return obf, []

    # 2) Reversed-string anti-scrape (typical: starts with `ed.` because reversed `.de`)
    if obf.startswith("ed."):
        rev = obf[::-1]
        # the (ta) pattern reversed becomes (at) — already handled above if we re-apply
        rev = re.sub(r"\s*[\(\[]\s*at\s*[\)\]]\s*", "@", rev, flags=re.IGNORECASE)
        if EMAIL_RE.match(rev):
            return rev, []

    # 3) base64
    if BASE64_LIKELY_RE.match(s):
        try:
            decoded = base64.b64decode(s, validate=True).decode("utf-8", errors="ignore")
            if EMAIL_RE.match(decoded):
                return decoded, []
        except Exception:
            pass

    # 4) extract first valid email from anywhere in the string (legacy behaviour)
    matches = EMAIL_FIND_RE.findall(s)
    valid = [m for m in matches if EMAIL_RE.match(m)]
    if valid:
        return valid[0], valid[1:]

    return email, []


def reverse_geocode(lat: float, lon: float, session: requests.Session) -> str | None:
    """Look up address from coordinates via Nominatim. Rate-limited 1 req/s by caller."""
    try:
        r = session.get(
            NOMINATIM,
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 18, "addressdetails": 1},
            headers={"User-Agent": USER_AGENT, "Accept-Language": "de"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        a = data.get("address", {})
        road = a.get("road") or ""
        house = a.get("house_number") or ""
        plz = a.get("postcode") or ""
        city = (
            a.get("city") or a.get("town") or a.get("village") or
            a.get("municipality") or a.get("county") or ""
        )
        line = []
        if road:
            line.append((road + " " + house).strip())
        if plz or city:
            line.append((plz + " " + city).strip())
        return ", ".join(line) if line else None
    except Exception:
        return None


def main() -> None:
    conn = psycopg2.connect(
        host=os.getenv("PGHOST"), port=os.getenv("PGPORT"),
        dbname=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"), password=os.getenv("PGPASSWORD"),
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ---- Phase A: format-edge cases ----
    cur.execute(
        """
        SELECT api_id, ort, lat, lon,
               telefon_clean, email_clean, adresse_clean,
               geschaeftsfuehrung_clean, einrichtungsleitung_clean
        FROM pflegeheime
        WHERE quality='suspect'
        """
    )
    rows = cur.fetchall()
    upd = conn.cursor()
    a_tel = a_email = 0
    for r in rows:
        tel = r["telefon_clean"] or ""
        email = r["email_clean"] or ""

        new_tel, _ = fix_phone(tel)
        new_email, _ = fix_email(email)

        if new_tel != tel or new_email != email:
            if new_tel != tel: a_tel += 1
            if new_email != email: a_email += 1
            upd.execute(
                "UPDATE pflegeheime SET telefon_clean=%s, email_clean=%s WHERE api_id=%s",
                (new_tel, new_email, r["api_id"]),
            )
    conn.commit()
    print(f"phase A — phone fixes:  {a_tel}")
    print(f"phase A — email fixes:  {a_email}")

    # ---- Phase B: reverse-geocode rows without PLZ ----
    cur.execute(
        """
        SELECT api_id, ort, lat, lon, adresse_clean
        FROM pflegeheime
        WHERE quality='suspect'
          AND adresse_clean IS NOT NULL
          AND adresse_clean NOT IN ('','No clear Data')
          AND lat IS NOT NULL AND lon IS NOT NULL
          AND adresse_clean !~ '\\m\\d{5}\\M'
        """
    )
    geo_rows = cur.fetchall()
    print(f"phase B — reverse-geocoding {len(geo_rows)} rows (1 req/s)…")
    sess = requests.Session()
    geo_fixed = 0
    for r in geo_rows:
        addr = reverse_geocode(float(r["lat"]), float(r["lon"]), sess)
        time.sleep(1.0)  # Nominatim policy
        if addr and PLZ_RE.search(addr):
            upd.execute(
                "UPDATE pflegeheime SET adresse_clean=%s WHERE api_id=%s",
                (addr, r["api_id"]),
            )
            geo_fixed += 1
    conn.commit()
    print(f"phase B — geocoded fixes: {geo_fixed}")

    # ---- Phase C: re-validate everything ----
    cur.execute(
        """
        SELECT api_id, ort,
               telefon_clean, email_clean, adresse_clean,
               geschaeftsfuehrung_clean, einrichtungsleitung_clean, quality
        FROM pflegeheime WHERE quality='suspect'
        """
    )
    rows = cur.fetchall()
    flipped = 0
    for r in rows:
        cleaned = {
            "telefon": r["telefon_clean"] or "",
            "email": r["email_clean"] or "",
            "adresse": r["adresse_clean"] or "",
            "geschaeftsfuehrung": r["geschaeftsfuehrung_clean"] or "",
            "einrichtungsleitung": r["einrichtungsleitung_clean"] or "",
            "notes": "",
        }
        cleaned = validate_cleaned(cleaned, normalize_ort(r["ort"] or ""))
        if cleaned["quality"] != r["quality"]:
            flipped += 1
        upd.execute(
            """UPDATE pflegeheime SET quality=%s, clean_notes=NULLIF(%s,'')
               WHERE api_id=%s""",
            (cleaned["quality"], cleaned.get("notes", ""), r["api_id"]),
        )
    conn.commit()
    print(f"phase C — re-validated, flipped quality: {flipped}")

    # ---- Final report ----
    upd.execute(
        """SELECT cleaner, quality, COUNT(*) FROM pflegeheime
           WHERE cleaner IS NOT NULL GROUP BY cleaner, quality ORDER BY cleaner, quality"""
    )
    print("\nfinal:")
    for cl, q, n in upd.fetchall():
        print(f"  {cl:<28} {q:<10} {n}")


if __name__ == "__main__":
    main()
