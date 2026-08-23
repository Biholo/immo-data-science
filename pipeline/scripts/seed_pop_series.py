"""
Seed yearly population/aging time series from INSEE IC-Évol-Struct-Pop CSVs.

Source: csv/base-ic-evol-struct-pop/base-ic-evol-struct-pop-{year}.CSV  (2017-2022)

Series produced (city level, frequency=yearly):
  population   = SUM(POP)              — total population (count)
  aging_index  = SUM(POP65P) / SUM(POP0019)  — ratio 65+/0-19

Run:
  python -m pipeline.scripts.seed_pop_series [--dept 77] [--dry-run]
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

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "base-ic-evol-struct-pop"

POP_SERIES_DEF = {
    "population":  {"source": "INSEE", "frequency": "ANNUAL", "unit": "count", "chart_type": "LINE"},
    "aging_index": {"source": "INSEE", "frequency": "ANNUAL", "unit": "ratio", "chart_type": "LINE"},
}


def load_csv_year(path: Path, year: int) -> dict[str, dict[str, float | None]]:
    """Aggregate IRIS → commune, return population/aging_index per insee_code."""
    yy = str(year)[-2:]
    # [pop, pop65p, pop0019]
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)

        def col(name: str) -> int:
            return header.index(name)

        i_com    = col("COM")
        i_pop    = col(f"P{yy}_POP")
        i_pop65  = col(f"P{yy}_POP65P")
        i_pop019 = col(f"P{yy}_POP0019")

        def _f(row: list[str], i: int) -> float:
            try:
                return float(row[i]) if row[i].strip() else 0.0
            except (ValueError, IndexError):
                return 0.0

        for row in reader:
            com = row[i_com].strip().zfill(5)
            com = ARR_TO_COMMUNE.get(com, com)
            a = acc[com]
            a[0] += _f(row, i_pop)
            a[1] += _f(row, i_pop65)
            a[2] += _f(row, i_pop019)

    out = {}
    for com, (pop, pop65, pop019) in acc.items():
        if pop <= 0:
            continue
        out[com] = {
            "population":  pop,
            "aging_index": pop65 / pop019 if pop019 > 0 else None,
        }
    return out


def load_all_years(csv_dir: Path = CSV_DIR) -> dict[int, dict[str, dict[str, float | None]]]:
    all_years: dict[int, dict] = {}
    for csv_file in sorted(csv_dir.glob("*.CSV")):
        m = re.search(r"(\d{4})", csv_file.name)
        if not m:
            continue
        year = int(m.group(1))
        print(f"  {csv_file.name} → year {year}")
        all_years[year] = load_csv_year(csv_file, year)
        print(f"    {len(all_years[year])} communes")
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

    for serie_name, serie_meta in POP_SERIES_DEF.items():
        rows: list[tuple] = []
        for year, year_data in sorted(all_years.items()):
            period = f"{year}-01-01"
            for insee_code, vals in year_data.items():
                if insee_code not in allowed:
                    continue
                v = vals.get(serie_name)
                if v is None:
                    continue
                rows.append((insee_code, period, v))

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

    print("Loading IC-Évol-Struct-Pop CSV files...")
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
