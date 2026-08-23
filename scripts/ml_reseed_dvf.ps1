<#
Etape 1/4 du pipeline post-fix bug multi-lot DVF (ml/EXPERIMENTS_LOG.md
"Modele rendement" #0 : mutations multi-lots a nb_pieces mixtes surcomptees
dans price_sqm_all/appt/house -> 7.15% des lignes filtrees gonflees de +32%).
Seule etape qui ecrit en DB. La plus longue du pipeline complet.

Usage:
  .\scripts\ml_reseed_dvf.ps1
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

Step "1/1 run_dvf (national, tous niveaux geo, ecrit en DB)" "pipeline.scripts.run_dvf" @()

Write-Host "`n=== Reseed DVF termine ===" -ForegroundColor Green
