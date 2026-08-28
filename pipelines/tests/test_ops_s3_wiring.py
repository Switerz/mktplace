"""Gate S3 — wiring dos dois snapshots no full_daily (Parte F).

Comportamental: o pipeline e' executado inteiro com executor e preflight injetados,
sem subprocesso e sem banco. O que estes testes protegem:

- ordem: os dois snapshots depois das fontes de que cada tela depende, antes do
  health_check;
- dependencias explicitas, e o SKIP correto quando a dependencia falha;
- `critical=True` nos dois, com falha virando FAILED no resultado geral;
- orcamento interno somado abaixo do timeout externo de 9.000s;
- allowlist do preflight;
- zero Shopee no full_daily;
- os dois snapshots NAO passam pelo wrapper do O1, que permaneceu intocado;
- nenhuma tarefa do Windows e nenhuma referencia a Airflow.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import pipelines.ops.orchestrate as orch
import pipelines.ops.preflight as preflight
import pipelines.ops.serving_refresh as sr
import pipelines.sync_serving_snapshots as ss

MODULE_PATH = Path(orch.__file__)

SNAPSHOTS = ["serving_ml_cross_company", "serving_tiktok_channel_efficiency"]
SERVING_O1 = ["serving_ml", "serving_tiktok_brand", "serving_tiktok_creator"]
INGESTAO = ["daily_ml", "daily_tiktok", "gold_regional_incremental",
            "sync_region_if_needed", "sync_produtos_ml", "sync_produtos_tiktok"]


def make_preflight(blocked=()):
    def _fn(source):
        return (source not in blocked), []
    return _fn


def make_executor(returncodes=None, calls=None, timeout_on=()):
    returncodes = returncodes or {}
    calls = calls if calls is not None else []

    def _fn(step):
        calls.append(step.name)
        if step.name in timeout_on:
            raise subprocess.TimeoutExpired(cmd=step.module, timeout=step.timeout_seconds)
        return returncodes.get(step.name, 0)
    return _fn


def por_nome(pipeline="full_daily"):
    return {s.name: s for s in orch.PIPELINES[pipeline]}


# ===========================================================================
# Ordem
# ===========================================================================

#: Gate UE2-C Task 2/3 (2026-08-28): entre os snapshots e o health_check.
UE2C = ["tiktok_affiliate_cost_order_monthly"]


def test_f01_full_daily_tem_13_steps_na_ordem_exata():
    assert [s.name for s in orch.PIPELINES["full_daily"]] == (
        INGESTAO + SERVING_O1 + SNAPSHOTS + UE2C + ["health_check"])


def test_ue2c_step_fica_antes_do_health_check_que_segue_sendo_o_ultimo():
    """A posicao e' o contrato: o health_check tem de continuar sendo o ULTIMO
    step global, senao ele reportaria um estado anterior ao do proprio dia."""
    nomes = [s.name for s in orch.PIPELINES["full_daily"]]
    assert nomes[-1] == "health_check"
    assert nomes[-2] == "tiktok_affiliate_cost_order_monthly"
    assert orch.PIPELINES["full_daily"][-1].always_run is True


def test_f02_snapshots_rodam_depois_de_toda_a_ingestao_e_do_serving_o1():
    calls = []
    orch.run_pipeline("full_daily", executor=make_executor(calls=calls),
                      preflight_fn=make_preflight())
    for antes in INGESTAO + SERVING_O1:
        for snap in SNAPSHOTS:
            assert calls.index(antes) < calls.index(snap), f"{snap} antes de {antes}"


def test_f03_health_check_continua_o_ultimo_e_always_run():
    steps = orch.PIPELINES["full_daily"]
    assert steps[-1].name == "health_check"
    assert steps[-1].always_run is True
    calls = []
    orch.run_pipeline("full_daily", executor=make_executor(calls=calls),
                      preflight_fn=make_preflight())
    assert calls[-1] == "health_check"


def test_f04_ml_cross_company_roda_antes_do_channel_efficiency():
    calls = []
    orch.run_pipeline("full_daily", executor=make_executor(calls=calls),
                      preflight_fn=make_preflight())
    assert calls.index("serving_ml_cross_company") < calls.index("serving_tiktok_channel_efficiency")


# ===========================================================================
# Dependencias
# ===========================================================================

def test_f05_ml_cross_company_depende_de_sync_produtos_ml():
    """`/inteligencia` le os DOIS snapshots ML na mesma tela: publicar um sem o
    outro serviria metades de instantes diferentes."""
    assert por_nome()["serving_ml_cross_company"].depends_on == ("sync_produtos_ml",)


def test_f06_channel_efficiency_depende_das_tres_fontes_de_brand_detail():
    dep = por_nome()["serving_tiktok_channel_efficiency"].depends_on
    assert set(dep) == {"sync_produtos_tiktok", "serving_tiktok_brand", "serving_tiktok_creator"}


def test_f07_falha_de_sync_produtos_ml_pula_so_o_snapshot_ml():
    calls = []
    results = orch.run_pipeline(
        "full_daily", executor=make_executor(returncodes={"sync_produtos_ml": 1}, calls=calls),
        preflight_fn=make_preflight())
    assert results["serving_ml_cross_company"] == "SKIPPED"
    assert "serving_ml_cross_company" not in calls
    assert results["serving_tiktok_channel_efficiency"] == "SUCCESS"


@pytest.mark.parametrize("quebrado", ["sync_produtos_tiktok", "serving_tiktok_brand",
                                      "serving_tiktok_creator"])
def test_f08_falha_de_qualquer_dependencia_tiktok_pula_o_channel_efficiency(quebrado):
    calls = []
    results = orch.run_pipeline(
        "full_daily", executor=make_executor(returncodes={quebrado: 1}, calls=calls),
        preflight_fn=make_preflight())
    assert results["serving_tiktok_channel_efficiency"] == "SKIPPED"
    assert "serving_tiktok_channel_efficiency" not in calls


def test_f09_falha_de_tiktok_nao_impede_o_snapshot_ml():
    results = orch.run_pipeline(
        "full_daily", executor=make_executor(returncodes={"daily_tiktok": 1}),
        preflight_fn=make_preflight())
    assert results["serving_ml_cross_company"] == "SUCCESS"


def test_f10_falha_de_ml_nao_impede_o_snapshot_tiktok():
    results = orch.run_pipeline(
        "full_daily", executor=make_executor(returncodes={"sync_produtos_ml": 1}),
        preflight_fn=make_preflight())
    assert results["serving_tiktok_channel_efficiency"] == "SUCCESS"


def test_f11_bloqueio_no_preflight_do_snapshot_nao_afeta_o_outro():
    results = orch.run_pipeline(
        "full_daily", executor=make_executor(),
        preflight_fn=make_preflight(blocked=("serving_ml_cross_company",)))
    assert results["serving_ml_cross_company"] == "BLOCKED"
    assert results["serving_tiktok_channel_efficiency"] == "SUCCESS"


def test_f12_dependencias_apontam_para_steps_que_existem_no_pipeline():
    nomes = {s.name for s in orch.PIPELINES["full_daily"]}
    for s in orch.PIPELINES["full_daily"]:
        assert set(s.depends_on) <= nomes, f"{s.name} depende de step inexistente"


# ===========================================================================
# Criticidade e propagacao
# ===========================================================================

@pytest.mark.parametrize("nome", SNAPSHOTS)
def test_f13_snapshots_sao_criticos(nome):
    assert por_nome()[nome].critical is True


@pytest.mark.parametrize("nome", SNAPSHOTS)
def test_f14_falha_de_snapshot_derruba_o_pipeline_para_failed(nome):
    results = orch.run_pipeline("full_daily", executor=make_executor(returncodes={nome: 1}),
                               preflight_fn=make_preflight())
    assert orch.compute_overall_status("full_daily", results) == "FAILED"


@pytest.mark.parametrize("nome", SNAPSHOTS)
def test_f15_snapshot_bloqueado_no_preflight_tambem_vira_failed(nome):
    step = por_nome()[nome]
    results = orch.run_pipeline("full_daily", executor=make_executor(),
                               preflight_fn=make_preflight(blocked=(step.preflight_source,)))
    assert results[nome] == "BLOCKED"
    assert orch.compute_overall_status("full_daily", results) == "FAILED"


def test_f16_sucesso_dos_doze_produz_ok():
    results = orch.run_pipeline("full_daily", executor=make_executor(),
                               preflight_fn=make_preflight())
    assert all(v == "SUCCESS" for v in results.values())
    assert orch.compute_overall_status("full_daily", results) == "OK"


def test_f17_falha_de_snapshot_nao_desfaz_ingestao_ja_concluida():
    """O rollback e' da transacao do sync; o orquestrador nunca reverte step
    anterior. Cada step roda uma vez."""
    calls = []
    results = orch.run_pipeline(
        "full_daily",
        executor=make_executor(returncodes={n: 1 for n in SNAPSHOTS}, calls=calls),
        preflight_fn=make_preflight())
    for nome in INGESTAO + SERVING_O1:
        assert results[nome] == "SUCCESS"
        assert calls.count(nome) == 1


def test_f18_timeout_de_snapshot_nao_impede_o_health_check():
    calls = []
    orch.run_pipeline("full_daily",
                      executor=make_executor(calls=calls, timeout_on=("serving_tiktok_creator",)),
                      preflight_fn=make_preflight())
    assert calls[-1] == "health_check"


# ===========================================================================
# Comando, timeout e orcamento
# ===========================================================================

@pytest.mark.parametrize("nome,target", list(zip(SNAPSHOTS, ss.TARGET_ORDER)))
def test_f19_step_chama_o_modulo_proprio_com_um_target(nome, target):
    s = por_nome()[nome]
    assert s.module == "pipelines.sync_serving_snapshots"
    assert s.args == ("--target", target, "--apply")
    assert s.args.count("--apply") == 1


@pytest.mark.parametrize("nome", SNAPSHOTS)
def test_f20_step_nao_usa_o_wrapper_do_o1(nome):
    assert por_nome()[nome].module != "pipelines.ops.serving_refresh"


def test_f21_o_wrapper_do_o1_permaneceu_com_tres_targets():
    assert set(sr.TARGETS) == {"ml", "brand", "creator"}
    assert sr.TARGET_ORDER == ("ml", "brand", "creator")


def test_f22_timeouts_proporcionais_ao_volume():
    """Quatro linhas contra ~4,7 mil: o channel efficiency recebe o dobro."""
    p = por_nome()
    assert p["serving_ml_cross_company"].timeout_seconds == 300
    assert p["serving_tiktok_channel_efficiency"].timeout_seconds == 600
    assert (p["serving_tiktok_channel_efficiency"].timeout_seconds
            > p["serving_ml_cross_company"].timeout_seconds)


def test_f23_timeout_do_step_vem_da_spec_do_modulo():
    """Uma fonte de verdade: mudar a spec muda o step, sem editar dois lugares."""
    for nome, target in zip(SNAPSHOTS, ss.TARGET_ORDER):
        assert por_nome()[nome].timeout_seconds == ss.SPECS[target].step_timeout_seconds


def test_f24_orcamento_interno_cabe_no_timeout_externo():
    EXTERNO = 9000
    assert orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS == 7800
    assert orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS < EXTERNO
    margem = EXTERNO - orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS
    assert margem > 0.15 * orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS


def test_f25_orcamento_e_a_soma_real_dos_doze_steps():
    soma = sum(s.timeout_seconds for s in orch.PIPELINES["full_daily"])
    assert soma == orch.FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS == 7800


def test_f26_os_outros_dois_pipelines_nao_mudaram():
    assert [s.name for s in orch.PIPELINES["serving_refresh"]] == SERVING_O1
    assert orch.SERVING_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS == 3000
    assert orch.SHOPEE_MANUAL_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS == 3780
    assert sorted(orch.PIPELINES) == ["full_daily", "serving_refresh", "shopee_manual_refresh"]


# ===========================================================================
# Preflight
# ===========================================================================

@pytest.mark.parametrize("fonte", ["serving_ml_cross_company",
                                   "serving_tiktok_channel_efficiency"])
def test_f27_preflight_do_snapshot_exige_rds_neon_e_tabelas(fonte):
    checks = preflight.SOURCE_CHECKS[fonte]
    assert preflight.check_rds in checks
    assert preflight.check_neon in checks
    assert preflight.check_serving_s3_tables in checks


def test_f28_preflight_dos_snapshots_sao_fontes_separadas():
    a = por_nome()["serving_ml_cross_company"].preflight_source
    b = por_nome()["serving_tiktok_channel_efficiency"].preflight_source
    assert a != b
    assert {a, b} <= set(preflight.SOURCE_CHECKS)


def test_f29_preflight_nao_usa_check_de_shopee_nem_pg_local():
    for fonte in ("serving_ml_cross_company", "serving_tiktok_channel_efficiency"):
        checks = preflight.SOURCE_CHECKS[fonte]
        assert preflight.check_local_pg not in checks
        assert preflight.check_shopee_orders_files not in checks


def test_f30_check_das_tabelas_do_s3_lista_as_duas_novas():
    assert set(preflight._SERVING_S3_TARGET_TABLES) == {
        "marts.fact_ml_cross_company_summary",
        "marts.fact_tiktok_channel_efficiency_daily",
    }


def _sem_docstring(fn) -> str:
    """Codigo da funcao sem o docstring: o docstring de
    `check_tiktok_product_content_columns` EXPLICA que sem as colunas o sync
    "falharia no INSERT", e um grep no texto bruto casaria com a explicacao."""
    import ast
    import inspect
    import textwrap
    arvore = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    fdef = arvore.body[0]
    corpo = fdef.body
    if (corpo and isinstance(corpo[0], ast.Expr) and isinstance(corpo[0].value, ast.Constant)
            and isinstance(corpo[0].value.value, str)):
        corpo.pop(0)
    return ast.unparse(arvore)


def test_f31_check_das_colunas_do_produto_existe_e_e_read_only():
    codigo = _sem_docstring(preflight.check_tiktok_product_content_columns)
    assert "information_schema.columns" in codigo
    assert "readonly=True" in codigo
    assert "active_videos" in codigo and "video_views" in codigo
    for verbo in ("INSERT", "UPDATE ", "DELETE", "ALTER", "CREATE"):
        assert verbo not in codigo.upper(), verbo


def test_f32_preflight_do_s3_nao_le_linha_das_fatos():
    import inspect
    src = inspect.getsource(preflight.check_serving_s3_tables)
    assert "to_regclass" in src
    assert "FROM marts." not in src
    assert "COUNT(" not in src.upper()


# ===========================================================================
# Zero Shopee, zero Scheduler, zero Airflow
# ===========================================================================

def test_f33_full_daily_continua_sem_step_shopee():
    nomes = {s.name for s in orch.PIPELINES["full_daily"]}
    assert nomes.isdisjoint({"daily_shopee_orders", "daily_shopee_stats",
                             "daily_shopee_ads", "sync_produtos_shopee", "monitor_bug8"})
    for s in orch.PIPELINES["full_daily"]:
        assert "shopee" not in s.name.lower()
        assert "shopee" not in s.module.lower()


def test_f34_nenhuma_task_do_windows_e_criada_ou_alterada():
    baixo = MODULE_PATH.read_text(encoding="utf-8").lower()
    for termo in ("schtasks", "register-scheduledtask", "new-scheduledtask",
                  "set-scheduledtask", "enable-scheduledtask", "start-scheduledtask"):
        assert termo not in baixo, termo


def test_f35_nenhuma_referencia_a_airflow():
    for caminho in (MODULE_PATH, Path(preflight.__file__), Path(ss.__file__)):
        assert "airflow" not in caminho.read_text(encoding="utf-8").lower()


def test_f36_agenda_do_windows_continua_com_uma_tarefa():
    import pipelines.ops.schedule_plan as spl
    assert len(spl.PROPOSED_SCHEDULE) == 1
    assert spl.PROPOSED_SCHEDULE[0].task_key == "full_daily"
