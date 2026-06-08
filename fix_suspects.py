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
from dotenv import load_dotenv

from data_cleaner import (
    db_connect,
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
    print("\nfinal:")
    for cl, q, n in upd.fetchall():
        print(f"  {cl:<28} {q:<10} {n}")


if __name__ == "__main__":
    main()
