# Testes Pester (v3, ja disponivel no Windows PowerShell 5.1 -- nenhuma
# dependencia nova instalada) de scripts\run_with_lock.ps1.
#
# Usa apenas cmd.exe/powershell.exe com comandos inofensivos (exit N,
# Start-Sleep, cd) -- nunca chama nenhum pipeline real, nunca toca em banco.
#
# Cobre, formalizando o que foi validado manualmente durante a correcao
# desta revisao:
#   - lock atomico baseado em PID vivo/morto (sem StaleLockMinutes por
#     idade -- um lock so' e' recuperado se o processo dono nao existe mais,
#     nunca so' porque "ja faz tempo");
#   - concorrencia real: de duas tentativas simultaneas, exatamente uma
#     vence;
#   - WorkingDirectory sempre correto (default = raiz do repo), mesmo
#     quando o processo que chama este script esta posicionado em
#     C:\Windows\System32 (o "Start in" tipico e vazio do Task Scheduler).
#
# Uso:
#   Invoke-Pester scripts\run_with_lock.tests.ps1

$repoRoot = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "run_with_lock.ps1"
$logDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function New-DeadPid {
    # Um PID quase certamente morto: gera um numero alto e confirma que
    # Get-Process falha para ele antes de usar no teste.
    for ($candidate = 999900; $candidate -lt 999999; $candidate++) {
        if (-not (Get-Process -Id $candidate -ErrorAction SilentlyContinue)) {
            return $candidate
        }
    }
    throw "nao foi possivel achar um PID livre para simular processo morto"
}

Describe "run_with_lock.ps1" {

    It "propaga exit code 0 quando o comando tem sucesso" {
        & powershell -File $script pestertest_ok -TimeoutSeconds 30 cmd.exe /c "exit 0" | Out-Null
        $LASTEXITCODE | Should Be 0
    }

    It "propaga exit code diferente de zero quando o comando falha" {
        & powershell -File $script pestertest_fail -TimeoutSeconds 30 cmd.exe /c "exit 7" | Out-Null
        $LASTEXITCODE | Should Be 7
    }

    It "usa a raiz do repo como WorkingDirectory por padrao" {
        $out = & powershell -File $script pestertest_cwd_default -TimeoutSeconds 30 cmd.exe /c "cd"
        $statusLine = ($out | Where-Object { $_ -match "^STATUS=" }) -join " "
        $statusLine | Should Match "WORKDIR=$([regex]::Escape($repoRoot))"
        $logMatch = [regex]::Match($statusLine, "STDOUT_LOG=(\S+)")
        $logMatch.Success | Should Be $true
        (Get-Content $logMatch.Groups[1].Value -Raw).Trim() | Should Be $repoRoot
    }

    It "respeita -WorkingDirectory explicito mesmo quando o CHAMADOR esta em C:\Windows\System32" {
        # Simula o cenario real do Task Scheduler: "Start in" nao configurado
        # (ou vazio) faz o processo pai iniciar em System32. O script tem que
        # ignorar esse cwd herdado e usar -WorkingDirectory / seu default.
        $wrapperOut = Join-Path $logDir "pestertest_system32_wrapper_stdout.txt"
        Remove-Item $wrapperOut -Force -ErrorAction SilentlyContinue
        $proc = Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile", "-NonInteractive", "-File", $script, "pestertest_system32", "-TimeoutSeconds", "30", "cmd.exe", "/c", "cd") `
            -WorkingDirectory "C:\Windows\System32" `
            -NoNewWindow -PassThru -RedirectStandardOutput $wrapperOut
        $null = $proc.Handle
        $proc.WaitForExit(30000) | Should Be $true

        $statusLine = ((Get-Content $wrapperOut) | Where-Object { $_ -match "^STATUS=" }) -join " "
        $statusLine | Should Match "WORKDIR=$([regex]::Escape($repoRoot))"
        $logMatch = [regex]::Match($statusLine, "STDOUT_LOG=(\S+)")
        (Get-Content $logMatch.Groups[1].Value -Raw).Trim() | Should Be $repoRoot
        Remove-Item $wrapperOut -Force -ErrorAction SilentlyContinue
    }

    It "bloqueia quando o dono do lock ainda esta vivo, mesmo que o lock seja antigo" {
        # Prova que nao existe mais liberacao por idade: o PID gravado e' o
        # deste proprio processo de teste (garantidamente vivo), com data de
        # modificacao artificialmente antiga.
        $lockFile = Join-Path $logDir "pestertest_alive_owner.lock"
        Set-Content -Path $lockFile -Value ([string]$PID) -Encoding ascii -NoNewline
        (Get-Item $lockFile).LastWriteTime = (Get-Date).AddDays(-30)

        & powershell -File $script pestertest_alive_owner -TimeoutSeconds 30 cmd.exe /c "exit 0" 2>$null | Out-Null
        $exitCode = $LASTEXITCODE
        $stillThere = Test-Path $lockFile

        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
        $exitCode | Should Be 1
        $stillThere | Should Be $true
    }

    It "recupera um lock cujo dono nao esta mais rodando" {
        $deadPid = New-DeadPid
        $lockFile = Join-Path $logDir "pestertest_dead_owner.lock"
        Set-Content -Path $lockFile -Value ([string]$deadPid) -Encoding ascii -NoNewline

        & powershell -File $script pestertest_dead_owner -TimeoutSeconds 30 cmd.exe /c "exit 0" 2>$null | Out-Null
        $LASTEXITCODE | Should Be 0
    }

    It "de duas tentativas simultaneas pelo mesmo lock, exatamente uma vence" {
        $lockName = "pestertest_concurrency"
        Remove-Item (Join-Path $logDir "$lockName.lock") -Force -ErrorAction SilentlyContinue

        $job1 = Start-Job -ScriptBlock {
            param($script, $lockName)
            & powershell -File $script $lockName -TimeoutSeconds 30 powershell.exe -Command "Start-Sleep -Seconds 4" 2>$null
            $LASTEXITCODE
        } -ArgumentList $script, $lockName

        Start-Sleep -Milliseconds 800
        & powershell -File $script $lockName -TimeoutSeconds 30 cmd.exe /c "exit 0" 2>$null | Out-Null
        $secondExit = $LASTEXITCODE

        $firstExit = Receive-Job -Job $job1 -Wait
        Remove-Job -Job $job1 -Force

        $secondExit | Should Be 1
        ($firstExit | Select-Object -Last 1) | Should Be 0
    }

    It "recusa executar quando um argumento parece conter credenciais embutidas" {
        & powershell -File $script pestertest_cred -TimeoutSeconds 30 cmd.exe "postgresql://user:senha@host/db" 2>$null | Out-Null
        $exitCode = $LASTEXITCODE
        $lockLeftover = Join-Path $logDir "pestertest_cred.lock"
        $exitCode | Should Be 1
        (Test-Path $lockLeftover) | Should Be $false
    }

    It "mata o processo e retorna 124 quando o timeout estoura" {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        & powershell -File $script pestertest_timeout -TimeoutSeconds 2 powershell.exe -Command "Start-Sleep -Seconds 20" 2>$null | Out-Null
        $sw.Stop()
        $LASTEXITCODE | Should Be 124
        ($sw.Elapsed.TotalSeconds -lt 15) | Should Be $true
    }

    It "aguarda confirmacao real de termino do processo (nao so' dispara Stop-Process) antes de liberar o lock" {
        # Marca o processo filho com um argumento unico para conseguir
        # confirmar, de FORA do script, que ele realmente nao existe mais
        # no momento em que run_with_lock.ps1 retorna -- prova que o
        # script esperou a morte de verdade, nao so' chamou Stop-Process e
        # seguiu em frente otimisticamente.
        $marker = "pestertest_killwait_$([guid]::NewGuid().ToString('N').Substring(0, 8))"
        $lockFile = Join-Path $logDir "pestertest_killwait.lock"
        & powershell -File $script pestertest_killwait -TimeoutSeconds 2 powershell.exe -Command "Start-Sleep -Seconds 20 # $marker" 2>$null | Out-Null
        $LASTEXITCODE | Should Be 124

        $stillRunning = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*$marker*" }
        $stillRunning | Should Be $null

        # Filho terminou de verdade dentro da espera -> o lock TEM que ser
        # removido (distinto do cenario "filho sobrevive", testado abaixo,
        # onde o lock precisa ser preservado).
        (Test-Path $lockFile) | Should Be $false
    }

    It "troca o PID do lock do wrapper para o do processo filho logo apos inicia-lo" {
        # Enquanto o filho ainda esta rodando, o PID gravado no lock tem
        # que corresponder a ELE (Start-Sleep), nunca ao wrapper
        # (run_with_lock.ps1) -- confirma a troca atomica de dono do lock,
        # nao so' que "algum PID vivo" esteja la'.
        $lockName = "pestertest_pid_swap"
        $lockFile = Join-Path $logDir "$lockName.lock"
        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue

        $job = Start-Job -ScriptBlock {
            param($script, $lockName)
            & powershell -File $script $lockName -TimeoutSeconds 30 powershell.exe -Command "Start-Sleep -Seconds 3" 2>$null
            $LASTEXITCODE
        } -ArgumentList $script, $lockName

        Start-Sleep -Milliseconds 1200
        (Test-Path $lockFile) | Should Be $true
        $lockPidDuring = [int](Get-Content $lockFile -Raw).Trim()
        (Get-Process -Id $lockPidDuring -ErrorAction SilentlyContinue) | Should Not Be $null

        $ownerCmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $lockPidDuring" -ErrorAction SilentlyContinue).CommandLine
        $ownerCmdLine | Should Match "Start-Sleep"
        $ownerCmdLine | Should Not Match "run_with_lock\.ps1"

        Receive-Job -Job $job -Wait | Out-Null
        Remove-Job -Job $job -Force
        (Test-Path $lockFile) | Should Be $false
    }

    It "filho sobrevive ao Stop-Process -> lock preservado com o PID do filho, bloqueia nova tentativa, e e' recuperado quando o filho morre" {
        $lockName = "pestertest_survives_kill"
        $lockFile = Join-Path $logDir "$lockName.lock"
        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue

        $marker = "pestertest_survives_$([guid]::NewGuid().ToString('N').Substring(0, 8))"
        $wrapperOut = Join-Path $logDir "pestertest_survives_wrapper_stdout.txt"
        Remove-Item $wrapperOut -Force -ErrorAction SilentlyContinue

        # -SimulateStopProcessFailure (SOMENTE TESTES) pula o Stop-Process
        # real de proposito, para exercitar de forma segura e
        # deterministica o ramo "o filho continua vivo apos a espera" --
        # um processo Windows normal nao tem como ser fabricado para
        # sobreviver de verdade a um Stop-Process -Force sem recorrer a
        # processos protegidos do sistema (inseguro/inadequado num teste).
        #
        # O filho dorme 40s (mais que os 30s do kill-wait) de proposito:
        # como o Stop-Process real e' pulado, ele so' morre sozinho apos
        # os 40s completos -- exatamente o que permite observar o ramo
        # "ainda vivo apos os 30s de espera" de forma real, sem inventar
        # nada sobre o processo em si (ele e' um Start-Sleep comum).
        $wrapperProc = Start-Process -FilePath "powershell.exe" `
            -ArgumentList @("-NoProfile", "-NonInteractive", "-File", $script, $lockName, "-TimeoutSeconds", "1", "-SimulateStopProcessFailure", "powershell.exe", "-Command", "Start-Sleep -Seconds 40 # $marker") `
            -NoNewWindow -PassThru -RedirectStandardOutput $wrapperOut
        $null = $wrapperProc.Handle
        $wrapperProc.WaitForExit(45000) | Should Be $true

        # 1) BLOCKED/124, e o lock NAO foi removido
        $statusLine = ((Get-Content $wrapperOut) | Where-Object { $_ -match "^STATUS=" }) -join " "
        $statusLine | Should Match "STATUS=BLOCKED EXITCODE=124"
        (Test-Path $lockFile) | Should Be $true

        # 2) o lock contem o PID do FILHO (nao do wrapper, que ja terminou)
        # -- o filho real ainda esta vivo neste momento
        $childProcEntry = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*$marker*" }
        $childProcEntry | Should Not Be $null
        $lockPid = [int](Get-Content $lockFile -Raw).Trim()
        $lockPid | Should Be $childProcEntry.ProcessId

        # 3) nova tentativa pelo mesmo lock, com o filho ainda vivo -> BLOCKED
        & powershell -File $script $lockName -TimeoutSeconds 30 cmd.exe /c "exit 0" 2>$null | Out-Null
        $LASTEXITCODE | Should Be 1
        (Test-Path $lockFile) | Should Be $true

        # 4) espera o filho realmente terminar (fim natural do Start-Sleep
        # -Seconds 40 original) e confirma que a PROXIMA tentativa RECUPERA
        # o lock normalmente
        $deadline = (Get-Date).AddSeconds(25)
        while ((Get-Process -Id $childProcEntry.ProcessId -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 200
        }
        (Get-Process -Id $childProcEntry.ProcessId -ErrorAction SilentlyContinue) | Should Be $null

        & powershell -File $script $lockName -TimeoutSeconds 30 cmd.exe /c "exit 0" | Out-Null
        $LASTEXITCODE | Should Be 0
        (Test-Path $lockFile) | Should Be $false

        Remove-Item $wrapperOut -Force -ErrorAction SilentlyContinue
    }

    It "rejeita LockName com sequencia de path traversal ('..')" {
        & powershell -File $script "..\..\malicious" -TimeoutSeconds 30 cmd.exe /c "exit 0" 2>$null | Out-Null
        $LASTEXITCODE | Should Be 1
    }

    It "rejeita LockName com barra (nao pode virar subcaminho)" {
        & powershell -File $script "a/b" -TimeoutSeconds 30 cmd.exe /c "exit 0" 2>$null | Out-Null
        $LASTEXITCODE | Should Be 1
    }

    It "aceita LockName so' com letras, numeros, underscore e hifen" {
        & powershell -File $script "pestertest-lock_123" -TimeoutSeconds 30 cmd.exe /c "exit 0" | Out-Null
        $LASTEXITCODE | Should Be 0
    }

    It "nao deixa nenhum arquivo de lock para tras apos qualquer execucao (sucesso)" {
        & powershell -File $script pestertest_cleanup_ok -TimeoutSeconds 30 cmd.exe /c "exit 0" | Out-Null
        (Test-Path (Join-Path $logDir "pestertest_cleanup_ok.lock")) | Should Be $false
    }

    It "grava stdout e stderr em arquivos separados" {
        $out = & powershell -File $script pestertest_logs -TimeoutSeconds 30 cmd.exe /c "echo linha-stdout & echo linha-stderr 1>&2"
        $statusLine = $out | Where-Object { $_ -match "^STATUS=" }
        $stdoutMatch = [regex]::Match($statusLine, "STDOUT_LOG=(\S+)")
        $stderrMatch = [regex]::Match($statusLine, "STDERR_LOG=(\S+)")
        $stdoutMatch.Groups[1].Value | Should Not Be $stderrMatch.Groups[1].Value
        (Get-Content $stdoutMatch.Groups[1].Value -Raw) | Should Match "linha-stdout"
        (Get-Content $stderrMatch.Groups[1].Value -Raw) | Should Match "linha-stderr"
    }
}
