<#
Etape 4/4 du pipeline post-fix. Regenere les 16 figures memoire a partir des
artefacts frais, puis recap final pass/fail lu depuis run_metadata.json.

A lancer APRES ml_run_deliverables.ps1.

Usage:
  .\scripts\ml_run_reporting.ps1
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

Step "1/2 generate_charts (16 figures memoire)"       "ml.scripts.generate_charts"    @()
Step "2/2 check_performance (recap pass/fail)"        "ml.scripts.check_performance"  @()

Write-Host "`n=== Reporting a jour ===" -ForegroundColor Green
