"""Entity-Layer: Seed (kuratierte Major-Player + Träger aus den Heimen + Hersteller),
deterministisches Wortgrenzen-Alias-Tagging Artikel→Entität und Keyword-Event-
Klassifikation. NER-Extraktion ist als spätere Stufe vorgesehen (Spalten
entities.source / article_entities.method tragen dafür 'ner')."""
import json
import re

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
