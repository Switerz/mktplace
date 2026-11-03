"""Regra do alerta de vencidos da Shopee dentro da API (antes CLI em `pipelines/`).

Prova: a razao e' a mesma da tela (soma de vencidos sobre soma de backlog do
lote servido); avisa ao entrar, lembra as 9h/13h/17h de Brasilia e avisa uma
vez na volta; fotografia com mais de 60 min nao e' avaliada; a leitura abre a
transacao READ ONLY + REPEATABLE READ.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import alerta_vencidos as av


def _utc(dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 10, dia, hora, minuto, tzinfo=timezone.utc)


# 29/10 17:12 UTC = 14:12 em Brasilia; resumo da hora 17:00 UTC.
EFFECTIVE = _utc(29, 17, 12)
HORA = _utc(29, 17)
HORA_ANTERIOR = _utc(29, 15)  # DAG a cada 2 h: 12:00 em Brasilia


class _Resultado:
    def __init__(self, linhas: list[dict]):
        self._linhas = linhas

    def mappings(self):
        return self

    def all(self):
        return self._linhas

    def first(self):
        return self._linhas[0] if self._linhas else None


class _Sessao:
    """`Session` falsa: responde pelas tres consultas de `ler_canal`."""

    def __init__(self, banco: dict):
        self.banco = banco
        self.sql: list[str] = []
        self.rollbacks = 0

    def execute(self, clausula, params=None):
        sql = str(clausula)
        self.sql.append(sql)
        params = params or {}
        if sql.startswith("SET TRANSACTION"):
            return _Resultado([])
        dados = self.banco.get(params.get("canal"))
        if "SELECT DISTINCT refresh_batch_id" in sql:
            return _Resultado(dados["lotes"] if dados else [])
        if "SELECT brand" in sql:
            return _Resultado(dados["resumos"])
        assert params["hora"] == HORA  # anterior e' estritamente antes do servido
        ant = dados.get("anterior")
        return _Resultado(
            []
            if ant is None
            else [{"snapshot_hour": HORA_ANTERIOR, "contas": 2,
                   "backlog": ant[0], "vencidos": ant[1]}]
        )

    def rollback(self):
        self.rollbacks += 1


def _canal(resumos: list[tuple[str, int, int]], anterior: tuple[int, int] | None) -> dict:
    return {
        "lotes": [{"refresh_batch_id": "lote-1", "effective_at": EFFECTIVE}],
        "resumos": [
            {"brand": m, "backlog_count": b, "overdue_count": v, "snapshot_hour": HORA}
            for m, b, v in resumos
        ],
        "anterior": anterior,
    }


@pytest.fixture
def db_em_alerta() -> _Sessao:
    return _Sessao({"shopee": _canal([("apice", 1000, 40)], anterior=(1000, 20))})


def _leitura(backlog: int, vencidos: int, anterior: tuple[int, int] | None,
             hora: datetime = HORA, hora_anterior: datetime = HORA_ANTERIOR,
             effective_at: datetime = EFFECTIVE) -> av.Leitura:
    return av.Leitura(
        canal="shopee",
        effective_at=effective_at,
        hora=hora,
        total=av.Medida(backlog, vencidos),
        por_marca={"apice": av.Medida(backlog, vencidos)},
        anterior=av.Medida(*anterior) if anterior else None,
        hora_anterior=hora_anterior if anterior else None,
    )


# --- limite ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("backlog", "vencidos", "acima"),
    [(100, 3, False), (100, 4, True), (1000, 31, True), (0, 0, False)],
)
def test_limite_e_estritamente_acima_de_3_por_cento(backlog, vencidos, acima):
    assert av.Medida(backlog, vencidos).acima is acima


# --- quando avisa ------------------------------------------------------------
def test_avisa_quando_entra_acima_de_3_por_cento():
    assert av.decidir(_leitura(1000, 40, anterior=(1000, 20))) == av.ENTROU


def test_sem_historico_e_acima_conta_como_entrada():
    assert av.decidir(_leitura(1000, 40, anterior=None)) == av.ENTROU


def test_continua_acima_sem_passar_por_hora_de_lembrete_fica_quieto():
    # (12:00, 14:00] em Brasilia: 13h passou -> lembrete; (14:00, 16:00]: nao.
    assert av.decidir(_leitura(1000, 50, anterior=(1000, 40),
                               hora=_utc(29, 19), hora_anterior=_utc(29, 17))) is None


def test_continua_acima_e_passou_das_13h_lembra():
    assert av.decidir(_leitura(1000, 50, anterior=(1000, 40))) == av.LEMBRETE


@pytest.mark.parametrize(
    ("anterior_brt", "atual_brt", "lembra"),
    [
        (8, 9, True),     # DAG de hora em hora: a fotografia das 9h lembra
        (9, 10, False),   # ...e a seguinte nao repete
        (8, 10, True),    # DAG a cada 2 h nas horas pares: 9h cai no vao e conta
        (16, 18, True),   # 17h
        (18, 20, False),
        (20, 33, True),   # vao de 13 h atravessando a noite: 9h do dia seguinte
    ],
)
def test_hora_de_lembrete_e_a_que_o_relogio_atravessa(anterior_brt, atual_brt, lembra):
    base = _utc(29, 3)  # 00:00 em Brasilia
    desde = base + timedelta(hours=anterior_brt)
    ate = base + timedelta(hours=atual_brt)
    assert av.passou_hora_de_lembrete(desde, ate) is lembra


def test_avisa_uma_vez_quando_volta_ao_normal():
    assert av.decidir(_leitura(1000, 20, anterior=(1000, 40))) == av.SAIU


def test_abaixo_e_continua_abaixo_fica_quieto_mesmo_na_hora_de_lembrete():
    assert av.decidir(_leitura(1000, 10, anterior=(1000, 20))) is None


def test_exatamente_3_por_cento_nao_avisa():
    assert av.decidir(_leitura(1000, 30, anterior=(1000, 30))) is None


def test_fila_vazia_com_anterior_acima_sai_com_zero():
    leitura = _leitura(0, 0, anterior=(100, 5))
    assert av.decidir(leitura) == av.SAIU
    assert "voltaram a 0,0%" in av.montar_mensagem(leitura, av.SAIU)


# --- leitura --------------------------------------------------------------
def test_leitura_soma_contas_e_agrupa_por_marca():
    db = _Sessao({
        "shopee": _canal(
            [("apice", 600, 30), ("apice", 400, 10), ("lescent", 1000, 5)],
            anterior=(2000, 30),
        ),
    })
    leitura = av.ler_canal(db, "shopee")
    assert leitura.total == av.Medida(2000, 45)
    assert leitura.por_marca["apice"] == av.Medida(1000, 40)
    assert leitura.por_marca["lescent"] == av.Medida(1000, 5)
    assert leitura.anterior == av.Medida(2000, 30)
    assert (leitura.hora, leitura.hora_anterior) == (HORA, HORA_ANTERIOR)


def test_leitura_sem_historico():
    leitura = av.ler_canal(_Sessao({"shopee": _canal([("apice", 100, 1)], None)}), "shopee")
    assert leitura.anterior is None and leitura.hora_anterior is None


def test_sem_fotografia_nao_avalia():
    assert av.ler_canal(_Sessao({}), "shopee") is None


def test_lote_incoerente_nao_avalia():
    banco = {"shopee": _canal([("apice", 100, 10)], anterior=None)}
    banco["shopee"]["lotes"].append({"refresh_batch_id": "lote-2", "effective_at": EFFECTIVE})
    assert av.ler_canal(_Sessao(banco), "shopee") is None


def test_ler_canal_abre_transacao_read_only_repeatable_read(db_em_alerta):
    av.ler_canal(db_em_alerta, "shopee")
    primeiro = db_em_alerta.sql[0]
    assert "REPEATABLE READ" in primeiro and "READ ONLY" in primeiro


def test_so_a_shopee_e_avaliada():
    assert av.CANAL == "shopee"


# --- mensagem ---------------------------------------------------------------
def _leitura_com_marcas() -> av.Leitura:
    return av.Leitura(
        canal="shopee",
        effective_at=EFFECTIVE,
        hora=HORA,
        total=av.Medida(1264, 58),
        por_marca={
            "apice": av.Medida(727, 44),
            "barbours": av.Medida(316, 11),
            "lescent": av.Medida(111, 3),
        },
        anterior=None,
        hora_anterior=None,
    )


def test_mensagem_de_entrada_traz_numeros_marcas_e_link():
    texto = av.montar_mensagem(_leitura_com_marcas(), av.ENTROU)
    assert ":rotating_light: *Expedição Shopee: 4,6% da fila vencida* (limite 3%)" in texto
    assert "58 de 1.264 pedidos da fila passaram do prazo de despacho" in texto
    assert "29/10 às 14:12" in texto
    assert "Marcas acima de 3%: ÁPICE 6,1% (44 de 727) · BARBOURS 3,5% (11 de 316)" in texto
    assert "LESCENT" not in texto
    assert "<https://mktplace-gobeaute.vercel.app/expedicao|" in texto


def test_mensagem_de_lembrete_diz_que_continua():
    texto = av.montar_mensagem(_leitura_com_marcas(), av.LEMBRETE)
    assert ":warning: *Expedição Shopee: continua com 4,6% da fila vencida*" in texto
    assert "Marcas acima de 3%" in texto


def test_mensagem_de_normalizacao():
    texto = av.montar_mensagem(_leitura(1000, 21, anterior=(1000, 40)), av.SAIU)
    assert "vencidos voltaram a 2,1% da fila" in texto
    assert "Marcas acima" not in texto


# --- chave e avaliacao ------------------------------------------------------
def test_chave_da_fotografia_e_canal_mais_instante_utc():
    brt = timezone(timedelta(hours=-3))
    leitura = _leitura(1000, 40, None, effective_at=datetime(2026, 11, 3, 10, 2, tzinfo=brt))
    assert av.chave_da_fotografia(leitura) == "shopee:2026-11-03T13:02:00+00:00"


def test_avaliar_fotografia_de_61_min_nao_avalia(db_em_alerta):
    a = av.avaliar(db_em_alerta, agora=EFFECTIVE + timedelta(minutes=61))
    assert a.situacao == "fotografia_velha" and a.evento is None and a.mensagem is None


def test_avaliar_fotografia_de_60_min_ainda_avalia(db_em_alerta):
    assert av.avaliar(db_em_alerta, agora=EFFECTIVE + timedelta(minutes=60)).situacao == "evento"


def test_avaliar_em_alerta_devolve_evento_mensagem_e_chave(db_em_alerta):
    a = av.avaliar(db_em_alerta, agora=EFFECTIVE + timedelta(minutes=10))
    assert (a.situacao, a.evento) == ("evento", av.ENTROU)
    assert a.chave == "shopee:2026-10-29T17:12:00+00:00"
    assert "Abrir a Expedição" in a.mensagem
    assert a.fracao == pytest.approx(0.04)


def test_avaliar_sem_evento(db_em_alerta):
    db = _Sessao({"shopee": _canal([("apice", 1000, 10)], anterior=(1000, 20))})
    a = av.avaliar(db, agora=EFFECTIVE)
    assert (a.situacao, a.evento, a.mensagem, a.chave) == ("sem_evento", None, None, None)


def test_avaliar_sem_fotografia():
    assert av.avaliar(_Sessao({}), agora=EFFECTIVE).situacao == "sem_fotografia"


def test_avaliar_encerra_a_transacao_de_leitura(db_em_alerta):
    av.avaliar(db_em_alerta, agora=EFFECTIVE)
    assert db_em_alerta.rollbacks >= 1
