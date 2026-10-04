"""
ONE command to load / refresh the whole Rentium DB from immo-data-science.

  python -m pipeline.scripts.seed_all                       # France entière, tout (DVF, transactions, INSEE, POI...)
  python -m pipeline.scripts.seed_all --dept 77             # un département (dev / test)
  python -m pipeline.scripts.seed_all --list                # liste les étapes et leur état (entrées présentes ?)
  python -m pipeline.scripts.seed_all --dry-run             # affiche les commandes sans rien exécuter
  python -m pipeline.scripts.seed_all --only seed_poi       # une ou plusieurs étapes (séparées par des virgules)
  python -m pipeline.scripts.seed_all --from seed_housing_zone   # reprend à partir d'une étape (après un crash)
  python -m pipeline.scripts.seed_all --skip-dvf --skip-transactions --skip-poi   # que les données INSEE/DGFiP

Options de contenu :
  --skip-dvf            saute run_dvf (séries DVF département/région/pays + villes)
  --skip-transactions   saute seed_transactions + tout ce qui en dérive (prix de vente, fourchettes, volumes par typologie, IDW des fourchettes)
  --skip-poi            saute seed_poi (OpenStreetMap)
  --skip a,b            saute des étapes précises (voir --list)
  --refresh-permit      relance scrape_rental_permit (réseau) avant seed_rent_regulation
  --refresh-poi         re-télécharge le .osm.pbf même s'il existe déjà (à faire ~1x/mois)
  --poi-region NAME     extrait Geofabrik : france (défaut, ~4 Go) | ile-de-france | bretagne | ...
  --poi-pbf PATH        utilise ce .osm.pbf tel quel (aucun téléchargement)
  --dvf-raw-dir DIR     dossier des ValeursFoncieres-*.txt (défaut dvf-raw/ ; sinon variable DVF_RAW_DIR)
  --continue-on-error   ne s'arrête pas au 1er échec d'une étape non "soft"

Comportement :
  - chaque étape tourne dans un sous-processus `python -m pipeline.scripts.<étape>` (mêmes commandes
    que dans le README : on peut toujours relancer une étape seule)
  - une étape dont les fichiers source sont absents est SAUTÉE avec la raison (pas d'échec)
  - étapes "soft" (réseau / fichier jetable) : un échec n'arrête pas la suite
  - récapitulatif final (statut + durée par étape) ; code retour 1 si une étape non soft a échoué

Prérequis : `.env` avec DATABASE_URL, `pip install -r requirements.txt`, migrations Prisma appliquées
côté rentium/backend. Détail : docs/COMMANDES.md.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

GEOFABRIK_FRANCE = "https://download.geofabrik.de/europe/france-latest.osm.pbf"
GEOFABRIK_REGION = "https://download.geofabrik.de/europe/france/{region}-latest.osm.pbf"
POI_DIR = REPO_ROOT / "csv" / "poi"


@dataclass
class Step:
    name: str                                   # == module name in pipeline.scripts (used by --only/--from/--skip)
    label: str
    args: list[str] = field(default_factory=list)
    dept: bool = True                           # script accepts --dept
    soft: bool = False                          # failure does not stop the run
    requires: list[str] = field(default_factory=list)   # glob patterns (from repo root); step skipped if none match
    group: str = ""
    optional: bool = False                      # only run when explicitly enabled (e.g. --refresh-permit)
    module: str | None = None                   # defaults to name


def _steps() -> list[Step]:
    S = Step
    return [
        # 1. Base géographique
        S("seed_cities", "Communes + zones administratives (geo.api.gouv.fr)", group="1. Base géographique"),
        S("seed_rp", "INSEE RP 2022 : student_count", soft=True, group="1. Base géographique",
          requires=["csv/base-ic-activite-residents-2022.xlsx"]),

        # 2. DVF
        S("run_dvf_national", "DVF séries département / région / pays", module="run_dvf",
          args=["--geo", "department,region,country"], group="2. DVF", requires=["@dvf"]),
        S("run_dvf_city", "DVF séries villes + dénormalisation + interpolation IDW", module="run_dvf",
          args=["--geo", "city"], group="2. DVF", requires=["@dvf"]),
        S("seed_transactions", "Ventes DVF géolocalisées -> table transactions (geo-dvf)", soft=True,
          group="2b. Ventes DVF"),
        S("seed_price_range_series", "price_sqm_low / avg / high (P10, moyenne, P90 par trimestre, depuis transactions)", soft=True,
          group="2b. Ventes DVF"),
        S("seed_transaction_volume_typology", "transaction_volume_t1..t4 (ventes d'appartements par typologie, depuis geo-dvf)", soft=True,
          group="2b. Ventes DVF", requires=["dvf-raw/geo-dvf/full-*.csv.gz"]),
        S("seed_city_sale_prices", "cities.median_sale_price / avg_sale_price", soft=True, group="2b. Ventes DVF"),
        S("interpolate_price_range", "IDW : fourchettes de prix des communes sans ventes (colonnes cities + séries price_sqm_*)", soft=True,
          group="2b. Ventes DVF"),

        # 3. Séries INSEE / DGFiP
        S("seed_pop_series", "population + aging_index", group="3. Séries INSEE / DGFiP",
          requires=["csv/base-ic-evol-struct-pop/*.CSV"]),
        S("seed_logement_series", "vacancy / owner / social_housing / secondary_residence", group="3. Séries INSEE / DGFiP",
          requires=["csv/base-ic-logement/*.CSV"]),
        S("seed_rp_series", "unemployment_rate", group="3. Séries INSEE / DGFiP",
          requires=["csv/base-ic-activite-residents/*.CSV"]),
        S("seed_employment_series", "active_population (soft si enum absent)", soft=True, group="3. Séries INSEE / DGFiP",
          requires=["csv/base-ic-activite-residents/*.CSV"]),
        S("seed_fiscalite_series", "property_tax_rate (TFB 2018-2025)", group="3. Séries INSEE / DGFiP",
          requires=["csv/fiscalite/fiscalite-locale-des-particuliers.csv"]),
        S("prep_population_history", "XLSX INSEE -> CSV/an (one-off, sauté si XLSX absent)", dept=False,
          group="3. Séries INSEE / DGFiP", requires=["csv/population-historique/raw_histo_pop.xlsx"]),
        S("seed_population_history_series", "population_history 2006-2023", group="3. Séries INSEE / DGFiP",
          requires=["csv/population-historique/population-historique-*.CSV"]),
        S("seed_commerce_series", "retail_count + retail_share_* (BPE)", group="3. Séries INSEE / DGFiP",
          requires=["csv/bpe-commerces/*.CSV"]),
        S("seed_household_size_series", "household_size_*p_rate", group="3. Séries INSEE / DGFiP",
          requires=["csv/menages-taille/*.CSV"]),
        S("seed_rent_series", "rent_sqm_* (Carte des loyers 2025)", group="3. Séries INSEE / DGFiP",
          requires=["csv/loyers/*.csv"]),
        S("seed_median_income_series", "median_income (Filosofi 2021)", group="3. Séries INSEE / DGFiP",
          requires=["csv/DS_FILOSOFI_CC_data.csv"]),
        S("seed_company_creations_series", "company_creations (SIDE 2012-2025)", group="3. Séries INSEE / DGFiP",
          requires=["csv/creations-entreprises/*.CSV"]),

        # 4. Zonage / réglementation / LCD
        S("seed_housing_zone", "housing_zone + high_demand_zone (zonage ABC national)", group="4. Zonage & réglementation",
          requires=["csv/zonage-abc-national.csv"]),
        S("scrape_rental_permit", "Reconstruit rental-permit.csv (scraping, réseau)", dept=False, soft=True, optional=True,
          group="4. Zonage & réglementation"),
        S("seed_rent_regulation", "rent_control + rental_permit_required", dept=False, group="4. Zonage & réglementation",
          requires=["csv/reglementation-locative/rent-control.csv"]),
        S("seed_short_term_rental_score", "short_term_rental_score", group="4. Zonage & réglementation",
          requires=["csv/location-courte-duree/*"]),

        # 4b. POI
        S("seed_poi", "POI OpenStreetMap -> table pois (.osm.pbf Geofabrik)", soft=True, group="4b. POI", args=["@poi"]),

        # 5. Comptes absolus, âge médian, profil locataire
        S("seed_activity_counts_series", "unemployed_count + retired_count", group="5. Démographie dérivée",
          requires=["csv/base-ic-*/*.CSV"]),
        S("seed_median_age", "median_age", group="5. Démographie dérivée", requires=["csv/base-ic-evol-struct-pop/*.CSV"]),
        S("seed_tenant_profile", "tenant_profile + tenant_profile_breakdown", group="5. Démographie dérivée",
          requires=["csv/base-ic-evol-struct-pop/*.CSV"]),
        S("seed_city_breakdowns", "age_pyramid / population_status / housing_type / housing_occupancy / dwelling_size (JSONB)",
          group="5. Démographie dérivée",
          requires=["csv/base-ic-evol-struct-pop/*.CSV", "csv/base-ic-activite-residents/*.CSV", "csv/base-ic-logement/*.CSV"]),
        S("seed_student_breakdown", "student_breakdown (ESR, par type de formation)", soft=True, group="5. Démographie dérivée",
          requires=["csv/fr-esr-atlas_regional-effectifs-d-etudiants-inscrits_agregeables.csv"]),

        # 6. Dérivés (après leurs dépendances)
        S("seed_dashboard_fields", "tenant_rate / demographic_growth_5y / employment_growth", group="6. Champs dérivés"),
        S("seed_avg_property_tax", "avg_property_tax", group="6. Champs dérivés"),
        S("seed_gross_yield", "gross_yield_t1-4 + avg_rent_per_sqm / gross_yield", group="6. Champs dérivés"),
        S("seed_years_to_buy", "years_to_buy", group="6. Champs dérivés"),
        S("seed_housing_effort_rate", "housing_effort_rate", group="6. Champs dérivés"),
        S("seed_city_latest_snapshots", "owner_rate / vacancy_rate / median_income / unemployment_rate / company_creations",
          group="6. Champs dérivés"),

        # 7. Contrôles
        S("audit", "Audit qualité des séries (anomalies QoQ)", dept=False, soft=True, args=["--no-chart"], group="7. Contrôles"),
        S("audit_coverage", "Couverture de la base (colonnes cities, séries, tables)", dept=False, soft=True,
          group="7. Contrôles"),
    ]


# ── POI pbf ───────────────────────────────────────────────────────────────────

def poi_pbf_path(region: str) -> Path:
    return POI_DIR / f"{region}-latest.osm.pbf"


def download_pbf(region: str, dest: Path) -> None:
    url = GEOFABRIK_FRANCE if region == "france" else GEOFABRIK_REGION.format(region=region)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  Téléchargement {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "rentium-immo-data-science/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        last = 0.0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total and time.time() - last > 2:
                print(f"\r    {done / 1e6:8.1f} / {total / 1e6:.1f} Mo", end="", flush=True)
                last = time.time()
    print()
    tmp.replace(dest)


# ── helpers ───────────────────────────────────────────────────────────────────

def dvf_dir(cli_value: str | None) -> Path:
    raw = cli_value or os.environ.get("DVF_RAW_DIR")
    return Path(raw) if raw else REPO_ROOT / "dvf-raw"


def inputs_present(step: Step, dvf: Path) -> tuple[bool, str]:
    """(ok, reason). A step is runnable when EVERY `requires` pattern matches at least one file."""
    for pattern in step.requires:
        if pattern == "@dvf":
            if not any(dvf.glob("*.txt")):
                return False, f"aucun ValeursFoncieres-*.txt dans {dvf} (--dvf-raw-dir / DVF_RAW_DIR)"
        elif not any(REPO_ROOT.glob(pattern)):
            return False, f"fichier source absent : {pattern}"
    return True, ""


def fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)   # keep our headers ordered with the children's output
    p = argparse.ArgumentParser(description="Load / refresh the whole Rentium DB (see module docstring)")
    p.add_argument("--dept", default=None, help="Restreint à un département (ex. 77) ; plusieurs : 75,77 pour seed_cities")
    p.add_argument("--list", action="store_true", help="Liste les étapes + état des fichiers source, puis quitte")
    p.add_argument("--dry-run", action="store_true", help="Affiche les commandes, n'exécute rien")
    p.add_argument("--only", default=None, help="Étapes à lancer (virgules)")
    p.add_argument("--from", dest="from_step", default=None, help="Reprend à cette étape")
    p.add_argument("--skip", default="", help="Étapes à sauter (virgules)")
    p.add_argument("--skip-dvf", action="store_true")
    p.add_argument("--skip-transactions", action="store_true")
    p.add_argument("--skip-poi", action="store_true")
    p.add_argument("--refresh-permit", action="store_true")
    p.add_argument("--refresh-poi", action="store_true")
    p.add_argument("--poi-region", default="france", help="france (défaut) | ile-de-france | bretagne | ...")
    p.add_argument("--poi-pbf", default=None, help="Chemin d'un .osm.pbf existant (pas de téléchargement)")
    p.add_argument("--dvf-raw-dir", default=None)
    p.add_argument("--continue-on-error", action="store_true")
    args = p.parse_args()

    steps = _steps()
    names = [s.name for s in steps]

    def check_names(csv: str, opt: str) -> list[str]:
        out = [n.strip() for n in csv.split(",") if n.strip()]
        bad = [n for n in out if n not in names]
        if bad:
            print(f"{opt}: étape(s) inconnue(s) {bad}. Voir --list.")
            sys.exit(2)
        return out

    only = check_names(args.only, "--only") if args.only else None
    skip = set(check_names(args.skip, "--skip"))
    if args.skip_dvf:
        skip |= {"run_dvf_national", "run_dvf_city"}
    if args.skip_transactions:
        skip |= {"seed_transactions", "seed_city_sale_prices", "seed_price_range_series", "seed_transaction_volume_typology", "interpolate_price_range"}
    if args.skip_poi:
        skip.add("seed_poi")
    if args.from_step and args.from_step not in names:
        print(f"--from: étape inconnue {args.from_step!r}. Voir --list.")
        return 2

    dvf = dvf_dir(args.dvf_raw_dir)
    child_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    if args.dvf_raw_dir:
        child_env["DVF_RAW_DIR"] = str(dvf)

    if args.list:
        group = None
        for i, s in enumerate(steps, 1):
            if s.group != group:
                group = s.group
                print(f"\n{group}")
            ok, why = inputs_present(s, dvf)
            flags = ("soft " if s.soft else "") + ("optionnel " if s.optional else "")
            print(f"  {i:2d}. {s.name:34s} {'OK ' if ok else 'MANQUE'}  {flags}{s.label}" + ("" if ok else f"\n        -> {why}"))
        print(f"\nDVF brut : {dvf}")
        print(f"POI      : {poi_pbf_path(args.poi_region)} ({'présent' if poi_pbf_path(args.poi_region).exists() else 'sera téléchargé'})")
        return 0

    if not args.dry_run and not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL absent : renseigne-le dans .env (cf. README > Installation).")
        return 2

    # Which steps run
    selected: list[Step] = []
    started = args.from_step is None
    for s in steps:
        if s.name == args.from_step:
            started = True
        if not started:
            continue
        if only is not None:
            if s.name not in only:
                continue
        elif s.optional and not (s.name == "scrape_rental_permit" and args.refresh_permit):
            continue
        selected.append(s)

    results: list[tuple[str, str, float, str]] = []   # (name, status, seconds, detail)
    failed_hard = False
    t_all = time.time()

    for idx, s in enumerate(selected, 1):
        header = f"[{idx}/{len(selected)}] {s.name} - {s.label}"
        if s.name in skip:
            print(f"\n=== {header}\n    SAUTÉ (demandé)")
            results.append((s.name, "sauté", 0.0, "demandé"))
            continue

        ok, why = inputs_present(s, dvf)
        if not ok:
            print(f"\n=== {header}\n    SAUTÉ : {why}")
            results.append((s.name, "sauté", 0.0, why))
            continue

        cmd_args = [a for a in s.args if a != "@poi"]
        if s.dept and args.dept:
            cmd_args += ["--dept", args.dept]

        if s.name == "seed_poi":
            pbf = Path(args.poi_pbf) if args.poi_pbf else poi_pbf_path(args.poi_region)
            if not pbf.is_absolute():
                pbf = REPO_ROOT / pbf
            if not args.dry_run and (args.refresh_poi or not pbf.exists()) and not args.poi_pbf:
                try:
                    download_pbf(args.poi_region, pbf)
                except (urllib.error.URLError, OSError, TimeoutError) as e:
                    print(f"\n=== {header}\n    ÉCHEC téléchargement POI : {e}")
                    results.append((s.name, "échec (soft)", 0.0, f"téléchargement : {e}"))
                    continue
            elif not pbf.exists() and not args.dry_run:
                print(f"\n=== {header}\n    SAUTÉ : {pbf} introuvable")
                results.append((s.name, "sauté", 0.0, f"{pbf} introuvable"))
                continue
            cmd_args += ["--pbf", str(pbf)]

        module = f"pipeline.scripts.{s.module or s.name}"
        cmd = [sys.executable, "-u", "-m", module, *cmd_args]
        print(f"\n=== {header}\n    $ python -m {module} {' '.join(cmd_args)}".rstrip())

        if args.dry_run:
            results.append((s.name, "dry-run", 0.0, ""))
            continue

        t0 = time.time()
        rc = subprocess.run(cmd, cwd=REPO_ROOT, env=child_env).returncode
        dt = time.time() - t0
        if rc == 0:
            results.append((s.name, "ok", dt, ""))
        elif s.soft:
            print(f"    ÉCHEC (soft, code {rc}) : on continue")
            results.append((s.name, "échec (soft)", dt, f"code {rc}"))
        else:
            print(f"    ÉCHEC (code {rc})")
            results.append((s.name, "ÉCHEC", dt, f"code {rc}"))
            failed_hard = True
            if not args.continue_on_error:
                print("    Arrêt. Corrige puis relance avec --from " + s.name)
                break

    print("\n" + "=" * 78)
    print(f"{'étape':36s} {'statut':14s} {'durée':>10s}  détail")
    for name, status, dt, detail in results:
        print(f"{name:36s} {status:14s} {fmt_duration(dt):>10s}  {detail}")
    print(f"\nDurée totale : {fmt_duration(time.time() - t_all)}")
    skipped_missing = [n for n, st, _, d in results if st == "sauté" and d != "demandé"]
    if skipped_missing:
        print(f"Étapes sautées faute de fichiers source : {', '.join(skipped_missing)} (cf. README > Sources de données)")
    return 1 if failed_hard else 0


if __name__ == "__main__":
    sys.exit(main())
