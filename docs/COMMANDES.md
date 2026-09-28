# Commandes : tout charger dans la base Rentium

Guide unique pour enrichir la base de données **complètement** (DVF, ventes géolocalisées, INSEE, DGFiP,
loyers, réglementation, POI OpenStreetMap...) et vérifier ensuite que rien ne manque.

Toutes les commandes se lancent depuis la racine de `immo-data-science/`.

---

## 1. Démarrage rapide

```bash
pip install -r requirements.txt

# 1) Voir ce qui va tourner et si les fichiers source sont là
python -m pipeline.scripts.seed_all --list

# 2) Tout charger, France entière (long : voir §4 pour les durées)
python -m pipeline.scripts.seed_all

# 3) Vérifier la couverture de la base
python -m pipeline.scripts.audit_coverage
```

Test rapide sur un seul département (POI inclus, extrait régional léger) :

```bash
python -m pipeline.scripts.seed_all --dept 77 --poi-region ile-de-france
```

Équivalent PowerShell : `.\scripts\seed_all.ps1` (simple raccourci vers la commande Python ci-dessus).

---

## 2. Prérequis (une seule fois)

| Quoi | Comment |
|---|---|
| Dépendances Python | `pip install -r requirements.txt` (inclut `duckdb`, `psycopg2`, `pyosmium`, `shapely`) |
| Base | `.env` à la racine avec `DATABASE_URL="postgresql://user:pass@localhost:5432/rentium_db"` (tunnel SSH si base distante, cf. README) |
| Schéma | Migrations Prisma appliquées côté `rentium/backend` (enums `SerieName`, colonnes `cities`, tables `transactions` et `pois`, `pois.osm_id` en `BigInt`). Vérifier : `cd ../rentium/backend && npx prisma migrate status` |
| Fichiers source | Déposés dans `csv/` et `dvf-raw/` (liste et liens : README > « Sources de données »). `--list` indique ceux qui manquent |
| DVF brut ailleurs | Les `ValeursFoncieres-*.txt` pèsent ~2,7 Go. Pour les garder hors du repo : variable `DVF_RAW_DIR` (dans `.env`, ex. `DVF_RAW_DIR=C:\data\dvf-raw`) ou option `--dvf-raw-dir` |
| Réseau | Requis pour `seed_cities` (geo.api.gouv.fr), `seed_transactions` (geo-dvf ~500 Mo), `seed_poi` (Geofabrik + contours communes) |

> Attention : `prisma migrate deploy` applique **toutes** les migrations en attente côté Rentium, y compris
> celles qui suppriment des tables. Vérifier `migrate status` avant.

---

## 3. `seed_all` : options

```bash
python -m pipeline.scripts.seed_all [options]
```

| Option | Effet |
|---|---|
| *(aucune)* | Toutes les étapes, France entière, dans l'ordre des dépendances |
| `--list` | Liste les étapes, marque `MANQUE` celles dont les fichiers source sont absents, puis quitte |
| `--dry-run` | Affiche les commandes exactes, n'exécute rien |
| `--dept 77` | Restreint au département (villes, DVF city, transactions, POI...). Plusieurs : `75,77` pour `seed_cities` uniquement |
| `--only a,b` | Lance uniquement ces étapes (noms de `--list`) |
| `--from NOM` | Reprend à partir de l'étape `NOM` (après un crash) |
| `--skip a,b` | Saute ces étapes |
| `--skip-dvf` | Saute `run_dvf_national` + `run_dvf_city` |
| `--skip-transactions` | Saute `seed_transactions` + `seed_city_sale_prices` |
| `--skip-poi` | Saute `seed_poi` (évite le téléchargement OSM) |
| `--refresh-permit` | Ajoute `scrape_rental_permit` (scraping permis de louer, trimestriel) |
| `--refresh-poi` | Re-télécharge le `.osm.pbf` même s'il existe (mensuel) |
| `--poi-region NOM` | Extrait Geofabrik : `france` (défaut, ~4 Go) ou `ile-de-france`, `bretagne`... |
| `--poi-pbf CHEMIN` | Utilise ce `.osm.pbf` tel quel, sans téléchargement |
| `--dvf-raw-dir DIR` | Dossier des `ValeursFoncieres-*.txt` |
| `--continue-on-error` | Ne s'arrête pas au premier échec d'une étape « non soft » |

Comportement :
- Une étape dont les fichiers source sont absents est **sautée** avec la raison, pas en échec.
- Les étapes **soft** (réseau, fichiers jetables : `seed_rp`, `seed_transactions`, `seed_city_sale_prices`, `seed_poi`...) n'arrêtent pas la suite si elles échouent.
- Fin de run : tableau récapitulatif (statut + durée par étape). Code retour `1` si une étape non soft a échoué.
- Chaque étape est aussi lançable seule : `python -m pipeline.scripts.<étape>` (mêmes noms que dans `--list`).

Exemples courants :

```bash
# Données INSEE/DGFiP uniquement (pas de DVF, pas de POI, pas de téléchargement lourd)
python -m pipeline.scripts.seed_all --skip-dvf --skip-transactions --skip-poi

# Rafraîchir uniquement les ventes DVF + prix de vente par ville
python -m pipeline.scripts.seed_all --only seed_transactions,seed_city_sale_prices

# Un crash au milieu : reprendre où ça s'est arrêté
python -m pipeline.scripts.seed_all --from seed_rent_series

# Base neuve, DVF brut stocké ailleurs
python -m pipeline.scripts.seed_all --dvf-raw-dir "D:\dvf-raw"
```

---

## 4. Ce que fait chaque étape (ordre d'exécution)

Durées : **mesurées** sur cette machine quand indiquées, sinon ordre de grandeur (non mesuré).

| # | Commande (`python -m pipeline.scripts.X`) | Remplit | Source | Durée |
|---|---|---|---|---|
| 1 | `seed_cities` | `cities` (35 000 communes, `geo_location`, `department`), `administrative_zones`, `countries` | geo.api.gouv.fr | quelques min |
| 2 | `seed_rp` | `cities.student_count` | `csv/base-ic-activite-residents-2022.xlsx` | min |
| 3 | `run_dvf --geo department,region,country` | séries prix/surface/volume/VEFA/terrain (zones + pays) | `dvf-raw/*.txt` (DuckDB) | long |
| 4 | `run_dvf --geo city` | séries villes + `median_price_per_sqm`, `avg_price_per_sqm`, `transaction_volume`, tendance, sparkline + interpolation IDW des villes sans données | idem | long (étape la plus lourde) |
| 5 | `seed_transactions` | **table `transactions`** : 1 ligne par local vendu (~5,9 M lignes 2021-2025, lat/lon, `mutation_id`, `lots_in_mutation`) | geo-dvf (téléchargé, cache `dvf-raw/geo-dvf/`) | dizaines de min |
| 6 | `seed_city_sale_prices` | `cities.median_sale_price`, `avg_sale_price` (12 derniers mois) | table `transactions` | min |
| 7 | `seed_pop_series` | `population`, `aging_index` | INSEE IC évol-struct-pop | min |
| 8 | `seed_logement_series` | `vacancy_rate`, `owner_rate`, `social_housing_rate`, `secondary_residence_rate` | INSEE IC logement | min |
| 9 | `seed_rp_series` | `unemployment_rate` | INSEE IC activité | min |
| 10 | `seed_employment_series` | `active_population` | INSEE IC activité | min |
| 11 | `seed_fiscalite_series` | `property_tax_rate` (2018-2025) | DGFiP | min |
| 12 | `prep_population_history` | CSV annuels (one-off, sauté si le XLSX brut est absent) | `csv/population-historique/raw_histo_pop.xlsx` | < 1 min |
| 13 | `seed_population_history_series` | `population_history` (2006-2023) | CSV de l'étape 12 | min |
| 14 | `seed_commerce_series` | `retail_count`, `retail_share_*` | INSEE BPE | min |
| 15 | `seed_household_size_series` | `household_size_*p_rate` | INSEE MEN1 | ~11 min (mesuré) |
| 16 | `seed_rent_series` | `rent_sqm_*` | Carte des loyers 2025 | min |
| 17 | `seed_median_income_series` | `median_income` | INSEE Filosofi 2021 | min |
| 18 | `seed_company_creations_series` | `company_creations` | INSEE SIDE | ~3 min (mesuré) |
| 19 | `seed_housing_zone` | `housing_zone`, `high_demand_zone` (zonage ABC national) | `csv/zonage-abc-national.csv` | ~6 s (mesuré) |
| 20 | `scrape_rental_permit` *(optionnel `--refresh-permit`)* | reconstruit `rental-permit.csv` | scraping + geo.api.gouv.fr | min |
| 21 | `seed_rent_regulation` | `rent_control`, `rental_permit_required` | listes ANIL/préfectures | secondes |
| 22 | `seed_short_term_rental_score` | `short_term_rental_score` | InsideAirbnb + communes touristiques | ~15 s (mesuré) |
| 23 | **`seed_poi`** | **table `pois`** (OpenStreetMap) | `.osm.pbf` Geofabrik | long, voir §5 |
| 24 | `seed_activity_counts_series` | `unemployed_count`, `retired_count` | INSEE IC | min |
| 25 | `seed_median_age` | `median_age` | INSEE IC | min |
| 26 | `seed_tenant_profile` | `tenant_profile` | INSEE IC | secondes |
| 27 | `seed_dashboard_fields` | `tenant_rate`, `demographic_growth_5y`, `employment_growth` | séries déjà en base | ~30 s |
| 28 | `seed_avg_property_tax` | `avg_property_tax` | série `property_tax_rate` | secondes |
| 29 | `seed_gross_yield` | `gross_yield_t1-4`, `avg_rent_per_sqm`, `gross_yield` | séries loyers + DVF | min |
| 30 | `seed_years_to_buy` | `years_to_buy` | séries DVF + revenu | ~30 s |
| 31 | `seed_housing_effort_rate` | `housing_effort_rate` | séries loyers + revenu | ~2 min |
| 32 | `seed_city_latest_snapshots` | `owner_rate`, `vacancy_rate`, `median_income`, `unemployment_rate`, `annual_company_creations` | dernière valeur de chaque série | min |
| 33 | `audit` | contrôle qualité des séries (anomalies QoQ) | base | min |
| 34 | `audit_coverage` | couverture de la base (voir §6) | base | ~25 s (mesuré) |

Ordre des dépendances (à respecter si tu lances les étapes à la main) :
`seed_cities` → DVF (`run_dvf`) → `seed_transactions` → `seed_city_sale_prices` ; séries `seed_*_series` avant les champs dérivés (27 à 32) ;
`seed_gross_yield` après `seed_rent_series` + DVF ; `seed_years_to_buy` après `seed_median_income_series` + DVF ;
`seed_housing_effort_rate` après `seed_median_income_series` + `seed_rent_series`.

---

## 5. POI (OpenStreetMap)

**État actuel de la base : seul le département 77 est chargé (4 457 POI).** Pour couvrir la France entière,
lancer le run national ci-dessous.

### Tout automatiser (recommandé)

```bash
# France entière : télécharge france-latest.osm.pbf (~4 Go) dans csv/poi/ si absent, puis ingère
python -m pipeline.scripts.seed_all --only seed_poi

# Test / dev : un extrait régional + un département
python -m pipeline.scripts.seed_all --only seed_poi --poi-region ile-de-france --dept 77

# Toute l'Île-de-France (75, 77, 78, 91, 92, 93, 94, 95) : même extrait, sans --dept
python -m pipeline.scripts.seed_all --only seed_poi --poi-region ile-de-france

# Rafraîchissement mensuel (re-télécharge le .pbf)
python -m pipeline.scripts.seed_all --only seed_poi --refresh-poi

# Utiliser un .pbf déjà téléchargé
python -m pipeline.scripts.seed_all --only seed_poi --poi-pbf csv/poi/france-latest.osm.pbf
```

### Commandes manuelles équivalentes

```bash
# 1) Télécharger l'extrait Geofabrik (fichier de travail jetable, gitignore)
curl -L -o csv/poi/france-latest.osm.pbf https://download.geofabrik.de/europe/france-latest.osm.pbf
#   ou régional : https://download.geofabrik.de/europe/france/ile-de-france-latest.osm.pbf (~330 Mo)

# 2) Essai à blanc : stats, aucune écriture
python -m pipeline.scripts.seed_poi --pbf csv/poi/france-latest.osm.pbf --dry-run

# 3) Ingestion (upsert idempotent sur osm_id, relançable sans doublons)
python -m pipeline.scripts.seed_poi --pbf csv/poi/france-latest.osm.pbf
python -m pipeline.scripts.seed_poi --pbf csv/poi/france-latest.osm.pbf --dept 13   # un seul département
```

### Ce qui se passe

1. `poi_source.py` lit le `.pbf` en flux (pyosmium) et garde les POI de la whitelist. Le tag `name` est obligatoire.
2. `poi_geo.py` rattache chaque POI à sa commune par point-in-polygon. Les contours communaux viennent de
   `geo.api.gouv.fr`, téléchargés **département par département** puis mis en cache dans `csv/poi/communes-contours*.geojson`
   (le 1er run national est donc plus long, les suivants réutilisent le cache). Arrondissements Paris/Lyon/Marseille repliés sur la commune.
3. Upsert par lots de 5 000 dans `pois` (`ON CONFLICT (osm_id)`). Seules les communes présentes dans `cities` sont gardées.

Catégories chargées : `education`, `health`, `transport` (gares, arrêts de tram, aéroports, gares routières, ferries),
`shopping`, `culture`, `leisure`, `services` (détail des tags : README > « POI (OpenStreetMap) »).

Prérequis : `pois.osm_id` en `BigInt` (déjà le cas dans la base actuelle), et `cities` déjà seedée (`seed_cities`).

Avec `--dept`, les POI des autres départements présents dans le `.pbf` sont ignorés (comptés « dropped »). Le test du 77 via l'extrait Île-de-France prend ~40 s.

Après un run : `python -m pipeline.scripts.audit_coverage` affiche le nombre de départements couverts.

---

## 6. Vérifier la base

```bash
python -m pipeline.scripts.audit_coverage             # rapport complet
python -m pipeline.scripts.audit_coverage --strict    # code retour 1 s'il reste une ligne VIDE (scripts / CI)
```

Le rapport donne : nombre de lignes par table (`cities`, `transactions`, `pois`, `series`, `timeseries`...), taux de remplissage de
chaque colonne `cities`, nombre de séries par `SerieName` (villes / zones / pays), couverture POI par département.

| Statut | Signification |
|---|---|
| `OK` | rempli à 90 % ou plus |
| `PARTIEL` | rempli mais < 90 %, la raison est affichée (ex. prix DVF seulement pour les villes avec assez de ventes) |
| `VIDE` | 0 % alors qu'un script devrait le remplir : la commande à lancer est indiquée en face |
| `SANS SRC` | 0 % et aucune source de données dans `immo-data-science` (colonnes `image`, `eligible_zones`, `major_urban_projects`, `attractiveness_rank`, `avg_sale_days`, `avg_relocation_days`, `rental_tension` ; séries `search_time_*`, `sale_time_*`) |

Autre contrôle : `python -m pipeline.scripts.audit` (anomalies de séries : sauts trimestriels, valeurs aberrantes).

---

## 7. Fréquence de rafraîchissement

| Donnée | Fréquence | Commande |
|---|---|---|
| DVF (geo-dvf + fichiers DGFiP) | 2 fois par an (publication semestrielle) | `seed_all --only seed_transactions,seed_city_sale_prices` puis `run_dvf` (avec les nouveaux `ValeursFoncieres-*.txt`) |
| POI OpenStreetMap | mensuelle | `seed_all --only seed_poi --refresh-poi` |
| Permis de louer | trimestrielle | `seed_all --refresh-permit --only scrape_rental_permit,seed_rent_regulation` |
| INSEE / DGFiP / loyers | annuelle (à la sortie d'un nouveau millésime : déposer le fichier, relancer la série concernée) | `seed_all --skip-dvf --skip-transactions --skip-poi` |

`seed_transactions` télécharge à nouveau un fichier geo-dvf seulement si sa taille distante a changé (ou en lançant directement `python -m pipeline.scripts.seed_transactions --refresh`).
L'année en cours n'est pas publiée avant la 1re publication : elle est sautée sans erreur.

---

## 8. Dépannage

| Symptôme | Cause / solution |
|---|---|
| `run_dvf_*` marqué `MANQUE` | Aucun `ValeursFoncieres-*.txt` dans `dvf-raw/` : déposer les fichiers ou `--dvf-raw-dir` / `DVF_RAW_DIR` |
| `invalid input value for enum "SerieName"` (ou `SerieSource`) | Migration Prisma pas appliquée côté `rentium/backend`, ou valeur d'enum inconnue. `npx prisma migrate status` puis appliquer |
| `column "..." of relation "cities" does not exist` | Même cause : colonne ajoutée au schéma Prisma sans migration appliquée |
| Un seed de séries très lent (heures) | Ne devrait plus arriver (index utilisé par `upload.py`). Si oui : `ANALYZE series;` |
| Paris / Lyon / Marseille sans prix DVF | Corrigé (arrondissements repliés sur la commune dans `dvf.py`). Relancer `run_dvf --geo city --dept 75` (69, 13) |
| `seed_poi` : `countries row with iso_code='FR' not found` | Lancer `seed_cities` d'abord |
| `seed_poi` très long au 1er run | Téléchargement des contours communaux (101 départements) + lecture du `.pbf` de 4 Go. Utiliser `--poi-region` pour un test |
| Étape échouée au milieu d'un run | `seed_all --from <étape>` (les upserts sont idempotents) |
| Étape « sautée : fichier source absent » | Déposer le fichier (README > « Sources de données ») puis `seed_all --only <étape>` |

Toutes les étapes sont **idempotentes** (upsert ou suppression + réinsertion dans la même transaction) : on peut relancer sans risque de doublons.
