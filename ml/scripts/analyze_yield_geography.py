"""
Répond à "qu'est-ce qu'on peut déduire du rendement par commune" — analyse
descriptive du rendement observé (pas la prédiction du modèle) sur toute la
France, + cartes chaude/froide.

Pas de version "naïve" (loyer/prix nu) — retirée sur retour utilisateur : les
frais d'acquisition (notaire + travaux) sont du capital réellement dépensé
avant la mise en location, ils appartiennent au dénominateur même du "brut".
Formule brut désormais définie une seule fois, dans build_cross_sectional.py
(rendement_brut = loyer / coût_acquisition_total) — yield_model.py, ce
script et generate_recommendations.py en héritent tous, plus de calcul
dupliqué. Deux niveaux, même dénominateur (prix + notaire + travaux) :
  - rendement_brut_acquisition : loyer brut / coût d'acquisition (= rendement_reel amont)
  - rendement_net_acquisition  : (loyer - charges - taxe foncière) / coût d'acquisition

Livrables : rendement_by_department.csv, yield_map_france.png (brut),
yield_map_france_net.png (net), yield_map_brut_vs_net.png (comparatif),
yield_bubble_map.png (taille=volume de transactions), yield_bubble_map_population.png
(taille=population), yield_volume_distribution.png.

Run après ml.models.yield_model :
  python -m ml.scripts.analyze_yield_geography
Output: ml/artifacts/yield_geography/latest/
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import TwoSlopeNorm  # noqa: E402
from sklearn.neighbors import BallTree  # noqa: E402

from ml.config import ARTIFACTS_DIR  # noqa: E402
from ml.data.build_cross_sectional import build_cross_sectional  # noqa: E402
from ml.data.property_tax import load_property_tax  # noqa: E402
from ml.evaluation.artifacts import new_run_dir, publish_latest, save_dataframe, save_metadata  # noqa: E402
from ml.evaluation.plotting import save, setup_style  # noqa: E402
from ml.finance.cashflow import (  # noqa: E402
    FRAIS_NOTAIRE_ANCIEN_PCT,
    TRAVAUX_RATIO_DEFAULT,
    charges_annuelles_estimees,
    cout_acquisition_total,
    taxe_fonciere_annuelle,
)
from pipeline.services.db import get_connection  # noqa: E402

LON_RANGE = (-5.5, 10.0)
LAT_RANGE = (41.0, 51.5)
EARTH_RADIUS_KM = 6371.0

# Surface interpolée (IDW), pas un scatter — voir make_map(). k plus proches
# voisins pondérés par 1/distance², masqué au-delà de MAX_DIST_KM du point
# réel le plus proche pour ne pas "inventer" du rendement en pleine mer ou
# dans une zone sans commune échantillonnée (pas de vraie frontière France
# utilisée ici, la densité des communes DVF fait déjà office de masque).
GRID_RESOLUTION = 400
IDW_K = 10
MAX_DIST_KM = 18.0


def _latest(model: str) -> Path:
    return ARTIFACTS_DIR / model / "latest"


def load_data() -> pd.DataFrame:
    yield_pred = pd.read_csv(_latest("yield_model") / "predictions.csv", dtype={"insee_code": str})
    full = build_cross_sectional(exclude_estimated=True)
    df = yield_pred[["insee_code", "name", "department_code", "rendement_reel", "price_reel"]].merge(
        full[["population", "median_income", "transaction_volume", "rent_appt_all"]],
        left_on="insee_code", right_index=True, how="left",
    )
    tax = load_property_tax()
    df = df.merge(tax, on="insee_code", how="left")
    return df


def add_rendement_variants(df: pd.DataFrame) -> pd.DataFrame:
    """`rendement_reel` (chargé depuis yield_model.py) EST déjà le brut
    acquisition-ajusté : build_cross_sectional.py calcule rendement_brut
    comme loyer / coût_acquisition_total (prix+notaire+travaux), pas prix nu
    — source unique de la formule, voir sa docstring. Pas de recalcul ici,
    juste un alias pour la clarté des noms de colonnes dans ce script.

    Seule chose calculée ici : rendement_net_acquisition = (loyer - charges -
    taxe foncière) / MÊME coût d'acquisition — les charges d'exploitation
    récurrentes (après mise en location) sont ce qui distingue net de brut,
    pas les frais d'acquisition (déjà dans le brut, voir EXPERIMENTS_LOG.md
    "Modèle rendement" #6)."""
    df = df.copy()
    df["rendement_brut_acquisition"] = df["rendement_reel"]

    loyer_annuel_m2 = df["rent_appt_all"] * 12
    charges_m2 = charges_annuelles_estimees(loyer_annuel_m2)
    taux_foncier = df["taux_foncier_pct"].fillna(df["taux_foncier_pct"].median())
    taxe_m2 = taxe_fonciere_annuelle(loyer_annuel_m2, taux_foncier)
    cout_total_m2 = cout_acquisition_total(df["price_reel"], FRAIS_NOTAIRE_ANCIEN_PCT, TRAVAUX_RATIO_DEFAULT)

    # rendement_net() (ml/finance/cashflow.py) a une garde scalaire (if cout_acquisition
    # else 0.0) qui casse sur une Series pandas -> même formule (§6) réécrite en vectorisé.
    df["rendement_net_acquisition"] = (loyer_annuel_m2 - charges_m2 - taxe_m2) / cout_total_m2 * 100
    return df


def fetch_coords(insee_codes: list[str]) -> pd.DataFrame:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT insee_code, latitude, longitude FROM cities WHERE insee_code = ANY(%s)",
            (insee_codes,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return pd.DataFrame(rows, columns=["insee_code", "latitude", "longitude"])


def _mainland(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["longitude"].between(*LON_RANGE) & df["latitude"].between(*LAT_RANGE)]


def analyze(df: pd.DataFrame) -> dict:
    corr = df[["rendement_brut_acquisition", "price_reel", "population", "median_income"]].corr()["rendement_brut_acquisition"]

    by_dept = (
        df.groupby("department_code")["rendement_brut_acquisition"]
        .agg(["mean", "median", "count"])
        .query("count >= 10")
        .sort_values("mean", ascending=False)
    )

    weighted_mean = np.average(
        df["rendement_brut_acquisition"], weights=df["population"].fillna(df["population"].median())
    )
    tx_vol = df["transaction_volume"].fillna(0).clip(lower=0.001)
    tx_weighted_mean = np.average(df["rendement_brut_acquisition"], weights=tx_vol)

    brut_acq = df["rendement_brut_acquisition"].dropna()
    net = df["rendement_net_acquisition"].dropna()

    return {
        "corr_price": round(corr["price_reel"], 3),
        "corr_population": round(corr["population"], 3),
        "corr_median_income": round(corr["median_income"], 3),
        "national_mean_pop_weighted": round(weighted_mean, 2),
        "national_mean_tx_weighted": round(tx_weighted_mean, 2),
        "national_median_brut_acquisition": round(brut_acq.median(), 2),
        "national_mean_brut_acquisition": round(brut_acq.mean(), 2),
        "national_median_net_acquisition": round(net.median(), 2),
        "national_mean_net_acquisition": round(net.mean(), 2),
        "pct_communes_net_below_3pct": round((net < 3).mean() * 100, 1),
        "top5_depts": by_dept.head(5)["mean"].round(2).to_dict(),
        "bottom5_depts": by_dept.tail(5)["mean"].round(2).to_dict(),
        "by_dept_table": by_dept,
    }


def _idw_grid(
    geo: pd.DataFrame, value_col: str,
    lon_range: tuple[float, float] = LON_RANGE, lat_range: tuple[float, float] = LAT_RANGE,
    grid_resolution: int = GRID_RESOLUTION, idw_k: int = IDW_K, max_dist_km: float = MAX_DIST_KM,
) -> tuple[np.ndarray, np.ndarray, np.ma.MaskedArray]:
    """Inverse-distance-weighted interpolation of value_col onto a regular
    lon/lat grid, masked wherever the nearest real commune is farther than
    max_dist_km (so the surface doesn't invent values over the sea/abroad —
    density of real points substitutes for an actual France border polygon).
    Defaults are national; pass a tighter lon/lat_range + smaller max_dist_km
    for a regional zoom (see make_regional_map)."""
    lon_grid = np.linspace(*lon_range, grid_resolution)
    lat_grid = np.linspace(*lat_range, grid_resolution)
    lon_mesh, lat_mesh = np.meshgrid(lon_grid, lat_grid)

    points_rad = np.radians(geo[["latitude", "longitude"]].values)
    grid_rad = np.radians(np.column_stack([lat_mesh.ravel(), lon_mesh.ravel()]))

    tree = BallTree(points_rad, metric="haversine")
    k = min(idw_k, len(geo))
    dist, idx = tree.query(grid_rad, k=k)
    dist_km = dist * EARTH_RADIUS_KM

    values = geo[value_col].values[idx]
    weights = 1.0 / np.clip(dist_km, 0.05, None) ** 2
    interpolated = (values * weights).sum(axis=1) / weights.sum(axis=1)

    nearest_km = dist_km[:, 0]
    mask = nearest_km > max_dist_km
    grid_values = np.ma.masked_array(interpolated.reshape(lon_mesh.shape), mask=mask.reshape(lon_mesh.shape))
    return lon_mesh, lat_mesh, grid_values


def make_map(df: pd.DataFrame, value_col: str, national_median: float, run_dir: Path,
             filename: str, title: str, cbar_label: str) -> None:
    geo = _mainland(df.dropna(subset=["latitude", "longitude", value_col]))
    lon_mesh, lat_mesh, grid_values = _idw_grid(geo, value_col)

    p2, p98 = geo[value_col].quantile([0.02, 0.98])
    norm = TwoSlopeNorm(vmin=p2, vcenter=national_median, vmax=p98)

    fig, ax = plt.subplots(figsize=(8, 7.5))
    cmap = plt.get_cmap("RdBu_r").copy()
    mesh = ax.pcolormesh(lon_mesh, lat_mesh, grid_values, cmap=cmap, norm=norm, shading="gouraud")
    cbar = fig.colorbar(mesh, ax=ax, shrink=0.7)
    cbar.set_label(cbar_label, fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_aspect("equal")
    save(fig, run_dir / filename)


def make_comparison_map(df: pd.DataFrame, median_brut_acq: float, median_net: float, run_dir: Path) -> None:
    """2 tiers, MÊME échelle de couleur, MÊME dénominateur (prix + notaire +
    travaux) : brut (loyer non déduit des charges) vs net (loyer net des
    charges d'exploitation récurrentes + taxe foncière). Pas de version
    "naïve" (loyer/prix nu) — retirée, cf. retour utilisateur : les frais
    d'acquisition sont du capital réellement investi avant la mise en
    location, ils appartiennent au dénominateur même du "brut"."""
    geo = _mainland(df.dropna(subset=["latitude", "longitude", "rendement_brut_acquisition", "rendement_net_acquisition"]))
    p2, p98 = geo["rendement_brut_acquisition"].quantile([0.02, 0.98])
    norm = TwoSlopeNorm(vmin=p2, vcenter=median_brut_acq, vmax=p98)
    cmap = plt.get_cmap("RdBu_r").copy()

    fig, (ax0, ax1, cax) = plt.subplots(
        1, 3, figsize=(14.5, 7.3), gridspec_kw={"width_ratios": [1, 1, 0.05], "wspace": 0.15}
    )
    panels = [
        (ax0, "rendement_brut_acquisition", f"Brut / coût d'acquisition\n(prix+notaire+travaux, médiane {median_brut_acq:.1f}%)"),
        (ax1, "rendement_net_acquisition", f"Net / coût d'acquisition\n(+ charges + taxe foncière, médiane {median_net:.1f}%)"),
    ]
    for ax, col, label in panels:
        lon_mesh, lat_mesh, grid_values = _idw_grid(geo, col)
        mesh = ax.pcolormesh(lon_mesh, lat_mesh, grid_values, cmap=cmap, norm=norm, shading="gouraud")
        ax.set_title(label, fontsize=10.5)
        ax.set_aspect("equal")
        ax.set_xlabel("Longitude")
    ax0.set_ylabel("Latitude")
    cbar = fig.colorbar(mesh, cax=cax)
    cbar.set_label("Rendement %  (même échelle sur les 2 cartes)", fontsize=9)
    fig.suptitle(
        "Rendement brut vs net, même coût d'acquisition réel (prix + notaire + travaux)\n"
        "Seule différence : le net déduit charges d'exploitation + taxe foncière du loyer",
        fontsize=11.5, fontweight="bold",
    )
    save(fig, run_dir / "yield_map_brut_vs_net.png")


def make_bubble_map(df: pd.DataFrame, national_median: float, run_dir: Path,
                     size_col: str, size_labels: list[tuple[float, str]],
                     legend_title: str, filename: str, title: str) -> None:
    """Même géographie que make_map(), mais taille de bulle = size_col.
    Deux usages différents selon size_col :
      - transaction_volume : où ça s'ÉCHANGE (activité du marché, où les
        investisseurs achètent en pratique) — yield_bubble_map.png
      - population : où sont les GENS (poids réel du parc logement/marché
        locatif potentiel, indépendant de l'activité transactionnelle) —
        yield_bubble_map_population.png. Une grosse zone rouge sur la carte
        interpolée peut être une petite commune (peu d'habitants) ET peu
        active (peu de transactions) — les deux vues sont complémentaires,
        pas interchangeables."""
    geo = _mainland(df.dropna(subset=["latitude", "longitude", "rendement_brut_acquisition", size_col]))
    geo = geo[geo[size_col] > 0].sort_values(size_col)  # gros points dessinés en dernier

    p2, p98 = geo["rendement_brut_acquisition"].quantile([0.02, 0.98])
    vals = geo["rendement_brut_acquisition"].clip(p2, p98)
    norm = TwoSlopeNorm(vmin=p2, vcenter=national_median, vmax=p98)

    # Exposant 0.75 (entre sqrt=0.5 et linéaire=1) : contraste volontairement
    # exagéré ("plus choquant" — retour utilisateur) entre grosses et petites
    # bulles, tout en gardant les petites communes visibles (pas juste un point).
    size_max = geo[size_col].max()
    size = 8 + 900 * (geo[size_col] / size_max) ** 0.75

    fig, ax = plt.subplots(figsize=(8, 7.8))
    sc = ax.scatter(geo["longitude"], geo["latitude"], c=vals, cmap="RdBu_r", norm=norm,
                     s=size, alpha=0.65, linewidths=0.4, edgecolors="white")
    cbar = fig.colorbar(sc, ax=ax, shrink=0.65)
    cbar.set_label(f"Rendement brut % (médiane {national_median:.1f}%)", fontsize=9)

    for val, label in size_labels:
        s = 8 + 900 * (val / size_max) ** 0.75
        ax.scatter([], [], s=s, facecolor="#898781", edgecolor="white", linewidths=0.4, label=label)
    ax.legend(title=legend_title, loc="lower left", fontsize=8, title_fontsize=8.5,
              labelspacing=1.3, borderpad=1)

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(title, fontsize=11, linespacing=1.8)
    ax.set_aspect("equal")
    save(fig, run_dir / filename)


RENDEMENT_BUCKETS = [0, 5, 6, 7, 8, 9, 100]
RENDEMENT_LABELS = ["<5%", "5-6%", "6-7%", "7-8%", "8-9%", "9%+"]


def make_volume_distribution(df: pd.DataFrame, run_dir: Path) -> pd.DataFrame:
    """Quantifie l'illusion visuelle de la carte : répartition des COMMUNES
    par tranche de rendement vs répartition du VOLUME DE MARCHÉ (transactions
    DVF) par tranche. Si le marché réel est concentré dans les tranches
    basses alors que les communes sont majoritairement dans les tranches
    hautes, la carte surreprésente le rendement élevé."""
    d = df.dropna(subset=["rendement_brut_acquisition", "transaction_volume"]).copy()
    d["bucket"] = pd.cut(d["rendement_brut_acquisition"], RENDEMENT_BUCKETS, labels=RENDEMENT_LABELS, right=False)

    communes_pct = d["bucket"].value_counts(normalize=True).reindex(RENDEMENT_LABELS) * 100
    volume_pct = d.groupby("bucket", observed=True)["transaction_volume"].sum()
    volume_pct = (volume_pct / volume_pct.sum() * 100).reindex(RENDEMENT_LABELS)

    fig, ax = plt.subplots(figsize=(8, 5.5))
    x = np.arange(len(RENDEMENT_LABELS))
    width = 0.36
    ax.bar(x - width / 2, communes_pct, width, label="% des communes (ce que montre la carte)", color="#eb6834")
    ax.bar(x + width / 2, volume_pct, width, label="% du volume de marché (vraies transactions)", color="#2a78d6")
    ax.set_xticks(x)
    ax.set_xticklabels(RENDEMENT_LABELS)
    ax.set_ylabel("% du total")
    ax.set_title("Répartition par tranche de rendement — communes vs marché réel")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(True, axis="y")
    save(fig, run_dir / "yield_volume_distribution.png")

    return pd.DataFrame({"pct_communes": communes_pct, "pct_volume_marche": volume_pct})


def run(dry_run: bool = False) -> Path:
    print("Loading rendement_reel + socio-demo (population, median_income) + taxe foncière...")
    df = load_data()
    df = add_rendement_variants(df)
    print(f"  {len(df)} communes")

    print("Fetching lat/lon...")
    coords = fetch_coords(df["insee_code"].tolist())
    df = df.merge(coords, on="insee_code", how="left")

    stats = analyze(df)
    print(f"\n1) Brut / coût d'acquisition (notaire {FRAIS_NOTAIRE_ANCIEN_PCT*100:.0f}% + travaux "
          f"{TRAVAUX_RATIO_DEFAULT*100:.0f}%): médiane {stats['national_median_brut_acquisition']}% "
          f"(moyenne {stats['national_mean_brut_acquisition']}%, pondérée population "
          f"{stats['national_mean_pop_weighted']}%, pondérée volume de transactions {stats['national_mean_tx_weighted']}%)")
    print(f"2) Net / coût d'acquisition (+ charges + taxe foncière réelle): médiane "
          f"{stats['national_median_net_acquisition']}% (moyenne {stats['national_mean_net_acquisition']}%)")
    print(f"  -> {stats['pct_communes_net_below_3pct']}% des communes tombent sous 3% net d'acquisition")
    print(f"Corrélation avec prix/m²: {stats['corr_price']} | population: {stats['corr_population']} | "
          f"revenu médian: {stats['corr_median_income']}")
    print("\nTop 5 départements (rendement moyen):")
    print(stats["by_dept_table"].head(5).round(2))
    print("\nBottom 5 départements (rendement moyen):")
    print(stats["by_dept_table"].tail(5).round(2))

    if dry_run:
        print("[DRY RUN] Not writing artifacts.")
        return Path()

    run_dir = new_run_dir("yield_geography")
    setup_style()

    save_dataframe(run_dir, stats["by_dept_table"], "rendement_by_department.csv")
    make_map(df, "rendement_brut_acquisition", stats["national_median_brut_acquisition"], run_dir,
             filename="yield_map_france.png",
             title="Rendement brut / coût d'acquisition réel — surface interpolée (France métropolitaine)\n"
                    "Bleu = froid (rendement bas)  •  Rouge = chaud (rendement haut)",
             cbar_label=f"Rendement brut % (médiane {stats['national_median_brut_acquisition']:.1f}%)")
    make_map(df, "rendement_net_acquisition", stats["national_median_net_acquisition"], run_dir,
             filename="yield_map_france_net.png",
             title="Rendement NET / coût d'acquisition réel (notaire+travaux+charges+taxe foncière)\n"
                    "Bleu = froid (rendement bas)  •  Rouge = chaud (rendement haut)",
             cbar_label=f"Rendement net d'acquisition % (médiane {stats['national_median_net_acquisition']:.1f}%)")
    make_comparison_map(df, stats["national_median_brut_acquisition"],
                         stats["national_median_net_acquisition"], run_dir)
    make_bubble_map(
        df, stats["national_median_brut_acquisition"], run_dir,
        size_col="transaction_volume",
        size_labels=[(10, "10 tx"), (100, "100 tx"), (1000, "1000 tx")],
        legend_title="Volume de marché\n(transactions DVF)",
        filename="yield_bubble_map.png",
        title="Rendement brut par commune — taille = volume de marché (où ça s'échange)\n"
              "Grosse bulle = beaucoup de transactions, petite bulle = marché anecdotique",
    )
    make_bubble_map(
        df, stats["national_median_brut_acquisition"], run_dir,
        size_col="population",
        size_labels=[(1_000, "1 000 hab."), (20_000, "20 000 hab."), (200_000, "200 000 hab.")],
        legend_title="Population\n(habitants)",
        filename="yield_bubble_map_population.png",
        title="Rendement brut par commune — taille = population (où sont les gens)\n"
              "Grosse bulle = ville peuplée, petite bulle = commune peu peuplée",
    )
    distribution = make_volume_distribution(df, run_dir)
    save_dataframe(run_dir, distribution, "volume_distribution.csv")
    print("\nRépartition communes vs volume de marché par tranche de rendement:")
    print(distribution.round(1))

    save_metadata(run_dir, {
        "note": "Analyse descriptive du rendement observé (pas la prédiction du modèle) — coût d'acquisition réel, pas de version naïve",
        "n_communes": len(df),
        "national_mean_pop_weighted_pct": stats["national_mean_pop_weighted"],
        "national_mean_tx_weighted_pct": stats["national_mean_tx_weighted"],
        "national_median_brut_acquisition_pct": stats["national_median_brut_acquisition"],
        "national_mean_brut_acquisition_pct": stats["national_mean_brut_acquisition"],
        "national_median_net_acquisition_pct": stats["national_median_net_acquisition"],
        "national_mean_net_acquisition_pct": stats["national_mean_net_acquisition"],
        "pct_communes_net_below_3pct": stats["pct_communes_net_below_3pct"],
        "acquisition_cost_assumptions": {
            "frais_notaire_pct": FRAIS_NOTAIRE_ANCIEN_PCT,
            "travaux_ratio": f"{TRAVAUX_RATIO_DEFAULT} — convention 10% du prix (investisseurs locatifs, ancien), PAS une mesure",
            "charges_ratio": "10% du loyer annuel (hypothèse, voir ml/finance/cashflow.py)",
            "taxe_fonciere": "DGFiP réelle par commune (ml/data/property_tax.py)",
        },
        "correlations": {
            "price_reel": stats["corr_price"],
            "population": stats["corr_population"],
            "median_income": stats["corr_median_income"],
        },
        "top5_depts": stats["top5_depts"],
        "bottom5_depts": stats["bottom5_depts"],
    })

    latest = publish_latest("yield_geography", run_dir)
    print(f"\nDone. Artifacts written to {run_dir} (mirrored to {latest})")
    return run_dir


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(dry_run=args.dry_run)
