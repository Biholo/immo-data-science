<#
Raccourci PowerShell vers l'orchestrateur Python (source de vérité : pipeline/scripts/seed_all.py).
Toute la logique (ordre des étapes, fichiers requis, POI, reprise) vit côté Python : voir
`python -m pipeline.scripts.seed_all --help` et docs/COMMANDES.md.

  .\scripts\seed_all.ps1                   # France entière, tout (DVF, transactions, INSEE, POI...)
  .\scripts\seed_all.ps1 -Dept 77          # un département (dev / test)
  .\scripts\seed_all.ps1 -RefreshPermit    # relance aussi scrape_rental_permit (réseau)
  .\scripts\seed_all.ps1 -SkipDvf          # saute run_dvf
  .\scripts\seed_all.ps1 -SkipTransactions # saute seed_transactions + seed_city_sale_prices
  .\scripts\seed_all.ps1 -SkipPoi          # saute seed_poi (pas de téléchargement OSM ~4 Go)
  .\scripts\seed_all.ps1 -Extra "--from","seed_housing_zone"   # toute autre option de seed_all.py
#>

param(
    [string]$Dept = $null,
    [switch]$RefreshPermit,
    [switch]$SkipDvf,
    [switch]$SkipTransactions,
    [switch]$SkipPoi,
    [string[]]$Extra = @()
)

Set-Location (Join-Path $PSScriptRoot "..")
$env:PYTHONIOENCODING = "utf-8"

$pyArgs = @()
if ($Dept)             { $pyArgs += @("--dept", $Dept) }
if ($RefreshPermit)    { $pyArgs += "--refresh-permit" }
if ($SkipDvf)          { $pyArgs += "--skip-dvf" }
if ($SkipTransactions) { $pyArgs += "--skip-transactions" }
if ($SkipPoi)          { $pyArgs += "--skip-poi" }
$pyArgs += $Extra

python -m pipeline.scripts.seed_all @pyArgs
exit $LASTEXITCODE
