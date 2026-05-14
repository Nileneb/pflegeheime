"""Round 5: very-specific edge cases that surfaced in round 4 inspection.

Email:
  - 'info @ X.de'          → strip whitespace around @
  - '[email@]X.de'         → strip [email@] prefix
  - 'www.X.de'             → URL not email → No clear Data
  - 'X@...' / 'X@...lined' → placeholder fragments → No clear Data
  - 'X@residenz–phoenix.de'→ em-dash in domain → replace with hyphen

Phone:
  - '+49 X (ServiceCenter)'→ strip parenthetical context
  - '02181/6XXXXXX'        → masked → No clear Data
  - '02902...'             → incomplete fragment → No clear Data
"""

import os
import re
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from data_cleaner import PHONE_RE, EMAIL_RE, validate_cleaned
from fix_suspects import normalize_ort

load_dotenv()

EMAIL_FIND_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
EM_DASH = "–"  # –


def fix_phone(tel: str) -> str:
    if not tel:
        return tel
    if PHONE_RE.match(tel):
        return tel

    # mask / incomplete / placeholder
    if re.search(r"X{3,}|\.{3,}", tel):
        return "No clear Data"

    # strip parenthetical context: '+49 X (ServiceCenter Essen)' → '+49 X'
    stripped = re.sub(r"\s*\([^)]*\)\s*$", "", tel).strip()
    if PHONE_RE.match(stripped):
        return stripped

    return tel


def fix_email(email: str) -> str:
    if not email:
        return email
    s = email.strip()
    if EMAIL_RE.match(s):
        return s

    # placeholder fragments
    if re.search(r"\.{3,}", s):
        return "No clear Data"

    # 'www.X.de' → URL not email
    if re.match(r"^(https?://|www\.)", s, re.IGNORECASE) and "@" not in s:
        return "No clear Data"

    # '[email@]X.de' → strip the prefix
    s_clean = re.sub(r"^\s*\[email@\]\s*", "", s, flags=re.IGNORECASE)

    # strip whitespace around @ : 'info @ X.de' → 'info@X.de'
    s_clean = re.sub(r"\s*@\s*", "@", s_clean)

    # em-dash inside domain → hyphen
    s_clean = s_clean.replace(EM_DASH, "-")

    if EMAIL_RE.match(s_clean):
        return s_clean

    # extract any valid email
    for m in EMAIL_FIND_RE.findall(s_clean):
        if EMAIL_RE.match(m):
            return m

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

    a_tel = a_email = flipped = 0
    for r in rows:
        old_t = r["telefon_clean"] or ""
        old_e = r["email_clean"] or ""
        new_t = fix_phone(old_t)
        new_e = fix_email(old_e)
        if new_t != old_t: a_tel += 1
        if new_e != old_e: a_email += 1

        cleaned = {
            "telefon": new_t,
            "email": new_e,
            "adresse": r["adresse_clean"] or "",
            "geschaeftsfuehrung": r["geschaeftsfuehrung_clean"] or "",
            "einrichtungsleitung": r["einrichtungsleitung_clean"] or "",
            "notes": "",
        }
        cleaned = validate_cleaned(cleaned, normalize_ort(r["ort"] or ""))
        if cleaned["quality"] != "suspect":
            flipped += 1
        upd.execute(
            """UPDATE pflegeheime SET telefon_clean=%s, email_clean=%s,
                                       quality=%s, clean_notes=NULLIF(%s,'')
               WHERE api_id=%s""",
            (new_t, new_e, cleaned["quality"], cleaned.get("notes", ""), r["api_id"]),
        )
    conn.commit()

    print(f"phone fixes:   {a_tel}")
    print(f"email fixes:   {a_email}")
    print(f"quality flips: {flipped}")

    upd.execute(
        """SELECT cleaner, quality, COUNT(*) FROM pflegeheime
           WHERE cleaner IS NOT NULL GROUP BY cleaner, quality ORDER BY cleaner, quality"""
    )
    print("\nfinal:")
    for cl, q, n in upd.fetchall():
        print(f"  {cl:<28} {q:<10} {n}")


if __name__ == "__main__":
    main()
