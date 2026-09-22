"""Gate PMA-2C5B — o pipeline `pma_refresh`.

O que estes testes travam, em uma frase: os tres canais existem, nesta ordem,
sao independentes, e o desfecho de cada publisher chega ao resumo com o NOME
que ele tem — recusa nao vira sucesso, lock ocupado nao vira falha, e commit
indeterminado nao vira nem uma coisa nem outra.

Nenhum teste aqui executa publisher, abre conexao ou toca banco: `executor` e
`preflight_fn` sao injetados, como no resto da suite do orquestrador.
"""
from __future__ import annotations

import pytest

from pipelines.ops import orchestrate as orch


# ---------------------------------------------------------------------------
# 1. O plano
# ---------------------------------------------------------------------------

def test_pma_refresh_existe_e_e_pipeline_proprio():
    """Nao esta dentro de `full_daily`: precisa poder ser desligado sozinho."""
    assert "pma_refresh" in orch.PIPELINES
    nomes_full = [s.name for s in orch.PIPELINES["full_daily"]]
    for canal in ("pma_ml", "pma_shopee", "pma_tiktok"):
        assert canal not in nomes_full, (
            "o PMA nao pode viver dentro do full_daily: desligar a fotografia "
            "exigiria desligar a ingestao do dia")


def test_os_tres_canais_estao_na_ordem_ml_shopee_tiktok():
    """A ordem importa mesmo sem dependencia: o ML e' o unico com teto D-1 e
    janela de recomposicao, e deixa-lo primeiro evita que uma janela longa
    atrase as duas fotografias de D0."""
    assert [s.name for s in orch.PIPELINES["pma_refresh"]] == [
        "pma_ml", "pma_shopee", "pma_tiktok"]


def test_os_tres_canais_sao_nao_criticos():
    for step in orch.PIPELINES["pma_refresh"]:
        assert step.critical is False, (
            f"{step.name} critico faria um canal derrubar o pipeline inteiro")


def test_nenhum_canal_depende_de_outro():
    """`depends_on` no orquestrador vira SKIPPED quando o anterior nao teve
    SUCCESS. Encadear os tres transformaria uma recusa legitima da Shopee em
    'o TikTok nem tentou' — exatamente o que o gate proibe."""
    for step in orch.PIPELINES["pma_refresh"]:
        assert step.depends_on == (), (
            f"{step.name} nao pode depender de outro canal")


def test_cada_canal_tem_preflight_proprio():
    fontes = {s.name: s.preflight_source for s in orch.PIPELINES["pma_refresh"]}
    assert fontes == {"pma_ml": "pma_ml", "pma_shopee": "pma_shopee",
                      "pma_tiktok": "pma_tiktok"}
    from pipelines.ops import preflight as pf
    for fonte in fontes.values():
        assert fonte in pf.SOURCE_CHECKS, f"preflight {fonte} nao registrado"


def test_usa_os_publishers_canonicos_sem_duplicar_regra():
    modulos = {s.name: s.module for s in orch.PIPELINES["pma_refresh"]}
    assert modulos["pma_ml"] == "pipelines.sync_ml_listing_price_serving"
    assert modulos["pma_shopee"] == "pipelines.channel_offer_publisher"
    assert modulos["pma_tiktok"] == "pipelines.channel_offer_publisher"


def test_cada_canal_publica_o_seu_marketplace_com_apply():
    args = {s.name: s.args for s in orch.PIPELINES["pma_refresh"]}
    assert args["pma_shopee"] == ("--marketplace", "shopee", "--apply")
    assert args["pma_tiktok"] == ("--marketplace", "tiktok", "--apply")
    assert args["pma_ml"][0] == "--apply"


# ---------------------------------------------------------------------------
# 2. Politica de data por canal
# ---------------------------------------------------------------------------

def test_lookback_do_ml_e_medido_e_cabe_no_teto():
    """A fonte do ML nao tem maturacao (medido no PMA-2C5A: 100% das linhas
    extraidas no proprio `ref_date`, zero re-extracao). O lookback existe so'
    para tolerar execucao perdida, e precisa ser barato."""
    from pipelines import sync_ml_listing_price_serving as ml
    assert orch.PMA_ML_LOOKBACK_DAYS >= ml.MIN_LOOKBACK_DAYS
    assert orch.PMA_ML_LOOKBACK_DAYS < ml.DEFAULT_LOOKBACK_DAYS, (
        "30 dias seria 10x o custo diario para comprar tolerancia que a fonte "
        "nao pede")
    # ~880 itens/dia medidos; o teto e' 200.000 por janela.
    projetado = orch.PMA_ML_LOOKBACK_DAYS * 900
    assert projetado < ml.MAX_ROWS_PER_WINDOW / 10, (
        f"{projetado} linhas projetadas e' alto demais para uma janela diaria")


def test_o_ml_recebe_o_lookback_configurado():
    args = dict(zip(*[iter(orch.PIPELINES["pma_refresh"][0].args[1:])] * 2))
    assert args["--lookback-days"] == str(orch.PMA_ML_LOOKBACK_DAYS)


def test_o_teto_d1_do_ml_continua_sendo_do_publisher():
    """O orquestrador NAO reimplementa a politica de data: quem recusa o dia
    corrente e' `validate_window`, no publisher."""
    from datetime import date
    from pipelines import sync_ml_listing_price_serving as ml
    hoje = date(2026, 9, 22)
    de, ate = ml.incremental_window(today=hoje,
                                    lookback_days=orch.PMA_ML_LOOKBACK_DAYS)
    assert ate == date(2026, 9, 21), "o teto tem de ser D-1"
    assert (ate - de).days == orch.PMA_ML_LOOKBACK_DAYS - 1
    with pytest.raises(ml.SyncError):
        ml.validate_window(hoje, hoje, today=hoje)


def test_shopee_e_tiktok_nao_recebem_janela_nem_data():
    """As duas fotografias sao de D0 e a data sai da FONTE (watermark da conta
    e `snapshot_date`). Passar `--observed-date` aqui fixaria um dia e faria a
    execucao diaria republicar sempre o mesmo."""
    for nome in ("pma_shopee", "pma_tiktok"):
        step = next(s for s in orch.PIPELINES["pma_refresh"] if s.name == nome)
        assert "--observed-date" not in step.args
        assert "--lookback-days" not in step.args


# ---------------------------------------------------------------------------
# 3. Traducao dos exit codes — a maquina de estados chega inteira ao resumo
# ---------------------------------------------------------------------------

def _roda(codigos: dict[str, int], preflight_ok=True):
    def executor(step):
        return codigos[step.name]

    def preflight(_fonte):
        return (preflight_ok, [])

    return orch.run_pipeline("pma_refresh", executor=executor,
                             preflight_fn=preflight)


@pytest.mark.parametrize("codigo,esperado", [
    (0, "SUCCESS"),
    (1, "FAILED"),
    (2, "REFUSED"),
    (3, "LOCKED"),
    (4, "INDETERMINATE"),
    (5, "FAILED"),
])
def test_cada_exit_code_vira_o_status_certo(codigo, esperado):
    r = _roda({"pma_ml": codigo, "pma_shopee": codigo, "pma_tiktok": codigo})
    assert set(r.values()) == {esperado}


def test_exit_code_desconhecido_vira_failed_e_nao_sucesso():
    """Um codigo que o publisher nao documentou nao pode ser lido como
    desfecho seguro."""
    r = _roda({"pma_ml": 99, "pma_shopee": 0, "pma_tiktok": 0})
    assert r["pma_ml"] == "FAILED"


def test_recusa_NUNCA_vira_sucesso():
    r = _roda({"pma_ml": 2, "pma_shopee": 2, "pma_tiktok": 2})
    assert "SUCCESS" not in r.values()
    assert orch.compute_overall_status("pma_refresh", r) == "DEGRADED"


def test_lock_ocupado_nao_e_falha_mas_tambem_nao_e_sucesso():
    r = _roda({"pma_ml": 3, "pma_shopee": 3, "pma_tiktok": 3})
    assert set(r.values()) == {"LOCKED"}
    assert orch.compute_overall_status("pma_refresh", r) == "DEGRADED"


def test_indeterminado_aparece_com_nome_proprio():
    """`INDETERMINATE` nunca pode ser lido como falha: afirmar rollback quando
    o commit foi tentado seria inventar um fato."""
    r = _roda({"pma_ml": 4, "pma_shopee": 0, "pma_tiktok": 0})
    assert r["pma_ml"] == "INDETERMINATE"
    assert r["pma_ml"] != "FAILED"


def test_o_mapa_de_exit_code_nao_contamina_os_pipelines_antigos():
    """`exit_status_map=None` preserva byte a byte o comportamento historico."""
    for nome in ("full_daily", "shopee_manual_refresh", "serving_refresh"):
        for step in orch.PIPELINES[nome]:
            assert step.exit_status_map is None, (
                f"{nome}/{step.name} ganhou traducao nova sem pedir")

    def executor(step):
        return 2  # codigo que no PMA seria REFUSED

    r = orch.run_pipeline("serving_refresh", executor=executor,
                          preflight_fn=lambda _f: (True, []))
    assert set(r.values()) == {"FAILED"}, (
        "fora do PMA, qualquer exit != 0 continua sendo FAILED")


# ---------------------------------------------------------------------------
# 4. Isolamento — um canal nao impede os outros
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("codigo_ruim", [1, 2, 3, 4])
def test_desfecho_ruim_no_ml_nao_impede_shopee_nem_tiktok(codigo_ruim):
    r = _roda({"pma_ml": codigo_ruim, "pma_shopee": 0, "pma_tiktok": 0})
    assert r["pma_shopee"] == "SUCCESS"
    assert r["pma_tiktok"] == "SUCCESS"


@pytest.mark.parametrize("codigo_ruim", [1, 2, 3, 4])
def test_desfecho_ruim_na_shopee_nao_impede_o_tiktok(codigo_ruim):
    r = _roda({"pma_ml": 0, "pma_shopee": codigo_ruim, "pma_tiktok": 0})
    assert r["pma_tiktok"] == "SUCCESS"


def test_nenhum_canal_e_silenciosamente_ignorado():
    """Todo step precisa aparecer no resultado, com status proprio. Um canal
    ausente do dicionario seria indistinguivel de um canal que passou."""
    r = _roda({"pma_ml": 0, "pma_shopee": 2, "pma_tiktok": 3})
    assert set(r) == {"pma_ml", "pma_shopee", "pma_tiktok"}
    assert "SKIPPED" not in r.values()


def test_um_canal_bloqueado_nao_bloqueia_os_outros():
    bloqueados = {"pma_shopee"}

    def executor(step):
        return 0

    def preflight(fonte):
        return (fonte not in {"pma_shopee"}, [])

    r = orch.run_pipeline("pma_refresh", executor=executor,
                          preflight_fn=preflight)
    assert r["pma_shopee"] == "BLOCKED"
    assert r["pma_ml"] == "SUCCESS"
    assert r["pma_tiktok"] == "SUCCESS"
    assert bloqueados  # o teste descreve o cenario que montou


# ---------------------------------------------------------------------------
# 5. Status agregado
# ---------------------------------------------------------------------------

def test_tudo_publicado_e_ok():
    r = _roda({"pma_ml": 0, "pma_shopee": 0, "pma_tiktok": 0})
    assert orch.compute_overall_status("pma_refresh", r) == "OK"


@pytest.mark.parametrize("codigo", [1, 2, 3, 4])
def test_um_canal_ruim_rebaixa_para_degraded_e_nunca_para_failed(codigo):
    """`critical=False` e' o que garante isolamento: o pipeline nao 'falha'
    porque um canal recusou."""
    r = _roda({"pma_ml": codigo, "pma_shopee": 0, "pma_tiktok": 0})
    assert orch.compute_overall_status("pma_refresh", r) == "DEGRADED"


def test_todos_bloqueados_e_blocked_e_nao_degraded():
    """Sem VPN nada foi lido, nada foi escrito e nada precisa ser
    reconciliado. Esse estado merece nome proprio."""
    r = orch.run_pipeline("pma_refresh", executor=lambda s: 0,
                          preflight_fn=lambda _f: (False, []))
    assert set(r.values()) == {"BLOCKED"}
    assert orch.compute_overall_status("pma_refresh", r) == "BLOCKED"


def test_blocked_parcial_nao_vira_blocked_agregado():
    def preflight(fonte):
        return (fonte != "pma_ml", [])

    r = orch.run_pipeline("pma_refresh", executor=lambda s: 0,
                          preflight_fn=preflight)
    assert orch.compute_overall_status("pma_refresh", r) == "DEGRADED"


def test_o_blocked_agregado_nao_muda_o_exit_code_dos_pipelines_antigos():
    """`main` devolve 1 tanto em FAILED quanto em BLOCKED, entao um
    `full_daily` inteiramente bloqueado continua saindo 1 como antes."""
    r = {s.name: "BLOCKED" for s in orch.PIPELINES["full_daily"]}
    assert orch.compute_overall_status("full_daily", r) == "BLOCKED"


# ---------------------------------------------------------------------------
# 6. Inercia — este gate nao agenda nada
# ---------------------------------------------------------------------------

def test_pma_refresh_nao_entra_na_agenda_proposta():
    """O gate implementa a orquestracao e NAO ativa o agendamento."""
    from pipelines.ops import schedule_plan
    texto = str(schedule_plan.__file__)
    assert texto  # o modulo existe
    fonte = __import__("pathlib").Path(schedule_plan.__file__).read_text(
        encoding="utf-8", errors="replace")
    assert "pma_refresh" not in fonte, (
        "nenhuma tarefa de PMA pode ser proposta ao Task Scheduler neste gate")
