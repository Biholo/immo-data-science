"""
Seed yearly business-creation time series from INSEE SIDE (Systeme d'Information
sur la Demographie d'Entreprises), commune level.

Source: csv/creations-entreprises/creations-entreprises-2012-2025.CSV
  Derived (filtered, not modified value-wise) from the single national bulk file
  of the data.gouv.fr / INSEE dataset
    "Creations d'entreprises au niveau communal et supra communal par secteur
     d'activite (A10) et forme legale"
    https://www.data.gouv.fr/datasets/creations-dentreprises-au-niveau-communal-et-supra-communal-par-secteur-dactivite-a10-et-forme-legale
    stable resource: https://www.data.gouv.fr/api/1/datasets/r/4403b565-ecc1-4c99-a468-aea9aa49a11e
      -> redirects to https://api.insee.fr/melodi/file/DS_SIDE_CREA_ENT_COM/DS_SIDE_CREA_ENT_COM_2025_CSV_FR
    (plain file download, no API key; ZIP ~44 MB -> DS_SIDE_CREA_ENT_COM_2025_data.csv ~410 MB)

  The raw file is long/tidy with columns:
    ACTIVITY;FREQ;GEO;GEO_OBJECT;LEGAL_FORM;SIDE_MEASURE;OBS_STATUS;TIME_PERIOD;OBS_VALUE
  We keep only the rows we need and drop every column that then becomes constant:
    GEO_OBJECT == "COM"   (commune level; "ARM" arrondissement rows are dropped --
                           at COM level Paris/Lyon/Marseille are ALREADY consolidated,
                           GEO = 75056 / 69123 / 13055, verified in the source)
    ACTIVITY   == "_T"    (all sectors -- A10 breakdown not needed)
    LEGAL_FORM == "_T"    (all legal forms -- individual entrepreneur + all company
                           types, see "definition" note below)
    SIDE_MEASURE == "BURE" (only measure in the file)
  leaving the 3-column schema actually stored here: GEO;TIME_PERIOD;OBS_VALUE
  (one row per commune per year, 2012-2025, ~419k rows, ~5.6 MB).

Definition chosen -- WHAT is counted:
  SIDE_MEASURE "BURE" = "Nombre de nouvelles unites legales enregistrees", i.e.
  creation of *enterprises* (unites legales), NOT etablissements. This is INSEE's
  headline "creations d'entreprises" figure. It is the ALL-INCLUSIVE count:
  it *includes micro-entrepreneurs / auto-entrepreneurs* (INSEE's "y compris
  micro-entrepreneurs" series) -- there is no "hors micro" variant in this file.
  Chosen because it is the broadest, single most-comparable-over-time measure and
  the one every INSEE press release headlines.

  Comparability note: SIDE replaced the old REE in 2022 and INSEE warns SIDE and
  REE numbers are not comparable -- but this single SIDE file is recomputed on one
  consistent SIDE methodology back to 2012, so 2012-2025 within it IS a coherent
  series. That is exactly why we take one file rather than splicing REE + SIDE.

Series produced (city level, frequency=yearly):
  company_creations = OBS_VALUE   (count of new enterprises registered in the year)

source = "INSEE": SerieSource enum has CALC/DVF/INSEE/SCR only -- same convention
as every other government-CSV series already in this pipeline.

NOTE: "company_creations" must exist in the Postgres `SerieName` enum before the
first real run (it does -- verified; confirm at runtime with
`SELECT enum_range(NULL::"SerieName")`). Cf. README.md "Verifier les enums Postgres".

Run:
  python -m pipeline.scripts.seed_company_creations_series [--dept 77] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

from ..services.geo import ARR_TO_COMMUNE

load_dotenv()

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "creations-entreprises"

COMPANY_CREATIONS_SERIES_DEF = {
    "company_creations": {"source": "INSEE", "frequency": "ANNUAL", "unit": "count", "chart_type": "BAR"},
}

# A single French commune reasonably tops out around Paris' ~120k/yr; anything an
# order of magnitude past that is a parsing/column-shift bug, not real data.
VALUE_MAX_SANE = 250_000


def load_long_format_csv(path: Path) -> dict[int, dict[str, float]]:
    """Read the ';'-delimited long file (GEO;TIME_PERIOD;OBS_VALUE) and return
    {year: {insee_code: count}}, summing after ARR_TO_COMMUNE consolidation
    (a defensive no-op here -- COM rows are already consolidated at source)."""
    by_year: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    n_rows = 0
    n_bad = 0

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            try:
                year = int(row["TIME_PERIOD"])
                insee = row["GEO"].strip().zfill(5)
                raw = row["OBS_VALUE"]
                if not raw or not raw.strip():
                    continue
                value = float(raw)
            except (KeyError, ValueError, AttributeError):
                n_bad += 1
                continue

            if value < 0 or value > VALUE_MAX_SANE:
                print(f"    ! suspicious value {value} for {insee} ({year}) -- skipped")
                continue

            insee = ARR_TO_COMMUNE.get(insee, insee)
            by_year[year][insee] += value
            n_rows += 1

    if n_bad:
        print(f"    ({n_bad} unparseable rows skipped)")
    print(f"    {n_rows} rows over {len(by_year)} years")
    return {y: dict(d) for y, d in by_year.items()}


def load_all_years(csv_dir: Path = CSV_DIR) -> dict[int, dict[str, dict[str, float]]]:
    """Returns {year: {insee_code: {'company_creations': value}}}."""
    merged: dict[int, dict[str, float]] = {}

    files = sorted(csv_dir.glob("*.CSV"))
    if not files:
        return {}

    for csv_file in files:
        print(f"  {csv_file.name} -> grouping by TIME_PERIOD column")
        years = load_long_format_csv(csv_file)
        for year, communes in years.items():
            merged.setdefault(year, {}).update(communes)

    return {
        year: {insee: {"company_creations": value} for insee, value in communes.items()}
        for year, communes in merged.items()
    }


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

    for serie_name, serie_meta in COMPANY_CREATIONS_SERIES_DEF.items():
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
        years_covered = sorted(all_years.keys())
        print(f"  {serie_name}: {len(rows)} data points (years {years_covered[0]}-{years_covered[-1]})")

        if not dry_run and rows:
            stats = upsert_series_and_timeseries(conn, serie_def, "city", rows, dry_run=False)
            print(f"    -> {stats['series_upserted']} series, {stats['timeseries_inserted']} ts")
        elif dry_run:
            sample = rows[:8]
            print(f"    [DRY RUN] skipped -- sample: {sample}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Loading creations-entreprises CSV files...")
    all_years = load_all_years()
    if not all_years:
        print(f"No usable data found in {CSV_DIR}"); sys.exit(1)

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
