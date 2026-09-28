"""
Loads every real DVF sale (Maison / Appartement / Local commercial) into the
`transactions` table of the Rentium DB — the table behind "Ventes comparables"
(radius search via PostGIS).

Source: geo-dvf (etalab) = the DGFiP DVF files enriched with longitude/latitude
per row. The raw DGFiP files in dvf-raw/ carry NO coordinates, so they cannot
feed a geolocated table.
  https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/full.csv.gz  (~100 Mo/an)

Model rules (see rentium/backend/prisma/schema/transaction.prisma):
  - one row per LOCAL sold (Maison=1, Appartement=2, Local industriel/commercial=4);
    dependances (type 3), terrains and rows without local are skipped
  - `price`            = mutation TOTAL price (DVF repeats it on every row — never split)
  - `mutation_id`      = geo-dvf id_mutation ("2025-1234", unique across years)
  - `lots_in_mutation` = number of rows kept for that mutation (1 = single-unit sale,
                         >1 = block / building sale)
  - nature_mutation    : Vente + Vente en l'etat futur d'achevement (same scope as run_dvf)
  - Paris / Lyon / Marseille arrondissements are folded onto the parent commune
    (ARR_TO_COMMUNE) so city_id matches the `cities` table
  - rows whose commune is not in `cities` (merged communes...) keep city_id NULL

Idempotent: for each year loaded, existing source='DVF' rows of that year (and of
--dept when given) are deleted then re-inserted, in ONE transaction per year.
Downloaded files are cached in dvf-raw/geo-dvf/ and re-downloaded only when the
remote size changed (DVF is republished twice a year) or with --refresh.

  python -m pipeline.scripts.seed_transactions                       # 2021 -> current year
  python -m pipeline.scripts.seed_transactions --years 2024,2025
  python -m pipeline.scripts.seed_transactions --dept 77 --dry-run
  python -m pipeline.scripts.seed_transactions --refresh             # force re-download

Run AFTER seed_cities (needs `cities`) and BEFORE seed_city_sale_prices.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import duckdb
import psycopg2
from dotenv import load_dotenv

from ..services.geo import ARR_TO_COMMUNE

load_dotenv()

GEO_DVF_URL = "https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/full.csv.gz"
CACHE_DIR = Path(__file__).parent.parent.parent / "dvf-raw" / "geo-dvf"
FIRST_YEAR = 2021
USER_AGENT = "rentium-immo-data-science/1.0"

MIN_PRICE = 1_000          # symbolic sales (1 EUR, family transfers...) are noise
MIN_SURFACE = 5            # m2
MAX_SURFACE = 50_000       # m2, absurd values (unit errors)
MAX_PRICE = 500_000_000


def _remote_size(url: str) -> int | None:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FileNotFoundError(url) from e
        return None
    except (urllib.error.URLError, TimeoutError):
        return None


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r    {done / 1e6:6.1f} / {total / 1e6:.1f} Mo", end="", flush=True)
    print()
    tmp.replace(dest)


def ensure_file(year: int, refresh: bool) -> Path | None:
    """Returns the local geo-dvf file for `year`, downloading it if needed. None if the year is not published."""
    url = GEO_DVF_URL.format(year=year)
    dest = CACHE_DIR / f"full-{year}.csv.gz"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        remote = _remote_size(url)
    except FileNotFoundError:
        print(f"  {year}: not published on geo-dvf, skipped")
        return None

    if dest.exists() and not refresh and (remote is None or dest.stat().st_size == remote):
        return dest

    print(f"  {year}: downloading {url}")
    _download(url, dest)
    return dest


def build_stage_csv(con: duckdb.DuckDBPyConnection, src: Path, dept: str | None, out: Path) -> int:
    """Cleans one geo-dvf year and writes the rows to `out` (CSV, no header). Returns the row count."""
    con.execute("CREATE OR REPLACE TABLE arr_commune (code VARCHAR, commune VARCHAR)")
    con.executemany("INSERT INTO arr_commune VALUES (?, ?)", list(ARR_TO_COMMUNE.items()))

    dept_clause = "AND code_departement = ?" if dept else ""
    params = [dept] if dept else []
    src_sql = src.as_posix().replace("'", "''")

    con.execute(f"""
        CREATE OR REPLACE TABLE stage AS
        WITH src AS (
            SELECT DISTINCT
                id_mutation,
                CAST(date_mutation AS DATE)                                   AS mutation_date,
                TRY_CAST(valeur_fonciere AS DOUBLE)                           AS price,
                CASE WHEN adresse_nom_voie IS NULL THEN NULL
                     ELSE TRIM(CONCAT_WS(' ', adresse_numero, adresse_suffixe, adresse_nom_voie))
                END                                                           AS address,
                code_postal,
                code_commune,
                id_parcelle,
                lot1_numero, lot2_numero, lot3_numero, lot4_numero, lot5_numero,
                code_type_local,
                TRY_CAST(surface_reelle_bati AS DOUBLE)                       AS surface,
                nombre_pieces_principales,
                TRY_CAST(longitude AS DOUBLE)                                 AS lon,
                TRY_CAST(latitude AS DOUBLE)                                  AS lat
            FROM read_csv('{src_sql}', header=true, all_varchar=true, delim=',', quote='"', escape='"')
            WHERE nature_mutation IN ('Vente', 'Vente en l''état futur d''achèvement')
              AND code_type_local IN ('1', '2', '4')
              {dept_clause}
        ),
        kept AS (
            SELECT * FROM src
            WHERE price BETWEEN {MIN_PRICE} AND {MAX_PRICE}
              AND surface BETWEEN {MIN_SURFACE} AND {MAX_SURFACE}
        )
        SELECT
            id_mutation                                              AS mutation_id,
            mutation_date,
            price,
            address,
            code_postal                                              AS postal_code,
            COALESCE(a.commune, k.code_commune)                      AS insee_code,
            CASE code_type_local WHEN '1' THEN 'HOUSE'
                                 WHEN '2' THEN 'APARTMENT'
                                 ELSE 'COMMERCIAL' END               AS property_type,
            surface,
            CAST(COUNT(*) OVER (PARTITION BY id_mutation) AS INTEGER) AS lots_in_mutation,
            lon,
            lat
        FROM kept k
        LEFT JOIN arr_commune a ON a.code = k.code_commune
    """, params)

    n = con.execute("SELECT COUNT(*) FROM stage").fetchone()[0]
    out_sql = out.as_posix().replace("'", "''")
    con.execute(f"COPY stage TO '{out_sql}' (FORMAT CSV, HEADER false)")
    return n


STAGE_DDL = """
    CREATE TEMP TABLE _dvf_stage (
        mutation_id      text,
        mutation_date    date,
        price            double precision,
        address          text,
        postal_code      text,
        insee_code       text,
        property_type    text,
        surface          double precision,
        lots_in_mutation integer,
        lon              double precision,
        lat              double precision
    ) ON COMMIT DROP
"""

INSERT_SQL = """
    INSERT INTO transactions (
        id, city_id, mutation_id, lots_in_mutation, address, postal_code, geo_location,
        price, surface, property_type, mutation_date, source
    )
    SELECT
        gen_random_uuid()::text,
        c.id,
        s.mutation_id,
        s.lots_in_mutation,
        s.address,
        s.postal_code,
        CASE WHEN s.lon IS NOT NULL AND s.lat IS NOT NULL
             THEN ST_SetSRID(ST_MakePoint(s.lon, s.lat), 4326) END,
        s.price,
        s.surface,
        s.property_type::"TransactionPropertyType",
        s.mutation_date,
        'DVF'::"TransactionSource"
    FROM _dvf_stage s
    LEFT JOIN (
        -- `cities` may hold duplicate insee_code (legacy fixtures): keep one, prefer the one with DVF/estimated prices
        SELECT DISTINCT ON (insee_code) id, insee_code
        FROM cities
        ORDER BY insee_code, (price_data_source IS NOT NULL) DESC, created_at ASC
    ) c ON c.insee_code = s.insee_code
"""


def load_year(conn, year: int, csv_path: Path, dept: str | None) -> tuple[int, int, int]:
    """Replaces the DVF rows of `year` (and `dept`) in one transaction. Returns (deleted, inserted, no_city)."""
    cur = conn.cursor()
    try:
        cur.execute(STAGE_DDL)
        with open(csv_path, "r", encoding="utf-8") as f:
            cur.copy_expert("COPY _dvf_stage FROM STDIN WITH (FORMAT csv)", f)

        delete_sql = """
            DELETE FROM transactions
            WHERE source = 'DVF' AND mutation_date >= %s AND mutation_date < %s
        """
        params: list = [date(year, 1, 1), date(year + 1, 1, 1)]
        if dept:
            prefix = "20" if dept in ("2A", "2B") else dept
            delete_sql += """
              AND (city_id IN (SELECT id FROM cities WHERE department_code = %s)
                   OR (city_id IS NULL AND postal_code LIKE %s))
            """
            params += [dept, prefix + "%"]
        cur.execute(delete_sql, params)
        deleted = cur.rowcount

        cur.execute(INSERT_SQL)
        inserted = cur.rowcount

        cur.execute("""
            SELECT COUNT(*) FROM _dvf_stage s
            WHERE NOT EXISTS (SELECT 1 FROM cities c WHERE c.insee_code = s.insee_code)
        """)
        no_city = cur.fetchone()[0]
        conn.commit()
        return deleted, inserted, no_city
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def parse_years(value: str | None) -> list[int]:
    if not value:
        return list(range(FIRST_YEAR, date.today().year + 1))
    years: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            years.extend(range(int(a), int(b) + 1))
        else:
            years.append(int(part))
    return sorted(set(years))


def main() -> None:
    parser = argparse.ArgumentParser(description="Load DVF sales (geo-dvf) into the transactions table")
    parser.add_argument("--years", default=None, help="e.g. 2024,2025 or 2021-2025 (default: 2021 -> current year)")
    parser.add_argument("--dept", default=None, help="Restrict to one department code (e.g. 77)")
    parser.add_argument("--dry-run", action="store_true", help="Download + clean, no DB writes")
    parser.add_argument("--refresh", action="store_true", help="Force re-download of the geo-dvf files")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn and not args.dry_run:
        print("DATABASE_URL not set")
        sys.exit(1)

    years = parse_years(args.years)
    dept = args.dept.strip() if args.dept else None
    print(f"Years: {years}" + (f" | dept={dept}" if dept else ""))

    conn = psycopg2.connect(dsn) if not args.dry_run else None
    con = duckdb.connect(":memory:")
    con.execute("SET memory_limit='3GB'")

    total_inserted = total_no_city = total_rows = 0
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            for year in years:
                src = ensure_file(year, args.refresh)
                if src is None:
                    continue

                out = Path(tmpdir) / f"stage-{year}.csv"
                n = build_stage_csv(con, src, dept, out)
                total_rows += n
                print(f"  {year}: {n:,} locals kept")

                if args.dry_run:
                    continue

                deleted, inserted, no_city = load_year(conn, year, out, dept)
                total_inserted += inserted
                total_no_city += no_city
                print(f"  {year}: -{deleted:,} old / +{inserted:,} inserted ({no_city:,} without matching city)")

        if args.dry_run:
            print(f"\n[DRY RUN] {total_rows:,} rows would be inserted")
            return

        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*), COUNT(geo_location), COUNT(city_id), COUNT(DISTINCT mutation_id),
                   MIN(mutation_date), MAX(mutation_date)
            FROM transactions WHERE source = 'DVF'
        """)
        n, n_geo, n_city, n_mut, dmin, dmax = cur.fetchone()
        cur.close()
        print(f"\nDone. transactions[DVF]: {n:,} rows, {n_mut:,} mutations, {dmin} -> {dmax}")
        if n:
            print(f"  geolocated: {n_geo / n:.1%} | linked to a city: {n_city / n:.1%}")
    finally:
        con.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    main()
