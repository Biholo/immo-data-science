"""
Seed cities.student_breakdown -- higher-education enrolment by type of programme.

Source: fr-esr-atlas_regional-effectifs-d-etudiants-inscrits_agregeables.csv
  - Col 1  : commune INSEE code
  - Col 3  : regroupement (programme / institution family, ASCII code)
  - Col 9  : effectif (students enrolled, per regroupement x sex)
  - Col 28 : civil year of the enrolment

Stored as JSONB with English keys (raw head-counts, not percentages):

  universities         UNIV, EPEU                 public universities + private university institutions
  engineering          ING_autres, INP, UT        engineering schools and polytechnic institutes
  business             EC_COM                     business, management and accounting schools
  vocational           STS                        BTS-type technician sections
  preparatory          CPGE                       classes preparatoires
  paramedical_social   EC_PARAM                   paramedical and social schools
  arts_culture         EC_ART                     art and culture schools
  other                GE, ENS, EC_JUR, EC_autres other institutions

Latest year per commune; Paris/Lyon/Marseille arrondissements are folded into their
parent commune (same as seed_students.py).

NOTE: this is a different perimeter from cities.student_count (INSEE RP, everyone aged
15-64 in education, high-schoolers included). The sum of this breakdown is only the
higher-education enrolment, so the two numbers are NOT expected to match.

Run:
  python -m pipeline.scripts.seed_student_breakdown [--dept 77] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import json
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

REGROUPEMENT_TO_KEY = {
    "UNIV": "universities",
    "EPEU": "universities",
    "ING_autres": "engineering",
    "INP": "engineering",
    "UT": "engineering",
    "EC_COM": "business",
    "STS": "vocational",
    "CPGE": "preparatory",
    "EC_PARAM": "paramedical_social",
    "EC_ART": "arts_culture",
    "GE": "other",
    "ENS": "other",
    "EC_JUR": "other",
    "EC_autres": "other",
}


def load_breakdowns(csv_path: str) -> dict[str, dict[str, int]]:
    """Returns {insee_code: {key: head_count}} for the latest year of each commune."""
    latest_year: dict[str, str] = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)
        for row in reader:
            code = row[1].strip().zfill(5)
            annee = row[28].strip()
            if annee > latest_year.get(code, "0"):
                latest_year[code] = annee

    raw: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    unknown: set[str] = set()
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)
        for row in reader:
            code = row[1].strip().zfill(5)
            if row[28].strip() != latest_year.get(code):
                continue
            key = REGROUPEMENT_TO_KEY.get(row[3].strip())
            if key is None:
                unknown.add(row[3].strip())
                key = "other"
            try:
                raw[code][key] += int(row[9])
            except (ValueError, IndexError):
                pass
    if unknown:
        print(f"  NOTE: unmapped regroupements folded into 'other': {sorted(unknown)}")

    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for code, parts in raw.items():
        canonical = ARR_TO_COMMUNE.get(code, code)
        for key, n in parts.items():
            out[canonical][key] += n
    return {c: {k: v for k, v in parts.items() if v > 0} for c, parts in out.items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Loading {args.csv} ...")
    breakdowns = {c: b for c, b in load_breakdowns(args.csv).items() if b}
    print(f"  {len(breakdowns)} communes with higher-education enrolment")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    try:
        dept_clause = "AND department_code = %s" if args.dept else ""
        params = (list(breakdowns), args.dept) if args.dept else (list(breakdowns),)
        cur.execute(f"SELECT insee_code, id, name FROM cities WHERE insee_code = ANY(%s) {dept_clause}", params)
        matched = cur.fetchall()
        print(f"  {len(matched)} / {len(breakdowns)} communes matched in DB")

        rows = [(json.dumps(breakdowns[insee]), cid) for insee, cid, _ in matched]

        if args.dry_run:
            for insee, _, name in sorted(matched, key=lambda m: -sum(breakdowns[m[0]].values()))[:8]:
                print(f"    {name:25s} {breakdowns[insee]}")
            print("\n[DRY RUN] no writes")
            return

        psycopg2.extras.execute_values(
            cur,
            """
            UPDATE cities SET
                student_breakdown = data.breakdown::jsonb,
                updated_at        = NOW()
            FROM (VALUES %s) AS data(breakdown, id)
            WHERE cities.id = data.id
            """,
            rows,
            template="(%s, %s)",
        )
        conn.commit()
        print(f"\nDone. {len(rows)} cities updated with student_breakdown.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
