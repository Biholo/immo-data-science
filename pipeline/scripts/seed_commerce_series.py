"""
Seed yearly commerce/retail-equipment time series from INSEE BPE (Base
Permanente des Équipements) "Commerce" domain (FACILITY_DOM / DOM == "B").

Source: csv/bpe-commerces/bpe-commerces-{year}.CSV

INSEE ships the BPE "ensemble" file in two different shapes depending on
edition. Both are handled here, detected from the header:

  - 2025-style ("GEO_OBJECT" in header, DS_BPE dissemination format):
    long/tidy format, already pre-aggregated to subdomain totals
    (FACILITY_TYPE == "_T"), commune codes already consolidated
    (Paris/Lyon/Marseille appear as one row each, e.g. GEO="75056").
    Columns used: GEO, GEO_OBJECT, FACILITY_DOM, FACILITY_SDOM, OBS_VALUE.

  - 2021-style ("DEPCOM" in header, bpeYY_ensemble format):
    one row per commune x TYPEQU (equipment type), needs summing per
    subdomain. Commune codes are NOT consolidated — Paris/Lyon/Marseille
    appear as arrondissements (75101, 69381, 13201, ...) and must be
    folded via ARR_TO_COMMUNE.
    Columns used: DEPCOM, DOM, SDOM, TYPEQU, NB_EQUIP.

Commerce subdomains (official INSEE 2nd-level nomenclature, domain B):
  B1 = Grandes surfaces (hyper/supermarkets, superstores)
  B2 = Commerces alimentaires (bakeries, butchers, grocery, ...)
  B3 = Commerces spécialisés non-alimentaires (clothing, furniture,
       books, hardware, ...)
There is no B4+; every domain-B equipment type falls under B1/B2/B3, so
retail_count is simply their sum.

Series produced (city level, frequency=yearly):
  retail_count               = B1 + B2 + B3                 (count)
  retail_share_large_format  = B1 / retail_count             (ratio)
  retail_share_grocery       = B2 / retail_count             (ratio)
  retail_share_specialty     = B3 / retail_count             (ratio)

retail_density (per 1000 inhabitants) was skipped: it would require
joining against the population series seeded by seed_pop_series.py,
which lives in a separate DB table (series/timeseries) rather than a
column on `cities`. Left as raw retail_count for now.

Run:
  python -m pipeline.scripts.seed_commerce_series [--dept 77] [--dry-run]
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

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "bpe-commerces"

COMMERCE_SERIES_DEF = {
    "retail_count":              {"source": "INSEE", "frequency": "ANNUAL", "unit": "count", "chart_type": "BAR"},
    "retail_share_large_format": {"source": "INSEE", "frequency": "ANNUAL", "unit": "ratio", "chart_type": "LINE"},
    "retail_share_grocery":      {"source": "INSEE", "frequency": "ANNUAL", "unit": "ratio", "chart_type": "LINE"},
    "retail_share_specialty":    {"source": "INSEE", "frequency": "ANNUAL", "unit": "ratio", "chart_type": "LINE"},
}

SUBDOMS = ("B1", "B2", "B3")


def _f(val: str) -> float:
    try:
        return float(val) if val.strip() else 0.0
    except (ValueError, AttributeError):
        return 0.0


def load_csv_year(path: Path, year: int) -> dict[str, dict[str, float | None]]:
    """Aggregate BPE commerce rows -> B1/B2/B3 totals per insee_code."""
    # [B1, B2, B3]
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)

        def col(name: str) -> int:
            return header.index(name)

        if "GEO_OBJECT" in header:
            # 2025-style DS_BPE long format, pre-filtered to
            # GEO_OBJECT=COM, FACILITY_DOM=B, FACILITY_TYPE=_T
            i_geo = col("GEO")
            i_geo_obj = col("GEO_OBJECT")
            i_sdom = col("FACILITY_SDOM")
            i_val = col("OBS_VALUE")

            for row in reader:
                if row[i_geo_obj] != "COM":
                    continue
                sdom = row[i_sdom]
                if sdom not in SUBDOMS:
                    continue  # skip the "_T" grand-total row, we derive it
                com = row[i_geo].strip().zfill(5)
                com = ARR_TO_COMMUNE.get(com, com)
                acc[com][SUBDOMS.index(sdom)] += _f(row[i_val])

        elif "DEPCOM" in header:
            # older bpeYY_ensemble wide format, one row per commune x TYPEQU,
            # commune codes NOT consolidated (Paris/Lyon/Marseille as
            # arrondissements) -> fold via ARR_TO_COMMUNE.
            i_depcom = col("DEPCOM")
            i_sdom = col("SDOM")
            i_val = col("NB_EQUIP")

            for row in reader:
                sdom = row[i_sdom]
                if sdom not in SUBDOMS:
                    continue
                com = row[i_depcom].strip().zfill(5)
                com = ARR_TO_COMMUNE.get(com, com)
                acc[com][SUBDOMS.index(sdom)] += _f(row[i_val])

        else:
            raise ValueError(f"Unrecognized BPE CSV format in {path.name}: {header}")

    out = {}
    for com, (b1, b2, b3) in acc.items():
        total = b1 + b2 + b3
        if total <= 0:
            continue
        out[com] = {
            "retail_count":              total,
            "retail_share_large_format": b1 / total,
            "retail_share_grocery":      b2 / total,
            "retail_share_specialty":    b3 / total,
        }
    return out


def load_all_years(csv_dir: Path = CSV_DIR) -> dict[int, dict[str, dict[str, float | None]]]:
    all_years: dict[int, dict] = {}
    for csv_file in sorted(csv_dir.glob("*.CSV")):
        m = re.search(r"(\d{4})", csv_file.name)
        if not m:
            continue
        year = int(m.group(1))
        print(f"  {csv_file.name} -> year {year}")
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

    for serie_name, serie_meta in COMMERCE_SERIES_DEF.items():
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
            print(f"    -> {stats['series_upserted']} series, {stats['timeseries_inserted']} ts")
        elif dry_run:
            print(f"    [DRY RUN] skipped")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Loading BPE commerce CSV files...")
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
