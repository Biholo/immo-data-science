# Roadmap IA — 3 axes de modélisation

Objectif : ajouter une couche IA/ML au-dessus du pipeline DVF existant, pour transformer
les séries brutes en signaux actionnables pour l'investissement immobilier.

Axes retenus (validés) : **score d'opportunité**, **forecasting prix**, **clustering marchés**.
Non retenu pour l'instant : anomaly detection (couvert par règle fixe dans `audit.py`),
yield forecasting (bloqué tant que `rent_sqm_*` non intégré).

---

## 0. Dépendances data — état actuel

| Feature | Statut | Bloque |
|---|---|---|
| `price_sqm_all` + 13 autres séries DVF | Acquis | — |
| `population`, `unemployment_rate`, `owner_rate`, `vacancy_rate`, `social_housing_rate`, `aging_index`, `secondary_residence_rate` | Acquis | — |
| `housing_zone` (DHUP A/Abis/B1/B2/C) | Acquis (dashboard field) | — |
| `price_growth_3y`, `transaction_volume` (dénormalisé) | Acquis (`denormalize.py`) | — |
| `median_income` (Filosofi) | **Pas encore** (ROADMAP.md Phase 3) | Score opportunité v2 (proxy revenu réel au lieu d'`unemployment_rate` seul) |
| `rent_sqm_t1..t4` (Carte des loyers) | **Pas encore** | Yield forecasting (hors scope ici) |
| `price_data_source` (`dvf` vs `estimated`/IDW) | Acquis | Filtrage obligatoire — exclure/pondérer `estimated` pour entraînement (bruit IDW) |

**Conclusion** : les 3 axes sont lançables *maintenant* avec les 21/39 séries acquises.
`median_income` (Phase 3 du ROADMAP data) améliorera la précision du score opportunité mais n'est pas bloquant.

---

## 1. Score d'opportunité (sous-évaluation)

**Principe** : régression sur profil socio-éco → prix attendu. Résidu (prix réel − prix prédit)
= signal sous/sur-évaluation. Remplace le composite manuel `attractivenessRank` prévu en
ROADMAP.md §Phase 3 par un score appris.

- **Target** : `price_sqm_all`, dernier trimestre, par commune
- **Features (v1, dispo maintenant)** :
  `population`, `unemployment_rate`, `owner_rate`, `vacancy_rate`, `social_housing_rate`,
  `aging_index`, `secondary_residence_rate`, `housing_zone` (encodé)
- **Filtrage entraînement** : exclure ou down-weighter communes avec `price_data_source = estimated`
  (prix interpolé IDW, pas une vraie observation marché)
- **Modèle** : gradient boosting (XGBoost/LightGBM) — cross-sectionnel, ~35k communes, pas de
  série temporelle ici
- **Sortie** : `opportunity_score = (prix_réel - prix_prédit) / prix_prédit`, négatif = sous-évalué
- **v2 (post Phase 3 data)** : ajouter `median_income` en feature → proxy revenu direct au lieu
  d'`unemployment_rate` seul, devrait réduire le bruit du résidu

---

## 2. Forecasting prix

**Principe** : projeter `price_sqm_all` (et variantes T1-T4) sur 4-8 trimestres par commune.

- **Contrainte** : seulement 17 points trimestriels par commune (2021-2025) — trop court pour
  LSTM fiable. Deux options :
  - Gradient boosting avec features de lag (t-1, t-2, t-4) + `demographicGrowth5y` +
    `company_creations` (Phase 3) comme leading indicators, entraîné sur toutes les communes
    en pool (pas un modèle par ville)
  - Prophet par commune (gère bien peu de points + saisonnalité), plus simple à démarrer
- **Filtrage** : exclure communes `price_data_source = estimated` de l'entraînement (même
  raison qu'axe 1) — possible de leur appliquer le forecast une fois modèle entraîné sur
  communes réelles similaires (cluster, cf axe 3)
- **Sortie** : `price_forecast_next_q`, `price_forecast_4q`, intervalle de confiance

---

## 3. Clustering marchés

**Principe** : segmenter les communes par profil, pour filtrer par typologie d'investissement
plutôt que ville par ville.

- **Features** : mêmes 7 socio-éco que axe 1 + `transaction_volume`, `price_growth_3y`
  (déjà dénormalisés sur `cities`)
- **Modèle** : K-means (simple, k à déterminer par elbow/silhouette) ou HDBSCAN (pas besoin de
  fixer k, gère mieux les communes atypiques/outliers)
- **Sortie** : `cluster_id` + label interprétable à documenter manuellement après inspection
  (ex. "tendu étudiant", "périurbain vieillissant", "littoral résidences secondaires")
- **Usage transverse** : sert aussi à axe 1 (comparer résidu dans le même cluster plutôt que
  vs toute la France) et axe 2 (forecast des communes `estimated` via voisins du même cluster)

---

## 4. Stockage

Nouvelle table `city_scores` (ou colonnes dénormalisées sur `cities`, pattern identique à
`denormalize.py` existant) :

| Colonne | Source |
|---|---|
| `opportunity_score` | Axe 1 |
| `cluster_id` | Axe 3 |
| `cluster_label` | Axe 3 (manuel, post-inspection) |
| `price_forecast_next_q` | Axe 2 |
| `price_forecast_4q` | Axe 2 |
| `model_version` | Traçabilité — quel run/date a produit le score |

---

## 5. Ordre de build

1. **Clustering** — le plus simple (unsupervised, pas de target à valider), sert de brique
   aux deux autres axes (comparaison intra-cluster, imputation `estimated`)
2. **Score opportunité** — dépend du clustering pour comparaison intra-cluster (optionnel en v1,
   peut tourner sans)
3. **Forecasting** — le plus complexe (peu de points, bruit IDW), bénéficie du clustering pour
   traiter les communes `estimated`

Pas de code pour l'instant — ce doc sert de référence avant industrialisation en Python
(pattern `pipeline/ml/`, cohérent avec `pipeline/services/` existant).
