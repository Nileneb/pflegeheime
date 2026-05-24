#!/usr/bin/env python3
"""QA-Filter über websearch_gf: behalte nur saubere Personen-Parts.

Die Web-Such-Treffer können Navigations-/Footer-Fragmente enthalten ("Aktuelles
Mitmachen, …"). Da kein Quelltext mitgespeichert ist, filtern wir rein über
looks_like_person + Lang-Token-Check: unsaubere Parts raus, leere Treffer → NULL.

Usage: python scripts/qa_websearch_gf.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect
from scripts.gf_extract import looks_like_person

LONG = re.compile(r"\b\w{18,}\b")


def main():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT api_id, gf FROM websearch_gf WHERE gf IS NOT NULL AND gf<>''")
    rows = cur.fetchall()
    changed = nulled = 0
    for api_id, gf in rows:
        parts = [p.strip() for p in gf.split(",") if p.strip()]
        keep = [p for p in parts if looks_like_person(p) and not LONG.search(p)]
        new = ", ".join(keep)
        if new == gf:
            continue
        if new:
            changed += 1
            cur.execute("UPDATE websearch_gf SET gf=%s WHERE api_id=%s", (new, api_id))
        else:
            nulled += 1
            cur.execute("UPDATE websearch_gf SET gf=NULL, status='filtered_junk' WHERE api_id=%s", (api_id,))
    conn.commit()
    cur.execute("SELECT count(*) FROM websearch_gf WHERE gf IS NOT NULL AND gf<>''")
    print(f"QA: {changed} bereinigt, {nulled} genullt. Übrig mit GF: {cur.fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    main()
