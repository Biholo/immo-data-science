"""
Model 3 — Gradient Boosting price forecasting with lags, pooled across communes.
See CONTEXTE_ML_MEMOIRE.md §4.

One GradientBoostingRegressor PER horizon (t+1, t+2, t+3, t+4), each trained
directly on real historical lags (never on another model's prediction). This
replaced an earlier recursive rollout (single model, fed its own t+1
prediction back in as lag1 to get t+2, etc.) after that approach produced
unstable results on edge-case communes — see EXPERIMENTS_LOG.md §Modèle 3 #2
(Bohain-en-Vermandois: 687€/m² -> recursive forecast of 4021€ at t+1, 2007€ at
t+4, +192% growth_1y — clearly broken, and 111/3183 communes exceeded ±20%
"growth" in one year). Direct per-horizon models cannot compound error this
way: every prediction traces back to one real, observed row.

Chronological split: for horizon h, train = rows whose TARGET quarter
(anchor + h quarters) falls before FORECAST_TEST_YEAR, test = target quarter
in FORECAST_TEST_YEAR. Baseline is the naive P_hat_(t+h) = P_t (persistence).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import FORECAST_TEST_YEAR, GB_DEFAULT_PARAMS, LAGS, PRICE_MODEL_FEATURES, PRICE_SERIE  # noqa: E402
from ml.data.build_panel import build_panel  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata, save_model, timer  # noqa: E402
from ml.evaluation.metrics import print_comparison, regression_report  # noqa: E402
from ml.models.price_model import load_cluster_assignments  # noqa: E402

LAG_COLS = [f"{PRICE_SERIE}_lag{lag}" for lag in LAGS]
STATIC_FEATURES = [f for f in PRICE_MODEL_FEATURES if f != "transaction_volume"]
REQUIRED_FEATURES = LAG_COLS + STATIC_FEATURES + ["transaction_volume"]
N_EXAMPLES = 8
FORECAST_HORIZONS = [1, 2, 3, 4]
EXTRAPOLATION_RISK_THRESHOLD = 20.0  # |growth_1y| beyond this (%) flagged, not filtered — see forecast_latest
INTERVAL_QUANTILES = (0.1, 0.9)  # matches Model 4's Q10/Q90 convention


def quarter_onehot(period: pd.Series) -> pd.DataFrame:
    q = period.dt.quarter
    return pd.DataFrame({f"quarter_{i}": (q == i).astype(int) for i in range(1, 5)}, index=period.index)


def build_design_matrix(panel: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    X = panel[REQUIRED_FEATURES].copy()
    X = pd.concat([X, quarter_onehot(panel["period"])], axis=1)
    if "cluster_id" in panel.columns:
        cluster_cat = panel["cluster_id"].fillna(-1).astype(int).astype(str)
        X = pd.concat([X, pd.get_dummies(cluster_cat, prefix="cluster")], axis=1)
    return X, list(X.columns)


def add_direct_targets(panel: pd.DataFrame, horizons: list[int] = FORECAST_HORIZONS) -> pd.DataFrame:
    """
    For each horizon h, adds target_t{h} (the real price h quarters ahead of
    this row, within the same commune) and target_period_t{h} (that quarter's
    calendar date, needed to split train/test by the TARGET's year rather
    than the anchor's year — otherwise a t+4 test row anchored in 2025 would
    need 2026 data that doesn't exist).
    """
    panel = panel.sort_values(["insee_code", "period"]).copy()
    for h in horizons:
        panel[f"target_t{h}"] = panel.groupby("insee_code")[PRICE_SERIE].shift(-h)
        panel[f"target_period_t{h}"] = panel.groupby("insee_code")["period"].shift(-h)
    return panel


def train_horizon_models(
    panel: pd.DataFrame, X_all: pd.DataFrame, horizons: list[int] = FORECAST_HORIZONS,
) -> dict[int, dict]:
    """
    Trains one model per horizon. Returns {h: {"model", "baseline_metrics",
    "model_metrics", "test_predictions", "train_rows", "test_rows"}}.
    """
    results: dict[int, dict] = {}

    for h in horizons:
        target_col, target_period_col = f"target_t{h}", f"target_period_t{h}"
        # PRICE_SERIE itself (not just the lags) is required: it's both a training
        # signal already implied by the lags of FUTURE rows, and the naive baseline
        # P_hat_(t+h) = P_t reads it directly. Some panel rows have transaction_volume
        # but no price_sqm_all (different min_n thresholds in series.py) — without this,
        # baseline_pred silently contains NaN and every baseline metric comes out NaN.
        needed = REQUIRED_FEATURES + [target_col, target_period_col, PRICE_SERIE]
        clean_h = panel.dropna(subset=needed)

        X_h = X_all.loc[clean_h.index]
        y_h = clean_h[target_col]

        train_mask = clean_h[target_period_col].dt.year < FORECAST_TEST_YEAR
        test_mask = clean_h[target_period_col].dt.year == FORECAST_TEST_YEAR

        X_train, y_train = X_h[train_mask], y_h[train_mask]
        X_test, y_test = X_h[test_mask], y_h[test_mask]

        print(f"  t+{h}: {len(clean_h)} usable rows, train={train_mask.sum()}, test={test_mask.sum()}")

        model = GradientBoostingRegressor(**GB_DEFAULT_PARAMS)
        model.fit(X_train, y_train)

        # Growing uncertainty interval per horizon: two extra quantile regressors
        # (tau=0.1/0.9), same log1p-target trick as ml/models/quantile.py (price/m²
        # is right-skewed — see EXPERIMENTS_LOG.md §Modèle 4 #1 for why raw-scale
        # quantile GB underperforms on the upper tail).
        y_train_log = np.log1p(y_train)
        quantile_models = {}
        for tau in INTERVAL_QUANTILES:
            qm = GradientBoostingRegressor(**{**GB_DEFAULT_PARAMS, "loss": "quantile", "alpha": tau})
            qm.fit(X_train, y_train_log)
            quantile_models[tau] = qm

        baseline_pred = clean_h.loc[test_mask, PRICE_SERIE]  # naive: P_hat_(t+h) = P_t
        model_pred = pd.Series(model.predict(X_test), index=X_test.index)

        baseline_metrics = regression_report(y_test, baseline_pred)
        model_metrics = regression_report(y_test, model_pred)
        print_comparison(f"  t+{h} test set ({FORECAST_TEST_YEAR})", baseline_metrics, model_metrics)

        q10_test = pd.Series(np.expm1(quantile_models[0.1].predict(X_test)), index=X_test.index)
        q90_test = pd.Series(np.expm1(quantile_models[0.9].predict(X_test)), index=X_test.index)
        coverage = float(((y_test >= q10_test) & (y_test <= q90_test)).mean())
        mean_width_pct = float(((q90_test - q10_test) / model_pred.clip(lower=1)).mean() * 100)
        print(f"  t+{h} interval: coverage={coverage*100:.1f}% (target ~80%), "
              f"mean width={mean_width_pct:.1f}% of point forecast")

        test_predictions = pd.DataFrame({
            "insee_code": clean_h.loc[test_mask, "insee_code"],
            "name": clean_h.loc[test_mask, "name"],
            "anchor_period": clean_h.loc[test_mask, "period"],
            "target_period": clean_h.loc[test_mask, target_period_col],
            "price_reel": y_test,
            "price_predit_model": model_pred,
            "price_predit_baseline": baseline_pred,
            "borne_basse_q10": q10_test,
            "borne_haute_q90": q90_test,
        })

        results[h] = {
            "model": model,
            "quantile_models": quantile_models,
            "baseline_metrics": baseline_metrics,
            "model_metrics": model_metrics,
            "interval_coverage": coverage,
            "interval_mean_width_pct": mean_width_pct,
            "test_predictions": test_predictions,
            "train_rows": int(train_mask.sum()),
            "test_rows": int(test_mask.sum()),
        }

    return results


def forecast_latest(
    horizon_results: dict[int, dict], panel: pd.DataFrame, feature_cols: list[str],
) -> pd.DataFrame:
    """
    For each commune, takes its single most recent row with complete features
    and applies all 4 horizon models directly to it — no chaining, no
    predictions feeding into other predictions. Returns one row per commune:
    insee_code, name, last_actual_period, last_actual_price,
    forecast_t1..t4, forecast_t{h}_q10/q90 (interval, widening with h — see
    INTERVAL_QUANTILES), growth_1y.
    """
    complete = panel.dropna(subset=REQUIRED_FEATURES + [PRICE_SERIE])
    latest = complete.sort_values("period").groupby("insee_code").tail(1).copy()

    X_latest, _ = build_design_matrix(latest)
    X_latest = X_latest.reindex(columns=feature_cols, fill_value=0)

    out = pd.DataFrame({
        "insee_code": latest["insee_code"].values,
        "name": latest["name"].values,
        "last_actual_period": latest["period"].values,
        "last_actual_price": latest[PRICE_SERIE].values,
    })

    for h, r in horizon_results.items():
        out[f"forecast_t{h}"] = r["model"].predict(X_latest)
        out[f"forecast_t{h}_q10"] = np.expm1(r["quantile_models"][0.1].predict(X_latest))
        out[f"forecast_t{h}_q90"] = np.expm1(r["quantile_models"][0.9].predict(X_latest))

    out["growth_1y"] = (out["forecast_t4"] / out["last_actual_price"] - 1) * 100
    # Direct per-horizon models can't compound error the way a recursive rollout
    # did (see EXPERIMENTS_LOG.md §Modèle 3 #2/#9), but t+4 is still a genuine
    # extrapolation for the most recent quarter (no ground truth exists yet to
    # check against) — flag rather than hide the communes where that risk shows
    # up as an implausible year-over-year swing.
    out["extrapolation_risk"] = out["growth_1y"].abs() > EXTRAPOLATION_RISK_THRESHOLD
    return out


def run(dept_filter: str | None = None, dry_run: bool = False) -> Path:
    print("Building commune x quarter panel...")
    panel = build_panel(dept_filter=dept_filter)
    print(f"  {len(panel)} rows, {panel['insee_code'].nunique()} communes, {panel['period'].nunique()} quarters")

    clusters = load_cluster_assignments()
    if clusters is not None:
        # clusters is indexed by insee_code (see price_model.load_cluster_assignments);
        # panel has insee_code as a plain column, not its index — merge on that explicitly.
        panel = panel.merge(clusters, left_on="insee_code", right_index=True, how="left")

    panel = add_direct_targets(panel)
    X_all, feature_cols = build_design_matrix(panel)

    print(f"Training {len(FORECAST_HORIZONS)} direct horizon models (pooled across communes)...")
    with timer() as t:
        horizon_results = train_horizon_models(panel, X_all)

    print("Direct forecast (t+1..t+4, with widening Q10/Q90 interval) for the latest quarter per commune...")
    forecast_summary = forecast_latest(horizon_results, panel, feature_cols)
    print(f"  {len(forecast_summary)} communes forecast")
    for h in FORECAST_HORIZONS:
        width = (forecast_summary[f"forecast_t{h}_q90"] - forecast_summary[f"forecast_t{h}_q10"]).median()
        print(f"  t+{h} median interval width (Q90-Q10): {width:.0f}€/m²")

    growth = forecast_summary["growth_1y"].dropna()
    n_flagged = int(forecast_summary["extrapolation_risk"].sum())
    n_extreme_50 = int((growth.abs() > 50).sum())
    print(f"  growth_1y: median={growth.median():.1f}%, "
          f"{n_flagged}/{len(forecast_summary)} flagged extrapolation_risk (>±{EXTRAPOLATION_RISK_THRESHOLD:.0f}%), "
          f"{n_extreme_50}/{len(growth)} exceed ±50%")

    test_predictions_t1 = horizon_results[1]["test_predictions"]
    examples = (
        test_predictions_t1.sample(min(N_EXAMPLES, len(test_predictions_t1)), random_state=42)
        if len(test_predictions_t1) else test_predictions_t1
    )

    importance_t1 = pd.Series(
        horizon_results[1]["model"].feature_importances_, index=feature_cols
    ).sort_values(ascending=False)

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        print(importance_t1.head(10))
        return Path()

    run_dir = new_run_dir("forecasting")
    save_dataframe(run_dir, test_predictions_t1, "test_predictions.csv")
    save_dataframe(run_dir, examples, "example_communes.csv")
    save_dataframe(run_dir, forecast_summary, "forecast_summary_t4.csv")
    save_dataframe(run_dir, importance_t1.to_frame("importance"), "feature_importance.csv")
    for h, r in horizon_results.items():
        save_model(run_dir, r["model"], f"model_t{h}.joblib")
        save_model(run_dir, r["quantile_models"][0.1], f"model_t{h}_q10.joblib")
        save_model(run_dir, r["quantile_models"][0.9], f"model_t{h}_q90.joblib")

    save_metadata(run_dir, {
        "model": "GradientBoostingRegressor x4 (one per horizon, direct — not recursive)",
        "hyperparameters": GB_DEFAULT_PARAMS,
        "lags": LAGS,
        "static_features": STATIC_FEATURES,
        "cluster_feature_used": clusters is not None,
        "features_encoded": feature_cols,
        "n_panel_rows": len(panel),
        "n_communes": panel["insee_code"].nunique(),
        "n_quarters": panel["period"].nunique(),
        "train_test_split": f"train: target quarter < {FORECAST_TEST_YEAR}, test: target quarter = {FORECAST_TEST_YEAR} (chronological, per-horizon)",
        "baseline": "naive P_hat_(t+h) = P_t (persistence)",
        "interval_quantiles": INTERVAL_QUANTILES,
        "metrics_by_horizon": {
            h: {
                "train_rows": r["train_rows"], "test_rows": r["test_rows"],
                "baseline_metrics_test": r["baseline_metrics"], "model_metrics_test": r["model_metrics"],
                "interval_coverage_test": r["interval_coverage"],
                "interval_mean_width_pct_test": r["interval_mean_width_pct"],
            }
            for h, r in horizon_results.items()
        },
        "n_communes_with_t4_forecast": len(forecast_summary),
        "growth_1y_median_pct": float(growth.median()) if len(growth) else None,
        "extrapolation_risk_threshold_pct": EXTRAPOLATION_RISK_THRESHOLD,
        "n_flagged_extrapolation_risk": n_flagged,
        "growth_1y_n_exceeding_50pct": n_extreme_50,
        "dept_filter": dept_filter,
        **t,
    })

    latest = publish_latest("forecasting", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(dept_filter=args.dept, dry_run=args.dry_run)
