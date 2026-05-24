#!/usr/bin/env python3
"""Halluzinations-Check: GF-Daten bei Zeilen ohne Quelle?"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect, _is_data

conn = db_connect()
cur = conn.cursor()

# Kreuztabelle: status (Quelle vorhanden?) x GF-real?
print("=== GF-real nach Original-Scrape-Status ===")
cur.execute("""
   SELECT status, website, geschaeftsfuehrung, geschaeftsfuehrung_clean,
          telefon_clean, email_clean, adresse_clean
   FROM pflegeheime
""")
rows = cur.fetchall()

buckets = {}
for status, web, gf_orig, gf_clean, tel, email, addr in rows:
    has_web = bool((web or "").strip())
    key = (status, "web" if has_web else "noweb")
    b = buckets.setdefault(key, {"n":0, "gf_orig":0, "gf_clean":0,
                                 "gf_invented":0, "tel":0, "email":0, "addr":0})
    b["n"] += 1
    if _is_data(gf_orig or ""): b["gf_orig"] += 1
    if _is_data(gf_clean or ""): b["gf_clean"] += 1
    # invented = clean hat GF, original NICHT und keine website
    if _is_data(gf_clean or "") and not _is_data(gf_orig or "") and not has_web:
        b["gf_invented"] += 1
    if _is_data(tel or ""): b["tel"] += 1
    if _is_data(email or ""): b["email"] += 1
    if _is_data(addr or ""): b["addr"] += 1

print(f"{'status/web':28} {'N':>5} {'GForig':>7} {'GFclean':>8} {'INVENT':>7} {'tel':>5} {'mail':>5} {'addr':>5}")
for k in sorted(buckets):
    b = buckets[k]
    print(f"{str(k):28} {b['n']:5} {b['gf_orig']:7} {b['gf_clean']:8} {b['gf_invented']:7} {b['tel']:5} {b['email']:5} {b['addr']:5}")

# Konkrete verdächtige Beispiele: noweb + search_failed + GF vorhanden
print("\n=== 12 verdächtige GF (no website, search_failed, aber GF 'gefunden') ===")
cur.execute("""
   SELECT name, ort, geschaeftsfuehrung_clean, telefon_clean, email_clean, clean_notes
   FROM pflegeheime
   WHERE (website IS NULL OR website='') AND status='search_failed'
   ORDER BY api_id LIMIT 200
""")
shown = 0
for name, ort, gf, tel, email, notes in cur.fetchall():
    if _is_data(gf or ""):
        print(f"  {name[:38]:38} {ort[:14]:14} GF={gf[:30]:30} tel={tel or '-'}")
        shown += 1
        if shown >= 12: break

# Telefon/email auch bei noweb+search_failed?
print("\n=== Kontaktdaten bei no website + search_failed ===")
cur.execute("""
   SELECT count(*),
          count(*) FILTER (WHERE telefon_clean IS NOT NULL AND telefon_clean <> '' AND telefon_clean NOT ILIKE '%no clear%'),
          count(*) FILTER (WHERE email_clean   IS NOT NULL AND email_clean   <> '' AND email_clean   NOT ILIKE '%no clear%')
   FROM pflegeheime WHERE (website IS NULL OR website='') AND status='search_failed'
""")
n, t, e = cur.fetchone()
print(f"  no-web+search_failed: {n} rows, mit tel={t}, mit email={e}")

conn.close()
