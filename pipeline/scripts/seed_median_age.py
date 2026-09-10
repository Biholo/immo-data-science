"""
Fills cities.median_age with an APPROXIMATE median age, computed by linear
interpolation over INSEE's grouped age brackets. There is no exact median
age in French open data at commune level — INSEE only publishes population
counts in uneven-width age buckets, so this is the standard grouped-median
estimator, not a true median computed from individual ages.

Source: csv/base-ic-evol-struct-pop/base-ic-evol-struct-pop-{year}.CSV
Latest year available: 2022. Header verified directly (not assumed) —
actual bucket columns present in that file:

  P{yy}_POP0002  [0, 3)    width 3
  P{yy}_POP0305  [3, 6)    width 3
  P{yy}_POP0610  [6, 11)   width 5
  P{yy}_POP1117  [11, 18)  width 7
  P{yy}_POP1824  [18, 25)  width 7
  P{yy}_POP2539  [25, 40)  width 15
  P{yy}_POP4054  [40, 55)  width 15
  P{yy}_POP5564  [55, 65)  width 10
  P{yy}_POP6579  [65, 80)  width 15
  P{yy}_POP80P   [80, ?)   open-ended — width ASSUMED as 20 (matches the
                 magnitude of neighboring buckets); only matters for the
                 rare commune whose median falls in this bucket at all
                 (French commune medians are overwhelmingly 30-55, well
                 below 80), so this assumption has negligible effect on
                 the reported values.

Method (standard grouped/interpolated median):
  N  = total population = sum of all bucket counts for the commune
  For the bucket containing the N/2-th individual (cumulative freq CF just
  before the bucket, bucket freq f, bucket lower bound L, bucket width h):

    median_age = L + ((N/2 - CF) / f) * h

  This assumes individuals are uniformly distributed within each bucket,
  which is an approximation — real single-year age distributions are not
  perfectly uniform within a bucket (e.g. slight concentration effects
  around round numbers), so treat cities.median_age as an ESTIMATE, not an
  exact statistic. Communes with zero population are skipped (NULL).

Uses ARR_TO_COMMUNE for Paris/Lyon/Marseille arrondissement -> commune
aggregation, same as the other seed_*_series.py scripts.

Run:
  python -m pipeline.scripts.seed_median_age [--dept 77] [--dry-run]
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
import psycopg2.extras
from dotenv import load_dotenv

from ..services.geo import ARR_TO_COMMUNE

load_dotenv()

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "base-ic-evol-struct-pop"

# (column suffix, lower bound, width). Order matters — must be ascending by L.
AGE_BUCKETS: list[tuple[str, int, int]] = [
    ("POP0002", 0, 3),
    ("POP0305", 3, 3),
    ("POP0610", 6, 5),
    ("POP1117", 11, 7),
    ("POP1824", 18, 7),
    ("POP2539", 25, 15),
    ("POP4054", 40, 15),
    ("POP5564", 55, 10),
    ("POP6579", 65, 15),
    ("POP80P", 80, 20),  # open-ended bucket, width assumed
]

# Sanity range mentioned in the task: French commune median ages roughly 30-55.
# Real communes can drift outside this (student towns skew young, retirement
# communes skew old), so this is used only for eyeballing --dry-run output,
# not as a hard validation filter.
MEDIAN_AGE_SANITY_MIN = 15.0
MEDIAN_AGE_SANITY_MAX = 75.0


def _latest_year_csv(csv_dir: Path = CSV_DIR) -> tuple[Path, int] | None:
    best: tuple[Path, int] | None = None
    for csv_file in csv_dir.glob("*.CSV"):
        m = re.search(r"(\d{4})", csv_file.name)
        if not m:
            continue
        year = int(m.group(1))
        if best is None or year > best[1]:
            best = (csv_file, year)
    return best


def _f(row: list[str], i: int) -> float:
    try:
        return float(row[i]) if row[i].strip() else 0.0
    except (ValueError, IndexError):
        return 0.0


def load_age_buckets(csv_dir: Path = CSV_DIR) -> tuple[dict[str, list[float]], int]:
    """Returns ({insee_code: [bucket_count, ...]} in AGE_BUCKETS order, year),
    aggregated IRIS -> commune."""
    found = _latest_year_csv(csv_dir)
    if not found:
        return {}, 0
    path, year = found
    yy = str(year)[-2:]
    n_buckets = len(AGE_BUCKETS)
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0] * n_buckets)

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        i_com = header.index("COM")
        col_indices = [header.index(f"P{yy}_{suffix}") for suffix, _, _ in AGE_BUCKETS]

        for row in reader:
            com = row[i_com].strip().zfill(5)
            com = ARR_TO_COMMUNE.get(com, com)
            a = acc[com]
            for bi, ci in enumerate(col_indices):
                a[bi] += _f(row, ci)

    return dict(acc), year


def compute_median_age(bucket_counts: list[float]) -> float | None:
    """Grouped-median interpolation: L + ((N/2 - CF) / f) * h."""
    n = sum(bucket_counts)
    if n <= 0:
        return None

    target = n / 2
    cf = 0.0
    for (_, lower, width), f in zip(AGE_BUCKETS, bucket_counts):
        if f > 0 and target <= cf + f:
            return lower + ((target - cf) / f) * width
        cf += f

    # target fell past the last bucket (shouldn't happen since cf sums to n) —
    # fall back to the last bucket's lower bound.
    return AGE_BUCKETS[-1][1]


def compute_all_median_ages(buckets_by_city: dict[str, list[float]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for insee, counts in buckets_by_city.items():
        median = compute_median_age(counts)
        if median is not None:
            out[insee] = round(median)
    return out


def _fetch_city_id_map(cur, dept: str | None) -> dict[str, str]:
    dept_clause = "WHERE department_code = %s" if dept else ""
    params = [dept] if dept else []
    cur.execute(f"SELECT insee_code, id FROM cities {dept_clause}", params)
    return {r[0]: r[1] for r in cur.fetchall()}


def apply_updates(conn, cur, median_age: dict[str, int], id_map: dict[str, str], dry_run: bool) -> int:
    rows = [
        (median_age[insee], id_map[insee])
        for insee in median_age
        if insee in id_map
    ]

    if dry_run:
        print(f"  [DRY RUN] Would update {len(rows)} cities")
        if rows:
            r = rows[0]
            print(f"  Sample city_id={r[1]}: median_age={r[0]}")
        return len(rows)

    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE cities SET
            median_age = data.median_age,
            updated_at = NOW()
        FROM (VALUES %s) AS data(median_age, id)
        WHERE cities.id = data.id
        """,
        rows,
        template="(%s::integer, %s)",
    )
    conn.commit()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to dept code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    print("Loading age bucket columns from base-ic-evol-struct-pop...")
    buckets_by_city, year = load_age_buckets()
    print(f"  year {year}: {len(buckets_by_city)} communes")

    print("Computing approximate median age (grouped-median interpolation)...")
    median_age = compute_all_median_ages(buckets_by_city)
    print(f"  {len(median_age)} communes")

    if median_age:
        sample_vals = list(median_age.values())[:10]
        print(f"  Sample values: {sample_vals}")
        out_of_range = sum(
            1 for v in median_age.values()
            if not (MEDIAN_AGE_SANITY_MIN <= v <= MEDIAN_AGE_SANITY_MAX)
        )
        if out_of_range:
            print(f"  ! {out_of_range} communes outside sanity range "
                  f"[{MEDIAN_AGE_SANITY_MIN}, {MEDIAN_AGE_SANITY_MAX}] — worth a manual look")

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    try:
        print("Fetching city id map...")
        id_map = _fetch_city_id_map(cur, args.dept)
        print(f"  {len(id_map)} cities")

        print("Applying updates...")
        n = apply_updates(conn, cur, median_age, id_map, args.dry_run)
        print(f"Done. {n} cities updated.")

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
