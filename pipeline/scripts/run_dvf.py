"""
Usage:
  python -m pipeline.scripts.run_dvf                                    # all geo levels, upload to DB
  python -m pipeline.scripts.run_dvf --geo city                        # city only
  python -m pipeline.scripts.run_dvf --geo dept,region                 # dept + region
  python -m pipeline.scripts.run_dvf --dry-run                         # compute only, no DB writes
  python -m pipeline.scripts.run_dvf --serie price_sqm_all --dry-run   # single series
  python -m pipeline.scripts.run_dvf --geo department --csv out.csv    # export to CSV
  python -m pipeline.scripts.run_dvf --preview 10                      # print 10 sample rows per pair
  python -m pipeline.scripts.run_dvf --geo city --dept 77              # city-level for dept 77 only
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from ..services.dvf import compute_all, GeoLevel
from ..services.series import SERIES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DVF series pipeline")
    parser.add_argument(
        "--geo",
        default="city,department,region,country",
        help="Comma-separated geo levels: city,department,region,country",
    )
    parser.add_argument(
        "--serie",
        default=None,
        help="Run only this serie name (for testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute but skip DB writes",
    )
    parser.add_argument(
        "--csv",
        default=None,
        metavar="FILE",
        help="Export all computed results to CSV (implies --dry-run)",
    )
    parser.add_argument(
        "--preview",
        type=int,
        default=0,
        metavar="N",
        help="Print N sample rows per (serie, geo) pair (implies --dry-run)",
    )
    parser.add_argument(
        "--dept",
        default=None,
        help="Restrict city-level to one department code (e.g. 77)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    geo_levels: list[GeoLevel] = []
    for g in args.geo.split(","):
        g = g.strip()
        alias = {"dept": "department", "dep": "department", "reg": "region"}
        geo_levels.append(alias.get(g, g))

    valid = {"city", "department", "region", "country"}
    bad = set(geo_levels) - valid
    if bad:
        print(f"Unknown geo levels: {bad}. Valid: {valid}")
        sys.exit(1)

    # Filter series if --serie specified
    active_series = SERIES
    if args.serie:
        active_series = [s for s in SERIES if s["name"] == args.serie]
        if not active_series:
            print(f"Unknown serie: {args.serie}")
            print("Available:", [s["name"] for s in SERIES])
            sys.exit(1)

    import pipeline.services.dvf as dvf_mod
    original_series = dvf_mod.SERIES
    dvf_mod.SERIES = active_series

    dept_filter = args.dept.strip() if args.dept else None
    if dept_filter:
        print(f"City filter: dept={dept_filter}")

    print(f"Running {len(active_series)} series × {len(geo_levels)} geo levels")
    all_results = compute_all(geo_levels, dept_filter=dept_filter)

    dvf_mod.SERIES = original_series

    if args.csv:
        _export_csv(all_results, args.csv)
        return

    if args.preview:
        _print_preview(all_results, args.preview)
        return

    if args.dry_run:
        print("\n[DRY RUN] Skipping DB upload")
        total_rows = sum(len(v) for v in all_results.values())
        print(f"Computed {total_rows} result rows across {len(all_results)} (serie, geo) pairs")
        for (name, geo), rows in sorted(all_results.items()):
            print(f"  {name:25s} @ {geo:12s}: {len(rows):6d} rows")
        return

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set. Set it in .env or environment.")
        sys.exit(1)

    print(f"\nConnecting to DB...")
    from ..services.upload import run_upload
    from .denormalize import _fetch_city_timeseries, compute_city_snapshots, apply_updates
    import psycopg2
    conn = psycopg2.connect(dsn)
    try:
        run_upload(conn, all_results, dry_run=False)

        if "city" in geo_levels:
            print("\nDenormalizing city snapshot fields...")
            from .denormalize import _fetch_dept_medians, _fetch_city_dept_map
            cur = conn.cursor()
            dept_medians = _fetch_dept_medians(cur, dept_filter)
            city_dept_map = _fetch_city_dept_map(cur, dept_filter)
            price_series = _fetch_city_timeseries(cur, "price_sqm_all", dept_filter)
            volume_series = _fetch_city_timeseries(cur, "transaction_volume", dept_filter)
            cur.close()
            snapshots = compute_city_snapshots(price_series, volume_series, dept_medians, city_dept_map)
            cur = conn.cursor()
            n = apply_updates(conn, cur, snapshots, dry_run=False)
            cur.close()
            print(f"  {n} cities updated with snapshot fields")

            # Interpolate cities flagged as outlier or still missing price
            from .interpolate import run as interpolate_run
            print("\nInterpolating missing/outlier cities...")
            n_interp = interpolate_run(conn, dept=dept_filter)
            print(f"  {n_interp} cities estimated via IDW")
    finally:
        conn.close()


def _export_csv(all_results: dict, path: str) -> None:
    total = sum(len(v) for v in all_results.values())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["serie", "geo_level", "geo_code", "period", "value"])
        for (serie_name, geo_level), rows in sorted(all_results.items()):
            for geo_code, period, value in rows:
                writer.writerow([
                    serie_name,
                    geo_level,
                    geo_code,
                    period.strftime("%Y-%m-%d") if isinstance(period, datetime) else period,
                    round(value, 4) if value is not None else "",
                ])
    print(f"Exported {total} rows → {path}")


def _print_preview(all_results: dict, n: int) -> None:
    for (serie_name, geo_level), rows in sorted(all_results.items()):
        print(f"\n{'-'*60}")
        print(f"{serie_name} @ {geo_level}  ({len(rows)} rows total, showing {min(n, len(rows))})")
        print(f"{'geo_code':<12} {'period':<14} {'value':>12}")
        for geo_code, period, value in rows[:n]:
            period_str = period.strftime("%Y-%m-%d") if isinstance(period, datetime) else str(period)
            value_str = f"{value:,.2f}" if value is not None else "None"
            print(f"{geo_code:<12} {period_str:<14} {value_str:>12}")


if __name__ == "__main__":
    main()
