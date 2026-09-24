"""Gate EXP-TK-OPS-1 — regras da serie diaria de atraso do TikTok Shop.

Estes testes exercitam a LOGICA PURA: prazo em dias uteis, feriado, maturacao,
fechamento do grao e as duas leituras da janela. Nada aqui toca banco — a
extracao contra a fonte real foi reconciliada separadamente contra um SQL
independente (mesmos 21 dias, mesmos numeros) e esta registrada no relatorio
do gate.

O foco e' o que quebra em silencio: um feriado esquecido nao levanta excecao,
ele so' devolve uma taxa errada; uma coorte imatura contada como madura nao
falha, ela so' mostra 0% num dia que vai terminar em 80%.
"""
from __future__ import annotations

import ast
import inspect
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipelines.expedicao import tiktok_daily as td
from pipelines.expedicao.tiktok_daily import (
    CoorteDiaria,
    Evento,
    ForaDaCoberturaDeFeriados,
    agregar_janela,
    dia_util,
    hoje_brt,
    linhas_para_publicar,
    marcas_criticas,
    prazo_de,
    prazos_da_janela,
    primeiro_dia_do_incidente,
    serie_por_dia,
)


# ---------------------------------------------------------------------------
# Auxiliar
# ---------------------------------------------------------------------------
def coorte(
    paid: date,
    brand: str = "kokeshi",
    *,
    pagos: int = 100,
    desp_prazo: int | None = None,
    desp_atras: int = 0,
    desp_pend_venc: int = 0,
    desp_pend_prazo: int = 0,
    col_prazo: int | None = None,
    col_atras: int = 0,
    col_pend_venc: int = 0,
    col_pend_prazo: int = 0,
    cancelados: int = 0,
    deadline: date | None = None,
) -> CoorteDiaria:
    """Coorte que FECHA por construcao: o "no prazo" absorve o resto."""
    d = deadline or prazo_de(paid)
    return CoorteDiaria(
        paid_date=paid,
        brand=brand,
        shop_name=brand.upper(),
        deadline_date=d,
        pedidos_pagos=pagos,
        cancelados=cancelados,
        despacho_no_prazo=(
            pagos - desp_atras - desp_pend_venc - desp_pend_prazo
            if desp_prazo is None else desp_prazo
        ),
        despacho_atrasado=desp_atras,
        despacho_pendente_vencido=desp_pend_venc,
        despacho_pendente_no_prazo=desp_pend_prazo,
        coleta_no_prazo=(
            pagos - col_atras - col_pend_venc - col_pend_prazo
            if col_prazo is None else col_prazo
        ),
        coleta_atrasada=col_atras,
        coleta_pendente_vencida=col_pend_venc,
        coleta_pendente_no_prazo=col_pend_prazo,
    )


# ---------------------------------------------------------------------------
# Prazo em dias uteis
# ---------------------------------------------------------------------------
def test_prazo_pula_o_fim_de_semana():
    # 2026-09-03 e' quinta. +2 uteis = segunda 07/09... que e' FERIADO, entao
    # cai em 08/09 (terca).
    assert prazo_de(date(2026, 9, 3)) == date(2026, 9, 8)
    # 2026-09-01 e' terca; +2 uteis = quinta 03/09, sem fim de semana no meio.
    assert prazo_de(date(2026, 9, 1)) == date(2026, 9, 3)


def test_prazo_de_sexta_cai_na_terca_quando_nao_ha_feriado():
    # 2026-09-18 e' sexta. Sabado e domingo nao contam: seg 21 = 1o, ter 22 = 2o.
    assert prazo_de(date(2026, 9, 18)) == date(2026, 9, 22)


def test_pagar_no_fim_de_semana_nao_consome_prazo():
    # Sabado 05/09, domingo 06/09 e o feriado 07/09 vencem todos no MESMO dia:
    # a contagem so' comeca no proximo dia util (08/09 = 1o, 09/09 = 2o).
    assert prazo_de(date(2026, 9, 5)) == date(2026, 9, 9)
    assert prazo_de(date(2026, 9, 6)) == date(2026, 9, 9)
    assert prazo_de(date(2026, 9, 7)) == date(2026, 9, 9)


def test_feriado_nacional_nao_conta_como_dia_util():
    assert dia_util(date(2026, 9, 7)) is False      # Independencia, segunda
    assert dia_util(date(2026, 9, 8)) is True       # terca seguinte
    assert dia_util(date(2026, 4, 3)) is False      # Sexta-feira Santa
    assert dia_util(date(2026, 2, 17)) is False     # Carnaval (terca)
    assert dia_util(date(2026, 11, 20)) is False    # Consciencia Negra


def test_natal_empurra_o_prazo_para_depois_do_fim_de_semana():
    # 24/12/2026 e' quinta. 25 (Natal, sexta) nao conta, 26-27 e' fim de
    # semana: seg 28 = 1o util, ter 29 = 2o.
    assert prazo_de(date(2026, 12, 24)) == date(2026, 12, 29)


def test_prazo_e_sempre_posterior_ao_pagamento():
    d = date(2026, 1, 1)
    while d < date(2027, 12, 1):
        assert prazo_de(d) > d
        d += timedelta(days=1)


def test_data_fora_da_cobertura_de_feriados_levanta():
    # Silencio aqui seria um prazo errado publicado como se fosse certo.
    with pytest.raises(ForaDaCoberturaDeFeriados):
        dia_util(date(2028, 1, 3))
    with pytest.raises(ForaDaCoberturaDeFeriados):
        dia_util(date(2024, 12, 31))


def test_cobertura_de_feriados_cobre_a_janela_que_o_prazo_alcanca():
    # `prazo_de` no ultimo dia coberto anda para frente e nao pode sair da
    # lista. Se este teste quebrar, e' hora de acrescentar o ano seguinte.
    ultimo = td.FERIADOS_COBERTURA[1] - timedelta(days=10)
    assert prazo_de(ultimo) <= td.FERIADOS_COBERTURA[1]


def test_todo_feriado_declarado_esta_dentro_da_cobertura():
    ini, fim = td.FERIADOS_COBERTURA
    for f in td.FERIADOS_NACIONAIS:
        assert ini <= f <= fim, f


# ---------------------------------------------------------------------------
# Fuso
# ---------------------------------------------------------------------------
def test_hoje_brt_vira_o_dia_tres_horas_antes_do_utc():
    # 00:30 UTC ainda e' o dia anterior em Brasilia. Errar isto joga a coorte
    # da madrugada para o dia errado.
    assert hoje_brt(datetime(2026, 9, 24, 0, 30, tzinfo=timezone.utc)) == date(2026, 9, 23)
    assert hoje_brt(datetime(2026, 9, 24, 3, 30, tzinfo=timezone.utc)) == date(2026, 9, 24)


def test_hoje_brt_recusa_datetime_ingenuo():
    with pytest.raises(ValueError):
        hoje_brt(datetime(2026, 9, 24, 12, 0))


# ---------------------------------------------------------------------------
# Fechamento do grao
# ---------------------------------------------------------------------------
def test_particao_que_nao_fecha_levanta():
    c = CoorteDiaria(
        paid_date=date(2026, 9, 10), brand="kokeshi", shop_name="K",
        deadline_date=date(2026, 9, 14), pedidos_pagos=100, cancelados=0,
        despacho_no_prazo=50, despacho_atrasado=10,
        despacho_pendente_vencido=0, despacho_pendente_no_prazo=0,
        coleta_no_prazo=100, coleta_atrasada=0,
        coleta_pendente_vencida=0, coleta_pendente_no_prazo=0,
    )
    with pytest.raises(ValueError, match="despacho nao fecha"):
        c.validar()


def test_coleta_que_nao_fecha_levanta_mesmo_com_despacho_correto():
    c = coorte(date(2026, 9, 10))
    quebrada = CoorteDiaria(**{**c.__dict__, "coleta_no_prazo": c.coleta_no_prazo - 1})
    with pytest.raises(ValueError, match="coleta nao fecha"):
        quebrada.validar()


def test_prazo_anterior_ao_pagamento_levanta():
    c = coorte(date(2026, 9, 10), deadline=date(2026, 9, 9))
    with pytest.raises(ValueError, match="nao e' posterior"):
        c.validar()


def test_as_duas_familias_nunca_se_somam():
    # O mesmo pedido e' contado uma vez em despacho e uma vez em coleta. Somar
    # as duas daria 2x o total de pedidos.
    c = coorte(date(2026, 9, 10), pagos=100, desp_atras=1, col_atras=80)
    soma_tudo = (
        c.despacho_no_prazo + c.despacho_atrasado + c.despacho_pendente_vencido
        + c.despacho_pendente_no_prazo + c.coleta_no_prazo + c.coleta_atrasada
        + c.coleta_pendente_vencida + c.coleta_pendente_no_prazo
    )
    assert soma_tudo == 2 * c.pedidos_pagos
    assert c.atrasados(Evento.DESPACHO) == 1
    assert c.atrasados(Evento.COLETA) == 80


# ---------------------------------------------------------------------------
# Maturacao — o requisito de "nunca mostrar 0% que ainda nao amadureceu"
# ---------------------------------------------------------------------------
def test_coorte_de_hoje_nao_e_madura():
    hoje = date(2026, 9, 24)
    c = coorte(hoje, pagos=1700, col_pend_prazo=1700)
    assert c.madura(hoje) is False
    # e' exatamente o caso perigoso: taxa 0% num dia que nao terminou
    assert c.taxa(Evento.COLETA) == 0.0


def test_coorte_vence_hoje_ainda_nao_e_madura():
    # Vencer HOJE nao fecha a coorte: ainda ha o dia inteiro para despachar.
    hoje = date(2026, 9, 24)
    c = coorte(date(2026, 9, 22), deadline=hoje)
    assert c.madura(hoje) is False


def test_coorte_vencida_ontem_e_madura():
    hoje = date(2026, 9, 24)
    c = coorte(date(2026, 9, 21), deadline=date(2026, 9, 23))
    assert c.madura(hoje) is True


def test_coorte_em_risco_e_a_que_vence_ja():
    hoje = date(2026, 9, 24)
    vence_hoje = coorte(date(2026, 9, 22), deadline=hoje)
    vence_amanha = coorte(date(2026, 9, 23), deadline=date(2026, 9, 25))
    vence_depois = coorte(date(2026, 9, 24), deadline=date(2026, 9, 28))
    assert vence_hoje.em_risco(hoje) is True
    assert vence_amanha.em_risco(hoje) is True
    assert vence_depois.em_risco(hoje) is False


def test_janela_ignora_coortes_imaturas_por_padrao():
    hoje = date(2026, 9, 24)
    madura = coorte(date(2026, 9, 21), deadline=date(2026, 9, 23), pagos=100, col_atras=50)
    imatura = coorte(date(2026, 9, 24), deadline=date(2026, 9, 28), pagos=900,
                     col_pend_prazo=900)
    j = agregar_janela([madura, imatura], Evento.COLETA, hoje)
    # 50/100 = 50%. Incluir a imatura daria 50/1000 = 5% e esconderia o incidente.
    assert j.razao_dos_totais == pytest.approx(0.50)
    assert j.coortes_maduras == 1
    assert j.coortes_parciais == 1
    assert agregar_janela(
        [madura, imatura], Evento.COLETA, hoje, somente_maduras=False
    ).razao_dos_totais == pytest.approx(0.05)


def test_coorte_sem_pedido_pago_tem_taxa_nula_e_nao_zero():
    # Zero entraria numa media como "dia perfeito" e puxaria a taxa para baixo.
    c = coorte(date(2026, 9, 10), pagos=0)
    assert c.taxa(Evento.COLETA) is None
    j = agregar_janela([c], Evento.COLETA, date(2026, 9, 24))
    assert j.razao_dos_totais is None
    assert j.media_das_diarias is None


# ---------------------------------------------------------------------------
# As duas leituras da janela
# ---------------------------------------------------------------------------
def test_as_duas_leituras_divergem_quando_o_volume_diario_varia():
    """A media das diarias trata um dia de 10 pedidos como um dia de 10.000.

    Medido na janela real de 2026-09-24: razao 2,28% contra media 5,24%, ou
    seja 2,95 pp de diferenca. Publicar so' uma seria escolher sem prova.
    """
    hoje = date(2026, 9, 24)
    grande = coorte(date(2026, 9, 14), deadline=date(2026, 9, 16), pagos=10_000, col_atras=100)
    pequeno = coorte(date(2026, 9, 15), deadline=date(2026, 9, 17), pagos=10, col_atras=5)
    j = agregar_janela([grande, pequeno], Evento.COLETA, hoje)
    assert j.razao_dos_totais == pytest.approx(105 / 10_010)      # ~1,05%
    assert j.media_das_diarias == pytest.approx((0.01 + 0.50) / 2)  # 25,5%
    assert j.divergencia_pp is not None and j.divergencia_pp < -20


def test_media_das_diarias_e_por_DIA_e_nao_por_coorte():
    """Cada DIA vale um ponto, nao cada (dia, marca).

    Tirar a media das coortes cruas da outro numero, porque uma marca pequena
    pesaria igual a uma grande. Medido na janela real: 5,24% pela media por
    coorte contra 2,25% pela media por dia. Como a tela publica as duas
    leituras para reconciliar com a planilha, o CLI tem de calcular o mesmo.
    """
    hoje = date(2026, 9, 24)
    # Um unico dia, duas marcas: 100% numa marca de 10 pedidos e 0% numa de
    # 990. A taxa DO DIA e' 1%. A media por coorte daria 50%.
    coortes = [
        coorte(date(2026, 9, 14), "lescent", deadline=date(2026, 9, 16),
               pagos=10, col_atras=10),
        coorte(date(2026, 9, 14), "kokeshi", deadline=date(2026, 9, 16), pagos=990),
    ]
    j = agregar_janela(coortes, Evento.COLETA, hoje)
    assert j.media_das_diarias == pytest.approx(0.01)
    assert j.razao_dos_totais == pytest.approx(0.01)


def test_as_duas_leituras_coincidem_com_volume_uniforme():
    hoje = date(2026, 9, 24)
    a = coorte(date(2026, 9, 14), deadline=date(2026, 9, 16), pagos=100, col_atras=10)
    b = coorte(date(2026, 9, 15), deadline=date(2026, 9, 17), pagos=100, col_atras=30)
    j = agregar_janela([a, b], Evento.COLETA, hoje)
    assert j.razao_dos_totais == pytest.approx(j.media_das_diarias)
    assert j.divergencia_pp == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Consolidacao e leitura do incidente
# ---------------------------------------------------------------------------
def test_serie_por_dia_soma_numerador_e_denominador_e_nao_taxas():
    hoje = date(2026, 9, 24)
    # 90% de 10 pedidos e 0% de 990: a taxa do dia e' 0,9%, nao 45%.
    pequena = coorte(date(2026, 9, 14), "lescent", deadline=date(2026, 9, 16),
                     pagos=10, col_atras=9)
    grande = coorte(date(2026, 9, 14), "kokeshi", deadline=date(2026, 9, 16),
                    pagos=990, col_atras=0)
    (dia, taxa, pagos, madura), = serie_por_dia([pequena, grande], Evento.COLETA, hoje)
    assert dia == date(2026, 9, 14)
    assert pagos == 1000
    assert taxa == pytest.approx(0.009)
    assert madura is True


def test_serie_por_dia_marca_o_dia_como_parcial_se_qualquer_marca_for_imatura():
    hoje = date(2026, 9, 24)
    madura = coorte(date(2026, 9, 22), "kokeshi", deadline=date(2026, 9, 23))
    imatura = coorte(date(2026, 9, 22), "lescent", deadline=date(2026, 9, 24))
    (_d, _t, _p, mad), = serie_por_dia([madura, imatura], Evento.COLETA, hoje)
    assert mad is False


def test_incidente_usa_o_dia_consolidado_e_nao_a_marca_isolada():
    """Uma marca ruim num dia calmo nao e' o inicio do incidente."""
    hoje = date(2026, 9, 24)
    coortes = [
        # dia 1: uma marca pequena em 60%, carteira em 3% -> NAO e' o inicio
        coorte(date(2026, 9, 14), "lescent", deadline=date(2026, 9, 16),
               pagos=50, col_atras=30),
        coorte(date(2026, 9, 14), "kokeshi", deadline=date(2026, 9, 16), pagos=950),
        # dia 2: carteira em 50% -> ESTE e' o inicio
        coorte(date(2026, 9, 15), "lescent", deadline=date(2026, 9, 17),
               pagos=500, col_atras=250),
        coorte(date(2026, 9, 15), "kokeshi", deadline=date(2026, 9, 17),
               pagos=500, col_atras=250),
    ]
    assert primeiro_dia_do_incidente(coortes, Evento.COLETA, hoje) == date(2026, 9, 15)


def test_incidente_ignora_coorte_imatura():
    hoje = date(2026, 9, 24)
    c = coorte(date(2026, 9, 24), deadline=date(2026, 9, 28), pagos=100, col_pend_prazo=100)
    assert primeiro_dia_do_incidente([c], Evento.COLETA, hoje) is None


def test_sem_incidente_devolve_none():
    hoje = date(2026, 9, 24)
    c = coorte(date(2026, 9, 14), deadline=date(2026, 9, 16), pagos=1000, col_atras=5)
    assert primeiro_dia_do_incidente([c], Evento.COLETA, hoje) is None


def test_marcas_criticas_descarta_base_pequena():
    """1 atraso em 3 pedidos e' 33% e nao significa nada operacionalmente."""
    hoje = date(2026, 9, 24)
    ruidosa = coorte(date(2026, 9, 14), "denavita", deadline=date(2026, 9, 16),
                     pagos=3, col_atras=1)
    real = coorte(date(2026, 9, 14), "kokeshi", deadline=date(2026, 9, 16),
                  pagos=1000, col_atras=200)
    ranking = marcas_criticas([ruidosa, real], Evento.COLETA, hoje)
    assert [m for m, _t, _n in ranking] == ["kokeshi"]


def test_marcas_criticas_ordena_da_pior_para_a_melhor():
    hoje = date(2026, 9, 24)
    cs = [
        coorte(date(2026, 9, 14), "gocase", deadline=date(2026, 9, 16), pagos=1000, col_atras=2),
        coorte(date(2026, 9, 14), "barbours", deadline=date(2026, 9, 16), pagos=1000, col_atras=400),
        coorte(date(2026, 9, 14), "kokeshi", deadline=date(2026, 9, 16), pagos=1000, col_atras=350),
    ]
    assert [m for m, _t, _n in marcas_criticas(cs, Evento.COLETA, hoje)] == [
        "barbours", "kokeshi", "gocase"
    ]


# ---------------------------------------------------------------------------
# Janela e publicacao
# ---------------------------------------------------------------------------
def test_janela_de_7_dias_tem_8_coortes():
    """"7 dias + hoje" sao 8 datas de pagamento, nao 7."""
    p = prazos_da_janela(date(2026, 9, 24), 7)
    assert len(p) == 8
    assert p[0][0] == "2026-09-17"
    assert p[-1][0] == "2026-09-24"


def test_prazos_da_janela_casam_com_prazo_de():
    for pago, vence in prazos_da_janela(date(2026, 9, 24), 21):
        assert prazo_de(date.fromisoformat(pago)).isoformat() == vence


def test_linhas_publicadas_seguem_a_ordem_das_colunas():
    hoje = date(2026, 9, 24)
    agora = datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc)
    c = coorte(date(2026, 9, 21), deadline=date(2026, 9, 23), pagos=10, col_atras=4)
    lote, linhas = linhas_para_publicar([c], hoje=hoje, effective_at=agora, watermark=agora)
    assert len(linhas) == 1
    (linha,) = linhas
    assert len(linha) == len(td.COLUNAS)
    campos = dict(zip(td.COLUNAS, linha))
    assert campos["channel"] == "tiktokshop"
    assert campos["refresh_batch_id"] == str(lote)
    assert campos["paid_date"] == date(2026, 9, 21)
    assert campos["is_mature"] is True
    assert campos["coleta_atrasada"] == 4
    assert campos["effective_at"] == agora


def test_publicacao_marca_coorte_imatura_como_parcial():
    hoje = date(2026, 9, 24)
    agora = datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc)
    c = coorte(hoje, deadline=date(2026, 9, 28), pagos=10, col_pend_prazo=10)
    _lote, linhas = linhas_para_publicar([c], hoje=hoje, effective_at=agora, watermark=None)
    campos = dict(zip(td.COLUNAS, linhas[0]))
    assert campos["is_mature"] is False
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
# Barreiras estruturais
# ---------------------------------------------------------------------------
def _fonte() -> str:
    return Path(inspect.getfile(td)).read_text(encoding="utf-8")


@pytest.mark.parametrize("sql", [td.FONTE_SQL, td.WATERMARK_SQL, td.DELETE_JANELA_SQL])
def test_sql_nao_seleciona_pii(sql):
    """`cpf` esta em `raw.tiktok_shop_orders` e nao pode encostar nesta serie.

    `tracking_number` e `order_id` tambem ficam de fora do que sai: o grao
    publicado e' (dia, marca) e nenhum identificador individual atravessa.
    """
    baixo = sql.lower()
    for proibido in ("cpf", "tracking_number", "buyer", "recipient", "phone", "address"):
        assert proibido not in baixo, proibido


def test_select_nao_usa_asterisco():
    assert "select *" not in td.FONTE_SQL.lower()


def test_delete_da_janela_e_sempre_limitado_por_canal_e_intervalo():
    """DELETE sem as tres condicoes apagaria a serie historica inteira."""
    d = td.DELETE_JANELA_SQL.lower()
    assert "where" in d
    assert "channel = %(channel)s" in d
    assert "paid_date >= %(desde)s" in d
    assert "paid_date <= %(ate)s" in d


def test_modulo_nao_le_o_relogio_dentro_da_transformacao():
    """`hoje` e `effective_at` entram por parametro, sempre.

    Relogio lido no meio da transformacao faz duas coortes da mesma execucao
    serem classificadas contra instantes diferentes.
    """
    arvore = ast.parse(_fonte())
    proibidas = {"now", "utcnow", "today"}
    achadas = [
        n.func.attr
        for n in ast.walk(arvore)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in proibidas
    ]
    assert achadas == [], achadas


def test_o_prazo_e_declarado_como_reconstruido():
    """O contrato precisa dizer, no codigo, que o prazo nao e' o do TikTok."""
    assert td.PRAZO_E_RECONSTRUIDO is True


def test_cancelado_fica_fora_do_denominador():
    assert "CANCELLED" in td.STATUS_EXCLUIDOS


def test_status_pos_coleta_nao_inclui_awaiting_collection():
    """AWAITING_COLLECTION e' o DESPACHO, nao a coleta.

    Medido: a transicao para AWAITING_COLLECTION coincide com `rts_time`
    (mediana 0,00 h), enquanto IN_TRANSIT vem 44,93 h depois. Incluir
    AWAITING_COLLECTION aqui colapsaria os dois eventos num so' e apagaria o
    incidente de coleta.
    """
    assert "AWAITING_COLLECTION" not in td.STATUS_POS_COLETA
    assert set(td.STATUS_POS_COLETA) == {"IN_TRANSIT", "DELIVERED", "COMPLETED"}
