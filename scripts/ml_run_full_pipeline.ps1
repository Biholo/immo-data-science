<#
Pipeline complet post-fix bug multi-lot DVF (ml/EXPERIMENTS_LOG.md "Modele
rendement" #0 et #7) : reseed DB -> modeles -> livrables -> reporting.

Chaque etape est aussi un script independant, lancable seul si besoin de
reprendre au milieu :
  ml_reseed_dvf.ps1        (1/4 - ecrit en DB, le plus long)
  ml_run_models.ps1        (2/4 - 4 modeles memoire + yield_model --tune)
  ml_run_deliverables.ps1  (3/4 - Top 20, cash-flow, cartes rendement)
  ml_run_reporting.ps1     (4/4 - figures memoire + recap pass/fail)

Usage:
  .\scripts\ml_run_full_pipeline.ps1              # tout, dans l'ordre
  .\scripts\ml_run_full_pipeline.ps1 -SkipReseed  # saute l'etape DB (deja fait)
#>

param(
    [switch]$SkipReseed
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

function RunStage {
    param([string]$Script)
    Write-Host "`n########## $Script ##########" -ForegroundColor Magenta
    & (Join-Path $here $Script)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "PIPELINE STOPPED at $Script (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

if (-not $SkipReseed) {
    RunStage "ml_reseed_dvf.ps1"
} else {
    Write-Host "Reseed DVF saute (-SkipReseed)" -ForegroundColor Yellow
}

RunStage "ml_run_models.ps1"
RunStage "ml_run_deliverables.ps1"
RunStage "ml_run_reporting.ps1"

Write-Host "`n=== Pipeline complet termine ===" -ForegroundColor Green
