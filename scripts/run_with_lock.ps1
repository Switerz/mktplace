# run_with_lock.ps1 - evita duas execucoes simultaneas da mesma fonte de
# sync, aplica timeout, recupera lock de processo morto, forca o working
# directory do processo filho, e grava stdout/stderr em logs separados.
#
# Uso:
#   powershell -File scripts\run_with_lock.ps1 <nome-lock> [-TimeoutSeconds N] [-WorkingDirectory DIR] <executavel> [args...]
#
# Exemplo:
#   powershell -File scripts\run_with_lock.ps1 daily_ml -TimeoutSeconds 900 `
#     "C:\Users\Notebook\Desktop\mktplace\apps\api\.venv\Scripts\python.exe" `
#     -m pipelines.ingestion.daily_performance --source ml --mode incremental
#
# Comportamento:
#   - LOCK ATOMICO: cria logs\<nome-lock>.lock com [System.IO.File]::Open(...,
#     FileMode.CreateNew, ..., FileShare.None) — uma UNICA chamada de SO que
#     falha com IOException se o arquivo ja existir. Isso elimina a corrida
#     entre "checar se existe" e "criar" (Test-Path + Set-Content antigos
#     tinham essa janela); entre duas tentativas simultaneas, exatamente uma
#     vence.
#   - O lock grava, inicialmente, o PID do processo deste script — e'
#     ATUALIZADO para o PID do processo FILHO assim que ele e' iniciado
#     (troca atomica via File.Replace, sem janela em que o lock fique
#     ausente/vazio/ilegivel). Se a criacao falhar porque o lock ja
#     existe, le o PID gravado (wrapper ou filho, dependendo do momento)
#     e checa com Get-Process se ele ainda esta vivo:
#       * vivo  -> BLOCKED (exit 1) SEMPRE, nao importa ha quanto tempo o
#         lock existe. Um lock nunca pode liberar uma execucao que ainda
#         esta rodando de verdade.
#       * morto (processo nao existe mais - crash, maquina reiniciada, ou
#         conteudo ilegivel) -> trata como execucao perdida, remove o lock
#         orfao e tenta adquirir de novo UMA vez; se a segunda tentativa
#         tambem falhar (outra execucao venceu a corrida de recuperacao),
#         BLOCKED sem prosseguir.
#   - -WorkingDirectory (default: raiz do repositorio, calculada a partir
#     deste script) e' sempre passado a Start-Process -WorkingDirectory —
#     garante que o processo filho roda com o diretorio de trabalho correto
#     independente de onde o Task Scheduler (ou quem chamou este script)
#     estava posicionado (ex.: Task Scheduler sem "Start in" configurado
#     costuma usar C:\Windows\System32).
#   - Aplica -TimeoutSeconds (default 3600) ao processo filho; se estourar,
#     mata o processo (Stop-Process -Force) e AGUARDA ate 30s a confirmacao
#     real de que ele terminou (Get-Process deixa de encontra-lo):
#       * se o filho realmente morreu dentro desses 30s -> o lock E'
#         REMOVIDO normalmente;
#       * se o filho SOBREVIVE aos 30s de espera -> o lock **NAO** e'
#         removido (fica com o PID do filho, ainda vivo) — uma nova
#         tentativa vai encontrar esse PID vivo e ficar BLOCKED, em vez de
#         "recuperar" o lock e rodar uma segunda execucao ao lado de um
#         processo zumbi. So' quando esse PID finalmente morrer (fora
#         desta execucao) e' que uma tentativa futura recupera o lock.
#     Em ambos os casos reporta STATUS=BLOCKED e exit code 124 (convencao
#     Unix para timeout). Nunca tenta de novo sozinho.
#   - LockName e' validado contra `^[A-Za-z0-9_-]+$` ANTES de qualquer
#     acesso a disco — protege contra path traversal (".."/"/") no nome
#     do arquivo de lock.
#   - Grava stdout e stderr em arquivos SEPARADOS e datados:
#     logs\<nome-lock>_<yyyyMMdd_HHmmss>_stdout.log e ..._stderr.log.
#   - Imprime uma linha final "STATUS=SUCCESS|FAILED|BLOCKED ..." para grep
#     externo, alem de propagar o exit code do comando real.
#   - Nunca imprime credenciais: recusa rodar se algum argumento parecer
#     uma connection string com usuario:senha embutidos (defesa em
#     profundidade — os scripts desta automacao sempre leem credenciais de
#     variavel de ambiente, nunca de argumento).
#
# Politica de retencao de logs: nao ha limpeza automatica aqui (mantido
# simples de proposito). Ver docs/runbook_sync_produtos.md para o comando
# manual/periodico recomendado de retencao.

param(
    [Parameter(Mandatory = $true, Position = 0)][string]$LockName,
    [int]$TimeoutSeconds = 3600,
    [string]$WorkingDirectory = "",
    # SOMENTE PARA TESTES AUTOMATIZADOS (Pester) - nunca usar em producao,
    # nunca referenciado por run_task.ps1/schedule_plan.py. Pula a chamada
    # real a Stop-Process no ramo de timeout, para permitir testar de
    # forma segura e deterministica o comportamento de "o processo filho
    # continua vivo apos a tentativa de mata-lo" (um processo Windows
    # normal nao tem como ser fabricado para sobreviver de verdade a um
    # Stop-Process -Force sem recorrer a processos protegidos do sistema,
    # o que seria inseguro/inadequado num teste automatizado).
    [switch]$SimulateStopProcessFailure,
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)][string[]]$Cmd
)

$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($WorkingDirectory)) {
    $WorkingDirectory = $repoRoot
}

# LockName vira diretamente parte de um caminho de arquivo
# (logs\<LockName>.lock) - validar ANTES de tocar no sistema de arquivos
# protege contra path traversal (".." / "/" / "\") que apontaria o lock
# para fora de logs\, seja por erro de configuracao ou entrada inesperada.
if ($LockName -notmatch '^[A-Za-z0-9_-]+$') {
    Write-Error "LockName invalido: '$LockName'. So' letras, numeros, underscore e hifen sao permitidos."
    Write-Output "STATUS=BLOCKED"
    exit 1
}

$logDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$lockFile = Join-Path $logDir "$LockName.lock"

foreach ($arg in $Cmd) {
    if ($arg -match '://[^/\s@]+:[^/\s@]+@') {
        Write-Error "Argumento parece conter uma connection string com credenciais embutidas - abortando sem executar. Use variaveis de ambiente, nunca argumentos de linha de comando."
        Write-Output "STATUS=BLOCKED"
        exit 1
    }
}

function Try-AcquireLock {
    param([string]$Path)
    try {
        # FileMode.CreateNew + FileShare.None: uma unica chamada atomica de
        # SO. Lanca IOException se o arquivo ja existir - sem essa
        # exclusividade, duas execucoes poderiam ambas "ver" ausencia de
        # lock antes de qualquer uma escrever (a corrida que este design
        # elimina).
        $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $sw = New-Object System.IO.StreamWriter($fs)
        $sw.Write([string]$PID)
        $sw.Flush()
        $sw.Close()
        $fs.Close()
        return $true
    }
    catch [System.IO.IOException] {
        return $false
    }
}

function Test-LockOwnerAlive {
    param([string]$Path)
    try {
        $content = (Get-Content -Path $Path -ErrorAction Stop -Raw).Trim()
        $ownerPid = [int]$content
        $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
        return [bool]$proc
    }
    catch {
        # Conteudo ilegivel/arquivo sumiu no meio da leitura -> nao da para
        # confirmar que alguem esta vivo; trata como morto (seguro
        # recuperar), nunca como vivo (o que travaria o lock para sempre).
        return $false
    }
}

function Update-LockOwnerPid {
    # Troca o PID gravado no lock (inicialmente o do proprio wrapper) pelo
    # PID do processo FILHO, assim que ele existe - a partir da', o lock
    # reflete o dono real do trabalho, nao so' o wrapper que espera por
    # ele. Isso importa porque o wrapper fica vivo (bloqueado em
    # WaitForExit) durante toda a espera, mas se o filho sobreviver a um
    # Stop-Process e o lock NAO for removido (ver mais abaixo), a proxima
    # tentativa so' consegue detectar corretamente "ainda em execucao" se
    # o lock apontar para o PID que de fato continua vivo (o filho) - nao
    # para o PID do wrapper, que vai morrer quando este script terminar.
    #
    # Troca ATOMICA via escrever num arquivo temporario e substituir com
    # [System.IO.File]::Replace (rename atomico ao nivel do sistema de
    # arquivos): o caminho do lock nunca fica ausente, vazio ou truncado
    # em nenhum instante observavel. Isso e' o que evita a corrida pedida
    # explicitamente: um leitor concorrente (outra tentativa cujo
    # Try-AcquireLock falhou e que agora chama Test-LockOwnerAlive) sempre
    # ve OU o conteudo antigo (PID do wrapper, ainda vivo nesse instante)
    # OU o novo (PID do filho) - nunca um estado intermediario que
    # pudesse ser mal interpretado como "lock morto" e abrir uma janela
    # para essa outra execucao adquirir o mesmo lock.
    param([string]$Path, [int]$NewOwnerPid)
    $tempPath = "$Path.$NewOwnerPid.tmp"
    $fs = [System.IO.File]::Open($tempPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    $sw = New-Object System.IO.StreamWriter($fs)
    $sw.Write([string]$NewOwnerPid)
    $sw.Flush()
    $sw.Close()
    $fs.Close()
    # File.Replace(source, destination, $null) lanca "O caminho tem um
    # formato invalido" no Windows PowerShell 5.1 (o $null nao chega como
    # null de verdade nesse binding) - passar um caminho de backup real e
    # apaga-lo em seguida e' o jeito que funciona de verdade nesta versao,
    # descoberto empiricamente ao escrever os testes desta correcao.
    $backupPath = "$Path.bak"
    [System.IO.File]::Replace($tempPath, $Path, $backupPath)
    Remove-Item $backupPath -Force -ErrorAction SilentlyContinue
}

$acquired = Try-AcquireLock -Path $lockFile
if (-not $acquired) {
    if (Test-LockOwnerAlive -Path $lockFile) {
        Write-Error "Lock '$LockName' pertence a um processo ainda em execucao - outra execucao esta em andamento. Abortando sem rodar $($Cmd[0])."
        Write-Output "STATUS=BLOCKED"
        exit 1
    }
    Write-Warning "Lock '$LockName' pertence a um processo que nao esta mais rodando - tratando como execucao perdida e recuperando."
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    $acquired = Try-AcquireLock -Path $lockFile
    if (-not $acquired) {
        Write-Error "Outra execucao venceu a corrida de recuperacao do lock '$LockName' - abortando sem tentar de novo."
        Write-Output "STATUS=BLOCKED"
        exit 1
    }
}

$tag = (Get-Date).ToString("yyyyMMdd_HHmmss")
$stdoutLog = Join-Path $logDir "$($LockName)_$($tag)_stdout.log"
$stderrLog = Join-Path $logDir "$($LockName)_$($tag)_stderr.log"

$status = "FAILED"
$exitCode = 1
# Controla se o `finally` remove o lock. Por padrao SIM (execucao concluiu
# normalmente, com ou sem sucesso, ou nem chegou a iniciar o filho) - so'
# vira $false no caso especifico de o processo filho sobreviver ao
# Stop-Process + espera apos um timeout (ver abaixo). Sem essa flag, o
# `finally` removia o lock incondicionalmente mesmo com o filho ainda
# vivo, permitindo que a proxima tentativa "recuperasse" o lock e rodasse
# uma segunda execucao ao lado de um processo zumbi que pode continuar
# escrevendo em banco/arquivo.
$removeLockOnExit = $true
try {
    $startParams = @{
        FilePath                = $Cmd[0]
        NoNewWindow             = $true
        PassThru                = $true
        WorkingDirectory        = $WorkingDirectory
        RedirectStandardOutput  = $stdoutLog
        RedirectStandardError   = $stderrLog
    }
    if ($Cmd.Length -gt 1) {
        $startParams.ArgumentList = $Cmd[1..($Cmd.Length - 1)]
    }

    $proc = Start-Process @startParams
    # Acessar .Handle imediatamente e' necessario para o .NET reter direitos
    # suficientes para ler .ExitCode depois do WaitForExit — sem isso,
    # ExitCode volta vazio mesmo com o processo ja tendo terminado
    # (comportamento conhecido de Start-Process -PassThru no Windows
    # PowerShell 5.1).
    $null = $proc.Handle

    # Troca o dono do lock (wrapper -> filho) ANTES de esperar por ele -
    # a execucao so' e' considerada "plenamente iniciada" depois que o
    # lock ja reflete o PID real que esta fazendo o trabalho. Falha aqui
    # e' tratada como nao-fatal: o lock so' fica com o PID do wrapper
    # (que continua vivo por toda a espera de qualquer forma), nunca pior
    # do que o comportamento anterior a esta correcao.
    #
    # RISCO RESIDUAL CONHECIDO, NAO BLOQUEANTE (documentado, nao corrigido
    # nesta revisao de proposito - corrigir isso seria redesenhar a
    # estrategia de lock, fora do escopo pedido): se Update-LockOwnerPid
    # falhar, o script apenas avisa (Write-Warning) e SEGUE em frente sem
    # abortar a execucao. O lock fica com o PID do wrapper ate o fim desta
    # execucao, o que ainda e' seguro enquanto o wrapper estiver vivo, mas
    # significa que, se o TIMEOUT estourar depois dessa falha, o ramo
    # "filho sobrevive ao Stop-Process" (ver mais abaixo) vai preservar o
    # lock com o PID do WRAPPER (que esta prestes a morrer quando este
    # script terminar), nao o do filho real - reabrindo, so' nesse cenario
    # composto (falha de Update-LockOwnerPid E timeout com filho
    # sobrevivente), a mesma janela que esta correcao existe para fechar.
    # Isso deve ser MONITORADO via grep do texto "Nao foi possivel
    # atualizar o dono do lock" nos logs stderr (`logs\*_stderr.log`) -
    # nenhuma ocorrencia esperada em operacao normal; qualquer ocorrencia
    # merece investigacao manual antes da proxima execucao daquele lock.
    try {
        Update-LockOwnerPid -Path $lockFile -NewOwnerPid $proc.Id
    }
    catch {
        Write-Warning "Nao foi possivel atualizar o dono do lock para o PID do processo filho ($($proc.Id)): $($_.Exception.Message). O lock continua com o PID do wrapper ate o fim desta execucao."
    }

    $finished = $proc.WaitForExit($TimeoutSeconds * 1000)

    if (-not $finished) {
        Write-Error "Timeout de $TimeoutSeconds s atingido - matando processo (PID $($proc.Id))."
        if ($SimulateStopProcessFailure) {
            Write-Warning "SimulateStopProcessFailure ativo (SOMENTE TESTES) - Stop-Process real foi pulado de proposito."
        }
        else {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }

        # Stop-Process nao e' garantidamente sincrono - aguarda a
        # confirmacao REAL de termino antes de decidir o que fazer com o
        # lock. Sem isso, uma nova execucao poderia comecar enquanto o
        # processo "morto" ainda esta finalizando (flush de arquivo,
        # transacao em andamento, conexao de banco ainda aberta),
        # competindo pelos mesmos recursos que a execucao anterior ainda
        # nao largou de verdade.
        $killWaitSeconds = 30
        $killDeadline = (Get-Date).AddSeconds($killWaitSeconds)
        while ((Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) -and (Get-Date) -lt $killDeadline) {
            Start-Sleep -Milliseconds 200
        }

        if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
            # O filho sobreviveu ao Stop-Process e aos 30s de espera - o
            # lock NAO e' removido. Como o lock ja foi trocado para o PID
            # do filho (acima), ele continua contendo um PID
            # verdadeiramente vivo: a PROXIMA tentativa vai encontrar esse
            # PID vivo via Test-LockOwnerAlive e ficar BLOCKED
            # corretamente, em vez de "recuperar" um lock orfao e rodar
            # uma segunda execucao ao lado de um processo zumbi. Quando
            # esse PID finalmente morrer (mais tarde, fora desta
            # execucao), uma tentativa futura vai reconhecer o PID morto
            # e recuperar o lock normalmente.
            Write-Warning "Processo PID $($proc.Id) ainda nao terminou apos $killWaitSeconds s de espera pos-Stop-Process - o LOCK SERA MANTIDO (nao removido), contendo o PID do filho, ate ele realmente terminar."
            $removeLockOnExit = $false
        }

        $status = "BLOCKED"
        $exitCode = 124
    }
    else {
        $exitCode = $proc.ExitCode
        $status = if ($exitCode -eq 0) { "SUCCESS" } else { "FAILED" }
    }
}
catch {
    $errText = $_.Exception.Message
    Write-Error "Erro ao executar $($Cmd[0]): $errText"
    $status = "FAILED"
    $exitCode = 1
}
finally {
    if ($removeLockOnExit) {
        Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
    }
}

Write-Output "STATUS=$status EXITCODE=$exitCode WORKDIR=$WorkingDirectory STDOUT_LOG=$stdoutLog STDERR_LOG=$stderrLog"
exit $exitCode
