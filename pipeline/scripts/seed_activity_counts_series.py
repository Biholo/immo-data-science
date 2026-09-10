"""
Fills cities.unemployed_count and cities.retired_count as absolute-count
snapshots (latest year available), sourced directly from INSEE IC CSVs
already present in this repo. No SerieName enum values are involved — these
are direct UPDATEs onto cities snapshot columns, same style as denormalize.py.

  unemployed_count = SUM(P{yy}_CHOM1564)   — chômeurs 15-64 ans (absolute count)
    from csv/base-ic-activite-residents/base-ic-activite-residents-{year}.CSV
    latest year available: 2021.

  retired_count = SUM(P{yy}_POP65P)        — population 65 ans et plus (absolute count)
    from csv/base-ic-evol-struct-pop/base-ic-evol-struct-pop-{year}.CSV
    latest year available: 2022.

IMPORTANT CAVEAT / proxy decision (read before trusting retired_count):
  The IC-Activité-Résidents table also has a "retraités/préretraités" column,
  P{yy}_RETR1564 — but it is scoped to residents aged 15-64 ONLY. Since the
  legal retirement age in France is ~62-64, RETR1564 only counts early/
  pre-retirees and MISSES essentially the entire true retiree population
  (65+), badly undercounting true "number of retirees" in a commune (a
  handful to a few dozen per commune, vs. a France-wide retiree population
  in the millions). This repo's cities.retired_count column was previously
  seeded that way by seed_rp.py (P22_RETR1564, from the 2022 RP xlsx) — this
  script INTENTIONALLY OVERWRITES those values with a better proxy.

  We use P{yy}_POP65P (total population aged 65+) instead. This is NOT a
  count of retirees either — some 65+ residents still work, and some under-65
  residents are already retired — but the vast majority (survey estimates:
  ~95%+) of the 65+ population in France is retired, so POP65P tracks the
  true magnitude and geographic distribution of "retired population" far
  more faithfully than RETR1564 does. Treat cities.retired_count as an
  "approximate retired population, proxied by residents aged 65+" — NOT an
  exact retiree count. This traceability note is deliberate: if the schema
  owner wants a differently-named column for this (e.g. `pop_65_plus`)
  instead of overloading `retired_count`, that's a naming call outside this
  script's scope.

  unemployed_count has no such caveat: CHOM1564 (chômeurs 15-64) covers
  essentially all working-age job seekers, so it is used as-is (job seekers
  65+ are a negligible population in France and not tracked separately at
  commune level in this source).

Uses ARR_TO_COMMUNE for Paris/Lyon/Marseille arrondissement -> commune
aggregation, same as seed_employment_series.py / seed_pop_series.py.

Run:
  python -m pipeline.scripts.seed_activity_counts_series [--dept 77] [--dry-run]
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

ACTIVITE_CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "base-ic-activite-residents"
POP_CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "base-ic-evol-struct-pop"


def _latest_year_csv(csv_dir: Path) -> tuple[Path, int] | None:
    """Returns (path, year) for the CSV file with the highest year in its name."""
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


def load_unemployed_count(csv_dir: Path = ACTIVITE_CSV_DIR) -> tuple[dict[str, int], int]:
    """Returns ({insee_code: unemployed_count}, year), aggregated IRIS -> commune."""
    found = _latest_year_csv(csv_dir)
    if not found:
        return {}, 0
    path, year = found
    yy = str(year)[-2:]
    acc: dict[str, float] = defaultdict(float)

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        i_com = header.index("COM")
        i_chom = header.index(f"P{yy}_CHOM1564")

        for row in reader:
            com = row[i_com].strip().zfill(5)
            com = ARR_TO_COMMUNE.get(com, com)
            acc[com] += _f(row, i_chom)

    return {com: round(v) for com, v in acc.items()}, year


def load_retired_count_proxy(csv_dir: Path = POP_CSV_DIR) -> tuple[dict[str, int], int]:
    """Returns ({insee_code: pop_65_plus}, year), aggregated IRIS -> commune.

    See module docstring: this is POP65P (population 65+), used as the
    proxy for "retired population" — NOT a direct retiree count.
    """
    found = _latest_year_csv(csv_dir)
    if not found:
        return {}, 0
    path, year = found
    yy = str(year)[-2:]
    acc: dict[str, float] = defaultdict(float)

    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        i_com = header.index("COM")
        i_pop65 = header.index(f"P{yy}_POP65P")

        for row in reader:
            com = row[i_com].strip().zfill(5)
            com = ARR_TO_COMMUNE.get(com, com)
            acc[com] += _f(row, i_pop65)

    return {com: round(v) for com, v in acc.items()}, year


def _fetch_city_id_map(cur, dept: str | None) -> dict[str, str]:
    """Returns {insee_code: city_id}."""
    dept_clause = "WHERE department_code = %s" if dept else ""
    params = [dept] if dept else []
    cur.execute(f"SELECT insee_code, id FROM cities {dept_clause}", params)
    return {r[0]: r[1] for r in cur.fetchall()}


def apply_updates(
    conn,
    cur,
    unemployed_count: dict[str, int],
    retired_count: dict[str, int],
    id_map: dict[str, str],
    dry_run: bool,
) -> int:
    insee_codes = (set(unemployed_count) | set(retired_count)) & set(id_map)
    rows = [
        (
            unemployed_count.get(insee),
            retired_count.get(insee),
            id_map[insee],
        )
        for insee in insee_codes
    ]

    if dry_run:
        print(f"  [DRY RUN] Would update {len(rows)} cities")
        if rows:
            r = rows[0]
            print(f"  Sample city_id={r[2]}: unemployed_count={r[0]}, retired_count={r[1]}")
        return len(rows)

    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE cities SET
            unemployed_count = data.unemployed_count,
            retired_count    = data.retired_count,
            updated_at       = NOW()
        FROM (VALUES %s) AS data(unemployed_count, retired_count, id)
        WHERE cities.id = data.id
        """,
        rows,
        template="(%s::integer, %s::integer, %s)",
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

    print("Loading unemployed_count (CHOM1564) from base-ic-activite-residents...")
    unemployed_count, u_year = load_unemployed_count()
    print(f"  year {u_year}: {len(unemployed_count)} communes")

    print("Loading retired_count proxy (POP65P) from base-ic-evol-struct-pop...")
    retired_count, r_year = load_retired_count_proxy()
    print(f"  year {r_year}: {len(retired_count)} communes")

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    try:
        print("Fetching city id map...")
        id_map = _fetch_city_id_map(cur, args.dept)
        print(f"  {len(id_map)} cities")

        print("Applying updates...")
        n = apply_updates(conn, cur, unemployed_count, retired_count, id_map, args.dry_run)
        print(f"Done. {n} cities updated.")

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
