"""
Coverage audit of the Rentium DB: "is everything that immo-data-science can load actually loaded?"

Prints:
  1. row counts of the main tables (cities, transactions, pois, series, timeseries...)
  2. fill rate of every `cities` column
  3. number of series per SerieName (city / zone / country level)
  4. POI coverage by department

Each line gets a status:
  OK        >= 90 % filled
  PARTIEL   filled but < 90 %  (normal for columns that only exist where DVF has data — see PARTIAL_BY_DESIGN)
  VIDE      0 % and a script should fill it -> run the seed named in the hint
  SANS SRC  0 % and no data source exists in immo-data-science (see NO_SOURCE)

  python -m pipeline.scripts.audit_coverage
  python -m pipeline.scripts.audit_coverage --strict     # exit code 1 if any VIDE (CI / seed_all)
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()

# cities columns that are not data (technical / identity) — not audited
IGNORED_CITY_COLUMNS = {
    "id", "name", "english_name", "french_name", "normalized_name", "country_id", "created_at", "updated_at",
    "osmId", "administrative_zone_id", "is_premium", "insee_code", "postalCode",
}

# 0 % expected: no source in immo-data-science (README > "Chantiers restants")
NO_SOURCE = {
    "image", "eligible_zones", "major_urban_projects", "attractiveness_rank",
    "avg_sale_days", "avg_relocation_days", "rental_tension",
}
NO_SOURCE_SERIES_PREFIX = ("search_time_", "sale_time_")

# < 90 % expected by design
PARTIAL_BY_DESIGN = {
    "price_trend": "villes avec prix DVF (pas les villes estimées IDW)",
    "sparkline_path": "villes avec prix DVF",
    "price_growth_3y": "villes DVF avec >= 13 trimestres",
    "years_to_buy": "villes avec prix DVF + revenu médian",
    "median_income": "Filosofi 2021 : communes non couvertes (secret statistique, ~11 %)",
    "housing_effort_rate": "dépend de median_income (Filosofi ~89 % des communes)",
    "gross_yield": "villes avec prix DVF par typologie (T1-T4)",
    "avg_sale_price": "villes avec >= 10 ventes en 12 mois",
    "median_sale_price": "villes avec >= 10 ventes en 12 mois",
    "rent_control": "liste curée (~70 communes), NULL ailleurs",
    "rental_permit_required": "liste (~700 communes), NULL ailleurs",
    "high_demand_zone": "zonage ABC (34 875 communes)",
    "housing_zone": "zonage ABC (34 875 communes)",
}

# column / serie -> command that fills it
HINTS = {
    "geo_location": "seed_cities",
    "department": "seed_cities",
    "median_price_per_sqm": "run_dvf --geo city", "avg_price_per_sqm": "run_dvf --geo city",
    "transaction_volume": "run_dvf --geo city", "price_data_source": "run_dvf --geo city",
    "median_sale_price": "seed_transactions puis seed_city_sale_prices",
    "avg_sale_price": "seed_transactions puis seed_city_sale_prices",
    "student_count": "seed_rp", "median_age": "seed_median_age", "tenant_profile": "seed_tenant_profile",
    "retired_count": "seed_activity_counts_series", "unemployed_count": "seed_activity_counts_series",
    "demographic_growth_5y": "seed_dashboard_fields", "employment_growth": "seed_dashboard_fields",
    "tenant_rate": "seed_dashboard_fields", "avg_property_tax": "seed_avg_property_tax",
    "avg_rent_per_sqm": "seed_gross_yield", "gross_yield": "seed_gross_yield",
    "years_to_buy": "seed_years_to_buy", "housing_effort_rate": "seed_housing_effort_rate",
    "owner_rate": "seed_city_latest_snapshots", "vacancy_rate": "seed_city_latest_snapshots",
    "median_income": "seed_city_latest_snapshots", "unemployment_rate": "seed_city_latest_snapshots",
    "annual_company_creations": "seed_city_latest_snapshots",
    "housing_zone": "seed_housing_zone", "high_demand_zone": "seed_housing_zone",
    "rent_control": "seed_rent_regulation", "rental_permit_required": "seed_rent_regulation",
    "short_term_rental_score": "seed_short_term_rental_score",
}
SERIES_HINTS = {
    "price_": "run_dvf", "surface_median_": "run_dvf", "transaction_volume": "run_dvf", "vefa_share": "run_dvf",
    "land_price_sqm": "run_dvf",
    "population": "seed_pop_series / seed_population_history_series", "aging_index": "seed_pop_series",
    "company_creations": "seed_company_creations_series", "unemployment_rate": "seed_rp_series",
    "active_population": "seed_employment_series", "median_income": "seed_median_income_series",
    "secondary_residence_rate": "seed_logement_series", "social_housing_rate": "seed_logement_series",
    "owner_rate": "seed_logement_series", "vacancy_rate": "seed_logement_series",
    "property_tax_rate": "seed_fiscalite_series", "rent_sqm_": "seed_rent_series",
    "gross_yield_": "seed_gross_yield", "housing_effort_rate": "seed_housing_effort_rate",
    "years_to_buy": "seed_years_to_buy", "retail_": "seed_commerce_series",
    "household_size_": "seed_household_size_series",
}


def series_hint(name: str) -> str:
    for prefix, hint in SERIES_HINTS.items():
        if name.startswith(prefix):
            return hint
    return ""


def status(filled: int, total: int, no_source: bool) -> str:
    if total == 0:
        return "VIDE"
    if filled == 0:
        return "SANS SRC" if no_source else "VIDE"
    if filled / total >= 0.9:
        return "OK"
    return "PARTIEL"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="exit 1 if a VIDE line remains")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        return 2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    empty = 0

    print("== Tables ==")
    for label, sql in [
        ("cities", "SELECT COUNT(*) FROM cities"),
        ("administrative_zones", "SELECT COUNT(*) FROM administrative_zones"),
        ("transactions (DVF)", "SELECT COUNT(*) FROM transactions WHERE source = 'DVF'"),
        ("pois", "SELECT COUNT(*) FROM pois"),
        ("series", "SELECT COUNT(*) FROM series"),
        ("timeseries", "SELECT COUNT(*) FROM timeseries"),
    ]:
        try:
            cur.execute(sql)
            n = cur.fetchone()[0]
            flag = "" if n else "   <- VIDE"
            empty += 0 if n else 1
            print(f"  {label:24s} {n:>12,}{flag}")
        except psycopg2.Error as e:
            conn.rollback()
            print(f"  {label:24s} ERREUR {str(e).splitlines()[0]}")

    cur.execute("SELECT COUNT(*) FROM cities")
    total = cur.fetchone()[0]

    print(f"\n== Colonnes cities ({total:,} villes) ==")
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'cities' ORDER BY ordinal_position")
    cols = [r[0] for r in cur.fetchall() if r[0] not in IGNORED_CITY_COLUMNS]
    cur.execute("SELECT " + ", ".join(f'COUNT("{c}")' for c in cols) + " FROM cities")
    for col, filled in zip(cols, cur.fetchone()):
        st = status(filled, total, col in NO_SOURCE)
        note = ""
        if st == "VIDE":
            empty += 1
            note = f"-> {HINTS.get(col, 'voir README')}"
        elif st == "PARTIEL":
            note = PARTIAL_BY_DESIGN.get(col, f"-> {HINTS.get(col, 'à vérifier')}")
        print(f"  {col:28s} {filled:>8,} {filled / total:7.1%}  {st:9s} {note}")

    print("\n== Séries par nom (villes / zones admin / pays) ==")
    cur.execute("""
        SELECT e.enumlabel,
               COUNT(*) FILTER (WHERE s.city_id IS NOT NULL),
               COUNT(*) FILTER (WHERE s.administrative_zone_id IS NOT NULL),
               COUNT(*) FILTER (WHERE s.country_id IS NOT NULL)
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid AND t.typname = 'SerieName'
        LEFT JOIN series s ON s.name::text = e.enumlabel
        GROUP BY e.enumlabel, e.enumsortorder ORDER BY e.enumsortorder
    """)
    for name, c_city, c_zone, c_country in cur.fetchall():
        any_ = c_city + c_zone + c_country
        st = "OK" if any_ else ("SANS SRC" if name.startswith(NO_SOURCE_SERIES_PREFIX) else "VIDE")
        note = ""
        if st == "VIDE":
            empty += 1
            note = f"-> {series_hint(name) or 'voir README'}"
        print(f"  {name:30s} {c_city:>7,} {c_zone:>5,} {c_country:>4,}  {st:9s} {note}")

    print("\n== POI par département (top 10) ==")
    cur.execute("""
        SELECT c.department_code, COUNT(*) FROM pois p JOIN cities c ON c.id = p.city_id
        GROUP BY 1 ORDER BY 2 DESC LIMIT 10
    """)
    rows = cur.fetchall()
    cur.execute("SELECT COUNT(DISTINCT c.department_code) FROM pois p JOIN cities c ON c.id = p.city_id")
    n_dept = cur.fetchone()[0]
    for dept, n in rows:
        print(f"  {dept:4s} {n:>9,}")
    print(f"  -> {n_dept} département(s) couvert(s) sur ~101"
          + ("   (POI incomplets : python -m pipeline.scripts.seed_all --only seed_poi)" if n_dept < 90 else ""))

    cur.close()
    conn.close()
    print(f"\n{empty} ligne(s) VIDE à traiter." if empty else "\nRien de VIDE hors colonnes sans source.")
    return 1 if (args.strict and empty) else 0


if __name__ == "__main__":
    sys.exit(main())
