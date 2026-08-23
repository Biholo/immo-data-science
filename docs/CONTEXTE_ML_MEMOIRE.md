# Contexte ML / Data pour Rentium et ImmoTrust

## 1. Modèles à développer

Pour Rentium, rester sur **3 modèles ML principaux** est suffisant pour le mémoire et plus crédible que d'empiler les modèles.

| Modèle | Objectif | Priorité |
|---|---|---|
| **K-Means clustering** | Segmenter les communes en typologies de marché | Obligatoire |
| **Gradient Boosting prix attendu** | Estimer le prix/m² cohérent avec les caractéristiques d'une commune | Obligatoire |
| **Gradient Boosting forecasting** | Prévoir le prix/m² à t+1 et éventuellement t+4 | Obligatoire |
| **Régression quantile** | Fournir une fourchette d'incertitude | Fortement recommandé |
| **Loyer au m²** | Prédire le loyer | Pas maintenant |

Ne pas développer le modèle de loyer tant que la donnée locative n'est pas suffisamment propre.

Pour ImmoTrust, **aucun modèle à entraîner**. L'approche repose sur :

- Claude pré-entraîné ;
- prompt engineering ;
- règles métier ;
- structured output ;
- orchestration multi-documents.

---

# 2. Modèle 1 : clustering des communes

## Objectif

Segmenter les communes françaises en typologies de marchés comparables afin de pouvoir raisonner sur des profils de territoires plutôt que uniquement ville par ville.

## Inputs

Features minimales :

- `population`
- `unemployment_rate`
- `owner_rate`
- `vacancy_rate`
- `social_housing_rate`
- `aging_index`
- `secondary_residence_rate`
- `transaction_volume`
- `price_growth_3y`

Features optionnelles :

- densité de POI ;
- accessibilité aux transports ;
- zonage, avec prudence car catégoriel.

Ne pas intégrer directement `price_sqm` si le clustering sert ensuite à contextualiser le score de prix, afin d'éviter un raisonnement circulaire.

## Prétraitement indispensable

K-Means étant sensible aux échelles, standardiser les variables :

\[
z_i = \frac{x_i - \mu}{\sigma}
\]

avec :

- \(x_i\) : valeur originale ;
- \(\mu\) : moyenne de la variable ;
- \(\sigma\) : écart-type.

## Formule du modèle

Objectif K-Means :

\[
\min_{C_1,\dots,C_K}
\sum_{k=1}^{K}
\sum_{x_i \in C_k}
\|x_i-\mu_k\|^2
\]

avec :

- \(K\) : nombre de clusters ;
- \(\mu_k\) : centroïde du cluster \(k\).

## Métrique principale

Coefficient de silhouette :

\[
s(i)=
\frac{b(i)-a(i)}
{\max(a(i),b(i))}
\]

avec :

- \(a(i)\) : distance moyenne entre l'observation \(i\) et les autres points de son cluster ;
- \(b(i)\) : plus petite distance moyenne entre \(i\) et les points d'un autre cluster.

## Résultats à conserver

Pour pouvoir documenter le modèle dans le mémoire, produire et sauvegarder :

- nombre de communes utilisées ;
- liste finale des features ;
- nombre de clusters testés ;
- score de silhouette pour chaque valeur de \(K\) ;
- \(K\) retenu ;
- taille de chaque cluster ;
- moyenne et médiane de chaque variable par cluster ;
- 5 à 10 communes représentatives par cluster ;
- labels métier attribués aux clusters.

---

# 3. Modèle 2 : Gradient Boosting pour le prix attendu

## Objectif

Estimer le prix au mètre carré attendu d'une commune à partir de ses caractéristiques socio-économiques et territoriales, puis comparer cette estimation au prix réellement observé.

Ce modèle sert directement à produire un **signal d'opportunité**.

## Target

\[
y = Prix/m^2
\]

Deux options possibles selon le dataset final :

- dernière observation fiable par commune ;
- observation `commune × trimestre`.

## Features

Base recommandée :

- population ;
- chômage ;
- taux de vacance ;
- taux de propriétaires ;
- part de logements sociaux ;
- indice de vieillissement ;
- part de résidences secondaires ;
- zonage ;
- volume de transactions ;
- éventuellement POI et accessibilité.

Attention à `price_growth_3y` : ne l'utiliser que si sa construction n'introduit ni fuite temporelle ni relation trop directe avec la cible.

## Modèle

Formulation simplifiée du Gradient Boosting :

\[
F_M(x)=F_0(x)+
\sum_{m=1}^{M}\eta h_m(x)
\]

avec :

- \(F_0\) : prédiction initiale ;
- \(h_m\) : arbre ajouté à l'itération \(m\) ;
- \(\eta\) : taux d'apprentissage ;
- \(M\) : nombre d'arbres.

Implémentations possibles :

- XGBoost ;
- LightGBM ;
- `GradientBoostingRegressor` de scikit-learn.

## Score d'opportunité

Le score d'opportunité correspond au résidu normalisé :

\[
Score_{opp}
=
\frac{P_{observé}-P_{prédit}}
{P_{prédit}}
\]

Interprétation :

- `score < 0` : prix observé inférieur au prix attendu ;
- `score ≈ 0` : prix cohérent avec les caractéristiques ;
- `score > 0` : prix observé supérieur au prix attendu.

Exemple :

- prix attendu : 3 000 €/m² ;
- prix observé : 2 700 €/m².

\[
\frac{2700-3000}{3000}=-0.10=-10\%
\]

Le territoire se situe donc **10 % sous le niveau prédit par ses caractéristiques**.

Important dans le mémoire : parler de **signal de sous-évaluation**, jamais de preuve qu'un marché est réellement sous-évalué.

## Métriques obligatoires

### MAE

\[
MAE=
\frac{1}{n}
\sum_{i=1}^{n}
|y_i-\hat y_i|
\]

### RMSE

\[
RMSE=
\sqrt{
\frac{1}{n}
\sum_{i=1}^{n}
(y_i-\hat y_i)^2
}
\]

### R² optionnel

\[
R^2=
1-
\frac{
\sum_{i=1}^{n}(y_i-\hat y_i)^2
}{
\sum_{i=1}^{n}(y_i-\bar y)^2
}
\]

MAE + RMSE suffisent si l'objectif est de limiter l'accumulation de métriques.

## Baseline indispensable

Comparer le modèle ML à une prédiction simple.

Exemples :

- médiane du département ;
- médiane nationale ;
- moyenne nationale.

Le modèle n'a d'intérêt que s'il apporte un gain mesurable par rapport à cette baseline.

## Résultats à conserver

- dataset final ;
- nombre de communes d'entraînement ;
- nombre d'observations ;
- features exactes ;
- traitement des valeurs manquantes ;
- traitement de `price_data_source = estimated` ;
- modèle exact utilisé ;
- hyperparamètres ;
- protocole train / validation / test ;
- MAE baseline ;
- MAE modèle ;
- RMSE baseline ;
- RMSE modèle ;
- éventuellement R² ;
- feature importance ;
- idéalement valeurs SHAP ;
- 10 exemples de communes avec :
  - prix réel ;
  - prix prédit ;
  - score d'opportunité ;
  - cluster.

---

# 4. Modèle 3 : forecasting des prix

## Objectif

Prévoir l'évolution du prix au mètre carré à court terme à l'échelle communale.

L'historique 2021-2025 étant relativement court, privilégier un **Gradient Boosting mutualisé entre les communes** plutôt qu'un LSTM ou un modèle profond entraîné séparément sur chaque ville.

## Structure

Pour une commune \(c\) au trimestre \(t\) :

\[
\hat P_{c,t+1}
=
f(
P_{c,t},
P_{c,t-1},
P_{c,t-2},
P_{c,t-4},
X_{c,t}
)
\]

avec :

- \(P_{c,t}\) : prix courant ;
- \(P_{c,t-1}\), \(P_{c,t-2}\), \(P_{c,t-4}\) : valeurs retardées ;
- \(X_{c,t}\) : caractéristiques territoriales disponibles au trimestre \(t\).

Lags recommandés :

- `t-1`
- `t-2`
- `t-4`

## Croissance prévue

Pour une prévision à quatre trimestres :

\[
Growth_{1Y}
=
\frac{\hat P_{t+4}-P_t}
{P_t}
\times 100
\]

Exemple de restitution :

> Hausse prévue à 1 an : +3,8 %

Cette information est plus directement exploitable par l'utilisateur qu'un prix futur présenté seul.

## Validation

Ne pas utiliser de split aléatoire.

Exemple :

- entraînement : 2021 à 2024 ;
- validation / test : 2025.

Le protocole doit toujours respecter l'ordre chronologique.

## Baseline

Baseline naïve :

\[
\hat P_{t+1}=P_t
\]

Hypothèse :

> le prix du trimestre suivant est égal au dernier prix observé.

Le modèle doit faire mieux que cette baseline pour être pertinent.

## Métriques

Utiliser au minimum :

- MAE ;
- RMSE.

Optionnellement :

- MAPE si les conditions d'utilisation sont adaptées ;
- R² ;
- erreur par horizon de prévision.

## Résultats à conserver

- nombre total d'observations `commune × trimestre` ;
- périodes disponibles ;
- lags utilisés ;
- features supplémentaires ;
- protocole temporel ;
- MAE baseline ;
- MAE modèle ;
- RMSE baseline ;
- RMSE modèle ;
- résultats à `t+1` ;
- résultats à `t+4` si développés ;
- 5 à 10 communes témoins avec :
  - historique réel ;
  - prédiction ;
  - erreur ;
  - croissance prévue.

---

# 5. Modèle 4 optionnel : régression quantile

## Objectif

Fournir une fourchette d'incertitude plutôt qu'une prédiction unique.

Cette approche est fortement recommandée car elle soutient directement le principe :

> **une prédiction n'est pas une vérité.**

Au lieu de produire uniquement :

\[
\hat y
\]

le modèle peut générer :

\[
Q_{0.1}(y|x),
\quad
Q_{0.5}(y|x),
\quad
Q_{0.9}(y|x)
\]

Exemple :

- prix central : 3 000 €/m² ;
- borne basse : 2 700 €/m² ;
- borne haute : 3 350 €/m².

## Fonction Pinball

\[
L_{\tau}(y,\hat y)
=
\tau\max(0,y-\hat y)
+
(1-\tau)\max(0,\hat y-y)
\]

avec :

- \(\tau=0.1\) : borne basse ;
- \(\tau=0.5\) : médiane ;
- \(\tau=0.9\) : borne haute.

Cette formule est particulièrement intéressante à intégrer au mémoire pour montrer comment l'incertitude peut être directement modélisée.

---

# 6. Fonctions qui doivent rester déterministes

Certaines briques de Rentium ne doivent pas être apprises par un modèle ML.

Lorsque la formule métier est connue, elle doit être calculée directement.

## Mensualité d'un prêt amortissable

\[
M=
C
\frac{i(1+i)^n}
{(1+i)^n-1}
\]

avec :

- \(C\) : capital emprunté ;
- \(i\) : taux mensuel ;
- \(n\) : nombre de mensualités.

## Rendement brut

\[
R_{brut}
=
\frac{Loyers_{annuels}}
{Coût_{acquisition}}
\times 100
\]

## Rendement net

\[
R_{net}
=
\frac{
Loyers_{annuels}-Charges_{annuelles}
}
{Coût_{acquisition}}
\times 100
\]

## Rendement net-net

Formulation conceptuelle :

\[
R_{net-net}
=
\frac{
Loyers-Charges-Fiscalité
}
{Coût_{acquisition}}
\times 100
\]

La fiscalité exacte dépend du régime fiscal implémenté.

## Loyer corrigé de la vacance

\[
Loyer_{effectif}
=
Loyer_{théorique}
\times
(1-Taux_{vacance})
\]

## Cash-flow mensuel

\[
CF_m=
Loyer_{net,m}
-
Mensualité
-
Charges_m
-
Fiscalité_m
\]

Ces calculs doivent rester déterministes et testables.

---

# 7. Score de comparaison Rentium

## Objectif

Produire un classement adapté au profil de l'investisseur en combinant plusieurs indicateurs de marché et financiers.

Le scoring doit rester explicable et documenté.

Exemples de composantes actuellement envisagées :

### Sécurité

\[
Sécurité
=
TensionLocative
+
\frac{\max(0,EvolutionPrix3ans)}{5}
-
\frac{DélaiRelocation}{10}
\]

### Localisation

\[
Localisation
=
\frac{WalkScore}{10}
+
Transports
+
\frac{Commerces}{4}
\]

Le score final dépend ensuite des pondérations associées au profil investisseur.

## À conserver pour le mémoire

- formule finale exacte ;
- poids par profil ;
- justification métier des poids ;
- cas de test ;
- exemples de changements de classement selon le profil.

Les pondérations doivent être présentées comme **heuristiques** si elles ne sont pas apprises statistiquement.

---

# 8. ImmoTrust : benchmark à développer

Aucun nouveau modèle ML n'est nécessaire.

ImmoTrust repose sur :

- LLM pré-entraîné ;
- prompt engineering ;
- structured outputs ;
- règles métier ;
- orchestration ;
- garde-fous applicatifs.

L'enjeu principal pour le mémoire est donc **l'évaluation du pipeline**.

## Jeu de test recommandé

Préparer idéalement entre **30 et 50 documents**.

Répartition possible :

- DPE ;
- PV d'AG ;
- états datés ;
- devis ;
- documents génériques.

Pour chaque document, constituer une vérité terrain :

```yaml
type_document: ...
champs_attendus:
  ...
anomalies_attendues:
  ...
niveau_anomalie:
  ...
```

## Classification documentaire

### Accuracy

\[
Accuracy=
\frac{
Nombre\ de\ prédictions\ correctes
}{
Nombre\ total\ de\ prédictions
}
\]

## Détection des anomalies

### Precision

\[
Precision=
\frac{TP}{TP+FP}
\]

### Recall

\[
Recall=
\frac{TP}{TP+FN}
\]

### F1

\[
F1=
2
\frac{
Precision\times Recall
}{
Precision+Recall
}
\]

avec :

- `TP` : vrai positif ;
- `FP` : faux positif ;
- `FN` : faux négatif.

## Autres mesures recommandées

Mesurer également :

- temps moyen d'analyse ;
- coût moyen par document ;
- stabilité sur 3 exécutions du même document ;
- taux de JSON invalide ;
- éventuellement comparaison Haiku vs Sonnet ;
- taux d'extraction correcte des champs structurés.

---

# 9. Ordre recommandé de développement

1. **Clustering K-Means**
2. **Gradient Boosting prix/m²**
3. **Score d'opportunité**
4. **Forecasting Gradient Boosting avec lags**
5. **Régression quantile** si le temps le permet
6. **Baselines + MAE / RMSE**
7. **Feature importance / SHAP**
8. **Benchmark ImmoTrust**
9. **Conservation des résultats expérimentaux et artefacts pour le mémoire**

---

# 10. Artefacts à sauvegarder pour la rédaction du mémoire

Pour chaque entraînement ou benchmark, sauvegarder systématiquement :

- date du run ;
- version du dataset ;
- version du code / commit Git ;
- features utilisées ;
- hyperparamètres ;
- taille train / validation / test ;
- métriques ;
- temps d'entraînement ;
- modèle sérialisé ;
- CSV ou JSON des prédictions ;
- graphiques générés ;
- erreurs remarquables ;
- feature importance / SHAP ;
- exemples représentatifs ;
- limites identifiées.

L'objectif est de pouvoir reconstruire précisément les résultats présentés dans le mémoire sans dépendre d'informations mémorisées ou de captures isolées.
