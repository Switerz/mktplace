"""CX-PEDIDOS-MOTIVO (05/11/2026) — pedidos de um motivo do Pareto."""
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import cx_atendimento_service as cx
from app.services import cx_pedidos_service as svc

AGORA = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)


class Fonte:
    """Devolve linhas por SQL e guarda os parametros de cada chamada."""

    def __init__(self, rotas, falhas=()):
        self.rotas = rotas
        self.falhas = set(falhas)
        self.chamadas = []

    def __call__(self, sql, params):
        self.chamadas.append((sql, params))
        if sql in self.falhas:
            raise RuntimeError("caiu")
        return self.rotas.get(sql, [])


def _dm(**extra):
    rotas = {
        cx.SQL_SHOPEE_MOTIVOS: [
            {"motivo": "Out of Stock", "autor": "seller", "quantidade": 3},
            {"motivo": "unpaid order", "autor": "system", "quantidade": 50},
            {"motivo": "Change of mind", "autor": "buyer", "quantidade": 9},
        ],
        svc.SQL_CANC_PEDIDOS: [
            {"marca": "apice", "order_sn": "A1", "dia": date(2026, 11, 3), "autor": "seller"},
            {"marca": "barbours", "order_sn": "B1", "dia": date(2026, 11, 4), "autor": "seller"},
        ],
        cx.SQL_TABELAS_CX: [{"returns": True, "chat": True}],
        cx.SQL_DEVOLUCOES_MOTIVOS: [
            {"motivo": "SPILLED_CONTENTS", "quantidade": 2, "canceladas": 1},
            {"motivo": "CHANGE_MIND", "quantidade": 5, "canceladas": 0},
        ],
        svc.SQL_DEV_PEDIDOS: [
            {"marca": "lescent", "order_sn": "L9", "return_sn": "R9", "dia": date(2026, 11, 2),
             "status": "ACCEPTED", "refund_amount": 59.9},
            {"marca": "lescent", "order_sn": "L8", "return_sn": "R8", "dia": date(2026, 11, 1),
             "status": "REQUESTED", "refund_amount": 40.0},
        ],
    }
    rotas.update(extra)
    return Fonte(rotas)


def _torre(**extra):
    rotas = {
        cx.SQL_SHOPEE_EXPORT_MOTIVOS: [{"motivo": "Fora de estoque", "quantidade": 1}],
        svc.SQL_CANC_PEDIDOS_EXPORT: [],
        cx.SQL_TABELAS_CX_EXPORT: [{"returns": False, "chat": False}],
    }
    rotas.update(extra)
    return Fonte(rotas)


def _codigo(texto, autor="seller"):
    return cx.rotular_shopee(texto, autor)[0]


def test_cancelamento_filtra_pelos_textos_do_codigo_e_total_e_o_do_pareto():
    dm = _dm()
    cod = _codigo("Out of Stock")
    r = svc.montar_pedidos_motivo(tipo="cancelamento", codigo=cod, consultar=dm, consultar_torre=_torre(), agora=AGORA)
    assert r.estado == "dado" and r.total == 3 and not r.truncado and not r.parcial
    assert [p.pedido for p in r.pedidos] == ["B1", "A1"], "do mais recente para o mais antigo"
    assert r.pedidos[0].situacao == "Cancelado pela loja"
    lista = [p for s, p in dm.chamadas if s == svc.SQL_CANC_PEDIDOS][0]
    assert lista["textos"] == ["Out of Stock"] and lista["teto"] == svc.TETO + 1


def test_nao_pago_nunca_entra_na_lista():
    cod = _codigo("unpaid order", "system")
    r = svc.montar_pedidos_motivo(tipo="cancelamento", codigo=cod, consultar=_dm(), consultar_torre=_torre(), agora=AGORA)
    assert r.estado == "vazio_legitimo" and r.total == 0 and r.pedidos == []


def test_devolucao_sem_as_canceladas_e_valor_so_da_reembolsada():
    r = svc.montar_pedidos_motivo(tipo="devolucao", codigo="spilled_contents", consultar=_dm(),
                                  consultar_torre=_torre(), agora=AGORA)
    assert r.total == 2 and r.rotulo == "Produto vazou"
    assert [(p.pedido, p.devolucao, p.situacao, p.valor) for p in r.pedidos] == [
        ("L9", "R9", "Reembolsada", 59.9), ("L8", "R8", "Solicitada", None)]
    assert "status IS DISTINCT FROM 'CANCELLED'" in svc.SQL_DEV_PEDIDOS


def test_kokeshi_pelo_neon_e_falha_numa_fonte_vira_parcial():
    cod = cx.rotular_shopee_pt("Fora de estoque")[0]
    torre = _torre(**{svc.SQL_CANC_PEDIDOS_EXPORT: [
        {"marca": "kokeshi", "order_sn": "K1", "dia": date(2026, 11, 4), "autor": None}]})
    r = svc.montar_pedidos_motivo(tipo="cancelamento", codigo=cod, brands=["kokeshi"],
                                  consultar=_dm(), consultar_torre=torre, agora=AGORA)
    assert [(p.marca, p.pedido, p.situacao) for p in r.pedidos] == [("kokeshi", "K1", "Cancelado")]
    dm = _dm()
    dm.falhas.add(cx.SQL_SHOPEE_MOTIVOS)
    r2 = svc.montar_pedidos_motivo(tipo="cancelamento", codigo=cod, consultar=dm, consultar_torre=torre, agora=AGORA)
    assert r2.parcial and [p.pedido for p in r2.pedidos] == ["K1"]


def test_tudo_caido_e_erro_com_mensagem_fixa():
    dm = _dm()
    dm.falhas.update({cx.SQL_SHOPEE_MOTIVOS})
    torre = _torre()
    torre.falhas.add(cx.SQL_SHOPEE_EXPORT_MOTIVOS)
    r = svc.montar_pedidos_motivo(tipo="cancelamento", codigo="x", consultar=dm, consultar_torre=torre, agora=AGORA)
    assert r.estado == "erro" and r.motivo_indisponivel == cx.MOTIVO_ERRO and r.total is None


def test_teto_corta_a_lista_e_marca_truncado():
    muitas = [{"marca": "apice", "order_sn": f"A{i}", "dia": date(2026, 11, 1), "autor": "seller"}
              for i in range(svc.TETO + 1)]
    dm = _dm(**{svc.SQL_CANC_PEDIDOS: muitas,
                cx.SQL_SHOPEE_MOTIVOS: [{"motivo": "Out of Stock", "autor": "seller", "quantidade": 900}]})
    r = svc.montar_pedidos_motivo(tipo="cancelamento", codigo=_codigo("Out of Stock"), consultar=dm,
                                  consultar_torre=_torre(), agora=AGORA)
    assert len(r.pedidos) == svc.TETO and r.truncado and r.total == 900


def test_numero_retido_quando_a_flag_desliga(monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_numero_pedido_publico", False)
    r = svc.montar_pedidos_motivo(tipo="devolucao", codigo="spilled_contents", consultar=_dm(),
                                  consultar_torre=_torre(), agora=AGORA)
    assert not r.numero_publico
    assert all(p.pedido is None and p.devolucao is None for p in r.pedidos)
    assert r.pedidos[0].valor == 59.9


@pytest.mark.parametrize("motivo", ["DROP TABLE", "a" * 81, "x;y", ""])
def test_rota_recusa_motivo_fora_do_formato_sem_ecoar(motivo):
    c = TestClient(app)
    res = c.get("/api/v1/cx/shopee/pedidos", params={"tipo": "cancelamento", "motivo": motivo})
    assert res.status_code == 422
    if motivo:
        assert motivo not in res.text


def test_rota_recusa_tipo_desconhecido():
    res = TestClient(app).get("/api/v1/cx/shopee/pedidos", params={"tipo": "chat", "motivo": "x"})
    assert res.status_code == 422
