"""
Series definitions. Each entry maps to one Serie row per geographic entity.

Fields:
  name          → SerieName enum value
  source        → SerieSource enum value
  unit          → display unit string
  frequency     → SerieFrequency enum value
  chart_type    → SerieChartType enum value
  view          → DuckDB view to query (dvf | dvf_mutation_prices | dvf_mutation_terrain)
  nature_clause → SQL fragment for WHERE nature_mutation ...
  where         → additional SQL WHERE conditions
  select        → aggregate SQL expression → becomes timeserie.value
  min_n         → skip group if fewer distinct mutations
  special       → optional: 'vefa_share' for custom query logic

Why dvf_mutation_prices?
  DVF raw repeats valeur_fonciere on every local row of a mutation.
  A mutation selling 3 apartments at 900k has 3 rows each showing 900k.
  price/m² must be computed on (valeur_fonciere / SUM(surface_bati)) at mutation level.
  dvf_mutation_prices does this aggregation and exposes:
    - nb_locaux_this_group: count of locaux with same (type_local, nb_pieces) in mutation
    - total_locaux:         total count of all locaux in mutation
    - n_distinct_types:     count of distinct type_local values in mutation
"""

VENTE_NATURE = "nature_mutation IN ('Vente', 'Vente en l''état futur d''achèvement')"
TERRAIN_NATURE = "nature_mutation = 'Vente terrain à bâtir'"

P50_PRICE_SQM = "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY valeur_fonciere / surface_bati)"
P50_SURFACE = "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY surface_bati)"
P50_TERRAIN = "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY valeur_fonciere / surface_terrain)"

# For price_sqm_all/appt/house: allow multi-local mutations but require same
# type_local AND same nb_pieces across every local in the mutation. Without
# total_locaux = nb_locaux_this_group, a mutation mixing room counts (e.g. an
# immeuble sold as 2xT2 + 1xT3 in one sale) gets split by dvf_mutation_prices
# into one subgroup per nb_pieces, and valeur_fonciere (the TOTAL mutation
# price, repeated on every DVF row) gets counted in full for EACH subgroup —
# duplicating the price. Measured on the national cache: 7.15% of rows
# passing this filter were affected, median price/sqm 3333€ vs 2515€ for
# clean single-group mutations (+32% overstatement on those rows). See
# ml/EXPERIMENTS_LOG.md "Modèle rendement" #0.
BASE_PRICE_MULTI = (
    "n_distinct_types = 1 "
    "AND total_locaux = nb_locaux_this_group "
    "AND surface_bati BETWEEN 9 AND 2000 "
    "AND valeur_fonciere > 0 "
    "AND valeur_fonciere / surface_bati BETWEEN 500 AND 30000"
)

# For price_sqm_t1/t2/t3/t4: require ALL locaux in mutation to be same type+pieces
# (so valeur_fonciere / SUM(surface) is unambiguous per type)
BASE_PRICE_MONO = (
    "total_locaux = nb_locaux_this_group "
    "AND surface_bati BETWEEN 9 AND 500 "
    "AND valeur_fonciere > 0 "
    "AND valeur_fonciere / surface_bati BETWEEN 500 AND 30000"
)

SERIES: list[dict] = [
    # ── Prix au m² — source: dvf_mutation_prices ─────────────────────────
    # Multi-local mutations allowed for all/appt/house (same type_local required)
    {
        "name": "price_sqm_all",
        "source": "DVF",
        "unit": "€/m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "view": "dvf_mutation_prices",
        "nature_clause": VENTE_NATURE,
        "where": f"type_local IN (1, 2) AND {BASE_PRICE_MULTI}",
        "select": P50_PRICE_SQM,
        "min_n": 10,
    },
    {
        "name": "price_sqm_appt",
        "source": "DVF",
        "unit": "€/m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "view": "dvf_mutation_prices",
        "nature_clause": VENTE_NATURE,
        "where": f"type_local = 2 AND {BASE_PRICE_MULTI}",
        "select": P50_PRICE_SQM,
        "min_n": 10,
    },
    {
        "name": "price_sqm_house",
        "source": "DVF",
        "unit": "€/m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "view": "dvf_mutation_prices",
        "nature_clause": VENTE_NATURE,
        "where": f"type_local = 1 AND {BASE_PRICE_MULTI}",
        "select": P50_PRICE_SQM,
        "min_n": 10,
    },
    # Single-type-and-pieces mutations only for T1-T4 (nb_pieces must be unambiguous)
    {
        "name": "price_sqm_t1",
        "source": "DVF",
        "unit": "€/m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "view": "dvf_mutation_prices",
        "nature_clause": VENTE_NATURE,
        "where": f"type_local = 2 AND nb_pieces IN (0, 1) AND {BASE_PRICE_MONO}",
        "select": P50_PRICE_SQM,
        "min_n": 10,
    },
    {
        "name": "price_sqm_t2",
        "source": "DVF",
        "unit": "€/m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "view": "dvf_mutation_prices",
        "nature_clause": VENTE_NATURE,
        "where": f"type_local = 2 AND nb_pieces = 2 AND {BASE_PRICE_MONO}",
        "select": P50_PRICE_SQM,
        "min_n": 10,
    },
    {
        "name": "price_sqm_t3",
        "source": "DVF",
        "unit": "€/m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "view": "dvf_mutation_prices",
        "nature_clause": VENTE_NATURE,
        "where": f"type_local = 2 AND nb_pieces = 3 AND {BASE_PRICE_MONO}",
        "select": P50_PRICE_SQM,
        "min_n": 10,
    },
    {
        "name": "price_sqm_t4",
        "source": "DVF",
        "unit": "€/m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "view": "dvf_mutation_prices",
        "nature_clause": VENTE_NATURE,
        "where": f"type_local = 2 AND nb_pieces >= 4 AND {BASE_PRICE_MONO}",
        "select": P50_PRICE_SQM,
        "min_n": 10,
    },
    # ── Surface médiane — source: dvf (raw rows) ─────────────────────────
    # surface_reelle_bati per row = individual local's surface → no dedup needed
    {
        "name": "surface_median_t1",
        "source": "DVF",
        "unit": "m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "nature_clause": VENTE_NATURE,
        "where": "type_local = 2 AND nb_pieces IN (0, 1) AND surface_bati BETWEEN 9 AND 500",
        "select": P50_SURFACE,
        "min_n": 10,
    },
    {
        "name": "surface_median_t2",
        "source": "DVF",
        "unit": "m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "nature_clause": VENTE_NATURE,
        "where": "type_local = 2 AND nb_pieces = 2 AND surface_bati BETWEEN 9 AND 500",
        "select": P50_SURFACE,
        "min_n": 10,
    },
    {
        "name": "surface_median_t3",
        "source": "DVF",
        "unit": "m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "nature_clause": VENTE_NATURE,
        "where": "type_local = 2 AND nb_pieces = 3 AND surface_bati BETWEEN 9 AND 500",
        "select": P50_SURFACE,
        "min_n": 10,
    },
    {
        "name": "surface_median_t4",
        "source": "DVF",
        "unit": "m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "nature_clause": VENTE_NATURE,
        "where": "type_local = 2 AND nb_pieces >= 4 AND surface_bati BETWEEN 9 AND 500",
        "select": P50_SURFACE,
        "min_n": 10,
    },
    # ── Volume & marché ──────────────────────────────────────────────────
    # transaction_volume: COUNT(DISTINCT mutation_id) already correct on raw view
    {
        "name": "transaction_volume",
        "source": "DVF",
        "unit": "transactions",
        "frequency": "QUARTERLY",
        "chart_type": "BAR",
        "nature_clause": VENTE_NATURE,
        "where": "type_local IN (1, 2)",
        "select": "COUNT(DISTINCT mutation_id)",
        "min_n": 1,
    },
    {
        "name": "vefa_share",
        "source": "DVF",
        "unit": "%",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "special": "vefa_share",
        "where": "",
        "min_n": 10,
    },
    # land_price_sqm: dvf_mutation_terrain aggregates SUM(surface_terrain) per mutation
    {
        "name": "land_price_sqm",
        "source": "DVF",
        "unit": "€/m²",
        "frequency": "QUARTERLY",
        "chart_type": "LINE",
        "view": "dvf_mutation_terrain",
        "nature_clause": TERRAIN_NATURE,
        "where": "surface_terrain > 0 AND valeur_fonciere > 0",
        "select": P50_TERRAIN,
        "min_n": 5,
    },
]
