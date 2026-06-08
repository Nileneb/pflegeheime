from marktradar import migrate


def test_migrate_heime_counts(conn, tmp_path):
    csv = tmp_path / "h.csv"
    csv.write_text(
        "﻿ID;Name;Träger;Ort;Kreis;Adresse;Telefon;E-Mail;Website;"
        "Geschäftsführung;GF-Quelle;Einrichtungsleitung;EL-Quelle;Facebook;Instagram;"
        "LinkedIn;YouTube;Social Media (alle);Newsletter;Qualität;Quellen/Hinweise\n"
        "1;Haus Sonne;Caritas;Aachen;SR Aachen;Str 1;0241 1;a@b.de;http://x;"
        "Max M;web;Erika B;web;;;;;;;OK;notes\n", encoding="utf-8")
    n = migrate.migrate_heime(conn, str(csv))
    assert n == 1
    row = conn.execute("SELECT name, traeger, geschaeftsfuehrung FROM pflegeheime").fetchone()
    assert row["name"] == "Haus Sonne"
    assert row["traeger"] == "Caritas"
    assert row["geschaeftsfuehrung"] == "Max M"


def test_migrate_articles_counts(conn, tmp_path):
    csv = tmp_path / "n.csv"
    csv.write_text(
        "﻿Datum;Quelle (Domain);Titel;Kategorie;Grund;Link\n"
        "2026-06-01;example.org;Eröffnung XY;News;echt;http://x/1\n", encoding="utf-8")
    n = migrate.migrate_articles(conn, str(csv))
    assert n == 1
    row = conn.execute("SELECT title, relevant, source_domain FROM articles").fetchone()
    assert row["title"] == "Eröffnung XY"
    assert row["relevant"] == 1
    assert row["source_domain"] == "example.org"
