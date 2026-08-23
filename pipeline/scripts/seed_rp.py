"""
Seed INSEE RP 2022 data into cities table.

Source: base-ic-activite-residents-2022.xlsx (sheet IRIS)
Granularité: IRIS → agrège par commune (COM) via SUM

Variables extraites:
  P22_ETUD1564  → student_count    (élèves, étudiants, stagiaires 15-64)
  P22_RETR1564  → retired_count    (retraités ou préretraités 15-64)
  P22_CHOM1564  → unemployed_count (chômeurs 15-64, total tous diplômes)

Remplace student_count ESR par valeur RP (périmètre plus large).

Run:
  python -m pipeline.scripts.seed_rp [--xlsx path] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from ..services.geo import ARR_TO_COMMUNE

load_dotenv()

DEFAULT_XLSX = str(
    Path(__file__).parent.parent.parent
    / "csv"
    / "base-ic-activite-residents-2022.xlsx"
)

SHEET = "IRIS"
HEADER_ROW = 5   # 0-indexed row index in the sheet

# Column names in the header row
COL_COM    = "COM"
COL_ETUD   = "P22_ETUD1564"
COL_RETR   = "P22_RETR1564"
COL_CHOM   = "P22_CHOM1564"


def load_rp_data(xlsx_path: str) -> dict[str, dict[str, int]]:
    """
    Returns {insee_code: {student, retired, unemployed}} aggregated at commune level.
    Reads IRIS sheet and sums across all IRIS of each commune.
    """
    try:
        import openpyxl
    except ImportError:
        print("pip install openpyxl"); sys.exit(1)

    print(f"Loading {xlsx_path} ...")
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[SHEET]

    rows = ws.iter_rows(values_only=True)

    # Skip metadata rows, find header
    header = None
    skipped = 0
    for row in rows:
        if skipped < HEADER_ROW:
            skipped += 1
            continue
        header = list(row)
        break

    if not header:
        raise ValueError("Header row not found")

    # Column indices
    try:
        i_com  = header.index(COL_COM)
        i_etud = header.index(COL_ETUD)
        i_retr = header.index(COL_RETR)
        i_chom = header.index(COL_CHOM)
    except ValueError as e:
        raise ValueError(f"Column not found: {e}. Check HEADER_ROW constant.") from e

    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"student": 0, "retired": 0, "unemployed": 0})

    for row in rows:
        com = str(row[i_com] or "").strip().zfill(5)
        if not com or com == "00000":
            continue
        com = ARR_TO_COMMUNE.get(com, com)

        def _int(val) -> int:
            try:
                return int(float(val)) if val is not None else 0
            except (ValueError, TypeError):
                return 0

        totals[com]["student"]    += _int(row[i_etud])
        totals[com]["retired"]    += _int(row[i_retr])
        totals[com]["unemployed"] += _int(row[i_chom])

    wb.close()
    print(f"  {len(totals)} communes aggregated from IRIS data")
    return dict(totals)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xlsx", default=DEFAULT_XLSX)
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    data = load_rp_data(args.xlsx)

    # Sanity check
    for city, code in [("Paris", "75056"), ("Lyon", "69123"), ("Toulouse", "31555"), ("Melun", "77288")]:
        d = data.get(code, {})
        print(f"  {city} ({code}): etudiants={d.get('student',0)}, retraites={d.get('retired',0)}, chomeurs={d.get('unemployed',0)}")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set"); sys.exit(1)

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    # Match to DB (optionally restricted to one department)
    codes = list(data.keys())
    dept_clause = "AND department_code = %s" if args.dept else ""
    params = (codes, args.dept) if args.dept else (codes,)
    cur.execute(
        f"SELECT insee_code, id FROM cities WHERE insee_code = ANY(%s) {dept_clause}",
        params,
    )
    id_map = {row[0]: row[1] for row in cur.fetchall()}
    print(f"  {len(id_map)} / {len(data)} communes matched in DB")

    rows = [
        (d["student"], d["retired"], d["unemployed"], id_map[code])
        for code, d in data.items()
        if code in id_map
    ]

    if args.dry_run:
        print(f"[DRY RUN] Would update {len(rows)} cities")
        cur.close(); conn.close()
        return

    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE cities SET
            student_count    = data.student,
            retired_count    = data.retired,
            unemployed_count = data.unemployed,
            updated_at       = NOW()
        FROM (VALUES %s) AS data(student, retired, unemployed, id)
        WHERE cities.id = data.id
        """,
        rows,
        template="(%s::integer, %s::integer, %s::integer, %s)",
    )
    conn.commit()
    print(f"Done. {len(rows)} cities updated (student_count, retired_count, unemployed_count).")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
