"""
Testes de pipelines/ops/schedule_plan.py: dados da agenda PROPOSTA — este
modulo NUNCA deve executar nada (nao importa subprocess/os.system/winreg,
nao chama schtasks nem qualquer API do Task Scheduler). So' declara dados
(UMA tarefa, delegando a scripts\\run_task.ps1 -TaskKey full_daily) e sabe
renderiza-los como texto `schtasks /create ...` E como XML do Task
Scheduler (para MultipleInstancesPolicy/StartWhenAvailable/
ExecutionTimeLimit, que schtasks /create simples nao representa) para
revisao humana.

A dependencia real entre passos (monitor do Bug 8 so' depois do sync
Produtos Shopee, health check por ultimo) NAO vive aqui — vive em
pipelines/ops/orchestrate.py (sequenciamento em processo, testado em
test_ops_orchestrate.py). Uma unica tarefa (nao 2, nao 8): 2 tarefas
agendadas em horarios separados nao garantiam que a primeira tivesse
terminado antes da segunda comecar.
"""
import re
from pathlib import Path

import pipelines.ops.schedule_plan as sp

MODULE_PATH = Path(sp.__file__)


def _parse_xml_or_raise(xml_text: str):
    import xml.etree.ElementTree as ET
    return ET.fromstring(xml_text.encode("utf-16"))


def _tokenize_or_raise(command: str):
    """Usa o parser real do PowerShell (via subprocess do proprio teste,
    nao do modulo sob teste) para confirmar que o valor de /tr tokeniza
    como string unica, sem erro de parsing — a mesma validacao que foi
    feita manualmente com [Parser]::ParseInput durante a correcao do bug
    de aspas aninhadas."""
    import subprocess

    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$cmd = @'\n{command}\n'@; "
        "$tokens = $null; $errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseInput($cmd, [ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors.Count -gt 0) { Write-Output ('PARSE_ERRORS:' + $errors.Count) } else { Write-Output 'PARSE_OK' }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Estrutura: UMA tarefa (full_daily), via run_task.ps1 -TaskKey
# ---------------------------------------------------------------------------

def test_apenas_uma_tarefa_proposta():
    """Regressao explicita: o desenho de 2 tarefas (daily_ingestion +
    produtos_and_monitor, agendadas em horarios separados) foi substituido
    por uma unica tarefa orquestrada, porque nao havia garantia de que a
    primeira tivesse terminado antes da segunda comecar."""
    assert len(sp.PROPOSED_SCHEDULE) == 1
    assert sp.PROPOSED_SCHEDULE[0].task_key == "full_daily"


def test_cada_tarefa_delega_a_run_task_com_a_taskkey_certa():
    for task in sp.PROPOSED_SCHEDULE:
        rendered = sp.render_schtasks_command(task)
        assert "run_task.ps1" in rendered
        assert f"-TaskKey {task.task_key}" in rendered


def test_run_task_script_e_caminho_absoluto():
    assert Path(sp.RUN_TASK_SCRIPT).is_absolute()


def test_todas_as_tarefas_tem_horario_hhmm_valido():
    for task in sp.PROPOSED_SCHEDULE:
        assert re.match(r"^\d{2}:\d{2}$", task.time_hhmm)


# ---------------------------------------------------------------------------
# Renderizacao das aspas — corrigido nesta revisao (nested-quote bug)
# ---------------------------------------------------------------------------

def test_render_usa_aspas_dobradas_nao_aninhamento_invalido():
    """O bug original tinha varios niveis de aspas simples/duplas
    aninhadas dentro de /tr "..." — invalido em cmd.exe/PowerShell. A
    correcao usa a convencao de dobrar a aspa ("") para o caminho citado
    dentro do valor de /tr, que ja esta dentro de aspas externas."""
    task = sp.PROPOSED_SCHEDULE[0]
    rendered = sp.render_schtasks_command(task)
    assert f'""{sp.RUN_TASK_SCRIPT}""' in rendered
    assert "'" not in rendered  # nenhuma aspa simples usada


def test_comando_renderizado_tokeniza_como_string_unica_no_parser_real():
    """Validacao rigorosa: usa o parser real do PowerShell para confirmar
    que o comando nao tem erro de sintaxe e que o valor de /tr e' um unico
    token — nao apenas uma checagem textual de aspas."""
    for task in sp.PROPOSED_SCHEDULE:
        rendered = sp.render_schtasks_command(task)
        output = _tokenize_or_raise(rendered)
        assert output == "PARSE_OK", f"erro de parsing em '{task.task_name}': {output}"


# ---------------------------------------------------------------------------
# /f (overwrite) nunca por padrao
# ---------------------------------------------------------------------------

def test_render_nao_inclui_f_por_padrao():
    for task in sp.PROPOSED_SCHEDULE:
        rendered = sp.render_schtasks_command(task)
        assert not rendered.rstrip().endswith("/f")
        assert " /f" not in rendered


def test_render_inclui_f_somente_quando_explicitamente_pedido():
    task = sp.PROPOSED_SCHEDULE[0]
    rendered = sp.render_schtasks_command(task, allow_overwrite=True)
    assert rendered.rstrip().endswith("/f")


# ---------------------------------------------------------------------------
# Nenhuma credencial embutida; render_all so' retorna texto
# ---------------------------------------------------------------------------

def test_nenhuma_credencial_embutida_em_nenhum_comando_renderizado():
    rendered_all = sp.render_all()
    assert not re.search(r"://[^/\s@]+:[^/\s@]+@", rendered_all)


def test_render_all_nao_executa_nada_so_retorna_texto():
    result = sp.render_all()
    assert isinstance(result, str)
    assert "schtasks /create" in result
    invocation_lines = [line for line in result.splitlines() if line.startswith("schtasks /create /tn")]
    assert len(invocation_lines) == len(sp.PROPOSED_SCHEDULE)


# ---------------------------------------------------------------------------
# Configuracoes StartWhenAvailable / MultipleInstances / ExecutionTimeLimit
# — schtasks /create simples nao representa isso; a Fase 3B usaria o XML.
# ---------------------------------------------------------------------------

def test_render_task_scheduler_xml_e_xml_bem_formado():
    for task in sp.PROPOSED_SCHEDULE:
        xml_text = sp.render_task_scheduler_xml(task)
        root = _parse_xml_or_raise(xml_text)
        assert root is not None


def test_xml_gera_tarefa_desativada_por_padrao():
    """Regressao desta revisao: a tarefa importada na Fase 3B tem que
    nascer DESATIVADA (Settings/Enabled=false) — a ativacao real e' um
    passo manual e separado, so' depois de importar, consultar e validar
    a definicao. O trigger continua configurado (para a revisao humana
    ver o horario), so' o Settings/Enabled geral e' que fica false."""
    for task in sp.PROPOSED_SCHEDULE:
        xml_text = sp.render_task_scheduler_xml(task)
        assert "<Enabled>false</Enabled>" in xml_text
        # o trigger continua habilitado/configurado — so' o Settings geral
        # (a segunda ocorrencia de <Enabled>) e' que desativa a tarefa
        assert xml_text.count("<Enabled>") == 2
        assert xml_text.count("<Enabled>true</Enabled>") == 1
        assert xml_text.count("<Enabled>false</Enabled>") == 1


def test_render_task_scheduler_xml_nao_aceita_parametro_para_habilitar():
    """Nao pode existir um jeito de chamar render_task_scheduler_xml() que
    produza Enabled=true por engano ou por um caller esquecer de passar
    algo — a funcao so' aceita `task`, sem nenhum parametro tipo
    `enabled=True` (mesmo com default seguro seria fragil: dependeria de
    ninguem nunca passar True sem querer)."""
    import inspect
    sig = inspect.signature(sp.render_task_scheduler_xml)
    assert list(sig.parameters) == ["task"]


def test_xml_documenta_que_ativacao_e_manual_e_separada_da_importacao():
    xml_text = sp.render_task_scheduler_xml(sp.PROPOSED_SCHEDULE[0])
    assert "manual" in xml_text.lower()
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "Enabled=false" in source
    assert "manual" in source.lower()


def test_xml_configura_multiple_instances_ignore_new():
    """Impede uma NOVA instancia enquanto a anterior ainda esta rodando —
    protecao do proprio Task Scheduler, EM CIMA do lock de arquivo
    (run_with_lock.ps1), nao no lugar dele."""
    xml_text = sp.render_task_scheduler_xml(sp.PROPOSED_SCHEDULE[0])
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in xml_text


def test_xml_configura_start_when_available():
    xml_text = sp.render_task_scheduler_xml(sp.PROPOSED_SCHEDULE[0])
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml_text


def test_xml_configura_execution_time_limit_maior_que_o_timeout_do_lock():
    """Regressao desta revisao: ExecutionTimeLimit do Task Scheduler tem
    que ficar ACIMA de EXTERNAL_LOCK_TIMEOUT_SECONDS (o -TimeoutSeconds do
    proprio run_with_lock.ps1), nunca igual — depois que o
    run_with_lock.ps1 detecta seu proprio timeout de 9000s, ele ainda
    gasta tempo com Stop-Process, ate 30s de espera pela confirmacao real
    de termino do filho, e escrita dos logs finais. Se fossem iguais, o
    Task Scheduler poderia matar o wrapper no meio dessa limpeza."""
    xml_text = sp.render_task_scheduler_xml(sp.PROPOSED_SCHEDULE[0])
    assert "<ExecutionTimeLimit>PT2H40M</ExecutionTimeLimit>" in xml_text
    assert sp.EXTERNAL_LOCK_TIMEOUT_SECONDS == 9000
    assert sp.TASK_SCHEDULER_EXECUTION_TIME_LIMIT_SECONDS == 9600
    assert sp.TASK_SCHEDULER_EXECUTION_TIME_LIMIT_SECONDS > sp.EXTERNAL_LOCK_TIMEOUT_SECONDS


def test_execution_time_limit_e_maior_que_o_orcamento_interno_dos_steps():
    """Trava os numeros em sincronia: 9000s (timeout do lock) tem que
    ficar acima de 7800s (soma dos timeouts individuais dos steps de
    pipelines.ops.orchestrate.PIPELINES['full_daily'] — ml=900+tiktok=900+
    regional=300+sync_region=120+produtos_ml=600+produtos_tiktok=600 do Gate C1,
    mais serving_ml=600+serving_tiktok_brand=600+serving_tiktok_creator=1800 do
    Checkpoint O1 Task 2/2, mais serving_ml_cross_company=300+
    serving_tiktok_channel_efficiency=600 do Gate S3, mais
    tiktok_affiliate_cost_order_monthly=300 do Gate UE2-C, mais
    health_check=180), com margem; e o
    ExecutionTimeLimit do Task Scheduler (9600s) tem que ficar acima do timeout
    do lock, com margem adicional para a limpeza pos-timeout (Stop-Process +
    espera + logs)."""
    import pipelines.ops.orchestrate as orch
    internal_budget_seconds = 7800
    assert orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS == internal_budget_seconds, (
        "orcamento hardcoded neste teste saiu de sincronia com a soma real "
        "dos timeouts de PIPELINES['full_daily'] — atualize os dois numeros juntos"
    )
    assert sp.EXTERNAL_LOCK_TIMEOUT_SECONDS > internal_budget_seconds
    margin = sp.EXTERNAL_LOCK_TIMEOUT_SECONDS - internal_budget_seconds
    assert margin > 0.15 * internal_budget_seconds

    cleanup_margin = sp.TASK_SCHEDULER_EXECUTION_TIME_LIMIT_SECONDS - sp.EXTERNAL_LOCK_TIMEOUT_SECONDS
    assert cleanup_margin >= 300, "margem insuficiente para Stop-Process + espera de ate 30s + logs"


# ---------------------------------------------------------------------------
# Gate C1 (2026-07-16) — Shopee vira pipeline manual separado; a agenda
# PROPOSTA (schtasks/Task Scheduler) continua com uma unica tarefa, e essa
# tarefa continua apontando exclusivamente para full_daily.
# ---------------------------------------------------------------------------

def test_shopee_manual_refresh_nao_tem_nenhuma_tarefa_agendada():
    """Gate C1: shopee_manual_refresh existe em orchestrate.PIPELINES mas
    NUNCA deve ganhar uma entrada em PROPOSED_SCHEDULE — e' manual, sob
    demanda, nunca disparado pelo Task Scheduler."""
    task_keys = [task.task_key for task in sp.PROPOSED_SCHEDULE]
    assert "shopee_manual_refresh" not in task_keys


def test_unica_tarefa_agendada_continua_sendo_full_daily_apos_gate_c1():
    assert len(sp.PROPOSED_SCHEDULE) == 1
    assert sp.PROPOSED_SCHEDULE[0].task_key == "full_daily"
    rendered = sp.render_schtasks_command(sp.PROPOSED_SCHEDULE[0])
    assert "-TaskKey full_daily" in rendered
    assert "shopee" not in rendered.lower()


def test_notes_da_tarefa_full_daily_documenta_a_exclusao_do_shopee():
    """As notes deste module descrevem o CONTEUDO do full_daily para quem
    for revisar a agenda antes de ativar — desde o Gate C1, Shopee so' pode
    aparecer nas notes para EXPLICAR que ficou de fora (vive em
    shopee_manual_refresh, rodado manualmente), nunca implicando que faz
    parte do que este full_daily executa."""
    notes = sp.PROPOSED_SCHEDULE[0].notes.lower()
    assert "shopee_manual_refresh" in notes
    assert "nao faz parte deste pipeline" in notes or "não faz parte deste pipeline" in notes


def test_xml_nunca_e_importado_ou_aplicado_pelo_modulo():
    """render_task_scheduler_xml devolve TEXTO — nenhuma funcao deste
    modulo chama schtasks /create /xml nem qualquer API do Task
    Scheduler."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "schtasks /create /xml" not in source.lower()
    assert "register-scheduledtask" not in source.lower()
    assert "new-scheduledtask" not in source.lower()


def test_horario_nao_confirmado_e_sinalizado_como_hipotese():
    """Ponto 5 da revisao: 'remover horario sem justificativa OU marcar
    como hipotese que exige confirmacao antes da Fase 3B' — como nao ha
    telemetria real de quando RDS/Shopee tipicamente atualizam, o horario
    06:00 fica marcado explicitamente como hipotese, nunca como validado."""
    task = sp.PROPOSED_SCHEDULE[0]
    assert task.time_is_confirmed is False
    assert "HIPOTESE" in task.notes.upper() or "HIPÓTESE" in task.notes.upper()

    rendered_all = sp.render_all()
    assert "HIPOTESE" in rendered_all.upper()

    xml_text = sp.render_task_scheduler_xml(task)
    assert "HIPOTESE" in xml_text.upper()


# ---------------------------------------------------------------------------
# Guardas estruturais: este modulo nunca ativa nada
# ---------------------------------------------------------------------------

def test_modulo_nunca_importa_capacidade_de_execucao():
    """Checa so' linhas de import de verdade (nao a prosa do docstring, que
    menciona esses nomes exatamente para explicar que nao sao usados)."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    import_lines = [line.strip() for line in source.splitlines() if line.strip().startswith(("import ", "from "))]
    forbidden = ("subprocess", "os", "winreg", "win32com", "ctypes")
    for line in import_lines:
        for name in forbidden:
            assert not re.match(rf"^(import\s+{name}\b|from\s+{name}\b)", line), \
                f"import proibido encontrado: {line!r}"


def test_nenhuma_chamada_de_execucao_de_processo_no_modulo():
    """Guarda a INTENCAO real (nunca dispara um processo), sem depender de
    onde a palavra 'schtasks' aparece no texto/prosa — checar so' import
    (teste acima) ja e' suficiente para provar que subprocess/os.system nao
    estao disponiveis neste modulo, mas aqui tambem garantimos que nenhuma
    chamada de execucao aparece mesmo via nome qualificado (ex.: um import
    faltando detectado por engano)."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    for pattern in (r"subprocess\.\w+\(", r"os\.system\(", r"os\.popen\(", r"os\.exec\w*\("):
        assert not re.search(pattern, source), f"chamada de execucao proibida encontrada: {pattern}"


def test_nunca_referencia_datamart_database_url():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "DATAMART_DATABASE_URL" not in source


# ---------------------------------------------------------------------------
# Checkpoint O1 Task 2/2 (2026-08-17) — TaskKey manual serving_refresh
# ---------------------------------------------------------------------------

RUN_TASK_PS1 = Path(sp.__file__).resolve().parents[2] / "scripts" / "run_task.ps1"


def _task_definitions_via_powershell():
    """Le o mapeamento REAL de TaskKeys usando o proprio PowerShell: dot-source
    de run_task.ps1 (que so' carrega funcoes, nao executa nada) e serializa
    Get-TaskDefinitions como JSON. E' comportamental — exercita o mesmo codigo
    que roda quando o Task Scheduler dispara, em vez de casar texto com regex.
    """
    import json
    import subprocess

    script = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{RUN_TASK_PS1}'; "
        "Get-TaskDefinitions | ConvertTo-Json -Depth 5 -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_22_taskkey_serving_refresh_existe_e_roda_o_pipeline_certo():
    defs = _task_definitions_via_powershell()
    assert "serving_refresh" in defs
    cfg = defs["serving_refresh"]
    assert cfg["Module"] == "pipelines.ops.orchestrate"
    assert list(cfg["ModuleArgs"]) == ["--pipeline", "serving_refresh"]


def test_22_as_taskkeys_sao_exatamente_tres():
    defs = _task_definitions_via_powershell()
    assert set(defs) == {"full_daily", "shopee_manual_refresh", "serving_refresh"}


def test_23_serving_refresh_usa_o_lock_do_full_daily():
    """Compartilhar o lock e' o que torna a sobreposicao IMPOSSIVEL por
    construcao: se o full_daily agendado estiver rodando, esta TaskKey sai
    BLOCKED em vez de disputar as mesmas fontes e o mesmo destino."""
    defs = _task_definitions_via_powershell()
    assert defs["serving_refresh"]["Lock"] == "full_daily"
    assert defs["serving_refresh"]["Lock"] == defs["full_daily"]["Lock"]


def test_23_shopee_manual_refresh_mantem_lock_proprio():
    """Contraste deliberado: Shopee mexe em fontes disjuntas e pode rodar em
    paralelo; serving nao pode."""
    defs = _task_definitions_via_powershell()
    assert defs["shopee_manual_refresh"]["Lock"] == "shopee_manual_refresh"
    assert defs["shopee_manual_refresh"]["Lock"] != defs["full_daily"]["Lock"]


def test_23_serving_refresh_reusa_o_mesmo_timeout_externo():
    defs = _task_definitions_via_powershell()
    assert defs["serving_refresh"]["TimeoutSeconds"] == sp.EXTERNAL_LOCK_TIMEOUT_SECONDS
    assert defs["serving_refresh"]["TimeoutSeconds"] == defs["full_daily"]["TimeoutSeconds"]


def test_23_resolve_task_invocation_resolve_serving_refresh():
    """O resolvedor real (mesma funcao que o Task Scheduler exercita) devolve o
    lock e o comando corretos para a TaskKey nova."""
    import json
    import subprocess

    script = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{RUN_TASK_PS1}'; "
        "Resolve-TaskInvocation -TaskKey serving_refresh -RepoRoot 'C:\\repo' "
        "-PythonExe 'python.exe' -LockScript 'lock.ps1' | ConvertTo-Json -Depth 5 -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    inv = json.loads(result.stdout)
    assert inv["LockName"] == "full_daily"
    assert list(inv["ModuleArgs"]) == ["-m", "pipelines.ops.orchestrate",
                                       "--pipeline", "serving_refresh"]


def test_24_serving_refresh_nao_esta_na_agenda_proposta():
    """Contingencia MANUAL: nenhuma tarefa do Windows para ela."""
    chaves = {t.task_key for t in sp.PROPOSED_SCHEDULE}
    assert "serving_refresh" not in chaves
    assert chaves == {"full_daily"}


def test_24_continua_havendo_exatamente_uma_tarefa_agendada():
    assert len(sp.PROPOSED_SCHEDULE) == 1
    assert sp.PROPOSED_SCHEDULE[0].task_name == "mktplace_full_daily"


def test_24_nenhum_comando_renderizado_menciona_serving_refresh():
    for task in sp.PROPOSED_SCHEDULE:
        rendered = sp.render_schtasks_command(task)
        assert "serving_refresh" not in rendered
        assert "-TaskKey full_daily" in rendered


def _ps1_sem_comentarios(path: Path) -> str:
    """Codigo PowerShell sem comentarios.

    Necessario pelo mesmo motivo de `test_modulo_nunca_importa_capacidade_de_execucao`
    acima: run_task.ps1 DOCUMENTA por que existe — "um comando /tr do schtasks
    com varios niveis de aspas e' fragil" — e um grep no texto bruto casaria com
    essa explicacao, nao com uma chamada real. Sem AST para PowerShell, remover
    comentario de linha e bloco `<# #>` e' a aproximacao honesta.
    """
    import re as _re
    texto = path.read_text(encoding="utf-8")
    texto = _re.sub(r"<#.*?#>", " ", texto, flags=_re.S)
    linhas = []
    for linha in texto.splitlines():
        sem = _re.sub(r"(?<!`)#.*$", "", linha)
        linhas.append(sem)
    return "\n".join(linhas)


def test_24_run_task_ps1_nao_cria_nem_altera_tarefa():
    """O wrapper resolve e delega; nunca registra, habilita ou dispara tarefa.

    A TaskKey serving_refresh e' contingencia MANUAL: existir no mapeamento nao
    cria agendamento algum.
    """
    baixo = _ps1_sem_comentarios(RUN_TASK_PS1).lower()
    assert "serving_refresh" in baixo, "a TaskKey deveria estar no codigo, nao so' no comentario"
    for termo in ("schtasks", "register-scheduledtask", "new-scheduledtask",
                  "set-scheduledtask", "enable-scheduledtask",
                  "start-scheduledtask", "unregister-scheduledtask"):
        assert termo not in baixo, termo


def test_24_run_task_ps1_nao_menciona_airflow():
    assert "airflow" not in RUN_TASK_PS1.read_text(encoding="utf-8").lower()


def test_orcamento_do_serving_refresh_cabe_no_timeout_externo():
    import pipelines.ops.orchestrate as orch
    assert orch.SERVING_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS == 3000
    assert orch.SERVING_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS < sp.EXTERNAL_LOCK_TIMEOUT_SECONDS
    margem = sp.EXTERNAL_LOCK_TIMEOUT_SECONDS - orch.SERVING_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS
    assert margem > 0.15 * orch.SERVING_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS
