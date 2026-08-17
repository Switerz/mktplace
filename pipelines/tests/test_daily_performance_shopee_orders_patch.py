"""Gate SD2-C — testes focais do patch parcial de Shopee Orders no Daily.

Nenhum teste abre conexao real de banco: `local_session` e' monkeypatchado e os
conectores/transforms sao substituidos por fakes.

Os testes de preservacao NAO conferem apenas o texto do SQL: `_aplicar_set()`
interpreta as atribuicoes reais do `DO UPDATE SET` e as aplica sobre uma linha
preexistente, provando o efeito observavel do upsert.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import date

import pytest

from pipelines.ingestion import daily_performance as dp

TABELA = "marts.fact_marketplace_daily_performance"


# ---------------------------------------------------------------------------
# Utilitarios: extrair e APLICAR o DO UPDATE SET de um SQL de upsert
# ---------------------------------------------------------------------------
def _texto(sql) -> str:
    return str(sql)


def _colunas_do_insert(sql) -> list[str]:
    corpo = _texto(sql).split("VALUES", 1)[0]
    dentro = corpo[corpo.index("(") + 1: corpo.rindex(")")]
    return [c.strip() for c in dentro.split(",") if c.strip()]


def _atribuicoes_do_set(sql) -> dict[str, str]:
    txt = _texto(sql)
    trecho = txt.split("DO UPDATE SET", 1)[1]
    # divide por virgulas que nao estao dentro de parenteses
    partes, nivel, atual = [], 0, ""
    for ch in trecho:
        if ch == "(":
            nivel += 1
        elif ch == ")":
            nivel -= 1
        if ch == "," and nivel == 0:
            partes.append(atual); atual = ""
        else:
            atual += ch
    partes.append(atual)
    out = {}
    for p in partes:
        if "=" not in p:
            continue
        col, expr = p.split("=", 1)
        out[col.strip()] = " ".join(expr.split())
    return out


def _aplicar_set(sql, existente: dict, excluded: dict, agora="AGORA") -> dict:
    """Aplica as atribuicoes do DO UPDATE SET sobre `existente`.

    Suporta as tres formas usadas neste modulo: `EXCLUDED.col`, `NOW()` e
    `COALESCE(<tabela>.col, EXCLUDED.col)`.
    """
    resultado = dict(existente)
    for col, expr in _atribuicoes_do_set(sql).items():
        if expr.upper() == "NOW()":
            resultado[col] = agora
            continue
        m = re.fullmatch(r"COALESCE\(\s*([\w.]+)\s*,\s*EXCLUDED\.(\w+)\s*\)", expr, re.I)
        if m:
            alvo = m.group(1).rsplit(".", 1)[-1]
            atual = existente.get(alvo)
            resultado[col] = atual if atual is not None else excluded.get(m.group(2))
            continue
        m = re.fullmatch(r"EXCLUDED\.(\w+)", expr, re.I)
        if m:
            resultado[col] = excluded.get(m.group(1))
            continue
        raise AssertionError(f"expressao nao suportada no SET: {col} = {expr!r}")
    return resultado


# Colunas de que Orders NAO e' fonte e que devem sobreviver intactas.
NAO_PERTENCEM_A_ORDERS = [
    "gmv",
    "visitors", "conversion_rate", "new_buyers", "repeat_buyers",
    "repeat_buyer_rate_pct",
    "ad_spend", "ad_revenue", "ad_impressions", "ad_clicks",
    "roas", "acos_pct", "ctr_pct", "cpc",
    "gmv_video", "gmv_live", "gmv_card",
    "target_revenue", "target_attainment_pct", "projected_month_revenue",
    "refunded_orders", "problem_rate", "avg_delivery_hours", "avg_delivery_days",
    "data_quality_score",
]

PERTENCEM_A_ORDERS = [
    "orders", "units_sold", "avg_ticket",
    "canceled_orders", "returned_orders", "cancel_rate_pct", "delivered_orders",
    "total_settlement", "total_fees", "avg_fee_pct", "avg_settlement_pct",
    "seller_shipping_cost", "shipping_pct_of_gmv",
]


def _linha_preexistente() -> dict:
    """Linha diaria ja publicada por shop-stats (GMV + funil) e por Ads."""
    return {
        "date": date(2026, 8, 14), "loja_id": 3, "marketplace_id": 3, "empresa_id": 1,
        "gmv": 146003.50, "visitors": 116863, "conversion_rate": 2.31,
        "new_buyers": 900, "repeat_buyers": 400, "repeat_buyer_rate_pct": 30.7,
        "unique_buyers": 1300,
        "ad_spend": 9544.56, "ad_revenue": 51000.00, "ad_impressions": 1775827,
        "ad_clicks": 22907, "roas": 5.34, "acos_pct": 18.7, "ctr_pct": 1.29,
        "cpc": 0.42,
        "gmv_video": None, "gmv_live": None, "gmv_card": None,
        "target_revenue": 200000.0, "target_attainment_pct": 73.0,
        "projected_month_revenue": 4100000.0,
        "refunded_orders": 7, "problem_rate": 1.2,
        "avg_delivery_hours": 40.0, "avg_delivery_days": 1.7,
        "data_quality_score": 0.95,
        "orders": 111, "units_sold": 111, "avg_ticket": 1.0,
        "canceled_orders": 1, "returned_orders": 1, "cancel_rate_pct": 1.0,
        "delivered_orders": 1, "total_settlement": 1.0, "total_fees": 1.0,
        "avg_fee_pct": 1.0, "avg_settlement_pct": 1.0,
        "seller_shipping_cost": 1.0, "shipping_pct_of_gmv": 1.0,
        "source_updated_at": "ANTES", "ingested_at": "ANTES",
    }


def _excluded_de_orders() -> dict:
    """O que o transform de orders produz: metricas proprias + None no resto."""
    return {
        "date": date(2026, 8, 14), "loja_id": 3, "marketplace_id": 3, "empresa_id": 1,
        "gmv": 99999.99,          # subtotal dos pedidos — NAO pode vencer
        "visitors": None, "conversion_rate": None,
        "new_buyers": None, "repeat_buyers": None, "repeat_buyer_rate_pct": None,
        "unique_buyers": 777,
        "ad_spend": None, "ad_revenue": None, "ad_impressions": None,
        "ad_clicks": None, "roas": None, "acos_pct": None, "ctr_pct": None,
        "cpc": None,
        "gmv_video": None, "gmv_live": None, "gmv_card": None,
        "target_revenue": None, "target_attainment_pct": None,
        "projected_month_revenue": None,
        "refunded_orders": None, "problem_rate": None,
        "avg_delivery_hours": None, "avg_delivery_days": None,
        "data_quality_score": None,
        "orders": 2244, "units_sold": 2600, "avg_ticket": 65.1,
        "canceled_orders": 120, "returned_orders": 3, "cancel_rate_pct": 5.1,
        "delivered_orders": 170, "total_settlement": 120000.0,
        "total_fees": 18000.0, "avg_fee_pct": 12.3, "avg_settlement_pct": 82.1,
        "seller_shipping_cost": 4000.0, "shipping_pct_of_gmv": 2.7,
        "source_updated_at": None,
    }


# ---------------------------------------------------------------------------
# 1-6: preservacao e atualizacao no conflito (comportamento aplicado)
# ---------------------------------------------------------------------------
def test_orders_preserva_gmv_de_shop_stats():
    antes = _linha_preexistente()
    depois = _aplicar_set(dp.PATCH_SHOPEE_ORDERS_SQL, antes, _excluded_de_orders())
    assert depois["gmv"] == antes["gmv"] == 146003.50
    assert depois["gmv"] != _excluded_de_orders()["gmv"]


def test_orders_preserva_funil_de_shop_stats():
    antes = _linha_preexistente()
    depois = _aplicar_set(dp.PATCH_SHOPEE_ORDERS_SQL, antes, _excluded_de_orders())
    for col in ("visitors", "conversion_rate", "new_buyers", "repeat_buyers",
                "repeat_buyer_rate_pct"):
        assert depois[col] == antes[col], col


def test_orders_preserva_todos_os_campos_de_ads():
    antes = _linha_preexistente()
    depois = _aplicar_set(dp.PATCH_SHOPEE_ORDERS_SQL, antes, _excluded_de_orders())
    for col in ("ad_spend", "ad_revenue", "ad_impressions", "ad_clicks",
                "roas", "acos_pct", "ctr_pct", "cpc"):
        assert depois[col] == antes[col], col


def test_orders_preserva_tudo_que_nao_lhe_pertence():
    antes = _linha_preexistente()
    depois = _aplicar_set(dp.PATCH_SHOPEE_ORDERS_SQL, antes, _excluded_de_orders())
    for col in NAO_PERTENCEM_A_ORDERS:
        assert depois[col] == antes[col], col


def test_orders_atualiza_os_campos_de_que_e_fonte():
    excl = _excluded_de_orders()
    depois = _aplicar_set(dp.PATCH_SHOPEE_ORDERS_SQL, _linha_preexistente(), excl)
    for col in PERTENCEM_A_ORDERS:
        assert depois[col] == excl[col], col


def test_orders_nao_sobrescreve_unique_buyers_autoritativo():
    antes = _linha_preexistente()
    depois = _aplicar_set(dp.PATCH_SHOPEE_ORDERS_SQL, antes, _excluded_de_orders())
    assert depois["unique_buyers"] == 1300


def test_orders_preenche_unique_buyers_quando_vazio():
    antes = _linha_preexistente()
    antes["unique_buyers"] = None
    depois = _aplicar_set(dp.PATCH_SHOPEE_ORDERS_SQL, antes, _excluded_de_orders())
    assert depois["unique_buyers"] == 777


# ---------------------------------------------------------------------------
# 7-8: chave nova nasce honesta
# ---------------------------------------------------------------------------
def test_insert_de_chave_nova_nao_traz_gmv():
    cols = _colunas_do_insert(dp.PATCH_SHOPEE_ORDERS_SQL)
    assert "gmv" not in cols


def test_insert_de_chave_nova_nao_fabrica_funil_nem_ads():
    cols = _colunas_do_insert(dp.PATCH_SHOPEE_ORDERS_SQL)
    for col in ("visitors", "conversion_rate", "new_buyers", "repeat_buyers",
                "repeat_buyer_rate_pct", "ad_spend", "ad_revenue",
                "ad_impressions", "ad_clicks", "roas", "acos_pct", "ctr_pct",
                "cpc"):
        assert col not in cols, col


def test_insert_de_chave_nova_traz_chaves_e_metricas_de_orders():
    cols = _colunas_do_insert(dp.PATCH_SHOPEE_ORDERS_SQL)
    for col in ("date", "loja_id", "marketplace_id", "empresa_id"):
        assert col in cols, col
    for col in PERTENCEM_A_ORDERS:
        assert col in cols, col


# ---------------------------------------------------------------------------
# 9-12: isolamento por source (comportamento real de run())
# ---------------------------------------------------------------------------
class _FakeResult:
    def scalar_one(self):
        return 1


class _SessaoQueRegistra:
    def __init__(self, registro):
        self.registro = registro

    def execute(self, sql, params=None):
        self.registro.append(str(sql))
        return _FakeResult()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _upserts_usados(monkeypatch, source):
    registro: list[str] = []

    @contextmanager
    def _fake_session():
        yield _SessaoQueRegistra(registro)

    monkeypatch.setattr(dp, "local_session", _fake_session)

    linha = {"date": date(2026, 8, 14), "loja_id": 3, "marketplace_id": 3,
             "empresa_id": 1}
    alvo = {
        "shopee": (dp.shopee_connector, "fetch_backfill", dp.shopee_transform),
        "shopee-stats": (dp.shopee_connector, "fetch_shop_stats_backfill",
                         dp.shopee_stats_transform),
        "shopee-ads": (dp.shopee_connector, "fetch_ads_backfill", dp.shopee_ads_transform),
        "tiktok": (dp.tiktok_connector, "fetch_backfill", dp.tiktok_transform),
        "ml": (dp.ml_connector, "fetch_backfill", dp.ml_transform),
    }[source]
    conector, metodo, transform = alvo
    monkeypatch.setattr(conector, metodo, lambda *a, **k: [{"x": 1}])
    monkeypatch.setattr(transform, "transform_batch", lambda rows: [linha])
    monkeypatch.setattr(dp.quality, "run_checks", lambda *a, **k: None, raising=False)

    dp.run(source=source, mode="backfill", days_back=1)
    return [s for s in registro if "INSERT INTO marts.fact_marketplace_daily" in s]


def test_source_shopee_usa_patch_de_orders(monkeypatch):
    usados = _upserts_usados(monkeypatch, "shopee")
    assert usados and all(s == str(dp.PATCH_SHOPEE_ORDERS_SQL) for s in usados)


def test_source_shopee_stats_continua_no_patch_proprio(monkeypatch):
    usados = _upserts_usados(monkeypatch, "shopee-stats")
    assert usados and all(s == str(dp.PATCH_SHOP_STATS_SQL) for s in usados)


def test_source_shopee_ads_continua_no_patch_proprio(monkeypatch):
    usados = _upserts_usados(monkeypatch, "shopee-ads")
    assert usados and all(s == str(dp.PATCH_ADS_SQL) for s in usados)


def test_source_tiktok_continua_no_upsert_completo(monkeypatch):
    usados = _upserts_usados(monkeypatch, "tiktok")
    assert usados and all(s == str(dp.UPSERT_SQL) for s in usados)


def test_source_ml_continua_no_upsert_completo(monkeypatch):
    usados = _upserts_usados(monkeypatch, "ml")
    assert usados and all(s == str(dp.UPSERT_SQL) for s in usados)


# ---------------------------------------------------------------------------
# 11: shop-stats continua autoritativo do GMV; ads continua so em ads
# ---------------------------------------------------------------------------
def test_shop_stats_continua_sobrescrevendo_gmv():
    sets = _atribuicoes_do_set(dp.PATCH_SHOP_STATS_SQL)
    assert sets["gmv"] == "EXCLUDED.gmv"


def test_ads_continua_restrito_a_ads():
    sets = _atribuicoes_do_set(dp.PATCH_ADS_SQL)
    tocadas = set(sets) - {"source_updated_at", "ingested_at"}
    assert tocadas == {"ad_spend", "ad_revenue", "ad_impressions", "ad_clicks",
                       "roas", "acos_pct", "ctr_pct", "cpc"}


# ---------------------------------------------------------------------------
# 13-15: contrato estrutural do patch
# ---------------------------------------------------------------------------
def test_set_de_orders_nao_toca_em_coluna_de_outra_fonte():
    sets = set(_atribuicoes_do_set(dp.PATCH_SHOPEE_ORDERS_SQL))
    for col in NAO_PERTENCEM_A_ORDERS:
        assert col not in sets, col


def test_set_de_orders_cobre_todas_as_colunas_proprias():
    sets = set(_atribuicoes_do_set(dp.PATCH_SHOPEE_ORDERS_SQL))
    for col in PERTENCEM_A_ORDERS:
        assert col in sets, col


def test_patch_de_orders_nao_tem_interpolacao_insegura():
    txt = str(dp.PATCH_SHOPEE_ORDERS_SQL)
    assert "%" not in txt
    assert "format(" not in txt
    assert "+" not in txt
    # todo parametro e' bind nomeado
    assert re.search(r":date\b", txt)


def test_colunas_do_insert_e_do_values_tem_o_mesmo_tamanho():
    txt = str(dp.PATCH_SHOPEE_ORDERS_SQL)
    cols = _colunas_do_insert(dp.PATCH_SHOPEE_ORDERS_SQL)
    bloco = txt.split("VALUES", 1)[1]
    dentro = bloco[bloco.index("(") + 1: bloco.index(")")]
    valores = [v.strip() for v in dentro.split(",") if v.strip()]
    assert len(cols) == len(valores)


def test_upsert_completo_permanece_intacto_para_ml_e_tiktok():
    """O UPSERT_SQL nao pode ter sido estreitado: ML/TikTok dependem dele."""
    sets = set(_atribuicoes_do_set(dp.UPSERT_SQL))
    for col in ("gmv", "visitors", "conversion_rate", "ad_spend", "gmv_video",
                "orders", "units_sold", "total_settlement"):
        assert col in sets, col


def test_patch_aceita_o_dict_canonico_completo_do_transform():
    """O `run()` passa a linha canonica INTEIRA (~44 chaves) para o execute.

    Como este patch declara menos binds que o UPSERT completo, e' preciso provar
    que os parametros excedentes nao quebram a compilacao — caso contrario a
    escrita falharia so' em producao.
    """
    from sqlalchemy.dialects import postgresql

    from pipelines.transforms.shopee_orders_daily import transform

    linha = transform({
        "date": date(2026, 8, 14), "brand": "kokeshi",
        "gmv": 1.0, "orders": 2, "units_sold": 3, "avg_ticket": 4.0,
        "unique_buyers": 5, "canceled_orders": 6, "returned_orders": 7,
        "cancel_rate_pct": 8.0, "delivered_orders": 9,
        "total_settlement": 10.0, "total_fees": 11.0, "avg_fee_pct": 12.0,
        "avg_settlement_pct": 13.0, "seller_shipping_cost": 14.0,
        "shipping_pct_of_gmv": 15.0,
    })
    assert linha is not None
    # o transform devolve muito mais chaves do que o patch declara
    binds = set(re.findall(r":(\w+)", str(dp.PATCH_SHOPEE_ORDERS_SQL)))
    assert set(linha) - binds, "esperado haver chaves excedentes neste cenario"
    # toda chave que o SQL exige precisa existir na linha canonica
    assert binds <= set(linha), f"binds sem valor: {binds - set(linha)}"
    # e a compilacao com o dict completo nao pode levantar
    compilado = dp.PATCH_SHOPEE_ORDERS_SQL.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    assert "INSERT INTO marts.fact_marketplace_daily_performance" in str(compilado)


@pytest.mark.parametrize("col", ["gmv", "visitors", "ad_spend"])
def test_regressao_orders_nunca_zera_coluna_de_outra_fonte(col):
    antes = _linha_preexistente()
    excl = _excluded_de_orders()
    excl[col] = None
    depois = _aplicar_set(dp.PATCH_SHOPEE_ORDERS_SQL, antes, excl)
    assert depois[col] == antes[col]
    assert depois[col] is not None
