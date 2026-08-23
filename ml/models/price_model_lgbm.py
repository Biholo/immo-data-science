"""
Model 2 — LightGBM comparison with native categorical support (NOT the
mémoire deliverable — CONTEXTE_ML_MEMOIRE.md leaves the exact GB
implementation open ("XGBoost/LightGBM/scikit-learn"), but ml/models/price_model.py
is the version everything else in this repo (mémoire artifacts, EXPERIMENTS_LOG
entries, run_all.py) is built around. This script is a documented comparison
point, not a replacement).

ml/models/price_model.py one-hot encodes `department_code` (~100 sparse
columns) and `housing_zone`/`cluster_id`. LightGBM handles categoricals
natively (integer-encoded internally, split on subsets directly) — usually
faster to train and sometimes more accurate on high-cardinality columns like
department_code, since one-hot forces the tree to reconstruct department
groupings split-by-split instead of considering them directly.

Same feature set, same cleaning, same train/val/test split (identical
RANDOM_STATE + filtering → same rows) as price_model.py, so the metrics below
are directly comparable to that model's "latest" run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import lightgbm as lgb
import pandas as pd
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import PRICE_MODEL_CATEGORICAL, PRICE_MODEL_FEATURES, RANDOM_STATE, TARGET_COL  # noqa: E402
from ml.data.build_cross_sectional import build_cross_sectional  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata, save_model, timer  # noqa: E402
from ml.evaluation.metrics import print_comparison, regression_report  # noqa: E402
from ml.models.price_model import dept_median_baseline, load_cluster_assignments  # noqa: E402

LGBM_PARAMS = dict(
    n_estimators=300,
    max_depth=3,
    learning_rate=0.05,
    subsample=0.8,
    random_state=RANDOM_STATE,
    verbose=-1,
)


def build_design_matrix_native(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Categoricals stay as pandas 'category' dtype — no one-hot, LightGBM splits on them directly."""
    X = df[PRICE_MODEL_FEATURES].copy()
    cat_cols = []

    for col in PRICE_MODEL_CATEGORICAL:
        if col in df.columns:
            X[col] = df[col].fillna("unknown").astype("category")
            cat_cols.append(col)
    if "cluster_id" in df.columns:
        X["cluster_id"] = df["cluster_id"].fillna(-1).astype(int).astype("category")
        cat_cols.append("cluster_id")

    return X, cat_cols


def run(dept_filter: str | None = None, dry_run: bool = False) -> Path:
    print("Loading cross-sectional feature table...")
    df = build_cross_sectional(dept_filter=dept_filter, exclude_estimated=True)
    print(f"  {len(df)} communes (price_data_source != 'estimated')")

    clusters = load_cluster_assignments()
    if clusters is not None:
        df = df.join(clusters, how="left")

    required = PRICE_MODEL_FEATURES + [TARGET_COL, "department_code"]
    clean = df.dropna(subset=required)
    print(f"  {len(clean)} communes with complete features + target ({len(df) - len(clean)} dropped)")

    X_full, cat_cols = build_design_matrix_native(clean)
    y_full = clean[TARGET_COL]

    idx_train, idx_temp = train_test_split(clean.index, test_size=0.30, random_state=RANDOM_STATE)
    idx_val, idx_test = train_test_split(idx_temp, test_size=0.50, random_state=RANDOM_STATE)
    print(f"  Split: train={len(idx_train)}, val={len(idx_val)}, test={len(idx_test)} (identical to price_model.py)")

    X_train, y_train = X_full.loc[idx_train], y_full.loc[idx_train]
    X_test, y_test = X_full.loc[idx_test], y_full.loc[idx_test]

    print("Training LGBMRegressor (native categorical, no one-hot)...")
    with timer() as t:
        model = lgb.LGBMRegressor(**LGBM_PARAMS)
        model.fit(X_train, y_train, categorical_feature=cat_cols)

    baseline_test_pred = dept_median_baseline(clean.loc[idx_train], clean.loc[idx_test])
    model_test_pred = pd.Series(model.predict(X_test), index=idx_test)

    baseline_metrics = regression_report(y_test, baseline_test_pred)
    model_metrics = regression_report(y_test, model_test_pred)
    print_comparison("Test set (LightGBM)", baseline_metrics, model_metrics)

    importance = pd.Series(model.feature_importances_, index=X_full.columns).sort_values(ascending=False)

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        print(importance.head(10))
        return Path()

    run_dir = new_run_dir("price_model_lgbm")
    save_dataframe(run_dir, importance.to_frame("importance"), "feature_importance.csv")
    save_model(run_dir, model, "model.joblib")

    save_metadata(run_dir, {
        "model": "LGBMRegressor (native categorical)",
        "note": "Comparison to ml/models/price_model.py (sklearn GB, one-hot categorical) — same split/features",
        "hyperparameters": LGBM_PARAMS,
        "categorical_features": cat_cols,
        "target": TARGET_COL,
        "features_raw": PRICE_MODEL_FEATURES,
        "cluster_feature_used": clusters is not None,
        "n_communes_used": len(clean),
        "split_sizes": {"train": len(idx_train), "val": len(idx_val), "test": len(idx_test)},
        "baseline": "department median price/m² (train-only)",
        "baseline_metrics_test": baseline_metrics,
        "model_metrics_test": model_metrics,
        "dept_filter": dept_filter,
        **t,
    })

    latest = publish_latest("price_model_lgbm", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(dept_filter=args.dept, dry_run=args.dry_run)
