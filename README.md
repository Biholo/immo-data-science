# immo-data-science

Pipeline de données et modèles IA pour l'investissement immobilier résidentiel en France —
projet support du mémoire *« L'intelligence artificielle appliquée à l'investissement
immobilier »* (Rentium).

Calcule des séries temporelles immobilières à partir des données DVF (DGFiP) et INSEE,
les stocke en PostgreSQL, et entraîne les modèles de scoring/forecasting/clustering
utilisés par la plateforme Rentium.

**Auteur** : Kilian Trouet

## Vue d'ensemble

- **14 séries** × **4 niveaux géo** (commune, département, région, pays)
- **2021–2025**, fréquence trimestrielle
- ~35 000 communes seedées depuis `geo.api.gouv.fr`
- Données socio-démographiques INSEE RP 2022 (étudiants, retraités, chômeurs)
- Interpolation spatiale IDW pour les communes sans données DVF

---

## Structure

```
immo-data-science/
├── pipeline/
│   ├── services/          # logique métier réutilisable
│   │   ├── db.py          # connexion PostgreSQL centralisée
│   │   ├── geo.py         # mappings département → région + arrondissements → commune (codes INSEE)
│   │   ├── series.py      # définitions des 14 séries
│   │   ├── dvf.py         # moteur DuckDB (calcul des séries)
│   │   └── upload.py      # upload PostgreSQL (upsert série + timeseries)
│   └── scripts/           # points d'entrée CLI
│       ├── run_dvf.py     # pipeline principal DVF
│       ├── denormalize.py # snapshot fields sur table cities
│       ├── interpolate.py # interpolation IDW villes sans data
│       ├── audit.py       # visualisation qualité données (terminal)
│       ├── seed_cities.py # seed 35 000 communes
│       ├── seed_rp.py     # seed INSEE RP 2022 (étudiants/retraités/chômeurs)
│       ├── seed_students.py # seed ESR étudiants uniquement (optionnel)
│       ├── seed_pop_series.py        # série population + aging_index
│       ├── seed_logement_series.py   # séries vacancy_rate/owner_rate/social_housing_rate/secondary_residence_rate
│       ├── seed_rp_series.py         # série unemployment_rate
│       ├── seed_employment_series.py # série active_population (ACT1564)
│       ├── seed_housing_zone.py      # cities.housing_zone + cities.high_demand_zone
│       └── seed_dashboard_fields.py  # cities.tenant_rate / demographic_growth_5y / employment_growth
├── ml/                    # modèles IA du mémoire (clustering, prix, forecasting, quantile...) — voir ml/README.md
├── scripts/               # wrappers PowerShell — orchestration multi-étapes (seed, ré-entraînement, reporting)
│   ├── seed_national.ps1        # seed complet (communes + séries socio-démo + DVF)
│   ├── seed_resume.ps1          # reprise seed_national après crash
│   ├── run_ml.ps1               # les 4 modèles mémoire (ml.scripts.run_all)
│   ├── ml_reseed_dvf.ps1         # (1/4) reseed DB après fix pipeline
│   ├── ml_run_models.ps1         # (2/4) réentraîne les modèles
│   ├── ml_run_deliverables.ps1   # (3/4) Top 20, cash-flow, cartes rendement
│   ├── ml_run_reporting.ps1      # (4/4) figures mémoire + recap pass/fail
│   └── ml_run_full_pipeline.ps1  # enchaîne 1→4
├── memoire/redaction/latest/  # visuels retenus pour la rédaction (régénéré par ml/scripts/collect_memoire_visuals.py)
├── dvf-raw/               # fichiers DVF bruts .txt — VIDE dans ce repo, voir "Sources de données"
├── csv/                   # fichiers source INSEE/DHUP/ANIL/DGFiP — VIDE dans ce repo, voir "Sources de données"
├── dvf_cache.duckdb       # cache DuckDB (non versionné)
├── .env                   # DATABASE_URL (non versionné)
└── requirements.txt
```

---

## Sources de données

`dvf-raw/` et `csv/` sont vides dans ce repo (fichiers sources trop volumineux — 3,8 Go
cumulés — et déjà librement téléchargeables). Structure des dossiers conservée
(`.gitkeep`) : à toi de déposer chaque fichier au bon endroit avant de lancer le pipeline.

| Source | Lien de téléchargement | Emplacement attendu | Consommé par |
|---|---|---|---|
| DVF (Demandes de valeurs foncières), DGFiP | [cadastre.data.gouv.fr/dvf](https://cadastre.data.gouv.fr/dvf) | `dvf-raw/ValeursFoncieres-{2021..2025}.txt` | `pipeline/services/dvf.py` |
| INSEE IC — Activité des résidents 2022 | [insee.fr/fr/statistiques/8647006](https://www.insee.fr/fr/statistiques/8647006) | `csv/base-ic-activite-residents-2022.xlsx` + historique 2017-2021 dans `csv/base-ic-activite-residents/*.CSV` | `pipeline/scripts/seed_rp.py`, `seed_rp_series.py`, `seed_employment_series.py` |
| INSEE IC — Logement 2017-2022 | [insee.fr/fr/statistiques/8647012](https://www.insee.fr/fr/statistiques/8647012) | `csv/base-ic-logement/*.CSV` | `pipeline/scripts/seed_logement_series.py` |
| INSEE IC — Évolution structure population 2017-2022 | [insee.fr/fr/information/2383389](https://www.insee.fr/fr/information/2383389) | `csv/base-ic-evol-struct-pop/*.CSV` | `pipeline/scripts/seed_pop_series.py` |
| geo.api.gouv.fr (API live, pas de fichier) | [geo.api.gouv.fr/communes](https://geo.api.gouv.fr/communes) | — appelée directement au run | `pipeline/scripts/seed_cities.py` |
| DHUP — Zonage ABC (Île-de-France uniquement) | [data.gouv.fr — zonage ABC](https://www.data.gouv.fr/datasets/logement-liste-des-communes-selon-le-zonage-abc) | `csv/logement-liste-des-communes-selon-le-zonage-abc.csv` | `pipeline/scripts/seed_housing_zone.py` |
| Zonage ABC national (remplace la version IDF-only côté `ml/`) | [data.gouv.fr — zonage ABC national](https://www.data.gouv.fr/datasets/liste-des-communes-selon-le-zonage-abc) | `csv/zonage-abc-national.csv` | `ml/data/zonage_national.py` |
| ESR — effectifs étudiants (legacy, supplanté par INSEE RP) | [data.enseignementsup-recherche.gouv.fr](https://data.enseignementsup-recherche.gouv.fr) | `csv/fr-esr-atlas_regional-effectifs-d-etudiants-inscrits_agregeables.csv` | `pipeline/scripts/seed_students.py` (optionnel) |
| INSEE Filosofi 2021 — revenus, pauvreté, niveau de vie | [insee.fr/fr/statistiques/7756729](https://www.insee.fr/fr/statistiques/7756729) (`base-cc-filosofi-2021-geo2025_csv.zip`) | `csv/DS_FILOSOFI_CC_data.csv` | `ml/data/median_income.py` |
| Carte des loyers 2025, ANIL/DHUP | [data.gouv.fr — Carte des loyers](https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025) | `csv/loyers/*.csv` (4 fichiers, noms exacts dans `ml/data/rent.py`) | `ml/data/rent.py` |
| DGFiP — Fiscalité locale des particuliers | [data.gouv.fr — fiscalité locale](https://www.data.gouv.fr/datasets/fiscalite-locale-des-particuliers) | `csv/fiscalite/fiscalite-locale-des-particuliers.csv` | `ml/data/property_tax.py` |
| INSEE — Grille communale de densité 2024 | [insee.fr/fr/information/6439600](https://www.insee.fr/fr/information/6439600) | `csv/grille-densite-communale-2024.xlsx` | `ml/data/density_grid.py` |

---

## Documentation

| Document | Contenu |
|---|---|
| [`docs/CONTEXTE_ML_MEMOIRE.md`](docs/CONTEXTE_ML_MEMOIRE.md) | Cahier des charges des 4 modèles ML (clustering, prix, forecasting, quantile) |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Suivi des 39 séries cibles, sources de données, plan d'acquisition |
| [`docs/ROADMAP_IA.md`](docs/ROADMAP_IA.md) | Architecture retenue pour la couche IA (3 axes de modélisation) |
| [`docs/MEMOIRE_PARTIE3_INPUTS.md`](docs/MEMOIRE_PARTIE3_INPUTS.md) | Matière brute (chiffres, métriques mesurées) pour la rédaction de la Partie 3 |
| [`ml/README.md`](ml/README.md) | Documentation du dossier `ml/` : modèles, artefacts, commandes d'entraînement |
| [`ml/EXPERIMENTS_LOG.md`](ml/EXPERIMENTS_LOG.md) | Journal d'expérimentation — tout ce qui a été testé, y compris les échecs |
| [`ml/PERFORMANCE.md`](ml/PERFORMANCE.md) | Tableau de bord des métriques officielles par modèle |

---

## Installation

```bash
pip install -r requirements.txt
```

**`.env`** à la racine :
```
DATABASE_URL="postgresql://user:password@localhost:5432/ma_base"
```

Si la DB est distante, tunnel SSH avant de lancer les commandes :
```bash
ssh -L 5432:localhost:5432 user@your-server
# Puis DATABASE_URL=postgresql://user:pass@localhost:5432/immo
```

### Vérifier les enums Postgres avant premier run

`name`, `source`, `frequency`, `chart_type` des séries doivent correspondre exactement
aux enums Prisma côté Rentium :
```bash
\dT+ "SerieName"   # dans psql
```

---

## Commandes

### 1. Seed géographique (à faire en premier)

```bash
# Toutes les communes françaises (~35 000)
python -m pipeline.scripts.seed_cities

# Seulement certains départements
python -m pipeline.scripts.seed_cities --dept 75,69,33

# Dry-run (aucune écriture)
python -m pipeline.scripts.seed_cities --dry-run
```

### 2. Seed données socio-démographiques

```bash
# INSEE RP 2022 — étudiants + retraités + chômeurs (recommandé)
python -m pipeline.scripts.seed_rp

# ESR — étudiants uniquement (optionnel, écrasé par seed_rp)
python -m pipeline.scripts.seed_students
```

> `seed_rp` écrase `student_count` posé par `seed_students`. Lancer `seed_rp` suffit.

### 3. Pipeline DVF (calcul + upload des séries)

```bash
# Tous niveaux géo
python -m pipeline.scripts.run_dvf

# Villes uniquement — département 77
python -m pipeline.scripts.run_dvf --geo city --dept 77

# Départements + régions + pays (sans villes)
python -m pipeline.scripts.run_dvf --geo department,region,country

# Dry-run (calcul sans écriture DB)
python -m pipeline.scripts.run_dvf --geo department,region,country --dry-run

# Série unique pour test
python -m pipeline.scripts.run_dvf --serie price_sqm_t2 --geo department --dry-run

# Export CSV
python -m pipeline.scripts.run_dvf --geo department --csv output.csv

# Preview N lignes
python -m pipeline.scripts.run_dvf --geo department --preview 10
```

Le pipeline `run_dvf` enchaîne automatiquement (si `--geo city`) :
1. Calcul DuckDB
2. Upload séries + timeseries
3. Dénormalisation (`median_price_per_sqm`, `avg_price_per_sqm`, `sparkline_path`, etc.)
4. Interpolation IDW des villes sans données

### 4. Dénormalisation et interpolation (standalone)

```bash
# Recalcule les snapshot fields sur cities depuis les timeseries
python -m pipeline.scripts.denormalize --dept 77

# Interpole les villes sans prix via IDW (voisins les plus proches)
python -m pipeline.scripts.interpolate --dept 77 --k 5 --max-dist 30
```

### 5. Séries socio-démo INSEE + champs dashboard dérivés

```bash
# Séries annuelles (city level) — pattern seed script identique aux autres
python -m pipeline.scripts.seed_pop_series          # population, aging_index
python -m pipeline.scripts.seed_logement_series     # vacancy_rate, owner_rate, social_housing_rate, secondary_residence_rate
python -m pipeline.scripts.seed_rp_series           # unemployment_rate
python -m pipeline.scripts.seed_employment_series   # active_population (ACT1564)

# Colonnes cities dérivées du zonage DHUP
python -m pipeline.scripts.seed_housing_zone        # housing_zone + high_demand_zone

# Colonnes cities dérivées des séries ci-dessus — à lancer APRÈS les 4 seed_*_series
python -m pipeline.scripts.seed_dashboard_fields    # tenant_rate, demographic_growth_5y, employment_growth
```

### 6. Audit qualité

```bash
python -m pipeline.scripts.audit
python -m pipeline.scripts.audit --geo department
python -m pipeline.scripts.audit --no-chart   # sans graphiques terminal
python -m pipeline.scripts.audit --qoq 0.25   # seuil anomalie QoQ à 25%
```

---

## Ordre recommandé (premier run)

```bash
# 1. Préparer la DB
python -m pipeline.scripts.seed_cities
python -m pipeline.scripts.seed_rp

# 2. Lancer le pipeline complet
python -m pipeline.scripts.run_dvf --geo department,region,country
python -m pipeline.scripts.run_dvf --geo city  # long (~35k communes)

# 3. Séries socio-démo + champs dashboard dérivés
python -m pipeline.scripts.seed_pop_series
python -m pipeline.scripts.seed_logement_series
python -m pipeline.scripts.seed_rp_series
python -m pipeline.scripts.seed_employment_series
python -m pipeline.scripts.seed_housing_zone
python -m pipeline.scripts.seed_dashboard_fields

# 4. Vérifier la qualité
python -m pipeline.scripts.audit
```

---

## Séries calculées

| Nom | Unité | Description |
|-----|-------|-------------|
| `price_sqm_all` | €/m² | Prix médian toutes typologies (appt + maison) |
| `price_sqm_appt` | €/m² | Prix médian appartements |
| `price_sqm_house` | €/m² | Prix médian maisons |
| `price_sqm_t1` | €/m² | Prix médian studios / T1 |
| `price_sqm_t2` | €/m² | Prix médian T2 |
| `price_sqm_t3` | €/m² | Prix médian T3 |
| `price_sqm_t4` | €/m² | Prix médian T4+ |
| `surface_median_t1` | m² | Surface médiane T1 |
| `surface_median_t2` | m² | Surface médiane T2 |
| `surface_median_t3` | m² | Surface médiane T3 |
| `surface_median_t4` | m² | Surface médiane T4+ |
| `transaction_volume` | nb | Nombre de mutations distinctes |
| `vefa_share` | % | Part des ventes en VEFA |
| `land_price_sqm` | €/m² | Prix médian terrain à bâtir |
| `population` | count | Population totale (INSEE IC évol-struct-pop) |
| `aging_index` | ratio | POP65+ / POP0-19 |
| `vacancy_rate` | ratio | LOGVAC / LOG (INSEE IC logement) |
| `owner_rate` | ratio | RP_PROP / RP |
| `social_housing_rate` | ratio | RP_LOCHLMV / RP |
| `secondary_residence_rate` | ratio | RSECOCC / LOG |
| `unemployment_rate` | ratio | CHOM1564 / ACT1564 (INSEE IC activité résidents) |
| `active_population` | count | SUM(ACT1564) — actifs occupés 15-64 ans |

> ⚠️ `active_population` doit être ajouté à l'enum Postgres `SerieName` avant le premier run réel de `seed_employment_series.py` (cf. section "Vérifier les enums Postgres" ci-dessus).

---

## Champs dénormalisés sur `cities`

| Colonne | Source |
|---------|--------|
| `median_price_per_sqm` | Dernier trimestre `price_sqm_all` |
| `avg_price_per_sqm` | Moyenne des 4 derniers trimestres |
| `transaction_volume` | Somme des 4 derniers trimestres |
| `price_growth_3y` | Évolution sur 3 ans (%) |
| `price_trend` | `up` / `down` / `stable` (delta dernier QoQ) |
| `sparkline_path` | JSON des 8 dernières valeurs trimestrielles |
| `price_data_source` | `dvf` (données réelles) ou `estimated` (IDW) |
| `student_count` | INSEE RP 2022 — élèves/étudiants 15-64 ans |
| `retired_count` | INSEE RP 2022 — retraités 15-64 ans |
| `unemployed_count` | INSEE RP 2022 — chômeurs 15-64 ans |
| `housing_zone` | Zonage DHUP A/Abis/B1/B2/C (`seed_housing_zone.py`) |
| `high_demand_zone` | `housing_zone IN (A, A_BIS)` (`seed_housing_zone.py`) |
| `tenant_rate` | `1 - owner_rate` (dernière valeur), `seed_dashboard_fields.py` |
| `demographic_growth_5y` | `(population[Y] / population[Y-5] - 1) × 100`, `seed_dashboard_fields.py` |
| `employment_growth` | `(active_population[Y] / active_population[Y-5] - 1) × 100`, `seed_dashboard_fields.py` |

> `rental_tension_score` : pas géré par ce pipeline (hors périmètre pour l'instant).

---

## Prérequis DB

### Contraintes pour les upserts idempotents

```sql
-- Évite les doublons séries
ALTER TABLE series ADD CONSTRAINT series_name_geo_unique
  UNIQUE NULLS NOT DISTINCT (name, city_id, administrative_zone_id, country_id);

-- Évite les doublons timeseries
ALTER TABLE timeseries ADD CONSTRAINT timeseries_serie_ts_dim_unique
  UNIQUE NULLS NOT DISTINCT (serie_id, timestamp, dimension);

-- Évite la collision code='11' entre région IDF et département Aude
ALTER TABLE administrative_zones ADD CONSTRAINT az_code_type_unique
  UNIQUE (code, type);
```

> `NULLS NOT DISTINCT` requiert PostgreSQL ≥ 15.

### Colonnes supplémentaires sur `cities`

```sql
ALTER TABLE cities ADD COLUMN IF NOT EXISTS price_data_source TEXT;
ALTER TABLE cities ADD COLUMN IF NOT EXISTS retired_count INTEGER;
ALTER TABLE cities ADD COLUMN IF NOT EXISTS unemployed_count INTEGER;
```

---

## Points techniques importants

### Piège DVF — valeur_foncière répétée

Dans DVF brut, `valeur_fonciere` est la valeur **totale de la mutation**, répétée sur chaque ligne locale. Une vente de 3 appartements à 900k génère 3 lignes avec 900k chacune.

La vue `dvf_mutation_prices` agrège `SUM(surface_bati)` par mutation avant de calculer le prix/m².

### Codes INSEE communes

`code_dept` + `LPAD(code_commune, 3, '0')` = code INSEE 5 caractères.

### Paris / Lyon / Marseille

Les arrondissements (75101–75120, 69381–69389, 13201–13216) sont consolidés vers la commune principale dans les seeds.

### Filtres qualité prix

```
surface_bati BETWEEN 9 AND 2000
valeur_fonciere / surface_bati BETWEEN 500 AND 30000
```

Outliers (ville avec prix > 3× médiane départementale) → exclus de DVF, remplis par IDW.

### Interpolation IDW

Pour les communes sans données (< 10 transactions) :
- K=5 voisins les plus proches avec données DVF
- Distance max 30 km
- Fallback : médiane du département

---

## Volumes estimés

| Niveau | Paires (série, geo) | Timeseries rows |
|--------|---------------------|-----------------|
| country | 14 | ~280 |
| region | 252 | ~4 760 |
| department | 1 344 | ~27 160 |
| city | ~500 000 | ~1 200 000 |
