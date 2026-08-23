<#
Resumes the national seed from where seed_national.ps1 crashed.
seed_employment_series AND seed_dashboard_fields are both SKIPPED: both
reference the serie name "active_population" as a literal, which doesn't
exist in the Postgres "SerieName" enum (Prisma-schema gap) -> PG rejects the
query outright, even just to check if rows exist. Not needed anyway: neither
tenant_rate, demographic_growth_5y nor employment_growth (what
seed_dashboard_fields produces) are used by any of the 4 ml/ mémoire models.

Runs: seed_housing_zone -> run_dvf --geo city

Usage:
  .\scripts\seed_resume.ps1
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

Step "1/2 seed_housing_zone"  "pipeline.scripts.seed_housing_zone" @()
Step "2/2 run_dvf --geo city" "pipeline.scripts.run_dvf"           @("--geo", "city")

Write-Host "`n=== Seed resume done ===" -ForegroundColor Green
