# run_task.ps1 - wrapper fino chamado pelo Task Scheduler. Recebe so' uma
# -TaskKey curta (sem espacos, sem aspas aninhadas) e resolve internamente
# o lock/timeout/comando reais, delegando a run_with_lock.ps1.
#
# Motivo de existir: um comando /tr do schtasks com varios niveis de aspas
# aninhadas (powershell -File "..." nome -TimeoutSeconds N "python.exe" -m
# modulo --arg valor) e' fragil — o Windows quebra a tokenizacao em casos
# nao triviais. Com -TaskKey, o /tr fica com UM nivel de aspas (so' o
# caminho deste script), e o mapeamento real fica aqui, testavel
# isoladamente (via dot-source, sem executar nada) sem tocar no Task
# Scheduler nem em nenhum pipeline real.
#
# Uso real (Task Scheduler):
#   powershell -NoProfile -NonInteractive -File scripts\run_task.ps1 -TaskKey daily_ingestion
#
# Uso em teste (Pester) — carrega so' as funcoes, nao executa nada:
#   . .\scripts\run_task.ps1
#   Resolve-TaskInvocation -TaskKey daily_ingestion -RepoRoot "C:\repo" -PythonExe "python.exe" -LockScript "lock.ps1"

param(
    [string]$TaskKey
)

function Get-TaskDefinitions {
    # Unica fonte de verdade do mapeamento TaskKey -> (lock, timeout,
    # modulo). Mantido em paridade com pipelines/ops/schedule_plan.py
    # (Python, EXTERNAL_LOCK_TIMEOUT_SECONDS) — aquele modulo gera/
    # documenta esta tabela para revisao; esta e' a versao que roda de
    # fato quando o Task Scheduler dispara.
    #
    # UMA UNICA TaskKey (full_daily): o desenho anterior com 2 TaskKeys
    # separadas (daily_ingestion + produtos_and_monitor, agendadas em
    # horarios diferentes) foi descartado porque nao havia garantia de que
    # a primeira tivesse terminado antes da segunda comecar — agora tudo
    # roda sob o MESMO lock, em pipelines.ops.orchestrate.PIPELINES["full_daily"].
    #
    # TimeoutSeconds=9000 (2h30) tem que ficar MAIOR que a soma dos
    # timeouts individuais dos steps internos (6780s — ver
    # pipelines/ops/orchestrate.py:FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS),
    # com margem: senao este timeout EXTERNO mataria o processo pai antes
    # que os timeouts internos por step tivessem chance de proteger as
    # fontes independentes seguintes.
    return @{
        "full_daily" = @{ Lock = "full_daily"; TimeoutSeconds = 9000; Module = "pipelines.ops.orchestrate"; ModuleArgs = @("--pipeline", "full_daily") }
    }
}

function Resolve-TaskInvocation {
    param(
        [Parameter(Mandatory = $true)][string]$TaskKey,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][string]$LockScript
    )
    $tasks = Get-TaskDefinitions
    if (-not $tasks.ContainsKey($TaskKey)) {
        return $null
    }
    $cfg = $tasks[$TaskKey]
    $moduleArgs = @("-m", $cfg.Module) + $cfg.ModuleArgs
    return [PSCustomObject]@{
        LockScript       = $LockScript
        LockName         = $cfg.Lock
        TimeoutSeconds   = $cfg.TimeoutSeconds
        WorkingDirectory = $RepoRoot
        PythonExe        = $PythonExe
        ModuleArgs       = $moduleArgs
    }
}

# So' executa de verdade quando chamado diretamente (nao quando
# dot-sourced pelos testes para so' carregar as funcoes acima).
if ($MyInvocation.InvocationName -ne '.') {
    if ([string]::IsNullOrWhiteSpace($TaskKey)) {
        Write-Error "Parametro -TaskKey obrigatorio."
        exit 1
    }

    $repoRoot = Split-Path -Parent $PSScriptRoot
    $python = Join-Path $repoRoot "apps\api\.venv\Scripts\python.exe"
    $lockScript = Join-Path $PSScriptRoot "run_with_lock.ps1"

    $invocation = Resolve-TaskInvocation -TaskKey $TaskKey -RepoRoot $repoRoot -PythonExe $python -LockScript $lockScript
    if ($null -eq $invocation) {
        $known = (Get-TaskDefinitions).Keys -join ", "
        Write-Error "TaskKey desconhecida: '$TaskKey'. Opcoes: $known"
        exit 1
    }

    & powershell -NoProfile -NonInteractive -File $invocation.LockScript $invocation.LockName `
        -TimeoutSeconds $invocation.TimeoutSeconds -WorkingDirectory $invocation.WorkingDirectory `
        $invocation.PythonExe @($invocation.ModuleArgs)
    exit $LASTEXITCODE
}
