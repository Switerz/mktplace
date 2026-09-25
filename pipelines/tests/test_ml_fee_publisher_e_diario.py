"""
MARGEM-REAL-3B — convivência entre o publisher financeiro e o pipeline diário.

O publisher NÃO substitui `daily_performance --source ml`. Os dois escrevem na
mesma linha da fato, com escopos diferentes e de propósito:

  * o **diário** mantém a fotografia completa da janela recente — GMV, pedidos,
    Ads, frete e operação — e, desde o MARGEM-REAL-2, também a comissão;
  * o **publisher** toca duas colunas, e existe para o backfill histórico e
    para reparo financeiro.

O risco de ter dois escritores é um desfazer o outro. Estes testes medem
exatamente isso, nas duas ordens.

Sem PostgreSQL (`PMA_TEST_PG_BIN`, `.local/postgres16` ou no PATH), SKIP.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from pipelines import sync_ml_marketplace_fees as pub
from pipelines.ingestion import daily_performance as dp
from pipelines.quality.ml_fee_reconciliation import reconciliar
from pipelines.tests.banco_descartavel import DDL as MARTS_DDL
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES,
    cluster_descartavel,
    postgres_disponivel,
)
from pipelines.transforms import ml_gestao_diaria as ml_transform

pytestmark = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

D1 = date(2026, 7, 10)
KOKESHI, ML_ID = 3, 2
BRANDS = ("barbours", "kokeshi", "lescent", "rituaria")


@pytest.fixture()
def banco():
    with cluster_descartavel() as url:
        eng = create_engine(url)
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for stmt in filter(None, (s.strip() for s in MARTS_DDL.split(";"))):
                conn.execute(text(stmt))
        yield eng
        eng.dispose()


@pytest.fixture()
def fabrica(banco):
    return sessionmaker(bind=banco)


@contextmanager
def _lock_livre(marketplace_id):
    yield 918_130_002


def _bruta(**over) -> dict:
    """Linha crua do conector do ML — a mesma que alimenta os dois caminhos."""
    row = {
        "date": D1, "brand": "kokeshi",
        "gmv": Decimal("1000.00"), "orders": 10, "units_sold": 12,
        "avg_ticket": Decimal("100.00"), "unique_buyers": 9,
        "canceled_orders": 1, "cancel_rate_pct": Decimal("9.1"),
        "delivered_orders": 8, "avg_delivery_days": Decimal("3"),
        "seller_shipping_cost": Decimal("120.00"),
        "shipping_pct_of_gmv": Decimal("12.00"),
        "ad_spend": Decimal("50.00"), "ad_revenue": Decimal("400.00"),
        "ad_impressions": 10000, "ad_clicks": 300, "roas": Decimal("8.00"),
        "marketplace_fee": Decimal("170.00"), "fee_orders": 10,
        "paid_gmv": Decimal("1000.00"), "paid_orders_src": 10,
    }
    row.update(over)
    return row


def _rodar_diario(banco, brutas):
    """Reproduz o caminho do pipeline diário: guardrail + transform + UPSERT."""
    reconciliar(brutas, brands_esperadas=BRANDS)
    canonicas = ml_transform.transform_batch(brutas)
    with banco.begin() as conn:
        for c in canonicas:
            conn.execute(dp.UPSERT_SQL, c)


def _rodar_publisher(banco, fabrica, brutas):
    def _fetch(df, dt):
        reconciliar(brutas, brands_esperadas=BRANDS, date_from=df, date_to=dt)
        return brutas
    return pub.publicar(D1, D1, fetch=_fetch, session_factory=fabrica,
                        lock_cm=_lock_livre)


def _linha(banco):
    with banco.connect() as conn:
        r = conn.execute(text(
            "SELECT gmv, orders, units_sold, ad_spend, seller_shipping_cost, "
            "total_fees, avg_fee_pct "
            "FROM marts.fact_marketplace_daily_performance "
            "WHERE date=:d AND loja_id=:l AND marketplace_id=2"
        ), {"d": D1, "l": KOKESHI}).mappings().first()
    return dict(r) if r else None


# ---------------------------------------------------------------------------
# 2. O pipeline incremental continua completo
# ---------------------------------------------------------------------------

def test_diario_continua_publicando_a_fotografia_completa(banco):
    _rodar_diario(banco, [_bruta()])
    l = _linha(banco)
    assert l["gmv"] == Decimal("1000.00")
    assert l["orders"] == 10
    assert l["units_sold"] == 12
    assert l["ad_spend"] == Decimal("50.00")
    assert l["seller_shipping_cost"] == Decimal("120.00")
    # e a comissão, desde o MARGEM-REAL-2
    assert l["total_fees"] == Decimal("170.00")
    assert l["avg_fee_pct"] == Decimal("17.00")


# ---------------------------------------------------------------------------
# 3. Execução incremental posterior não volta a NULL
# ---------------------------------------------------------------------------

def test_incremental_depois_do_patch_nao_zera_a_comissao(banco, fabrica):
    """A ordem que mais preocupa: o backfill publica, e o diário roda depois.

    Com o transform do MARGEM-REAL-2 o diário reescreve a comissão com o valor
    da fonte — não com NULL. É o que garante que a próxima execução das 06:00
    não desfaça o backfill.
    """
    _rodar_diario(banco, [_bruta(marketplace_fee=None, paid_gmv=None,
                                 paid_orders_src=None, gmv=Decimal("0.00"),
                                 orders=0)])
    assert _linha(banco)["total_fees"] is None

    _rodar_diario(banco, [_bruta()])          # dia amadurece, fonte aparece
    _rodar_publisher(banco, fabrica, [_bruta(marketplace_fee=Decimal("175.00"))])
    assert _linha(banco)["total_fees"] == Decimal("175.00")

    # agora o diário roda de novo, como às 06:00
    _rodar_diario(banco, [_bruta(marketplace_fee=Decimal("175.00"))])
    l = _linha(banco)
    assert l["total_fees"] == Decimal("175.00"), "o diario NAO pode zerar"
    assert l["total_fees"] is not None


def test_patch_depois_do_diario_preserva_a_fotografia(banco, fabrica):
    """A ordem inversa: o diário publica tudo, o patch corrige só a comissão."""
    _rodar_diario(banco, [_bruta()])
    antes = _linha(banco)

    _rodar_publisher(banco, fabrica, [_bruta(marketplace_fee=Decimal("165.00"))])
    depois = _linha(banco)

    for col in ("gmv", "orders", "units_sold", "ad_spend", "seller_shipping_cost"):
        assert depois[col] == antes[col], f"{col} mudou e nao devia"
    assert depois["total_fees"] == Decimal("165.00")


# ---------------------------------------------------------------------------
# 5. Comissão não é somada duas vezes
# ---------------------------------------------------------------------------

def test_comissao_nunca_e_somada_duas_vezes(banco, fabrica):
    """Os dois escritores ATRIBUEM, nunca acumulam.

    Rodar diário + patch + diário + patch tem de terminar no mesmo valor de uma
    execução só. Se algum deles usasse `total_fees = total_fees + :x`, este
    teste mostraria 680 em vez de 170.
    """
    _rodar_diario(banco, [_bruta()])
    _rodar_publisher(banco, fabrica, [_bruta()])
    _rodar_diario(banco, [_bruta()])
    _rodar_publisher(banco, fabrica, [_bruta()])

    l = _linha(banco)
    assert l["total_fees"] == Decimal("170.00")
    assert l["avg_fee_pct"] == Decimal("17.00")


def _sem_espacos(sql: str) -> str:
    """O SQL é alinhado para leitura; a asserção não pode depender disso."""
    return "".join(str(sql).lower().split())


def test_sql_do_patch_atribui_e_nao_acumula():
    sql = _sem_espacos(pub.SQL_PATCH_FEES)
    assert "total_fees=:total_fees" in sql
    assert "total_fees+" not in sql
    assert "total_fees=total_fees" not in sql


def test_upsert_do_diario_tambem_atribui_e_nao_acumula():
    sql = _sem_espacos(dp.UPSERT_SQL)
    assert "total_fees=excluded.total_fees" in sql
    assert "total_fees+" not in sql
