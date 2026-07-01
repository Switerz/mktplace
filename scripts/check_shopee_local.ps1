$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Psql = Join-Path $Root ".local\postgres16\pgsql\bin\psql.exe"
$env:PGPASSWORD = "postgres"
& $Psql -h localhost -p 5432 -U postgres -d mktplace_control -c "SELECT l.brand_key, COUNT(*) days, MIN(f.date) min_date, MAX(f.date) max_date, ROUND(SUM(COALESCE(f.gmv,0)),2) gmv, SUM(COALESCE(f.orders,0)) orders, ROUND(SUM(COALESCE(f.ad_spend,0)),2) ad_spend FROM marts.fact_marketplace_daily_performance f JOIN marts.dim_loja l ON l.loja_id=f.loja_id WHERE f.marketplace_id=3 GROUP BY l.brand_key ORDER BY l.brand_key;"
