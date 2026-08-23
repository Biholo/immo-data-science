"""
Model 1 — HDBSCAN comparison (NOT the mémoire deliverable).

CONTEXTE_ML_MEMOIRE.md §1/§2 explicitly mandates K-Means for Modèle 1 — this
script does NOT replace ml/models/clustering.py, it exists to produce a
documented point of comparison for the mémoire's discussion section ("on a
aussi testé X, silhouette Y vs Z, on garde K-Means car..."). Not wired into
ml/scripts/run_all.py.

HDBSCAN doesn't force every point into a cluster (unlike K-Means) — points
that don't fit any dense region get label -1 ("noise"). Silhouette is computed
excluding noise points, since -1 isn't a real cluster.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import HDBSCAN
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import CLUSTERING_LOG_FEATURES  # noqa: E402
from ml.data.build_cross_sectional import build_cross_sectional  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata, save_model, timer  # noqa: E402
from ml.models.clustering import cluster_profile, prepare_data  # noqa: E402

MIN_CLUSTER_SIZE = 50  # min commune count to count as a real typology, not noise/outlier fragment


def run(dept_filter: str | None = None, min_cluster_size: int = MIN_CLUSTER_SIZE, dry_run: bool = False) -> Path:
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

    print(f"Fitting HDBSCAN (min_cluster_size={min_cluster_size})...")
    with timer() as t:
        hdb = HDBSCAN(min_cluster_size=min_cluster_size)
        labels = hdb.fit_predict(X_scaled)

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int((labels == -1).sum())
    print(f"  {n_clusters} clusters found, {n_noise} noise points ({n_noise/len(labels)*100:.1f}%)")

    mask = labels != -1
    silhouette = float(silhouette_score(X_scaled[mask], labels[mask])) if n_clusters >= 2 else float("nan")
    print(f"  Silhouette (excl. noise): {silhouette:.4f}")

    profile = cluster_profile(clean, labels)
    assignments = pd.DataFrame({"insee_code": clean.index, "cluster_id": labels}).set_index("insee_code")
    assignments = assignments.join(df[["name", "department_code"]], how="left")

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        print(profile)
        return Path()

    run_dir = new_run_dir("clustering_hdbscan")
    save_dataframe(run_dir, assignments, "cluster_assignments.csv")
    save_dataframe(run_dir, profile, "cluster_profile.csv")
    save_model(run_dir, hdb, "hdbscan.joblib")
    save_model(run_dir, scaler, "scaler.joblib")

    save_metadata(run_dir, {
        "model": "HDBSCAN",
        "note": "Comparison only — CONTEXTE_ML_MEMOIRE.md mandates K-Means as Modèle 1 (see ml/models/clustering.py)",
        "min_cluster_size": min_cluster_size,
        "n_communes_used": len(clean),
        "n_clusters_found": n_clusters,
        "n_noise_points": n_noise,
        "noise_pct": round(n_noise / len(labels) * 100, 2),
        "silhouette_excluding_noise": silhouette,
        "dept_filter": dept_filter,
        **t,
    })

    latest = publish_latest("clustering_hdbscan", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--min-cluster-size", type=int, default=MIN_CLUSTER_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(dept_filter=args.dept, min_cluster_size=args.min_cluster_size, dry_run=args.dry_run)
