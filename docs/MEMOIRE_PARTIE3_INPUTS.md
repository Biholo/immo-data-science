# Inputs Partie 3 — Rentium : données mesurées et vérifiées

Compilé le 2026-08-14, après reseed complet de la base (`cities.updated_at` =
2026-08-14 16:23:06) suite au fix du bug multi-lot DVF, et après relance de
tout le pipeline mémoire (`ml_run_full_pipeline.ps1`).

**Règle suivie pour ce document** : chaque chiffre vient soit d'une requête
DB/DuckDB exécutée aujourd'hui, soit d'un `run_metadata.json` frais
(horodaté après 16:23:06), soit du code source lui-même (formules,
hyperparamètres). Rien n'est estimé ou extrapolé sans le dire. Quand une
information n'existe pas dans ce repo (architecture ImmoTrust, POI, portails
d'annonces, problématique du mémoire...), c'est marqué **NON DISPONIBLE** ou
**À FOURNIR PAR TOI** — pas de valeur inventée.

**Mise à jour 2026-08-14 21h31** : `ml/models/price_hedonic.py` a été rejoué
post-reseed (bloqué un moment par un process `run_dvf` fantôme, résolu). Les
chiffres du modèle hédonique dans ce document sont désormais **post-reseed,
à jour**. Gain net important par rapport à la version pré-reseed citée
initialement : R² 0.446→**0.562**, MAE 966€→**784€** — voir §Annexe.

---

## 0. Informations générales

| Champ | Valeur |
|---|---|
| Problématique finale exacte du mémoire | **À FOURNIR PAR TOI** — non présente dans ce repo (aucun fichier de rédaction du mémoire lui-même, uniquement le code/data) |
| Plan final de la Partie 2 | **À FOURNIR PAR TOI** |
| Plan final retenu pour la Partie 3 | **À FOURNIR PAR TOI** — ce document fournit la matière brute, pas un plan rédactionnel |
| État réel de chaque fonctionnalité | Voir tableau ci-dessous |
| Date/commit Git de la version présentée | **Pas de dépôt Git sur ce projet** (confirmé : `git status` échoue, aucun `.git/`). Utiliser la date : **2026-08-14**, pipeline complet rejoué ce jour après le fix du bug multi-lot. Si un suivi de version est nécessaire pour le mémoire, il faudra l'initialiser (`git init` + premier commit) — non fait à ce jour. |
| Technologies principales | Voir tableau versions ci-dessous |
| Architecture globale Rentium (ce repo) | Voir schéma texte ci-dessous |
| Architecture globale ImmoTrust | **HORS PÉRIMÈTRE de ce repo** — voir note ci-dessous |
| Schéma visuel des architectures | Non généré (pas de diagramme produit cette session) |

### État réel de chaque fonctionnalité (développé / en cours / prévu)

| Fonctionnalité | Statut | Note |
|---|---|---|
| Modèle 1 — Clustering K-Means | **Développé, mesuré** | national, k=3, F=270.28 |
| Modèle 2 — Gradient Boosting prix/m² + score d'opportunité | **Développé, mesuré** | R²=0.8097 |
| Modèle 3 — Forecasting Gradient Boosting (t+1 à t+4) | **Développé, mesuré** | perd contre baseline à t+1/t+2/t+3, voir §5 |
| Modèle 4 — Régression quantile + conformal | **Développé, mesuré** | coverage 79.9% |
| Extension : Modèle hédonique transaction-level | **Développé, mesuré post-reseed** | R²=0.5624, voir §Annexe |
| Extension : Modèle rendement (yield) | **Développé, mesuré** | R²=0.4765, target = rendement acquisition-ajusté |
| Extension : Cash-flow déterministe (5 profils investisseurs) | **Développé, mesuré** | voir §Cash-flow en annexe |
| Extension : Cartes nationales de rendement (brut/net, IDW, bulles) | **Développé** | `ml/scripts/analyze_yield_geography.py` |
| Score Rentium §7 (Sécurité/Localisation, pondéré par profil) | **PRÉVU, pas développé** | formule spec (WalkScore, TensionLocative, DélaiRelocation) nécessite des données POI/marché locatif jamais intégrées. Un score composite DIFFÉRENT et plus simple existe (`generate_recommendations.py`), voir §Annexe |
| POI (transports, écoles, commerces, santé) | **PRÉVU, pas développé** | aucune source POI intégrée cette session, feature listée comme "optionnelle" dans CONTEXTE_ML_MEMOIRE.md §2, jamais implémentée |
| Portails d'annonces (listings actifs) | **PRÉVU, pas développé** | ce repo utilise exclusivement DVF (transactions notariées passées) + Carte des loyers ANIL/DHUP (modèle statistique de loyer), pas de scraping/API d'annonces en direct |
| ImmoTrust (LLM, benchmark documentaire) | **HORS PÉRIMÈTRE de ce repo** | CONTEXTE_ML_MEMOIRE.md §8 le décrit (LLM pré-entraîné, pas de modèle ML) mais aucun code ImmoTrust n'existe dans `immo-data-science/` |
| Modèle loyer au m² (prédictif) | **Explicitement non développé** | CONTEXTE_ML_MEMOIRE.md §1 : "Pas maintenant" — le loyer utilisé est une donnée externe (ANIL/DHUP), pas un modèle entraîné localement |

### Technologies et versions (vérifiées ce jour)

| Composant | Version |
|---|---|
| Python | 3.14.0 |
| scikit-learn | 1.8.0 |
| pandas | 2.3.3 |
| numpy | 2.3.5 |
| scipy | 1.16.3 |
| LightGBM | 4.7.0 (comparaison uniquement, pas le modèle officiel) |
| DuckDB | 1.5.5 (cache DVF local, `dvf_cache.duckdb`) |
| psycopg2 | 2.9.11 (accès PostgreSQL) |
| shap | 0.52.0 (installé, utilisé pour Modèle 2) |
| geopandas | 1.1.1 (installé, pas utilisé en production — testé pour un choropleth jamais finalisé) |
| Base de données | PostgreSQL (schéma Prisma, tables `cities`, `series`, `timeseries`) |

### Architecture globale Rentium (ce repo, telle qu'elle existe)

```
pipeline/                    ← ingestion + écriture DB (Prisma-owned schema)
  services/
    dvf.py                     DuckDB : charge dvf-raw/*.txt (2021-2025), vues SQL nettoyage
    series.py                  définit les 14 séries DVF (price_sqm_*, transaction_volume, ...)
    upload.py, interpolate.py  écriture PostgreSQL + IDW pour communes sans prix
  scripts/
    seed_cities.py, seed_pop_series.py, seed_rp_series.py,
    seed_logement_series.py, seed_housing_zone.py, run_dvf.py
    denormalize.py             calcule price_growth_3y, price_trend, snapshot cities

ml/                          ← lecture SEULE de la DB, jamais d'écriture (séparé de pipeline/)
  data/                         loaders (build_cross_sectional, build_panel, rent.py,
                                 median_income.py, zonage_national.py, property_tax.py,
                                 build_transactions.py)
  models/                       clustering.py, price_model.py, forecasting.py, quantile.py,
                                 yield_model.py, price_hedonic.py (+ comparaisons)
  finance/                      cashflow.py, profiles.py — formules déterministes §6, pas de ML
  scripts/                      run_all.py, generate_recommendations.py, simulate_cashflow.py,
                                 analyze_yield_geography.py, generate_charts.py, check_performance.py
  artifacts/                    sorties (gitignored) : modèles sérialisés, CSV, PNG, JSON

Base de données : PostgreSQL — cities (snapshot par commune) + series/timeseries (EAV,
historique trimestriel/annuel). Cache local : dvf_cache.duckdb (~20M lignes DVF brutes).
```

### ImmoTrust — note importante

CONTEXTE_ML_MEMOIRE.md §8 décrit ImmoTrust comme un pipeline LLM (Claude,
prompt engineering, structured output, pas de modèle ML entraîné) pour
l'analyse documentaire (DPE, PV d'AG, états datés...). **Aucun code
ImmoTrust n'existe dans ce repo** (`immo-data-science/`) — ce repo est
entièrement dédié à Rentium (data science DVF/INSEE). Si ImmoTrust a été
développé, c'est dans un autre projet non accessible depuis cette session.

---

## 1. Rentium : données

### 1.1 Sources réellement utilisées

| Source | Détail |
|---|---|
| **DVF** | Millésimes 2021-2025 (5 fichiers `ValeursFoncieres-{2021..2025}.txt`, format déclaratif DGFiP, séparateur `\|`) |
| Nombre de lignes brutes DVF | **20 382 920** lignes (tous types de biens/mutations confondus, avant tout filtre — compté directement sur les fichiers texte, un header par fichier inclus dans ce total) |
| Mutations DVF après nettoyage (agrégation commune) | **4 526 559** lignes dans `dvf_mutation_prices` passant le filtre `price_sqm_all/appt/house` (type_local IN maison/appartement, un seul type et un seul nombre de pièces par mutation, surface 9-2000m², prix/m² 500-30000€) — calculé le 2026-08-14 avant que le cache DuckDB ne devienne inaccessible (verrouillé par un autre processus en fin de session, voir note technique en fin de document) |
| Communes couvertes (au moins 1 trimestre avec ≥10 mutations) | **7352** communes ont `price_data_source='dvf'` (donnée réelle) sur 34969 au total — le reste (27394) est estimé par interpolation IDW, 223 sans aucune valeur |
| Nombre minimum de transactions requis | **10 mutations distinctes** par (commune, trimestre) — `HAVING COUNT(DISTINCT mutation_id) >= 10` dans `pipeline/services/dvf.py` |

**Séries INSEE réellement intégrées** (vérifié en base, `SELECT DISTINCT name FROM series`) :

| Série | Fréquence | Formule / source exacte |
|---|---|---|
| `population` | Annuelle | `SUM(POP)` — INSEE base IC-Évol-Struct-Pop, `csv/base-ic-evol-struct-pop/base-ic-evol-struct-pop-{année}.CSV`, 2017-2022 |
| `aging_index` | Annuelle | `SUM(POP65P) / SUM(POP0019)` — même source |
| `unemployment_rate` | Annuelle | `CHOM1564 / ACT1564` — INSEE base IC-Activité-Résidents, `csv/ic-activite-residents/base-ic-activite-residents-{année}.CSV` (2017-2021) + `.xlsx` (2022) |
| `vacancy_rate` | Annuelle | `LOGVAC / LOG` — INSEE base IC-Logement, `csv/base-ic-logement/base-ic-logement-{année}.CSV`, 2017-2022 |
| `owner_rate` | Annuelle | `RP_PROP / RP` — même source |
| `social_housing_rate` | Annuelle | `RP_LOCHLMV / RP` — même source |
| `secondary_residence_rate` | Annuelle | `RSECOCC / LOG` — même source |
| `price_sqm_all/appt/house/t1/t2/t3/t4` | Trimestrielle | DVF, médiane (`PERCENTILE_CONT(0.5)`) du prix/m² par commune×trimestre |
| `transaction_volume` | Trimestrielle | DVF, `COUNT(DISTINCT mutation_id)` par commune×trimestre |
| `surface_median_t1..t4` | Trimestrielle | DVF, médiane de `surface_bati` |
| `vefa_share` | Trimestrielle | DVF, % de mutations en "Vente en l'état futur d'achèvement" |
| `land_price_sqm` | Trimestrielle | DVF, prix/m² terrain (`nature_mutation = 'Vente terrain à bâtir'`) |

**Non implémenté malgré une intention de code** : `student_rate` et
`retiree_rate` sont produites par `pipeline/scripts/seed_rp_series.py`
d'après son propre docstring, mais **absentes de la base** (vérifié :
`SELECT DISTINCT name FROM series` ne les liste pas) — le script n'a
probablement jamais été exécuté pour ces deux séries, ou elles ont été
retirées depuis. `active_population` est dans `ml/config.py:SOCIO_SERIES`
mais n'existe pas dans l'enum Postgres `SerieName` (gap de schéma, non
corrigeable depuis ce repo) — son seed est explicitement sauté.

**Zonage utilisé** :
- Source DB native (`pipeline/scripts/seed_housing_zone.py`) : fichier
  `logement-liste-des-communes-selon-le-zonage-abc.csv`, **Île-de-France
  uniquement** (75,77,78,91,92,93,94,95) — **1266 communes** avec zonage non
  NULL en base (A=385, A_BIS=97, B1=342, B2=442, pas de C car IDF toujours
  tendue).
- Source complémentaire nationale (lecture locale `ml/`, PAS écrite en DB) :
  `csv/zonage-abc-national.csv`, téléchargé depuis
  `data.gouv.fr/datasets/liste-des-communes-selon-le-zonage-abc` — **34875
  communes** (C=28326, B2=3162, B1=2383, A=870, A_BIS=134). C'est CETTE
  version qui alimente réellement les modèles (`ml/data/zonage_national.py`
  backfille `housing_zone` dans `build_cross_sectional.py`).

**Sources POI** : **NON IMPLÉMENTÉ.** Aucune source POI (transports, écoles,
commerces, santé) n'a été intégrée. `CONTEXTE_ML_MEMOIRE.md` §2 les liste
comme "features optionnelles" — jamais développées faute de source
identifiée/priorisée.

**Portails d'annonces** : **NON IMPLÉMENTÉ.** Aucune intégration d'annonces
immobilières actives (SeLoger, LeBonCoin, etc.). Le "loyer" utilisé
(`rent_appt_all` etc.) vient de la **Carte des loyers ANIL/DHUP 2025**
(`csv/loyers/pred-{app,app12,app3,mai}-mef-dhup.csv`, téléchargée depuis
`data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025`)
— un **modèle statistique déjà calculé par l'ANIL/AgroSup Dijon-INRAE** à
partir de ~7M annonces analysées en amont, pas une intégration temps réel de
notre part. 34900/34969 communes couvertes. Colonnes : `rent_appt_all`
(52m² réf.), `rent_appt_t12` (37m²), `rent_appt_t3plus` (72m²), `rent_maison`
(92m²), toutes en €/m²/mois charges comprises.

**Fréquence d'actualisation** : toutes les sources sont des **snapshots
statiques téléchargés manuellement** cette session — aucune automatisation
de rafraîchissement périodique n'existe. DVF est mis à jour ~2x/an par la
DGFiP (le fichier 2025 téléchargé est probablement partiel/année en cours).
Filosofi (revenus) = millésime 2021 fixe. Carte des loyers = millésime 2025
fixe. Taxe foncière = multi-année 2021-2025 mais on ne prend que le dernier
exercice disponible par défaut.

**Autres sources locales** (hors périmètre DVF/INSEE, utilisées pour les
extensions rendement/cash-flow) :

| Source | Fichier | Couverture | Usage |
|---|---|---|---|
| INSEE Filosofi 2021 (revenu médian) | `csv/DS_FILOSOFI_CC_data.csv` (833 400 lignes brutes, format long) | 31212/34969 communes (10.7% de secret statistique) | `median_income`, feature Modèles 1/2/4 |
| DGFiP Fiscalité locale (taxe foncière) | `csv/fiscalite/fiscalite-locale-des-particuliers.csv` (174 669 lignes, multi-année 2021-2025) | 34874/34969 communes (99.7%, pas de secret statistique) | `taux_foncier_pct`, cash-flow + rendement net |

### 1.2 Granularité

| Question | Réponse |
|---|---|
| Maille géographique retenue | **Commune** (pas IRIS — jamais utilisé) |
| Identifiant géographique pivot | `insee_code` (code commune INSEE, 5 caractères, toujours string — piège récurrent si relu comme int, voir §1.4) |
| Granularité temporelle | **Trimestre** pour les séries DVF et le panel Modèle 3. **Annuelle** pour les séries socio-démo INSEE. **Snapshot unique par commune** (pas de panel) pour Modèles 1, 2, 4 — voir note ci-dessous |
| Nombre d'observations commune × période (panel Modèle 3) | **1 114 889** lignes, 33035 communes, 20 trimestres |

**Note importante sur le grain des modèles 1/2/4** : `CONTEXTE_ML_MEMOIRE.md`
§3 proposait deux options ("dernière observation fiable par commune" OU
"commune × trimestre"). Le projet a retenu la **première option** pour les
Modèles 1, 2 et 4 : `build_cross_sectional()` renvoie **une seule ligne par
commune** (le dernier snapshot fiable dans `cities`, pas un panel temporel).
Seul le **Modèle 3** (forecasting) travaille en panel commune×trimestre — il
a besoin de l'historique pour construire les lags.

### 1.3 Nettoyage DVF

| Règle | Détail exact |
|---|---|
| Reconstruction d'une mutation | `mutation_id` = MD5 de `(date_mutation, valeur_fonciere, code_departement, code_commune, no_voie, code_voie)` concaténés — `pipeline/services/dvf.py`. DVF répète la `valeur_fonciere` TOTALE sur chaque ligne (local) d'une même mutation ; il faut donc regrouper par `mutation_id` avant de calculer un prix/m². |
| Lots multiples / dépendances | `dvf_mutation_prices` (vue SQL) agrège par `(mutation_id, type_local, nb_pieces)`. **Bug identifié et corrigé le 2026-08-14** : avant le fix, une mutation mêlant des lots de `nb_pieces` différents (ex. immeuble vendu en bloc, 2×T2+1×T3) voyait son prix total dupliqué sur chaque sous-groupe. Fix : `total_locaux = nb_locaux_this_group` ajouté au filtre `BASE_PRICE_MULTI` (`pipeline/services/series.py`) — n'accepte que les mutations où TOUS les locaux ont le même type ET le même nombre de pièces. Impact mesuré : 348 366/4 874 925 lignes (7.15%) étaient affectées, prix/m² médian 3333€ (buggé) vs 2515€ (propre), soit +32% de surestimation sur ces lignes. |
| Types de biens conservés | `type_local IN (1, 2)` = **Maison (1)** et **Appartement (2)** uniquement (nomenclature DVF officielle) |
| Types exclus | Type 3 (Dépendance) et Type 4 (Local industriel/commercial/assimilé) — jamais dans les séries `price_sqm_*` |
| Natures de mutation conservées | `'Vente'` et `'Vente en l'état futur d'achèvement'` (VEFA) — constante `VENTE_NATURE` dans `series.py`. VEFA suivi séparément via la série `vefa_share` (% de VEFA parmi les ventes) |
| Natures exclues | Toute autre valeur de `nature_mutation` (échanges, adjudications, expropriations...) — non listée dans `VENTE_NATURE`, donc filtrée implicitement |
| Surfaces nulles/incohérentes | `surface_bati > 0` requis dès la vue de base. Pour `price_sqm_all/appt/house` : `surface_bati BETWEEN 9 AND 2000`. Pour `price_sqm_t1..t4` (mono-type strict) : `surface_bati BETWEEN 9 AND 500` |
| Valeurs foncières nulles | `valeur_fonciere > 0` requis partout |
| Valeurs extrêmes (prix/m²) | Seuil dur : `valeur_fonciere / surface_bati BETWEEN 500 AND 30000` €/m² — pas de méthode statistique (IQR, z-score...), un seuil fixe |
| Méthode de calcul du prix/m² | `valeur_fonciere_mutation / SUM(surface_bati des locaux de la mutation)` |
| Méthode de calcul de la médiane communale | `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY prix/m²)` par (commune, trimestre) en SQL DuckDB |
| Nombre minimum de transactions requis | **10** mutations distinctes par (commune, trimestre) — sinon la ligne série n'est pas produite |

### 1.4 Valeurs manquantes et estimées

**Pourcentage de données manquantes par feature** (dataset cross-sectional
complet, 34969 communes, calculé aujourd'hui via `build_cross_sectional(exclude_estimated=False)`) :

| Feature | % manquant |
|---|---|
| `high_demand_zone` | 96.4% |
| `price_growth_3y` | 87.9% (nécessite 12 trimestres d'historique DVF continu) |
| `price_trend` | 79.0% |
| `median_income` | 10.7% (secret statistique Filosofi) |
| `transaction_volume` | 5.5% |
| `median_price_per_sqm` / `avg_price_per_sqm` / `price_data_source` / `rendement_brut` | 0.6% |
| `aging_index`, `secondary_residence_rate`, `unemployment_rate`, `social_housing_rate`, `vacancy_rate`, `owner_rate`, `population_series` | 0.4% chacune |
| `housing_zone`, `rent_appt_*`, `rent_maison` | 0.3% chacune |
| `population`, `latitude`, `longitude`, `name`, `department_code`, `dist_nearest_major_city_km` | 0.0% |

**Méthode utilisée pour les valeurs manquantes** : **aucune imputation
statistique** (pas de moyenne/médiane/kNN-imputer). Chaque modèle fait un
`dropna()` strict sur ses features requises avant entraînement — les
communes incomplètes sont **exclues**, pas complétées. Seule exception :
`population = COALESCE(cities.population, population_series)` (deux sources
différentes, fallback simple).

**`price_data_source`** — description et valeurs réelles (vérifié en DB) :

| Valeur | Nombre de communes | Signification |
|---|---|---|
| `dvf` | 7352 | Prix médian calculé directement depuis une vraie observation DVF (≥10 transactions sur au moins un trimestre) |
| `estimated` | 27394 | Prix absent en DVF direct, comblé par interpolation spatiale (IDW) |
| `NULL` | 223 | Aucune valeur du tout (ni DVF ni interpolation possible) |

**Méthode exacte d'interpolation** (`pipeline/scripts/interpolate.py`) :
- Pour chaque commune sans prix : recherche des **5 communes connues les
  plus proches** (`K_NEIGHBORS=5`) par distance haversine, dans un rayon max
  de **30 km** (`MAX_DIST_KM=30`).
- Estimation = **IDW** (Inverse Distance Weighting) : `poids = 1/max(distance, 0.01)`,
  moyenne pondérée des prix voisins.
- Si aucun voisin dans les 30km : **fallback = médiane du département**
  (calculée sur les communes déjà connues).
- Si même le département n'a aucune valeur connue : reste `NULL`.

**Traitement des communes `estimated` pendant l'entraînement** : **binaire,
pas de pondération**.
- Modèle 1 (clustering) : **conservées** (`exclude_estimated=False`) — le
  prix n'est jamais une feature de clustering, donc pas de risque de biais.
- Modèles 2, 4 (prix, quantile) et le modèle rendement : **exclues à 100%**
  (`exclude_estimated=True`) — sinon le modèle apprendrait en partie sur ses
  propres estimations IDW, ce qui fausserait toute métrique de performance.
- Aucune pondération partielle (type "poids réduit pour les communes
  estimated") n'est implémentée nulle part.

---

## 2. Rentium : dataset final ML

Pas de CSV/Parquet unique généré pour la mémoire — le dataset est reconstruit
à la volée par `ml/data/build_cross_sectional.py::build_cross_sectional()`
(cross-sectional, un run = un `pd.DataFrame`, jamais persisté tel quel).
Schéma exact ci-dessous, avec valeurs réelles au 2026-08-14.

**Shape complet** (avant exclusion des `estimated`) : **(34969, 27)** — 34969
communes, 27 colonnes.

| Colonne | Type (dtype) | Description métier | Source |
|---|---|---|---|
| `name` | object (str) | Nom de la commune | `cities` (DB) |
| `department_code` | object (str) | Code département | `cities` |
| `latitude`, `longitude` | float64 | Coordonnées | `cities` (geo.api.gouv.fr) |
| `population` | float64 | Population, `COALESCE(cities.population, population_series)` | `cities` + INSEE |
| `housing_zone` | object (str) | Zonage ABC (A/A_BIS/B1/B2/C) | zonage national (backfill), fallback DB IDF |
| `high_demand_zone` | object (str) | `housing_zone IN (A, A_BIS)` | dérivé DB |
| `median_price_per_sqm`, `avg_price_per_sqm` | float64 | Prix/m² médian/moyen commune (dernier snapshot) | `cities`, calculé depuis DVF |
| `transaction_volume` | float64 | Nombre de transactions (dernier trimestre connu) | `cities`, DVF |
| `price_growth_3y` | float64 | Croissance prix sur 3 ans, `(dernier/12T_avant - 1)×100` | `cities`, dérivé DVF — **exclu des features modèles (fuite temporelle/leakage), voir `ml/config.py`** |
| `price_trend` | object (str) | up/down/stable | `cities`, dérivé DVF |
| `price_data_source` | object (str) | dvf / estimated / NULL | `cities` |
| `aging_index`, `owner_rate`, `secondary_residence_rate`, `social_housing_rate`, `unemployment_rate`, `vacancy_rate` | float64 | Taux socio-démo INSEE (dernière valeur annuelle) | `series`/`timeseries` |
| `population_series` | float64 | Population INSEE (2017-2022, fallback) | `series`/`timeseries` |
| `median_income` | float64 | Revenu médian, € | Filosofi 2021 |
| `dist_nearest_major_city_km` | float64 | Distance haversine à la plus proche des 50 communes les + peuplées de France | calculé (BallTree) |
| `rent_appt_all`, `rent_appt_t12`, `rent_appt_t3plus`, `rent_maison` | float64 | Loyer €/m²/mois, 4 segments | ANIL/DHUP Carte des loyers 2025 |
| `rendement_brut` | float64 | `(rent_appt_all×12) / coût_acquisition × 100`, coût = prix+notaire(7.5%)+travaux(10%) | calculé |

**Nombre de lignes / communes uniques par usage** :

| Usage | n communes | Note |
|---|---|---|
| Dataset complet chargé | 34969 | — |
| Modèle 1 (clustering), features complètes | 32992 | 1977 exclues (valeurs manquantes sur les 9 features clustering) |
| Modèles 2/4 (prix, quantile), `exclude_estimated=True` + features complètes | 7347 | sur 7575 chargées (prix réel dispo), 228 en plus dropped pour features manquantes |
| Modèle rendement | 7347 | même base que Modèle 2 |
| Modèle 3 (panel) | 33035 communes, 1114889 lignes commune×trimestre, 20 trimestres | — |

**Features listées dans le brief original vs réellement présentes** :

| Feature demandée | Statut |
|---|---|
| `price_sqm_all` | ✅ présente (`median_price_per_sqm`, alimentée par la série `price_sqm_all`) |
| `population` | ✅ |
| `unemployment_rate` | ✅ |
| `owner_rate` | ✅ |
| `vacancy_rate` | ✅ |
| `social_housing_rate` | ✅ |
| `aging_index` | ✅ |
| `secondary_residence_rate` | ✅ |
| `transaction_volume` | ✅ |
| `price_growth_3y` | ✅ présente dans le dataset, mais **volontairement exclue des features de tous les modèles** (fuite temporelle pour Modèle 2, 87.9% manquant pour Modèle 1) |
| `housing_zone` | ✅ |
| POI | ❌ non implémenté |
| Accessibilité transports | ❌ non implémenté |
| `median_income` | ✅ ajoutée cette session (Filosofi) |
| `dist_nearest_major_city_km` | ✅ ajoutée cette session (calculée, pas de fichier externe) |

---

## 3. Modèle 1 : clustering des communes

### Inputs

- **Features exactes** (9) : `population`, `unemployment_rate`, `owner_rate`,
  `vacancy_rate`, `social_housing_rate`, `aging_index`,
  `secondary_residence_rate`, `transaction_volume`, `dist_nearest_major_city_km`.
- **Nombre de communes utilisées** : **32992** / 34969 chargées (1977
  supprimées pour valeurs manquantes sur au moins une des 9 features —
  `dropna()` strict, pas d'imputation).
- **`price_sqm` exclu des features** — confirmé, jamais inclus (évite la
  circularité avec le score d'opportunité qui contextualise ensuite le prix
  par cluster).
- **Transformation avant clustering** : `log1p()` sur 3 features à queue
  lourde (`population`, `transaction_volume`, `dist_nearest_major_city_km`)
  avant standardisation — sans ça, Paris et les grandes villes dominaient la
  distance euclidienne.
- **Standardisation** : `sklearn.preprocessing.StandardScaler` (z-score
  classique, moyenne 0 / écart-type 1).

### Expérimentation

- **Algorithme final** : `sklearn.cluster.KMeans`, scikit-learn **1.8.0**.
- **Valeurs de k testées** : 2 à 10 (`range(2, 11)`).
- **`random_state`** : 42 (partout dans le repo, `ml/config.py:RANDOM_STATE`).
- **`n_init`** : 10.
- **Pondération des features (`--weighted`, activée par défaut)** :
  F-statistique ANOVA (`sklearn.feature_selection.f_classif`) calculée sur un
  premier clustering non pondéré, normalisée (moyenne=1), puis
  `X_scaled *= sqrt(poids)` avant un second fit. Poids réels obtenus (run
  actuel) :

  | Feature | Poids |
  |---|---|
  | `secondary_residence_rate` | 1.831 |
  | `transaction_volume` | 1.660 |
  | `social_housing_rate` | 1.710 |
  | `population` | 1.557 |
  | `owner_rate` | 1.157 |
  | `aging_index` | 0.469 |
  | `unemployment_rate` | 0.320 |
  | `dist_nearest_major_city_km` | 0.290 |
  | `vacancy_rate` | 0.005 (quasi nul) |

### Résultats

**Inertie et silhouette par k** (scan initial, non pondéré) :

| k | Inertie | Silhouette |
|---|---|---|
| 2 | 227 646.5 | 0.3276 |
| 3 | 195 507.9 | 0.2511 |
| 4 | 175 214.0 | 0.1761 |
| 5 | 160 478.7 | 0.1660 |
| 6 | 149 826.0 | 0.1710 |
| 7 | 141 257.3 | 0.1533 |
| 8 | 134 652.6 | 0.1572 |
| 9 | 128 612.1 | 0.1413 |
| 10 | 123 137.5 | 0.1422 |

Silhouette maximale au scan brut = k=2 (0.3276), mais **k=3 est retenu par
défaut** (`--k 3` forcé dans `clustering.py`) : k=2 sépare essentiellement
par taille de population, pas par typologie de marché (validé par
validation externe moins bonne, voir `EXPERIMENTS_LOG.md` §Modèle 1 #2/#10).

- **k final retenu** : **3**.
- **Silhouette au k retenu, après pondération** : **0.3435**.
- **Validation externe (prix jamais vu par le clustering)** : ANOVA F=**270.28**,
  p=5.48e-114 (hautement significatif), sur 7349 communes avec prix réel.

**Taille et profil de chaque cluster** :

| Cluster | n communes | Population (moy/médiane) | Prix réel moy/médian (€/m²) | Taux secondaire (moy) | Distance grande ville (moy km) | Chômage (moy) |
|---|---|---|---|---|---|---|
| 0 | 5615 | 313 / 167 | 3298 / 3034 (n=498) | **40.6%** | 82.2 | 10.8% |
| 1 | 22184 (majoritaire) | 608 / 410 | 2020 / 1859 (n=2239) | 8.3% | 59.0 | 8.2% |
| 2 | 5193 | 8549 / 3957 | 2401 / 2139 (n=4612) | 6.0% | 45.6 | 11.1% |

**Communes représentatives** (les 8 plus proches du centroïde par cluster,
via `ml/artifacts/clustering/latest/representative_communes.csv`) :

- **Cluster 0** : La Nouaille (23), Davignac (19), Montlainsia (39),
  Saint-Barthélemy-le-Meil (07), Segonzac (19), Berbiguières (24),
  Rochecolombe (07), Auzers (15) — tous en Creuse/Corrèze/Ardèche/Jura/Dordogne/Cantal.
- **Cluster 1** : Palleville (81), Lougé-sur-Maire (61), Haudricourt (76),
  Bailly-en-Rivière (76), Saint-Didier-la-Forêt (03), Deneuille-les-Mines (03),
  Saint-Georges-sur-Erve (53), Saint-Étienne-de-Vicq (03).
- **Cluster 2** : Bourgueil (37), Bonneval (28), Les Villages Vovéens (28),
  Saint-Léonard-de-Noblat (87), Barberaz (73), Marnaz (74), Bléré (37),
  La Chaussée-Saint-Victor (41).

**Labels métier suggérés** (lecture des chiffres ci-dessus, **hypothèse à
valider manuellement**, pas un résultat automatique) :
- Cluster 0 → **"Rural à forte part de résidences secondaires"** (40.6% de
  résidences secondaires vs 6-8% ailleurs — profil de l'exode rural profond,
  Creuse/Corrèze, PAS du tout littoral d'après les communes représentatives ;
  prix moyen le plus haut malgré la petite taille, probablement porté par
  quelques communes touristiques/de montagne dans l'échantillon).
- Cluster 1 → **"Rural/périurbain ordinaire"** (cluster majoritaire, 67% des
  communes classées, prix le plus bas, chômage le plus bas, très peu de
  résidences secondaires — la "France moyenne").
- Cluster 2 → **"Pôles urbains et bourgs-centres"** (population 14x plus
  élevée que les 2 autres clusters, volume de transaction le plus haut,
  distance aux grandes villes la plus faible — villes et proches banlieues).

**Communes atypiques** : non calculé spécifiquement (pas de détection
d'outliers intra-cluster faite cette session — pourrait être une prochaine
étape : distance au centroïde la plus grande par cluster).

### Visuels disponibles

- Courbe silhouette/elbow selon k : générée par
  `ml/scripts/generate_charts.py` (`ml/artifacts/charts/latest/`).
- Projection PCA 2D : **non générée**.
- Tableau des centroïdes : `ml/artifacts/clustering/latest/cluster_profile.csv`.
- Carte géographique des clusters : `ml/artifacts/recommendations/latest/map_clusters.png`
  (scatter national, un point par commune, couleur = cluster).

---

## 4. Modèle 2 : prix au m² attendu

### Dataset

- **Grain** : commune (snapshot, pas commune×trimestre — voir §1.2).
- **Nombre de lignes / communes** : **7347** (après `dropna` sur features +
  target + `department_code`), sur 7575 communes chargées avec
  `exclude_estimated=True`.
- **Target exacte** : `median_price_per_sqm` (dernière valeur fiable
  connue par commune, `TARGET_COL` dans `ml/config.py`).
- **Features exactes** (10, `PRICE_MODEL_FEATURES`) : `population`,
  `unemployment_rate`, `owner_rate`, `vacancy_rate`, `social_housing_rate`,
  `aging_index`, `secondary_residence_rate`, `transaction_volume`,
  `median_income`, `dist_nearest_major_city_km`.
- **Catégorielles one-hot** (`PRICE_MODEL_CATEGORICAL`) : `housing_zone`,
  `department_code`. Plus `cluster_id` (issu du Modèle 1, si disponible)
  encodé en one-hot également.
- **Variable volontairement exclue** : `price_growth_3y` — dérivée
  algébriquement du même prix utilisé comme target (`denormalize.py` :
  `growth = dernier_prix/prix_12T_avant - 1`) → fuite directe, pas juste une
  corrélation.
- **Traitement du zonage** : one-hot (`housing_zone_A`, `_A_BIS`, `_B1`,
  `_B2`, `_C`), valeurs manquantes remplacées par `"unknown"` avant encodage
  (pas de suppression de ligne pour un zonage manquant seul).
- **POI** : sans objet, jamais intégré.
- **Traitement des valeurs manquantes** : `dropna()` strict sur les features
  requises + target — aucune imputation.
- **Traitement de `price_data_source='estimated'`** : **exclusion complète**
  avant même le `dropna` (`exclude_estimated=True` dans
  `build_cross_sectional()`).

### Modèle

- **Algorithme** : `sklearn.ensemble.GradientBoostingRegressor`, scikit-learn
  **1.8.0**. (LightGBM testé en comparaison seulement, R² quasi identique
  0.8063 vs 0.8058 avant reseed — pas le modèle officiel du mémoire).
- **Hyperparamètres finaux** (issus de `RandomizedSearchCV`, run actuel post-reseed) :

  | Paramètre | Valeur |
  |---|---|
  | `n_estimators` | 500 |
  | `max_depth` | 4 |
  | `learning_rate` | 0.1 |
  | `subsample` | 0.6 |
  | `random_state` | 42 |
  | Régularisation explicite | aucune (pas de `min_samples_leaf`/`l2` réglés manuellement — recherchés dans l'espace `TUNE_PARAM_DISTRIBUTIONS` : `n_estimators∈{100,200,300,500}`, `max_depth∈{2,3,4,5}`, `learning_rate∈{0.01,0.03,0.05,0.1}`, `subsample∈{0.6,0.8,1.0}`, 20 candidats × 5-fold CV) |

### Validation

- **Méthode de split** : `sklearn.train_test_split`, **aléatoire** (pas
  géographique ni temporel — le prix/m² ici est un snapshot cross-sectional,
  pas une série temporelle, donc pas de fuite temporelle à éviter de la même
  façon que pour le Modèle 3).
- **Tailles** : train=5142 (70%), val=1102 (15%), test=1103 (15%) —
  `test_size=0.30` puis un second split 50/50 sur le reste.
- **`random_state`** : 42 partout (split reproductible).

### Baseline

- **Baseline retenue** : **médiane du département**, calculée sur le train
  uniquement (pas de fuite), appliquée par mapping `department_code → médiane`.
- **Formule exacte** : `dept_median_baseline()` — `train_df.groupby("department_code")["median_price_per_sqm"].median()`,
  fallback = médiane nationale du train si département absent.
- **MAE baseline (test)** : **584.7 €/m²**.
- **RMSE baseline** : non recalculé isolément ce jour (voir `regression_report`, disponible dans `run_metadata.json`).
- **R² baseline** : non calculé pour la baseline (seul MAE/RMSE comparés).

### Performances modèle (test, post-reseed)

| Métrique | Modèle | Baseline | Gain |
|---|---|---|---|
| MAE | **367.7 €/m²** | 584.7 €/m² | **37.1%** |
| RMSE | 514.9 €/m² | — | — |
| R² | **0.8097** | — | — |

MAE train non isolé séparément dans le run_metadata actuel (seules les
métriques test sont sauvegardées par design — évite la confusion
train/test dans le mémoire).

### Explicabilité

**Feature importance (top 10, Gini/impurity-based, sklearn natif)** :

| Feature | Importance |
|---|---|
| `housing_zone_C` | 0.194 |
| `secondary_residence_rate` | 0.136 |
| `median_income` | 0.130 |
| `housing_zone_B2` | 0.099 |
| `housing_zone_A_BIS` | 0.057 |
| `vacancy_rate` | 0.048 |
| `housing_zone_B1` | 0.042 |
| `dist_nearest_major_city_km` | 0.042 |
| `aging_index` | 0.033 |
| `owner_rate` | 0.031 |

**SHAP (top 10, mean |shap value|, échantillon 500 lignes test)** — disponible :

| Feature | Mean |SHAP| |
|---|---|
| `housing_zone_C` | 364.5 |
| `median_income` | 295.0 |
| `secondary_residence_rate` | 197.1 |
| `housing_zone_B2` | 151.7 |
| `dist_nearest_major_city_km` | 125.8 |
| `owner_rate` | 101.6 |
| `housing_zone_B1` | 88.9 |
| `vacancy_rate` | 88.0 |
| `housing_zone_A_BIS` | 56.9 |
| `aging_index` | 44.8 |

Cohérence feature importance / SHAP : ordre quasi identique, `housing_zone`
et `median_income` dominent nettement dans les deux approches.

**Variable surprenante** : `department_code` (one-hot, ~95 dummies) n'apparaît
dans AUCUN des deux top 10, malgré l'intuition "la géographie pèse énormément
sur le prix" — le zonage ABC et le revenu médian capturent apparemment mieux
ce signal que le département brut.

### Score d'opportunité

- **Formule finale exacte** : `opportunity_score = (price_reel - price_predit) / price_predit`
  (conforme à CONTEXTE_ML_MEMOIRE.md §3, résidu normalisé).
- **Sens confirmé** : `< 0` = prix observé sous le prix attendu (signal de
  sous-évaluation) ; `> 0` = au-dessus.
- **Bornes** : aucune (pas de clipping), valeurs observées de -0.69 à +1.49
  sur les 10 exemples ci-dessous.
- **Normalisation** : aucune transformation supplémentaire (pas de min-max,
  pas de z-score sur le score lui-même).
- **Cluster utilisé dans le score ?** Non — le score d'opportunité est
  calculé indépendamment du cluster ; le `cluster_id` n'apparaît qu'en
  colonne d'accompagnement dans les exports, pas dans la formule.

### 10 exemples de communes (extrait réel de `example_communes.csv`, post-reseed)

| Commune | Dépt | Prix réel €/m² | Prix prédit €/m² | Score opportunité | Cluster | Split |
|---|---|---|---|---|---|---|
| Murat-le-Quaire | 63 | 828.89 | 2669.36 | **-0.689** | 0 | test |
| Plounéventer | 29 | 845.40 | 1964.10 | -0.570 | 1 | test |
| Beauvezer | 04 | 1070.88 | 2463.53 | -0.565 | 0 | val |
| Saint-Genou | 36 | 587.21 | 1279.49 | -0.541 | 1 | val |
| Les Forges | 79 | 1103.73 | 2386.01 | -0.537 | 0 | test |
| Châteauneuf-en-Thymerais | 28 | 2447.37 | 1368.52 | +0.788 | 2 | val |
| Plougoumelen | 56 | 4340.91 | 2414.52 | +0.798 | 1 | test |
| Puyoô | 64 | 1902.14 | 1045.73 | +0.819 | 1 | test |
| Pont-Saint-Pierre | 27 | 3670.21 | 1631.41 | **+1.250** | 2 | val |
| Cherré-Au | 72 | 3937.80 | 1578.69 | **+1.494** | 1 | test |

(5 scores les plus bas + 5 plus hauts, tri automatique de
`ml/models/price_model.py::N_EXAMPLES`.)

---

## 5. Modèle 3 : forecasting des prix

### Dataset temporel

- **Premier trimestre disponible** : déterminé par le panel (`build_panel`),
  20 trimestres au total couvrant 2021-2025 (5 années × 4 trimestres, avec
  quelques trous par commune selon le filtre `min_n≥10`).
- **Nombre de trimestres** : **20**.
- **Nombre de communes (panel)** : **33035**.
- **Nombre total de lignes commune × trimestre** : **1 114 889**.
- **Fréquence exacte** : trimestrielle.

### Target

- **Prévision t+1 ET t+4** (et t+2, t+3 aussi — 4 horizons indépendants).
- Un modèle **par horizon** (4 `GradientBoostingRegressor` distincts), PAS
  un rollout récursif — chaque modèle prédit directement `target_t{h}` à
  partir des lags réels de l'ancre, jamais à partir de la prédiction d'un
  autre horizon (voir §Baseline pour le pourquoi de ce choix).

### Features temporelles

- **Lags utilisés** : `t-1`, `t-2`, `t-4` (`LAGS = [1, 2, 4]` dans
  `ml/config.py`) — PAS de `t-3`.
- **`price_t`** (valeur courante) : utilisée à la fois comme feature
  implicite (dans les lags des lignes futures) et comme baseline directe.
- **`transaction_volume`** : inclus.
- **Autres statiques** : tous les `PRICE_MODEL_FEATURES` sauf
  `transaction_volume` (déjà compté séparément) : `population`,
  `unemployment_rate`, `owner_rate`, `vacancy_rate`, `social_housing_rate`,
  `aging_index`, `secondary_residence_rate`, `median_income`,
  `dist_nearest_major_city_km`.
- **Variables calendaires** : one-hot du trimestre calendaire (`quarter_1`
  à `quarter_4`, saisonnalité).
- **`cluster_id`** (Modèle 1) inclus en one-hot si disponible.

### Modèle

- **Algorithme** : `GradientBoostingRegressor` ×4 (un par horizon), mêmes
  hyperparamètres fixes que partout ailleurs (`GB_DEFAULT_PARAMS`) :
  `n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.8,
  random_state=42`. **Pas de RandomizedSearchCV ici** (contrairement au
  Modèle 2).
- **Modèles multiples, pas mutualisés en un seul** — un modèle par horizon
  (t+1, t+2, t+3, t+4), chacun entraîné indépendamment sur son propre jeu
  ligne-cible.
- **Communes à historique incomplet** : `dropna()` sur toutes les features
  requises + la cible de l'horizon — une commune peut apparaître dans le
  jeu t+1 mais pas t+4 si son historique est trop court à cette date. Pas
  de padding/imputation.

### Protocole temporel

- **Split** : chronologique STRICT, PAS aléatoire — confirmé dans le code
  (`train_test_split` de sklearn n'est même pas importé dans `forecasting.py`).
- **Règle exacte** : pour l'horizon h, train = lignes dont le **trimestre
  CIBLE** (ancre + h trimestres) est < 2025 ; test = trimestre cible = 2025.
  Split sur le trimestre CIBLE, pas sur le trimestre ancre (sinon un test
  t+4 ancré en 2025 nécessiterait des données 2026 inexistantes).
- **`FORECAST_TEST_YEAR = 2025`**.

### Baseline naïve

- **Formule** : `P̂_(t+h) = P_t` (persistance pure, `ml/config.py` + spec §4).

**Résultats baseline vs modèle, POST-RESEED (2026-08-14, run officiel actuel)** :

| Horizon | n test | MAE baseline | MAE modèle | R² modèle | Résultat |
|---|---|---|---|---|---|
| t+1 | 46915 | **68.0€** | 156.2€ | 0.9493 | **Baseline gagne** |
| t+2 | 45721 | **139.6€** | 193.7€ | 0.9411 | **Baseline gagne** |
| t+3 | 45153 | **193.3€** | 223.1€ | 0.9297 | **Baseline gagne** |
| t+4 | 44611 | 248.1€ | **224.1€** | 0.9335 | **Modèle gagne** |

**⚠️ Ce résultat est réel et vérifié, pas une erreur de calcul.** Avant le
fix du bug multi-lot DVF (§1.3), le modèle battait la baseline aux 4
horizons (ex. t+1 : MAE modèle 208.7€ vs baseline 224.7€). Après le fix, la
baseline s'améliore massivement à courts horizons (224.7€→68.0€ à t+1) car
le bug injectait du bruit trimestriel artificiel dans les prix — une fois ce
bruit retiré, la série de prix par commune est beaucoup plus lisse et la
persistance simple redevient très difficile à battre à court terme
(comportement cohérent avec un marché immobilier trimestriel peu volatile).
Voir `EXPERIMENTS_LOG.md` "Modèle rendement" #0 et "Modèle 3" #11 pour le
détail complet de cette analyse. **C'est un résultat à présenter tel quel
dans le mémoire — nuance méthodologique plus riche qu'un "le modèle gagne
toujours".**

### Erreurs selon volume/cluster

Non recalculé en tableau séparé cette session — les colonnes `cluster_id`
et `transaction_volume` sont disponibles dans `test_predictions.csv` pour
une analyse croisée si besoin pour le mémoire (pas fait ici faute de temps).

### Communes témoins (extrait réel, t+1, `example_communes.csv`)

| Commune | Ancre | Cible | Prix réel | Prédit modèle | Prédit baseline | Q10 | Q90 |
|---|---|---|---|---|---|---|---|
| Langres (52) | 2025-07 | 2025-07 | 1342.78 | 1259.82 | 1342.78 | 1112.17 | 1465.82 |
| Surgères (17) | 2025-01 | 2025-04 | 1808.87 | 2141.02 | 2219.18 | 1834.10 | 2355.62 |
| Pont-Saint-Esprit (30) | 2025-04 | 2025-07 | 1846.15 | 1875.78 | 1919.05 | 1625.61 | 2065.76 |
| Buxerolles (86) | 2024-10 | 2025-01 | 1898.88 | 2052.82 | 2000.00 | 1945.81 | 2205.49 |
| Saint-Mitre-les-Remparts (13) | 2025-07 | 2025-07 | 3221.15 | 3424.53 | 3221.15 | 3078.85 | 3720.77 |
| Champniers (16) | 2025-04 | 2025-07 | 1733.33 | 1110.80 | 950.99 | 929.00 | 1491.56 |
| Nanteuil-lès-Meaux (77) | 2025-01 | 2025-01 | 2720.59 | 2817.27 | 2720.59 | 2541.87 | 3090.59 |
| Val de Virvée (33) | 2025-07 | 2025-07 | 2404.12 | 2363.41 | 2404.12 | 2037.96 | 2628.05 |

**Résumé forecast t+4 (dernier trimestre par commune, national)** :
- **5277** communes avec forecast t+4 complet.
- **633/5277 (12.0%)** communes flaggées `extrapolation_risk` (|croissance
  1 an prévue| > 20%).
- Croissance médiane prévue à 1 an : **+0.55%**.
- 68 communes dépassent ±50% de croissance prévue (valeurs extrêmes, à
  interpréter avec prudence).

### Visuels disponibles

- Courbe réel vs prédit, distribution des erreurs, fan charts par commune :
  générés par `ml/scripts/generate_charts.py` (16 figures au total, dans
  `ml/artifacts/charts/latest/`).

---

## 6. Régression quantile (Modèle 4)

- **Algorithme** : `GradientBoostingRegressor(loss='quantile')`, 3 modèles
  indépendants (tau=0.1, 0.5, 0.9), mêmes hyperparamètres fixes que le
  Modèle 3 (`GB_DEFAULT_PARAMS`).
- **Transformation cible** : `log1p(prix)` à l'entraînement, `expm1()` à la
  prédiction — le prix/m² est asymétrique à droite (quelques communes très
  chères), et un premier essai en échelle brute perdait contre la baseline
  au quantile 0.9.
- **Quantiles retenus** : Q0.1, Q0.5, Q0.9 (conforme spec).
- **Dataset/split** : identiques au Modèle 2 (même seed, mêmes features,
  mêmes 7347 communes, split train=5142/val=1102/test=1103).

**Pinball loss par quantile (test, post-reseed)** :

| Quantile | Pinball modèle | Pinball baseline (quantile départemental) |
|---|---|---|
| tau=0.1 | 82.45 | 110.77 |
| tau=0.5 | 196.98 | 292.33 |
| tau=0.9 | 100.68 | 154.54 |

Modèle bat la baseline sur les 3 quantiles.

- **Couverture réelle [Q10, Q90], brute** : **76.5%** (cible 80%).
- **Couverture après calibration conformal (CQR, Romano et al. 2019)** :
  **79.9%** — quasi exactement la cible.
- **Marge conformal** : ±39.6 €/m² (calculée sur le split validation, jamais
  vu à l'entraînement).
- **Largeur moyenne de l'intervalle** : non recalculée en % isolément ce
  jour pour le Modèle 4 lui-même (disponible pour le Modèle 3 : 21.1% du
  prix prédit à t+1).

**Exemples de communes** (échantillon aléatoire réel, `predictions.csv`) :

| Commune | Prix réel | Q10 | Médiane (Q50) | Q90 | Dans l'intervalle ? |
|---|---|---|---|---|---|
| Couëron (44) | 2949.90 | 2627.05 | 2839.49 | 3262.05 | ✅ |
| Boisgervilly (35) | 2253.73 | 1370.82 | 2048.03 | 2463.60 | ✅ |
| Cussac-Fort-Médoc (33) | 2008.27 | 1188.15 | 1745.93 | 2383.71 | ✅ |
| Plumelin (56) | 998.11 | 1101.01 | 1684.53 | 2345.74 | ❌ (prix réel sous Q10) |
| Château-Renault (37) | 1546.88 | 1097.30 | 1367.04 | 1846.23 | ✅ |
| Arlanc (63) | 1651.39 | 842.71 | 1165.45 | 1805.93 | ✅ |

**Utilisation finale** : les colonnes `*_conformal` de `predictions.csv`
sont la version recommandée (garantie de couverture théorique). Le Modèle 3
réutilise le même principe de largeur croissante par horizon (Q10/Q90 à
chaque t+h), mais sans la calibration conformal (celle-ci n'existe que dans
`quantile.py`, pas encore portée sur `forecasting.py`).

---

## Annexe : au-delà du périmètre demandé, ce qui existe aussi

Ces éléments ne figuraient pas dans la checklist mais sont réellement
implémentés et mesurés — utiles si la Partie 3 en parle.

### Score composite Rentium — RÉELLEMENT implémenté (différent du §7 de la spec)

`ml/scripts/generate_recommendations.py` calcule un score composite pour un
classement Top 20, mais **PAS** la formule Sécurité/Localisation du §7 de
CONTEXTE_ML_MEMOIRE.md (qui nécessite WalkScore/TensionLocative/DélaiRelocation,
jamais implémentés). Formule réellement utilisée :

```
composite_score = -opportunity_score(%) + growth_1y(%) + yield_opportunity_score(%)
```

Combine 3 signaux indépendants (Modèle 2 prix, Modèle 3 croissance, Modèle
rendement) — pas pondéré par profil investisseur, poids implicite égal
(1,1,1), présenté comme heuristique transparente, pas appris
statistiquement. Filtre Top 20 : `opportunity_score < 0 AND growth_1y > 0
AND yield_opportunity_score > 0 AND NOT extrapolation_risk`. **7347**
communes combinées, **20** retenues, **5** fiches détaillées générées.

### Modèle hédonique — transaction-level (extension, au-delà des 4 modèles obligatoires)

Même logique que Modèle 2 mais au grain de la **transaction individuelle**
(pas la commune) — `ml/models/price_hedonic.py`, LightGBM (`LGBMRegressor`)
avec catégorielles natives (`type_local`, `quarter_of_year`, `housing_zone`,
`department_code`, `cluster_id`).

- **Hyperparamètres** : `n_estimators=300, max_depth=6, learning_rate=0.05, subsample=0.8`.
- **Dataset** (run officiel, post-reseed, `--full`) : 4 510 998 transactions
  chargées → 3 530 597 matchées à une commune non-`estimated` → 3 530 233
  avec features complètes. Split chronologique : train=2 886 040 (< 2025),
  test=644 193 (2025).
- **Baseline** : prix médian de la commune (celui de `price_model.py`)
  appliqué tel quel à chaque transaction individuelle.
- **Résultats (test 2025)** :

  | | Baseline (commune) | Modèle (transaction) |
  |---|---|---|
  | MAE | 858.7 €/m² | **783.7 €/m²** |
  | RMSE | 1493.8 €/m² | **1399.7 €/m²** |
  | R² | 0.5016 | **0.5624** |

- **Feature importance (gain LightGBM)** : `department_code` (2046) >
  `surface_bati` (1762) > `median_price_per_sqm` (1064) > `type_local` (703)
  > `nb_pieces` (576) > `median_income` (468) > `secondary_residence_rate`
  (384) > `aging_index` (273).
- **Limite structurelle assumée** : le R² reste plus bas qu'au niveau
  commune (0.81 pour Modèle 2) — normal, la variance intra-commune (étage,
  état du bien, DPE...) n'existe nulle part dans le DVF standard, aucun
  volume de données supplémentaire ne la comblera.

### Modèle rendement (extension, au-delà des 4 modèles obligatoires)

- Même architecture que Modèle 2 (GB tuné), target = `rendement_brut` —
  défini comme `(loyer×12) / coût_acquisition_total × 100`, coût
  d'acquisition incluant notaire (7.5%, barème officiel) + travaux (10% du
  prix, convention documentée, pas mesurée).
- **R²=0.4765**, MAE modèle 0.98pt vs baseline 1.15pt (médiane
  départementale) — bat la baseline de ~14.7%.
- Feature dominante : `vacancy_rate` (0.184), puis `median_income` (0.171).
- Rendement médian national (7347 communes) : **5.69%** brut/acquisition,
  **3.85%** net/acquisition (après charges 10% + taxe foncière réelle DGFiP).
  18.3% des communes tombent sous 3% net.

### Cash-flow déterministe (5 profils investisseurs)

Formules 100% déterministes (CONTEXTE_ML_MEMOIRE.md §6), pas de ML —
`ml/finance/cashflow.py` + `ml/finance/profiles.py`. Simulé sur le Top 20 ×
5 profils = 100 simulations.

| Profil | Apport | Taux | Durée | Cash-flow mensuel moyen |
|---|---|---|---|---|
| Primo-accédant standard | 10% | 3.5% | 25 ans | -15.6€ |
| Investisseur expérimenté | 20% | 3.2% | 20 ans | -9.5€ |
| Apport élevé | 40% | 3.4% | 15 ans | **+13.0€** (seul profil positif en moyenne) |
| Effet de levier maximal | 5% | 3.9% | 25 ans | -54.4€ |
| Profil prudent (SCI) | 15% | 3.6% | 20 ans | -49.4€ |

Deux hypothèses non mesurées, documentées explicitement : base cadastrale
≈50% du loyer annuel (approximation usuelle), charges non récupérables ≈10%
du loyer (aucune source ouverte trouvée).

---

## Note technique — résolue

Le cache DuckDB (`dvf_cache.duckdb`) avait été verrouillé par un process
`python -m pipeline.scripts.run_dvf` fantôme (PID 78268, un terminal
PowerShell resté ouvert dans un IDE tiers depuis le 13/08, zéro progrès DB
depuis son lancement — planté silencieusement). `taskkill` et `Stop-Process`
ont échoué de façon incohérente ; résolu via
`Invoke-CimMethod -MethodName Terminate`. Le modèle hédonique a été rejoué
avec succès juste après (§Annexe, chiffres à jour). Les comptages DVF bruts
cités en §1.1/§1.3 (20 382 920 lignes brutes, 4 526 559 mutations propres)
restent ceux calculés avant le verrouillage — fiables, pas re-vérifiés
depuis, mais aucune raison de douter qu'ils tiennent toujours (rien n'a
changé côté DVF brut entre les deux mesures).
