"""
Visuels supplémentaires demandés pour le mémoire, au-delà de generate_charts.py
(16 figures modèles) et analyze_yield_geography.py (cartes nationales rendement).

Produit :
  - yield_map_idf.png / yield_map_77.png      zoom régional rendement (IDW, plus fin) — DB requise
  - cashflow_map_national.png                  carte nationale cash-flow (1 profil, réf. 52m²) — DB requise
  - price_data_provenance_map.png              dvf réel / estimé IDW / sans donnée, par commune — DB requise
  - data_quality_bug_before_after.png          bug multi-lot DVF, avant/après (chiffres figés, voir EXPERIMENTS_LOG.md)
  - price_opportunity_top_bottom.png           10 communes les + sous/sur-évaluées (Modèle 2)
  - pipeline_diagram.png                       schéma du pipeline Rentium (ingestion -> modèles -> décision)
  - cluster_profile_heatmap.png                profil standardisé des 3 clusters (z-score vs moyenne nationale)
  - cashflow_bars_by_profile.png               cash-flow moyen par profil, en barres (vs boxplot de simulate_cashflow.py)
  - forecast_fan_chart_curated.png             2 communes choisies (marché stable vs extrapolation_risk)
  - forecast_mae_bars_by_horizon.png           MAE baseline vs modèle, barres groupées t+1..t+4
  - shap_beeswarm.png                          SHAP global modèle prix (X_test reconstruit à l'identique) — DB requise

Les 4 derniers + les 3 avant ne nécessitent PAS de DB (CSV/JSON déjà sur
disque) — le script continue même si Postgres est down, seules les cartes
marquées "DB requise" seront sautées (voir run()).

Run après ml.models.yield_model + ml.scripts.simulate_cashflow :
  python -m ml.scripts.generate_memoire_extra_charts
Output: ml/artifacts/memoire_extra/latest/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import TwoSlopeNorm  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

from ml.config import ARTIFACTS_DIR, PRICE_MODEL_FEATURES, RANDOM_STATE, TARGET_COL  # noqa: E402
from ml.data.build_cross_sectional import build_cross_sectional  # noqa: E402
from ml.data.property_tax import load_property_tax  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe  # noqa: E402
from ml.evaluation.plotting import CAT_COLORS, COLOR_BASELINE, COLOR_CRITICAL, COLOR_GOOD, COLOR_MODEL, save, setup_style  # noqa: E402
from ml.finance.cashflow import assurance_mensuelle, cash_flow_mensuel, charges_annuelles_estimees, loyer_effectif, taxe_fonciere_annuelle  # noqa: E402
from ml.finance.profiles import PROFILES  # noqa: E402
from ml.models.price_model import build_design_matrix, load_cluster_assignments  # noqa: E402
from ml.scripts.analyze_yield_geography import (  # noqa: E402
    add_rendement_variants,
    fetch_coords,
    load_data as load_yield_data,
    make_map as make_yield_style_map,
    _idw_grid,
)
from pipeline.services.db import get_connection  # noqa: E402
from pipeline.services.dvf import build_connection as build_dvf_connection  # noqa: E402
from pipeline.services.series import BASE_PRICE_MONO, BASE_PRICE_MULTI, VENTE_NATURE  # noqa: E402
from sklearn.model_selection import train_test_split  # noqa: E402

REFERENCE_SURFACE_M2 = 52.0
IDF_DEPTS = ["75", "77", "78", "91", "92", "93", "94", "95"]


def _latest(model: str) -> Path:
    return ARTIFACTS_DIR / model / "latest"


def _bbox(geo: pd.DataFrame, pad: float = 0.08) -> tuple[tuple[float, float], tuple[float, float]]:
    lon_range = (geo["longitude"].min() - pad, geo["longitude"].max() + pad)
    lat_range = (geo["latitude"].min() - pad, geo["latitude"].max() + pad)
    return lon_range, lat_range


# ── 1-2. Zoom régional rendement (IDF, Seine-et-Marne) ──────────────────────

def make_regional_yield_map(df: pd.DataFrame, dept_codes: list[str], region_name: str,
                             filename: str, run_dir: Path) -> None:
    geo = df[df["department_code"].isin(dept_codes)].dropna(
        subset=["latitude", "longitude", "rendement_brut_acquisition"]
    )
    if len(geo) < 5:
        print(f"  (skip {filename} — seulement {len(geo)} communes)")
        return

    lon_range, lat_range = _bbox(geo)
    median = geo["rendement_brut_acquisition"].median()
    p2, p98 = geo["rendement_brut_acquisition"].quantile([0.02, 0.98])
    norm = TwoSlopeNorm(vmin=p2, vcenter=median, vmax=p98)

    lon_mesh, lat_mesh, grid_values = _idw_grid(
        geo, "rendement_brut_acquisition", lon_range=lon_range, lat_range=lat_range,
        grid_resolution=350, idw_k=8, max_dist_km=10.0,
    )

    fig, ax = plt.subplots(figsize=(8, 7.5))
    cmap = plt.get_cmap("RdBu_r").copy()
    mesh = ax.pcolormesh(lon_mesh, lat_mesh, grid_values, cmap=cmap, norm=norm, shading="gouraud")
    ax.scatter(geo["longitude"], geo["latitude"], s=5, color="black", alpha=0.35, linewidths=0)
    cbar = fig.colorbar(mesh, ax=ax, shrink=0.75)
    cbar.set_label(f"Rendement brut % (médiane {median:.1f}%)", fontsize=9)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"Rendement brut par commune — {region_name} (n={len(geo)})\n"
                 "Bleu = froid (rendement bas)  •  Rouge = chaud (rendement haut)",
                 fontsize=11, linespacing=1.7)
    ax.set_aspect("equal")
    save(fig, run_dir / filename)


# ── 2b. Carte rendement brut national + cercles Top 20 (superposition) ──────

def make_yield_map_with_top20(df: pd.DataFrame, run_dir: Path) -> None:
    """Même fond que yield_map_france.png (surface IDW rendement brut), avec
    les cercles noirs du Top 20 (map_opportunities.png) superposés dessus."""
    top20_path = _latest("recommendations") / "top_20_opportunities.csv"
    if not top20_path.exists():
        print("  (skip carte rendement + Top 20 — top_20_opportunities.csv absent, lance generate_recommendations)")
        return
    top20 = pd.read_csv(top20_path, dtype={"insee_code": str})

    geo = df.dropna(subset=["latitude", "longitude", "rendement_brut_acquisition"])
    geo = geo[geo["longitude"].between(-5.5, 10.0) & geo["latitude"].between(41.0, 51.5)]
    median = geo["rendement_brut_acquisition"].median()
    p2, p98 = geo["rendement_brut_acquisition"].quantile([0.02, 0.98])
    norm = TwoSlopeNorm(vmin=p2, vcenter=median, vmax=p98)

    lon_mesh, lat_mesh, grid_values = _idw_grid(geo, "rendement_brut_acquisition")

    fig, ax = plt.subplots(figsize=(8, 7.8))
    cmap = plt.get_cmap("RdBu_r").copy()
    mesh = ax.pcolormesh(lon_mesh, lat_mesh, grid_values, cmap=cmap, norm=norm, shading="gouraud")

    top20_geo = geo[geo["insee_code"].isin(top20["insee_code"])]
    ax.scatter(top20_geo["longitude"], top20_geo["latitude"], facecolors="none", edgecolors="black",
               s=110, linewidths=1.4, label="Top 20 opportunités", zorder=5)

    cbar = fig.colorbar(mesh, ax=ax, shrink=0.7)
    cbar.set_label(f"Rendement brut % (médiane {median:.1f}%)", fontsize=9)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("Rendement brut par commune, avec le Top 20 opportunités\n"
                 "Bleu = froid (rendement bas)  •  Rouge = chaud (rendement haut)",
                 fontsize=11, linespacing=1.7)
    ax.set_aspect("equal")
    ax.legend(loc="lower left", fontsize=9)
    save(fig, run_dir / "yield_map_with_top20.png")


# ── 3. Carte nationale cash-flow (1 profil) ──────────────────────────────────

def compute_cashflow_national(profile_name: str) -> pd.DataFrame:
    profile = next(p for p in PROFILES if p.name == profile_name)

    full = build_cross_sectional(exclude_estimated=True)
    tax = load_property_tax()
    df = full[["name", "department_code", "median_price_per_sqm", "rent_appt_all", "vacancy_rate"]].copy()
    df = df.join(tax, how="left")
    df = df.dropna(subset=["median_price_per_sqm", "rent_appt_all"])

    prix_achat = df["median_price_per_sqm"] * REFERENCE_SURFACE_M2
    apport = prix_achat * profile.apport_pct
    capital = prix_achat - apport

    n = profile.duree_annees * 12
    i = profile.taux_annuel / 12
    # mensualite_pret() (ml/finance/cashflow.py) a une garde scalaire (if capital<=0 /
    # if i==0) qui casse sur une Series -> formule reprise en vectorise (i>0 et
    # capital>0 garantis ici, prix/taux toujours positifs).
    mensualite = capital * (i * (1 + i) ** n) / ((1 + i) ** n - 1)
    assurance = assurance_mensuelle(capital, profile.assurance_pct_annuel)
    mensualite_totale = mensualite + assurance

    loyer_theorique_mensuel = df["rent_appt_all"] * REFERENCE_SURFACE_M2
    loyer_annuel = loyer_theorique_mensuel * 12
    vacance = df["vacancy_rate"].fillna(0.0)
    loyer_eff = loyer_effectif(loyer_theorique_mensuel, vacance)

    charges_an = charges_annuelles_estimees(loyer_annuel)
    charges_m = charges_an / 12

    taux_foncier = df["taux_foncier_pct"].fillna(df["taux_foncier_pct"].median())
    taxe_an = taxe_fonciere_annuelle(loyer_annuel, taux_foncier)
    taxe_m = taxe_an / 12

    df["cash_flow_mensuel"] = cash_flow_mensuel(loyer_eff, mensualite_totale, charges_m, taxe_m)
    return df


def make_cashflow_map(df: pd.DataFrame, run_dir: Path, profile_name: str) -> None:
    geo = df.dropna(subset=["latitude", "longitude", "cash_flow_mensuel"])
    geo = geo[geo["longitude"].between(-5.5, 10.0) & geo["latitude"].between(41.0, 51.5)]

    p2, p98 = geo["cash_flow_mensuel"].quantile([0.02, 0.98])
    norm = TwoSlopeNorm(vmin=p2, vcenter=0.0, vmax=p98)

    lon_mesh, lat_mesh, grid_values = _idw_grid(geo, "cash_flow_mensuel")

    fig, ax = plt.subplots(figsize=(8, 7.5))
    cmap = plt.get_cmap("RdBu_r").copy()
    mesh = ax.pcolormesh(lon_mesh, lat_mesh, grid_values, cmap=cmap, norm=norm, shading="gouraud")
    cbar = fig.colorbar(mesh, ax=ax, shrink=0.7)
    cbar.set_label(f"Cash-flow mensuel (€, appt {REFERENCE_SURFACE_M2:.0f}m² réf., 0 = équilibre)", fontsize=9)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(
        f"Cash-flow mensuel par commune — profil \"{profile_name}\"\n"
        "Bleu = négatif (déficit)  •  Rouge = positif (excédent)",
        fontsize=11, linespacing=1.7,
    )
    ax.set_aspect("equal")
    save(fig, run_dir / "cashflow_map_national.png")


# ── 4. Bug multi-lot, avant/après ────────────────────────────────────────────

def make_bug_before_after(run_dir: Path) -> None:
    """Chiffres figés (calcul direct sur dvf_cache.duckdb, 2026-08-14, voir
    EXPERIMENTS_LOG.md 'Modèle rendement' #0) — pas recalculés ici, le fix est
    déjà appliqué en DB donc l'état 'avant bug' n'est plus reproductible en
    l'état sans revert le code."""
    labels = ["Mutations multi-lots\nà pièces mixtes (buggé)", "Mutations propres\n(un seul type+pièces)"]
    values = [3333.33, 2515.15]
    ns = [348366, 4526559]
    colors = [COLOR_CRITICAL, COLOR_GOOD]

    fig, ax = plt.subplots(figsize=(7, 6))
    bars = ax.bar(labels, values, color=colors, width=0.55, zorder=2)
    for bar, val, n in zip(bars, values, ns):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 40, f"{val:,.0f} €/m²\n(n={n:,})".replace(",", " "),
                ha="center", fontsize=10, fontweight="bold")
    ax.set_ylabel("Prix/m² médian (€)")
    ax.set_title("Bug de reconstruction des mutations DVF multi-lots\nSurestimation +32% sur 7.15% des lignes filtrées")
    ax.grid(True, axis="y")
    ax.set_ylim(0, max(values) * 1.25)
    save(fig, run_dir / "data_quality_bug_before_after.png")


# ── 5. Carte provenance des prix (dvf / estimated / none) ───────────────────

def make_price_provenance_map(run_dir: Path) -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT latitude, longitude, price_data_source FROM cities WHERE latitude IS NOT NULL")
        rows = cur.fetchall()
    finally:
        conn.close()
    df = pd.DataFrame(rows, columns=["latitude", "longitude", "price_data_source"])
    df = df[df["longitude"].between(-5.5, 10.0) & df["latitude"].between(41.0, 51.5)]
    df["price_data_source"] = df["price_data_source"].fillna("none")

    order = [("dvf", COLOR_GOOD, "DVF réel"), ("estimated", "#e0a03c", "Estimé (IDW)"), ("none", "#999999", "Sans donnée")]

    fig, ax = plt.subplots(figsize=(8, 7.8))
    for source, color, label in order:
        sub = df[df["price_data_source"] == source]
        n = len(sub)
        ax.scatter(sub["longitude"], sub["latitude"], s=6, alpha=0.55, color=color,
                   label=f"{label} (n={n:,})".replace(",", " "))
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title("Origine du prix au m² utilisé par commune\n"
                 "La couverture nationale repose majoritairement sur une reconstruction spatiale",
                 fontsize=11, linespacing=1.7)
    ax.set_aspect("equal")
    ax.legend(markerscale=3, loc="lower left", fontsize=9)
    save(fig, run_dir / "price_data_provenance_map.png")


# ── 6. Top/Bottom score d'opportunité ────────────────────────────────────────

def make_opportunity_top_bottom(run_dir: Path) -> None:
    df = pd.read_csv(_latest("price_model") / "example_communes.csv")
    df = df.sort_values("opportunity_score")
    labels = [f"{row['name']} ({row['department_code']})" for _, row in df.iterrows()]
    values = (df["opportunity_score"] * 100).values
    colors = [COLOR_GOOD if v < 0 else COLOR_CRITICAL for v in values]

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.barh(labels, values, color=colors)
    ax.axvline(0, color=COLOR_BASELINE, linewidth=1.2)
    ax.set_xlabel("Écart entre prix observé et prix attendu par le modèle (%)")
    ax.set_title("Modèle 2 — Communes les plus éloignées de leur prix attendu\n"
                 "Signal relatif de sous/sur-évaluation, pas une preuve de marché sous-évalué")
    ax.grid(True, axis="x")
    save(fig, run_dir / "price_opportunity_top_bottom.png")


# ── 8. Profil des clusters — heatmap standardisée (pas un radar) ────────────

def make_cluster_heatmap(run_dir: Path) -> None:
    """Écart de chaque cluster à la MOYENNE NATIONALE, en écarts-types (z-score),
    pas juste une normalisation entre les 3 clusters — lisible en 'ce cluster
    est +2 écarts-types au-dessus de la moyenne nationale sur cette variable'."""
    df = pd.read_csv(_latest("clustering") / "cluster_assignments.csv", dtype={"insee_code": str})
    features = [c for c in df.columns if c not in ("insee_code", "cluster_id", "name", "department_code")]

    z = (df[features] - df[features].mean()) / df[features].std()
    z["cluster_id"] = df["cluster_id"]
    heat = z.groupby("cluster_id")[features].mean()

    fig, ax = plt.subplots(figsize=(9, 4.5))
    vmax = np.abs(heat.values).max()
    im = ax.imshow(heat.values, cmap="RdBu_r", vmin=-vmax, vmax=vmax, aspect="auto")
    ax.set_xticks(range(len(features)))
    ax.set_xticklabels(features, rotation=35, ha="right", fontsize=9)
    ax.set_yticks(range(len(heat)))
    ax.set_yticklabels([f"Cluster {c}" for c in heat.index])
    for i in range(len(heat)):
        for j in range(len(features)):
            val = heat.values[i, j]
            ax.text(j, i, f"{val:+.1f}", ha="center", va="center",
                     fontsize=8.5, color="white" if abs(val) > vmax * 0.5 else "black")
    cbar = fig.colorbar(im, ax=ax, shrink=0.85)
    cbar.set_label("Écart à la moyenne nationale (écarts-types)", fontsize=9)
    ax.set_title("Modèle 1 — Profil standardisé des clusters (vs moyenne nationale)")
    save(fig, run_dir / "cluster_profile_heatmap.png")


# ── 9. Cash-flow des 5 profils, en barres (moyenne) ──────────────────────────

def make_cashflow_bars(run_dir: Path) -> None:
    df = pd.read_csv(_latest("cashflow") / "cashflow_simulations.csv")
    order = [p.name for p in PROFILES]
    means = df.groupby("profil")["cash_flow_mensuel"].mean().reindex(order)
    short = {
        "Primo-accédant standard": "Primo-\naccédant",
        "Investisseur expérimenté (bon dossier)": "Investisseur\nexpérimenté",
        "Apport élevé / achat comptant partiel": "Apport\nélevé",
        "Effet de levier maximal (faible apport)": "Levier\nmaximal",
        "Profil prudent (SCI, taux fixe long)": "Profil\nprudent (SCI)",
    }
    colors = [COLOR_GOOD if v >= 0 else COLOR_CRITICAL for v in means]

    fig, ax = plt.subplots(figsize=(8, 5.5))
    bars = ax.bar([short[n] for n in order], means.values, color=colors)
    for bar, val in zip(bars, means.values):
        offset = 3 if val >= 0 else -3
        va = "bottom" if val >= 0 else "top"
        ax.text(bar.get_x() + bar.get_width() / 2, val + offset, f"{val:+.0f}€", ha="center", va=va, fontsize=10, fontweight="bold")
    ax.axhline(0, color=COLOR_BASELINE, linewidth=1.2)
    ax.set_ylabel("Cash-flow mensuel moyen (€, Top 20, appt 52m² réf.)")
    ax.set_title("Cash-flow moyen par profil investisseur\n(moyenne sur les 20 mêmes communes, seul le financement change)")
    ax.grid(True, axis="y")
    save(fig, run_dir / "cashflow_bars_by_profile.png")


# ── 10. Fan chart — 2 communes curatées (stable / volatile) ─────────────────

FAN_CHART_COMMUNES = [
    ("59350", "Lille — marché stable"),
    ("29036", "Collorec — extrapolation_risk"),
]


def make_fan_chart_curated(run_dir: Path) -> None:
    df = pd.read_csv(_latest("forecasting") / "forecast_summary_t4.csv", dtype={"insee_code": str}).set_index("insee_code")
    codes = [c for c, _ in FAN_CHART_COMMUNES if c in df.index]
    if not codes:
        print("  (skip fan chart — communes choisies absentes de forecast_summary_t4.csv)")
        return

    fig, axes = plt.subplots(1, len(codes), figsize=(6.5 * len(codes), 4.6))
    if len(codes) == 1:
        axes = [axes]

    for ax, (code, label) in zip(axes, FAN_CHART_COMMUNES):
        if code not in df.index:
            continue
        row = df.loc[code]
        xs = [0, 1, 2, 3, 4]
        point = [row["last_actual_price"]] + [row[f"forecast_t{h}"] for h in range(1, 5)]
        q10 = [row["last_actual_price"]] + [row[f"forecast_t{h}_q10"] for h in range(1, 5)]
        q90 = [row["last_actual_price"]] + [row[f"forecast_t{h}_q90"] for h in range(1, 5)]

        ax.fill_between(xs, q10, q90, color=COLOR_MODEL, alpha=0.18, label="Intervalle Q10-Q90")
        ax.plot(xs, point, color=COLOR_MODEL, marker="o", linewidth=2, label="Prévision")
        ax.scatter([0], [row["last_actual_price"]], color=COLOR_BASELINE, s=60, zorder=5, label="Dernier prix réel")
        ax.set_xticks(xs)
        ax.set_xticklabels(["t0"] + [f"t+{h}" for h in range(1, 5)])
        ax.set_ylabel("Prix (€/m²)")
        ax.set_title(f"{row['name']}\n{label} — croissance 1 an: {row['growth_1y']:.1f}%", fontsize=10.5)
        ax.legend(fontsize=8, loc="upper left")
        ax.grid(True)

    fig.suptitle("Modèle 3 — Prévision avec intervalle, deux profils de marché contrastés", fontweight="bold")
    save(fig, run_dir / "forecast_fan_chart_curated.png")


# ── 11. MAE baseline vs modèle, barres groupées t+1..t+4 ────────────────────

def make_forecast_bars(run_dir: Path) -> None:
    meta = json.loads((_latest("forecasting") / "run_metadata.json").read_text(encoding="utf-8"))
    horizons = sorted(meta["metrics_by_horizon"].keys(), key=int)
    mae_model = [meta["metrics_by_horizon"][h]["model_metrics_test"]["mae"] for h in horizons]
    mae_baseline = [meta["metrics_by_horizon"][h]["baseline_metrics_test"]["mae"] for h in horizons]

    x = np.arange(len(horizons))
    width = 0.36

    fig, ax = plt.subplots(figsize=(8, 5.5))
    b1 = ax.bar(x - width / 2, mae_baseline, width, label="Baseline (persistance)", color=COLOR_BASELINE)
    b2 = ax.bar(x + width / 2, mae_model, width, label="Gradient Boosting", color=COLOR_MODEL)
    for bars in (b1, b2):
        for bar in bars:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 3, f"{bar.get_height():.0f}€",
                     ha="center", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels([f"t+{h}" for h in horizons])
    ax.set_ylabel("MAE (€/m²)")
    ax.set_title("Modèle 3 — MAE baseline vs Gradient Boosting par horizon\n"
                 "La persistance simple gagne à court terme, le modèle reprend l'avantage à t+4")
    ax.legend()
    ax.grid(True, axis="y")
    save(fig, run_dir / "forecast_mae_bars_by_horizon.png")


# ── 13. Évolution du prix au m² par typologie (appartement vs maison) ───────

def compute_price_evolution_by_type() -> pd.DataFrame:
    """Prix médian national par trimestre, par type_local (1=maison, 2=appartement).
    Même filtre que price_sqm_appt/price_sqm_house (pipeline/services/series.py) :
    mutations mono-type + mono-nb_pieces uniquement, prix/m² dans [500, 30000]€
    (voir BASE_PRICE_MULTI, bug multi-lot documenté dans series.py)."""
    con = build_dvf_connection()
    try:
        sql = f"""
            SELECT
                DATE_TRUNC('quarter', date_mutation) AS period,
                type_local,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY valeur_fonciere / surface_bati) AS price_sqm,
                COUNT(DISTINCT mutation_id) AS n_tx
            FROM dvf_mutation_prices
            WHERE {VENTE_NATURE}
              AND type_local IN (1, 2)
              AND {BASE_PRICE_MULTI}
              AND date_mutation IS NOT NULL
            GROUP BY 1, 2
            HAVING COUNT(DISTINCT mutation_id) >= 10
            ORDER BY 1, 2
        """
        df = con.execute(sql).df()
    finally:
        con.close()
    df["type_label"] = df["type_local"].map({1: "Maison", 2: "Appartement"})
    return df


def make_price_evolution_by_type(run_dir: Path) -> None:
    df = compute_price_evolution_by_type()
    fig, ax = plt.subplots(figsize=(8, 5.5))
    colors = {"Appartement": CAT_COLORS[0], "Maison": CAT_COLORS[1]}
    for label, color in colors.items():
        sub = df[df["type_label"] == label].sort_values("period")
        ax.plot(sub["period"], sub["price_sqm"], marker="o", markersize=3,
                 linewidth=1.8, color=color, label=label)
    ax.set_ylabel("Prix médian (€/m²)")
    ax.set_xlabel("Trimestre")
    ax.set_title("Évolution du prix médian au m² par typologie de bien\n"
                 "France entière, mutations DVF 2021-2025")
    ax.legend()
    ax.grid(True, axis="y")
    fig.autofmt_xdate()
    save(fig, run_dir / "price_evolution_by_type.png")
    save_dataframe(run_dir, df, "price_evolution_by_type.csv")


# ── 14. Évolution du prix au m² par nombre de pièces (T1-T4+, appartements) ──

ROOM_ORDER = ["T1", "T2", "T3", "T4+"]


def compute_price_evolution_by_rooms() -> pd.DataFrame:
    """Prix médian national par trimestre, appartements uniquement, par nb de
    pièces. Même filtre que price_sqm_t1..t4 (pipeline/services/series.py) :
    BASE_PRICE_MONO (tous les locaux de la mutation doivent partager le même
    type+nb_pieces, sinon nb_pieces est ambigu au niveau mutation).
    Pas de détail T1-T4 pour les maisons : la typologie DVF/pipeline ne
    définit ces séries que pour type_local=2 (appartement)."""
    con = build_dvf_connection()
    try:
        sql = f"""
            SELECT
                DATE_TRUNC('quarter', date_mutation) AS period,
                CASE
                    WHEN nb_pieces IN (0, 1) THEN 'T1'
                    WHEN nb_pieces = 2 THEN 'T2'
                    WHEN nb_pieces = 3 THEN 'T3'
                    WHEN nb_pieces >= 4 THEN 'T4+'
                END AS room_label,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY valeur_fonciere / surface_bati) AS price_sqm,
                COUNT(DISTINCT mutation_id) AS n_tx
            FROM dvf_mutation_prices
            WHERE {VENTE_NATURE}
              AND type_local = 2
              AND {BASE_PRICE_MONO}
              AND date_mutation IS NOT NULL
            GROUP BY 1, 2
            HAVING COUNT(DISTINCT mutation_id) >= 10
            ORDER BY 1, 2
        """
        df = con.execute(sql).df()
    finally:
        con.close()
    return df


def make_price_evolution_by_rooms(run_dir: Path) -> None:
    df = compute_price_evolution_by_rooms()
    colors = dict(zip(ROOM_ORDER, CAT_COLORS[:4]))

    fig, ax = plt.subplots(figsize=(8.5, 5.8))
    for label in ROOM_ORDER:
        sub = df[df["room_label"] == label].sort_values("period")
        if sub.empty:
            continue
        ax.plot(sub["period"], sub["price_sqm"], marker="o", markersize=3,
                 linewidth=1.8, color=colors[label], label=label)
    ax.set_ylabel("Prix médian (€/m²)")
    ax.set_xlabel("Trimestre")
    ax.set_title("Évolution du prix médian au m² par nombre de pièces (T1-T4+)\n"
                 "Appartements, France entière, DVF 2021-2025")
    ax.legend(title="Typologie")
    ax.grid(True, axis="y")
    fig.autofmt_xdate()
    save(fig, run_dir / "price_evolution_by_rooms.png")
    save_dataframe(run_dir, df, "price_evolution_by_rooms.csv")


# ── 15. Carte nationale prix/m² (même style que yield_map_france.png) ───────

def make_price_map_france(run_dir: Path) -> None:
    """Réutilise make_map() d'analyze_yield_geography.py (IDW national, même
    palette RdBu_r divergente centrée sur la médiane) — juste value_col=
    median_price_per_sqm au lieu de rendement_brut_acquisition. build_cross_
    sectional() a déjà latitude/longitude/median_price_per_sqm (cities snapshot),
    pas besoin de fetch_coords séparé. exclude_estimated=True : seulement le
    prix DVF réel, pas les communes IDW-comblées (cohérent avec le filtre
    utilisé pour la carte de rendement)."""
    df = build_cross_sectional(exclude_estimated=True)
    df = df.dropna(subset=["latitude", "longitude", "median_price_per_sqm"])
    median = df["median_price_per_sqm"].median()

    make_yield_style_map(
        df, "median_price_per_sqm", median, run_dir,
        filename="price_map_france.png",
        title="Prix médian au m² par commune — surface interpolée (France métropolitaine)\n"
              "Bleu = bas  •  Rouge = élevé",
        cbar_label=f"Prix médian €/m² (médiane {median:.0f}€)",
    )
    save_dataframe(run_dir, df[["name", "department_code", "median_price_per_sqm"]], "price_map_france_communes.csv")


# ── 12. SHAP global du modèle de prix (beeswarm) ─────────────────────────────

def make_shap_beeswarm(run_dir: Path) -> None:
    """Reconstruit EXACTEMENT le X_test de ml/models/price_model.py (même
    RANDOM_STATE=42, même build_cross_sectional/dropna/split) pour que les
    valeurs SHAP recalculées ici correspondent aux vraies lignes test du
    modèle sauvegardé — pas un jeu de données différent. Nécessite la DB
    (build_cross_sectional) et `shap` (pip install shap)."""
    import joblib
    import shap

    print("  Rebuilding X_test (identique à price_model.py)...")
    df = build_cross_sectional(exclude_estimated=True)
    clusters = load_cluster_assignments()
    if clusters is not None:
        df = df.join(clusters, how="left")

    required = PRICE_MODEL_FEATURES + [TARGET_COL, "department_code"]
    clean = df.dropna(subset=required)
    X_full, feature_names = build_design_matrix(clean)

    idx_train, idx_temp = train_test_split(clean.index, test_size=0.30, random_state=RANDOM_STATE)
    idx_val, idx_test = train_test_split(idx_temp, test_size=0.50, random_state=RANDOM_STATE)
    X_test = X_full.loc[idx_test]

    model = joblib.load(_latest("price_model") / "model.joblib")

    sample = X_test.sample(min(500, len(X_test)), random_state=RANDOM_STATE)
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(sample)

    fig = plt.figure(figsize=(9, 7))
    shap.summary_plot(shap_values, sample, max_display=12, show=False, plot_size=None)
    fig = plt.gcf()
    fig.suptitle("Modèle 2 — SHAP global (impact de chaque feature sur la prédiction)", fontsize=12, fontweight="bold", y=1.02)
    save(fig, run_dir / "shap_beeswarm.png")


# ── 7. Schéma du pipeline Rentium ────────────────────────────────────────────

def _box(ax, xy, w, h, text, color, fontsize=9.5, fontweight="normal"):
    box = FancyBboxPatch(
        xy, w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
        linewidth=1.2, edgecolor="#3a3835", facecolor=color, alpha=0.9, zorder=2,
    )
    ax.add_patch(box)
    ax.text(xy[0] + w / 2, xy[1] + h / 2, text, ha="center", va="center",
             fontsize=fontsize, fontweight=fontweight, zorder=3, linespacing=1.4)


def _arrow(ax, start, end):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=14,
                                   linewidth=1.3, color="#5a5854", zorder=1))


def make_pipeline_diagram(run_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 12))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 15.5)
    ax.axis("off")

    src_color, ingest_color, db_color, model_color, fin_color = (
        "#c9d6ec", "#f5d9b8", "#d3e8d3", CAT_COLORS[0], "#e8d3ea"
    )

    # Sources (row of 6 small boxes)
    sources = ["DVF", "INSEE", "Filosofi", "Zonage ABC", "Carte des\nloyers", "Fiscalité\nlocale"]
    src_w = 1.5
    for i, s in enumerate(sources):
        _box(ax, (0.15 + i * (src_w + 0.05), 14.2), src_w, 0.9, s, src_color, fontsize=8.5)

    _arrow(ax, (5, 14.2), (5, 13.3))
    _box(ax, (3, 12.5), 4, 0.8, "INGESTION\n(pipeline/)", ingest_color, fontweight="bold")

    _arrow(ax, (5, 12.5), (5, 11.6))
    _box(ax, (2.5, 10.3), 5, 1.3,
         "Nettoyage DVF\nReconstruction mutations · Filtrage\nAgrégation trimestrielle",
         ingest_color)

    _arrow(ax, (5, 10.3), (5, 9.4))
    _box(ax, (3.5, 8.6), 3, 0.8, "PostgreSQL\n(cities, series, timeseries)", db_color, fontweight="bold")

    _arrow(ax, (5, 8.6), (5, 7.7))
    _box(ax, (3, 6.9), 4, 0.8, "Feature engineering\n(ml/data/)", db_color)

    _arrow(ax, (5, 6.9), (5, 6.1))
    # 3 model boxes
    model_labels = ["Clustering\nK-Means", "Prix attendu\nGradient Boosting", "Forecasting\nGradient Boosting"]
    model_w = 2.6
    xs = [1.0, 3.7, 6.4]
    for x, label in zip(xs, model_labels):
        _box(ax, (x, 5.0), model_w, 1.0, label, model_color, fontweight="bold")
        _arrow(ax, (5, 6.1), (x + model_w / 2, 6.0))

    _arrow(ax, (2.3, 5.0), (5, 4.1))
    _arrow(ax, (5.0, 5.0), (5, 4.1))
    _arrow(ax, (7.7, 5.0), (5, 4.1))
    _box(ax, (2.5, 3.3), 5, 0.8, "Scores + intervalles d'incertitude\n(opportunité, croissance, Q10/Q90)", model_color)

    _arrow(ax, (5, 3.3), (5, 2.4))
    _box(ax, (2, 1.6), 6, 0.8, "Moteur financier déterministe\n(ml/finance/ — cash-flow, rendement, §6)", fin_color, fontweight="bold")

    _arrow(ax, (5, 1.6), (5, 0.7))
    _box(ax, (2.5, 0.0), 5, 0.7, "Aide à la décision (Top 20, fiches, cartes)", fin_color)

    ax.set_title("Pipeline Rentium — de la donnée brute à la décision", fontsize=13, fontweight="bold", pad=15)
    save(fig, run_dir / "pipeline_diagram.png")


def run(cashflow_profile: str = "Primo-accédant standard", dry_run: bool = False) -> Path:
    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        return Path()

    run_dir = new_run_dir("memoire_extra")
    setup_style()

    # ── Local-only, aucune connexion DB requise (CSV/JSON déjà sur disque) ──
    local_charts = [
        ("Bug multi-lot avant/après", lambda: make_bug_before_after(run_dir)),
        ("Top/Bottom opportunité", lambda: make_opportunity_top_bottom(run_dir)),
        ("Schéma pipeline", lambda: make_pipeline_diagram(run_dir)),
        ("Heatmap standardisée des clusters", lambda: make_cluster_heatmap(run_dir)),
        ("Cash-flow en barres par profil", lambda: make_cashflow_bars(run_dir)),
        ("Fan chart 2 communes curatées", lambda: make_fan_chart_curated(run_dir)),
        ("MAE baseline vs modèle, barres par horizon", lambda: make_forecast_bars(run_dir)),
    ]
    for label, fn in local_charts:
        try:
            fn()
        except Exception as e:
            print(f"  (skip {label} — {e})")

    # ── Nécessite dvf-raw/*.txt + DuckDB (pas Postgres) ──────────────────────
    try:
        print("Computing évolution prix par typologie (DuckDB, dvf-raw)...")
        make_price_evolution_by_type(run_dir)
    except Exception as e:
        print(f"  (skip évolution prix par typologie — {e})")

    try:
        print("Computing évolution prix par nb de pièces T1-T4+ (DuckDB, dvf-raw)...")
        make_price_evolution_by_rooms(run_dir)
    except Exception as e:
        print(f"  (skip évolution prix T1-T4+ — {e})")

    # ── Nécessite la DB Postgres (get_connection) — sauté proprement si down ──
    try:
        print("Loading yield data (national + IDF/77 zoom)...")
        yield_df = load_yield_data()
        yield_df = add_rendement_variants(yield_df)
        coords = fetch_coords(yield_df["insee_code"].tolist())
        yield_df = yield_df.merge(coords, on="insee_code", how="left")

        print(f"Computing national cash-flow for profile '{cashflow_profile}'...")
        cf_df = compute_cashflow_national(cashflow_profile)
        cf_coords = fetch_coords(list(cf_df.index))
        cf_df = cf_df.merge(cf_coords.set_index("insee_code"), left_index=True, right_index=True, how="left")
        print(f"  {len(cf_df)} communes, cash-flow médian={cf_df['cash_flow_mensuel'].median():.0f}€")

        make_regional_yield_map(yield_df, IDF_DEPTS, "Île-de-France", "yield_map_idf.png", run_dir)
        make_regional_yield_map(yield_df, ["77"], "Seine-et-Marne", "yield_map_77.png", run_dir)
        make_yield_map_with_top20(yield_df, run_dir)
        make_cashflow_map(cf_df, run_dir, cashflow_profile)
        make_price_provenance_map(run_dir)
        make_price_map_france(run_dir)
        save_dataframe(run_dir, cf_df[["name", "department_code", "cash_flow_mensuel"]], "cashflow_national_summary.csv")
    except Exception as e:
        print(f"  (DB indisponible — cartes régionales/nationales sautées: {e})")

    try:
        print("Computing SHAP beeswarm (rebuild X_test, needs DB)...")
        make_shap_beeswarm(run_dir)
    except Exception as e:
        print(f"  (skip SHAP beeswarm — {e})")

    latest = publish_latest("memoire_extra", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="Primo-accédant standard", help="Profil investisseur pour la carte cash-flow")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(cashflow_profile=args.profile, dry_run=args.dry_run)
