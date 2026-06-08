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
from data_cleaner import db_connect
import re
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
    rows = upd.fetchall()
    if rows:
        print(f"\nDomains ohne MX (Sample, max 20 von vermutlich mehr):")
        for (d,) in rows:
            print(f"  {d}")


if __name__ == "__main__":
    main()
