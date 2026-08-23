"""
Loader for DGFiP "Fiscalité locale des particuliers" — commune-level taxe
foncière rate (Taux_Global_TFB), used by ml/finance/cashflow.py.

Pure local file read, no DB involved: csv/fiscalite/fiscalite-locale-des-particuliers.csv,
downloaded from https://www.data.gouv.fr/datasets/fiscalite-locale-des-particuliers
(multi-year 2021-2025, ~35k communes, 100% coverage on the latest year — no
statistical-secrecy suppression here, unlike Filosofi/LOVAC).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

TAX_FILE = Path(__file__).parent.parent.parent / "csv" / "fiscalite" / "fiscalite-locale-des-particuliers.csv"


def load_property_tax(path: Path = TAX_FILE, year: int | None = None) -> pd.DataFrame:
    """Returns a DataFrame indexed by insee_code with column taux_foncier_pct
    (Taux_Global_TFB, %) for the given year (latest available if None)."""
    df = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype={"INSEE COM": str, "EXERCICE": str})
    target_year = str(year) if year else df["EXERCICE"].max()
    df = df[df["EXERCICE"] == target_year]
    df = df.dropna(subset=["Taux_Global_TFB"])

    out = df[["INSEE COM", "Taux_Global_TFB"]].rename(
        columns={"INSEE COM": "insee_code", "Taux_Global_TFB": "taux_foncier_pct"}
    )
    out["insee_code"] = out["insee_code"].str.zfill(5)
    return out.drop_duplicates(subset="insee_code").set_index("insee_code")


if __name__ == "__main__":
    tax = load_property_tax()
    print(tax.shape)
    print(tax["taux_foncier_pct"].describe())
