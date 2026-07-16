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
# Gate B6.1d (2026-07-16) — bug corrigido: `Invoke-ResolvedTask` passa o
# comando real a run_with_lock.ps1 via DOT-SOURCE em processo (nunca mais
# um `powershell -File run_with_lock.ps1 ...` aninhado), com o array de
# argumentos ja construido ligado explicitamente a `-Cmd` numa unica
# expressao PowerShell. Causa raiz do bug (achado no Gate B6.1c, real:
# `full_daily` falhava com "orchestrate.py: error: the following arguments
# are required: --pipeline" antes de qualquer step rodar): run_with_lock.ps1
# usa atributos `[Parameter(...)]`, o que o PowerShell trata como um
# "advanced script" com TODOS os CommonParameters habilitados implicitamente
# (Verbose, Debug, PipelineVariable, WhatIf, Confirm, etc). `--pipeline` e'
# um prefixo AMBIGUO-LIVRE de `-PipelineVariable` — quando os argumentos
# chegavam como texto bruto de linha de comando (via `-File` aninhado), o
# parameter binder do PowerShell silenciosamente consumia `--pipeline` E o
# valor seguinte (`full_daily`) como `-PipelineVariable`, sem nunca deixa-los
# chegar em `$Cmd`/`Start-Process`. Passar o array ja construido via
# dot-source elimina essa classe inteira de colisao — nao so' para
# `--pipeline`, para qualquer futuro flag que coincida com um
# CommonParameter (ex.: `--verbose`, `--debug`).
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

function Invoke-ResolvedTask {
    # Gate B6.1d: dot-source de run_with_lock.ps1 EM PROCESSO (nunca um
    # `powershell -File run_with_lock.ps1 ...` aninhado) — o comando real
    # (PythonExe + ModuleArgs, incluindo flags como `--pipeline`) e' passado
    # como um ARRAY JA' CONSTRUIDO, ligado explicitamente ao parametro -Cmd
    # numa unica expressao PowerShell avaliada NESTE processo. Isso nunca
    # re-tokeniza os argumentos como texto de linha de comando atravessando
    # um novo processo — e' exatamente essa re-tokenizacao que fazia
    # `--pipeline full_daily` ser silenciosamente consumido pelo PowerShell
    # como o parametro comum `-PipelineVariable` (ver nota no topo deste
    # arquivo). `-Cmd` continua aceitando `ValueFromRemainingArguments` para
    # quem chama run_with_lock.ps1 diretamente via CLI com argumentos
    # simples (uso original, ainda suportado) — aqui so' preferimos o
    # binding NOMEADO explicito, que sempre funciona independente disso.
    param(
        [Parameter(Mandatory = $true)][PSCustomObject]$Invocation
    )
    $fullCmd = @($Invocation.PythonExe) + $Invocation.ModuleArgs
    . $Invocation.LockScript -LockName $Invocation.LockName -TimeoutSeconds $Invocation.TimeoutSeconds `
        -WorkingDirectory $Invocation.WorkingDirectory -Cmd $fullCmd
    # run_with_lock.ps1 termina com `exit $exitCode` (sempre, em qualquer
    # desfecho) — como foi dot-sourced (nao um processo filho separado),
    # esse `exit` encerra ESTE MESMO processo com o exit code correto.
    # Nenhuma linha depois desta chamada (nem o `exit $LASTEXITCODE` no
    # bloco abaixo) e' de fato alcancada no caminho real; fica so' como
    # rede de seguranca caso o comportamento de `exit` mude no futuro.
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

    Invoke-ResolvedTask -Invocation $invocation
    exit $LASTEXITCODE
}
