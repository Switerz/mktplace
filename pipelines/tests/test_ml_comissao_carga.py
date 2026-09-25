"""
MARGEM-REAL-2 — a comissão do ML atravessando a carga até a fato.

`test_ml_comissao_sql.py` prova a consulta; este arquivo prova o que acontece
DEPOIS dela: transform -> `daily_performance.UPSERT_SQL` -> fato.

O que se mede aqui:

  * a comissão chega à fato e sobrevive a um rerun;
  * uma execução normal do ML deixa de gravar NULL onde antes gravava;
  * o rerun não estraga os demais campos da linha;
  * e o caso degenerado — fonte de comissão vazia num dia já publicado —
    está medido e documentado, não suposto.

Sem PostgreSQL (`PMA_TEST_PG_BIN`, `.local/postgres16` ou no PATH), SKIP.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from pipelines.ingestion import daily_performance as dp
from pipelines.tests.banco_descartavel import DDL as MARTS_DDL
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES,
    cluster_descartavel,
    postgres_disponivel,
)
from pipelines.transforms import ml_gestao_diaria

pytestmark = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

CHAVE = {"date": "2026-07-10", "loja_id": 3, "marketplace_id": 2}


@pytest.fixture()
def banco():
    with cluster_descartavel() as url:
        eng = create_engine(url)
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for stmt in filter(None, (s.strip() for s in MARTS_DDL.split(";"))):
                conn.execute(text(stmt))
        yield eng
        eng.dispose()


def _linha_gold(**over) -> dict:
    """Uma linha como o conector do ML a devolve."""
    row = {
        "date": "2026-07-10",
        "brand": "kokeshi",
        "gmv": 1000.0,
        "orders": 10,
        "units_sold": 12,
        "avg_ticket": 100.0,
        "unique_buyers": 9,
        "canceled_orders": 1,
        "cancel_rate_pct": 9.1,
        "delivered_orders": 8,
        "avg_delivery_days": 3.0,
        "seller_shipping_cost": 120.0,
        "shipping_pct_of_gmv": 12.0,
        "ad_spend": 50.0,
        "ad_revenue": 400.0,
        "ad_impressions": 10000,
        "ad_clicks": 300,
        "roas": 8.0,
        "acos_pct": 12.5,
        "ctr_pct": 3.0,
        "cpc": 0.17,
        "marketplace_fee": 170.0,
        "fee_orders": 10,
    }
    row.update(over)
    return row


def _carregar(banco, *rows):
    canonicos = ml_gestao_diaria.transform_batch(list(rows))
    with banco.begin() as conn:
        for c in canonicos:
            conn.execute(dp.UPSERT_SQL, c)
    return canonicos


def _fato(banco):
    with banco.connect() as conn:
        row = conn.execute(
            text(
                "SELECT * FROM marts.fact_marketplace_daily_performance "
                "WHERE date=:date AND loja_id=:loja_id AND marketplace_id=:marketplace_id"
            ),
            CHAVE,
        ).mappings().first()
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# A comissão chega
# ---------------------------------------------------------------------------

def test_comissao_chega_a_fato_com_take_rate(banco):
    _carregar(banco, _linha_gold())
    linha = _fato(banco)
    assert float(linha["total_fees"]) == 170.0
    assert float(linha["avg_fee_pct"]) == 17.0


def test_execucao_normal_deixa_de_gravar_null(banco):
    """A regressão que este gate fecha.

    Antes, o transform devolvia `total_fees: None` incondicionalmente e o
    `ON CONFLICT ... DO UPDATE SET total_fees = EXCLUDED.total_fees` gravava
    NULL a cada execução. Agora a execução normal carrega o valor.
    """
    _carregar(banco, _linha_gold())
    assert _fato(banco)["total_fees"] is not None

    # segunda execução normal, mesma fonte: o valor permanece
    _carregar(banco, _linha_gold())
    assert float(_fato(banco)["total_fees"]) == 170.0


def test_sinal_positivo_sobrevive_ao_armazenamento(banco):
    _carregar(banco, _linha_gold(marketplace_fee=170.0))
    assert float(_fato(banco)["total_fees"]) > 0


# ---------------------------------------------------------------------------
# Idempotência e preservação
# ---------------------------------------------------------------------------

def test_rerun_e_idempotente(banco):
    _carregar(banco, _linha_gold())
    primeira = _fato(banco)
    _carregar(banco, _linha_gold())
    segunda = _fato(banco)

    ignorar = {"ingested_at", "id"}
    assert {k: v for k, v in primeira.items() if k not in ignorar} == \
           {k: v for k, v in segunda.items() if k not in ignorar}


def test_rerun_nao_estraga_os_demais_campos_da_linha(banco):
    _carregar(banco, _linha_gold())
    _carregar(banco, _linha_gold())
    linha = _fato(banco)
    assert float(linha["gmv"]) == 1000.0
    assert linha["orders"] == 10
    assert float(linha["ad_spend"]) == 50.0
    assert float(linha["seller_shipping_cost"]) == 120.0


def test_comissao_atualizada_na_fonte_atualiza_a_fato(banco):
    """Correção legítima para cima ou para baixo precisa atravessar."""
    _carregar(banco, _linha_gold(marketplace_fee=170.0))
    assert float(_fato(banco)["total_fees"]) == 170.0

    _carregar(banco, _linha_gold(marketplace_fee=185.0))
    linha = _fato(banco)
    assert float(linha["total_fees"]) == 185.0
    assert float(linha["avg_fee_pct"]) == 18.5


def test_ads_e_frete_seguem_em_colunas_proprias_e_nao_entram_no_fee(banco):
    """Contraprova de dupla contagem na linha já gravada."""
    _carregar(banco, _linha_gold())
    linha = _fato(banco)
    assert float(linha["total_fees"]) == 170.0
    assert float(linha["ad_spend"]) == 50.0
    assert float(linha["seller_shipping_cost"]) == 120.0
    assert float(linha["total_fees"]) != 170.0 + 50.0 + 120.0


# ---------------------------------------------------------------------------
# Ausência
# ---------------------------------------------------------------------------

def test_dia_sem_comissao_grava_null_e_nao_zero(banco):
    _carregar(banco, _linha_gold(marketplace_fee=None, gmv=0.0, orders=0))
    linha = _fato(banco)
    assert linha["total_fees"] is None
    assert linha["avg_fee_pct"] is None


def test_fonte_de_comissao_vazia_num_dia_ja_publicado_APAGA_o_valor(banco):
    """Comportamento MEDIDO, não desejado — e é por isso que está aqui.

    O `ON CONFLICT` da fato é `DO UPDATE SET total_fees = EXCLUDED.total_fees`,
    sem COALESCE, e é compartilhado com TikTok e Shopee. Logo: se um rerun
    encontrar a gold com o dia mas `api.ml_orders` sem nenhum pedido pago nele,
    o NULL sobrescreve a comissão já publicada.

    Na prática a fonte transacional só cresce, então o cenário exige um
    incidente de ingestão — mas ele não é impossível, e quem operar o backfill
    precisa saber que a proteção é a ordem de carga e o lock, não o SQL.

    Este teste falha no dia em que alguém adicionar COALESCE ao upsert. Isso é
    intencional: a mudança de contrato deve ser deliberada, não silenciosa.
    """
    _carregar(banco, _linha_gold(marketplace_fee=170.0))
    assert float(_fato(banco)["total_fees"]) == 170.0

    _carregar(banco, _linha_gold(marketplace_fee=None))
    assert _fato(banco)["total_fees"] is None


# ---------------------------------------------------------------------------
# Transação
# ---------------------------------------------------------------------------

def test_erro_no_meio_do_lote_desfaz_a_transacao_inteira(banco):
    """Rollback: meia carga é pior que carga nenhuma, porque parece completa."""
    ok = ml_gestao_diaria.transform(_linha_gold())

    with pytest.raises(Exception):
        with banco.begin() as conn:
            conn.execute(dp.UPSERT_SQL, ok)
            quebrado = dict(ok)
            quebrado["loja_id"] = None      # NOT NULL na fato
            conn.execute(dp.UPSERT_SQL, quebrado)

    assert _fato(banco) is None, "nada deve ter sido gravado"


def test_marca_fora_da_dimensao_nao_gera_linha(banco):
    canonicos = _carregar(banco, _linha_gold(brand="azbuy"))
    assert canonicos == []
    assert _fato(banco) is None
