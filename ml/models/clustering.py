"""
Model 1 — K-Means clustering of communes into market typologies.
See CONTEXTE_ML_MEMOIRE.md §2.

Deliberately excludes price/m² from features (avoids circularity — this
clustering is meant to later contextualize the price model's residual).
Cluster *labels* (e.g. "tendu étudiant", "périurbain vieillissant") are NOT
assigned automatically — CONTEXTE_ML_MEMOIRE.md is explicit that this is a
manual step done after inspecting cluster_profile.csv. This script produces
everything needed to do that inspection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import f_oneway
from sklearn.cluster import KMeans
from sklearn.feature_selection import f_classif
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import CLUSTERING_FEATURES, CLUSTERING_LOG_FEATURES, RANDOM_STATE  # noqa: E402
from ml.data.build_cross_sectional import build_cross_sectional  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata, save_model, timer  # noqa: E402

K_RANGE = range(2, 11)
SILHOUETTE_SAMPLE_SIZE = 5000
N_REPRESENTATIVE = 8


def prepare_data(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (clean_features_df, dropped_rows_report). Index = insee_code."""
    features = df[CLUSTERING_FEATURES].copy()
    mask = features.notna().all(axis=1)
    clean = features[mask]
    dropped = features[~mask]
    return clean, dropped


def scan_k(X_scaled: np.ndarray, k_range=K_RANGE, random_state: int = RANDOM_STATE) -> pd.DataFrame:
    """Fits KMeans for each k, returns DataFrame(k, inertia, silhouette)."""
    rows = []
    n = X_scaled.shape[0]
    sample_size = min(SILHOUETTE_SAMPLE_SIZE, n) if n > SILHOUETTE_SAMPLE_SIZE else None

    for k in k_range:
        km = KMeans(n_clusters=k, random_state=random_state, n_init=10)
        labels = km.fit_predict(X_scaled)
        sil = silhouette_score(X_scaled, labels, sample_size=sample_size, random_state=random_state)
        rows.append({"k": k, "inertia": km.inertia_, "silhouette": sil})
        print(f"  k={k}: inertia={km.inertia_:.1f}, silhouette={sil:.4f}")

    return pd.DataFrame(rows)


def fit_final(X_scaled: np.ndarray, k: int, random_state: int = RANDOM_STATE) -> KMeans:
    km = KMeans(n_clusters=k, random_state=random_state, n_init=10)
    km.fit(X_scaled)
    return km


def cluster_profile(clean: pd.DataFrame, labels: np.ndarray) -> pd.DataFrame:
    """Mean + median per feature per cluster, plus cluster size."""
    tmp = clean.copy()
    tmp["cluster_id"] = labels
    agg = tmp.groupby("cluster_id").agg(["mean", "median", "count"])
    agg.columns = [f"{col}_{stat}" for col, stat in agg.columns]
    return agg


def representative_communes(
    clean: pd.DataFrame, X_scaled: np.ndarray, labels: np.ndarray, kmeans: KMeans,
    meta: pd.DataFrame, n: int = N_REPRESENTATIVE,
) -> pd.DataFrame:
    """For each cluster, the n communes closest to the centroid (scaled-space distance)."""
    rows = []
    for cluster_id in sorted(set(labels)):
        idx = np.where(labels == cluster_id)[0]
        centroid = kmeans.cluster_centers_[cluster_id]
        dists = np.linalg.norm(X_scaled[idx] - centroid, axis=1)
        order = idx[np.argsort(dists)][:n]
        for i in order:
            insee_code = clean.index[i]
            row = {"cluster_id": cluster_id, "insee_code": insee_code}
            if insee_code in meta.index:
                row["name"] = meta.loc[insee_code, "name"]
                row["department_code"] = meta.loc[insee_code, "department_code"]
            rows.append(row)
    return pd.DataFrame(rows)


def compute_feature_weights(X_scaled: np.ndarray, labels: np.ndarray, feature_names: list[str]) -> pd.Series:
    """
    ANOVA F-statistic per feature against an already-fit set of cluster labels —
    how much does each feature actually separate the groups K-Means found on
    its own. Normalized so the mean weight is 1 (keeps overall distance scale
    roughly comparable to the unweighted run). "Semi-supervised light": weights
    come from an initial unweighted clustering, then get used to refit.
    """
    f_stats, _ = f_classif(X_scaled, labels)
    f_stats = np.nan_to_num(f_stats, nan=0.0)
    if f_stats.sum() == 0:
        return pd.Series(1.0, index=feature_names)
    weights = f_stats / f_stats.mean()
    return pd.Series(weights, index=feature_names)


def external_price_validation(clean: pd.DataFrame, labels: np.ndarray, meta: pd.DataFrame) -> dict:
    """
    External validation (as opposed to silhouette, which is internal): does
    price/m² — never used as a clustering feature, never seen by K-Means —
    differ significantly across the discovered clusters? One-way ANOVA F-test.

    Restricted to price_data_source != 'estimated' (real DVF observations
    only) so IDW-smoothed prices don't artificially inflate or deflate the
    apparent separation.
    """
    price = meta.loc[meta.index.intersection(clean.index), ["median_price_per_sqm", "price_data_source"]].copy()
    price["cluster_id"] = pd.Series(labels, index=clean.index).loc[price.index]
    real = price[price["price_data_source"] != "estimated"].dropna(subset=["median_price_per_sqm"])

    groups = [g["median_price_per_sqm"].values for _, g in real.groupby("cluster_id") if len(g) >= 5]
    price_by_cluster = real.groupby("cluster_id")["median_price_per_sqm"].agg(["mean", "median", "count"])

    if len(groups) < 2:
        return {
            "n_communes_with_real_price": len(real),
            "anova_f": None, "anova_p": None,
            "note": "fewer than 2 clusters have >=5 communes with real price data",
            "price_by_cluster": price_by_cluster,
        }

    f_stat, p_value = f_oneway(*groups)
    return {
        "n_communes_with_real_price": len(real),
        "anova_f": float(f_stat),
        "anova_p": float(p_value),
        "price_by_cluster": price_by_cluster,
    }


def run(
    dept_filter: str | None = None,
    k: int | None = None,
    weighted: bool = False,
    dry_run: bool = False,
) -> Path:
    print("Loading cross-sectional feature table...")
    df = build_cross_sectional(dept_filter=dept_filter, exclude_estimated=False)
    print(f"  {len(df)} communes loaded")

    clean, dropped = prepare_data(df)
    print(f"  {len(clean)} communes with complete features ({len(dropped)} dropped for missing values)")

    # log1p the heavy-tailed features (population, transaction_volume) before scaling —
    # StandardScaler alone doesn't fix skew, and a handful of huge cities were
    # dominating euclidean distance. cluster_profile/representative_communes still
    # report real (untransformed) values from `clean`, only the fit input changes.
    for_scaling = clean.copy()
    for col in CLUSTERING_LOG_FEATURES:
        for_scaling[col] = np.log1p(for_scaling[col])

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(for_scaling.values)

    print(f"Scanning k in {list(K_RANGE)}...")
    with timer() as t:
        scan = scan_k(X_scaled)
    chosen_k = k or int(scan.loc[scan["silhouette"].idxmax(), "k"])
    print(f"Chosen k = {chosen_k}" + (" (override)" if k else " (max silhouette)"))

    kmeans = fit_final(X_scaled, chosen_k)
    labels = kmeans.labels_
    silhouette_final = float(scan.loc[scan["k"] == chosen_k, "silhouette"].iloc[0])

    feature_weights = None
    if weighted:
        print("Computing feature weights (ANOVA F-stat vs initial unweighted clusters)...")
        feature_weights = compute_feature_weights(X_scaled, labels, CLUSTERING_FEATURES)
        print(feature_weights.sort_values(ascending=False).to_string())

        X_scaled = X_scaled * np.sqrt(feature_weights.values)
        print("Refitting KMeans on weighted feature space...")
        kmeans = fit_final(X_scaled, chosen_k)
        labels = kmeans.labels_
        sample_size = min(SILHOUETTE_SAMPLE_SIZE, len(X_scaled)) if len(X_scaled) > SILHOUETTE_SAMPLE_SIZE else None
        silhouette_final = float(silhouette_score(X_scaled, labels, sample_size=sample_size, random_state=RANDOM_STATE))
        print(f"  Silhouette after weighting: {silhouette_final:.4f}")

    profile = cluster_profile(clean, labels)
    reps = representative_communes(clean, X_scaled, labels, kmeans, df, n=N_REPRESENTATIVE)

    print("External validation: price/m² across clusters (never used as a feature)...")
    validation = external_price_validation(clean, labels, df)
    if validation["anova_f"] is not None:
        print(f"  ANOVA F={validation['anova_f']:.2f}, p={validation['anova_p']:.2e}"
              f" (n={validation['n_communes_with_real_price']} communes with real price)")
    else:
        print(f"  {validation['note']}")

    assignments = pd.DataFrame({"insee_code": clean.index, "cluster_id": labels}).set_index("insee_code")
    assignments = assignments.join(df[["name", "department_code"]], how="left")
    # Raw feature values kept alongside cluster_id (not just the aggregated
    # cluster_profile.csv) so ml/scripts/generate_charts.py can plot a real
    # per-commune scatter offline, without a DB round-trip.
    assignments = assignments.join(clean, how="left")

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        print(profile)
        return Path()

    run_dir = new_run_dir("clustering")
    save_dataframe(run_dir, scan, "silhouette_scan.csv")
    save_dataframe(run_dir, assignments, "cluster_assignments.csv")
    save_dataframe(run_dir, profile, "cluster_profile.csv")
    save_dataframe(run_dir, reps, "representative_communes.csv")
    save_dataframe(run_dir, validation["price_by_cluster"], "external_validation_price_by_cluster.csv")
    save_model(run_dir, kmeans, "kmeans.joblib")
    save_model(run_dir, scaler, "scaler.joblib")
    if feature_weights is not None:
        save_dataframe(run_dir, feature_weights.to_frame("weight"), "feature_weights.csv")

    save_metadata(run_dir, {
        "model": "KMeans",
        "weighted": weighted,
        "feature_weights": feature_weights.to_dict() if feature_weights is not None else None,
        "n_communes_loaded": len(df),
        "n_communes_used": len(clean),
        "n_communes_dropped_missing_values": len(dropped),
        "features": CLUSTERING_FEATURES,
        "log_transformed_features": CLUSTERING_LOG_FEATURES,
        "k_range_tested": list(K_RANGE),
        "k_chosen": chosen_k,
        "silhouette_at_chosen_k": silhouette_final,
        "cluster_sizes": {int(c): int(n) for c, n in pd.Series(labels).value_counts().items()},
        "external_validation_price": {
            "n_communes_with_real_price": validation["n_communes_with_real_price"],
            "anova_f": validation["anova_f"],
            "anova_p": validation["anova_p"],
        },
        "dept_filter": dept_filter,
        **t,
    })

    latest = publish_latest("clustering", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    # Defaults below = the version documented as "official" in PERFORMANCE.md
    # (k=3, weighted): silhouette-max alone picks k=2, which EXPERIMENTS_LOG.md
    # §Modèle 1 #2/#9/#10 shows is a misleading split (population size, not
    # market typology — worse external validation despite higher silhouette).
    # Use --k 0 for the old "let silhouette decide" behavior, --no-weighted to disable reweighting.
    parser.add_argument("--k", type=int, default=3, help="Force k (0 = pick by max silhouette instead)")
    parser.add_argument("--weighted", action=argparse.BooleanOptionalAction, default=True,
                         help="Reweight features by ANOVA F-stat and refit (see ml/EXPERIMENTS_LOG.md); --no-weighted to disable")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    k = None if args.k == 0 else args.k
    run(dept_filter=args.dept, k=k, weighted=args.weighted, dry_run=args.dry_run)
