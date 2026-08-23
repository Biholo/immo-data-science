"""
Seed student_count in cities table from ESR student enrollment CSV.

Source: fr-esr-atlas_regional-effectifs-d-etudiants-inscrits_agregeables.csv
  - Col 1  : Code commune (INSEE 5-char)
  - Col 9  : Nombre total d'étudiants inscrits (by regroupement + sexe)
  - Col 28 : Rentrée (année civile)

Strategy:
  - Use latest year available per commune
  - Sum all rows (regroupements × sexe) for that year = total enrollment
  - Consolidate Paris/Lyon/Marseille arrondissements → commune principale
  - UPDATE cities.student_count

Run:
  python -m pipeline.scripts.seed_students [--csv path] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from ..services.geo import ARR_TO_COMMUNE

load_dotenv()

DEFAULT_CSV = str(
    Path(__file__).parent.parent.parent
    / "csv"
    / "fr-esr-atlas_regional-effectifs-d-etudiants-inscrits_agregeables.csv"
)


def load_student_counts(csv_path: str) -> dict[str, int]:
    """Returns {insee_code: student_count} using latest year per commune."""

    # Pass 1: find latest year per raw code
    latest_year: dict[str, str] = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)
        for row in reader:
            code = row[1].strip().zfill(5)
            annee = row[28].strip()
            if annee > latest_year.get(code, "0"):
                latest_year[code] = annee

    # Pass 2: sum for latest year
    raw_totals: dict[str, int] = defaultdict(int)
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)
        for row in reader:
            code = row[1].strip().zfill(5)
            if row[28].strip() != latest_year.get(code):
                continue
            try:
                raw_totals[code] += int(row[9])
            except (ValueError, IndexError):
                pass

    # Consolidate arrondissements → commune principale
    totals: dict[str, int] = defaultdict(int)
    for code, n in raw_totals.items():
        canonical = ARR_TO_COMMUNE.get(code, code)
        totals[canonical] += n

    return dict(totals)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Loading {args.csv} ...")
    counts = load_student_counts(args.csv)
    print(f"  {len(counts)} communes with student data")

    # Sanity check
    for city, code in [("Paris", "75056"), ("Lyon", "69123"), ("Marseille", "13055"), ("Toulouse", "31555")]:
        print(f"  {city} ({code}): {counts.get(code, 'N/A')} students")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set"); sys.exit(1)

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    # Match to DB cities (optionally restricted to one department)
    insee_codes = list(counts.keys())
    dept_clause = "AND department_code = %s" if args.dept else ""
    params = (insee_codes, args.dept) if args.dept else (insee_codes,)
    cur.execute(
        f"SELECT insee_code, id FROM cities WHERE insee_code = ANY(%s) {dept_clause}",
        params,
    )
    id_map = {row[0]: row[1] for row in cur.fetchall()}
    print(f"  {len(id_map)} / {len(counts)} communes matched in DB")

    rows = [(counts[code], id_map[code]) for code in counts if code in id_map]

    if args.dry_run:
        print(f"[DRY RUN] Would update {len(rows)} cities")
        for n, city_id in sorted(rows, key=lambda x: -x[0])[:5]:
            cur.execute("SELECT name FROM cities WHERE id = %s", (city_id,))
            print(f"  {cur.fetchone()[0]}: {n} students")
        cur.close()
        conn.close()
        return

    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE cities SET
            student_count = data.n,
            updated_at    = NOW()
        FROM (VALUES %s) AS data(n, id)
        WHERE cities.id = data.id
        """,
        rows,
        template="(%s::integer, %s)",
    )
    conn.commit()
    print(f"Done. {len(rows)} cities updated with student_count.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
