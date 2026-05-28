#!/usr/bin/env python3
"""Träger-Namen sauber aus dem gecachten Impressum-Text neu extrahieren.

Der alte guess_traeger() (crawl_impressum_gf.py TRAEGER_RE) lieferte Müll:
„Impressum … GmbH" (Präfix nicht entfernt), „… Palliative V" (Bug: optionaler
Punkt ließ `e\\.?V` ein einzelnes „V" nach e-Endung matchen), „USt-Ident… V",
„Urheberrecht. Die V". Wir extrahieren neu aus `domain_impressum.impressum_text`
(grounded) mit striktem Cleaning und nullen, was sich nicht säubern lässt.

  python -u scripts/fix_traeger.py            # dry-run (zeigt vorher/nachher)
  python -u scripts/fix_traeger.py --apply
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect

# Rechtsform — e.V. verlangt PFLICHT-Punkt nach e (sonst matcht „Palliative V").
RECHTSFORM = (r"(?:gGmbH|gemeinnützige\s+GmbH|GmbH\s*&\s*Co\.?\s*KGaA|GmbH\s*&\s*Co\.?\s*KG|"
              r"GmbH|gAG|\bKGaA\b|\bAG\b|\bSE\b|e\.\s?V\.?|\beG\b|\bKG\b|GbR|gUG|"
              r"\bUG\b\s*(?:\(haftungsbeschränkt\))?|Stiftung\s*&\s*Co\.?\s*KG|Stiftung)")
CAND = re.compile(r"([A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&.\-/ ]{2,80}?\b" + RECHTSFORM + r")")

LEAD_NOISE = {
    "impressum", "kontakt", "herausgeber", "diensteanbieter", "über", "anschrift",
    "adresse", "datenschutz", "startseite", "home", "angaben", "vertreten", "durch",
    "telefon", "fax", "sie", "wir", "der", "die", "das", "verantwortlich", "inhaltlich",
    "name", "firma", "betreiber", "träger", "trägerschaft", "seiten", "service",
    "schutz", "hinweis", "gemäß", "tmg", "nach", "und", "im", "am", "die",
    "anbieter", "diensteanbieter", "inhaber", "eigentümer", "verein",
    # Navigations-/Footer-Wörter, die vor dem Namen im Impressum-Text kleben
    "teilzeit", "vollzeit", "mehr", "erfahren", "menü", "menu", "suche", "schließen",
    "öffnen", "weiter", "zurück", "mitarbeiter", "aktuelles", "leistungen", "angebote",
    "standorte", "standort", "unternehmen", "presse", "spenden", "newsletter",
    "mitmachen", "mitgliedschaft", "downloads", "veranstaltungen", "termine",
}
FORBIDDEN = ("impressum", "kontakt", "herausgeber", "rechtsform", "ust", "ident",
             "steuernummer", "urheberrecht", "gesetzliche", "verantwortlich",
             "insbesondere", "unsere", "hygiene", "kooperationspartner",
             "datenschutz", "telefon", "vertretungsberechtigt", "registergericht",
             "amtsgericht", "handelsregister", "vereinsregister", "stellenangebot",
             "karriere", "geschäftsführer", "geschäftsführung", "vorstand",
             "prokurist", "vertreten durch", "@", "www", "http",
             # Web-Agentur-Credits (nicht der Träger)
             "design", "programmierung", "webdesign", "website", "webseite",
             "realisierung", "realisiert", "umsetzung", "gestaltung", "konzeption",
             "agentur", "webentwicklung", "powered", "©", "copyright",
             "doccheck", "dochcheck", "jimdo", "wix", "wordpress",
             "urheber", "kennzeichenrecht", "darlehnskasse", "volksbank", "sparkasse")

REF_END = re.compile(RECHTSFORM + r"\.?$")
import re as _re2
_NORM = lambda s: _re2.sub(r"[^a-zäöüß]", "", s.lower())

# generische Wörter (kein Eigenname) — ein guter Träger-Name hat >=1 Token außerhalb
GENERIC = {
    "kirchliche", "kirchlichen", "katholische", "katholischen", "evangelische",
    "evangelischen", "evangelisch", "christliche", "deutsche", "deutschen",
    "gemeinnützige", "gemeinnützigen", "seniorenheim", "seniorenzentrum",
    "seniorenresidenz", "seniorenstift", "altenheim", "altenzentrum", "altenhilfe",
    "pflegeheim", "pflegezentrum", "pflege", "haus", "hauses", "stiftung", "gmbh",
    "ggmbh", "gag", "verein", "verband", "gesellschaft", "senioren", "wohnen",
    "residenz", "zentrum", "wohnstift", "stift", "diakonie", "diakonische",
    "diakonisches", "caritas", "und", "der", "die", "das", "für", "am", "im",
    "vom", "von", "hl", "st", "sankt", "co", "kg", "ag", "ug", "ev",
}


def distinctiveness(name: str) -> int:
    return sum(1 for t in name.split()
              if _NORM(t) and _NORM(t) not in GENERIC and len(_NORM(t)) >= 3)


def clean_candidate(s: str):
    s = re.sub(r"\s+", " ", s).strip(" .,;:-")
    # führende Rausch-Wörter iterativ abschneiden; auch „Stadt Die …"-Präfixe
    toks = s.split()
    changed = True
    while changed and toks:
        changed = False
        if re.fullmatch(r"[-–—•·.,/&]+", toks[0]):
            toks.pop(0); changed = True
        elif toks[0].lower().strip(".") in LEAD_NOISE:
            toks.pop(0); changed = True
        elif len(toks) >= 2 and toks[1].lower().strip(".") in {"die", "der", "das"}:
            toks.pop(0); changed = True
    return " ".join(toks)


# Satz-/Füllwörter — ein Träger-Name enthält sie nie (kennzeichnen Fließtext)
WORD_FORBIDDEN = {"ist", "eine", "ein", "wird", "sind", "war", "sowie", "selbstständige",
                  "selbständige", "unsere", "unser", "diese", "dieser", "alle", "wurde"}


def valid(s: str) -> bool:
    if not s or not (6 <= len(s) <= 90):
        return False
    low = s.lower()
    if any(f in low for f in FORBIDDEN):
        return False
    if any(t.lower().strip(".") in WORD_FORBIDDEN for t in s.split()):
        return False
    if re.search(r"\d", s):
        return False
    if not REF_END.search(s):
        return False
    if distinctiveness(s) < 1:        # reine Generik ("Gemeinnützige GmbH") verwerfen
        return False
    return len([t for t in s.split() if len(t) > 1]) >= 2


def extract_traeger(text: str):
    """Träger = häufigster gültiger Kandidat (Betreiber wiederholt sich im Text:
    Kopf, Copyright, Adresse), bei Gleichstand der kürzeste (entfernt GF-Namen-
    Präfixe und „Stellenangebote …"-Rauschen). Suche im oberen Teil — die Web-
    Agentur steht meist ganz unten."""
    if not text:
        return None
    from collections import Counter
    t = re.sub(r"\s+", " ", text)[:4000]
    cands = []
    for m in CAND.finditer(t):
        c = clean_candidate(m.group(1))
        if valid(c):
            cands.append(c)
    if not cands:
        return None
    cnt = Counter(_NORM(c) for c in cands)
    # bevorzuge: >=1 Eigenname-Token, dann häufigster, dann kürzester
    return sorted(cands, key=lambda c: (-min(distinctiveness(c), 1),
                                        -cnt[_NORM(c)], len(c.split()), len(c)))[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--show", type=int, default=30)
    args = ap.parse_args()

    conn = db_connect(); cur = conn.cursor()
    cur.execute("""SELECT domain, traeger, impressum_text FROM domain_impressum
                   WHERE impressum_text IS NOT NULL""")
    rows = cur.fetchall()

    upd = []
    changed = nulled = kept = 0
    samples = []
    for dom, old, text in rows:
        new = extract_traeger(text)
        if new != old:
            if new is None:
                nulled += 1
            else:
                changed += 1
            if len(samples) < args.show:
                samples.append((dom, old, new))
        else:
            kept += 1
        upd.append((new, dom))

    print(f"Domains mit Impressum-Text: {len(rows)}")
    print(f"  geändert: {changed} | genullt (kein sauberer Träger): {nulled} | unverändert: {kept}\n")
    print("=== Stichprobe vorher → nachher ===")
    for dom, old, new in samples:
        print(f"  {dom:30}\n     ALT: {old}\n     NEU: {new}")

    if args.apply:
        cur.executemany("UPDATE domain_impressum SET traeger=%s WHERE domain=%s", upd)
        conn.commit()
        print(f"\nApplied: {len(upd)} Domains aktualisiert.")
    else:
        print("\n(dry-run — mit --apply schreiben)")
    conn.close()


if __name__ == "__main__":
    main()
