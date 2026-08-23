"""
Deterministic financial calculators — CONTEXTE_ML_MEMOIRE.md §6 is explicit
that these must stay rule-based and testable, NOT learned: "Certaines
briques de Rentium ne doivent pas être apprises par un modèle ML. Lorsque la
formule métier est connue, elle doit être calculée directement." Every
function here is a pure, unit-testable translation of one formula from that
section. No ML, no DB, no randomness.

Three inputs are estimated rather than directly observed, and ALL are
explicitly flagged in their docstring/return, not silently assumed:
  - taxe foncière needs a "valeur locative cadastrale" per property, which
    isn't published at commune granularity anywhere found this session — the
    standard real-estate rule of thumb (base cadastrale ≈ 50% of market
    annual rent) is used instead of pretending we have the real base.
  - charges non récupérables (syndic, entretien, assurance PNO) have no open
    data source at all — a flat 10% of loyer annuel is a documented
    assumption, not a measurement.
  - travaux (rénovation) have no open data source at all either — a flat 10%
    of prix d'achat is a documented convention (usuelle chez les investisseurs
    locatifs sur de l'ancien), not a measurement.

Rendement "brut" vs "net" — la distinction qui compte est le MOMENT de la
dépense, pas juste "avec ou sans frais": frais de notaire + travaux sont
dépensés AVANT la mise en location (capital investi, toujours dans le
dénominateur — même le "brut" les inclut, cf. rendement_brut ci-dessous).
Charges + taxe foncière sont des dépenses APRÈS mise en location (récurrentes,
seul le "net" les déduit du numérateur). "Brut" ne veut donc pas dire "sans
aucun frais" — ça veut dire "sans déduire les charges d'exploitation
récurrentes du loyer".
"""

from __future__ import annotations

CADASTRAL_BASE_RATIO = 0.5   # valeur locative cadastrale ≈ 50% du loyer annuel de marché (approximation usuelle, pas une vraie base DGFiP)
CHARGES_RATIO_DEFAULT = 0.10  # charges non récupérables ≈ 10% du loyer annuel (hypothèse, aucune source ouverte)
FRAIS_NOTAIRE_ANCIEN_PCT = 0.075  # frais de notaire/acquisition, bien ancien (~95% du volume DVF) : barème officiel connu, ~7-8% du prix, PAS une hypothèse — voir notaires.fr
FRAIS_NOTAIRE_NEUF_PCT = 0.025    # bien neuf/VEFA : ~2-3%, DVF ne distingue pas fiablement ancien/neuf à la maille commune donc ANCIEN reste le défaut national
TRAVAUX_RATIO_DEFAULT = 0.10      # travaux ≈ 10% du prix d'achat (convention usuelle citée par les investisseurs locatifs pour de l'ancien, PAS une donnée mesurée — aucune source ouverte à la maille commune)


def mensualite_pret(capital: float, taux_annuel: float, duree_annees: int) -> float:
    """
    Mensualité d'un prêt amortissable — CONTEXTE_ML_MEMOIRE.md §6:
    M = C × i(1+i)^n / ((1+i)^n - 1)
    capital: montant emprunté (€). taux_annuel: taux nominal (ex: 0.035 = 3.5%).
    duree_annees: durée du prêt.
    """
    if capital <= 0:
        return 0.0
    n = duree_annees * 12
    i = taux_annuel / 12
    if i == 0:
        return capital / n
    return capital * (i * (1 + i) ** n) / ((1 + i) ** n - 1)


def assurance_mensuelle(capital: float, assurance_pct_annuel: float) -> float:
    """Assurance emprunteur, calculée sur le capital initial (pratique courante), €/mois."""
    return capital * assurance_pct_annuel / 12


def rendement_brut(loyer_annuel: float, cout_acquisition: float) -> float:
    """§6: R_brut = Loyers_annuels / Coût_acquisition × 100.
    cout_acquisition doit être le capital réellement investi avant location —
    passer `cout_acquisition_total()` (prix + notaire + travaux), pas le prix
    nu, sinon "brut" sous-entend à tort "sans les frais d'acquisition"."""
    return loyer_annuel / cout_acquisition * 100 if cout_acquisition else 0.0


def rendement_net(loyer_annuel: float, charges_annuelles: float, cout_acquisition: float) -> float:
    """§6: R_net = (Loyers_annuels - Charges_annuelles) / Coût_acquisition × 100.
    Même cout_acquisition que rendement_brut (voir sa docstring) — la seule
    différence net/brut est la déduction des charges d'exploitation récurrentes."""
    return (loyer_annuel - charges_annuelles) / cout_acquisition * 100 if cout_acquisition else 0.0


def cout_acquisition_total(
    prix_achat: float,
    frais_notaire_pct: float = FRAIS_NOTAIRE_ANCIEN_PCT,
    travaux_ratio: float = TRAVAUX_RATIO_DEFAULT,
) -> float:
    """Prix d'achat + frais de notaire (barème officiel) + travaux (convention
    10% du prix, pas une mesure — voir docstring du module). C'est le capital
    RÉELLEMENT investi avant la mise en location : appartient au dénominateur
    de TOUT rendement, y compris le "brut" — seules les charges d'exploitation
    récurrentes (après mise en location) distinguent brut de net.
    Passer travaux_ratio=0 pour exclure les travaux (ex: bien déjà rénové)."""
    return prix_achat * (1 + frais_notaire_pct + travaux_ratio)


def loyer_effectif(loyer_theorique: float, taux_vacance: float) -> float:
    """§6: Loyer_effectif = Loyer_théorique × (1 - Taux_vacance).
    taux_vacance ici = `vacancy_rate` INSEE (vacance logement générale, pas
    spécifiquement locative — le meilleur proxy disponible, voir EXPERIMENTS_LOG.md)."""
    return loyer_theorique * (1 - taux_vacance)


def taxe_fonciere_annuelle(
    loyer_annuel_theorique: float,
    taux_foncier_pct: float,
    cadastral_base_ratio: float = CADASTRAL_BASE_RATIO,
) -> float:
    """
    Approximation : base cadastrale non publiée à la maille commune, estimée
    à `cadastral_base_ratio` × loyer annuel théorique (usage courant en
    simulation immobilière, PAS une vraie valeur locative cadastrale DGFiP).
    taxe = base_estimée × taux_foncier_pct / 100.
    """
    base_estimee = loyer_annuel_theorique * cadastral_base_ratio
    return base_estimee * taux_foncier_pct / 100


def charges_annuelles_estimees(loyer_annuel: float, charges_ratio: float = CHARGES_RATIO_DEFAULT) -> float:
    """Hypothèse (pas une donnée) : charges non récupérables ≈ `charges_ratio` × loyer annuel."""
    return loyer_annuel * charges_ratio


def cash_flow_mensuel(
    loyer_net_mensuel: float,
    mensualite_totale: float,
    charges_mensuelles: float,
    fiscalite_mensuelle: float,
) -> float:
    """§6: CF_m = Loyer_net,m - Mensualité - Charges_m - Fiscalité_m."""
    return loyer_net_mensuel - mensualite_totale - charges_mensuelles - fiscalite_mensuelle
