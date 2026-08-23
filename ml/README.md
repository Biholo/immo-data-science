# ml/ — Rentium ML models (mémoire)

Implements the 4 models specified in [`docs/CONTEXTE_ML_MEMOIRE.md`](../docs/CONTEXTE_ML_MEMOIRE.md): clustering,
price/m² estimation + opportunity score, forecasting, quantile regression.

See `EXPERIMENTS_LOG.md` for every test that underperformed or failed outright
(with numbers, cause, and fix status) — written for the mémoire's
discussion/limites section, kept updated as new issues turn up.

See `PERFORMANCE.md` for a condensed dashboard of official metrics over time
(one row per meaningful run, per model) — "where do we stand", not "why".

Run `python -m ml.scripts.check_performance` any time for the live answer,
read straight off disk (`ml/artifacts/*/latest/run_metadata.json`) — not a
summary from memory, whatever's actually there right now.

Run `python -m ml.scripts.generate_charts` for every mémoire figure (16 PNGs:
silhouette curve, cluster profiles, réel-vs-prédit scatters, forecast fan
charts, coverage bars, feature importance, synthesis) — reads straight from
`ml/artifacts/*/latest/*.csv`, no retraining, output in `ml/artifacts/charts/`.

## Design

- **Separate from `pipeline/`, reuses it.** This folder never modifies
  `pipeline/` and never writes to the shared Postgres schema (`cities`,
  `series`, `timeseries` — owned by the Prisma project). It only runs
  read-only queries through the existing `pipeline.services.db.get_connection()`.
  All outputs (trained models, predictions, metrics) go to `ml/artifacts/`,
  a plain local folder — nothing here can conflict with the DVF pipeline or
  the production DB.
- **Mirrors the `pipeline/scripts` + `pipeline/services` pattern**: `ml/data/`
  and `ml/evaluation/` are the reusable library layer, `ml/models/*.py` are
  both importable (`run()` function) and directly runnable as CLI scripts.

```
ml/
  config.py                    feature lists, hyperparams, paths — single source of truth
  data/
    build_cross_sectional.py   commune-level feature table (Models 1, 2, 4)
    build_panel.py             commune x quarter panel with lags (Model 3)
    median_income.py           INSEE Filosofi loader (median_income feature)
    zonage_national.py         national zonage ABC loader (housing_zone, replaces IDF-only DB value)
    density_grid.py            INSEE density typology loader (clustering_hierarchical.py only)
    build_transactions.py      individual DVF transactions, not commune-aggregated (price_hedonic.py)
    rent.py                    ANIL/DHUP Carte des loyers loader (rent_appt_*/rent_maison, rendement_brut)
    property_tax.py            DGFiP Fiscalité locale loader (taux_foncier_pct, used by ml/finance/cashflow.py)
  finance/                     deterministic financial formulas — CONTEXTE_ML_MEMOIRE.md §6, NOT ML
    cashflow.py                mensualite_pret, rendement_brut/net, taxe_fonciere_annuelle, cash_flow_mensuel...
    profiles.py                5 InvestorProfile presets (apport/taux/duree/assurance) for cash-flow sensitivity
  evaluation/
    metrics.py                 MAE / RMSE / R2 / pinball loss
    artifacts.py                run folder + run_metadata.json conventions
  models/
    clustering.py               Model 1 — K-Means (--weighted, --k)
    clustering_gmm.py             comparison: Gaussian Mixture (soft membership)
    clustering_hdbscan.py           comparison: HDBSCAN (density-based — see EXPERIMENTS_LOG #8)
    clustering_hierarchical.py        comparison: INSEE density presplit + K-Means per group
    price_model.py               Model 2 — Gradient Boosting price/m2 + opportunity score (--tune)
    price_model_lgbm.py            comparison: LightGBM, native categorical (see EXPERIMENTS_LOG #6)
    forecasting.py                Model 3 — 4 direct per-horizon GB models + growing Q10/Q90 interval
    quantile.py                    Model 4 — quantile regression + conformal calibration (optional)
    price_hedonic.py                extension: transaction-level LightGBM (not commune-averaged), see EXPERIMENTS_LOG
    yield_model.py                   extension: rendement_brut (%) instead of price — "does it pay off", not just "is it cheap"
  scripts/
    run_all.py                  runs the 4 mémoire models in the recommended order
    generate_charts.py          16 mémoire figures from saved artifacts (no retrain, no DB)
    generate_recommendations.py combines all models into a Top-N ranking, per-commune fiches, maps
    simulate_cashflow.py        Top 20 x 5 investor profiles -> monthly cash-flow (deterministic, ml/finance/)
    analyze_yield_geography.py  national rendement maps (brut/net, IDW interpolated + bubble maps), see EXPERIMENTS_LOG
    generate_memoire_extra_charts.py  IDF/77 zoom maps, national cash-flow map, DVF bug before/after,
                                       price provenance map, opportunity top/bottom, pipeline diagram
    collect_memoire_visuals.py  copies the chosen mémoire visuals into memoire/redaction/latest/
                                 (repo root, outside ml/artifacts/ — run last, after the 3 scripts above)
    check_performance.py        live pass/fail summary read from run_metadata.json
  artifacts/                    gitignored — regenerated by running the scripts
```

The `clustering_*` and `price_model_lgbm.py` files are comparison scripts, not
mémoire deliverables — CONTEXTE_ML_MEMOIRE.md mandates flat K-Means for
Modèle 1; `price_model.py` (sklearn GB) is what `run_all.py` and every
`EXPERIMENTS_LOG.md` entry are built around. They're kept because their
results are worth citing in the mémoire's methodology/discussion section
("we also tried X, here's why we didn't switch").

## Setup

```bash
pip install -r requirements.txt -r requirements-ml.txt
# DATABASE_URL / SSH tunnel: same as the main pipeline, see README.md "Installation"
```

## Run

```bash
# Everything, in the recommended order (clustering -> price -> forecasting -> quantile)
python -m ml.scripts.run_all [--dept 77] [--weighted] [--tune] [--skip-quantile] [--dry-run]

# Or one model at a time
python -m ml.models.clustering [--dept 77] [--k 6] [--weighted] [--dry-run]
python -m ml.models.price_model [--dept 77] [--tune] [--dry-run]
python -m ml.models.forecasting [--dept 77] [--dry-run]
python -m ml.models.quantile [--dept 77] [--dry-run]

# Comparison scripts (not mémoire deliverables, see EXPERIMENTS_LOG.md)
python -m ml.models.clustering_gmm [--dept 77] [--k 3] [--dry-run]
python -m ml.models.clustering_hdbscan [--dept 77] [--min-cluster-size 50] [--dry-run]
python -m ml.models.clustering_hierarchical [--dept 77] [--k-per-group 2] [--dry-run]
python -m ml.models.price_model_lgbm [--dept 77] [--dry-run]

# Extensions (not in CONTEXTE_ML_MEMOIRE.md's 4 mandated models, but built to answer
# "how much does it actually yield / can I afford it", see EXPERIMENTS_LOG.md)
python -m ml.models.price_hedonic [--full] [--dry-run]     # transaction-level, not commune-averaged
python -m ml.models.yield_model [--dept 77] [--tune] [--dry-run]   # target=rendement_brut instead of price
python -m ml.scripts.generate_recommendations [--dry-run]  # Top 20, fiches, maps — run after price/yield/clustering
python -m ml.scripts.simulate_cashflow [--dry-run]          # Top 20 x 5 profils bancaires -> cash-flow réel
python -m ml.scripts.analyze_yield_geography [--dry-run]    # cartes nationales rendement brut/net (IDW + bulles)
```

`--weighted` (clustering) reweights features by ANOVA F-stat before the final
fit — better silhouette AND external validation on the base feature set, but
made things worse once `median_income` was added (see EXPERIMENTS_LOG.md §Modèle 1
#9) — check both before picking one for the mémoire. `--tune` (price_model)
runs RandomizedSearchCV instead of the fixed `GB_DEFAULT_PARAMS` — slower,
real measured gain (see EXPERIMENTS_LOG.md §Modèle 2 #4/#5).

`price_model.py` and `quantile.py` automatically pick up
`ml/artifacts/clustering/latest/cluster_assignments.csv` if present, and use
`cluster_id` as an extra feature (Model 1 -> Model 2/4 handoff described in
[`docs/ROADMAP_IA.md`](../docs/ROADMAP_IA.md)). Run clustering first if you want that.

Every run writes to `ml/artifacts/<model>/<timestamp>/` and mirrors the same
files into `ml/artifacts/<model>/latest/` (stable path for citing files in the
mémoire). Each run folder always has a `run_metadata.json` covering the
"Artefacts à sauvegarder" checklist in `CONTEXTE_ML_MEMOIRE.md` §10: features,
hyperparameters, split sizes, baseline vs model metrics, training time.

## Known data limitations (see EXPERIMENTS_LOG.md for the full, numbered detail)

These are inherent to the current dataset, not bugs in this pipeline — worth
stating explicitly in the mémoire rather than treated as things to silently fix:

- **Socio-demo staleness**: INSEE annual series (`unemployment_rate`,
  `owner_rate`, `vacancy_rate`, `social_housing_rate`, `secondary_residence_rate`,
  `aging_index`) are capped at millésime 2022 (INSEE publication lag).
  `build_panel.py` carries these forward as constants across all quarters,
  including the 2023-2025 test/forecast period. `cities.population` is the
  one exception — sourced live from geo.api.gouv.fr, not stuck at 2022.
  `median_income` (Filosofi) is a one-off 2021 snapshot, same caveat applies.
- **`price_growth_3y` excluded from `CLUSTERING_FEATURES` and `PRICE_MODEL_FEATURES`**
  (see `ml/config.py` comments) — two different reasons: leakage for Model 2
  (it's derived from the same price used as target), and near-total missingness
  for Model 1 (only 9% of communes have the 12 quarters of DVF history it needs).
- **`housing_zone` (zonage ABC) now covers all of France** (34875/34969
  communes, `ml/data/zonage_national.py`) — was IDF-only via the DB column
  until this was added; the DB column itself is still IDF-only, this is an
  ml/-side override, not a DB fix.
- **No POI density / transport accessibility** — never ingested anywhere in
  the repo. Marked optional in CONTEXTE_ML_MEMOIRE.md §2, so `CLUSTERING_FEATURES`
  and `PRICE_MODEL_FEATURES` don't include it. `dist_nearest_major_city_km`
  (haversine to the nearest of the top-50 communes by population) covers part
  of the same idea for Model 2, computed with zero new data.
- **`price_data_source` granularity**: the `estimated` vs `dvf` flag on
  `cities` is a single snapshot, not per-quarter. `build_cross_sectional.py`
  uses it to exclude IDW-interpolated communes from Model 2/4 training.
  `build_panel.py` doesn't need an equivalent per-row flag: the quarterly
  `series`/`timeseries` rows it reads are never touched by
  `pipeline/scripts/interpolate.py`, so every panel row is already a real
  DVF observation by construction.
- **`median_income` weakened Model 1's clustering** despite raising silhouette
  (0.374→0.547) — external validation (price correlation) got worse (F=291→137).
  The ANOVA reweighting essentially zeroed it out (weight 0.0002) in favor of
  population/transaction_volume. Helped Model 2 enormously (R²=0.72→0.81) —
  income matters for price, not for market-typology separation. See
  EXPERIMENTS_LOG.md §Modèle 1 #9, a genuine open question on which clustering
  version (with or without `median_income`) to present in the mémoire.
