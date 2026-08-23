"""
Fills cities snapshot fields derived from already-seeded timeseries/columns:

  tenant_rate           = 1 - latest owner_rate            (needs seed_logement_series.py run)
  demographic_growth_5y = (population[Y] / population[Y-5] - 1) × 100
                                                             (needs seed_pop_series.py run)
  employment_growth     = (active_population[Y] / active_population[Y-5] - 1) × 100
                                                             (needs seed_employment_series.py run)

high_demand_zone is NOT handled here — it's set directly in seed_housing_zone.py
alongside housing_zone (same source file, no timeseries lookup needed).

Growth fields are null when a city has no data point exactly 5 years before its
latest one (e.g. active_population only spans 2017-2022 → only cities with both
2017 and 2022 get employment_growth).

Run after seed_logement_series.py / seed_pop_series.py / seed_employment_series.py:
  python -m pipeline.scripts.seed_dashboard_fields [--dept 77] [--dry-run]
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

GROWTH_WINDOW_YEARS = 5


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


def compute_tenant_rate(owner_rate_series: dict[str, list]) -> dict[str, float]:
    """tenant_rate = 1 - latest owner_rate, per city."""
    out: dict[str, float] = {}
    for city_id, pts in owner_rate_series.items():
        if not pts:
            continue
        latest_owner_rate = pts[-1][1]
        out[city_id] = round(1 - latest_owner_rate, 4)
    return out


def compute_growth_5y(series: dict[str, list]) -> dict[str, float]:
    """(value[latest_year] / value[latest_year - 5] - 1) × 100, per city."""
    out: dict[str, float] = {}
    for city_id, pts in series.items():
        if not pts:
            continue
        by_year = {ts.year: v for ts, v in pts}
        latest_year = max(by_year)
        base_year = latest_year - GROWTH_WINDOW_YEARS
        base = by_year.get(base_year)
        if base is None or base <= 0:
            continue
        out[city_id] = round((by_year[latest_year] / base - 1) * 100, 2)
    return out


def apply_updates(
    conn,
    cur,
    tenant_rate: dict[str, float],
    demographic_growth_5y: dict[str, float],
    employment_growth: dict[str, float],
    dry_run: bool,
) -> int:
    city_ids = set(tenant_rate) | set(demographic_growth_5y) | set(employment_growth)
    rows = [
        (
            tenant_rate.get(cid),
            demographic_growth_5y.get(cid),
            employment_growth.get(cid),
            cid,
        )
        for cid in city_ids
    ]

    if dry_run:
        print(f"  [DRY RUN] Would update {len(rows)} cities")
        if rows:
            r = rows[0]
            print(f"  Sample city_id={r[3]}: tenant_rate={r[0]}, demographic_growth_5y={r[1]}, employment_growth={r[2]}")
        return len(rows)

    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE cities SET
            tenant_rate           = data.tenant_rate,
            demographic_growth_5y = data.demographic_growth_5y,
            employment_growth     = data.employment_growth,
            updated_at            = NOW()
        FROM (VALUES %s) AS data(tenant_rate, demographic_growth_5y, employment_growth, id)
        WHERE cities.id = data.id
        """,
        rows,
        template="(%s::double precision, %s::double precision, %s::double precision, %s)",
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
        print("Fetching owner_rate timeseries...")
        owner_rate_series = _fetch_city_timeseries(cur, "owner_rate", args.dept)
        print(f"  {len(owner_rate_series)} cities with owner_rate data")

        print("Fetching population timeseries...")
        population_series = _fetch_city_timeseries(cur, "population", args.dept)
        print(f"  {len(population_series)} cities with population data")

        print("Fetching active_population timeseries...")
        active_population_series = _fetch_city_timeseries(cur, "active_population", args.dept)
        print(f"  {len(active_population_series)} cities with active_population data")

        print("Computing tenant_rate...")
        tenant_rate = compute_tenant_rate(owner_rate_series)
        print(f"  {len(tenant_rate)} cities")

        print("Computing demographic_growth_5y...")
        demographic_growth_5y = compute_growth_5y(population_series)
        print(f"  {len(demographic_growth_5y)} cities")

        print("Computing employment_growth...")
        employment_growth = compute_growth_5y(active_population_series)
        print(f"  {len(employment_growth)} cities")

        print("Applying updates...")
        n = apply_updates(conn, cur, tenant_rate, demographic_growth_5y, employment_growth, args.dry_run)
        print(f"Done. {n} cities updated.")

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
