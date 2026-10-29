"""FRESHNESS-SHOPEE-DAILY-1 — contrato puro do `daily_shopee` (sem banco).

Grao e formulas, dono de coluna por loja, kokeshi fora, janela movel,
idempotencia por conteudo, guarda de colapso, flag e codigos de saida. O SQL,
a transacao, o lock e a reconciliacao pre-commit ficam em
`test_sync_shopee_daily_api_integracao.py`, contra Postgres real.
"""
from __future__ import annotations

import contextlib
from datetime import date, datetime, timedelta, timezone

import pytest

from pipelines import sync_shopee_daily_api as D
from pipelines import sync_shopee_daily_api_shadow as SH
from pipelines.ingestion import daily_performance as DP
from pipelines.ingestion.fato_diaria_lock import FatoDiariaLockUnavailable
from pipelines.transforms import shopee_orders_daily as T

AGORA = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)
HOJE = date(2026, 10, 21)
D1 = date(2026, 10, 10)


def bruta(brand="apice", d=D1, **kw) -> SH.LinhaDiaria:
    base = dict(date=d, brand=brand, orders=10, units_sold=12, canceled_orders=2, delivered_orders=7,
                to_return_orders=1, unpaid_orders=0, seller_shipping_cost=100.0, product_subtotal=900.0,
                is_partial=False)
    base.update(kw)
    return SH.LinhaDiaria(**base)


# ---------------------------------------------------------------------------
# Lojas: API x arquivo
# ---------------------------------------------------------------------------

def test_lojas_com_api_sao_as_quatro_e_kokeshi_fica_fora():
    assert T.CONTAS_COM_API == SH.CONTAS_API
    assert T.LOJAS_COM_API == {1, 2, 4, 5}
    assert T.BRAND_TO_LOJA["kokeshi"] == 3 and 3 not in T.LOJAS_COM_API


def test_kokeshi_nunca_vira_linha_da_api():
    with pytest.raises(D.ShopeeDailyApiError):
        D.linha_da_fato(bruta(brand="kokeshi"))


def test_colunas_donas_nao_incluem_funil_nem_ads():
    for c in ("visitors", "conversion_rate", "unique_buyers", "new_buyers", "repeat_buyers",
              "ad_spend", "ad_revenue", "roas"):
        assert c not in D.COLUNAS_DONAS
    assert not set(D.COLUNAS_API) & set(D.COLUNAS_ANULADAS)


# ---------------------------------------------------------------------------
# Grao e formulas
# ---------------------------------------------------------------------------

def test_formulas_iguais_as_do_export():
    r = D.linha_da_fato(bruta())
    assert r["loja_id"] == 1
    assert (r["gmv"], r["orders"], r["units_sold"], r["canceled_orders"], r["delivered_orders"]) == (
        900.0, 10, 12, 2, 7)
    assert r["avg_ticket"] == 90.0                     # gmv / orders
    assert r["cancel_rate_pct"] == round(2 / 12 * 100, 2)   # cancelados / (pedidos + cancelados)
    assert r["shipping_pct_of_gmv"] == round(100 / 900 * 100, 2)
    assert r["seller_shipping_cost"] == 100.0


def test_ausencia_nao_vira_zero_colunas_sem_fonte_ficam_nulas():
    r = D.linha_da_fato(bruta())
    for c in D.COLUNAS_ANULADAS:
        assert r[c] is None, c


def test_dia_todo_cancelado_e_zero_provado_sem_divisao_por_zero():
    r = D.linha_da_fato(bruta(orders=0, units_sold=0, delivered_orders=0, to_return_orders=0,
                              canceled_orders=3, seller_shipping_cost=0.0, product_subtotal=0.0))
    assert (r["orders"], r["gmv"], r["canceled_orders"]) == (0, 0.0, 3)
    assert r["avg_ticket"] is None and r["shipping_pct_of_gmv"] is None
    assert r["cancel_rate_pct"] == 100.0


# ---------------------------------------------------------------------------
# Janela movel
# ---------------------------------------------------------------------------

def test_janela_de_60_dias_terminando_em_d_menos_1():
    inicio, fim = SH.janela(HOJE, None, D.DIAS_JANELA)
    assert fim == HOJE - timedelta(days=1)
    assert (fim - inicio).days == D.DIAS_JANELA - 1


def test_desde_explicito_estende_a_janela_para_tras():
    assert SH.janela(HOJE, date(2026, 1, 1), D.DIAS_JANELA) == (date(2026, 1, 1), HOJE - timedelta(days=1))


def test_incluir_hoje_estende_o_fim_ate_d0_sem_mudar_o_inicio():
    padrao = SH.janela(HOJE, None, D.DIAS_JANELA)
    liberada = SH.janela(HOJE, None, D.DIAS_JANELA, incluir_hoje=True)
    assert liberada == (padrao[0], HOJE)


# ---------------------------------------------------------------------------
# Idempotencia, colapso e reconciliacao (logica pura)
# ---------------------------------------------------------------------------

def _publicada(linha: dict) -> dict:
    return {k: linha[k] for k in D.COLUNAS_DONAS}


def test_idempotencia_conteudo_igual_nao_grava():
    linhas = [D.linha_da_fato(bruta()), D.linha_da_fato(bruta(brand="barbours"))]
    antes = {(r["date"], r["loja_id"]): _publicada(r) for r in linhas}
    assert D.a_gravar(antes, linhas) == []


def test_cancelamento_tardio_regrava_so_o_dia_que_mudou():
    hoje = [D.linha_da_fato(bruta()), D.linha_da_fato(bruta(d=D1 + timedelta(days=1)))]
    antes = {(r["date"], r["loja_id"]): _publicada(r) for r in hoje}
    amanha = [D.linha_da_fato(bruta(orders=9, canceled_orders=3, product_subtotal=810.0)), hoje[1]]
    mudou = D.a_gravar(antes, amanha)
    assert [(r["date"], r["orders"], r["canceled_orders"]) for r in mudou] == [(D1, 9, 3)]


def test_linha_vinda_do_arquivo_com_repasse_e_regravada_com_nulo():
    linha = D.linha_da_fato(bruta())
    antes = {(D1, 1): {**_publicada(linha), "total_fees": 123.45, "total_settlement": 800.0}}
    assert D.a_gravar(antes, [linha]) == [linha]


def test_colapso_da_fonte_aborta():
    antes = {(D1, 1): {**_publicada(D.linha_da_fato(bruta(orders=100))), "orders": 100}}
    with pytest.raises(D.ShopeeDailyApiError, match="colapso"):
        D.verificar_colapso(antes, [D.linha_da_fato(bruta(orders=50))])
    D.verificar_colapso(antes, [D.linha_da_fato(bruta(orders=85))])


def test_loja_sem_dado_na_fonte_com_publicado_aborta_por_colapso():
    antes = {(D1, 2): {**_publicada(D.linha_da_fato(bruta(brand="barbours"))), "orders": 10}}
    with pytest.raises(D.ShopeeDailyApiError, match="loja 2"):
        D.verificar_colapso(antes, [D.linha_da_fato(bruta(brand="apice"))])


def test_dia_publicado_sem_fonte_e_reportado_e_nao_tocado():
    linha = D.linha_da_fato(bruta())
    outro = D1 + timedelta(days=3)
    antes = {(D1, 1): _publicada(linha), (outro, 1): {**_publicada(linha), "orders": 5}}
    assert D.dias_publicados_sem_fonte(antes, [linha]) == [f"1/{outro}"]
    assert all(r["date"] != outro for r in D.a_gravar(antes, [linha]))


def test_comparacao_por_mes_publicado_x_api():
    linha = D.linha_da_fato(bruta())
    antes = {(D1, 1): {**_publicada(linha), "gmv": 870.0}}
    [item] = D.comparar_por_mes(antes, [linha])
    assert (item["loja_id"], item["mes"]) == (1, "2026-10")
    assert item["orders"] == {"publicado": 10.0, "api": 10.0, "dif_pct": 0.0}
    assert item["gmv"]["dif_pct"] == round((900 / 870 - 1) * 100, 2)


# ---------------------------------------------------------------------------
# Precedencia nos carregadores de arquivo
# ---------------------------------------------------------------------------

def _linha_arquivo(loja):
    return {"date": D1, "loja_id": loja, "marketplace_id": 3}


def test_precedencia_pedidos_do_arquivo_nao_escrevem_lojas_com_api():
    escritas = DP.colunas_da_api_por_loja("shopee", [_linha_arquivo(l) for l in (1, 2, 3, 4, 5)],
                                          DP.PATCH_SHOPEE_ORDERS_SQL)
    assert [r["loja_id"] for _sql, r in escritas] == [3]
    assert all(sql is DP.PATCH_SHOPEE_ORDERS_SQL for sql, _ in escritas)


def test_precedencia_shop_stats_escreve_funil_mas_nao_gmv_nas_lojas_com_api():
    escritas = DP.colunas_da_api_por_loja("shopee-stats", [_linha_arquivo(l) for l in (1, 3)],
                                          DP.PATCH_SHOP_STATS_SQL)
    por_loja = {r["loja_id"]: sql for sql, r in escritas}
    assert por_loja[3] is DP.PATCH_SHOP_STATS_SQL           # kokeshi: inalterado, com gmv
    assert por_loja[1] is DP.PATCH_SHOP_STATS_SEM_GMV_SQL
    sem_gmv = str(DP.PATCH_SHOP_STATS_SEM_GMV_SQL)
    assert "gmv" not in sem_gmv and "visitors" in sem_gmv


def test_precedencia_nao_afeta_ads_nem_outros_marketplaces():
    linhas = [_linha_arquivo(l) for l in (1, 3)]
    for source, sql in (("shopee-ads", DP.PATCH_ADS_SQL), ("ml", DP.UPSERT_SQL), ("tiktok", DP.UPSERT_SQL)):
        assert DP.colunas_da_api_por_loja(source, linhas, sql) == [(sql, r) for r in linhas]


# ---------------------------------------------------------------------------
# Flag, dry-run e codigos de saida
# ---------------------------------------------------------------------------

class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _preparar_dry_run(monkeypatch, linhas_brutas):
    snap = SH.Snapshot(captured_at=AGORA, inicio=D1, fim=D1, marcas=SH.CONTAS_API,
                       max_ingested_at=AGORA, rows=list(linhas_brutas))
    monkeypatch.setattr(D, "coletar_fonte", lambda *a: snap)
    monkeypatch.setattr(D, "local_engine", lambda: type("E", (), {"connect": lambda self: _Conn(),
                                                                  "begin": lambda self: pytest.fail("escreveu")})())
    monkeypatch.setattr(D, "publicado", lambda conn, inicio, fim: {})
    monkeypatch.setattr(D, "fato_diaria_lock", lambda *a, **k: pytest.fail("dry-run nao pega lock"))
    monkeypatch.setattr(D, "audit_start", lambda *a: pytest.fail("dry-run nao audita"))


def test_apply_sem_flag_vira_dry_run_declarado_e_nao_escreve(monkeypatch):
    _preparar_dry_run(monkeypatch, [bruta()])
    res = D.run(apply=True, agora=AGORA, ambiente={D.FLAG_HABILITADO: "1"})
    assert res["aplicado"] is False and "dry-run" in res["modo"] and D.FLAG_HABILITADO in res["modo"]
    assert res["linhas_que_mudariam"] == 1


def test_dry_run_puro_nao_escreve(monkeypatch):
    _preparar_dry_run(monkeypatch, [bruta()])
    assert D.run(apply=False, agora=AGORA, ambiente={D.FLAG_HABILITADO: "true"})["modo"] == "dry-run"


def test_run_incluir_hoje_publica_janela_ate_d0(monkeypatch):
    _preparar_dry_run(monkeypatch, [bruta()])
    vistas = []
    original = SH.janela
    monkeypatch.setattr(SH, "janela", lambda *a, **k: vistas.append((a, k)) or original(*a, **k))
    res = D.run(apply=False, agora=AGORA, ambiente={}, incluir_hoje=True)
    assert res["janela"][1] == str(HOJE)
    assert res["incluir_hoje"] is True


def test_main_repassa_incluir_hoje(monkeypatch):
    recebidos = {}
    monkeypatch.setattr(D, "run", lambda **k: recebidos.update(k) or {})
    assert D.main(["--incluir-hoje"]) == 0
    assert recebidos["incluir_hoje"] is True
    assert D.main([]) == 0
    assert recebidos["incluir_hoje"] is False


def test_main_lock_ocupado_sai_75(monkeypatch):
    def ocupado(**k):
        raise FatoDiariaLockUnavailable(3, 918130003)
    monkeypatch.setattr(D, "run", ocupado)
    assert D.main(["--apply"]) == 75


def test_main_contrato_sai_1(monkeypatch):
    def quebra(**k):
        raise SH.ShopeeDailyShadowError("fonte VELHA ou ausente — nada foi publicado: apice: sem carga")
    monkeypatch.setattr(D, "run", quebra)
    assert D.main([]) == 1


def test_lock_e_o_mesmo_dos_carregadores_de_arquivo(monkeypatch):
    vistos = []

    @contextlib.contextmanager
    def lock(mkt, **k):
        vistos.append(mkt)
        raise FatoDiariaLockUnavailable(mkt, 918130003)
        yield  # pragma: no cover

    monkeypatch.setattr(D, "fato_diaria_lock", lock)
    monkeypatch.setattr(D, "coletar_fonte", lambda *a: pytest.fail("leu a fonte sem o lock"))
    with pytest.raises(FatoDiariaLockUnavailable):
        D.run(apply=True, agora=AGORA, ambiente={D.FLAG_HABILITADO: "true"})
    assert vistos == [3]
