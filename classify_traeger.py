"""Classify each Pflegeheim by Träger (operating organization).

Pure pattern matching against name + cleaned email-domain + website host.
No LLM. No external API.

Output: 'traeger' column gets one of:
  Korian | Caritas | Diakonie | DRK | AWO | Johanniter | Malteser |
  Alloheim | Vitanas | Kursana | pro Seniore | Casa Reha | Contilia |
  Auvictum | ESV | ASB | Kommunal | Privat/Sonstige
"""

import os
from data_cleaner import db_connect
import re
from urllib.parse import urlparse
from collections import Counter
from dotenv import load_dotenv

load_dotenv()

ALTER_SQL = "ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS traeger TEXT;"

# Order matters — first match wins. More specific patterns first.
RULES: list[tuple[str, list[str]]] = [
    # (label, list of regex patterns to match against name|email|website)
    ("Korian",      [r"\bkorian\b", r"\bcuranum\b"]),
    ("Caritas",     [r"\bcaritas\b", r"caritasverband"]),
    ("Diakonie",    [r"\bdiakonie\b", r"diakonisch", r"\bdiakonis\b"]),
    ("DRK",         [r"\bdrk\b", r"deutsches?\s+rotes?\s+kreuz", r"rotkreuz"]),
    ("AWO",         [r"\bawo\b", r"arbeiterwohlfahrt"]),
    ("Johanniter",  [r"\bjohanniter\b"]),
    ("Malteser",    [r"\bmalteser\b"]),
    ("Alloheim",    [r"\balloheim\b"]),
    ("Vitanas",     [r"\bvitanas\b"]),
    ("Kursana",     [r"\bkursana\b"]),
    ("pro Seniore", [r"pro[\s-]?seniore"]),
    ("Casa Reha",   [r"casa[\s-]?reha"]),
    ("Contilia",    [r"\bcontilia\b"]),
    ("Auvictum",    [r"\bauvictum\b"]),
    ("ESV Volmarstein", [r"\besv\.de\b", r"evangelische\s+stiftung\s+volmarstein"]),
    ("Vianobis",    [r"\bvianobis\b"]),
    ("ASB",         [r"\basb\b", r"arbeiter[\s-]?samariter"]),
    ("Heinrichs",   [r"heinrichs[-\s]?gruppe"]),
    ("Kursana",     [r"\bkursana\b"]),
    ("FFC-Stiftung", [r"\bffc[-\s]?stiftung\b", r"fuerstin[-\s]?franziska"]),
    ("Heilig-Geist-Stiftung", [r"heilig[-\s]?geist[-\s]?stiftung"]),
    ("Bremmsche Stiftung", [r"bremm.?sche[-\s]?stiftung"]),
    ("Kommunal",    [r"\bkommunal\b", r"stadt\s+\w+\s+gmbh", r"städtisch"]),
    ("Kirchlich (kath.)", [r"\bkath\.?\b", r"\bkatholisch", r"\bpfarr"]),
    ("Kirchlich (ev.)",   [r"\bev\.?\b", r"\bevangelisch", r"\bkirchengemeinde"]),
]


def _norm(s: str | None) -> str:
    """Lowercase, strip url-scheme, get plain searchable text."""
    if not s:
        return ""
    s = str(s).lower()
    # If it's a URL, just take the host
    if s.startswith(("http://", "https://")):
        host = urlparse(s).netloc.lower()
        return host
    return s


def classify(name: str, email: str, website: str) -> str:
    """Return Träger label. 'Privat/Sonstige' if no rule matches."""
    haystack = " ".join([_norm(name), _norm(email), _norm(website)])
    for label, patterns in RULES:
        for p in patterns:
            if re.search(p, haystack, re.IGNORECASE):
                return label
    return "Privat/Sonstige"


def main() -> None:
    conn = db_connect()
    conn.commit()

    print("Träger-Verteilung:")
    for label, n in counter.most_common():
        bar = "█" * (n * 40 // max(counter.values()))
        print(f"  {label:<25} {n:5}  {bar}")
    print(f"\nGesamt: {sum(counter.values())}")
    print(f"Klassifiziert (nicht Privat/Sonstige): "
          f"{sum(counter.values()) - counter['Privat/Sonstige']} "
          f"({100*(sum(counter.values()) - counter['Privat/Sonstige'])/sum(counter.values()):.1f}%)")


if __name__ == "__main__":
    main()
