"""Auto-cleanup pass over suspect rows.

Fixes the three big buckets:
1. Telefon-Format ungewöhnlich     → split on common separators, pick first valid
2. Email-Format ungewöhnlich       → regex-extract first valid email
3. Adresse-Ort nicht gefunden      → relax match (strip "(Westf.)", ", Rhld." etc.)

Plus:
4. München-Vorwahl-Halluzination   → nuke phone (set to "No clear Data")

After fixing, re-runs validate_cleaned() so quality flips ok/suspect/empty correctly.
No LLM calls.
"""

import os
import re
from data_cleaner import db_connect
from dotenv import load_dotenv

from data_cleaner import (
    PHONE_RE, EMAIL_RE, PLZ_RE, MUNICH_PHONE_RE,
    validate_cleaned,
)

load_dotenv()

# Strip these suffixes from `ort` before doing case-insensitive substring match.
# Mostly German cities with regional disambiguation parens or commas.
ORT_SUFFIX_RE = re.compile(
    r"\s*[\(,]\s*(?:westf?\.?|rhld\.?|rheinland|westfalen|ruhr|sieg|"
    r"sauerland|niederrhein)\s*\)?$",
    re.IGNORECASE,
)

# How to split a multi-value field. Order matters: try the most specific first.
TEL_SEP_RE = re.compile(r"\s*(?:,|;|\||/(?!\d)|\s+oder\s+|\s+or\s+|\s+und\s+)\s*", re.IGNORECASE)
EMAIL_FIND_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def normalize_ort(ort: str) -> str:
    """Strip regional suffixes so 'Halle (Westf.)' matches 'Halle' in address."""
    if not ort:
        return ""
    return ORT_SUFFIX_RE.sub("", ort).strip()


def fix_telefon(tel: str) -> tuple[str, list[str]]:
    """Return (fixed_tel, leftover_others). If nothing valid, returns ('', [])."""
    if not tel or PHONE_RE.match(tel):
        return tel, []
    candidates = [c.strip() for c in TEL_SEP_RE.split(tel) if c.strip()]
    if not candidates:
        return tel, []
    valid = [c for c in candidates if PHONE_RE.match(c)]
    if not valid:
        return tel, []
    return valid[0], valid[1:]


def fix_email(email: str) -> tuple[str, list[str]]:
    """Find first valid email in the field; return (fixed, leftover_others)."""
    if not email or EMAIL_RE.match(email):
        return email, []
    matches = EMAIL_FIND_RE.findall(email)
    if not matches:
        return email, []
    valid = [m for m in matches if EMAIL_RE.match(m)]
    if not valid:
        return email, []
    return valid[0], valid[1:]


def main() -> None:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT api_id, name, ort, quality,
               telefon_clean, email_clean, adresse_clean,
               geschaeftsfuehrung_clean, einrichtungsleitung_clean, clean_notes
        FROM pflegeheime
        WHERE quality = 'suspect'
        """
    )
    rows = cur.fetchall()
    print(f"processing {len(rows)} suspect rows…\n")

    upd = conn.cursor()
    fixed_tel = 0
    fixed_email = 0
    nuked_munich = 0
    relaxed_ort = 0
    flipped_ok = 0
    transitions = {}

    for r in rows:
        tel = r["telefon_clean"] or ""
        email = r["email_clean"] or ""
        addr = r["adresse_clean"] or ""
        ort = r["ort"] or ""
        ort_norm = normalize_ort(ort)

        # --- Munich-hallucination: nuke phone ---
        if tel and MUNICH_PHONE_RE.match(tel) and ort_norm and ort_norm.lower() not in {"münchen", "munich"}:
            tel = "No clear Data"
            nuked_munich += 1

        # --- Tel split + pick first valid ---
        new_tel, extra_tels = fix_telefon(tel)
        if new_tel != tel:
            tel = new_tel
            fixed_tel += 1

        # --- Email extract first valid ---
        new_email, extra_emails = fix_email(email)
        if new_email != email:
            email = new_email
            fixed_email += 1

        # Track which ones used the relaxed ort-match
        if ort_norm != ort and ort_norm and ort_norm.lower() in addr.lower():
            relaxed_ort += 1

        # Build cleaned dict and re-validate using normalized ort
        cleaned = {
            "telefon": tel,
            "email": email,
            "adresse": addr,
            "geschaeftsfuehrung": r["geschaeftsfuehrung_clean"] or "",
            "einrichtungsleitung": r["einrichtungsleitung_clean"] or "",
            "notes": "",  # rebuild fresh
        }
        cleaned = validate_cleaned(cleaned, ort_norm)

        # Append leftover tels/emails to notes (preserve them, don't lose data)
        extras = []
        if extra_tels:
            extras.append(f"weitere Tel: {', '.join(extra_tels)}")
        if extra_emails:
            extras.append(f"weitere Email: {', '.join(extra_emails)}")
        if extras:
            existing = (cleaned.get("notes") or "").strip()
            sep = " | " if existing else ""
            cleaned["notes"] = existing + sep + "; ".join(extras)

        new_q = cleaned["quality"]
        key = f"suspect → {new_q}"
        transitions[key] = transitions.get(key, 0) + 1
        if new_q == "ok":
            flipped_ok += 1

        upd.execute(
            """
            UPDATE pflegeheime SET
              quality = %s,
              clean_notes = NULLIF(%s, ''),
              telefon_clean = %s,
              email_clean = %s
            WHERE api_id = %s
            """,
            (new_q, cleaned.get("notes", ""), tel, email, r["api_id"]),
        )

    conn.commit()

    print(f"telefon split-fix:        {fixed_tel}")
    print(f"email extract-fix:        {fixed_email}")
    print(f"munich-halu-nuke:         {nuked_munich}")
    print(f"ort-match relaxed:        {relaxed_ort}")
    print()
    print("transitions:")
    for k, n in sorted(transitions.items()):
        print(f"  {k:<22} {n}")
    print(f"\nflipped to OK: {flipped_ok}")

    upd.execute(
        """
        SELECT cleaner, quality, COUNT(*)
        FROM pflegeheime
        WHERE cleaner IS NOT NULL
        GROUP BY cleaner, quality
        ORDER BY cleaner, quality
        """
    )
    print("\nfinal:")
    for cl, q, n in upd.fetchall():
        print(f"  {cl:<28} {q:<10} {n}")


if __name__ == "__main__":
    main()
