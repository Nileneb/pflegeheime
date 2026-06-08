"""Quellen-Seed (laufender Newsstrom). Alle URLs am 2026-06-08 live verifiziert
(>=1 Feed-Entry, via 3 Recherche-Subagents). `verify()` prunt tote Feeds (enabled=0).

Tier 1 = laufender Newsstrom (Politik/Institutionen/Träger/Fachpresse).
Tier 2 = langsamere Spezialquellen (Hersteller/Software).
Tier 3 = internationale Wissenschaft (Journals/Institute, EN, langsamer Takt).
presseportal = news aktuell (dpa-Tochter): Themen-Feeds (st/…) + Firmen-Feeds (pm_…).
Quellenübergreifende Link-Dedup im Ingest fängt geteilte presseportal-Artikel ab.

`tier` ist die manuelle Start-Einstufung; die LAUFENDE Güte kommt dynamisch aus
`stats()` (Wilson-Lower-Bound auf dem Relevanz-Yield) — stabil relevante Quellen
steigen über Volumen langfristig nach oben, Noise sinkt in den Bodensatz."""
import math
from datetime import datetime, timedelta, timezone


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
    # ── WHO / EU / Public-Health-Institutionen ───────────────────────────────
    _f("WHO News (English)", "https://www.who.int/rss-feeds/news-english.xml", "INT", 1),
    _f("EU-Kommission Presscorner", "https://ec.europa.eu/commission/presscorner/api/rss", "EU", 1),
    _f("ECDC News", "https://www.ecdc.europa.eu/en/taxonomy/term/1307/feed", "EU", 1),
    _f("ECDC Publications", "https://www.ecdc.europa.eu/en/taxonomy/term/1244/feed", "EU", 1),
    _f("RKI aktuell", "https://www.rki.de/SiteGlobals/Functions/RSS/RSS-neue-Dokumente.xml?nn=16776976", "DE", 1),
    _f("RKI Epidemiologisches Bulletin", "https://www.rki.de/SiteGlobals/Functions/RSS/RSS-EpidBull.xml?nn=16776976", "DE", 1),
    # ── Parteien (Diskurs-Akteure, Tier 2 — viel allg. Politik, qwen filtert) ─
    _f("CDU", "https://www.cdu.de/feed", "DE", 2),
    _f("CDU/CSU Bundestagsfraktion", "https://www.presseportal.de/rss/pm_7846.rss2?langid=1", "DE", 2),
    _f("CSU", "https://www.csu.de/rss/", "DE", 2),
    _f("SPD", "https://www.spd.de/aktuelles/feed.rss", "DE", 2),
    _f("SPD Bundestagsfraktion", "https://www.spdfraktion.de/presse/pressemitteilungen/feed", "DE", 2),
    _f("Bündnis 90/Die Grünen Bundestagsfraktion", "https://www.gruene-bundestag.de/pressemitteilungen-rss-feed.xml", "DE", 2),
    _f("FDP", "https://www.fdp.de/rss.xml", "DE", 2),
    _f("Die Linke", "https://www.die-linke.de/presse/feed.rss", "DE", 2),
    _f("Die Linke Bundestagsfraktion", "https://www.dielinkebt.de/presse/pressemitteilungen/feed.rss", "DE", 2),
    _f("AfD", "https://www.afd.de/feed/", "DE", 2),
    _f("AfD Bundestagsfraktion", "https://afdbundestag.de/pressemitteilungen/feed/", "DE", 2),
    _f("BSW", "https://bsw-vg.de/feed/", "DE", 2),
]


# ── Tier 3: internationale Wissenschaft (Journals/Institute, EN/DE) ──────────
# Am 2026-06-08 live verifiziert (>=3 Feed-Entries). Langsamerer Takt, oft EN —
# die Relevanz-Klassifikation + der dynamische Score (stats()) sortieren ab, was
# nicht ins Pflege-/Gesundheits-/Sozial-Spektrum passt.
TIER3 = [
    # Open-Access-Journals (PLOS / BMJ)
    _f("PLOS Medicine", "https://journals.plos.org/plosmedicine/feed/atom", "INT", 3),
    _f("PLOS Global Public Health", "https://journals.plos.org/globalpublichealth/feed/atom", "INT", 3),
    _f("PLOS ONE", "https://journals.plos.org/plosone/feed/atom", "INT", 3),
    _f("BMJ Open", "https://bmjopen.bmj.com/rss/current.xml", "INT", 3),
    # Lancet-Familie
    _f("The Lancet", "https://www.thelancet.com/rssfeed/lancet_current.xml", "INT", 3),
    _f("Lancet Public Health", "https://www.thelancet.com/rssfeed/lanpub_current.xml", "INT", 3),
    _f("Lancet Healthy Longevity", "https://www.thelancet.com/rssfeed/lanhl_current.xml", "INT", 3),
    _f("Lancet Global Health", "https://www.thelancet.com/rssfeed/langlo_current.xml", "INT", 3),
    # Elsevier ScienceDirect (Pflege-/Sozialwissenschaft)
    _f("Int. J. Nursing Studies", "https://rss.sciencedirect.com/publication/science/00207489", "INT", 3),
    _f("Social Science & Medicine", "https://rss.sciencedirect.com/publication/science/02779536", "INT", 3),
    _f("Archives of Gerontology and Geriatrics", "https://rss.sciencedirect.com/publication/science/01674943", "INT", 3),
    # Wiley
    _f("Journal of Advanced Nursing", "https://onlinelibrary.wiley.com/feed/13652648/most-recent", "INT", 3),
    # Springer (BMC + Gerontologie/Public Health; DE-Journals besonders on-topic)
    _f("BMC Geriatrics", "https://link.springer.com/search.rss?facet-journal-id=12877", "INT", 3),
    _f("BMC Nursing", "https://link.springer.com/search.rss?facet-journal-id=12912", "INT", 3),
    _f("BMC Health Services Research", "https://link.springer.com/search.rss?facet-journal-id=12913", "INT", 3),
    _f("BMC Public Health", "https://link.springer.com/search.rss?facet-journal-id=12889", "INT", 3),
    _f("European Journal of Ageing", "https://link.springer.com/search.rss?facet-journal-id=10433", "INT", 3),
    _f("Aging Clinical and Experimental Research", "https://link.springer.com/search.rss?facet-journal-id=40520", "INT", 3),
    _f("GeroScience", "https://link.springer.com/search.rss?facet-journal-id=11357", "INT", 3),
    _f("European Journal of Epidemiology", "https://link.springer.com/search.rss?facet-journal-id=10654", "INT", 3),
    _f("International Journal of Public Health", "https://link.springer.com/search.rss?facet-journal-id=38", "INT", 3),
    _f("Quality of Life Research", "https://link.springer.com/search.rss?facet-journal-id=11136", "INT", 3),
    _f("Journal of Public Health (Springer)", "https://link.springer.com/search.rss?facet-journal-id=10389", "INT", 3),
    _f("Zeitschrift für Gerontologie und Geriatrie", "https://link.springer.com/search.rss?facet-journal-id=391", "DE", 3),
    _f("Prävention und Gesundheitsförderung", "https://link.springer.com/search.rss?facet-journal-id=11553", "DE", 3),
    _f("Bundesgesundheitsblatt", "https://link.springer.com/search.rss?facet-journal-id=103", "DE", 3),
    # Policy-Institute
    _f("Commonwealth Fund", "https://www.commonwealthfund.org/rss.xml", "INT", 3),
]

ALL = TIER1 + TIER3


def seed(conn) -> int:
    """Fügt alle Seed-Quellen (Tier 1+3) ein (UNIQUE url → idempotent).
    Gibt Anzahl NEUER Zeilen zurück."""
    n = 0
    for s in ALL:
        cur = conn.execute(
            "INSERT OR IGNORE INTO sources(name,type,url,tier,region,enabled) "
            "VALUES (?,?,?,?,?,1)", (s["name"], s["type"], s["url"], s["tier"], s["region"]))
        n += cur.rowcount
    conn.commit()
    return n


def _wilson(pos: int, n: int, z: float = 1.96) -> float:
    """Untere Grenze des Wilson-Konfidenzintervalls für den Relevanz-Anteil.
    WHY: belohnt ANHALTENDE Relevanz mit Volumen — 200 Artikel @60% schlagen 5 @80%
    (kleine Stichprobe = breite Unsicherheit = niedrige Untergrenze). Genau das
    Verhalten 'stabil relevante Quellen setzen sich langfristig durch'."""
    if n <= 0:
        return 0.0
    phat = pos / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def stats(conn, recent_days: int = 30) -> list[dict]:
    """Dynamische Quellen-Güte: je Quelle Volumen, Relevanz-Yield, Recency und ein
    Wilson-Score. Absteigend nach (score, recent, total) sortiert → Top-Quellen oben,
    Noise im Bodensatz. Auto-Disable findet NICHT statt (soft ranking)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=recent_days)).isoformat()
    rows = conn.execute(
        "SELECT s.id, s.name, s.region, s.tier, s.enabled, s.last_status, s.last_fetched, "
        "  COUNT(a.id) AS total, "
        "  SUM(CASE WHEN a.relevant=1 THEN 1 ELSE 0 END) AS relevant, "
        "  SUM(CASE WHEN a.published >= ? THEN 1 ELSE 0 END) AS recent "
        "FROM sources s LEFT JOIN articles a ON a.source_id = s.id "
        "GROUP BY s.id", (cutoff,)).fetchall()
    out = []
    for r in rows:
        total = r["total"] or 0
        rel = r["relevant"] or 0
        out.append({
            "id": r["id"], "name": r["name"], "region": r["region"], "tier": r["tier"],
            "enabled": r["enabled"], "last_status": r["last_status"],
            "last_fetched": r["last_fetched"], "total": total, "relevant": rel,
            "recent": r["recent"] or 0,
            "ratio": round(rel / total, 3) if total else 0.0,
            "score": round(_wilson(rel, total), 4),
        })
    out.sort(key=lambda x: (x["score"], x["recent"], x["total"]), reverse=True)
    return out


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
