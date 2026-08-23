"""
Builds the commune x quarter panel used by Model 3 (forecasting with lags).

Pulls price_sqm_all and transaction_volume straight from the QUARTERLY
`series`/`timeseries` rows (pipeline/services/dvf.py -> pipeline/scripts/run_dvf.py).

Note on price_data_source: unlike the cities snapshot columns, these
per-quarter rows are NEVER touched by pipeline/scripts/interpolate.py (which
only fills the `cities.median_price_per_sqm` snapshot via IDW). Every row in
this panel is therefore a genuine DVF observation (min_n>=10 transactions,
enforced at compute time in pipeline/services/series.py) — no need for a
separate per-row price_data_source flag here.

Static socio-demo features are merged in as constants per commune (carried
forward across all quarters) — see module docstring in build_cross_sectional.py
for the staleness caveat (INSEE annual data capped at millésime 2022).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pipeline.services.db import get_connection  # noqa: E402

from ml.config import LAGS, PRICE_SERIE, VOLUME_SERIE  # noqa: E402
from ml.data.build_cross_sectional import build_cross_sectional  # noqa: E402


def _fetch_quarterly_serie(conn, serie_name: str, dept_filter: str | None = None) -> pd.DataFrame:
    dept_clause = "AND c.department_code = %s" if dept_filter else ""
    params: list = [serie_name]
    if dept_filter:
        params.append(dept_filter)

    sql = f"""
        SELECT c.insee_code, t.timestamp::date AS period, t.value
        FROM series s
        JOIN timeseries t ON t.serie_id = s.id
        JOIN cities c ON s.city_id = c.id
        WHERE s.name::text = %s
          AND s.city_id IS NOT NULL
          AND s.frequency::text = 'QUARTERLY'
          {dept_clause}
        ORDER BY c.insee_code, t.timestamp
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    df = pd.DataFrame(rows, columns=["insee_code", "period", serie_name])
    df["period"] = pd.to_datetime(df["period"])  # psycopg2 returns datetime.date (object dtype)
    return df


def build_panel(
    conn=None,
    dept_filter: str | None = None,
    lags: list[int] = LAGS,
    merge_static_features: bool = True,
) -> pd.DataFrame:
    """
    Returns a long DataFrame, one row per (insee_code, period=quarter), with:
      - price_sqm_all, transaction_volume (raw quarterly observations)
      - price_sqm_all_lag{L} for each L in `lags` (shifted within each commune,
        computed on the actual quarter sequence present for that commune —
        NOT a fixed-step shift, since some communes have gaps: see min_n filter)
      - static socio-demo columns (merged from build_cross_sectional), if
        merge_static_features=True

    Sort order is (insee_code, period) ascending — required for any
    chronological train/test split downstream.
    """
    owns_conn = conn is None
    conn = conn or get_connection()
    try:
        price = _fetch_quarterly_serie(conn, PRICE_SERIE, dept_filter)
        volume = _fetch_quarterly_serie(conn, VOLUME_SERIE, dept_filter)
        static = build_cross_sectional(conn, dept_filter=dept_filter, exclude_estimated=False) if merge_static_features else None
    finally:
        if owns_conn:
            conn.close()

    panel = price.merge(volume, on=["insee_code", "period"], how="outer")
    panel = panel.sort_values(["insee_code", "period"]).reset_index(drop=True)

    for lag in lags:
        panel[f"{PRICE_SERIE}_lag{lag}"] = panel.groupby("insee_code")[PRICE_SERIE].shift(lag)

    if static is not None:
        # Only bring in feature columns, not the price/volume snapshot fields
        # (those would leak the cross-sectional target into a panel row).
        static_features = static.drop(
            columns=[c for c in ("median_price_per_sqm", "avg_price_per_sqm", "transaction_volume",
                                  "price_growth_3y", "price_trend") if c in static.columns]
        )
        panel = panel.merge(static_features, left_on="insee_code", right_index=True, how="left")

    return panel


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    args = parser.parse_args()

    df = build_panel(dept_filter=args.dept)
    print(df.shape)
    print(df.head(10))
    print(f"Communes: {df['insee_code'].nunique()}, quarters: {df['period'].nunique()}")
