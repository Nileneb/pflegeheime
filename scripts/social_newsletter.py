#!/usr/bin/env python3
"""Phase 2e — Social-Media-Profile + Newsletter pro Domain.

Social-Links und Newsletter-Anmeldungen sind (wie die GF) TRÄGER-Eigenschaften:
sie stehen site-weit im Footer/Header. Der GF-Crawler hat den Footer
WEGGESCHNITTEN (page_text() entfernt nav/header/footer), darum lässt sich das
NICHT aus domain_impressum.impressum_text gewinnen — wir holen die Startseite
frisch und parsen ALLE <a href>.

Pro Plattform wird nur die echte PROFIL-URL behalten; Share-/Intent-/Plugin-Links
(facebook.com/sharer, twitter.com/intent, …) werden verworfen. Newsletter wird
über (a) Link-Text/href "newsletter", (b) bekannte ESP-Domains (Mailchimp,
CleverReach, Brevo …) und (c) ein E-Mail-Formular im Newsletter-Kontext erkannt.

Bounded: ≤2 HTTP-Requests/Domain. Resumable über domain_social.fetched_at.
Mit  python -u  starten (unbuffered).

Usage:
  python -u scripts/social_newsletter.py --workers 12
  python -u scripts/social_newsletter.py --limit 30
  python -u scripts/social_newsletter.py --refetch-failed
"""
import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept-Language": "de-DE,de;q=0.9"}
FETCH_TIMEOUT = int(os.getenv("FETCH_TIMEOUT", "8"))

PLATFORMS = ["facebook", "instagram", "linkedin", "youtube",
             "twitter", "tiktok", "xing", "pinterest", "whatsapp"]

DDL = """
CREATE TABLE IF NOT EXISTS domain_social (
    domain             TEXT PRIMARY KEY,
    source_url         TEXT,
    facebook           TEXT,
    instagram          TEXT,
    linkedin           TEXT,
    youtube            TEXT,
    twitter            TEXT,
    tiktok             TEXT,
    xing               TEXT,
    pinterest          TEXT,
    whatsapp           TEXT,
    newsletter_url     TEXT,
    newsletter_provider TEXT,
    has_newsletter     INTEGER DEFAULT 0,
    status             TEXT,
    fetched_at         TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_local = threading.local()


def sess():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session(); s.headers.update(HEADERS)
        _local.s = s
    return s


def fetch(url):
    try:
        r = sess().get(url, timeout=FETCH_TIMEOUT, allow_redirects=True, verify=False)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
            return r.text, r.url
    except Exception:
        pass
    return None, None


# ---------- Social-Link-Klassifikation ----------------------------------------
# Generische Reject-Tokens: Share/Intent/Plugin/Tracking — KEINE Profile.
_REJECT = (
    "sharer", "share.php", "/share?", "/share/", "sharearticle", "/intent/",
    "/dialog/", "/plugins/", "/tr?", "/widgets/", "addthis", "addtoany",
    "/oauth", "/login", "/signup", "/help", "/about", "/privacy", "/policy",
    "/terms", "/tos", "u=http", "?text=", "&text=", "service=",
)


def _reject(url_l: str) -> bool:
    return any(tok in url_l for tok in _REJECT)


def _seg1(path: str):
    parts = [p for p in path.split("/") if p]
    return parts[0] if parts else ""


def classify_social(href: str):
    """Return (platform, normalized_profile_url) or (None, None)."""
    if not href:
        return None, None
    u = href.strip()
    ul = u.lower()
    if ul.startswith(("mailto:", "tel:", "javascript:", "#")):
        return None, None
    # wa.me / whatsapp – nur echter Kontakt (Nummer), KEIN Share-Link (text=)
    if "wa.me/" in ul:
        tail = ul.split("wa.me/", 1)[1].split("?")[0].split("/")[0]
        if re.fullmatch(r"\+?\d{6,}", tail):
            return "whatsapp", "https://wa.me/" + tail.lstrip("+")
        return None, None
    if "api.whatsapp.com/send" in ul:
        m = re.search(r"phone=(\+?\d{6,})", ul)
        return ("whatsapp", "https://wa.me/" + m.group(1).lstrip("+")) if m else (None, None)
    if not ul.startswith("http"):
        return None, None
    try:
        p = urlparse(u)
    except Exception:
        return None, None
    host = p.netloc.lower().replace("www.", "").replace("m.", "").replace("de.", "")
    path = p.path or "/"
    seg = _seg1(path).lower()
    full_l = (host + path + ("?" + p.query if p.query else "")).lower()

    def clean(keep_query=False):
        netloc = p.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        base = f"https://{netloc}{p.path.rstrip('/')}"
        if keep_query and p.query:
            base += "?" + p.query
        return base

    if host in ("facebook.com", "fb.com", "fb.me"):
        if seg == "profile.php" and "id=" in (p.query or ""):
            return "facebook", clean(keep_query=True)
        if _reject(full_l) or not seg or seg in (
                "sharer", "dialog", "plugins", "tr", "profile.php",
                "search", "watch", "events", "marketplace", "gaming", "login"):
            return None, None
        return "facebook", clean()
    if host == "instagram.com":
        if _reject(full_l) or seg in ("", "p", "explore", "reel", "reels", "accounts", "stories", "direct"):
            return None, None
        return "instagram", clean()
    if host == "linkedin.com":
        if any(k in path.lower() for k in ("/company/", "/in/", "/school/", "/showcase/")) and not _reject(full_l):
            return "linkedin", clean()
        return None, None
    if host in ("youtube.com", "youtu.be"):
        if host == "youtu.be":
            return None, None
        if seg in ("channel", "c", "user") or seg.startswith("@"):
            return "youtube", clean()
        return None, None
    if host in ("twitter.com", "x.com"):
        if _reject(full_l) or seg in ("", "intent", "share", "home", "hashtag", "search", "i", "explore", "messages", "privacy", "tos", "settings"):
            return None, None
        return "twitter", clean()
    if host == "tiktok.com":
        if seg.startswith("@"):
            return "tiktok", clean()
        return None, None
    if host == "xing.com":
        if any(k in path.lower() for k in ("/profile/", "/pages/", "/companies/")) and not _reject(full_l):
            return "xing", clean()
        return None, None
    if host.startswith("pinterest."):
        if _reject(full_l) or seg in ("", "pin", "search"):
            return None, None
        return "pinterest", clean()
    return None, None


# ---------- Newsletter-Erkennung -----------------------------------------------
ESP = {
    "list-manage.com": "Mailchimp", "mailchi.mp": "Mailchimp",
    "cleverreach.com": "CleverReach", "crm-cleverreach": "CleverReach",
    "sibforms.com": "Brevo", "sendinblue.com": "Brevo", "brevo.com": "Brevo",
    "rapidmail.de": "rapidmail", "rapidmail.com": "rapidmail",
    "mailerlite.com": "MailerLite", "ml-attach": "MailerLite",
    "getresponse.com": "GetResponse", "hsforms.com": "HubSpot",
    "hubspotusercontent": "HubSpot", "convertkit.com": "ConvertKit",
    "kit.com": "ConvertKit", "newsletter2go": "Newsletter2Go",
    "inxmail.de": "Inxmail", "mailingwork.de": "mailingwork",
    "klick-tipp.com": "KlickTipp", "quentn.com": "Quentn",
}


def detect_newsletter(soup: BeautifulSoup, base: str):
    """Return (newsletter_url|None, provider|None, has_newsletter_bool)."""
    nl_url = None
    provider = None
    # 1) ESP-Domain in irgendeinem href oder Formular-Action
    candidates = []
    for a in soup.find_all("a", href=True):
        candidates.append(a["href"])
    for fo in soup.find_all("form", action=True):
        candidates.append(fo["action"])
    for href in candidates:
        hl = href.lower()
        for key, name in ESP.items():
            if key in hl:
                provider = name
                if not nl_url and href.lower().startswith("http"):
                    nl_url = href
                break
    # 2) Link mit "newsletter" in href/Text
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        txt = (a.get_text() or "").lower()
        if "newsletter" in href.lower() or "newsletter" in txt:
            if href.lower().startswith(("mailto:", "javascript:", "#")):
                continue
            nl_url = urljoin(base, href)
            break
    # 3) E-Mail-Formular im Newsletter-Kontext
    has = bool(nl_url or provider)
    if not has:
        for fo in soup.find_all("form"):
            has_email = fo.find("input", attrs={"type": "email"}) is not None
            ctx = (fo.get_text(" ", strip=True) or "").lower()
            ctx += " " + " ".join(fo.get("class") or []) + " " + (fo.get("id") or "")
            if has_email and ("newsletter" in ctx or "anmeld" in ctx and "newsletter" in ctx):
                has = True
                break
    return nl_url, provider, bool(has or nl_url or provider)


def best(url_list):
    """Pick the cleanest profile URL: fewest path segments, then shortest."""
    if not url_list:
        return None
    return sorted(set(url_list), key=lambda u: (urlparse(u).path.count("/"), len(u)))[0]


def scrape(soup: BeautifulSoup, base: str):
    found = {p: [] for p in PLATFORMS}
    for a in soup.find_all("a", href=True):
        plat, prof = classify_social(a["href"])
        if plat:
            found[plat].append(prof)
    res = {p: best(found[p]) for p in PLATFORMS}
    nl_url, provider, has = detect_newsletter(soup, base)
    res["newsletter_url"] = nl_url
    res["newsletter_provider"] = provider
    res["has_newsletter"] = has
    return res


def url_variants(sample_url):
    p = urlparse(sample_url)
    netloc = p.netloc
    bare = netloc[4:] if netloc.startswith("www.") else netloc
    www = netloc if netloc.startswith("www.") else "www." + netloc
    out, seen = [], set()
    for scheme in ("https", "http"):
        for nl in (netloc, www, bare):
            u = f"{scheme}://{nl}/"
            if u not in seen:
                seen.add(u); out.append(u)
    if sample_url not in seen:
        out.append(sample_url)
    return out


def process(domain, sample_url):
    html = final = None
    for cand in url_variants(sample_url):
        html, final = fetch(cand)
        if html:
            break
    root = url_variants(sample_url)[0]
    if not html:
        out = {p: None for p in PLATFORMS}
        out.update(newsletter_url=None, newsletter_provider=None, has_newsletter=False,
                   status="fetch_failed", source_url=root, domain=domain)
        return out
    soup = BeautifulSoup(html, "lxml")
    res = scrape(soup, final or root)
    n_social = sum(1 for p in PLATFORMS if res.get(p))
    res["status"] = "ok" if (n_social or res["has_newsletter"]) else "no_social"
    res["source_url"] = final or root
    res["domain"] = domain
    return res


def regdom(u):
    try:
        return urlparse(u).netloc.lower().replace("www.", "")
    except Exception:
        return None


UPSERT = """
INSERT INTO domain_social
  (domain, source_url, facebook, instagram, linkedin, youtube, twitter, tiktok,
   xing, pinterest, whatsapp, newsletter_url, newsletter_provider, has_newsletter,
   status, fetched_at)
VALUES (%(domain)s,%(source_url)s,%(facebook)s,%(instagram)s,%(linkedin)s,
        %(youtube)s,%(twitter)s,%(tiktok)s,%(xing)s,%(pinterest)s,%(whatsapp)s,
        %(newsletter_url)s,%(newsletter_provider)s,%(has_newsletter)s,%(status)s,NOW())
ON CONFLICT (domain) DO UPDATE SET
  source_url=EXCLUDED.source_url, facebook=EXCLUDED.facebook,
  instagram=EXCLUDED.instagram, linkedin=EXCLUDED.linkedin,
  youtube=EXCLUDED.youtube, twitter=EXCLUDED.twitter, tiktok=EXCLUDED.tiktok,
  xing=EXCLUDED.xing, pinterest=EXCLUDED.pinterest, whatsapp=EXCLUDED.whatsapp,
  newsletter_url=EXCLUDED.newsletter_url,
  newsletter_provider=EXCLUDED.newsletter_provider,
  has_newsletter=EXCLUDED.has_newsletter, status=EXCLUDED.status, fetched_at=NOW()
"""


def main():
    import urllib3
    urllib3.disable_warnings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--refetch-failed", action="store_true")
    args = ap.parse_args()

    conn = db_connect()
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT web_api FROM pflegeheime WHERE web_api IS NOT NULL AND web_api<>''")
        urls = [r[0] for r in cur.fetchall()]
    dom_sample = {}
    for u in urls:
        d = regdom(u)
        if not d:
            continue
        if d not in dom_sample or len(urlparse(u).path) > len(urlparse(dom_sample[d]).path):
            dom_sample[d] = u

    with conn.cursor() as cur:
        if args.refetch_failed:
            cur.execute("SELECT domain FROM domain_social WHERE status='ok' OR status='no_social'")
        else:
            cur.execute("SELECT domain FROM domain_social WHERE status IS NOT NULL")
        done = {r[0] for r in cur.fetchall()}
    todo = [(d, s) for d, s in sorted(dom_sample.items()) if d not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"Domains gesamt: {len(dom_sample)} | erledigt: {len(done)} | jetzt: {len(todo)}", flush=True)

    lock = threading.Lock()
    stats = {}
    done_n = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process, d, s): d for d, s in todo}
        for fut in as_completed(futs):
            d = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = {p: None for p in PLATFORMS}
                res.update(newsletter_url=None, newsletter_provider=None,
                           has_newsletter=False, status=f"error:{str(exc)[:25]}",
                           source_url=dom_sample.get(d), domain=d)
            with lock:
                with conn.cursor() as cur:
                    cur.execute(UPSERT, res)
                conn.commit()
                done_n += 1
                st = res["status"]
                stats[st] = stats.get(st, 0) + 1
                if st == "ok":
                    chans = [p for p in PLATFORMS if res.get(p)]
                    nl = " +Newsletter" if res["has_newsletter"] else ""
                    print(f"  ✓ {d:30} {','.join(chans)}{nl}", flush=True)
                if done_n % 50 == 0 or done_n == len(todo):
                    rate = done_n / max(time.time() - t0, 1e-6)
                    print(f"  --- {done_n}/{len(todo)} ({rate:.1f} dom/s) {stats} ---", flush=True)
    conn.close()
    print(f"\nFertig. {stats}", flush=True)


if __name__ == "__main__":
    main()
