"""
Seed long-run yearly population time series from INSEE's "Historique des
populations communales" (https://www.insee.fr/fr/statistiques/3698339).

Source: csv/population-historique/population-historique-{year}.CSV (2006-2023)
These per-year CSVs are produced from the raw wide-format INSEE workbook
(csv/population-historique/raw_histo_pop.xlsx) by the one-off reshape script
pipeline/scripts/prep_population_history.py — run that first if the CSVs are
missing. See that script's docstring for why only the PMUN2006-PMUN2023
vintages are used (older PSDC/PTOT vintages use a different, non-comparable
population-counting methodology) and why Paris/Lyon/Marseille arrondissements
are folded into their parent commune there already (so, unlike
seed_pop_series.py, this script does NOT need ARR_TO_COMMUNE — the per-year
CSVs are already at plain commune level).

Series produced (city level, frequency=yearly):
  population_history = POPULATION   — total population (count), one
                           datapoint per available census year (2006-2023)

Run:
  python -m pipeline.scripts.seed_population_history_series [--dept 77] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "population-historique"

POP_HISTORY_SERIES_DEF = {
    "population_history": {"source": "INSEE", "frequency": "ANNUAL", "unit": "count", "chart_type": "LINE"},
}


def load_csv_year(path: Path, year: int) -> dict[str, dict[str, float | None]]:
    """Parse one population-historique-{year}.CSV into {insee_code: {metric: value}}."""
    out: dict[str, dict[str, float | None]] = {}

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)

        def col(name: str) -> int:
            return header.index(name)

        i_com = col("COM")
        i_pop = col("POPULATION")

        for row in reader:
            if not row or not row[i_com].strip():
                continue
            com = row[i_com].strip().zfill(5)
            try:
                pop = float(row[i_pop]) if row[i_pop].strip() else None
            except (ValueError, IndexError):
                pop = None
            if pop is None or pop <= 0:
                continue
            out[com] = {"population_history": pop}

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

    for serie_name, serie_meta in POP_HISTORY_SERIES_DEF.items():
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

    print("Loading population-historique CSV files...")
    all_years = load_all_years()
    if not all_years:
        print(f"No files found in {CSV_DIR}. Run 'python -m pipeline.scripts.prep_population_history' first.")
        sys.exit(1)

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
