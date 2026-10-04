"""
Seed the INSEE distribution columns of `cities` (JSONB, English keys), one snapshot per commune.
They feed pie / pyramid charts, so only the latest census year is kept (no history).

Columns written:

  age_pyramid  { "male": {band: head-count}, "female": {band: head-count} }
      bands: 0_14, 15_29, 30_44, 45_59, 60_74, 75_plus
      source: csv/base-ic-evol-struct-pop  (P{yy}_H0014 ... P{yy}_F75P), latest year

  population_status_breakdown  percentages summing to ~100
      children          POP0014                       (evol-struct-pop)
      employed          ACTOCC1564                    (activite-residents, 15-64)
      unemployed        CHOM1564
      students          ETUD1564                      (pupils / students / trainees 15-64)
      early_retirees    RETR1564                      (retired or early-retired, 15-64)
      other_inactive    AINACT1564
      seniors_65_plus   POP65P                        (evol-struct-pop)
      The two sources must share the same year: the latest year present in BOTH is used.

  housing_type_breakdown  % of the dwelling stock (P{yy}_LOG)
      houses (MAISON), apartments (APPART), other (the rest)

  housing_occupancy_breakdown  % of the dwelling stock
      primary_residences (RP), secondary_residences (RSECOCC, includes occasional dwellings),
      vacant (LOGVAC)

  dwelling_size_breakdown  % of primary residences, by number of rooms
      one_room, two_rooms, three_rooms, four_rooms, five_plus_rooms   (RP_1P .. RP_5PP)
      source for the three housing breakdowns: csv/base-ic-logement, latest year

All files are IRIS-level: summed per commune, Paris/Lyon/Marseille arrondissements are
folded into their parent commune. A column is left NULL for a commune when its source has
no usable total.

Run:
  python -m pipeline.scripts.seed_city_breakdowns [--dept 77] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from ..services.geo import ARR_TO_COMMUNE

load_dotenv()

CSV_ROOT = Path(__file__).parent.parent.parent / "csv"
POP_DIR = CSV_ROOT / "base-ic-evol-struct-pop"
ACT_DIR = CSV_ROOT / "base-ic-activite-residents"
LOG_DIR = CSV_ROOT / "base-ic-logement"

AGE_BANDS = ["0014", "1529", "3044", "4559", "6074", "75P"]
AGE_BAND_KEYS = {"0014": "0_14", "1529": "15_29", "3044": "30_44", "4559": "45_59", "6074": "60_74", "75P": "75_plus"}

ROOM_KEYS = {"1P": "one_room", "2P": "two_rooms", "3P": "three_rooms", "4P": "four_rooms", "5PP": "five_plus_rooms"}


def _years(csv_dir: Path) -> dict[int, Path]:
    out: dict[int, Path] = {}
    for f in csv_dir.glob("*.CSV"):
        m = re.search(r"(\d{4})", f.name)
        if m:
            out[int(m.group(1))] = f
    return out


def _f(row: list[str], i: int) -> float:
    try:
        return float(row[i]) if row[i].strip() else 0.0
    except (ValueError, IndexError):
        return 0.0


def load_sums(path: Path, year: int, columns: list[str]) -> dict[str, dict[str, float]]:
    """{insee_code: {column: sum over IRIS}} for the P{yy}_/C{yy}_ columns named in `columns` (without prefix)."""
    yy = str(year)[-2:]
    acc: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        i_com = header.index("COM")
        idx = {c: header.index(f"P{yy}_{c}") for c in columns}
        for row in reader:
            com = row[i_com].strip().zfill(5)
            com = ARR_TO_COMMUNE.get(com, com)
            a = acc[com]
            for c, i in idx.items():
                a[c] += _f(row, i)
    return acc


def pct(parts: dict[str, float]) -> dict[str, float]:
    """Percentages (1 decimal) of `parts`, which should be exhaustive; {} when the total is 0."""
    total = sum(parts.values())
    return {k: round(v / total * 100, 1) for k, v in parts.items()} if total > 0 else {}


def build_age_pyramid() -> dict[str, dict]:
    years = _years(POP_DIR)
    year = max(years)
    cols = [f"H{b}" for b in AGE_BANDS] + [f"F{b}" for b in AGE_BANDS]
    print(f"  age_pyramid: base-ic-evol-struct-pop {year}")
    out: dict[str, dict] = {}
    for com, v in load_sums(years[year], year, cols).items():
        male = {AGE_BAND_KEYS[b]: round(v[f"H{b}"]) for b in AGE_BANDS}
        female = {AGE_BAND_KEYS[b]: round(v[f"F{b}"]) for b in AGE_BANDS}
        if sum(male.values()) + sum(female.values()) > 0:
            out[com] = {"male": male, "female": female}
    return out


def build_population_status() -> dict[str, dict]:
    pop_years, act_years = _years(POP_DIR), _years(ACT_DIR)
    year = max(set(pop_years) & set(act_years))
    print(f"  population_status_breakdown: evol-struct-pop + activite-residents {year}")
    pop = load_sums(pop_years[year], year, ["POP0014", "POP65P"])
    act = load_sums(act_years[year], year, ["ACTOCC1564", "CHOM1564", "ETUD1564", "RETR1564", "AINACT1564"])
    out: dict[str, dict] = {}
    for com, a in act.items():
        p = pop.get(com)
        if not p:
            continue
        shares = pct(
            {
                "children": p["POP0014"],
                "employed": a["ACTOCC1564"],
                "unemployed": a["CHOM1564"],
                "students": a["ETUD1564"],
                "early_retirees": a["RETR1564"],
                "other_inactive": a["AINACT1564"],
                "seniors_65_plus": p["POP65P"],
            }
        )
        if shares:
            out[com] = shares
    return out


def build_housing() -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    years = _years(LOG_DIR)
    year = max(years)
    print(f"  housing_*_breakdown, dwelling_size_breakdown: base-ic-logement {year}")
    cols = ["LOG", "MAISON", "APPART", "RP", "RSECOCC", "LOGVAC"] + [f"RP_{k}" for k in ROOM_KEYS]
    types: dict[str, dict] = {}
    occupancy: dict[str, dict] = {}
    sizes: dict[str, dict] = {}
    for com, v in load_sums(years[year], year, cols).items():
        if v["LOG"] > 0:
            other = max(v["LOG"] - v["MAISON"] - v["APPART"], 0.0)
            types[com] = pct({"houses": v["MAISON"], "apartments": v["APPART"], "other": other})
            occupancy[com] = pct({"primary_residences": v["RP"], "secondary_residences": v["RSECOCC"], "vacant": v["LOGVAC"]})
        room_shares = pct({key: v[f"RP_{k}"] for k, key in ROOM_KEYS.items()})
        if room_shares:
            sizes[com] = room_shares
    return types, occupancy, sizes


COLUMNS = [
    "age_pyramid",
    "population_status_breakdown",
    "housing_type_breakdown",
    "housing_occupancy_breakdown",
    "dwelling_size_breakdown",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to dept code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    print("Building distributions ...")
    types, occupancy, sizes = build_housing()
    by_column = {
        "age_pyramid": build_age_pyramid(),
        "population_status_breakdown": build_population_status(),
        "housing_type_breakdown": types,
        "housing_occupancy_breakdown": occupancy,
        "dwelling_size_breakdown": sizes,
    }
    for col, data in by_column.items():
        print(f"    {col}: {len(data)} communes")

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    try:
        dept_clause = "WHERE department_code = %s" if args.dept else ""
        cur.execute(f"SELECT insee_code, id, name FROM cities {dept_clause}", [args.dept] if args.dept else [])
        cities = cur.fetchall()

        rows = []
        for insee, cid, _ in cities:
            values = [json.dumps(by_column[c][insee]) if insee in by_column[c] else None for c in COLUMNS]
            rows.append((*values, cid))
        print(f"  {len(rows)} cities in scope")

        if args.dry_run:
            name_by_id = {cid: name for _, cid, name in cities}
            for r in rows[:3]:
                print(f"\n    {name_by_id[r[-1]]}")
                for col, val in zip(COLUMNS, r):
                    print(f"      {col}: {val}")
            print("\n[DRY RUN] no writes")
            return

        sets = ",\n                ".join(f"{c} = data.{c}::jsonb" for c in COLUMNS)
        psycopg2.extras.execute_values(
            cur,
            f"""
            UPDATE cities SET
                {sets},
                updated_at = NOW()
            FROM (VALUES %s) AS data({", ".join(COLUMNS)}, id)
            WHERE cities.id = data.id
            """,
            rows,
            template="(" + ", ".join(["%s"] * (len(COLUMNS) + 1)) + ")",
            page_size=1000,
        )
        conn.commit()
        print(f"\nDone. {len(rows)} cities updated.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
