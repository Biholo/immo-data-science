"""
Combines all 4 mémoire models into concrete, presentable deliverables — not
metrics, actual outputs a reader can look at:

1. all_communes.csv / top_20_opportunities.csv — one row per commune joining
   cluster (Modèle 1), opportunity_score (Modèle 2), growth_1y +
   extrapolation_risk (Modèle 3), conformal interval (Modèle 4), and
   yield_opportunity_score (Modèle rendement, extension) into a single
   composite ranking. Heuristic composite (equal-weighted, transparent — same
   spirit as CONTEXTE_ML_MEMOIRE.md §7's Rentium score, not hidden):
     composite_score = -opportunity_score(%) + growth_1y(%) + yield_opportunity_score(%)
   i.e. undervalued AND growing AND yielding more than fundamentals predict
   all push a commune up — three independent models agreeing, not one proxy.
   Top 20 requires opportunity_score < 0, growth_1y > 0, yield_opportunity_score > 0,
   and extrapolation_risk == False (or unknown) — "cheap, growing, AND actually
   pays off", not just "cheap and growing" (see user feedback in EXPERIMENTS_LOG.md
   — price+growth alone doesn't answer "does it pay off").

2. fiche_<insee_code>.png — one-page profile per top commune: key numbers +
   forecast fan chart, the "what would an investor actually be shown" artifact.

3. map_opportunities.png / map_clusters.png — lat/lon scatter (mainland
   France only), no basemap library needed. The only piece here that touches
   the DB (read-only, just latitude/longitude — nothing else in ml/ needs it
   to run, see ml/README.md "Design").

Run after ml.scripts.run_all:
  python -m ml.scripts.generate_recommendations
Output: ml/artifacts/recommendations/latest/
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import TwoSlopeNorm  # noqa: E402

from ml.config import ARTIFACTS_DIR  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata  # noqa: E402
from ml.evaluation.plotting import CAT_COLORS, COLOR_BASELINE, COLOR_MODEL, save, setup_style  # noqa: E402
from pipeline.services.db import get_connection  # noqa: E402

TOP_N = 20
N_FICHES = 5
# Mainland France + Corsica bounding box — DOM-TOM excluded so the scatter
# reads as a recognizable map instead of a few stray points an ocean away.
LON_RANGE = (-5.5, 10.0)
LAT_RANGE = (41.0, 51.5)


def _latest(model: str) -> Path:
    return ARTIFACTS_DIR / model / "latest"


def load_combined() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (combined, forecast_full) — forecast_full kept separately (all 4 horizons'
    q10/q90) since fiches need more columns than the ranking table wants to carry."""
    clusters = pd.read_csv(_latest("clustering") / "cluster_assignments.csv", dtype={"insee_code": str})
    price = pd.read_csv(_latest("price_model") / "predictions.csv", dtype={"insee_code": str})
    forecast = pd.read_csv(_latest("forecasting") / "forecast_summary_t4.csv", dtype={"insee_code": str})
    quantile = pd.read_csv(_latest("quantile") / "predictions.csv", dtype={"insee_code": str})
    yield_df = pd.read_csv(_latest("yield_model") / "predictions.csv", dtype={"insee_code": str}) \
        if (_latest("yield_model") / "predictions.csv").exists() else None

    df = price.drop(columns=["cluster_id"]).merge(
        clusters[["insee_code", "cluster_id"]], on="insee_code", how="left"
    )
    df = df.merge(
        forecast[["insee_code", "growth_1y", "extrapolation_risk", "last_actual_price"]],
        on="insee_code", how="left",
    )
    df = df.merge(
        quantile[["insee_code", "borne_basse_q10_conformal", "borne_haute_q90_conformal"]],
        on="insee_code", how="left",
    )
    if yield_df is not None:
        df = df.merge(
            yield_df[["insee_code", "rendement_reel", "rendement_predit", "yield_opportunity_score"]],
            on="insee_code", how="left",
        )
    else:
        print("  (yield_model artifacts absent — run ml.models.yield_model first for the rendement signal)")
        df["rendement_reel"] = df["rendement_predit"] = df["yield_opportunity_score"] = np.nan
    return df, forecast


def compute_score(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["opportunity_pct"] = df["opportunity_score"] * 100
    df["yield_opportunity_pct"] = df["yield_opportunity_score"] * 100
    df["composite_score"] = (
        -df["opportunity_pct"] + df["growth_1y"].fillna(0) + df["yield_opportunity_pct"].fillna(0)
    )
    return df


def build_top20(df: pd.DataFrame) -> pd.DataFrame:
    """
    Three independent models have to agree: undervalued (Modèle 2), growing
    (Modèle 3), AND yielding more than its fundamentals predict (Modèle
    rendement) — "cheap and rising" alone doesn't say whether it pays off.
    """
    candidates = df[
        (df["opportunity_score"] < 0)
        & (df["growth_1y"] > 0)
        & (df["yield_opportunity_score"] > 0)
        & (~df["extrapolation_risk"].fillna(True).astype(bool))
    ].copy()
    return candidates.sort_values("composite_score", ascending=False).head(TOP_N)


# ── Fiches communes ──────────────────────────────────────────────────────

def make_fiche(row: pd.Series, forecast_full: pd.DataFrame, run_dir: Path) -> None:
    frow = forecast_full[forecast_full["insee_code"] == row["insee_code"]]
    has_forecast = not frow.empty
    frow = frow.iloc[0] if has_forecast else None

    fig = plt.figure(figsize=(9.5, 5.6))
    gs = fig.add_gridspec(1, 2, width_ratios=[1, 1.4], wspace=0.35)
    ax_text = fig.add_subplot(gs[0])
    ax_text.axis("off")

    cluster_txt = f"Cluster {int(row['cluster_id'])}" if pd.notna(row.get("cluster_id")) else "Cluster N/A"
    risk_txt = "à vérifier (extrapolation_risk)" if bool(row.get("extrapolation_risk")) else "fiable"
    lines = [
        (f"{row['name']}", 15, "bold"),
        (f"Département {row['department_code']}  ·  {cluster_txt}", 10, "normal"),
        ("", 6, "normal"),
        (f"Prix réel:            {row['price_reel']:,.0f} €/m²".replace(",", " "), 11, "normal"),
        (f"Prix attendu (Modèle 2): {row['price_predit']:,.0f} €/m²".replace(",", " "), 11, "normal"),
        (f"Opportunity score:    {row['opportunity_pct']:+.1f}%", 11, "bold"),
        ("", 6, "normal"),
        (f"Rendement brut réel:  {row['rendement_reel']:.1f}%", 11, "normal"),
        (f"Rendement attendu:    {row['rendement_predit']:.1f}%", 11, "normal"),
        (f"Yield opportunity score: {row['yield_opportunity_pct']:+.1f}%", 11, "bold"),
        ("", 6, "normal"),
        (f"Croissance prévue (1 an): {row['growth_1y']:+.1f}%", 11, "bold"),
        (f"Fourchette (conformal): {row['borne_basse_q10_conformal']:,.0f} – {row['borne_haute_q90_conformal']:,.0f} €/m²".replace(",", " "), 10, "normal"),
        (f"Fiabilité prévision 1 an: {risk_txt}", 10, "normal"),
    ]
    y = 1.0
    for text, size, weight in lines:
        ax_text.text(0, y, text, va="top", fontsize=size, fontweight=weight, transform=ax_text.transAxes)
        y -= 0.075 if text else 0.035

    ax_chart = fig.add_subplot(gs[1])
    if has_forecast:
        xs = [0, 1, 2, 3, 4]
        point = [frow["last_actual_price"]] + [frow[f"forecast_t{h}"] for h in range(1, 5)]
        q10 = [frow["last_actual_price"]] + [frow[f"forecast_t{h}_q10"] for h in range(1, 5)]
        q90 = [frow["last_actual_price"]] + [frow[f"forecast_t{h}_q90"] for h in range(1, 5)]
        ax_chart.fill_between(xs, q10, q90, color=COLOR_MODEL, alpha=0.18, label="Intervalle Q10-Q90")
        ax_chart.plot(xs, point, color=COLOR_MODEL, marker="o", linewidth=2, label="Prévision")
        ax_chart.set_xticks(xs)
        ax_chart.set_xticklabels(["t0"] + [f"t+{h}" for h in range(1, 5)])
        ax_chart.legend(fontsize=8)
    else:
        ax_chart.text(0.5, 0.5, "Pas de prévision t+4\ndisponible pour cette commune",
                       ha="center", va="center", fontsize=10, color=COLOR_BASELINE)
        ax_chart.set_xticks([])
    ax_chart.set_ylabel("Prix (€/m²)")
    ax_chart.grid(True)

    fig.suptitle("Fiche commune", fontweight="bold", fontsize=12)
    save(fig, run_dir / f"fiche_{row['insee_code']}_{row['name'].replace(' ', '_')}.png")


# ── Cartes ────────────────────────────────────────────────────────────────

def fetch_coords(insee_codes: list[str]) -> pd.DataFrame:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT insee_code, latitude, longitude FROM cities WHERE insee_code = ANY(%s)",
            (insee_codes,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["insee_code", "latitude", "longitude"])


def _mainland(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        df["longitude"].between(*LON_RANGE) & df["latitude"].between(*LAT_RANGE)
    ]


def make_map_opportunities(df: pd.DataFrame, top20: pd.DataFrame, run_dir: Path) -> None:
    geo = _mainland(df.dropna(subset=["latitude", "longitude", "opportunity_pct"]))
    if geo.empty:
        print("  (skip map_opportunities — no geocoded rows)")
        return

    cap = 30
    vals = geo["opportunity_pct"].clip(-cap, cap)
    norm = TwoSlopeNorm(vmin=-cap, vcenter=0, vmax=cap)

    fig, ax = plt.subplots(figsize=(7, 7.5))
    sc = ax.scatter(geo["longitude"], geo["latitude"], c=vals, cmap="RdBu_r", norm=norm, s=8, alpha=0.75)
    top20_geo = geo[geo["insee_code"].isin(top20["insee_code"])]
    ax.scatter(top20_geo["longitude"], top20_geo["latitude"], facecolors="none", edgecolors="black",
               s=90, linewidths=1.2, label=f"Top {TOP_N} opportunités")
    cbar = fig.colorbar(sc, ax=ax, shrink=0.7)
    cbar.set_label("Opportunity score (%) — bleu = sous-évalué, rouge = sur-évalué")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("Modèle 2 — Score d'opportunité (France métropolitaine)")
    ax.set_aspect("equal")
    ax.legend(loc="lower left")
    save(fig, run_dir / "map_opportunities.png")


# Labels métier — lecture des chiffres réels (cluster_profile.csv), pas une
# décision automatique, voir MEMOIRE_PARTIE3_INPUTS.md §3 "Labels suggérés".
CLUSTER_LABELS = {
    0: "Rural, forte part résidences secondaires",
    1: "Rural/périurbain ordinaire (majoritaire)",
    2: "Pôles urbains et bourgs-centres",
}


def make_map_clusters(run_dir: Path) -> None:
    """Population COMPLÈTE du clustering (32992 communes), pas le sous-ensemble
    restreint aux communes avec prix DVF réel utilisé par map_opportunities —
    sinon la légende affiche des effectifs qui ne correspondent pas aux
    vraies tailles de cluster (voir cluster_profile.csv)."""
    clusters = pd.read_csv(_latest("clustering") / "cluster_assignments.csv", dtype={"insee_code": str})
    coords = fetch_coords(clusters["insee_code"].tolist())
    df = clusters.merge(coords, on="insee_code", how="left")

    geo = _mainland(df.dropna(subset=["latitude", "longitude", "cluster_id"]))
    if geo.empty:
        print("  (skip map_clusters — no geocoded rows)")
        return

    fig, ax = plt.subplots(figsize=(8, 7.8))
    # 32992 points -> forte superposition : petit et transparent pour que le
    # cluster 2 (pôles urbains, le plus repérable) ressorte au-dessus du
    # fond rural (dessiné en dernier, ordre croissant des cluster_id).
    for i, c in enumerate(sorted(geo["cluster_id"].unique())):
        sub = geo[geo["cluster_id"] == c]
        label = CLUSTER_LABELS.get(int(c), f"Cluster {int(c)}")
        ax.scatter(sub["longitude"], sub["latitude"], s=3, alpha=0.3,
                   color=CAT_COLORS[i % len(CAT_COLORS)],
                   label=f"Cluster {int(c)} — {label} (n={len(sub)})")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("Modèle 1 — Typologie des communes (France métropolitaine)")
    ax.set_aspect("equal")
    ax.legend(markerscale=3, loc="lower left", fontsize=8.5, framealpha=0.95)
    save(fig, run_dir / "map_clusters.png")


# ── Main ──────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> Path:
    print("Loading and combining all 4 models...")
    combined, forecast_full = load_combined()
    combined = compute_score(combined)
    print(f"  {len(combined)} communes combined")

    top20 = build_top20(combined)
    print(f"  {len(top20)} communes in Top {TOP_N} (sous-évalué + croissance + rendement + fiable)")
    if len(top20):
        print(top20[["name", "department_code", "opportunity_pct", "growth_1y", "yield_opportunity_pct"]].to_string(index=False))

    print("Fetching coordinates for maps...")
    coords = fetch_coords(combined["insee_code"].tolist())
    combined_geo = combined.merge(coords, on="insee_code", how="left")

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        return Path()

    run_dir = new_run_dir("recommendations")
    setup_style()

    save_dataframe(run_dir, combined, "all_communes.csv")
    save_dataframe(run_dir, top20, "top_20_opportunities.csv")

    print(f"Generating {min(N_FICHES, len(top20))} fiches...")
    for _, row in top20.head(N_FICHES).iterrows():
        make_fiche(row, forecast_full, run_dir)

    print("Generating maps...")
    make_map_opportunities(combined_geo, top20, run_dir)
    make_map_clusters(run_dir)

    save_metadata(run_dir, {
        "n_communes_combined": len(combined),
        "n_top20": len(top20),
        "composite_score_formula": "-opportunity_score(%) + growth_1y(%) + yield_opportunity_score(%)",
        "top20_filters": "opportunity_score < 0 AND growth_1y > 0 AND yield_opportunity_score > 0 AND NOT extrapolation_risk",
        "n_fiches": min(N_FICHES, len(top20)),
    })

    latest = publish_latest("recommendations", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(dry_run=args.dry_run)
