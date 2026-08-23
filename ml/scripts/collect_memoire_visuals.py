"""
Rassemble les visuels retenus pour la rédaction du mémoire dans un seul
dossier, à part de ml/artifacts/ (qui reste le répertoire de travail brut,
un sous-dossier par script). Renomme chaque fichier en français (numéroté
3.1.x pour le corps, annexe_Ax pour l'annexe, extra_ pour le reste) et écrit
SOURCES.md avec la ligne de citation de chaque figure.

Simple copie (pas de recalcul) depuis les `latest/` déjà générés par
generate_charts.py, generate_recommendations.py et
generate_memoire_extra_charts.py, lancer ces 3 scripts avant celui-ci.

Run :
  python -m ml.scripts.collect_memoire_visuals
Output : memoire/redaction/latest/ (+ memoire/redaction/<timestamp>/, historique)
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ml.config import ARTIFACTS_DIR  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent.parent
REDACTION_DIR = REPO_ROOT / "memoire" / "redaction"

DVF = "DVF, DGFiP (2021-2025)"
INSEE = "INSEE (2017-2022)"
FILOSOFI = "INSEE Filosofi (2021)"
ZONAGE = "zonage ABC, data.gouv.fr"
LOYERS = "Carte des loyers ANIL/DHUP (2025)"
FISCA = "Fiscalité locale, DGFiP (2021-2025)"
INTERNE = "calcul interne"

# (dossier source, nom original) -> (nom français final, sources utilisées)
RENAME_MAP: dict[tuple[str, str], tuple[str, list[str]]] = {
    # ── Corps du mémoire, §3.1 ────────────────────────────────────────────
    ("memoire_extra", "pipeline_diagram.png"): (
        "3.1.1_schema_pipeline_rentium.png", [INTERNE]),
    ("memoire_extra", "data_quality_bug_before_after.png"): (
        "3.1.2_bug_multilot_avant_apres.png", [DVF]),
    ("recommendations", "map_clusters.png"): (
        "3.1.3_carte_nationale_clusters.png", [DVF, INSEE, ZONAGE]),
    ("charts", "05_price_scatter.png"): (
        "3.1.4_prix_reel_vs_predit.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("memoire_extra", "forecast_mae_bars_by_horizon.png"): (
        "3.1.5_mae_baseline_vs_modele_horizons.png", [DVF]),

    # ── Annexe (dans l'ordre donné) ────────────────────────────────────────
    ("memoire_extra", "price_data_provenance_map.png"): (
        "annexe_A1_carte_provenance_prix.png", [DVF, INTERNE]),
    ("charts", "01_clustering_silhouette.png"): (
        "annexe_A2_courbe_silhouette_k.png", [DVF, INSEE, ZONAGE]),
    ("memoire_extra", "cluster_profile_heatmap.png"): (
        "annexe_A3_profil_clusters_heatmap.png", [DVF, INSEE, ZONAGE]),
    ("memoire_extra", "price_evolution_by_type.png"): (
        "annexe_A4_evolution_prix_typologie.png", [DVF]),
    ("memoire_extra", "shap_beeswarm.png"): (
        "annexe_A5_shap_global_prix.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("recommendations", "map_opportunities.png"): (
        "annexe_A6_carte_score_opportunite.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("memoire_extra", "price_opportunity_top_bottom.png"): (
        "annexe_A7_top_bottom_opportunite.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("memoire_extra", "forecast_fan_chart_curated.png"): (
        "annexe_A8_fan_chart_forecast.png", [DVF]),
    ("charts", "12_quantile_coverage.png"): (
        "annexe_A9_couverture_quantile.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("memoire_extra", "cashflow_bars_by_profile.png"): (
        "annexe_A10_cashflow_barres_profils.png", [DVF, LOYERS, FISCA, INTERNE]),
    ("yield_geography", "yield_map_brut_vs_net.png"): (
        "annexe_A11_carte_rendement_brut_net.png", [DVF, LOYERS, FISCA, INTERNE]),
    ("memoire_extra", "yield_map_with_top20.png"): (
        "annexe_A12_carte_rendement_top20.png", [DVF, LOYERS, FISCA, INTERNE]),
    ("memoire_extra", "price_evolution_by_rooms.png"): (
        "annexe_A13_evolution_prix_T1_T4.png", [DVF]),
    ("memoire_extra", "price_map_france.png"): (
        "annexe_A14_carte_prix_m2.png", [DVF, INTERNE]),

    # ── Le reste (déjà produit, gardé, renommé en clair) ───────────────────
    ("charts", "02_clustering_profile.png"): (
        "extra_profil_clusters_barres.png", [DVF, INSEE, ZONAGE]),
    ("charts", "03_clustering_price_boxplot.png"): (
        "extra_prix_par_cluster_boxplot.png", [DVF, INSEE, ZONAGE]),
    ("charts", "04_clustering_scatter.png"): (
        "extra_population_distance_par_cluster.png", [DVF, INSEE, ZONAGE]),
    ("charts", "06_price_importance.png"): (
        "extra_importance_features_prix.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("charts", "07_price_opportunity_hist.png"): (
        "extra_histogramme_score_opportunite.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("charts", "08_price_baseline_bar.png"): (
        "extra_prix_modele_vs_baseline.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("charts", "09_forecast_horizon_metrics.png"): (
        "extra_r2_mae_par_horizon_courbes.png", [DVF]),
    ("charts", "10_forecast_examples.png"): (
        "extra_exemples_forecast_4_communes.png", [DVF]),
    ("charts", "11_forecast_growth_risk.png"): (
        "extra_distribution_croissance_prevue.png", [DVF]),
    ("charts", "13_quantile_bands.png"): (
        "extra_bandes_prediction_quantile.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("charts", "14_hedonic_scatter.png"): (
        "extra_hedonique_reel_vs_predit.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("charts", "15_hedonic_importance.png"): (
        "extra_hedonique_importance_features.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("charts", "16_synthesis.png"): (
        "extra_synthese_gains_modeles.png", [DVF, INSEE, FILOSOFI, ZONAGE]),
    ("yield_geography", "yield_map_france.png"): (
        "extra_carte_rendement_brut_national.png", [DVF, LOYERS, FISCA, INTERNE]),
    ("yield_geography", "yield_map_france_net.png"): (
        "extra_carte_rendement_net_national.png", [DVF, LOYERS, FISCA, INTERNE]),
    ("memoire_extra", "cashflow_map_national.png"): (
        "extra_carte_cashflow_national.png", [DVF, LOYERS, FISCA, INTERNE]),
    ("memoire_extra", "yield_map_idf.png"): (
        "extra_carte_rendement_ile_de_france.png", [DVF, LOYERS, FISCA, INTERNE]),
    ("memoire_extra", "yield_map_77.png"): (
        "extra_carte_rendement_seine_et_marne.png", [DVF, LOYERS, FISCA, INTERNE]),
}


def _latest(model: str) -> Path:
    if model == "charts":
        return ARTIFACTS_DIR / "charts"  # generate_charts.py saves flat, pas de sous-dossier latest/
    return ARTIFACTS_DIR / model / "latest"


def run() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = REDACTION_DIR / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    sources_lines = ["# Sources des visuels du mémoire\n"]
    for (model, orig_name), (new_name, sources) in sorted(RENAME_MAP.items(), key=lambda kv: kv[1][0]):
        src = _latest(model) / orig_name
        if not src.exists():
            print(f"  (manquant : {model}/{orig_name}, lance le script correspondant d'abord)")
            continue
        shutil.copy2(src, run_dir / new_name)
        copied += 1
        if sources == [INTERNE]:
            citation = "Source : réalisation personnelle, 2026."
        else:
            citation = f"Source : réalisation personnelle à partir des données {', '.join(sources)}, 2026."
        sources_lines.append(f"## {new_name}\n(fichier d'origine : `{orig_name}`)\n\n{citation}\n")

    (run_dir / "SOURCES.md").write_text("\n".join(sources_lines), encoding="utf-8")
    print(f"{copied} visuels copiés + SOURCES.md dans {run_dir}")

    latest_dir = REDACTION_DIR / "latest"
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(run_dir, latest_dir)
    print(f"Mirroré vers {latest_dir}")
    return latest_dir


if __name__ == "__main__":
    run()
