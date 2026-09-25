"""
MARGEM-REAL-2B — o guardrail contra a fato real, em PostgreSQL descartável.

`test_ml_fee_guardrail.py` prova que a barreira dispara. Aqui se prova o que
importa de verdade: **quando ela dispara, a fato não muda uma linha**.

Um guardrail que levanta mas deixa metade da carga gravada é pior que nenhum,
porque produz uma fotografia meio velha e meio nova sem aviso. Por isso estes
testes tiram um snapshot completo da tabela antes, provocam a falha, e comparam
byte a byte depois — incluindo as linhas de Shopee e TikTok, que compartilham a
mesma fato e o mesmo `UPSERT_SQL`.

Sem PostgreSQL (`PMA_TEST_PG_BIN`, `.local/postgres16` ou no PATH), SKIP.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from pipelines.ingestion import daily_performance as dp
from pipelines.quality.ml_fee_reconciliation import (
    MlFeeReconciliationError,
    reconciliar,
)
from pipelines.tests.banco_descartavel import DDL as MARTS_DDL
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES,
    cluster_descartavel,
    postgres_disponivel,
)
from pipelines.transforms import ml_gestao_diaria

pytestmark = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

BRANDS = ("barbours", "kokeshi", "lescent", "rituaria")
TIKTOK_ID, ML_ID, SHOPEE_ID = 1, 2, 3
D = date(2026, 7, 10)


@pytest.fixture()
def banco():
    with cluster_descartavel() as url:
        eng = create_engine(url)
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for stmt in filter(None, (s.strip() for s in MARTS_DDL.split(";"))):
                conn.execute(text(stmt))
        yield eng
        eng.dispose()


def _bruta(**over) -> dict:
    """Linha crua do conector: colunas da Gold + comissão + reconciliação."""
    row = {
        "date": D,
        "brand": "kokeshi",
        "gmv": Decimal("1000.00"),
        "orders": 10,
        "units_sold": 12,
        "avg_ticket": Decimal("100.00"),
        "seller_shipping_cost": Decimal("120.00"),
        "ad_spend": Decimal("50.00"),
        "marketplace_fee": Decimal("170.00"),
        "fee_orders": 10,
        "paid_gmv": Decimal("1000.00"),
        "paid_orders_src": 10,
    }
    row.update(over)
    return row


def _publicar(banco, brutas):
    """Reproduz a ordem real do loader: guardrail ANTES do upsert."""
    canonicos = ml_gestao_diaria.transform_batch(list(brutas))
    reconciliar(brutas, brands_esperadas=BRANDS)
    with banco.begin() as conn:
        for c in canonicos:
            conn.execute(dp.UPSERT_SQL, c)


def _snapshot(banco):
    with banco.connect() as conn:
        rows = conn.execute(text(
            "SELECT date, loja_id, marketplace_id, gmv, orders, total_fees, "
            "avg_fee_pct, ad_spend, seller_shipping_cost "
            "FROM marts.fact_marketplace_daily_performance "
            "ORDER BY date, loja_id, marketplace_id"
        )).mappings().all()
    return [dict(r) for r in rows]


def _semear_outros_canais(banco):
    """Uma linha de Shopee e uma de TikTok, com comissão já publicada."""
    base = {
        "date": D, "empresa_id": 1, "gmv": 5000, "orders": 50,
        "units_sold": 60, "avg_ticket": 100, "unique_buyers": 45,
        "new_buyers": None, "repeat_buyers": None, "repeat_buyer_rate_pct": None,
        "visitors": None, "conversion_rate": None, "canceled_orders": 2,
        "returned_orders": None, "refunded_orders": None, "problem_rate": None,
        "cancel_rate_pct": None, "delivered_orders": None,
        "avg_delivery_hours": None, "avg_delivery_days": None,
        "ad_spend": None, "ad_revenue": None, "ad_impressions": None,
        "ad_clicks": None, "roas": None, "acos_pct": None, "ctr_pct": None,
        "cpc": None, "gmv_video": None, "gmv_live": None, "gmv_card": None,
        "total_settlement": None, "avg_settlement_pct": None,
        "seller_shipping_cost": None, "shipping_pct_of_gmv": None,
        "target_revenue": None, "target_attainment_pct": None,
        "projected_month_revenue": None, "data_quality_score": None,
        "source_updated_at": None,
    }
    with banco.begin() as conn:
        conn.execute(dp.UPSERT_SQL, {**base, "loja_id": 3, "marketplace_id": SHOPEE_ID,
                                     "total_fees": 1300, "avg_fee_pct": 26.0})
        conn.execute(dp.UPSERT_SQL, {**base, "loja_id": 3, "marketplace_id": TIKTOK_ID,
                                     "total_fees": -1500, "avg_fee_pct": 30.0})


# ---------------------------------------------------------------------------
# 7. Falha preserva integralmente o snapshot anterior
# ---------------------------------------------------------------------------

def test_7_falha_preserva_o_snapshot_anterior_byte_a_byte(banco):
    _publicar(banco, [_bruta()])
    antes = _snapshot(banco)
    assert antes[0]["total_fees"] == Decimal("170.00")

    # Agora a fonte perde a célula paga — exatamente o incidente que o
    # guardrail existe para conter.
    with pytest.raises(MlFeeReconciliationError):
        _publicar(banco, [_bruta(marketplace_fee=None, fee_orders=None,
                                 paid_gmv=None, paid_orders_src=None)])

    assert _snapshot(banco) == antes


def test_7b_comissao_publicada_nao_e_apagada_nem_zerada(banco):
    _publicar(banco, [_bruta()])
    with pytest.raises(MlFeeReconciliationError):
        _publicar(banco, [_bruta(marketplace_fee=None, fee_orders=None,
                                 paid_gmv=None, paid_orders_src=None)])
    linha = _snapshot(banco)[0]
    assert linha["total_fees"] == Decimal("170.00"), "não apagou"
    assert linha["total_fees"] != 0, "não virou zero"
    assert linha["avg_fee_pct"] == Decimal("17.00")


def test_7c_lote_parcialmente_ruim_nao_grava_nem_as_celulas_boas(banco):
    """Fail-closed é sobre a fotografia inteira: publicar as boas deixaria a
    fato meio atualizada, e nada no banco diria isso."""
    antes = _snapshot(banco)
    assert antes == []

    rows = [
        _bruta(brand="kokeshi"),
        _bruta(brand="barbours", paid_gmv=None, paid_orders_src=None,
               marketplace_fee=None),
    ]
    with pytest.raises(MlFeeReconciliationError):
        _publicar(banco, rows)

    assert _snapshot(banco) == [], "nenhuma linha boa foi publicada"


def test_7d_gmv_divergente_tambem_preserva(banco):
    _publicar(banco, [_bruta()])
    antes = _snapshot(banco)
    with pytest.raises(MlFeeReconciliationError):
        _publicar(banco, [_bruta(marketplace_fee=Decimal("999.00"),
                                 paid_gmv=Decimal("1200.00"))])
    assert _snapshot(banco) == antes


# ---------------------------------------------------------------------------
# 8. Shopee e TikTok intactos
# ---------------------------------------------------------------------------

def test_8_falha_do_ml_nao_altera_shopee_nem_tiktok(banco):
    _semear_outros_canais(banco)
    outros_antes = [r for r in _snapshot(banco) if r["marketplace_id"] != ML_ID]
    assert len(outros_antes) == 2

    with pytest.raises(MlFeeReconciliationError):
        _publicar(banco, [_bruta(marketplace_fee=None, fee_orders=None,
                                 paid_gmv=None, paid_orders_src=None)])

    outros_depois = [r for r in _snapshot(banco) if r["marketplace_id"] != ML_ID]
    assert outros_depois == outros_antes


def test_8b_carga_bem_sucedida_do_ml_tambem_nao_toca_os_outros(banco):
    """O sucesso não pode vazar para os vizinhos mais do que a falha."""
    _semear_outros_canais(banco)
    outros_antes = [r for r in _snapshot(banco) if r["marketplace_id"] != ML_ID]

    _publicar(banco, [_bruta()])

    outros_depois = [r for r in _snapshot(banco) if r["marketplace_id"] != ML_ID]
    assert outros_depois == outros_antes
    # e o sinal de cada canal continua com a convenção que é dele
    tk = next(r for r in outros_depois if r["marketplace_id"] == TIKTOK_ID)
    sh = next(r for r in outros_depois if r["marketplace_id"] == SHOPEE_ID)
    ml = next(r for r in _snapshot(banco) if r["marketplace_id"] == ML_ID)
    assert tk["total_fees"] < 0
    assert sh["total_fees"] > 0
    assert ml["total_fees"] > 0


# ---------------------------------------------------------------------------
# 6 (na fato). Zero comprovado grava zero
# ---------------------------------------------------------------------------

def test_6_fato_zero_comprovado_grava_zero_e_nao_null(banco):
    _publicar(banco, [_bruta(marketplace_fee=Decimal("0.00"), fee_orders=10)])
    linha = _snapshot(banco)[0]
    assert linha["total_fees"] == Decimal("0.00")
    assert linha["total_fees"] is not None
    assert linha["avg_fee_pct"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# 1 (na fato). Fonte completa atualiza
# ---------------------------------------------------------------------------

def test_1_fonte_completa_atualiza_a_comissao(banco):
    _publicar(banco, [_bruta(marketplace_fee=Decimal("170.00"))])
    assert _snapshot(banco)[0]["total_fees"] == Decimal("170.00")

    _publicar(banco, [_bruta(marketplace_fee=Decimal("185.00"))])
    linha = _snapshot(banco)[0]
    assert linha["total_fees"] == Decimal("185.00")
    assert linha["avg_fee_pct"] == Decimal("18.50")


def test_1b_guardrail_roda_antes_do_upsert_no_loader():
    """Contraprova de POSIÇÃO: a chamada tem de estar antes da escrita.

    Se alguém mover o guardrail para depois do upsert, ele vira um relatório
    de algo que já aconteceu — e todos os testes acima continuariam passando,
    porque eles chamam as duas coisas na ordem certa por conta própria.
    """
    import inspect

    fonte = inspect.getsource(dp.run)
    pos_guardrail = fonte.index("ml_fee_reconciliation.reconciliar")
    pos_upsert = fonte.index("session.execute(upsert_sql, row)")
    assert pos_guardrail < pos_upsert


def test_guardrail_so_roda_para_a_fonte_ml():
    """Contraprova de ESCOPO.

    Shopee e TikTok não têm `paid_gmv` nem `paid_orders_src` nas suas linhas
    cruas — se o guardrail rodasse para eles, toda carga desses canais abortaria
    com "fonte paga não tem a célula". A chamada precisa estar sob
    `if source == "ml"`, e nada no teste 8 provaria isso, porque lá o `source`
    nunca é outro.
    """
    import inspect
    import re

    fonte = inspect.getsource(dp.run)
    chamada = fonte.index("ml_fee_reconciliation.reconciliar")
    antes = fonte[:chamada]
    guarda = re.search(r'if\s+source\s*==\s*[\'"]ml[\'"]\s*:\s*$', antes.rstrip().split("\n")[-1])
    assert guarda is not None, (
        "a chamada do guardrail nao esta imediatamente sob `if source == \"ml\":`"
    )


def test_linhas_cruas_de_shopee_nao_passariam_pelo_guardrail():
    """O outro lado da mesma prova: se alguém remover o `if`, isto mostra o
    estrago — uma linha típica de Shopee aborta na hora."""
    linha_shopee = {"date": D, "brand": "kokeshi", "gmv": Decimal("5000.00"),
                    "orders": 50}
    with pytest.raises(MlFeeReconciliationError):
        reconciliar([linha_shopee], brands_esperadas=BRANDS)
