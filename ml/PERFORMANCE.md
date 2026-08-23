# Suivi de performance — Rentium ML

Tableau de bord condensé : évolution des métriques officielles au fil des
runs. Pour le détail des essais, des causes d'échec et des décisions
méthodologiques, voir `EXPERIMENTS_LOG.md` — ce fichier-ci ne répond qu'à
"où on en est, dans le temps", pas "pourquoi".

Convention : **gras** = version actuellement recommandée pour le mémoire.
Toutes les lignes "national" sont sur les ~35k communes françaises (dept-77
= phase de test initiale sur 507 communes, gardée pour référence historique).

---

## Modèle 1 — Clustering (K-Means)

| Date | Config | n communes | k | Silhouette | ANOVA F (validation prix) | Note |
|---|---|---|---|---|---|---|
| 2026-08-13 | dept-77, non pondéré | 86 | 4 | 0.28 | — (pas encore mesuré) | Test initial |
| 2026-08-13 | national, `price_growth_3y` inclus | 3281 | 3 | 0.31 | — | Avant le fix #1 (dropout 91%) |
| 2026-08-13 | national, sans `price_growth_3y`, non pondéré | 32992 | 3 | 0.3065 | 194.50 | |
| 2026-08-13 | national, **pondéré** (ANOVA) | 32992 | 3 | 0.3740 | **291.44** | **Meilleur sur les 2 métriques** |
| 2026-08-14 | national, pondéré + `median_income` | 29601 | 2 | 0.5469 | 137.43 | Silhouette trompeuse, F dégradé (§Modèle 1 #9) |
| 2026-08-14 | national, pondéré + `dist_nearest_major_city_km` (k auto) | 32992 | 2 | 0.5188 | 137.63 | Même symptôme |
| 2026-08-14 | national, pondéré + distance, k forcé | 32992 | 3 | 0.3195 | 241.92 | Distance pas assez pondérée par l'ANOVA auto |
| 2026-08-14 | national, hiérarchique (densité INSEE, banlieue séparée) | 32992 | 4 groupes × k=2 | 0.28-0.31 (par groupe) | 165.54 | Seule version à isoler une vraie banlieue (n=1737, vérifiée) |
| 2026-08-14 | national, GMM (comparaison) | 32992 | 3 | 0.1322 | 146.70 | Détecte 1113 communes "frontière" |
| 2026-08-14 | national, HDBSCAN (comparaison) | 32992 | — | N/A | — | 0 cluster, 100% noise — échec robuste |
| 2026-08-14 | national, pondéré + distance, k=3 — run officiel (avant reseed) | 32992 | 3 | 0.3195 | 241.92 | `dist_nearest_major_city_km` restée en permanence dans `CLUSTERING_FEATURES` (utile pour Modèle 2/3 via `cluster_id`) → F=291.44 n'est plus atteignable sans la retirer à nouveau. |
| 2026-08-14 | **national, post-reseed (fix bug multi-lot DVF) — RUN OFFICIEL ACTUEL** | — | 3 | 0.3435 | **270.28** | Reseed complet (`python -m pipeline.scripts.run_dvf` après fix `series.py`). F remonte (241.92→270.28) — cohérent : moins de bruit sur les prix DVF utilisés en validation externe. |

**Recommandé (mis à jour)**: **Version officielle = F=270.28** (national, pondéré, k=3, post-reseed). Version banlieue (hiérarchique) à citer séparément pour le récit si besoin.

---

## Modèle 2 — Prix/m² (Gradient Boosting)

| Date | Config | n | R² | MAE modèle | MAE baseline | Gain vs baseline |
|---|---|---|---|---|---|---|
| 2026-08-13 | dept-77, features de base | 194 | 0.51 | 453€ | 714€ | 36.5% |
| 2026-08-13 | national, features de base | 7349 | 0.46-0.54 | 575-490€ | 613€ | 6-20% |
| 2026-08-14 | national, + `department_code` | 7349 | 0.68 | — | 613€ | 20% |
| 2026-08-14 | national, + `--tune` (RandomizedSearchCV) | 7347 | 0.72 | 456€ | 613€ | 26% |
| 2026-08-14 | national, + `median_income` + `dist_nearest_major_city_km` + zonage national + `--tune` | 7347 | 0.8065 | 371€ | 604€ | 38.6% |
| 2026-08-14 | national, LightGBM catégoriel natif (comparaison) | 7347 | 0.8063 | 366€ | 604€ | 39.4% (quasi-égalité avec sklearn GB) |
| 2026-08-14 | national, run officiel (avant reseed, clustering F=241.92 en amont) | 7347 | 0.8058 | 370€ | 604€ | 38.7% |
| 2026-08-14 | **national, post-reseed (fix bug multi-lot DVF) — RUN OFFICIEL ACTUEL** | — | **0.8097** | **367.7€** | 584.7€ | **37.1%** |

**Recommandé**: `python -m ml.models.price_model --tune` (avec `median_income`/`dist_nearest_major_city_km`/zonage déjà dans `PRICE_MODEL_FEATURES` par défaut). Stable run après run. Baseline aussi plus basse post-reseed (604€→585€) — même cause que Modèle 3 : moins de bruit multi-lot dans les prix.

---

## Modèle 3 — Forecasting (Gradient Boosting + lags)

| Date | Config | Horizon | R² modèle | MAE modèle | MAE baseline | Coverage intervalle |
|---|---|---|---|---|---|---|
| 2026-08-13 | dept-77, récursif (1 modèle, rollout) | t+1..t+4 | 0.85 | 211€ | 239€ | — |
| 2026-08-13 | national, récursif | t+1 | 0.9409 | 200.81€ | — | — (bug compounding, voir §Modèle 3 #2) |
| 2026-08-14 | national, **modèles directs par horizon** (fix) | t+1 | 0.9388 | 209.26€ | 224.71€ | — |
| | | t+2 | 0.9381 | 216.22€ | 231.03€ | — |
| | | t+3 | 0.9342 | 226.25€ | 242.52€ | — |
| | | t+4 | 0.9246 | 238.70€ | 253.45€ | — |
| 2026-08-14 | national, + features enrichies (`median_income` etc, héritées de `PRICE_MODEL_FEATURES`) + intervalles Q10/Q90 | t+1 | 0.9396 | 208.10€ | 224.71€ | 82.1% |
| | | t+2 | 0.9385 | 215.46€ | 231.03€ | 83.2% |
| | | t+3 | 0.9352 | 225.00€ | 242.52€ | 83.3% |
| | | t+4 | 0.9255 | 237.28€ | 253.45€ | 82.2% |
| 2026-08-14 | national, run officiel (avant reseed) | t+1 | 0.9390 | 208.70€ | 224.71€ | 82.1% |
| | | t+2 | 0.9387 | 215.42€ | 231.03€ | 83.3% |
| | | t+3 | 0.9352 | 224.84€ | 242.52€ | 83.2% |
| | | t+4 | 0.9254 | 237.41€ | 253.45€ | 82.1% |
| 2026-08-14 | **national, post-reseed (fix bug multi-lot DVF) — RUN OFFICIEL ACTUEL** | t+1 | 0.9493 | **156.2€** | **68.0€ (BASELINE GAGNE)** | 80.7% |
| | | t+2 | 0.9411 | 193.7€ | 139.6€ (BASELINE GAGNE) | 80.7% |
| | | t+3 | 0.9297 | 223.1€ | 193.3€ (BASELINE GAGNE) | 80.7% |
| | | t+4 | **0.9335** | **224.1€** | 248.1€ (modèle gagne) | 80.9% |

**⚠️ Régression post-reseed, voir `EXPERIMENTS_LOG.md` §Modèle 3 #11 pour l'analyse complète.** Le modèle ne bat la baseline naïve (persistance, P_hat=P_t) qu'à t+4. À t+1/t+2/t+3 la baseline devient soudainement très précise (MAE 68-193€ contre 224-243€ avant reseed) — le bug multi-lot injectait du bruit trimestriel artificiel que le modèle apprenait partiellement à compenser ; une fois ce bruit retiré, la persistance simple redevient difficile à battre à court terme (comportement attendu sur un marché immobilier peu volatile). **Recommandé**: présenter les 4 horizons tels quels dans le mémoire, avec cette explication — ne pas cacher que le modèle perd à courts horizons, c'est un résultat honnête et cohérent une fois la cause comprise. `python -m ml.models.forecasting` reste la commande officielle.

---

## Modèle 4 — Quantile Regression

| Date | Config | n test | Coverage [Q10,Q90] | Pinball tau=0.5 (modèle vs baseline) |
|---|---|---|---|---|
| 2026-08-13 | dept-77, prix brut | 30 | 43-47% | — |
| 2026-08-13 | national, prix brut | 1103 | 76.4% | — (tau=0.9 perdait contre baseline) |
| 2026-08-14 | national, log1p(target) (fix) | 1103 | 76.5% | 249 vs 306 |
| 2026-08-14 | national, + features enrichies | 1103 | 76.6% | 196 vs 302 |
| 2026-08-14 | national, + conformal prediction (CQR) | 1103 | 82.8% | (pinball inchangé, coverage recalibrée séparément) |
| 2026-08-14 | national, run officiel (avant reseed) | 1103 | 82.5% (marge conformal ±67.4€) | 196 vs 302 |
| 2026-08-14 | **national, post-reseed — RUN OFFICIEL ACTUEL** | 1103 | **79.9%** (marge conformal ±39.6€) | 197 vs 292 |

**Recommandé**: colonnes `*_conformal` de `predictions.csv` — garantie de coverage théorique, pas juste mesurée. Post-reseed, coverage tombe à 79.9% — quasi pile sur la cible 80% (mieux calibré qu'avant, qui surcouvrait à 82.5%), et la marge conformal se resserre (±67€→±39€, cohérent avec des prix moins bruités).

---

## Modèle hédonique — transaction-level (extension, hors cahier des charges)

| Date | Config | n test | R² modèle | R² baseline | MAE modèle | MAE baseline |
|---|---|---|---|---|---|---|
| 2026-08-14 | national, échantillon 500k transactions, LightGBM | 71105 | 0.44 | 0.36 | 974€ | 1009€ |
| 2026-08-14 | national, `--full`, PRÉ-RESEED (4.86M transactions chargées) | 689133 | 0.4461 | 0.3635 | 966€ | 1007€ |
| 2026-08-14 | **national, `--full`, POST-RESEED (21h31) — RUN OFFICIEL ACTUEL** | 644193 | **0.5624** | 0.5016 | **783.7€** | 858.7€ |

**Statut**: `price_hedonic.py` avait été oublié une première fois dans `ml_run_full_pipeline.ps1` (corrigé, voir `ml_run_models.ps1` étape 3/3), puis bloqué par un process `run_dvf` fantôme (terminal PowerShell resté ouvert dans un IDE depuis le 13/08, tué manuellement). Une fois rejoué : **4 510 998 transactions chargées** (vs 4.86M avant, -7.2%, cohérent avec le fix multi-lot), 3 530 597 matchées à une commune non-estimated, 3 530 233 avec features complètes, train=2 886 040/test=644 193. **Gain net important** : R² 0.446→**0.562** (+26%), MAE 966€→**784€**. Le gain dépasse la simple baisse de bruit transactionnel — la feature `median_price_per_sqm` (commune, utilisée en contexte) est elle-même beaucoup moins bruitée post-fix, ce qui améliore mécaniquement tout modèle qui s'appuie dessus (même dynamique que la baseline de Modèle 3, voir plus haut).

---

## Modèle rendement — extension (répond à "ça rapporte", pas juste "c'est cher")

| Date | Config | n test | R² modèle | R² baseline | MAE modèle (pts) | MAE baseline (pts) |
|---|---|---|---|---|---|---|
| 2026-08-14 | national, GB tuné, target=rendement_brut naïf (loyer/prix nu, avant reseed) | 1103 | 0.4592 | 0.2104 | 1.16 | 1.41 |
| 2026-08-14 | **national, target=rendement_brut acquisition-ajusté (notaire+travaux) + post-reseed — RUN OFFICIEL ACTUEL** | 1103 | **0.4765** | 0.2705 | **0.98** | 1.15 |

**Statut**: R² améliore légèrement (0.459→0.477). Target changé (voir `EXPERIMENTS_LOG.md` "Modèle rendement" #6/#7) — plus comparable en valeur brute au run précédent (échelle différente : médiane nationale 6.6%→5.7%), mais le modèle bat toujours nettement sa baseline (~14.7% mieux en MAE). Intégré au Top 20 combiné (`generate_recommendations.py`, `yield_opportunity_pct` dans le `composite_score`).

---

## Cash-flow — extension (calcul déterministe §6, pas un modèle ML)

| Date | Config | n simulations | Cash-flow mensuel moyen (min-max) | Note |
|---|---|---|---|---|
| 2026-08-14 | Top 20 × 5 profils, réf. 52m², apport/capital sur prix nu (avant fix acquisition) | 100 | -35€ à +34€ selon profil | Apport élevé = seul profil médiane confortablement positive |
| 2026-08-14 | **Top 20 × 5 profils, apport/capital sur coût d'acquisition réel (prix+notaire+travaux) + post-reseed — RUN OFFICIEL ACTUEL** | 100 | **-54€ à +13€ selon profil** | Tous les profils sauf "apport élevé" ont une moyenne négative |

**Statut**: fonctionnel, plus réaliste. Inclure notaire+travaux dans l'apport/capital emprunté (voir `EXPERIMENTS_LOG.md` "Modèle rendement" #7) fait chuter le cash-flow moyen partout (ex: Primo-accédant +5€→-16€) — 4 profils sur 5 sont désormais en moyenne négative sur le Top 20, seul "Apport élevé" reste positif (+13€ moyen, +22€ médian). Détail par commune × profil dans `ml/artifacts/cashflow/latest/cashflow_simulations.csv`. Hypothèses non mesurées inchangées (base cadastrale ≈50% loyer, charges ≈10% loyer, travaux ≈10% prix) — voir `EXPERIMENTS_LOG.md`.

---

## État au 2026-08-14 19h30 : cohérent, post-reseed complet

Pipeline complet relancé dans l'ordre après le fix du bug multi-lot DVF
(`pipeline/services/series.py`) : reseed DB → 4 modèles mémoire → yield_model
--tune → generate_recommendations → simulate_cashflow → analyze_yield_geography
→ generate_charts → check_performance (voir `ml_run_full_pipeline.ps1`).
`cities.updated_at` confirmé à 2026-08-14 16:23 (post-fix). C'est l'état à
citer dans le mémoire tel quel.

**Impact du reseed, résumé** :
- Modèle 1 (clustering) : F=241.92→**270.28** (amélioré, moins de bruit prix)
- Modèle 2 (price) : R²=0.8058→**0.8097** (stable/amélioré)
- Modèle 3 (forecasting) : **régression réelle** — ne bat plus la baseline à t+1/t+2/t+3 (voir ligne dédiée ci-dessus et `EXPERIMENTS_LOG.md` §Modèle 3 #11). À documenter honnêtement dans le mémoire, pas un bug.
- Modèle 4 (quantile) : coverage 82.5%→**79.9%** (mieux calibré, plus proche de la cible 80%)
- Modèle hédonique : **inchangé** (0.4461→0.4463), sanity check — n'était pas censé être affecté
- Modèle rendement : target changé (acquisition-ajusté) + reseed, R²=0.459→**0.477**
- Cash-flow : formule apport/capital corrigée, résultats significativement plus prudents (4/5 profils passent en moyenne négative sur le Top 20)

## Prochaines mesures à faire

- [x] ~~Modèle hédonique sur `--full`~~ — fait, confirmé stable à l'échelle et au reseed.
- [x] ~~Modèle 1 : valider la version recommandée~~ — F=270.28 post-reseed, nouvelle référence.
- [x] ~~Reseed DVF (fix bug multi-lot) + pipeline complet relancé~~ — fait 2026-08-14, tous les artefacts à jour.
- [x] ~~Cohérence rendement_brut (naïf vs acquisition-ajusté)~~ — source unique dans `build_cross_sectional.py`, tout en hérite.
- [ ] Décider comment présenter la régression du Modèle 3 dans le mémoire (résultat honnête à assumer, pas à cacher — voir discussion ci-dessus)
- [ ] Rédaction du mémoire — aucune ligne écrite à ce stade, tout ce fichier + `EXPERIMENTS_LOG.md` existent pour l'alimenter
