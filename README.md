# immo-data-science

Pipeline de données et modèles IA pour l'investissement immobilier résidentiel en France —
projet support du mémoire *« L'intelligence artificielle appliquée à l'investissement
immobilier »* (Rentium).

Calcule des séries temporelles immobilières à partir des données DVF (DGFiP) et INSEE,
les stocke en PostgreSQL, et entraîne les modèles de scoring/forecasting/clustering
utilisés par la plateforme Rentium.

**Auteur** : Kilian Trouet

## Vue d'ensemble

- **~40 séries** × **4 niveaux géo** (commune, département, région, pays)
- DVF **2021–2025** trimestriel ; séries INSEE/DGFiP annuelles (population jusqu'à 2006, TFB jusqu'à 2018)
- ~35 000 communes seedées depuis `geo.api.gouv.fr`
- Données socio-démographiques INSEE RP 2022 (étudiants, retraités, chômeurs, âge médian)
- Répartition commerces (BPE), taille des ménages, loyers DHUP, rendement brut, taxe foncière
- Zonage ABC national, encadrement des loyers, permis de louer
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
│   │   ├── upload.py      # upload PostgreSQL (upsert série + timeseries)
│   │   ├── poi_source.py  # handler osmium : stream .osm.pbf → POI whitelistés
│   │   └── poi_geo.py     # STRtree point-in-polygon : POI → code INSEE commune
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
│       ├── seed_fiscalite_series.py  # série property_tax_rate (Taux_Global_TFB, 2018-2025)
│       ├── prep_population_history.py       # one-off : XLSX INSEE large → CSV/an (à lancer avant seed_population_history_series)
│       ├── scrape_rental_permit.py    # (trimestriel) reconstruit rental-permit.csv : LocService + couches data.gouv + 26 lignes préfecture
│       ├── seed_population_history_series.py # série population_history (2006-2023, PMUN)
│       ├── seed_commerce_series.py          # séries retail_count + retail_share_* (BPE, 2021 & 2025)
│       ├── seed_household_size_series.py    # séries household_size_1p_rate … 5p_plus_rate (RP MEN1, 2016 & 2022)
│       ├── seed_rent_series.py              # séries rent_sqm_all/house/t1-t4 (Carte des loyers DHUP 2025)
│       ├── seed_gross_yield.py              # séries gross_yield_t1-4 + cities.avg_rent_per_sqm / gross_yield
│       ├── seed_median_income_series.py     # série median_income (INSEE Filosofi 2021)
│       ├── seed_years_to_buy.py             # série years_to_buy = price_sqm_all × 70m² ÷ median_income
│       ├── seed_housing_effort_rate.py      # série housing_effort_rate (rent_sqm_all × 70m² ÷ revenu mensuel) — attend rent_sqm_all
│       ├── seed_company_creations_series.py # série company_creations (INSEE SIDE, 2012-2025)
│       ├── seed_avg_property_tax.py         # cities.avg_property_tax (← dernière valeur property_tax_rate)
│       ├── seed_activity_counts_series.py   # cities.unemployed_count + retired_count (comptes absolus, national)
│       ├── seed_median_age.py               # cities.median_age (médiane approx. par interpolation de tranches)
│       ├── seed_tenant_profile.py           # cities.tenant_profile (règle : structure d'âge + student_count)
│       ├── seed_short_term_rental_score.py  # cities.short_term_rental_score (InsideAirbnb + communes touristiques)
│       ├── seed_rent_regulation.py          # cities.rent_control + rental_permit_required (ANIL / préfectures)
│       ├── seed_poi.py               # table pois : POI OpenStreetMap (Geofabrik .osm.pbf) → commune par point-in-polygon
│       ├── seed_housing_zone.py      # cities.housing_zone + cities.high_demand_zone (zonage ABC NATIONAL)
│       ├── seed_dashboard_fields.py  # cities.tenant_rate / demographic_growth_5y / employment_growth
│       └── seed_city_latest_snapshots.py # cities.owner_rate / vacancy_rate / median_income / annual_company_creations (← dernière valeur de série)
├── ml/                    # modèles IA du mémoire (clustering, prix, forecasting, quantile...) — voir ml/README.md
├── scripts/               # wrappers PowerShell — orchestration multi-étapes (seed, ré-entraînement, reporting)
│   ├── seed_all.ps1            # TOUT seeder en une commande (ordre des dépendances + audit)
│   ├── seed_national.ps1        # (legacy) seed partiel : communes + 4 séries socio-démo + DVF
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
| DHUP — Zonage ABC (Île-de-France, legacy) | [data.gouv.fr — zonage ABC](https://www.data.gouv.fr/datasets/logement-liste-des-communes-selon-le-zonage-abc) | `csv/logement-liste-des-communes-selon-le-zonage-abc.csv` | `pipeline/scripts/seed_housing_zone.py --csv <ce fichier>` (rollback uniquement) |
| Zonage ABC **national** (arrêté en vigueur, ~34 900 communes) | [data.gouv.fr — zonage ABC national](https://www.data.gouv.fr/datasets/liste-des-communes-selon-le-zonage-abc) | `csv/zonage-abc-national.csv` | `pipeline/scripts/seed_housing_zone.py` (défaut), `ml/data/zonage_national.py` |
| INSEE — Historique des populations communales (1876-2023, on ne garde que PMUN 2006-2023) | [insee.fr/fr/statistiques/3698339](https://www.insee.fr/fr/statistiques/3698339) | `csv/population-historique/raw_histo_pop.xlsx` → `prep_population_history.py` → `population-historique-{2006..2023}.CSV` | `pipeline/scripts/seed_population_history_series.py` |
| INSEE — BPE (Base Permanente des Équipements), domaine Commerce | [insee.fr/fr/statistiques/8217537](https://www.insee.fr/fr/statistiques/8217537) (2025) + `bpe21_ensemble` sur data.gouv.fr (2021) | `csv/bpe-commerces/bpe-commerces-{2021,2025}.CSV` | `pipeline/scripts/seed_commerce_series.py` |
| INSEE — Couples-Familles-Ménages, table MEN1 (taille des ménages) | [insee.fr/fr/statistiques/8582448](https://www.insee.fr/fr/statistiques/8582448) (2022) + [4171364](https://www.insee.fr/fr/statistiques/4171364) (2016) | `csv/menages-taille/menages-taille-{2016,2022}.CSV` (pré-agrégé, dimension CS retirée) | `pipeline/scripts/seed_household_size_series.py` |
| ANIL / Légifrance / préfectures — encadrement des loyers & permis de louer | [anil.org](https://www.anil.org) + arrêtés préfectoraux (URLs + dates dans l'en-tête de chaque CSV) | `csv/reglementation-locative/rent-control.csv` (~70 communes), `rental-permit.csv` (~700 communes) | `pipeline/scripts/seed_rent_regulation.py` |
| Permis de louer — spine rafraîchissable (scraping, pas de fichier à déposer) | [LocService.fr](https://www.locservice.fr/guides/guide-proprietaire/mettre-son-logement-location/permis-de-louer) + couches data.gouv.fr (DDTM Pas-de-Calais & Hérault WFS ; métropoles Aix-Marseille, Bordeaux OpenDataSoft ; MEL HTML) + `geo.api.gouv.fr` pour le géocodage | `csv/reglementation-locative/rental-permit.csv` (regénéré) + `rental-permit-unresolved.csv` (revue manuelle) | `pipeline/scripts/scrape_rental_permit.py` (trimestriel) |
| ESR — effectifs étudiants (legacy, supplanté par INSEE RP) | [data.enseignementsup-recherche.gouv.fr](https://data.enseignementsup-recherche.gouv.fr) | `csv/fr-esr-atlas_regional-effectifs-d-etudiants-inscrits_agregeables.csv` | `pipeline/scripts/seed_students.py` (optionnel) |
| INSEE Filosofi 2021 — revenus, pauvreté, niveau de vie | [insee.fr/fr/statistiques/7756729](https://www.insee.fr/fr/statistiques/7756729) (`base-cc-filosofi-2021-geo2025_csv.zip`) | `csv/DS_FILOSOFI_CC_data.csv` | `pipeline/scripts/seed_median_income_series.py`, `ml/data/median_income.py` |
| INSEE SIDE — créations d'entreprises niveau communal (2012-2025) | [data.gouv.fr — créations d'entreprises communal](https://www.data.gouv.fr/datasets/creations-dentreprises-au-niveau-communal-et-supra-communal-par-secteur-dactivite-a10-et-forme-legale) (GET direct, sans clé API ; ZIP 44 Mo → CSV 410 Mo, filtré à la volée) | `csv/creations-entreprises/creations-entreprises-2012-2025.CSV` (dérivé, `GEO;TIME_PERIOD;OBS_VALUE`) | `pipeline/scripts/seed_company_creations_series.py` |
| InsideAirbnb — annonces location courte durée (couverture FR partielle : Paris, Lyon, Bordeaux métropole, Pays Basque) | [insideairbnb.com/get-the-data](https://insideairbnb.com/get-the-data) | `csv/location-courte-duree/insideairbnb-*-listings.csv.gz` | `pipeline/scripts/seed_short_term_rental_score.py` |
| Atout France — communes touristiques / stations classées de tourisme | [public.opendatasoft.com — classement touristique](https://public.opendatasoft.com/explore/dataset/economicref-france-commune-classement-touristique) | `csv/location-courte-duree/communes-touristiques.csv` | `pipeline/scripts/seed_short_term_rental_score.py` |
| DGFiP — Fiscalité locale des particuliers 2021-2025 (taux TFB/TFNB/TH) | [data.economie.gouv.fr — fiscalite-locale-des-particuliers-geo](https://data.economie.gouv.fr/explore/dataset/fiscalite-locale-des-particuliers-geo) | `csv/fiscalite/fiscalite-locale-des-particuliers.csv` | `pipeline/scripts/seed_fiscalite_series.py` |
| DGFiP — REI 2018-2020 (taux TFB reconstruit, dataset "particuliers-geo" ne couvre que 2021+) | [data.economie.gouv.fr — impots-locaux-fichier-de-recensement-des-elements-dimposition-a-la-fiscalite-dir](https://data.economie.gouv.fr/explore/dataset/impots-locaux-fichier-de-recensement-des-elements-dimposition-a-la-fiscalite-dir) (pièces jointes `rei_{2018,2019,2020}_..._zip`, ~150 Mo/an) | `csv/fiscalite/rei-taux-global-tfb-{2018,2019,2020}.csv` (dérivé — colonnes `INSEE COM;EXERCICE;Taux_Global_TFB`, formule documentée en tête de `seed_fiscalite_series.py`) | `pipeline/scripts/seed_fiscalite_series.py` |
| Carte des loyers 2025, ANIL/DHUP | [data.gouv.fr — Carte des loyers](https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025) | `csv/loyers/*.csv` (4 fichiers : `pred-app`, `pred-app12`, `pred-app3`, `pred-mai` ; encodage cp1252, décimale virgule) | `pipeline/scripts/seed_rent_series.py`, `ml/data/rent.py` |
| DGFiP — Fiscalité locale des particuliers | [data.gouv.fr — fiscalité locale](https://www.data.gouv.fr/datasets/fiscalite-locale-des-particuliers) | `csv/fiscalite/fiscalite-locale-des-particuliers.csv` | `ml/data/property_tax.py` |
| INSEE — Grille communale de densité 2024 | [insee.fr/fr/information/6439600](https://www.insee.fr/fr/information/6439600) | `csv/grille-densite-communale-2024.xlsx` | `ml/data/density_grid.py` |
| Geofabrik — extraits OpenStreetMap (`.osm.pbf`) pour les POI | [download.geofabrik.de/europe/france-latest.osm.pbf](https://download.geofabrik.de/europe/france-latest.osm.pbf) (national, ~4 Go) ou un extrait régional, ex. [ile-de-france-latest.osm.pbf](https://download.geofabrik.de/europe/france/ile-de-france-latest.osm.pbf) (~330 Mo) | `csv/poi/*.osm.pbf` (fichier de travail jetable, gitignore) | `pipeline/scripts/seed_poi.py` → table `pois` |

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
python -m pipeline.scripts.seed_fiscalite_series    # property_tax_rate (2018-2025)
python -m pipeline.scripts.prep_population_history          # one-off : XLSX → CSV/an (avant le seed suivant)
python -m pipeline.scripts.seed_population_history_series   # population_history (2006-2023)
python -m pipeline.scripts.seed_commerce_series            # retail_count + retail_share_* (2021, 2025)
python -m pipeline.scripts.seed_household_size_series      # household_size_1p_rate … 5p_plus_rate (2016, 2022)
python -m pipeline.scripts.seed_rent_series               # rent_sqm_all/house/t1-t4
python -m pipeline.scripts.seed_median_income_series      # median_income (Filosofi 2021)
python -m pipeline.scripts.seed_company_creations_series  # company_creations (SIDE 2012-2025)

# Colonnes cities dérivées du zonage ABC national
python -m pipeline.scripts.seed_housing_zone        # housing_zone + high_demand_zone (défaut = fichier national)

# Réglementation locative + score location courte durée
python -m pipeline.scripts.seed_rent_regulation           # rent_control + rental_permit_required
python -m pipeline.scripts.seed_short_term_rental_score   # short_term_rental_score

# Comptes absolus + âge médian + profil locataire (UPDATE direct cities) — data INSEE déjà en repo
python -m pipeline.scripts.seed_activity_counts_series   # unemployed_count, retired_count (national)
python -m pipeline.scripts.seed_median_age               # median_age (approx.)
python -m pipeline.scripts.seed_tenant_profile           # tenant_profile

# Colonnes/séries dérivées — à lancer APRÈS leurs dépendances
python -m pipeline.scripts.seed_dashboard_fields    # tenant_rate, demographic_growth_5y, employment_growth
python -m pipeline.scripts.seed_avg_property_tax    # avg_property_tax  (APRÈS seed_fiscalite_series)
python -m pipeline.scripts.seed_gross_yield         # gross_yield_t1-4 + avg_rent_per_sqm/gross_yield  (APRÈS seed_rent_series + DVF)
python -m pipeline.scripts.seed_years_to_buy        # years_to_buy  (APRÈS seed_median_income_series + DVF price_sqm_all)
python -m pipeline.scripts.seed_housing_effort_rate # housing_effort_rate  (APRÈS seed_median_income_series + seed_rent_series)
python -m pipeline.scripts.seed_city_latest_snapshots # cities.owner_rate/vacancy_rate/median_income/annual_company_creations (APRÈS leurs seed_*_series)
```

> Colonnes `cities` requises côté affordability : `median_income`, `years_to_buy`, `housing_effort_rate` — ajoutées à `rentium/backend/prisma/schema/city.prisma` (section *Affordability*). Migration Prisma à appliquer (`npx prisma migrate dev`) avant que `seed_years_to_buy` / `seed_housing_effort_rate` / `seed_city_latest_snapshots` puissent dénormaliser.

### 6. Audit qualité

```bash
python -m pipeline.scripts.audit
python -m pipeline.scripts.audit --geo department
python -m pipeline.scripts.audit --no-chart   # sans graphiques terminal
python -m pipeline.scripts.audit --qoq 0.25   # seuil anomalie QoQ à 25%
```

---

## Tout seeder — une commande

```powershell
.\scripts\seed_all.ps1                 # France entière (l'étape DVF city est la plus longue)
.\scripts\seed_all.ps1 -Dept 77        # un département (dev/test)
.\scripts\seed_all.ps1 -RefreshPermit  # relance aussi scrape_rental_permit (réseau) avant seed_rent_regulation
.\scripts\seed_all.ps1 -SkipDvf        # saute run_dvf (DVF déjà seedé)
```

`seed_all.ps1` enchaîne, dans l'ordre des dépendances : `seed_cities` → `seed_rp` → DVF (department/region/country puis city, avec denormalize + interpolate) → séries INSEE/DGFiP (`seed_pop_series`, `seed_logement_series`, `seed_rp_series`, `seed_employment_series`, `seed_fiscalite_series`, `prep_population_history` + `seed_population_history_series`, `seed_commerce_series`, `seed_household_size_series`, `seed_rent_series`, `seed_median_income_series`, `seed_company_creations_series`) → `seed_housing_zone` → `seed_rent_regulation` → `seed_short_term_rental_score` → `seed_poi` (soft, table `pois`) → `seed_activity_counts_series` → `seed_median_age` → `seed_tenant_profile` → dérivés (`seed_dashboard_fields`, `seed_avg_property_tax`, `seed_gross_yield`, `seed_years_to_buy`, `seed_housing_effort_rate`, `seed_city_latest_snapshots`) → `audit`.

Prérequis : `.env` (DATABASE_URL), fichiers déposés dans `csv/` + `dvf-raw/`, migration Prisma appliquée côté `rentium/backend` (enum `SerieName` — `active_population` ajouté le 2026-09-10 — + colonnes `cities`). Si la migration n'est pas passée : `seed_employment_series` sauté en soft-fail, `employment_growth` reste nul.

Détail commande par commande : voir section "Commandes" ci-dessus.

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
| `property_tax_rate` | % | Taux_Global_TFB — taxe foncière bâti effective (DGFiP, 2018-2025) |
| `population_history` | count | Population municipale PMUN, série longue annuelle (INSEE, 2006-2023) |
| `retail_count` | count | Nombre de commerces (BPE domaine B = B1+B2+B3), 2021 & 2025 |
| `retail_share_large_format` | ratio | Part B1 — grandes surfaces (hyper/super) |
| `retail_share_grocery` | ratio | Part B2 — commerces alimentaires |
| `retail_share_specialty` | ratio | Part B3 — commerces spécialisés non-alimentaires |
| `household_size_1p_rate` | ratio | Part ménages 1 personne (RP MEN1, 2016 & 2022) |
| `household_size_2p_rate` | ratio | Part ménages 2 personnes |
| `household_size_3p_rate` | ratio | Part ménages 3 personnes |
| `household_size_4p_rate` | ratio | Part ménages 4 personnes |
| `household_size_5p_plus_rate` | ratio | Part ménages 5 personnes ou + (les 5 parts somment à 1) |
| `rent_sqm_all` | €/m²/mois | Loyer d'annonce prédit, tous appartements (Carte des loyers DHUP 2025) |
| `rent_sqm_house` | €/m²/mois | Loyer prédit, maisons |
| `rent_sqm_t1` / `rent_sqm_t2` | €/m²/mois | Loyer prédit appt 1-2 pièces (**même valeur** t1=t2 — DHUP ne sépare pas) |
| `rent_sqm_t3` / `rent_sqm_t4` | €/m²/mois | Loyer prédit appt 3+ pièces (**même valeur** t3=t4) |
| `gross_yield_t1`…`gross_yield_t4` | % | (rent_sqm_t{n} × 12) / price_sqm_t{n} × 100 — rendement brut par typologie |
| `median_income` | € | Niveau de vie médian annuel (INSEE Filosofi 2021, MED_SL) |
| `years_to_buy` | années | `price_sqm_all` × 70 m² ÷ `median_income` |
| `housing_effort_rate` | ratio | `rent_sqm_all` × 70 m² ÷ (`median_income` / 12) — part du revenu au logement |
| `company_creations` | count | Créations d'unités légales /an (INSEE SIDE, 2012-2025, micro-entrepreneurs inclus) |

> ✅ Enum `SerieName` : `property_tax_rate`, `rent_sqm_all`, `rent_sqm_house`, `population_history`, `retail_count`, `retail_share_large_format`, `retail_share_grocery`, `retail_share_specialty`, `household_size_1p_rate`…`5p_plus_rate` — **migration Postgres appliquée le 2026-09-10** (vérifié via `SELECT enum_range(NULL::"SerieName")`). `rent_sqm_t*` et `gross_yield_t*` préexistaient.
> ⏳ `active_population` : ajouté à `serie.prisma` le 2026-09-10, **migration à appliquer** (`npx prisma migrate dev` côté `rentium/backend`) — sans elle `seed_employment_series` échoue et `employment_growth` reste nul.
> `source = "INSEE"` par convention pour toutes les sources gouvernementales non-DVF — l'enum `SerieSource` n'a pas de valeur `DGFIP` (seulement `CALC`/`DVF`/`INSEE`/`SCR`).

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
| `retired_count` | INSEE IC évol-struct-pop 2022 — **POP65P** (population 65+, proxy national). Remplace l'ancien RETR1564 (15-64) qui sous-estimait ×5-6. `seed_activity_counts_series.py` |
| `unemployed_count` | INSEE IC activité résidents 2021 — CHOM1564, compte absolu national. `seed_activity_counts_series.py` |
| `median_age` | Médiane approx. par interpolation des tranches d'âge INSEE (pas de médiane exacte en open data commune). `seed_median_age.py` |
| `housing_zone` | Zonage ABC **national** A/Abis/B1/B2/C (`seed_housing_zone.py`, défaut = `csv/zonage-abc-national.csv`) |
| `high_demand_zone` | `housing_zone IN (A, A_BIS)` (`seed_housing_zone.py`) |
| `rent_control` | Encadrement des loyers en vigueur (bool). Liste curée ANIL/Légifrance, ~70 communes. `seed_rent_regulation.py` |
| `rental_permit_required` | Permis de louer requis (bool). **~700 communes** (26 préfecture + couches data.gouv + spine LocService géocodée), rafraîchi par `scrape_rental_permit.py` puis `seed_rent_regulation.py`. Voir « Rafraîchir les données ». |
| `avg_property_tax` | Dernière valeur `property_tax_rate` (%), `seed_avg_property_tax.py` |
| `avg_rent_per_sqm` | Dernière valeur `rent_sqm_all` (€/m²/mois), `seed_gross_yield.py` |
| `gross_yield` | Rendement brut agrégé ville (%), `seed_gross_yield.py` |
| `tenant_rate` | `1 - owner_rate` (dernière valeur), `seed_dashboard_fields.py` |
| `demographic_growth_5y` | `(population[Y] / population[Y-5] - 1) × 100`, `seed_dashboard_fields.py` |
| `employment_growth` | `(active_population[Y] / active_population[Y-5] - 1) × 100`, `seed_dashboard_fields.py` |

> `avg_sale_days`, `search_time_*`, `sale_time_*`, `short_term_rental_score`, `avg_relocation_days`, `tenant_profile`, `company_creations` : **pas encore gérés** — voir section "Chantiers restants".
> Hors périmètre (retirés du schéma le 2026-09-10) : `listings_count_t*`, `rental_tension_score`, `supply_demand_ratio`, `properties_for_sale`.

---

## Chantiers restants

### Bloqués sur le projet `scraping-marketplaces` (flux d'annonces actives, pas branché)
Nécessitent un suivi longitudinal des annonces (stockage + re-scrape planifié + matching entre runs pour mesurer la durée de vie d'une annonce). Le scraper actuel est *stateless* (1 run = 1 snapshot, `published_at` quasi toujours nul) → pas faisable en l'état.
| Cible | Raison |
|---|---|
| `search_time_t*` (temps de recherche location) | Durée en ligne des annonces loc — besoin suivi longitudinal |
| `sale_time_t*` / `avg_sale_days` (champ cities) | Durée en ligne des annonces vente — besoin suivi longitudinal |

### Retirés du périmètre (2026-09-10, sur décision)
`listings_count_t*`, `rental_tension_score`, `supply_demand_ratio`, `properties_for_sale` — retirés de `serie.prisma` / `city.prisma`. Ne doivent pas apparaître dans les graphiques.

### Scripts écrits cette session — à lancer en réel (ou déblocage mineur)
| Cible | État | Note |
|---|---|---|
| `company_creations` | ✅ script + source prêts | `seed_company_creations_series.py`, INSEE SIDE 2012-2025. Enum OK. Lancer le seed réel. |
| `median_income` (série) | ✅ **seedé en réel** (national, 31 212 pts) | `seed_median_income_series.py`, Filosofi 2021 |
| `years_to_buy` (série) | ✅ **seedé en réel** (national, 7 378 pts) | `seed_years_to_buy.py` = `price_sqm_all` × 70 m² ÷ `median_income` |
| `housing_effort_rate` (série) | ⏳ script prêt, bloqué | `seed_housing_effort_rate.py` — attend que `seed_rent_series.py` (`rent_sqm_all`) tourne en réel |
| `short_term_rental_score` (champ) | ✅ script prêt | `seed_short_term_rental_score.py` — composite InsideAirbnb + communes touristiques. Couverture InsideAirbnb = Paris/Lyon/Bordeaux/Pays Basque only → 190 communes haute confiance, reste = flag tourisme/zonage. Confiance dans sidecar `csv/location-courte-duree/str_score_confidence.csv`. |
| `tenant_profile` (champ) | ✅ script prêt, caveat | `seed_tenant_profile.py` — règle sur structure d'âge + `student_count`. ~80 % des communes → "familles" (net seulement aux extrêmes). Meilleur une fois `household_size_*` + `student_count` national seedés. |

### Pas de source exploitable trouvée
| Cible | Constat | Piste |
|---|---|---|
| `rental_permit_required` | Toujours pas de registre national officiel ingérable (décision commune par commune / EPCI par EPCI). | ✅ **traité en spine rafraîchissable** par `pipeline/scripts/scrape_rental_permit.py` (voir « Rafraîchir les données » ci-dessous) — 698 communes vs 26 avant. Reste à améliorer : géocodage des ~28 noms non résolus (EPCI, quartiers, coquilles) listés dans `csv/reglementation-locative/rental-permit-unresolved.csv`. |
| `avg_relocation_days` | Aucune source ouverte (OLL = loyers only ; CLAMEUR mort/payant) | Proxy possible depuis la série `vacancy_rate` existante |
| `short_term_rental_score` — couverture | InsideAirbnb ne publie que 4 zones FR | Compléter avec déclarations meublés de tourisme en mairie (data.gouv.fr, fragmenté) |

### Améliorations
| Cible | Note |
|---|---|
| BPE commerces — historique | Seules 2021 & 2025 récupérées (millésimes intermédiaires 404 ou flags présence/absence) — rechercher 2019/2022/2023/2024 exploitables |
| `retail_count` → densité /1000 hab | Nécessite jointure avec la série `population` — ajouter un `retail_density` dérivé ou calculer côté app |
| `household_size_*`, `student_count` national | Seeds pas encore lancés en réel — les lancer améliore `tenant_profile` |

---

## Rafraîchir les données

### `rental_permit_required` — `pipeline/scripts/scrape_rental_permit.py` (à relancer **trimestriellement**)

Faute de registre national, `rental-permit.csv` est reconstruit par scraping. Le script n'écrit
qu'un CSV (aucune écriture DB) et fusionne 3 niveaux de confiance, dédoublonnés par code INSEE
avec priorité `prefecture > datagouv > locservice` (chaque ligne garde son `source_url` + `as_of_date` ;
colonnes d'audit `source_type`, `match_confidence`) :

1. **26 lignes préfecture** (Loiret + Tarn) déjà présentes — **conservées telles quelles**, jamais écrasées.
2. **Couches open-data dept/métropole** (INSEE fournis, plus fiables ; parsées sans dépendance SIG —
   WFS-GML via regex, API OpenDataSoft v2.1 en JSON) :
   - DDTM Pas-de-Calais (62) « Mise en place du Permis de louer » — WFS geo-IDE
   - DDTM Hérault (34) « Communes ayant mis en œuvre l'AMPL » — WFS geo-IDE
   - Métropole Aix-Marseille-Provence « Secteurs soumis au permis de louer » — OpenDataSoft
   - Bordeaux Métropole « Permis de louer / diviser / déclaration » — OpenDataSoft
   - Métropole Européenne de Lille — pas de WFS public ni de ressource data.gouv exploitable
     (entrées data.gouv = Roubaix seul et sans fichier ; `opendata.roubaix.fr` a un certificat TLS
     expiré) → la liste des 29 communes est lue sur `lillemetropole.fr/permis-de-louer`.
3. **Spine LocService.fr** « Permis de louer » (~680 noms, MAJ ~mensuelle, HTML sans INSEE) →
   géocodage `geo.api.gouv.fr`. Faible confiance (`low` si ambigu, `medium` si nom unique).
   Les noms non résolus (EPCI, quartiers, communes étrangères, coquilles) ne sont **pas** perdus :
   ils vont dans `csv/reglementation-locative/rental-permit-unresolved.csv` pour revue manuelle.

```bash
python -m pipeline.scripts.scrape_rental_permit                 # ré-écrit rental-permit.csv
python -m pipeline.scripts.scrape_rental_permit --skip-locservice  # couches data.gouv only
python -m pipeline.scripts.seed_rent_regulation --dry-run       # vérifier que le CSV élargi parse
```

Dernier run (2026-09-10) : 26 préfecture + 190 couches data.gouv + 482 LocService = **698 communes uniques**,
28 noms non résolus. `seed_rent_regulation --dry-run` : 698/698 lignes appariées dans `cities`.

### POI (OpenStreetMap) — `pipeline/scripts/seed_poi.py` (à relancer **mensuellement**)

Alimente la table `pois` (un point d'intérêt par ligne, rattaché à une commune) à partir
d'un extrait Geofabrik `.osm.pbf`. Le `.pbf` est un fichier de travail jetable (gitignore) :
le re-télécharger et relancer le script suffit à rafraîchir.

Pipeline : `poi_source.py` (stream osmium, nœuds + ways, centroïde pour les ways, `osm_id`
négatif pour les ways) → filtre whitelist ci-dessous → `poi_geo.py` (point-in-polygon sur les
contours communaux `geo.api.gouv.fr`, STRtree ; fallback commune la plus proche < 2 km sinon
drop ; arrondissements Paris/Lyon/Marseille repliés via `ARR_TO_COMMUNE`) → upsert
`ON CONFLICT (osm_id)` par lots de 5000.

Le tag `name` est **obligatoire** (sans nom = drop). Whitelist tag → (catégorie, type) :

| catégorie | match OSM | type |
|---|---|---|
| education | `amenity` ∈ {school, kindergarten, college, university} | la valeur `amenity` |
| health | `amenity` ∈ {hospital, clinic, doctors, pharmacy, dentist} | la valeur `amenity` |
| transport | `railway` ∈ {station, halt, tram_stop} | train_station / train_halt / tram_stop |
| transport | `amenity=bus_station` ; `aeroway=aerodrome` ; `amenity=ferry_terminal` | bus_station / airport / ferry_terminal |
| shopping | `shop` ∈ {supermarket, mall, department_store} | la valeur `shop` |
| culture | `tourism=museum` ; `amenity` ∈ {library, theatre, cinema} | museum / la valeur `amenity` |
| leisure | `leisure` ∈ {sports_centre, stadium, park} | la valeur `leisure` |
| services | `amenity` ∈ {post_office, bank, police, fire_station, townhall} | la valeur `amenity` |

Prérequis schéma : `pois.osm_id` doit être `BigInt` (les ids de nœuds OSM dépassent int4).
`rentium/backend/prisma/schema/poi.prisma` : `osmId Int` → `osmId BigInt`, puis migration Prisma.

```bash
# extrait régional (dev/test) — couvre le 77
curl -L -o csv/poi/ile-de-france-latest.osm.pbf https://download.geofabrik.de/europe/france/ile-de-france-latest.osm.pbf
python -m pipeline.scripts.seed_poi --pbf csv/poi/ile-de-france-latest.osm.pbf --dept 77 --dry-run
python -m pipeline.scripts.seed_poi --pbf csv/poi/ile-de-france-latest.osm.pbf --dept 77

# run national
curl -L -o csv/poi/france-latest.osm.pbf https://download.geofabrik.de/europe/france-latest.osm.pbf
python -m pipeline.scripts.seed_poi --pbf csv/poi/france-latest.osm.pbf --dry-run
python -m pipeline.scripts.seed_poi --pbf csv/poi/france-latest.osm.pbf
```

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

Gérées côté Prisma (`rentium/backend/prisma/schema/*.prisma`). Pour un run direct sans Prisma :
```sql
ALTER TABLE cities ADD COLUMN IF NOT EXISTS price_data_source TEXT;
ALTER TABLE cities ADD COLUMN IF NOT EXISTS retired_count INTEGER;
ALTER TABLE cities ADD COLUMN IF NOT EXISTS unemployed_count INTEGER;
ALTER TABLE cities ADD COLUMN IF NOT EXISTS median_age DOUBLE PRECISION;
ALTER TABLE cities ADD COLUMN IF NOT EXISTS avg_property_tax DOUBLE PRECISION;
ALTER TABLE cities ADD COLUMN IF NOT EXISTS avg_rent_per_sqm DOUBLE PRECISION;
ALTER TABLE cities ADD COLUMN IF NOT EXISTS gross_yield DOUBLE PRECISION;
ALTER TABLE cities ADD COLUMN IF NOT EXISTS rent_control BOOLEAN;
ALTER TABLE cities ADD COLUMN IF NOT EXISTS rental_permit_required BOOLEAN;
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
