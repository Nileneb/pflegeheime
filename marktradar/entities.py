"""Entity-Layer: Seed (kuratierte Major-Player + Träger aus den Heimen + Hersteller),
deterministisches Wortgrenzen-Alias-Tagging Artikel→Entität, Keyword-Event-
Klassifikation und LLM-Stance pro Thema (Diskurs). NER-Extraktion ist als spätere
Stufe vorgesehen (Spalten entities.source / article_entities.method tragen dafür 'ner')."""
import json
import re

import requests

from marktradar import embeddings

OLLAMA_HOST = embeddings.OLLAMA_HOST
STANCE_MODEL = "qwen3.5:9b"

# Kuratierte große Betreiber/Träger (canonical, [aliases])
SEED_TRAEGER = [
    ("Korian", ["Korian Deutschland", "Curanum"]),
    ("Alloheim", ["Alloheim Senioren-Residenzen"]),
    ("Orpea", ["Orpea Deutschland", "Celenus"]),
    ("Pro Seniore", ["Victor's Group"]),
    ("Kursana", ["Dussmann"]),
    ("Vivantes", []),
    ("Caritas", ["Caritasverband", "Caritas-Trägergesellschaft"]),
    ("Diakonie", ["Diakonisches Werk", "Diakoneo"]),
    ("AWO", ["Arbeiterwohlfahrt"]),
    ("DRK", ["Deutsches Rotes Kreuz"]),
    ("Johanniter", ["Johanniter-Unfall-Hilfe"]),
    ("Malteser", ["Malteser Hilfsdienst"]),
    ("ASB", ["Arbeiter-Samariter-Bund"]),
    ("Convivo", []),
    ("Emvia Living", ["Emvia"]),
    ("Cura Seniorencentren", []),
    ("DOREA", []),
    ("Alpenland", []),
]

# Kleine Hersteller-/Zulieferer-Liste (Medizintechnik, Pflegehilfsmittel, Software)
SEED_HERSTELLER = [
    ("Paul Hartmann", ["Hartmann"]),
    ("Essity", ["Tena"]),
    ("Stiegelmeyer", []),
    ("Hermann Bock", []),
    ("Connext", ["Vivendi"]),
    ("DAN Produkte", []),
]

# Parteien (Diskurs-Akteure)
SEED_PARTEIEN = [
    ("CDU", ["CDU/CSU", "Union", "Christdemokraten"]),
    ("CSU", []),
    ("SPD", ["Sozialdemokraten"]),
    ("Bündnis 90/Die Grünen", ["Die Grünen", "Grüne", "B90"]),
    ("FDP", ["Freie Demokraten"]),
    ("Die Linke", ["Linkspartei", "Linksfraktion"]),
    ("AfD", ["Alternative für Deutschland"]),
    ("BSW", ["Bündnis Sahra Wagenknecht"]),
]

# Institutionen / Verbände / Gewerkschaften (Diskurs-Akteure jenseits Träger)
SEED_INSTITUTION = [
    ("WHO", ["Weltgesundheitsorganisation", "World Health Organization"]),
    ("RKI", ["Robert Koch-Institut", "Robert-Koch-Institut"]),
    ("BMG", ["Bundesgesundheitsministerium", "Gesundheitsministerium"]),
    ("GKV-Spitzenverband", ["GKV-Spitzenverband"]),
    ("vdek", ["Ersatzkassen"]),
    ("ver.di", ["verdi"]),
    ("VdK", ["Sozialverband VdK"]),
    ("Deutscher Pflegerat", ["Pflegerat"]),
    ("BVMed", ["Bundesverband Medizintechnologie"]),
]

EVENT_RULES = [
    ("insolvenz", r"insolven|pleite|zahlungsunf|gläubiger|schutzschirm|sanierungsverfahren"),
    ("politik", r"reform|gesetz|verordnung|förder|bundestag|ministerium|vergütung|"
                r"personalschlüssel|tariftreue|pflegegrad|pflegeneuordnung"),
    ("expansion", r"eröffn|neubau|übernahme|übernimmt|expansion|investiert|baubeginn|"
                  r"richtfest|fusion|akqui"),
    ("schliessung", r"schließ|schliess|gibt auf|standortschließung"),
    ("personalie", r"geschäftsführ|vorstand|wechsel an der spitze|neue leitung|"
                   r"ernennt|berufen"),
    ("auszeichnung", r"auszeichnung|\bpreis\b|ausgezeichnet|prämiert|zertifi"),
    ("produkt", r"\bsoftware\b|digital|\bapp\b|launch|markteinführung|innovation"),
]
_EVENT_RES = [(t, re.compile(p, re.I)) for t, p in EVENT_RULES]


def _clean_traeger(v: str) -> str | None:
    v = (v or "").strip()
    if not v or len(v) < 3:
        return None
    if re.search(r"no\s*clear\s*data|^n/?a$|unbekannt|^null$", v, re.I):
        return None
    return v


def seed_entities(conn) -> int:
    """Seedet kuratierte Träger + Hersteller + distinct Träger aus den Heimen.
    Idempotent (UNIQUE name). Gibt Anzahl NEUER Entitäten zurück."""
    n = 0

    def ins(name, typ, aliases, src):
        nonlocal n
        cur = conn.execute(
            "INSERT OR IGNORE INTO entities(name,type,aliases,region,source) "
            "VALUES (?,?,?,?,?)",
            (name, typ, json.dumps(aliases, ensure_ascii=False) if aliases else None,
             None, src))
        n += cur.rowcount

    for name, al in SEED_TRAEGER:
        ins(name, "traeger", al, "seed")
    for name, al in SEED_HERSTELLER:
        ins(name, "hersteller", al, "seed")
    for name, al in SEED_PARTEIEN:
        ins(name, "partei", al, "seed")
    for name, al in SEED_INSTITUTION:
        ins(name, "behoerde", al, "seed")
    seen = {r["name"].lower() for r in conn.execute("SELECT name FROM entities").fetchall()}
    for r in conn.execute("SELECT DISTINCT traeger FROM pflegeheime").fetchall():
        t = _clean_traeger(r["traeger"])
        if t and t.lower() not in seen:
            ins(t, "traeger", [], "heime")
            seen.add(t.lower())
    conn.commit()
    return n


def _build_matchers(conn):
    """[(entity_id, compiled_regex)] aus Name + Aliassen, Wortgrenzen, case-insensitive."""
    out = []
    for r in conn.execute("SELECT id, name, aliases FROM entities").fetchall():
        names = [r["name"]] + (json.loads(r["aliases"]) if r["aliases"] else [])
        pat = "|".join(re.escape(t) for t in names if t)
        if pat:
            out.append((r["id"], re.compile(rf"\b(?:{pat})\b", re.I)))
    return out


def tag_articles(conn, article_ids=None, matchers=None) -> int:
    """Verknüpft Artikel mit Entitäten via Wortgrenzen-Alias-Match auf title+summary.
    article_ids=None → alle noch ungetaggten Artikel. Gibt Anzahl neuer Links zurück."""
    if matchers is None:
        matchers = _build_matchers(conn)
    if article_ids is None:
        rows = conn.execute(
            "SELECT a.id, a.title, a.summary FROM articles a "
            "WHERE a.id NOT IN (SELECT article_id FROM article_entities)").fetchall()
    else:
        if not article_ids:
            return 0
        ph = ",".join("?" * len(article_ids))
        rows = conn.execute(
            f"SELECT id, title, summary FROM articles WHERE id IN ({ph})",
            list(article_ids)).fetchall()
    n = 0
    for a in rows:
        text = f"{a['title'] or ''} {a['summary'] or ''}"
        for eid, rgx in matchers:
            if rgx.search(text):
                cur = conn.execute(
                    "INSERT OR IGNORE INTO article_entities(article_id,entity_id,method) "
                    "VALUES (?,?,'alias')", (a["id"], eid))
                n += cur.rowcount
    conn.commit()
    return n


def event_type(title: str, summary: str = "") -> str | None:
    text = f"{title or ''} {summary or ''}"
    for typ, rgx in _EVENT_RES:
        if rgx.search(text):
            return typ
    return None


def classify_events(conn, article_ids=None) -> int:
    """Setzt articles.event_type per Keyword-Taxonomie. article_ids=None → alle ohne
    event_type. Gibt Anzahl gesetzter zurück."""
    if article_ids is None:
        rows = conn.execute(
            "SELECT id, title, summary FROM articles WHERE event_type IS NULL").fetchall()
    else:
        if not article_ids:
            return 0
        ph = ",".join("?" * len(article_ids))
        rows = conn.execute(
            f"SELECT id, title, summary FROM articles WHERE id IN ({ph})",
            list(article_ids)).fetchall()
    n = 0
    for a in rows:
        et = event_type(a["title"], a["summary"] or "")
        if et:
            conn.execute("UPDATE articles SET event_type=? WHERE id=?", (et, a["id"]))
            n += 1
    conn.commit()
    return n


# ── LLM-Stance pro Diskurs-Thema ─────────────────────────────────────────────
# Keyword-GATE (welche Themen ein Artikel überhaupt berühren KÖNNTE) → bounded
# LLM-Aufrufe nur auf Treffer. Stance selbst kommt vom LLM (qwen), nicht vom Keyword.
TOPIC_PREFILTER = {
    "Pflegereform": r"pflegereform|pflegeneuordnung|\breform\b|gesetzentwurf|referentenentwurf",
    "Finanzierung & Tariftreue": r"tariftreue|finanzier|vergütung|\bbeitrag|\bkosten\b|sparen|\bspar|eigenanteil",
    "Personal & Fachkräfte": r"personal|fachkräfte|fachkraft|personalbemessung|personaluntergrenze|pflegekräfte|ausbildung",
    "Bürokratie & Digitalisierung": r"bürokrat|digitalisier|dokumentation|entlastung|\bki\b|software",
    "Prävention & Versorgung": r"prävention|versorgung|vorsorge|\bambulant|stationär",
}
_PREFILTER = {t: re.compile(p, re.I) for t, p in TOPIC_PREFILTER.items()}
STANCE_LABELS = ("kritisch", "fordernd", "befürwortend", "neutral")
_STANCE_SYS = (
    "Du bestimmst die HALTUNG des Absenders einer Pflege-/Gesundheits-Meldung zu "
    "vorgegebenen Themen. Pro Thema genau eine Haltung: 'kritisch' (lehnt ab/warnt), "
    "'fordernd' (verlangt Maßnahmen), 'befürwortend' (unterstützt/begrüßt) oder "
    "'neutral' (nur Bericht ohne Wertung). Antworte NUR als JSON-Objekt "
    "{\"<thema>\": \"<haltung>\"} für genau die genannten Themen.")


def classify_topics(conn, article_ids=None) -> dict:
    """Bestimmt je Artikel die Stance zu den Themen, die er (per Keyword-Gate) berührt,
    via qwen → article_topics. Bounded: LLM nur auf Themen-Treffer. Idempotent
    (überspringt bereits klassifizierte). Gibt {classified, failed} zurück."""
    if article_ids is None:
        rows = conn.execute(
            "SELECT id, title, summary FROM articles a "
            "WHERE id NOT IN (SELECT article_id FROM article_topics)").fetchall()
    else:
        if not article_ids:
            return {"classified": 0, "failed": 0}
        ph = ",".join("?" * len(article_ids))
        rows = conn.execute(
            f"SELECT id, title, summary FROM articles WHERE id IN ({ph})",
            list(article_ids)).fetchall()
    classified = failed = 0
    for a in rows:
        text = f"{a['title'] or ''} {a['summary'] or ''}"
        topics = [t for t, rgx in _PREFILTER.items() if rgx.search(text)]
        if not topics:
            continue
        try:
            payload = {"model": STANCE_MODEL, "format": "json", "stream": False,
                       "think": False, "messages": [
                           {"role": "system", "content": _STANCE_SYS},
                           {"role": "user", "content":
                            f"Themen: {', '.join(topics)}\nTitel: {a['title']}\n"
                            f"Text: {a['summary'] or ''}"[:1600]}],
                       "options": {"temperature": 0.0, "num_ctx": 2048, "num_predict": 160}}
            r = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=90)
            r.raise_for_status()
            d = json.loads(r.json().get("message", {}).get("content", "") or "{}")
        except Exception:
            failed += 1
            continue
        for t in topics:
            st = str(d.get(t, "neutral")).lower().strip()
            if st not in STANCE_LABELS:
                st = "neutral"
            conn.execute("INSERT OR IGNORE INTO article_topics(article_id,topic,stance) "
                         "VALUES (?,?,?)", (a["id"], t, st))
        classified += 1
        if classified % 20 == 0:  # WHY: live-Fortschritt im Viewer + crash-safe
            conn.commit()
    conn.commit()
    return {"classified": classified, "failed": failed}
