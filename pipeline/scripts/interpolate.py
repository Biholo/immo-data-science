"""
Spatial price interpolation for communes without DVF price data.

Strategy:
  1. For each city missing median_price_per_sqm, find K nearest cities WITH data
  2. Compute IDW (Inverse Distance Weighting) estimate
  3. Fallback to department median if no neighbor within MAX_DIST_KM
  4. Update cities — sparkline_path stays NULL to signal "estimated, no history"

Run after denormalize:
  python -m pipeline.scripts.interpolate [--dept 77] [--k 5] [--max-dist 30]
"""

from __future__ import annotations

import argparse
import math
import os
import sys

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

K_NEIGHBORS = 5
MAX_DIST_KM = 30.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def idw(price_dist_pairs: list[tuple[float, float]]) -> float:
    weights = [1.0 / max(d, 0.01) for _, d in price_dist_pairs]
    total_w = sum(weights)
    return sum(p * w for (p, _), w in zip(price_dist_pairs, weights)) / total_w


def run(
    conn,
    dept: str | None = None,
    k: int = K_NEIGHBORS,
    max_dist: float = MAX_DIST_KM,
    dry_run: bool = False,
) -> int:
    """Programmatic entry point — returns number of cities updated."""
    cur = conn.cursor()
    n = _interpolate(cur, conn, dept=dept, k=k, max_dist=max_dist, dry_run=dry_run)
    cur.close()
    return n


def _interpolate(cur, conn, dept, k, max_dist, dry_run) -> int:
    dept_clause = "AND department_code = %s" if dept else ""
    params_dept = [dept] if dept else []

    cur.execute(f"""
        SELECT id, insee_code, name, latitude, longitude,
               median_price_per_sqm, department_code
        FROM cities
        WHERE median_price_per_sqm IS NOT NULL
          AND latitude IS NOT NULL AND longitude IS NOT NULL
          {dept_clause}
    """, params_dept)
    known = cur.fetchall()
    print(f"Cities with price data : {len(known)}")

    cur.execute(f"""
        SELECT id, insee_code, name, latitude, longitude, department_code
        FROM cities
        WHERE median_price_per_sqm IS NULL
          AND latitude IS NOT NULL AND longitude IS NOT NULL
          {dept_clause}
    """, params_dept)
    missing = cur.fetchall()
    print(f"Cities missing price   : {len(missing)}")

    if not missing:
        print("Nothing to interpolate.")
        return 0

    # Dept fallback medians from already-set cities
    cur.execute("""
        SELECT department_code,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY median_price_per_sqm)
        FROM cities
        WHERE median_price_per_sqm IS NOT NULL
        GROUP BY department_code
    """)
    dept_medians: dict[str, float] = {r[0]: r[1] for r in cur.fetchall()}

    updates: list[tuple] = []
    no_neighbor = 0

    for city in missing:
        city_id, _, _, lat, lon, dept_code = city
        if lat is None or lon is None:
            continue

        dists = []
        for krow in known:
            _, _, _, klat, klon, kprice, _ = krow
            if klat is None or klon is None:
                continue
            d = haversine_km(lat, lon, klat, klon)
            dists.append((kprice, d))

        dists.sort(key=lambda x: x[1])
        neighbors = [(p, d) for p, d in dists[:k] if d <= max_dist]

        if not neighbors:
            fallback = dept_medians.get(dept_code)
            if fallback:
                updates.append((round(fallback, 2), city_id))
                no_neighbor += 1
            continue

        updates.append((round(idw(neighbors), 2), city_id))

    idw_count = len(updates) - no_neighbor
    print(f"Estimated via IDW      : {idw_count}")
    print(f"Estimated via dept avg : {no_neighbor}")
    print(f"Total to update        : {len(updates)}")

    if dry_run:
        print("[DRY RUN] No writes.")
        if updates:
            cur.execute("SELECT name FROM cities WHERE id = %s", (updates[0][1],))
            row = cur.fetchone()
            print(f"Sample: {row[0]} -> {updates[0][0]:.0f} EUR/m2 (estimated)")
        return len(updates)

    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE cities SET
            avg_price_per_sqm    = data.price,
            median_price_per_sqm = data.price,
            price_data_source    = 'estimated',
            updated_at           = NOW()
        FROM (VALUES %s) AS data(price, id)
        WHERE cities.id = data.id
          AND cities.median_price_per_sqm IS NULL
        """,
        updates,
        template="(%s::double precision, %s)",
    )
    conn.commit()
    print(f"Done. {len(updates)} cities updated with estimated prices.")
    return len(updates)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--k", type=int, default=K_NEIGHBORS)
    parser.add_argument("--max-dist", type=float, default=MAX_DIST_KM)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set"); sys.exit(1)

    conn = psycopg2.connect(dsn)
    try:
        run(conn, dept=args.dept, k=args.k, max_dist=args.max_dist, dry_run=args.dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
