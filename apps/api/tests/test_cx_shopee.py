"""Aba CX (menu principal) — `GET /api/v1/cx/shopee` (04/11/2026).

Junta o recorte de atendimento da Saude da Conta (fotografia oficial) com
cancelamentos e devolucoes dos pedidos Shopee. Uma fonte caida nao derruba a
outra.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.routers import cx_atendimento as rota
from app.services import cx_atendimento_service as svc
from app.services import performance_oficial_service as po
from tests import test_cx_atendimento as base_cx
from tests import test_performance_oficial as base_po

AGORA = base_cx.AGORA


class FonteCx:
    """Roteia o SQL dos pedidos (CX) e o da Saude da Conta (Performance)."""

    def __init__(self, falhas_cx=(), falhas_po=(), metricas=None, tabelas=(True, True),
                 falhas_coleta=(), devolucoes=None, motivos=None, nao_lidas=None):
        self.cx = base_cx.Fonte(falhas=falhas_cx)
        self.po = base_po.Fonte(falhas=falhas_po, metricas=metricas)
        self.falhas_coleta = set(falhas_coleta)
        self.coleta = {
            svc.SQL_TABELAS_CX: ("tabelas_cx", [{"returns": tabelas[0], "chat": tabelas[1]}]),
            svc.SQL_DEVOLUCOES_MARCAS: ("devolucoes_marcas", devolucoes if devolucoes is not None else [
                {"marca": "apice", "devolucoes": 5, "aceitas": 3, "em_andamento": 1, "canceladas": 1,
                 "reembolsado": 120.5},
                {"marca": "barbours", "devolucoes": 1, "aceitas": 1, "em_andamento": 0, "canceladas": 0,
                 "reembolsado": None},
            ]),
            svc.SQL_DEVOLUCOES_MOTIVOS: ("devolucoes_motivos", motivos if motivos is not None else [
                {"motivo": "CHANGE_MIND", "quantidade": 2, "canceladas": 1},
                {"motivo": "SPILLED_CONTENTS", "quantidade": 1, "canceladas": 0},
                {"motivo": "NOVO_MOTIVO_X", "quantidade": 1, "canceladas": 0},
            ]),
            svc.SQL_NAO_LIDAS: ("nao_lidas", nao_lidas if nao_lidas is not None else [
                {"marca": "apice", "unread_count": 12, "captured_at": AGORA},
                {"marca": "barbours", "unread_count": None, "captured_at": AGORA},
            ]),
        }
        self.chamadas = []

    def __call__(self, sql, params):
        if sql in (po.SQL_EXISTE, po.SQL_ML, po.SQL_SHOPEE, po.SQL_SHOPEE_METRICAS):
            return self.po(sql, params)
        if sql in self.coleta:
            nome, dados = self.coleta[sql]
            self.chamadas.append((nome, params))
            if nome in self.falhas_coleta:
                raise RuntimeError(f"falhou em {base_cx.SEGREDO}")
            return dados
        return self.cx(sql, params)


class TorreCx:
    """Neon da Torre (Kokeshi pelo bot). Padrao: 036 ainda nao aplicada."""

    def __init__(self, tabelas=(False, False), falhas=(), saude=None, pedidos=None, motivos_canc=None,
                 devolucoes=None, motivos=None, carga=None, nao_lidas=None):
        self.falhas = set(falhas)
        self.po = base_po.FonteTorre(existe=saude is not None, shopee=saude or [],
                                     metricas=[dict(m, brand="kokeshi") for m in base_po._shopee_metricas()[:2]]
                                     if saude else [])
        self.rotas = {
            svc.SQL_TABELAS_CX_EXPORT: ("tabelas_cx_export", [{"returns": tabelas[0], "chat": tabelas[1]}]),
            svc.SQL_SHOPEE_EXPORT_MARCAS: ("export_marcas", pedidos or []),
            svc.SQL_SHOPEE_EXPORT_MOTIVOS: ("export_motivos", motivos_canc or []),
            svc.SQL_DEVOLUCOES_COM_CARGA_EXPORT: ("carga", carga or []),
            svc.SQL_DEVOLUCOES_MARCAS_EXPORT: ("devolucoes_marcas_export", devolucoes or []),
            svc.SQL_DEVOLUCOES_MOTIVOS_EXPORT: ("devolucoes_motivos_export", motivos or []),
            svc.SQL_NAO_LIDAS_EXPORT: ("nao_lidas_export", nao_lidas or []),
        }
        self.chamadas = []

    def __call__(self, sql, params):
        if sql in (po.SQL_EXISTE, po.SQL_SHOPEE_EXPORT, po.SQL_SHOPEE_METRICAS_EXPORT):
            return self.po(sql, params)
        nome, dados = self.rotas[sql]
        self.chamadas.append((nome, params))
        if nome in self.falhas:
            raise RuntimeError(f"falhou em {base_cx.SEGREDO}")
        return dados


def _montar(**kw):
    fonte = kw.pop("fonte", None) or FonteCx()
    torre = kw.pop("torre", None) or TorreCx()
    return svc.montar_cx_shopee(consultar=fonte, consultar_torre=torre, agora=AGORA, **kw), fonte


def test_chat_traz_so_metricas_de_atendimento_com_a_meta_da_shopee():
    resp, _ = _montar()
    assert resp.chat.estado == "dado"
    conta = resp.chat.contas[0]
    assert conta.marca == "apice"
    assert [m.codigo for m in conta.metricas] == ["shopee_11"]      # envio fica de fora
    m = conta.metricas[0]
    assert m.rotulo == "Taxa de resposta do chat" and m.valor == 71 and m.meta == 80
    assert m.situacao == "fora"
    assert conta.destaques == [] and conta.tom == "atencao"
    assert conta.resumo == "1 métrica(s) de atendimento fora da meta"


def test_atendimento_e_o_canal_shopee_dos_pedidos():
    resp, _ = _montar(dias=7)
    assert resp.atendimento.canal == "shopee"
    assert resp.janela.dias == 7
    codigos = [i.codigo for i in resp.atendimento.indicadores]
    assert codigos == ["shopee_cancelamentos", "shopee_taxa_cancelamento", "shopee_em_devolucao"]


def test_saude_da_conta_caida_nao_derruba_cancelamentos():
    fonte = FonteCx(falhas_po={"shopee"})
    resp, _ = _montar(fonte=fonte)
    assert resp.chat.estado == "erro"
    assert resp.atendimento.indicadores[0].estado == "dado"
    assert base_cx.SEGREDO not in resp.model_dump_json()


def test_pedidos_caidos_nao_derrubam_o_chat():
    fonte = FonteCx(falhas_cx={"shopee_marcas", "shopee_motivos", "shopee_devolucao"})
    resp, _ = _montar(fonte=fonte)
    assert resp.chat.estado == "dado" and resp.chat.contas
    assert all(i.estado == "erro" for i in resp.atendimento.indicadores)
    assert base_cx.SEGREDO not in resp.model_dump_json()


@pytest.fixture
def cliente(monkeypatch):
    app = FastAPI()
    app.include_router(rota.router)
    monkeypatch.setattr(settings, "cx_api_enabled", True)
    chamadas = []
    original = svc.montar_cx_shopee

    def falso(*, brands=None, dias=30):
        chamadas.append((brands, dias))
        return original(brands=brands, dias=dias, consultar=FonteCx(), agora=AGORA)

    monkeypatch.setattr(svc, "montar_cx_shopee", falso)
    return TestClient(app), chamadas


def test_rota_shopee_responde_e_repassa_filtros(cliente):
    c, chamadas = cliente
    r = c.get("/api/v1/cx/shopee", params={"brands": "apice", "dias": "90"})
    assert r.status_code == 200
    assert chamadas == [(["apice"], 90)]
    corpo = r.json()
    assert set(corpo) == {"gerado_em", "janela", "chat", "atendimento", "devolucoes"}


def test_flag_desligada_e_404(cliente, monkeypatch):
    c, chamadas = cliente
    monkeypatch.setattr(settings, "cx_api_enabled", False)
    assert c.get("/api/v1/cx/shopee").status_code == 404
    assert chamadas == []


def test_flag_nasce_ligada():
    from app.config import Settings
    assert Settings.model_fields["cx_api_enabled"].default is True


# ---------------------------------------------------------------------------
# Coleta de CX (devolucoes + conversas nao lidas), 04/11/2026
# ---------------------------------------------------------------------------
def test_devolucoes_com_motivo_valor_e_zero_medido_por_marca():
    resp, fonte = _montar(dias=7)
    d = resp.devolucoes
    assert (d.estado, d.total, d.aceitas, d.em_andamento, d.canceladas) == ("dado", 6, 4, 1, 1)
    assert d.reembolsado == 120.5, "so' as aceitas somam reembolso"
    por_marca = {m.marca: m for m in d.marcas}
    assert por_marca["lescent"].devolucoes == 0, "tabela existe e sem linha = zero medido"
    assert por_marca["lescent"].reembolsado == 0.0
    assert por_marca["barbours"].reembolsado is None, "aceita com valor ausente nao vira zero"
    rotulos = {i.rotulo: i for i in d.motivos.itens}
    assert rotulos["Mudou de ideia"].quantidade == 2
    assert rotulos["Produto vazou"].mapeado is True
    assert rotulos["NOVO_MOTIVO_X"].mapeado is False
    assert d.motivos.excluidos == 1, "cancelados pelo comprador ficam fora dos motivos"
    params = dict(fonte.chamadas)["devolucoes_marcas"]
    assert params["marcas"] == ["apice", "barbours", "lescent", "rituaria"]
    assert params["inicio"] == resp.janela.inicio and params["fim"] == resp.janela.fim


def test_nao_lidas_vem_da_ultima_foto_e_nulo_nao_vira_zero():
    resp, _ = _montar()
    assert resp.chat.nao_lidas_estado == "dado"
    nl = {n.marca: n.valor for n in resp.chat.nao_lidas}
    assert nl == {"apice": 12, "barbours": None}


def test_sem_coleta_ainda_e_sem_fonte_com_motivo():
    resp, fonte = _montar(fonte=FonteCx(tabelas=(False, False)))
    assert resp.devolucoes.estado == "sem_fonte"
    assert "shopee_cx_coleta" in resp.devolucoes.motivo_indisponivel
    assert resp.devolucoes.total is None and resp.devolucoes.marcas == []
    assert resp.chat.nao_lidas_estado == "sem_fonte" and resp.chat.nao_lidas == []
    assert [n for n, _ in fonte.chamadas] == ["tabelas_cx"]


def test_falha_na_coleta_vira_erro_sem_vazar_e_sem_derrubar_o_resto():
    resp, _ = _montar(fonte=FonteCx(falhas_coleta={"tabelas_cx"}))
    assert resp.devolucoes.estado == "erro" and resp.chat.nao_lidas_estado == "erro"
    assert resp.chat.estado == "dado" and resp.atendimento.indicadores[0].estado == "dado"
    assert base_cx.SEGREDO not in resp.model_dump_json()


def test_rotulo_de_devolucao():
    assert svc.rotular_devolucao("CHANGE_MIND")[1:] == ("Mudou de ideia", "comprador", True)
    assert svc.rotular_devolucao(None)[0] == "sem_motivo"
    assert svc.rotular_devolucao("x_novo")[3] is False


def test_conversas_vem_da_ultima_foto_e_coluna_ausente_vira_nulo():
    """04/11/2026: volume de conversas (get_conversation_list) na mesma foto."""
    resp, _ = _montar(fonte=FonteCx(nao_lidas=[
        {"marca": "apice", "unread_count": 3, "captured_at": AGORA, "conversas_7d": 40,
         "conversas_30d": 180, "aguardando_resposta": 5, "conversas_truncado": False},
        {"marca": "barbours", "unread_count": 1, "captured_at": AGORA},
    ]))
    nl = {n.marca: n for n in resp.chat.nao_lidas}
    assert (nl["apice"].conversas_7d, nl["apice"].conversas_30d, nl["apice"].aguardando_resposta) == (40, 180, 5)
    assert nl["apice"].conversas_truncado is False
    assert nl["barbours"].conversas_30d is None and nl["barbours"].conversas_truncado is None


def test_sql_de_conversas_nao_quebra_sem_as_colunas_novas():
    assert "to_jsonb(s) ->> 'conversas_30d'" in svc.SQL_NAO_LIDAS
    assert "FROM raw.shopee_chat_snapshot s" in svc.SQL_NAO_LIDAS



# ---------------------------------------------------------------------------
# Kokeshi pelo bot do Seller Centre (CX-KOKESHI-BOT, 05/11/2026)
# ---------------------------------------------------------------------------
def _torre_kokeshi(**kw):
    saude = [dict(base_po._shopee()[0], brand="kokeshi", overall_rating=3)]
    base = dict(
        tabelas=(True, True), saude=saude,
        pedidos=[{"marca": "kokeshi", "pedidos": 900, "cancelados": 18, "cancelados_comprador": None,
                  "nao_pagos": 40, "atualizado_em": AGORA}],
        motivos_canc=[{"motivo": "Solicitado pelo comprador: mudou de ideia", "quantidade": 12},
                      {"motivo": "Falha na entrega", "quantidade": 6}],
        carga=[{"marca": "kokeshi"}],
        devolucoes=[{"marca": "kokeshi", "devolucoes": 7, "aceitas": 5, "em_andamento": 1, "canceladas": 1,
                     "reembolsado": 300.0}],
        motivos=[{"motivo": "CHANGE_MIND", "quantidade": 4, "canceladas": 1},
                 {"motivo": "NOT_RECEIPT", "quantidade": 2, "canceladas": 0}],
        nao_lidas=[{"marca": "kokeshi", "unread_count": 9, "captured_at": AGORA, "conversas_7d": 50,
                    "conversas_30d": 210, "aguardando_resposta": 4, "conversas_truncado": None}],
    )
    base.update(kw)
    return TorreCx(**base)


def test_kokeshi_entra_em_todos_os_blocos_da_aba_cx():
    resp, _ = _montar(torre=_torre_kokeshi())
    # chat: Saude da Conta do bot + nao lidas/conversas do bot
    assert "kokeshi" in [c.marca for c in resp.chat.contas]
    nl = {n.marca: n for n in resp.chat.nao_lidas}
    assert (nl["kokeshi"].valor, nl["kokeshi"].conversas_30d, nl["kokeshi"].aguardando_resposta) == (9, 210, 4)
    assert nl["apice"].valor == 12 and resp.chat.nao_lidas_estado == "dado"
    # cancelamentos: export de pedidos, motivo em portugues legivel (mapeado)
    k = {m.marca: m for m in resp.atendimento.marcas}["kokeshi"]
    assert (k.pedidos, k.cancelamentos, k.cancelamentos_comprador, k.em_devolucao) == (900, 18, None, None)
    rot = {i.rotulo: i for i in resp.atendimento.motivos.itens}
    assert rot["Solicitado pelo comprador: mudou de ideia"].natureza == "comprador"
    assert rot["Falha na entrega"].natureza == "logistica" and rot["Falha na entrega"].mapeado
    assert resp.atendimento.cobertura.fora == []
    # devolucoes: somadas com as lojas de app, motivos juntos
    d = resp.devolucoes
    assert d.estado == "dado" and d.total == 6 + 7 and d.reembolsado == round(120.5 + 300.0, 2)
    assert {m.marca: m.devolucoes for m in d.marcas}["kokeshi"] == 7
    mud = {i.codigo: i.quantidade for i in d.motivos.itens}
    assert mud["change_mind"] == 2 + 4 and mud["not_receipt"] == 2


def test_kokeshi_sem_036_fica_ausente_e_as_outras_seguem():
    resp, _ = _montar()
    nl = {n.marca for n in resp.chat.nao_lidas}
    assert "kokeshi" not in nl and resp.chat.nao_lidas_estado == "dado"
    d = resp.devolucoes
    assert d.estado == "dado" and {m.marca: m.devolucoes for m in d.marcas}["kokeshi"] is None
    assert d.total == 6, "a Kokeshi sem carga nao entra como zero"


def test_kokeshi_sem_carga_de_devolucoes_e_ausente_nao_zero():
    resp, _ = _montar(torre=_torre_kokeshi(carga=[], devolucoes=[], motivos=[]))
    assert {m.marca: m.devolucoes for m in resp.devolucoes.marcas}["kokeshi"] is None


def test_neon_caido_nao_derruba_as_lojas_com_app():
    torre = _torre_kokeshi(falhas={"export_marcas", "nao_lidas_export", "carga"})
    resp, _ = _montar(torre=torre)
    k = {m.marca: m for m in resp.atendimento.marcas}["kokeshi"]
    assert k.pedidos is None and k.cancelamentos is None
    assert resp.atendimento.indicadores[0].estado == "dado"
    assert resp.chat.nao_lidas_estado == "dado"
    assert resp.devolucoes.estado == "dado"
    assert base_cx.SEGREDO not in resp.model_dump_json()


def test_filtro_so_kokeshi_nao_toca_o_data_mart_do_cx():
    resp, fonte = _montar(brands=["kokeshi"], torre=_torre_kokeshi())
    assert fonte.chamadas == [] and fonte.cx.chamadas == []
    assert [m.marca for m in resp.atendimento.marcas] == ["kokeshi"]
    assert resp.devolucoes.estado == "dado" and resp.devolucoes.total == 7
    assert [n.marca for n in resp.chat.nao_lidas] == ["kokeshi"]


def test_rotulo_pt_do_export():
    assert svc.rotular_shopee_pt("Cancelado pelo vendedor: sem estoque")[2] == "vendedor"
    assert svc.rotular_shopee_pt("-") == ("sem_motivo", "Sem motivo informado", "plataforma", True)
    assert svc.rotular_shopee_pt("Outro motivo")[1:] == ("Outro motivo", "plataforma", True)


def test_sql_do_export_tira_o_nao_pago_e_usa_o_dia_de_brasilia():
    for sql in (svc.SQL_SHOPEE_EXPORT_MARCAS, svc.SQL_SHOPEE_EXPORT_MOTIVOS):
        assert "marts.expedicao_shopee_export_pedido" in sql and "nao pago" in sql
        assert "pay_time IS NULL OR" in sql, "cancelado sem pagamento = nao pago, sem depender do texto"
        assert "AT TIME ZONE 'America/Sao_Paulo'" in sql
    assert "NULL::int AS cancelados_comprador" in svc.SQL_SHOPEE_EXPORT_MARCAS


def test_so_kokeshi_com_neon_caido_e_erro_nao_vazio():
    resp, _ = _montar(brands=["kokeshi"], torre=_torre_kokeshi(falhas={"export_marcas"}))
    assert resp.atendimento.indicadores[0].estado == "erro"
