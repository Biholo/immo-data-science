"""
Loader for the national zonage ABC (tension locative) file — used to backfill
`housing_zone` in build_cross_sectional.py for communes outside Île-de-France.

pipeline/scripts/seed_housing_zone.py's source file only covers IDF (its own
docstring says so). This is a separate, national file: 34875 communes vs
~1266. Kept as a pure local ml/ read (like density_grid.py) rather than
written back to `cities.housing_zone` — no DB write, no dependency on the
Prisma migration that would be needed to make seed_housing_zone.py itself
national.

Source: csv/zonage-abc-national.csv, downloaded from
https://www.data.gouv.fr/api/1/datasets/r/13f7282b-8a25-43ab-9713-8bb4e476df55
(https://www.data.gouv.fr/datasets/liste-des-communes-selon-le-zonage-abc).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

ZONAGE_FILE = Path(__file__).parent.parent.parent / "csv" / "zonage-abc-national.csv"

# Matches ZONE_MAP in pipeline/scripts/seed_housing_zone.py — keep in sync.
ZONE_MAP = {"Abis": "A_BIS", "A": "A", "B1": "B1", "B2": "B2", "C": "C"}


def load_zonage_national(path: Path = ZONAGE_FILE) -> pd.DataFrame:
    """Returns a DataFrame indexed by insee_code with column housing_zone (A/A_BIS/B1/B2/C)."""
    records = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader)
        i_code = 0  # CODGEO
        i_zone = header.index([h for h in header if h.startswith("Zonage")][0])

        for row in reader:
            if not row or not row[i_code]:
                continue
            zone = ZONE_MAP.get(row[i_zone].strip())
            if zone:
                records.append((row[i_code].strip().zfill(5), zone))

    return pd.DataFrame(records, columns=["insee_code", "housing_zone"]).set_index("insee_code")


if __name__ == "__main__":
    zonage = load_zonage_national()
    print(zonage.shape)
    print(zonage["housing_zone"].value_counts())
