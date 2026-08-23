<#
Etape 2/4 du pipeline post-fix. Relance les 4 modeles memoire (clustering ->
price -> forecasting -> quantile) puis reentraine le modele rendement sur la
nouvelle formule de rendement_brut (cout d'acquisition reel = prix + notaire
+ travaux, voir ml/EXPERIMENTS_LOG.md "Modele rendement" #7).

A lancer APRES ml_reseed_dvf.ps1 (median_price_per_sqm change en DB).

Usage:
  .\scripts\ml_run_models.ps1
#>

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$env:PYTHONIOENCODING = "utf-8"

function Step {
    param([string]$Label, [string]$Module, [string[]]$Args)
    Write-Host "`n=== $Label ===" -ForegroundColor Cyan
    python -m $Module @Args
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Label (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Step "1/3 run_all (clustering -> price -> forecasting -> quantile)" "ml.scripts.run_all"    @()
Step "2/3 yield_model --tune"                                       "ml.models.yield_model" @("--tune")
# price_hedonic.py reutilise BASE_PRICE_MULTI (pipeline/services/series.py) -> EST affecte
# par le fix multi-lot, oublie une premiere fois (voir docs/MEMOIRE_PARTIE3_INPUTS.md, avertissement en tete).
Step "3/3 price_hedonic --full"                                     "ml.models.price_hedonic" @("--full")

Write-Host "`n=== Modeles a jour ===" -ForegroundColor Green
