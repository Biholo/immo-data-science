"""
DuckDB computation engine.
Loads all DVF files, creates cleaned view, runs series queries at each geo level.
"""

from __future__ import annotations

import duckdb
from pathlib import Path
from typing import Literal

from .geo import DEPT_TO_REGION, REGION_NAMES
from .series import SERIES

GeoLevel = Literal["city", "department", "region", "country"]

DVF_GLOB = str(Path(__file__).parent.parent.parent / "dvf-raw" / "*.txt")

VENTE_NATURES = ("Vente", "Vente en l'état futur d'achèvement")


DB_CACHE = str(Path(__file__).parent.parent.parent / "dvf_cache.duckdb")


def build_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(DB_CACHE)
    con.execute("SET memory_limit='4GB'")
    con.execute("SET temp_directory='/tmp/duckdb_tmp'")

    con.execute(f"""
        CREATE OR REPLACE VIEW dvf_raw AS
        SELECT * FROM read_csv(
            '{DVF_GLOB}',
            delim='|',
            header=true,
            quote='',
            encoding='utf-8'
        )
    """)

    # Cleaned view: DuckDB auto-infers Date=DATE, surfaces=BIGINT, type_local=BIGINT
    # Only Valeur fonciere needs manual parsing (French comma decimal)
    con.execute("""
        CREATE OR REPLACE VIEW dvf AS
        SELECT
            "Date mutation"                                           AS date_mutation,
            "Nature mutation"                                         AS nature_mutation,
            TRY_CAST(REPLACE("Valeur fonciere", ',', '.') AS DOUBLE) AS valeur_fonciere,
            "Code departement"                                        AS code_dept,
            "Code departement" || LPAD(CAST("Code commune" AS VARCHAR), 3, '0') AS code_insee,
            "Code type local"                                         AS type_local,
            "Surface reelle bati"                                     AS surface_bati,
            "Nombre pieces principales"                               AS nb_pieces,
            "Surface terrain"                                         AS surface_terrain,
            -- Mutation dedup key: multiple rows (locaux) can share one mutation
            MD5(CONCAT_WS('|',
                CAST("Date mutation" AS VARCHAR),
                REPLACE("Valeur fonciere", ',', '.'),
                "Code departement",
                CAST("Code commune" AS VARCHAR),
                CAST("No voie" AS VARCHAR),
                "Code voie"
            )) AS mutation_id
        FROM dvf_raw
        WHERE "Date mutation" IS NOT NULL
    """)

    # ── Mutation-level price view ────────────────────────────────────────────
    # valeur_fonciere in DVF raw = TOTAL mutation price, repeated on every local row.
    # Must aggregate to mutation level (SUM surfaces) before computing price/m².
    con.execute("""
        CREATE OR REPLACE VIEW dvf_mutation_prices AS
        WITH raw AS (
            SELECT *
            FROM dvf
            WHERE type_local IN (1, 2) AND surface_bati > 0
        ),
        mutation_totals AS (
            SELECT
                mutation_id,
                COUNT(*)                AS total_locaux,
                COUNT(DISTINCT type_local) AS n_distinct_types
            FROM raw
            GROUP BY mutation_id
        ),
        by_group AS (
            SELECT
                r.mutation_id,
                r.date_mutation,
                r.nature_mutation,
                r.code_dept,
                r.code_insee,
                r.type_local,
                r.nb_pieces,
                MAX(r.valeur_fonciere)  AS valeur_fonciere,
                SUM(r.surface_bati)     AS surface_bati,
                COUNT(*)                AS nb_locaux_this_group
            FROM raw r
            GROUP BY 1, 2, 3, 4, 5, 6, 7
        )
        SELECT g.*, m.total_locaux, m.n_distinct_types
        FROM by_group g
        JOIN mutation_totals m ON g.mutation_id = m.mutation_id
    """)

    # ── Mutation-level terrain view ──────────────────────────────────────────
    con.execute("""
        CREATE OR REPLACE VIEW dvf_mutation_terrain AS
        SELECT
            mutation_id,
            date_mutation,
            nature_mutation,
            code_dept,
            code_insee,
            MAX(valeur_fonciere)    AS valeur_fonciere,
            SUM(surface_terrain)    AS surface_terrain
        FROM dvf
        WHERE surface_terrain > 0 AND valeur_fonciere > 0
        GROUP BY mutation_id, date_mutation, nature_mutation, code_dept, code_insee
    """)

    # Dept → region lookup table
    rows = [(dept, code) for dept, code in DEPT_TO_REGION.items()]
    con.execute("CREATE OR REPLACE TABLE dept_region (code_dept VARCHAR, region_code VARCHAR)")
    con.executemany("INSERT INTO dept_region VALUES (?, ?)", rows)

    # Region code → name lookup
    region_rows = [(code, name) for code, name in REGION_NAMES.items()]
    con.execute("CREATE OR REPLACE TABLE region_names (region_code VARCHAR, region_name VARCHAR)")
    con.executemany("INSERT INTO region_names VALUES (?, ?)", region_rows)

    return con


def _geo_col(geo_level: GeoLevel) -> str:
    return {
        "city": "code_insee",
        "department": "code_dept",
        "region": "region_code",
        "country": "'FR'",
    }[geo_level]


def _geo_join(geo_level: GeoLevel) -> str:
    if geo_level == "region":
        return "LEFT JOIN dept_region USING (code_dept)"
    return ""


def _dept_clause(geo_level: GeoLevel, dept_filter: str | None) -> str:
    if dept_filter and geo_level == "city":
        return f"AND code_dept = '{dept_filter}'"
    return ""


def _standard_query(serie: dict, geo_level: GeoLevel, dept_filter: str | None = None) -> str:
    geo_col = _geo_col(geo_level)
    geo_join = _geo_join(geo_level)
    from_table = serie.get("view", "dvf")
    dept_clause = _dept_clause(geo_level, dept_filter)

    return f"""
    SELECT
        {geo_col}                                   AS geo_code,
        DATE_TRUNC('quarter', date_mutation)        AS period,
        {serie['select']}                           AS value,
        COUNT(DISTINCT mutation_id)                 AS n_tx
    FROM {from_table}
    {geo_join}
    WHERE {serie['nature_clause']}
      AND {serie['where']}
      AND date_mutation IS NOT NULL
      {dept_clause}
    GROUP BY 1, 2
    HAVING COUNT(DISTINCT mutation_id) >= {serie['min_n']}
      AND geo_code IS NOT NULL
    ORDER BY 1, 2
    """


def _vefa_share_query(geo_level: GeoLevel, min_n: int, dept_filter: str | None = None) -> str:
    geo_col = _geo_col(geo_level)
    geo_join = _geo_join(geo_level)
    dept_clause = _dept_clause(geo_level, dept_filter)

    vefa = "Vente en l''état futur d''achèvement"  # SQL-escaped apostrophes
    return f"""
    SELECT
        {geo_col} AS geo_code,
        DATE_TRUNC('quarter', date_mutation) AS period,
        COUNT(DISTINCT CASE WHEN nature_mutation = '{vefa}'
                            THEN mutation_id END) * 100.0
        / NULLIF(COUNT(DISTINCT CASE WHEN nature_mutation IN ('Vente', '{vefa}')
                                      THEN mutation_id END), 0) AS value,
        COUNT(DISTINCT mutation_id) AS n_tx
    FROM dvf
    {geo_join}
    WHERE nature_mutation IN ('Vente', '{vefa}')
      AND type_local IN (1, 2)
      AND date_mutation IS NOT NULL
      {dept_clause}
    GROUP BY 1, 2
    HAVING COUNT(DISTINCT mutation_id) >= {min_n}
      AND geo_code IS NOT NULL
    ORDER BY 1, 2
    """


def compute_serie(
    con: duckdb.DuckDBPyConnection,
    serie: dict,
    geo_level: GeoLevel,
    dept_filter: str | None = None,
) -> list[tuple]:
    """
    Returns list of (geo_code, period, value) tuples.
    period is a datetime (first day of quarter).
    dept_filter restricts city-level queries to one department code.
    """
    if serie.get("special") == "vefa_share":
        sql = _vefa_share_query(geo_level, serie["min_n"], dept_filter)
    else:
        sql = _standard_query(serie, geo_level, dept_filter)

    rows = con.execute(sql).fetchall()
    # (geo_code, period, value, n_tx) → drop n_tx
    return [(r[0], r[1], r[2]) for r in rows if r[2] is not None]


def compute_all(
    geo_levels: list[GeoLevel] = ("city", "department", "region", "country"),
    dept_filter: str | None = None,
) -> dict[tuple[str, str], list[tuple]]:
    """
    Returns {(serie_name, geo_level): [(geo_code, period, value), ...]}
    dept_filter: if set, restricts city-level to that department code (e.g. '77').
    """
    print("Loading DVF files into DuckDB...")
    con = build_connection()

    results: dict[tuple[str, str], list[tuple]] = {}

    for serie in SERIES:
        for geo_level in geo_levels:
            key = (serie["name"], geo_level)
            print(f"  Computing {serie['name']} @ {geo_level}...", end=" ", flush=True)
            rows = compute_serie(con, serie, geo_level, dept_filter)
            results[key] = rows
            print(f"{len(rows)} rows")

    con.close()
    return results
