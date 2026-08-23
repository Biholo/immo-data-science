"""
Seed yearly socio-demo time series from INSEE IC-Activité-Résidents files.

Sources:
  csv/ic-activite-residents/base-ic-activite-residents-{year}.CSV  (2017-2021)
  csv/base-ic-activite-residents-2022.xlsx                          (2022)

Series produced (city level, frequency=yearly):
  unemployment_rate  = CHOM1564 / ACT1564

Period format: YYYY-01-01 (ISO date).

Note: vacancy_rate / owner_rate require INSEE base-ic-logement files (not available here).

Run:
  python -m pipeline.scripts.seed_rp_series [--dept 77] [--dry-run]
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

RP_SERIES_DEF = {
    "unemployment_rate": {"source": "INSEE", "frequency": "ANNUAL", "unit": "ratio", "chart_type": "LINE"},
}


def _rates(chom: float, act: float) -> dict[str, float | None]:
    return {
        "unemployment_rate": chom / act if act > 0 else None,
    }


def load_csv_year(path: Path, year: int) -> dict[str, dict[str, float | None]]:
    """Read one annual CSV, aggregate IRIS → commune, return rates per insee_code."""
    yy = str(year)[-2:]
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])  # chom,act,pop

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)

        def col(name: str) -> int:
            return header.index(name)

        i_com  = col("COM")
        i_chom = col(f"P{yy}_CHOM1564")
        i_act  = col(f"P{yy}_ACT1564")
        i_pop  = col(f"P{yy}_POP1564")

        def _f(row: list[str], i: int) -> float:
            try:
                return float(row[i]) if row[i].strip() else 0.0
            except (ValueError, IndexError):
                return 0.0

        for row in reader:
            com = row[i_com].strip().zfill(5)
            com = ARR_TO_COMMUNE.get(com, com)
            a = acc[com]
            a[0] += _f(row, i_chom)
            a[1] += _f(row, i_act)
            a[2] += _f(row, i_pop)

    return {com: _rates(vals[0], vals[1]) for com, vals in acc.items() if vals[2] > 0}


def load_xlsx_year(xlsx_path: Path, year: int = 2022) -> dict[str, dict[str, float | None]]:
    """Read 2022 XLSX, aggregate IRIS → commune, return rates per insee_code."""
    try:
        import openpyxl
    except ImportError:
        print("pip install openpyxl"); sys.exit(1)

    yy = str(year)[-2:]
    wb = openpyxl.load_workbook(str(xlsx_path), read_only=True, data_only=True)
    ws = wb["IRIS"]
    rows_iter = ws.iter_rows(values_only=True)

    # Skip 5 metadata rows, then read header
    for _ in range(5):
        next(rows_iter)
    header = list(next(rows_iter))

    i_com  = header.index("COM")
    i_chom = header.index(f"P{yy}_CHOM1564")
    i_act  = header.index(f"P{yy}_ACT1564")
    i_pop  = header.index(f"P{yy}_POP1564")

    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])

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
        a = acc[com]
        a[0] += _n(row[i_chom])
        a[1] += _n(row[i_act])
        a[2] += _n(row[i_pop])

    wb.close()
    return {com: _rates(vals[0], vals[1]) for com, vals in acc.items() if vals[2] > 0}


def load_all_years(
    csv_dir: Path = CSV_DIR,
    xlsx_path: Path = XLSX_2022,
) -> dict[int, dict[str, dict[str, float | None]]]:
    """Returns {year: {insee_code: {serie_name: value}}}."""
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

    for serie_name, serie_meta in RP_SERIES_DEF.items():
        rows: list[tuple] = []
        for year, year_data in sorted(all_years.items()):
            period = f"{year}-01-01"
            for insee_code, rates in year_data.items():
                if dept_filter and insee_code not in allowed:
                    continue
                if not dept_filter and insee_code not in allowed:
                    continue
                v = rates.get(serie_name)
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

    print("Loading RP CSV files...")
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
