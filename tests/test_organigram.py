from marktradar import organigram


def test_seed_idempotent_and_tree(conn):
    n = organigram.seed(conn)
    assert n == len(organigram.SEED_UNITS)
    assert organigram.seed(conn) == 0  # idempotent
    tree = organigram.tree(conn)
    assert len(tree) == 1 and tree[0]["name"] == "Bergische Diakonie"
    sektoren = tree[0]["children"]
    assert any(s["name"] == "Altenhilfe" for s in sektoren)
    # Einrichtungen hängen unter Altenhilfe → Pflegeeinrichtungen
    alten = next(s for s in sektoren if s["name"] == "Altenhilfe")
    pe = next(b for b in alten["children"] if b["name"] == "Pflegeeinrichtungen")
    assert any(e["name"] == "Haus Otto Ohl" for e in pe["children"])


def test_persons_recursive_and_stats(conn):
    organigram.seed(conn)
    st = organigram.stats(conn)
    assert st["personen"] == len(organigram.SEED_PERSONS)
    assert st["einrichtungen"] == 9
    # Person unter Altenhilfe (unit_id=3) rekursiv → Hans Muster (Haus Otto Ohl)
    alten = organigram.persons(conn, unit_id=3)
    assert any(p["last_name"] == "Muster" for p in alten)


def test_add_unit_and_person(conn):
    organigram.seed(conn)
    u = organigram.add_unit(conn, "Neuer Standort", parent_id=14, type="einrichtung")
    assert u["level"] == 3
    p = organigram.add_person(conn, "Test", "Person", role="Leitung", unit_id=u["id"])
    found = organigram.persons(conn, unit_id=u["id"])
    assert any(x["last_name"] == "Person" for x in found)
