"""Testet das Organigram-Tool: baut die reale Bergische-Diakonie-Struktur
ausschließlich über organigram.add_unit / add_person auf (genau die Logik, die
die MCP-Tools add_org_unit / add_org_person deployen) und verifiziert per
tree()/stats(). Aufruf: python -m scripts.build_bda_organigram [DB_PATH]"""
import sys
from marktradar import db, organigram

TRAEGER = "Bergische Diakonie"

# Verschachtelt: (name, type, short_name, [children])
STRUCTURE = (
    "Bergische Diakonie", "root", "BDA", [
        ("Leitung & Verwaltung", "sektor", "Leitung", [
            ("Aufsichtsrat", "bereich", None, []),
            ("Vorstand", "bereich", None, [
                ("Sekretariat Vorstand", "angebot", None, []),
                ("Zentrale Unternehmenskommunikation", "angebot", "ZUK", []),
                ("Angebotsberatung", "angebot", None, []),
                ("Zentrale Dienste", "angebot", None, []),
                ("Verwaltung", "angebot", None, []),
            ]),
        ]),
        ("Altenhilfe", "sektor", "Alten", [
            ("Pflegeeinrichtungen", "bereich", None, [
                ("Haus Otto Ohl", "einrichtung", None, []),
                ("Haus Karl Heinersdorff", "einrichtung", None, []),
                ("Haus August von der Twer", "einrichtung", None, []),
                ("Haus Luise von der Heyden", "einrichtung", None, []),
                ("Diakoniezentrum Heiligenhaus", "einrichtung", None, []),
                ("Diakoniezentrum Monheim", "einrichtung", None, []),
                ("Haus Monheim", "einrichtung", None, []),
                ("Haus Lennep", "einrichtung", None, []),
                ("Pflegeeinrichtung Stockder-Stiftung", "einrichtung", None, []),
            ]),
            ("Tagespflege", "bereich", None, [
                ("Tagespflege Haus August von der Twer", "einrichtung", None, []),
                ("Tagespflege Diakoniezentrum Heiligenhaus", "einrichtung", None, []),
                ("Tagespflege Diakoniezentrum Monheim", "einrichtung", None, []),
            ]),
            ("Service Wohnen", "bereich", None, [
                ("Wohnen am Angergarten", "einrichtung", None, []),
                ("Service Wohnen Diakoniezentrum Heiligenhaus", "einrichtung", None, []),
                ("Service Wohnen Diakoniezentrum Monheim", "einrichtung", None, []),
            ]),
            ("Ambulante Pflege", "bereich", None, [
                ("Diakoniestation Niederberg / Remscheid-Lennep", "einrichtung", None, []),
            ]),
        ]),
        ("Kinder, Jugend & Familie", "sektor", "KJF", [
            ("Erzieherische Hilfen", "bereich", None, [
                ("Stationäre Angebote", "angebot", None, []),
                ("Tagesgruppen", "angebot", None, []),
                ("Soziale Gruppenarbeit", "angebot", None, []),
                ("Ambulante Erziehungshilfe", "angebot", None, []),
                ("Beratung Eltern, Kinder & Jugendliche", "angebot", None, []),
                ("Hilfen straffällige junge Menschen", "angebot", None, []),
            ]),
            ("HPZ & Fachklinik Kinder- und Jugendpsychiatrie", "bereich", "HPZ", [
                ("Institutsambulanz", "angebot", None, []),
                ("Stationäre Behandlungsgruppen", "angebot", None, []),
                ("Tagesklinik", "angebot", None, []),
            ]),
            ("Evangelische Förderschule (KJF)", "bereich", "Förderschule", [
                ("Schuldiagnostik (KJF)", "angebot", None, []),
                ("Stationärer Bereich (KJF)", "angebot", None, []),
                ("Teilstationärer Bereich (KJF)", "angebot", None, []),
            ]),
            ("Projekte KJF", "bereich", None, [
                ("Care Leaver", "angebot", None, []),
                ("Moki „inklusiv“", "angebot", None, []),
            ]),
        ]),
        ("Sozialtherapeutische Hilfe", "sektor", "STH", [
            ("Wohnangebote", "bereich", None, [
                ("Besondere Wohnformen (§ 42a SGB XII)", "einrichtung", None, []),
                ("Wohnen zu Hause (Ambulant Betreutes Wohnen)", "einrichtung", None, []),
                ("Spezialpflege", "einrichtung", None, []),
            ]),
            ("Begleitende Fachdienste", "bereich", None, [
                ("Fachabt. Tagesstruktur & Schulung", "angebot", None, []),
                ("Psychologischer Dienst", "angebot", None, []),
                ("Offenes Atelier", "angebot", None, []),
                ("Freundeskreis Ateliers", "angebot", None, []),
            ]),
            ("Soziale Dienste Niederberg", "bereich", None, [
                ("Stadtteilzentren", "angebot", None, []),
                ("Fachstelle Sucht", "angebot", None, []),
                ("Schuldner- & Insolvenzberatung", "angebot", None, []),
                ("Wohnungslosenberatung", "angebot", None, []),
                ("Betreutes Wohnen / Wohnungslosenhilfe", "angebot", None, []),
                ("Betriebliche Sozialberatung", "angebot", None, []),
                ("Inklusionshilfe", "angebot", None, []),
                ("Flexible Erzieherische Hilfen", "angebot", None, []),
                ("Diakonie InfoPUNKT", "angebot", None, []),
                ("Projekt FamilienPaten", "angebot", None, []),
                ("Stadtlotsen", "angebot", None, []),
            ]),
        ]),
        ("Bildung", "sektor", "Bildung", [
            ("Evangelisches Berufskolleg", "bereich", "EBK", [
                ("Fachoberschule", "angebot", None, []),
                ("Sozialassistenz / Heilerziehungshilfe", "angebot", None, []),
                ("Heilerziehungspflege", "angebot", None, []),
                ("Sozialpädagogik", "angebot", None, []),
                ("Heilpädagogik", "angebot", None, []),
            ]),
            ("Bildungszentrum", "bereich", None, [
                ("Seminare & Termine", "angebot", None, []),
                ("Fort- & Weiterbildung", "angebot", None, []),
                ("Förderung & Finanzierung", "angebot", None, []),
                ("Seminarraum-Vermietung", "angebot", None, []),
            ]),
            ("Schule für Pflegeberufe", "bereich", None, [
                ("Pflegeausbildung (generalistisch)", "einrichtung", None, []),
            ]),
            ("Evangelische Förderschule (Bildung)", "bereich", None, [
                ("Schuldiagnostik (Bildung)", "angebot", None, []),
                ("Stationärer Bereich (Bildung)", "angebot", None, []),
                ("Teilstationärer Bereich (Bildung)", "angebot", None, []),
            ]),
        ]),
        ("Weitere Angebote", "sektor", "Weitere", [
            ("Tafel", "bereich", None, [
                ("Lebensmittelausgabe", "angebot", None, []),
            ]),
            ("Integrationsfachdienst Wuppertal", "bereich", None, [
                ("IFD Wuppertal", "angebot", None, []),
            ]),
            ("Sozialpsychiatrische Zentren", "bereich", None, [
                ("SPZ Wuppertal", "angebot", None, []),
            ]),
            ("Ko(m)-Kolleg", "bereich", None, [
                ("Fortbildung Kompetenzen", "angebot", None, []),
            ]),
            ("Nutzerbeirat", "bereich", None, [
                ("Mitbestimmung der Nutzenden", "angebot", None, []),
            ]),
            ("rückenwind", "bereich", None, [
                ("Unterstützungsprogramm", "angebot", None, []),
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

    def insert(node, parent_id):
        name, typ, short, children = node
        res = organigram.add_unit(conn, name, parent_id, typ, TRAEGER, short)
        name_to_id[name] = res["id"]
        for ch in children:
            insert(ch, res["id"])

    insert(STRUCTURE, None)

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
        print(f"ROOT {r['name']}: {count(r)} Knoten, {len(r['children'])} Sektoren")
        for sek in r["children"]:
            print(f"  {sek['icon'] or '•'} {sek['name']}: {len(sek['children'])} Bereiche, "
                  f"{count(sek)-1} Unterknoten")
    print("PERSONEN:", [f"{p['first_name']} {p['last_name']} → {p['unit']}"
                        for p in organigram.persons(conn, traeger=TRAEGER)])


if __name__ == "__main__":
    main()
