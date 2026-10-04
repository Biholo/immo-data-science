"""
Seed the quarterly price-per-m2 RANGE series of each commune from the DVF sales loaded in
the `transactions` table (seed_transactions.py must have run):

  price_sqm_low    10th percentile of the sale price per m2   ("bas" of the market)
  price_sqm_avg    mean of the sale price per m2
  price_sqm_high   90th percentile of the sale price per m2   ("haut" of the market)

and the same three figures for the PURCHASE PRICE of the whole property (EUR):

  sale_price_low      10th percentile of the sale price
  sale_price_median   median sale price
  sale_price_avg      mean sale price
  sale_price_high     90th percentile of the sale price

(The true minimum / maximum of a commune's sales are not published: one odd sale would define
them. The 10th / 90th percentiles describe the "cheap" and "expensive" ends of the market.)

Complements price_sqm_all (median, computed by run_dvf from the raw DVF files, which are not
needed here).

  scope    : single-unit sales only (lots_in_mutation = 1) of Maison / Appartement -- a
             building sale carries the whole building's price, not one property's
  filters  : same sanity bounds as the price_sqm series (surface 9-2000 m2, 500-30000 EUR/m2)
  window   : ROLLING 12 MONTHS ending at the end of each quarter, stamped with the first day of
             that quarter (2025-10-01 = sales of 2025-01-01 .. 2025-12-31). A single quarter has
             too few sales in most communes for a 10th / 90th percentile. The first point is
             2021-10-01, the first quarter whose full 12 months are loaded (transactions start
             in 2021-01).
  min sales: a (commune, window) with fewer than --min-n qualifying sales is skipped -- a
             10th / 90th percentile over a handful of sales is noise (series.py uses 10 too)

Create-or-update: an existing serie is reused and its points are updated, never duplicated (re-runnable).
A commune that stops qualifying keeps its previous points.

Run:
  python -m pipeline.scripts.seed_price_range_series [--dept 77] [--min-n 10] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2
from dotenv import load_dotenv

from ..services.upload import upsert_series_and_timeseries

load_dotenv()

# serie name -> (column index in AGG_SQL rows, unit)
SERIES_DEFS = {
    "price_sqm_low": (2, "€/m²"),
    "price_sqm_avg": (3, "€/m²"),
    "price_sqm_high": (4, "€/m²"),
    "sale_price_low": (6, "€"),
    "sale_price_median": (7, "€"),
    "sale_price_avg": (9, "€"),
    "sale_price_high": (8, "€"),
}

COMMON = {"source": "DVF", "frequency": "QUARTERLY", "chart_type": "LINE"}

AGG_SQL = """
    WITH bounds AS (
        SELECT date_trunc('quarter', MIN(mutation_date))::date AS first_day,
               date_trunc('quarter', MAX(mutation_date))::date AS last_quarter
        FROM transactions WHERE source = 'DVF'
    ),
    quarters AS (
        -- first quarter whose 12 months are fully covered by the data, up to the latest quarter
        SELECT q::date AS quarter
        FROM bounds, generate_series(first_day + INTERVAL '9 months', last_quarter, INTERVAL '3 months') AS q
    )
    SELECT
        c.insee_code,
        q.quarter,
        PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY t.price / t.surface)               AS p10,
        AVG(t.price / t.surface)                                                       AS avg,
        PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY t.price / t.surface)               AS p90,
        COUNT(*)                                                                       AS n_sales,
        PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY t.price)                           AS price_p10,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY t.price)                           AS price_p50,
        PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY t.price)                           AS price_p90,
        AVG(t.price)                                                                   AS price_avg
    FROM quarters q
    JOIN transactions t
      ON t.mutation_date >= q.quarter - INTERVAL '9 months'
     AND t.mutation_date <  q.quarter + INTERVAL '3 months'
    JOIN cities c ON c.id = t.city_id
    WHERE t.source = 'DVF'
      AND t.lots_in_mutation = 1
      AND t.property_type IN ('HOUSE', 'APARTMENT')
      AND t.surface BETWEEN 9 AND 2000
      AND t.price / t.surface BETWEEN 500 AND 30000
      {dept_clause}
    GROUP BY c.insee_code, q.quarter
    HAVING COUNT(*) >= %s
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to dept code (e.g. 77)")
    parser.add_argument("--min-n", type=int, default=10, help="Min qualifying sales per (commune, quarter), default 10")
    parser.add_argument("--only", default=None, help="Comma-separated serie names to (re)build, e.g. sale_price_avg. Default: all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    series_defs = SERIES_DEFS
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        unknown = wanted - set(SERIES_DEFS)
        if unknown:
            print(f"Unknown series: {sorted(unknown)}. Available: {list(SERIES_DEFS)}")
            sys.exit(1)
        series_defs = {n: d for n, d in SERIES_DEFS.items() if n in wanted}

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), MAX(mutation_date) FROM transactions WHERE source = 'DVF'")
            n_tx, last_date = cur.fetchone()
            if not n_tx:
                print("transactions table has no DVF rows -- run pipeline.scripts.seed_transactions first")
                sys.exit(1)
            print(f"{n_tx:,} DVF rows, latest sale {last_date:%Y-%m-%d}")

            params: list = []
            dept_clause = ""
            if args.dept:
                dept_clause = "AND c.department_code = %s"
                params.append(args.dept)
            params.append(args.min_n)
            cur.execute(AGG_SQL.format(dept_clause=dept_clause), params)
            rows = cur.fetchall()

        print(f"  {len(rows)} (commune, window) pairs with >= {args.min_n} qualifying sales")
        if not rows:
            return

        cities = {r[0] for r in rows}
        print(f"  {len(cities)} communes")

        for name, (col_idx, unit) in series_defs.items():
            series_rows = [(r[0], r[1].isoformat(), round(float(r[col_idx]), 1)) for r in rows]
            print(f"  {name}: {len(series_rows)} points", end=" ")
            stats = upsert_series_and_timeseries(conn, {"name": name, "unit": unit, **COMMON}, "city", series_rows, dry_run=args.dry_run)
            print(f"-> {stats['series_upserted']} series, {stats['timeseries_inserted']} timeseries rows")

        if args.dry_run:
            sample = sorted(rows, key=lambda r: -r[5])[:3]
            print("\n  sample (insee, quarter, p10, avg, p90, n):")
            for r in sample:
                print("   ", r[0], r[1], round(r[2]), round(r[3]), round(r[4]), r[5])
            print("\n[DRY RUN] no writes")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
