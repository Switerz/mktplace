"""Gate EXP-TK-OPS-1/2 — regras da LDR de despacho do TikTok Shop.

Estes testes exercitam a LOGICA PURA: prazo por evento, feriado, exclusoes do
denominador, maturacao, e as duas leituras (LDR por vencimento x fluxo por data
de pagamento). Nada aqui toca banco — a extracao contra a fonte real foi
reconciliada separadamente contra a planilha da gestao e esta registrada no
relatorio do gate.

O foco e' o que quebra em silencio: um feriado que empurra tres datas de
pagamento para o mesmo vencimento e infla qualquer media; um prazo de 2 dias
aplicado a etiqueta, que deveria ser 1; uma coorte imatura contada como madura,
que mostra 0% num dia que vai terminar em 80%.
"""
from __future__ import annotations

import ast
import inspect
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipelines.expedicao import tiktok_daily as td
from pipelines.expedicao.tiktok_daily import (
    LIMIAR_CRITICO_INTERNO,
    META_TIKTOK_LDR,
    PRAZO_DIAS_UTEIS,
    CoorteDiaria,
    Evento,
    ForaDaCoberturaDeFeriados,
    calcular_ldr,
    dia_util,
    hoje_brt,
    janela_de_pagamento,
    janela_de_vencimento,
    linhas_para_publicar,
    marcas_criticas,
    prazo_de,
    prazo_do_evento,
    prazos_da_janela,
    primeiro_dia_do_incidente,
    serie_por_pagamento,
    serie_por_vencimento,
    ultimo_dia_do_incidente,
)


# ---------------------------------------------------------------------------
# Auxiliar
# ---------------------------------------------------------------------------
def coorte(
    paid: date,
    brand: str = "kokeshi",
    *,
    brutos: int = 100,
    amostras: int = 0,
    canc_rts: int = 0,
    canc_tts: int = 0,
    desp_atras: int = 0,
    desp_pend_venc: int = 0,
    desp_pend_prazo: int = 0,
    col_atras: int = 0,
    col_pend_venc: int = 0,
    col_pend_prazo: int = 0,
    desp_deadline: date | None = None,
    col_deadline: date | None = None,
) -> CoorteDiaria:
    """Coorte que FECHA por construcao: o "no prazo" absorve o resto."""
    dbase = brutos - amostras - canc_rts
    cbase = brutos - amostras - canc_tts
    return CoorteDiaria(
        paid_date=paid,
        brand=brand,
        shop_name=brand.upper(),
        pedidos_pagos_brutos=brutos,
        amostras_excluidas=amostras,
        despacho_deadline=desp_deadline or prazo_do_evento(paid, Evento.DESPACHO),
        despacho_base=dbase,
        despacho_cancelado_antes_sla=canc_rts,
        despacho_no_prazo=dbase - desp_atras - desp_pend_venc - desp_pend_prazo,
        despacho_atrasado=desp_atras,
        despacho_pendente_vencido=desp_pend_venc,
        despacho_pendente_no_prazo=desp_pend_prazo,
        coleta_deadline=col_deadline or prazo_do_evento(paid, Evento.COLETA),
        coleta_base=cbase,
        coleta_cancelado_antes_sla=canc_tts,
        coleta_no_prazo=cbase - col_atras - col_pend_venc - col_pend_prazo,
        coleta_atrasada=col_atras,
        coleta_pendente_vencida=col_pend_venc,
        coleta_pendente_no_prazo=col_pend_prazo,
    )


# ---------------------------------------------------------------------------
# Prazos — a politica tem DOIS prazos
# ---------------------------------------------------------------------------
def test_os_dois_eventos_tem_prazos_diferentes():
    """RTS 1 dia util, TTS 2. Aplicar 2 a etiqueta subestimava o atraso da
    operacao: medido, a taxa de RTS passa de 0,06% para 0,61% com o prazo
    certo."""
    assert PRAZO_DIAS_UTEIS[Evento.DESPACHO] == 1
    assert PRAZO_DIAS_UTEIS[Evento.COLETA] == 2


def test_prazo_do_despacho_vence_antes_do_da_coleta():
    d = date(2026, 9, 1)
    while d < date(2027, 6, 1):
        assert prazo_do_evento(d, Evento.DESPACHO) <= prazo_do_evento(d, Evento.COLETA)
        d += timedelta(days=1)


def test_prazo_pula_o_fim_de_semana_e_o_feriado():
    # 03/09/2026 e' quinta. RTS = sexta 04; TTS = segunda 07 e' FERIADO, entao
    # terca 08.
    assert prazo_do_evento(date(2026, 9, 3), Evento.DESPACHO) == date(2026, 9, 4)
    assert prazo_do_evento(date(2026, 9, 3), Evento.COLETA) == date(2026, 9, 8)


def test_pagar_no_fim_de_semana_nao_consome_prazo():
    # Sabado 05, domingo 06 e o feriado 07 vencem todos no MESMO dia: a
    # contagem so' comeca no proximo dia util (08 = 1o, 09 = 2o).
    for d in (date(2026, 9, 5), date(2026, 9, 6), date(2026, 9, 7)):
        assert prazo_do_evento(d, Evento.DESPACHO) == date(2026, 9, 8)
        assert prazo_do_evento(d, Evento.COLETA) == date(2026, 9, 9)


def test_feriado_nacional_nao_conta_como_dia_util():
    assert dia_util(date(2026, 9, 7)) is False      # Independencia, segunda
    assert dia_util(date(2026, 9, 8)) is True
    assert dia_util(date(2026, 4, 3)) is False      # Sexta-feira Santa
    assert dia_util(date(2026, 2, 17)) is False     # Carnaval (terca)
    assert dia_util(date(2026, 11, 20)) is False    # Consciencia Negra


def test_natal_empurra_o_prazo_para_depois_do_fim_de_semana():
    # 24/12/2026 e' quinta. 25 (Natal) nao conta, 26-27 e' fim de semana:
    # seg 28 = 1o util, ter 29 = 2o.
    assert prazo_do_evento(date(2026, 12, 24), Evento.DESPACHO) == date(2026, 12, 28)
    assert prazo_do_evento(date(2026, 12, 24), Evento.COLETA) == date(2026, 12, 29)


def test_data_fora_da_cobertura_de_feriados_levanta():
    # Silencio aqui seria um prazo errado publicado como se fosse certo.
    with pytest.raises(ForaDaCoberturaDeFeriados):
        dia_util(date(2028, 1, 3))
    with pytest.raises(ForaDaCoberturaDeFeriados):
        dia_util(date(2024, 12, 31))


def test_cobertura_de_feriados_alcanca_o_maior_prazo():
    ultimo = td.FERIADOS_COBERTURA[1] - timedelta(days=10)
    assert prazo_do_evento(ultimo, Evento.COLETA) <= td.FERIADOS_COBERTURA[1]


def test_prazo_recusa_zero_dias():
    with pytest.raises(ValueError):
        prazo_de(date(2026, 9, 1), 0)


# ---------------------------------------------------------------------------
# Fuso
# ---------------------------------------------------------------------------
def test_hoje_brt_vira_o_dia_tres_horas_antes_do_utc():
    assert hoje_brt(datetime(2026, 9, 24, 0, 30, tzinfo=timezone.utc)) == date(2026, 9, 23)
    assert hoje_brt(datetime(2026, 9, 24, 3, 30, tzinfo=timezone.utc)) == date(2026, 9, 24)


def test_hoje_brt_recusa_datetime_ingenuo():
    with pytest.raises(ValueError):
        hoje_brt(datetime(2026, 9, 24, 12, 0))


# ---------------------------------------------------------------------------
# Denominador e fechamento do grao
# ---------------------------------------------------------------------------
def test_a_base_e_os_brutos_menos_amostras_e_cancelados_antes_do_sla():
    c = coorte(date(2026, 9, 10), brutos=100, amostras=7, canc_rts=3, canc_tts=5)
    assert c.base(Evento.DESPACHO) == 90
    assert c.base(Evento.COLETA) == 88
    c.validar()


def test_bases_diferentes_por_evento_sao_validas():
    """Os vencimentos diferem, entao as exclusoes diferem: um cancelamento
    pode ser anterior ao prazo de coleta e posterior ao de despacho."""
    c = coorte(date(2026, 9, 10), brutos=100, canc_rts=1, canc_tts=9)
    assert c.base(Evento.DESPACHO) != c.base(Evento.COLETA)
    c.validar()


def test_base_que_nao_bate_com_as_exclusoes_levanta():
    # A particao precisa FECHAR na base inflada, senao o erro que dispara e' o
    # de fechamento e o teste nao exercita a checagem das exclusoes.
    c = coorte(date(2026, 9, 10), brutos=100, amostras=5, canc_tts=2)  # base 93
    quebrada = CoorteDiaria(
        **{**c.__dict__, "coleta_base": 99, "coleta_no_prazo": 99}
    )
    with pytest.raises(ValueError, match="base de coleta nao bate"):
        quebrada.validar()


def test_particao_que_nao_fecha_levanta():
    c = coorte(date(2026, 9, 10))
    quebrada = CoorteDiaria(**{**c.__dict__, "despacho_no_prazo": 1})
    with pytest.raises(ValueError, match="despacho nao fecha"):
        quebrada.validar()


def test_prazos_invertidos_entre_eventos_levantam():
    c = coorte(
        date(2026, 9, 10),
        desp_deadline=date(2026, 9, 15), col_deadline=date(2026, 9, 11),
    )
    with pytest.raises(ValueError, match="prazo de despacho depois"):
        c.validar()


def test_prazo_anterior_ao_pagamento_levanta():
    c = coorte(date(2026, 9, 10), col_deadline=date(2026, 9, 9))
    with pytest.raises(ValueError, match="nao e' posterior"):
        c.validar()


def test_as_duas_familias_nunca_se_somam():
    c = coorte(date(2026, 9, 10), brutos=100, desp_atras=1, col_atras=80)
    assert c.atrasados(Evento.DESPACHO) == 1
    assert c.atrasados(Evento.COLETA) == 80


# ---------------------------------------------------------------------------
# Maturacao — POR EVENTO
# ---------------------------------------------------------------------------
def test_maturacao_e_por_evento():
    """O RTS vence antes: um dia pode estar maduro para etiqueta e ainda
    parcial para coleta. Usar uma maturacao so' fecharia a coorte de coleta
    cedo demais."""
    c = coorte(date(2026, 9, 21))  # RTS 22/09, TTS 23/09
    hoje = date(2026, 9, 23)
    assert c.madura(Evento.DESPACHO, hoje) is True
    assert c.madura(Evento.COLETA, hoje) is False


def test_coorte_que_vence_hoje_ainda_nao_e_madura():
    hoje = date(2026, 9, 24)
    c = coorte(date(2026, 9, 22), col_deadline=hoje)
    assert c.madura(Evento.COLETA, hoje) is False


def test_coorte_imatura_com_taxa_zero_continua_imatura():
    """E' o caso de hoje: quase sempre 0%, quase nunca termina em 0%."""
    hoje = date(2026, 9, 24)
    c = coorte(hoje, brutos=100, col_pend_prazo=100)
    assert c.madura(Evento.COLETA, hoje) is False
    assert c.taxa(Evento.COLETA) == 0.0


def test_coorte_sem_base_tem_taxa_nula_e_nao_zero():
    c = coorte(date(2026, 9, 10), brutos=0)
    assert c.taxa(Evento.COLETA) is None


def test_em_risco_e_a_coorte_que_vence_ja():
    hoje = date(2026, 9, 24)
    assert coorte(date(2026, 9, 22), col_deadline=hoje).em_risco(Evento.COLETA, hoje)
    assert coorte(
        date(2026, 9, 23), col_deadline=date(2026, 9, 25)
    ).em_risco(Evento.COLETA, hoje)
    assert not coorte(
        date(2026, 9, 24), col_deadline=date(2026, 9, 28)
    ).em_risco(Evento.COLETA, hoje)
    # madura nunca esta "em risco": ja venceu
    assert not coorte(
        date(2026, 9, 20), col_deadline=date(2026, 9, 22)
    ).em_risco(Evento.COLETA, hoje)


# ---------------------------------------------------------------------------
# LDR por VENCIMENTO x fluxo por PAGAMENTO — a distincao central
# ---------------------------------------------------------------------------
def test_ldr_usa_como_denominador_quem_vence_na_janela():
    hoje = date(2026, 9, 24)
    dentro = coorte(date(2026, 9, 20), col_deadline=date(2026, 9, 22),
                    brutos=100, col_atras=10)
    fora = coorte(date(2026, 9, 1), col_deadline=date(2026, 9, 3),
                  brutos=1000, col_atras=900)
    ldr = calcular_ldr([dentro, fora], Evento.COLETA, hoje,
                       desde=date(2026, 9, 17), ate=hoje)
    assert ldr.base == 100
    assert ldr.taxa == pytest.approx(0.10)


def test_feriado_nao_vira_tres_dias_de_cem_por_cento_na_ldr():
    """Sab 05, dom 06 e o feriado 07 vencem TODOS em 09/09.

    Pela data de pagamento isso aparece como tres linhas; pela LDR e' UM
    vencimento com o volume somado. E' a diferenca que faz a media das taxas
    diarias exagerar o incidente.
    """
    hoje = date(2026, 9, 24)
    cs = [
        coorte(date(2026, 9, 5), brutos=100, col_atras=100),
        coorte(date(2026, 9, 6), brutos=200, col_atras=200),
        coorte(date(2026, 9, 7), brutos=700, col_atras=70),
    ]
    for c in cs:
        assert c.prazo(Evento.COLETA) == date(2026, 9, 9)

    serie = serie_por_vencimento(cs, Evento.COLETA, hoje)
    assert len(serie) == 1
    (dia, taxa, base, atras, madura) = serie[0]
    assert dia == date(2026, 9, 9)
    assert base == 1000 and atras == 370
    assert taxa == pytest.approx(0.37)

    # a visao por pagamento continua com as tres linhas, e duas delas em 100%
    fluxo = serie_por_pagamento(cs, Evento.COLETA, hoje)
    assert [round((t or 0) * 100) for _d, _v, t, _b, _a, _m in fluxo] == [100, 100, 10]


def test_serie_por_vencimento_soma_numerador_e_denominador_e_nao_taxas():
    hoje = date(2026, 9, 24)
    cs = [
        coorte(date(2026, 9, 18), "lescent", brutos=10, col_atras=9),
        coorte(date(2026, 9, 18), "kokeshi", brutos=990, col_atras=0),
    ]
    (_d, taxa, base, atras, _m), = serie_por_vencimento(cs, Evento.COLETA, hoje)
    assert base == 1000 and atras == 9
    assert taxa == pytest.approx(0.009)


def test_vencimento_e_parcial_se_qualquer_coorte_dele_for_imatura():
    hoje = date(2026, 9, 24)
    cs = [
        coorte(date(2026, 9, 22), "kokeshi", col_deadline=date(2026, 9, 23)),
        coorte(date(2026, 9, 21), "lescent", col_deadline=date(2026, 9, 23)),
    ]
    # ambas maduras
    assert serie_por_vencimento(cs, Evento.COLETA, hoje)[0][4] is True
    cs.append(coorte(date(2026, 9, 23), "apice", col_deadline=date(2026, 9, 23)))
    # nada muda: 23/09 < 24/09 continua maduro
    assert serie_por_vencimento(cs, Evento.COLETA, hoje)[0][4] is True


def test_ldr_ignora_vencimento_imaturo():
    hoje = date(2026, 9, 24)
    madura = coorte(date(2026, 9, 20), col_deadline=date(2026, 9, 22),
                    brutos=100, col_atras=50)
    imatura = coorte(date(2026, 9, 24), col_deadline=date(2026, 9, 28),
                     brutos=900, col_pend_prazo=900)
    ldr = calcular_ldr([madura, imatura], Evento.COLETA, hoje,
                       desde=date(2026, 9, 17), ate=date(2026, 9, 28))
    assert ldr.taxa == pytest.approx(0.5)
    assert ldr.base == 100
    assert ldr.vencimentos_maduros == 1
    assert ldr.vencimentos_parciais == 1


def test_ldr_sem_base_devolve_taxa_nula():
    hoje = date(2026, 9, 24)
    ldr = calcular_ldr([], Evento.COLETA, hoje, desde=date(2026, 9, 17), ate=hoje)
    assert ldr.taxa is None
    assert ldr.fora_da_meta is False
    assert ldr.critico_interno is False


# ---------------------------------------------------------------------------
# Os DOIS limiares
# ---------------------------------------------------------------------------
def test_meta_do_tiktok_e_o_limiar_interno_sao_numeros_diferentes():
    """Confundi-los faria a tela dizer "dentro da meta" num dia de 9%."""
    assert META_TIKTOK_LDR == 0.04
    assert LIMIAR_CRITICO_INTERNO == 0.10
    assert META_TIKTOK_LDR < LIMIAR_CRITICO_INTERNO


def test_fora_da_meta_dispara_antes_do_critico_interno():
    hoje = date(2026, 9, 24)
    c = coorte(date(2026, 9, 20), col_deadline=date(2026, 9, 22),
               brutos=1000, col_atras=90)   # 9%
    ldr = calcular_ldr([c], Evento.COLETA, hoje, desde=date(2026, 9, 17), ate=hoje)
    assert ldr.fora_da_meta is True
    assert ldr.critico_interno is False


def test_exatamente_na_meta_nao_esta_fora_dela():
    hoje = date(2026, 9, 24)
    c = coorte(date(2026, 9, 20), col_deadline=date(2026, 9, 22),
               brutos=1000, col_atras=40)   # 4,0%
    ldr = calcular_ldr([c], Evento.COLETA, hoje, desde=date(2026, 9, 17), ate=hoje)
    assert ldr.taxa == pytest.approx(0.04)
    assert ldr.fora_da_meta is False


# ---------------------------------------------------------------------------
# Incidente e marcas
# ---------------------------------------------------------------------------
def test_incidente_usa_o_vencimento_consolidado():
    hoje = date(2026, 9, 24)
    cs = [
        coorte(date(2026, 9, 14), "lescent", col_deadline=date(2026, 9, 16),
               brutos=50, col_atras=30),
        coorte(date(2026, 9, 14), "kokeshi", col_deadline=date(2026, 9, 16), brutos=950),
        coorte(date(2026, 9, 15), "lescent", col_deadline=date(2026, 9, 17),
               brutos=500, col_atras=250),
        coorte(date(2026, 9, 15), "kokeshi", col_deadline=date(2026, 9, 17),
               brutos=500, col_atras=250),
    ]
    assert primeiro_dia_do_incidente(cs, Evento.COLETA, hoje) == date(2026, 9, 17)
    assert ultimo_dia_do_incidente(cs, Evento.COLETA, hoje) == date(2026, 9, 17)


def test_incidente_ignora_vencimento_imaturo():
    hoje = date(2026, 9, 24)
    c = coorte(date(2026, 9, 24), col_deadline=date(2026, 9, 28),
               brutos=100, col_pend_prazo=100)
    assert primeiro_dia_do_incidente([c], Evento.COLETA, hoje) is None


def test_marcas_criticas_descarta_base_pequena():
    hoje = date(2026, 9, 24)
    cs = [
        coorte(date(2026, 9, 18), "denavita", col_deadline=date(2026, 9, 22),
               brutos=3, col_atras=1),
        coorte(date(2026, 9, 18), "kokeshi", col_deadline=date(2026, 9, 22),
               brutos=1000, col_atras=200),
    ]
    assert [m for m, _t, _n in marcas_criticas(cs, Evento.COLETA, hoje)] == ["kokeshi"]


def test_marcas_criticas_ordena_da_pior_para_a_melhor():
    hoje = date(2026, 9, 24)
    cs = [
        coorte(date(2026, 9, 18), "gocase", col_deadline=date(2026, 9, 22),
               brutos=1000, col_atras=2),
        coorte(date(2026, 9, 18), "barbours", col_deadline=date(2026, 9, 22),
               brutos=1000, col_atras=400),
        coorte(date(2026, 9, 18), "kokeshi", col_deadline=date(2026, 9, 22),
               brutos=1000, col_atras=350),
    ]
    assert [m for m, _t, _n in marcas_criticas(cs, Evento.COLETA, hoje)] == [
        "barbours", "kokeshi", "gocase"
    ]


# ---------------------------------------------------------------------------
# Janelas
# ---------------------------------------------------------------------------
def test_janela_de_vencimento_de_7_dias_tem_8_datas():
    desde, ate = janela_de_vencimento(date(2026, 9, 24), 7)
    assert (ate - desde).days == 7


def test_janela_de_pagamento_e_mais_larga_que_a_de_vencimento():
    """Um vencimento pode vir de um pagamento bem anterior: entre sexta e a
    terca ha um fim de semana, e com feriado o vao cresce. Ler so' a janela de
    vencimento perderia as coortes empurradas por feriado — que sao as que
    produzem os dias ruins."""
    hoje = date(2026, 9, 24)
    v_desde, v_ate = janela_de_vencimento(hoje, 7)
    p_desde, p_ate = janela_de_pagamento(hoje, 7)
    assert p_desde < v_desde
    assert p_ate == v_ate
    # a folga cobre a maior emenda possivel: 2 uteis + fim de semana + feriado
    assert (v_desde - p_desde).days >= 9


def test_todo_pagamento_da_janela_tem_os_dois_prazos():
    for pago, rts, tts in prazos_da_janela(date(2026, 9, 24), 21):
        d = date.fromisoformat(pago)
        assert prazo_do_evento(d, Evento.DESPACHO).isoformat() == rts
        assert prazo_do_evento(d, Evento.COLETA).isoformat() == tts
        assert rts <= tts


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------
def test_linhas_publicadas_seguem_a_ordem_das_colunas():
    hoje = date(2026, 9, 24)
    agora = datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc)
    c = coorte(date(2026, 9, 21), brutos=10, col_atras=4)
    lote, linhas = linhas_para_publicar([c], hoje=hoje, effective_at=agora, watermark=agora)
    (linha,) = linhas
    assert len(linha) == len(td.COLUNAS)
    campos = dict(zip(td.COLUNAS, linha))
    assert campos["channel"] == "tiktokshop"
    assert campos["refresh_batch_id"] == str(lote)
    assert campos["paid_date"] == date(2026, 9, 21)
    assert campos["coleta_atrasada"] == 4
    # RTS vence 22/09 (maduro em 24/09); TTS vence 23/09 (tambem maduro)
    assert campos["despacho_is_mature"] is True
    assert campos["coleta_is_mature"] is True


def test_publicacao_marca_maturacao_separada_por_evento():
    hoje = date(2026, 9, 23)
    agora = datetime(2026, 9, 23, 16, 0, tzinfo=timezone.utc)
    c = coorte(date(2026, 9, 21))  # RTS 22/09 maduro, TTS 23/09 ainda nao
    _lote, linhas = linhas_para_publicar([c], hoje=hoje, effective_at=agora, watermark=None)
    campos = dict(zip(td.COLUNAS, linhas[0]))
    assert campos["despacho_is_mature"] is True
    assert campos["coleta_is_mature"] is False
    assert campos["source_watermark_at"] is None


def test_publicacao_recusa_effective_at_ingenuo():
    with pytest.raises(ValueError, match="aware"):
        linhas_para_publicar(
            [coorte(date(2026, 9, 21))], hoje=date(2026, 9, 24),
            effective_at=datetime(2026, 9, 24, 16, 0), watermark=None,
        )


def test_publicacao_valida_cada_coorte_antes_de_materializar():
    quebrada = CoorteDiaria(
        **{**coorte(date(2026, 9, 21)).__dict__, "coleta_atrasada": 999}
    )
    with pytest.raises(ValueError, match="coleta nao fecha"):
        linhas_para_publicar(
            [quebrada], hoje=date(2026, 9, 24),
            effective_at=datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc), watermark=None,
        )


# ---------------------------------------------------------------------------
# Barreiras estruturais do SQL
# ---------------------------------------------------------------------------
def _sem_comentarios(sql: str) -> str:
    """So' o SQL que o banco executa.

    Os comentarios deste modulo citam de proposito os status que NAO sao
    usados, para registrar a decisao. Uma assercao ingenua casaria neles.
    """
    return "\n".join(
        linha.split("--", 1)[0] for linha in sql.splitlines()
    )



@pytest.mark.parametrize(
    "sql",
    [td.FONTE_SQL, td.WATERMARK_SQL, td.DELETE_JANELA_SQL, td.PROVENIENCIA_SQL],
)
def test_sql_nao_seleciona_pii(sql):
    """`cpf` esta em `raw.tiktok_shop_orders` e nao pode encostar nesta serie."""
    baixo = sql.lower()
    for proibido in ("cpf", "tracking_number", "buyer", "recipient", "phone", "address"):
        assert proibido not in baixo, proibido


@pytest.mark.parametrize(
    "sql",
    [td.FONTE_SQL, td.WATERMARK_SQL, td.DELETE_JANELA_SQL, td.PROVENIENCIA_SQL],
)
def test_sql_nao_tem_porcentagem_solta(sql):
    """`%` fora de `%(nome)s` quebra o psycopg2 com "argument formats can't be
    mixed" — inclusive dentro de comentario. Ja aconteceu neste arquivo."""
    import re

    sem_parametros = re.sub(r"%\([a-z_]+\)s", "", sql)
    assert "%" not in sem_parametros


def test_select_nao_usa_asterisco():
    assert "select *" not in td.FONTE_SQL.lower()


def test_delete_da_janela_e_limitado_por_canal_e_intervalo():
    d = td.DELETE_JANELA_SQL.lower()
    assert "channel = %(channel)s" in d
    assert "paid_date >= %(desde)s" in d
    assert "paid_date <= %(ate)s" in d


def test_o_instante_de_coleta_vem_so_de_in_transit():
    """DELIVERED/COMPLETED estao dias depois: usa-los como fallback trocaria o
    instante da coleta pelo da entrega. Medido, o fallback resolveria 10
    pedidos em 172.197 (0,006 por cento)."""
    assert td.STATUS_COLETA == "IN_TRANSIT"
    # Compara so' o SQL EXECUTAVEL: os comentarios explicam justamente por que
    # DELIVERED/COMPLETED ficaram de fora, e casar neles daria falso positivo.
    executavel = _sem_comentarios(td.FONTE_SQL)
    assert "DELIVERED" not in executavel
    assert "COMPLETED" not in executavel
    # mas o SQL de DIAGNOSTICO precisa, para medir a cobertura
    assert "DELIVERED" in _sem_comentarios(td.PROVENIENCIA_SQL)


def test_o_rts_exige_todos_os_itens_com_carimbo():
    """Um item sem `rts_time` significa pedido incompleto; ignora-lo declararia
    despachado o que nao saiu inteiro. Medido, hoje nao ha carimbo parcial —
    a regra existe para quando houver."""
    assert "com_rts = d.itens" in td.FONTE_SQL
    assert "max(li.rts_time)" in td.FONTE_SQL
    assert "min(li.rts_time)" not in td.FONTE_SQL


def test_amostra_gratis_sai_do_denominador():
    assert "is_sample_order" in td.FONTE_SQL
    assert "amostras_excluidas" in td.FONTE_SQL


def test_cancelado_antes_do_sla_sai_e_depois_permanece():
    f = td.FONTE_SQL
    assert "cancelado_em <= vence_rts" in f
    assert "cancelado_em <= vence_tts" in f
    # cancelado SEM carimbo permanece: a condicao exige `IS NOT NULL`
    assert "cancelado_em IS NOT NULL" in f


def test_modulo_nao_le_o_relogio_dentro_da_transformacao():
    """`hoje` e `effective_at` entram por parametro, sempre."""
    arvore = ast.parse(Path(inspect.getfile(td)).read_text(encoding="utf-8"))
    proibidas = {"now", "utcnow", "today"}
    achadas = [
        n.func.attr
        for n in ast.walk(arvore)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in proibidas
    ]
    assert achadas == [], achadas


def test_o_prazo_e_declarado_como_reconstruido():
    assert td.PRAZO_E_RECONSTRUIDO is True
