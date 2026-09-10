"""
Seed the city-level `median_income` series from INSEE Filosofi 2021
(revenus, pauvreté et niveau de vie), commune level.

Source: csv/DS_FILOSOFI_CC_data.csv — long format, one row per (commune,
indicator), already present in the repo (no download). Downloaded from
https://www.insee.fr/fr/statistiques/7756729
(base-cc-filosofi-2021-geo2025_csv.zip).

Filtering mirrors ml/data/median_income.py EXACTLY:
  - FILOSOFI_MEASURE == 'MED_SL'  ("niveau de vie médian en euros")
  - GEO_OBJECT       == 'COM'
  - drop rows with empty OBS_VALUE (statistical secrecy on small communes,
    CONF_STATUS='C')
  - GEO code zero-padded to 5 chars

Caveat carried downstream: MED_SL is the *niveau de vie médian* — median
disposable income per consumption unit ("unité de consommation"), annual,
in euros. It is NOT median household income. The derived affordability
series (housing_effort_rate, years_to_buy) use it as an approximation and
say so in their own docstrings.

Single snapshot: year 2021, period 2021-01-01, one datapoint per commune.
The load_csv_year / load_all_years shape is kept even though there is only
one file and one year, for consistency with the sibling seed_*_series.py.

Series produced (city level):
  median_income   unit "€", frequency ANNUAL, source INSEE, chart_type LINE

Run:
  python -m pipeline.scripts.seed_median_income_series [--dept 77] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

FILOSOFI_FILE = Path(__file__).parent.parent.parent / "csv" / "DS_FILOSOFI_CC_data.csv"
MEASURE = "MED_SL"
MEDIAN_INCOME_YEAR = 2021
MEDIAN_INCOME_PERIOD = f"{MEDIAN_INCOME_YEAR}-01-01"

MEDIAN_INCOME_SERIES_DEF = {
    "median_income": {"source": "INSEE", "frequency": "ANNUAL", "unit": "€", "chart_type": "LINE"},
}


def load_csv_year(path: Path, year: int) -> dict[str, float]:
    """Parse the Filosofi long-format CSV, return {insee_code: median_income}.

    `year` is accepted only for signature-parity with the sibling loaders —
    Filosofi 2021 is a single vintage and the file is not year-partitioned.
    """
    out: dict[str, float] = {}
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";", quotechar='"')
        for row in reader:
            if row["GEO_OBJECT"] != "COM" or row["FILOSOFI_MEASURE"] != MEASURE:
                continue
            val = row["OBS_VALUE"].strip()
            if not val:
                continue
            try:
                out[row["GEO"].strip().zfill(5)] = float(val)
            except ValueError:
                continue
    return out


def load_all_years(path: Path = FILOSOFI_FILE) -> dict[int, dict[str, float]]:
    """Single-vintage source: degenerates to one {year: {...}} bucket
    (MEDIAN_INCOME_YEAR), kept as a dict for shape-consistency with the
    multi-year sibling seed_*_series.py scripts."""
    if not path.exists():
        print(f"  MISSING file: {path}")
        return {}
    print(f"  {path.name} → year {MEDIAN_INCOME_YEAR}")
    data = load_csv_year(path, MEDIAN_INCOME_YEAR)
    print(f"    {len(data)} communes with {MEASURE}")
    return {MEDIAN_INCOME_YEAR: data}


def seed_series(
    conn,
    all_years: dict[int, dict[str, float]],
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

    for serie_name, serie_meta in MEDIAN_INCOME_SERIES_DEF.items():
        rows: list[tuple] = []
        for year, year_data in sorted(all_years.items()):
            period = f"{year}-01-01"
            for insee_code, v in year_data.items():
                if insee_code not in allowed:
                    continue
                rows.append((insee_code, period, v))

        serie_def = {"name": serie_name, **serie_meta}
        print(f"  {serie_name}: {len(rows)} data points")
        if rows:
            vals = sorted(v for _, _, v in rows)
            mid = vals[len(vals) // 2]
            print(f"    range €{vals[0]:.0f}..€{vals[-1]:.0f}, median €{mid:.0f}")
            print(f"    sample: {rows[:3]}")

        if not dry_run and rows:
            stats = upsert_series_and_timeseries(conn, serie_def, "city", rows, dry_run=False)
            print(f"    → {stats['series_upserted']} series, {stats['timeseries_inserted']} ts")
        elif dry_run:
            print("    [DRY RUN] skipped")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Loading INSEE Filosofi 2021 CSV...")
    all_years = load_all_years()
    if not all_years or not any(all_years.values()):
        print(f"No data found in {FILOSOFI_FILE}")
        sys.exit(1)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    try:
        seed_series(conn, all_years, dept_filter=args.dept, dry_run=args.dry_run)
    finally:
        conn.close()

    print("Done.")


if __name__ == "__main__":
    main()
