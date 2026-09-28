"""
Extra commune-level features for the v2 models (ml/config.py FEATURE_LEVEL >= 2), all read-only:

  add_national_features (level >= 2)
      latest INSEE/DGFiP/BPE series (property_tax_rate, household_size_*, company_creations, retail_*),
      `cities` columns that were never used (median_age, tenant_rate, short_term_rental_score, growth rates,
      student/retired shares), distance to Paris, and the SALES MIX seen in `transactions`
      (house_share, commercial_share, bloc_share, mean_surface_unit — counts and surfaces only, never prices,
      so nothing here leaks the price target).
      The ANIL rents (rent_appt_*, rent_maison) and latitude/longitude are already in build_cross_sectional.
  add_poi_features (level >= 3)
      OSM POIs from the `pois` table: count per category, total per 1000 inhabitants, distance from the
      commune centroid to the nearest transport / health / education POI. The POI load covers metropolitan
      France only (Geofabrik france-latest has no DOM-TOM): DOM communes stay NaN and are imputed.
  impute_sparse
      department-median then global-median imputation + `<col>_missing` flag for the structurally sparse
      features (config.PRICE_IMPUTED), so thin/small communes are not dropped.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from ml.config import PRICE_IMPUTED
from ml.data.build_cross_sectional import EARTH_RADIUS_KM, SHADOWED_CITIES_SQL, _fetch_latest_socio_series

PARIS = (48.8566, 2.3522)
NATIONAL_SERIES = [
    "property_tax_rate", "company_creations",
    "household_size_1p_rate", "household_size_2p_rate", "household_size_3p_rate",
    "household_size_4p_rate", "household_size_5p_plus_rate",
    "retail_count", "retail_share_large_format", "retail_share_grocery", "retail_share_specialty",
]
POI_CATEGORIES = ["education", "services", "leisure", "health", "shopping", "culture", "transport"]

CITY_COLUMNS_SQL = f"""
    SELECT insee_code, median_age, tenant_rate, short_term_rental_score,
           demographic_growth_5y, employment_growth, student_count, retired_count
    FROM cities WHERE id NOT IN ({SHADOWED_CITIES_SQL})
"""
SALES_MIX_SQL = f"""
    SELECT c.insee_code,
        COUNT(*) FILTER (WHERE t.property_type = 'HOUSE' AND t.lots_in_mutation = 1)::float
            / NULLIF(COUNT(*) FILTER (WHERE t.property_type IN ('HOUSE', 'APARTMENT') AND t.lots_in_mutation = 1), 0)
            AS house_share,
        COUNT(*) FILTER (WHERE t.property_type = 'COMMERCIAL')::float / COUNT(*) AS commercial_share,
        COUNT(*) FILTER (WHERE t.lots_in_mutation > 1)::float / COUNT(*) AS bloc_share,
        AVG(t.surface) FILTER (WHERE t.lots_in_mutation = 1 AND t.property_type IN ('HOUSE', 'APARTMENT')
                                 AND t.surface BETWEEN 9 AND 2000) AS mean_surface_unit
    FROM transactions t JOIN cities c ON c.id = t.city_id
    WHERE t.source = 'DVF' AND c.id NOT IN ({SHADOWED_CITIES_SQL})
    GROUP BY c.insee_code
"""


def _query_df(conn, sql: str, index: str = "insee_code") -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    return pd.DataFrame(rows, columns=cols).set_index(index)


def _haversine_to_point(lat: pd.Series, lon: pd.Series, point: tuple[float, float]) -> pd.Series:
    la1, lo1, la2, lo2 = np.radians(lat), np.radians(lon), np.radians(point[0]), np.radians(point[1])
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def add_national_features(df: pd.DataFrame, conn) -> pd.DataFrame:
    series = _fetch_latest_socio_series(conn, names=NATIONAL_SERIES)
    df = df.join(series, how="left")

    cols = _query_df(conn, CITY_COLUMNS_SQL)
    df = df.join(cols, how="left")
    pop = df["population"].where(df["population"] > 0)
    df["student_share"] = df["student_count"] / pop
    df["retired_share"] = df["retired_count"] / pop
    df["company_creations_per_1000"] = df["company_creations"] / pop * 1000
    df["retail_per_1000"] = df["retail_count"] / pop * 1000
    df["dist_paris_km"] = _haversine_to_point(df["latitude"], df["longitude"], PARIS)

    df = df.join(_query_df(conn, SALES_MIX_SQL), how="left")
    return df


def add_poi_features(df: pd.DataFrame, conn) -> pd.DataFrame:
    counts = _query_df(
        conn,
        f"""SELECT c.insee_code, p.category, COUNT(*) AS n FROM pois p JOIN cities c ON c.id = p.city_id
            WHERE c.id NOT IN ({SHADOWED_CITIES_SQL}) GROUP BY 1, 2""",
    ).reset_index().pivot(index="insee_code", columns="category", values="n")
    counts = counts.reindex(columns=POI_CATEGORIES).add_prefix("poi_")
    df = df.join(counts, how="left")

    metropole = ~df["department_code"].astype(str).str.startswith(("97", "98"))
    poi_cols = [f"poi_{c}" for c in POI_CATEGORIES]
    df.loc[metropole, poi_cols] = df.loc[metropole, poi_cols].fillna(0)  # no POI in a metropolitan commune = 0
    pop = df["population"].where(df["population"] > 0)
    df["poi_total_per_1000"] = df[poi_cols].sum(axis=1, min_count=1) / pop * 1000

    pts = _query_df(conn, "SELECT osm_id, category, lat, lon FROM pois", index="osm_id")
    has_coords = df["latitude"].notna() & df["longitude"].notna() & metropole
    centroids = np.radians(df.loc[has_coords, ["latitude", "longitude"]].values)
    for cat in ("transport", "health", "education"):
        sub = pts[pts["category"] == cat]
        col = f"dist_nearest_{cat}_km"
        df[col] = np.nan
        if len(sub):
            tree = BallTree(np.radians(sub[["lat", "lon"]].values), metric="haversine")
            dist, _ = tree.query(centroids, k=1)
            df.loc[has_coords, col] = dist[:, 0] * EARTH_RADIUS_KM
    return df


def impute_sparse(df: pd.DataFrame, columns: list[str] = PRICE_IMPUTED) -> pd.DataFrame:
    """Department median, then global median; always adds `<col>_missing` (0/1) for every present column."""
    df = df.copy()
    dept = df["department_code"]
    for col in columns:
        if col not in df.columns:
            continue
        missing = df[col].isna()
        df[f"{col}_missing"] = missing.astype(int)
        if missing.any():
            df[col] = df[col].fillna(df.groupby(dept)[col].transform("median")).fillna(df[col].median())
    return df
