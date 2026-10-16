"""Gate SHOPEE-MANUAL-RETIREMENT-1 — shadow diario contra Postgres REAL.

O que um dublê nao prova: o SQL de agregacao (um pedido com N itens nao pode
multiplicar o frete), o escopo do DELETE, o rollback da guarda de colapso, o
advisory lock e os CHECKs da migration 027. Cluster descartavel; nunca toca
Neon nem Data Mart.
"""
from __future__ import annotations

import types
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from pipelines import sync_shopee_daily_api_shadow as S
from pipelines.tests.postgres_descartavel import MOTIVO_SEM_POSTGRES, cluster_da_sessao, postgres_disponivel

pytestmark = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

REPO = Path(__file__).resolve().parents[2]
HOJE = date(2026, 10, 16)
AGORA = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
D1, D2, D3 = date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)

DDL_BASE = [
    "CREATE SCHEMA marts", "CREATE SCHEMA audit", "CREATE SCHEMA silver",
    """CREATE TABLE audit.source_sync_run (
        sync_run_id SERIAL PRIMARY KEY, source_name VARCHAR(100) NOT NULL, marketplace_id INT, loja_id INT,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ,
        status VARCHAR(20) NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed')),
        rows_extracted INT, rows_loaded INT, error_message TEXT)""",
    # Subconjunto das colunas reais de silver.stg_shopee_orders / _order_items.
    """CREATE TABLE silver.stg_shopee_orders (
        shop_account TEXT, order_sn TEXT, created_date_brt DATE, order_status TEXT,
        estimated_shipping_fee NUMERIC, ingested_at TIMESTAMPTZ)""",
    """CREATE TABLE silver.stg_shopee_order_items (
        shop_account TEXT, order_sn TEXT, order_item_id BIGINT, quantity INT, item_total NUMERIC)""",
]


def carregar_migration_027():
    caminho = REPO / "apps" / "api" / "alembic" / "versions" / "027_create_fact_shopee_daily_api_shadow.py"
    fonte = caminho.read_text(encoding="utf-8")
    ns: dict = {}
    exec(compile(fonte.replace("from alembic import op", "op = None"), str(caminho), "exec"), ns)  # noqa: S102
    return types.SimpleNamespace(**{k: v for k, v in ns.items() if not k.startswith("__")})


def _banco(base: str, nome: str, com_migration: bool = True):
    adm = create_engine(base, future=True, isolation_level="AUTOCOMMIT")
    with adm.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {nome} WITH (FORCE)"))
        c.execute(text(f"CREATE DATABASE {nome}"))
    adm.dispose()
    eng = create_engine(base.rsplit("/", 1)[0] + "/" + nome, future=True)
    with eng.begin() as c:
        for s in DDL_BASE + (carregar_migration_027().UPGRADE_SQL if com_migration else []):
            c.execute(text(s))
    return eng


@pytest.fixture
def banco():
    with cluster_da_sessao() as base:
        eng = _banco(base, "shp_shadow")
        yield eng
        eng.dispose()


def _pedido(eng, conta, sn, d, status, frete, itens):
    with eng.begin() as c:
        c.execute(text("INSERT INTO silver.stg_shopee_orders VALUES (:a, :sn, :d, :st, :f, :ing)"),
                  {"a": conta, "sn": sn, "d": d, "st": status, "f": frete, "ing": AGORA})
        for i, (q, tot) in enumerate(itens):
            c.execute(text("INSERT INTO silver.stg_shopee_order_items VALUES (:a, :sn, :i, :q, :t)"),
                      {"a": conta, "sn": sn, "i": i, "q": q, "t": tot})


def _semear(eng):
    # apice D1: pedido com 3 itens (frete 10 conta UMA vez), um concluido, um cancelado.
    _pedido(eng, "apice", "A1", D1, "shipped", 10, [(1, 50), (2, 30), (1, 20)])
    _pedido(eng, "apice", "A2", D1, "completed", 5, [(1, 40)])
    _pedido(eng, "apice", "A3", D1, "cancelled", 7, [(4, 99)])
    _pedido(eng, "apice", "A4", D2, "to_return", 3, [(1, 10)])
    _pedido(eng, "apice", "A5", D2, "unpaid", 2, [(2, 22)])
    _pedido(eng, "barbours", "B1", D1, "completed", 8, [(3, 60)])
    # kokeshi NUNCA entra, mesmo presente na fonte.
    _pedido(eng, "kokeshi", "K1", D1, "completed", 9, [(1, 15)])
    # fora da janela pedida
    _pedido(eng, "apice", "A9", date(2026, 9, 1), "completed", 1, [(1, 1)])


def _snap(eng, marcas=("apice", "barbours"), inicio=D1, fim=D3) -> S.Snapshot:
    with eng.connect() as c:
        return S.read_source(c, marcas, inicio, fim, HOJE, AGORA, AGORA)


def _shadow(eng) -> dict:
    with eng.connect() as c:
        return {(r["date"], r["brand"]): dict(r) for r in c.execute(text(
            f"SELECT * FROM {S.SHADOW}")).mappings().all()}


# ---------------------------------------------------------------------------
# SQL de agregacao
# ---------------------------------------------------------------------------

def test_sql_agrega_por_pedido_sem_multiplicar_frete_nem_contar_cancelado(banco):
    _semear(banco)
    snap = _snap(banco)
    por = {(r.date, r.brand): r for r in snap.rows}
    a1 = por[(D1, "apice")]
    assert (a1.orders, a1.canceled_orders, a1.delivered_orders) == (2, 1, 1)
    assert a1.units_sold == 5                       # 1+2+1 de A1, 1 de A2; os 4 do cancelado fora
    assert a1.seller_shipping_cost == 15.0          # 10 + 5: frete do pedido de 3 itens UMA vez
    assert a1.product_subtotal == 140.0
    a2 = por[(D2, "apice")]
    assert (a2.orders, a2.to_return_orders, a2.unpaid_orders) == (2, 1, 1)
    assert set(b for _, b in por) == {"apice", "barbours"}   # kokeshi e fora-da-janela ausentes
    assert (D3, "apice") not in por                 # dia sem pedido nao vira linha zerada


def test_frescor_real(banco):
    _semear(banco)
    with banco.connect() as c:
        assert S.validate_frescor(c, ("apice", "barbours"), AGORA) == AGORA
        with pytest.raises(S.ShopeeDailyShadowError, match="lescent"):
            S.validate_frescor(c, ("apice", "lescent"), AGORA)


# ---------------------------------------------------------------------------
# Transacao
# ---------------------------------------------------------------------------

def test_publica_e_delete_so_alcanca_a_janela_e_as_marcas(banco):
    _semear(banco)
    with banco.begin() as c:   # linha fora da janela, de publicacao anterior
        c.execute(text(f"""INSERT INTO {S.SHADOW} VALUES ('2026-09-01', 'apice', 1, 1, 0, 1, 0, 0, 1, 1,
                       'api', :r, now(), now(), false)"""), {"r": "b" * 32})
    snap = _snap(banco)
    with banco.begin() as c:
        assert S.publish_in_transaction(c, snap, "a" * 32) == {"linhas_publicadas": 3}
    linhas = _shadow(banco)
    assert (date(2026, 9, 1), "apice") in linhas                 # fora da janela: intocada
    assert linhas[(D1, "apice")]["source"] == "api"
    assert linhas[(D1, "apice")]["seller_shipping_cost"] == Decimal("15.00")


def test_noop_quando_conteudo_identico(banco, monkeypatch):
    _semear(banco)
    monkeypatch.setattr(S, "local_engine", lambda: banco)
    snap = _snap(banco)
    assert S._publicar(snap, "a" * 32) == {"linhas_publicadas": 3}
    res = S._publicar(snap, "c" * 32)
    assert res["noop"] is True
    assert {r["source_run_id"] for r in _shadow(banco).values()} == {"a" * 32}   # nada reescrito


def test_sem_migration_recusa_com_mensagem_clara(monkeypatch):
    with cluster_da_sessao() as base:
        eng = _banco(base, "shp_sem_027", com_migration=False)
    try:
        _semear(eng)
        monkeypatch.setattr(S, "local_engine", lambda: eng)
        with pytest.raises(S.ShopeeDailyShadowError, match="migration 027"):
            S._publicar(_snap(eng), "a" * 32)
    finally:
        eng.dispose()


def test_colapso_da_fonte_faz_rollback_e_preserva_o_publicado(banco):
    _semear(banco)
    with banco.begin() as c:
        S.publish_in_transaction(c, _snap(banco), "a" * 32)
    antes = _shadow(banco)
    colapsada = S.Snapshot(captured_at=AGORA, inicio=D1, fim=D3, marcas=("apice", "barbours"),
                           max_ingested_at=AGORA,
                           rows=[r for r in _snap(banco).rows if r.brand == "barbours"])
    with pytest.raises(S.ShopeeDailyShadowError, match="colapso"):
        with banco.begin() as c:
            S.publish_in_transaction(c, colapsada, "d" * 32)
    assert _shadow(banco) == antes


def test_execucao_concorrente_e_recusada(banco, monkeypatch):
    _semear(banco)
    monkeypatch.setattr(S, "local_engine", lambda: banco)
    with banco.connect() as outra:
        tx = outra.begin()
        outra.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": S.ADVISORY_LOCK_KEY})
        with pytest.raises(S.ConcurrentRunError):
            S._publicar(_snap(banco), "a" * 32)
        tx.rollback()


# ---------------------------------------------------------------------------
# Migration 027: o CHECK e' a ultima barreira
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("campo, valor, restricao", [
    ("brand", "kokeshi", "ck_fsdas_brand"),
    ("source", "manual_export", "ck_fsdas_source"),
    ("delivered_orders", 99, "ck_fsdas_subconjuntos"),
    ("seller_shipping_cost", Decimal("NaN"), "ck_fsdas_valores"),
    ("product_subtotal", Decimal("-1"), "ck_fsdas_valores"),
    ("orders", -1, "ck_fsdas_contagens"),
    ("source_run_id", "nao-hex", "ck_fsdas_run_id"),
])
def test_check_da_migration_recusa(banco, campo, valor, restricao):
    linha = {"date": D1, "brand": "apice", "orders": 10, "units_sold": 10, "canceled_orders": 0,
             "delivered_orders": 1, "to_return_orders": 0, "unpaid_orders": 0,
             "seller_shipping_cost": Decimal("1"), "product_subtotal": Decimal("1"), "source": "api",
             "source_run_id": "a" * 32, "source_captured_at": AGORA, "source_max_ingested_at": AGORA,
             "is_partial": False}
    linha[campo] = valor
    cols = ", ".join(linha)
    with pytest.raises(IntegrityError, match=restricao):
        with banco.begin() as c:
            c.execute(text(f"INSERT INTO {S.SHADOW} ({cols}) VALUES ({', '.join(':' + k for k in linha)})"), linha)


def test_downgrade_derruba_so_a_tabela(banco):
    with banco.begin() as c:
        for s in carregar_migration_027().DOWNGRADE_SQL:
            c.execute(text(s))
        assert c.execute(text("SELECT to_regclass('marts.fact_shopee_daily_api_shadow')")).scalar() is None
        assert c.execute(text("SELECT to_regclass('audit.source_sync_run')")).scalar() is not None
