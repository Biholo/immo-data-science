"""
Modèle hédonique (transaction-level) — corrige le défaut structurel de
ml/models/price_model.py: celui-ci prédit un prix moyen/médian par COMMUNE,
aveugle à la variance intra-commune (un Haussmannien centre-ville vs un HLM
des années 70 dans la même ville). Ici, une ligne = une transaction DVF réelle
(surface, type de bien, nombre de pièces), pas une moyenne.

Pas un modèle du cahier des charges CONTEXTE_ML_MEMOIRE.md — extension
exploratoire au-delà des 4 modèles mandatés, casse le plafond de précision
identifié dans EXPERIMENTS_LOG.md §Modèle 2 #3.

Design: chaque transaction reçoit à la fois ses caractéristiques propres
(surface_bati, type_local, nb_pieces) ET le contexte de sa commune (mêmes
features que price_model.py — population, median_income, dist_nearest_major_city_km,
etc.), PLUS le prix médian de la commune (`median_price_per_sqm`) comme
feature — pas une fuite : c'est une info légitimement disponible au moment de
la prédiction (déjà calculée par denormalize.py), le modèle apprend à corriger
cette moyenne selon les caractéristiques du bien plutôt que de repartir de zéro.

Baseline = le prix médian de la commune (median_price_per_sqm) appliqué tel
quel à chaque transaction — exactement ce que price_model.py "sait" déjà.
Le gain mesuré ici = la valeur ajoutée réelle de descendre au niveau transaction.

Limite honnête : le DVF standard n'a pas l'étage, l'état du bien, l'année de
construction, ni le DPE — surface/type/pièces/terrain seulement. "Hédonique"
mais incomplet, pas un modèle immobilier professionnel complet.
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
from ml.data.build_transactions import DEFAULT_SAMPLE, build_transactions  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata, save_model, timer  # noqa: E402
from ml.evaluation.metrics import print_comparison, regression_report  # noqa: E402
from ml.models.price_model import load_cluster_assignments  # noqa: E402

TRANSACTION_NUMERIC = ["surface_bati", "nb_pieces", TARGET_COL]  # commune baseline included as a feature, see docstring
LGBM_PARAMS = dict(
    n_estimators=300,
    max_depth=6,          # deeper than price_model's default (3) — far more rows (100k-1M+) can support it
    learning_rate=0.05,
    subsample=0.8,
    random_state=RANDOM_STATE,
    verbose=-1,
)


def build_design_matrix(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    X = df[TRANSACTION_NUMERIC + PRICE_MODEL_FEATURES].copy()

    cat_cols = ["type_local"]
    X["type_local"] = df["type_local"].astype("category")
    X["quarter_of_year"] = df["period"].dt.quarter.astype("category")
    cat_cols.append("quarter_of_year")

    for col in PRICE_MODEL_CATEGORICAL:
        if col in df.columns:
            X[col] = df[col].fillna("unknown").astype("category")
            cat_cols.append(col)
    if "cluster_id" in df.columns:
        X["cluster_id"] = df["cluster_id"].fillna(-1).astype(int).astype("category")
        cat_cols.append("cluster_id")

    return X, cat_cols


def run(
    dept_filter: str | None = None,
    sample: int | None = DEFAULT_SAMPLE,
    dry_run: bool = False,
) -> Path:
    print(f"Loading DVF transactions (sample={sample or 'full'})...")
    tx = build_transactions(dept_filter=dept_filter, sample=sample)
    print(f"  {len(tx)} transactions loaded")

    print("Loading commune context (same features as price_model.py)...")
    communes = build_cross_sectional(dept_filter=dept_filter, exclude_estimated=True)
    clusters = load_cluster_assignments()
    if clusters is not None:
        communes = communes.join(clusters, how="left")

    df = tx.merge(communes, on="insee_code", how="inner", suffixes=("", "_commune"))
    print(f"  {len(df)} transactions matched to a real (non-estimated) commune price")

    required = TRANSACTION_NUMERIC + PRICE_MODEL_FEATURES
    clean = df.dropna(subset=required)
    print(f"  {len(clean)} transactions with complete features ({len(df) - len(clean)} dropped)")

    X_full, cat_cols = build_design_matrix(clean)
    y_full = clean["price_sqm"]
    baseline_full = clean[TARGET_COL]  # commune median, same value repeated per transaction

    train_mask = clean["period"].dt.year < 2025
    test_mask = clean["period"].dt.year == 2025
    print(f"  Train: {train_mask.sum()} (< 2025) | Test: {test_mask.sum()} (2025)")

    idx_train = clean.index[train_mask]
    idx_test = clean.index[test_mask]
    X_train, y_train = X_full.loc[idx_train], y_full.loc[idx_train]
    X_test, y_test = X_full.loc[idx_test], y_full.loc[idx_test]

    print("Training LGBMRegressor (transaction-level, native categorical)...")
    with timer() as t:
        model = lgb.LGBMRegressor(**LGBM_PARAMS)
        model.fit(X_train, y_train, categorical_feature=cat_cols)

    baseline_test_pred = baseline_full.loc[idx_test]
    model_test_pred = pd.Series(model.predict(X_test), index=idx_test)

    baseline_metrics = regression_report(y_test, baseline_test_pred)
    model_metrics = regression_report(y_test, model_test_pred)
    print_comparison("Test set (transactions, 2025)", baseline_metrics, model_metrics)

    importance = pd.Series(model.feature_importances_, index=X_full.columns).sort_values(ascending=False)
    print(importance.head(8))

    test_predictions = pd.DataFrame({
        "insee_code": clean.loc[idx_test, "insee_code"],
        "type_local": clean.loc[idx_test, "type_local"].map({1: "maison", 2: "appartement"}),
        "surface_bati": clean.loc[idx_test, "surface_bati"],
        "nb_pieces": clean.loc[idx_test, "nb_pieces"],
        "prix_m2_reel": y_test,
        "prix_m2_predit_transaction": model_test_pred,
        "prix_m2_baseline_commune": baseline_test_pred,
    }) if test_mask.sum() else pd.DataFrame()
    examples = test_predictions.sample(min(10, len(test_predictions)), random_state=RANDOM_STATE) if len(test_predictions) else test_predictions
    # test_predictions (full test set) kept separate from examples (10 rows for
    # mémoire citation) — the full set is what ml/scripts/generate_charts.py
    # plots (scatter réel vs prédit needs more than 10 points to be readable).
    plot_sample = test_predictions.sample(min(5000, len(test_predictions)), random_state=RANDOM_STATE) if len(test_predictions) else test_predictions

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        return Path()

    run_dir = new_run_dir("price_hedonic")
    save_dataframe(run_dir, examples, "example_transactions.csv")
    save_dataframe(run_dir, plot_sample, "test_predictions_sample.csv")
    save_dataframe(run_dir, importance.to_frame("importance"), "feature_importance.csv")
    save_model(run_dir, model, "model.joblib")

    save_metadata(run_dir, {
        "model": "LGBMRegressor (transaction-level hedonic)",
        "note": "Extension beyond CONTEXTE_ML_MEMOIRE.md's 4 mandated models — see EXPERIMENTS_LOG.md §Modèle 2 #3",
        "hyperparameters": LGBM_PARAMS,
        "categorical_features": cat_cols,
        "sample_size": sample,
        "n_transactions_loaded": len(tx),
        "n_transactions_matched": len(df),
        "n_transactions_used": len(clean),
        "train_test_split": "chronological: train < 2025, test = 2025",
        "train_rows": int(train_mask.sum()),
        "test_rows": int(test_mask.sum()),
        "baseline": "commune median_price_per_sqm applied to every transaction in that commune (= price_model.py's own answer)",
        "baseline_metrics_test": baseline_metrics,
        "model_metrics_test": model_metrics,
        "dept_filter": dept_filter,
        **t,
    })

    latest = publish_latest("price_hedonic", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--full", action="store_true", help="No sampling, use all usable transactions (~4.86M national)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(dept_filter=args.dept, sample=None if args.full else args.sample, dry_run=args.dry_run)
