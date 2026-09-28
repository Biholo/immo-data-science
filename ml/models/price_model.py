"""
Model 2 — Gradient Boosting price/m² estimation + opportunity score.
See CONTEXTE_ML_MEMOIRE.md §3.

opportunity_score = (price_observed - price_predicted) / price_predicted
Negative → observed price below what socio-economic characteristics predict
("signal de sous-évaluation" — a signal, never framed as proof of an
under-valued market, per the mémoire's own caveat).

price_growth_3y is intentionally excluded from features (see ml/config.py) —
it's algebraically derived from the same latest price used as target here,
so including it would be leakage, not just correlation.

If a clustering run exists (ml/artifacts/clustering/latest/), its cluster_id
is merged in as an extra categorical feature and carried into the output
predictions — reused, not recomputed (Model 1 -> Model 2 handoff described
in ROADMAP_IA.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import FEATURE_LEVEL, GB_DEFAULT_PARAMS, ML_VERSION, PRICE_MODEL_CATEGORICAL, PRICE_MODEL_FEATURES, RANDOM_STATE, TARGET_COL  # noqa: E402
from ml.data.price_frame import load_cluster_assignments, load_price_frame  # noqa: E402,F401  (re-exported: quantile.py imports it from here)
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata, save_model, timer  # noqa: E402
from ml.evaluation.metrics import print_comparison, regression_report  # noqa: E402

N_EXAMPLES = 10


def build_design_matrix(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """One-hot encodes categoricals over the FULL clean set so train/test share columns."""
    base = df[PRICE_MODEL_FEATURES].copy()

    cat_cols = []
    for col in PRICE_MODEL_CATEGORICAL:
        if col in df.columns:
            base[col] = df[col].fillna("unknown")
            cat_cols.append(col)
    if "cluster_id" in df.columns:
        base["cluster_id"] = df["cluster_id"].fillna(-1).astype(int).astype(str)
        cat_cols.append("cluster_id")

    X = pd.get_dummies(base, columns=cat_cols, dummy_na=False)
    return X, list(X.columns)


def dept_median_baseline(train_df: pd.DataFrame, apply_to: pd.DataFrame, target_col: str = TARGET_COL) -> pd.Series:
    """Predicts department median of target_col (computed on TRAIN only, no leakage).
    Reused as-is by ml/models/yield_model.py with target_col='rendement_brut'."""
    dept_medians = train_df.groupby("department_code")[target_col].median()
    overall_median = train_df[target_col].median()
    return apply_to["department_code"].map(dept_medians).fillna(overall_median)


TUNE_PARAM_DISTRIBUTIONS = {
    "n_estimators": [100, 200, 300, 500],
    "max_depth": [2, 3, 4, 5],
    "learning_rate": [0.01, 0.03, 0.05, 0.1],
    "subsample": [0.6, 0.8, 1.0],
}
TUNE_N_ITER = 20
TUNE_CV = 5


def run(dept_filter: str | None = None, dry_run: bool = False, tune: bool = False) -> Path:
    print(f"Loading price frame (feature level {FEATURE_LEVEL}, target {TARGET_COL})...")
    frame = load_price_frame(dept_filter)
    df, clean = frame.df, frame.clean
    idx_train, idx_val, idx_test = frame.idx_train, frame.idx_val, frame.idx_test

    X_full, feature_names = build_design_matrix(clean)
    y_full = clean[TARGET_COL]

    print(f"  Split: train={len(idx_train)}, val={len(idx_val)}, test={len(idx_test)}")

    X_train, y_train = X_full.loc[idx_train], y_full.loc[idx_train]
    X_val, y_val = X_full.loc[idx_val], y_full.loc[idx_val]
    X_test, y_test = X_full.loc[idx_test], y_full.loc[idx_test]

    print(f"  Split: train={len(idx_train)}, val={len(idx_val)}, test={len(idx_test)}")

    hyperparams_used = GB_DEFAULT_PARAMS
    with timer() as t:
        if tune:
            print(f"Tuning GradientBoostingRegressor (RandomizedSearchCV, {TUNE_N_ITER} candidates x {TUNE_CV}-fold)...")
            from sklearn.model_selection import RandomizedSearchCV
            search = RandomizedSearchCV(
                GradientBoostingRegressor(random_state=RANDOM_STATE),
                TUNE_PARAM_DISTRIBUTIONS,
                n_iter=TUNE_N_ITER,
                cv=TUNE_CV,
                scoring="neg_mean_absolute_error",
                random_state=RANDOM_STATE,
                n_jobs=-1,
            )
            search.fit(X_train, y_train)
            model = search.best_estimator_
            hyperparams_used = search.best_params_
            print(f"  Best params: {hyperparams_used}")
        else:
            print("Training GradientBoostingRegressor (fixed hyperparameters, use --tune to search)...")
            model = GradientBoostingRegressor(**GB_DEFAULT_PARAMS)
            model.fit(X_train, y_train)

    baseline_test_pred = dept_median_baseline(clean.loc[idx_train], clean.loc[idx_test])
    model_test_pred = pd.Series(model.predict(X_test), index=idx_test)

    baseline_metrics = regression_report(y_test, baseline_test_pred)
    model_metrics = regression_report(y_test, model_test_pred)
    print_comparison("Test set", baseline_metrics, model_metrics)

    importance = pd.Series(model.feature_importances_, index=feature_names).sort_values(ascending=False)

    shap_summary = None
    try:
        import shap
        explainer = shap.TreeExplainer(model)
        sample = X_test.sample(min(500, len(X_test)), random_state=RANDOM_STATE)
        shap_values = explainer.shap_values(sample)
        shap_summary = pd.Series(abs(shap_values).mean(axis=0), index=feature_names).sort_values(ascending=False)
    except ImportError:
        print("  shap not installed (pip install shap) — skipping SHAP values.")

    # Comparable-to-v1 view: test communes that also have real quarterly DVF prices (v1's population).
    dvf_test = idx_test[(clean.loc[idx_test, "price_data_source"] == "dvf").values] if "price_data_source" in clean else idx_test[:0]
    subset_metrics = None
    if FEATURE_LEVEL >= 1 and len(dvf_test):
        subset_metrics = {
            "n": len(dvf_test),
            "baseline": regression_report(y_full.loc[dvf_test], baseline_test_pred.loc[dvf_test]),
            "model": regression_report(y_full.loc[dvf_test], model_test_pred.loc[dvf_test]),
        }
        print(f"  Test communes with real quarterly DVF price (n={len(dvf_test)}): "
              f"MAE={subset_metrics['model']['mae']:.1f} R2={subset_metrics['model']['r2']:.3f}")

    # Opportunity score on the full clean population (train+val+test)
    predicted_full = pd.Series(model.predict(X_full), index=clean.index)

    split = pd.Series(index=clean.index, dtype=object)
    split.loc[idx_train] = "train"
    split.loc[idx_val] = "val"
    split.loc[idx_test] = "test"

    predictions = pd.DataFrame({
        "name": clean["name"],
        "department_code": clean["department_code"],
        "price_reel": y_full,
        "price_predit": predicted_full,
        "opportunity_score": (y_full - predicted_full) / predicted_full,
        "cluster_id": clean.get("cluster_id"),
        "split": split,
        **{c: clean[c] for c in ("price_reliability", "n_own", "n_pooled", "pool_radius_km") if c in clean},
    })

    examples = pd.concat([
        predictions.sort_values("opportunity_score").head(N_EXAMPLES // 2),
        predictions.sort_values("opportunity_score").tail(N_EXAMPLES // 2),
    ])

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        print(importance.head(10))
        return Path()

    run_dir = new_run_dir("price_model")
    save_dataframe(run_dir, predictions, "predictions.csv")
    save_dataframe(run_dir, examples, "example_communes.csv")
    save_dataframe(run_dir, importance.to_frame("importance"), "feature_importance.csv")
    if shap_summary is not None:
        save_dataframe(run_dir, shap_summary.to_frame("mean_abs_shap"), "shap_summary.csv")
    save_model(run_dir, model, "model.joblib")

    save_metadata(run_dir, {
        "model": "GradientBoostingRegressor",
        "tuned": tune,
        "hyperparameters": hyperparams_used,
        "target": TARGET_COL,
        "features_raw": PRICE_MODEL_FEATURES,
        "features_encoded": feature_names,
        "ml_version": ML_VERSION or "v1",
        "feature_level": FEATURE_LEVEL,
        "cluster_feature_used": frame.clusters_used,
        "n_communes_loaded": len(df),
        "n_communes_used": len(clean),
        "split_sizes": {"train": len(idx_train), "val": len(idx_val), "test": len(idx_test)},
        "baseline": "department median price/m² (train-only)",
        "baseline_metrics_test": baseline_metrics,
        "model_metrics_test": model_metrics,
        "metrics_test_on_real_dvf_communes": subset_metrics,
        **frame.meta,
        "dept_filter": dept_filter,
        **t,
    })

    latest = publish_latest("price_model", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--dry-run", action="store_true")
    # Default True = the "official" PERFORMANCE.md version (R²=0.81 vs 0.79
    # untuned) — real, measured gain (EXPERIMENTS_LOG.md §Modèle 2 #4). Slower
    # (RandomizedSearchCV, ~100 fits); --no-tune for the old fast/fixed behavior.
    parser.add_argument("--tune", action=argparse.BooleanOptionalAction, default=True,
                         help="RandomizedSearchCV over hyperparameters; --no-tune to use fixed GB_DEFAULT_PARAMS instead (faster)")
    args = parser.parse_args()

    run(dept_filter=args.dept, dry_run=args.dry_run, tune=args.tune)
