"""
Seed French communes from geo.api.gouv.fr into the cities table.
Also seeds administrative_zones (regions + departments) and countries if not present.

Run AFTER setting DATABASE_URL in .env:
  python -m pipeline.scripts.seed_cities [--dry-run] [--dept 75,69,33]

Source: https://geo.api.gouv.fr/communes
~35,000 communes, includes coordinates, population, dept/region.

Order of operations:
  1. Upsert France in countries
  2. Upsert regions → administrative_zones (level=1)
  3. Upsert departments → administrative_zones (level=2, parent=region)
  4. Upsert communes → cities (linked to country + dept administrative_zone)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from urllib.request import urlopen
from urllib.error import URLError

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

GEO_API = (
    "https://geo.api.gouv.fr/communes"
    "?fields=nom,code,departement,region,population,centre,codesPostaux"
    "&format=json"
)

ZONE_TABLE = "administrative_zones"
CITY_TABLE = "cities"


def fetch_communes(dept_filter: list[str] | None) -> list[dict]:
    print("Fetching communes from geo.api.gouv.fr ...")
    if dept_filter:
        # API only accepts one codeDepartement at a time
        all_data: list[dict] = []
        for dept in dept_filter:
            url = GEO_API + f"&codeDepartement={dept}"
            try:
                with urlopen(url, timeout=60) as r:
                    data = json.loads(r.read())
                all_data.extend(data)
                print(f"  dept {dept}: {len(data)} communes")
            except URLError as e:
                print(f"Network error for dept {dept}: {e}")
                sys.exit(1)
        print(f"  Total: {len(all_data)} communes fetched")
        return all_data
    else:
        try:
            with urlopen(GEO_API, timeout=120) as r:
                data = json.loads(r.read())
        except URLError as e:
            print(f"Network error: {e}")
            sys.exit(1)
        print(f"  {len(data)} communes fetched")
        return data


def ensure_france(conn, cur) -> str:
    """Upsert France in countries, return its id."""
    cur.execute("""
        INSERT INTO countries (id, name, iso_code, currency, created_at, updated_at)
        VALUES (gen_random_uuid()::text, 'France', 'FR', 'EUR', NOW(), NOW())
        ON CONFLICT DO NOTHING
    """)
    cur.execute("SELECT id FROM countries WHERE iso_code = 'FR'")
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Failed to upsert France in countries table.")
    return row[0]


def upsert_zones(
    conn, cur, communes: list[dict], france_id: str
) -> tuple[dict[str, str], dict[str, str]]:
    """
    Upsert regions (level=1) then departments (level=2, with parent_id).
    Returns (region_id_map, dept_id_map) keyed by INSEE code.
    UNIQUE constraint: (code, type) — avoids region/dept code collisions (e.g. both have code '11').
    """
    regions: dict[str, str] = {}          # code → name
    depts: dict[str, tuple[str, str]] = {}  # code → (name, region_code)

    for c in communes:
        r = c.get("region", {})
        d = c.get("departement", {})
        if r.get("code"):
            regions[r["code"]] = r.get("nom", "")
        if d.get("code"):
            depts[d["code"]] = (d.get("nom", ""), r.get("code", ""))

    region_ids: dict[str, str] = {}
    dept_ids: dict[str, str] = {}

    for code, name in regions.items():
        cur.execute(
            "SELECT id FROM administrative_zones WHERE code = %s AND type = 'region'",
            (code,),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE administrative_zones SET name = %s, updated_at = NOW() WHERE id = %s",
                (name, row[0]),
            )
            region_ids[code] = row[0]
        else:
            zone_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO administrative_zones
                    (id, code, name, type, country_id, level, level_name,
                     french_name, english_name, created_at, updated_at)
                VALUES (%s, %s, %s, 'region', %s, 1, 'region', %s, %s, NOW(), NOW())
                RETURNING id
            """, (zone_id, code, name, france_id, name, name))
            region_ids[code] = cur.fetchone()[0]

    for code, (name, region_code) in depts.items():
        parent_id = region_ids.get(region_code)
        cur.execute(
            "SELECT id FROM administrative_zones WHERE code = %s AND type = 'department'",
            (code,),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE administrative_zones SET name = %s, parent_id = %s, updated_at = NOW() WHERE id = %s",
                (name, parent_id, row[0]),
            )
            dept_ids[code] = row[0]
        else:
            zone_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO administrative_zones
                    (id, code, name, type, country_id, parent_id, level, level_name,
                     french_name, english_name, created_at, updated_at)
                VALUES (%s, %s, %s, 'department', %s, %s, 2, 'department', %s, %s, NOW(), NOW())
                RETURNING id
            """, (zone_id, code, name, france_id, parent_id, name, name))
            dept_ids[code] = cur.fetchone()[0]

    print(f"  Zones: {len(region_ids)} regions + {len(dept_ids)} departments upserted")
    return region_ids, dept_ids


def upsert_cities(
    conn,
    cur,
    communes: list[dict],
    france_id: str,
    dept_ids: dict[str, str],
    dry_run: bool,
) -> int:
    rows = []
    for c in communes:
        code = c.get("code", "")
        name = c.get("nom", "")
        dept_code = c.get("departement", {}).get("code", "")
        pop = c.get("population")
        postal = (c.get("codesPostaux") or [""])[0] or None

        coords = c.get("centre", {}).get("coordinates")
        lat = coords[1] if coords else None
        lon = coords[0] if coords else None

        zone_id = dept_ids.get(dept_code)

        rows.append((
            str(uuid.uuid4()),  # id
            name,               # name
            name,               # english_name (no translation available)
            name,               # french_name
            name,               # normalized_name
            france_id,          # country_id
            pop,                # population
            postal,             # postalCode
            lat,                # latitude
            lon,                # longitude
            "",                 # osmId — NOT NULL, use empty string as placeholder
            zone_id,            # administrative_zone_id
            dept_code,          # department_code
            code,               # insee_code
        ))

    if dry_run:
        print(f"  [DRY RUN] Would upsert {len(rows)} cities")
        if rows:
            sample = rows[0]
            print(f"  Sample: name={sample[1]}, insee={sample[13]}, dept={sample[12]}, postal={sample[7]}")
        return len(rows)

    # Split into INSERT (new) vs UPDATE (existing) — avoids ON CONFLICT constraint dependency
    insee_codes = [r[13] for r in rows]
    cur.execute("SELECT insee_code, id FROM cities WHERE insee_code = ANY(%s)", (insee_codes,))
    existing = {r[0]: r[1] for r in cur.fetchall()}

    to_insert = [r for r in rows if r[13] not in existing]
    to_update = [r for r in rows if r[13] in existing]

    if to_insert:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO cities (
                id, name, english_name, french_name, normalized_name,
                country_id, population, "postalCode", latitude, longitude,
                "osmId", administrative_zone_id, department_code, insee_code,
                is_premium, created_at, updated_at
            )
            VALUES %s
            """,
            [r + (False, datetime.now(), datetime.now()) for r in to_insert],
            template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            page_size=500,
        )

    if to_update:
        psycopg2.extras.execute_values(
            cur,
            """
            UPDATE cities SET
                name            = data.name,
                french_name     = data.french_name,
                population      = data.population,
                "postalCode"    = COALESCE(data.postal, cities."postalCode"),
                latitude        = COALESCE(data.lat, cities.latitude),
                longitude       = COALESCE(data.lon, cities.longitude),
                department_code = data.dept_code,
                administrative_zone_id = COALESCE(data.zone_id, cities.administrative_zone_id),
                updated_at      = NOW()
            FROM (VALUES %s) AS data(
                name, french_name, population, postal, lat, lon,
                dept_code, zone_id, insee_code
            )
            WHERE cities.insee_code = data.insee_code
            """,
            [(r[1], r[3], r[6], r[7], r[8], r[9], r[12], r[11], r[13]) for r in to_update],
            template="(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            page_size=500,
        )

    conn.commit()
    print(f"  {len(to_insert)} inserted, {len(to_update)} updated")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dept", default=None, help="Comma-separated dept codes (e.g. 75,69,33)")
    args = parser.parse_args()

    dept_filter = args.dept.split(",") if args.dept else None
    communes = fetch_communes(dept_filter)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    try:
        print("Ensuring France in countries...")
        france_id = ensure_france(conn, cur)
        if not args.dry_run:
            conn.commit()
        print(f"  France id: {france_id}")

        print("Upserting administrative zones...")
        _region_ids, dept_ids = upsert_zones(conn, cur, communes, france_id)
        if not args.dry_run:
            conn.commit()

        print("Upserting cities...")
        n = upsert_cities(conn, cur, communes, france_id, dept_ids, args.dry_run)

        print(f"\nDone. {n} cities seeded.")

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
