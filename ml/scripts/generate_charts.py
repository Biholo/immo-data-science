"""
Generates every mémoire figure from already-saved artifacts (CSVs +
run_metadata.json under ml/artifacts/<model>/latest/) — no retraining, no DB
connection needed. Run any time after ml.scripts.run_all:

  python -m ml.scripts.generate_charts

Output: ml/artifacts/charts/*.png — one folder, all figures, ready to drop
into the mémoire. Each chart function is independently try/except-wrapped in
main() so one missing/stale artifact doesn't block the rest.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import ARTIFACTS_DIR  # noqa: E402
from ml.evaluation.plotting import (  # noqa: E402
    CAT_COLORS,
    COLOR_BASELINE,
    COLOR_CRITICAL,
    COLOR_GOOD,
    COLOR_MODEL,
    save,
    setup_style,
)

import matplotlib.pyplot as plt  # noqa: E402

CHARTS_DIR = ARTIFACTS_DIR / "charts"


def _latest(model: str) -> Path:
    return ARTIFACTS_DIR / model / "latest"


def _meta(model: str) -> dict:
    return json.loads((_latest(model) / "run_metadata.json").read_text(encoding="utf-8"))


def _read_csv(path: Path, **kwargs) -> pd.DataFrame:
    return pd.read_csv(path, **kwargs)


# ── Modèle 1 — Clustering ────────────────────────────────────────────────

def chart_silhouette() -> None:
    df = _read_csv(_latest("clustering") / "silhouette_scan.csv")
    k_chosen = _meta("clustering")["k_chosen"]

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(df["k"], df["silhouette"], color=COLOR_MODEL, linewidth=2, marker="o", markersize=6)
    chosen = df[df["k"] == k_chosen]
    ax.scatter(chosen["k"], chosen["silhouette"], color=COLOR_CRITICAL, s=90, zorder=5, label=f"k retenu = {k_chosen}")
    ax.set_xlabel("Nombre de clusters (k)")
    ax.set_ylabel("Score de silhouette")
    ax.set_title("Modèle 1 — Score de silhouette par k")
    ax.grid(True, axis="y")
    ax.legend()
    save(fig, CHARTS_DIR / "01_clustering_silhouette.png")


def chart_cluster_profile() -> None:
    profile = _read_csv(_latest("clustering") / "cluster_profile.csv")
    features = [c[:-5] for c in profile.columns if c.endswith("_mean")]

    fig, axes = plt.subplots(3, 3, figsize=(13, 10))
    for ax, feat in zip(axes.flat, features):
        vals = profile[f"{feat}_mean"]
        ax.bar(profile["cluster_id"].astype(str), vals, color=CAT_COLORS[: len(profile)])
        ax.set_title(feat, fontsize=10)
        ax.grid(True, axis="y")
    for ax in axes.flat[len(features):]:
        ax.axis("off")
    fig.suptitle("Modèle 1 — Profil moyen des clusters", fontweight="bold", fontsize=13)
    save(fig, CHARTS_DIR / "02_clustering_profile.png")


def chart_price_boxplot() -> None:
    assignments = _read_csv(_latest("clustering") / "cluster_assignments.csv", dtype={"insee_code": str})
    preds = _read_csv(_latest("price_model") / "predictions.csv", dtype={"insee_code": str})
    merged = assignments[["insee_code", "cluster_id"]].merge(
        preds[["insee_code", "price_reel"]], on="insee_code", how="inner"
    )
    if merged.empty:
        print("  (skip 03 — no overlap between clustering and price_model artifacts)")
        return

    clusters = sorted(merged["cluster_id"].unique())
    data = [merged.loc[merged["cluster_id"] == c, "price_reel"] for c in clusters]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    bp = ax.boxplot(data, tick_labels=[f"Cluster {c}" for c in clusters], patch_artist=True, showfliers=False)
    for patch, color in zip(bp["boxes"], CAT_COLORS):
        patch.set_facecolor(color)
        patch.set_alpha(0.55)
    ax.set_ylabel("Prix réel (€/m²)")
    ax.set_title("Modèle 1 — Distribution du prix par cluster\n(validation externe, prix jamais utilisé en feature)")
    ax.grid(True, axis="y")
    save(fig, CHARTS_DIR / "03_clustering_price_boxplot.png")


def chart_cluster_scatter() -> None:
    df = _read_csv(_latest("clustering") / "cluster_assignments.csv")
    if "dist_nearest_major_city_km" not in df.columns:
        print("  (skip 04 — dist_nearest_major_city_km not in cluster_assignments.csv, rerun clustering)")
        return

    fig, ax = plt.subplots(figsize=(7, 5.5))
    for i, c in enumerate(sorted(df["cluster_id"].unique())):
        sub = df[df["cluster_id"] == c]
        ax.scatter(
            np.log1p(sub["population"]), sub["dist_nearest_major_city_km"].clip(upper=250),
            s=10, alpha=0.35, color=CAT_COLORS[i % len(CAT_COLORS)], label=f"Cluster {c}",
        )
    ax.set_xlabel("Population (log)")
    ax.set_ylabel("Distance à la ville majeure la plus proche (km, plafonné à 250)")
    ax.set_title("Modèle 1 — Population vs distance, par cluster")
    ax.grid(True)
    ax.legend(markerscale=3)
    save(fig, CHARTS_DIR / "04_clustering_scatter.png")


# ── Modèle 2 — Prix ───────────────────────────────────────────────────────

def chart_price_scatter() -> None:
    df = _read_csv(_latest("price_model") / "predictions.csv")
    test = df[df["split"] == "test"]
    meta = _meta("price_model")
    m = meta["model_metrics_test"]
    baseline_mae = meta["baseline_metrics_test"]["mae"]
    gain_pct = (1 - m["mae"] / baseline_mae) * 100

    fig, ax = plt.subplots(figsize=(6.5, 6))
    ax.scatter(test["price_reel"], test["price_predit"], s=14, alpha=0.4, color=COLOR_MODEL)
    lims = [
        min(test["price_reel"].min(), test["price_predit"].min()),
        max(test["price_reel"].max(), test["price_predit"].max()),
    ]
    ax.plot(lims, lims, color=COLOR_BASELINE, linewidth=1.5, linestyle="--", label="y = x (prédiction parfaite)")
    ax.text(
        0.03, 0.97,
        f"R² = {m['r2']:.3f}\nMAE modèle = {m['mae']:.0f} €/m²\nMAE baseline = {baseline_mae:.0f} €/m²\n"
        f"Gain = {gain_pct:.1f}%",
        transform=ax.transAxes, va="top", ha="left", fontsize=9.5,
        bbox=dict(facecolor="#fcfcfb", edgecolor="#d8d5cf", pad=6),
    )
    ax.set_xlabel("Prix réel (€/m²)")
    ax.set_ylabel("Prix prédit (€/m²)")
    ax.set_title(f"Modèle 2 — Prix réel vs prédit (test, n={len(test)})")
    ax.legend(loc="lower right")
    ax.grid(True)
    save(fig, CHARTS_DIR / "05_price_scatter.png")


def chart_price_importance() -> None:
    df = _read_csv(_latest("price_model") / "feature_importance.csv")
    df.columns = ["feature", "importance"]
    top = df.sort_values("importance", ascending=True).tail(12)

    fig, ax = plt.subplots(figsize=(7, 6))
    ax.barh(top["feature"], top["importance"], color=COLOR_MODEL)
    ax.set_xlabel("Importance (Gradient Boosting)")
    ax.set_title("Modèle 2 — Importance des features (top 12)")
    ax.grid(True, axis="x")
    save(fig, CHARTS_DIR / "06_price_importance.png")


def chart_opportunity_hist() -> None:
    df = _read_csv(_latest("price_model") / "predictions.csv")
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.hist((df["opportunity_score"] * 100).clip(-50, 50), bins=60, color=COLOR_MODEL, alpha=0.85)
    ax.axvline(0, color=COLOR_BASELINE, linewidth=1.5, linestyle="--")
    ax.set_xlabel("Opportunity score (%, plafonné à ±50)")
    ax.set_ylabel("Nombre de communes")
    ax.set_title("Modèle 2 — Distribution du score d'opportunité\n(négatif = sous-évalué, positif = sur-évalué)")
    ax.grid(True, axis="y")
    save(fig, CHARTS_DIR / "07_price_opportunity_hist.png")


def chart_price_baseline_bar() -> None:
    meta = _meta("price_model")
    m, b = meta["model_metrics_test"], meta["baseline_metrics_test"]
    labels = ["MAE", "RMSE"]
    x = np.arange(len(labels))
    width = 0.32

    fig, ax = plt.subplots(figsize=(6, 4.5))
    ax.bar(x - width / 2, [b["mae"], b["rmse"]], width, label="Baseline (médiane département)", color=COLOR_BASELINE)
    ax.bar(x + width / 2, [m["mae"], m["rmse"]], width, label="Modèle", color=COLOR_MODEL)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("€/m²")
    ax.set_title(f"Modèle 2 — Modèle vs baseline (R² modèle = {m['r2']:.3f})")
    ax.legend()
    ax.grid(True, axis="y")
    save(fig, CHARTS_DIR / "08_price_baseline_bar.png")


# ── Modèle 3 — Forecasting ────────────────────────────────────────────────

def chart_forecast_horizon_metrics() -> None:
    meta = _meta("forecasting")
    horizons = sorted(meta["metrics_by_horizon"].keys(), key=int)
    r2_model = [meta["metrics_by_horizon"][h]["model_metrics_test"]["r2"] for h in horizons]
    r2_baseline = [meta["metrics_by_horizon"][h]["baseline_metrics_test"]["r2"] for h in horizons]
    mae_model = [meta["metrics_by_horizon"][h]["model_metrics_test"]["mae"] for h in horizons]
    mae_baseline = [meta["metrics_by_horizon"][h]["baseline_metrics_test"]["mae"] for h in horizons]
    x = [f"t+{h}" for h in horizons]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    axes[0].plot(x, r2_baseline, color=COLOR_BASELINE, marker="o", linewidth=2, label="Baseline")
    axes[0].plot(x, r2_model, color=COLOR_MODEL, marker="o", linewidth=2, label="Modèle")
    axes[0].set_ylabel("R²")
    axes[0].set_title("R² par horizon")
    axes[0].legend()
    axes[0].grid(True)

    axes[1].plot(x, mae_baseline, color=COLOR_BASELINE, marker="o", linewidth=2, label="Baseline")
    axes[1].plot(x, mae_model, color=COLOR_MODEL, marker="o", linewidth=2, label="Modèle")
    axes[1].set_ylabel("MAE (€/m²)")
    axes[1].set_title("MAE par horizon")
    axes[1].legend()
    axes[1].grid(True)

    fig.suptitle("Modèle 3 — Performance par horizon de prévision", fontweight="bold")
    save(fig, CHARTS_DIR / "09_forecast_horizon_metrics.png")


def chart_forecast_examples() -> None:
    df = _read_csv(_latest("forecasting") / "forecast_summary_t4.csv").dropna(subset=["growth_1y"])
    n = min(4, len(df))
    if n == 0:
        print("  (skip 10 — no communes with a full t+4 forecast)")
        return
    sample = df.sample(n, random_state=7)

    fig, axes = plt.subplots(1, n, figsize=(4 * n, 4.2))
    if n == 1:
        axes = [axes]

    for ax, (_, row) in zip(axes, sample.iterrows()):
        xs = [0, 1, 2, 3, 4]
        point = [row["last_actual_price"]] + [row[f"forecast_t{h}"] for h in range(1, 5)]
        q10 = [row["last_actual_price"]] + [row[f"forecast_t{h}_q10"] for h in range(1, 5)]
        q90 = [row["last_actual_price"]] + [row[f"forecast_t{h}_q90"] for h in range(1, 5)]

        ax.fill_between(xs, q10, q90, color=COLOR_MODEL, alpha=0.18, label="Intervalle Q10-Q90")
        ax.plot(xs, point, color=COLOR_MODEL, marker="o", linewidth=2, label="Prévision")
        ax.set_xticks(xs)
        ax.set_xticklabels(["t0"] + [f"t+{h}" for h in range(1, 5)])
        ax.set_title(f"{row['name']}\ncroissance 1 an: {row['growth_1y']:.1f}%", fontsize=10)
        ax.grid(True)

    axes[0].set_ylabel("Prix (€/m²)")
    axes[0].legend(fontsize=8, loc="upper left")
    fig.suptitle("Modèle 3 — Exemples de prévision avec intervalle croissant", fontweight="bold")
    save(fig, CHARTS_DIR / "10_forecast_examples.png")


def chart_growth_risk_hist() -> None:
    df = _read_csv(_latest("forecasting") / "forecast_summary_t4.csv").dropna(subset=["growth_1y"])
    normal = df.loc[~df["extrapolation_risk"], "growth_1y"].clip(-60, 60)
    risky = df.loc[df["extrapolation_risk"], "growth_1y"].clip(-60, 60)
    bins = np.linspace(-60, 60, 60)

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.hist(normal, bins=bins, color=COLOR_MODEL, alpha=0.85, label=f"Fiable (n={len(normal)})")
    ax.hist(risky, bins=bins, color=COLOR_CRITICAL, alpha=0.85, label=f"extrapolation_risk (n={len(risky)})")
    ax.set_xlabel("Croissance prévue à 1 an (%, plafonné à ±60)")
    ax.set_ylabel("Nombre de communes")
    ax.set_title("Modèle 3 — Distribution de la croissance prévue")
    ax.legend()
    ax.grid(True, axis="y")
    save(fig, CHARTS_DIR / "11_forecast_growth_risk.png")


# ── Modèle 4 — Quantile ───────────────────────────────────────────────────

def chart_coverage_bar() -> None:
    meta = _meta("quantile")
    raw = meta["empirical_coverage_q10_q90_raw"] * 100
    conf = meta["empirical_coverage_q10_q90_conformal"] * 100

    fig, ax = plt.subplots(figsize=(5.5, 4.5))
    bars = ax.bar(["Brute", "Conformal"], [raw, conf], color=[COLOR_BASELINE, COLOR_MODEL], zorder=2)
    ax.axhline(80, color=COLOR_GOOD, linewidth=1.5, linestyle="--", label="Cible 80%", zorder=1)
    for bar, val in zip(bars, [raw, conf]):
        # White-boxed label so it stays readable where a bar crosses the target line
        # (76.8% sits right under 80 and would otherwise print on top of the dashes).
        ax.text(bar.get_x() + bar.get_width() / 2, val + 3, f"{val:.1f}%", ha="center", fontsize=10,
                zorder=3, bbox=dict(facecolor="#fcfcfb", edgecolor="none", pad=1.5))
    ax.set_ylabel("Coverage [Q10, Q90] (%)")
    ax.set_ylim(0, 100)
    ax.set_title("Modèle 4 — Coverage brute vs conformal")
    ax.legend()
    ax.grid(True, axis="y")
    save(fig, CHARTS_DIR / "12_quantile_coverage.png")


def chart_quantile_bands() -> None:
    # Sorted by the model's OWN median prediction (Q50), not price_reel: Q10/Q90
    # are a function of the same feature vector as Q50, so this order makes the
    # band read as a smooth "low to high" fan. Sorting by price_reel instead
    # made the band a jagged zigzag — Q10/Q90 don't move monotonically with
    # price_reel (each commune's bounds depend on its own features), so ranking
    # by ground truth scatters unrelated bounds next to each other on the x-axis.
    df = _read_csv(_latest("quantile") / "predictions.csv").sort_values("prix_central_q50").reset_index(drop=True)
    x = np.arange(len(df))

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.fill_between(x, df["borne_basse_q10_conformal"], df["borne_haute_q90_conformal"],
                     color=COLOR_MODEL, alpha=0.18, label="Intervalle Q10-Q90 (conformal)")
    ax.plot(x, df["prix_central_q50"], color=COLOR_MODEL, linewidth=1.5, label="Prédiction médiane (Q50)")
    ax.scatter(x, df["price_reel"], color=COLOR_CRITICAL, s=4, alpha=0.5, label="Prix réel")
    ax.set_xlabel("Communes test (triées par prédiction médiane)")
    ax.set_ylabel("Prix (€/m²)")
    ax.set_title("Modèle 4 — Fourchette de prédiction sur le test set")
    ax.legend()
    ax.grid(True)
    save(fig, CHARTS_DIR / "13_quantile_bands.png")


# ── Modèle hédonique ──────────────────────────────────────────────────────

def chart_hedonic_scatter() -> None:
    path = _latest("price_hedonic") / "test_predictions_sample.csv"
    if not path.exists():
        print("  (skip 14 — test_predictions_sample.csv absent, rerun ml.models.price_hedonic)")
        return
    df = _read_csv(path)

    fig, ax = plt.subplots(figsize=(6.5, 6))
    ax.scatter(df["prix_m2_reel"], df["prix_m2_predit_transaction"], s=6, alpha=0.25, color=COLOR_MODEL)
    lo = df[["prix_m2_reel", "prix_m2_predit_transaction"]].min().min()
    hi = df[["prix_m2_reel", "prix_m2_predit_transaction"]].quantile(0.99).max()
    ax.plot([lo, hi], [lo, hi], color=COLOR_BASELINE, linewidth=1.5, linestyle="--", label="y = x")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("Prix réel (€/m², transaction)")
    ax.set_ylabel("Prix prédit (€/m², transaction)")
    ax.set_title(f"Modèle hédonique — Prix réel vs prédit (n={len(df)})")
    ax.legend()
    ax.grid(True)
    save(fig, CHARTS_DIR / "14_hedonic_scatter.png")


def chart_hedonic_importance() -> None:
    df = _read_csv(_latest("price_hedonic") / "feature_importance.csv")
    df.columns = ["feature", "importance"]
    top = df.sort_values("importance", ascending=True).tail(12)

    fig, ax = plt.subplots(figsize=(7, 6))
    ax.barh(top["feature"], top["importance"], color=COLOR_MODEL)
    ax.set_xlabel("Importance (LightGBM)")
    ax.set_title("Modèle hédonique — Importance des features (top 12)")
    ax.grid(True, axis="x")
    save(fig, CHARTS_DIR / "15_hedonic_importance.png")


# ── Synthèse ───────────────────────────────────────────────────────────────

def chart_synthesis() -> None:
    pm = _meta("price_model")
    m, b = pm["model_metrics_test"], pm["baseline_metrics_test"]
    gain_price = (b["mae"] - m["mae"]) / b["mae"] * 100

    fm = _meta("forecasting")
    h1 = fm["metrics_by_horizon"]["1"]
    gain_forecast = (h1["baseline_metrics_test"]["mae"] - h1["model_metrics_test"]["mae"]) / h1["baseline_metrics_test"]["mae"] * 100

    hm = _meta("price_hedonic")
    mh, bh = hm["model_metrics_test"], hm["baseline_metrics_test"]
    gain_hedonic = (bh["mae"] - mh["mae"]) / bh["mae"] * 100

    labels = ["Modèle 2\n(Prix)", "Modèle 3\n(t+1)", "Hédonique"]
    values = [gain_price, gain_forecast, gain_hedonic]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    bars = ax.bar(labels, values, color=CAT_COLORS[: len(values)])
    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.5, f"{val:.1f}%", ha="center", fontsize=10)
    ax.set_ylabel("Gain MAE vs baseline (%)")
    ax.set_title("Synthèse — Gain de chaque modèle vs sa baseline")
    ax.grid(True, axis="y")
    save(fig, CHARTS_DIR / "16_synthesis.png")


CHARTS = [
    ("Modèle 1 — silhouette", chart_silhouette),
    ("Modèle 1 — profil clusters", chart_cluster_profile),
    ("Modèle 1 — prix par cluster", chart_price_boxplot),
    ("Modèle 1 — scatter population/distance", chart_cluster_scatter),
    ("Modèle 2 — scatter réel/prédit", chart_price_scatter),
    ("Modèle 2 — feature importance", chart_price_importance),
    ("Modèle 2 — histogramme opportunity_score", chart_opportunity_hist),
    ("Modèle 2 — modèle vs baseline", chart_price_baseline_bar),
    ("Modèle 3 — métriques par horizon", chart_forecast_horizon_metrics),
    ("Modèle 3 — exemples de prévision", chart_forecast_examples),
    ("Modèle 3 — histogramme croissance/risque", chart_growth_risk_hist),
    ("Modèle 4 — coverage", chart_coverage_bar),
    ("Modèle 4 — bandes de prédiction", chart_quantile_bands),
    ("Hédonique — scatter réel/prédit", chart_hedonic_scatter),
    ("Hédonique — feature importance", chart_hedonic_importance),
    ("Synthèse", chart_synthesis),
]


def main() -> None:
    setup_style()
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    ok, failed = 0, []
    for label, fn in CHARTS:
        try:
            fn()
            ok += 1
        except FileNotFoundError as e:
            print(f"  (skip {label} — artifact missing: {e.filename})")
            failed.append(label)
        except Exception:
            print(f"  FAILED: {label}")
            traceback.print_exc()
            failed.append(label)

    print(f"\n{ok}/{len(CHARTS)} graphiques générés dans {CHARTS_DIR}")
    if failed:
        print(f"Échecs/sautés: {', '.join(failed)}")


if __name__ == "__main__":
    main()
