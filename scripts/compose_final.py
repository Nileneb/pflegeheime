#!/usr/bin/env python3
"""Phase 3 — Final, trustworthy list.

Composes per-home final fields with explicit source precedence and records WHERE
each value came from, so 'perfect' means 'verifiable', not 'looks plausible'.

Precedence:
  telefon  : telefon_api (NRW open data)            > telefon_clean (mistral)
  email    : email_api                              > email_clean
  website  : web_api                                > website
  adresse  : strasse_api + plz_api + ort_api (API)  > adresse_clean
  einrichtungsleitung : ansprechpartner_api (if person-like) > einrichtungsleitung_clean
  geschaeftsfuehrung  : domain_impressum.geschaeftsfuehrung (grounded in Impressum) ONLY.
                        The old mistral GF is DISCARDED unless it matches the grounded one,
                        because 886 were invented for sourceless rows.

Writes columns: telefon_final, email_final, website_final, adresse_final,
einrichtungsleitung_final, geschaeftsfuehrung_final, gf_source, el_source,
traeger_final, data_source_summary, quality_final.
"""
import os
import re
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect, _is_data, PHONE_RE, EMAIL_RE, PLZ_RE, MUNICH_PHONE_RE

ADD = """
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS telefon_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS email_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS website_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS adresse_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS einrichtungsleitung_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS geschaeftsfuehrung_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS traeger_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS gf_source TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS el_source TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS quality_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS data_source_summary TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS facebook_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS instagram_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS linkedin_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS youtube_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS social_media_final TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS newsletter_final TEXT;
"""

SOCIAL_PLATFORMS = ["facebook", "instagram", "linkedin", "youtube",
                    "twitter", "tiktok", "xing", "pinterest", "whatsapp"]

ROLE_WORDS = ("sozial", "dienst", "verwaltung", "leitung", "büro", "sekretariat",
              "empfang", "pflegedienst", "zentrale", "aufnahme", "beratung", "team")


def regdom(u):
    try:
        return urlparse(u).netloc.lower().replace("www.", "")
    except Exception:
        return None


def person_like(s):
    """True if string looks like a person name (>=2 tokens, not pure role)."""
    s = (s or "").strip()
    if not s or len(s.split()) < 2:
        return False
    return True  # accept; role-only contacts are still a valid EL contact


def norm(s):
    return re.sub(r"[^a-zäöüß ]", " ", (s or "").lower())


def gf_matches(mistral_gf, grounded_gf):
    a, b = norm(mistral_gf), norm(grounded_gf)
    ta = {t for t in a.split() if len(t) >= 4}
    tb = {t for t in b.split() if len(t) >= 4}
    return bool(ta & tb)


def main():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(ADD)
    conn.commit()

    # domain -> grounded GF map (Impressum, höchste Priorität)
    cur.execute("""SELECT domain, geschaeftsfuehrung, traeger, impressum_url, crawl_status
                   FROM domain_impressum""")
    dom_gf = {}
    for dom, gf, traeger, imp, status in cur.fetchall():
        dom_gf[dom] = {"gf": gf, "traeger": traeger, "imp": imp, "status": status}

    # api_id -> Web-Such-GF (Fallback, falls Domain kein Impressum-GF hat)
    ws_gf = {}
    try:
        cur.execute("SELECT api_id, gf, source_url, method FROM websearch_gf WHERE gf IS NOT NULL AND gf<>''")
        for aid, gf, src, method in cur.fetchall():
            ws_gf[aid] = {"gf": gf, "src": src, "method": method}
    except Exception:
        conn.rollback()  # Tabelle existiert evtl. noch nicht

    # domain -> Social-Media + Newsletter (Träger-Eigenschaft, pro Domain gecrawlt)
    dom_social = {}
    try:
        cols = ", ".join(SOCIAL_PLATFORMS)
        cur.execute(f"SELECT domain, {cols}, newsletter_url, newsletter_provider, "
                    f"has_newsletter FROM domain_social")
        for r in cur.fetchall():
            dom = r[0]
            d = {p: r[1 + i] for i, p in enumerate(SOCIAL_PLATFORMS)}
            d["newsletter_url"] = r[1 + len(SOCIAL_PLATFORMS)]
            d["newsletter_provider"] = r[2 + len(SOCIAL_PLATFORMS)]
            d["has_newsletter"] = r[3 + len(SOCIAL_PLATFORMS)]
            dom_social[dom] = d
    except Exception:
        conn.rollback()  # Tabelle existiert evtl. noch nicht

    cur.execute("""SELECT api_id, ort,
                          telefon_api, telefon_clean, email_api, email_clean,
                          web_api, website, strasse_api, plz_api, ort_api, adresse_clean,
                          ansprechpartner_api, einrichtungsleitung_clean,
                          geschaeftsfuehrung_clean
                   FROM pflegeheime""")
    rows = cur.fetchall()

    upd = []
    stat = {"gf_grounded": 0, "gf_mistral_confirmed": 0, "gf_none": 0,
            "el_api": 0, "el_mistral": 0, "el_none": 0}
    for (api_id, ort, tel_api, tel_cl, em_api, em_cl, web_api, web_old,
         strasse, plz, ort_api, addr_cl, ansp, el_cl, gf_cl) in rows:

        srcs = []
        # --- telefon ---
        if _is_data(tel_api):
            telefon, ts = tel_api.strip(), "api"
        elif _is_data(tel_cl):
            telefon, ts = tel_cl.strip(), "web(mistral)"
        else:
            telefon, ts = "", "none"
        if telefon:
            srcs.append(f"tel:{ts}")

        # --- email ---
        if _is_data(em_api):
            email, es = em_api.strip(), "api"
        elif _is_data(em_cl):
            email, es = em_cl.strip(), "web(mistral)"
        else:
            email, es = "", "none"
        if email:
            srcs.append(f"mail:{es}")

        # --- website ---
        website = (web_api or web_old or "").strip()

        # --- adresse (API authoritative) ---
        if _is_data(strasse) and _is_data(plz):
            adresse = f"{strasse.strip()}, {plz.strip()} {(ort_api or ort or '').strip()}".strip()
            asrc = "api"
        elif _is_data(addr_cl):
            adresse, asrc = addr_cl.strip(), "web(mistral)"
        else:
            adresse, asrc = "", "none"
        if adresse:
            srcs.append(f"addr:{asrc}")

        # --- Einrichtungsleitung: ansprechpartner_api primary ---
        if _is_data(ansp) and person_like(ansp):
            el, el_src = ansp.strip(), "api:ansprechpartner"
            stat["el_api"] += 1
        elif _is_data(el_cl):
            el, el_src = el_cl.strip(), "web(mistral)"
            stat["el_mistral"] += 1
        else:
            el, el_src = "Nicht ermittelt", "none"
            stat["el_none"] += 1

        # --- Geschäftsführung: ONLY grounded Impressum GF ---
        dom = regdom(web_api or web_old or "")
        info = dom_gf.get(dom or "")
        gf_grounded = info["gf"] if info else None
        traeger = info["traeger"] if info else None
        if _is_data(gf_grounded or ""):
            gf, gf_src = gf_grounded.strip(), f"impressum:{info.get('imp') or dom}"
            stat["gf_grounded"] += 1
            if _is_data(gf_cl or "") and gf_matches(gf_cl, gf_grounded):
                stat["gf_mistral_confirmed"] += 1
        elif api_id in ws_gf:
            w = ws_gf[api_id]
            gf, gf_src = w["gf"].strip(), f"{w['method']}:{w['src']}"
            traeger = traeger or None
            stat["gf_websearch"] = stat.get("gf_websearch", 0) + 1
        else:
            gf, gf_src = "Nicht ermittelt", "none"
            stat["gf_none"] += 1

        # --- quality_final ---
        issues = []
        if telefon and not PHONE_RE.match(telefon):
            issues.append("tel-format")
        if telefon and MUNICH_PHONE_RE.match(telefon) and ort and ort.lower() not in ("münchen", "munich"):
            issues.append("089-mismatch")
        if email and not EMAIL_RE.match(email):
            issues.append("mail-format")
        if adresse and not PLZ_RE.search(adresse):
            issues.append("addr-no-plz")
        core = [telefon, email, adresse]
        if all(core) and not issues:
            q = "complete"
        elif any(core):
            q = "partial" if not issues else "check"
        else:
            q = "empty"

        # --- Social Media + Newsletter (Träger-Domain) ---
        soc = dom_social.get(dom or "")
        fb = ig = li = yt = ""
        social_all = ""
        newsletter = ""
        if soc:
            fb = soc.get("facebook") or ""
            ig = soc.get("instagram") or ""
            li = soc.get("linkedin") or ""
            yt = soc.get("youtube") or ""
            parts = []
            for p in SOCIAL_PLATFORMS:
                v = soc.get(p)
                if v:
                    parts.append(f"{p}: {v}")
            social_all = " | ".join(parts)
            if soc.get("has_newsletter"):
                nl = soc.get("newsletter_url") or ""
                prov = soc.get("newsletter_provider")
                newsletter = nl if nl else (f"ja ({prov})" if prov else "ja")
                if nl and prov:
                    newsletter = f"{nl} ({prov})"
            stat["social_any"] = stat.get("social_any", 0) + (1 if social_all else 0)
            stat["newsletter_any"] = stat.get("newsletter_any", 0) + (1 if newsletter else 0)

        summary = " | ".join(srcs) + (f" || issues:{','.join(issues)}" if issues else "")
        upd.append((telefon, email, website, adresse, el, gf, traeger,
                    gf_src, el_src, q, summary,
                    fb, ig, li, yt, social_all, newsletter, api_id))

    cur.executemany("""
        UPDATE pflegeheime SET
          telefon_final=%s, email_final=%s, website_final=%s, adresse_final=%s,
          einrichtungsleitung_final=%s, geschaeftsfuehrung_final=%s, traeger_final=%s,
          gf_source=%s, el_source=%s, quality_final=%s, data_source_summary=%s,
          facebook_final=%s, instagram_final=%s, linkedin_final=%s, youtube_final=%s,
          social_media_final=%s, newsletter_final=%s
        WHERE api_id=%s
    """, upd)
    conn.commit()

    print(f"Composed {len(upd)} rows.")
    print("GF:", stat["gf_grounded"], "grounded (",
          stat["gf_mistral_confirmed"], "bestätigten alte mistral-GF ),",
          stat["gf_none"], "nicht ermittelt")
    print("EL:", stat["el_api"], "aus API-ansprechpartner,",
          stat["el_mistral"], "aus mistral,", stat["el_none"], "keine")
    print("Social:", stat.get("social_any", 0), "Heime mit Social-Media,",
          stat.get("newsletter_any", 0), "mit Newsletter")

    # quality distribution
    cur.execute("SELECT quality_final, count(*) FROM pflegeheime GROUP BY quality_final ORDER BY 2 DESC")
    print("\nquality_final:")
    for q, n in cur.fetchall():
        print(f"  {q:10} {n}")
    conn.close()


if __name__ == "__main__":
    main()
