"""Re-runs validate_cleaned() over all already-cleaned DB rows. No LLM calls.

Useful after extending JUNK_RE / hallucination heuristics: re-classifies
quality + clean_notes + nukes junk strings in person fields. The 5 OK qwen
rows were validated under the old rules and may also flip to suspect.
"""

import os
from data_cleaner import db_connect
from dotenv import load_dotenv

from data_cleaner import validate_cleaned
from fix_suspects import normalize_ort

load_dotenv()

DB_KW = dict(
    host=os.getenv("PGHOST"), port=os.getenv("PGPORT"),
    dbname=os.getenv("PGDATABASE"),
    user=os.getenv("PGUSER"), password=os.getenv("PGPASSWORD"),
)


def main() -> None:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT api_id, name, ort, cleaner, quality,
               telefon_clean, email_clean, adresse_clean,
               geschaeftsfuehrung_clean, einrichtungsleitung_clean, clean_notes
        FROM pflegeheime
        WHERE cleaner IS NOT NULL
        """
    )
    rows = cur.fetchall()
    print(f"re-validating {len(rows)} rows…\n")

    transitions: dict[str, int] = {}
    upd = conn.cursor()
    for r in rows:
        cleaned = {
            "telefon": r["telefon_clean"] or "",
            "email": r["email_clean"] or "",
            "adresse": r["adresse_clean"] or "",
            "geschaeftsfuehrung": r["geschaeftsfuehrung_clean"] or "",
            "einrichtungsleitung": r["einrichtungsleitung_clean"] or "",
            "notes": "",  # rebuild notes fresh; old ones may carry stale issues
        }
        cleaned = validate_cleaned(cleaned, normalize_ort(r["ort"] or ""))

        old_q = r["quality"]
        new_q = cleaned["quality"]
        key = f"{old_q} → {new_q}"
        transitions[key] = transitions.get(key, 0) + 1

        upd.execute(
            """
            UPDATE pflegeheime SET
              quality = %s,
              clean_notes = NULLIF(%s, ''),
              geschaeftsfuehrung_clean = %s,
              einrichtungsleitung_clean = %s
            WHERE api_id = %s
            """,
            (
                cleaned["quality"],
                cleaned.get("notes", ""),
                cleaned["geschaeftsfuehrung"] or "No clear Data",
                cleaned["einrichtungsleitung"] or "No clear Data",
                r["api_id"],
            ),
        )

    conn.commit()

    print("transitions:")
    for k, n in sorted(transitions.items()):
        print(f"  {k:<22} {n}")

    upd.execute(
        """
        SELECT cleaner, quality, COUNT(*)
        FROM pflegeheime
        WHERE cleaner IS NOT NULL
        GROUP BY cleaner, quality
        ORDER BY cleaner, quality
        """
    )
    print("\nfinal:")
    for cl, q, n in upd.fetchall():
        print(f"  {cl:<18} {q:<10} {n}")


if __name__ == "__main__":
    main()
