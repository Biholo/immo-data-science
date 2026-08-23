# Roadmap — 39 courbes cibles

Objectif : couvrir les 39 séries listées ci-dessous. Classement par difficulté d'acquisition,
avec méthode d'obtention pour chaque item non trivial.

**Règle générale difficulté :**
- **Acquis** : déjà calculé par le pipeline actuel.
- **Facile** : calcul pur à partir de données déjà en base (aucune nouvelle source).
- **Moyen** : nouvelle source, mais open data officielle gratuite, format fichier statique
  (même pattern que les seed scripts existants).
- **Difficile** : aucun dataset public structuré n'existe → nécessite scraping d'annonces.

**Correction importante** : tout "difficile" n'égale pas scraping automatiquement. Les loyers
ont une alternative officielle (Carte des loyers, SDES). Le scraping reste incontournable
uniquement pour les données de flux d'annonces (délais, stock).

---

## 1. Acquis (21/39) — rien à faire

| Courbe | Source | Fichier pipeline |
|---|---|---|
| `price_sqm_all` | DVF | `services/series.py` |
| `price_sqm_appt` | DVF | idem |
| `price_sqm_house` | DVF | idem |
| `price_sqm_t1` | DVF | idem |
| `price_sqm_t2` | DVF | idem |
| `price_sqm_t3` | DVF | idem |
| `price_sqm_t4` | DVF | idem |
| `surface_median_t1` | DVF | idem |
| `surface_median_t2` | DVF | idem |
| `surface_median_t3` | DVF | idem |
| `surface_median_t4` | DVF | idem |
| `transaction_volume` | DVF | idem |
| `vefa_share` | DVF | idem |
| `land_price_sqm` | DVF | idem |
| `population` | INSEE (base-ic-evol-struct-pop) | `seed_pop_series.py` |
| `unemployment_rate` | INSEE (base-ic-activite-residents) | `seed_rp_series.py` |
| `secondary_residence_rate` | INSEE (base-ic-logement) | `seed_logement_series.py` |
| `aging_index` | INSEE (base-ic-evol-struct-pop) | `seed_pop_series.py` |
| `social_housing_rate` | INSEE (base-ic-logement) | `seed_logement_series.py` |
| `owner_rate` | INSEE (base-ic-logement) | `seed_logement_series.py` |
| `vacancy_rate` | INSEE (base-ic-logement) | `seed_logement_series.py` |

---

## 2. Facile (2/39) — calcul pur, zéro nouvelle source

| Courbe | Comment l'obtenir |
|---|---|
| `years_to_buy` | `price_sqm_all × surface_moyenne / median_income`. Dès que `median_income` dispo (§3), pur calcul SQL/Python, pas de fichier de plus. |
| `housing_effort_rate` | Mensualité estimée (prix bien type × taux crédit courant / durée) ÷ `median_income`. Combine deux champs déjà/bientôt en base. Formule à figer (hypothèse taux d'emprunt + durée à documenter en annexe méthodo). |

---

## 3. Moyen (7/39) — nouvelle source, open data officielle, pattern seed script identique à l'existant

| Courbe | Source | Comment |
|---|---|---|
| `median_income` | **INSEE Filosofi** — "Revenus, pauvreté et niveau de vie" (data.gouv.fr / insee.fr), fichier commune, gratuit, annuel. | Même pattern que `seed_logement_series.py` : télécharger CSV, join sur code INSEE 5 car., écrire script `seed_income_series.py`. |
| `company_creations` | **INSEE Sirene** (API REST officielle, gratuite) ou stock annuel data.gouv.fr "Créations d'entreprises par commune". | API à intégrer (pas juste fichier statique) : requête par code commune/année, ou téléchargement du stock annuel si dispo en CSV — vérifier avant de coder l'API. |
| `rent_sqm_t1` | **Carte des loyers** (SDES / data.gouv.fr, "Indicateurs de loyers d'annonce par commune"), gratuite, annuelle, **déjà ventilée par typologie** (studio/T1, T2, T3, T4+). | Télécharger CSV, join code INSEE, script `seed_rent_series.py` — pattern identique aux autres seeds. |
| `rent_sqm_t2` | idem | idem |
| `rent_sqm_t3` | idem | idem |
| `rent_sqm_t4` | idem | idem |
| `gross_yield_t1..t4` | Calcul pur : `(rent_sqm_tX × 12) / price_sqm_tX`. | Une fois `rent_sqm_tX` en base (ci-dessus), zéro nouvelle source — dérivé au même titre que `years_to_buy`. |

`gross_yield_t1..t4` compte comme 4 courbes distinctes mais 1 seule dépendance (Carte des loyers) + calcul. Total "Moyen" réel = 3 sources à intégrer (Filosofi, Sirene, Carte des loyers) qui débloquent 7 courbes.

---

## 4. Difficile (9/39) — scraping requis, aucun dataset public structuré

| Courbe | Pourquoi pas d'alternative officielle |
|---|---|
| `search_time_t1` | Délai de recherche acheteur par typologie — mesurable seulement via durée de vie des annonces actives (date publication → date dépublication), donnée propriétaire des plateformes. |
| `search_time_t2` | idem |
| `search_time_t3` | idem |
| `search_time_t4` | idem |
| `sale_time_t1` | Délai vente réel par typologie — idem, nécessite tracking annonce (mise en ligne → retrait/vendu). DVF ne donne pas de date de mise en vente, seulement date de mutation notariée. |
| `sale_time_t2` | idem |
| `sale_time_t3` | idem |
| `sale_time_t4` | idem |
| `listings_count_t1..t4` | Stock d'annonces actives à un instant T — n'existe dans aucun open data, seulement observable en scrapant les plateformes d'annonces (SeLoger, LeBonCoin, PAP...). |

**Cas particulier `rental_tension_score`** : pas listé ci-dessus car composite. Deux options :
- **Proxy Moyen** (pas de scraping) : construire un score à partir de champs déjà/bientôt dispo — `vacancy_rate` (bas = tendu), `housing_zone` DHUP (A/Abis = tendu), croissance population, ratio `owner_rate`. Approximatif mais 100% open data.
- **Version scraping (Difficile)** : score réel basé sur `listings_count` + `search_time`/`sale_time` — nécessite les 9 courbes scraping ci-dessus d'abord.

Recommandation : démarrer avec la version proxy (Moyen) tant que le scraping n'existe pas, documenter clairement la limite méthodologique dans le mémoire.

---

## 5. Sources — liens de téléchargement exacts

Liens vérifiés par recherche web (août 2026). Pour les jeux de données millésimés
(Filosofi, Carte des loyers, INSEE IRIS), le lien pointe vers le dernier millésime trouvé —
vérifier qu'une année plus récente n'a pas été publiée depuis.

### 5.1 Déjà en base (fichier présent dans le repo ou API déjà appelée)

| Source | Lien direct | Donnée fournie ici | Où dans le repo |
|---|---|---|---|
| DVF (Demandes de valeurs foncières) | https://cadastre.data.gouv.fr/dvf | 14 séries prix/surface/volume/VEFA/terrain (§1) | `dvf-raw/*.txt` |
| INSEE IRIS — "Activité des résidents en 2022" | https://www.insee.fr/fr/statistiques/8647006 | `unemployment_rate`, actifs (`ACT1564`) | `csv/base-ic-activite-residents-2022.xlsx` + historique 2017-2021 |
| INSEE IRIS — "Logement en 2022" | https://www.insee.fr/fr/statistiques/8647012 | `secondary_residence_rate`, `social_housing_rate`, `owner_rate`, `vacancy_rate` | `csv/base-ic-logement/` |
| INSEE IRIS — "évolution structure population" | https://www.insee.fr/fr/information/2383389 (portail, même famille) | `population`, `aging_index`, tranches d'âge détaillées | `csv/base-ic-evol-struct-pop/` |
| geo.api.gouv.fr | https://geo.api.gouv.fr/communes | Backbone communes (coords, code INSEE, population) | appelée par `seed_cities.py` |
| DHUP — Zonage A/Abis/B1/B2/C | https://www.data.gouv.fr/datasets/logement-liste-des-communes-selon-le-zonage-abc | `housing_zone`, `highDemandZone`, base d'`eligibleZones` | `csv/logement-liste-des-communes-selon-le-zonage-abc.csv` |
| ESR — effectifs étudiants | data.enseignementsup-recherche.gouv.fr | `studentCount` (source alternative, supplantée par INSEE RP) | `csv/fr-esr-atlas_regional-effectifs-d-etudiants-inscrits_agregeables.csv` |

### 5.2 Pas encore — à télécharger/intégrer

| Source | Lien direct | Donnée fournie ici |
|---|---|---|
| INSEE Filosofi — "Revenus, pauvreté et niveau de vie en 2021 (Iris)" | https://www.insee.fr/fr/statistiques/8229323 | `median_income` — ⚠️ 2021 = dernier millésime dispo, la version 2022 n'a pas pu être produite (qualité statistique insuffisante) |
| INSEE Sirene (API) | https://api.insee.fr (créer compte + application, doc sur portail-api.insee.fr) | `company_creations` / `annualCompanyCreations` |
| Carte des loyers (SDES/ANIL) — millésime 2025 | https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025 | `rent_sqm_t1..t4` — calculé par ANIL depuis annonces leboncoin/SeLoger/PAP |
| DGFiP — fiscalité locale des particuliers | https://www.data.gouv.fr/datasets/fiscalite-locale-des-particuliers (visu : https://data.economie.gouv.fr/explore/assets/fiscalite-locale-des-particuliers-geo/) | `avgPropertyTax` |
| SNCF — liste des gares | https://ressources.data.sncf.com/explore/dataset/liste-des-gares/ | `highSpeedRailOrAirport` (volet ferroviaire) |
| Aéroports français (coordonnées géo) | https://www.data.gouv.fr/datasets/aeroports-francais-coordonnees-geographiques | `highSpeedRailOrAirport` (volet aérien) |
| Encadrement des loyers (villes concernées) | pas de dataset téléchargeable unique trouvé — ANIL (anil.org) ou service-public.fr, arrêtés préfectoraux par ville | `rentControl` |
| Permis de louer | pas de dataset téléchargeable unique trouvé — fragmenté par arrêté municipal, à vérifier commune par commune (mairie / ANIL) | `rentalPermitRequired` |

`rentControl` et `rentalPermitRequired` : aucun jeu de données téléchargeable trouvé lors de la
recherche — confirmé fragmenté, pas d'automatisation fiable possible tant qu'aucun portail
officiel ne les centralise.

---

## 6. Annexe — champs dashboard facile/moyen (liste précédente), sources détaillées

Correspond aux champs `camelCase` évoqués plus tôt (dashboard), distincts des 39 séries `snake_case` ci-dessus.

| Champ | Catégorie | Où le trouver |
|---|---|---|
| `tenantRate` | Facile | Calcul `1 - owner_rate`, déjà en base. Pas de source externe. |
| `highDemandZone` | Facile | Calcul bool depuis `housing_zone` (DHUP, cf. §5). Pas de source externe. |
| `demographicGrowth5y` | Facile | Calcul sur série `population` déjà en base (INSEE IC évol-struct-pop, cf. §5). Pas de source externe. |
| `employmentGrowth` | Facile | Champ `ACT1564` déjà présent dans le fichier INSEE "activité résidents" (cf. §5) déjà téléchargé — juste à extraire en série annuelle. |
| `avgPropertyTax` | Facile (nouvelle source, pattern connu) | data.economie.gouv.fr / collectivites-locales.gouv.fr, cf. §5. |
| `eligibleZones` (Pinel) | Facile (nouvelle source, pattern connu) | Réutilise le zonage DHUP A/Abis/B1/B2/C (cf. §5) — Pinel suit ce même zonage. |
| `rentControl` | Facile (source fragmentée) | ANIL (anil.org) ou service-public.fr, liste des ~25 villes sous encadrement, arrêtés préfectoraux. |
| `medianAge` | Facile (nouvelle source, pattern connu) | Fichier INSEE IC "évolution structure population" déjà téléchargé (cf. §5) — tranches d'âge détaillées présentes, interpolation à coder. |
| `highSpeedRailOrAirport` | Facile (nouvelle source, pattern connu) | data.sncf.com (gares) + data.gouv.fr (aéroports DGAC), croisement géo via coords déjà en base. |
| `annualCompanyCreations` | Moyen (API à intégrer) | api.insee.fr — Sirene, cf. §5. |
| `rentalPermitRequired` | Moyen (fragmenté) | Pas de dataset national — mairie / ANIL, commune par commune. |
| `attractivenessRank` | Moyen (composite) | Pas de source externe — score à construire une fois les champs ci-dessus en base. |

---

## Résumé

| Catégorie | Nb courbes | Action |
|---|---|---|
| Acquis | 21 | rien |
| Facile | 2 | calcul, pas de nouvelle donnée |
| Moyen | 7 | 3 nouvelles sources open data (Filosofi, Sirene, Carte des loyers) + dérivés |
| Difficile (scraping) | 9 | infra scraping à construire (absente du repo actuel) |
| Composite (proxy possible) | 1 (`rental_tension_score`) | version Moyen dispo immédiatement, version fine nécessite scraping |

**Total sans scraping atteignable : 30/39** (21 acquis + 2 facile + 7 moyen). Les 9 restants
(délais + stock d'annonces) sont structurellement bloqués sans scraping — aucune agence
publique ne redistribue cette donnée en open data.

---

## Plan d'action

Ordre par dépendances : calcul pur d'abord (rien à télécharger), puis sources faciles
(pattern seed script déjà connu), puis sources moyennes (nouvelle intégration), scraping
en dernier (plus gros chantier, aucune dépendance amont).

### Phase 1 — Calcul pur, zéro téléchargement
- [x] `tenantRate` = `1 - owner_rate` → `seed_dashboard_fields.py`
- [x] `highDemandZone` = bool depuis `housing_zone` (A/A_bis) → `seed_housing_zone.py`
- [x] `demographicGrowth5y` = calcul sur série `population` existante, fenêtre 5 ans → `seed_dashboard_fields.py`
- [x] `employmentGrowth` = extraction `ACT1564` (`seed_employment_series.py`, série `active_population`) + calcul 5 ans → `seed_dashboard_fields.py`. ⚠️ ajouter `active_population` à l'enum Postgres `SerieName` avant le premier run réel.
- [ ] `rental_tension_score` (version proxy) — **hors périmètre pour l'instant** (décision explicite, pas de formule figée)

### Phase 2 — Sources faciles, pattern seed script identique à l'existant
- [ ] `eligibleZones` (Pinel) — réutiliser directement le fichier zonage DHUP déjà en base, pas de nouveau téléchargement
- [ ] `medianAge` — extraire tranches d'âge du fichier INSEE `base-ic-evol-struct-pop` déjà téléchargé, coder l'interpolation
- [ ] `avgPropertyTax` — télécharger CSV DGFiP/DGCL, écrire `seed_property_tax_series.py`
- [ ] `highSpeedRailOrAirport` — télécharger dataset gares SNCF + aéroports DGAC, croiser avec coords déjà en base via haversine existant
- [ ] `rentControl` — construire liste fermée (~25 villes) à la main via ANIL/service-public.fr, coder en dur ou petit CSV

### Phase 3 — Sources moyennes, nouvelle intégration
- [ ] Intégrer **INSEE Filosofi** → `median_income` (`seed_income_series.py`)
- [ ] Intégrer **Carte des loyers (SDES)** → `rent_sqm_t1..t4` (`seed_rent_series.py`)
- [ ] Intégrer **API Sirene** → `company_creations` / `annualCompanyCreations`
- [ ] Dérivés une fois ce qui précède en base :
  - [ ] `gross_yield_t1..t4` = `rent_sqm_tX × 12 / price_sqm_tX`
  - [ ] `years_to_buy` = `price_sqm_all × surface / median_income`
  - [ ] `housing_effort_rate` = mensualité estimée / `median_income` (figer hypothèse taux + durée crédit)
  - [ ] `attractivenessRank` = score composite final, une fois tous les champs ci-dessus dispo

### Phase 4 — Fragmenté, vérif manuelle
- [ ] `rentalPermitRequired` — recensement commune par commune (mairie/ANIL), pas d'automatisation possible

### Phase 5 — Scraping (chantier séparé, aucune dépendance amont)
- [ ] Construire infra scraping (absente du repo actuel) : choix plateformes (SeLoger/LeBonCoin/PAP), respect CGU, rate-limiting
- [ ] `listings_count_t1..t4` — stock annonces actives par typologie
- [ ] `search_time_t1..t4` — durée de vie annonce (publication → dépublication)
- [ ] `sale_time_t1..t4` — délai vente réel par typologie
- [ ] `rental_tension_score` (version fine) — remplacer le proxy Phase 1 une fois `listings_count`/`search_time`/`sale_time` dispo

Phases 1-4 = 30/39 courbes, aucun scraping. Phase 5 seule requiert l'infra scraping, pour
les 9 courbes structurellement bloquées sans elle.
