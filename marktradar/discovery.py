"""Autonome Source-Discovery: findet RSS-Feeds auf Domains, die der Newsstrom
bereits zitiert (Artikel-Outlinks), oder auf explizit übergebenen Domains.
Kandidaten landen in Quarantäne (enabled=0, discovered=1) — analog Stupid-Listen-
Philosophie wird NIE ungeprüft auto-enabled; approve_source() re-verifiziert."""
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from marktradar import ingest, stupid


def _domain(url: str) -> str:
    return (urlparse(url).netloc or "").replace("www.", "").lower()


def is_feed_href(href: str) -> bool:
    h = href.lower()
    if "feedback" in h:
        return False
    return ("/feed" in h or "rss" in h or h.endswith(".xml")
            or "atom" in h or "format=feed" in h)


def find_feeds(html: bytes | str, base: str) -> list[str]:
    """Feed-URLs einer Seite: RSS-Autodiscovery (<link rel=alternate>) + Href-Heuristik."""
    soup = BeautifulSoup(html, "lxml")
    feeds = []
    for ln in soup.find_all("link", attrs={"rel": True}):
        rel = " ".join(ln.get("rel") or []).lower()
        typ = (ln.get("type") or "").lower()
        if "alternate" in rel and ("rss" in typ or "atom" in typ) and ln.get("href"):
            feeds.append(urljoin(base, ln["href"]))
    for a in soup.find_all("a", href=True):
        if is_feed_href(a["href"]):
            feeds.append(urljoin(base, a["href"]))
    seen, out = set(), []
    for f in feeds:
        if f not in seen and not ingest.JUNK_FEED.search(f):
            seen.add(f)
            out.append(f)
    return out


def candidate_domains(conn, limit: int = 30) -> list[str]:
    """Domains aus Artikel-Links, die noch keine registrierte Quelle sind —
    meistzitierte zuerst. Quarantäne-Domains (Stupid-Liste) werden nie Kandidat."""
    have = {_domain(r["url"]) for r in conn.execute("SELECT url FROM sources").fetchall()}
    have.update((r["source_domain"] or "").lower() for r in conn.execute(
        "SELECT DISTINCT source_domain FROM articles "
        "WHERE source_domain IS NOT NULL").fetchall())
    counts: dict[str, int] = {}
    for r in conn.execute(
            "SELECT link FROM articles WHERE link IS NOT NULL AND link != ''").fetchall():
        d = _domain(r["link"])
        if d and d not in have and not stupid.is_stupid_domain(d):
            counts[d] = counts.get(d, 0) + 1
    return [d for d, _ in sorted(counts.items(), key=lambda x: -x[1])][:limit]


def discover_sources(conn, seed_domains: list[str] | None = None,
                     limit: int = 10) -> dict:
    """Entdeckt Feeds auf Kandidaten-Domains und registriert sie als
    enabled=0/discovered=1 (Quarantäne). Jeder Feed wird vor dem Insert geparst
    (≥1 Entry). Fehler je Domain isoliert. Gibt {checked, found:[...], errors}."""
    domains = [d.strip().lower().replace("www.", "")
               for d in (seed_domains or candidate_domains(conn, limit))][:limit]
    found, errors = [], []
    for dom in domains:
        if stupid.is_stupid_domain(dom):
            errors.append({"domain": dom, "error": "quarantänisierte Domain (stupid-Liste)"})
            continue
        try:
            html = ingest.fetch(f"https://{dom}/")
            if html is None:
                errors.append({"domain": dom, "error": "Homepage nicht erreichbar"})
                continue
            for feed_url in find_feeds(html, f"https://{dom}/")[:5]:
                try:
                    content = ingest.fetch(feed_url)
                    if not content or not ingest.parse_feed(content):
                        continue
                except Exception:
                    continue
                cur = conn.execute(
                    "INSERT OR IGNORE INTO sources"
                    "(name,type,url,tier,region,enabled,discovered,discovered_from) "
                    "VALUES (?,?,?,?,?,0,1,?)",
                    (dom, "rss", feed_url, 3, "DE", dom))
                if cur.rowcount:
                    found.append({"id": cur.lastrowid, "domain": dom, "url": feed_url})
                break  # erster valider Feed je Domain reicht
        except Exception as e:
            errors.append({"domain": dom, "error": str(e)})
    conn.commit()
    return {"checked": len(domains), "found": found, "errors": errors}


def approve_source(conn, source_id: int) -> dict:
    """Aktiviert eine entdeckte Quelle nach Re-Verifikation (Feed liefert Items)."""
    row = conn.execute("SELECT id, name, url, enabled FROM sources WHERE id=?",
                       (source_id,)).fetchone()
    if row is None:
        return {"error": f"source {source_id} nicht gefunden"}
    try:
        content = ingest.fetch(row["url"])
        ok = bool(content) and len(ingest.parse_feed(content)) > 0
        err = None
    except Exception as e:
        ok, err = False, str(e)
    if not ok:
        status = f"approve fehlgeschlagen: {err or 'kein gültiger Feed'}"
        conn.execute("UPDATE sources SET last_status=? WHERE id=?", (status, source_id))
        conn.commit()
        return {"error": status, "name": row["name"], "url": row["url"]}
    conn.execute("UPDATE sources SET enabled=1, last_status='approved' WHERE id=?",
                 (source_id,))
    conn.commit()
    return {"ok": True, "id": source_id, "name": row["name"], "url": row["url"]}
