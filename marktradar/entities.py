"""Entity-Layer: Seed (kuratierte Major-Player + Träger aus den Heimen + Hersteller),
deterministisches Wortgrenzen-Alias-Tagging Artikel→Entität, Keyword-Event-
Klassifikation und LLM-Stance pro Thema (Diskurs). NER-Extraktion ist als spätere
Stufe vorgesehen (Spalten entities.source / article_entities.method tragen dafür 'ner')."""
import json
import os
import re

import requests

from marktradar import embeddings

OLLAMA_HOST = embeddings.OLLAMA_HOST
STANCE_MODEL = os.getenv("CHAT_MODEL", "qwen3.5:9b")

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

SEED_EVENT_RULES = [
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
_SEED_EVENT_RES = [(t, re.compile(p, re.I)) for t, p in SEED_EVENT_RULES]


def seed_event_types(conn) -> int:
    """Seedet die Event-Typ-Taxonomie aus den bisherigen Hardcodes in die DB.
    Idempotent (UNIQUE name). Gibt Anzahl NEUER Typen zurück."""
    n = 0
    for name, pattern in SEED_EVENT_RULES:
        cur = conn.execute(
            "INSERT OR IGNORE INTO event_types(name,pattern,created_by,created) "
            "VALUES (?,?,'seed',datetime('now'))", (name, pattern))
        n += cur.rowcount
    conn.commit()
    return n


def _load_event_matchers(conn) -> list:
    """[(name, compiled_regex)] aller enabled Event-Typen. Seedet lazy bei leerer
    Tabelle. Kaputtes Pattern (user/auto) wird disabled statt den Lauf zu killen."""
    if not conn.execute("SELECT 1 FROM event_types LIMIT 1").fetchone():
        seed_event_types(conn)
    out = []
    for r in conn.execute(
            "SELECT id, name, pattern FROM event_types WHERE enabled=1 ORDER BY id").fetchall():
        try:
            out.append((r["name"], re.compile(r["pattern"], re.I)))
        except re.error:
            conn.execute("UPDATE event_types SET enabled=0, "
                         "description=COALESCE(description,'')||' [disabled: invalid regex]' "
                         "WHERE id=?", (r["id"],))
            conn.commit()
    return out


def list_event_types(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, pattern, description, enabled, created_by, created "
        "FROM event_types ORDER BY id").fetchall()]


def add_event_type(conn, name: str, pattern: str, description: str | None = None,
                   created_by: str = "manual") -> dict:
    name = (name or "").strip().lower()
    if not name or not (pattern or "").strip():
        return {"error": "name und pattern erforderlich"}
    try:
        re.compile(pattern, re.I)
    except re.error as e:
        return {"error": f"ungültiges Regex-Pattern: {e}"}
    cur = conn.execute(
        "INSERT OR IGNORE INTO event_types(name,pattern,description,created_by,created) "
        "VALUES (?,?,?,?,datetime('now'))", (name, pattern, description, created_by))
    conn.commit()
    if cur.rowcount == 0:
        return {"error": f"event_type '{name}' existiert bereits"}
    return {"ok": True, "id": cur.lastrowid, "name": name}


_SUGGEST_SYS = (
    "Du analysierst Schlagzeilen aus der Marktbeobachtung, die KEINEM bekannten "
    "Event-Typ zugeordnet werden konnten. Schlage 1–3 NEUE Event-Typen vor, die "
    "wiederkehrende Ereignismuster abdecken. Je Typ: name (1 wort, lowercase, "
    "deutsch), pattern (case-insensitive Regex mit |-Alternativen über typische "
    "Schlüsselwörter), description (1 Satz). Antworte NUR als JSON-Liste: "
    "[{\"name\":\"...\",\"pattern\":\"...\",\"description\":\"...\"}].")


def suggest_event_types(conn, sample: int = 50, min_unclassified: int = 20) -> dict:
    """LLM-Vorschläge für neue Event-Typen aus unklassifizierten relevanten Artikeln.
    Vorschläge landen in Quarantäne (enabled=0, created_by='auto') — Aktivierung nur
    explizit. Gibt {suggested:[...], skipped?} zurück."""
    rows = conn.execute(
        "SELECT title FROM articles WHERE event_type IS NULL AND relevant=1 "
        "ORDER BY published DESC LIMIT ?", (sample,)).fetchall()
    if len(rows) < min_unclassified:
        return {"suggested": [], "skipped": f"nur {len(rows)} unklassifizierte Artikel "
                                            f"(< {min_unclassified})"}
    existing = {r["name"] for r in conn.execute("SELECT name FROM event_types").fetchall()}
    payload = {"model": STANCE_MODEL, "format": "json", "stream": False,
               "think": False, "messages": [
                   {"role": "system", "content": _SUGGEST_SYS},
                   {"role": "user", "content":
                    "Bekannte Typen: " + ", ".join(sorted(existing)) + "\nSchlagzeilen:\n" +
                    "\n".join(f"- {r['title']}" for r in rows)[:4000]}],
               "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 400}}
    r = requests.post(f"{embeddings.CHAT_HOST}/api/chat", json=payload,
                      headers=embeddings.chat_headers(), timeout=120)
    r.raise_for_status()
    data = json.loads(r.json().get("message", {}).get("content", "") or "[]")
    items = data if isinstance(data, list) else data.get("event_types") or data.get("typen") or []
    suggested = []
    for it in items[:3]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name", "")).strip().lower()
        pattern = str(it.get("pattern", "")).strip()
        if not name or name in existing or not pattern:
            continue
        try:
            re.compile(pattern, re.I)
        except re.error:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO event_types(name,pattern,description,enabled,"
            "created_by,created) VALUES (?,?,?,0,'auto',datetime('now'))",
            (name, pattern, str(it.get("description", "")).strip()[:200] or None))
        suggested.append({"name": name, "pattern": pattern})
    conn.commit()
    return {"suggested": suggested}


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


def event_type(title: str, summary: str = "", matchers=None) -> str | None:
    """matchers=None → Seed-Taxonomie (rückwärtskompatibel, ohne DB nutzbar)."""
    text = f"{title or ''} {summary or ''}"
    for typ, rgx in (matchers if matchers is not None else _SEED_EVENT_RES):
        if rgx.search(text):
            return typ
    return None


def classify_events(conn, article_ids=None) -> int:
    """Setzt articles.event_type per DB-getriebener Keyword-Taxonomie (event_types).
    article_ids=None → alle ohne event_type. Gibt Anzahl gesetzter zurück."""
    matchers = _load_event_matchers(conn)
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
        et = event_type(a["title"], a["summary"] or "", matchers=matchers)
        if et:
            conn.execute("UPDATE articles SET event_type=? WHERE id=?", (et, a["id"]))
            n += 1
    conn.commit()
    return n


# ── LLM-Stance pro Diskurs-Thema ─────────────────────────────────────────────
# Keyword-GATE (welche Themen ein Artikel überhaupt berühren KÖNNTE) → bounded
# LLM-Aufrufe nur auf Treffer. Stance selbst kommt vom LLM (qwen), nicht vom Keyword.
SEED_TOPICS = {
    "Pflegereform": r"pflegereform|pflegeneuordnung|\breform\b|gesetzentwurf|referentenentwurf",
    "Finanzierung & Tariftreue": r"tariftreue|finanzier|vergütung|\bbeitrag|\bkosten\b|sparen|\bspar|eigenanteil",
    "Personal & Fachkräfte": r"personal|fachkräfte|fachkraft|personalbemessung|personaluntergrenze|pflegekräfte|ausbildung",
    "Bürokratie & Digitalisierung": r"bürokrat|digitalisier|dokumentation|entlastung|\bki\b|software",
    "Prävention & Versorgung": r"prävention|versorgung|vorsorge|\bambulant|stationär",
}


def seed_topics(conn) -> int:
    """Seedet die Diskurs-Themen (Domain 'pflege') aus den bisherigen Hardcodes.
    Idempotent (UNIQUE name)."""
    n = 0
    for name, prefilter in SEED_TOPICS.items():
        cur = conn.execute(
            "INSERT OR IGNORE INTO topics(name,prefilter,domain,created_by) "
            "VALUES (?,?,'pflege','seed')", (name, prefilter))
        n += cur.rowcount
    conn.commit()
    return n


def _load_topic_prefilters(conn) -> dict:
    """{topic: compiled_prefilter} aller enabled Topics. Seedet lazy bei leerer
    Tabelle. Kaputtes Pattern wird disabled statt den Lauf zu killen."""
    if not conn.execute("SELECT 1 FROM topics LIMIT 1").fetchone():
        seed_topics(conn)
    out = {}
    for r in conn.execute(
            "SELECT id, name, prefilter FROM topics WHERE enabled=1 ORDER BY id").fetchall():
        try:
            out[r["name"]] = re.compile(r["prefilter"], re.I)
        except re.error:
            conn.execute("UPDATE topics SET enabled=0 WHERE id=?", (r["id"],))
            conn.commit()
    return out


def add_topic(conn, name: str, prefilter: str, domain: str = "custom") -> dict:
    """Neues Beobachtungs-Thema ohne Codeänderung — Multi-Domain-Seam fürs BI-Modell."""
    name = (name or "").strip()
    if not name or not (prefilter or "").strip():
        return {"error": "name und prefilter erforderlich"}
    try:
        re.compile(prefilter, re.I)
    except re.error as e:
        return {"error": f"ungültiges Prefilter-Regex: {e}"}
    cur = conn.execute(
        "INSERT OR IGNORE INTO topics(name,prefilter,domain,created_by) "
        "VALUES (?,?,?,'manual')", (name, prefilter, domain))
    conn.commit()
    if cur.rowcount == 0:
        return {"error": f"topic '{name}' existiert bereits"}
    return {"ok": True, "id": cur.lastrowid, "name": name}


def list_topics(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, prefilter, domain, enabled, created_by "
        "FROM topics ORDER BY id").fetchall()]
STANCE_LABELS = ("kritisch", "fordernd", "befürwortend", "neutral")
POS_PALETTE = ["#5b8def", "#2ecc71", "#f0a830", "#9b6dff", "#26c6da", "#ff7043",
               "#ff4d4d", "#7a8290"]
_SYNTH_SYS = (
    "Du destillierst aus mehreren Schlagzeilen zu EINEM Pflege-/Gesundheits-Thema die "
    "zentralen, wiederkehrenden POSITIONEN im Diskurs. Gib 4–6 prägnante Positionen "
    "(label ≤ 6 Wörter, konkret, keine Dopplungen), je mit Tendenz 'pro' (befürwortet), "
    "'contra' (lehnt ab/warnt) oder 'gemischt'. Antworte NUR als JSON-Liste: "
    "[{\"label\":\"...\",\"valence\":\"pro|contra|gemischt\"}].")
_CLS_SYS = (
    "Du ordnest eine Meldung je Thema GENAU EINER der vorgegebenen Positionen zu "
    "(oder 'sonstige') und bestimmst die Tendenz des Absenders: 'pro' (plädiert dafür), "
    "'contra' (dagegen) oder 'neutral' (nur Bericht). Antworte NUR als JSON "
    "{\"<thema>\": {\"position\":\"<eine Position oder sonstige>\", \"valence\":\"pro|contra|neutral\"}}.")


def _vnorm(v: str) -> str:
    v = str(v or "").lower()
    if any(k in v for k in ("pro", "für", "befürwort", "unterstütz")):
        return "pro"
    if any(k in v for k in ("contra", "kontra", "gegen", "kritisch", "ablehn")):
        return "contra"
    return "neutral"


def synthesize_positions(conn, sample: int = 40, min_hits: int = 3) -> dict:
    """Destilliert je Thema 4–6 kanonische Positionen (mit Farbe + Tendenz) aus echten
    Schlagzeilen → topic_positions. Gibt {topic: anzahl} zurück."""
    out = {}
    for topic, rgx in _load_topic_prefilters(conn).items():
        rows = conn.execute(
            "SELECT title, summary FROM articles "
            "ORDER BY published DESC NULLS LAST LIMIT 800").fetchall()
        hits = [r["title"] for r in rows if rgx.search(f"{r['title']} {r['summary'] or ''}")][:sample]
        if len(hits) < min_hits:
            continue
        try:
            payload = {"model": STANCE_MODEL, "format": "json", "stream": False,
                       "think": False, "messages": [
                           {"role": "system", "content": _SYNTH_SYS},
                           {"role": "user", "content":
                            f"Thema: {topic}\nSchlagzeilen:\n" + "\n".join(f"- {h}" for h in hits)[:4000]}],
                       "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 300}}
            r = requests.post(f"{embeddings.CHAT_HOST}/api/chat", json=payload, headers=embeddings.chat_headers(), timeout=120)
            r.raise_for_status()
            data = json.loads(r.json().get("message", {}).get("content", "") or "{}")
            positions = data if isinstance(data, list) else data.get("positionen") or data.get("positions") or []
        except Exception:
            continue
        conn.execute("DELETE FROM topic_positions WHERE topic=?", (topic,))
        n = 0
        for i, p in enumerate(positions[:6]):
            label = str(p.get("label", "")).strip()[:60] if isinstance(p, dict) else str(p)[:60]
            if not label:
                continue
            conn.execute("INSERT OR IGNORE INTO topic_positions(topic,label,valence,color,ord) "
                         "VALUES (?,?,?,?,?)",
                         (topic, label, _vnorm(p.get("valence") if isinstance(p, dict) else ""),
                          POS_PALETTE[i % len(POS_PALETTE)], i))
            n += 1
        out[topic] = n
    conn.commit()
    return out


def classify_topics(conn, article_ids=None) -> dict:
    """Ordnet je Artikel pro berührtem Thema eine kanonische Position + Pro/Contra-Valenz
    zu (qwen, Thema-gegated), UPSERT in article_topics. Gibt {classified, failed}."""
    if not conn.execute("SELECT 1 FROM topic_positions LIMIT 1").fetchone():
        synthesize_positions(conn)
    pos_by_topic = {}
    for r in conn.execute("SELECT topic, label FROM topic_positions ORDER BY ord").fetchall():
        pos_by_topic.setdefault(r["topic"], []).append(r["label"])
    if article_ids is None:
        rows = conn.execute(
            "SELECT id, title, summary FROM articles WHERE id NOT IN "
            "(SELECT article_id FROM article_topics WHERE position IS NOT NULL)").fetchall()
    else:
        if not article_ids:
            return {"classified": 0, "failed": 0}
        ph = ",".join("?" * len(article_ids))
        rows = conn.execute(
            f"SELECT id, title, summary FROM articles WHERE id IN ({ph})",
            list(article_ids)).fetchall()
    classified = failed = 0
    prefilters = _load_topic_prefilters(conn)
    for a in rows:
        text = f"{a['title'] or ''} {a['summary'] or ''}"
        topics = [t for t, rgx in prefilters.items() if rgx.search(text) and pos_by_topic.get(t)]
        if not topics:
            continue
        plist = "\n".join(f"{t}: {', '.join(pos_by_topic[t])}" for t in topics)
        try:
            payload = {"model": STANCE_MODEL, "format": "json", "stream": False,
                       "think": False, "messages": [
                           {"role": "system", "content": _CLS_SYS},
                           {"role": "user", "content":
                            f"Positionen je Thema:\n{plist}\n\nTitel: {a['title']}\n"
                            f"Text: {a['summary'] or ''}"[:1800]}],
                       "options": {"temperature": 0.0, "num_ctx": 4096, "num_predict": 220}}
            r = requests.post(f"{embeddings.CHAT_HOST}/api/chat", json=payload, headers=embeddings.chat_headers(), timeout=90)
            r.raise_for_status()
            d = json.loads(r.json().get("message", {}).get("content", "") or "{}")
        except Exception:
            failed += 1
            continue
        for t in topics:
            v = d.get(t) or {}
            pos = str(v.get("position", "sonstige")).strip()[:60] if isinstance(v, dict) else "sonstige"
            if pos not in pos_by_topic[t]:
                pos = "sonstige"
            val = _vnorm(v.get("valence") if isinstance(v, dict) else "")
            conn.execute(
                "INSERT INTO article_topics(article_id,topic,stance,valence,position) "
                "VALUES (?,?,?,?,?) ON CONFLICT(article_id,topic) DO UPDATE SET "
                "stance=excluded.stance, valence=excluded.valence, position=excluded.position",
                (a["id"], t, val, val, pos))
        classified += 1
        if classified % 15 == 0:
            conn.commit()
    conn.commit()
    return {"classified": classified, "failed": failed}


def mirror_heime(conn, limit: int | None = None) -> int:
    """Spiegelt pflegeheime-Register-Zeilen als Entitäten (kind 'einrichtung') —
    macht das Register zur Basis des generischen BI-Entity-Modells, get_entity/
    timeline/list_entities sehen die Einrichtungen. Bewusst OHNE Aliase (Heim-Namen
    sind oft generisch) und nur manuell aufrufbar, nie im bootstrap. Idempotent."""
    sql = ("SELECT id, name, kreis FROM pflegeheime WHERE name IS NOT NULL "
           "AND length(trim(name)) >= 3 ORDER BY id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    n = 0
    for r in conn.execute(sql).fetchall():
        cur = conn.execute(
            "INSERT OR IGNORE INTO entities(name,type,region,source,ref_table,ref_id) "
            "VALUES (?,?,?,'register','pflegeheime',?)",
            (r["name"].strip(), "einrichtung", r["kreis"], r["id"]))
        n += cur.rowcount
    conn.commit()
    return n


# Entity-Typ → Farbe (Akteur-Chips im Viewer, konsistent mit dem Entitäten-Graph).
ACTOR_COLORS = {"traeger": "#67d98b", "hersteller": "#f0a830",
                "partei": "#c792ea", "behoerde": "#5b8def",
                "einrichtung": "#26c6da"}


def actors_for_hashtags(conn, per: int = 6) -> dict:
    """Top-Akteure (Entitäten) je Hashtag über den geteilten `articles`-Join.
    → {hashtag_id: [{name, type, n}]}. Verbindet die bisher getrennten Linsen
    Entität (WER) und Hashtag (WAS) zur Akteur×Thema-Sicht, ohne sie zu mergen."""
    rows = conn.execute(
        "SELECT ah.hashtag_id AS hid, e.name, e.type, COUNT(*) AS n "
        "FROM article_entities ae "
        "JOIN article_hashtags ah ON ah.article_id = ae.article_id "
        "JOIN entities e ON e.id = ae.entity_id "
        "GROUP BY ah.hashtag_id, e.id "
        "ORDER BY ah.hashtag_id, n DESC").fetchall()
    out: dict = {}
    for r in rows:
        lst = out.setdefault(r["hid"], [])
        if len(lst) < per:
            lst.append({"name": r["name"], "type": r["type"], "n": r["n"]})
    return out
