"""Cobertura de coleta da GUARDA da Expedicao TikTok (EXPEDICAO-TIKTOK-COBERTURA-1).

Incidente medido em 21/10/2026: o 1o run produtivo de
`marketplace_tower_expedicao_refresh` bloqueou o TikTok com 92,89 de cobertura
de IN_TRANSIT. Nao era a fonte: a proveniencia punha no denominador os pedidos
pagos hoje e ontem, ainda dentro do prazo de coleta. Nas coortes vencidas a
cobertura era 99,98. A guarda passa a medir so' as coortes vencidas, no escopo
publicado, com `so_posterior` e pendentes vencidos contando como NAO coberto.

Duas camadas:
  * pura — a decomposicao fecha, `None` sem elegiveis, inconsistencia levanta;
  * PostgreSQL real (pulada sem servidor) — o SQL classifica cada caso como a
    metrica publicada, e o escopo/cancelado/amostra ficam fora.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipelines.expedicao import tiktok_daily as td
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES, cluster_da_sessao, postgres_disponivel,
)

HOJE = date(2026, 10, 21)


def _cob(**kw):
    base = dict(hoje=HOJE, desde=HOJE - timedelta(days=16), ate=HOJE, pedidos=0,
                com_in_transit=0, so_posterior=0, pendentes_vencidos=0, imaturos=0)
    base.update(kw)
    return td.CoberturaColeta(**base)


# ===========================================================================
# Pura
# ===========================================================================

def test_elegiveis_e_cobertura_ignoram_imaturos():
    c = _cob(pedidos=1000, com_in_transit=900, so_posterior=5, pendentes_vencidos=5, imaturos=90)
    assert c.elegiveis == 910
    assert c.cobertura == pytest.approx(900 / 910)


def test_so_posterior_conta_como_nao_coberto():
    c = _cob(pedidos=100, com_in_transit=90, so_posterior=10)
    assert c.cobertura == pytest.approx(0.90)


def test_sem_coorte_vencida_devolve_none():
    assert _cob(pedidos=50, imaturos=50).cobertura is None
    assert _cob().cobertura is None


@pytest.mark.parametrize("kw", [
    dict(pedidos=10, com_in_transit=9),                      # falta 1
    dict(pedidos=10, com_in_transit=11, imaturos=-1),        # negativo
])
def test_decomposicao_que_nao_fecha_levanta(kw):
    with pytest.raises(td.CoberturaInconsistente):
        _cob(**kw)


def test_imaturo_nao_reduz_e_vencido_pendente_reduz():
    base = _cob(pedidos=100, com_in_transit=100)
    com_imaturos = _cob(pedidos=100 + 5000, com_in_transit=100, imaturos=5000)
    com_pendente = _cob(pedidos=101, com_in_transit=100, pendentes_vencidos=1)
    assert com_imaturos.cobertura == base.cobertura == 1.0
    assert com_pendente.cobertura < base.cobertura


def test_como_dict_tem_a_decomposicao_inteira():
    d = _cob(pedidos=3, com_in_transit=1, so_posterior=1, imaturos=1).como_dict()
    assert {"com_in_transit", "so_posterior", "pendentes_vencidos", "imaturos",
            "elegiveis", "cobertura", "pedidos"} <= set(d)


# ===========================================================================
# PostgreSQL real
# ===========================================================================

pg = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

DDL = """
DROP SCHEMA IF EXISTS raw CASCADE; CREATE SCHEMA raw;
CREATE TABLE raw.tiktok_shop_orders (
    order_id text PRIMARY KEY, brand text, shop_name text, paid_at timestamp,
    is_sample_order boolean, order_status text, extracted_at timestamp);
CREATE TABLE raw.tiktok_shop_line_items (order_id text, rts_time timestamp);
CREATE TABLE raw.tiktok_shop_order_status_log (
    order_id text, new_status text, updated_at_tiktok timestamp, detected_at timestamp);
"""

VENCIDO = date(2026, 10, 8)        # prazo de coleta bem antes de HOJE
RECENTE = HOJE                     # pago hoje: prazo de coleta ainda nao venceu
assert td.prazo_do_evento(VENCIDO, td.Evento.COLETA) < HOJE
assert td.prazo_do_evento(RECENTE, td.Evento.COLETA) >= HOJE

#: (order_id, brand, pago, status atual, amostra, eventos [(status, updated_at_tiktok?)])
CASOS = [
    ("V_IT",       "apice",  VENCIDO, "DELIVERED",           False, [("IN_TRANSIT", True), ("DELIVERED", True)]),
    ("V_IT2",      "apice",  VENCIDO, "IN_TRANSIT",          False, [("IN_TRANSIT", True)]),
    ("V_POST",     "lescent", VENCIDO, "DELIVERED",          False, [("DELIVERED", True)]),          # perdeu o IN_TRANSIT
    ("V_IT_SEMTS", "lescent", VENCIDO, "DELIVERED",          False, [("IN_TRANSIT", False), ("DELIVERED", True)]),
    ("V_AWAIT",    "barbours", VENCIDO, "AWAITING_COLLECTION", False, [("AWAITING_COLLECTION", True)]),
    ("V_NADA",     "barbours", VENCIDO, "AWAITING_SHIPMENT", False, []),
    ("R_NADA",     "apice",  RECENTE, "AWAITING_COLLECTION", False, []),
    ("R_IT",       "apice",  RECENTE, "IN_TRANSIT",          False, [("IN_TRANSIT", True)]),
    # Fora do denominador inteiro:
    ("X_CANC",     "apice",  VENCIDO, "CANCELLED",           False, []),
    ("X_AMOSTRA",  "apice",  VENCIDO, "AWAITING_SHIPMENT",   True,  []),
    ("X_GOCASE",   "gocase", VENCIDO, "AWAITING_SHIPMENT",   False, []),
]


def _preparar(url, casos=CASOS):
    import psycopg2
    with psycopg2.connect(url) as c, c.cursor() as cur:
        cur.execute(DDL)
        for oid, brand, pago, st, amostra, evs in casos:
            cur.execute("INSERT INTO raw.tiktok_shop_orders VALUES (%s,%s,'loja',%s,%s,%s,%s)",
                        (oid, brand, f"{pago} 10:00", amostra, st, f"{HOJE} 12:00"))
            cur.execute("INSERT INTO raw.tiktok_shop_line_items VALUES (%s, NULL)", (oid,))
            for i, (ev, com_ts) in enumerate(evs):
                instante = f"{pago + timedelta(days=1 + i)} 09:00"
                cur.execute("INSERT INTO raw.tiktok_shop_order_status_log VALUES (%s,%s,%s,%s)",
                            (oid, ev, instante if com_ts else None, instante))


def _medir(url):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    with psycopg2.connect(url, cursor_factory=RealDictCursor) as c:
        c.set_session(readonly=True)
        return td.ler_cobertura_coleta(c, HOJE)


@pg
def test_decomposicao_exaustiva_contra_o_sql_real():
    with cluster_da_sessao() as url:
        _preparar(url)
        c = _medir(url)
    assert (c.com_in_transit, c.so_posterior, c.pendentes_vencidos, c.imaturos) == (2, 2, 2, 2)
    assert c.pedidos == 8, "cancelado, amostra e marca fora do escopo nao entram"
    assert c.com_in_transit + c.so_posterior + c.pendentes_vencidos + c.imaturos == c.pedidos
    assert c.elegiveis == 6
    assert c.cobertura == pytest.approx(2 / 6)


@pg
def test_pedido_recente_no_prazo_nao_reduz_cobertura():
    vencidos_cobertos = [(f"V{i}", "apice", VENCIDO, "DELIVERED", False, [("IN_TRANSIT", True)])
                         for i in range(20)]
    recentes = [(f"R{i}", "apice", RECENTE, "AWAITING_COLLECTION", False, []) for i in range(300)]
    with cluster_da_sessao() as url:
        _preparar(url, vencidos_cobertos)
        so_vencidos = _medir(url)
        _preparar(url, vencidos_cobertos + recentes)
        com_recentes = _medir(url)
    assert so_vencidos.cobertura == com_recentes.cobertura == 1.0
    assert com_recentes.imaturos == 300


@pg
def test_pedido_vencido_em_awaiting_reduz_cobertura():
    vencidos_cobertos = [(f"V{i}", "apice", VENCIDO, "DELIVERED", False, [("IN_TRANSIT", True)])
                         for i in range(19)]
    travado = [("T", "apice", VENCIDO, "AWAITING_COLLECTION", False, [("AWAITING_COLLECTION", True)])]
    with cluster_da_sessao() as url:
        _preparar(url, vencidos_cobertos + travado)
        c = _medir(url)
    assert c.pendentes_vencidos == 1
    assert c.cobertura == pytest.approx(19 / 20)


@pg
def test_pendentes_casam_com_a_metrica_publicada():
    """`so_posterior + pendentes_vencidos` == soma de `coleta_pendente_vencida` da `extrair`.

    Sem cancelados no cenario: a metrica publicada mantem cancelado sem carimbo
    no denominador, e a guarda exclui todo cancelado (como a proveniencia).
    """
    casos = [c for c in CASOS if c[3] != "CANCELLED"]
    with cluster_da_sessao() as url:
        _preparar(url, casos)
        cob = _medir(url)
        import psycopg2
        from psycopg2.extras import RealDictCursor
        with psycopg2.connect(url, cursor_factory=RealDictCursor) as c:
            c.set_session(readonly=True)
            coortes = td.extrair(c, HOJE)
    vencidas = [k for k in coortes if k.coleta_deadline < HOJE]
    assert sum(k.coleta_pendente_vencida for k in vencidas) == cob.so_posterior + cob.pendentes_vencidos
    assert sum(k.coleta_no_prazo + k.coleta_atrasada for k in vencidas) == cob.com_in_transit


@pg
def test_dia_sem_prazo_levanta():
    with cluster_da_sessao() as url:
        _preparar(url)
        import psycopg2
        from psycopg2.extras import RealDictCursor
        with psycopg2.connect(url, cursor_factory=RealDictCursor) as c:
            with pytest.raises(td.CoberturaInconsistente, match="sem prazo"):
                td.ler_cobertura_coleta(c, HOJE, prazos=[[HOJE.isoformat(), HOJE.isoformat(), HOJE.isoformat()]])
