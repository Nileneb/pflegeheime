"""Validate cleaned email addresses without sending mail.

This local version does MX-only (residential ISPs block outbound port 25
so SMTP RCPT-probes cannot run from a workstation).

Output column 'email_status':
  mx_ok       — domain has at least one MX record (mail-capable)
  no_mx       — domain has no MX record (definitely broken)
  no_email    — row has no email or local-only placeholder
  malformed   — string in email_clean is not a valid email shape

For a definitive 'mailbox exists?' check, run rcpt-probe-via-ssh on a server
where outbound :25 is open (e.g. mcp.linn.games) — see email_probe_remote.py
(separate script, opt-in).
"""

from __future__ import annotations

import os
import re
from data_cleaner import db_connect
import dns.resolver
import dns.exception
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

ALTER_SQL = "ALTER TABLE pflegeheime ADD COLUMN IF NOT EXISTS email_status TEXT;"

EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")
DNS_TIMEOUT = 5

resolver = dns.resolver.Resolver()
resolver.timeout = DNS_TIMEOUT
resolver.lifetime = DNS_TIMEOUT


def has_mx(domain: str) -> bool:
    try:
        answers = resolver.resolve(domain, "MX")
        return len(list(answers)) > 0
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN,
            dns.exception.DNSException):
        # Some domains accept mail at A-record; check that as fallback.
        try:
            answers = resolver.resolve(domain, "A")
            return len(list(answers)) > 0
        except dns.exception.DNSException:
            return False


def main() -> None:
    conn = db_connect()
    with conn.cursor() as cur:
        cur.execute(ALTER_SQL)
    conn.commit()

    cur = conn.cursor()
    cur.execute(
        """
        SELECT api_id, email_clean
        FROM pflegeheime
        WHERE cleaner IS NOT NULL
        """
    )
    rows = cur.fetchall()

    by_domain: dict[str, list[str]] = defaultdict(list)
    skipped_no_email = []
    skipped_malformed = []
    for r in rows:
        addr = (r["email_clean"] or "").strip()
        if not addr or addr.lower() in {"no clear data", "n/a"}:
            skipped_no_email.append(r["api_id"])
            continue
        if not EMAIL_RE.match(addr):
            skipped_malformed.append(r["api_id"])
            continue
        domain = addr.split("@", 1)[1].lower()
        by_domain[domain].append(r["api_id"])

    print(f"checking {sum(len(v) for v in by_domain.values())} addresses across "
          f"{len(by_domain)} unique domains "
          f"(skip: {len(skipped_no_email)} no-email, "
          f"{len(skipped_malformed)} malformed)\n")

    upd = conn.cursor()
    for api_id in skipped_no_email:
        upd.execute("UPDATE pflegeheime SET email_status=%s WHERE api_id=%s",
                    ("no_email", api_id))
    for api_id in skipped_malformed:
        upd.execute("UPDATE pflegeheime SET email_status=%s WHERE api_id=%s",
                    ("malformed", api_id))
    conn.commit()

    domain_status: dict[str, str] = {}

    def check(domain: str) -> tuple[str, str]:
        return domain, ("mx_ok" if has_mx(domain) else "no_mx")

    completed = 0
    total = len(by_domain)
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = [ex.submit(check, d) for d in by_domain]
        for fut in as_completed(futures):
            domain, status = fut.result()
            domain_status[domain] = status
            completed += 1
            if completed % 100 == 0 or completed == total:
                print(f"  {completed}/{total} domains")

    for domain, api_ids in by_domain.items():
        status = domain_status[domain]
        for api_id in api_ids:
            upd.execute("UPDATE pflegeheime SET email_status=%s WHERE api_id=%s",
                        (status, api_id))
    conn.commit()

    upd.execute(
        """SELECT email_status, COUNT(*) FROM pflegeheime
           WHERE cleaner IS NOT NULL AND email_status IS NOT NULL
           GROUP BY email_status ORDER BY COUNT(*) DESC"""
    )
    print("\nEmail-Status (lokaler MX-Check):")
    for status, n in upd.fetchall():
        print(f"  {status:<12} {n:5}")

    upd.execute(
        """SELECT DISTINCT split_part(email_clean,'@',2) AS domain
           FROM pflegeheime
           WHERE cleaner IS NOT NULL AND email_status='no_mx'
           ORDER BY domain LIMIT 20"""
    )
    rows = upd.fetchall()
    if rows:
        print(f"\nDomains ohne MX (Sample, max 20 von vermutlich mehr):")
        for (d,) in rows:
            print(f"  {d}")


if __name__ == "__main__":
    main()
