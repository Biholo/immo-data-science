"""
Seed rent_control (encadrement des loyers) and rental_permit_required (permis de
louer) into the cities table from hand-curated, source-cited CSVs.

Sources: see comments at the top of each CSV file for exact source URLs and
as-of dates for every commune listed. In short:
  - csv/reglementation-locative/rent-control.csv
    ~70 communes across 9 territories (Paris, Lille agglo, Plaine Commune,
    Lyon/Villeurbanne, Est Ensemble, Montpellier, Bordeaux, Pays Basque,
    Grenoble-Alpes Metropole) sourced from ANIL / legifrance.gouv.fr /
    ecologie.gouv.fr and cross-checked press reporting for Grenoble.
  - csv/reglementation-locative/rental-permit.csv
    26 communes across only 2 departements (Loiret, Tarn) whose prefectures
    publish a readable list. THIS IS A PARTIAL, REGIONAL DELIVERABLE, not a
    national one -- no official national "permis de louer" list exists (the
    decision is made commune-by-commune / EPCI-by-EPCI). See CSV header for
    other structured-but-unparsed sources found (Herault, Pas-de-Calais,
    Aix-Marseille-Provence shapefiles; Seine-Saint-Denis interactive map).

Both CSVs only ever list communes where the flag is TRUE (opt-in disclosure
lists) -- there is no "FALSE" row. Communes not present in a CSV are simply
left untouched in the DB (NULL stays NULL); this script never writes FALSE.

Run:
  python -m pipeline.scripts.seed_rent_regulation [--rent-control-csv path]
      [--rental-permit-csv path] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

BASE_CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "reglementation-locative"
DEFAULT_RENT_CONTROL_CSV = str(BASE_CSV_DIR / "rent-control.csv")
DEFAULT_RENTAL_PERMIT_CSV = str(BASE_CSV_DIR / "rental-permit.csv")

REQUIRED_COLUMNS = ["rent_control", "rental_permit_required"]


def load_flagged_communes(csv_path: str, flag_column: str) -> set[str]:
    """Reads a curated CSV and returns the set of INSEE codes flagged TRUE.

    Skips comment lines (starting with '#') and blank lines before the header.
    """
    codes: set[str] = set()
    with open(csv_path, encoding="utf-8-sig") as f:
        lines = [line for line in f if not line.lstrip().startswith("#") and line.strip()]
    reader = csv.DictReader(lines)
    if flag_column not in (reader.fieldnames or []):
        print(f"  WARNING: column '{flag_column}' not found in {csv_path} (found {reader.fieldnames})")
        return codes
    for row in reader:
        code = row["insee_code"].strip().zfill(5)
        flag = row[flag_column].strip().upper()
        if flag == "TRUE":
            codes.add(code)
    return codes


def check_columns_exist(cur) -> None:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name='cities' AND column_name = ANY(%s)",
        (REQUIRED_COLUMNS,),
    )
    found = {r[0] for r in cur.fetchall()}
    missing = [c for c in REQUIRED_COLUMNS if c not in found]
    if missing:
        print(f"  NOTE: DB columns missing on cities table: {missing}")
        print("  (continuing anyway -- schema owner needs to add them before a real run can UPDATE)")
    else:
        print(f"  OK: columns present: {sorted(found)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rent-control-csv", default=DEFAULT_RENT_CONTROL_CSV)
    parser.add_argument("--rental-permit-csv", default=DEFAULT_RENTAL_PERMIT_CSV)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    print("Checking cities.rent_control / cities.rental_permit_required exist ...")
    check_columns_exist(cur)

    print(f"\nLoading {args.rent_control_csv} ...")
    rent_control_codes = load_flagged_communes(args.rent_control_csv, "rent_control")
    print(f"  {len(rent_control_codes)} communes flagged rent_control=TRUE")

    print(f"\nLoading {args.rental_permit_csv} ...")
    rental_permit_codes = load_flagged_communes(args.rental_permit_csv, "rental_permit_required")
    print(f"  {len(rental_permit_codes)} communes flagged rental_permit_required=TRUE")

    all_codes = list(rent_control_codes | rental_permit_codes)
    cur.execute("SELECT insee_code, id FROM cities WHERE insee_code = ANY(%s)", (all_codes,))
    id_map = {row[0]: row[1] for row in cur.fetchall()}

    rent_control_rows = [
        (True, id_map[code]) for code in rent_control_codes if code in id_map
    ]
    rental_permit_rows = [
        (True, id_map[code]) for code in rental_permit_codes if code in id_map
    ]

    print(f"\n  rent_control:           {len(rent_control_rows)} / {len(rent_control_codes)} communes matched in DB")
    unmatched_rc = sorted(rent_control_codes - id_map.keys())
    if unmatched_rc:
        print(f"    unmatched insee_codes: {unmatched_rc}")

    print(f"  rental_permit_required: {len(rental_permit_rows)} / {len(rental_permit_codes)} communes matched in DB")
    unmatched_rp = sorted(rental_permit_codes - id_map.keys())
    if unmatched_rp:
        print(f"    unmatched insee_codes: {unmatched_rp}")

    if args.dry_run:
        print(f"\n[DRY RUN] Would UPDATE rent_control=true for {len(rent_control_rows)} cities")
        print(f"[DRY RUN] Would UPDATE rental_permit_required=true for {len(rental_permit_rows)} cities")
        cur.close()
        conn.close()
        return

    if rent_control_rows:
        psycopg2.extras.execute_values(
            cur,
            """
            UPDATE cities SET
                rent_control = data.flag,
                updated_at   = NOW()
            FROM (VALUES %s) AS data(flag, id)
            WHERE cities.id = data.id
            """,
            rent_control_rows,
            template="(%s, %s)",
        )
    if rental_permit_rows:
        psycopg2.extras.execute_values(
            cur,
            """
            UPDATE cities SET
                rental_permit_required = data.flag,
                updated_at             = NOW()
            FROM (VALUES %s) AS data(flag, id)
            WHERE cities.id = data.id
            """,
            rental_permit_rows,
            template="(%s, %s)",
        )
    conn.commit()
    print(
        f"\nDone. {len(rent_control_rows)} cities updated with rent_control=true, "
        f"{len(rental_permit_rows)} cities updated with rental_permit_required=true."
    )
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
