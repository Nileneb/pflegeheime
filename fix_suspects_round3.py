"""Round 3: edge cases that round 2 missed.

Tightens the JUNK detection (drop trailing \\b → match 'Telefonnummern' too)
and reverses obfuscated email *before* applying (at)/(dot) substitution.
"""

import os
import re
import base64
from dotenv import load_dotenv

from data_cleaner import db_connect, PHONE_RE, EMAIL_RE, validate_cleaned
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
    conn = db_connect()
    print("\nfinal:")
    for cl, q, n in upd.fetchall():
        print(f"  {cl:<28} {q:<10} {n}")


if __name__ == "__main__":
    main()
