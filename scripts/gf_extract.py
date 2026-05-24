#!/usr/bin/env python3
"""Regex-first Geschäftsführer extractor for German Impressum text.

German Impressum (§5 TMG) names the Vertretungsberechtigten in a few canonical
shapes. We anchor on the keyword, capture forward to the next structural keyword
(Registergericht/Rechtsform/USt/…), and clean. Grounded by construction.

extract_gf(text) -> (gf_string|None, method)  method in {'regex','none'}
"""
import re

GF_ANCHOR = re.compile(
    r"(?:vertretungsberechtigt[a-zäöü]*\s+(?:gesch[aä]ftsf[uü]hr[a-zäöü]*|vorst[aä]nde?)"
    r"|vertreten\s+durch(?:\s+(?:den|die|das|der)\b)?"
    r"|gesch[aä]ftsf[uü]hr(?:er|erin|erinnen|ende[r]?|ung)?"
    r"|vorstand(?:svorsitzende[r]?)?"
    r"|inhaber(?:in)?"
    r"|komplement[aä]r(?:in)?"
    r")\s*(?:der\s+gesellschaft)?\s*:?\s*",
    re.I,
)

STOP = re.compile(
    r"(registergericht|registernummer|registereintrag|handelsregister|vereinsregister|"
    r"\bregister\b|amtsgericht|rechtsform|umsatzsteuer|umst[\.\- ]?id|ust[\.\- ]?id|"
    r"steuernummer|steuer[\- ]?id|\bhrb\b|\bhra\b|\bvr\s?\d|"
    r"verwaltungsrat|aufsichtsrat|kuratorium|gesellschafter|sitz\s+der|geschäftssitz|"
    r"telefon|\btel\.?\b|telefax|\bfax\b|e\-?mail|@|www\.|https?:|"
    r"funktions|beauftragt|datenschutz|verantwortlich\s+f|inhaltlich|redaktion|"
    r"zust[aä]ndige?\s+aufsicht|aufsichtsbeh|berufsbez|kammer|plattform|streitschlicht|"
    r"online[\- ]streit|verbraucherstreit|haftung|disclaimer|copyright|©|"
    r"\bimpressum\b|datenschutzerkl|nutzungsbed|cookie)",
    re.I,
)

# titles kept as part of name
TITLE = r"(?:Dr\.|Prof\.|Dipl\.[\-\wäöü]*|med\.|rer\.|nat\.|jur\.|Pfarrer|Pastor|Diakon|Schwester)"
# leading filler removed
LEAD = re.compile(r"^(?:die|der|den|das|durch|herrn?|frau|den\s+gesch[aä]ftsf[uü]hrer)\s+", re.I)

NAME_TOKEN = re.compile(
    r"(?:" + TITLE + r"\s+)*"
    r"[A-ZÄÖÜ][a-zäöüß]+(?:[\-\.][A-ZÄÖÜ]?[a-zäöüß]*)?"        # first name (or initial)
    r"(?:\s+[A-ZÄÖÜ]\.)?"                                       # middle initial
    r"(?:\s+(?:von|van|de|der|den|zu|zur)\b)*"
    r"\s+[A-ZÄÖÜ][a-zäöüß]+(?:[\-][A-ZÄÖÜ][a-zäöüß]+)?"         # surname
)

ROLE_NOISE = re.compile(
    r"\b(vorsitzende[r]?|stellv\.?|stellvertretende[r]?|ceo|cfo|coo|finanzen|"
    r"betrieb|gesch[aä]ftsf[uü]hr[a-zäöü]*|vorstand[a-zäöü]*|sprecher[a-zäöü]*)\b",
    re.I,
)

# Words that disqualify a token sequence from being a person name.
NON_NAME = {
    # Rechtsform / Organisation
    "gmbh", "ggmbh", "ag", "gag", "kg", "gbr", "ug", "gug", "co", "mbh", "ohg", "se",
    "ev", "e.v", "verein", "verband", "gesellschaft", "stiftung", "werk", "werke",
    "holding", "gruppe", "group", "konzern", "betriebsgesellschaft",
    # Träger / Einrichtungstyp
    "haus", "hauses", "senioren", "seniorenzentrum", "seniorenheim", "seniorenresidenz",
    "seniorenstift", "senioreneinrichtung", "senioreneinrichtungen", "altenheim",
    "altenzentrum", "altenhilfe", "pflege", "pflegeheim", "pflegezentrum", "pflegedienst",
    "wohnstift", "residenz", "zentrum", "stift", "klinik", "diakonie", "caritas",
    "kirchengemeinde", "pfarrgemeinde", "gemeinde", "orden", "deutsche", "deutschen",
    "deutscher", "deutschland", "trägerschaft", "träger", "kuratorium",
    # Abteilung / Rolle
    "zentrale", "dienste", "dienst", "verwaltung", "verwaltungs", "qualitätsmanagement",
    "hygiene", "kontakt", "beauftragte", "beauftragter", "leitung", "geschäftsbereich",
    "bereich", "abteilung", "sekretariat", "empfang", "soziale", "sozialer", "sozialdienst",
    "vertretungsberechtigt", "vertretungsberechtigte", "vorstand", "geschäftsführung",
    "geschäftsführer", "aufnahme", "beratung", "team", "standort", "standorte",
    # Geo / Adresse
    "straße", "strasse", "str", "weg", "platz", "allee", "ring", "gasse", "ufer",
    "essen", "köln", "münster", "düsseldorf", "bonn", "aachen", "bezirksregierung",
    # Pronomen / Satzanfänge / Disclaimer-/Web-Vokabular (regex false positives)
    "sie", "wir", "ich", "uns", "unsere", "unser", "ihre", "ihren", "diese", "dieser",
    "sollten", "bitte", "wenn", "falls", "hier", "dies", "das", "alle", "kein", "keine",
    "internetauftritts", "internetauftritt", "rechtsverstöße", "rechtsverstoße",
    "inhalte", "inhalt", "hinweis", "hinweise", "haftung", "gewähr", "urheberrecht",
    "quelle", "quellen", "verlinkung", "links", "datenschutzerklärung", "nutzungsbedingungen",
    "konzeption", "gestaltung", "umsetzung", "realisierung", "programmierung", "webdesign",
    "fotos", "foto", "bilder", "copyright", "redaktionell", "verantwortlich", "redaktion",
    # weitere beobachtete Impressum-Nomen (keine Personennamen)
    "medizinproduktesicherheit", "zertifikate", "zertifikat", "generic", "übereinstimmung",
    "suche", "titel", "prokurist", "prokuristen", "rediger", "steuerliche", "obere",
    "untere", "fuhr", "wandrahm", "hansa", "hamburg", "berlin", "münchen",
    "geschäftsstelle", "hauptverwaltung", "vorsitz", "schriftführer", "kassenwart",
    # Navigations-/Footer-Vokabular (Websuche-Treffer)
    "aktuelles", "aktuell", "mitmachen", "mitgliedschaft", "spenden", "spende",
    "mediathek", "projekte", "projekt", "historie", "presse", "downloads", "download",
    "services", "service", "karriere", "expansion", "medien", "kosten", "qualität",
    "betreuung", "leistungen", "angebote", "angebot", "kontaktformular", "anfahrt",
    "stellenangebote", "jobs", "news", "newsletter", "veranstaltungen", "termine",
    "startseite", "navigation", "suche", "menü", "datenschutz", "cookie", "cookies",
    "prokuristin", "stiftungsregister", "vereinsregister", "handelsregister",
    "stiftungsaufsicht", "gerichtsstand", "angaben", "registergericht", "registernummer",
    "stiftungsverzeichnis", "aufsichtsbehörde", "berufsbezeichnung",
}

TITLES_LOWER = {"dr", "prof", "dipl", "med", "rer", "nat", "jur", "kfm", "kffr",
                "ing", "mag", "phd", "ll.m", "mba", "pfarrer", "pastor", "diakon",
                "schwester", "pater", "herr", "frau", "herrn",
                "diplom-kaufmann", "diplom-kauffrau", "kaufmann", "kauffrau", "oec",
                "betriebswirt", "betriebswirtin", "diplom", "msc", "bsc"}


def looks_like_person(name: str) -> bool:
    """A plausible German personal name: First [Mid] Last, no org/role/geo words."""
    toks = name.replace(".", ". ").split()
    core = []
    for t in toks:
        tl = t.lower().strip(".")
        if tl in TITLES_LOWER:
            continue
        if tl in {"von", "van", "de", "der", "den", "zu", "zur", "am", "im"}:
            core.append(t)
            continue
        if tl in NON_NAME:
            return False
        if not re.match(r"^[A-ZÄÖÜ][a-zäöüß]*([\-'][A-ZÄÖÜ]?[a-zäöüß]+)?\.?$", t):
            # allow single capital initial like "C."
            if not re.match(r"^[A-ZÄÖÜ]\.$", t):
                return False
        core.append(t)
    real = [c for c in core if c.lower().strip(".") not in
            {"von", "van", "de", "der", "den", "zu", "zur"} and not re.match(r"^[A-ZÄÖÜ]\.$", c)]
    return 2 <= len(real) <= 4


def _clean_span(span: str) -> str:
    span = re.sub(r"\([^)]*\)", " ", span)        # drop (Vorsitzender / CEO …)
    span = LEAD.sub("", span.strip())
    span = re.sub(r"\s+", " ", span).strip(" .,;:-/|")
    return span


def extract_gf(text: str):
    if not text:
        return None, "none"
    t = re.sub(r"\s+", " ", text)
    for m in GF_ANCHOR.finditer(t):
        tail = t[m.end():m.end() + 400]
        stop = STOP.search(tail)
        span = tail[:stop.start()] if stop else tail[:160]
        names = NAME_TOKEN.findall(span)
        # NAME_TOKEN with groups returns weird; use finditer for full match
        found = [mm.group(0).strip() for mm in NAME_TOKEN.finditer(_clean_span(span))]
        # filter role-only & dedupe preserving order
        out = []
        seen = set()
        for n in found:
            n2 = ROLE_NOISE.sub("", n).strip(" .,;:-")
            n2 = re.sub(r"\s+", " ", n2)
            if n2.lower() in seen:
                continue
            if looks_like_person(n2):
                seen.add(n2.lower())
                out.append(n2)
        if out:
            return ", ".join(out[:8]), "regex"
    return None, "none"


HAS_ANCHOR = re.compile(r"gesch[aä]ftsf[uü]hr|vertretungsberechtigt|vertreten\s+durch|vorstand|inhaber", re.I)


def has_gf_keyword(text: str) -> bool:
    return bool(text and HAS_ANCHOR.search(text))


if __name__ == "__main__":
    tests = {
        "korian": "E info@korian.de Vertretungsberechtigte Geschäftsführer: Christian Gharieb (Vorsitzender der Geschäftsführung / CEO) Vincent Savajols (Geschäftsführer Finanzen / CFO) Registergericht: Amtsgericht München Registernummer: HRB 275673",
        "alloheim": "Steuer-Identifikationsnummer DE296523024 Vertreten durch Dr. Steffen Hehner, Carolin Behrendt, Petra Groth, Dr. Christian Mayer, Christoph Mosler, Thomas Rietz, Philipp Schuh Verwaltungsrat Marcus Friedrichs (Vorsitzender)",
        "johanneswerk": "E-Mail: kommunikation@johanneswerk.de Vertreten durch die Geschäftsführung: Sabine Hirte (Vorsitzende) Dr. Bodo de Vries (stellvertretender Vorsitzender) Burkhard Bensiek Frank Lohmann Rechtsform: gemeinnützige Gesellschaft",
        "seniorenpark": "www.senioren-park.de Geschäftsführung Jan C. Schreiter Thomas Goetz Martin Niggehoff Ewa Woroch Handelsregister Köln: HRB 56990",
        "single": "Geschäftsführer: Max Mustermann Registergericht Köln",
        "none": "Hier gibt es keine relevante Angabe, nur Adresse Musterstr 1 12345 Stadt",
    }
    for k, v in tests.items():
        print(f"{k:14} -> {extract_gf(v)}")
