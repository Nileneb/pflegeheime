#!/usr/bin/env python3
"""Wie viel hat der API-Refresh gebracht? Abdeckung + Vergleich zu mistral."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect, _is_data

conn = db_connect(); cur = conn.cursor()
def sc(sql,*p): cur.execute(sql,p); return cur.fetchone()[0]
N = sc("SELECT count(*) FROM pflegeheime")
print(f"TOTAL {N}\n--- API-Feld-Abdeckung (nicht leer) ---")
for col in ["telefon_api","email_api","web_api","strasse_api","plz_api","ansprechpartner_api","fax_api"]:
    n = sc(f"SELECT count(*) FROM pflegeheime WHERE {col} IS NOT NULL AND {col}<>''")
    print(f"  {col:22} {n:5}  ({100*n//N}%)")

print("\n--- Adresse komplett (strasse+plz+ort) ---")
print("  ", sc("SELECT count(*) FROM pflegeheime WHERE strasse_api<>'' AND plz_api<>'' AND ort_api<>''"))

# Wie viele der 1041 'no website' haben jetzt eine offizielle web_api?
print("\n--- Ex-'no website' Zeilen, jetzt mit offizieller web_api ---")
print("  ", sc("SELECT count(*) FROM pflegeheime WHERE (website IS NULL OR website='') AND web_api IS NOT NULL AND web_api<>''"),
      "von", sc("SELECT count(*) FROM pflegeheime WHERE (website IS NULL OR website='')"))

# Konflikt: mistral-telefon vs api-telefon (digit-vergleich)
import re
def digits(s): return re.sub(r"\D","", s or "")
cur.execute("SELECT telefon_clean, telefon_api FROM pflegeheime WHERE telefon_api<>''")
both=match=diff=0
for mc, api in cur.fetchall():
    if _is_data(mc or ""):
        both+=1
        # vergleiche letzte 7 stellen (durchwahl-toleranz)
        dm, da = digits(mc), digits(api)
        if dm and da and (dm[-6:]==da[-6:] or da in dm or dm in da):
            match+=1
        else:
            diff+=1
print(f"\n--- Telefon: mistral vs API (beide vorhanden: {both}) ---")
print(f"  stimmt grob überein: {match}")
print(f"  WEICHT AB:           {diff}   <- mistral war hier wahrscheinlich falsch")

# ansprechpartner: wie oft eine echte Person (enthält Leerzeichen, nicht nur Rolle)?
cur.execute("SELECT ansprechpartner_api FROM pflegeheime WHERE ansprechpartner_api<>''")
aps=[r[0] for r in cur.fetchall()]
person_like = sum(1 for a in aps if len(a.split())>=2 and not any(w in a.lower() for w in ["sozial","dienst","verwaltung","leitung","büro","sekretariat","empfang","pflegedienst"]))
print(f"\n--- ansprechpartner_api ({len(aps)} gesetzt) ---")
print(f"  sieht nach Personenname aus: ~{person_like}")
for a in aps[:8]: print("   ", a)
conn.close()
