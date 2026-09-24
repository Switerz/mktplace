"""Gate EXP-TK-OPS-1 — serving da serie diaria do TikTok Shop.

O que estes testes travam, em uma frase: a tela nunca mostra `0%` de uma coorte
que ainda pode piorar, nunca soma taxas, nunca some quando a tabela nao existe,
e nunca deixa de dizer que o prazo e' reconstruido.

O dublê responde por TRECHO da consulta, e devolve MAPPINGS (dicionarios),
porque e' assim que o servico le: `.mappings()`. Um dublê que devolvesse tuplas
passaria nos testes e quebraria no primeiro contato com o banco — foi o defeito
medido no gate do PostgresHook.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import expedicao_tiktok_service as svc

AGORA = datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc)
HOJE = date(2026, 9, 24)  # 13:00 BRT do mesmo instante
LOTE = "4210639f-32dc-4f59-bcfe-b02a82f3441d"


def linha(
    paid: date,
    brand: str = "kokeshi",
    *,
    deadline: date | None = None,
    pagos: int = 100,
    mature: bool = True,
    col_atras: int = 0,
    col_pend_venc: int = 0,
    col_pend_prazo: int = 0,
    desp_atras: int = 0,
    desp_pend_venc: int = 0,
    desp_pend_prazo: int = 0,
    cancelados: int = 0,
    effective_at: datetime | None = None,
) -> dict:
    """Linha publicada que FECHA por construcao (o "no prazo" absorve o resto)."""
    return {
        "paid_date": paid,
        "brand": brand,
        "shop_name": brand.upper(),
        "deadline_date": deadline or (paid + timedelta(days=2)),
        "is_mature": mature,
        "pedidos_pagos": pagos,
        "cancelados": cancelados,
        "despacho_no_prazo": pagos - desp_atras - desp_pend_venc - desp_pend_prazo,
        "despacho_atrasado": desp_atras,
        "despacho_pendente_vencido": desp_pend_venc,
        "despacho_pendente_no_prazo": desp_pend_prazo,
        "coleta_no_prazo": pagos - col_atras - col_pend_venc - col_pend_prazo,
        "coleta_atrasada": col_atras,
        "coleta_pendente_vencida": col_pend_venc,
        "coleta_pendente_no_prazo": col_pend_prazo,
        "refresh_batch_id": LOTE,
        "effective_at": effective_at or AGORA,
        "source_watermark_at": AGORA,
    }


class _Resultado:
    def __init__(self, linhas, escalar=None):
        self._linhas = linhas
        self._escalar = escalar

    def mappings(self):
        return self._linhas

    def scalar(self):
        return self._escalar


class SessaoFake:
    """Responde por trecho da consulta e guarda o que recebeu."""

    def __init__(self, *, linhas=None, tabela_existe=True):
        self.linhas = [] if linhas is None else linhas
        self.tabela_existe = tabela_existe
        self.consultas: list[tuple[str, dict]] = []

    def execute(self, clause, params=None):
        sql = str(clause)
        self.consultas.append((sql, dict(params or {})))
        if "to_regclass" in sql:
            return _Resultado([], escalar=self.tabela_existe)
        return _Resultado(self.linhas)


def serie(linhas, **kw):
    kw.setdefault("agora", AGORA)
    return svc.obter_serie(SessaoFake(linhas=linhas), **kw)


# ---------------------------------------------------------------------------
# Disponibilidade
# ---------------------------------------------------------------------------
def test_tabela_ausente_nao_vira_500_nem_tela_de_zero():
    """A migration 020 pode nao ter sido aplicada ainda.

    Devolver 0% seria lido como "nenhum atraso", que e' o oposto de "sem
    medicao" — e nesta tela em particular e' o pior desfecho possivel.
    """
    r = svc.obter_serie(SessaoFake(tabela_existe=False), agora=AGORA)
    assert r["availability"] == "unavailable"
    assert r["unavailable_reason"] == svc.UNAVAILABLE_NOT_MIGRATED
    assert r["daily"] == []
    assert r["window"] is None


def test_tabela_vazia_e_um_motivo_diferente_de_tabela_ausente():
    r = serie([])
    assert r["unavailable_reason"] == svc.UNAVAILABLE_NO_DATA


def test_indisponivel_ainda_declara_que_o_prazo_e_reconstruido():
    # O aviso nao pode depender de haver dado: a tela o exibe sempre.
    r = svc.obter_serie(SessaoFake(tabela_existe=False), agora=AGORA)
    assert r["deadline_is_reconstructed"] is True


def test_disponivel_declara_que_o_prazo_e_reconstruido():
    r = serie([linha(date(2026, 9, 20))])
    assert r["availability"] == "available"
    assert r["deadline_is_reconstructed"] is True


# ---------------------------------------------------------------------------
# Evento
# ---------------------------------------------------------------------------
def test_evento_padrao_e_a_coleta():
    assert svc.resolver_evento(None) == "coleta"
    assert serie([linha(date(2026, 9, 20))])["event"] == "coleta"


def test_evento_fora_da_allowlist_levanta():
    with pytest.raises(svc.EventoInvalido):
        svc.resolver_evento("qualquer")
    with pytest.raises(svc.EventoInvalido):
        svc.resolver_evento("IN_TRANSIT")


def test_os_dois_eventos_contam_populacoes_DIFERENTES():
    """O mesmo pedido e' contado uma vez em cada evento.

    Medido na fonte: despacho ~0,05% e coleta 88,9% no mesmo dia. Colapsar os
    dois trocaria o dono do problema — a operacao etiquetou no prazo, quem nao
    coletou foi a transportadora.
    """
    ls = [linha(date(2026, 9, 20), pagos=100, col_atras=80, desp_atras=1)]
    assert serie(ls, evento="coleta")["window"]["late_orders"] == 80
    assert serie(ls, evento="despacho")["window"]["late_orders"] == 1


# ---------------------------------------------------------------------------
# Consolidacao
# ---------------------------------------------------------------------------
def test_consolida_marcas_somando_numerador_e_denominador_e_nao_taxas():
    """90% de 10 pedidos + 0% de 990 e' 0,9% no dia, nao 45%."""
    ls = [
        linha(date(2026, 9, 20), "lescent", pagos=10, col_atras=9),
        linha(date(2026, 9, 20), "kokeshi", pagos=990),
    ]
    (dia,) = serie(ls)["daily"]
    assert dia["pedidos_pagos"] == 1000
    assert dia["atrasados"] == 9
    assert dia["rate"] == pytest.approx(0.009)


def test_dia_e_parcial_se_qualquer_marca_dele_for_imatura():
    ls = [
        linha(date(2026, 9, 22), "kokeshi", mature=True),
        linha(date(2026, 9, 22), "lescent", mature=False),
    ]
    (dia,) = serie(ls)["daily"]
    assert dia["is_mature"] is False


def test_dia_sem_pedido_pago_tem_taxa_nula_e_nao_zero():
    (dia,) = serie([linha(date(2026, 9, 20), pagos=0)])["daily"]
    assert dia["rate"] is None


# ---------------------------------------------------------------------------
# Maturacao — o requisito central
# ---------------------------------------------------------------------------
def test_coorte_imatura_nunca_e_marcada_como_critica():
    """Pintar de vermelho um dia que ainda da' para cumprir acusa a operacao
    por algo que nao aconteceu."""
    ls = [linha(date(2026, 9, 24), mature=False, pagos=100, col_pend_prazo=100)]
    (dia,) = serie(ls)["daily"]
    assert dia["is_critical"] is False


def test_coorte_imatura_fica_fora_da_taxa_da_janela():
    """Incluir quem ainda tem prazo dilui a taxa e esconde o incidente."""
    ls = [
        linha(date(2026, 9, 20), pagos=100, col_atras=50),
        linha(date(2026, 9, 24), pagos=900, mature=False, col_pend_prazo=900),
    ]
    j = serie(ls)["window"]
    assert j["rate_ratio_of_totals"] == pytest.approx(0.5)
    assert j["paid_orders"] == 100
    assert j["mature_cohorts"] == 1
    assert j["partial_cohorts"] == 1


def test_coorte_madura_acima_do_limiar_e_critica():
    (dia,) = serie([linha(date(2026, 9, 20), pagos=100, col_atras=10)])["daily"]
    assert dia["is_critical"] is True
    (dia,) = serie([linha(date(2026, 9, 20), pagos=100, col_atras=9)])["daily"]
    assert dia["is_critical"] is False


# ---------------------------------------------------------------------------
# As duas leituras da janela
# ---------------------------------------------------------------------------
def test_publica_as_duas_leituras_da_janela():
    """A fonte nao diz qual a plataforma usa; publicar so' uma e' escolher sem
    prova. Medido em 2026-09-24: elas divergiram 3,96 pp na mesma janela."""
    ls = [
        linha(date(2026, 9, 18), pagos=10_000, col_atras=100),
        linha(date(2026, 9, 19), pagos=10, col_atras=5),
    ]
    j = serie(ls)["window"]
    assert j["rate_ratio_of_totals"] == pytest.approx(105 / 10_010)
    assert j["rate_mean_of_daily"] == pytest.approx((0.01 + 0.5) / 2)


def test_media_das_diarias_e_por_dia_e_nao_por_linha_publicada():
    """Cada DIA vale um ponto, nao cada (dia, marca)."""
    ls = [
        linha(date(2026, 9, 20), "lescent", pagos=10, col_atras=10),
        linha(date(2026, 9, 20), "kokeshi", pagos=990),
    ]
    j = serie(ls)["window"]
    assert j["rate_mean_of_daily"] == pytest.approx(0.01)
    assert j["rate_ratio_of_totals"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Acionavel
# ---------------------------------------------------------------------------
def test_separa_o_que_ja_venceu_do_que_ainda_da_para_tratar():
    ls = [
        linha(date(2026, 9, 20), pagos=100, col_pend_venc=7),
        linha(date(2026, 9, 24), deadline=date(2026, 9, 25), mature=False,
              pagos=50, col_pend_prazo=50),
    ]
    j = serie(ls)["window"]
    assert j["pending_overdue"] == 7
    assert j["pending_on_time"] == 50
    # vence amanha -> ainda da' para tratar
    assert j["pending_at_risk"] == 50


def test_pendente_que_vence_depois_de_amanha_nao_conta_como_risco():
    ls = [linha(date(2026, 9, 24), deadline=date(2026, 9, 28), mature=False,
                pagos=50, col_pend_prazo=50)]
    assert serie(ls)["window"]["pending_at_risk"] == 0


def test_marca_o_periodo_do_incidente():
    ls = [
        linha(date(2026, 9, 17), pagos=100, col_atras=1),
        linha(date(2026, 9, 18), pagos=100, col_atras=35),
        linha(date(2026, 9, 19), pagos=100, col_atras=88),
        linha(date(2026, 9, 20), pagos=100, col_atras=2),
    ]
    j = serie(ls)["window"]
    assert j["incident_start"] == date(2026, 9, 18)
    assert j["incident_end"] == date(2026, 9, 19)


def test_sem_dia_critico_nao_inventa_incidente():
    j = serie([linha(date(2026, 9, 20), pagos=100, col_atras=1)])["window"]
    assert j["incident_start"] is None
    assert j["incident_end"] is None


# ---------------------------------------------------------------------------
# Marcas
# ---------------------------------------------------------------------------
def test_marca_com_base_pequena_fica_fora_do_ranking():
    """1 atraso em 3 pedidos vira 33% e lideraria sem significar nada."""
    ls = [
        linha(date(2026, 9, 20), "denavita", pagos=3, col_atras=1),
        linha(date(2026, 9, 20), "kokeshi", pagos=1000, col_atras=200),
    ]
    assert [m["brand"] for m in serie(ls)["brands"]] == ["kokeshi"]


def test_ranking_de_marcas_vai_da_pior_para_a_melhor():
    ls = [
        linha(date(2026, 9, 20), "gocase", pagos=1000, col_atras=2),
        linha(date(2026, 9, 20), "barbours", pagos=1000, col_atras=400),
        linha(date(2026, 9, 20), "kokeshi", pagos=1000, col_atras=350),
    ]
    assert [m["brand"] for m in serie(ls)["brands"]] == ["barbours", "kokeshi", "gocase"]


def test_marca_imatura_nao_entra_no_ranking():
    ls = [linha(date(2026, 9, 24), "kokeshi", pagos=1000, mature=False,
                col_pend_prazo=1000)]
    assert serie(ls)["brands"] == []


def test_filtro_de_marca_e_aplicado():
    ls = [
        linha(date(2026, 9, 20), "kokeshi", pagos=1000, col_atras=200),
        linha(date(2026, 9, 20), "gocase", pagos=1000, col_atras=1),
    ]
    r = serie(ls, brands=["gocase"])
    assert [m["brand"] for m in r["brands"]] == ["gocase"]
    assert r["window"]["paid_orders"] == 1000


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------
def test_alertas_sao_texto_pronto_em_portugues_do_brasil():
    """Alerta com `88.9%` e `2026-09-07` no meio de uma tela em portugues faz
    o operador achar que veio de outro sistema."""
    ls = [linha(date(2026, 9, 18), pagos=1000, col_atras=889, col_pend_venc=0)]
    msgs = " ".join(a["message"] for a in serie(ls)["alerts"])
    assert "18/09/2026" in msgs
    assert "88,9%" in msgs
    assert "2026-09-18" not in msgs


def test_alerta_de_pendentes_vencidos_e_critico():
    ls = [linha(date(2026, 9, 20), pagos=100, col_pend_venc=7)]
    a = [x for x in serie(ls)["alerts"] if x["code"] == "pendentes_vencidos"]
    assert a and a[0]["severity"] == "critical"


def test_alerta_de_risco_e_aviso_e_nao_critico():
    ls = [linha(date(2026, 9, 24), deadline=date(2026, 9, 25), mature=False,
                pagos=50, col_pend_prazo=50)]
    a = [x for x in serie(ls)["alerts"] if x["code"] == "em_risco"]
    assert a and a[0]["severity"] == "warning"


def test_fotografia_velha_vira_alerta():
    velha = AGORA - timedelta(hours=30)
    ls = [linha(date(2026, 9, 20), effective_at=velha)]
    codigos = {a["code"] for a in serie(ls)["alerts"]}
    assert "fotografia_velha" in codigos


def test_fotografia_recente_nao_alerta():
    ls = [linha(date(2026, 9, 20), effective_at=AGORA - timedelta(hours=2))]
    codigos = {a["code"] for a in serie(ls)["alerts"]}
    assert "fotografia_velha" not in codigos


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------
def test_a_consulta_e_sempre_limitada_ao_canal_tiktok():
    s = SessaoFake(linhas=[linha(date(2026, 9, 20))])
    svc.obter_serie(s, agora=AGORA)
    sql, params = next(c for c in s.consultas if "expedicao_tiktok" in c[0])
    assert params["canal"] == "tiktokshop"
    assert "channel = :canal" in sql


def test_a_janela_pedida_vira_intervalo_de_datas():
    s = SessaoFake(linhas=[linha(date(2026, 9, 20))])
    svc.obter_serie(s, dias=7, agora=AGORA)
    _sql, params = next(c for c in s.consultas if "expedicao_tiktok" in c[0])
    assert params["ate"] == HOJE
    assert params["desde"] == HOJE - timedelta(days=7)


def test_janela_absurda_e_limitada_em_vez_de_derrubar_a_consulta():
    s = SessaoFake(linhas=[linha(date(2026, 9, 20))])
    svc.obter_serie(s, dias=9999, agora=AGORA)
    _sql, params = next(c for c in s.consultas if "expedicao_tiktok" in c[0])
    assert params["desde"] == HOJE - timedelta(days=svc.JANELA_MAX_DIAS)


def test_from_reflete_o_dado_existente_e_nao_o_intervalo_pedido():
    """Dizer "de 25/08" quando a serie comeca em 20/09 faz o operador achar
    que houve semanas sem atraso nenhum."""
    r = serie([linha(date(2026, 9, 20))], dias=30)
    assert r["window"]["from"] == date(2026, 9, 20)
    assert r["window"]["days"] == 30


def test_sql_nao_seleciona_pii():
    baixo = svc.SERIE_SQL.lower()
    for proibido in ("cpf", "tracking", "order_id", "buyer", "phone"):
        assert proibido not in baixo, proibido


def test_o_servico_nunca_escreve():
    for sql in (svc.SERIE_SQL,):
        baixo = sql.lower()
        for proibido in ("insert", "update ", "delete", "drop", "truncate"):
            assert proibido not in baixo, proibido
