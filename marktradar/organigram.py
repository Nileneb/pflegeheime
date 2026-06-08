"""Organigramm-Layer: hierarchische Org-Einheiten + Personen je Träger.
Adaptiert aus dem BDA-Organigram-Entwurf, integriert in die marktradar-DB
(Tabellen org_units / org_persons). Seed: Bergische Diakonie als Beispiel."""

DEFAULT_TRAEGER = "Bergische Diakonie"

# (name, short_name, parent_id, level, type, color, icon, sort_order) — parent_id
# referenziert die 1-basierte Position in dieser Liste (Reihenfolge = id).
SEED_UNITS = [
    ("Bergische Diakonie", "BDA", None, 0, "root", "#003F7D", "✝", 0),
    ("Leitung & Verwaltung", "Leitung", 1, 1, "sektor", "#E8860A", "🏢", 1),
    ("Altenhilfe", "Alten", 1, 1, "sektor", "#2E8B57", "🧓", 2),
    ("Kinder, Jugend & Familie", "KJF", 1, 1, "sektor", "#6B48C8", "👶", 3),
    ("Sozialtherapeutische Hilfe", "STH", 1, 1, "sektor", "#1A8C8C", "🤝", 4),
    ("Bildung", "Bildung", 1, 1, "sektor", "#C0392B", "🎓", 5),
    ("Weitere Angebote", "Weitere", 1, 1, "sektor", "#3949AB", "➕", 6),
    ("Vorstand", None, 2, 2, "bereich", "#E8860A", "🏛️", 0),
    ("Aufsichtsrat", None, 2, 2, "bereich", "#E8860A", "⚖️", 1),
    ("Zentrale Unternehmenskommunikation", "ZUK", 2, 2, "bereich", "#E8860A", "📣", 2),
    ("Angebotsberatung", None, 2, 2, "bereich", "#E8860A", "📞", 3),
    ("Zentrale Dienste", None, 2, 2, "bereich", "#E8860A", "🔧", 4),
    ("Verwaltung", None, 2, 2, "bereich", "#E8860A", "💼", 5),
    ("Pflegeeinrichtungen", None, 3, 2, "bereich", "#2E8B57", "🏠", 0),
    ("Tagespflege", None, 3, 2, "bereich", "#2E8B57", "☀️", 1),
    ("Service Wohnen", None, 3, 2, "bereich", "#2E8B57", "🏡", 2),
    ("Ambulante Pflege", None, 3, 2, "bereich", "#2E8B57", "🚗", 3),
    ("Haus Otto Ohl", None, 14, 3, "einrichtung", "#2E8B57", "🏠", 0),
    ("Haus Karl Heinersdorff", None, 14, 3, "einrichtung", "#2E8B57", "🏠", 1),
    ("Haus August von der Twer", None, 14, 3, "einrichtung", "#2E8B57", "🏠", 2),
    ("Haus Luise von der Heyden", None, 14, 3, "einrichtung", "#2E8B57", "🏠", 3),
    ("Diakoniezentrum Heiligenhaus", None, 14, 3, "einrichtung", "#2E8B57", "🏠", 4),
    ("Diakoniezentrum Monheim", None, 14, 3, "einrichtung", "#2E8B57", "🏠", 5),
    ("Haus Monheim", None, 14, 3, "einrichtung", "#2E8B57", "🏠", 6),
    ("Haus Lennep", None, 14, 3, "einrichtung", "#2E8B57", "🏠", 7),
    ("Pflegeeinrichtung Stockder-Stiftung", None, 14, 3, "einrichtung", "#2E8B57", "🏠", 8),
    ("Erzieherische Hilfen", None, 4, 2, "bereich", "#6B48C8", "🧡", 0),
    ("HPZ & Fachklinik Kinder- und Jugendpsychiatrie", "HPZ", 4, 2, "bereich", "#6B48C8", "🧠", 1),
    ("Evangelische Förderschule", "Förderschule", 4, 2, "bereich", "#6B48C8", "🏫", 2),
    ("Projekte KJF", None, 4, 2, "bereich", "#6B48C8", "🌱", 3),
    ("Wohnangebote", None, 5, 2, "bereich", "#1A8C8C", "🏘️", 0),
    ("Begleitende Fachdienste", None, 5, 2, "bereich", "#1A8C8C", "🔬", 1),
    ("Soziale Dienste Niederberg", None, 5, 2, "bereich", "#1A8C8C", "🌍", 2),
    ("Evangelisches Berufskolleg", "EBK", 6, 2, "bereich", "#C0392B", "🏫", 0),
    ("Bildungszentrum", None, 6, 2, "bereich", "#C0392B", "📚", 1),
    ("Schule für Pflegeberufe", None, 6, 2, "bereich", "#C0392B", "💉", 2),
]
SEED_PERSONS = [
    ("Max", "Mustermann", "m.mustermann@bergische-diakonie.de", "Vorstand", 8, "leitung"),
    ("Renate", "Zanjani", "renate.zanjani@bergische-diakonie.de", "AL ZUK", 10, "leitung"),
    ("Simone", "Küster", "simone.kuester@bergische-diakonie.de", "Sekretariat", 8, "mitarbeitende"),
    ("Hans", "Muster", "h.muster@bergische-diakonie.de", "Pflegefachkraft", 18, "mitarbeitende"),
    ("Erika", "Beispiel", "e.beispiel@bergische-diakonie.de", "Sozialarbeiterin", 28, "mitarbeitende"),
]


def seed(conn, traeger: str = DEFAULT_TRAEGER) -> int:
    """Seedet die Org-Struktur eines Trägers (idempotent: nur wenn leer).
    Gibt Anzahl angelegter Einheiten zurück."""
    if conn.execute("SELECT 1 FROM org_units WHERE traeger=? LIMIT 1", (traeger,)).fetchone():
        return 0
    for i, (name, short, parent, level, typ, color, icon, sort) in enumerate(SEED_UNITS, start=1):
        conn.execute(
            "INSERT INTO org_units(id,traeger,name,short_name,parent_id,level,type,color,"
            "icon,sort_order) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (i, traeger, name, short, parent, level, typ, color, icon, sort))
    for first, last, email, role, unit_id, typ in SEED_PERSONS:
        conn.execute(
            "INSERT INTO org_persons(traeger,first_name,last_name,email,role,unit_id,type) "
            "VALUES (?,?,?,?,?,?,?)", (traeger, first, last, email, role, unit_id, typ))
    conn.commit()
    return len(SEED_UNITS)


def tree(conn, traeger: str = DEFAULT_TRAEGER) -> list[dict]:
    """Verschachtelter Org-Baum (root → sektoren → bereiche → einrichtungen)."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id,name,short_name,parent_id,level,type,icon,color,sort_order "
        "FROM org_units WHERE traeger=? AND active=1 "
        "ORDER BY level, sort_order, name", (traeger,)).fetchall()]
    by_id = {r["id"]: {**r, "children": []} for r in rows}
    roots = []
    for r in rows:
        node = by_id[r["id"]]
        if r["parent_id"] and r["parent_id"] in by_id:
            by_id[r["parent_id"]]["children"].append(node)
        elif not r["parent_id"]:
            roots.append(node)
    return roots


def persons(conn, unit_id: int | None = None, traeger: str = DEFAULT_TRAEGER) -> list[dict]:
    """Personen eines Trägers; mit unit_id inkl. aller Untereinheiten (rekursiv)."""
    if unit_id is not None:
        sql = ("WITH RECURSIVE sub(id) AS ("
               " SELECT id FROM org_units WHERE id=? "
               " UNION ALL SELECT o.id FROM org_units o JOIN sub s ON o.parent_id=s.id) "
               "SELECT p.first_name,p.last_name,p.email,p.role,p.type,o.name AS unit "
               "FROM org_persons p LEFT JOIN org_units o ON p.unit_id=o.id "
               "WHERE p.active=1 AND p.unit_id IN (SELECT id FROM sub) ORDER BY p.last_name")
        rows = conn.execute(sql, (unit_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT p.first_name,p.last_name,p.email,p.role,p.type,o.name AS unit "
            "FROM org_persons p LEFT JOIN org_units o ON p.unit_id=o.id "
            "WHERE p.active=1 AND p.traeger=? ORDER BY p.last_name", (traeger,)).fetchall()
    return [dict(r) for r in rows]


def stats(conn, traeger: str = DEFAULT_TRAEGER) -> dict:
    one = lambda q: conn.execute(q, (traeger,)).fetchone()[0]
    return {
        "traeger": traeger,
        "einheiten": one("SELECT count(*) FROM org_units WHERE traeger=? AND active=1"),
        "einrichtungen": conn.execute(
            "SELECT count(*) FROM org_units WHERE traeger=? AND type='einrichtung' AND active=1",
            (traeger,)).fetchone()[0],
        "personen": one("SELECT count(*) FROM org_persons WHERE traeger=? AND active=1"),
    }


def add_unit(conn, name: str, parent_id: int | None = None, type: str = "bereich",
             traeger: str = DEFAULT_TRAEGER, short_name: str = None) -> dict:
    level = 0
    if parent_id:
        p = conn.execute("SELECT level FROM org_units WHERE id=?", (parent_id,)).fetchone()
        level = (p["level"] + 1) if p else 1
    cur = conn.execute(
        "INSERT INTO org_units(traeger,name,short_name,parent_id,level,type) "
        "VALUES (?,?,?,?,?,?)", (traeger, name, short_name, parent_id, level, type))
    conn.commit()
    return {"id": cur.lastrowid, "name": name, "parent_id": parent_id, "level": level}


def add_person(conn, first_name: str, last_name: str, role: str = None,
               unit_id: int | None = None, email: str = None,
               type: str = "mitarbeitende", traeger: str = DEFAULT_TRAEGER) -> dict:
    cur = conn.execute(
        "INSERT INTO org_persons(traeger,first_name,last_name,email,role,unit_id,type) "
        "VALUES (?,?,?,?,?,?,?)", (traeger, first_name, last_name, email, role, unit_id, type))
    conn.commit()
    return {"id": cur.lastrowid, "name": f"{first_name} {last_name}", "unit_id": unit_id}
