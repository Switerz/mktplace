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
# Uso real (Task Scheduler ou manual):
#   powershell -NoProfile -NonInteractive -File scripts\run_task.ps1 -TaskKey full_daily
#   powershell -NoProfile -NonInteractive -File scripts\run_task.ps1 -TaskKey shopee_manual_refresh
#   powershell -NoProfile -NonInteractive -File scripts\run_task.ps1 -TaskKey serving_refresh
#
# Uso em teste (Pester) — carrega so' as funcoes, nao executa nada:
#   . .\scripts\run_task.ps1
#   Resolve-TaskInvocation -TaskKey full_daily -RepoRoot "C:\repo" -PythonExe "python.exe" -LockScript "lock.ps1"

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
    # TRES TaskKeys: as duas do Gate C1 (2026-07-16), abaixo, e
    # "serving_refresh" (Checkpoint O1 Task 2/2, 2026-08-17), documentada logo
    # depois destas.
    #   - "full_daily": fontes REALMENTE recorrentes (ml, tiktok, regional,
    #     produtos ml/tiktok, health_check) — a UNICA candidata a rodar sob
    #     o Task Scheduler (ver schedule_plan.py, que so' agenda esta).
    #   - "shopee_manual_refresh": Shopee (orders/stats/ads) + produtos
    #     Shopee + Bug 8 + health_check — MANUAL, sob demanda, nunca
    #     agendada. Existe aqui so' para reaproveitar o mesmo wrapper de
    #     lock/timeout/log (run_with_lock.ps1) quando o operador decide
    #     rodar Shopee manualmente, evitando concorrencia com um
    #     `full_daily` em andamento (locks distintos, mas o operador ainda
    #     assim nunca deveria rodar os dois ao mesmo tempo por engano).
    #
    # O desenho anterior a isso, com 2 TaskKeys agendadas em horarios
    # diferentes (daily_ingestion + produtos_and_monitor), foi descartado
    # porque nao havia garantia de que a primeira tivesse terminado antes
    # da segunda comecar. Essa garantia ("um pipeline so' avanca depois que
    # o anterior de verdade terminou, nunca por horario") continua valendo
    # para cada TaskKey individualmente — as duas de agora sao
    # independentes de proposito (uma automatica, outra manual), nao
    # amarradas uma a outra.
    #
    # TimeoutSeconds=9000 (2h30) tem que ficar MAIOR que a soma dos
    # timeouts individuais dos steps internos de cada pipeline (ver
    # pipelines/ops/orchestrate.py:FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS
    # = 6600s desde o Checkpoint O1 Task 2/2, que somou 3000s dos tres steps
    # de serving aos 3600s do Gate C1;
    # SHOPEE_MANUAL_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS = 3780s; e
    # SERVING_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS = 3000s), com
    # margem: senao este timeout EXTERNO mataria o processo pai antes que
    # os timeouts internos por step tivessem chance de proteger as fontes
    # independentes seguintes. Os dois pipelines reaproveitam o mesmo
    # 9000s por simplicidade — ambos os orcamentos internos cabem com
    # folga.
    # TERCEIRA TaskKey desde o Checkpoint O1 Task 2/2 (2026-08-17):
    #   - "serving_refresh": so' os tres steps de serving (ML, TikTok brand,
    #     TikTok creator), MANUAL, NUNCA agendada (sem entrada em
    #     schedule_plan.py PROPOSED_SCHEDULE, sem tarefa criada no Task
    #     Scheduler). Serve a UM cenario real: o `full_daily` das 06:00 foi
    #     bloqueado no preflight porque a VPN estava fora, e mais tarde o
    #     operador quer atualizar so' o serving que `/operacoes` le, sem
    #     reprocessar daily/produtos/regional.
    #
    #     Lock = "full_daily" DE PROPOSITO, nao um lock proprio: as duas
    #     TaskKeys mexem nas mesmas fontes e no mesmo destino, e compartilhar o
    #     lock torna a sobreposicao impossivel por construcao — se o `full_daily`
    #     agendado estiver em andamento, esta TaskKey sai BLOCKED sem rodar, em
    #     vez de disputar a mesma janela de dados. E' o oposto do desenho de
    #     "shopee_manual_refresh", que tem lock separado porque mexe em fontes
    #     disjuntas (Shopee) e pode legitimamente rodar em paralelo.
    return @{
        "full_daily" = @{ Lock = "full_daily"; TimeoutSeconds = 9000; Module = "pipelines.ops.orchestrate"; ModuleArgs = @("--pipeline", "full_daily") }
        "shopee_manual_refresh" = @{ Lock = "shopee_manual_refresh"; TimeoutSeconds = 9000; Module = "pipelines.ops.orchestrate"; ModuleArgs = @("--pipeline", "shopee_manual_refresh") }
        "serving_refresh" = @{ Lock = "full_daily"; TimeoutSeconds = 9000; Module = "pipelines.ops.orchestrate"; ModuleArgs = @("--pipeline", "serving_refresh") }
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
