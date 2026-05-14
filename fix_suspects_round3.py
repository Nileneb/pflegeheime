"""Round 3: edge cases that round 2 missed.

Tightens the JUNK detection (drop trailing \\b → match 'Telefonnummern' too)
and reverses obfuscated email *before* applying (at)/(dot) substitution.
"""

import os
import re
import base64
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from data_cleaner import PHONE_RE, EMAIL_RE, validate_cleaned
from fix_suspects import normalize_ort

load_dotenv()

# More permissive — no trailing word-boundary, allows "Telefonnummern"/"Emails"/"Emailadresse"
JUNK_PHONE_RE = re.compile(
    r"keine?\s+(verl.ssliche?n?\s+)?(telefon|tel\.?\s)",
    re.IGNORECASE,
)
JUNK_EMAIL_RE = re.compile(
    r"keine?\s+(verl.ssliche?n?\s+)?(email|e-?mail|mail|emailadresse)|"
    r"nachricht\s+senden|kontakt\s*formular|"
    r"keine?\s+verl.ssliche?n?\s+daten\b|"
    r"no\s+(clear|specific)?\s*email\s+found",
    re.IGNORECASE,
)

EMAIL_FIND_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
BASE64_LIKELY_RE = re.compile(r"^[A-Za-z0-9+/]{16,}={0,2}$")


def fix_phone(tel: str) -> str:
    """Return cleaned phone; 'No clear Data' for boilerplate; unchanged otherwise."""
    if not tel:
        return tel
    if PHONE_RE.match(tel):
        return tel
    if JUNK_PHONE_RE.search(tel):
        return "No clear Data"
    return tel


def _try_email_variants(s: str) -> str | None:
    """Try a string + (at)/(dot) variants. Return first valid email or None."""
    if EMAIL_RE.match(s):
        return s
    d = re.sub(r"\s*[\(\[]\s*at\s*[\)\]]\s*", "@", s, flags=re.IGNORECASE)
    d = re.sub(r"\s*[\(\[]\s*dot\s*[\)\]]\s*", ".", d, flags=re.IGNORECASE)
    if EMAIL_RE.match(d):
        return d
    # extract any valid email from the string
    for m in EMAIL_FIND_RE.findall(d):
        if EMAIL_RE.match(m):
            return m
    return None


def fix_email(email: str) -> str:
    if not email:
        return email
    s = email.strip()
    if EMAIL_RE.match(s):
        return s
    if JUNK_EMAIL_RE.search(s):
        return "No clear Data"

    # Try reverse first if obviously reversed (ends in '.de'/'.com'/'.org' is hard
    # to detect when reversed; the cheap heuristic is "starts with 'ed.' or 'moc.'").
    candidates = [s]
    if s.startswith(("ed.", "moc.", "gro.", "rd.", "kc.", "ku.")):
        candidates.append(s[::-1])

    for c in candidates:
        v = _try_email_variants(c)
        if v:
            return v

    # base64
    if BASE64_LIKELY_RE.match(s):
        try:
            decoded = base64.b64decode(s, validate=True).decode("utf-8", errors="ignore")
            if EMAIL_RE.match(decoded):
                return decoded
        except Exception:
            pass

    return email


def main() -> None:
    conn = psycopg2.connect(
        host=os.getenv("PGHOST"), port=os.getenv("PGPORT"),
        dbname=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"), password=os.getenv("PGPASSWORD"),
    )
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """SELECT api_id, ort, telefon_clean, email_clean, adresse_clean,
                  geschaeftsfuehrung_clean, einrichtungsleitung_clean
           FROM pflegeheime WHERE quality='suspect'"""
    )
    rows = cur.fetchall()
    upd = conn.cursor()

    a_tel = a_email = 0
    flipped = 0
    for r in rows:
        old_tel = r["telefon_clean"] or ""
        old_email = r["email_clean"] or ""
        new_tel = fix_phone(old_tel)
        new_email = fix_email(old_email)

        if new_tel != old_tel: a_tel += 1
        if new_email != old_email: a_email += 1

        cleaned = {
            "telefon": new_tel,
            "email": new_email,
            "adresse": r["adresse_clean"] or "",
            "geschaeftsfuehrung": r["geschaeftsfuehrung_clean"] or "",
            "einrichtungsleitung": r["einrichtungsleitung_clean"] or "",
            "notes": "",
        }
        cleaned = validate_cleaned(cleaned, normalize_ort(r["ort"] or ""))
        if cleaned["quality"] != "suspect":
            flipped += 1
        upd.execute(
            """UPDATE pflegeheime SET
                 telefon_clean=%s, email_clean=%s,
                 quality=%s, clean_notes=NULLIF(%s,'')
               WHERE api_id=%s""",
            (new_tel, new_email, cleaned["quality"], cleaned.get("notes", ""), r["api_id"]),
        )
    conn.commit()

    print(f"phone fixes:  {a_tel}")
    print(f"email fixes:  {a_email}")
    print(f"flipped quality (mostly suspect→ok|empty): {flipped}")

    upd.execute(
        """SELECT cleaner, quality, COUNT(*) FROM pflegeheime
           WHERE cleaner IS NOT NULL GROUP BY cleaner, quality ORDER BY cleaner, quality"""
    )
    print("\nfinal:")
    for cl, q, n in upd.fetchall():
        print(f"  {cl:<28} {q:<10} {n}")


if __name__ == "__main__":
    main()
