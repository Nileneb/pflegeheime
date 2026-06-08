"""Tier-1-Quellen-Seed (laufender Newsstrom).

Alle URLs wurden am 2026-06-08 live verifiziert (>=1 Feed-Entry). `verify()` prunt
künftig tote Feeds (enabled=0). presseportal = news aktuell (dpa-Tochter) → der
kostenlose „dpa"-Newsstrom für die Branchen-Themen Pflege/Altenpflege."""

TIER1 = [
    {"name": "presseportal Pflege (dpa-Tochter)", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.presseportal.de/rss/st/Pflege.rss2?langid=1"},
    {"name": "presseportal Altenpflege (dpa-Tochter)", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.presseportal.de/rss/st/Altenpflege.rss2?langid=1"},
    {"name": "BMG Pressemitteilungen", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.bundesgesundheitsministerium.de/pressemitteilungen.xml"},
    {"name": "BMG Meldungen", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.bundesgesundheitsministerium.de/meldungen.xml"},
    {"name": "GKV-Spitzenverband Pressemitteilungen", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.gkv-spitzenverband.de/gkv_spitzenverband/presse/pressemitteilungen_und_statements/rss_pressemitteilungen.xml"},
    {"name": "Häusliche Pflege (Vincentz)", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.haeusliche-pflege.net/feed/"},
    {"name": "Altenheim (Vincentz)", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.altenheim.net/rss"},
    {"name": "Care vor9", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.carevor9.de/view/rss"},
    {"name": "Ärzte Zeitung Politik", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.aerztezeitung.de/Politik.rss"},
    {"name": "DRK Aktuelles", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.drk.de/aktuell.rss"},
    {"name": "MAGS NRW Presse", "type": "rss", "tier": 1, "region": "NRW",
     "url": "https://www.mags.nrw/rss.xml"},
]


def seed(conn) -> int:
    """Fügt Tier-1-Quellen ein (UNIQUE url → idempotent). Gibt Anzahl NEUER Zeilen zurück."""
    n = 0
    for s in TIER1:
        cur = conn.execute(
            "INSERT OR IGNORE INTO sources(name,type,url,tier,region,enabled) "
            "VALUES (?,?,?,?,?,1)", (s["name"], s["type"], s["url"], s["tier"], s["region"]))
        n += cur.rowcount
    conn.commit()
    return n


def verify(conn) -> list[dict]:
    """GETtet jede Quelle, parst als Feed; tote/leere Quellen werden disabled.
    Gibt Statusliste zurück. Nur bei echtem Setup laufen lassen (Netz nötig)."""
    from marktradar import ingest
    out = []
    for row in conn.execute("SELECT id, name, url FROM sources").fetchall():
        err = None
        try:
            content = ingest.fetch(row["url"])
            ok = bool(content) and len(ingest.parse_feed(content)) > 0
        except Exception as e:
            ok, err = False, str(e)
        status = "ok" if ok else (f"verify: {err}" if err else "verify: kein gültiger Feed")
        conn.execute("UPDATE sources SET enabled=?, last_status=? WHERE id=?",
                     (1 if ok else 0, status, row["id"]))
        out.append({"name": row["name"], "ok": ok})
    conn.commit()
    return out
