"""
Spatial interpolation (IDW) of the price ranges for communes that have too few sales of their own.

Same rule as pipeline/scripts/interpolate.py (the thesis rule): for each commune without data, take
the K nearest communes WITH data within MAX_DIST_KM and average them with weights 1 / distance.
No neighbour in range -> the commune is left empty (a department-wide guess would be misleading on a
price gauge).

Two things are filled:

  1. cities snapshot columns behind the "Prix au m²" / "Prix d'achat" gauges:
       avg_price_per_sqm, median_price_per_sqm, price_per_sqm_low, price_per_sqm_high,
       avg_sale_price, median_sale_price, sale_price_low, sale_price_high
     Only the columns that are NULL are written (real figures are never overwritten), and
     cities.price_range_estimated is set to true so the UI can say it is an estimate.
     "Communes with data" = the ones whose range comes from >= 10 real sales (seed_city_sale_prices).

  2. quarterly series price_sqm_house / price_sqm_appt / price_sqm_all of the communes that have none:
       for each quarter, IDW of the neighbours that have a value that quarter (>= 2 of them required).
     They are stored with source = 'CALC' so they can be told apart from DVF series. Create-or-update:
     an existing estimated series is reused and its points are updated, never duplicated. The estimated
     series of a commune that has meanwhile got a real series of its own is removed.

Run after seed_city_sale_prices and the DVF series:
  python -m pipeline.scripts.interpolate_price_range [--dept 77] [--k 5] [--max-dist 30] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections import defaultdict

import numpy as np
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

K_NEIGHBORS = 5
MAX_DIST_KM = 30.0
EARTH_RADIUS_KM = 6371.0
CHUNK = 400

RANGE_COLUMNS = [
    "avg_price_per_sqm",
    "median_price_per_sqm",
    "price_per_sqm_low",
    "price_per_sqm_high",
    "avg_sale_price",
    "median_sale_price",
    "sale_price_low",
    "sale_price_high",
]
SERIES_TO_FILL = ["price_sqm_all", "price_sqm_house", "price_sqm_appt"]
MIN_NEIGHBORS_PER_QUARTER = 2


def neighbours(target_xy: np.ndarray, known_xy: np.ndarray, k: int, max_dist: float) -> list[tuple[np.ndarray, np.ndarray]]:
    """For each target (lat, lon in radians): indices into `known_xy` and distances (km) of its <= k nearest known points within max_dist."""
    out: list[tuple[np.ndarray, np.ndarray]] = []
    if len(known_xy) == 0:
        return [(np.array([], dtype=int), np.array([])) for _ in range(len(target_xy))]
    klat, klon = known_xy[:, 0], known_xy[:, 1]
    for start in range(0, len(target_xy), CHUNK):
        block = target_xy[start:start + CHUNK]
        dlat = klat[None, :] - block[:, 0:1]
        dlon = klon[None, :] - block[:, 1:2]
        a = np.sin(dlat / 2) ** 2 + np.cos(block[:, 0:1]) * np.cos(klat[None, :]) * np.sin(dlon / 2) ** 2
        dist = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))
        kk = min(k, dist.shape[1])
        idx = np.argpartition(dist, kk - 1, axis=1)[:, :kk]
        for row in range(dist.shape[0]):
            d = dist[row, idx[row]]
            keep = d <= max_dist
            out.append((idx[row][keep], d[keep]))
    return out


def idw(values: np.ndarray, dists: np.ndarray) -> float | None:
    """Inverse-distance weighted mean, ignoring NaN values."""
    ok = ~np.isnan(values)
    if not ok.any():
        return None
    w = 1.0 / np.maximum(dists[ok], 0.01)
    return float(np.sum(values[ok] * w) / np.sum(w))


# ── 1. snapshot columns ───────────────────────────────────────────────────────

def interpolate_columns(cur, conn, dept, k, max_dist, dry_run) -> None:
    dept_clause = "AND department_code = %s" if dept else ""
    cur.execute(
        f"""
        SELECT id, latitude, longitude, {", ".join(RANGE_COLUMNS)}, price_per_sqm_low
        FROM cities
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        """
    )
    rows = cur.fetchall()
    ids = [r[0] for r in rows]
    xy = np.radians(np.array([[r[1], r[2]] for r in rows], dtype=float))
    values = np.array([[np.nan if v is None else v for v in r[3:3 + len(RANGE_COLUMNS)]] for r in rows], dtype=float)
    has_range = np.array([r[3 + RANGE_COLUMNS.index("price_per_sqm_low")] is not None and r[3 + RANGE_COLUMNS.index("sale_price_low")] is not None for r in rows])

    # targets: no range of their own (optionally restricted to one department)
    in_scope = np.ones(len(rows), dtype=bool)
    if dept:
        cur.execute("SELECT id FROM cities WHERE department_code = %s", (dept,))
        scope = {r[0] for r in cur.fetchall()}
        in_scope = np.array([i in scope for i in ids])
    target_idx = np.where(~has_range & in_scope)[0]
    known_idx = np.where(has_range)[0]
    print(f"  snapshot columns: {len(known_idx)} communes with a real range, {len(target_idx)} without")

    found = neighbours(xy[target_idx], xy[known_idx], k, max_dist)
    updates = []
    for t, (nidx, nd) in zip(target_idx, found):
        if len(nidx) == 0:
            continue
        nvals = values[known_idx[nidx]]
        est = [idw(nvals[:, c], nd) for c in range(len(RANGE_COLUMNS))]
        updates.append((ids[t], *est))
    print(f"  {len(updates)} communes estimated ({len(target_idx) - len(updates)} have no neighbour within {max_dist:g} km)")
    if dry_run or not updates:
        return

    sets = ",\n                ".join(f"{c} = COALESCE(cities.{c}, data.{c})" for c in RANGE_COLUMNS)
    psycopg2.extras.execute_values(
        cur,
        f"""
        UPDATE cities SET
                {sets},
                price_range_estimated = true,
                updated_at = NOW()
        FROM (VALUES %s) AS data(id, {", ".join(RANGE_COLUMNS)})
        WHERE cities.id = data.id
        """,
        updates,
        template="(%s, " + ", ".join(["%s::double precision"] * len(RANGE_COLUMNS)) + ")",
        page_size=1000,
    )
    conn.commit()
    print(f"  {len(updates)} cities updated")


# ── 2. quarterly series ───────────────────────────────────────────────────────

def interpolate_series(cur, conn, dept, k, max_dist, dry_run) -> None:
    cur.execute("SELECT id, latitude, longitude FROM cities WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    coords = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    in_scope: set[str] | None = None
    if dept:
        cur.execute("SELECT id FROM cities WHERE department_code = %s", (dept,))
        in_scope = {r[0] for r in cur.fetchall()}

    for name in SERIES_TO_FILL:
        cur.execute(
            """
            SELECT s.city_id, t.timestamp::date, t.value
            FROM series s JOIN timeseries t ON t.serie_id = s.id
            WHERE s.name::text = %s AND s.source::text <> 'CALC' AND s.city_id IS NOT NULL AND t.dimension IS NULL
            """,
            (name,),
        )
        real: dict[str, dict] = defaultdict(dict)
        for city_id, ts, value in cur.fetchall():
            real[city_id][ts] = value

        cur.execute("SELECT city_id, id FROM series WHERE name::text = %s AND source::text = 'CALC' AND city_id IS NOT NULL", (name,))
        calc_series: dict[str, str] = dict(cur.fetchall())

        known_ids = [c for c in real if c in coords]
        target_ids = [c for c in coords if c not in real and (in_scope is None or c in in_scope)]
        known_xy = np.radians(np.array([coords[c] for c in known_ids], dtype=float))
        target_xy = np.radians(np.array([coords[c] for c in target_ids], dtype=float))
        found = neighbours(target_xy, known_xy, k, max_dist)

        out_rows: list[tuple] = []
        serie_rows: list[tuple] = []
        for city_id, (nidx, nd) in zip(target_ids, found):
            if len(nidx) < MIN_NEIGHBORS_PER_QUARTER:
                continue
            per_quarter: dict = defaultdict(list)
            for j, dist in zip(nidx, nd):
                for ts, value in real[known_ids[j]].items():
                    per_quarter[ts].append((value, dist))
            points = []
            for ts, pairs in per_quarter.items():
                if len(pairs) < MIN_NEIGHBORS_PER_QUARTER:
                    continue
                points.append((ts, idw(np.array([p[0] for p in pairs]), np.array([p[1] for p in pairs]))))
            if not points:
                continue
            serie_id = calc_series.get(city_id)
            if serie_id is None:
                serie_id = str(uuid.uuid4())
                serie_rows.append((serie_id, name, city_id))
            out_rows.extend((serie_id, ts, round(value, 1)) for ts, value in sorted(points))

        estimated_cities = len({r[0] for r in out_rows})
        print(f"  {name}: {len(known_ids)} real series, {estimated_cities} estimated ({len(serie_rows)} new, {len(out_rows)} points, {len(target_ids) - estimated_cities} communes without enough neighbours)")
        if dry_run:
            continue

        # estimated series of communes that now have a real series are stale: drop them
        stale = [calc_series[c] for c in real if c in calc_series]
        if stale:
            cur.execute("DELETE FROM timeseries WHERE serie_id = ANY(%s)", (stale,))
            cur.execute("DELETE FROM series WHERE id = ANY(%s)", (stale,))
        if not out_rows:
            conn.commit()
            continue

        if serie_rows:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO series (id, name, source, frequency, unit, chart_type, city_id, created_at, updated_at)
                VALUES %s
                """,
                serie_rows,
                template="(%s, %s::\"SerieName\", 'CALC', 'QUARTERLY', '€/m²', 'LINE', %s, NOW(), NOW())",
                page_size=2000,
            )

        # points: update the ones that exist, insert the others
        cur.execute(
            """
            SELECT t.serie_id, t.timestamp::date, t.id
            FROM timeseries t JOIN series s ON s.id = t.serie_id
            WHERE s.name::text = %s AND s.source::text = 'CALC' AND s.city_id IS NOT NULL AND t.dimension IS NULL
            """,
            (name,),
        )
        existing_points = {(r[0], r[1]): r[2] for r in cur.fetchall()}
        to_insert = [(str(uuid.uuid4()), ts, value, serie_id) for serie_id, ts, value in out_rows if (serie_id, ts) not in existing_points]
        to_update = [(value, existing_points[(serie_id, ts)]) for serie_id, ts, value in out_rows if (serie_id, ts) in existing_points]
        if to_insert:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO timeseries (id, timestamp, value, dimension, serie_id, created_at, updated_at)
                VALUES %s
                """,
                to_insert,
                template="(%s, %s, %s, NULL, %s, NOW(), NOW())",
                page_size=5000,
            )
        if to_update:
            psycopg2.extras.execute_values(
                cur,
                """
                UPDATE timeseries SET value = data.v, updated_at = NOW()
                FROM (VALUES %s) AS data(v, id)
                WHERE timeseries.id = data.id
                """,
                to_update,
                template="(%s::double precision, %s)",
                page_size=5000,
            )
        conn.commit()
        print(f"    {len(to_insert)} points created, {len(to_update)} updated")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict targets to one department code (e.g. 77); neighbours stay national")
    parser.add_argument("--k", type=int, default=K_NEIGHBORS)
    parser.add_argument("--max-dist", type=float, default=MAX_DIST_KM)
    parser.add_argument("--skip-series", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    try:
        print("IDW interpolation of the price ranges ...")
        interpolate_columns(cur, conn, args.dept, args.k, args.max_dist, args.dry_run)
        if not args.skip_series:
            interpolate_series(cur, conn, args.dept, args.k, args.max_dist, args.dry_run)
        if args.dry_run:
            print("\n[DRY RUN] no writes")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
