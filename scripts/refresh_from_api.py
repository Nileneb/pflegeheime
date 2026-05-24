#!/usr/bin/env python3
"""Phase 1 — Authoritative refresh from the NRW Heimfinder *detail* endpoint.

The list endpoint only gives name/ort/kreis/geo. The per-facility detail
endpoint  …/api/heimfinder/einrichtung/{id}  returns the OFFICIAL
telefon/fax/email/web/ansprechpartner + full street address. That is open
government data — no hallucination. We store it in dedicated *_api columns so
the 39 manual fixes and existing cleaned data stay untouched; Phase 3 composes
the final fields with API-first precedence.

Resumable: rows already fetched (detail_fetched_at NOT NULL) are skipped unless
--force. Gentle concurrency (default 6 threads), per-request retry.

Usage:
  python scripts/refresh_from_api.py            # fetch all missing
  python scripts/refresh_from_api.py --force    # re-fetch everything
  python scripts/refresh_from_api.py --workers 8
"""
import argparse
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_cleaner import db_connect

DETAIL_URL = "https://pfadwtg.mags.nrw/api/heimfinder/einrichtung/{id}"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
           "Accept-Language": "de-DE,de;q=0.9"}

ADD_COLS = """
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS strasse_api         TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS plz_api             TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS ort_api             TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS telefon_api         TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS fax_api             TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS email_api           TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS web_api             TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS ansprechpartner_api TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS typ_api             TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS nrw_key             TEXT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS detail_http_status  INT;
ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS detail_fetched_at   TIMESTAMPTZ;
"""

_local = threading.local()


def session() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        _local.s = s
    return s


def norm(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def fetch_detail(api_id: str) -> dict:
    """Return parsed fields + http status. Retries twice on transient error."""
    url = DETAIL_URL.format(id=api_id)
    last_exc = None
    for attempt in range(3):
        try:
            r = session().get(url, timeout=20)
            status = r.status_code
            if status != 200:
                return {"_status": status}
            j = r.json()
            rec = j.get(str(api_id)) or (next(iter(j.values())) if j else {})
            e = rec.get("einrichtung", {}) or {}
            a = rec.get("adresse", {}) or {}
            web = norm(e.get("web"))
            if web and not web.startswith(("http://", "https://")):
                web = "https://" + web
            return {
                "_status": 200,
                "strasse_api": norm(a.get("strasse")),
                "plz_api": norm(a.get("plz")),
                "ort_api": norm(a.get("ort")),
                "telefon_api": norm(e.get("telefon")),
                "fax_api": norm(e.get("fax")),
                "email_api": norm(e.get("email")),
                "web_api": web,
                "ansprechpartner_api": norm(e.get("ansprechpartner")),
                "typ_api": norm(e.get("typ")),
                "nrw_key": norm(e.get("nrw_key")),
            }
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(0.5 * (attempt + 1))
    return {"_status": -1, "_err": str(last_exc)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-fetch already-fetched rows")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    conn = db_connect()
    with conn.cursor() as cur:
        cur.execute(ADD_COLS)
    conn.commit()

    with conn.cursor() as cur:
        if args.force:
            cur.execute("SELECT api_id FROM pflegeheime ORDER BY api_id")
        else:
            cur.execute("SELECT api_id FROM pflegeheime WHERE detail_fetched_at IS NULL ORDER BY api_id")
        ids = [r[0] for r in cur.fetchall()]
    print(f"Zu holen: {len(ids)} Detail-Datensätze (workers={args.workers})")

    UPDATE = """
        UPDATE pflegeheime SET
            strasse_api=%(strasse_api)s, plz_api=%(plz_api)s, ort_api=%(ort_api)s,
            telefon_api=%(telefon_api)s, fax_api=%(fax_api)s, email_api=%(email_api)s,
            web_api=%(web_api)s, ansprechpartner_api=%(ansprechpartner_api)s,
            typ_api=%(typ_api)s, nrw_key=%(nrw_key)s,
            detail_http_status=%(_status)s, detail_fetched_at=NOW()
        WHERE api_id=%(api_id)s
    """
    done = ok = miss = err = 0
    write_lock = threading.Lock()
    t0 = time.time()

    def work(api_id):
        d = fetch_detail(api_id)
        d["api_id"] = api_id
        for k in ("strasse_api", "plz_api", "ort_api", "telefon_api", "fax_api",
                  "email_api", "web_api", "ansprechpartner_api", "typ_api", "nrw_key"):
            d.setdefault(k, None)
        return d

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(work, i) for i in ids]
        for fut in as_completed(futures):
            d = fut.result()
            with write_lock:
                with conn.cursor() as cur:
                    cur.execute(UPDATE, d)
                conn.commit()
                done += 1
                if d["_status"] == 200:
                    ok += 1
                elif d["_status"] == 404:
                    miss += 1
                else:
                    err += 1
                if done % 100 == 0 or done == len(ids):
                    rate = done / max(time.time() - t0, 1e-6)
                    print(f"  {done}/{len(ids)}  ok={ok} 404={miss} err={err}  ({rate:.1f}/s)")

    conn.close()
    print(f"\nFertig. ok={ok}  404={miss}  err={err}  von {len(ids)}")


if __name__ == "__main__":
    main()
