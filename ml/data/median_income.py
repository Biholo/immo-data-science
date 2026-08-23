"""
Loader for INSEE Filosofi 2021 (revenus, pauvreté et niveau de vie) at
commune level — used to add `median_income` as a feature to Models 1/2/4.

Pure local file read, no DB involved: csv/DS_FILOSOFI_CC_data.csv, downloaded
from https://www.insee.fr/fr/statistiques/7756729
(base-cc-filosofi-2021-geo2025_csv.zip).

Long format: one row per (commune, indicator). We only keep
FILOSOFI_MEASURE == 'MED_SL' ("Niveau de vie médian en euros"), GEO_OBJECT
== 'COM'. Some communes have no value (CONF_STATUS='C', statistical secrecy
on small populations) — OBS_VALUE is empty, dropped.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

FILOSOFI_FILE = Path(__file__).parent.parent.parent / "csv" / "DS_FILOSOFI_CC_data.csv"
MEASURE = "MED_SL"


def load_median_income(path: Path = FILOSOFI_FILE) -> pd.DataFrame:
    """Returns a DataFrame indexed by insee_code with column median_income (float, euros/an)."""
    records = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";", quotechar='"')
        for row in reader:
            if row["GEO_OBJECT"] != "COM" or row["FILOSOFI_MEASURE"] != MEASURE:
                continue
            val = row["OBS_VALUE"].strip()
            if not val:
                continue
            records.append((row["GEO"].strip().zfill(5), float(val)))

    return pd.DataFrame(records, columns=["insee_code", "median_income"]).set_index("insee_code")


if __name__ == "__main__":
    income = load_median_income()
    print(income.shape)
    print(income["median_income"].describe())
