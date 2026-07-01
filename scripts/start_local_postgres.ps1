$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$PgBin = Join-Path $Root ".local\postgres16\pgsql\bin"
$PgData = Join-Path $Root ".local\pgdata"
$PgLog = Join-Path $Root ".local\postgres.log"

if (-not (Test-Path (Join-Path $PgBin "pg_ctl.exe"))) {
    throw "Postgres portatil nao encontrado em $PgBin"
}
if (-not (Test-Path $PgData)) {
    throw "Cluster local nao encontrado em $PgData"
}

& (Join-Path $PgBin "pg_ctl.exe") -D $PgData -l $PgLog -o "-p 5432" start
$env:PGPASSWORD = "postgres"
& (Join-Path $PgBin "psql.exe") -h localhost -p 5432 -U postgres -d mktplace_control -c "SELECT current_database(), current_user;"
