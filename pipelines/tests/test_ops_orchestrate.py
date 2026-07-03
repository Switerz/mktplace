"""
Testes de pipelines/ops/orchestrate.py: amarra preflight a execucao real
(aprovado->roda, bloqueado->comando NUNCA executa), sequenciamento por
depends_on/always_run, timeout INDIVIDUAL por step (uma fonte travada nao
consome o timeout global nem trava fontes independentes seguintes), e
propagacao de exit code — tudo com executor/preflight_fn injetados
(fakes). Nenhum subprocess real, nenhum banco tocado.
"""
import re
import subprocess
from pathlib import Path

import pytest

import pipelines.ops.orchestrate as orch


def make_preflight(blocked_sources=()):
    def _fn(source):
        if source in blocked_sources:
            return False, []
        return True, []
    return _fn


def make_executor(returncodes=None, calls=None, timeout_on=()):
    """timeout_on: nomes de step que devem levantar subprocess.TimeoutExpired
    em vez de retornar um exit code — simula o comportamento real de
    subprocess.run(timeout=...) sem precisar de um processo de verdade."""
    returncodes = returncodes or {}
    calls = calls if calls is not None else []

    def _fn(step):
        calls.append(step.name)
        if step.name in timeout_on:
            raise subprocess.TimeoutExpired(cmd=step.module, timeout=step.timeout_seconds)
        return returncodes.get(step.name, 0)
    return _fn


# ---------------------------------------------------------------------------
# preflight obrigatoriamente amarrado a execucao real
# ---------------------------------------------------------------------------

def test_preflight_aprovado_executa_o_comando_real():
    calls = []
    executor = make_executor(calls=calls)
    preflight_fn = make_preflight(blocked_sources=())

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert calls[:5] == ["daily_ml", "daily_tiktok", "daily_shopee_orders", "daily_shopee_stats", "daily_shopee_ads"]
    assert all(v == "SUCCESS" for v in results.values())


def test_preflight_bloqueado_o_comando_real_nunca_e_chamado():
    calls = []
    executor = make_executor(calls=calls)
    preflight_fn = make_preflight(blocked_sources=("tiktok_daily",))

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert "daily_tiktok" not in calls, "executor foi chamado mesmo com preflight bloqueado"
    assert results["daily_tiktok"] == "BLOCKED"
    assert results["daily_ml"] == "SUCCESS"
    assert results["daily_shopee_orders"] == "SUCCESS"


def test_preflight_bloqueado_nao_impede_passos_independentes_de_rodar():
    calls = []
    executor = make_executor(calls=calls)
    preflight_fn = make_preflight(blocked_sources=("shopee_daily",))

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert results["daily_shopee_orders"] == "BLOCKED"
    assert results["daily_shopee_stats"] == "SUCCESS"
    assert results["daily_shopee_ads"] == "SUCCESS"


# ---------------------------------------------------------------------------
# exit code do comando real e' propagado
# ---------------------------------------------------------------------------

def test_exit_code_diferente_de_zero_vira_failed():
    executor = make_executor(returncodes={"daily_ml": 7})
    preflight_fn = make_preflight()

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert results["daily_ml"] == "FAILED"
    assert results["daily_tiktok"] == "SUCCESS"


# ---------------------------------------------------------------------------
# timeout INDIVIDUAL por step
# ---------------------------------------------------------------------------

def test_timeout_individual_marca_o_step_como_failed():
    calls = []
    executor = make_executor(calls=calls, timeout_on=("daily_ml",))
    preflight_fn = make_preflight()

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert results["daily_ml"] == "FAILED"


def test_timeout_de_uma_fonte_nao_impede_fontes_independentes_seguintes():
    """Regressao pedida explicitamente: timeout de ML seguido de execucao
    NORMAL de TikTok/Shopee — uma fonte travada nao pode consumir o
    timeout global nem travar as demais."""
    calls = []
    executor = make_executor(calls=calls, timeout_on=("daily_ml",))
    preflight_fn = make_preflight()

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert results["daily_ml"] == "FAILED"
    assert results["daily_tiktok"] == "SUCCESS"
    assert results["daily_shopee_orders"] == "SUCCESS"
    assert results["daily_shopee_stats"] == "SUCCESS"
    assert results["daily_shopee_ads"] == "SUCCESS"
    assert "daily_tiktok" in calls and "daily_shopee_orders" in calls


def test_default_executor_propaga_timeout_individual_do_step(monkeypatch):
    """Confirma que _default_executor de verdade passa
    step.timeout_seconds para subprocess.run — e' isso que faz o timeout
    disparar por conta do proprio Python, nao so' na documentacao."""
    captured = {}

    def _fake_run(cmd, cwd=None, timeout=None):
        captured["timeout"] = timeout
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(orch.subprocess, "run", _fake_run)
    step = orch.Step("x", "algum.modulo", ("--flag",), timeout_seconds=42)
    with pytest.raises(subprocess.TimeoutExpired):
        orch._default_executor(step)
    assert captured["timeout"] == 42


def test_timeout_budget_individual_soma_menos_que_o_recomendado_externo():
    """O orcamento somado dos timeouts individuais tem que caber com
    margem dentro do timeout externo documentado em scripts/run_task.ps1
    (9000s) — se um dia a soma dos steps ultrapassar isso, o lock externo
    mataria o processo pai antes dos timeouts internos protegerem as
    fontes independentes."""
    RECOMMENDED_EXTERNAL_LOCK_TIMEOUT_SECONDS = 9000
    assert orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS < RECOMMENDED_EXTERNAL_LOCK_TIMEOUT_SECONDS
    margin = RECOMMENDED_EXTERNAL_LOCK_TIMEOUT_SECONDS - orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS
    assert margin > 0.15 * orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS, "margem de seguranca insuficiente"


# ---------------------------------------------------------------------------
# depends_on / always_run — sequenciamento real, nunca por horario
# ---------------------------------------------------------------------------

def test_monitor_bug8_so_roda_se_sync_shopee_teve_sucesso_real():
    calls = []
    executor = make_executor(calls=calls)
    preflight_fn = make_preflight()

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert "monitor_bug8" in calls
    assert results["monitor_bug8"] == "SUCCESS"


def test_monitor_bug8_e_pulado_se_sync_shopee_falhar():
    executor = make_executor(returncodes={"sync_produtos_shopee": 1})
    preflight_fn = make_preflight()

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert results["sync_produtos_shopee"] == "FAILED"
    assert results["monitor_bug8"] == "SKIPPED"


def test_monitor_bug8_e_pulado_se_sync_shopee_foi_bloqueado_no_preflight():
    executor = make_executor()
    preflight_fn = make_preflight(blocked_sources=("produtos_shopee",))

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert results["sync_produtos_shopee"] == "BLOCKED"
    assert results["monitor_bug8"] == "SKIPPED"


def test_monitor_bug8_e_pulado_se_sync_shopee_deu_timeout():
    executor = make_executor(timeout_on=("sync_produtos_shopee",))
    preflight_fn = make_preflight()

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert results["sync_produtos_shopee"] == "FAILED"
    assert results["monitor_bug8"] == "SKIPPED"


def test_health_check_sempre_roda_mesmo_se_tudo_antes_falhou():
    calls = []
    executor = make_executor(
        returncodes={"sync_produtos_ml": 1, "sync_produtos_tiktok": 1, "sync_produtos_shopee": 1},
        calls=calls,
    )
    preflight_fn = make_preflight()

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert "health_check" in calls
    assert results["health_check"] == "SUCCESS"
    assert results["monitor_bug8"] == "SKIPPED"


def test_health_check_sempre_roda_mesmo_com_tudo_bloqueado_no_preflight():
    calls = []
    executor = make_executor(calls=calls)
    preflight_fn = make_preflight(blocked_sources=("produtos_ml", "produtos_tiktok", "produtos_shopee"))

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert "health_check" in calls
    assert results["health_check"] == "SUCCESS"


def test_pipeline_desconhecido_levanta_erro():
    with pytest.raises(ValueError):
        orch.run_pipeline("pipeline_que_nao_existe")


def test_pipelines_antigos_de_2_tarefas_nao_existem_mais():
    """Regressao explicita: o desenho anterior (2 tarefas agendadas
    separadamente, 'daily_ingestion' + 'produtos_and_monitor') foi
    substituido por um unico pipeline orquestrado, porque a segunda tarefa
    podia comecar por horario antes da primeira ter terminado de verdade."""
    assert "daily_ingestion" not in orch.PIPELINES
    assert "produtos_and_monitor" not in orch.PIPELINES
    assert set(orch.PIPELINES) == {"full_daily"}


# ---------------------------------------------------------------------------
# Sequenciamento GLOBAL: health_check e' comprovadamente o ULTIMO passo,
# em qualquer combinacao de sucesso/falha/bloqueio/timeout anterior
# ---------------------------------------------------------------------------

def test_health_check_e_sempre_o_ultimo_step_da_definicao_do_pipeline():
    """Garantia estrutural: nao existe segunda tarefa/segundo pipeline
    agendado depois deste — health_check ser o ULTIMO item da tupla
    PIPELINES['full_daily'] e' o que torna 'health check por ultimo,
    sempre' uma propriedade do desenho, nao uma esperanca de horario."""
    steps = orch.PIPELINES["full_daily"]
    assert steps[-1].name == "health_check"
    assert steps[-1].always_run is True


@pytest.mark.parametrize("scenario", [
    {"returncodes": {}, "timeout_on": (), "blocked": ()},
    {"returncodes": {"daily_ml": 1}, "timeout_on": (), "blocked": ()},
    {"returncodes": {}, "timeout_on": ("daily_tiktok",), "blocked": ()},
    {"returncodes": {}, "timeout_on": (), "blocked": ("shopee_daily", "produtos_ml")},
    {"returncodes": {"sync_produtos_shopee": 1}, "timeout_on": ("daily_shopee_ads",), "blocked": ("tiktok_daily",)},
])
def test_health_check_roda_por_ultimo_em_qualquer_cenario_misto(scenario):
    """Executa o pipeline inteiro com uma mistura de sucesso/falha/timeout/
    bloqueio e confirma que health_check e' sempre o ULTIMO nome a ser
    chamado pelo executor, nunca antecipado."""
    calls = []
    executor = make_executor(returncodes=scenario["returncodes"], calls=calls, timeout_on=scenario["timeout_on"])
    preflight_fn = make_preflight(blocked_sources=scenario["blocked"])

    orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert calls[-1] == "health_check", f"health_check nao foi o ultimo chamado: {calls}"


# ---------------------------------------------------------------------------
# _default_executor — cwd explicito na raiz do repo (nunca herdado)
# ---------------------------------------------------------------------------

def test_default_executor_usa_cwd_explicito_da_raiz_do_repo(monkeypatch):
    captured = {}

    class _FakeCompletedProcess:
        returncode = 0

    def _fake_run(cmd, cwd=None, timeout=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        return _FakeCompletedProcess()

    monkeypatch.setattr(orch.subprocess, "run", _fake_run)
    step = orch.Step("x", "algum.modulo", ("--flag",), timeout_seconds=99)
    rc = orch._default_executor(step)

    assert rc == 0
    assert captured["cwd"] == str(orch.REPO_ROOT)
    assert captured["timeout"] == 99


# ---------------------------------------------------------------------------
# Guardas estruturais
# ---------------------------------------------------------------------------

MODULE_PATH = Path(orch.__file__)


def test_nunca_referencia_datamart_database_url():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert not re.search(r'os\.environ(?:\.get)?\(\s*["\']DATAMART_DATABASE_URL', source)
    assert not re.search(r'os\.getenv\(\s*["\']DATAMART_DATABASE_URL', source)


def test_nunca_ativa_task_scheduler():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "schtasks" not in source.lower()
    assert "register-scheduledtask" not in source.lower()


def test_todos_os_steps_tem_timeout_individual_positivo():
    for step in orch.PIPELINES["full_daily"]:
        assert step.timeout_seconds > 0, f"{step.name} sem timeout individual"


def test_shopee_orders_stats_ads_sao_passos_separados_no_pipeline():
    """Bug corrigido numa revisao anterior: a agenda antiga so' cobria
    'shopee', ignorando que daily_performance trata shopee/shopee-stats/
    shopee-ads como fontes distintas."""
    names = {step.name for step in orch.PIPELINES["full_daily"]}
    assert {"daily_shopee_orders", "daily_shopee_stats", "daily_shopee_ads"} <= names
