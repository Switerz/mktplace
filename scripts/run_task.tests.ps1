# Testes Pester de scripts\run_task.ps1: resolucao de -TaskKey para a
# invocacao real (lock/timeout/modulo), sem nunca executar nada quando
# carregado via dot-source. Nenhum pipeline real, nenhum banco tocado,
# nenhuma tarefa criada no Task Scheduler.
#
# Uso:
#   Invoke-Pester scripts\run_task.tests.ps1

$repoRoot = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "run_task.ps1"

Describe "run_task.ps1 (dot-source, so' carrega as funcoes)" {

    . $script

    It "conhece exatamente a TaskKey unica da agenda proposta (full_daily)" {
        (Get-TaskDefinitions).Keys | Should Be @("full_daily")
    }

    It "resolve full_daily com lock/timeout/modulo corretos" {
        $inv = Resolve-TaskInvocation -TaskKey "full_daily" -RepoRoot $repoRoot -PythonExe "python.exe" -LockScript "lock.ps1"
        $inv.LockName | Should Be "full_daily"
        $inv.TimeoutSeconds | Should Be 9000
        $inv.WorkingDirectory | Should Be $repoRoot
        ($inv.ModuleArgs -join " ") | Should Be "-m pipelines.ops.orchestrate --pipeline full_daily"
    }

    It "TimeoutSeconds externo (9000) e' maior que o orcamento somado dos steps internos (7200 desde o Gate B2)" {
        $inv = Resolve-TaskInvocation -TaskKey "full_daily" -RepoRoot $repoRoot -PythonExe "python.exe" -LockScript "lock.ps1"
        $internalBudget = 7200
        ($inv.TimeoutSeconds -gt $internalBudget) | Should Be $true
    }

    It "retorna `$null para uma TaskKey desconhecida (nunca lanca excecao)" {
        $inv = Resolve-TaskInvocation -TaskKey "chave_que_nao_existe" -RepoRoot $repoRoot -PythonExe "python.exe" -LockScript "lock.ps1"
        $inv | Should Be $null
    }

    It "dot-sourcing nao executa nada (nenhum processo, nenhum erro, sem -TaskKey)" {
        # Se o bloco `if ($MyInvocation.InvocationName -ne '.')` no fim do
        # script nao existisse, dot-sourcing sem -TaskKey teria disparado
        # Write-Error + exit -- o fato de chegarmos ate aqui sem excecao ja
        # prova o comportamento; a asserção abaixo so' formaliza.
        { . $script } | Should Not Throw
    }

    It "nunca passa -SimulateStopProcessFailure ao run_with_lock.ps1 (flag e' SOMENTE para testes)" {
        $source = Get-Content $script -Raw
        $source | Should Not Match "SimulateStopProcessFailure"
    }
}

Describe "run_task.ps1 (execucao direta, so' validando o roteamento de erro)" {

    It "recusa rodar sem -TaskKey e sai com codigo diferente de zero" {
        & powershell -NoProfile -NonInteractive -File $script 2>$null | Out-Null
        $LASTEXITCODE | Should Not Be 0
    }

    It "recusa uma -TaskKey desconhecida e sai com codigo diferente de zero" {
        & powershell -NoProfile -NonInteractive -File $script -TaskKey "chave_invalida" 2>$null | Out-Null
        $LASTEXITCODE | Should Not Be 0
    }
}

# ---------------------------------------------------------------------------
# Gate B6.1d — Invoke-ResolvedTask preserva argumentos "--flag" (ex.:
# --pipeline) ate' o comando real, atraves de dot-source em processo de
# run_with_lock.ps1 (nunca mais um `powershell -File run_with_lock.ps1 ...`
# aninhado). Reproduz o bug real do Gate B6.1c (--pipeline full_daily
# silenciosamente consumido como o CommonParameter -PipelineVariable) com um
# arg-dumper TEMPORARIO no lugar do Python/orchestrate real -- nenhum
# pipeline real, nenhum banco tocado, nenhum arquivo permanente criado no
# repo (tudo em $env:TEMP, removido no AfterAll).
# ---------------------------------------------------------------------------

Describe "run_task.ps1 (Gate B6.1d - Invoke-ResolvedTask preserva argumentos --flag)" {

    $lockScript = Join-Path $PSScriptRoot "run_with_lock.ps1"
    $harnessDir = Join-Path $env:TEMP "run_task_b6_1d_tests"
    # Limpa qualquer residuo de uma execucao anterior (idempotente) antes de
    # recriar -- nao usa AfterAll de proposito (Pester 3.4 nao garante que
    # variaveis definidas direto no corpo do Describe sejam visiveis la',
    # ja observado nesta mesma revisao). O diretorio fica em $env:TEMP,
    # fora do repo, sem custo de limpeza real.
    Remove-Item $harnessDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $harnessDir | Out-Null

    # arg-dumper: script SEM param() proprio -- tudo que receber vira $args,
    # sem nenhum CommonParameter/binding especial no meio do caminho.
    $argDumper = Join-Path $harnessDir "argdump.ps1"
    Set-Content -Path $argDumper -Encoding utf8 -Value @'
$args | ForEach-Object { "[$_]" } | Out-File -FilePath $env:B6_1D_DUMP_FILE -Encoding utf8
'@

    function New-B61dHarness {
        # Gera um harness SEM NENHUM parametro de CLI proprio -- todos os
        # valores (RunTaskScript/LockScript/PythonExe/ModuleArgs/etc) vem
        # EMBUTIDOS como literais PowerShell no corpo do arquivo gerado
        # (aspas simples escapadas dobrando ''), nao como argumentos de
        # linha de comando. Isso evita deliberadamente a MESMA classe de
        # risco que este Gate corrige na producao: um harness de teste com
        # parametros CLI proprios estaria sujeito a re-tokenizacao ao ser
        # invocado via `-File` a partir do processo do Pester, exatamente
        # como o bug original. Invocar o harness gerado (`& powershell -File
        # harness.ps1`, SEM nenhum argumento extra) elimina esse risco por
        # completo para a propria infraestrutura de teste.
        param(
            [Parameter(Mandatory = $true)][string]$Path,
            [Parameter(Mandatory = $true)][string]$RunTaskScript,
            [Parameter(Mandatory = $true)][string]$LockScript,
            [Parameter(Mandatory = $true)][string]$PythonExe,
            [Parameter(Mandatory = $true)][string[]]$ModuleArgs,
            [Parameter(Mandatory = $true)][string]$WorkingDirectory,
            [Parameter(Mandatory = $true)][string]$LockName,
            [int]$TimeoutSeconds = 30
        )
        function Esc([string]$s) { $s -replace "'", "''" }
        $moduleArgsLiteral = ($ModuleArgs | ForEach-Object { "'$(Esc $_)'" }) -join ", "
        $content = @"
. '$(Esc $RunTaskScript)'
`$invocation = [PSCustomObject]@{
    LockScript       = '$(Esc $LockScript)'
    LockName         = '$(Esc $LockName)'
    TimeoutSeconds   = $TimeoutSeconds
    WorkingDirectory = '$(Esc $WorkingDirectory)'
    PythonExe        = '$(Esc $PythonExe)'
    ModuleArgs       = @($moduleArgsLiteral)
}
Invoke-ResolvedTask -Invocation `$invocation
"@
        Set-Content -Path $Path -Value $content -Encoding utf8
    }

    It "reproduz o bug real e confirma o fix: --pipeline full_daily sobrevive ate' o comando" {
        $dumpFile = Join-Path $harnessDir "dumped_pipeline.txt"
        Remove-Item $dumpFile -Force -ErrorAction SilentlyContinue
        $harness = Join-Path $harnessDir "harness_pipeline.ps1"
        New-B61dHarness -Path $harness -RunTaskScript $script -LockScript $lockScript -PythonExe "powershell.exe" `
            -ModuleArgs @("-NoProfile", "-File", $argDumper, "-m", "pipelines.ops.orchestrate", "--pipeline", "full_daily") `
            -WorkingDirectory $repoRoot -LockName "b6_1d_pipeline_test" -TimeoutSeconds 30

        $env:B6_1D_DUMP_FILE = $dumpFile
        try {
            & powershell -NoProfile -NonInteractive -File $harness 2>$null | Out-Null
        } finally {
            Remove-Item Env:\B6_1D_DUMP_FILE -ErrorAction SilentlyContinue
        }

        (Test-Path $dumpFile) | Should Be $true
        # Should Contain (Pester 3.4.0) tem um bug conhecido nesta maquina
        # com arrays multi-elemento vindos de Get-Content ("parametro
        # -Encoding nao encontrado") -- usa -contains nativo do PowerShell
        # em vez da asserção Contain do Pester para sidestepar isso.
        $dumped = Get-Content $dumpFile
        ($dumped -contains "[-m]") | Should Be $true
        ($dumped -contains "[pipelines.ops.orchestrate]") | Should Be $true
        ($dumped -contains "[--pipeline]") | Should Be $true
        ($dumped -contains "[full_daily]") | Should Be $true
        (Test-Path (Join-Path $repoRoot "logs\b6_1d_pipeline_test.lock")) | Should Be $false
    }

    It "-WorkingDirectory com path contendo espaco continua funcionando atraves de Invoke-ResolvedTask" {
        # NOTA: um path COM ESPACO como elemento de -ModuleArgs (ex.: um
        # -File "<path com espaco>\script.ps1" dentro do array de
        # argumentos do comando) esbarra numa limitacao PRE-EXISTENTE e
        # SEPARADA de run_with_lock.ps1 -- Start-Process -ArgumentList (Windows
        # PowerShell 5.1) nao adiciona aspas automaticamente em torno de
        # elementos do array que contem espaco, entao esse elemento especifico
        # quebra em 2 tokens. Confirmado nesta revisao que isso e' PRE-
        # EXISTENTE (reproduz identico chamando run_with_lock.ps1 direto,
        # sem passar por Invoke-ResolvedTask) e NAO afeta o full_daily real
        # (nenhum path/arg de full_daily tem espaco: apps\api\.venv\Scripts\
        # python.exe, -m pipelines.ops.orchestrate --pipeline full_daily) --
        # fica documentado aqui como limitacao conhecida, fora do escopo
        # deste Gate (que corrige especificamente a colisao --pipeline vs.
        # -PipelineVariable). -WorkingDirectory, por outro lado, e' um
        # parametro NOMEADO proprio de Start-Process (nunca faz parte do
        # array de -ArgumentList), e' testado aqui com um path com espaco de
        # verdade.
        $spacedWorkDir = Join-Path $harnessDir "workdir com espaco"
        New-Item -ItemType Directory -Force -Path $spacedWorkDir | Out-Null

        $harness = Join-Path $harnessDir "harness_spaced_workdir.ps1"
        New-B61dHarness -Path $harness -RunTaskScript $script -LockScript $lockScript -PythonExe "cmd.exe" `
            -ModuleArgs @("/c", "cd") -WorkingDirectory $spacedWorkDir -LockName "b6_1d_spaced_workdir_test" -TimeoutSeconds 30

        $wrapperOut = Join-Path $harnessDir "wrapper_spaced_workdir_stdout.txt"
        Remove-Item $wrapperOut -Force -ErrorAction SilentlyContinue
        $proc = Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile", "-NonInteractive", "-File", $harness) `
            -NoNewWindow -PassThru -RedirectStandardOutput $wrapperOut
        $null = $proc.Handle
        $proc.WaitForExit(30000) | Should Be $true

        $statusLine = ((Get-Content $wrapperOut) | Where-Object { $_ -match "^STATUS=" }) -join " "
        $statusLine | Should Match "WORKDIR=$([regex]::Escape($spacedWorkDir))"
        $statusLine | Should Match "EXITCODE=0"
    }

    It "ainda aplica -WorkingDirectory corretamente atraves de Invoke-ResolvedTask" {
        $harness = Join-Path $harnessDir "harness_workdir.ps1"
        New-B61dHarness -Path $harness -RunTaskScript $script -LockScript $lockScript -PythonExe "cmd.exe" `
            -ModuleArgs @("/c", "cd") -WorkingDirectory $repoRoot -LockName "b6_1d_workdir_test" -TimeoutSeconds 30

        $wrapperOut = Join-Path $harnessDir "wrapper_workdir_stdout.txt"
        Remove-Item $wrapperOut -Force -ErrorAction SilentlyContinue
        $proc = Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile", "-NonInteractive", "-File", $harness) `
            -NoNewWindow -PassThru -RedirectStandardOutput $wrapperOut
        $null = $proc.Handle
        $proc.WaitForExit(30000) | Should Be $true

        $statusLine = ((Get-Content $wrapperOut) | Where-Object { $_ -match "^STATUS=" }) -join " "
        $statusLine | Should Match "WORKDIR=$([regex]::Escape($repoRoot))"
        $statusLine | Should Match "EXITCODE=0"
    }

    It "lock e timeout continuam funcionando atraves de Invoke-ResolvedTask (timeout real -> BLOCKED/124)" {
        $harness = Join-Path $harnessDir "harness_timeout.ps1"
        New-B61dHarness -Path $harness -RunTaskScript $script -LockScript $lockScript -PythonExe "powershell.exe" `
            -ModuleArgs @("-Command", "Start-Sleep -Seconds 20") -WorkingDirectory $repoRoot -LockName "b6_1d_timeout_test" -TimeoutSeconds 2

        $wrapperOut = Join-Path $harnessDir "wrapper_timeout_stdout.txt"
        Remove-Item $wrapperOut -Force -ErrorAction SilentlyContinue
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $proc = Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile", "-NonInteractive", "-File", $harness) `
            -NoNewWindow -PassThru -RedirectStandardOutput $wrapperOut
        $null = $proc.Handle
        $proc.WaitForExit(15000) | Should Be $true
        $sw.Stop()

        $statusLine = ((Get-Content $wrapperOut) | Where-Object { $_ -match "^STATUS=" }) -join " "
        $statusLine | Should Match "STATUS=BLOCKED EXITCODE=124"
        ($sw.Elapsed.TotalSeconds -lt 10) | Should Be $true
        (Test-Path (Join-Path $repoRoot "logs\b6_1d_timeout_test.lock")) | Should Be $false
    }

    It "nunca usa -SimulateStopProcessFailure em Invoke-ResolvedTask (flag e' SOMENTE para testes de run_with_lock.ps1)" {
        $source = Get-Content $script -Raw
        $invokeResolvedTaskBody = $source.Substring($source.IndexOf("function Invoke-ResolvedTask"))
        $invokeResolvedTaskBody = $invokeResolvedTaskBody.Substring(0, $invokeResolvedTaskBody.IndexOf("`n}`n"))
        $invokeResolvedTaskBody | Should Not Match "SimulateStopProcessFailure"
    }
}
