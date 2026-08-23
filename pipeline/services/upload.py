"""
PostgreSQL upload: upserts Serie rows then bulk-inserts Timeserie rows.

Assumptions about your DB schema (adjust constants below if different):
  - City table has column "inseeCode" (5-char INSEE commune code)
  - AdministrativeZone table has column "code" (dept code like "75", region code like "IDF")
  - Country table has column "isoCode" (e.g. "FR")
  - All tables use lowercase snake_case in Postgres (Prisma @map convention)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as PgConnection

# ── Adjust these to match your actual column names ───────────────────────────
# Actual Postgres column names (after Prisma @map transforms camelCase → snake_case)
CITY_CODE_COL = "insee_code"
ZONE_CODE_COL = "code"
COUNTRY_ISO_COL = "iso_code"  # adjust if different
# ─────────────────────────────────────────────────────────────────────────────

from .series import SERIES


def connect(dsn: str) -> PgConnection:
    return psycopg2.connect(dsn)


def _fetch_geo_ids(cur, geo_level: str, geo_codes: set[str]) -> dict[str, str]:
    """Returns {geo_code: db_id}."""
    if not geo_codes:
        return {}

    codes = list(geo_codes)

    if geo_level == "city":
        cur.execute(
            f"SELECT {CITY_CODE_COL}, id FROM cities WHERE {CITY_CODE_COL} = ANY(%s)",
            (codes,),
        )
    elif geo_level == "department":
        cur.execute(
            f"SELECT {ZONE_CODE_COL}, id FROM administrative_zones"
            f" WHERE {ZONE_CODE_COL} = ANY(%s) AND type = 'department'",
            (codes,),
        )
    elif geo_level == "region":
        cur.execute(
            f"SELECT {ZONE_CODE_COL}, id FROM administrative_zones"
            f" WHERE {ZONE_CODE_COL} = ANY(%s) AND type = 'region'",
            (codes,),
        )
    elif geo_level == "country":
        cur.execute(
            f"SELECT {COUNTRY_ISO_COL}, id FROM countries WHERE {COUNTRY_ISO_COL} = ANY(%s)",
            (codes,),
        )
    else:
        return {}

    return {row[0]: row[1] for row in cur.fetchall()}


def _serie_geo_kwargs(geo_level: str, geo_id: str) -> dict[str, Any]:
    if geo_level == "city":
        return {"city_id": geo_id, "administrative_zone_id": None, "country_id": None}
    elif geo_level in ("department", "region"):
        return {"city_id": None, "administrative_zone_id": geo_id, "country_id": None}
    elif geo_level == "country":
        return {"city_id": None, "administrative_zone_id": None, "country_id": geo_id}
    return {}


def upsert_series_and_timeseries(
    conn: PgConnection,
    serie_def: dict,
    geo_level: str,
    rows: list[tuple],  # [(geo_code, period, value)]
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Returns {"series_upserted": N, "timeseries_inserted": N}.
    """
    if not rows:
        return {"series_upserted": 0, "timeseries_inserted": 0}

    geo_codes = {r[0] for r in rows}

    with conn.cursor() as cur:
        geo_id_map = _fetch_geo_ids(cur, geo_level, geo_codes)

    # Group rows by geo_code
    by_geo: dict[str, list[tuple]] = {}
    for geo_code, period, value in rows:
        if geo_code not in geo_id_map:
            continue  # no matching entity in DB
        by_geo.setdefault(geo_code, []).append((period, value))

    series_upserted = 0
    timeseries_inserted = 0

    with conn.cursor() as cur:
        for geo_code, ts_rows in by_geo.items():
            geo_id = geo_id_map[geo_code]
            geo_kwargs = _serie_geo_kwargs(geo_level, geo_id)

            city_id   = geo_kwargs.get("city_id")
            az_id     = geo_kwargs.get("administrative_zone_id")
            ctry_id   = geo_kwargs.get("country_id")

            if not dry_run:
                # SELECT-then-INSERT/UPDATE — avoids ON CONFLICT constraint dependency
                cur.execute(
                    """
                    SELECT id FROM series
                    WHERE name = %s
                    AND city_id IS NOT DISTINCT FROM %s
                    AND administrative_zone_id IS NOT DISTINCT FROM %s
                    AND country_id IS NOT DISTINCT FROM %s
                    """,
                    (serie_def["name"], city_id, az_id, ctry_id),
                )
                row = cur.fetchone()
                if row:
                    serie_id = row[0]
                    cur.execute(
                        "UPDATE series SET updated_at = NOW() WHERE id = %s",
                        (serie_id,),
                    )
                else:
                    serie_id = str(uuid.uuid4())
                    cur.execute(
                        """
                        INSERT INTO series (id, name, source, frequency, unit, chart_type,
                                            city_id, administrative_zone_id, country_id,
                                            created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        """,
                        (
                            serie_id,
                            serie_def["name"],
                            serie_def["source"],
                            serie_def["frequency"],
                            serie_def["unit"],
                            serie_def["chart_type"],
                            city_id, az_id, ctry_id,
                        ),
                    )
            else:
                serie_id = str(uuid.uuid4())

            series_upserted += 1

            if not dry_run:
                # Fetch existing timeseries for this serie (dimension IS NULL)
                cur.execute(
                    "SELECT timestamp::date, id FROM timeseries WHERE serie_id = %s AND dimension IS NULL",
                    (serie_id,),
                )
                existing_ts = {row[0].isoformat(): row[1] for row in cur.fetchall()}

                to_insert = [
                    (str(uuid.uuid4()), period, float(value), None, serie_id)
                    for period, value in ts_rows
                    if period not in existing_ts
                ]
                to_update = [
                    (float(value), existing_ts[period])
                    for period, value in ts_rows
                    if period in existing_ts
                ]

                if to_insert:
                    psycopg2.extras.execute_values(
                        cur,
                        """
                        INSERT INTO timeseries (id, timestamp, value, dimension, serie_id,
                                               created_at, updated_at)
                        VALUES %s
                        """,
                        to_insert,
                        template="(%s, %s, %s, %s, %s, NOW(), NOW())",
                    )
                if to_update:
                    psycopg2.extras.execute_values(
                        cur,
                        """
                        UPDATE timeseries SET value = data.v, updated_at = NOW()
                        FROM (VALUES %s) AS data(v, id)
                        WHERE timeseries.id = data.id
                        """,
                        to_update,
                        template="(%s::double precision, %s)",
                    )
                timeseries_inserted += len(to_insert) + len(to_update)
            else:
                timeseries_inserted += len(ts_rows)

    if not dry_run:
        conn.commit()

    return {"series_upserted": series_upserted, "timeseries_inserted": timeseries_inserted}


def run_upload(
    conn: PgConnection,
    all_results: dict[tuple[str, str], list[tuple]],
    dry_run: bool = False,
) -> None:
    serie_map = {s["name"]: s for s in SERIES}
    total_series = 0
    total_ts = 0

    for (serie_name, geo_level), rows in all_results.items():
        serie_def = serie_map[serie_name]
        label = f"{serie_name} @ {geo_level}"
        print(f"  Uploading {label} ({len(rows)} rows)...", end=" ", flush=True)

        stats = upsert_series_and_timeseries(conn, serie_def, geo_level, rows, dry_run)
        total_series += stats["series_upserted"]
        total_ts += stats["timeseries_inserted"]
        print(f"done ({stats['series_upserted']} series, {stats['timeseries_inserted']} ts)")

    print(f"\nTotal: {total_series} series upserted, {total_ts} timeseries inserted")
    if dry_run:
        print("(DRY RUN — no DB writes)")
