"""
DERIVED series: `housing_effort_rate` — the share of median household income
spent on housing (rent). Standard "taux d'effort" proxy:

    housing_effort_rate =
        (rent_sqm_all × TYPICAL_SURFACE_SQM)          [€ / month, typical dwelling]
        ─────────────────────────────────────────
        (median_income / 12)                         [€ / month]

Inputs (both city-level, already-seeded series):
  rent_sqm_all   — DHUP/ANIL "Carte des loyers" 2025, €/m²/month
                   (seeded this session by seed_rent_series.py)
  median_income  — INSEE Filosofi 2021, annual, € (seeded by
                   seed_median_income_series.py)

APPROXIMATIONS (documented deliberately):
  * median_income here is Filosofi MED_SL = *niveau de vie médian*, i.e.
    median disposable income per consumption unit, not per household. A real
    household taux d'effort would divide by household income; using niveau
    de vie understates the denominator for multi-person households, so this
    proxy runs high relative to the INSEE ENL figure. Treat it as a
    cross-commune comparison index, not an absolute rate.
  * TYPICAL_SURFACE_SQM = 70 m². Rationale: INSEE Enquête Logement 2020 —
    the average main residence is ~90 m², but rented dwellings and
    apartments are markedly smaller (apartments ~63 m²). 70 m² approximates
    a typical rented 3-room dwelling (T3) and is applied uniformly to every
    commune so the metric stays comparable. seed_years_to_buy.py imports
    this same constant.

Output series (city level):
  housing_effort_rate   unit "ratio", frequency ANNUAL, source "CALC",
                        chart_type "LINE"
  one datapoint per city where BOTH inputs exist.

"As of" date: MEDIAN_INCOME_PERIOD (2021-01-01) — the income denominator is
the oldest / limiting input and the defining vintage of the computation
(same convention as seed_gross_yield.py timestamping at the limiting date).

`cities` denormalisation: a snapshot column `cities.housing_effort_rate` is
written IF it exists (checked via information_schema.columns). It does NOT
currently exist in the schema — the UPDATE is skipped silently and noted.

Prereq for a REAL (non-dry-run) seed: rent_sqm_all must already be seeded.
As of this session it is NOT (0 rows) — run this script --dry-run only until
seed_rent_series.py has been run for real.

Run:
  python -m pipeline.scripts.seed_housing_effort_rate [--dept 77] [--dry-run]
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

# Typical dwelling surface used to turn €/m² into a whole-dwelling figure.
# See module docstring for the rationale. seed_years_to_buy.py imports this.
TYPICAL_SURFACE_SQM = 70.0

SERIE_NAME = "housing_effort_rate"
SERIE_DEF = {
    "name": SERIE_NAME,
    "source": "CALC",
    "frequency": "ANNUAL",
    "unit": "ratio",
    "chart_type": "LINE",
}
DENORM_COL = "housing_effort_rate"

# Plausibility band for a per-commune taux d'effort proxy — values outside
# are printed for inspection (usually a unit bug: annual vs monthly income,
# or €/m² read as a whole-dwelling price).
SANE_MIN, SANE_MAX = 0.05, 0.80


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


def compute_housing_effort_rate(
    rent_series: dict[str, list],
    income_series: dict[str, list],
) -> dict[str, float]:
    """Returns {city_id: housing_effort_rate} for cities present in both inputs."""
    out: dict[str, float] = {}
    for city_id, rent_pts in rent_series.items():
        income_pts = income_series.get(city_id)
        if not rent_pts or not income_pts:
            continue
        rent_sqm_month = rent_pts[-1][1]
        income_year = income_pts[-1][1]
        if not income_year or income_year <= 0 or not rent_sqm_month or rent_sqm_month <= 0:
            continue
        monthly_rent = rent_sqm_month * TYPICAL_SURFACE_SQM
        monthly_income = income_year / 12.0
        out[city_id] = round(monthly_rent / monthly_income, 4)
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

        print("Fetching rent_sqm_all timeseries...")
        rent_series = _fetch_city_timeseries(cur, "rent_sqm_all", args.dept)
        print(f"  rent_sqm_all: {len(rent_series)} cities")
        if not rent_series:
            print("  NOTE — rent_sqm_all has no rows yet; run seed_rent_series.py for real first.")

        print("Fetching median_income timeseries...")
        income_series = _fetch_city_timeseries(cur, "median_income", args.dept)
        print(f"  median_income: {len(income_series)} cities")

        print("Computing housing_effort_rate...")
        values_by_city = compute_housing_effort_rate(rent_series, income_series)
        print(f"  housing_effort_rate: {len(values_by_city)} cities")

        id_to_insee = _fetch_city_id_to_insee(cur, args.dept)
        if values_by_city:
            vals = sorted(values_by_city.values())
            print(f"  range {vals[0]}..{vals[-1]}, median {vals[len(vals) // 2]}")
            sample = list(values_by_city.items())[:3]
            print(f"  sample: {[(id_to_insee.get(c, c), v) for c, v in sample]}")
            _report_outliers(values_by_city, id_to_insee)

        print("Seeding housing_effort_rate timeseries...")
        seed_timeseries(conn, cur, values_by_city, id_to_insee, args.dry_run)

        print("Applying cities denormalisation...")
        n = apply_city_updates(conn, cur, values_by_city, args.dry_run)
        print(f"Done. {n} cities updated.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
