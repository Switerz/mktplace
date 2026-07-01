# run_with_lock.ps1 - evita duas execucoes simultaneas da mesma fonte de sync.
#
# Uso:
#   powershell -File scripts\run_with_lock.ps1 <nome-lock> <executavel> [args...]
#
# Exemplo:
#   powershell -File scripts\run_with_lock.ps1 daily_ml `
#     "C:\Users\Notebook\Desktop\mktplace\apps\api\.venv\Scripts\python.exe" `
#     -m pipelines.ingestion.daily_performance --source ml --mode incremental
#
# Cria logs\<nome-lock>.lock enquanto o comando roda. Se o lock ja existir,
# aborta com exit code 1 (nao mata nem espera o processo anterior).
# O exit code do comando executado e propagado - Task Scheduler marca
# "Last Run Result" diferente de zero quando a fonte falha, permitindo
# alerta externo.

param(
    [Parameter(Mandatory = $true)][string]$LockName,
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)][string[]]$Cmd
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$lockFile = Join-Path $logDir "$LockName.lock"

if (Test-Path $lockFile) {
    $age = (Get-Date) - (Get-Item $lockFile).LastWriteTime
    $ageMin = [int]$age.TotalMinutes
    Write-Error "Lock '$LockName' ja existe (criado ha $ageMin min) - outra execucao pode estar em andamento. Abortando sem rodar $($Cmd[0])."
    exit 1
}

Set-Content -Path $lockFile -Value (Get-Date).ToString("o") -Encoding utf8

try {
    & $Cmd[0] $Cmd[1..($Cmd.Length - 1)]
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) { $exitCode = 0 }
}
catch {
    $errText = $_.Exception.Message
    Write-Error "Erro ao executar $($Cmd[0]): $errText"
    $exitCode = 1
}
finally {
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
}

exit $exitCode
