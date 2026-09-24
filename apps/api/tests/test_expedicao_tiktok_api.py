"""Gate EXP-TK-OPS-1/2 — serving da LDR do TikTok Shop.

O que estes testes travam, em uma frase: a LDR usa como denominador quem VENCE
no periodo (nunca quem pagou), os dois limiares nao se confundem, a tela nunca
mostra `0%` de uma coorte que ainda pode piorar, e a resposta nunca deixa de
dizer que o prazo e' reconstruido.

O dublê responde por TRECHO da consulta e devolve MAPPINGS (dicionarios),
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
    rts_deadline: date | None = None,
    tts_deadline: date | None = None,
    brutos: int = 100,
    amostras: int = 0,
    canc_rts: int = 0,
    canc_tts: int = 0,
    rts_mature: bool = True,
    tts_mature: bool = True,
    desp_atras: int = 0,
    desp_pend_venc: int = 0,
    desp_pend_prazo: int = 0,
    col_atras: int = 0,
    col_pend_venc: int = 0,
    col_pend_prazo: int = 0,
    effective_at: datetime | None = None,
) -> dict:
    """Linha publicada que FECHA por construcao."""
    dbase = brutos - amostras - canc_rts
    cbase = brutos - amostras - canc_tts
    return {
        "paid_date": paid,
        "brand": brand,
        "shop_name": brand.upper(),
        "pedidos_pagos_brutos": brutos,
        "amostras_excluidas": amostras,
        "despacho_deadline": rts_deadline or (paid + timedelta(days=1)),
        "despacho_base": dbase,
        "despacho_cancelado_antes_sla": canc_rts,
        "despacho_no_prazo": dbase - desp_atras - desp_pend_venc - desp_pend_prazo,
        "despacho_atrasado": desp_atras,
        "despacho_pendente_vencido": desp_pend_venc,
        "despacho_pendente_no_prazo": desp_pend_prazo,
        "despacho_is_mature": rts_mature,
        "coleta_deadline": tts_deadline or (paid + timedelta(days=2)),
        "coleta_base": cbase,
        "coleta_cancelado_antes_sla": canc_tts,
        "coleta_no_prazo": cbase - col_atras - col_pend_venc - col_pend_prazo,
        "coleta_atrasada": col_atras,
        "coleta_pendente_vencida": col_pend_venc,
        "coleta_pendente_no_prazo": col_pend_prazo,
        "coleta_is_mature": tts_mature,
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
    r = svc.obter_serie(SessaoFake(tabela_existe=False), agora=AGORA)
    assert r["availability"] == "unavailable"
    assert r["unavailable_reason"] == svc.UNAVAILABLE_NOT_MIGRATED
    assert r["ldr"] is None
    assert r["payment_flow"] == []


def test_tabela_vazia_e_um_motivo_diferente_de_tabela_ausente():
    assert serie([])["unavailable_reason"] == svc.UNAVAILABLE_NO_DATA


def test_indisponivel_ainda_declara_prazo_reconstruido_e_os_limiares():
    r = svc.obter_serie(SessaoFake(tabela_existe=False), agora=AGORA)
    assert r["deadline_is_reconstructed"] is True
    assert r["targets"]["tiktok_ldr"] == 0.04
    assert r["targets"]["internal_critical"] == 0.10
    assert r["sla_business_days"] == {"despacho": 1, "coleta": 2}


# ---------------------------------------------------------------------------
# Evento
# ---------------------------------------------------------------------------
def test_evento_padrao_e_a_coleta():
    assert svc.resolver_evento(None) == "coleta"


def test_evento_fora_da_allowlist_levanta():
    with pytest.raises(svc.EventoInvalido):
        svc.resolver_evento("IN_TRANSIT")


def test_cada_evento_tem_prazo_proprio():
    """RTS 1 dia util, TTS 2. Aplicar o mesmo prazo aos dois subestimava o
    atraso da operacao."""
    assert svc.SLA_DIAS_UTEIS["despacho"] == 1
    assert svc.SLA_DIAS_UTEIS["coleta"] == 2


def test_os_dois_eventos_contam_populacoes_diferentes():
    ls = [linha(date(2026, 9, 20), brutos=100, col_atras=80, desp_atras=1)]
    assert serie(ls, evento="coleta")["ldr"]["late"] == 80
    assert serie(ls, evento="despacho")["ldr"]["late"] == 1


# ---------------------------------------------------------------------------
# LDR — o denominador e' quem VENCE na janela
# ---------------------------------------------------------------------------
def test_ldr_usa_quem_vence_na_janela_e_nao_quem_pagou():
    """Coorte paga ha muito tempo mas com vencimento dentro da janela ENTRA;
    coorte paga na janela com vencimento fora dela NAO entra."""
    dentro = linha(date(2026, 9, 10), tts_deadline=date(2026, 9, 22),
                   brutos=100, col_atras=10)
    fora = linha(date(2026, 9, 23), tts_deadline=date(2026, 10, 1),
                 brutos=900, tts_mature=False, col_pend_prazo=900)
    r = serie([dentro, fora], dias=7)
    assert r["ldr"]["base"] == 100
    assert r["ldr"]["rate"] == pytest.approx(0.10)


def test_feriado_vira_um_vencimento_com_volume_somado():
    """Sab, dom e o feriado vencem no mesmo dia. Pela LDR isso e' UMA linha
    com a base somada; pelo fluxo de pagamento seriam tres linhas de 100%."""
    venc = date(2026, 9, 22)
    ls = [
        linha(date(2026, 9, 18), tts_deadline=venc, brutos=100, col_atras=100),
        linha(date(2026, 9, 19), tts_deadline=venc, brutos=200, col_atras=200),
        linha(date(2026, 9, 20), tts_deadline=venc, brutos=700, col_atras=70),
    ]
    r = serie(ls)
    assert len(r["ldr"]["daily"]) == 1
    (dia,) = r["ldr"]["daily"]
    assert dia["due_date"] == venc
    assert dia["base"] == 1000 and dia["late"] == 370
    assert dia["rate"] == pytest.approx(0.37)
    # o fluxo por pagamento mantem as tres linhas, duas em 100%
    assert [round((x["rate"] or 0) * 100) for x in r["payment_flow"]] == [100, 100, 10]


def test_ldr_consolida_marcas_somando_numerador_e_denominador():
    venc = date(2026, 9, 22)
    ls = [
        linha(date(2026, 9, 20), "lescent", tts_deadline=venc, brutos=10, col_atras=9),
        linha(date(2026, 9, 20), "kokeshi", tts_deadline=venc, brutos=990),
    ]
    (dia,) = serie(ls)["ldr"]["daily"]
    assert dia["base"] == 1000
    assert dia["rate"] == pytest.approx(0.009)


def test_vencimento_e_parcial_se_qualquer_marca_dele_for_imatura():
    venc = date(2026, 9, 24)
    ls = [
        linha(date(2026, 9, 22), "kokeshi", tts_deadline=venc, tts_mature=True),
        linha(date(2026, 9, 22), "lescent", tts_deadline=venc, tts_mature=False),
    ]
    (dia,) = serie(ls)["ldr"]["daily"]
    assert dia["is_mature"] is False
    assert dia["is_critical"] is False
    assert dia["above_target"] is False


def test_vencimento_imaturo_fica_fora_da_taxa_da_janela():
    ls = [
        linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
              brutos=100, col_atras=50),
        linha(date(2026, 9, 22), tts_deadline=date(2026, 9, 24), tts_mature=False,
              brutos=900, col_pend_prazo=900),
    ]
    j = serie(ls)["ldr"]
    assert j["rate"] == pytest.approx(0.5)
    assert j["base"] == 100
    assert j["mature_due_days"] == 1
    assert j["partial_due_days"] == 1


def test_vencimento_sem_base_tem_taxa_nula_e_nao_zero():
    (dia,) = serie([linha(date(2026, 9, 20), brutos=0)])["ldr"]["daily"]
    assert dia["rate"] is None


# ---------------------------------------------------------------------------
# Os DOIS limiares
# ---------------------------------------------------------------------------
def test_acima_de_4_por_cento_e_fora_da_meta_do_tiktok():
    ls = [linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
                brutos=1000, col_atras=50)]  # 5%
    j = serie(ls)["ldr"]
    assert j["above_target"] is True
    assert j["internal_critical"] is False
    assert j["daily"][0]["above_target"] is True
    assert j["daily"][0]["is_critical"] is False


def test_acima_de_10_por_cento_e_tambem_critico_interno():
    ls = [linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
                brutos=1000, col_atras=120)]
    j = serie(ls)["ldr"]
    assert j["above_target"] is True
    assert j["internal_critical"] is True


def test_exatamente_na_meta_nao_esta_fora_dela():
    ls = [linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
                brutos=1000, col_atras=40)]
    j = serie(ls)["ldr"]
    assert j["rate"] == pytest.approx(0.04)
    assert j["above_target"] is False


def test_o_alerta_de_conformidade_cita_a_meta_e_nao_o_limiar_interno():
    ls = [linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
                brutos=1000, col_atras=50)]
    alertas = {a["code"]: a for a in serie(ls)["alerts"]}
    assert "fora_da_meta_tiktok" in alertas
    assert "4%" in alertas["fora_da_meta_tiktok"]["message"]
    assert "dias_criticos_internos" not in alertas


def test_dentro_da_meta_gera_alerta_informativo_e_nao_critico():
    ls = [linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
                brutos=1000, col_atras=10)]
    a = [x for x in serie(ls)["alerts"] if x["code"] == "dentro_da_meta_tiktok"]
    assert a and a[0]["severity"] == "info"


def test_os_dois_limiares_geram_alertas_com_codigos_diferentes():
    ls = [linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
                brutos=1000, col_atras=150)]
    codigos = {a["code"] for a in serie(ls)["alerts"]}
    assert "fora_da_meta_tiktok" in codigos
    assert "dias_criticos_internos" in codigos


# ---------------------------------------------------------------------------
# Fluxo por data de pagamento — a visao da gestao
# ---------------------------------------------------------------------------
def test_o_fluxo_reproduz_as_colunas_da_planilha():
    ls = [linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22), brutos=100,
                amostras=4, canc_tts=6, col_atras=7, col_pend_venc=3)]
    (f,) = serie(ls)["payment_flow"]
    assert f["paid_orders"] == 100
    assert f["excluded_samples"] == 4
    assert f["excluded_cancelled"] == 6
    assert f["base"] == 90
    assert f["shipped_late"] == 7
    assert f["pending_overdue"] == 3
    assert f["shipped_on_time"] == 80
    assert f["late"] == 10
    assert f["rate"] == pytest.approx(10 / 90)


def test_o_fluxo_nao_e_a_ldr():
    """Denominadores diferentes: a LDR agrega por vencimento e so' conta
    coortes maduras; o fluxo lista toda data de pagamento lida."""
    ls = [
        linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
              brutos=100, col_atras=50),
        linha(date(2026, 9, 23), tts_deadline=date(2026, 9, 28), tts_mature=False,
              brutos=900, col_pend_prazo=900),
    ]
    r = serie(ls)
    assert len(r["payment_flow"]) == 2
    assert len(r["ldr"]["daily"]) == 1


# ---------------------------------------------------------------------------
# Exclusoes do denominador
# ---------------------------------------------------------------------------
def test_amostra_gratis_sai_do_denominador():
    ls = [linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
                brutos=100, amostras=20, col_atras=8)]
    assert serie(ls)["ldr"]["base"] == 80
    assert serie(ls)["ldr"]["rate"] == pytest.approx(0.1)


def test_cancelado_antes_do_sla_sai_do_denominador_do_evento():
    ls = [linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
                brutos=100, canc_tts=10, canc_rts=4)]
    assert serie(ls, evento="coleta")["ldr"]["base"] == 90
    assert serie(ls, evento="despacho")["ldr"]["base"] == 96


# ---------------------------------------------------------------------------
# Acionavel
# ---------------------------------------------------------------------------
def test_separa_o_que_ja_venceu_do_que_ainda_da_para_tratar():
    ls = [
        linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
              brutos=100, col_pend_venc=7),
        linha(date(2026, 9, 23), tts_deadline=date(2026, 9, 25), tts_mature=False,
              brutos=50, col_pend_prazo=50),
    ]
    j = serie(ls)["ldr"]
    assert j["pending_overdue"] == 7
    assert j["pending_on_time"] == 50
    assert j["pending_at_risk"] == 50


def test_pendente_que_vence_depois_de_amanha_nao_conta_como_risco():
    ls = [linha(date(2026, 9, 24), tts_deadline=date(2026, 9, 28), tts_mature=False,
                brutos=50, col_pend_prazo=50)]
    assert serie(ls)["ldr"]["pending_at_risk"] == 0


def test_marca_o_periodo_do_incidente_pelo_vencimento():
    ls = [
        linha(date(2026, 9, 17), tts_deadline=date(2026, 9, 19), brutos=100, col_atras=1),
        linha(date(2026, 9, 18), tts_deadline=date(2026, 9, 21), brutos=100, col_atras=35),
        linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22), brutos=100, col_atras=88),
        linha(date(2026, 9, 21), tts_deadline=date(2026, 9, 23), brutos=100, col_atras=2),
    ]
    j = serie(ls)["ldr"]
    assert j["incident_start"] == date(2026, 9, 21)
    assert j["incident_end"] == date(2026, 9, 22)


# ---------------------------------------------------------------------------
# Marcas
# ---------------------------------------------------------------------------
def test_marca_com_base_pequena_fica_fora_do_ranking():
    ls = [
        linha(date(2026, 9, 20), "denavita", tts_deadline=date(2026, 9, 22),
              brutos=3, col_atras=1),
        linha(date(2026, 9, 20), "kokeshi", tts_deadline=date(2026, 9, 22),
              brutos=1000, col_atras=200),
    ]
    assert [m["brand"] for m in serie(ls)["brands"]] == ["kokeshi"]


def test_marca_acima_da_meta_e_sinalizada():
    ls = [
        linha(date(2026, 9, 20), "kokeshi", tts_deadline=date(2026, 9, 22),
              brutos=1000, col_atras=200),
        linha(date(2026, 9, 20), "gocase", tts_deadline=date(2026, 9, 22),
              brutos=1000, col_atras=2),
    ]
    marcas = {m["brand"]: m for m in serie(ls)["brands"]}
    assert marcas["kokeshi"]["above_target"] is True
    assert marcas["gocase"]["above_target"] is False


def test_marca_fora_da_janela_de_vencimento_nao_entra_no_ranking():
    ls = [linha(date(2026, 9, 1), "kokeshi", tts_deadline=date(2026, 9, 3),
                brutos=1000, col_atras=900)]
    assert serie(ls, dias=7)["brands"] == []


# ---------------------------------------------------------------------------
# Alertas
# ---------------------------------------------------------------------------
def test_alertas_em_portugues_do_brasil():
    ls = [linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22),
                brutos=1000, col_atras=889)]
    msgs = " ".join(a["message"] for a in serie(ls)["alerts"])
    assert "22/09/2026" in msgs
    assert "88,9%" in msgs
    assert "2026-09-22" not in msgs


def test_fotografia_velha_vira_alerta():
    ls = [linha(date(2026, 9, 20), effective_at=AGORA - timedelta(hours=30))]
    assert "fotografia_velha" in {a["code"] for a in serie(ls)["alerts"]}


def test_fotografia_recente_nao_alerta():
    ls = [linha(date(2026, 9, 20), effective_at=AGORA - timedelta(hours=2))]
    assert "fotografia_velha" not in {a["code"] for a in serie(ls)["alerts"]}


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------
def test_a_consulta_e_sempre_limitada_ao_canal_tiktok():
    s = SessaoFake(linhas=[linha(date(2026, 9, 20))])
    svc.obter_serie(s, agora=AGORA)
    sql, params = next(c for c in s.consultas if "expedicao_tiktok" in c[0])
    assert params["canal"] == "tiktokshop"
    assert "channel = :canal" in sql


def test_le_mais_datas_de_pagamento_do_que_a_janela_de_vencimento():
    """Uma coorte empurrada por feriado paga bem antes do vencimento. Ler so' a
    janela de vencimento perderia justamente o dia ruim."""
    s = SessaoFake(linhas=[linha(date(2026, 9, 20))])
    svc.obter_serie(s, dias=7, agora=AGORA)
    _sql, params = next(c for c in s.consultas if "expedicao_tiktok" in c[0])
    assert params["ate"] == HOJE
    assert params["desde"] < HOJE - timedelta(days=7)


def test_janela_absurda_e_limitada_em_vez_de_derrubar_a_consulta():
    s = SessaoFake(linhas=[linha(date(2026, 9, 20))])
    svc.obter_serie(s, dias=9999, agora=AGORA)
    _sql, params = next(c for c in s.consultas if "expedicao_tiktok" in c[0])
    assert params["desde"] >= HOJE - timedelta(days=svc.JANELA_MAX_DIAS + 30)


def test_from_reflete_o_vencimento_existente_e_nao_o_intervalo_pedido():
    r = serie([linha(date(2026, 9, 20), tts_deadline=date(2026, 9, 22))], dias=30)
    assert r["ldr"]["from"] == date(2026, 9, 22)
    assert r["ldr"]["days"] == 30


def test_sql_nao_seleciona_pii():
    baixo = svc.SERIE_SQL.lower()
    for proibido in ("cpf", "tracking", "order_id", "buyer", "phone"):
        assert proibido not in baixo, proibido


def test_o_servico_nunca_escreve():
    baixo = svc.SERIE_SQL.lower()
    for proibido in ("insert", "update ", "delete", "drop", "truncate"):
        assert proibido not in baixo, proibido
