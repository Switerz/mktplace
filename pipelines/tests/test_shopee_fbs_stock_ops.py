"""STOCK-DAILY-SHOPEE-TIKTOK-1 — preflight, primeira execucao, reconciliacao e rollback.

Contra PostgreSQL REAL, com a mesma guarda do teste do publicador: so' roda se
`TEST_PG_DSN` existe e e' exatamente a DSN que o pipeline usa. Fonte e destino
apontam para o mesmo banco descartavel, semeado pela fixture `banco`.

A reconciliacao so' vale se DETECTA: cada adulteracao abaixo tem de virar
`DIVERGENTE`, e a fonte recapturada tem de virar `NAO_APLICAVEL` -- nunca OK.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from pipelines.tests.test_sync_shopee_fbs_stock_publish import (  # noqa: F401
    DSN, TOTAL_KITS, TOTAL_LOCALIZACOES, TOTAL_PRODUTOS, TOTAL_UNIDADES,
    _auditoria, _contagens, _locks_advisory, banco, pytestmark,
)

if DSN:  # pragma: no branch
    from sqlalchemy import text

    import pipelines.sync_shopee_fbs_stock_daily as P
    from pipelines.ops import shopee_fbs_stock_ops as OPS


def _sql(banco, sql: str, **p) -> None:
    with banco.begin() as c:
        c.execute(text(sql), p)


def _publicar() -> dict:
    return P.run(apply=True)


def _status(rec: dict) -> dict:
    return {nome: c["status"] for nome, c in rec["checagens"].items()}


# --------------------------------------------------------------------------- #
# Preflight                                                                    #
# --------------------------------------------------------------------------- #
def test_preflight_pronto_na_fonte_do_dia(banco):
    res = OPS.preflight(P._hoje_brt())
    assert res["pronto"] is True, res["problemas"]
    assert res["neon"]["locks"] == 0 and res["neon"]["auditorias_running"] == 0
    assert [c["shop_account"] for c in res["portao_fonte"]] == list(P.CONTAS_ESPERADAS)
    # Preflight nao escreve nada.
    assert _contagens(banco)["prod_linhas"] == 0 and _auditoria(banco) == []


def test_preflight_usa_o_mesmo_portao_do_publicador(banco):
    _sql(banco, "UPDATE silver.stg_shopee_orders SET ingested_at = now() - interval '3 days' "
                "WHERE shop_account = 'apice'")
    res = OPS.preflight(P._hoje_brt())
    assert res["pronto"] is False
    assert any(p.startswith("apice: ultima ingestao de pedidos") for p in res["problemas"])
    assert OPS.main(["preflight"]) == P.EXIT_FONTE_NAO_PRONTA


def test_preflight_recusa_auditoria_presa(banco):
    _sql(banco, "INSERT INTO audit.source_sync_run (source_name, marketplace_id, status, "
                "started_at) VALUES (:s, 3, 'running', now())", s=P.AUDIT_SOURCE)
    res = OPS.preflight(P._hoje_brt())
    assert res["pronto"] is False
    assert any("running" in p for p in res["problemas"])


# --------------------------------------------------------------------------- #
# Primeira execucao                                                            #
# --------------------------------------------------------------------------- #
def test_primeira_execucao_exige_confirmacao_explicita(banco):
    assert OPS.main(["primeira-execucao"]) == P.EXIT_FALHA
    assert _contagens(banco)["prod_linhas"] == 0 and _auditoria(banco) == []


def test_primeira_execucao_publica_e_reconcilia(banco, capsys):
    assert OPS.main(["primeira-execucao", OPS.CONFIRMACAO]) == 0
    saida = capsys.readouterr().out
    assert '"etapa": "concluida"' in saida and "reverter --ref-date" in saida
    c = _contagens(banco)
    assert (c["prod_linhas"], c["loc_linhas"], c["prod_soma"]) == (
        TOTAL_PRODUTOS, TOTAL_LOCALIZACOES, TOTAL_UNIDADES)
    assert [a["status"] for a in _auditoria(banco)] == ["success"]
    assert _locks_advisory(banco) == 0


def test_primeira_execucao_nao_escreve_se_o_preflight_recusa(banco):
    _sql(banco, "UPDATE raw.shopee_products SET ingested_at = now() - interval '2 days'; "
                "UPDATE silver.stg_shopee_products SET ingested_at = now() - interval '2 days'")
    assert OPS.main(["primeira-execucao", OPS.CONFIRMACAO]) == P.EXIT_FONTE_NAO_PRONTA
    assert _contagens(banco)["prod_linhas"] == 0 and _auditoria(banco) == []


# --------------------------------------------------------------------------- #
# Reconciliacao                                                                #
# --------------------------------------------------------------------------- #
def test_reconciliacao_limpa_fecha_tudo(banco):
    _publicar()
    rec = OPS.reconciliar(P._hoje_brt())
    assert rec["ok"] is True
    assert set(_status(rec).values()) == {"OK"}, _status(rec)
    r = rec["resumo"]
    assert (r["produtos"], r["localizacoes"], r["vendavel_total"]) == (
        TOTAL_PRODUTOS, TOTAL_LOCALIZACOES, TOTAL_UNIDADES)
    assert r["vendavel_operacional"] + r["vendavel_kits"] == r["vendavel_total"]
    assert rec["checagens"]["kits_isolados"]["kits"] == TOTAL_KITS


@pytest.mark.parametrize("adulteracao, checagens_que_acusam", [
    # Quantidade do produto mexida: fonte, grao fino e total acusam.
    ("UPDATE marts.fact_shopee_fbs_stock_daily SET full_stock_saleable = full_stock_saleable + 1, "
     "full_stock_total = full_stock_total + 1 "
     "WHERE item_id = (SELECT MIN(item_id) FROM marts.fact_shopee_fbs_stock_daily)",
     {"produtos_batem_com_a_fonte", "localizacoes_somam_o_produto", "vendavel_total_bate"}),
    # Uma localizacao a mais no grao fino.
    ("UPDATE marts.fact_shopee_fbs_stock_location_daily SET full_stock = full_stock + 5 "
     "WHERE (item_id, location_id) = (SELECT item_id, location_id "
     "FROM marts.fact_shopee_fbs_stock_location_daily ORDER BY 1, 2 LIMIT 1)",
     {"localizacoes_somam_o_produto", "localizacoes_batem_com_a_fonte"}),
    # Classificacao trocada (ruptura escondida, por exemplo).
    ("UPDATE marts.fact_shopee_fbs_stock_daily SET classificacao_torre = 'SUFICIENTE' "
     "WHERE classificacao_torre = 'SEM_GIRO_CANDIDATO'",
     {"classificacao_recalculada_bate"}),
    # Kit sem a marca de kit: sairia contado como estoque operacional.
    ("UPDATE marts.fact_shopee_fbs_stock_daily SET is_kit = FALSE, "
     "classificacao_torre = 'SEM_GIRO_CANDIDATO' WHERE is_kit AND units_sold_28d = 0 "
     "AND full_stock_saleable > 0",
     {"produtos_batem_com_a_fonte", "classificacao_recalculada_bate"}),
    # Produto que sumiu da fotografia.
    ("DELETE FROM marts.fact_shopee_fbs_stock_daily "
     "WHERE item_id = (SELECT MAX(item_id) FROM marts.fact_shopee_fbs_stock_daily)",
     {"produtos_batem_com_a_fonte", "vendavel_total_bate"}),
])
def test_reconciliacao_acusa_cada_adulteracao(banco, adulteracao, checagens_que_acusam):
    _publicar()
    _sql(banco, adulteracao)
    rec = OPS.reconciliar(P._hoje_brt())
    acusaram = {n for n, s in _status(rec).items() if s == "DIVERGENTE"}
    assert rec["ok"] is False
    assert checagens_que_acusam <= acusaram, (checagens_que_acusam, acusaram)


def test_reconciliacao_acusa_auditoria_presa(banco):
    _publicar()
    _sql(banco, "INSERT INTO audit.source_sync_run (source_name, marketplace_id, status, "
                "started_at) VALUES (:s, 3, 'running', now())", s=P.AUDIT_SOURCE)
    rec = OPS.reconciliar(P._hoje_brt())
    assert rec["checagens"]["zero_auditorias_presas"]["status"] == "DIVERGENTE"
    assert rec["ok"] is False


def test_fonte_recapturada_vira_nao_aplicavel_e_nunca_ok(banco):
    _publicar()
    # O catalogo do dia seguinte sobrescreveu a raw (e mudou um estoque).
    _sql(banco, "UPDATE raw.shopee_products SET ingested_at = ingested_at + interval '1 day'; "
                "UPDATE silver.stg_shopee_products SET ingested_at = ingested_at + interval '1 day'")
    rec = OPS.reconciliar(P._hoje_brt())
    st = _status(rec)
    for nome in ("produtos_batem_com_a_fonte", "localizacoes_batem_com_a_fonte",
                 "vendavel_total_bate", "classificacao_recalculada_bate"):
        assert st[nome] == "NAO_APLICAVEL", nome
    # O que nao depende da fonte continua sendo checado.
    assert st["localizacoes_somam_o_produto"] == "OK"


def test_reconciliar_data_sem_fotografia_nao_e_ok(banco):
    rec = OPS.reconciliar(P._hoje_brt() - timedelta(days=400))
    assert rec["ok"] is False and "nenhuma fotografia" in rec["erro"]


def test_classificacao_reescrita_concorda_com_a_do_publicador():
    """Duas implementacoes da mesma regra: discordancia aqui e' defeito em uma."""
    for kit in (False, True):
        for vendavel in (0, 1, 5, 50, 500):
            for unidades in (0, 1, 7, 28, 280):
                cob = (Decimal(vendavel) / (Decimal(unidades) / 28)) if unidades else None
                assert OPS.classificacao_esperada(kit, vendavel, unidades) == \
                    P.classificar(kit, vendavel, unidades, cob), (kit, vendavel, unidades)


# --------------------------------------------------------------------------- #
# Rollback                                                                     #
# --------------------------------------------------------------------------- #
def test_reverter_sem_apply_so_conta(banco):
    pub = _publicar()
    res = OPS.reverter(P._hoje_brt(), pub["run_id"], apply=False)
    assert (res["linhas_produto"], res["linhas_location"]) == (TOTAL_PRODUTOS, TOTAL_LOCALIZACOES)
    assert _contagens(banco)["prod_linhas"] == TOTAL_PRODUTOS
    assert _locks_advisory(banco) == 0


def test_reverter_apaga_so_o_run_e_preserva_o_dia_anterior(banco):
    ontem = P._hoje_brt() - timedelta(days=1)
    _sql(banco, f"""
        INSERT INTO {P.FACT_PRODUTO}
            (ref_date, brand, shop_account, item_id, model_id, is_kit,
             full_stock_saleable, full_stock_total, location_count,
             units_sold_28d, days_with_sales_28d, avg_daily_units_28d,
             cobertura_torre_dias, classificacao_torre, vinculo_vendas,
             source_run_id, source_captured_at)
        VALUES (:d,'apice','apice','999','0',FALSE, 7, 7, 1, 0, 0, 0, NULL,
                'SEM_GIRO_CANDIDATO','SEM_VENDA_NA_JANELA','anterior', NOW())""", d=ontem)
    pub = _publicar()
    assert OPS.main(["reverter", "--ref-date", str(P._hoje_brt()),
                     "--run-id", pub["run_id"], "--apply"]) == 0
    with banco.connect() as c:
        hoje_n = c.execute(text(f"SELECT COUNT(*) FROM {P.FACT_PRODUTO} WHERE ref_date = :d"),
                           {"d": P._hoje_brt()}).scalar()
        loc_n = c.execute(text(f"SELECT COUNT(*) FROM {P.FACT_LOCATION} WHERE ref_date = :d"),
                          {"d": P._hoje_brt()}).scalar()
        ontem_n = c.execute(text(f"SELECT COUNT(*) FROM {P.FACT_PRODUTO} WHERE ref_date = :d"),
                            {"d": ontem}).scalar()
    assert (hoje_n, loc_n, ontem_n) == (0, 0, 1)
    assert _locks_advisory(banco) == 0


def test_reverter_recusa_run_errado_e_dia_com_outro_run(banco):
    pub = _publicar()
    with pytest.raises(P.ShopeeStockSyncError, match="OUTRO run"):
        OPS.reverter(P._hoje_brt(), "run-que-nao-existe", apply=True)
    assert _contagens(banco)["prod_linhas"] == TOTAL_PRODUTOS
    with pytest.raises(P.ShopeeStockSyncError, match="nenhuma linha"):
        OPS.reverter(P._hoje_brt() - timedelta(days=9), pub["run_id"], apply=True)


def test_reverter_respeita_o_lock_da_publicacao(banco):
    pub = _publicar()
    outro = banco.connect()
    try:
        outro.execute(text("SELECT pg_advisory_lock(:k)"), {"k": P.ADVISORY_LOCK_KEY})
        assert OPS.main(["reverter", "--ref-date", str(P._hoje_brt()),
                         "--run-id", pub["run_id"], "--apply"]) == P.EXIT_LOCK
        assert _contagens(banco)["prod_linhas"] == TOTAL_PRODUTOS
    finally:
        outro.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": P.ADVISORY_LOCK_KEY})
        outro.close()
