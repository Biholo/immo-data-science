"""
One-off reshape: INSEE "Historique des populations communales" wide XLSX →
per-year long CSVs (mirrors the csv/<dataset>/<dataset>-{year}.CSV convention
used by the other seed_*_series.py scripts).

Source (download manually, see https://www.insee.fr/fr/statistiques/3698339):
  csv/population-historique/raw_histo_pop.xlsx
  sheet "pop_1876_2023", header row 6, data from row 7.
  Columns: CODGEO, REG, DEP, LIBGEO, then one population column per census
  "vintage": PMUN{year} for 2006-2023 (recensement annuel, RP legal
  populations, current methodology), PSDC{year} for 1962-1999 ("population
  sans doubles comptes" - older methodology), PTOT{year} for 1876-1954
  ("population totale" - pre-war census methodology).

Why we only keep PMUN2006-PMUN2023 (18 years):
  PSDC and PTOT use different population-counting conventions than the
  modern PMUN ("population municipale") series and are not directly
  comparable to it (INSEE's own documentation flags this discontinuity).
  Mixing them into one "population_history" series would produce
  misleading jumps at the 1999/2006 boundary. PMUN2006-2023 already gives
  18 years of consistent, comparable annual-census history per commune,
  which satisfies the "long-run" goal without the comparability landmine.
  (This is an explicit, documented choice - see task instructions: "skip
  years with too much missing/incomparable geography ... just use the
  PMUN{year} columns present".)

Geography: the raw file lists Paris/Lyon/Marseille as individual
arrondissements (75101-75120, 69381-69389, 13201-13216), not as their
parent commune. We fold these into the parent commune code (75056, 69123,
13055) via ARR_TO_COMMUNE, summing population across arrondissements,
exactly like seed_pop_series.py does for the IRIS-level source.

Output: csv/population-historique/population-historique-{year}.CSV
  Columns: COM;POPULATION  (";" delimiter, utf-8-sig, to match the other
  per-year CSV files already in this repo).

Run once:
  python -m pipeline.scripts.prep_population_history
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import openpyxl

from ..services.geo import ARR_TO_COMMUNE

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "population-historique"
RAW_XLSX = CSV_DIR / "raw_histo_pop.xlsx"

SHEET_NAME = "pop_1876_2023"
HEADER_ROW = 6  # 1-indexed row holding CODGEO / PMUN{year} / ... headers

# Only the modern, mutually-comparable "population municipale" vintages.
MIN_PMUN_YEAR = 2006
MAX_PMUN_YEAR = 2023


def reshape(raw_xlsx: Path = RAW_XLSX, out_dir: Path = CSV_DIR) -> dict[int, int]:
    """Read the wide INSEE xlsx and write one COM;POPULATION CSV per PMUN year.

    Returns {year: commune_count} for the years actually written.
    """
    wb = openpyxl.load_workbook(raw_xlsx, read_only=True, data_only=True)
    ws = wb[SHEET_NAME]

    rows_iter = ws.iter_rows(min_row=HEADER_ROW, values_only=True)
    header = list(next(rows_iter))

    col_idx = {name: i for i, name in enumerate(header) if name}
    i_codgeo = col_idx["CODGEO"]

    year_cols: dict[int, int] = {}
    for year in range(MIN_PMUN_YEAR, MAX_PMUN_YEAR + 1):
        key = f"PMUN{year}"
        if key in col_idx:
            year_cols[year] = col_idx[key]

    if not year_cols:
        raise RuntimeError(f"No PMUN{{year}} columns found in header: {header}")

    # {year: {commune: population}}
    acc: dict[int, dict[str, float]] = {y: defaultdict(float) for y in year_cols}

    for row in rows_iter:
        codgeo = row[i_codgeo]
        if not codgeo:
            continue
        com = str(codgeo).strip().zfill(5)
        com = ARR_TO_COMMUNE.get(com, com)

        for year, idx in year_cols.items():
            val = row[idx]
            if val is None or val == "":
                continue
            try:
                pop = float(val)
            except (TypeError, ValueError):
                continue
            acc[year][com] += pop

    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[int, int] = {}
    for year in sorted(acc):
        communes = acc[year]
        out_path = out_dir / f"population-historique-{year}.CSV"
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("COM;POPULATION\n")
            for com in sorted(communes):
                pop = communes[com]
                f.write(f"{com};{int(round(pop))}\n")
        counts[year] = len(communes)
        print(f"  wrote {out_path.name}: {len(communes)} communes")

    return counts


def main() -> None:
    print(f"Reading {RAW_XLSX} ...")
    counts = reshape()
    print(f"Done. {len(counts)} years written: {sorted(counts)}")


if __name__ == "__main__":
    main()
