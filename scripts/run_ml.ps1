<#
Trains all 4 mémoire ML models (clustering, price/opportunity score, forecasting,
quantile) on whatever data currently sits in the DB. Run seed_national.ps1 first
if the DB isn't populated yet.

Usage:
  .\scripts\run_ml.ps1                    # full dataset currently in DB
  .\scripts\run_ml.ps1 -Dept 77           # restrict to one department
  .\scripts\run_ml.ps1 -SkipQuantile      # skip Model 4 (optional per docs/CONTEXTE_ML_MEMOIRE.md)
  .\scripts\run_ml.ps1 -Tune              # RandomizedSearchCV for price_model (slower, ~100 fits)
  .\scripts\run_ml.ps1 -Weighted           # ANOVA-weighted clustering features (better silhouette + external validation)
  .\scripts\run_ml.ps1 -DryRun            # compute + print, no artifacts written
#>

param(
    [string]$Dept = $null,
    [switch]$SkipQuantile,
    [switch]$Tune,
    [switch]$Weighted,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$env:PYTHONIOENCODING = "utf-8"

$mlArgs = @()
if ($Dept) { $mlArgs += @("--dept", $Dept) }
if ($SkipQuantile) { $mlArgs += "--skip-quantile" }
if ($Tune) { $mlArgs += "--tune" }
if ($Weighted) { $mlArgs += "--weighted" }
if ($DryRun) { $mlArgs += "--dry-run" }

Write-Host "`n=== ml.scripts.run_all $($mlArgs -join ' ') ===" -ForegroundColor Cyan
python -m ml.scripts.run_all @mlArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAILED: ML pipeline (exit $LASTEXITCODE)" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "`n=== ML pipeline done. Artifacts under ml/artifacts/<model>/latest/ ===" -ForegroundColor Green
