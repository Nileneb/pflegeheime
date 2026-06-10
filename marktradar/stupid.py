"""Stupid-Liste: Quarantäne für Desinfo-Quellen (AfD-nahe Kanäle).

Grundsatz (User-Entscheidung 2026-06-10): Wir sammeln weiter, um informiert
sein zu KÖNNEN — aber wir verbreiten den Inhalt nicht. Posts/Artikel aus
quarantänisierten QUELLEN erscheinen in keinem normalen Output (Karte, Feed,
Standard-Suche, Entity-Timelines). Sie werden ausschließlich ausgespielt, wenn
die Anfrage den Akteur EXPLIZIT nennt (z. B. „welche Falschmeldungen hat die
AfD zu Gesundheit verbreitet") — und dann immer zusammen mit WARNUNG.

Wichtige Abgrenzung: quarantänisiert wird die QUELLE (Domain/Handle), nicht
die Erwähnung. Seriöse Presse, die ÜBER den Akteur berichtet, bleibt normal
sichtbar — sonst würde die Quarantäne Berichterstattung zensieren statt
Desinfo-Verbreitung zu stoppen.

Pattern nach ranking.INSTITUTIONS: Registry + Substring-Match, ein zentrales
Modul, das alle Output-Pfade importieren.
"""
from __future__ import annotations

import re

WARNUNG = (
    "⚠️ Quarantäne-Quelle (Stupid-Liste): Dieser Inhalt stammt von einem als "
    "desinformationsanfällig eingestuften, möglicherweise verfassungsfeindlichen "
    "Absender (vgl. Verfassungsschutz-Einstufung der AfD). Aussagen NICHT als "
    "Fakten übernehmen — immer gegen seriöse Quellen (RKI, BMG, Fachpresse) "
    "prüfen. Ausgabe erfolgt nur, weil explizit nach diesem Akteur gefragt wurde."
)


def _entry(name: str, domains: list[str], handles: list[str] | None = None,
           entities: list[str] | None = None, ask_terms: list[str] | None = None) -> dict:
    """Eine Quarantäne-Einheit. `domains`/`handles` = lowercase-Substrings für
    Quell-Erkennung; `ask_terms` = Wort-Pattern, die eine Anfrage als explizite
    Nachfrage nach diesem Akteur qualifizieren."""
    return {"name": name, "domains": domains, "handles": handles or [],
            "entities": entities or [], "ask_terms": ask_terms or []}


# Erweiterbar — neue Quellen einfach ergänzen. Substring-Match auf lowercase.
STUPID_SOURCES: list[dict] = [
    _entry("AfD", domains=["afd.de", "afdbundestag.de", "afd-fraktion",
                           "alternativefuer.de", "afdkompakt.de"],
           handles=["afd"],
           entities=["AfD"],
           ask_terms=[r"\bafd\b", r"alternative f(ü|u)r deutschland"]),
    _entry("Compact (AfD-nah)", domains=["compact-online.de", "compact-magazin.com"],
           handles=["compactmagazin"],
           ask_terms=[r"\bcompact\b"]),
]


def match_stupid(text: str | None) -> dict | None:
    """Erste Registry-Einheit, deren Domain im Text (URL/Domain/Author) vorkommt."""
    if not text:
        return None
    low = text.lower()
    for entry in STUPID_SOURCES:
        if any(d in low for d in entry["domains"]):
            return entry
    return None


def is_stupid_domain(source_domain: str | None) -> bool:
    return match_stupid(source_domain) is not None


def is_stupid_handle(handle: str | None) -> bool:
    """Account-Handles (mastodon/bluesky): Prefix-Match auf den Nutzerteil
    (z. B. 'afd', 'afd_fraktion', '@afd@mastodon.social')."""
    if not handle:
        return False
    user = handle.lower().lstrip("@").split("@")[0]
    for entry in STUPID_SOURCES:
        if any(user.startswith(h) for h in entry["handles"]):
            return True
    return False


def is_stupid_post(post: dict) -> bool:
    """Zentraler Post-Check für alle Output-Pfade: Domain in url/source_domain
    ODER Author-Handle quarantänisiert."""
    return (is_stupid_domain(post.get("source_domain"))
            or match_stupid(post.get("url") or post.get("link")) is not None
            or is_stupid_handle(post.get("author")))


def explicit_ask(query: str | None) -> dict | None:
    """Erst wenn die ANFRAGE den Akteur explizit nennt, darf Quarantäne-Inhalt
    raus (mit WARNUNG). Liefert die gematchte Registry-Einheit oder None."""
    if not query:
        return None
    low = query.lower()
    for entry in STUPID_SOURCES:
        if any(re.search(t, low) for t in entry["ask_terms"]):
            return entry
    return None


def is_stupid_entity(name: str | None) -> bool:
    """Entity-Namen (z. B. für get_entity/timeline): direkter Akteur-Lookup ist
    eine explizite Nachfrage — Ausgabe dann MIT Warnung, nicht unterdrückt."""
    if not name:
        return False
    return any(name.strip().lower() == e.lower()
               for entry in STUPID_SOURCES for e in entry["entities"])
