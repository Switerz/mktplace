"""
Definicao de dados da agenda PROPOSTA de agendamento — NAO EXECUTA NADA.

Este modulo nao importa subprocess, os.system nem qualquer API do Windows
Task Scheduler. Ele so' declara a tarefa proposta (nome, TaskKey, horario)
e sabe RENDERIZAR, como TEXTO para revisao humana:
  - o comando `schtasks /create ...` (simples, mas nao representa todas
    as configuracoes de seguranca desejadas — ver abaixo);
  - a definicao XML equivalente do Task Scheduler, que E' o jeito seguro
    de expressar `MultipleInstancesPolicy`, `StartWhenAvailable` e
    `ExecutionTimeLimit` (schtasks /create simples nao tem flags para
    essas 3 configuracoes).
Nenhuma das duas formas e' executada/importada por este modulo. A
ativacao real (rodar de fato o `schtasks /create` simples ou o
equivalente com `/xml`) e' a Fase 3B, fora do escopo deste modulo, e
exige autorizacao explicita separada. O XML gerado tem `Settings/Enabled
= false` sempre — mesmo depois de importado na Fase 3B, a tarefa fica
registrada mas DESATIVADA ate um passo manual e separado (consultar a
tarefa importada, validar que bate com o revisado aqui, e so' entao
habilitar) — nunca ativa no mesmo passo da importacao.

UMA UNICA tarefa no Task Scheduler (nao 2, nao 8): a dependencia real
entre passos (ex.: monitor do Bug 8 so' depois do sync de Produtos Shopee
TER terminado; health check so' depois de TUDO) e' resolvida DENTRO da
tarefa por `pipelines.ops.orchestrate.PIPELINES["full_daily"]` (sequencial
em processo, nunca por intervalo de horario ou por uma segunda tarefa
agendada num horario "com folga"). Um desenho anterior com 2 tarefas
separadas (`daily_ingestion` @ 06:00 + `produtos_and_monitor` @ 06:35) foi
descartado nesta revisao: nao havia garantia de que a primeira tivesse
terminado antes da segunda comecar (a primeira podia legitimamente rodar
ate seu timeout total enquanto a segunda comecava por horario) — o health
check da segunda tarefa podia rodar ANTES do fim real da primeira.

A tarefa chama `scripts\\run_task.ps1 -TaskKey full_daily` — um wrapper
fino com UM nivel de aspas (evita a fragilidade de comandos `/tr`
aninhados com varios niveis de aspas). O mapeamento real TaskKey -> lock/
timeout/modulo vive em `scripts\\run_task.ps1` (fonte de verdade de
execucao); este modulo Python so' precisa saber o nome da TaskKey e o
horario, para gerar os artefatos de revisao.

Uso (so' imprime os comandos/XML propostos, nao cria nada):
    python -m pipelines.ops.schedule_plan
"""
from __future__ import annotations

from dataclasses import dataclass

REPO_ROOT = r"C:\Users\Notebook\Desktop\mktplace"
RUN_TASK_SCRIPT = rf"{REPO_ROOT}\scripts\run_task.ps1"

# Timeout externo do run_with_lock.ps1 para a TaskKey full_daily. Tem que
# ser MAIOR que a soma dos timeouts individuais dos steps de
# pipelines.ops.orchestrate.PIPELINES["full_daily"] (7200s desde o Gate B2,
# que acrescentou gold_regional_incremental=300s e
# sync_region_if_needed=120s), com margem para overhead de spawn de
# processo Python + imports pandas/sqlalchemy + latencia de rede VPN/Neon
# entre passos — senao o lock externo mataria o processo pai ANTES que os
# timeouts internos por step tivessem chance de proteger as fontes
# independentes seguintes. Duplicado aqui de proposito (sem import cruzado
# de pipelines.ops.orchestrate, para nao dar a este modulo nenhuma
# dependencia transitiva de subprocess) — ver
# pipelines/ops/orchestrate.py:FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS
# (7200s) e o teste que trava os dois valores em sincronia.
EXTERNAL_LOCK_TIMEOUT_SECONDS = 9000  # 2h30 (PT2H30M) — margem de ~1800s (~25%) sobre 7200s

# ExecutionTimeLimit do PROPRIO Task Scheduler (hard-limit independente do
# -TimeoutSeconds do run_with_lock.ps1) precisa ficar ACIMA de
# EXTERNAL_LOCK_TIMEOUT_SECONDS (9000s), nao igual — depois que o
# run_with_lock.ps1 detecta seu proprio timeout, ele ainda gasta tempo
# chamando Stop-Process, aguardando ate 30s a confirmacao real de termino
# do processo filho, e gravando os logs finais antes de sair. Se
# ExecutionTimeLimit fosse igual a 9000s, o Task Scheduler poderia matar o
# WRAPPER no meio dessa limpeza, antes de ele decidir se o lock deve ou
# nao ser removido (ver run_with_lock.ps1:removeLockOnExit) — deixando um
# lock em estado inconsistente. 9600s (PT2H40M) da' 600s de margem para
# Stop-Process + espera de ate 30s + escrita dos logs.
TASK_SCHEDULER_EXECUTION_TIME_LIMIT_SECONDS = 9600  # 2h40 (PT2H40M) — margem de 600s sobre os 9000s do lock


@dataclass(frozen=True)
class ScheduledTask:
    task_name: str
    task_key: str
    time_hhmm: str
    time_is_confirmed: bool
    notes: str


# UMA UNICA tarefa — a ordem/dependencia real dos passos internos vive em
# pipelines/ops/orchestrate.py (PIPELINES["full_daily"]), nunca em horario
# nem em uma segunda tarefa.
PROPOSED_SCHEDULE: tuple[ScheduledTask, ...] = (
    ScheduledTask(
        task_name="mktplace_full_daily",
        task_key="full_daily",
        time_hhmm="06:00",
        time_is_confirmed=False,
        notes=(
            "HIPOTESE, NAO CONFIRMADA — 06:00 foi herdado da proposta original "
            "sem uma verificacao read-only de quando o RDS (gold.*) e os exports "
            "Shopee tipicamente ficam disponiveis nesta operacao. Antes da Fase "
            "3B, confirmar isso (ex.: rodar `python -m pipelines.ops.preflight` "
            "manualmente por alguns dias nesse horario, ou revisar os "
            "timestamps reais de atualizacao das fontes) — se RDS/Shopee "
            "tipicamente atualizam DEPOIS das 06:00, a tarefa vai bloquear no "
            "preflight (RDS) ou processar arquivos do dia anterior (Shopee) "
            "todo dia, ate o horario ser ajustado com base em dado real, nao "
            "em uma suposicao herdada. Roda em processo unico via "
            "pipelines.ops.orchestrate.PIPELINES['full_daily']: ml/tiktok/"
            "shopee(+stats+ads) diarios, depois Produtos ML/TikTok/Shopee, "
            "depois o monitor do Bug 8 (so' se o sync Shopee teve SUCCESS "
            "nesta execucao), depois o health check (sempre, por ultimo, "
            "garantido pela posicao dele em PIPELINES['full_daily'] + "
            "always_run=True — nao ha segunda tarefa/segundo lock depois desta "
            "para rodar antes)."
        ),
    ),
)


def render_schtasks_command(task: ScheduledTask, allow_overwrite: bool = False) -> str:
    """Retorna o comando schtasks /create SIMPLES como TEXTO, para revisao
    rapida — nunca o executa (este modulo nao importa subprocess/os.system).

    AVISO: `schtasks /create` com flags simples NAO representa
    `MultipleInstancesPolicy`, `StartWhenAvailable` nem `ExecutionTimeLimit`
    de forma segura — use `render_task_scheduler_xml()` (abaixo) como a
    definicao de referencia para a Fase 3B; este comando serve so' para
    conferencia rapida do nome/horario/TaskKey.

    O valor de /tr precisa de aspas em volta do caminho do script (defesa
    contra um caminho com espaco em outra maquina), mas ja esta DENTRO das
    aspas externas do proprio /tr "..." — usar aspas simples criaria aspas
    aninhadas invalidas. A convencao correta do Windows (cmd.exe e
    PowerShell) para um literal " dentro de uma string "..." e' dobrar a
    aspa (""); e' assim que o proprio schtasks espera receber um caminho
    de executavel citado dentro do valor de /tr.

    allow_overwrite=False (padrao) NAO inclui /f — se a tarefa ja existir,
    schtasks recusa com erro visivel, forcando um humano a diagnosticar
    antes de decidir sobrescrever. So' passar allow_overwrite=True depois
    de checar manualmente que a tarefa existente pode ser substituida."""
    inner = f'powershell.exe -NoProfile -NonInteractive -File ""{RUN_TASK_SCRIPT}"" -TaskKey {task.task_key}'
    overwrite_flag = " /f" if allow_overwrite else ""
    return f'schtasks /create /tn "{task.task_name}" /tr "{inner}" /sc daily /st {task.time_hhmm}{overwrite_flag}'


def _execution_time_limit_iso8601() -> str:
    hours, remainder = divmod(TASK_SCHEDULER_EXECUTION_TIME_LIMIT_SECONDS, 3600)
    minutes = remainder // 60
    if minutes:
        return f"PT{hours}H{minutes}M"
    return f"PT{hours}H"


def render_task_scheduler_xml(task: ScheduledTask) -> str:
    """Retorna a definicao XML do Task Scheduler como TEXTO, para revisao
    — nunca a importa/aplica (nenhuma chamada ao `schtasks` com a flag de
    importacao de XML, nem a qualquer API do Task Scheduler, existe neste
    modulo).

    Esta e' a forma RECOMENDADA de ativar a tarefa na Fase 3B (em vez do
    `schtasks /create` simples acima), porque so' o XML representa com
    seguranca:
      - MultipleInstancesPolicy=IgnoreNew — o proprio Task Scheduler
        recusa iniciar uma nova instancia enquanto a anterior ainda esta
        rodando, EM CIMA do lock de arquivo (`run_with_lock.ps1`) — duas
        camadas independentes de protecao contra concorrencia, nao uma
        so';
      - StartWhenAvailable=true — se o notebook estiver desligado/
        suspenso no horario agendado, a tarefa roda assim que a maquina
        estiver disponivel de novo, em vez de simplesmente pular o dia
        (relevante dado que o host e' um notebook, nao um servidor
        sempre ligado — ver secao de decisao sobre o host no runbook);
      - ExecutionTimeLimit — hard-limit do proprio Task Scheduler,
        independente do -TimeoutSeconds do run_with_lock.ps1 (defesa em
        profundidade: mesmo se o wrapper PowerShell falhar em aplicar seu
        proprio timeout, o Task Scheduler mata o processo de qualquer
        jeito). Calculado a partir de
        TASK_SCHEDULER_EXECUTION_TIME_LIMIT_SECONDS (9600s/PT2H40M) —
        deliberadamente MAIOR que EXTERNAL_LOCK_TIMEOUT_SECONDS (9000s,
        o -TimeoutSeconds do proprio run_with_lock.ps1), com 600s de
        margem para o wrapper terminar Stop-Process + espera de ate 30s
        pela confirmacao real de termino do processo filho + escrita dos
        logs finais, sem o Task Scheduler mata-lo no meio dessa limpeza.

    IMPORTANTE — Settings/Enabled=false SEMPRE, sem parametro para mudar
    isso: a tarefa e' gerada DESATIVADA de proposito. O trigger continua
    totalmente configurado (horario, recorrencia diaria) para que a
    revisao humana veja exatamente quando ela rodaria SE fosse ativada,
    mas o Task Scheduler nunca dispara uma tarefa com Settings/Enabled=
    false, mesmo com o trigger habilitado — a tarefa fica so' registrada,
    inerte. A ativacao (mudar para Enabled=true) e' um passo MANUAL e
    SEPARADO da Fase 3B, feito somente depois de importar este XML (via
    `schtasks` com a flag de importacao), consultar a tarefa no Agendador
    de Tarefas do Windows (linha de comando com a flag de consulta, ou a
    UI) e validar que a definicao importada bate com o que foi revisado
    aqui — nunca no mesmo passo da importacao. Esta funcao nao aceita
    nenhum parametro
    tipo `enabled=True`: seria fragil demais depender de um caller lembrar
    de passar `enabled=False` explicitamente sempre; o default seguro
    (desativado) e' o UNICO comportamento possivel."""
    description = (
        f"{task.task_name} — proposta Fase 3A, NAO ativada (Settings/Enabled=false de proposito). "
        f"Horario {'CONFIRMADO' if task.time_is_confirmed else 'HIPOTESE, requer confirmacao antes da Fase 3B'}. "
        f"Ativacao (Enabled=true) e' um passo manual e separado, so' depois de importar, consultar e validar esta definicao."
    )
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{description}</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T{task.time_hhmm}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>{_execution_time_limit_iso8601()}</ExecutionTimeLimit>
    <Priority>7</Priority>
    <Enabled>false</Enabled>
  </Settings>
  <Actions>
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -NonInteractive -File "{RUN_TASK_SCRIPT}" -TaskKey {task.task_key}</Arguments>
    </Exec>
  </Actions>
</Task>'''


def render_all() -> str:
    lines = [
        "# Proposta de agenda — NAO EXECUTAR sem autorizacao explicita (Fase 3B)",
        "# Sem /f: se a tarefa ja existir, o comando abaixo falha com erro visivel",
        "# em vez de sobrescrever silenciosamente. Diagnosticar antes de decidir",
        "# sobrescrever (chamar render_schtasks_command(task, allow_overwrite=True)).",
        "#",
        "# AVISO: schtasks /create (abaixo) NAO configura MultipleInstancesPolicy,",
        "# StartWhenAvailable nem ExecutionTimeLimit. Para a Fase 3B, revisar a",
        "# definicao XML equivalente (render_task_scheduler_xml) — essa e' a forma",
        "# recomendada de ativacao. Nenhuma das duas e' executada por este modulo.",
        "#",
        "# O XML gera a tarefa com Settings/Enabled=false (DESATIVADA) sempre —",
        "# mesmo apos importada na Fase 3B, so' habilitar manualmente depois de",
        "# consultar a tarefa importada e validar que bate com o revisado aqui.",
        "",
    ]
    for task in PROPOSED_SCHEDULE:
        lines.append(f"# {task.task_name} ({task.task_key} @ {task.time_hhmm}, horario {'confirmado' if task.time_is_confirmed else 'HIPOTESE — requer confirmacao'})")
        lines.append(f"# {task.notes}")
        lines.append(render_schtasks_command(task))
        lines.append("")
        lines.append(f"# --- XML equivalente recomendado para {task.task_name} (texto, nao aplicado) ---")
        lines.append(render_task_scheduler_xml(task))
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_all())
