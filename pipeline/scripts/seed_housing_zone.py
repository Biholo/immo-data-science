"""
Seed housing_zone (zonage ABC tension locative) into cities table.
Also derives high_demand_zone = housing_zone IN (A, A_BIS).

Source: logement-liste-des-communes-selon-le-zonage-abc.csv
  Col 0: Code commune (INSEE)
  Col 1: Département
  Col 2: Commune (nom)
  Col 3: Zonage en vigueur (A, Abis, B1, B2, C)
  Col 4: Reclassement (Oui/Non) — ignoré

Note: fichier source = Île-de-France uniquement (75,77,78,91,92,93,94,95).
Pas de zone C dans ce fichier (IDF = zone tendue partout).

Run:
  python -m pipeline.scripts.seed_housing_zone [--csv path] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DEFAULT_CSV = str(
    Path(__file__).parent.parent.parent
    / "csv"
    / "logement-liste-des-communes-selon-le-zonage-abc.csv"
)

ZONE_MAP = {
    "Abis": "A_BIS",
    "A": "A",
    "B1": "B1",
    "B2": "B2",
    "C": "C",
}

HIGH_DEMAND_ZONES = {"A", "A_BIS"}


def load_zones(csv_path: str) -> dict[str, str]:
    """Returns {insee_code: zone_enum_value}."""
    zones: dict[str, str] = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader)  # header
        for row in reader:
            code = row[0].strip().zfill(5)
            raw_zone = row[3].strip()
            zone = ZONE_MAP.get(raw_zone)
            if zone:
                zones[code] = zone
    return zones


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Loading {args.csv} ...")
    zones = load_zones(args.csv)
    print(f"  {len(zones)} communes with zonage data")

    counts = Counter(zones.values())
    for z, n in sorted(counts.items()):
        print(f"  {z}: {n} communes")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set"); sys.exit(1)

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    codes = list(zones.keys())
    dept_clause = "AND department_code = %s" if args.dept else ""
    params = (codes, args.dept) if args.dept else (codes,)
    cur.execute(
        f"SELECT insee_code, id FROM cities WHERE insee_code = ANY(%s) {dept_clause}",
        params,
    )
    id_map = {row[0]: row[1] for row in cur.fetchall()}
    print(f"  {len(id_map)} / {len(zones)} communes matched in DB")

    rows = [
        (zones[code], zones[code] in HIGH_DEMAND_ZONES, id_map[code])
        for code in zones if code in id_map
    ]

    if args.dry_run:
        print(f"[DRY RUN] Would update {len(rows)} cities")
        n_high = sum(1 for _, high, _ in rows if high)
        print(f"  high_demand_zone=true for {n_high} / {len(rows)} cities")
        cur.close(); conn.close()
        return

    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE cities SET
            housing_zone     = data.zone::"HousingZone",
            high_demand_zone = data.high_demand_zone,
            updated_at       = NOW()
        FROM (VALUES %s) AS data(zone, high_demand_zone, id)
        WHERE cities.id = data.id
        """,
        rows,
        template="(%s, %s, %s)",
    )
    conn.commit()
    print(f"Done. {len(rows)} cities updated with housing_zone / high_demand_zone.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
