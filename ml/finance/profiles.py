"""
5 profils investisseurs — conditions bancaires distinctes, pour tester la
sensibilité du cash-flow au financement plutôt qu'à la seule commune. Taux
illustratifs (marché français, ordre de grandeur 2026) — À AJUSTER avec les
taux réels du moment, ce ne sont pas des taux scrapés en direct.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InvestorProfile:
    name: str
    apport_pct: float          # % du prix d'achat apporté en cash
    taux_annuel: float         # taux nominal du prêt (décimal, ex: 0.035 = 3.5%)
    duree_annees: int          # durée du prêt
    assurance_pct_annuel: float  # assurance emprunteur, % du capital/an


PROFILES: list[InvestorProfile] = [
    InvestorProfile(
        name="Primo-accédant standard",
        apport_pct=0.10, taux_annuel=0.035, duree_annees=25, assurance_pct_annuel=0.0034,
    ),
    InvestorProfile(
        name="Investisseur expérimenté (bon dossier)",
        apport_pct=0.20, taux_annuel=0.032, duree_annees=20, assurance_pct_annuel=0.0025,
    ),
    InvestorProfile(
        name="Apport élevé / achat comptant partiel",
        apport_pct=0.40, taux_annuel=0.034, duree_annees=15, assurance_pct_annuel=0.0028,
    ),
    InvestorProfile(
        name="Effet de levier maximal (faible apport)",
        apport_pct=0.05, taux_annuel=0.039, duree_annees=25, assurance_pct_annuel=0.0036,
    ),
    InvestorProfile(
        name="Profil prudent (SCI, taux fixe long)",
        apport_pct=0.15, taux_annuel=0.036, duree_annees=20, assurance_pct_annuel=0.0030,
    ),
]
