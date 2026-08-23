"""
Loader for the ANIL/DHUP "Carte des loyers" 2025 — commune-level rent
estimates, split into 4 property segments. Used to compute rendement_brut
(gross rental yield) and to feed a future yield-prediction model.

Pure local file read, no DB involved: csv/loyers/*.csv, downloaded from
https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025
(~7M rental listings analyzed by ANIL, methodology by AgroSup Dijon/INRAE).

`loypredm2` is €/m²/MONTH, charges included, vacant unfurnished reference
property (sanity-checked: ~9-13€/m² for rural communes matches known French
rural rent levels; Paris-range communes would show ~25-30€/m²).

Files (identified via their real filename in the HTTP Content-Disposition
header — the data.gouv.fr page itself doesn't label the download buttons):
  pred-app-mef-dhup.csv    -> appartements tous types (52m² référence)
  pred-app12-mef-dhup.csv  -> appartements T1-T2 (37m² référence)
  pred-app3-mef-dhup.csv   -> appartements T3+ (72m² référence)
  pred-mai-mef-dhup.csv    -> maisons (92m² référence)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

RENT_DIR = Path(__file__).parent.parent.parent / "csv" / "loyers"

FILES = {
    "rent_appt_all": "pred-app-mef-dhup.csv",
    "rent_appt_t12": "pred-app12-mef-dhup.csv",
    "rent_appt_t3plus": "pred-app3-mef-dhup.csv",
    "rent_maison": "pred-mai-mef-dhup.csv",
}


def _load_one(path: Path) -> pd.Series:
    df = pd.read_csv(path, sep=";", decimal=",", encoding="latin-1", dtype={"INSEE_C": str})
    df = df.dropna(subset=["loypredm2"])
    return df.set_index("INSEE_C")["loypredm2"]


def load_rent(rent_dir: Path = RENT_DIR) -> pd.DataFrame:
    """Returns a DataFrame indexed by insee_code with 4 columns (€/m²/mois):
    rent_appt_all, rent_appt_t12, rent_appt_t3plus, rent_maison."""
    series = {}
    for col, filename in FILES.items():
        path = rent_dir / filename
        if not path.exists():
            print(f"  Warning: {path} missing, skipping {col}")
            continue
        series[col] = _load_one(path)

    out = pd.DataFrame(series)
    out.index.name = "insee_code"
    return out


if __name__ == "__main__":
    rent = load_rent()
    print(rent.shape)
    print(rent.describe())
