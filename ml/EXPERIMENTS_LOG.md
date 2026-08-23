# Journal des essais — Rentium ML (mémoire)

Suivi de tout ce qui n'a pas marché du premier coup, ou qui reste une limite
connue plutôt qu'un problème corrigé. Objectif: alimenter la section
discussion/limites du mémoire avec des essais concrets et des chiffres, pas
des généralités.

Format par entrée: **Essai** (quoi), **Résultat** (chiffres réels), **Cause**,
**Statut** (Corrigé / Limite acceptée / Ouvert).

Mis à jour à chaque nouveau test qui échoue ou sous-performe — pas seulement
au moment de la rédaction.

---

## Modèle 1 — Clustering

### #1 — `price_growth_3y` comme feature clustering
- **Essai**: CONTEXTE_ML_MEMOIRE.md §2 liste `price_growth_3y` parmi les features.
- **Résultat**: seulement 3281/34969 communes (9%) avaient cette donnée — toutes les 8 autres features étaient ~99% complètes (vérifié colonne par colonne).
- **Cause**: nécessite 12 trimestres DVF consécutifs ; la plupart des communes n'ont pas assez d'historique de transactions.
- **Statut**: **Corrigé** — retiré de `CLUSTERING_FEATURES` (`ml/config.py`). n passe de 3281 à 32992.

### #2 — k optimal par silhouette max = k=2
- **Essai**: scan k=2 à 10, sélection automatique par silhouette maximal.
- **Résultat**: k=2 gagne (silhouette 0.349) mais découpe juste "hameaux ruraux" (n=27157, population moyenne 521) vs "communes plus peuplées" (n=5835, population moyenne 7797) — pas une typologie de marché exploitable, juste un split par taille.
- **Cause**: la silhouette favorise structurellement les découpages grossiers dominés par la variable à plus forte variance (population, même après log-transform).
- **Statut**: **Limite acceptée** — k=3 retenu manuellement (silhouette 0.306, légèrement inférieur à k=2) pour une typologie interprétable : rural profond/résidences secondaires, rural ordinaire, pôles urbains secondaires.

### #3 — Silhouette plafonne à ~0.31-0.35 quel que soit k
- **Essai**: k=2 à k=10 testés, log-transform population/transaction_volume appliqué avant scaling.
- **Résultat**: aucun k ne dépasse 0.35. Repère standard (Kaufman & Rousseeuw) : 0.25-0.5 = structure faible-à-raisonnable, jamais "forte" (>0.5) sur ce dataset.
- **Cause probable**: les communes françaises sont sur un continuum rural→urbain, pas naturellement séparées en clusters nets — plafond peut-être structurel, pas un manque d'effort.
- **Statut**: **Ouvert / limite structurelle** — pistes non testées : `median_income` (Filosofi), zonage national, densité POI (toutes nécessitent une nouvelle source de donnée, pas du tuning).

### #4 — Deux typologies différentes selon le run
- **Essai**: comparaison du clustering avant/après retrait de `price_growth_3y`.
- **Résultat**: avant (n=3281, communes avec activité DVF suffisante) → littoral/résidences secondaires, villes moyennes en tension, périurbain dense. Après (n=32992, toutes communes) → rural profond, rural ordinaire, pôles urbains secondaires.
- **Cause**: retirer `price_growth_3y` change QUI est inclus (plus de sélection vers les communes à marché actif), donc change la composition de l'échantillon clusterisé.
- **Statut**: **Ouvert — décision à prendre** pour savoir quelle typologie raconter dans le mémoire (ou documenter les deux comme sensibilité au choix de features).

### #5 — Pondération des features par ANOVA F-stat (`--weighted`)
- **Essai**: `ml/models/clustering.py --weighted` — fit K-Means non-pondéré une première fois, calcule le F-stat ANOVA de chaque feature vs les clusters trouvés, repondère (poids = F/moyenne(F)), refit K-Means sur l'espace pondéré.
- **Résultat**: silhouette **0.3065 → 0.3740** (k=3, national). Poids trouvés : `secondary_residence_rate`=1.89, `social_housing_rate`=1.53, `transaction_volume`=1.50, `population`=1.33, `owner_rate`=1.06, `aging_index`=0.42, `unemployment_rate`=0.25, **`vacancy_rate`=0.006 (quasi inutile pour séparer ces clusters)**.
- **Cause**: pondération équitable (StandardScaler seul) laissait des features peu discriminantes (vacancy_rate, unemployment_rate) diluer la distance autant que les features qui séparent vraiment les groupes.
- **Statut**: **Corrigé / amélioration confirmée** — silhouette ET validation externe meilleures (voir #7). Toujours du K-Means (conforme au doc), juste une repondération. Devenu la version recommandée (`--weighted`), gardé optionnel (pas par défaut) pour ne pas invalider silencieusement les chiffres déjà discutés.

### #6 — Validation externe post-hoc (prix jamais utilisé en feature)
- **Essai**: ANOVA F-test du prix/m² réel (`price_data_source != 'estimated'`) entre clusters, après coup — le prix n'entre jamais dans `CLUSTERING_FEATURES`.
- **Résultat**: K-Means non-pondéré : F=194.50, p=4.9e-83 (n=7349). K-Means pondéré : **F=291.44, p=1.6e-122**. Extrêmement significatif dans les deux cas — les clusters ne sont pas arbitraires, le prix diffère vraiment entre eux malgré n'avoir jamais été vu par le modèle.
- **Statut**: **Corrigé / ajout positif** — comble un vrai trou méthodologique (silhouette = validation interne uniquement). À citer dans le mémoire comme preuve que le clustering capture un signal de marché réel.

### #7 — Comparatif des 4 approches de clustering (national, k=3)
| Approche | Silhouette | ANOVA F (prix) | p-value | Statut |
|---|---|---|---|---|
| K-Means flat (non-pondéré) | 0.3065 | 194.50 | 4.9e-83 | Baseline |
| **K-Means flat pondéré** | **0.3740** | **291.44** | 1.6e-122 | **Meilleur sur les 2 métriques — retenu** |
| GMM (`clustering_gmm.py`) | 0.1322 (labels durs) | 146.70 | 3.4e-63 | Moins bon, mais détecte 1113/32992 (3.4%) communes "frontière" entre clusters — info que K-Means dur ne donne pas |
| Hiérarchique densité INSEE (`clustering_hierarchical.py`) | 0.0389 (global) / 0.32-0.36 (par groupe) | 210.09 | **2.4e-285** | Silhouette globale trompeuse — la métrique n'est pas adaptée pour comparer des sous-clusters formés dans des voisinages différents. Meilleure significativité (p) mais F-stat plus faible que le pondéré. |
| **HDBSCAN** (`clustering_hdbscan.py`) | **N/A — 0 cluster trouvé** | — | — | **Échec net**, voir #8 |

### #8 — HDBSCAN : 0 cluster trouvé (100% noise)
- **Essai**: `ml/models/clustering_hdbscan.py`, testé avec `min_cluster_size` ∈ {50, 500} et `min_samples` ∈ {5, 15, 30} (4 combinaisons).
- **Résultat**: **100% des 32992 communes classées "noise" (-1) dans tous les cas**, 0 cluster détecté. Silhouette non calculable (NaN).
- **Cause**: pas un problème de réglage (testé sur une large plage de paramètres) — résultat négatif robuste. Dans l'espace à 8 dimensions (log-transformé + standardisé), les données forment un nuage continu (gradient rural→urbain) sans les vraies "poches" de densité dont HDBSCAN a besoin. Cohérent avec le plafond de silhouette du K-Means (jamais >0.37, jamais "forte" séparation) : la structure est réelle mais diffuse, pas le type de clusters denses-et-séparés que HDBSCAN cible.
- **Statut**: **Ouvert / résultat négatif définitif** — bon résultat à citer tel quel dans le mémoire ("on a testé une méthode density-based, elle confirme par l'échec que la structure des données est diffuse plutôt que compacte").

### #9 — Ajout de `median_income` (Filosofi) : silhouette monte, validation externe descend
- **Essai**: `median_income` (INSEE Filosofi 2021, niveau communal) ajouté à `CLUSTERING_FEATURES`, en plus du `--weighted`.
- **Résultat**: silhouette **0.374 → 0.547** (k=2 redevient optimal, franchit pour la première fois le seuil "structure forte" >0.5). Mais validation externe (prix, jamais en feature) **se dégrade** : ANOVA F **291.44 → 137.43** (n=7347). Poids ANOVA trouvés : `transaction_volume`=2.70, `population`=2.16, `social_housing_rate`=2.14, `owner_rate`=1.77 — **`median_income`=0.0002, quasi nul**. Profil des 2 clusters : `median_income_mean` = 23077€ (cluster rural, n=23925) vs 23261€ (cluster villes, n=5676) — **écart de 0.8%, aucun pouvoir séparateur** sur ce découpage.
- **Cause**: le split k=2 redevient un simple clivage par taille de population/volume de transactions (comme avant l'ajout de `secondary_residence_rate` en tête de liste) — `median_income` ne varie quasiment pas entre "petit village" et "ville moyenne" en France (le revenu médian dépend plus du profil socio-pro que de la taille de la commune), donc le rescaling ANOVA l'écrase à raison. La silhouette plus haute vient d'un split plus "facile" géométriquement (population/volume très séparables), pas d'un split plus pertinent pour le prix.
- **Leçon méthodo à citer telle quelle dans le mémoire**: silhouette seule peut tromper — un score plus haut n'implique pas un clustering plus utile. Cet essai est exactement pourquoi la validation externe (#6) a été ajoutée : sans elle, on aurait présenté ce run comme une amélioration alors que c'est une régression sur le critère qui compte (le lien au prix réel).
- **Statut**: **Ouvert — décision à prendre**. Options : (a) garder k=3 non-repondéré comme version "officielle" du mémoire (meilleur F=291, typologie interprétable) et documenter ce run comme sensibilité négative ; (b) repondérer manuellement en excluant `median_income` du rescaling ANOVA automatique pour éviter qu'il se fasse écraser par les features de volume. Pas encore tranché.
- **Suite** : `median_income` retiré de `CLUSTERING_FEATURES` (config.py) suite à ce résultat — reste dans `PRICE_MODEL_FEATURES` où il aide vraiment (#5 Modèle 2).

### #10 — Faire émerger un cluster "banlieue" : 3 essais, 1 seul marche
- **Essai A — `dist_nearest_major_city_km` ajoutée à `CLUSTERING_FEATURES` (K-Means classique, pondéré)** : silhouette=0.32 (k=3), poids ANOVA de la distance = **0.30**, écrasé par `transaction_volume`=1.75/`social_housing_rate`=1.74/`secondary_residence_rate`=1.63. F=241.92 (mieux que #9 mais pire que la version sans distance, F=291.44). **Profil des 3 clusters** : distances moyennes 45.6km / 58.5km / 81.7km — aucun cluster net "proche ville" vs "loin de tout", juste un gradient continu noyé dans les autres features. **Pas de banlieue isolée.**
- **Essai B — même feature, k=2 (silhouette max)** : silhouette=0.52, poids distance encore plus faible (0.25), F=137.63. Même symptôme que #9 : silhouette trompeuse, split par taille pas par géographie.
- **Essai C — `clustering_hierarchical.py`, grille densité INSEE, niveau "Ceintures urbaines" (banlieue officielle) séparé de "Petites villes" au lieu d'être fusionné** : groupe `ceintures_urbaines` isolé, **n=1737**, vérifié manuellement — **Rognac, Auriol, Ventabren, Coudoux (banlieue Marseille/Aix), Toussieu (banlieue Lyon), Saint-Berthevin (banlieue Laval), Plérin (banlieue Saint-Brieuc)** — toutes de vraies banlieues reconnaissables. F=165.54 (silhouette par groupe 0.28, cohérent avec les autres groupes ~0.28-0.31).
- **Cause de l'échec K-Means (A/B)**: le K-Means pondéré priorise toujours les features à forte variance brute (volume, logement social) sur un signal géographique qui, même informatif, a une variance plus modeste dans l'espace standardisé. Aucune repondération automatique (ANOVA) ne lui donne assez de poids pour dominer un axe de partition.
- **Pourquoi C marche**: parce que ce n'est pas le K-Means qui doit *découvrir* la banlieue — la catégorie est **donnée a priori** par l'INSEE (grille de densité), le K-Means ne fait que sous-segmenter à l'intérieur. Approche "coup de pouce" plutôt que "laisser l'algorithme trouver tout seul".
- **Statut**: **Corrigé via l'approche hiérarchique (#7 ci-dessus)** — c'est la seule des 3 qui délivre une vraie banlieue vérifiable. Le K-Means pondéré classique (A/B) n'y arrive pas, même avec la feature distance ajoutée — limite structurelle de l'algorithme sur ce jeu de features, pas un manque de données.

---

## Modèle 2 — Prix/m²

### #1 — Pas de feature géographique
- **Essai**: modèle initial avec seulement les 8 features socio-éco (pas de département/région).
- **Résultat**: R²=0.46 (dept-77, n=194), R²=0.54 (national, n=7349) — gain faible vs baseline médiane département (~6% de mieux en MAE).
- **Cause**: aucune variable ne capturait explicitement la géographie (Paris vs Creuse), alors que c'est un facteur prix énorme.
- **Statut**: **Corrigé** — `department_code` ajouté en catégoriel (`PRICE_MODEL_CATEGORICAL`). R² national → 0.68, gain vs baseline → ~20% en MAE (575→490 vs baseline 613).

### #2 — `price_growth_3y` dans les features prix
- **Essai**: doc CONTEXTE_ML_MEMOIRE.md liste `price_growth_3y` comme feature possible pour Modèle 2, avec un avertissement explicite sur le risque de fuite.
- **Résultat**: jamais testé en pratique — fuite identifiée avant entraînement.
- **Cause**: `growth_3y = (latest/12Q_ago - 1)`, où `latest` = la target elle-même → corrélation directe garantie, pas un vrai signal prédictif.
- **Statut**: **Corrigé préventivement** — jamais inclus dans `PRICE_MODEL_FEATURES`.

### #3 — Plafond R² au niveau commune
- **Essai**: —
- **Résultat**: R²=0.68 avec `department_code`, probablement proche du plafond pour ce niveau de granularité.
- **Cause**: le modèle prédit un prix par COMMUNE (agrégat), pas par bien — la variance intra-commune (un Haussmannien vs un HLM des années 70 dans la même ville) est invisible à ce niveau.
- **Statut**: **Ouvert / limite structurelle** — dépasser ça demanderait un modèle niveau transaction individuelle (chaque ligne DVF), un projet différent, pas une amélioration incrémentale.

### #4 — Hyperparamètres jamais recherchés (RandomizedSearchCV)
- **Essai**: `--tune` (RandomizedSearchCV, 20 candidats x 5-fold, sur train=5144 lignes national).
- **Résultat**: R²=0.68 → **0.72**, MAE 456 vs 613 baseline (**26% de mieux**, contre 20% sans tuning). Meilleurs paramètres trouvés : `{subsample: 0.6, n_estimators: 500, max_depth: 4, learning_rate: 0.1}` — plus profond et plus d'arbres que les défauts (`GB_DEFAULT_PARAMS`: depth=3, n_estimators=300, lr=0.05).
- **Statut**: **Corrigé / amélioration confirmée** — gain réel et mesuré. `GB_DEFAULT_PARAMS` pas mis à jour avec ces valeurs (spécifiques à ce split/cette taille d'échantillon, mieux vaut retuner si le dataset change) ; `--tune` reste recommandé pour la version finale citée dans le mémoire plutôt que ces valeurs codées en dur.

### #5 — `median_income` + `dist_nearest_major_city_km` + zonage national : le plus gros gain de la session
- **Essai**: contrairement au clustering (#9 ci-dessus), Modèle 2 combine `median_income` (Filosofi), `dist_nearest_major_city_km` (calculée depuis lat/lon existant, distance haversine BallTree au plus proche des 50 communes les plus peuplées de France, calcul national fixe même avec `--dept`) et `housing_zone` désormais couvert nationalement (34875 communes vs 1266 IDF avant), en plus du `--tune` déjà en place.
- **Résultat**: R²=0.72 → **0.8065**, MAE 371 vs 604 baseline (**38.6% de mieux**, contre 26% avec juste le tuning). Le plus gros bond mesuré sur n'importe quel modèle cette session.
- **Cause**: contrairement au clustering, le prix EST directement lié à la géographie fine (distance à la métropole) et au niveau de vie — ces deux features avaient un vrai pouvoir explicatif pour la target, alors qu'elles n'avaient quasiment aucun pouvoir séparateur pour des clusters dominés par la taille de population. Cohérent : `median_income` était déjà identifié comme le levier le plus prometteur justement parce que le prix (contrairement à un clustering socio-démo générique) dépend directement du pouvoir d'achat local.
- **Statut**: **Corrigé / amélioration confirmée, gain majeur**. Version à citer comme résultat final du Modèle 2 dans le mémoire.

### #6 — LightGBM catégoriel natif vs sklearn GB + one-hot : égalité
- **Essai**: `ml/models/price_model_lgbm.py` — même split/features que `price_model.py`, mais `department_code`/`housing_zone`/`cluster_id` en dtype `category` natif (pas de one-hot ~100 colonnes creuses).
- **Résultat**: R²=0.8063, MAE=365.86 (LightGBM, hyperparamètres par défaut) vs R²=0.8065, MAE=371.25 (sklearn GB, tuné via `--tune`) — **écart négligeable, quasi une égalité**. LightGBM légèrement meilleur en MAE, légèrement moins bon en R², différence dans le bruit.
- **Cause**: à ce volume (7347 communes, ~100 colonnes one-hot dont la plupart creuses), l'arbre GB de sklearn arrive déjà à reconstruire les regroupements départementaux efficacement malgré le one-hot — l'avantage théorique du catégoriel natif ne se matérialise pas ici. Note : comparaison pas totalement équitable (LightGBM pas passé par `--tune`), un vrai avantage pourrait émerger avec plus de catégories ou un tuning symétrique.
- **Statut**: **Ouvert / résultat neutre** — pas de gain net à ce stade, pas la peine de remplacer `price_model.py` par LightGBM pour l'instant. `price_model.py` (sklearn GB) reste le modèle "officiel" du mémoire.
---

## Modèle 3 — Forecasting

### #1 — `cluster_id` absent des features
- **Essai**: `forecasting.py` n'incluait pas `cluster_id` (contrairement à `price_model.py`).
- **Résultat**: ajouté — impact quasi nul (MAE 200.81 → 200.87, R² 0.9409 → 0.9408, national).
- **Cause**: le modèle est déjà proche du plafond avec juste les lags (t-1/t-2/t-4) ; `cluster_id` apporte peu d'info marginale ici.
- **Statut**: **Corrigé (neutre)** — gardé pour cohérence méthodo avec Modèle 2, pas pour le gain de perf.

### #2 — Forecast récursif t+4 instable sur certaines communes
- **Essai**: rolling forecast sur 4 trimestres, réinjection de chaque prédiction comme lag pour l'étape suivante (un seul modèle, entraîné pour t+1 seulement).
- **Résultat**: exemple Bohain-en-Vermandois — prix réel 687€/m², `forecast_t1`=4021€, `forecast_t4`=2007€, `growth_1y`=+192%. Sur 3183 communes forecastées : **5 (0.16%) dépassent ±50%** de croissance annoncée, **111 (3.5%) dépassent ±20%** — beaucoup trop pour un marché immobilier réel sur 1 an. Médiane à +2.2% (plausible), la queue est cassée.
- **Cause**: compounding error sur communes à prix de départ atypique / peu représentées dans l'entraînement. Aucun garde-fou de sanité sur la sortie du forecast récursif.
- **Statut**: **Remplacé** — voir #9. L'approche récursive n'existe plus dans `forecasting.py`.

### #9 — Fix : modèles directs par horizon au lieu du récursif
- **Essai**: remplacé le modèle unique + rollout récursif par **4 modèles indépendants** (un par horizon t+1/t+2/t+3/t+4), chacun entraîné directement sur `target_t{h} = prix réel h trimestres plus tard`, avec des lags 100% réels (jamais une prédiction réinjectée). Split chronologique par trimestre CIBLE (pas par trimestre d'ancrage — sinon t+4 ancré en 2025 demanderait des données 2026 inexistantes).
- **Résultat mesuré** (national, test=2025) :

  | Horizon | Baseline MAE | Modèle MAE | Baseline R² | Modèle R² |
  |---|---|---|---|---|
  | t+1 | 224.71 | 209.26 | 0.9124 | 0.9388 |
  | t+2 | 231.03 | 216.22 | 0.9259 | 0.9381 |
  | t+3 | 242.52 | 226.25 | 0.9211 | 0.9342 |
  | t+4 | 253.45 | 238.70 | 0.9148 | 0.9246 |

  Dégradation propre et attendue avec l'horizon (R² baisse progressivement 0.939→0.925) — signe sain, contrairement au récursif qui masquait artificiellement l'incertitude croissante.

- **Bohain-en-Vermandois spécifiquement** : n'apparaît plus dans `forecast_summary_t4.csv` avec une valeur délirante — son prix courant (`price_sqm_all`) est lui-même NaN pour son dernier trimestre disponible, donc maintenant explicitement exclu (`last_actual_price`/`growth_1y` = NaN) plutôt que de produire un chiffre faux avec une fausse confiance.

- **Mais** : le taux de communes à croissance extrême a *augmenté* en proportion — **495/3877 (12.8%) dépassent ±20%**, **46/3877 (1.2%) dépassent ±50%** (vs 3.5%/0.16% avec le récursif). Pas une régression du fix : le récursif chaînait un modèle 1-step très conservateur, ce qui *masquait* l'incertitude réelle à t+4 plutôt que de la révéler. Le modèle direct t+4, entraîné sur la vraie relation P_t→P_(t+4) (plus bruitée, moins de lignes d'entraînement : 24833 vs plus pour t+1), expose une difficulté d'extrapolation réelle sur les communes atypiques — c'est plus honnête, pas plus cassé.
- **Mitigation ajoutée**: colonne `extrapolation_risk` (bool, `|growth_1y| > 20%`) dans `forecast_summary_t4.csv` — signale plutôt que cache les prédictions à risque. Pas un clip silencieux.
- **Statut**: **Corrigé (bug de compounding) + limite documentée (difficulté d'extrapolation réelle, désormais visible via `extrapolation_risk`)**. À citer dans le mémoire : le fix élimine un vrai bug mécanique, mais révèle que ~13% des communes ont une prévision à 1 an peu fiable — c'est une découverte légitime sur les limites du forecasting à 4 trimestres avec seulement 20 trimestres d'historique, pas un défaut du code.

### #10 — Intervalles d'incertitude croissants par horizon (Q10/Q90 x4)
- **Essai**: 2 régresseurs quantile (tau=0.1/0.9, log1p(target) comme Modèle 4) ajoutés par horizon, en plus du modèle point déjà en place — 12 modèles GB au total (4 horizons × 3 : point + Q10 + Q90).
- **Résultat mesuré** (national, test=2025) :

  | Horizon | Coverage [Q10,Q90] | Largeur médiane (Q90-Q10) |
  |---|---|---|
  | t+1 | 82.1% | 709€/m² |
  | t+2 | 83.2% | 728€/m² |
  | t+3 | 83.3% | 749€/m² |
  | t+4 | 82.2% | 743€/m² |

  Coverage bien calibrée sur les 4 horizons (proche de 80%, même légèrement au-dessus — mieux calibré que le Modèle 4 brut, 76.5%, sans même la correction conformal). Largeur croît de +5.6% entre t+1 et t+3, signal honnête d'incertitude grandissante, cohérent avec la difficulté croissante déjà observée (#9 ci-dessus).
- **Statut**: **Corrigé / amélioration confirmée** — les intervalles sont maintenant dans `forecast_summary_t4.csv` (`forecast_t{h}_q10`/`q90`), à utiliser à la place d'un chiffre point unique dans le mémoire, surtout pour les communes `extrapolation_risk=True`.

### #11 — Post-reseed (fix bug multi-lot #0) : le modèle perd contre la baseline à t+1/t+2/t+3
- **Contexte**: après le fix du bug multi-lot (`pipeline/services/series.py`, voir "Modèle rendement" #0) et le reseed complet de la DB (`python -m pipeline.scripts.run_dvf`, confirmé via `cities.updated_at`=2026-08-14 16:23), tout le pipeline mémoire a été relancé (`ml_run_full_pipeline.ps1`).
- **Résultat mesuré** (national, test=2025, avant vs après reseed) :

  | Horizon | Baseline MAE avant | Baseline MAE après | Modèle MAE avant | Modèle MAE après | Résultat après |
  |---|---|---|---|---|---|
  | t+1 | 224.71€ | **68.0€** | 208.70€ | 156.2€ | **baseline gagne** |
  | t+2 | 231.03€ | **139.6€** | 215.42€ | 193.7€ | **baseline gagne** |
  | t+3 | 242.52€ | **193.3€** | 224.84€ | 223.1€ | **baseline gagne** |
  | t+4 | 253.45€ | 248.1€ | 237.41€ | **224.1€** | modèle gagne |

  La baseline (persistance naïve, P̂ₜ₊ₕ = Pₜ) s'améliore massivement à courts horizons (t+1 : 224.71€→68.0€, ÷3.3) tandis que le modèle ne s'améliore que modestement (208.70€→156.2€). Le crossover (le modèle redevient meilleur) n'a lieu qu'à t+4.
- **Cause identifiée**: le bug multi-lot (#0 ci-dessus) injectait du bruit trimestriel artificiel dans `price_sqm_all` — une vente d'immeuble à pièces mixtes dupliquait le prix total sur plusieurs sous-groupes, créant de faux pics ponctuels par commune-trimestre. Ce bruit rendait la persistance naïve mauvaise (le trimestre précédent n'était pas fiable) ET donnait au modèle GB une occasion artificielle de "gagner" en apprenant partiellement à filtrer ce bruit via les features statiques. Une fois les prix nettoyés, la série trimestrielle par commune est nettement plus lisse — la persistance simple redevient une prédiction très forte à courts horizons, ce qui est attendu sur un marché immobilier trimestriel intrinsèquement peu volatile (peu de transactions, prix qui bougent lentement).
- **Pas un bug de ce fix-ci** : `n_test` reste stable/cohérent par horizon (46915→44611 lignes), pas de chute suspecte de volume de données. R² du modèle reste élevé (0.93-0.95) à tous les horizons — le modèle explique toujours très bien la variance, il est simplement dépassé par une baseline devenue très forte.
- **Statut**: **Résultat honnête, pas corrigé (rien à corriger)**. À documenter tel quel dans le mémoire : "le modèle direct par horizon ne bat la persistance simple qu'à partir de t+4 une fois les données source nettoyées — la performance apparente à courts horizons avant correction du bug DVF était partiellement un artefact du bruit dans les données, pas une vraie capacité prédictive". C'est un point de discussion méthodologique plus riche que "le modèle bat toujours la baseline" — montre une vraie rigueur d'analyse (avant/après correction d'un biais de données).

---

## Modèle 4 — Quantile

### #1 — tau=0.9 perdait contre la baseline
- **Essai**: `GradientBoostingRegressor(loss='quantile')` entraîné directement sur price/m² brut.
- **Résultat**: pinball tau=0.9 = 162.57 (modèle) vs 161.59 (baseline département) — le modèle **perdait** sur la borne haute, gagnait partout ailleurs (tau=0.1, tau=0.5).
- **Cause**: price/m² right-skewed (peu de communes très chères étirent la queue haute) ; GB optimise mal cette asymétrie en échelle brute.
- **Statut**: **Corrigé** — `log1p(target)` avant entraînement, `expm1` sur les prédictions. tau=0.9 national : 132.94 (modèle) vs 161.59 (baseline) — gagne net maintenant.

### #2 — Coverage [Q10,Q90] sous la cible
- **Essai**: coverage empirique visé ~80% (intervalle Q10-Q90 = 80% par construction si bien calibré).
- **Résultat**: 76.4% (dept-77, n=194 test) → 76.5% (national, n=1103 test) — proche mais pas pile, quasi inchangé malgré le fix log-transform.
- **Cause**: pas identifiée précisément — possiblement hétéroscédasticité résiduelle non capturée par les features actuelles.
- **Statut**: **Remplacé** — voir #3, calibration conformal résout ça proprement plutôt que d'accepter l'écart.

### #3 — Conformal prediction (CQR) pour calibrer la coverage exactement
- **Essai**: split conformal calibration (Conformalized Quantile Regression, Romano et al. 2019) sur le split `val` (`idx_val`, calculé depuis le début mais jamais utilisé jusqu'ici). Score de non-conformité `E_i = max(Q10(x_i)-y_i, y_i-Q90(x_i))` sur le val set, marge = quantile empirique (1-α) de `{E_i}` avec correction petit-échantillon, intervalle élargi de `[Q10-marge, Q90+marge]`.
- **Résultat**: coverage brute 76.5% → **coverage conformal 82.8%**. Marge calibrée : ±60.4€/m² (calculée sur n_val=1102, national). Garantie distribution-free (sous exchangeabilité) plutôt qu'empirique/espérée.
- **Statut**: **Corrigé** — colonnes `borne_basse_q10_conformal`/`borne_haute_q90_conformal`/`within_interval_conformal` ajoutées à `predictions.csv`, en plus des colonnes brutes (les deux gardées pour comparaison). Version conformal recommandée pour le mémoire — coverage garantie théoriquement, pas juste mesurée a posteriori.

---

## Modèle hédonique — extension transaction-level (hors cahier des charges)

Pas un des 4 modèles de `CONTEXTE_ML_MEMOIRE.md` — construit pour corriger le
défaut structurel identifié en Modèle 2 #3 (moyenne commune aveugle à la
variance intra-commune). `ml/models/price_hedonic.py` + `ml/data/build_transactions.py`.

### #1 — Premier run national (échantillon 500k transactions)
- **Essai**: LightGBM niveau transaction (surface_bati, type_local, nb_pieces + tout le contexte commune de `price_model.py`, y compris `median_price_per_sqm` comme feature — le modèle apprend à corriger la moyenne commune, pas à repartir de zéro). Baseline = `median_price_per_sqm` de la commune appliqué tel quel à chaque transaction (= la réponse actuelle de Modèle 2).
- **Résultat** (test=2025, n=71105 transactions, échantillon national 500k) :

  |  | Baseline (commune) | Modèle (transaction) |
  |---|---|---|
  | MAE | 1009€/m² | **974€/m²** |
  | RMSE | 2001€/m² | **1875€/m²** |
  | R² | 0.36 | **0.44** |

  Gain réel mais modeste : MAE -3.5%, R² +22% en relatif. `surface_bati` est de très loin la feature la plus importante (importance 2250, devant `department_code`=1573 et `median_price_per_sqm`=901 elle-même).
- **Pourquoi R² plus bas qu'au niveau commune (0.81 pour Modèle 2)** : pas comparable directement — au niveau transaction, la variance à expliquer inclut tout ce que le DVF standard ne capture pas (étage, état du bien, année de construction, DPE, négociation). Le modèle commune "lisse" cette variance en moyennant ; le modèle transaction l'affronte directement, donc plus dur, R² plus bas, mais la comparaison qui compte est **vs sa propre baseline** (commune appliquée telle quelle), pas vs le R² de Modèle 2.
- **Statut**: **Limite acceptée / résultat honnête** — le modèle prouve que la granularité aide (bat sa baseline), mais le DVF standard plafonne ce qu'on peut expliquer sans données bien-spécifiques (étage/état/DPE) qui n'existent nulle part dans ce dataset. À citer comme démonstration du concept, pas comme solution complète.

### #2 — Confirmation à pleine échelle (`--full`, 4.86M transactions)
- **Essai**: même modèle, plus d'échantillonnage — les 4856815 transactions utilisables chargées en entier (3795685 après jointure commune + nettoyage, soit 53x l'échantillon #1).
- **Résultat** (test=2025, n=689133) :

  |  | Baseline (commune) | Modèle (transaction) |
  |---|---|---|
  | MAE | 1007.38€/m² | **965.87€/m²** |
  | RMSE | 1987.06€/m² | **1853.59€/m²** |
  | R² | 0.3635 | **0.4461** |

  **Quasi identique à l'échantillon 500k** (R²=0.44→0.446, MAE 974€→966€) — confirme que le résultat #1 n'était pas un artefact d'échantillonnage, tient à pleine échelle. Feature importance stable aussi : `surface_bati` toujours en tête (1984), `department_code` (1948), `median_price_per_sqm` (976).
- **Statut**: **Confirmé à l'échelle** — le plafond identifié en #1 (limites du DVF standard, pas un problème de volume de données) est donc structurel, pas résolvable en ajoutant plus de transactions du même type.

### #3 — Rejoué post-reseed (fix bug multi-lot) : gain net important, pas juste une confirmation
- **Contexte** : ce modèle avait été (à tort) jugé "non affecté" par le fix du bug multi-lot (§Modèle rendement #0) et oublié une première fois dans `ml_run_full_pipeline.ps1` — or `ml/data/build_transactions.py` importe directement `BASE_PRICE_MULTI` de `pipeline/services/series.py`, donc il EST affecté. Rejoué le 2026-08-14 21h31 après déblocage d'un process `run_dvf` fantôme (terminal PowerShell resté ouvert dans un IDE depuis le 13/08, bloquait `dvf_cache.duckdb`, tué manuellement après échec de `taskkill`/`Stop-Process`, résolu via `Invoke-CimMethod -MethodName Terminate`).
- **Résultat** (test=2025, n=644193, vs pré-reseed n=689133) :

  |  | Baseline (commune) | Modèle (transaction) |
  |---|---|---|
  | MAE | 858.7€/m² (vs 1007.4€ avant) | **783.7€/m²** (vs 965.9€ avant) |
  | RMSE | 1493.8€/m² | **1399.7€/m²** |
  | R² | 0.5016 (vs 0.3635 avant) | **0.5624** (vs 0.4461 avant) |

  4 510 998 transactions chargées (vs 4 856 815 avant, -7.1% — cohérent avec le fix). **Gain net bien plus important qu'une simple baisse de bruit** : R² +26% en relatif (0.446→0.562), alors que Modèle 2 (même fix) ne gagnait que +0.5 point de R² (0.806→0.810). Explication : ce modèle utilise `median_price_per_sqm` (commune) comme FEATURE de contexte — une feature elle-même beaucoup moins bruitée post-fix améliore mécaniquement le modèle qui s'appuie dessus, en plus de la baisse de bruit sur sa propre target transactionnelle. Même dynamique que la baseline de Modèle 3 (persistance) qui s'est effondrée à la baisse après le fix.
- **Statut**: **Confirmé et amélioré à l'échelle, post-reseed**. Nouvelle référence officielle pour le mémoire : R²=0.5624 (pas 0.4461).

---

## Modèle rendement — extension (répond à "ça rapporte combien", pas juste "c'est cher ou pas")

Retour utilisateur explicite : sans rendement, l'outil reste un indicateur de
prix, pas un vrai outil d'aide à l'investissement locatif. `ml/models/yield_model.py`
+ `ml/data/rent.py`, même architecture que Modèle 2 (GB, mêmes
`PRICE_MODEL_FEATURES`) mais target = `rendement_brut` (%) au lieu du prix.

### #0 — Bug trouvé côté pipeline DVF : immeubles multi-lots à pièces mixtes surcomptés dans `price_sqm_all`
- **Question posée par l'utilisateur** : est-ce que les immeubles avec plusieurs lots sont bien pris en compte ?
- **Réponse** : oui, ils NE sont PAS exclus (contrairement à ce qu'on pourrait croire) — mais un vrai bug existe dans `pipeline/services/series.py`. DVF répète la `valeur_fonciere` TOTALE de la mutation sur chaque ligne de local. `dvf_mutation_prices` (dans `pipeline/services/dvf.py`) regroupe par (mutation_id, type_local, nb_pieces) : quand un immeuble vendu en bloc contient des lots de nb_pieces DIFFÉRENTS (ex. 2×T2 + 1×T3 vendus ensemble), chaque sous-groupe récupère le prix TOTAL de la mutation (`MAX(valeur_fonciere)`), pas sa part — le prix est donc compté plusieurs fois pour la même vente.
- **Mesure** (requête directe sur `dvf_cache.duckdb`, filtre `price_sqm_all` actuel) : 348 366 / 4 874 925 lignes (7,15%) sont dans ce cas. Leur prix/m² médian = 3333€/m² contre 2515€/m² pour les mutations "propres" (un seul type+nb_pieces) — **+32% de surestimation** sur ces lignes. Impact sur la médiane nationale pondérée globale : 2555€/m² (avec bug) vs 2515€/m² (sans) — **modeste au niveau national (+1.6%)**, mais peut fausser fortement la médiane trimestrielle d'une commune à faible volume où une seule vente d'immeuble mixte pèse beaucoup (`min_n=10` mutations).
- **Le garde-fou existe déjà, mais seulement pour T1-T4** : `BASE_PRICE_MONO` (`total_locaux = nb_locaux_this_group`) impose l'homogénéité complète pour `price_sqm_t1..t4`, mais `BASE_PRICE_MULTI` (utilisé par `price_sqm_all/appt/house` — celui qui alimente `median_price_per_sqm` sur `cities`, donc TOUT le pipeline ml/) ne vérifie que `n_distinct_types = 1` (même nature appart/maison), pas l'homogénéité des pièces.
- **Fix proposé (pas encore appliqué)** : ajouter `AND total_locaux = nb_locaux_this_group` à `BASE_PRICE_MULTI` dans `pipeline/services/series.py`, alignant son comportement sur `BASE_PRICE_MONO`. Vit dans `pipeline/` (code partagé, écrit en DB) — nécessite un reseed complet, pas fait sans confirmation explicite.
- **Statut**: **Bug identifié et quantifié, pas corrigé**. À citer dans le mémoire (limites de la donnée source, pas du modèle) même si non corrigé avant la rédaction.

### #1 — Premier run national
- **Source**: Carte des loyers ANIL/DHUP 2025 (`csv/loyers/*.csv`, 4 fichiers — appart tous types/T1-T2/T3+/maisons, identifiés via `Content-Disposition` HTTP car data.gouv.fr n'affiche pas de nom de fichier lisible). `rendement_brut = (loyer_appt_tous_types_annuel / prix_m²) × 100`, calculé dans `build_cross_sectional.py`. 7352/8414 communes réelles couvertes.
- **Résultat** (test, n=1103) :

  |  | Baseline (médiane département) | Modèle |
  |---|---|---|
  | MAE (points de rendement) | 1.41 | **1.16** |
  | RMSE | 1.92 | **1.59** |
  | R² | 0.21 | **0.46** |

  Bat la baseline nettement (~17.4% de mieux en MAE). `median_income` = feature la plus importante (0.19) — cohérent : zones à haut revenu = prix élevés sans loyer proportionnellement plus haut = rendement mécaniquement plus faible.
- **Exemple concret** : Murat-le-Quaire (63) — rendement réel 13.4% vs prédit 5.6%, prix 829€/m² seulement. **Breil-sur-Roya (06)** ressort ici aussi avec yield_opportunity_score=+1.03 — déjà présent dans le Top 20 prix (`generate_recommendations.py`) — double signal (sous-évalué ET rendement supérieur aux fondamentaux) sur la même commune, validation croisée entre deux modèles indépendants.
- **Statut**: **Corrigé / nouveau modèle fonctionnel**. Intégré dans `generate_recommendations.py` (composite_score inclut `yield_opportunity_pct`) — voir Top 20.

### #2 — Géographie du rendement : `ml/scripts/analyze_yield_geography.py`
- **Corrélations** (7347 communes, `rendement_reel` observé) : prix/m² **-0.754** (facteur dominant), revenu médian **-0.376**, population **-0.093** (quasi nulle — la taille de la commune seule n'explique presque rien, c'est le niveau de prix qui compte).
- **Médiane nationale 6.64%** (moyenne 7.02%, moyenne pondérée population 6.52%, moyenne pondérée volume de transactions DVF 6.35% — les grandes villes à fort volume tirent la moyenne vers le bas mais pas jusqu'à expliquer un écart avec des chiffres externes, voir ci-dessous).
- **Écart avec la moyenne SeLoger 2025 citée par l'utilisateur (~5.2% brut national)** : le calcul par transaction_volume-weighted (6.35%) réduit l'écart mais ne le ferme pas. Explication la plus probable, non vérifiée faute d'accès à la méthodologie SeLoger : (1) SeLoger utilise vraisemblablement le **prix affiché/demandé** (asking price) comme dénominateur, systématiquement au-dessus du prix de vente notarié DVF utilisé ici (négociation moyenne ~3-8% en France) → dénominateur plus gros → rendement plus bas ; (2) possible inclusion des frais de notaire (~7-8% dans l'ancien) dans le "coût d'acquisition" du calcul SeLoger, qu'on n'inclut pas ici (§6 utilise `Coût_acquisition` = prix nu) ; (3) le rendement ici est calculé sur `median_price_per_sqm` (DVF, avec le biais multi-lot documenté en #0 ci-dessus) vs un loyer moyen modèle ANIL/DHUP — deux sources différentes de celles de SeLoger. **Aucune de ces trois causes n'est confirmée par une source externe** — hypothèses de méthodologie, pas un résultat vérifié.
- **Carte** : premier jet en scatter (points, beaucoup de "trous" blancs dans les zones à faible densité communale). Remplacé par une **surface interpolée IDW** (k=10 plus proches voisins, pondération 1/distance², `sklearn.neighbors.BallTree` haversine — même pattern que `dist_nearest_major_city_km`), masquée au-delà de 18km du point réel le plus proche pour ne pas inventer de valeur hors de France (pas de polygone de frontière utilisé — la densité des communes DVF suffit à dessiner le contour). `GRID_RESOLUTION=400`, `MAX_DIST_KM=18`.
- **Retour utilisateur sur la carte interpolée** : "beaucoup de rouge" donne l'impression que le rendement est très élevé partout en France — biais réel de la carte (chaque commune pèse pareil visuellement, peu importe son volume de transactions). Deux ajouts pour corriger : `yield_bubble_map.png` (même géographie, taille de bulle = `transaction_volume`) et `yield_volume_distribution.png` (barres % communes vs % volume de marché par tranche de rendement). **Confirmé chiffré** : tranche 9%+ = 17.2% des communes mais seulement 9.2% du volume réel de transactions ; tranche <5% = 16.2% des communes mais 25.5% du volume réel. La carte interpolée surreprésente visuellement le haut rendement — le marché réel (pondéré transactions) est nettement plus concentré vers le bas/milieu de la distribution. `national_mean_tx_weighted` = 6.35% (vs 7.02% non pondéré).
- **Statut**: **Fonctionnel**. 3 cartes/graphiques + `rendement_by_department.csv` + `volume_distribution.csv` dans `ml/artifacts/yield_geography/latest/`.

### #3 — Rendement brut n'inclut NI frais de notaire NI travaux (confirmé, pas un bug — question utilisateur)
- **Question** : "est-ce que les frais de travaux et les frais de notaire sont pris en compte ?" — non, par définition. `rendement_brut = (rent_appt_all × 12) / median_price_per_sqm × 100` (`build_cross_sectional.py`) — exactement la formule §6 R_brut = Loyers/Coût_acquisition, coût_acquisition = prix nu. C'est la définition standard de "brut" (avant tout frais), pas une erreur, mais ça explique en partie l'impression "beaucoup de rouge".
- **Ajout** : `rendement_net_acquisition` (nouveau, dans `analyze_yield_geography.py`), dénominateur = prix × (1+`FRAIS_NOTAIRE_ANCIEN_PCT`=7.5%, barème officiel notaires.fr, pas une hypothèse), numérateur = loyer - charges estimées (10%) - taxe foncière réelle (DGFiP, par commune). **Travaux toujours exclus** — aucune source ouverte à la maille commune, les inclure serait inventer un chiffre.
- **Résultat, 7347 communes** : médiane **6.64% (brut) → 4.19% (net d'acquisition)**, soit **-37%**. 11.2% des communes tombent sous 3% net d'acquisition (zéro sur la carte brute). Carte comparative côte à côte, même échelle de couleur : `yield_map_brut_vs_net.png` — la carte "net" est presque entièrement bleue/blanche, confirmant visuellement que la carte "brut" seule surreprésentait le rendement.
- **Statut**: **Fonctionnel**. `yield_map_france_net.png` + `yield_map_brut_vs_net.png` ajoutés. `rendement_net_acquisition` reste optimiste (pas de travaux) — à toujours présenter à côté du brut dans le mémoire, jamais seul.

### #4 — Correction méthodologique : frais de notaire + travaux appartiennent au "brut", pas seulement au "net"
- **Argument utilisateur** : le rendement ne démarre qu'une fois le lot loué — donc TOUT ce qui est dépensé avant (prix + notaire + travaux, du capital investi) doit être au dénominateur, même pour un calcul "brut". Seules les charges d'exploitation récurrentes (après mise en location : charges, taxe foncière) distinguent net de brut. Argument correct, adopté — c'est la distinction moment-de-la-dépense (capex avant location vs opex après), pas juste "avec ou sans frais".
- **Ajout** : `TRAVAUX_RATIO_DEFAULT = 0.10` (10% du prix d'achat, convention usuelle citée par les investisseurs locatifs sur de l'ancien — PAS une mesure, aucune source ouverte à la maille commune) dans `ml/finance/cashflow.py`. `cout_acquisition_total()` étendu pour inclure prix + notaire (7.5%, barème officiel) + travaux (10%, convention).
- **3 niveaux calculés et comparés** (7347 communes, même échelle de couleur sur la carte) :

  | Niveau | Dénominateur | Numérateur | Médiane | Moyenne |
  |---|---|---|---|---|
  | 1. Naïf | prix nu | loyer brut | 6.64% | 7.02% |
  | 2. Brut / coût d'acquisition | prix + notaire + travaux | loyer brut | **5.65%** | 5.98% |
  | 3. Net / coût d'acquisition | prix + notaire + travaux | loyer - charges - taxe foncière | **3.83%** | 4.06% |

  18.8% des communes tombent sous 3% en net d'acquisition (0% sur la carte naïve). Le simple ajout des frais d'acquisition (niveau 1→2, sans toucher au numérateur) fait déjà baisser la médiane de ~15% (6.64→5.65).
- **Statut**: **Fonctionnel**. `yield_map_brut_vs_net.png` refait en 3 panneaux (naïf | brut/acquisition | net/acquisition). `rendement_brut_acquisition` et `rendement_net_acquisition` restent tous deux optimistes si le bien nécessite plus de travaux que la convention 10%.

### #5 — Bulle = volume de transactions ≠ bulle = population
- **Remarque utilisateur** : `yield_bubble_map.png` (taille = `transaction_volume`) montre où le marché s'ÉCHANGE (activité, où les investisseurs achètent en pratique), pas où sont les gens — deux questions différentes, pas interchangeables.
- **Ajout** : `make_bubble_map()` généralisé (`size_col` paramétrable) + `yield_bubble_map_population.png` (taille = `population`, déjà chargée via `build_cross_sectional`). Grosses bulles bleues = grandes villes peuplées à faible rendement, cohérent avec `corr_population=-0.093` (quasi nulle en pondéré-commune, mais la carte montre que les VILLES ellse-mêmes tirent le rendement pondéré-population vers le bas, voir `national_mean_pop_weighted`).
- **Statut**: **Fonctionnel**. Les deux bubble maps sont complémentaires : transaction_volume = où on investit en pratique aujourd'hui, population = où est le marché locatif potentiel (taille du parc/de la demande).

### #6 — Retrait de la version "naïve" + bulles agrandies (retour utilisateur)
- **"Naïf" retiré partout** : `make_comparison_map` passe de 3 à 2 panneaux (brut/acquisition vs net/acquisition uniquement), `yield_map_france.png` affiche désormais `rendement_brut_acquisition` (plus `rendement_reel`), `analyze()`/corrélations/tableau par département tous recalculés sur `rendement_brut_acquisition`. La colonne `rendement_reel` (prix nu, héritée de `yield_model.py`) n'est plus chargée/affichée dans ce script.
- **Nouveaux chiffres de référence** (rendement_brut_acquisition, 7347 communes) : médiane **5.7%** (vs 6.64% naïf), net 3.8%. Top département Aisne (02) 8.4% (vs 9.85% naïf), bottom Corse-du-Sud (2A) 3.5% (vs 4.11% naïf) — l'écart top/bottom se resserre légèrement une fois les frais d'acquisition inclus partout.
- **Bulles agrandies** : taille passée de `4 + 190*sqrt(x/max)` à `8 + 900*(x/max)^0.75` — exposant 0.75 (entre sqrt et linéaire) exagère volontairement le contraste grosses/petites communes ("plus choquant", retour utilisateur) tout en gardant les petites communes visibles.
- **Statut**: **Fonctionnel**. `rendement_reel` (naïf) reste la target de `yield_model.py` (le modèle ML entraîné n'a pas été retouché — seule cette couche d'analyse géographique descriptive a été corrigée) — écart méthodologique documenté ici, pas un changement du modèle.

### #7 — Cohérence : source unique de rendement_brut, coût d'acquisition partout (2026-08-14)
- **Problème identifié** : `yield_model.py` (modèle ML entraîné, R²=0.46) et `generate_recommendations.py` (Top 20, `yield_opportunity_score`) tournaient encore sur `rendement_brut` NAÏF (loyer/prix nu), pendant qu'`analyze_yield_geography.py` calculait séparément une version acquisition-ajustée — deux définitions de "rendement brut" coexistaient dans le repo, contradictoires si citées toutes les deux dans le mémoire.
- **Fix** : `build_cross_sectional.py` (source unique, utilisée par TOUS les modèles) calcule maintenant `rendement_brut = (rent_appt_all × 12) / cout_acquisition_total(median_price_per_sqm) × 100` — `cout_acquisition_total` = `ml.finance.cashflow.cout_acquisition_total` (prix + notaire 7.5% + travaux 10%). Un seul endroit définit la formule, tout le reste (yield_model.py, generate_recommendations.py, analyze_yield_geography.py) en hérite.
- **`yield_model.py` retrainé** (dry-run) sur la nouvelle target : R²=0.4605 (vs 0.4592 avant — quasi identique, transformation ~monotone du target, structure du modèle inchangée). `analyze_yield_geography.py` simplifié : ne recalcule plus `rendement_brut_acquisition`, alias direct de `rendement_reel` amont (plus de duplication de formule).
- **`simulate_cashflow.py` corrigé en cohérence** : apport et capital emprunté portent désormais sur `cout_acquisition_total(prix_achat)` (prix+notaire+travaux), pas le prix nu. Impact mesuré (dry-run, Top 20 × 5 profils) : cash-flow mensuel médian Primo-accédant +28€ → **-23€**, Levier maximal -0€ → **-64€**. Seul le profil "Apport élevé" reste proche de l'équilibre (médiane +4€). Confirme que la rigueur ajoutée sur le rendement change concrètement les résultats du cash-flow, pas juste une correction cosmétique.
- **Statut**: **Fonctionnel, cohérent**. Nécessite de relancer toute la chaîne (yield_model → generate_recommendations → simulate_cashflow → analyze_yield_geography) pour propager le fix dans les artefacts déjà générés — pas encore fait à cette date, voir README.md pour les commandes.

---

## Cash-flow — extension (calcul déterministe, PAS un modèle ML — CONTEXTE_ML_MEMOIRE.md §6)

Retour utilisateur : le rendement brut/net répond à "ça rapporte combien en théorie" mais pas à "est-ce que je peux financer ça et rester positif chaque mois". `ml/finance/cashflow.py` (formules pures §6) + `ml/finance/profiles.py` (5 profils bancaires) + `ml/scripts/simulate_cashflow.py` (orchestration Top 20 × 5 profils). Bien de référence : appartement 52m² (cohérent avec la référence surface de la Carte des loyers ANIL "appartements tous types").

### #1 — Premier run, Top 20 × 5 profils (100 simulations)
- **Source ajoutée**: DGFiP "Fiscalité locale des particuliers" (`csv/fiscalite/fiscalite-locale-des-particuliers.csv`, 42MB, 174668 lignes multi-années 2021-2025) — taux `Taux_Global_TFB` par commune, encodage `utf-8-sig`. **34874/34969 communes couvertes (100% sur le dernier exercice, pas de secret statistique ici contrairement à Filosofi/LOVAC)**.
- **Deux hypothèses non mesurées, explicitement documentées** (pas de source ouverte trouvée pour ni l'une ni l'autre) :
  - base cadastrale ≈ 50% du loyer annuel théorique (règle usuelle de simulation immobilière, pas la vraie valeur locative DGFiP — non publiée à la maille commune)
  - charges non récupérables ≈ 10% du loyer annuel (hypothèse, aucune source ouverte)
- **Résultat** (20 communes du Top 20 avec `price_data_source` réel, hors communes estimées — 20/20 couvertes) :

  | Profil | Cash-flow mensuel moyen | médian | min | max |
  |---|---|---|---|---|
  | Primo-accédant standard (10% apport, 3.5%/25ans) | +5€ | +28€ | -174€ | +108€ |
  | Investisseur expérimenté (20% apport, 3.2%/20ans) | +11€ | +32€ | -166€ | +114€ |
  | Apport élevé (40% apport, 3.4%/15ans) | **+34€** | +51€ | -135€ | +139€ |
  | Effet de levier maximal (5% apport, 3.9%/25ans) | **-35€** | -0€ | -226€ | +74€ |
  | Profil prudent SCI (15% apport, 3.6%/20ans) | -30€ | +3€ | -219€ | +77€ |

  Écart de ~70€/mois entre le meilleur (apport élevé) et le pire (levier maximal) profil, **sur les mêmes 20 communes** — le financement pèse autant que le choix de la commune sur le cash-flow réel. Seul le profil "apport élevé" a une médiane confortablement positive ; les 4 autres oscillent près de 0, donc la moitié des communes du Top 20 (sélectionnées sur prix+croissance+rendement) sont cash-flow négatif pour un investisseur à faible apport.
- **Limite connue, pas contournée** : le taux de vacance utilisé (`vacancy_rate` INSEE, logement général) n'est pas un taux de vacance locative — voir `## Nouvelles sources de données`, aucune source ouverte trouvée à la maille commune pour le délai de relocation réel.
- **Statut**: **Fonctionnel**. Artefacts : `ml/artifacts/cashflow/latest/cashflow_simulations.csv` (100 lignes), `summary_by_profile.csv`, `cashflow_by_profile.png`.

---

## Nouvelles sources de données intégrées (2026-08-14)

Toutes en lecture locale pure côté `ml/` (pas d'écriture DB, pas de migration Prisma) — voir `ml/README.md` "Design".

| Source | Fichier | Couverture | Utilisée par |
|---|---|---|---|
| INSEE Filosofi 2021 (`median_income`) | `csv/DS_FILOSOFI_CC_data.csv` (44MB, format long, indicateur `MED_SL`) | 31212/34969 communes (certaines sous secret statistique) | Modèle 1 (#9), Modèle 2 (#5) |
| Zonage ABC national | `csv/zonage-abc-national.csv` (téléchargé depuis data.gouv.fr, `CODGEO;DEP;LIBGEO;Zonage`) | 34875/34969 communes (vs 1266 IDF-only avant) | Modèle 2 (#5), remplace le `housing_zone` DB pour l'entraînement (sans toucher `cities` en DB) |
| `dist_nearest_major_city_km` | Calculée, pas de fichier — BallTree haversine sur lat/lon + population déjà en base (top 50 communes par population = référence nationale fixe) | 34969/34969 (toutes les communes avec lat/lon) | Modèle 2 (#5), Modèle 3 (hérité via `PRICE_MODEL_FEATURES` partagé) |
| Carte des loyers ANIL/DHUP 2025 (`rent_appt_*`, `rent_maison`) | `csv/loyers/pred-{app,app12,app3,mai}-mef-dhup.csv` (4 fichiers, ~4.5MB chacun, encodage Latin-1) | 34900/34969 communes | `rendement_brut` (calculé), Modèle rendement (§ ci-dessus) |
| DGFiP Fiscalité locale des particuliers (`taux_foncier_pct`) | `csv/fiscalite/fiscalite-locale-des-particuliers.csv` (42MB, 174668 lignes, multi-années 2021-2025, encodage utf-8-sig) | 34874/34969 communes (100%, pas de secret statistique) | Cash-flow (§ ci-dessus) |

Attention : le fichier zonage placé par l'utilisateur dans `csv/logement-liste-des-communes-selon-le-zonage-abc.csv` est resté IDF-only (1266 lignes, source régionale différente) — le fichier national utilisé est un fichier distinct (`csv/zonage-abc-national.csv`), téléchargé et validé séparément.

---

## Pipeline data (bugs infra, pas des résultats de modèle — mais utile pour la section méthodo)

### #1 — Enum Postgres `SerieName` rejette une comparaison texte
- **Résultat**: `psycopg2.errors.UndefinedFunction: operator does not exist: "SerieName" = text`
- **Statut**: **Corrigé** — cast `::text` explicite dans `build_cross_sectional.py` / `build_panel.py`.

### #2 — `datetime.date` non converti (dtype object)
- **Résultat**: `AttributeError: Can only use .dt accessor with datetimelike values`
- **Statut**: **Corrigé** — `pd.to_datetime()` explicite dans `build_panel.py`.

### #3 — `cluster_id` toujours NaN après jointure
- **Résultat**: 0/194 communes avec `cluster_id` malgré `cluster_feature_used=true` dans les métadonnées.
- **Cause**: CSV relu avec `insee_code` en int64 au lieu de string ; le join échouait silencieusement (aucune erreur, juste zéro correspondance).
- **Statut**: **Corrigé** — `dtype=str` forcé à la lecture (`load_cluster_assignments`).

### #4 — `active_population` absent de l'enum `SerieName`
- **Résultat**: `psycopg2.errors.InvalidTextRepresentation: invalid input value for enum "SerieName": "active_population"`
- **Cause**: gap dans le schéma Prisma (pas fixable depuis ce repo).
- **Statut**: **Contourné** — `seed_employment_series` et `seed_dashboard_fields` retirés du pipeline seed national (`seed_national.ps1`) ; de toute façon inutiles pour les 4 modèles mémoire.
