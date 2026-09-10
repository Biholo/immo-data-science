"""
DERIVED series: `years_to_buy` — how many years of median income the full
purchase price of a typical dwelling represents (a house-price-to-income
ratio, the "prix/revenu" affordability metric):

    years_to_buy =
        (price_sqm_all × TYPICAL_SURFACE_SQM)   [€, full price of typical dwelling]
        ────────────────────────────────────
        median_income                          [€ / year]

Inputs (both city-level, already-seeded series):
  price_sqm_all  — DVF-derived €/m² sale price, quarterly; latest quarter
                   used per city (already seeded, ~159k timeseries rows)
  median_income  — INSEE Filosofi 2021, annual, € (seeded by
                   seed_median_income_series.py)

APPROXIMATIONS (documented deliberately):
  * median_income is Filosofi MED_SL = *niveau de vie médian* (median
    disposable income per consumption unit), not household income. This
    inflates years_to_buy vs a household-income ratio; use it as a
    cross-commune comparison index.
  * TYPICAL_SURFACE_SQM is imported from seed_housing_effort_rate.py (70 m²,
    rationale documented there) so both affordability metrics use one
    consistent reference dwelling.

Output series (city level):
  years_to_buy   unit "years", frequency ANNUAL, source "CALC",
                 chart_type "LINE"
  one datapoint per city where BOTH inputs exist.

"As of" date: MEDIAN_INCOME_PERIOD (2021-01-01) — income is the limiting /
defining vintage (same convention as seed_gross_yield.py).

`cities` denormalisation: `cities.years_to_buy` is written IF it exists
(checked via information_schema.columns). It does NOT currently exist in the
schema — the UPDATE is skipped silently and noted.

Prereqs for a REAL seed: price_sqm_all (already seeded) and median_income
(run seed_median_income_series.py for real first).

Run:
  python -m pipeline.scripts.seed_years_to_buy [--dept 77] [--dry-run]
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

from .seed_median_income_series import MEDIAN_INCOME_PERIOD
from .seed_housing_effort_rate import TYPICAL_SURFACE_SQM

SERIE_NAME = "years_to_buy"
SERIE_DEF = {
    "name": SERIE_NAME,
    "source": "CALC",
    "frequency": "ANNUAL",
    "unit": "years",
    "chart_type": "LINE",
}
DENORM_COL = "years_to_buy"

# Plausibility band for a per-commune price/income ratio — values outside
# are printed for inspection (usually a unit bug: €/m² read as full price,
# or annual vs monthly income).
SANE_MIN, SANE_MAX = 1.0, 40.0


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


def _fetch_city_id_to_insee(cur, dept: str | None) -> dict[str, str]:
    dept_clause = "AND department_code = %s" if dept else ""
    params = [dept] if dept else []
    cur.execute(f"SELECT id, insee_code FROM cities WHERE insee_code IS NOT NULL {dept_clause}", params)
    return {r[0]: r[1] for r in cur.fetchall()}


def check_serie_names_exist(cur, names: list[str]) -> set[str]:
    try:
        cur.execute('SELECT unnest(enum_range(NULL::"SerieName"))::text')
        existing = {r[0] for r in cur.fetchall()}
    except Exception as e:
        print(f"  WARNING: could not read SerieName enum ({e}); assuming all present")
        return set(names)
    return {n for n in names if n in existing}


def check_columns_exist(cur, table: str, columns: list[str]) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s AND column_name = ANY(%s)",
        (table, columns),
    )
    return {r[0] for r in cur.fetchall()}


def compute_years_to_buy(
    price_series: dict[str, list],
    income_series: dict[str, list],
) -> dict[str, float]:
    """Returns {city_id: years_to_buy} for cities present in both inputs."""
    out: dict[str, float] = {}
    for city_id, price_pts in price_series.items():
        income_pts = income_series.get(city_id)
        if not price_pts or not income_pts:
            continue
        price_sqm = price_pts[-1][1]
        income_year = income_pts[-1][1]
        if not income_year or income_year <= 0 or not price_sqm or price_sqm <= 0:
            continue
        dwelling_price = price_sqm * TYPICAL_SURFACE_SQM
        out[city_id] = round(dwelling_price / income_year, 3)
    return out


def _report_outliers(values_by_city: dict[str, float], id_to_insee: dict[str, str]) -> None:
    lo = {cid: v for cid, v in values_by_city.items() if v < SANE_MIN}
    hi = {cid: v for cid, v in values_by_city.items() if v > SANE_MAX}
    if lo or hi:
        print(f"  OUTLIERS outside [{SANE_MIN}, {SANE_MAX}]: {len(lo)} low, {len(hi)} high")
        for cid, v in list(lo.items())[:5] + list(hi.items())[:5]:
            print(f"    insee={id_to_insee.get(cid, cid)}  value={v}")


def apply_city_updates(conn, cur, values_by_city: dict[str, float], dry_run: bool) -> int:
    present = check_columns_exist(cur, "cities", [DENORM_COL])
    if DENORM_COL not in present:
        print(f"  cities.{DENORM_COL} column absent — skipping denormalisation (noted).")
        return 0

    rows = [(v, cid) for cid, v in values_by_city.items()]
    if dry_run:
        print(f"  [DRY RUN] Would update {len(rows)} cities' {DENORM_COL}")
        if rows:
            print(f"  Sample city_id={rows[0][1]}: {DENORM_COL}={rows[0][0]}")
        return len(rows)

    psycopg2.extras.execute_values(
        cur,
        f"""
        UPDATE cities SET
            {DENORM_COL} = data.v,
            updated_at   = NOW()
        FROM (VALUES %s) AS data(v, id)
        WHERE cities.id = data.id
        """,
        rows,
        template="(%s::double precision, %s)",
    )
    conn.commit()
    return len(rows)


def seed_timeseries(conn, cur, values_by_city: dict[str, float],
                    id_to_insee: dict[str, str], dry_run: bool) -> None:
    from ..services.upload import upsert_series_and_timeseries

    present = check_serie_names_exist(cur, [SERIE_NAME])
    if SERIE_NAME not in present:
        print(f"  BLOCKER — '{SERIE_NAME}' not in SerieName enum (will be SKIPPED on real run)")

    rows = [
        (id_to_insee[cid], MEDIAN_INCOME_PERIOD, v)
        for cid, v in values_by_city.items()
        if cid in id_to_insee
    ]
    print(f"  {SERIE_NAME}: {len(rows)} data points")

    if dry_run:
        print("    [DRY RUN] skipped")
        return
    if SERIE_NAME not in present:
        print(f"    SKIPPED — '{SERIE_NAME}' not in SerieName enum yet")
        return
    if rows:
        stats = upsert_series_and_timeseries(conn, SERIE_DEF, "city", rows, dry_run=False)
        print(f"    → {stats['series_upserted']} series, {stats['timeseries_inserted']} ts")


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
        print(f"TYPICAL_SURFACE_SQM = {TYPICAL_SURFACE_SQM} m²")

        print("Fetching price_sqm_all timeseries...")
        price_series = _fetch_city_timeseries(cur, "price_sqm_all", args.dept)
        print(f"  price_sqm_all: {len(price_series)} cities")

        print("Fetching median_income timeseries...")
        income_series = _fetch_city_timeseries(cur, "median_income", args.dept)
        print(f"  median_income: {len(income_series)} cities")

        print("Computing years_to_buy...")
        values_by_city = compute_years_to_buy(price_series, income_series)
        print(f"  years_to_buy: {len(values_by_city)} cities")

        id_to_insee = _fetch_city_id_to_insee(cur, args.dept)
        if values_by_city:
            vals = sorted(values_by_city.values())
            print(f"  range {vals[0]}..{vals[-1]}, median {vals[len(vals) // 2]}")
            sample = list(values_by_city.items())[:3]
            print(f"  sample: {[(id_to_insee.get(c, c), v) for c, v in sample]}")
            _report_outliers(values_by_city, id_to_insee)

        print("Seeding years_to_buy timeseries...")
        seed_timeseries(conn, cur, values_by_city, id_to_insee, args.dry_run)

        print("Applying cities denormalisation...")
        n = apply_city_updates(conn, cur, values_by_city, args.dry_run)
        print(f"Done. {n} cities updated.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
