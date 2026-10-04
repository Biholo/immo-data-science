"""
Compare a TARGET database (e.g. production) with a REFERENCE database (e.g. your complete local one)
and run ONLY the seed steps whose data is missing on the target.

  # 1. read-only report: what is missing on prod?
  python -m pipeline.scripts.seed_missing --target "postgresql://user:pass@prod-host:5432/rentium"

  # 2. run only the missing steps on prod (asks for the host name, nothing runs without --run)
  python -m pipeline.scripts.seed_missing --target "postgresql://..." --run

The reference is DATABASE_URL of the .env (your local base) unless --reference is given. The target URL
can also come from the TARGET_DATABASE_URL environment variable, so it never has to be typed in a shell
history.

How a step is judged missing: each step owns one or more probes (a COUNT on the table / column / serie
it fills). A step is MISSING when, for any of its probes, the target holds less than --ratio (default 98 %)
of what the reference holds. Probes are plain SELECT COUNTs: the report never writes anything.

Steps run through `seed_all --only ...` (so the usual order, "soft" steps and source-file checks apply)
with DATABASE_URL pointing at the target. Every step is idempotent (upsert, or delete + re-insert), so
re-running a step that was only partly loaded completes it without duplicating rows.

Steps that need raw files absent from this repo (run_dvf_* needs the ValeursFoncieres-*.txt files) cannot
be re-run: they are reported as "non relançable" with the reason, and have to be loaded another way.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(REPO_ROOT, ".env"))


def serie(name: str, level: str = "city_id") -> str:
    return f"SELECT COUNT(DISTINCT {level}) FROM series WHERE name::text = '{name}' AND {level} IS NOT NULL"


def column(name: str) -> str:
    return f"SELECT COUNT({name}) FROM cities"


@dataclass
class Probe:
    label: str
    sql: str


# step -> probes (a COUNT each). Order = seed_all order, which is also the order they are run in.
PROBES: dict[str, list[Probe]] = {
    "seed_cities": [Probe("communes", "SELECT COUNT(*) FROM cities"), Probe("zones administratives", "SELECT COUNT(*) FROM administrative_zones")],
    "seed_rp": [Probe("cities.student_count", column("student_count"))],
    "seed_transactions": [Probe("transactions", "SELECT COUNT(*) FROM transactions")],
    "seed_price_range_series": [Probe("série price_sqm_low", serie("price_sqm_low")), Probe("série sale_price_median", serie("sale_price_median"))],
    "seed_transaction_volume_typology": [Probe("série transaction_volume_t2", serie("transaction_volume_t2"))],
    "seed_city_sale_prices": [Probe("cities.sale_price_low", column("sale_price_low")), Probe("cities.median_sale_price", column("median_sale_price"))],
    "interpolate_price_range": [Probe("cities.price_range_estimated", "SELECT COUNT(*) FROM cities WHERE price_range_estimated"), Probe("séries estimées (CALC)", "SELECT COUNT(*) FROM series WHERE source::text = 'CALC'")],
    "seed_pop_series": [Probe("série population", serie("population"))],
    "seed_logement_series": [Probe("série vacancy_rate", serie("vacancy_rate")), Probe("série secondary_residence_rate", serie("secondary_residence_rate"))],
    "seed_rp_series": [Probe("série unemployment_rate", serie("unemployment_rate"))],
    "seed_employment_series": [Probe("série active_population", serie("active_population"))],
    "seed_fiscalite_series": [Probe("série property_tax_rate", serie("property_tax_rate"))],
    "seed_population_history_series": [Probe("série population_history", serie("population_history"))],
    "seed_commerce_series": [Probe("série retail_count", serie("retail_count"))],
    "seed_household_size_series": [Probe("série household_size_1p_rate", serie("household_size_1p_rate"))],
    "seed_rent_series": [Probe("série rent_sqm_all", serie("rent_sqm_all"))],
    "seed_median_income_series": [Probe("série median_income", serie("median_income"))],
    "seed_company_creations_series": [Probe("série company_creations", serie("company_creations"))],
    "seed_housing_zone": [Probe("cities.housing_zone", column("housing_zone"))],
    "seed_rent_regulation": [Probe("cities.rent_control", column("rent_control"))],
    "seed_short_term_rental_score": [Probe("cities.short_term_rental_score", column("short_term_rental_score"))],
    "seed_poi": [Probe("POI", "SELECT COUNT(*) FROM pois"), Probe("communes avec au moins un POI", "SELECT COUNT(DISTINCT city_id) FROM pois")],
    "seed_activity_counts_series": [Probe("cities.unemployed_count", column("unemployed_count")), Probe("cities.retired_count", column("retired_count"))],
    "seed_median_age": [Probe("cities.median_age", column("median_age"))],
    "seed_tenant_profile": [Probe("cities.tenant_profile_breakdown", column("tenant_profile_breakdown"))],
    "seed_city_breakdowns": [Probe("cities.age_pyramid", column("age_pyramid")), Probe("cities.housing_type_breakdown", column("housing_type_breakdown"))],
    "seed_student_breakdown": [Probe("cities.student_breakdown", column("student_breakdown"))],
    "seed_dashboard_fields": [Probe("cities.tenant_rate", column("tenant_rate"))],
    "seed_avg_property_tax": [Probe("cities.avg_property_tax", column("avg_property_tax"))],
    "seed_gross_yield": [Probe("cities.gross_yield", column("gross_yield"))],
    "seed_years_to_buy": [Probe("cities.years_to_buy", column("years_to_buy"))],
    "seed_housing_effort_rate": [Probe("cities.housing_effort_rate", column("housing_effort_rate"))],
    "seed_city_latest_snapshots": [Probe("cities.owner_rate", column("owner_rate")), Probe("cities.median_income", column("median_income"))],
}

# steps that cannot be re-run from this repo, with the probes that show they are incomplete
NOT_RERUNNABLE: dict[str, tuple[str, list[Probe]]] = {
    "run_dvf (séries DVF)": (
        "demande les fichiers bruts ValeursFoncieres-*.txt (absents de dvf-raw/) : à charger autrement, p. ex. copie des tables series/timeseries depuis la base de référence",
        [Probe("série price_sqm_all", serie("price_sqm_all")), Probe("série price_sqm_house", serie("price_sqm_house")), Probe("série transaction_volume", serie("transaction_volume")), Probe("série vefa_share", serie("vefa_share")), Probe("cities.median_price_per_sqm", column("median_price_per_sqm"))],
    ),
}


def scalar(cur, sql: str) -> int:
    try:
        cur.execute(sql)
        return int(cur.fetchone()[0] or 0)
    except psycopg2.Error as e:
        cur.connection.rollback()
        return -1 if "does not exist" in str(e) else 0


def poi_gaps(ref_cur, target_cur, ratio: float) -> list[tuple[str, int, int]]:
    """Departments where the target holds fewer POIs than the reference: [(dept, target, reference)]."""
    sql = "SELECT c.department_code, COUNT(*) FROM pois p JOIN cities c ON c.id = p.city_id GROUP BY 1"
    ref_cur.execute(sql)
    ref = dict(ref_cur.fetchall())
    try:
        target_cur.execute(sql)
        target = dict(target_cur.fetchall())
    except psycopg2.Error:
        target_cur.connection.rollback()
        target = {}
    return sorted((d, target.get(d, 0), n) for d, n in ref.items() if d and target.get(d, 0) < ratio * n)


def host_of(dsn: str) -> str:
    u = urlparse(dsn)
    return f"{u.hostname}:{u.port or 5432}/{(u.path or '').lstrip('/')}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run only the seed steps whose data is missing on the target database")
    parser.add_argument("--target", default=os.environ.get("TARGET_DATABASE_URL"), help="database to complete (or TARGET_DATABASE_URL)")
    parser.add_argument("--reference", default=os.environ.get("DATABASE_URL"), help="complete database to compare with (default: DATABASE_URL of .env)")
    parser.add_argument("--ratio", type=float, default=0.98, help="a step is missing when the target holds less than this share of the reference (default 0.98)")
    parser.add_argument("--run", action="store_true", help="run the missing steps on the target (default: report only)")
    parser.add_argument("--yes", action="store_true", help="do not ask for the target host name before running")
    args = parser.parse_args()

    if not args.target:
        print("Cible absente : --target <url> ou variable TARGET_DATABASE_URL.")
        return 2
    if not args.reference:
        print("Référence absente : --reference <url> ou DATABASE_URL dans .env.")
        return 2
    if args.target == args.reference:
        print("La cible et la référence sont la même base : rien à comparer.")
        return 2

    ref_conn, target_conn = psycopg2.connect(args.reference), psycopg2.connect(args.target)
    ref_conn.set_session(readonly=True)
    target_conn.set_session(readonly=True)
    ref, target = ref_conn.cursor(), target_conn.cursor()
    print(f"Référence : {host_of(args.reference)}\nCible     : {host_of(args.target)}\n")

    missing: list[str] = []
    print(f"{'étape':36s} {'sonde':36s} {'cible':>10s} {'référence':>10s}  état")
    for step, probes in PROBES.items():
        step_missing = False
        for i, probe in enumerate(probes):
            t, r = scalar(target, probe.sql), scalar(ref, probe.sql)
            bad = r > 0 and (t < 0 or t < args.ratio * r)
            step_missing |= bad
            print(f"{step if i == 0 else '':36s} {probe.label:36s} {max(t, 0):>10,} {max(r, 0):>10,}  {'MANQUE' if bad else 'ok'}".replace(",", " "))
        if step_missing:
            missing.append(step)

    gaps = poi_gaps(ref, target, args.ratio) if "seed_poi" in missing else []
    if gaps:
        print(f"\nPOI : {len(gaps)} département(s) incomplet(s) sur la cible (code, cible, référence) :")
        print("  " + ", ".join(f"{d} ({t:,}/{r:,})".replace(",", " ") for d, t, r in gaps[:40]) + (" ..." if len(gaps) > 40 else ""))

    print()
    for name, (why, probes) in NOT_RERUNNABLE.items():
        gaps_here = [p.label for p in probes if scalar(ref, p.sql) > 0 and scalar(target, p.sql) < args.ratio * scalar(ref, p.sql)]
        if gaps_here:
            print(f"NON RELANÇABLE : {name} incomplet ({', '.join(gaps_here)}) : {why}")

    ref_conn.close()
    target_conn.close()

    print(f"\n{len(missing)} étape(s) à relancer : {', '.join(missing) if missing else 'aucune'}")
    if not missing or not args.run:
        if missing:
            print("Rapport seulement. Ajoute --run pour exécuter ces étapes sur la cible.")
        return 0

    if not args.yes:
        answer = input(f"Écrire sur {host_of(args.target)} ? Tape le nom d'hôte pour confirmer : ").strip()
        if answer != (urlparse(args.target).hostname or ""):
            print("Annulé.")
            return 1

    env = {**os.environ, "DATABASE_URL": args.target}
    cmd = [sys.executable, "-u", "-m", "pipeline.scripts.seed_all", "--only", ",".join(missing)]
    print("\n$ " + " ".join(cmd) + f"   (DATABASE_URL = {host_of(args.target)})\n")
    return subprocess.call(cmd, cwd=REPO_ROOT, env=env)


if __name__ == "__main__":
    sys.exit(main())
