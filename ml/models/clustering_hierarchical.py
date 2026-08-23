"""
Model 1 — Hierarchical comparison (NOT the mémoire deliverable).

CONTEXTE_ML_MEMOIRE.md §1/§2 explicitly mandates a flat K-Means for Modèle 1 —
this script does NOT replace ml/models/clustering.py. It presplits communes
using INSEE's own official density typology (grille communale de densité,
ml/data/density_grid.py) into 4 coarse groups, then runs K-Means *within*
each group on the same CLUSTERING_FEATURES. Idea: the flat K-Means result is
dominated by the urban/rural gradient (see EXPERIMENTS_LOG.md #2 — k=2 just
splits on population size); presplitting on an authoritative urban/rural
signal first might let the within-group K-Means find genuinely different
structure (e.g. two distinct rural profiles) that gets drowned out otherwise.

Not wired into ml/scripts/run_all.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import CLUSTERING_FEATURES, CLUSTERING_LOG_FEATURES, RANDOM_STATE  # noqa: E402
from ml.data.build_cross_sectional import build_cross_sectional  # noqa: E402
from ml.data.density_grid import load_density_grid  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata, save_model, timer  # noqa: E402
from ml.models.clustering import cluster_profile, external_price_validation, prepare_data  # noqa: E402

# Coarsened from INSEE's 7 levels — see ml/data/density_grid.py docstring.
# Level 4 ("Ceintures urbaines") kept SEPARATE from level 3 ("Petites villes")
# on purpose: it's INSEE's own official definition of "banlieue"/commuter belt.
# An earlier version merged them into one "villes_ceintures" group, which hid
# the suburb category entirely — see EXPERIMENTS_LOG.md §Modèle 1.
DENSITY_GROUPS = {
    1: "urbain_dense", 2: "urbain_dense",
    3: "petites_villes",
    4: "ceintures_urbaines",  # = banlieue
    5: "rural_sous_influence",
    6: "rural_profond", 7: "rural_profond",
}
MIN_GROUP_SIZE = 100  # below this, keep the group whole (sub-clustering ~10 points is noise)
K_PER_GROUP = 2
SILHOUETTE_SAMPLE_SIZE = 5000


def run(dept_filter: str | None = None, k_per_group: int = K_PER_GROUP, dry_run: bool = False) -> Path:
    print("Loading cross-sectional feature table...")
    df = build_cross_sectional(dept_filter=dept_filter, exclude_estimated=False)
    print(f"  {len(df)} communes loaded")

    clean, dropped = prepare_data(df)
    print(f"  {len(clean)} communes with complete features ({len(dropped)} dropped for missing values)")

    density = load_density_grid()
    clean = clean.join(density[["density_code"]], how="inner")
    n_no_density = len(clean) - clean["density_code"].notna().sum()
    print(f"  {len(clean)} communes matched with INSEE density grid ({n_no_density} unmatched, dropped)")
    clean["density_group"] = clean["density_code"].map(DENSITY_GROUPS)

    for_scaling = clean[CLUSTERING_FEATURES].copy()
    for col in CLUSTERING_LOG_FEATURES:
        for_scaling[col] = np.log1p(for_scaling[col])
    scaler = StandardScaler()
    X_scaled = pd.DataFrame(scaler.fit_transform(for_scaling.values), index=clean.index, columns=CLUSTERING_FEATURES)

    all_labels = pd.Series(index=clean.index, dtype=object)
    group_reports = []

    with timer() as t:
        for group_name, group_df in clean.groupby("density_group"):
            X_group = X_scaled.loc[group_df.index]

            if len(group_df) < MIN_GROUP_SIZE:
                all_labels.loc[group_df.index] = f"{group_name}_0"
                group_reports.append({"density_group": group_name, "n": len(group_df), "k_used": 1, "silhouette": None})
                print(f"  {group_name}: n={len(group_df)} < {MIN_GROUP_SIZE}, kept whole (no sub-clustering)")
                continue

            km = KMeans(n_clusters=k_per_group, random_state=RANDOM_STATE, n_init=10)
            local_labels = km.fit_predict(X_group.values)
            all_labels.loc[group_df.index] = [f"{group_name}_{i}" for i in local_labels]

            sample_size = min(SILHOUETTE_SAMPLE_SIZE, len(X_group)) if len(X_group) > SILHOUETTE_SAMPLE_SIZE else None
            sil = float(silhouette_score(X_group.values, local_labels, sample_size=sample_size, random_state=RANDOM_STATE))
            group_reports.append({"density_group": group_name, "n": len(group_df), "k_used": k_per_group, "silhouette": sil})
            print(f"  {group_name}: n={len(group_df)}, k={k_per_group}, silhouette={sil:.4f}")

    labels = all_labels.values
    sample_size = min(SILHOUETTE_SAMPLE_SIZE, len(X_scaled)) if len(X_scaled) > SILHOUETTE_SAMPLE_SIZE else None
    global_silhouette = float(silhouette_score(X_scaled.values, labels, sample_size=sample_size, random_state=RANDOM_STATE))
    print(f"Global silhouette (all sub-clusters as one label space): {global_silhouette:.4f}")

    profile = cluster_profile(clean[CLUSTERING_FEATURES], labels)
    validation = external_price_validation(clean[CLUSTERING_FEATURES], labels, df)
    if validation["anova_f"] is not None:
        print(f"External validation (price/m², never a feature): ANOVA F={validation['anova_f']:.2f}, "
              f"p={validation['anova_p']:.2e}")

    assignments = pd.DataFrame({
        "insee_code": clean.index,
        "cluster_id": labels,
        "density_group": clean["density_group"].values,
        "density_code": clean["density_code"].values,
    }).set_index("insee_code")
    assignments = assignments.join(df[["name", "department_code"]], how="left")

    group_report_df = pd.DataFrame(group_reports)

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        print(profile)
        return Path()

    run_dir = new_run_dir("clustering_hierarchical")
    save_dataframe(run_dir, assignments, "cluster_assignments.csv")
    save_dataframe(run_dir, profile, "cluster_profile.csv")
    save_dataframe(run_dir, group_report_df, "group_report.csv")
    save_dataframe(run_dir, validation["price_by_cluster"], "external_validation_price_by_cluster.csv")
    save_model(run_dir, scaler, "scaler.joblib")

    save_metadata(run_dir, {
        "model": "Hierarchical (INSEE density presplit -> KMeans per group)",
        "note": "Comparison only — CONTEXTE_ML_MEMOIRE.md mandates flat K-Means as Modèle 1 (see ml/models/clustering.py)",
        "density_groups": DENSITY_GROUPS,
        "min_group_size": MIN_GROUP_SIZE,
        "k_per_group": k_per_group,
        "n_communes_used": len(clean),
        "n_communes_unmatched_density": int(n_no_density),
        "global_silhouette": global_silhouette,
        "group_report": group_reports,
        "external_validation_price": {
            "n_communes_with_real_price": validation["n_communes_with_real_price"],
            "anova_f": validation["anova_f"],
            "anova_p": validation["anova_p"],
        },
        "dept_filter": dept_filter,
        **t,
    })

    latest = publish_latest("clustering_hierarchical", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--k-per-group", type=int, default=K_PER_GROUP)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(dept_filter=args.dept, k_per_group=args.k_per_group, dry_run=args.dry_run)
