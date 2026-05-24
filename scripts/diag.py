#!/usr/bin/env python3
"""Datenstand-Diagnose: was ist in der DB, wie ist die Qualität, wo fehlt GF?"""
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect, _is_data

conn = db_connect()
cur = conn.cursor()

def scalar(sql, *p):
    cur.execute(sql, p); return cur.fetchone()[0]

print("=" * 60)
total = scalar("SELECT count(*) FROM pflegeheime")
print(f"TOTAL rows:        {total}")
cleaned = scalar("SELECT count(*) FROM pflegeheime WHERE cleaned = TRUE")
print(f"cleaned = TRUE:    {cleaned}")
print(f"cleaned = FALSE:   {total - cleaned}")

print("\n--- quality ---")
cur.execute("SELECT quality, count(*) FROM pflegeheime GROUP BY quality ORDER BY 2 DESC")
for q, c in cur.fetchall():
    print(f"  {str(q):10} {c}")

print("\n--- cleaner ---")
cur.execute("SELECT cleaner, count(*) FROM pflegeheime GROUP BY cleaner ORDER BY 2 DESC")
for q, c in cur.fetchall():
    print(f"  {str(q):20} {c}")

print("\n--- original scraped status ---")
cur.execute("SELECT status, count(*) FROM pflegeheime GROUP BY status ORDER BY 2 DESC")
for q, c in cur.fetchall():
    print(f"  {str(q):20} {c}")

# Geschäftsführer-Abdeckung (das Kernziel)
print("\n--- Geschäftsführung (clean) Abdeckung ---")
cur.execute("SELECT geschaeftsfuehrung_clean FROM pflegeheime WHERE cleaned = TRUE")
gf_rows = [r[0] for r in cur.fetchall()]
gf_real = sum(1 for g in gf_rows if _is_data(g or ""))
print(f"  cleaned rows:            {len(gf_rows)}")
print(f"  GF mit echten Daten:     {gf_real}")
print(f"  GF leer/No clear Data:   {len(gf_rows) - gf_real}")

# Einrichtungsleitung-Abdeckung
cur.execute("SELECT einrichtungsleitung_clean FROM pflegeheime WHERE cleaned = TRUE")
el_rows = [r[0] for r in cur.fetchall()]
el_real = sum(1 for g in el_rows if _is_data(g or ""))
print(f"\n  EL mit echten Daten:     {el_real} / {len(el_rows)}")

# website-Abdeckung (für GF-Suche relevant)
print("\n--- website-Abdeckung ---")
with_web = scalar("SELECT count(*) FROM pflegeheime WHERE website IS NOT NULL AND website <> ''")
print(f"  mit website:    {with_web}")
print(f"  ohne website:   {total - with_web}")

# Beispiele: cleaned rows ohne GF (Kandidaten für GF-Suche)
print("\n--- 8 Beispiele: cleaned, aber GF fehlt, MIT website ---")
cur.execute("""
    SELECT name, ort, website, geschaeftsfuehrung_clean, quality
    FROM pflegeheime
    WHERE cleaned = TRUE
      AND website IS NOT NULL AND website <> ''
    ORDER BY api_id LIMIT 40
""")
shown = 0
for name, ort, web, gf, q in cur.fetchall():
    if not _is_data(gf or ""):
        print(f"  [{q}] {name[:35]:35} {ort[:15]:15} {(web or '')[:40]}")
        shown += 1
        if shown >= 8:
            break

conn.close()
print("=" * 60)
