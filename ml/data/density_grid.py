"""
Loader for INSEE's official "grille communale de densité" (7-level density
typology) — used by ml/models/clustering_hierarchical.py to presplit communes
before sub-clustering. Pure local file read, no DB involved, no new pipeline/
dependency: csv/grille-densite-communale-2024.xlsx, downloaded from
https://www.insee.fr/fr/information/6439600 (geography as of 2024-01-01).

7 levels (INSEE definition):
  1 = Grands centres urbains
  2 = Centres urbains intermédiaires
  3 = Petites villes
  4 = Ceintures urbaines
  5 = Rural sous forte influence d'un pôle
  6 = Rural à habitat dispersé
  7 = Rural à habitat très dispersé
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pandas as pd

DENSITY_FILE = Path(__file__).parent.parent.parent / "csv" / "grille-densite-communale-2024.xlsx"


def load_density_grid(path: Path = DENSITY_FILE) -> pd.DataFrame:
    """Returns a DataFrame indexed by insee_code with columns density_code (int 1-7), density_label."""
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb["Grille_Densite"]
    rows_iter = ws.iter_rows(values_only=True)

    header = None
    for row in rows_iter:
        if row and row[0] == "CODGEO":
            header = row
            break
    if header is None:
        raise RuntimeError(f"CODGEO header not found in {path} — file format may have changed")

    i_code = header.index("CODGEO")
    i_dens = header.index("DENS")
    i_lib = header.index("LIBDENS")

    records = []
    for row in rows_iter:
        if not row or row[i_code] is None:
            continue
        records.append((str(row[i_code]).strip().zfill(5), int(row[i_dens]), row[i_lib]))

    wb.close()
    return pd.DataFrame(records, columns=["insee_code", "density_code", "density_label"]).set_index("insee_code")


if __name__ == "__main__":
    grid = load_density_grid()
    print(grid.shape)
    print(grid["density_code"].value_counts().sort_index())
