"""
Model 1 — Gaussian Mixture Model comparison (NOT the mémoire deliverable).

CONTEXTE_ML_MEMOIRE.md §1/§2 explicitly mandates K-Means for Modèle 1 — this
script does NOT replace ml/models/clustering.py, it exists as a documented
point of comparison. Unlike K-Means (hard assignment), GMM gives a soft
membership probability per commune per cluster — useful to flag communes that
sit "between" two typologies (e.g. between "rural ordinaire" and "pôle urbain
secondaire") rather than force them into whichever centroid is nearest.

Not wired into ml/scripts/run_all.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import silhouette_score
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import CLUSTERING_LOG_FEATURES, RANDOM_STATE  # noqa: E402
from ml.data.build_cross_sectional import build_cross_sectional  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata, save_model, timer  # noqa: E402
from ml.models.clustering import cluster_profile, external_price_validation, prepare_data  # noqa: E402

SILHOUETTE_SAMPLE_SIZE = 5000
BOUNDARY_ENTROPY_THRESHOLD = 0.5  # normalized entropy above this -> "boundary" commune


def _normalized_entropy(probs: np.ndarray) -> np.ndarray:
    """Row-wise entropy of membership probabilities, normalized to [0, 1] by log(n_components)."""
    n_components = probs.shape[1]
    eps = 1e-12
    ent = -(probs * np.log(probs + eps)).sum(axis=1)
    return ent / np.log(n_components)


def run(dept_filter: str | None = None, k: int = 3, dry_run: bool = False) -> Path:
    print("Loading cross-sectional feature table...")
    df = build_cross_sectional(dept_filter=dept_filter, exclude_estimated=False)
    print(f"  {len(df)} communes loaded")

    clean, dropped = prepare_data(df)
    print(f"  {len(clean)} communes with complete features ({len(dropped)} dropped for missing values)")

    for_scaling = clean.copy()
    for col in CLUSTERING_LOG_FEATURES:
        for_scaling[col] = np.log1p(for_scaling[col])

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(for_scaling.values)

    print(f"Fitting GaussianMixture (k={k})...")
    with timer() as t:
        gmm = GaussianMixture(n_components=k, random_state=RANDOM_STATE, n_init=10)
        gmm.fit(X_scaled)
        labels = gmm.predict(X_scaled)
        probs = gmm.predict_proba(X_scaled)

    sample_size = min(SILHOUETTE_SAMPLE_SIZE, len(X_scaled)) if len(X_scaled) > SILHOUETTE_SAMPLE_SIZE else None
    silhouette = float(silhouette_score(X_scaled, labels, sample_size=sample_size, random_state=RANDOM_STATE))
    print(f"  Silhouette (hard labels, for comparability with K-Means): {silhouette:.4f}")

    entropy = _normalized_entropy(probs)
    n_boundary = int((entropy > BOUNDARY_ENTROPY_THRESHOLD).sum())
    print(f"  {n_boundary}/{len(clean)} communes ({n_boundary/len(clean)*100:.1f}%) are 'boundary' "
          f"(normalized entropy > {BOUNDARY_ENTROPY_THRESHOLD}) — ambiguous between clusters, "
          f"invisible to a hard K-Means assignment")

    profile = cluster_profile(clean, labels)
    validation = external_price_validation(clean, labels, df)
    if validation["anova_f"] is not None:
        print(f"  External validation (price/m², never a feature): ANOVA F={validation['anova_f']:.2f}, "
              f"p={validation['anova_p']:.2e}")

    assignments = pd.DataFrame({
        "insee_code": clean.index,
        "cluster_id": labels,
        "membership_entropy": entropy,
        "is_boundary": entropy > BOUNDARY_ENTROPY_THRESHOLD,
    }).set_index("insee_code")
    for i in range(k):
        assignments[f"proba_cluster_{i}"] = probs[:, i]
    assignments = assignments.join(df[["name", "department_code"]], how="left")

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        print(profile)
        return Path()

    run_dir = new_run_dir("clustering_gmm")
    save_dataframe(run_dir, assignments, "cluster_assignments.csv")
    save_dataframe(run_dir, profile, "cluster_profile.csv")
    save_dataframe(run_dir, validation["price_by_cluster"], "external_validation_price_by_cluster.csv")
    save_model(run_dir, gmm, "gmm.joblib")
    save_model(run_dir, scaler, "scaler.joblib")

    save_metadata(run_dir, {
        "model": "GaussianMixture",
        "note": "Comparison only — CONTEXTE_ML_MEMOIRE.md mandates K-Means as Modèle 1 (see ml/models/clustering.py)",
        "k": k,
        "n_communes_used": len(clean),
        "silhouette_hard_labels": silhouette,
        "n_boundary_communes": n_boundary,
        "boundary_entropy_threshold": BOUNDARY_ENTROPY_THRESHOLD,
        "external_validation_price": {
            "n_communes_with_real_price": validation["n_communes_with_real_price"],
            "anova_f": validation["anova_f"],
            "anova_p": validation["anova_p"],
        },
        "dept_filter": dept_filter,
        **t,
    })

    latest = publish_latest("clustering_gmm", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(dept_filter=args.dept, k=args.k, dry_run=args.dry_run)
