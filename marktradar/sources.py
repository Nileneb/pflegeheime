"""Tier-1-Quellen-Seed. URLs sind Kandidaten — `verify()` prunt tote Feeds."""

TIER1 = [
    {"name": "presseportal Gesundheit/Soziales", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.presseportal.de/rss/themen/gesundheit.rss2"},
    {"name": "BMG Presse", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.bundesgesundheitsministerium.de/rss/presse.xml"},
    {"name": "pflegen-online (Vincentz)", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.pflegen-online.de/rss"},
    {"name": "Altenheim (Vincentz)", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.altenheim.net/rss"},
    {"name": "Ärzte Zeitung Pflege", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.aerztezeitung.de/rss/pflege.rss"},
    {"name": "GKV-Spitzenverband Presse", "type": "rss", "tier": 1, "region": "DE",
     "url": "https://www.gkv-spitzenverband.de/service/presse/pressemitteilungen/pressemitteilungen.rss"},
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
        try:
            content = ingest.fetch(row["url"])
            ok = bool(content) and len(ingest.parse_feed(content)) > 0
        except Exception:
            ok = False
        conn.execute("UPDATE sources SET enabled=?, last_status=? WHERE id=?",
                     (1 if ok else 0, "ok" if ok else "verify: kein gültiger Feed", row["id"]))
        out.append({"name": row["name"], "ok": ok})
    conn.commit()
    return out
