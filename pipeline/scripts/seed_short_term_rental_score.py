"""
Seed cities.short_term_rental_score — a 0..100 COMPOSITE score of how
attractive / active a commune is for short-term furnished rental (Airbnb-style
"location meublée de courte durée").

No single source gives this number; it is assembled from three inputs, all of
which are already downloaded into csv/location-courte-duree/ (+ two files that
already live in the repo for other pipelines):

  1. InsideAirbnb per-area "listings.csv.gz" (detailed file)
     https://insideairbnb.com/get-the-data/  ->  https://data.insideairbnb.com/...
     French coverage is SMALL and shrinking. As scraped 2026-09-10 only 4 areas
     are published:
        - Paris            france/ile-de-france/paris/2026-06-16
        - Lyon             france/auvergne-rhone-alpes/lyon/2026-06-22
        - Bordeaux (Métropole, ~36 communes)
                           france/nouvelle-aquitaine/bordeaux/2026-06-22
        - Pays Basque (CA Pays Basque, ~152 communes, dépt 64)
                           france/pyrénées-atlantiques/pays-basque/2026-06-23
     Marseille / Nice / Côte d'Azur / the Alps / Brittany / anywhere rural: NO
     InsideAirbnb data at all. This is the single biggest limitation of this
     score — see "confidence" below.
     Fields used per listing: latitude, longitude, room_type, availability_365,
     number_of_reviews_ltm, has_availability.

  2. "Stations classées et communes touristiques - France" (Atout France, via
     OpenDataSoft), csv/location-courte-duree/communes-touristiques.csv
     https://public.opendatasoft.com/explore/dataset/economicref-france-commune-classement-touristique/
     1587 communes, each flagged "Station classée de tourisme" (strongest) or
     "Commune touristique". Reference year 2022 (the canonical open list; the
     official list drifts slowly). National coverage.

  3. Housing-market tension, from two files already in the repo:
       - csv/zonage-abc-national.csv (zonage ABC, arrêté du 23 juin 2026,
         ~34.9k communes — same file seed_housing_zone.py uses)
       - csv/reglementation-locative/rent-control.csv (~70 communes with
         encadrement des loyers — same file seed_rent_regulation.py uses)
     A tense / rent-controlled / touristic commune is exactly where STR
     conversion pressure and the numéro-d'enregistrement / changement-d'usage
     rules bite, so it is a strong STR-attractiveness signal even with zero
     Airbnb data. NB: the DB columns cities.high_demand_zone / rent_control are
     only seeded for Île-de-France today, so we read the national CSVs directly
     instead of the columns.

-------------------------------------------------------------------------------
COMPOSITE FORMULA  (score in 0..100, stored as double precision)

Per-commune sub-scores, each in 0..1:

  D  STR density   (InsideAirbnb only)
       active listings per 1000 residents, sqrt-compressed:
         D = min(1, sqrt(active_per_1000 / 60))
       "active" = has_availability != 'f' AND
                  (availability_365 > 0 OR number_of_reviews_ltm > 0)
       Residents (cities.population) are used, not dwellings — dwelling counts
       are not on the cities table. Documented approximation.

  E  Entire-home share (InsideAirbnb only)
       fraction of active listings with room_type == 'Entire home/apt'.
       High share = professionalised whole-unit STR (investment-grade) rather
       than spare-room home-sharing.

  T  Tourism classification (national)
       Station classée de tourisme -> 1.00
       Commune touristique         -> 0.55
       neither                     -> 0.00

  Z  Housing-market tension (national)
       in rent-control.csv         -> 1.00
       zonage A_BIS                -> 0.90
       zonage A                    -> 0.75
       zonage B1                   -> 0.45
       zonage B2                   -> 0.15
       zonage C / absent           -> 0.00

Blend depends on whether InsideAirbnb covers the commune:

  covered:      score = 100 * (0.42*D + 0.18*E + 0.22*T + 0.18*Z)
                confidence = "high"

  not covered:  raw   = 0.62*T + 0.38*Z
                score = 100 * 0.80 * raw          (hard cap 80: a commune with
                                                   no measured STR data can
                                                   never outrank a measured one)
                if T == 0 and Z <= 0.15:  score = 0
                confidence = "low"

Rationale for weights: with real data, observed STR intensity (D) dominates,
tempered by how "investor-shaped" the stock is (E); the two flags (T, Z) add
context and keep regulated hotspots from being under-rated. Without data we
have only the flags, and we deliberately keep those communes below the
measured field.

CONFIDENCE: there is no confidence column on `cities` and the Prisma schema is
out of scope, so reliability is NOT stored. It is reported in --dry-run output
and written to a sidecar csv/location-courte-duree/str_score_confidence.csv
(insee_code, score, confidence, D, E, T, Z, covered). Treat every "low"
(≈ 34.7k communes, everything outside the 4 InsideAirbnb areas) as a
flag-only estimate.

-------------------------------------------------------------------------------
Run:
  python -m pipeline.scripts.seed_short_term_rental_score --dept 75 --dry-run
  python -m pipeline.scripts.seed_short_term_rental_score --dept 15 --dry-run
  python -m pipeline.scripts.seed_short_term_rental_score            # REAL, all France
"""

from __future__ import annotations

import argparse
import csv
import gzip
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

from ..services.geo import ARR_TO_COMMUNE

load_dotenv()

REPO = Path(__file__).parent.parent.parent
STR_DIR = REPO / "csv" / "location-courte-duree"
ZONAGE_CSV = REPO / "csv" / "zonage-abc-national.csv"
RENT_CONTROL_CSV = REPO / "csv" / "reglementation-locative" / "rent-control.csv"
CONF_SIDECAR = STR_DIR / "str_score_confidence.csv"

# InsideAirbnb detailed files present in STR_DIR and how to resolve a listing to
# a commune:
#   mode "single"  -> the whole area is one commune (insee given)
#   mode "nearest" -> assign each listing to the nearest commune centroid among
#                     cities in `depts` (cities table already holds consolidated
#                     Paris/Lyon/Marseille codes, so no arrondissement leakage)
INSIDEAIRBNB_FILES = [
    {"file": "insideairbnb-paris-listings.csv.gz",       "mode": "single",  "insee": "75056"},
    {"file": "insideairbnb-lyon-listings.csv.gz",        "mode": "single",  "insee": "69123"},
    {"file": "insideairbnb-bordeaux-listings.csv.gz",    "mode": "nearest", "depts": ["33"]},
    {"file": "insideairbnb-pays-basque-listings.csv.gz", "mode": "nearest", "depts": ["64"]},
]

DENSITY_CAP = 60.0          # active listings / 1000 residents that saturates D
NOCOV_CAP = 0.80           # flag-only score ceiling (× 100)

W_COV = {"D": 0.42, "E": 0.18, "T": 0.22, "Z": 0.18}
W_NOCOV = {"T": 0.62, "Z": 0.38}

ZONE_TENSION = {"Abis": 0.90, "A_BIS": 0.90, "A": 0.75, "B1": 0.45, "B2": 0.15, "C": 0.0}
TOURISM_SCORE = {"station": 1.0, "commune": 0.55}


# ─────────────────────────────── source loaders ────────────────────────────────

def _int(v: str) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _is_active(row: dict) -> bool:
    if (row.get("has_availability") or "").strip().lower() == "f":
        return False
    return _int(row.get("availability_365")) > 0 or _int(row.get("number_of_reviews_ltm")) > 0


def load_tourism_flags() -> dict[str, str]:
    """{insee_code: 'station' | 'commune'} from the Atout France list."""
    path = STR_DIR / "communes-touristiques.csv"
    out: dict[str, str] = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f, delimiter=";"):
            code = (row.get("com_code_source") or "").strip().zfill(5)
            if not code or code == "00000":
                continue
            code = ARR_TO_COMMUNE.get(code, code)
            typ = (row.get("com_tourism_type") or "").lower()
            if "station" in typ:
                out[code] = "station"
            elif "touristique" in typ:
                out.setdefault(code, "commune")
    return out


def load_zonage() -> dict[str, str]:
    """{insee_code: raw zone label} from the national zonage ABC file."""
    out: dict[str, str] = {}
    with open(ZONAGE_CSV, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        i_zone = next(i for i, h in enumerate(header) if h.strip().startswith("Zonage"))
        for row in reader:
            if not row or not row[0].strip():
                continue
            code = ARR_TO_COMMUNE.get(row[0].strip().zfill(5), row[0].strip().zfill(5))
            out[code] = row[i_zone].strip()
    return out


def load_rent_control() -> set[str]:
    """INSEE codes flagged rent_control=TRUE in the curated repo CSV."""
    codes: set[str] = set()
    with open(RENT_CONTROL_CSV, encoding="utf-8-sig") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#") and ln.strip()]
    for row in csv.DictReader(lines):
        if (row.get("rent_control") or "").strip().upper() == "TRUE":
            codes.add((row.get("insee_code") or "").strip().zfill(5))
    return codes


def load_insideairbnb(cur) -> dict[str, dict]:
    """{insee_code: {'active': int, 'entire': int}} aggregated across all files."""
    agg: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [active, entire_home]

    for spec in INSIDEAIRBNB_FILES:
        path = STR_DIR / spec["file"]
        if not path.exists():
            print(f"  WARNING: {path.name} missing — skipped")
            continue

        centroids: list[tuple[str, float, float]] = []
        if spec["mode"] == "nearest":
            cur.execute(
                "SELECT insee_code, latitude, longitude FROM cities "
                "WHERE department_code = ANY(%s) AND latitude IS NOT NULL",
                (spec["depts"],),
            )
            centroids = [(c, float(la), float(lo)) for c, la, lo in cur.fetchall()]

        n_active = 0
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not _is_active(row):
                    continue
                n_active += 1
                if spec["mode"] == "single":
                    code = spec["insee"]
                else:
                    try:
                        la, lo = float(row["latitude"]), float(row["longitude"])
                    except (TypeError, ValueError):
                        continue
                    kx = math.cos(math.radians(la))
                    code = min(
                        centroids,
                        key=lambda c: (la - c[1]) ** 2 + ((lo - c[2]) * kx) ** 2,
                    )[0]
                code = ARR_TO_COMMUNE.get(code, code)
                agg[code][0] += 1
                if row.get("room_type") == "Entire home/apt":
                    agg[code][1] += 1
        print(f"  {spec['file']}: {n_active} active listings")

    return {c: {"active": a, "entire": e} for c, (a, e) in agg.items()}


# ─────────────────────────────── scoring ──────────────────────────────────────

def score_commune(pop: int | None, ia: dict | None, tourism: str | None,
                  zone: str | None, rc: bool) -> dict:
    T = TOURISM_SCORE.get(tourism or "", 0.0)
    if rc:
        Z = 1.0
    else:
        Z = ZONE_TENSION.get((zone or "").strip(), 0.0)

    covered = ia is not None and ia["active"] > 0
    if covered:
        per_1000 = (ia["active"] / pop * 1000) if pop else 0.0
        D = min(1.0, math.sqrt(per_1000 / DENSITY_CAP)) if per_1000 > 0 else 0.0
        E = ia["entire"] / ia["active"]
        raw = W_COV["D"] * D + W_COV["E"] * E + W_COV["T"] * T + W_COV["Z"] * Z
        score = 100.0 * raw
        conf = "high"
    else:
        D = E = 0.0
        raw = W_NOCOV["T"] * T + W_NOCOV["Z"] * Z
        score = 100.0 * NOCOV_CAP * raw
        if T == 0.0 and Z <= 0.15:
            score = 0.0
        conf = "low"

    return {
        "score": round(score, 1),
        "confidence": conf,
        "covered": covered,
        "D": round(D, 3), "E": round(E, 3), "T": T, "Z": Z,
    }


# ─────────────────────────────── main ─────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 75)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-sidecar", action="store_true", help="skip writing str_score_confidence.csv")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set"); sys.exit(1)

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='cities' AND column_name='short_term_rental_score'"
    )
    col = cur.fetchone()
    print(f"cities.short_term_rental_score: {'present ' + col[1] if col else 'MISSING'}")

    print("Loading sources ...")
    tourism = load_tourism_flags()
    zonage = load_zonage()
    rent_control = load_rent_control()
    print(f"  communes touristiques: {len(tourism)}  "
          f"({sum(1 for v in tourism.values() if v == 'station')} stations classées)")
    print(f"  zonage ABC communes:   {len(zonage)}")
    print(f"  rent-control communes: {len(rent_control)}")
    ia = load_insideairbnb(cur)
    print(f"  InsideAirbnb: {len(ia)} communes with >=1 active listing")

    dept_clause = "WHERE department_code = %s" if args.dept else ""
    params = (args.dept,) if args.dept else ()
    cur.execute(
        f"SELECT id, insee_code, name, population FROM cities {dept_clause}", params
    )
    cities = cur.fetchall()
    print(f"\n{len(cities)} cities in scope"
          f"{' (dept ' + args.dept + ')' if args.dept else ''}")

    rows: list[tuple] = []
    detail: list[tuple] = []
    conf_counter: Counter = Counter()
    for cid, code, name, pop in cities:
        r = score_commune(pop, ia.get(code), tourism.get(code),
                          zonage.get(code), code in rent_control)
        rows.append((r["score"], cid))
        conf_counter[r["confidence"]] += 1
        detail.append((code, name, pop, r))

    print(f"  confidence: {dict(conf_counter)}")
    nonzero = [d for d in detail if d[3]["score"] > 0]
    print(f"  non-zero scores: {len(nonzero)} / {len(detail)}")

    top = sorted(detail, key=lambda d: -d[3]["score"])[:12]
    print("\n  Top communes by score:")
    for code, name, pop, r in top:
        print(f"    {code} {name:<26.26} score={r['score']:>5}  conf={r['confidence']:<4} "
              f"cov={int(r['covered'])} D={r['D']} E={r['E']} T={r['T']} Z={r['Z']}")

    if not args.no_sidecar:
        with open(CONF_SIDECAR, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["insee_code", "name", "population", "score", "confidence",
                        "covered", "D_density", "E_entire_home", "T_tourism", "Z_tension"])
            for code, name, pop, r in sorted(detail):
                w.writerow([code, name, pop, r["score"], r["confidence"],
                            int(r["covered"]), r["D"], r["E"], r["T"], r["Z"]])
        print(f"\n  sidecar written: {CONF_SIDECAR}")

    if args.dry_run:
        print(f"\n[DRY RUN] Would UPDATE cities.short_term_rental_score for {len(rows)} cities")
        if col is None:
            print("[DRY RUN] NOTE: column short_term_rental_score is MISSING — real run would fail")
        cur.close(); conn.close()
        return

    if col is None:
        print("Aborting real run: cities.short_term_rental_score does not exist.")
        cur.close(); conn.close(); sys.exit(1)

    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE cities SET
            short_term_rental_score = data.score,
            updated_at              = NOW()
        FROM (VALUES %s) AS data(score, id)
        WHERE cities.id = data.id
        """,
        rows,
        template="(%s::double precision, %s)",
    )
    conn.commit()
    print(f"Done. {len(rows)} cities updated with short_term_rental_score.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
