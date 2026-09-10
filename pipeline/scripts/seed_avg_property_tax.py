"""
Fills cities.avg_property_tax from the already-seeded property_tax_rate
timeseries (city-level, see seed_fiscalite_series.py).

  avg_property_tax = latest property_tax_rate datapoint per city (%)

Mirrors pipeline/scripts/seed_dashboard_fields.py's structure (fetch a city
timeseries, take the latest point, UPDATE the cities snapshot column).

PREREQUISITE: "property_tax_rate" must exist as a Postgres `SerieName` enum
value AND pipeline.scripts.seed_fiscalite_series must have been run for real
(not just --dry-run) before this script has anything to read. Check with:
  SELECT enum_range(NULL::"SerieName")
As of 2026-09-10 this enum value does NOT exist yet in the target DB, so this
script is expected to find 0 rows to update until the schema owner adds it
and seed_fiscalite_series is run for real.

Run after seed_fiscalite_series.py:
  python -m pipeline.scripts.seed_avg_property_tax [--dept 77] [--dry-run]
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

SERIE_NAME = "property_tax_rate"


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


def compute_avg_property_tax(rate_series: dict[str, list]) -> dict[str, float]:
    """avg_property_tax = latest property_tax_rate, per city."""
    out: dict[str, float] = {}
    for city_id, pts in rate_series.items():
        if not pts:
            continue
        out[city_id] = round(pts[-1][1], 2)
    return out


def apply_updates(conn, cur, avg_property_tax: dict[str, float], dry_run: bool) -> int:
    rows = [(value, city_id) for city_id, value in avg_property_tax.items()]

    if dry_run:
        print(f"  [DRY RUN] Would update {len(rows)} cities")
        if rows:
            r = rows[0]
            print(f"  Sample city_id={r[1]}: avg_property_tax={r[0]}")
        return len(rows)

    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE cities SET
            avg_property_tax = data.avg_property_tax,
            updated_at       = NOW()
        FROM (VALUES %s) AS data(avg_property_tax, id)
        WHERE cities.id = data.id
        """,
        rows,
        template="(%s::double precision, %s)",
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

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    try:
        print(f"Fetching {SERIE_NAME} timeseries...")
        rate_series = _fetch_city_timeseries(cur, SERIE_NAME, args.dept)
        print(f"  {len(rate_series)} cities with {SERIE_NAME} data")

        print("Computing avg_property_tax...")
        avg_property_tax = compute_avg_property_tax(rate_series)
        print(f"  {len(avg_property_tax)} cities")
        if avg_property_tax:
            sample = list(avg_property_tax.values())[:5]
            print(f"  Sample values: {sample}")
        else:
            print(f"  No data found — is {SERIE_NAME} in the SerieName enum and has "
                  f"seed_fiscalite_series.py been run for real?")

        print("Applying updates...")
        n = apply_updates(conn, cur, avg_property_tax, args.dry_run)
        print(f"Done. {n} cities updated.")

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
