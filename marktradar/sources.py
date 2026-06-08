"""Quellen-Seed (laufender Newsstrom). Alle URLs am 2026-06-08 live verifiziert
(>=1 Feed-Entry, via 3 Recherche-Subagents). `verify()` prunt tote Feeds (enabled=0).

Tier 1 = laufender Newsstrom (Politik/Institutionen/Träger/Fachpresse).
Tier 2 = langsamere Spezialquellen (Hersteller/Software).
presseportal = news aktuell (dpa-Tochter): Themen-Feeds (st/…) + Firmen-Feeds (pm_…).
Quellenübergreifende Link-Dedup im Ingest fängt geteilte presseportal-Artikel ab."""


def _f(name, url, region="DE", tier=1):
    return {"name": name, "type": "rss", "tier": tier, "region": region, "url": url}


TIER1 = [
    # ── Politik / Bund / Länder / Institutionen ──────────────────────────────
    _f("presseportal Pflege (dpa)", "https://www.presseportal.de/rss/st/Pflege.rss2?langid=1"),
    _f("presseportal Altenpflege (dpa)", "https://www.presseportal.de/rss/st/Altenpflege.rss2?langid=1"),
    _f("presseportal Pflegeversicherung (dpa)", "https://www.presseportal.de/rss/st/Pflegeversicherung.rss2?langid=1"),
    _f("presseportal Pflegeheim (dpa)", "https://www.presseportal.de/rss/st/Pflegeheim.rss2?langid=1"),
    _f("presseportal Gesundheitspolitik (dpa)", "https://www.presseportal.de/rss/st/Gesundheitspolitik.rss2?langid=1"),
    _f("presseportal Krankenversicherung (dpa)", "https://www.presseportal.de/rss/st/Krankenversicherung.rss2?langid=1"),
    _f("presseportal Krankenkasse (dpa)", "https://www.presseportal.de/rss/st/Krankenkasse.rss2?langid=1"),
    _f("presseportal Sozialpolitik (dpa)", "https://www.presseportal.de/rss/st/Sozialpolitik.rss2?langid=1"),
    _f("presseportal Senioren (dpa)", "https://www.presseportal.de/rss/st/Senioren.rss2?langid=1"),
    _f("presseportal Gesundheitswesen (dpa)", "https://www.presseportal.de/rss/st/Gesundheitswesen.rss2?langid=1"),
    _f("presseportal Demenz (dpa)", "https://www.presseportal.de/rss/st/Demenz.rss2?langid=1"),
    _f("presseportal Medizintechnik (dpa)", "https://www.presseportal.de/rss/st/Medizintechnik.rss2?langid=1"),
    _f("BMG Pressemitteilungen", "https://www.bundesgesundheitsministerium.de/pressemitteilungen.xml"),
    _f("BMG Meldungen", "https://www.bundesgesundheitsministerium.de/meldungen.xml"),
    _f("BMAS (Arbeit & Soziales)", "https://www.bmas.de/SiteGlobals/Functions/RSSFeed/DE/RSSNewsfeed/RSSNewsfeed.xml"),
    _f("GKV-Spitzenverband", "https://www.gkv-spitzenverband.de/gkv_spitzenverband/presse/pressemitteilungen_und_statements/rss_pressemitteilungen.xml"),
    _f("Destatis Aktuell", "https://www.destatis.de/SiteGlobals/Functions/RSSFeed/DE/RSSNewsfeed/Aktuell.xml?nn=3624"),
    _f("vdek Ersatzkassen", "https://www.vdek.com/content/vdeksite/_jcr_content/par/pmlist.feed"),
    _f("MAGS NRW", "https://www.mags.nrw/rss.xml", "NRW"),
    _f("Bayern Landesportal", "https://www.bayern.de/rss/pm_alle.php", "BY"),
    _f("Baden-Württemberg Soziales", "https://www.baden-wuerttemberg.de/de/service/rss/xml/rss-soziales", "BW"),
    _f("Niedersachsen Landesregierung", "https://www.niedersachsen.de/rss/pressemitteilungen.xml", "NI"),
    _f("Schleswig-Holstein Soziales", "https://www.schleswig-holstein.de/DE/landesportal/service/RSS/RSS_PI_VIII/RSSNewsfeed_PI_VIII.xml", "SH"),
    _f("Schleswig-Holstein Gesundheit", "https://www.schleswig-holstein.de/DE/landesportal/service/RSS/RSS_PI_II/RSSNewsfeed_PI_II.xml", "SH"),
    # ── Träger / Betreiber ───────────────────────────────────────────────────
    _f("DRK Bundesverband", "https://www.drk.de/aktuell.rss"),
    _f("Johanniter-Unfall-Hilfe", "https://www.presseportal.de/rss/pm_14240.rss"),
    _f("Malteser Hilfsdienst", "https://www.presseportal.de/rss/pm_58983.rss"),
    _f("ASB", "https://www.presseportal.de/rss/pm_6532.rss"),
    _f("Der Paritätische", "https://www.der-paritaetische.de/xml/rss2/"),
    _f("Diakonie Deutschland", "https://www.presseportal.de/rss/pm_111550.rss"),
    _f("BAGFW (Freie Wohlfahrtspflege)", "https://www.presseportal.de/rss/pm_65567.rss"),
    _f("AWO Bayern", "https://awo-bayern.de/feed/", "BY"),
    _f("AWO Sachsen", "https://www.awo-sachsen.de/feed/", "SN"),
    _f("Diakonie Sachsen", "https://www.diakonie-sachsen.de/feed/", "SN"),
    _f("Diakonie Mitteldeutschland", "https://www.diakonie-mitteldeutschland.de/feed/"),
    _f("Caritas Dortmund", "http://www.caritas-dortmund.de/public/rss.ashx?id=537e8612-3bc1-40bb-8279-5c13939cdb8c", "NRW"),
    _f("Korian Deutschland", "https://www.korian.de/ratgeber-magazin-kategorie/neues-von-korian/feed/"),
    _f("Convivo", "https://www.convivo.de/feed/"),
    _f("EMVIA Living", "https://www.emvia-living.de/feed/"),
    _f("CURATA Seniorenwohnzentren", "https://www.curata.de/feed/"),
    _f("AZURIT Gruppe", "https://www.azurit-gruppe.de/feed/"),
    # ── Fachpresse ───────────────────────────────────────────────────────────
    _f("Altenheim (Vincentz)", "https://www.altenheim.net/feed/"),
    _f("Häusliche Pflege (Vincentz)", "https://www.haeusliche-pflege.net/feed/"),
    _f("Care vor9 (Vincentz)", "https://www.carevor9.de/view/rss"),
    _f("Ärzte Zeitung Politik", "https://www.aerztezeitung.de/Politik.rss"),
    _f("Deutsches Ärzteblatt", "https://rss.aerzteblatt.de/rss/news.asp"),
    _f("kma Online (Klinikmanagement)", "https://www.kma-online.de/dienste/feeds/aktuelles.xml"),
    _f("Pflegezeitschrift (Springer)", "https://link.springer.com/search.rss?facet-journal-id=41906"),
    _f("Heilberufe (Springer)", "https://link.springer.com/search.rss?facet-journal-id=58"),
    _f("healthcare-digital", "https://www.healthcare-digital.de/rss/news.xml", "DE", 2),
    # ── Hersteller / Zulieferer / Software (Tier 2) ──────────────────────────
    _f("DeviceMed (Medizintechnik)", "https://www.devicemed.de/rss/news.xml", "DE", 2),
    _f("BVMed (Medizintechnologie)", "https://www.bvmed.de/rss-feed.xml", "DE", 2),
    _f("Paul Hartmann AG", "https://www.presseportal.de/rss/pm_34248.rss2", "DE", 2),
    _f("Essity / TENA", "https://www.presseportal.de/rss/pm_65315.rss2", "DE", 2),
    _f("Drägerwerk AG", "https://www.presseportal.de/rss/pm_25153.rss2", "DE", 2),
    _f("CompuGroup Medical (CGM)", "https://www.presseportal.de/rss/pm_100921.rss2", "DE", 2),
    _f("MEDIFOX DAN (Pflegesoftware)", "https://www.presseportal.de/rss/pm_156286.rss2", "DE", 2),
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
