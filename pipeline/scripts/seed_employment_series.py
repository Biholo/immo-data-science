"""
Seed yearly active-population time series from INSEE IC-Activité-Résidents files.

Same source files as seed_rp_series.py (already downloaded):
  csv/base-ic-activite-residents/base-ic-activite-residents-{year}.CSV  (2017-2021)
  csv/base-ic-activite-residents-2022.xlsx                               (2022)

Series produced (city level, frequency=yearly):
  active_population = SUM(ACT1564)   — actifs occupés 15-64 ans (count)

`employment_growth` (dashboard field, cities.employment_growth) is derived from
this series in seed_dashboard_fields.py — not computed here.

NOTE: "active_population" must exist as a SerieName enum value in Postgres
before running for real (see README.md "Enum values" section).

Run:
  python -m pipeline.scripts.seed_employment_series [--dept 77] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

from ..services.geo import ARR_TO_COMMUNE

load_dotenv()

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "base-ic-activite-residents"
XLSX_2022 = Path(__file__).parent.parent.parent / "csv" / "base-ic-activite-residents-2022.xlsx"

EMPLOYMENT_SERIES_DEF = {
    "active_population": {"source": "INSEE", "frequency": "ANNUAL", "unit": "count", "chart_type": "LINE"},
}


def load_csv_year(path: Path, year: int) -> dict[str, float]:
    """Aggregate IRIS → commune, return SUM(ACT1564) per insee_code."""
    yy = str(year)[-2:]
    acc: dict[str, float] = defaultdict(float)

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)

        def col(name: str) -> int:
            return header.index(name)

        i_com = col("COM")
        i_act = col(f"P{yy}_ACT1564")

        def _f(row: list[str], i: int) -> float:
            try:
                return float(row[i]) if row[i].strip() else 0.0
            except (ValueError, IndexError):
                return 0.0

        for row in reader:
            com = row[i_com].strip().zfill(5)
            com = ARR_TO_COMMUNE.get(com, com)
            acc[com] += _f(row, i_act)

    return dict(acc)


def load_xlsx_year(xlsx_path: Path, year: int = 2022) -> dict[str, float]:
    """Read 2022 XLSX, aggregate IRIS → commune, return SUM(ACT1564) per insee_code."""
    try:
        import openpyxl
    except ImportError:
        print("pip install openpyxl"); sys.exit(1)

    yy = str(year)[-2:]
    wb = openpyxl.load_workbook(str(xlsx_path), read_only=True, data_only=True)
    ws = wb["IRIS"]
    rows_iter = ws.iter_rows(values_only=True)

    for _ in range(5):
        next(rows_iter)
    header = list(next(rows_iter))

    i_com = header.index("COM")
    i_act = header.index(f"P{yy}_ACT1564")

    acc: dict[str, float] = defaultdict(float)

    def _n(v) -> float:
        try:
            return float(v) if v is not None else 0.0
        except (ValueError, TypeError):
            return 0.0

    for row in rows_iter:
        com = str(row[i_com] or "").strip().zfill(5)
        if not com or com == "00000":
            continue
        com = ARR_TO_COMMUNE.get(com, com)
        acc[com] += _n(row[i_act])

    wb.close()
    return dict(acc)


def load_all_years(
    csv_dir: Path = CSV_DIR,
    xlsx_path: Path = XLSX_2022,
) -> dict[int, dict[str, float]]:
    """Returns {year: {insee_code: active_population}}."""
    all_years: dict[int, dict] = {}

    for csv_file in sorted(csv_dir.glob("*.CSV")):
        m = re.search(r"(\d{4})", csv_file.name)
        if not m:
            continue
        year = int(m.group(1))
        print(f"  {csv_file.name} → year {year}")
        all_years[year] = load_csv_year(csv_file, year)
        print(f"    {len(all_years[year])} communes")

    if xlsx_path.exists():
        print(f"  {xlsx_path.name} → year 2022")
        all_years[2022] = load_xlsx_year(xlsx_path, year=2022)
        print(f"    {len(all_years[2022])} communes")

    return all_years


def seed_series(
    conn,
    all_years: dict[int, dict],
    dept_filter: str | None = None,
    dry_run: bool = False,
) -> None:
    from ..services.upload import upsert_series_and_timeseries

    cur = conn.cursor()
    clause = "WHERE department_code = %s" if dept_filter else ""
    params = (dept_filter,) if dept_filter else ()
    cur.execute(f"SELECT insee_code FROM cities {clause}", params)
    allowed = {row[0] for row in cur.fetchall()}
    cur.close()

    for serie_name, serie_meta in EMPLOYMENT_SERIES_DEF.items():
        rows: list[tuple] = []
        for year, year_data in sorted(all_years.items()):
            period = f"{year}-01-01"
            for insee_code, value in year_data.items():
                if insee_code not in allowed:
                    continue
                if value <= 0:
                    continue
                rows.append((insee_code, period, value))

        serie_def = {"name": serie_name, **serie_meta}
        print(f"  {serie_name}: {len(rows)} data points")

        if not dry_run and rows:
            stats = upsert_series_and_timeseries(conn, serie_def, "city", rows, dry_run=False)
            print(f"    → {stats['series_upserted']} series, {stats['timeseries_inserted']} ts")
        elif dry_run:
            print(f"    [DRY RUN] skipped")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Loading IC-Activité-Résidents files for active_population...")
    all_years = load_all_years()
    if not all_years:
        print(f"No files found in {CSV_DIR}"); sys.exit(1)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set"); sys.exit(1)

    conn = psycopg2.connect(dsn)
    try:
        seed_series(conn, all_years, dept_filter=args.dept, dry_run=args.dry_run)
    finally:
        conn.close()

    print("Done.")


if __name__ == "__main__":
    main()
