"""Gate PMA-2C5D0 — publicacao e saude global sao EIXOS distintos.

O gate PMA-2C5C-O rodou o ensaio contra as fontes reais: os tres canais
montaram candidata com sucesso, e o agregado exibido foi `DEGRADED`. A causa
era o `health_check` saindo 1 por DEZ itens atrasados, nenhum deles do PMA.

Isso e' pior do que um rotulo feio. Um operador que le `DEGRADED` conclui que a
publicacao teve problema, e o passo natural e' reexecutar — e reexecutar canal
ja commitado e' a unica coisa que este pipeline nao pode induzir.

Aqui ficam travados os dois eixos, a matriz de exit inteira e a promessa de que
o diagnostico nunca transforma publicacao boa em falha.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipelines.ops import health_check as hc
from pipelines.ops import orchestrate as orch

HOJE = date(2026, 9, 22)


def _res(ml="SUCCESS", shopee="SUCCESS", tiktok="SUCCESS", health="SUCCESS"):
    return {"pma_ml": ml, "pma_shopee": shopee, "pma_tiktok": tiktok,
            "health_check": health}


def _exit(r):
    return orch.exit_code_do_pipeline(
        "pma_refresh", r, orch.compute_overall_status("pma_refresh", r))


# ---------------------------------------------------------------------------
# 1. A matriz de exit, exaustiva
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("health", ["SUCCESS", "FAILED", "BLOCKED", "SKIPPED"])
def test_tres_sucessos_sao_OK_e_exit_0_seja_qual_for_a_saude(health):
    """A linha central da tabela: a saude global NAO decide a publicacao."""
    r = _res(health=health)
    assert orch.pma_publication_status(r) == orch.PMA_PUB_OK
    assert _exit(r) == 0


@pytest.mark.parametrize("ruim", ["REFUSED", "LOCKED", "FAILED", "BLOCKED"])
@pytest.mark.parametrize("posicao", ["pma_ml", "pma_shopee", "pma_tiktok"])
@pytest.mark.parametrize("health", ["SUCCESS", "FAILED"])
def test_um_canal_ruim_derruba_para_DEGRADED_e_exit_1(ruim, posicao, health):
    r = _res(health=health)
    r[posicao] = ruim
    assert orch.pma_publication_status(r) == orch.PMA_PUB_DEGRADED
    assert _exit(r) == 1


@pytest.mark.parametrize("posicao", ["pma_ml", "pma_shopee", "pma_tiktok"])
@pytest.mark.parametrize("health", ["SUCCESS", "FAILED"])
def test_INDETERMINATE_tem_precedencia_sobre_tudo(posicao, health):
    """E' o unico desfecho em que nao se sabe o que foi gravado."""
    r = _res(health=health)
    r[posicao] = "INDETERMINATE"
    assert orch.pma_publication_status(r) == orch.PMA_PUB_INDETERMINATE
    assert _exit(r) == 1


def test_INDETERMINATE_vence_ate_quando_os_outros_dois_falharam():
    r = _res(ml="INDETERMINATE", shopee="FAILED", tiktok="BLOCKED")
    assert orch.pma_publication_status(r) == orch.PMA_PUB_INDETERMINATE


def test_todos_bloqueados_e_BLOCKED_e_nao_DEGRADED():
    """Queda de VPN: nada foi lido, nada escrito, nada a reconciliar.
    `DEGRADED` sugeriria sucesso parcial, e nao houve nenhum."""
    r = _res(ml="BLOCKED", shopee="BLOCKED", tiktok="BLOCKED")
    assert orch.pma_publication_status(r) == orch.PMA_PUB_BLOCKED
    assert _exit(r) == 1


def test_zero_canal_valido_com_desfechos_MISTOS_nao_vira_DEGRADED():
    r = _res(ml="FAILED", shopee="REFUSED", tiktok="LOCKED")
    assert orch.pma_publication_status(r) != orch.PMA_PUB_DEGRADED
    assert _exit(r) == 1


def test_zero_canal_valido_com_desfecho_UNICO_carrega_o_nome_do_desfecho():
    for desfecho in ("FAILED", "REFUSED", "LOCKED"):
        r = _res(ml=desfecho, shopee=desfecho, tiktok=desfecho)
        assert orch.pma_publication_status(r) == desfecho, desfecho
        assert _exit(r) == 1


def test_canal_ausente_do_resultado_nao_conta_como_sucesso():
    r = _res()
    del r["pma_tiktok"]
    assert orch.pma_publication_status(r) != orch.PMA_PUB_OK
    assert _exit(r) == 1


# ---------------------------------------------------------------------------
# 2. O eixo de saude
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bruto,esperado", [
    ("SUCCESS", "OK"), ("FAILED", "FAILED"), ("BLOCKED", "BLOCKED"),
    ("DEGRADED", "DEGRADED"), ("SKIPPED", "SKIPPED"),
])
def test_o_eixo_de_saude_repassa_o_step(bruto, esperado):
    assert orch.pma_health_status(_res(health=bruto)) == esperado


def test_saude_desconhecida_quando_o_step_nao_rodou():
    r = _res()
    del r["health_check"]
    assert orch.pma_health_status(r) == "UNKNOWN"


def test_o_health_check_continua_ultimo_e_always_run():
    steps = orch.PIPELINES["pma_refresh"]
    assert steps[-1].name == "health_check"
    assert steps[-1].always_run is True
    assert steps[-1].critical is False


def test_o_health_check_nao_entra_no_eixo_de_publicacao():
    assert "health_check" not in orch.PMA_CANAIS


# ---------------------------------------------------------------------------
# 3. A saida operacional
# ---------------------------------------------------------------------------

def _roda_main(monkeypatch, capsys, resultados, dry_run=True):
    import sys

    monkeypatch.setattr(orch, "run_pipeline",
                        lambda nome, **k: dict(resultados))
    argv = ["orchestrate", "--pipeline", "pma_refresh"]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setitem(sys.modules, "dotenv", type(sys)("dotenv"))
    sys.modules["dotenv"].load_dotenv = lambda **_k: None
    codigo = orch.main()
    return codigo, capsys.readouterr().out


def test_a_saida_nomeia_os_DOIS_eixos_e_o_exit(monkeypatch, capsys):
    codigo, saida = _roda_main(monkeypatch, capsys, _res(health="FAILED"))
    assert "STATUS PMA: OK (mode=dry_run)" in saida
    assert "STATUS SAUDE GLOBAL: FAILED" in saida
    assert "EXIT: 0" in saida
    assert codigo == 0


def test_a_saida_diz_que_a_saude_NAO_pertence_a_publicacao(monkeypatch, capsys):
    _, saida = _roda_main(monkeypatch, capsys, _res(health="FAILED"))
    assert "NAO pertencem a publicacao do PMA" in saida


def test_a_saida_desencoraja_retry_quando_a_publicacao_concluiu(
        monkeypatch, capsys):
    """A regra que o gate existe para garantir: saude ruim nao pede
    reexecucao de canal ja commitado."""
    _, saida = _roda_main(monkeypatch, capsys, _res(health="FAILED"),
                          dry_run=False)
    assert "NAO reexecute os publishers" in saida


def test_nao_ha_sugestao_de_retry_quando_tudo_esta_saudavel(monkeypatch, capsys):
    _, saida = _roda_main(monkeypatch, capsys, _res())
    assert "reexecute" not in saida.lower()


def test_STATUS_GERAL_nao_aparece_mais_para_o_pma_refresh(monkeypatch, capsys):
    """Era essa linha unica que exibia `DEGRADED` com os tres canais bons."""
    _, saida = _roda_main(monkeypatch, capsys, _res(health="FAILED"))
    assert "STATUS GERAL" not in saida


def test_o_ensaio_nunca_afirma_publicacao(monkeypatch, capsys):
    _, saida = _roda_main(monkeypatch, capsys, _res(health="FAILED"))
    minusculo = saida.lower().replace(
        "nenhuma fotografia foi publicada", "")
    for proibido in ("publicado", "publicada", "published"):
        assert proibido not in minusculo, proibido


def test_o_modo_apply_nao_e_rotulado_como_ensaio(monkeypatch, capsys):
    _, saida = _roda_main(monkeypatch, capsys, _res(), dry_run=False)
    assert "STATUS PMA: OK (mode=apply)" in saida
    assert "mode=dry_run" not in saida


@pytest.mark.parametrize("ruim,esperado", [
    ("REFUSED", "DEGRADED"), ("INDETERMINATE", "INDETERMINATE"),
])
def test_a_saida_carrega_o_desfecho_ruim_e_exit_1(monkeypatch, capsys,
                                                  ruim, esperado):
    r = _res(shopee=ruim)
    codigo, saida = _roda_main(monkeypatch, capsys, r)
    assert f"STATUS PMA: {esperado}" in saida
    assert "EXIT: 1" in saida
    assert codigo == 1


# ---------------------------------------------------------------------------
# 4. Pipelines legados — semantica preservada
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome", ["full_daily", "serving_refresh",
                                  "shopee_manual_refresh"])
def test_pipeline_legado_continua_imprimindo_STATUS_GERAL(nome, monkeypatch,
                                                          capsys):
    import sys

    steps = orch.PIPELINES[nome]
    resultados = {s.name: "SUCCESS" for s in steps}
    monkeypatch.setattr(orch, "run_pipeline", lambda n, **k: dict(resultados))
    monkeypatch.setattr(sys, "argv", ["orchestrate", "--pipeline", nome])
    monkeypatch.setitem(sys.modules, "dotenv", type(sys)("dotenv"))
    sys.modules["dotenv"].load_dotenv = lambda **_k: None
    codigo = orch.main()
    saida = capsys.readouterr().out
    assert "STATUS GERAL: OK (mode=apply)" in saida
    assert "STATUS PMA" not in saida
    assert "STATUS SAUDE GLOBAL" not in saida
    assert codigo == 0


@pytest.mark.parametrize("nome", ["full_daily", "serving_refresh",
                                  "shopee_manual_refresh"])
def test_legado_DEGRADED_continua_saindo_0(nome):
    """Regra historica: gap nao-critico conhecido nao faz a carga do dia
    falhar todo dia. Nao pode mudar por causa do pipeline novo."""
    steps = orch.PIPELINES[nome]
    nao_criticos = [s.name for s in steps if not s.critical]
    if not nao_criticos:
        pytest.skip(f"{nome} nao tem step nao-critico")
    resultados = {s.name: "SUCCESS" for s in steps}
    resultados[nao_criticos[0]] = "FAILED"
    overall = orch.compute_overall_status(nome, resultados)
    assert overall == "DEGRADED"
    assert orch.exit_code_do_pipeline(nome, resultados, overall) == 0


@pytest.mark.parametrize("nome", ["full_daily", "serving_refresh",
                                  "shopee_manual_refresh"])
def test_legado_FAILED_continua_saindo_1(nome):
    steps = orch.PIPELINES[nome]
    criticos = [s.name for s in steps if s.critical]
    resultados = {s.name: "SUCCESS" for s in steps}
    resultados[criticos[0]] = "FAILED"
    overall = orch.compute_overall_status(nome, resultados)
    assert orch.exit_code_do_pipeline(nome, resultados, overall) == 1


def test_so_o_pma_refresh_tem_politica_estrita():
    assert orch.PIPELINES_COM_EXIT_ESTRITO == frozenset({"pma_refresh"})


# ---------------------------------------------------------------------------
# 5. Atribuicao causal — so' com evidencia
# ---------------------------------------------------------------------------

def _classifica(observada, watermark=None, limite=1, **kw):
    return hc.classifica_canal_pma(
        observada=observada, today=HOJE, limite=limite,
        source_watermark=watermark, **kw)


def test_fotografia_velha_sem_watermark_e_snapshot_stale_sem_causa():
    status, causa, stale, motivo = _classifica(HOJE - timedelta(days=5))
    assert status == hc.PMA_STATUS_SNAPSHOT_STALE
    assert causa == hc.PMA_CAUSE_NOT_DETERMINED
    assert stale is True
    assert "NAO determinada" in motivo


def test_publisher_not_executed_exige_watermark_MAIS_NOVO():
    """Exatamente o caso de producao em 22/09: fonte em 22, fotografia em 17."""
    status, causa, _, motivo = _classifica(
        date(2026, 9, 17), watermark=date(2026, 9, 22))
    assert status == hc.PMA_STATUS_SNAPSHOT_STALE
    assert causa == hc.PMA_CAUSE_PUBLISHER_NOT_EXECUTED
    assert "publisher nao executou" in motivo


def test_source_stale_quando_a_fonte_tambem_esta_atrasada():
    status, causa, _, motivo = _classifica(
        date(2026, 9, 17), watermark=date(2026, 9, 17))
    assert causa == hc.PMA_CAUSE_SOURCE_STALE
    assert "fonte tambem esta atrasada" in motivo


@pytest.mark.parametrize("audit", ["success", "failed", "running", None])
def test_o_status_de_auditoria_NUNCA_decide_a_causa(audit):
    """A contraprova central deste gate."""
    _, sem_wm, _, _ = _classifica(date(2026, 9, 17), audit_status=audit)
    assert sem_wm == hc.PMA_CAUSE_NOT_DETERMINED, (
        f"auditoria={audit!r} nao pode virar causa")

    _, com_wm, _, _ = _classifica(date(2026, 9, 17),
                                  watermark=date(2026, 9, 22),
                                  audit_status=audit)
    assert com_wm == hc.PMA_CAUSE_PUBLISHER_NOT_EXECUTED, (
        "com evidencia, a causa vem da EVIDENCIA, nao da auditoria")


def test_a_causa_nao_e_afirmada_num_canal_saudavel():
    status, causa, stale, _ = _classifica(HOJE)
    assert status == hc.PMA_STATUS_OK
    assert causa is None
    assert stale is False


def test_auditoria_indeterminada_mantem_precedencia_sobre_o_atraso():
    status, _, stale, _ = _classifica(
        HOJE, audit_status="running",
        audit_error="INDETERMINADO: o commit levantou")
    assert status == hc.PMA_STATUS_AUDITORIA_INCOMPLETA
    assert stale is True


def test_sem_fotografia_alguma_a_causa_fica_indeterminada():
    status, causa, stale, _ = _classifica(None)
    assert status == hc.PMA_STATUS_SEM_FOTOGRAFIA
    assert causa == hc.PMA_CAUSE_NOT_DETERMINED
    assert stale is True


def test_a_taxonomia_antiga_de_causa_inferida_nao_existe_mais():
    for morto in ("PMA_STATUS_FONTE_ATRASADA",
                  "PMA_STATUS_PUBLISHER_NAO_EXECUTADO",
                  "PMA_STATUS_RECUSADO", "PMA_STATUS_LOCK"):
        assert not hasattr(hc, morto), (
            f"{morto} voltou: era causa por inferencia")


def test_o_health_check_nao_abre_conexao_nova_para_a_fonte():
    """O gate pediu para REMOVER a atribuicao falsa, nao para ampliar o
    processo. O watermark e' opcional e injetado por quem ja o tem."""
    import inspect

    assinatura = inspect.signature(hc.fetch_pma_channel_status)
    assert "source_watermarks" in assinatura.parameters
    assert assinatura.parameters["source_watermarks"].default is None
    corpo = inspect.getsource(hc.fetch_pma_channel_status)
    for proibido in ("connect(", "_read_only(", "DATAMART_DATABASE_URL"):
        assert proibido not in corpo, f"o health check abriu {proibido}"


def test_o_watermark_recebido_APARECE_no_relatorio():
    """`not_determined` sem o watermark ao lado seria lido como "sem
    problema". O campo existe para impedir essa leitura, e so' serve se
    estiver preenchido quando a medicao existe.
    """
    from pipelines.tests.test_pma_health_check_canais import ConnFake

    velha = HOJE - timedelta(days=5)
    conn = ConnFake(observadas={"ml": velha, "shopee": velha, "tiktok": velha})
    medidos = {"shopee": date(2026, 9, 22)}
    por_canal = {x.channel: x for x in hc.fetch_pma_channel_status(
        conn, today=HOJE, source_watermarks=medidos)}

    # Medido: o relatorio carrega o watermark E a causa que ele sustenta.
    assert por_canal["shopee"].source_watermark == "2026-09-22"
    assert por_canal["shopee"].cause == hc.PMA_CAUSE_PUBLISHER_NOT_EXECUTED

    # Nao medido: o campo fica nulo, e e' isso que justifica `not_determined`.
    assert por_canal["ml"].source_watermark is None
    assert por_canal["ml"].cause == hc.PMA_CAUSE_NOT_DETERMINED


def test_o_relatorio_json_expoe_causa_e_watermark():
    """Quem consome o `--json` precisa dos dois campos para agir."""
    from dataclasses import asdict

    from pipelines.tests.test_pma_health_check_canais import ConnFake

    velha = HOJE - timedelta(days=5)
    linhas = hc.fetch_pma_channel_status(
        ConnFake(observadas={"ml": velha, "shopee": velha, "tiktok": velha}),
        today=HOJE, source_watermarks={"tiktok": date(2026, 9, 22)})
    for x in linhas:
        d = asdict(x)
        assert "cause" in d and "source_watermark" in d, d.keys()
    por_canal = {x.channel: asdict(x) for x in linhas}
    assert por_canal["tiktok"]["source_watermark"] == "2026-09-22"
