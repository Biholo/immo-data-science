"""
Simule le cash-flow mensuel/annuel pour les communes du Top 20
(ml.scripts.generate_recommendations) croisées avec 5 profils investisseurs
(ml.finance.profiles) — calcul 100% déterministe (ml.finance.cashflow), pas
de ML ici, conforme à CONTEXTE_ML_MEMOIRE.md §6.

Bien de référence : appartement 52m² (même référence que la Carte des loyers
ANIL pour "appartements tous types" — choix cohérent avec le reste du
pipeline, pas arbitraire).

Apport/capital emprunté portent sur le coût d'acquisition RÉEL (prix + frais
de notaire + travaux, ml.finance.cashflow.cout_acquisition_total), pas le
prix nu — même convention que build_cross_sectional.py/analyze_yield_geography.py,
voir EXPERIMENTS_LOG.md "Modèle rendement" #6.

Run après ml.scripts.generate_recommendations :
  python -m ml.scripts.simulate_cashflow
Output: ml/artifacts/cashflow/latest/
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import matplotlib.pyplot as plt  # noqa: E402

from ml.config import ARTIFACTS_DIR  # noqa: E402
from ml.data.build_cross_sectional import build_cross_sectional  # noqa: E402
from ml.data.property_tax import load_property_tax  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata  # noqa: E402
from ml.evaluation.plotting import CAT_COLORS, save, setup_style  # noqa: E402
from ml.finance.cashflow import (  # noqa: E402
    assurance_mensuelle,
    cash_flow_mensuel,
    charges_annuelles_estimees,
    cout_acquisition_total,
    loyer_effectif,
    mensualite_pret,
    rendement_net,
    taxe_fonciere_annuelle,
)
from ml.finance.profiles import PROFILES  # noqa: E402

REFERENCE_SURFACE_M2 = 52.0  # même référence que rent_appt_all (ANIL, "appartements tous types")


def _latest(model: str) -> Path:
    return ARTIFACTS_DIR / model / "latest"


def load_target_communes() -> pd.DataFrame:
    """Top 20 du dernier run de generate_recommendations, enrichi loyer/vacance/prix/taxe foncière."""
    top20_path = _latest("recommendations") / "top_20_opportunities.csv"
    top20 = pd.read_csv(top20_path, dtype={"insee_code": str})

    full = build_cross_sectional(exclude_estimated=True)
    tax = load_property_tax()

    df = top20[["insee_code", "name", "department_code"]].merge(
        full[["median_price_per_sqm", "rent_appt_all", "vacancy_rate"]],
        left_on="insee_code", right_index=True, how="left",
    )
    df = df.merge(tax, on="insee_code", how="left")
    return df.dropna(subset=["median_price_per_sqm", "rent_appt_all"])


def simulate_one(row: pd.Series, profile) -> dict:
    prix_achat = row["median_price_per_sqm"] * REFERENCE_SURFACE_M2
    # Coût d'acquisition réel = prix + notaire + travaux (ml.finance.cashflow) — apport
    # et capital emprunté portent sur ce total, pas sur le prix nu. Frais de notaire
    # payés cash en pratique (rarement financés), travaux souvent inclus au prêt —
    # simplification ici : apport_pct s'applique au coût total, cohérent avec
    # generate_recommendations.py / analyze_yield_geography.py qui utilisent la
    # même définition de "coût d'acquisition" pour le rendement.
    cout_acquisition = cout_acquisition_total(prix_achat)
    apport = cout_acquisition * profile.apport_pct
    capital = cout_acquisition - apport

    mens_pret = mensualite_pret(capital, profile.taux_annuel, profile.duree_annees)
    mens_assurance = assurance_mensuelle(capital, profile.assurance_pct_annuel)
    mensualite_totale = mens_pret + mens_assurance

    loyer_theorique_mensuel = row["rent_appt_all"] * REFERENCE_SURFACE_M2
    loyer_annuel_theorique = loyer_theorique_mensuel * 12
    vacance = row["vacancy_rate"] if pd.notna(row["vacancy_rate"]) else 0.0
    loyer_eff_mensuel = loyer_effectif(loyer_theorique_mensuel, vacance)

    charges_an = charges_annuelles_estimees(loyer_annuel_theorique)
    charges_m = charges_an / 12

    taux_foncier = row["taux_foncier_pct"] if pd.notna(row.get("taux_foncier_pct")) else 0.0
    taxe_an = taxe_fonciere_annuelle(loyer_annuel_theorique, taux_foncier)
    taxe_m = taxe_an / 12

    cf_m = cash_flow_mensuel(loyer_eff_mensuel, mensualite_totale, charges_m, taxe_m)
    rend_net = rendement_net(loyer_annuel_theorique, charges_an + taxe_an, cout_acquisition)

    return {
        "insee_code": row["insee_code"],
        "name": row["name"],
        "profil": profile.name,
        "prix_achat": round(prix_achat),
        "cout_acquisition": round(cout_acquisition),
        "apport": round(apport),
        "capital_emprunte": round(capital),
        "taux_annuel_pct": profile.taux_annuel * 100,
        "duree_annees": profile.duree_annees,
        "mensualite_totale": round(mensualite_totale),
        "loyer_effectif_mensuel": round(loyer_eff_mensuel),
        "charges_mensuelles": round(charges_m),
        "fiscalite_mensuelle": round(taxe_m),
        "cash_flow_mensuel": round(cf_m),
        "cash_flow_annuel": round(cf_m * 12),
        "rendement_net_pct": round(rend_net, 2),
    }


SHORT_LABELS = {
    "Primo-accédant standard": "Primo-\naccédant",
    "Investisseur expérimenté (bon dossier)": "Investisseur\nexpérimenté",
    "Apport élevé / achat comptant partiel": "Apport\nélevé",
    "Effet de levier maximal (faible apport)": "Levier\nmaximal",
    "Profil prudent (SCI, taux fixe long)": "Profil\nprudent (SCI)",
}


def chart_cashflow_by_profile(results: pd.DataFrame, run_dir: Path) -> None:
    order = [p.name for p in PROFILES]
    data = [results.loc[results["profil"] == name, "cash_flow_mensuel"] for name in order]

    fig, ax = plt.subplots(figsize=(9, 5.5))
    bp = ax.boxplot(data, tick_labels=[SHORT_LABELS[n] for n in order], patch_artist=True, showfliers=True)
    for patch, color in zip(bp["boxes"], CAT_COLORS):
        patch.set_facecolor(color)
        patch.set_alpha(0.55)
    ax.axhline(0, color="#898781", linewidth=1.2, linestyle="--")
    ax.set_ylabel(f"Cash-flow mensuel (€, appartement {REFERENCE_SURFACE_M2:.0f}m² réf.)")
    ax.set_title("Cash-flow mensuel par profil investisseur — Top 20 opportunités")
    ax.tick_params(axis="x", labelsize=9)
    ax.grid(True, axis="y")
    fig.tight_layout()
    save(fig, run_dir / "cashflow_by_profile.png")


def run(dry_run: bool = False) -> Path:
    print("Loading Top 20 + rent + vacancy + property tax...")
    communes = load_target_communes()
    print(f"  {len(communes)} communes (of Top 20) with complete data")

    print(f"Simulating {len(communes)} communes x {len(PROFILES)} profils...")
    rows = [simulate_one(row, profile) for _, row in communes.iterrows() for profile in PROFILES]
    results = pd.DataFrame(rows)

    summary = results.groupby("profil")["cash_flow_mensuel"].agg(["mean", "median", "min", "max"]).reindex(
        [p.name for p in PROFILES]
    )
    print(summary.round(0))

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        return Path()

    run_dir = new_run_dir("cashflow")
    setup_style()

    save_dataframe(run_dir, results, "cashflow_simulations.csv")
    save_dataframe(run_dir, summary, "summary_by_profile.csv")
    chart_cashflow_by_profile(results, run_dir)

    save_metadata(run_dir, {
        "note": "Calcul déterministe (CONTEXTE_ML_MEMOIRE.md §6), pas un modèle ML — voir ml/finance/cashflow.py",
        "reference_surface_m2": REFERENCE_SURFACE_M2,
        "n_communes": len(communes),
        "n_profiles": len(PROFILES),
        "profiles": [
            {"name": p.name, "apport_pct": p.apport_pct, "taux_annuel": p.taux_annuel,
             "duree_annees": p.duree_annees, "assurance_pct_annuel": p.assurance_pct_annuel}
            for p in PROFILES
        ],
        "assumptions": {
            "cadastral_base_ratio": "50% du loyer annuel théorique (approximation, pas la vraie base DGFiP)",
            "charges_ratio": "10% du loyer annuel (hypothèse, aucune source ouverte trouvée)",
            "frais_notaire_pct": "7.5% du prix (barème officiel, bien ancien)",
            "travaux_ratio": "10% du prix (convention investisseurs locatifs, PAS une mesure)",
            "vacance": "vacancy_rate INSEE (logement général, pas spécifiquement locatif — meilleur proxy disponible)",
        },
    })

    latest = publish_latest("cashflow", run_dir)
    print(f"Done. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(dry_run=args.dry_run)
