"""
Loads individual DVF transactions (NOT aggregated to commune level) — the
per-property granularity ml/models/price_hedonic.py trains on.

Reuses pipeline/services/dvf.py's cached DuckDB connection and
pipeline/services/series.py's exact quality filters (VENTE_NATURE,
BASE_PRICE_MULTI) so this stays consistent with how price_sqm_all itself is
computed — same rows that build the commune-level target, just not
aggregated away.

~4.86M usable transactions nationally (2021-2025, mainland) — see
EXPERIMENTS_LOG.md. Default caps to a random sample for iteration speed;
pass sample=None for the full set.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pipeline.services.dvf import build_connection  # noqa: E402
from pipeline.services.series import BASE_PRICE_MULTI, VENTE_NATURE  # noqa: E402

DEFAULT_SAMPLE = 500_000


def build_transactions(
    dept_filter: str | None = None,
    sample: int | None = DEFAULT_SAMPLE,
    random_state: int = 42,
) -> pd.DataFrame:
    """
    Returns one row per DVF mutation: insee_code, department_code, period
    (quarter, Timestamp), type_local (1=maison, 2=appartement), nb_pieces,
    surface_bati, price_sqm. Same filters as price_sqm_all (series.py).
    """
    con = build_connection()
    dept_clause = f"AND code_dept = '{dept_filter}'" if dept_filter else ""

    sql = f"""
        SELECT
            code_insee AS insee_code,
            code_dept AS department_code,
            DATE_TRUNC('quarter', date_mutation) AS period,
            type_local,
            nb_pieces,
            surface_bati,
            valeur_fonciere / surface_bati AS price_sqm
        FROM dvf_mutation_prices
        WHERE {VENTE_NATURE}
          AND type_local IN (1, 2)
          AND {BASE_PRICE_MULTI}
          {dept_clause}
    """
    df = con.execute(sql).df()
    con.close()

    df["insee_code"] = df["insee_code"].astype(str).str.zfill(5)
    df["period"] = pd.to_datetime(df["period"])

    if sample and len(df) > sample:
        df = df.sample(sample, random_state=random_state).reset_index(drop=True)

    return df


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None)
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--full", action="store_true", help="No sampling, load all usable transactions")
    args = parser.parse_args()

    df = build_transactions(dept_filter=args.dept, sample=None if args.full else args.sample)
    print(df.shape)
    print(df.head())
    print(df["price_sqm"].describe())
