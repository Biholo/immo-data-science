<#
Seed EVERYTHING into the DB in dependency order, one command.

  .\scripts\seed_all.ps1                 # France entière (~35k communes ; l'étape DVF city est la plus longue)
  .\scripts\seed_all.ps1 -Dept 77        # un département (rapide, dev/test)
  .\scripts\seed_all.ps1 -Dept 75,77,92  # plusieurs départements
  .\scripts\seed_all.ps1 -RefreshPermit  # relance aussi scrape_rental_permit (réseau, ~700 géocodages) avant seed_rent_regulation
  .\scripts\seed_all.ps1 -SkipDvf        # saute run_dvf (utile si DVF déjà seedé et qu'on ne veut que les séries INSEE)

Prérequis :
  - .env avec DATABASE_URL
  - fichiers sources déposés dans csv/ et dvf-raw/ (cf. README "Sources de données")
  - migration Prisma appliquée côté rentium/backend (enum SerieName + colonnes cities).
    `active_population` a été ajouté à serie.prisma le 2026-09-10 — si la migration
    n'est pas encore passée, seed_employment_series est sauté en soft-fail et
    employment_growth reste nul.
#>

param(
    [string]$Dept = $null,
    [switch]$RefreshPermit,
    [switch]$SkipDvf
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$env:PYTHONIOENCODING = "utf-8"

$deptArgs = @()
if ($Dept) { $deptArgs = @("--dept", $Dept) }

function Step {
    param([string]$Label, [string]$Module, [string[]]$Args = @(), [switch]$Soft)
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    python -m $Module @Args
    if ($LASTEXITCODE -ne 0) {
        if ($Soft) {
            Write-Host "SKIP (soft-fail): $Label (exit $LASTEXITCODE)" -ForegroundColor Yellow
        } else {
            Write-Host "FAILED: $Label (exit $LASTEXITCODE)" -ForegroundColor Red
            exit $LASTEXITCODE
        }
    }
}

# ---------------------------------------------------------------------------
# 1. Base géographique — doit être en premier
# ---------------------------------------------------------------------------
Step "seed_cities" "pipeline.scripts.seed_cities" $(if ($Dept) { @("--dept", $Dept) } else { @() })
Step "seed_rp (RP 2022 : student_count)" "pipeline.scripts.seed_rp" $deptArgs -Soft

# ---------------------------------------------------------------------------
# 2. DVF — prix / surface / volume / VEFA / terrain (denormalize + interpolate inclus)
# ---------------------------------------------------------------------------
if (-not $SkipDvf) {
    Step "run_dvf (department,region,country)" "pipeline.scripts.run_dvf" (@("--geo", "department,region,country") + $deptArgs)
    Step "run_dvf (city)" "pipeline.scripts.run_dvf" (@("--geo", "city") + $deptArgs)
}

# ---------------------------------------------------------------------------
# 3. Séries socio-démo / INSEE / DGFiP
# ---------------------------------------------------------------------------
Step "seed_pop_series"                 "pipeline.scripts.seed_pop_series"                 $deptArgs
Step "seed_logement_series"            "pipeline.scripts.seed_logement_series"            $deptArgs
Step "seed_rp_series"                  "pipeline.scripts.seed_rp_series"                  $deptArgs
Step "seed_employment_series"          "pipeline.scripts.seed_employment_series"          $deptArgs -Soft
Step "seed_fiscalite_series"           "pipeline.scripts.seed_fiscalite_series"           $deptArgs
Step "prep_population_history"         "pipeline.scripts.prep_population_history"
Step "seed_population_history_series"  "pipeline.scripts.seed_population_history_series"   $deptArgs
Step "seed_commerce_series"            "pipeline.scripts.seed_commerce_series"            $deptArgs
Step "seed_household_size_series"      "pipeline.scripts.seed_household_size_series"       $deptArgs
Step "seed_rent_series"                "pipeline.scripts.seed_rent_series"                $deptArgs
Step "seed_median_income_series"       "pipeline.scripts.seed_median_income_series"       $deptArgs
Step "seed_company_creations_series"   "pipeline.scripts.seed_company_creations_series"   $deptArgs

# ---------------------------------------------------------------------------
# 4. Zonage + réglementation locative + score LCD (UPDATE direct cities)
# ---------------------------------------------------------------------------
Step "seed_housing_zone (national)" "pipeline.scripts.seed_housing_zone" $deptArgs
if ($RefreshPermit) {
    Step "scrape_rental_permit (réseau)" "pipeline.scripts.scrape_rental_permit"
}
Step "seed_rent_regulation"        "pipeline.scripts.seed_rent_regulation"
Step "seed_short_term_rental_score" "pipeline.scripts.seed_short_term_rental_score" $deptArgs

# POI OpenStreetMap → table `pois`. Soft-fail : le .pbf Geofabrik (france-latest ~4 Go,
# ou un extrait régional) est un fichier de travail jetable qui peut ne pas être présent.
# Cf. README "Rafraîchir les données > POI (OpenStreetMap)".
Step "seed_poi (OpenStreetMap → pois)" "pipeline.scripts.seed_poi" $deptArgs -Soft

# ---------------------------------------------------------------------------
# 5. Comptes absolus + âge médian + profil locataire (UPDATE direct cities)
# ---------------------------------------------------------------------------
Step "seed_activity_counts_series" "pipeline.scripts.seed_activity_counts_series" $deptArgs
Step "seed_median_age"             "pipeline.scripts.seed_median_age"             $deptArgs
Step "seed_tenant_profile"         "pipeline.scripts.seed_tenant_profile"         $deptArgs

# ---------------------------------------------------------------------------
# 6. Dérivés — APRÈS leurs dépendances ci-dessus
# ---------------------------------------------------------------------------
Step "seed_dashboard_fields"     "pipeline.scripts.seed_dashboard_fields"     $deptArgs
Step "seed_avg_property_tax"     "pipeline.scripts.seed_avg_property_tax"     $deptArgs
Step "seed_gross_yield"          "pipeline.scripts.seed_gross_yield"          $deptArgs
Step "seed_years_to_buy"         "pipeline.scripts.seed_years_to_buy"         $deptArgs
Step "seed_housing_effort_rate"  "pipeline.scripts.seed_housing_effort_rate"  $deptArgs
Step "seed_city_latest_snapshots" "pipeline.scripts.seed_city_latest_snapshots" $deptArgs

# ---------------------------------------------------------------------------
# 7. Audit qualité
# ---------------------------------------------------------------------------
Step "audit" "pipeline.scripts.audit"

Write-Host "`n=== seed_all terminé ===" -ForegroundColor Green
