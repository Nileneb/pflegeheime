"""Einmal-Migration: CSV-Bestände → SQLite. Idempotent (re-runnable)."""
import csv
import json
import sys

from marktradar import db


def migrate_heime(conn, csv_path: str) -> int:
    n = 0
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            social = {k: row.get(k) for k in ("Facebook", "Instagram", "LinkedIn", "YouTube")
                      if row.get(k)}
            conn.execute(
                "INSERT INTO pflegeheime(name,traeger,ort,kreis,adresse,telefon,email,website,"
                "geschaeftsfuehrung,einrichtungsleitung,social_json,newsletter,quality,quellen)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row.get("Name"), row.get("Träger"), row.get("Ort"), row.get("Kreis"),
                 row.get("Adresse"), row.get("Telefon"), row.get("E-Mail"), row.get("Website"),
                 row.get("Geschäftsführung"), row.get("Einrichtungsleitung"),
                 json.dumps(social, ensure_ascii=False) if social else None,
                 row.get("Newsletter"), row.get("Qualität"), row.get("Quellen/Hinweise")))
            n += 1
    conn.commit()
    return n


def migrate_articles(conn, csv_path: str) -> int:
    n = 0
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            dom = row.get("Quelle (Domain)") or ""
            guid = row.get("Link") or f"{dom}:{row.get('Titel')}"
            cur = conn.execute(
                "INSERT OR IGNORE INTO articles"
                "(source_id,source_domain,guid,link,title,published,kategorie,grund,relevant)"
                " VALUES (NULL,?,?,?,?,?,?,?,1)",
                (dom, guid, row.get("Link"), row.get("Titel"), row.get("Datum") or None,
                 row.get("Kategorie"), row.get("Grund")))
            n += cur.rowcount
    conn.commit()
    return n


def main():
    conn = db.connect()
    db.bootstrap(conn)
    h = migrate_heime(conn, "data/pflegeheime_final.csv")
    a = migrate_articles(conn, "out/newsletter_kandidaten.csv")
    print(f"Migriert: {h} Heime, {a} Artikel → {db.DEFAULT_DB}", file=sys.stderr)
    conn.close()


if __name__ == "__main__":
    main()
