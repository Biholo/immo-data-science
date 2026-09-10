"""
Ingest OpenStreetMap POIs (Geofabrik ``.osm.pbf``) into the Postgres ``pois`` table.

  python -m pipeline.scripts.seed_poi --pbf csv/poi/france-latest.osm.pbf [--dept 77] [--dry-run]

Pipeline:
  1. stream the pbf  -> pipeline/services/poi_source.py   (tag whitelist filter)
  2. geo-assign      -> pipeline/services/poi_geo.py       (point-in-polygon -> INSEE code)
  3. keep only POIs whose commune exists in ``cities`` (optionally ``--dept`` filtered)
  4. upsert into ``pois`` ON CONFLICT (osm_id)

Source files: download a Geofabrik extract into ``csv/poi/``.
  - national run : https://download.geofabrik.de/europe/france-latest.osm.pbf  (~4 GB)
  - dev / test   : https://download.geofabrik.de/europe/france/ile-de-france-latest.osm.pbf  (~250 MB, covers dept 77)

SCHEMA REQUIREMENT: OSM node ids exceed int4, so ``pois.osm_id`` must be BigInt.
Edit ``rentium/backend/prisma/schema/poi.prisma`` (``osmId Int`` -> ``osmId BigInt``)
and apply the Prisma migration before the first real run.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections import Counter
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from ..services.poi_geo import CommuneLocator
from ..services.poi_source import CATEGORIES, stream_pois

load_dotenv()

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PBF = _REPO_ROOT / "csv" / "poi" / "france-latest.osm.pbf"
BATCH_SIZE = 5000

UPSERT_SQL = """
    INSERT INTO pois (
        id, osm_id, name, category, type, lat, lon, street,
        city_id, administrative_zone_id, country_id, created_at, updated_at
    )
    VALUES %s
    ON CONFLICT (osm_id) DO UPDATE SET
        name                   = EXCLUDED.name,
        category               = EXCLUDED.category,
        type                   = EXCLUDED.type,
        lat                    = EXCLUDED.lat,
        lon                    = EXCLUDED.lon,
        street                 = EXCLUDED.street,
        city_id                = EXCLUDED.city_id,
        administrative_zone_id = EXCLUDED.administrative_zone_id,
        updated_at             = NOW()
    RETURNING (xmax = 0) AS inserted
"""
_TEMPLATE = "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())"


def _resolve_pbf(raw: str) -> Path:
    p = Path(raw)
    if not p.is_absolute():
        p = _REPO_ROOT / p
    return p


def load_cities(cur, dept: str | None) -> dict[str, tuple[str, str]]:
    """{insee_code: (city_id, department_code)}."""
    if dept:
        cur.execute(
            "SELECT insee_code, id, department_code FROM cities WHERE department_code = %s",
            (dept,),
        )
    else:
        cur.execute("SELECT insee_code, id, department_code FROM cities")
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def resolve_geo_refs(cur) -> tuple[str, dict[str, str]]:
    cur.execute("SELECT id FROM countries WHERE iso_code = 'FR'")
    row = cur.fetchone()
    if not row:
        raise RuntimeError("countries row with iso_code='FR' not found - run seed_cities first")
    france_id = row[0]

    cur.execute("SELECT code, id FROM administrative_zones WHERE type = 'department'")
    dept_zone_ids = {r[0]: r[1] for r in cur.fetchall()}
    return france_id, dept_zone_ids


def build_rows(
    pois: list[dict],
    locator: CommuneLocator,
    cities: dict[str, tuple[str, str]],
    france_id: str,
    dept_zone_ids: dict[str, str],
) -> tuple[dict[int, tuple], int]:
    """Geo-assign + filter to known communes. Returns ({osm_id: row}, dropped_no_commune)."""
    rows: dict[int, tuple] = {}
    dropped_no_commune = 0
    total = len(pois)
    for i, p in enumerate(pois, 1):
        if i % 50000 == 0:
            print(f"  geo-assign {i}/{total} ...")
        insee = locator.locate(p["lon"], p["lat"])
        if insee is None or insee not in cities:
            dropped_no_commune += 1
            continue
        city_id, dept_code = cities[insee]
        rows[p["osm_id"]] = (
            str(uuid.uuid4()),
            p["osm_id"],
            p["name"],
            p["category"],
            p["type"],
            p["lat"],
            p["lon"],
            p["street"],
            city_id,
            dept_zone_ids.get(dept_code),
            france_id,
        )
    return rows, dropped_no_commune


def upsert(conn, rows: list[tuple]) -> tuple[int, int]:
    inserted = updated = 0
    with conn.cursor() as cur:
        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start : start + BATCH_SIZE]
            res = psycopg2.extras.execute_values(
                cur, UPSERT_SQL, batch, template=_TEMPLATE, fetch=True
            )
            ins = sum(1 for r in res if r[0])
            inserted += ins
            updated += len(res) - ins
            conn.commit()
            print(f"  upserted {start + len(batch)}/{len(rows)} ({inserted} new, {updated} updated)")
    return inserted, updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pbf", default=str(DEFAULT_PBF), help="path to a Geofabrik .osm.pbf")
    parser.add_argument("--dept", default=None, help="restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true", help="print stats, write nothing")
    args = parser.parse_args()

    pbf = _resolve_pbf(args.pbf)
    if not pbf.exists():
        print(f"pbf not found: {pbf}")
        print("Download it from https://download.geofabrik.de/europe/france/ (or france-latest for the national run)")
        sys.exit(1)

    print(f"Streaming {pbf.name} ...")
    pois, scan = stream_pois(str(pbf))
    print(
        f"  scanned {scan['nodes_scanned'] + scan['ways_scanned']} elements "
        f"({scan['nodes_scanned']} nodes + {scan['ways_scanned']} ways)"
    )
    print(f"  kept after whitelist: {scan['kept']}   dropped for no-name: {scan['dropped_no_name']}")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cities = load_cities(cur, args.dept)
            france_id, dept_zone_ids = resolve_geo_refs(cur)
        print(
            f"  cities in scope: {len(cities)}"
            + (f" (dept {args.dept})" if args.dept else " (all France)")
        )
        if not cities:
            print("No cities in scope - run seed_cities first (or check --dept).")
            sys.exit(1)

        print("Assigning POIs to communes ...")
        locator = CommuneLocator(dept_codes=[args.dept] if args.dept else None)
        rows_by_id, dropped_no_commune = build_rows(
            pois, locator, cities, france_id, dept_zone_ids
        )
        rows = list(rows_by_id.values())

        cat_counts = Counter(r[3] for r in rows)
        geo = locator.stats

        print("\n=== Summary ===")
        print(f"  pbf elements scanned    : {scan['nodes_scanned'] + scan['ways_scanned']}")
        print(f"  kept after whitelist    : {scan['kept']}")
        print(f"  dropped (no name)       : {scan['dropped_no_name']}")
        print(f"  dropped (no commune)    : {dropped_no_commune}")
        print(f"  -> POIs to upsert        : {len(rows)}")
        print("  by category:")
        for cat in CATEGORIES:
            print(f"    {cat:<11}: {cat_counts.get(cat, 0)}")
        assigned = geo["polygon"] + geo["nearest"] + geo["dropped"]
        if assigned:
            print("  geo-assignment hit rate:")
            print(f"    polygon        : {geo['polygon']} ({100*geo['polygon']/assigned:.1f}%)")
            print(f"    nearest (<2km) : {geo['nearest']} ({100*geo['nearest']/assigned:.1f}%)")
            print(f"    dropped        : {geo['dropped']} ({100*geo['dropped']/assigned:.1f}%)")

        if args.dry_run:
            print("\n[DRY RUN] sample rows (name | category | type | lat | lon | insee-city_id):")
            for r in rows[:8]:
                print(f"    {r[2]} | {r[3]} | {r[4]} | {r[5]:.5f} | {r[6]:.5f} | {r[8]}")
            print(f"\n[DRY RUN] would upsert {len(rows)} POIs. Nothing written.")
            return

        if not rows:
            print("Nothing to upsert.")
            return

        print(f"\nUpserting {len(rows)} POIs (batch {BATCH_SIZE}) ...")
        inserted, updated = upsert(conn, rows)
        print(f"\nDone. {inserted} inserted, {updated} updated (total {inserted + updated}).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
