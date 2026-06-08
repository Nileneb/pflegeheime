"""Round 4: targeted edge-cases left after round 3.

1. Reversed-email with reversed brackets: 'ed.dlefeleib(ta)X' → reverse →
   'X)at(bielefeld.de'. Round 3 only matched (at), not )at(. Fix: be bracket-agnostic.
2. WordPress '[email protected]' literal in email field → No clear Data.
3. 'X@spam protectY-Z.de' obfuscation → strip 'spam protect' word.
4. Looser JUNK_PHONE_RE accepting 'Kein verlässliches Telefonnummern…' (mixed adjective forms).
"""

import os
import re
from dotenv import load_dotenv

from data_cleaner import db_connect, PHONE_RE, EMAIL_RE, validate_cleaned
from fix_suspects import normalize_ort

load_dotenv()

# Looser — match any verb form: kein/keine/keines, verlässlich(e/es/en/em)
JUNK_PHONE_RE = re.compile(r"keine?s?\s+\w*\s*telefon", re.IGNORECASE)
JUNK_EMAIL_RE = re.compile(
    r"keine?s?\s+\w*\s*(email|e-?mail|emailadresse)|"
    r"nachricht\s+senden|kontakt\s*formular|"
    r"keine?s?\s+\w*\s*daten|"
    r"no\s+(clear|specific)?\s*email\s+found",
    re.IGNORECASE,
)

EMAIL_FIND_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
EMAIL_PROTECTED_RE = re.compile(r"\[email[\s_]*protected\]", re.IGNORECASE)
SPAM_PROTECT_RE = re.compile(r"\s*spam\s*protect\s*", re.IGNORECASE)

# Bracket-agnostic (at) replacement — matches (at), )at(, [at], ]at[, just \W+ around 'at'
AT_OBFUSC_RE = re.compile(r"[\(\)\[\]\{\}<>]+\s*at\s*[\(\)\[\]\{\}<>]+", re.IGNORECASE)
DOT_OBFUSC_RE = re.compile(r"[\(\)\[\]\{\}<>]+\s*dot\s*[\(\)\[\]\{\}<>]+", re.IGNORECASE)


def fix_phone(tel: str) -> str:
    if not tel:
        return tel
    if PHONE_RE.match(tel):
        return tel
    if JUNK_PHONE_RE.search(tel):
        return "No clear Data"
    return tel


def _email_variants(s: str) -> str | None:
    if EMAIL_RE.match(s):
        return s
    d = AT_OBFUSC_RE.sub("@", s)
    d = DOT_OBFUSC_RE.sub(".", d)
    if EMAIL_RE.match(d):
        return d
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
    if EMAIL_PROTECTED_RE.search(s):
        return "No clear Data"

    # 'X@spam protectY-Z.de' → strip the 'spam protect' word
    s_clean = SPAM_PROTECT_RE.sub("", s).strip()
    if s_clean != s:
        v = _email_variants(s_clean)
        if v:
            return v

    # Try original + reversed
    candidates = [s_clean, s_clean[::-1]] if s_clean.startswith(("ed.", "moc.", "gro.")) else [s_clean]
    if not candidates[0].startswith(("ed.", "moc.", "gro.")):
        # Heuristic: if original starts with reversed-tld marker, also try reversed
        if s.startswith(("ed.", "moc.", "gro.")):
            candidates = [s, s[::-1]]
    for c in candidates:
        v = _email_variants(c)
        if v:
            return v

    return email


def main() -> None:
    conn = db_connect()
    print("\nfinal:")
    for cl, q, n in upd.fetchall():
        print(f"  {cl:<28} {q:<10} {n}")


if __name__ == "__main__":
    main()
