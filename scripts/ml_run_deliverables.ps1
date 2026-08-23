<#
Etape 3/4 du pipeline post-fix. Recombine les modeles en livrables concrets :
Top 20 + fiches + cartes, cash-flow 5 profils bancaires, cartes nationales
de rendement (brut/net, IDW + bulles).

A lancer APRES ml_run_models.ps1.

Usage:
  .\scripts\ml_run_deliverables.ps1
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

Step "1/3 generate_recommendations (Top 20, fiches, maps)"    "ml.scripts.generate_recommendations" @()
Step "2/3 simulate_cashflow (Top 20 x 5 profils)"              "ml.scripts.simulate_cashflow"         @()
Step "3/3 analyze_yield_geography (cartes nationales)"         "ml.scripts.analyze_yield_geography"   @()

Write-Host "`n=== Livrables a jour ===" -ForegroundColor Green
