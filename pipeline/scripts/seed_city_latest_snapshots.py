"""
Denormalise "latest timeseries value -> cities snapshot column" for series that
have no dedicated denorm step of their own.

  cities.owner_rate               <- latest owner_rate            (series = ratio 0-1 -> column in PERCENT)
  cities.vacancy_rate             <- latest vacancy_rate          (series = ratio 0-1 -> column in PERCENT)
  cities.unemployment_rate        <- latest unemployment_rate     (series = ratio 0-1 -> column in PERCENT)
  cities.median_income            <- latest median_income         (euros/an)
  cities.annual_company_creations <- latest company_creations     (count, rounded int)

Units: the timeseries store ratios (0.1055 = 10.55 %) but every rate column on `cities` is in
PERCENT (like demographic_growth_5y, gross_yield, avg_property_tax) because the frontend prints
them as-is with a "%" suffix. Ratio -> percent happens here (rounding mode "pct").

Prereqs: the matching seed_*_series scripts must have run for real first
(seed_logement_series, seed_rp_series, seed_median_income_series,
seed_company_creations_series).
Each target column is written only if it exists on `cities` (checked via
information_schema.columns) — missing columns are logged and skipped, same
convention as seed_years_to_buy.py / seed_housing_effort_rate.py.

Run:
  python -m pipeline.scripts.seed_city_latest_snapshots [--dept 77] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

# serie_name -> (cities column, pg cast, python rounding)
SNAPSHOTS: dict[str, tuple[str, str, str]] = {
    "owner_rate":        ("owner_rate", "double precision", "pct"),
    "vacancy_rate":      ("vacancy_rate", "double precision", "pct"),
    "unemployment_rate": ("unemployment_rate", "double precision", "pct"),
    "median_income":     ("median_income", "double precision", "float2"),
    "company_creations": ("annual_company_creations", "integer", "int"),
}


def _fetch_city_timeseries(cur, serie_name: str, dept: str | None) -> dict[str, list[tuple[date, float]]]:
    """Returns {city_id: [(timestamp, value), ...]} sorted ASC."""
    dept_clause = "AND c.department_code = %s" if dept else ""
    params = [serie_name]
    if dept:
        params.append(dept)

    cur.execute(f"""
        SELECT c.id, t.timestamp, t.value
        FROM series s
        JOIN timeseries t ON t.serie_id = s.id
        JOIN cities c ON s.city_id = c.id
        WHERE s.name = %s
          AND s.city_id IS NOT NULL
          {dept_clause}
        ORDER BY c.id, t.timestamp ASC
    """, params)

    result: dict[str, list] = defaultdict(list)
    for city_id, ts, val in cur.fetchall():
        result[city_id].append((ts, val))
    return result


def _round(value: float, mode: str) -> float | int:
    if mode == "int":
        return int(round(value))
    if mode == "float2":
        return round(value, 2)
    if mode == "pct":          # ratio 0-1 -> percent
        return round(value * 100, 2)
    return round(value, 4)


def _existing_columns(cur, columns: list[str]) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'cities' AND column_name = ANY(%s)",
        (columns,),
    )
    return {r[0] for r in cur.fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to dept code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    try:
        wanted_cols = [col for (col, _, _) in SNAPSHOTS.values()]
        present = _existing_columns(cur, wanted_cols)
        missing = set(wanted_cols) - present
        if missing:
            print(f"  cities columns absent — skipped: {sorted(missing)}")

        for serie_name, (col, cast, rmode) in SNAPSHOTS.items():
            if col not in present:
                continue

            series = _fetch_city_timeseries(cur, serie_name, args.dept)
            rows = [
                (_round(pts[-1][1], rmode), city_id)
                for city_id, pts in series.items()
                if pts
            ]
            print(f"  {serie_name} -> cities.{col}: {len(rows)} cities")
            if not rows:
                print(f"    (no {serie_name} timeseries — has its seed_*_series run for real?)")
                continue

            if args.dry_run:
                print(f"    [DRY RUN] sample: {rows[0]}")
                continue

            psycopg2.extras.execute_values(
                cur,
                f"""
                UPDATE cities SET {col} = data.val, updated_at = NOW()
                FROM (VALUES %s) AS data(val, id)
                WHERE cities.id = data.id
                """,
                rows,
                template=f"(%s::{cast}, %s)",
            )
            conn.commit()
            print(f"    -> {len(rows)} rows updated")

        print("Done.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
