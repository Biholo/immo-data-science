"""
Shared data preparation for Model 2 (price_model.py) and Model 4 (quantile.py): feature table, target,
train/val/test split. Both models call load_price_frame() so they stay on the exact same split.

FEATURE_LEVEL 0 (v1 / v2a): unchanged behaviour — real-DVF communes only (price_data_source != 'estimated'),
  target median_price_per_sqm, random 70/15/15 split of the communes with complete features.
FEATURE_LEVEL >= 1: sales-based target with neighbour pooling (ml/data/price_target.py), optional national /
  POI features, sparse features imputed. Split protocol:
    - candidates for validation/test = communes with >= MIN_RELIABLE own sales (their target is their own
      median: never pooled), split randomly 70/15/15 (RANDOM_STATE, same seed as v1);
    - every other commune with a target (pooled / own_thin) is TRAINING only;
    - pooled targets never use the sales of validation/test communes (no label leakage across the split).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from sklearn.model_selection import train_test_split

from ml.config import (
    ARTIFACTS_DIR, FEATURE_LEVEL, PRICE_MODEL_FEATURES, RANDOM_STATE, TARGET_COL,
)
from ml.data.build_cross_sectional import build_cross_sectional
from pipeline.services.db import get_connection


@dataclass
class PriceFrame:
    df: pd.DataFrame           # every commune loaded (before dropna)
    clean: pd.DataFrame        # communes with complete features + target
    idx_train: pd.Index
    idx_val: pd.Index
    idx_test: pd.Index
    clusters_used: bool
    meta: dict = field(default_factory=dict)


def load_cluster_assignments() -> pd.DataFrame | None:
    path = ARTIFACTS_DIR / "clustering" / "latest" / "cluster_assignments.csv"
    if not path.exists():
        print(f"  No clustering run found ({path.parent}) — skipping cluster_id feature.")
        return None
    # insee_code must stay string ("77014", not int 77014) to match build_cross_sectional's
    # index dtype — otherwise the join silently matches nothing (all NaN, no error).
    return pd.read_csv(path, index_col="insee_code", dtype={"insee_code": str})[["cluster_id"]]


def _split_70_15_15(index: pd.Index) -> tuple[pd.Index, pd.Index, pd.Index]:
    idx_train, idx_temp = train_test_split(index, test_size=0.30, random_state=RANDOM_STATE)
    idx_val, idx_test = train_test_split(idx_temp, test_size=0.50, random_state=RANDOM_STATE)
    return idx_train, idx_val, idx_test


def load_price_frame(dept_filter: str | None = None) -> PriceFrame:
    if FEATURE_LEVEL == 0:
        return _load_level0(dept_filter)
    return _load_sales_based(dept_filter)


def _join_clusters(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    clusters = load_cluster_assignments()
    if clusters is None:
        return df, False
    return df.join(clusters, how="left"), True


def _load_level0(dept_filter: str | None) -> PriceFrame:
    df = build_cross_sectional(dept_filter=dept_filter, exclude_estimated=True)
    print(f"  {len(df)} communes (price_data_source != 'estimated')")
    df, clusters_used = _join_clusters(df)

    required = PRICE_MODEL_FEATURES + [TARGET_COL, "department_code"]
    clean = df.dropna(subset=required)
    print(f"  {len(clean)} communes with complete features + target ({len(df) - len(clean)} dropped)")

    idx_train, idx_val, idx_test = _split_70_15_15(clean.index)
    return PriceFrame(df, clean, idx_train, idx_val, idx_test, clusters_used)


def _load_sales_based(dept_filter: str | None) -> PriceFrame:
    from ml.data.features_v2 import add_national_features, add_poi_features, impute_sparse
    from ml.data.price_target import MIN_RELIABLE, load_unit_sales, pool_price_targets, time_adjust

    conn = get_connection()
    try:
        # exclude_estimated=False: the target no longer comes from the IDW-filled column.
        df = build_cross_sectional(conn, dept_filter=dept_filter, exclude_estimated=False)
        if FEATURE_LEVEL >= 2:
            df = add_national_features(df, conn)
        if FEATURE_LEVEL >= 3:
            df = add_poi_features(df, conn)
        print("  Loading unit sales (transactions)...")
        raw_sales = load_unit_sales(conn)
    finally:
        conn.close()
    print(f"  {len(df)} communes, {len(raw_sales):,} unit sales")

    df = impute_sparse(df)
    df, clusters_used = _join_clusters(df)

    sales = time_adjust(raw_sales, df["department_code"])
    n_own = sales.groupby("insee_code").size().reindex(df.index).fillna(0)

    features_ok = df.dropna(subset=PRICE_MODEL_FEATURES + ["department_code"]).index
    candidates = features_ok[(n_own.reindex(features_ok) >= MIN_RELIABLE).values]
    _, idx_val_c, idx_test_c = _split_70_15_15(candidates)
    excluded = set(idx_val_c) | set(idx_test_c)

    print("  Pooling thin communes with their nearest neighbours...")
    target = pool_price_targets(sales, df[["latitude", "longitude", "department_code"]], excluded)
    df = df.join(target, how="left")

    clean = df.dropna(subset=PRICE_MODEL_FEATURES + [TARGET_COL, "department_code"])
    idx_val = clean.index.intersection(idx_val_c)
    idx_test = clean.index.intersection(idx_test_c)
    idx_train = clean.index.difference(idx_val).difference(idx_test)

    reliability = clean["price_reliability"].value_counts().to_dict()
    print(f"  {len(clean)} communes with complete features + target ({len(df) - len(clean)} dropped)")
    print(f"  Target provenance: {reliability}")
    meta = {"min_reliable_sales": MIN_RELIABLE, "target_provenance": reliability}
    return PriceFrame(df, clean, idx_train, idx_val, idx_test, clusters_used, meta)
