"""
Computes gross rental yield per apartment typology from rent_sqm_t{n}
(DHUP snapshot, seeded by seed_rent_series.py) and price_sqm_t{n}
(DVF-derived, already seeded by the DVF pipeline):

  gross_yield_t{n} = (rent_sqm_t{n} × 12) / price_sqm_t{n} × 100     [% / year]

for n in 1..4. Both series are fetched at "latest known value" per city:
price_sqm_t{n} is quarterly (latest quarter), rent_sqm_t{n} is a single
DHUP snapshot (one point). The resulting gross_yield_t{n} timeseries point
is timestamped at the rent snapshot date (RENT_SNAPSHOT_YEAR-01-01) since
that's the limiting/defining "as of" date of the computation.

Also denormalizes onto `cities`:
  cities.avg_rent_per_sqm = latest rent_sqm_all per city
      (falls back to mean of rent_sqm_t1..t4 if rent_sqm_all has no rows —
       e.g. if it's still blocked by the SerieName enum, see seed_rent_series.py)
  cities.gross_yield       = mean of available gross_yield_t1..t4 per city
                              (blended headline number, NOT per-typology)

Requires seed_rent_series.py to have been run for real first (rent_sqm_t1..t4
at minimum — those are already in the SerieName enum).

Run:
  python -m pipeline.scripts.seed_gross_yield [--dept 77] [--dry-run]
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

from .seed_rent_series import RENT_SNAPSHOT_YEAR, check_serie_names_exist

RENT_PERIOD = f"{RENT_SNAPSHOT_YEAR}-01-01"
TYPOLOGIES = ("t1", "t2", "t3", "t4")


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


def compute_gross_yield_by_typology(
    price_series: dict[str, dict[str, list]],
    rent_series: dict[str, dict[str, list]],
) -> dict[str, dict[str, float]]:
    """Returns {typology: {city_id: gross_yield_pct}}."""
    out: dict[str, dict[str, float]] = {t: {} for t in TYPOLOGIES}
    for t in TYPOLOGIES:
        prices = price_series.get(t, {})
        rents = rent_series.get(t, {})
        for city_id, price_pts in prices.items():
            rent_pts = rents.get(city_id)
            if not price_pts or not rent_pts:
                continue
            latest_price = price_pts[-1][1]
            latest_rent = rent_pts[-1][1]
            if not latest_price or latest_price <= 0:
                continue
            gy = (latest_rent * 12) / latest_price * 100
            out[t][city_id] = round(gy, 3)
    return out


def compute_denormalized_fields(
    rent_all_series: dict[str, list],
    rent_by_typology: dict[str, dict[str, list]],
    gross_yield_by_typology: dict[str, dict[str, float]],
) -> tuple[dict[str, float], dict[str, float]]:
    """Returns (avg_rent_per_sqm, gross_yield) keyed by city_id."""
    avg_rent_per_sqm: dict[str, float] = {}
    for city_id, pts in rent_all_series.items():
        if pts:
            avg_rent_per_sqm[city_id] = round(pts[-1][1], 2)

    if not avg_rent_per_sqm:
        print("  rent_sqm_all has no rows (enum blocker?) — falling back to mean(rent_sqm_t1..t4)")
        fallback_acc: dict[str, list[float]] = defaultdict(list)
        for t in TYPOLOGIES:
            for city_id, pts in rent_by_typology.get(t, {}).items():
                if pts:
                    fallback_acc[city_id].append(pts[-1][1])
        avg_rent_per_sqm = {cid: round(sum(vs) / len(vs), 2) for cid, vs in fallback_acc.items()}

    gross_yield: dict[str, float] = {}
    per_city_yields: dict[str, list[float]] = defaultdict(list)
    for t in TYPOLOGIES:
        for city_id, gy in gross_yield_by_typology.get(t, {}).items():
            per_city_yields[city_id].append(gy)
    for city_id, vs in per_city_yields.items():
        gross_yield[city_id] = round(sum(vs) / len(vs), 3)

    return avg_rent_per_sqm, gross_yield


def check_columns_exist(cur, table: str, columns: list[str]) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s AND column_name = ANY(%s)",
        (table, columns),
    )
    return {r[0] for r in cur.fetchall()}


def apply_city_updates(
    conn,
    cur,
    avg_rent_per_sqm: dict[str, float],
    gross_yield: dict[str, float],
    dry_run: bool,
) -> int:
    present_cols = check_columns_exist(cur, "cities", ["avg_rent_per_sqm", "gross_yield"])
    missing_cols = {"avg_rent_per_sqm", "gross_yield"} - present_cols
    if missing_cols:
        print(f"  BLOCKER — cities columns missing, skipping UPDATE for: {sorted(missing_cols)}")
        print("    → ask the owner of the Postgres schema repo to add these columns.")
        if not present_cols:
            return 0

    city_ids = set(avg_rent_per_sqm) | set(gross_yield)
    rows = [
        (
            avg_rent_per_sqm.get(cid) if "avg_rent_per_sqm" in present_cols else None,
            gross_yield.get(cid) if "gross_yield" in present_cols else None,
            cid,
        )
        for cid in city_ids
    ]

    if dry_run:
        print(f"  [DRY RUN] Would update {len(rows)} cities")
        if rows:
            r = rows[0]
            print(f"  Sample city_id={r[2]}: avg_rent_per_sqm={r[0]}, gross_yield={r[1]}")
        return len(rows)

    set_clauses = []
    if "avg_rent_per_sqm" in present_cols:
        set_clauses.append("avg_rent_per_sqm = data.avg_rent_per_sqm")
    if "gross_yield" in present_cols:
        set_clauses.append("gross_yield = data.gross_yield")

    psycopg2.extras.execute_values(
        cur,
        f"""
        UPDATE cities SET
            {", ".join(set_clauses)},
            updated_at = NOW()
        FROM (VALUES %s) AS data(avg_rent_per_sqm, gross_yield, id)
        WHERE cities.id = data.id
        """,
        rows,
        template="(%s::double precision, %s::double precision, %s)",
    )
    conn.commit()
    return len(rows)


def seed_gross_yield_timeseries(conn, cur, gross_yield_by_typology: dict[str, dict[str, float]],
                                 id_to_insee: dict[str, str], dry_run: bool) -> None:
    from ..services.upload import upsert_series_and_timeseries

    serie_names = [f"gross_yield_{t}" for t in TYPOLOGIES]
    present = check_serie_names_exist(cur, serie_names)
    missing = [n for n in serie_names if n not in present]
    if missing:
        print(f"  BLOCKER — missing from SerieName enum (will be SKIPPED on real run): {missing}")

    for t in TYPOLOGIES:
        serie_name = f"gross_yield_{t}"
        rows = [
            (id_to_insee[city_id], RENT_PERIOD, v)
            for city_id, v in gross_yield_by_typology.get(t, {}).items()
            if city_id in id_to_insee
        ]
        print(f"  {serie_name}: {len(rows)} data points")

        if dry_run:
            print(f"    [DRY RUN] skipped")
            continue
        if serie_name not in present:
            print(f"    SKIPPED — '{serie_name}' not in SerieName enum yet")
            continue

        serie_def = {
            "name": serie_name, "source": "COMPUTED", "frequency": "ANNUAL",
            "unit": "%", "chart_type": "LINE",
        }
        if rows:
            stats = upsert_series_and_timeseries(conn, serie_def, "city", rows, dry_run=False)
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
        print("Fetching price_sqm_t{1..4} timeseries...")
        price_series = {t: _fetch_city_timeseries(cur, f"price_sqm_{t}", args.dept) for t in TYPOLOGIES}
        for t in TYPOLOGIES:
            print(f"  price_sqm_{t}: {len(price_series[t])} cities")

        print("Fetching rent_sqm_t{1..4} timeseries...")
        rent_series = {t: _fetch_city_timeseries(cur, f"rent_sqm_{t}", args.dept) for t in TYPOLOGIES}
        for t in TYPOLOGIES:
            print(f"  rent_sqm_{t}: {len(rent_series[t])} cities")

        print("Fetching rent_sqm_all timeseries...")
        if "rent_sqm_all" in check_serie_names_exist(cur, ["rent_sqm_all"]):
            rent_all_series = _fetch_city_timeseries(cur, "rent_sqm_all", args.dept)
        else:
            print("  SKIPPED — 'rent_sqm_all' not in SerieName enum yet (see seed_rent_series.py blocker)")
            rent_all_series = {}
        print(f"  rent_sqm_all: {len(rent_all_series)} cities")

        print("Computing gross_yield_t{1..4}...")
        gross_yield_by_typology = compute_gross_yield_by_typology(price_series, rent_series)
        for t in TYPOLOGIES:
            print(f"  gross_yield_{t}: {len(gross_yield_by_typology[t])} cities")

        print("Computing denormalized cities fields...")
        avg_rent_per_sqm, gross_yield = compute_denormalized_fields(
            rent_all_series, rent_series, gross_yield_by_typology
        )
        print(f"  avg_rent_per_sqm: {len(avg_rent_per_sqm)} cities")
        print(f"  gross_yield (blended): {len(gross_yield)} cities")

        print("Seeding gross_yield_t{1..4} timeseries...")
        id_to_insee = _fetch_city_id_to_insee(cur, args.dept)
        seed_gross_yield_timeseries(conn, cur, gross_yield_by_typology, id_to_insee, args.dry_run)

        print("Applying cities UPDATE (avg_rent_per_sqm, gross_yield)...")
        n = apply_city_updates(conn, cur, avg_rent_per_sqm, gross_yield, args.dry_run)
        print(f"Done. {n} cities updated.")

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
