"""
Seed the quarterly sales-count series by typology of each commune:

  transaction_volume_t1   apartments with 0 or 1 room (studios)
  transaction_volume_t2   apartments with 2 rooms
  transaction_volume_t3   apartments with 3 rooms
  transaction_volume_t4   apartments with 4 rooms or more

Same definition as the `transaction_volume_t{n}` entries of services/series.py (apartments only,
buckets matching price_sqm_t1..t4, COUNT(DISTINCT mutation)), but computed straight from the
geo-dvf files cached by seed_transactions.py (dvf-raw/geo-dvf/full-{year}.csv.gz). The raw
DGFiP ValeursFoncieres files that run_dvf needs are not required, and the `transactions` table
cannot be used because it does not keep the number of rooms.

  scope    : nature_mutation Vente + VEFA, code_type_local = 2 (apartment)
  quarter  : first day of the quarter of the mutation date (same as the other QUARTERLY series)
  geography: Paris / Lyon / Marseille arrondissements folded onto the parent commune
  no minimum: a count is meaningful even with 1 sale (min_n = 1 in series.py)

Create-or-update: an existing serie is reused and its points are updated, never duplicated (re-runnable).

Run (after seed_transactions.py):
  python -m pipeline.scripts.seed_transaction_volume_typology [--dept 77] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import duckdb
import psycopg2
from dotenv import load_dotenv

from ..services.geo import ARR_TO_COMMUNE
from ..services.upload import upsert_series_and_timeseries

load_dotenv()

GEO_DVF_DIR = Path(__file__).parent.parent.parent / "dvf-raw" / "geo-dvf"

COMMON = {"source": "DVF", "unit": "transactions", "frequency": "QUARTERLY", "chart_type": "BAR"}
SERIES_NAMES = [f"transaction_volume_t{n}" for n in (1, 2, 3, 4)]


def load_counts(dept: str | None) -> list[tuple[str, str, int, int]]:
    """[(insee_code, quarter_iso, typology 1-4, n_sales)] from every geo-dvf year on disk."""
    files = sorted(GEO_DVF_DIR.glob("full-*.csv.gz"))
    if not files:
        print(f"No geo-dvf file in {GEO_DVF_DIR} -- run pipeline.scripts.seed_transactions first")
        sys.exit(1)
    print(f"  {len(files)} geo-dvf files: {', '.join(f.name for f in files)}")

    con = duckdb.connect()
    con.execute("CREATE TABLE arr_commune (code VARCHAR, commune VARCHAR)")
    con.executemany("INSERT INTO arr_commune VALUES (?, ?)", list(ARR_TO_COMMUNE.items()))

    dept_clause = "AND code_departement = ?" if dept else ""
    params = [dept] if dept else []
    glob_sql = (GEO_DVF_DIR / "full-*.csv.gz").as_posix().replace("'", "''")
    rows = con.execute(
        f"""
        WITH apartments AS (
            SELECT
                id_mutation,
                CAST(date_mutation AS DATE)                      AS mutation_date,
                code_commune,
                TRY_CAST(nombre_pieces_principales AS INTEGER)   AS pieces
            FROM read_csv('{glob_sql}', header=true, all_varchar=true, delim=',', quote='"', escape='"')
            WHERE nature_mutation IN ('Vente', 'Vente en l''état futur d''achèvement')
              AND code_type_local = '2'
              {dept_clause}
        )
        SELECT
            COALESCE(a.commune, x.code_commune)                  AS insee_code,
            CAST(date_trunc('quarter', x.mutation_date) AS DATE) AS quarter,
            CASE WHEN x.pieces IS NULL THEN NULL
                 WHEN x.pieces <= 1 THEN 1
                 WHEN x.pieces = 2 THEN 2
                 WHEN x.pieces = 3 THEN 3
                 ELSE 4 END                                      AS typology,
            COUNT(DISTINCT x.id_mutation)                        AS n_sales
        FROM apartments x
        LEFT JOIN arr_commune a ON a.code = x.code_commune
        WHERE x.pieces IS NOT NULL
        GROUP BY 1, 2, 3
        """,
        params,
    ).fetchall()
    return [(r[0], r[1].isoformat(), r[2], r[3]) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept", default=None, help="Restrict to dept code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(1)

    print("Counting apartment sales by typology ...")
    counts = load_counts(args.dept)
    print(f"  {len(counts)} (commune, quarter, typology) rows, {len({c[0] for c in counts})} communes")

    conn = psycopg2.connect(dsn)
    try:
        for n in (1, 2, 3, 4):
            name = f"transaction_volume_t{n}"
            rows = [(insee, quarter, float(count)) for insee, quarter, typology, count in counts if typology == n]
            print(f"  {name}: {len(rows)} points", end=" ")
            stats = upsert_series_and_timeseries(conn, {"name": name, **COMMON}, "city", rows, dry_run=args.dry_run)
            print(f"-> {stats['series_upserted']} series, {stats['timeseries_inserted']} timeseries rows")
        if args.dry_run:
            print("\n[DRY RUN] no writes")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
