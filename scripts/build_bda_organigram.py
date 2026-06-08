"""Testet das Organigram-Tool: baut die reale Bergische-Diakonie-Struktur
ausschließlich über organigram.add_unit / add_person auf (genau die Logik, die
die MCP-Tools add_org_unit / add_org_person deployen) und verifiziert per
tree()/stats(). Aufruf: python -m scripts.build_bda_organigram [DB_PATH]

Knoten-Tupel: (name, type, short_name, icon, color, children)
  - color nur auf root/sektoren gesetzt → add_unit vererbt sie nach unten.
  - icon None auf einem Leaf → erbt das Icon des Eltern-Bereichs (im build()).
"""
import sys
from marktradar import db, organigram

TRAEGER = "Bergische Diakonie"

# Sektor-Farben (HTML-Vorlage)
C_LEIT, C_ALT, C_KJF, C_STH, C_BIL, C_WEI = (
    "#E8860A", "#2E8B57", "#6B48C8", "#1A8C8C", "#C0392B", "#3949AB")

STRUCTURE = (
    "Bergische Diakonie", "root", "BDA", "✝", "#003F7D", [
        ("Leitung & Verwaltung", "sektor", "Leitung", "🏢", C_LEIT, [
            ("Aufsichtsrat", "bereich", None, "⚖️", None, []),
            ("Vorstand", "bereich", None, "🏛️", None, [
                ("Sekretariat Vorstand", "angebot", None, "📋", None, []),
                ("Zentrale Unternehmenskommunikation", "angebot", "ZUK", "📣", None, []),
                ("Angebotsberatung", "angebot", None, "📞", None, []),
                ("Zentrale Dienste", "angebot", None, "🔧", None, []),
                ("Verwaltung", "angebot", None, "💼", None, []),
            ]),
        ]),
        ("Altenhilfe", "sektor", "Alten", "🧓", C_ALT, [
            ("Pflegeeinrichtungen", "bereich", None, "🏠", None, [
                ("Haus Otto Ohl", "einrichtung", None, None, None, []),
                ("Haus Karl Heinersdorff", "einrichtung", None, None, None, []),
                ("Haus August von der Twer", "einrichtung", None, None, None, []),
                ("Haus Luise von der Heyden", "einrichtung", None, None, None, []),
                ("Diakoniezentrum Heiligenhaus", "einrichtung", None, None, None, []),
                ("Diakoniezentrum Monheim", "einrichtung", None, None, None, []),
                ("Haus Monheim", "einrichtung", None, None, None, []),
                ("Haus Lennep", "einrichtung", None, None, None, []),
                ("Pflegeeinrichtung Stockder-Stiftung", "einrichtung", None, None, None, []),
            ]),
            ("Tagespflege", "bereich", None, "☀️", None, [
                ("Tagespflege Haus August von der Twer", "einrichtung", None, None, None, []),
                ("Tagespflege Diakoniezentrum Heiligenhaus", "einrichtung", None, None, None, []),
                ("Tagespflege Diakoniezentrum Monheim", "einrichtung", None, None, None, []),
            ]),
            ("Service Wohnen", "bereich", None, "🏡", None, [
                ("Wohnen am Angergarten", "einrichtung", None, None, None, []),
                ("Service Wohnen Diakoniezentrum Heiligenhaus", "einrichtung", None, None, None, []),
                ("Service Wohnen Diakoniezentrum Monheim", "einrichtung", None, None, None, []),
            ]),
            ("Ambulante Pflege", "bereich", None, "🚗", None, [
                ("Diakoniestation Niederberg / Remscheid-Lennep", "einrichtung", None, None, None, []),
            ]),
        ]),
        ("Kinder, Jugend & Familie", "sektor", "KJF", "👶", C_KJF, [
            ("Erzieherische Hilfen", "bereich", None, "🧡", None, [
                ("Stationäre Angebote", "angebot", None, None, None, []),
                ("Tagesgruppen", "angebot", None, None, None, []),
                ("Soziale Gruppenarbeit", "angebot", None, None, None, []),
                ("Ambulante Erziehungshilfe", "angebot", None, None, None, []),
                ("Beratung Eltern, Kinder & Jugendliche", "angebot", None, None, None, []),
                ("Hilfen straffällige junge Menschen", "angebot", None, None, None, []),
            ]),
            ("HPZ & Fachklinik Kinder- und Jugendpsychiatrie", "bereich", "HPZ", "🧠", None, [
                ("Institutsambulanz", "angebot", None, None, None, []),
                ("Stationäre Behandlungsgruppen", "angebot", None, None, None, []),
                ("Tagesklinik", "angebot", None, None, None, []),
            ]),
            ("Evangelische Förderschule (KJF)", "bereich", "Förderschule", "🏫", None, [
                ("Schuldiagnostik (KJF)", "angebot", None, None, None, []),
                ("Stationärer Bereich (KJF)", "angebot", None, None, None, []),
                ("Teilstationärer Bereich (KJF)", "angebot", None, None, None, []),
            ]),
            ("Projekte KJF", "bereich", None, "🌱", None, [
                ("Care Leaver", "angebot", None, None, None, []),
                ("Moki „inklusiv“", "angebot", None, None, None, []),
            ]),
        ]),
        ("Sozialtherapeutische Hilfe", "sektor", "STH", "🤝", C_STH, [
            ("Wohnangebote", "bereich", None, "🏘️", None, [
                ("Besondere Wohnformen (§ 42a SGB XII)", "einrichtung", None, None, None, []),
                ("Wohnen zu Hause (Ambulant Betreutes Wohnen)", "einrichtung", None, None, None, []),
                ("Spezialpflege", "einrichtung", None, None, None, []),
            ]),
            ("Begleitende Fachdienste", "bereich", None, "🔬", None, [
                ("Fachabt. Tagesstruktur & Schulung", "angebot", None, None, None, []),
                ("Psychologischer Dienst", "angebot", None, None, None, []),
                ("Offenes Atelier", "angebot", None, None, None, []),
                ("Freundeskreis Ateliers", "angebot", None, None, None, []),
            ]),
            ("Soziale Dienste Niederberg", "bereich", None, "🌍", None, [
                ("Stadtteilzentren", "angebot", None, None, None, []),
                ("Fachstelle Sucht", "angebot", None, None, None, []),
                ("Schuldner- & Insolvenzberatung", "angebot", None, None, None, []),
                ("Wohnungslosenberatung", "angebot", None, None, None, []),
                ("Betreutes Wohnen / Wohnungslosenhilfe", "angebot", None, None, None, []),
                ("Betriebliche Sozialberatung", "angebot", None, None, None, []),
                ("Inklusionshilfe", "angebot", None, None, None, []),
                ("Flexible Erzieherische Hilfen", "angebot", None, None, None, []),
                ("Diakonie InfoPUNKT", "angebot", None, None, None, []),
                ("Projekt FamilienPaten", "angebot", None, None, None, []),
                ("Stadtlotsen", "angebot", None, None, None, []),
            ]),
        ]),
        ("Bildung", "sektor", "Bildung", "🎓", C_BIL, [
            ("Evangelisches Berufskolleg", "bereich", "EBK", "🏫", None, [
                ("Fachoberschule", "angebot", None, None, None, []),
                ("Sozialassistenz / Heilerziehungshilfe", "angebot", None, None, None, []),
                ("Heilerziehungspflege", "angebot", None, None, None, []),
                ("Sozialpädagogik", "angebot", None, None, None, []),
                ("Heilpädagogik", "angebot", None, None, None, []),
            ]),
            ("Bildungszentrum", "bereich", None, "📚", None, [
                ("Seminare & Termine", "angebot", None, None, None, []),
                ("Fort- & Weiterbildung", "angebot", None, None, None, []),
                ("Förderung & Finanzierung", "angebot", None, None, None, []),
                ("Seminarraum-Vermietung", "angebot", None, None, None, []),
            ]),
            ("Schule für Pflegeberufe", "bereich", None, "💉", None, [
                ("Pflegeausbildung (generalistisch)", "einrichtung", None, None, None, []),
            ]),
            ("Evangelische Förderschule (Bildung)", "bereich", None, "🌟", None, [
                ("Schuldiagnostik (Bildung)", "angebot", None, None, None, []),
                ("Stationärer Bereich (Bildung)", "angebot", None, None, None, []),
                ("Teilstationärer Bereich (Bildung)", "angebot", None, None, None, []),
            ]),
        ]),
        ("Weitere Angebote", "sektor", "Weitere", "➕", C_WEI, [
            ("Tafel", "bereich", None, "🥗", None, [
                ("Lebensmittelausgabe", "angebot", None, None, None, []),
            ]),
            ("Integrationsfachdienst Wuppertal", "bereich", None, "♿", None, [
                ("IFD Wuppertal", "angebot", None, None, None, []),
            ]),
            ("Sozialpsychiatrische Zentren", "bereich", None, "🧩", None, [
                ("SPZ Wuppertal", "angebot", None, None, None, []),
            ]),
            ("Ko(m)-Kolleg", "bereich", None, "🎓", None, [
                ("Fortbildung Kompetenzen", "angebot", None, None, None, []),
            ]),
            ("Nutzerbeirat", "bereich", None, "🗳️", None, [
                ("Mitbestimmung der Nutzenden", "angebot", None, None, None, []),
            ]),
            ("rückenwind", "bereich", None, "💚", None, [
                ("Unterstützungsprogramm", "angebot", None, None, None, []),
            ]),
        ]),
    ],
)

PERSONS = [
    ("Simone", "Küster", "Sekretariat Vorstand", "Sekretariat Vorstand", "mitarbeitende"),
    ("Renate", "Zanjani", "Abteilungsleitung", "Zentrale Unternehmenskommunikation", "leitung"),
]


def build(conn):
    name_to_id = {}

    def insert(node, parent_id, parent_icon):
        name, typ, short, icon, color, children = node
        eff_icon = icon or parent_icon  # Leaf ohne Icon erbt das des Bereichs
        res = organigram.add_unit(conn, name, parent_id, typ, TRAEGER, short, eff_icon, color)
        name_to_id[name] = res["id"]
        for ch in children:
            insert(ch, res["id"], eff_icon)

    insert(STRUCTURE, None, None)

    for first, last, role, unit_name, typ in PERSONS:
        uid = name_to_id.get(unit_name)
        organigram.add_person(conn, first, last, role, uid, None, typ)
    return name_to_id


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else None
    conn = db.connect(db_path) if db_path else db.connect()
    db.bootstrap(conn)
    # Idempotenz: vorhandene BDA-Struktur (Seed oder vorheriger Lauf) entfernen
    conn.execute("DELETE FROM org_persons WHERE traeger=?", (TRAEGER,))
    conn.execute("DELETE FROM org_units WHERE traeger=?", (TRAEGER,))
    conn.commit()
    build(conn)
    s = organigram.stats(conn, TRAEGER)
    print("STATS:", s)
    roots = organigram.tree(conn, TRAEGER)
    def count(n):
        return 1 + sum(count(c) for c in n["children"])
    for r in roots:
        print(f"ROOT {r['icon']} {r['name']} [{r['color']}]: {count(r)} Knoten, "
              f"{len(r['children'])} Sektoren")
        for sek in r["children"]:
            print(f"  {sek['icon']} {sek['name']} [{sek['color']}]: "
                  f"{len(sek['children'])} Bereiche, {count(sek)-1} Unterknoten")
    print("PERSONEN:", [f"{p['first_name']} {p['last_name']} → {p['unit']}"
                        for p in organigram.persons(conn, traeger=TRAEGER)])


if __name__ == "__main__":
    main()
