"""
Model 4 (optional) — Quantile regression for price/m² uncertainty bounds.
See CONTEXTE_ML_MEMOIRE.md §5.

Trains three GradientBoostingRegressor(loss='quantile') at tau=0.1/0.5/0.9,
giving [borne_basse, prix_central, borne_haute] instead of a single point
estimate. Reuses the exact same feature set, cleaning and train/val/test split
as ml/models/price_model.py (same RANDOM_STATE + identical filtering →
deterministic, reproducible split without needing to persist indices).

Reports pinball loss per quantile, plus empirical coverage: the % of test
communes whose real price actually falls inside [Q10, Q90] — should land
near 80% if the model's uncertainty is well calibrated.

Trained on log1p(price), predictions expm1'd back before scoring — price/m²
is right-skewed (few very expensive communes stretch the upper tail), and a
first pass on raw price systematically lost to the baseline at tau=0.9
(pinball 162.6 vs 161.6) while winning everywhere else. Log-space symmetrizes
the errors GB is optimizing over, which should help specifically the upper
quantile without touching how coverage/pinball are reported (both computed
back in € terms, so results stay comparable across runs).

Split conformal calibration (CQR — Romano, Patterson & Candès 2019) on top:
the raw Q10/Q90 predictions get 76.6% empirical coverage, not the ~80% their
names imply — GB's quantile loss has no coverage guarantee, it's just an
approximation. Conformal calibration uses the held-out VAL split (computed
but previously unused past the split itself) to measure how far the true
values fall outside [Q10, Q90] on unseen data, then widens the interval by
that empirical margin. This gives a distribution-free coverage guarantee
under exchangeability, not just a hope that the loss function was calibrated.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import GB_DEFAULT_PARAMS, PRICE_MODEL_FEATURES, RANDOM_STATE, TARGET_COL  # noqa: E402
from ml.data.build_cross_sectional import build_cross_sectional  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata, save_model, timer  # noqa: E402
from ml.evaluation.metrics import pinball_loss  # noqa: E402
from ml.models.price_model import build_design_matrix, load_cluster_assignments  # noqa: E402

QUANTILES = [0.1, 0.5, 0.9]
CONFORMAL_ALPHA = 0.2  # target interval = 1 - alpha = 80%, matching the Q10/Q90 nominal coverage


def dept_quantile_baseline(train_df: pd.DataFrame, apply_to: pd.DataFrame, tau: float) -> pd.Series:
    dept_q = train_df.groupby("department_code")[TARGET_COL].quantile(tau)
    overall_q = train_df[TARGET_COL].quantile(tau)
    return apply_to["department_code"].map(dept_q).fillna(overall_q)


def conformal_margin(q10: pd.Series, q90: pd.Series, y_true: pd.Series, alpha: float = CONFORMAL_ALPHA) -> float:
    """
    Split CQR calibration margin from a held-out set never used for training.
    Nonconformity score E_i = max(q10(x_i) - y_i, y_i - q90(x_i)) — how far
    outside the interval the true value falls (negative if inside). margin =
    the (1-alpha)-quantile of {E_i}, with the standard finite-sample
    correction (ceil((n+1)(1-alpha))/n). Widening [q10, q90] by this margin
    gives ~(1-alpha) coverage on new data under exchangeability.
    """
    scores = np.maximum(q10 - y_true, y_true - q90)
    n = len(scores)
    level = min(1.0, np.ceil((n + 1) * (1 - alpha)) / n)
    return float(np.quantile(scores, level))


def run(dept_filter: str | None = None, dry_run: bool = False) -> Path:
    print("Loading cross-sectional feature table...")
    df = build_cross_sectional(dept_filter=dept_filter, exclude_estimated=True)

    clusters = load_cluster_assignments()
    if clusters is not None:
        df = df.join(clusters, how="left")

    required = PRICE_MODEL_FEATURES + [TARGET_COL, "department_code"]
    clean = df.dropna(subset=required)
    print(f"  {len(clean)} communes with complete features + target")

    X_full, feature_names = build_design_matrix(clean)
    y_full = clean[TARGET_COL]

    idx_train, idx_temp = train_test_split(clean.index, test_size=0.30, random_state=RANDOM_STATE)
    idx_val, idx_test = train_test_split(idx_temp, test_size=0.50, random_state=RANDOM_STATE)
    print(f"  Split: train={len(idx_train)}, val={len(idx_val)}, test={len(idx_test)}")

    X_train, y_train = X_full.loc[idx_train], y_full.loc[idx_train]
    X_val, y_val = X_full.loc[idx_val], y_full.loc[idx_val]
    X_test, y_test = X_full.loc[idx_test], y_full.loc[idx_test]
    y_train_log = np.log1p(y_train)

    models: dict[float, GradientBoostingRegressor] = {}
    preds_test: dict[float, pd.Series] = {}
    metrics: dict[str, dict] = {}

    with timer() as t:
        for tau in QUANTILES:
            print(f"Training quantile regressor tau={tau}...")
            params = {**GB_DEFAULT_PARAMS, "loss": "quantile", "alpha": tau}
            model = GradientBoostingRegressor(**params)
            model.fit(X_train, y_train_log)
            models[tau] = model

            pred_test = pd.Series(np.expm1(model.predict(X_test)), index=idx_test)
            preds_test[tau] = pred_test

            baseline_pred = dept_quantile_baseline(clean.loc[idx_train], clean.loc[idx_test], tau)
            metrics[f"tau_{tau}"] = {
                "pinball_model": pinball_loss(y_test, pred_test, tau),
                "pinball_baseline_dept_quantile": pinball_loss(y_test, baseline_pred, tau),
            }
            print(f"  tau={tau}: pinball model={metrics[f'tau_{tau}']['pinball_model']:.2f}"
                  f" vs baseline={metrics[f'tau_{tau}']['pinball_baseline_dept_quantile']:.2f}")

    q10, q50, q90 = preds_test[0.1], preds_test[0.5], preds_test[0.9]
    coverage_raw = float(((y_test >= q10) & (y_test <= q90)).mean())
    print(f"  Empirical coverage [Q10, Q90] (raw): {coverage_raw*100:.1f}% (target ~80%)")

    q10_val = pd.Series(np.expm1(models[0.1].predict(X_val)), index=idx_val)
    q90_val = pd.Series(np.expm1(models[0.9].predict(X_val)), index=idx_val)
    margin = conformal_margin(q10_val, q90_val, y_val)
    q10_conf, q90_conf = q10 - margin, q90 + margin
    coverage_conf = float(((y_test >= q10_conf) & (y_test <= q90_conf)).mean())
    print(f"  Conformal margin (from val set): ±{margin:.1f}€/m²")
    print(f"  Empirical coverage [Q10, Q90] (conformal): {coverage_conf*100:.1f}% (target ~80%)")

    predictions = pd.DataFrame({
        "name": clean.loc[idx_test, "name"],
        "department_code": clean.loc[idx_test, "department_code"],
        "price_reel": y_test,
        "borne_basse_q10": q10,
        "prix_central_q50": q50,
        "borne_haute_q90": q90,
        "within_interval": (y_test >= q10) & (y_test <= q90),
        "borne_basse_q10_conformal": q10_conf,
        "borne_haute_q90_conformal": q90_conf,
        "within_interval_conformal": (y_test >= q10_conf) & (y_test <= q90_conf),
    })

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        return Path()

    run_dir = new_run_dir("quantile")
    save_dataframe(run_dir, predictions, "predictions.csv")
    for tau, model in models.items():
        save_model(run_dir, model, f"model_q{int(tau*100)}.joblib")

    save_metadata(run_dir, {
        "model": "GradientBoostingRegressor(loss='quantile')",
        "target_transform": "log1p (predictions expm1'd back to € before scoring)",
        "quantiles": QUANTILES,
        "hyperparameters": {k: v for k, v in GB_DEFAULT_PARAMS.items()},
        "target": TARGET_COL,
        "features_raw": PRICE_MODEL_FEATURES,
        "features_encoded": feature_names,
        "split_sizes": {"train": len(idx_train), "val": len(idx_val), "test": len(idx_test)},
        "metrics_test": metrics,
        "empirical_coverage_q10_q90_raw": coverage_raw,
        "conformal_alpha": CONFORMAL_ALPHA,
        "conformal_margin_eur": margin,
        "empirical_coverage_q10_q90_conformal": coverage_conf,
        "dept_filter": dept_filter,
        **t,
    })

    latest = publish_latest("quantile", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(dept_filter=args.dept, dry_run=args.dry_run)
