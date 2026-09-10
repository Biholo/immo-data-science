"""
Seed yearly household-size distribution time series from INSEE
"Couples-Familles-Ménages" table MEN1 (Ménages par taille du ménage et
catégorie socioprofessionnelle de la personne de référence), commune level.

Source: csv/menages-taille/menages-taille-{year}.CSV  (2016, 2022 editions)
  Pre-aggregated (CODGEO x NPERC, summed over the socio-professional-category
  dimension we don't need) from the raw INSEE long-format files:
    - TD_MEN1_2022_csv.zip  (2022 edition, https://www.insee.fr/fr/statistiques/8582448)
    - BTT_TD_MEN1_2016.zip  (2016 edition, https://www.insee.fr/fr/statistiques/4171364)
  Columns: NIVGEO;CODGEO;NPERC;NB
    NIVGEO = COM or ARM (Paris/Lyon/Marseille arrondissement)
    NPERC  = household size bucket: 1, 2, 3, 4, 5, 6 ("6 or more" persons)
    NB     = number of households (fractional — INSEE disclosure-control weighting)

Series produced (city level, frequency=yearly), each a ratio of total
households in the commune so a frontend can build a pie chart directly:
  household_size_1p_rate       = MEN(NPERC=1)          / MEN(total)
  household_size_2p_rate       = MEN(NPERC=2)          / MEN(total)
  household_size_3p_rate       = MEN(NPERC=3)          / MEN(total)
  household_size_4p_rate       = MEN(NPERC=4)          / MEN(total)
  household_size_5p_plus_rate  = MEN(NPERC=5 + NPERC=6) / MEN(total)

Run:
  python -m pipeline.scripts.seed_household_size_series [--dept 77] [--dry-run]
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

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "menages-taille"

HOUSEHOLD_SIZE_SERIES_DEF = {
    # chart_type LINE to match existing series convention (no PIE value seen
    # elsewhere in the codebase's SerieChartType usage) — the frontend
    # combines the latest value of these 5 sibling series into a pie chart.
    "household_size_1p_rate":      {"source": "INSEE", "frequency": "ANNUAL", "unit": "ratio", "chart_type": "LINE"},
    "household_size_2p_rate":      {"source": "INSEE", "frequency": "ANNUAL", "unit": "ratio", "chart_type": "LINE"},
    "household_size_3p_rate":      {"source": "INSEE", "frequency": "ANNUAL", "unit": "ratio", "chart_type": "LINE"},
    "household_size_4p_rate":      {"source": "INSEE", "frequency": "ANNUAL", "unit": "ratio", "chart_type": "LINE"},
    "household_size_5p_plus_rate": {"source": "INSEE", "frequency": "ANNUAL", "unit": "ratio", "chart_type": "LINE"},
}


def load_csv_year(path: Path, year: int) -> dict[str, dict[str, float | None]]:
    """Aggregate ARM → commune, return household-size rates per insee_code."""
    # [men_1p, men_2p, men_3p, men_4p, men_5p, men_6p_plus]
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0] * 6)

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)

        def col(name: str) -> int:
            return header.index(name)

        i_com   = col("CODGEO")
        i_nperc = col("NPERC")
        i_nb    = col("NB")

        def _f(row: list[str], i: int) -> float:
            try:
                return float(row[i]) if row[i].strip() else 0.0
            except (ValueError, IndexError):
                return 0.0

        for row in reader:
            com = row[i_com].strip().zfill(5)
            com = ARR_TO_COMMUNE.get(com, com)
            nperc = row[i_nperc].strip()
            nb = _f(row, i_nb)
            a = acc[com]
            if nperc in ("1", "2", "3", "4", "5"):
                a[int(nperc) - 1] += nb
            elif nperc == "6":
                a[5] += nb

    out = {}
    for com, (m1, m2, m3, m4, m5, m6p) in acc.items():
        total = m1 + m2 + m3 + m4 + m5 + m6p
        if total <= 0:
            continue
        out[com] = {
            "household_size_1p_rate":      m1 / total,
            "household_size_2p_rate":      m2 / total,
            "household_size_3p_rate":      m3 / total,
            "household_size_4p_rate":      m4 / total,
            "household_size_5p_plus_rate": (m5 + m6p) / total,
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

    if dry_run:
        _sanity_check(all_years, allowed)

    for serie_name, serie_meta in HOUSEHOLD_SIZE_SERIES_DEF.items():
        rows: list[tuple] = []
        for year, year_data in sorted(all_years.items()):
            period = f"{year}-01-01"
            for insee_code, rates in year_data.items():
                if insee_code not in allowed:
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


def _sanity_check(all_years: dict[int, dict], allowed: set[str]) -> None:
    """Print the 5 rates for a sample commune per year and verify they sum to ~1.0."""
    print("\n  [Sanity check] household-size rates should sum to ~1.0")
    for year, year_data in sorted(all_years.items()):
        sample_code = next((c for c in year_data if c in allowed), None)
        if sample_code is None:
            print(f"    {year}: no commune in filter found")
            continue
        rates = year_data[sample_code]
        total = sum(v for v in rates.values() if v is not None)
        print(f"    {year} commune {sample_code}: {rates} → sum={total:.4f}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Loading Couples-Familles-Ménages MEN1 (taille des ménages) CSV files...")
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
