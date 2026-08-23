"""
Shared config for the ml/ package: paths, feature lists, model defaults.

This package is a *consumer* of pipeline/ — it only ever runs read-only
queries against the existing Postgres DB (via pipeline.services.db) and
DuckDB cache. It never writes to `cities` / `series` / `timeseries`.
All outputs (models, predictions, metrics) land in ml/artifacts/, so this
folder can be developed and re-run freely without touching the shared
schema owned by the Prisma project.
"""

from __future__ import annotations

from pathlib import Path

ML_ROOT = Path(__file__).parent
ARTIFACTS_DIR = ML_ROOT / "artifacts"

RANDOM_STATE = 42

# ── Socio-demo series (annual, INSEE, EAV `series`/`timeseries` table) ──────
# Exact SerieName enum values used elsewhere in pipeline/services/series.py
# and pipeline/scripts/seed_*_series.py — keep in sync if those change.
SOCIO_SERIES = [
    "unemployment_rate",
    "owner_rate",
    "vacancy_rate",
    "social_housing_rate",
    "secondary_residence_rate",
    "aging_index",
    "population",          # also exists as a `cities.population` snapshot column (preferred, more recent)
    "active_population",
    "student_rate",
    "retiree_rate",
]

# ── DVF series (quarterly, computed by pipeline/services/dvf.py) ────────────
PRICE_SERIE = "price_sqm_all"
VOLUME_SERIE = "transaction_volume"

# ── Model 1 (clustering) + Model 2/4 (price) feature set ────────────────────
# Per CONTEXTE_ML_MEMOIRE.md §2: do NOT include price/m² itself in clustering
# features (circularity if clustering is later used to contextualize price).
#
# price_growth_3y deliberately excluded (unlike the doc's feature list): needs
# 12 quarters of DVF history, so only 3281/34969 communes (9%) have it at
# national scale — checked empirically, it's by far the biggest single-feature
# dropout (every other feature is ~99% complete). Dropping it takes clustering
# from n=3281 to n=~34845, a ~10x usable-sample increase, at the cost of losing
# one weak-signal feature. Worth stating this tradeoff explicitly in the mémoire.
CLUSTERING_FEATURES = [
    "population",
    "unemployment_rate",
    "owner_rate",
    "vacancy_rate",
    "social_housing_rate",
    "aging_index",
    "secondary_residence_rate",
    "transaction_volume",
    "dist_nearest_major_city_km",  # see build_cross_sectional.py — added to let a "banlieue" cluster emerge (close to a major city but not itself dense), previously indistinguishable from an isolated small town on the other 8 features alone
]
# median_income deliberately NOT here (unlike PRICE_MODEL_FEATURES): tested in
# EXPERIMENTS_LOG.md §Modèle 1 #9 — ANOVA weight ~0.0002 (quasi nul), barely
# differs between clusters (23077€ vs 23261€, 0.8%), and including it dragged
# external validation down (F=291→137) even though silhouette went up — a
# textbook case of silhouette being misleading. Real predictive power for
# Modèle 2 (price), near-zero separating power for typologies.

# population and transaction_volume are heavily right-skewed (Paris vs a
# hamlet of 80 people); dist_nearest_major_city_km has a long DOM-TOM tail
# (up to ~15000km) — log1p before StandardScaler so KMeans' euclidean
# distance isn't dominated by a handful of extreme values.
CLUSTERING_LOG_FEATURES = ["population", "transaction_volume", "dist_nearest_major_city_km"]

# Per CONTEXTE_ML_MEMOIRE.md §3: price_growth_3y is EXCLUDED here (not in
# clustering) because it is algebraically derived from the same latest price
# that becomes the regression target (denormalize.py: growth = latest/12Q_ago - 1)
# → direct leakage, not just correlation. Clustering doesn't have this problem
# since price is not its target.
PRICE_MODEL_FEATURES = [
    "population",
    "unemployment_rate",
    "owner_rate",
    "vacancy_rate",
    "social_housing_rate",
    "aging_index",
    "secondary_residence_rate",
    "transaction_volume",
    "median_income",              # INSEE Filosofi 2021 — see CLUSTERING_FEATURES comment
    "dist_nearest_major_city_km",  # computed in build_cross_sectional.py, no external file — captures periurban gradient department_code alone can't (e.g. Melun vs Fontainebleau, same dept)
]
PRICE_MODEL_CATEGORICAL = ["housing_zone", "department_code"]
# housing_zone: backfilled nationally via ml/data/zonage_national.py (was IDF-only before).
# department_code: real estate price is heavily geography-driven (Paris vs
# Creuse) and none of the socio-demo features above capture that directly —
# one-hot department is a cheap, high-leverage way to let the model learn a
# geographic price floor before layering socio-demo effects on top.

TARGET_COL = "median_price_per_sqm"

# ── Model 3 (forecasting) ────────────────────────────────────────────────────
LAGS = [1, 2, 4]
FORECAST_TEST_YEAR = 2025  # train < 2025, test = 2025 (chronological, no shuffling)

GB_DEFAULT_PARAMS = dict(
    n_estimators=300,
    max_depth=3,
    learning_rate=0.05,
    subsample=0.8,
    random_state=RANDOM_STATE,
)
