"""VENDAS-POR-HORA-1 — contrato puro de `sync_vendas_por_hora` (sem banco).

Relogio de cada canal, hora fechada, marca sem loja, a regra de cada canal no
SQL e a publicacao (DELETE + INSERT da janela, fonte por canal).
"""
from __future__ import annotations

import contextlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from pipelines import sync_vendas_por_hora as V

LOJAS = {"apice": 1, "barbours": 2, "kokeshi": 3, "lescent": 4, "rituaria": 5}
ML = next(c for c in V.CANAIS if c.marketplace_id == V.ML)
TIKTOK = next(c for c in V.CANAIS if c.marketplace_id == V.TIKTOK)
SHOPEE = next(c for c in V.CANAIS if c.marketplace_id == V.SHOPEE)
HOJE = date(2026, 11, 3)


def brt(h, m=0, d=HOJE):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=V.BRT)


# ---------------------------------------------------------------------------
# Relogio
# ---------------------------------------------------------------------------

def test_ml_soma_uma_hora_ao_date_created():
    """`date_created` do ML esta' em -04:00 (medido em 03/11/2026)."""
    assert "date_created + interval '1 hour'" in V.ML_SQL
    assert V.ML_SQL.count("- interval '1 hour'") == 2  # a janela tambem desloca
    assert "+ interval '1 hour'" in V.ML_FONTE_SQL


def test_shopee_converte_create_time_para_brt():
    assert "create_time AT TIME ZONE 'America/Sao_Paulo'" in V.SHOPEE_SQL


def test_fonte_sem_fuso_e_brt_e_shopee_perde_a_folga():
    assert V.para_brt(datetime(2026, 11, 3, 13, 52), ML) == brt(13, 52)
    assert V.para_brt(datetime(2026, 11, 3, 13, 52), TIKTOK) == brt(13, 52)
    carga = datetime(2026, 11, 3, 16, 13, tzinfo=timezone.utc)  # 13:13 BRT
    assert V.para_brt(carga, SHOPEE) == brt(13, 3)
    assert V.para_brt(None, ML) is None


# ---------------------------------------------------------------------------
# Regra de cada canal (a mesma da fato diaria)
# ---------------------------------------------------------------------------

def test_regra_do_ml_e_pedido_pago():
    assert "status = 'paid'" in V.ML_SQL
    assert "COUNT(DISTINCT order_id)" in V.ML_SQL


def test_regra_do_tiktok_deduplica_e_usa_a_allowlist_do_conector():
    from pipelines.connectors.tiktok.connector import COMMERCIAL_ORDER_STATUSES
    assert "DISTINCT ON (order_id)" in V.TIKTOK_SQL
    assert "updated_at DESC NULLS LAST, id DESC" in V.TIKTOK_SQL
    assert TIKTOK.extra["status_comerciais"] == tuple(COMMERCIAL_ORDER_STATUSES)


def test_regra_da_shopee_tira_cancelado_e_soma_item_total():
    assert "lower(order_status) <> 'cancelled'" in V.SHOPEE_SQL
    assert "SUM(item_total)" in V.SHOPEE_SQL


def test_kokeshi_nunca_entra_na_shopee():
    assert "kokeshi" not in SHOPEE.brands
    assert set(SHOPEE.brands) == {"apice", "barbours", "lescent", "rituaria"}


def test_marcas_sao_as_dos_conectores():
    from pipelines.connectors.mercadolivre.connector import BRANDS_IN_SCOPE as ML_B
    from pipelines.connectors.tiktok.connector import BRANDS_IN_SCOPE as TK_B
    assert ML.brands == tuple(ML_B) and TIKTOK.brands == tuple(TK_B)


# ---------------------------------------------------------------------------
# Hora fechada e montagem
# ---------------------------------------------------------------------------

def test_janela_e_hoje_e_os_sete_dias_anteriores():
    assert V.janela(HOJE) == (date(2026, 10, 27), date(2026, 11, 4))


def test_hora_fechada_so_quando_a_fonte_passou_do_fim_dela():
    assert V.hora_fechada(HOJE, 12, brt(13, 0))
    assert not V.hora_fechada(HOJE, 13, brt(13, 59))
    assert V.hora_fechada(HOJE - timedelta(days=1), 23, brt(0, 0))


def _linha(dia, hora, brand, gmv, pedidos):
    return {"dia": dia, "hora": hora, "brand": brand, "gmv": gmv, "pedidos": pedidos}


def test_montar_descarta_a_hora_aberta_e_mapeia_a_loja():
    linhas = [_linha(HOJE, 12, "barbours", Decimal("10.005"), 2),
              _linha(HOJE, 13, "barbours", Decimal("5"), 1),          # aberta
              _linha(HOJE - timedelta(days=1), 23, "kokeshi", 7, 1)]
    out = V.montar(ML, linhas, brt(13, 20), LOJAS)
    assert out == [
        {"dia": HOJE - timedelta(days=1), "hora": 23, "marketplace_id": V.ML, "loja_id": 3,
         "gmv": Decimal("7.00"), "pedidos": 1},
        {"dia": HOJE, "hora": 12, "marketplace_id": V.ML, "loja_id": 2,
         "gmv": Decimal("10.01"), "pedidos": 2},
    ]


def test_montar_aceita_dia_como_datetime():
    """O driver devolve `date`; o proxy do Data Mart, `datetime`."""
    out = V.montar(TIKTOK, [_linha(datetime(2026, 11, 3), 1, "apice", 1, 1)], brt(5), LOJAS)
    assert out[0]["dia"] == HOJE


def test_marca_sem_loja_aborta():
    with pytest.raises(V.VendasPorHoraError, match="sem loja"):
        V.montar(TIKTOK, [], brt(5), {"apice": 1})


def test_fonte_vazia_aborta():
    with pytest.raises(V.VendasPorHoraError, match="nenhum pedido"):
        V.montar(ML, [], None, LOJAS)


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------

class ConexaoFalsa:
    def __init__(self, lojas=LOJAS):
        self.execucoes = []
        self.lojas = lojas

    def execute(self, sql, params=None):
        self.execucoes.append((str(sql), params))
        if "FROM marts.dim_loja" in str(sql):
            return list(self.lojas.items())
        return None


def test_publicacao_trava_apaga_a_janela_dos_canais_e_grava_a_fonte():
    conn = ConexaoFalsa()
    linhas = [{"dia": HOJE, "hora": 1, "marketplace_id": 2, "loja_id": 2, "gmv": Decimal("1"), "pedidos": 1}]
    fontes = {2: brt(13), 1: brt(13, 30)}
    n = V.publicar_em_transacao(conn, linhas, fontes, date(2026, 10, 27), date(2026, 11, 4))
    assert n == 1
    sqls = [s for s, _ in conn.execucoes]
    assert "pg_advisory_xact_lock" in sqls[0]
    assert sqls[1].startswith(f"DELETE FROM {V.TABELA}")
    assert conn.execucoes[1][1] == {"inicio": date(2026, 10, 27), "fim": date(2026, 11, 4), "canais": [1, 2]}
    assert sqls[2].startswith(f"INSERT INTO {V.TABELA}")
    assert f"INSERT INTO {V.FONTES}" in sqls[3] and "ON CONFLICT (marketplace_id)" in sqls[3]
    assert conn.execucoes[3][1] == [{"m": 1, "f": brt(13, 30)}, {"m": 2, "f": brt(13)}]


class _Engine:
    def __init__(self, conn):
        self.conn = conn

    @contextlib.contextmanager
    def connect(self):
        yield self.conn

    begin = connect


def _fonte_fixa(monkeypatch, por_canal):
    monkeypatch.setattr(V, "ler_canal", lambda canal, inicio, fim: por_canal[canal.marketplace_id])


def test_run_dry_run_nao_grava_e_resume_hoje(monkeypatch):
    conn = ConexaoFalsa()
    monkeypatch.setattr(V, "local_engine", lambda: _Engine(conn))
    _fonte_fixa(monkeypatch, {
        V.ML: ([_linha(HOJE, 10, "barbours", 100, 2), _linha(HOJE, 13, "barbours", 9, 1)], brt(13, 40)),
        V.TIKTOK: ([_linha(HOJE, 10, "apice", 50, 1)], brt(13, 50)),
        V.SHOPEE: ([_linha(HOJE, 9, "lescent", 20, 1)], brt(13, 5)),
    })
    res = V.run(apply=False, agora=brt(13, 41))
    assert res["modo"] == "dry-run" and "linhas_gravadas" not in res
    assert res["canais"]["mercadolivre"]["hoje_gmv"] == "100.00"
    assert res["canais"]["mercadolivre"]["hoje_pedidos"] == 2
    assert not any("DELETE" in s for s, _ in conn.execucoes)


def test_run_apply_grava(monkeypatch):
    conn = ConexaoFalsa()
    monkeypatch.setattr(V, "local_engine", lambda: _Engine(conn))
    _fonte_fixa(monkeypatch, {m: ([_linha(HOJE, 1, b, 1, 1)], brt(13))
                              for m, b in ((V.ML, "barbours"), (V.TIKTOK, "apice"), (V.SHOPEE, "lescent"))})
    res = V.run(apply=True, agora=brt(13, 41))
    assert res["linhas_gravadas"] == 3


def test_canal_sem_hora_fechada_aborta_antes_de_gravar(monkeypatch):
    conn = ConexaoFalsa()
    monkeypatch.setattr(V, "local_engine", lambda: _Engine(conn))
    _fonte_fixa(monkeypatch, {
        V.ML: ([_linha(HOJE, 1, "barbours", 1, 1)], brt(13)),
        V.TIKTOK: ([], brt(13)),
        V.SHOPEE: ([_linha(HOJE, 1, "lescent", 1, 1)], brt(13)),
    })
    with pytest.raises(V.VendasPorHoraError, match="tiktok"):
        V.run(apply=True, agora=brt(13, 41))
    assert not any("DELETE" in s for s, _ in conn.execucoes)


def test_main_devolve_1_em_erro_de_contrato(monkeypatch):
    def falha(**_):
        raise V.VendasPorHoraError("x")
    monkeypatch.setattr(V, "run", falha)
    assert V.main([]) == 1
