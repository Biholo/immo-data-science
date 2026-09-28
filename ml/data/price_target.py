"""
Price/m² target built from the DVF sales in `transactions` (feature level >= 1, see ml/config.py).

v1 target = latest-quarter median of `price_sqm_all`, which needs >= 10 sales in ONE quarter: only ~7.3k
communes qualify. Here the unit is the SALE, not the quarter:

  1. every unit sale (lots_in_mutation = 1, house/apartment, 9-2000 m², 500-30000 €/m², same bounds as
     seed_city_sale_prices.py) becomes one €/m² observation, 2021-2025;
  2. observations are time-adjusted to the last year: pm2 * median(dept, last year) / median(dept, year),
     so a 2021 sale and a 2025 sale of the same market are comparable;
  3. a commune with >= MIN_RELIABLE own sales gets its own median  -> `price_reliability = 'own'`;
  4. a thinner commune is pooled with its nearest neighbours of the SAME department (nearest first, until
     >= MIN_POOL sales, within MAX_POOL_KM), distance-weighted median -> `'pooled'`. Its own sales are part of
     the pool. If the pool stays too small but the commune has >= MIN_OWN_THIN sales -> `'own_thin'`,
     otherwise no target (NaN).

Leakage control: pooling must never feed the sales of a validation/test commune into the target of a
training commune. `pool_price_targets(..., excluded=...)` therefore never uses the sales of the
excluded communes (val + test) as pooling sources; those communes are always 'own' (>= MIN_RELIABLE).

Read-only against the DB.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree

from ml.data.build_cross_sectional import SHADOWED_CITIES_SQL

EARTH_RADIUS_KM = 6371.0
MIN_RELIABLE = 30      # own sales needed to trust a commune's own median
MIN_POOL = 30          # sales to gather when pooling
MIN_OWN_THIN = 10      # fallback: own median from a thin sample, flagged 'own_thin'
MAX_POOL_KM = 25.0
MAX_NEIGHBOURS = 60
DIST_SCALE_KM = 5.0    # sale weight = 1 / (1 + distance / DIST_SCALE_KM)

SALES_SQL = f"""
    COPY (
        SELECT c.insee_code, EXTRACT(YEAR FROM t.mutation_date)::int AS year, t.price / t.surface AS pm2
        FROM transactions t
        JOIN cities c ON c.id = t.city_id
        WHERE t.source = 'DVF'
          AND t.lots_in_mutation = 1
          AND t.property_type IN ('HOUSE', 'APARTMENT')
          AND t.surface BETWEEN 9 AND 2000
          AND t.price / t.surface BETWEEN 500 AND 30000
          AND c.id NOT IN ({SHADOWED_CITIES_SQL})
    ) TO STDOUT WITH CSV HEADER
"""


def load_unit_sales(conn) -> pd.DataFrame:
    """One row per qualifying unit sale: insee_code, year, pm2 (raw €/m²)."""
    buf = io.StringIO()
    with conn.cursor() as cur:
        cur.copy_expert(SALES_SQL, buf)
    buf.seek(0)
    return pd.read_csv(buf, dtype={"insee_code": str})


def time_adjust(sales: pd.DataFrame, dept_of: pd.Series) -> pd.DataFrame:
    """Adds `dept` and `adj` (€/m² expressed at the last year's price level of the department)."""
    sales = sales.copy()
    sales["dept"] = sales["insee_code"].map(dept_of)
    sales = sales.dropna(subset=["dept"])
    ref_year = sales["year"].max()
    med = sales.groupby(["dept", "year"])["pm2"].median().unstack("year")
    factor = med[ref_year].values[:, None] / med.values  # (dept, year)
    factor = pd.DataFrame(factor, index=med.index, columns=med.columns).stack().rename("factor")
    sales = sales.join(factor, on=["dept", "year"])
    sales["factor"] = sales["factor"].fillna(1.0)
    sales["adj"] = sales["pm2"] * sales["factor"]
    return sales


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = np.cumsum(w)
    return float(v[np.searchsorted(cum, cum[-1] / 2.0)])


def pool_price_targets(
    sales: pd.DataFrame,
    communes: pd.DataFrame,
    excluded: set[str] | frozenset[str] = frozenset(),
) -> pd.DataFrame:
    """
    sales    : output of time_adjust (insee_code, dept, adj)
    communes : index insee_code, columns latitude, longitude, department_code (all communes to score)
    excluded : communes whose sales must not be used as pooling sources (validation/test communes)

    Returns a DataFrame indexed by insee_code: n_own, price_own, n_pooled, pool_radius_km, price_sqm_target,
    price_reliability ('own' | 'pooled' | 'own_thin' | NaN).
    """
    own = sales.groupby("insee_code")["adj"].agg(n_own="size", price_own="median")
    out = communes[["department_code"]].join(own).drop(columns="department_code")
    out["n_own"] = out["n_own"].fillna(0).astype(int)
    out["n_pooled"] = out["n_own"]
    out["pool_radius_km"] = 0.0
    out["price_sqm_target"] = np.nan
    out["price_reliability"] = pd.Series(pd.NA, index=out.index, dtype=object)

    reliable = out["n_own"] >= MIN_RELIABLE
    out.loc[reliable, "price_sqm_target"] = out.loc[reliable, "price_own"]
    out.loc[reliable, "price_reliability"] = "own"

    usable = sales[~sales["insee_code"].isin(excluded)]
    groups = {k: g.values for k, g in usable.groupby("insee_code")["adj"]}

    to_pool = out.index[~reliable & ~out.index.isin(excluded)]
    coords = np.radians(communes[["latitude", "longitude"]])
    for dept, dept_communes in communes.groupby("department_code"):
        sources = dept_communes.index[dept_communes.index.isin(list(groups))]
        targets = dept_communes.index.intersection(to_pool)
        if len(sources) == 0 or len(targets) == 0:
            continue
        tree = BallTree(coords.loc[sources].values, metric="haversine")
        k = min(MAX_NEIGHBOURS, len(sources))
        dist, ind = tree.query(coords.loc[targets].values, k=k)
        dist_km = dist * EARTH_RADIUS_KM
        for row, code in enumerate(targets):
            vals, wts, cum, radius = [], [], 0, 0.0
            for d, j in zip(dist_km[row], ind[row]):
                if d > MAX_POOL_KM:
                    break
                arr = groups[sources[j]]
                vals.append(arr)
                wts.append(np.full(len(arr), 1.0 / (1.0 + d / DIST_SCALE_KM)))
                cum += len(arr)
                radius = d
                if cum >= MIN_POOL:
                    break
            out.at[code, "n_pooled"] = cum
            out.at[code, "pool_radius_km"] = radius
            if cum >= MIN_POOL:
                out.at[code, "price_sqm_target"] = _weighted_median(np.concatenate(vals), np.concatenate(wts))
                out.at[code, "price_reliability"] = "pooled"
            elif out.at[code, "n_own"] >= MIN_OWN_THIN:
                out.at[code, "price_sqm_target"] = out.at[code, "price_own"]
                out.at[code, "price_reliability"] = "own_thin"
    return out
