"""
Updates cities table snapshot fields from computed timeseries.

Fields filled:
  median_price_per_sqm  → last quarter price_sqm_all
  avg_price_per_sqm     → mean of last 4 quarters price_sqm_all
  transaction_volume    → sum of last 4 quarters transaction_volume
  price_growth_3y       → (latest / price 12Q ago - 1) × 100
  price_trend           → up / down / stable  (last 2 quarters delta)
  sparkline_path        → JSON array of last 8 price_sqm_all values

Run after pipeline.scripts.run_dvf city upload:
  python -m pipeline.scripts.denormalize [--dept 77]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

PRICE_TREND_THRESHOLD = 0.02   # ±2% → stable


def _fetch_dept_medians(cur, dept: str | None) -> dict[str, float]:
    """Returns {dept_code: latest_median_price} from department-level series."""
    dept_clause = "AND az.code = %s" if dept else ""
    params = ["price_sqm_all"]
    if dept:
        params.append(dept)

    cur.execute(f"""
        SELECT az.code, t.value
        FROM series s
        JOIN timeseries t ON t.serie_id = s.id
        JOIN administrative_zones az ON s.administrative_zone_id = az.id
        WHERE s.name = %s
          AND s.administrative_zone_id IS NOT NULL
          AND az.type = 'department'
          {dept_clause}
          AND t.timestamp = (
              SELECT MAX(t2.timestamp) FROM timeseries t2 WHERE t2.serie_id = s.id
          )
    """, params)
    return {r[0]: r[1] for r in cur.fetchall()}


def _fetch_city_dept_map(cur, dept: str | None) -> dict[str, str]:
    """Returns {city_id: dept_code}."""
    dept_clause = "AND department_code = %s" if dept else ""
    params = [dept] if dept else []
    cur.execute(f"""
        SELECT id, department_code FROM cities
        WHERE department_code IS NOT NULL {dept_clause}
    """, params)
    return {r[0]: r[1] for r in cur.fetchall()}


def _fetch_city_timeseries(cur, serie_name: str, dept: str | None) -> dict[str, list]:
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


OUTLIER_RATIO = 3.0   # flag city if price > 3× dept median


def compute_city_snapshots(
    price_series: dict[str, list],
    volume_series: dict[str, list],
    dept_medians: dict[str, float] | None = None,
    city_dept_map: dict[str, str] | None = None,
) -> dict[str, dict]:
    """
    Returns {city_id: {field: value}} for all cities with price data.
    Outliers (price > OUTLIER_RATIO × dept median) are excluded → handled by interpolate.py.
    """
    snapshots: dict[str, dict] = {}
    outliers = 0

    for city_id, pts in price_series.items():
        if not pts:
            continue

        vals = [v for _, v in pts]
        latest = vals[-1]

        # Outlier check vs department median
        if dept_medians and city_dept_map:
            dept_code = city_dept_map.get(city_id)
            dept_ref = dept_medians.get(dept_code) if dept_code else None
            if dept_ref and latest > OUTLIER_RATIO * dept_ref:
                outliers += 1
                continue  # skip → stays NULL → interpolate.py will fill via IDW

        avg_4q = sum(vals[-4:]) / len(vals[-4:])

        # 3-year growth: 12 quarters back
        growth_3y = None
        if len(vals) >= 13:
            base = vals[-13]
            if base and base > 0:
                growth_3y = (latest / base - 1) * 100

        # price_trend from last 2 available quarters
        trend = "stable"
        if len(vals) >= 2:
            delta = (vals[-1] - vals[-2]) / vals[-2] if vals[-2] else 0
            if delta > PRICE_TREND_THRESHOLD:
                trend = "up"
            elif delta < -PRICE_TREND_THRESHOLD:
                trend = "down"

        # sparkline: last 8 values as JSON
        sparkline = json.dumps([round(v, 0) for v in vals[-8:]])

        snapshots[city_id] = {
            "median_price_per_sqm": round(latest, 2),
            "avg_price_per_sqm": round(avg_4q, 2),
            "price_growth_3y": round(growth_3y, 2) if growth_3y is not None else None,
            "price_trend": trend,
            "sparkline_path": sparkline,
        }

    if outliers:
        print(f"  Outliers removed (>{OUTLIER_RATIO}x dept median): {outliers}")

    # transaction_volume: sum last 4 quarters
    for city_id, pts in volume_series.items():
        if not pts:
            continue
        vol = sum(v for _, v in pts[-4:])
        if city_id not in snapshots:
            snapshots[city_id] = {}
        snapshots[city_id]["transaction_volume"] = int(vol)

    return snapshots


def apply_updates(conn, cur, snapshots: dict[str, dict], dry_run: bool) -> int:
    rows = []
    for city_id, fields in snapshots.items():
        rows.append((
            fields.get("median_price_per_sqm"),
            fields.get("avg_price_per_sqm"),
            fields.get("transaction_volume"),
            fields.get("price_growth_3y"),
            fields.get("price_trend"),
            fields.get("sparkline_path"),
            city_id,
        ))

    if dry_run:
        print(f"  [DRY RUN] Would update {len(rows)} cities")
        if rows:
            r = rows[0]
            print(f"  Sample city_id={r[6]}: median={r[0]}, avg={r[1]}, vol={r[2]}, growth3y={r[3]}, trend={r[4]}")
        return len(rows)

    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE cities SET
            median_price_per_sqm = data.median_price_per_sqm,
            avg_price_per_sqm    = data.avg_price_per_sqm,
            transaction_volume   = data.transaction_volume,
            price_growth_3y      = data.price_growth_3y,
            price_trend          = data.price_trend::\"PriceTrend\",
            sparkline_path       = data.sparkline_path,
            price_data_source    = 'dvf',
            updated_at           = NOW()
        FROM (VALUES %s) AS data(
            median_price_per_sqm, avg_price_per_sqm, transaction_volume,
            price_growth_3y, price_trend, sparkline_path, id
        )
        WHERE cities.id = data.id
        """,
        rows,
        template="(%s::double precision, %s::double precision, %s::integer, %s::double precision, %s, %s, %s)",
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
        print("Fetching dept reference prices...")
        dept_medians = _fetch_dept_medians(cur, args.dept)
        city_dept_map = _fetch_city_dept_map(cur, args.dept)
        print(f"  {len(dept_medians)} dept medians loaded")

        print("Fetching price_sqm_all timeseries...")
        price_series = _fetch_city_timeseries(cur, "price_sqm_all", args.dept)
        print(f"  {len(price_series)} cities with price data")

        print("Fetching transaction_volume timeseries...")
        volume_series = _fetch_city_timeseries(cur, "transaction_volume", args.dept)
        print(f"  {len(volume_series)} cities with volume data")

        print("Computing snapshots...")
        snapshots = compute_city_snapshots(price_series, volume_series, dept_medians, city_dept_map)
        print(f"  {len(snapshots)} cities to update")

        print("Applying updates...")
        n = apply_updates(conn, cur, snapshots, args.dry_run)
        print(f"Done. {n} cities updated.")

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
