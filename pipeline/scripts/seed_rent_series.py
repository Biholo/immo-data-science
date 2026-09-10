"""
Seed rent-per-sqm time series from the ANIL/DHUP "Carte des loyers" rent
prediction model (single national snapshot, no year encoded in the CSV
filenames themselves — confirmed as the 2025 edition via the existing
ml/data/rent.py loader elsewhere in this repo; see RENT_SNAPSHOT_YEAR below).

Source: csv/loyers/*.csv  (semicolon-delimited, quoted, FRENCH DECIMAL COMMA
in loypredm2 — e.g. "9,75769624568385" — must be replaced with '.' before
float()). Confirmed via the existing ml/data/rent.py loader in this repo:
this is the ANIL/DHUP "Carte des loyers" **2025** edition (data.gouv.fr
dataset "carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025").

Columns used: INSEE_C (commune code), loypredm2 (predicted rent €/m²/month).
TYPPRED ("commune" = enough local observations vs "maille" = interpolated
from a broader zone) is NOT filtered on here — both are kept; see report for
the commune/maille reliability split.

Series produced (city level, single snapshot dated RENT_SNAPSHOT_YEAR = 2025):
  rent_sqm_all   ← pred-app-mef-dhup.csv    (all apartment types combined)
  rent_sqm_t1    ← pred-app12-mef-dhup.csv  (apartments 1-2 rooms — NO exact
  rent_sqm_t2    ← pred-app12-mef-dhup.csv    1-vs-2-room split exists in this
                                               source; t1 and t2 draw the SAME
                                               value from the same file)
  rent_sqm_t3    ← pred-app3-mef-dhup.csv   (apartments 3+ rooms — same
  rent_sqm_t4    ← pred-app3-mef-dhup.csv     caveat: t3 and t4 draw the SAME
                                               value from the same file)
  rent_sqm_house ← pred-mai-mef-dhup.csv    (houses — NOT in the original
                                               SerieName enum list; added here
                                               as a useful extra, flag in report)

Run:
  python -m pipeline.scripts.seed_rent_series [--dept 77] [--dry-run]
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

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "loyers"

# DHUP "Carte des loyers" is a single snapshot — no year in the filenames or
# any README next to them. Treated as the latest known publication (~2023).
RENT_SNAPSHOT_YEAR = 2025

# serie_name -> source filename
RENT_FILES: dict[str, str] = {
    "rent_sqm_all":   "pred-app-mef-dhup.csv",
    "rent_sqm_t1":    "pred-app12-mef-dhup.csv",
    "rent_sqm_t2":    "pred-app12-mef-dhup.csv",
    "rent_sqm_t3":    "pred-app3-mef-dhup.csv",
    "rent_sqm_t4":    "pred-app3-mef-dhup.csv",
    "rent_sqm_house": "pred-mai-mef-dhup.csv",
}

# source = "INSEE" : l'enum SerieSource n'a que DVF/INSEE/SCR/CALC — pas de valeur
# "DHUP"/"ANIL". Même convention que les autres sources gouvernementales non-DVF
# (population, logement, fiscalité...). Le vrai producteur (ANIL/DHUP "Carte des
# loyers") reste documenté dans la docstring.
RENT_SERIES_DEF = {
    "rent_sqm_all":   {"source": "INSEE", "frequency": "ANNUAL", "unit": "€/m²/mois", "chart_type": "LINE"},
    "rent_sqm_t1":    {"source": "INSEE", "frequency": "ANNUAL", "unit": "€/m²/mois", "chart_type": "LINE"},
    "rent_sqm_t2":    {"source": "INSEE", "frequency": "ANNUAL", "unit": "€/m²/mois", "chart_type": "LINE"},
    "rent_sqm_t3":    {"source": "INSEE", "frequency": "ANNUAL", "unit": "€/m²/mois", "chart_type": "LINE"},
    "rent_sqm_t4":    {"source": "INSEE", "frequency": "ANNUAL", "unit": "€/m²/mois", "chart_type": "LINE"},
    "rent_sqm_house": {"source": "INSEE", "frequency": "ANNUAL", "unit": "€/m²/mois", "chart_type": "LINE"},
}


def load_rent_csv(path: Path) -> dict[str, float]:
    """Parse one DHUP loyers CSV, return {insee_code: loypredm2 (€/m²/month)}.

    Arrondissement rows (Paris/Lyon/Marseille) collapse onto the commune
    principale via ARR_TO_COMMUNE; when several rows land on the same
    target commune, their values are averaged.
    """
    acc: dict[str, list[float]] = defaultdict(list)
    commune_n = 0
    maille_n = 0

    # Source files are Windows-1252 (LIBGEO has accented chars like "â"), not UTF-8.
    with open(path, encoding="cp1252") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)

        def col(name: str) -> int:
            return header.index(name)

        i_insee = col("INSEE_C")
        i_loyer = col("loypredm2")
        i_typpred = col("TYPPRED") if "TYPPRED" in header else None

        for row in reader:
            if not row:
                continue
            insee = row[i_insee].strip().zfill(5)
            insee = ARR_TO_COMMUNE.get(insee, insee)

            raw = row[i_loyer].strip()
            if not raw:
                continue
            try:
                val = float(raw.replace(",", "."))
            except ValueError:
                continue

            acc[insee].append(val)

            if i_typpred is not None:
                if row[i_typpred].strip() == "commune":
                    commune_n += 1
                elif row[i_typpred].strip() == "maille":
                    maille_n += 1

    if commune_n or maille_n:
        total = commune_n + maille_n
        print(
            f"    TYPPRED split: commune={commune_n} ({commune_n/total:.0%}), "
            f"maille={maille_n} ({maille_n/total:.0%})"
        )

    return {insee: sum(vals) / len(vals) for insee, vals in acc.items()}


def load_all_years(csv_dir: Path = CSV_DIR) -> dict[int, dict[str, dict[str, float]]]:
    """Single-snapshot source: degenerates to one "year" bucket
    (RENT_SNAPSHOT_YEAR) holding all 6 rent series, kept as {year: {...}}
    for shape-consistency with the multi-year seed_*.py scripts.
    """
    year_data: dict[str, dict[str, float]] = {}
    seen_files: dict[str, dict[str, float]] = {}

    for serie_name, filename in RENT_FILES.items():
        path = csv_dir / filename
        if not path.exists():
            print(f"  MISSING file for {serie_name}: {path}")
            continue
        if filename not in seen_files:
            print(f"  {filename} → year {RENT_SNAPSHOT_YEAR}")
            seen_files[filename] = load_rent_csv(path)
            print(f"    {len(seen_files[filename])} communes")
        year_data[serie_name] = seen_files[filename]

    return {RENT_SNAPSHOT_YEAR: year_data}


def check_serie_names_exist(cur, names: list[str]) -> set[str]:
    """Returns the subset of `names` already present in the Postgres
    "SerieName" enum. Missing names are a blocker for the real (non-dry-run)
    seed of that specific series — see report."""
    try:
        cur.execute('SELECT unnest(enum_range(NULL::"SerieName"))::text')
        existing = {r[0] for r in cur.fetchall()}
    except Exception as e:
        print(f"  WARNING: could not read SerieName enum ({e}); assuming all present")
        return set(names)
    return {n for n in names if n in existing}


def seed_series(
    conn,
    all_years: dict[int, dict[str, dict[str, float]]],
    dept_filter: str | None = None,
    dry_run: bool = False,
) -> None:
    from ..services.upload import upsert_series_and_timeseries

    cur = conn.cursor()
    clause = "WHERE department_code = %s" if dept_filter else ""
    params = (dept_filter,) if dept_filter else ()
    cur.execute(f"SELECT insee_code FROM cities {clause}", params)
    allowed = {row[0] for row in cur.fetchall()}

    all_names = list(RENT_SERIES_DEF.keys())
    present = check_serie_names_exist(cur, all_names)
    missing = [n for n in all_names if n not in present]
    if missing:
        print(f"  BLOCKER — missing from SerieName enum (will be SKIPPED on real run): {missing}")
        print("    → ask the owner of the Postgres schema repo to add these labels before the real seed.")
    cur.close()

    for year, year_data in sorted(all_years.items()):
        period = f"{year}-01-01"
        for serie_name, serie_meta in RENT_SERIES_DEF.items():
            rates = year_data.get(serie_name, {})
            rows: list[tuple] = [
                (insee_code, period, v)
                for insee_code, v in rates.items()
                if insee_code in allowed
            ]

            serie_def = {"name": serie_name, **serie_meta}
            print(f"  {serie_name}: {len(rows)} data points")

            if dry_run:
                print(f"    [DRY RUN] skipped")
                continue

            if serie_name not in present:
                print(f"    SKIPPED — '{serie_name}' not in SerieName enum yet")
                continue

            if rows:
                stats = upsert_series_and_timeseries(conn, serie_def, "city", rows, dry_run=False)
                print(f"    → {stats['series_upserted']} series, {stats['timeseries_inserted']} ts")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Loading DHUP loyers CSV files...")
    all_years = load_all_years()
    if not all_years or not any(all_years.values()):
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
