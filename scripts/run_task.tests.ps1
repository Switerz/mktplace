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

    It "TimeoutSeconds externo (9000) e' maior que o orcamento somado dos steps internos (6780)" {
        $inv = Resolve-TaskInvocation -TaskKey "full_daily" -RepoRoot $repoRoot -PythonExe "python.exe" -LockScript "lock.ps1"
        $internalBudget = 6780
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
