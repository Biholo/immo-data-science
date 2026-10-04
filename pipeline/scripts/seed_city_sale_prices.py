"""
Fills cities.median_sale_price and cities.avg_sale_price (EUR, whole property), plus the "low" / "high"
ends of the market (10th / 90th percentiles) of the sale price and of the price per m2:

  sale_price_low / sale_price_high        EUR, whole property
  price_per_sqm_low / price_per_sqm_high  EUR/m2

from the DVF sales loaded in the `transactions` table. All of them share the same window and filters, so
the price gauges of the city page read one consistent snapshot.

  window   : the 12 months ending at the latest DVF sale in `transactions`
             (DVF lags by several months, so NOT "today - 12 months")
  scope    : single-unit sales only (lots_in_mutation = 1) of Maison / Appartement —
             a block/building sale carries the whole building's price, not one property's
  filters  : same sanity bounds as the price_sqm series (surface 9-2000 m2,
             500-30000 EUR/m2) so that outliers do not drag the average
  min sales: cities with fewer than --min-n qualifying sales in the window are set to
             NULL (a median over 3 sales is noise) — same idea as `min_n` in series.py

Complements the per-m2 fields already on `cities` (median_price_per_sqm =
latest quarter median, avg_price_per_sqm = mean of the last 4 quarters), which
come from the price_sqm_all timeseries (denormalize.py).

Run after seed_transactions.py:
  python -m pipeline.scripts.seed_city_sale_prices [--dept 77] [--min-n 10] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

AGG_SQL = """
    WITH win AS (
        SELECT MAX(mutation_date) AS mx FROM transactions WHERE source = 'DVF'
    ),
    sales AS (
        SELECT t.city_id, t.price, t.price / t.surface AS price_sqm
        FROM transactions t
        JOIN cities c ON c.id = t.city_id
        CROSS JOIN win
        WHERE t.source = 'DVF'
          AND t.lots_in_mutation = 1
          AND t.property_type IN ('HOUSE', 'APARTMENT')
          AND t.mutation_date > win.mx - INTERVAL '12 months'
          AND t.surface BETWEEN 9 AND 2000
          AND t.price / t.surface BETWEEN 500 AND 30000
          {dept_clause}
    )
    SELECT
        city_id,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price)::numeric, 0)::double precision AS median_price,
        ROUND(AVG(price)::numeric, 0)::double precision                                          AS avg_price,
        COUNT(*)                                                                                 AS n_sales,
        ROUND(PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY price)::numeric, 0)::double precision   AS price_p10,
        ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY price)::numeric, 0)::double precision   AS price_p90,
        ROUND(PERCENTILE_CONT(0.1) WITHIN GROUP (ORDER BY price_sqm)::numeric, 0)::double precision AS sqm_p10,
        ROUND(PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY price_sqm)::numeric, 0)::double precision AS sqm_p90
    FROM sales
    GROUP BY city_id
    HAVING COUNT(*) >= %s
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to dept code (e.g. 77)")
    parser.add_argument("--min-n", type=int, default=10, help="Min qualifying sales in the window (default 10)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*), MAX(mutation_date) FROM transactions WHERE source = 'DVF'")
        n_tx, last_date = cur.fetchone()
        if not n_tx:
            print("transactions table has no DVF rows — run pipeline.scripts.seed_transactions first")
            sys.exit(1)
        print(f"{n_tx:,} DVF rows, latest sale {last_date:%Y-%m-%d} -> window = 12 months before")

        params: list = []
        dept_clause = ""
        if args.dept:
            dept_clause = "AND c.department_code = %s"
            params.append(args.dept)
        params.append(args.min_n)

        cur.execute(AGG_SQL.format(dept_clause=dept_clause), params)
        rows = cur.fetchall()
        print(f"  {len(rows)} cities with >= {args.min_n} qualifying sales")
        if rows:
            top = sorted(rows, key=lambda r: -r[3])[:3]
            print("  Sample (city_id, median, avg, n):", top)

        if args.dry_run:
            print("[DRY RUN] no write")
            return

        # Cities of the scope that no longer qualify go back to NULL (idempotent re-run).
        scope_sql = "WHERE department_code = %s" if args.dept else ""
        cur.execute(
            f"UPDATE cities SET median_sale_price = NULL, avg_sale_price = NULL, sale_price_low = NULL, sale_price_high = NULL, price_per_sqm_low = NULL, price_per_sqm_high = NULL {scope_sql}",
            [args.dept] if args.dept else [],
        )
        psycopg2.extras.execute_values(
            cur,
            """
            UPDATE cities SET
                median_sale_price  = data.median_price,
                avg_sale_price     = data.avg_price,
                sale_price_low     = data.price_p10,
                sale_price_high    = data.price_p90,
                price_per_sqm_low  = data.sqm_p10,
                price_per_sqm_high = data.sqm_p90,
                updated_at         = NOW()
            FROM (VALUES %s) AS data(id, median_price, avg_price, price_p10, price_p90, sqm_p10, sqm_p90)
            WHERE cities.id = data.id
            """,
            [(r[0], r[1], r[2], r[4], r[5], r[6], r[7]) for r in rows],
            template="(%s, %s::double precision, %s::double precision, %s::double precision, %s::double precision, %s::double precision, %s::double precision)",
        )
        conn.commit()
        print(f"Done. {len(rows)} cities updated.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
