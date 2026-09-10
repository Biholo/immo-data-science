"""
Seed yearly taxe fonciere sur les proprietes baties (TFB) rate time series.

Sources:
  csv/fiscalite/fiscalite-locale-des-particuliers.csv        (2021-2025)
    DGFiP / data.economie.gouv.fr "Fiscalite locale des particuliers - Geo".
    Single LONG-format file (one row per commune per EXERCICE year), NOT split
    per year like the other datasets -> grouped here by the EXERCICE column
    instead of by filename.

  csv/fiscalite/rei-taux-global-tfb-{2018,2019,2020}.csv      (2018-2020)
    Derived from DGFiP's REI ("fichier de recensement des elements
    d'imposition a la fiscalite directe locale") national files, downloaded
    from data.economie.gouv.fr dataset
    "impots-locaux-fichier-de-recensement-des-elements-dimposition-a-la-fiscalite-dir"
    (attachments rei_2018_fichier_notice_trace_zip / rei_2019_.../ rei_2020_...).
    The REI files (huge national XLSX, ~150MB/year, one row per commune with
    ~1000 raw fiscal columns) do NOT contain a pre-computed "Taux_Global_TFB"
    column -- that field only appears from 2021 onward in the "particuliers-geo"
    dataset. It was reconstructed here as:

        Taux_Global_TFB = commune_rate + syndicats_rate + gfp_rate + tse_rate
                          + tse_autres_rate + tasa_rate + gemapi_rate
                          + department_rate

    The department_rate term matters ONLY for 2018-2020: departments still
    voted their own TFB rate through 2020; from 2021 the department's TFB was
    transferred to (folded into) the commune rate to compensate communes for
    the abolition of taxe d'habitation on main residences. Omitting the
    department term for pre-2021 years understates the historical rate by
    roughly half (validated: with it, dept-77 2020-reconstructed values land
    within ~1pt of the actual 2021 Taux_Global_TFB for the same communes;
    without it they land at roughly half that value). See
    C:\\Users\\Kilian\\...\\scratchpad\\rei\\extract_rei.py for the extraction
    script (not checked into the repo -- only the derived per-year CSVs are).

    Column-name resolution differs by year (2018 REI uses short DGFiP variable
    codes like E12/E22/E32VOTE as headers, 2019-2020 use long French titles
    like "FB - COMMUNE / TAUX VOTE") -- already resolved during extraction, so
    the derived CSVs share one simple schema: "INSEE COM;EXERCICE;Taux_Global_TFB".

Series produced (city level, frequency=yearly):
  property_tax_rate = Taux_Global_TFB   (%, commune's total effective TFB rate)

source = "INSEE": SerieSource enum currently only has CALC/DVF/INSEE/SCR (checked
via `SELECT enumlabel FROM pg_enum ...`) — no DGFIP value exists, so this follows
the same convention as every other non-DVF, non-calculated government CSV source
already seeded in this pipeline (population, logement, employment, rp).

NOTE: "property_tax_rate" must be added to the Postgres `SerieName` enum before
the first real run (cf. README.md "Verifier les enums Postgres" section, same
requirement as "active_population" in seed_employment_series.py).

Run:
  python -m pipeline.scripts.seed_fiscalite_series [--dept 77] [--dry-run]
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

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "fiscalite"
MAIN_FILE = CSV_DIR / "fiscalite-locale-des-particuliers.csv"
PRE_2021_GLOB = "rei-taux-global-tfb-*.csv"

# Sanity range for a French commune's taxe fonciere batie rate. Real rates
# range roughly 10-60%, with rare small/declining communes going much higher.
# A ~10-commune cluster (e.g. 05066, 11012, 11146/147/174/233/369/433, 97102)
# legitimately sits at 90-107% -- confirmed genuine (not a parsing bug) because
# the SAME communes show the SAME order of magnitude in the untouched
# official 2021-2025 file, consistently across years. Anything below/above
# this wider band is almost certainly a column mix-up.
RATE_MIN_SANE = 3.0
RATE_MAX_SANE = 120.0

FISCALITE_SERIES_DEF = {
    "property_tax_rate": {"source": "INSEE", "frequency": "ANNUAL", "unit": "%", "chart_type": "LINE"},
}


def load_long_format_csv(path: Path) -> dict[int, dict[str, float]]:
    """Read one ';'-delimited long-format file with columns
    'INSEE COM', 'EXERCICE', 'Taux_Global_TFB' and group rows by EXERCICE.

    Works for both the main 2021-2025 file (which carries many extra columns)
    and the derived pre-2021 REI CSVs (which carry only these 3 columns).
    """
    by_year: dict[int, dict[str, float]] = {}
    n_rows = 0
    n_bad = 0

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            try:
                year = int(row["EXERCICE"])
                insee = row["INSEE COM"].strip().zfill(5)
                raw = row["Taux_Global_TFB"]
                if not raw or not raw.strip():
                    continue
                value = float(raw)
            except (KeyError, ValueError, AttributeError):
                n_bad += 1
                continue

            n_rows += 1
            if not (RATE_MIN_SANE <= value <= RATE_MAX_SANE):
                print(f"    ! suspicious rate {value} for {insee} ({year}) — skipped")
                continue

            by_year.setdefault(year, {})[insee] = value

    if n_bad:
        print(f"    ({n_bad} unparseable rows skipped)")
    return by_year


def load_all_years(csv_dir: Path = CSV_DIR) -> dict[int, dict[str, dict[str, float]]]:
    """Returns {year: {insee_code: {'property_tax_rate': value}}}."""
    merged: dict[int, dict[str, float]] = {}

    if MAIN_FILE.exists():
        print(f"  {MAIN_FILE.name} → grouping by EXERCICE column")
        main_years = load_long_format_csv(MAIN_FILE)
        for year, communes in main_years.items():
            print(f"    EXERCICE {year}: {len(communes)} communes")
            merged.setdefault(year, {}).update(communes)
    else:
        print(f"  WARNING: {MAIN_FILE} not found")

    for csv_file in sorted(csv_dir.glob(PRE_2021_GLOB)):
        print(f"  {csv_file.name} → grouping by EXERCICE column")
        years = load_long_format_csv(csv_file)
        for year, communes in years.items():
            print(f"    EXERCICE {year}: {len(communes)} communes")
            if year in merged:
                print(f"    ! year {year} already present from another file — merging (existing values kept)")
                for insee, value in communes.items():
                    merged[year].setdefault(insee, value)
            else:
                merged[year] = communes

    return {
        year: {insee: {"property_tax_rate": value} for insee, value in communes.items()}
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

    for serie_name, serie_meta in FISCALITE_SERIES_DEF.items():
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
            print(f"    → {stats['series_upserted']} series, {stats['timeseries_inserted']} ts")
        elif dry_run:
            # Sample a few values so a human can eyeball sanity (10-60% typical).
            sample = rows[:5]
            print(f"    [DRY RUN] skipped — sample: {sample}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Loading fiscalite CSV files...")
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
