"""
Testes de pipelines/ops/orchestrate.py: amarra preflight a execucao real
(aprovado->roda, bloqueado->comando NUNCA executa), sequenciamento por
depends_on/always_run, timeout INDIVIDUAL por step (uma fonte travada nao
consome o timeout global nem trava fontes independentes seguintes), e
propagacao de exit code — tudo com executor/preflight_fn injetados
(fakes). Nenhum subprocess real, nenhum banco tocado.

Gate C1 (2026-07-16): PIPELINES virou DOIS pipelines independentes —
`full_daily` (automatico, so' fontes recorrentes: ml, tiktok, regional,
produtos ml/tiktok, health_check) e `shopee_manual_refresh` (manual, so'
sob demanda: shopee orders/stats/ads, produtos shopee, bug8, health_check).
Os testes abaixo refletem essa separacao.
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
# preflight obrigatoriamente amarrado a execucao real (full_daily)
# ---------------------------------------------------------------------------

def test_preflight_aprovado_executa_o_comando_real():
    calls = []
    executor = make_executor(calls=calls)
    preflight_fn = make_preflight(blocked_sources=())

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert calls[:2] == ["daily_ml", "daily_tiktok"]
    assert all(v == "SUCCESS" for v in results.values())


def test_preflight_bloqueado_o_comando_real_nunca_e_chamado():
    calls = []
    executor = make_executor(calls=calls)
    preflight_fn = make_preflight(blocked_sources=("tiktok_daily",))

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert "daily_tiktok" not in calls, "executor foi chamado mesmo com preflight bloqueado"
    assert results["daily_tiktok"] == "BLOCKED"
    assert results["daily_ml"] == "SUCCESS"


def test_preflight_bloqueado_nao_impede_passos_independentes_de_rodar():
    calls = []
    executor = make_executor(calls=calls)
    preflight_fn = make_preflight(blocked_sources=("ml_daily",))

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert results["daily_ml"] == "BLOCKED"
    assert results["daily_tiktok"] == "SUCCESS"
    assert results["sync_produtos_ml"] == "SUCCESS"


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
    NORMAL de TikTok/regional/produtos — uma fonte travada nao pode
    consumir o timeout global nem travar as demais."""
    calls = []
    executor = make_executor(calls=calls, timeout_on=("daily_ml",))
    preflight_fn = make_preflight()

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert results["daily_ml"] == "FAILED"
    assert results["daily_tiktok"] == "SUCCESS"
    assert results["gold_regional_incremental"] == "SUCCESS"
    assert results["sync_produtos_ml"] == "SUCCESS"
    assert results["sync_produtos_tiktok"] == "SUCCESS"
    assert "daily_tiktok" in calls and "sync_produtos_ml" in calls


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
    fontes independentes. Vale para os DOIS pipelines (Gate C1)."""
    RECOMMENDED_EXTERNAL_LOCK_TIMEOUT_SECONDS = 9000
    for budget in (orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS, orch.SHOPEE_MANUAL_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS):
        assert budget < RECOMMENDED_EXTERNAL_LOCK_TIMEOUT_SECONDS
        margin = RECOMMENDED_EXTERNAL_LOCK_TIMEOUT_SECONDS - budget
        assert margin > 0.15 * budget, "margem de seguranca insuficiente"


# ---------------------------------------------------------------------------
# depends_on / always_run — sequenciamento real, nunca por horario
# (monitor_bug8/sync_produtos_shopee vivem em shopee_manual_refresh desde o
# Gate C1 — nao existem mais em full_daily)
# ---------------------------------------------------------------------------

def test_monitor_bug8_so_roda_se_sync_shopee_teve_sucesso_real():
    calls = []
    executor = make_executor(calls=calls)
    preflight_fn = make_preflight()

    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)

    assert "monitor_bug8" in calls
    assert results["monitor_bug8"] == "SUCCESS"


def test_monitor_bug8_e_pulado_se_sync_shopee_falhar():
    executor = make_executor(returncodes={"sync_produtos_shopee": 1})
    preflight_fn = make_preflight()

    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)

    assert results["sync_produtos_shopee"] == "FAILED"
    assert results["monitor_bug8"] == "SKIPPED"


def test_monitor_bug8_e_pulado_se_sync_shopee_foi_bloqueado_no_preflight():
    executor = make_executor()
    preflight_fn = make_preflight(blocked_sources=("produtos_shopee",))

    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)

    assert results["sync_produtos_shopee"] == "BLOCKED"
    assert results["monitor_bug8"] == "SKIPPED"


def test_monitor_bug8_e_pulado_se_sync_shopee_deu_timeout():
    executor = make_executor(timeout_on=("sync_produtos_shopee",))
    preflight_fn = make_preflight()

    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)

    assert results["sync_produtos_shopee"] == "FAILED"
    assert results["monitor_bug8"] == "SKIPPED"


def test_health_check_sempre_roda_mesmo_se_tudo_antes_falhou_em_full_daily():
    calls = []
    executor = make_executor(
        returncodes={"sync_produtos_ml": 1, "sync_produtos_tiktok": 1},
        calls=calls,
    )
    preflight_fn = make_preflight()

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert "health_check" in calls
    assert results["health_check"] == "SUCCESS"


def test_health_check_sempre_roda_mesmo_com_tudo_bloqueado_no_preflight_em_full_daily():
    calls = []
    executor = make_executor(calls=calls)
    preflight_fn = make_preflight(blocked_sources=("produtos_ml", "produtos_tiktok"))

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert "health_check" in calls
    assert results["health_check"] == "SUCCESS"


def test_health_check_sempre_roda_mesmo_se_tudo_antes_falhou_em_shopee_manual_refresh():
    calls = []
    executor = make_executor(
        returncodes={"daily_shopee_orders": 1, "daily_shopee_stats": 1, "daily_shopee_ads": 1, "sync_produtos_shopee": 1},
        calls=calls,
    )
    preflight_fn = make_preflight()

    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)

    assert "health_check" in calls
    assert results["health_check"] == "SUCCESS"
    assert results["monitor_bug8"] == "SKIPPED"


def test_pipeline_desconhecido_levanta_erro():
    with pytest.raises(ValueError):
        orch.run_pipeline("pipeline_que_nao_existe")


def test_pipelines_antigos_de_2_tarefas_nao_existem_mais():
    """Regressao explicita: o desenho anterior (2 tarefas agendadas
    separadamente, 'daily_ingestion' + 'produtos_and_monitor') foi
    substituido por pipelines orquestrados, porque a segunda tarefa podia
    comecar por horario antes da primeira ter terminado de verdade."""
    assert "daily_ingestion" not in orch.PIPELINES
    assert "produtos_and_monitor" not in orch.PIPELINES


def test_pipelines_disponiveis_sao_exatamente_os_tres_conhecidos():
    """Gate C1: full_daily + shopee_manual_refresh. Checkpoint O1 Task 2/2
    (2026-08-17): serving_refresh, a TaskKey MANUAL de contingencia — nenhum
    quarto pipeline, e nenhuma task Shopee ou de serving agendada por engano."""
    assert set(orch.PIPELINES) == {"full_daily", "shopee_manual_refresh", "serving_refresh"}


# ---------------------------------------------------------------------------
# Sequenciamento GLOBAL: health_check e' comprovadamente o ULTIMO passo,
# em qualquer combinacao de sucesso/falha/bloqueio/timeout anterior
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pipeline_name", ["full_daily", "shopee_manual_refresh"])
def test_health_check_e_sempre_o_ultimo_step_da_definicao_do_pipeline(pipeline_name):
    """Garantia estrutural: nao existe segunda tarefa/segundo pipeline
    agendado depois deste — health_check ser o ULTIMO item da tupla
    PIPELINES[pipeline_name] e' o que torna 'health check por ultimo,
    sempre' uma propriedade do desenho, nao uma esperanca de horario.
    Vale para os DOIS pipelines desde o Gate C1."""
    steps = orch.PIPELINES[pipeline_name]
    assert steps[-1].name == "health_check"
    assert steps[-1].always_run is True


@pytest.mark.parametrize("scenario", [
    {"returncodes": {}, "timeout_on": (), "blocked": ()},
    {"returncodes": {"daily_ml": 1}, "timeout_on": (), "blocked": ()},
    {"returncodes": {}, "timeout_on": ("daily_tiktok",), "blocked": ()},
    {"returncodes": {}, "timeout_on": (), "blocked": ("ml_daily", "produtos_ml")},
    {"returncodes": {}, "timeout_on": ("sync_produtos_tiktok",), "blocked": ("tiktok_daily",)},
])
def test_health_check_roda_por_ultimo_em_qualquer_cenario_misto_full_daily(scenario):
    """Executa o pipeline inteiro com uma mistura de sucesso/falha/timeout/
    bloqueio e confirma que health_check e' sempre o ULTIMO nome a ser
    chamado pelo executor, nunca antecipado."""
    calls = []
    executor = make_executor(returncodes=scenario["returncodes"], calls=calls, timeout_on=scenario["timeout_on"])
    preflight_fn = make_preflight(blocked_sources=scenario["blocked"])

    orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert calls[-1] == "health_check", f"health_check nao foi o ultimo chamado: {calls}"


@pytest.mark.parametrize("scenario", [
    {"returncodes": {}, "timeout_on": (), "blocked": ()},
    {"returncodes": {"sync_produtos_shopee": 1}, "timeout_on": ("daily_shopee_ads",), "blocked": ("shopee-stats_daily",)},
])
def test_health_check_roda_por_ultimo_em_qualquer_cenario_misto_shopee_manual_refresh(scenario):
    calls = []
    executor = make_executor(returncodes=scenario["returncodes"], calls=calls, timeout_on=scenario["timeout_on"])
    preflight_fn = make_preflight(blocked_sources=scenario["blocked"])

    orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)

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


@pytest.mark.parametrize("pipeline_name", ["full_daily", "shopee_manual_refresh"])
def test_todos_os_steps_tem_timeout_individual_positivo(pipeline_name):
    for step in orch.PIPELINES[pipeline_name]:
        assert step.timeout_seconds > 0, f"{step.name} sem timeout individual"


def test_shopee_orders_stats_ads_sao_passos_separados_no_shopee_manual_refresh():
    """Bug corrigido numa revisao anterior: a agenda antiga so' cobria
    'shopee', ignorando que daily_performance trata shopee/shopee-stats/
    shopee-ads como fontes distintas. Desde o Gate C1, esses 3 passos vivem
    em shopee_manual_refresh, nao mais em full_daily."""
    names = {step.name for step in orch.PIPELINES["shopee_manual_refresh"]}
    assert {"daily_shopee_orders", "daily_shopee_stats", "daily_shopee_ads"} <= names


# =============================================================================
# Gate C1 (2026-07-16) — separacao Shopee manual x full_daily automatico
# =============================================================================

def test_full_daily_nao_contem_nenhum_step_shopee():
    """Achado do Gate B6.1c: daily_shopee_orders estourando timeout
    derrubava full_daily inteiro. A correcao e' de desenho: Shopee (cadencia
    manual) nao pode mais viver dentro de full_daily (cadencia diaria
    automatica)."""
    names = {step.name for step in orch.PIPELINES["full_daily"]}
    shopee_step_names = {"daily_shopee_orders", "daily_shopee_stats", "daily_shopee_ads", "sync_produtos_shopee", "monitor_bug8"}
    assert names.isdisjoint(shopee_step_names)


def test_full_daily_contem_exatamente_as_fontes_recorrentes_na_ordem_correta():
    """Os tres steps de serving entraram no Checkpoint O1 Task 2/2 (2026-08-17)
    DEPOIS de toda a ingestao e ANTES do health_check. A posicao e' o contrato:
    e' o que garante 'serving depois das fontes do dia' sem depender de
    horario."""
    names = [step.name for step in orch.PIPELINES["full_daily"]]
    assert names == [
        "daily_ml",
        "daily_tiktok",
        "gold_regional_incremental",
        "sync_region_if_needed",
        "sync_produtos_ml",
        "sync_produtos_tiktok",
        "serving_ml",
        "serving_tiktok_brand",
        "serving_tiktok_creator",
        "serving_ml_cross_company",
        "serving_tiktok_channel_efficiency",
        # Gate UE2-C Task 2/3 (2026-08-28): depois da ingestao e ANTES do
        # health_check, como todo step que publica dado que uma tela le.
        "tiktok_affiliate_cost_order_monthly",
        "health_check",
    ]


def test_shopee_manual_refresh_contem_os_steps_shopee_na_ordem_correta():
    names = [step.name for step in orch.PIPELINES["shopee_manual_refresh"]]
    assert names == [
        "daily_shopee_orders",
        "daily_shopee_stats",
        "daily_shopee_ads",
        "sync_produtos_shopee",
        "monitor_bug8",
        "health_check",
    ]


def test_monitor_bug8_depende_de_sync_produtos_shopee_em_shopee_manual_refresh():
    steps_by_name = {step.name: step for step in orch.PIPELINES["shopee_manual_refresh"]}
    assert steps_by_name["monitor_bug8"].depends_on == ("sync_produtos_shopee",)


def test_sync_produtos_shopee_continua_critical_false():
    steps_by_name = {step.name: step for step in orch.PIPELINES["shopee_manual_refresh"]}
    assert steps_by_name["sync_produtos_shopee"].critical is False


def test_daily_shopee_orders_stats_ads_sao_criticos_dentro_do_pipeline_manual():
    """Dentro de shopee_manual_refresh (execucao manual e deliberada), os 3
    steps diarios Shopee SAO criticos — uma falha real deve aparecer como
    FAILED, nao um DEGRADED silencioso que mascararia uma carga incompleta."""
    steps_by_name = {step.name: step for step in orch.PIPELINES["shopee_manual_refresh"]}
    for name in ("daily_shopee_orders", "daily_shopee_stats", "daily_shopee_ads"):
        assert steps_by_name[name].critical is True, f"{name} deveria ser critical=True dentro de shopee_manual_refresh"


def test_falha_de_daily_shopee_orders_nao_afeta_full_daily():
    """daily_shopee_orders nem existe mais em full_daily — uma falha nele
    (via timeout_on/returncodes) nao pode influenciar full_daily de forma
    alguma, porque o executor de full_daily nunca vai sequer tentar
    chama-lo."""
    calls = []
    executor = make_executor(calls=calls, returncodes={"daily_shopee_orders": 1}, timeout_on=("daily_shopee_orders",))
    preflight_fn = make_preflight()

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert "daily_shopee_orders" not in calls
    assert "daily_shopee_orders" not in results
    assert orch.compute_overall_status("full_daily", results) == "OK"


def test_falha_de_daily_shopee_orders_falha_shopee_manual_refresh():
    executor = make_executor(timeout_on=("daily_shopee_orders",))
    preflight_fn = make_preflight()

    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)

    assert results["daily_shopee_orders"] == "FAILED"
    assert orch.compute_overall_status("shopee_manual_refresh", results) == "FAILED"


def test_full_daily_com_todas_as_fontes_shopee_bloqueadas_no_preflight_nao_vira_failed():
    """Shopee stale/ausente/bloqueado (mesmo TODAS as suas fontes de
    preflight) nao pode afetar full_daily de jeito nenhum — os steps
    correspondentes nem fazem parte deste pipeline desde o Gate C1."""
    executor = make_executor()
    preflight_fn = make_preflight(blocked_sources=("shopee_daily", "shopee-stats_daily", "shopee-ads_daily", "produtos_shopee"))

    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert orch.compute_overall_status("full_daily", results) == "OK"
    assert all(status == "SUCCESS" for status in results.values())


def test_full_daily_nunca_fica_degraded_pois_todos_os_steps_sao_criticos():
    """Diferente de antes do Gate C1 (quando sync_produtos_shopee, nao-
    critico, vivia aqui e podia degradar o pipeline): hoje TODOS os steps
    de full_daily sao critical=True, entao o unico resultado possivel
    quando algo falha/bloqueia e' FAILED, nunca DEGRADED."""
    for step in orch.PIPELINES["full_daily"]:
        assert step.critical is True, f"{step.name} deveria ser critical=True em full_daily (Gate C1)"


# =============================================================================
# Gate B1 — politica critico/nao-critico (Step.critical + compute_overall_status)
# =============================================================================

def test_compute_overall_status_ok_quando_tudo_passa():
    executor = make_executor()
    preflight_fn = make_preflight()
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)
    assert orch.compute_overall_status("full_daily", results) == "OK"


def test_compute_overall_status_failed_quando_step_critico_falha():
    executor = make_executor(returncodes={"daily_ml": 1})
    preflight_fn = make_preflight()
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)
    assert orch.compute_overall_status("full_daily", results) == "FAILED"


def test_compute_overall_status_failed_quando_step_critico_bloqueado_no_preflight():
    executor = make_executor()
    preflight_fn = make_preflight(blocked_sources=("ml_daily",))
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)
    assert results["daily_ml"] == "BLOCKED"
    assert orch.compute_overall_status("full_daily", results) == "FAILED"


def test_compute_overall_status_degraded_quando_produtos_shopee_bloqueado_por_local_pg_url():
    """Cenario central do Gate B1 (hoje dentro de shopee_manual_refresh,
    desde o Gate C1): produtos_shopee bloqueado (LOCAL_PG_URL ausente) e'
    um gap manual conhecido — o pipeline deve reportar DEGRADED, nunca
    FAILED, quando os steps Shopee diarios passaram."""
    executor = make_executor()
    preflight_fn = make_preflight(blocked_sources=("produtos_shopee",))
    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)
    assert results["sync_produtos_shopee"] == "BLOCKED"
    assert orch.compute_overall_status("shopee_manual_refresh", results) == "DEGRADED"


def test_compute_overall_status_degraded_quando_sync_produtos_shopee_falha_execucao():
    """Nao-critico tambem cobre FAILED (nao so' BLOCKED) — sync_produtos_shopee
    pode falhar por outro motivo (nao so' preflight) e ainda assim so'
    degradar, nao derrubar o pipeline."""
    executor = make_executor(returncodes={"sync_produtos_shopee": 1})
    preflight_fn = make_preflight()
    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)
    assert results["sync_produtos_shopee"] == "FAILED"
    assert orch.compute_overall_status("shopee_manual_refresh", results) == "DEGRADED"


def test_compute_overall_status_skipped_por_dependencia_nao_critica_nao_derruba_pipeline():
    """monitor_bug8 SKIPPED porque sync_produtos_shopee (nao-critico) foi
    bloqueado nao deve, por si so', contribuir para FAILED nem para um
    segundo motivo de DEGRADED — SKIPPED nunca conta como falha aqui."""
    executor = make_executor()
    preflight_fn = make_preflight(blocked_sources=("produtos_shopee",))
    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)
    assert results["monitor_bug8"] == "SKIPPED"
    # DEGRADED so' por causa do proprio sync_produtos_shopee (nao-critico);
    # nunca escala pra FAILED so' porque um step downstream foi pulado.
    assert orch.compute_overall_status("shopee_manual_refresh", results) == "DEGRADED"


def test_compute_overall_status_failed_tem_prioridade_sobre_degraded():
    """Se um step critico E um nao-critico falham na mesma execucao, o
    resultado e' FAILED (critico sempre vence), nunca DEGRADED."""
    executor = make_executor(returncodes={"daily_shopee_orders": 1, "sync_produtos_shopee": 1})
    preflight_fn = make_preflight()
    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)
    assert orch.compute_overall_status("shopee_manual_refresh", results) == "FAILED"


def test_compute_overall_status_ok_mesmo_com_monitor_bug8_skipped_por_falha_critica_upstream():
    """Caso diferente do 'skip nao-critico': aqui o proprio
    sync_produtos_shopee teve SUCCESS mas outro step CRITICO falhou em
    paralelo (sem relacao de dependencia) — nao deve mudar o resultado do
    proprio sync_produtos_shopee nem do monitor_bug8, so' confirma que
    FAILED de um step critico independente ainda e' pego corretamente."""
    executor = make_executor(returncodes={"daily_shopee_stats": 1})
    preflight_fn = make_preflight()
    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)
    assert results["sync_produtos_shopee"] == "SUCCESS"
    assert results["monitor_bug8"] == "SUCCESS"
    assert orch.compute_overall_status("shopee_manual_refresh", results) == "FAILED"


@pytest.mark.parametrize("scenario,expected", [
    ({"returncodes": {}, "blocked": ()}, "OK"),
    ({"returncodes": {"daily_ml": 1}, "blocked": ()}, "FAILED"),
])
def test_compute_overall_status_cenarios_mistos_full_daily(scenario, expected):
    executor = make_executor(returncodes=scenario["returncodes"])
    preflight_fn = make_preflight(blocked_sources=scenario["blocked"])
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)
    assert orch.compute_overall_status("full_daily", results) == expected


@pytest.mark.parametrize("scenario,expected", [
    ({"returncodes": {}, "blocked": ()}, "OK"),
    ({"returncodes": {}, "blocked": ("produtos_shopee",)}, "DEGRADED"),
    ({"returncodes": {"sync_produtos_shopee": 1}, "blocked": ("shopee_daily",)}, "FAILED"),
])
def test_compute_overall_status_cenarios_mistos_shopee_manual_refresh(scenario, expected):
    executor = make_executor(returncodes=scenario["returncodes"])
    preflight_fn = make_preflight(blocked_sources=scenario["blocked"])
    results = orch.run_pipeline("shopee_manual_refresh", executor=executor, preflight_fn=preflight_fn)
    assert orch.compute_overall_status("shopee_manual_refresh", results) == expected


# =============================================================================
# Gate B2 — gold_regional_incremental + sync_region_if_needed integrados a
# full_daily, ambos CRITICOS
# =============================================================================

def test_gold_regional_e_sync_region_estao_na_ordem_correta():
    """Ordem exata: os 2 steps regionais ficam depois das fontes diarias
    (ml, tiktok) e antes de qualquer sync de Produtos."""
    names = [step.name for step in orch.PIPELINES["full_daily"]]
    assert names.index("daily_tiktok") < names.index("gold_regional_incremental")
    assert names.index("gold_regional_incremental") < names.index("sync_region_if_needed")
    assert names.index("sync_region_if_needed") < names.index("sync_produtos_ml")


def test_gold_regional_e_sync_region_sao_criticos():
    steps_by_name = {step.name: step for step in orch.PIPELINES["full_daily"]}
    assert steps_by_name["gold_regional_incremental"].critical is True
    assert steps_by_name["sync_region_if_needed"].critical is True


def test_sync_region_if_needed_depende_de_gold_regional_incremental():
    steps_by_name = {step.name: step for step in orch.PIPELINES["full_daily"]}
    assert steps_by_name["sync_region_if_needed"].depends_on == ("gold_regional_incremental",)


def test_gold_regional_incremental_falha_execucao_vira_failed_no_geral():
    """Regional e' critico: uma falha de execucao real (nao so' preflight)
    tem que reprovar o pipeline inteiro (FAILED), nao so' degradar."""
    executor = make_executor(returncodes={"gold_regional_incremental": 1})
    preflight_fn = make_preflight()
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)
    assert results["gold_regional_incremental"] == "FAILED"
    assert orch.compute_overall_status("full_daily", results) == "FAILED"


def test_gold_regional_incremental_bloqueado_no_preflight_vira_failed_no_geral():
    executor = make_executor()
    preflight_fn = make_preflight(blocked_sources=("gold_regional_incremental",))
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)
    assert results["gold_regional_incremental"] == "BLOCKED"
    assert orch.compute_overall_status("full_daily", results) == "FAILED"


def test_sync_region_if_needed_e_pulado_se_gold_regional_incremental_falhar():
    executor = make_executor(returncodes={"gold_regional_incremental": 1})
    preflight_fn = make_preflight()
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)
    assert results["sync_region_if_needed"] == "SKIPPED"


def test_sync_region_if_needed_bloqueado_no_preflight_vira_failed_no_geral():
    executor = make_executor()
    preflight_fn = make_preflight(blocked_sources=("sync_region_daily",))
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)
    assert results["sync_region_if_needed"] == "BLOCKED"
    assert orch.compute_overall_status("full_daily", results) == "FAILED"


def test_gold_regional_incremental_chama_o_modulo_certo_com_flag_incremental():
    steps_by_name = {step.name: step for step in orch.PIPELINES["full_daily"]}
    step = steps_by_name["gold_regional_incremental"]
    assert step.module == "pipelines.ingestion.gold_regional.loader"
    assert step.args == ("--incremental",)


def test_sync_region_if_needed_chama_o_modulo_certo_sem_flags():
    """sync_region_if_needed.py nao aceita/precisa de flags — a decisao
    diagnose-then-maybe-sync e' sempre a mesma, ao contrario de
    sync_region_daily.py (que tem --diagnose/--sync)."""
    steps_by_name = {step.name: step for step in orch.PIPELINES["full_daily"]}
    step = steps_by_name["sync_region_if_needed"]
    assert step.module == "pipelines.ops.sync_region_if_needed"
    assert step.args == ()


# =============================================================================
# Checkpoint O1 Task 2/2 (2026-08-17) — ponte de serving no full_daily
# =============================================================================

SERVING_STEPS = ["serving_ml", "serving_tiktok_brand", "serving_tiktok_creator"]


def _por_nome(pipeline="full_daily"):
    return {s.name: s for s in orch.PIPELINES[pipeline]}


def test_serving_ml_depende_so_de_daily_ml():
    assert _por_nome()["serving_ml"].depends_on == ("daily_ml",)


@pytest.mark.parametrize("nome", ["serving_tiktok_brand", "serving_tiktok_creator"])
def test_serving_tiktok_depende_so_de_daily_tiktok(nome):
    assert _por_nome()[nome].depends_on == ("daily_tiktok",)


def test_nao_existe_dependencia_cruzada_entre_canais():
    """ML nunca depende de TikTok e vice-versa. Uma dependencia comum faria a
    VPN cair para um canal e congelar o serving do outro de graca."""
    p = _por_nome()
    assert "daily_tiktok" not in p["serving_ml"].depends_on
    for nome in ("serving_tiktok_brand", "serving_tiktok_creator"):
        assert "daily_ml" not in p[nome].depends_on


def test_falha_de_daily_tiktok_bloqueia_so_o_serving_de_tiktok():
    """Item central do contrato: uma fonte de canal quebrada impede SOMENTE o
    serving daquele canal. serving_ml continua rodando."""
    calls = []
    executor = make_executor(returncodes={"daily_tiktok": 1}, calls=calls)
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=make_preflight())

    assert results["serving_tiktok_brand"] == "SKIPPED"
    assert results["serving_tiktok_creator"] == "SKIPPED"
    assert results["serving_ml"] == "SUCCESS"
    assert "serving_ml" in calls
    assert "serving_tiktok_brand" not in calls
    assert "serving_tiktok_creator" not in calls


def test_falha_de_daily_ml_bloqueia_so_o_serving_de_ml():
    calls = []
    executor = make_executor(returncodes={"daily_ml": 1}, calls=calls)
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=make_preflight())

    assert results["serving_ml"] == "SKIPPED"
    assert results["serving_tiktok_brand"] == "SUCCESS"
    assert results["serving_tiktok_creator"] == "SUCCESS"
    assert "serving_ml" not in calls


def test_fonte_de_canal_bloqueada_no_preflight_tambem_pula_so_aquele_serving():
    calls = []
    executor = make_executor(calls=calls)
    preflight_fn = make_preflight(blocked_sources=("tiktok_daily",))
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)

    assert results["daily_tiktok"] == "BLOCKED"
    assert results["serving_tiktok_brand"] == "SKIPPED"
    assert results["serving_ml"] == "SUCCESS"


def test_timeout_da_fonte_do_canal_pula_o_serving_daquele_canal():
    calls = []
    executor = make_executor(calls=calls, timeout_on=("daily_tiktok",))
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=make_preflight())

    assert results["daily_tiktok"] == "FAILED"
    assert results["serving_tiktok_creator"] == "SKIPPED"
    assert results["serving_ml"] == "SUCCESS"


def test_16_falha_de_brand_nao_impede_o_orquestrador_de_avaliar_creator():
    """Sao STEPS separados, com invocacoes separadas do wrapper: brand falhar
    nao cancela creator, cuja dependencia (daily_tiktok) foi satisfeita. E o
    oposto do --table all do CLI, onde o primeiro erro aborta o laco."""
    calls = []
    executor = make_executor(returncodes={"serving_tiktok_brand": 1}, calls=calls)
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=make_preflight())

    assert results["serving_tiktok_brand"] == "FAILED"
    assert results["serving_tiktok_creator"] == "SUCCESS"
    assert "serving_tiktok_creator" in calls


def test_17_falha_de_serving_nao_desfaz_ingestao_ja_concluida():
    """Os steps de ingestao rodaram e mantiveram SUCCESS: o orquestrador nao tem
    caminho que reverta um step anterior por causa de uma falha posterior. Cada
    carga tem transacao propria."""
    calls = []
    executor = make_executor(
        returncodes={"serving_ml": 1, "serving_tiktok_brand": 2, "serving_tiktok_creator": 124},
        calls=calls,
    )
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=make_preflight())

    assert results["daily_ml"] == "SUCCESS"
    assert results["daily_tiktok"] == "SUCCESS"
    assert results["sync_produtos_ml"] == "SUCCESS"
    assert results["sync_produtos_tiktok"] == "SUCCESS"
    assert results["gold_regional_incremental"] == "SUCCESS"
    for nome in ("daily_ml", "daily_tiktok", "sync_produtos_ml", "sync_produtos_tiktok"):
        assert calls.count(nome) == 1


@pytest.mark.parametrize("nome", SERVING_STEPS)
def test_18_falha_de_qualquer_serving_derruba_o_pipeline_para_failed(nome):
    """Decisao explicita do Checkpoint O1: critical=True. /operacoes ja esta em
    producao lendo estas tabelas, entao defasagem tem que aparecer como FAILED."""
    executor = make_executor(returncodes={nome: 1})
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=make_preflight())
    assert orch.compute_overall_status("full_daily", results) == "FAILED"


@pytest.mark.parametrize("nome", SERVING_STEPS)
def test_18_serving_bloqueado_no_preflight_tambem_vira_failed(nome):
    step = _por_nome()[nome]
    executor = make_executor()
    preflight_fn = make_preflight(blocked_sources=(step.preflight_source,))
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=preflight_fn)
    assert results[nome] == "BLOCKED"
    assert orch.compute_overall_status("full_daily", results) == "FAILED"


@pytest.mark.parametrize("nome", SERVING_STEPS)
def test_18_todos_os_steps_de_serving_sao_criticos(nome):
    assert _por_nome()[nome].critical is True


def test_19_sucesso_dos_tres_permite_health_check_executar():
    calls = []
    executor = make_executor(calls=calls)
    results = orch.run_pipeline("full_daily", executor=executor, preflight_fn=make_preflight())

    assert all(results[n] == "SUCCESS" for n in SERVING_STEPS)
    assert calls[-1] == "health_check"
    assert orch.compute_overall_status("full_daily", results) == "OK"


def test_19_health_check_roda_mesmo_com_os_tres_servings_falhando():
    calls = []
    executor = make_executor(returncodes={n: 1 for n in SERVING_STEPS}, calls=calls)
    orch.run_pipeline("full_daily", executor=executor, preflight_fn=make_preflight())
    assert calls[-1] == "health_check"


def test_20_serving_roda_depois_de_toda_a_ingestao_e_antes_do_health_check():
    calls = []
    executor = make_executor(calls=calls)
    orch.run_pipeline("full_daily", executor=executor, preflight_fn=make_preflight())

    ingestao = ["daily_ml", "daily_tiktok", "gold_regional_incremental",
                "sync_region_if_needed", "sync_produtos_ml", "sync_produtos_tiktok"]
    for fonte in ingestao:
        for serv in SERVING_STEPS:
            assert calls.index(fonte) < calls.index(serv), f"{serv} rodou antes de {fonte}"
    for serv in SERVING_STEPS:
        assert calls.index(serv) < calls.index("health_check")


def test_20_ordem_interna_do_serving_e_ml_brand_creator():
    calls = []
    executor = make_executor(calls=calls)
    orch.run_pipeline("full_daily", executor=executor, preflight_fn=make_preflight())
    assert [c for c in calls if c in SERVING_STEPS] == SERVING_STEPS


def test_21_nenhum_step_de_serving_toca_shopee():
    for pipeline in ("full_daily", "serving_refresh"):
        for step in orch.PIPELINES[pipeline]:
            if step.name in SERVING_STEPS:
                assert "shopee" not in step.name.lower()
                assert "shopee" not in step.module.lower()
                assert not any("shopee" in a.lower() for a in step.args)


def test_21_full_daily_continua_sem_nenhum_step_shopee_depois_da_ponte():
    names = {s.name for s in orch.PIPELINES["full_daily"]}
    shopee = {"daily_shopee_orders", "daily_shopee_stats", "daily_shopee_ads",
              "sync_produtos_shopee", "monitor_bug8"}
    assert names.isdisjoint(shopee)


def test_21_serving_refresh_nao_contem_nenhum_step_shopee():
    names = {s.name for s in orch.PIPELINES["serving_refresh"]}
    shopee = {"daily_shopee_orders", "daily_shopee_stats", "daily_shopee_ads",
              "sync_produtos_shopee", "monitor_bug8"}
    assert names.isdisjoint(shopee)


def test_22_serving_refresh_contem_somente_os_tres_steps_de_serving():
    assert [s.name for s in orch.PIPELINES["serving_refresh"]] == SERVING_STEPS


def test_22_serving_refresh_nao_tem_health_check():
    """health_check reprova por fontes ALHEIAS ao serving (em 17/08 saiu 1 so
    por ml_produto_ranking): incluir aqui faria uma recuperacao bem-sucedida
    reportar FAILED por um motivo que ela nao pode resolver."""
    assert "health_check" not in {s.name for s in orch.PIPELINES["serving_refresh"]}


def test_22_serving_refresh_nao_tem_ingestao_nem_produtos_nem_regional():
    names = {s.name for s in orch.PIPELINES["serving_refresh"]}
    assert names.isdisjoint({"daily_ml", "daily_tiktok", "sync_produtos_ml",
                             "sync_produtos_tiktok", "gold_regional_incremental",
                             "sync_region_if_needed"})


def test_22_steps_de_serving_refresh_nao_dependem_de_step_inexistente():
    """Um depends_on apontando para um step que nao existe NESTE pipeline faria
    todos virarem SKIPPED e a contingencia nunca rodaria."""
    nomes = {s.name for s in orch.PIPELINES["serving_refresh"]}
    for step in orch.PIPELINES["serving_refresh"]:
        assert set(step.depends_on) <= nomes
        assert step.depends_on == ()


def test_22_serving_refresh_executa_os_tres_de_fato():
    calls = []
    executor = make_executor(calls=calls)
    results = orch.run_pipeline("serving_refresh", executor=executor, preflight_fn=make_preflight())
    assert calls == SERVING_STEPS
    assert all(v == "SUCCESS" for v in results.values())
    assert orch.compute_overall_status("serving_refresh", results) == "OK"


def test_22_serving_refresh_falha_vira_failed():
    executor = make_executor(returncodes={"serving_tiktok_creator": 2})
    results = orch.run_pipeline("serving_refresh", executor=executor, preflight_fn=make_preflight())
    assert orch.compute_overall_status("serving_refresh", results) == "FAILED"


def test_22_mesmo_comando_e_criticidade_nos_dois_pipelines():
    """Os dois pipelines derivam do mesmo construtor: comando, preflight,
    timeout e criticidade nao podem divergir entre eles."""
    fd = _por_nome("full_daily")
    sr = _por_nome("serving_refresh")
    for nome in SERVING_STEPS:
        assert fd[nome].module == sr[nome].module
        assert fd[nome].args == sr[nome].args
        assert fd[nome].timeout_seconds == sr[nome].timeout_seconds
        assert fd[nome].preflight_source == sr[nome].preflight_source
        assert fd[nome].critical is True and sr[nome].critical is True
        assert fd[nome].depends_on != () and sr[nome].depends_on == ()


@pytest.mark.parametrize("nome", SERVING_STEPS)
def test_cada_step_de_serving_chama_o_wrapper_com_um_unico_target_e_apply(nome):
    step = _por_nome()[nome]
    assert step.module == "pipelines.ops.serving_refresh"
    assert step.args[0] == "--target"
    assert step.args[2] == "--apply"
    assert step.args.count("--target") == 1
    assert step.args.count("--apply") == 1
    assert "all" not in step.args


def test_os_tres_targets_do_wrapper_estao_cobertos_sem_repeticao():
    targets = [s.args[1] for s in orch.PIPELINES["full_daily"] if s.name in SERVING_STEPS]
    assert targets == ["ml", "brand", "creator"]
    assert len(set(targets)) == 3


def test_24_nenhuma_task_do_windows_e_criada_por_este_modulo():
    baixo = MODULE_PATH.read_text(encoding="utf-8").lower()
    for termo in ("schtasks", "register-scheduledtask", "new-scheduledtask",
                  "set-scheduledtask", "enable-scheduledtask", "start-scheduledtask"):
        assert termo not in baixo, termo


def test_24_nenhuma_referencia_a_airflow_ou_dag():
    baixo = MODULE_PATH.read_text(encoding="utf-8").lower()
    assert "airflow" not in baixo


def test_27_orcamento_interno_dos_tres_pipelines_cabe_no_timeout_externo():
    EXTERNO = 9000
    for budget in (orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS,
                   orch.SHOPEE_MANUAL_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS,
                   orch.SERVING_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS):
        assert budget < EXTERNO
        assert (EXTERNO - budget) > 0.15 * budget, "margem de seguranca insuficiente"


def test_27_orcamento_do_full_daily_e_a_soma_real_incluindo_serving():
    assert orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS == 7800
    assert orch.SERVING_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS == 3000


def test_27_creator_tem_orcamento_maior_que_ml_e_brand():
    """66.347 linhas numa janela de 90 dias, contra 360 do ML e 450 do brand."""
    p = _por_nome()
    assert p["serving_tiktok_creator"].timeout_seconds > p["serving_ml"].timeout_seconds
    assert p["serving_tiktok_creator"].timeout_seconds > p["serving_tiktok_brand"].timeout_seconds


def test_28_os_sete_steps_antigos_mantem_comando_criticidade_e_dependencia():
    """Trava o contrato preexistente: a ponte NAO pode ter mudado nenhum dos
    sete steps que ja rodavam."""
    esperado = {
        "daily_ml": ("pipelines.ingestion.daily_performance", ("--source", "ml", "--mode", "incremental"), 900, "ml_daily", (), True, False),
        # --days 10 adicionado em 18/08/2026 (ver test_29_*): unica alteracao
        # intencional neste conjunto; os outros seis seguem congelados.
        "daily_tiktok": ("pipelines.ingestion.daily_performance", ("--source", "tiktok", "--mode", "incremental", "--days", "10"), 900, "tiktok_daily", (), True, False),
        "gold_regional_incremental": ("pipelines.ingestion.gold_regional.loader", ("--incremental",), 300, "gold_regional_incremental", (), True, False),
        "sync_region_if_needed": ("pipelines.ops.sync_region_if_needed", (), 120, "sync_region_daily", ("gold_regional_incremental",), True, False),
        "sync_produtos_ml": ("pipelines.sync_produtos", ("--source", "ml"), 600, "produtos_ml", (), True, False),
        "sync_produtos_tiktok": ("pipelines.sync_produtos", ("--source", "tiktok"), 600, "produtos_tiktok", (), True, False),
        "health_check": ("pipelines.ops.health_check", ("--json",), 180, None, (), True, True),
    }
    p = _por_nome()
    for nome, alvo in esperado.items():
        s = p[nome]
        assert (s.module, s.args, s.timeout_seconds, s.preflight_source,
                s.depends_on, s.critical, s.always_run) == alvo, nome


def test_29_lookback_do_tiktok_cobre_a_maturacao_de_status():
    """O TikTok precisa de janela maior que o default de 3 dias.

    Medicao de 18/08/2026 em raw.tiktok_shop_orders: o GMV so' conta
    COMPLETED/DELIVERED/IN_TRANSIT, e o pedido leva mediana de 5,1 dias ate
    DELIVERED (p90 8,3) e 9,0 dias ate IN_TRANSIT. Com lookback de 3 dias o
    dia saia da rotina ainda ~50% imaturo e nunca era revisitado — foi o que
    congelou 14/08 em -59,7% do valor real. Este teste impede que a janela
    volte silenciosamente para o default.
    """
    p = _por_nome()
    args = p["daily_tiktok"].args
    assert "--days" in args, "daily_tiktok precisa de --days explicito"
    dias = int(args[args.index("--days") + 1])
    assert dias >= 8, (
        f"lookback do TikTok e' {dias}; precisa cobrir a estabilizacao "
        "observada (~8 dias) para nao deixar dia imaturo orfao")
    assert dias == 10, "valor acordado no gate de 18/08/2026"


def test_29_ml_mantem_o_lookback_default():
    """A mudanca e' cirurgica: so' o TikTok. O ML tem revisao retroativa por
    cancelamento, de magnitude bem menor (0,17% do mes de agosto), e sua
    janela nao foi alterada neste gate."""
    p = _por_nome()
    assert "--days" not in p["daily_ml"].args


def test_29_lookback_cabe_no_timeout_do_step():
    """Custo medido em 18/08/2026: o fetch de 17 dias levou 43s. Dez dias
    ficam bem abaixo do timeout do step, com folga de mais de uma ordem de
    grandeza."""
    p = _por_nome()
    assert p["daily_tiktok"].timeout_seconds == 900

def test_28_shopee_manual_refresh_ficou_intacto():
    names = [s.name for s in orch.PIPELINES["shopee_manual_refresh"]]
    assert names == ["daily_shopee_orders", "daily_shopee_stats", "daily_shopee_ads",
                     "sync_produtos_shopee", "monitor_bug8", "health_check"]
    assert orch.SHOPEE_MANUAL_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS == 3780
