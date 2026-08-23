"""
Builds the commune-level feature table used by Model 1 (clustering),
Model 2 (price/m² Gradient Boosting) and Model 4 (quantile regression).

Read-only against the existing DB — reuses pipeline.services.db.get_connection.
Two sources are joined:
  - `cities` snapshot columns (population, housing_zone, price_data_source,
    median_price_per_sqm, ... — filled by pipeline/scripts/denormalize.py etc.)
  - `series`/`timeseries` EAV rows for the annual INSEE socio-demo rates
    (unemployment_rate, owner_rate, vacancy_rate, ...), latest value per commune.

Known limitation (see CONTEXTE_ML_MEMOIRE.md discussion): the INSEE annual
series only go up to millésime 2022 at the time this was written (INSEE
publication lag ~2-3 years) — "latest value" for those columns is effectively
a 2022 snapshot even though DVF prices run to 2025. Document this as a
limitation in the mémoire rather than treating it as fixable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pipeline.services.db import get_connection  # noqa: E402

from ml.config import SOCIO_SERIES  # noqa: E402
from ml.data.median_income import load_median_income  # noqa: E402
from ml.data.rent import load_rent  # noqa: E402
from ml.data.zonage_national import load_zonage_national  # noqa: E402
from ml.finance.cashflow import cout_acquisition_total  # noqa: E402

EARTH_RADIUS_KM = 6371.0
N_MAJOR_CITIES = 50  # top-N communes by population, used as the "major city" reference set


def _fetch_major_cities(conn, n_major: int = N_MAJOR_CITIES) -> pd.DataFrame:
    """
    Top-N communes by population, queried WITHOUT any dept_filter — this is
    the reference set for dist_nearest_major_city_km, and it must stay
    national even when build_cross_sectional() is run with --dept. Otherwise
    a Seine-et-Marne-only run would lose Paris as a candidate "major city",
    breaking the whole point of the feature (periurban distance to the real
    nearest metropolis, not the nearest one inside an arbitrary dept filter).
    """
    sql = """
        SELECT insee_code, latitude, longitude, population
        FROM cities
        WHERE population IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY population DESC
        LIMIT %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, [n_major])
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["insee_code", "latitude", "longitude", "population"])


def _add_distance_to_major_city(df: pd.DataFrame, major: pd.DataFrame) -> pd.DataFrame:
    """
    dist_nearest_major_city_km: haversine distance from each commune to the
    nearest of the top-N communes by population (national reference set, see
    _fetch_major_cities). No new data needed — reuses lat/lon and population
    already in `cities`. Captures the periurban gradient (e.g. Melun vs
    Fontainebleau, same department, very different distance to Paris) that
    `department_code` alone can't.
    """
    has_coords = df["latitude"].notna() & df["longitude"].notna()
    df["dist_nearest_major_city_km"] = np.nan
    if major.empty:
        return df

    major_rad = np.radians(major[["latitude", "longitude"]].values)
    all_rad = np.radians(df.loc[has_coords, ["latitude", "longitude"]].values)

    tree = BallTree(major_rad, metric="haversine")
    dist, _ = tree.query(all_rad, k=1)
    df.loc[has_coords, "dist_nearest_major_city_km"] = dist[:, 0] * EARTH_RADIUS_KM
    return df


def _fetch_latest_socio_series(conn, dept_filter: str | None = None) -> pd.DataFrame:
    """Latest value per (commune, serie_name) for the annual INSEE series.

    Returns a wide DataFrame indexed by insee_code, one column per serie name.
    """
    dept_clause = "AND c.department_code = %s" if dept_filter else ""
    params: list = [SOCIO_SERIES]
    if dept_filter:
        params.append(dept_filter)

    sql = f"""
        SELECT DISTINCT ON (s.city_id, s.name)
            c.insee_code, s.name, t.value, t.timestamp
        FROM series s
        JOIN timeseries t ON t.serie_id = s.id
        JOIN cities c ON s.city_id = c.id
        WHERE s.name::text = ANY(%s)
          AND s.city_id IS NOT NULL
          AND s.frequency::text = 'ANNUAL'
          {dept_clause}
        ORDER BY s.city_id, s.name, t.timestamp DESC
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    long_df = pd.DataFrame(rows, columns=["insee_code", "name", "value", "timestamp"])
    if long_df.empty:
        return pd.DataFrame(columns=["insee_code", *SOCIO_SERIES]).set_index("insee_code")

    wide = long_df.pivot(index="insee_code", columns="name", values="value")
    wide.columns.name = None
    return wide


def _fetch_cities_snapshot(conn, dept_filter: str | None = None) -> pd.DataFrame:
    dept_clause = "WHERE department_code = %s" if dept_filter else ""
    params = [dept_filter] if dept_filter else []

    sql = f"""
        SELECT
            insee_code, name, department_code, latitude, longitude,
            population, housing_zone, high_demand_zone,
            median_price_per_sqm, avg_price_per_sqm, transaction_volume,
            price_growth_3y, price_trend, price_data_source
        FROM cities
        {dept_clause}
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]

    return pd.DataFrame(rows, columns=cols).set_index("insee_code")


def build_cross_sectional(
    conn=None,
    dept_filter: str | None = None,
    exclude_estimated: bool = True,
) -> pd.DataFrame:
    """
    Returns one row per commune, indexed by insee_code, with:
      - cities snapshot columns (name, department_code, lat/lon, housing_zone,
        high_demand_zone, median/avg_price_per_sqm, transaction_volume,
        price_growth_3y, price_trend, price_data_source)
      - socio-demo columns pivoted from `series`/`timeseries`:
        unemployment_rate, owner_rate, vacancy_rate, social_housing_rate,
        secondary_residence_rate, aging_index, active_population,
        student_rate, retiree_rate, population_series (raw INSEE annual pop)

    `population` = COALESCE(cities.population, population_series) — cities.population
    comes from geo.api.gouv.fr (more recent), INSEE series is the 2017-2022 fallback.

    Also joined in, all pure local-file reads (no DB writes anywhere, see
    ml/README.md "Design"):
      - median_income (ml/data/median_income.py, INSEE Filosofi 2021)
      - housing_zone, backfilled nationally (ml/data/zonage_national.py) —
        overrides the DB's IDF-only value when the national file has one
      - dist_nearest_major_city_km (computed here, no external file needed)
      - rent_appt_all/rent_appt_t12/rent_appt_t3plus/rent_maison (€/m²/mois,
        ml/data/rent.py, ANIL/DHUP Carte des loyers 2025) + rendement_brut (%),
        the piece needed to answer "does it pay off", not just "is it cheap"
        (see EXPERIMENTS_LOG.md). rendement_brut = (rent_appt_all × 12) /
        coût_acquisition × 100, coût_acquisition = median_price_per_sqm +
        frais de notaire + travaux (ml.finance.cashflow.cout_acquisition_total)
        — NOT prix nu. "Brut" ne veut pas dire "sans frais d'acquisition" :
        ces frais sont du capital dépensé avant la mise en location, donc au
        dénominateur même du brut (retour utilisateur, voir EXPERIMENTS_LOG.md
        "Modèle rendement" #6). Seules les charges d'exploitation récurrentes
        (après mise en location) distinguent net de brut — non déduites ici.

    exclude_estimated: drop rows where price_data_source == 'estimated' (IDW-filled,
    not a real DVF observation — see pipeline/scripts/interpolate.py). Required before
    using median_price_per_sqm as a regression target (Model 2/4); irrelevant for
    Model 1 clustering which doesn't use price as a feature.
    """
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        snapshot = _fetch_cities_snapshot(conn, dept_filter)
        socio = _fetch_latest_socio_series(conn, dept_filter)
        major_cities = _fetch_major_cities(conn)
    finally:
        if owns_conn:
            conn.close()

    socio = socio.rename(columns={"population": "population_series"})
    df = snapshot.join(socio, how="left")

    df["population"] = df["population"].fillna(df.get("population_series"))

    income = load_median_income()
    df = df.join(income, how="left")

    zonage = load_zonage_national()
    zonage_aligned = zonage["housing_zone"].reindex(df.index)
    df["housing_zone"] = zonage_aligned.combine_first(df["housing_zone"])

    df = _add_distance_to_major_city(df, major_cities)

    rent = load_rent()
    df = df.join(rent, how="left")
    # coût d'acquisition réel (prix + notaire + travaux), pas le prix nu — voir docstring ci-dessus.
    df["rendement_brut"] = (df["rent_appt_all"] * 12) / cout_acquisition_total(df["median_price_per_sqm"]) * 100

    if exclude_estimated:
        n_before = len(df)
        df = df[df["price_data_source"] != "estimated"]
        dropped = n_before - len(df)
        if dropped:
            print(f"  Excluded {dropped} communes with price_data_source='estimated'")

    return df


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--include-estimated", action="store_true")
    args = parser.parse_args()

    frame = build_cross_sectional(dept_filter=args.dept, exclude_estimated=not args.include_estimated)
    print(frame.shape)
    print(frame.head())
