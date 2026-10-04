"""
Derive cities.tenant_profile -- a short categorical descriptor of the typical
renter in a commune. There is NO open-data source for this field; it is a
rule-based derivation from data already in this repo.

Signals used (all commune level, ARR aggregated to commune principale):
  - age structure from csv/base-ic-evol-struct-pop/base-ic-evol-struct-pop-{year}.CSV
    (same file seed_median_age.py reads), latest year available:
      s_young_adult = POP1824 / POP     share aged 18-24
      s_prime       = POP2539 / POP     share aged 25-39
      s_child       = POP0014 / POP     share aged 0-14
      s_senior      = POP65P  / POP     share aged 65+
  - r_student = cities.student_count / cities.population   (higher-ed enrolment
    share; only a strong signal where student_count is a real spike -- note
    student_count is presently seeded for dept 77 only, so outside 77 rule 1
    below effectively never fires and the age-structure rules carry the label)

Rule (first match wins). Labels are French, matching the dashboard examples
("etudiants", "jeunes actifs", "familles"):

  1. r_student      >= 0.15   -> "etudiants"        real university town
  2. s_young_adult  >= 0.14   -> "etudiants"        >14% of pop is 18-24
  3. s_senior       >= 0.32   -> "retraites"        aging commune, thin rental demand
  4. s_child        >= 0.20 and s_child >= 0.85*s_prime  -> "familles"
  5. s_prime        >= 0.20 and s_prime > s_child         -> "jeunes actifs"
  6. else                     -> "familles"    (safest majority label for a
                                                French commune with children)

Communes with no age-structure row (no population) are left untouched (NULL).

Besides the dominant label, the full distribution is stored in
cities.tenant_profile_breakdown (JSONB, English keys, percentages summing to 100):

  students             POP1824                      18-24
  young_professionals  POP2539                      25-39
  families             POP0002+0305+0610+1117+4054  children + parent-age adults (0-17, 40-54)
  pre_retirees         POP5564                      55-64
  retirees             POP6579+POP80P               65+

IMPORTANT: this is a PROXY. It is the age structure of the WHOLE commune population,
not of the tenant households (INSEE's tenant-by-age-of-reference-person table is not
in this repo). The frontend labels it accordingly.

This is an approximation, not a measured statistic. Thresholds were eyeballed
against dept 77 and national percentiles (median commune: s_senior~0.21,
s_child~0.18, s_prime~0.18). Re-tune if the label mix looks wrong for a region.

Run:
  python -m pipeline.scripts.seed_tenant_profile [--dept 77] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from ..services.geo import ARR_TO_COMMUNE

load_dotenv()

CSV_DIR = Path(__file__).parent.parent.parent / "csv" / "base-ic-evol-struct-pop"

# thresholds -- see module docstring
T_STUDENT_RATIO   = 0.15
T_YOUNG_ADULT     = 0.14
T_SENIOR          = 0.32
T_CHILD_FAMILY    = 0.20
T_PRIME_YOUNG     = 0.20
T_CHILD_FALLBACK  = 0.18


def _latest_year_csv(csv_dir: Path = CSV_DIR) -> tuple[Path, int] | None:
    best: tuple[Path, int] | None = None
    for csv_file in csv_dir.glob("*.CSV"):
        m = re.search(r"(\d{4})", csv_file.name)
        if not m:
            continue
        year = int(m.group(1))
        if best is None or year > best[1]:
            best = (csv_file, year)
    return best


def _f(row: list[str], i: int) -> float:
    try:
        return float(row[i]) if row[i].strip() else 0.0
    except (ValueError, IndexError):
        return 0.0


def load_age_shares(csv_dir: Path = CSV_DIR) -> tuple[dict[str, dict], int]:
    """Returns ({insee_code: {pop, s_young_adult, s_prime, s_child, s_senior}}, year)."""
    found = _latest_year_csv(csv_dir)
    if not found:
        return {}, 0
    path, year = found
    yy = str(year)[-2:]

    brackets = sorted({b for group in BREAKDOWN_BRACKETS.values() for b in group})

    # [pop, p1824, p2539, p0014, p65p, *brackets]
    n_base = 5
    acc: dict[str, list[float]] = defaultdict(lambda: [0.0] * (n_base + len(brackets)))
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        i_com = header.index("COM")
        cols = [
            header.index(f"P{yy}_POP"),
            header.index(f"P{yy}_POP1824"),
            header.index(f"P{yy}_POP2539"),
            header.index(f"P{yy}_POP0014"),
            header.index(f"P{yy}_POP65P"),
            *[header.index(f"P{yy}_{b}") for b in brackets],
        ]
        for row in reader:
            com = row[i_com].strip().zfill(5)
            com = ARR_TO_COMMUNE.get(com, com)
            a = acc[com]
            for bi, ci in enumerate(cols):
                a[bi] += _f(row, ci)

    out: dict[str, dict[str, float]] = {}
    for com, vals in acc.items():
        pop, p1824, p2539, p0014, p65p = vals[:n_base]
        if pop <= 0:
            continue
        by_bracket = dict(zip(brackets, vals[n_base:]))
        group_sums = {k: sum(by_bracket[b] for b in group) for k, group in BREAKDOWN_BRACKETS.items()}
        total = sum(group_sums.values())
        out[com] = {
            "pop": pop,
            "s_young_adult": p1824 / pop,
            "s_prime": p2539 / pop,
            "s_child": p0014 / pop,
            "s_senior": p65p / pop,
            # percentages summing to ~100 (rounded to 1 decimal); empty when brackets are missing
            "breakdown": {k: round(v / total * 100, 1) for k, v in group_sums.items()} if total > 0 else {},
        }
    return out, year


# English breakdown keys -> INSEE age-bracket columns (without the P{yy}_ prefix)
BREAKDOWN_BRACKETS: dict[str, tuple[str, ...]] = {
    "students": ("POP1824",),
    "young_professionals": ("POP2539",),
    "families": ("POP0002", "POP0305", "POP0610", "POP1117", "POP4054"),
    "pre_retirees": ("POP5564",),
    "retirees": ("POP6579", "POP80P"),
}


def classify(shares: dict, r_student: float | None) -> str:
    if r_student is not None and r_student >= T_STUDENT_RATIO:
        return "etudiants"
    if shares["s_young_adult"] >= T_YOUNG_ADULT:
        return "etudiants"
    if shares["s_senior"] >= T_SENIOR:
        return "retraites"
    if shares["s_child"] >= T_CHILD_FAMILY and shares["s_child"] >= 0.85 * shares["s_prime"]:
        return "familles"
    if shares["s_prime"] >= T_PRIME_YOUNG and shares["s_prime"] > shares["s_child"]:
        return "jeunes actifs"
    return "familles"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to dept code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    print("Loading age structure from base-ic-evol-struct-pop ...")
    shares_by_insee, year = load_age_shares()
    print(f"  year {year}: {len(shares_by_insee)} communes")

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='cities' AND column_name='tenant_profile'"
        )
        if not cur.fetchone():
            print("  NOTE: cities.tenant_profile column missing -- schema owner must add it")

        dept_clause = "WHERE department_code = %s" if args.dept else ""
        params = [args.dept] if args.dept else []
        cur.execute(
            f"SELECT insee_code, id, population, student_count FROM cities {dept_clause}",
            params,
        )
        city_rows = cur.fetchall()
        print(f"  {len(city_rows)} cities in scope")

        updates: list[tuple[str, str, str]] = []
        label_counts: Counter[str] = Counter()
        no_age = 0
        for insee, cid, population, student_count in city_rows:
            shares = shares_by_insee.get(insee)
            if not shares:
                no_age += 1
                continue
            r_student = (
                student_count / population
                if student_count is not None and population
                else None
            )
            label = classify(shares, r_student)
            label_counts[label] += 1
            updates.append((label, json.dumps(shares["breakdown"]) if shares["breakdown"] else None, cid))

        print(f"\n  {len(updates)} cities classified, {no_age} skipped (no age-structure row)")
        print(f"  label distribution: {dict(label_counts)}")

        if args.dry_run:
            sample = updates[:15]
            cur.execute(
                "SELECT id, name FROM cities WHERE id = ANY(%s)",
                ([c for _, _, c in sample],),
            )
            names = {r[0]: r[1] for r in cur.fetchall()}
            print("\n  sample:")
            for label, breakdown, cid in sample:
                print(f"    {names.get(cid, cid):30s} -> {label:14s} {breakdown}")
            print("\n[DRY RUN] no writes")
            return

        psycopg2.extras.execute_values(
            cur,
            """
            UPDATE cities SET
                tenant_profile           = data.label,
                tenant_profile_breakdown = data.breakdown::jsonb,
                updated_at               = NOW()
            FROM (VALUES %s) AS data(label, breakdown, id)
            WHERE cities.id = data.id
            """,
            updates,
            template="(%s, %s, %s)",
        )
        conn.commit()
        print(f"\nDone. {len(updates)} cities updated with tenant_profile.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
