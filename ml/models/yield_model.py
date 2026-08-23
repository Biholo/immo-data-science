"""
Modèle rendement — symétrique à ml/models/price_model.py, mais répond à une
question différente et jusqu'ici absente : pas "c'est cher ou pas ?" (Modèle
2), mais "**ça rapporte combien ?**". Extension au-delà des 4 modèles du
cahier des charges (CONTEXTE_ML_MEMOIRE.md), construite suite au retour que
sans rendement, l'outil reste un indicateur de prix, pas un vrai outil
d'aide à l'investissement locatif — voir EXPERIMENTS_LOG.md.

Target = rendement_brut (%), calculé dans build_cross_sectional.py comme
`(loyer_m²_annuel / coût_acquisition_m²) × 100` à partir de la Carte des
loyers ANIL/DHUP 2025 — coût_acquisition inclut frais de notaire + travaux
(ml.finance.cashflow.cout_acquisition_total), PAS le prix nu (voir
EXPERIMENTS_LOG.md "Modèle rendement" #6 : les frais d'acquisition sont du
capital dépensé avant la mise en location, donc au dénominateur même du
brut). Mêmes features que Modèle 2 (PRICE_MODEL_FEATURES) — le rendement,
comme le prix, dépend des caractéristiques socio-éco de la commune, mais pas
forcément des MÊMES : un chômage élevé peut faire baisser le prix ET faire
monter le rendement relatif (loyers qui résistent mieux que les prix).

yield_opportunity_score = (rendement_réel - rendement_prédit) / rendement_prédit
positif = la commune rapporte PLUS que ce que ses fondamentaux justifient
(bon signal rendement) — symétrique à Modèle 2 où négatif = sous-évalué.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import GB_DEFAULT_PARAMS, PRICE_MODEL_FEATURES, RANDOM_STATE  # noqa: E402
from ml.data.build_cross_sectional import build_cross_sectional  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata, save_model, timer  # noqa: E402
from ml.evaluation.metrics import print_comparison, regression_report  # noqa: E402
from ml.models.price_model import build_design_matrix, dept_median_baseline, load_cluster_assignments  # noqa: E402

YIELD_TARGET_COL = "rendement_brut"
N_EXAMPLES = 10


def run(dept_filter: str | None = None, tune: bool = False, dry_run: bool = False) -> Path:
    print("Loading cross-sectional feature table...")
    df = build_cross_sectional(dept_filter=dept_filter, exclude_estimated=True)
    print(f"  {len(df)} communes (price_data_source != 'estimated')")

    clusters = load_cluster_assignments()
    if clusters is not None:
        df = df.join(clusters, how="left")

    required = PRICE_MODEL_FEATURES + [YIELD_TARGET_COL, "department_code"]
    clean = df.dropna(subset=required)
    print(f"  {len(clean)} communes with complete features + rendement_brut ({len(df) - len(clean)} dropped)")

    X_full, feature_names = build_design_matrix(clean)
    y_full = clean[YIELD_TARGET_COL]

    idx_train, idx_temp = train_test_split(clean.index, test_size=0.30, random_state=RANDOM_STATE)
    idx_val, idx_test = train_test_split(idx_temp, test_size=0.50, random_state=RANDOM_STATE)
    print(f"  Split: train={len(idx_train)}, val={len(idx_val)}, test={len(idx_test)}")

    X_train, y_train = X_full.loc[idx_train], y_full.loc[idx_train]
    X_test, y_test = X_full.loc[idx_test], y_full.loc[idx_test]

    hyperparams_used = GB_DEFAULT_PARAMS
    with timer() as t:
        if tune:
            print(f"Tuning GradientBoostingRegressor (RandomizedSearchCV)...")
            from sklearn.model_selection import RandomizedSearchCV
            from ml.models.price_model import TUNE_CV, TUNE_N_ITER, TUNE_PARAM_DISTRIBUTIONS
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

    baseline_test_pred = dept_median_baseline(clean.loc[idx_train], clean.loc[idx_test], target_col=YIELD_TARGET_COL)
    model_test_pred = pd.Series(model.predict(X_test), index=idx_test)

    baseline_metrics = regression_report(y_test, baseline_test_pred)
    model_metrics = regression_report(y_test, model_test_pred)
    print_comparison("Test set (rendement_brut, %)", baseline_metrics, model_metrics)

    importance = pd.Series(model.feature_importances_, index=feature_names).sort_values(ascending=False)

    # yield_opportunity_score on the full clean population
    predicted_full = pd.Series(model.predict(X_full), index=clean.index)

    split = pd.Series(index=clean.index, dtype=object)
    split.loc[idx_train] = "train"
    split.loc[idx_val] = "val"
    split.loc[idx_test] = "test"

    predictions = pd.DataFrame({
        "name": clean["name"],
        "department_code": clean["department_code"],
        "rendement_reel": y_full,
        "rendement_predit": predicted_full,
        "yield_opportunity_score": (y_full - predicted_full) / predicted_full,
        "price_reel": clean.get("median_price_per_sqm"),
        "cluster_id": clean.get("cluster_id"),
        "split": split,
    })

    examples = pd.concat([
        predictions.sort_values("yield_opportunity_score").head(N_EXAMPLES // 2),
        predictions.sort_values("yield_opportunity_score").tail(N_EXAMPLES // 2),
    ])

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        print(importance.head(10))
        return Path()

    run_dir = new_run_dir("yield_model")
    save_dataframe(run_dir, predictions, "predictions.csv")
    save_dataframe(run_dir, examples, "example_communes.csv")
    save_dataframe(run_dir, importance.to_frame("importance"), "feature_importance.csv")
    save_model(run_dir, model, "model.joblib")

    save_metadata(run_dir, {
        "model": "GradientBoostingRegressor (rendement_brut)",
        "note": "Extension beyond CONTEXTE_ML_MEMOIRE.md's 4 mandated models — answers 'does it pay off', not just 'is it cheap'",
        "tuned": tune,
        "hyperparameters": hyperparams_used,
        "target": YIELD_TARGET_COL,
        "target_unit": "% (rendement brut annuel)",
        "features_raw": PRICE_MODEL_FEATURES,
        "features_encoded": feature_names,
        "cluster_feature_used": clusters is not None,
        "n_communes_loaded": len(df),
        "n_communes_used": len(clean),
        "split_sizes": {"train": len(idx_train), "val": len(idx_val), "test": len(idx_test)},
        "baseline": "department median rendement_brut (train-only)",
        "baseline_metrics_test": baseline_metrics,
        "model_metrics_test": model_metrics,
        "dept_filter": dept_filter,
        **t,
    })

    latest = publish_latest("yield_model", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--tune", action=argparse.BooleanOptionalAction, default=True,
                         help="RandomizedSearchCV over hyperparameters; --no-tune for fixed GB_DEFAULT_PARAMS (faster)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(dept_filter=args.dept, tune=args.tune, dry_run=args.dry_run)
