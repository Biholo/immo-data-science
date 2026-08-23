<#
Seeds the full dataset into the local DB: communes, socio-demo series (INSEE),
housing zone, and DVF price series (denormalize + interpolate run automatically
inside run_dvf.py's city-level path).

Usage:
  .\scripts\seed_national.ps1                # full France (~35k communes, slow — DVF step is the heaviest)
  .\scripts\seed_national.ps1 -Dept 77       # restrict to one department (fast, dev/test)
  .\scripts\seed_national.ps1 -Dept 75,77,92 # restrict to several departments
#>

param(
    [string]$Dept = $null
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$env:PYTHONIOENCODING = "utf-8"  # seed_*.py scripts print accented/arrow chars — avoids cp1252 crash

function Step {
    param([string]$Label, [string]$Module, [string[]]$Args)
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    python -m $Module @Args
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Label (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

$deptArgs = @()
if ($Dept) { $deptArgs = @("--dept", $Dept) }

# seed_employment_series AND seed_dashboard_fields are both SKIPPED: both
# reference the serie name "active_population" as a literal, which doesn't
# exist in the Postgres "SerieName" enum (Prisma-schema gap, not fixable from
# here) -> PG rejects the query outright. Not needed anyway: neither
# tenant_rate, demographic_growth_5y nor employment_growth (what
# seed_dashboard_fields produces) are used by any of the 4 ml/ mémoire models.

Step "1/6 seed_cities"          "pipeline.scripts.seed_cities"          $(if ($Dept) { @("--dept", $Dept) } else { @() })
Step "2/6 seed_pop_series"      "pipeline.scripts.seed_pop_series"      $deptArgs
Step "3/6 seed_logement_series" "pipeline.scripts.seed_logement_series" $deptArgs
Step "4/6 seed_rp_series"       "pipeline.scripts.seed_rp_series"       $deptArgs
Step "5/6 seed_housing_zone"    "pipeline.scripts.seed_housing_zone"    $deptArgs
Step "6/6 run_dvf --geo city"   "pipeline.scripts.run_dvf"              $(@("--geo", "city") + $deptArgs)

Write-Host "`n=== Seed pipeline done ===" -ForegroundColor Green
