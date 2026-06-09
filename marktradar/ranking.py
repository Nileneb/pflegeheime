"""Ranking-Rails für den Hashtag-Globus.

Drei Hebel bestimmen, wie weit ein Post bei der Auswertung „nach oben gespült"
wird — OHNE dass andere Sprachen/Quellen ausgeschlossen werden (sie bleiben
erfasst, rutschen nur nach unten):

1. **Sprache (Lesbarkeit):** Deutsch zuerst, dann europäisch/westlich lesbar,
   dann der Rest. Damit ist Qualitätskontrolle durch den Leser überhaupt machbar.
2. **Quellen-Trust:** Ämter/Institutionen > etablierte Presse > Social. „Karl-
   Ludwig aus der Kneipe" (anonymer Social-Post) zählt weniger als eine Behörde.
3. **Geo-Präzision:** ein exakt verorteter Post (Institution mit bekanntem Sitz
   oder Gazetteer-Treffer) schlägt einen, der nur grob am Sprach-Centroid landet.

Institutionen tragen Hebel 2 UND 3 zugleich: sie haben einen exakten Standort
UND sind vertrauenswürdig — darum verknüpft `INSTITUTIONS` Trust mit Koordinaten.

Reine Daten + Helfer, keine I/O. Der `geo_precise`-Pfad importiert `hashtags`
lazy (nur für den Gazetteer), um einen Import-Zyklus zu vermeiden.
"""
from __future__ import annotations

# ── Rail 1: Sprach-Tiers (0 = muttersprachlich, höher = schwerer lesbar) ──────
LANG_TIERS: list[list[str]] = [
    ["de"],                                                       # 0
    ["en", "nl", "fr", "es", "it", "pt", "pl", "da", "sv", "no",  # 1: EU/westlich
     "fi", "cs", "sk", "ro", "hu", "el", "bg", "uk"],
    # Rest (ar, ru, tr, zh, ja, hi, …) fällt implizit auf den Rest-Tier.
]
_LANG_TIER: dict[str, int] = {c: i for i, grp in enumerate(LANG_TIERS) for c in grp}
REST_TIER: int = len(LANG_TIERS)  # = 2
# Lesbarkeits-Gewicht je Tier (Index = Tier; alles ab REST_TIER nutzt den letzten).
_READABILITY = [1.0, 0.6, 0.3]


def lang_tier(code: str | None) -> int:
    """Sprach-Tier eines ISO-Codes: 0 (de) … REST_TIER (unbekannt/fern)."""
    return _LANG_TIER.get((code or "").lower(), REST_TIER)


def readability(code: str | None) -> float:
    """Lesbarkeits-Gewicht 0..1 (Deutsch = 1.0)."""
    return _READABILITY[min(lang_tier(code), len(_READABILITY) - 1)]


# ── Rail 2+3: Institutionen-Registry (Trust + exakter Sitz) ──────────────────
# `match`: Substrings (lowercase), die im Outlet/Autor/URL die Institution erkennen.
def _inst(name, match, lat, lon, country):
    return {"name": name, "match": match, "lat": lat, "lon": lon, "country": country}


INSTITUTIONS: list[dict] = [
    _inst("RKI", ["robert koch", "rki.de", "rki "], 52.52, 13.40, "DE"),
    _inst("BMG", ["bundesgesundheitsministerium", "gesundheitsministerium"], 52.52, 13.38, "DE"),
    _inst("BMAS", ["bmas.de", "arbeit und soziales"], 52.51, 13.37, "DE"),
    _inst("Destatis", ["destatis", "statistisches bundesamt"], 50.08, 8.24, "DE"),
    _inst("GKV-Spitzenverband", ["gkv-spitzenverband", "gkv spitzenverband"], 52.52, 13.39, "DE"),
    _inst("vdek", ["vdek.com", "ersatzkassen"], 52.52, 13.39, "DE"),
    _inst("Bundestag", ["bundestag"], 52.52, 13.38, "DE"),
    _inst("MAGS NRW", ["mags.nrw", "mags nrw"], 51.23, 6.77, "DE"),
    _inst("Bayern", ["bayern.de", "bayerische staatsregierung"], 48.14, 11.58, "DE"),
    _inst("Baden-Württemberg", ["baden-wuerttemberg.de", "baden-württemberg.de"], 48.78, 9.18, "DE"),
    _inst("Niedersachsen", ["niedersachsen.de"], 52.38, 9.72, "DE"),
    _inst("Schleswig-Holstein", ["schleswig-holstein.de"], 54.32, 10.12, "DE"),
    _inst("WHO", ["who.int", "world health organization", "weltgesundheits"], 46.21, 6.14, "CH"),
    _inst("EU-Kommission", ["ec.europa.eu", "european commission", "eu-kommission",
                            "europäische kommission"], 50.85, 4.35, "BE"),
    _inst("ECDC", ["ecdc.europa", "ecdc "], 59.33, 18.06, "SE"),
    _inst("Commonwealth Fund", ["commonwealthfund", "commonwealth fund"], 40.71, -74.0, "US"),
    _inst("Lancet/BMJ", ["thelancet", "bmj.com", "bmjopen"], 51.50, -0.12, "GB"),
    _inst("PLOS", ["journals.plos", "plos "], 37.77, -122.42, "US"),
]

# Generische Marker → Trust ohne festen Sitz (regionale Ämter/Behörden/Kammern).
INSTITUTION_MARKERS: tuple[str, ...] = (
    "ministerium", "bundesamt", "behörde", "behoerde", "landesregierung",
    "senatsverwaltung", "staatskanzlei", "gesundheitsamt", "spitzenverband",
    "kassenärztliche", "kassenarztliche", "ärztekammer", "aerztekammer",
    "pflegekammer", "landesamt", "statistisches", ".europa.eu", "who.int",
    ".gov", ".bund.de", "kommission",
)

TRUST_WEIGHT: dict[str, float] = {"institution": 1.0, "press": 0.6, "social": 0.25}


def match_institution(text: str | None) -> dict | None:
    """Erste Institution mit bekanntem Sitz, deren Marker im Text vorkommt → Eintrag
    (inkl. lat/lon/country) | None. `text` = Autor + Outlet + URL, NICHT der Inhalt
    (sonst triggert ein zitiertes „Ministerium" im Fließtext falsch)."""
    if not text:
        return None
    low = text.lower()
    for inst in INSTITUTIONS:
        if any(m in low for m in inst["match"]):
            return inst
    return None


def _identity_text(post: dict) -> str:
    return f"{post.get('author', '')} {post.get('location_text', '')} {post.get('url', '')}"


def classify_source(post: dict) -> tuple[str, float]:
    """Trust-Klasse eines Posts → (label, weight). Reihenfolge: Institution (fester
    Sitz ODER generischer Amts-Marker) → Presse (News-Outlet) → Social."""
    ident = _identity_text(post).lower()
    if match_institution(ident) or any(m in ident for m in INSTITUTION_MARKERS):
        return "institution", TRUST_WEIGHT["institution"]
    if (post.get("source") or "") == "news":
        return "press", TRUST_WEIGHT["press"]
    return "social", TRUST_WEIGHT["social"]


def geo_precise(post: dict) -> bool:
    """True, wenn der Post exakt verortbar ist (Institutionssitz ODER Gazetteer-
    Treffer im location_text), statt nur grob am Sprach-Centroid zu landen."""
    if match_institution(_identity_text(post)):
        return True
    from marktradar import hashtags  # lazy: bricht Import-Zyklus
    return hashtags.geocode(post.get("location_text")) is not None


def score_post(post: dict) -> dict:
    """Gesamtscore 0..1 + transparente Aufschlüsselung. Gewichte: Lesbarkeit 45 %,
    Trust 40 %, Geo-Präzision 15 %. Ein deutscher, institutioneller, exakt verorteter
    Post = 1.0; ein anonymer fern-sprachiger Social-Post ≈ 0.24."""
    read = readability(post.get("lang_code"))
    label, trust = classify_source(post)
    precise = geo_precise(post)
    score = round(0.45 * read + 0.40 * trust + 0.15 * (1.0 if precise else 0.0), 4)
    return {
        "score": score,
        "lang_tier": lang_tier(post.get("lang_code")),
        "trust": label,
        "trust_weight": trust,
        "geo_precise": precise,
        "breakdown": {"readability": read, "trust": trust, "geo": 1.0 if precise else 0.0},
    }


def rank_posts(posts: list[dict]) -> list[dict]:
    """Posts nach Score absteigend (Tie-Break: neuste zuerst). Gibt KOPIEN, jede
    um die score_post-Felder ergänzt."""
    scored = [{**p, **score_post(p)} for p in posts]
    scored.sort(key=lambda p: (p["score"], p.get("published") or ""), reverse=True)
    return scored
