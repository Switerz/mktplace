"""Gate EXP-TK-OPS-2 — o artefato de reconciliacao e as regras candidatas.

Estes testes NAO tocam banco. Eles cobrem a parte que pode apodrecer em
silencio: a serie versionada da planilha da gestao e as tres regras de prazo
que o script compara contra ela.

A comparacao contra a fonte real vive em
`pipelines/reconciliation/tiktok_ldr_planilha.py` e precisa do Data Mart.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipelines.expedicao.tiktok_daily import (
    FERIADOS_NACIONAIS,
    PRAZO_DIAS_UTEIS,
    Evento,
    prazo_do_evento,
)
from pipelines.reconciliation import tiktok_ldr_planilha as rec

REF = rec.carregar_referencia()

#: Rodape da imagem enviada pelo gestor. Nao esta no CSV de proposito — e'
#: derivado das linhas, e guardar os dois criaria duas fontes para o mesmo
#: numero. Fica aqui para que o teste prove que as linhas reproduzem o rodape.
RODAPE_DA_IMAGEM = {
    "paid_orders": 23317,
    "shipped_within_sla": 15258,
    "shipped_after_sla": 7668,
    "not_shipped_overdue": 140,
    "not_shipped_on_time": 251,
}


# ---------------------------------------------------------------------------
# O artefato versionado
# ---------------------------------------------------------------------------
def test_a_referencia_existe_e_foi_transcrita_inteira():
    # 01/09 a 23/09 = 23 dias, sem buraco. Linha faltando significaria que algo
    # da captura nao foi transcrito — e o `.md` diz que nada foi deduzido.
    assert len(REF) == 23
    assert REF[0].paid_date == date(2026, 9, 1)
    assert REF[-1].paid_date == date(2026, 9, 23)
    esperadas = [date(2026, 9, 1) + timedelta(days=i) for i in range(23)]
    assert [r.paid_date for r in REF] == esperadas


def test_cada_dia_da_referencia_fecha():
    """As quatro colunas tem de somar o total de pagos.

    Erro de transcricao aparece aqui, e nao numa taxa que parece plausivel.
    """
    for r in REF:
        assert r.fecha(), r.paid_date


def test_as_linhas_reproduzem_o_rodape_da_imagem():
    for campo, esperado in RODAPE_DA_IMAGEM.items():
        obtido = sum(getattr(r, campo) for r in REF)
        assert obtido == esperado, f"{campo}: {obtido} != {esperado}"


def test_a_media_dos_ultimos_8_dias_nao_e_reproduzivel_com_exatidao():
    """A imagem mostra 1,09%; a coluna arredondada da' 1,125%.

    A planilha calcula a media com a taxa CHEIA e exibe so' o arredondado. Nao
    da' para reproduzir 1,09% a partir do que e' visivel — e afirmar que da'
    seria precisao falsa. O que se pode garantir e' que esta dentro da
    resolucao da propria planilha.
    """
    media = sum(r.late_rate_pct_rounded for r in REF[-8:]) / 8
    assert abs(media - 1.09) <= rec.RESOLUCAO_PP


def test_a_taxa_exibida_e_coerente_com_as_colunas():
    """A taxa da planilha tem de ser o numerador sobre os pagos, arredondado.

    Tolera 1 pp: a planilha exibe inteiro e nao se sabe o modo de
    arredondamento que ela usa.
    """
    for r in REF:
        if r.paid_orders == 0:
            continue
        calculada = 100.0 * r.atrasados / r.paid_orders
        assert abs(calculada - r.late_rate_pct_rounded) <= rec.RESOLUCAO_PP, r.paid_date


def test_o_numerador_e_postado_atrasado_mais_vencido_sem_postagem():
    r = next(x for x in REF if x.paid_date == date(2026, 9, 4))
    assert r.atrasados == r.shipped_after_sla + r.not_shipped_overdue == 65 + 17


def test_a_referencia_so_tem_as_colunas_agregadas_previstas():
    """Agregado por dia. Nenhum pedido, cliente, endereco ou rastreio.

    Allowlist em vez de lista de proibidos: procurar a substring "order"
    reprovaria `paid_orders`, que e' uma CONTAGEM. Fechar o conjunto pega
    tambem a coluna que ninguem pensou em proibir.
    """
    import csv

    with rec.REFERENCIA.open(encoding="utf-8", newline="") as fh:
        cabecalho = next(csv.reader(fh))
    assert cabecalho == [
        "paid_date", "paid_orders", "shipped_within_sla", "shipped_after_sla",
        "not_shipped_overdue", "not_shipped_on_time", "late_rate_pct_rounded",
    ]


def test_o_csv_so_tem_numeros_e_datas():
    import csv

    with rec.REFERENCIA.open(encoding="utf-8", newline="") as fh:
        for linha in csv.DictReader(fh):
            date.fromisoformat(linha["paid_date"])
            for chave, valor in linha.items():
                if chave == "paid_date":
                    continue
                assert valor.isdigit(), (chave, valor)


def test_a_proveniencia_esta_documentada_ao_lado():
    md = rec.REFERENCIA.with_suffix(".md")
    assert md.exists()
    texto = md.read_text(encoding="utf-8").lower()
    # o documento PRECISA dizer de onde veio e o que nao garante
    assert "captura" in texto
    assert "transcrit" in texto
    assert "barbours" in texto
    assert "24 h" in texto or "24h" in texto


# ---------------------------------------------------------------------------
# As tres regras candidatas
# ---------------------------------------------------------------------------
def test_a_regra_vigente_do_script_e_a_da_producao():
    """Se o script comparasse contra outra regra, a reconciliacao aprovaria uma
    coisa e a Torre publicaria outra."""
    assert rec.REGRA_VIGENTE == "uteis_com_feriado"
    hoje = date(2026, 9, 24)
    prazos = {p[0]: p for p in rec.prazos_para(rec.REGRA_VIGENTE, hoje, 30)}
    for pago, rts, tts in prazos.values():
        d = date.fromisoformat(pago)
        assert prazo_do_evento(d, Evento.DESPACHO).isoformat() == rts
        assert prazo_do_evento(d, Evento.COLETA).isoformat() == tts


def test_as_tres_regras_divergem_no_feriado():
    """Se as tres coincidissem, a comparacao nao discriminaria nada.

    04/09/2026 e' sexta e 07/09 e' feriado nacional:
      com feriado  -> ter 08 (1o util), qua 09 (2o)
      sem feriado  -> seg 07 (1o util), ter 08 (2o)
      corridos     -> dom 06
    """
    d = date(2026, 9, 4)
    assert rec._prazo(d, 2, considerar_feriado=True, corridos=False) == date(2026, 9, 9)
    assert rec._prazo(d, 2, considerar_feriado=False, corridos=False) == date(2026, 9, 8)
    assert rec._prazo(d, 2, considerar_feriado=False, corridos=True) == date(2026, 9, 6)


def test_a_regra_sem_feriado_ignora_exatamente_os_feriados():
    assert date(2026, 9, 7) in FERIADOS_NACIONAIS
    # sexta 04 + 1 util: com feriado pula 07, sem feriado usa 07
    assert rec._prazo(date(2026, 9, 4), 1, considerar_feriado=True, corridos=False) == date(2026, 9, 8)
    assert rec._prazo(date(2026, 9, 4), 1, considerar_feriado=False, corridos=False) == date(2026, 9, 7)


def test_as_regras_usam_o_prazo_de_cada_evento():
    """RTS 1 dia util, TTS 2. A reconciliacao nao pode achatar os dois."""
    hoje = date(2026, 9, 24)
    for _pago, rts, tts in rec.prazos_para("uteis_com_feriado", hoje, 20):
        assert rts <= tts
    assert PRAZO_DIAS_UTEIS[Evento.DESPACHO] == 1
    assert PRAZO_DIAS_UTEIS[Evento.COLETA] == 2


def test_dias_corridos_nunca_pulam_nada():
    for i in range(30):
        d = date(2026, 9, 1) + timedelta(days=i)
        assert rec._prazo(d, 2, considerar_feriado=False, corridos=True) == d + timedelta(days=2)


# ---------------------------------------------------------------------------
# Limiares do script
# ---------------------------------------------------------------------------
def test_a_resolucao_declarada_e_a_da_planilha():
    """A planilha exibe inteiro. Comparar com precisao melhor que 1 pp seria
    inventar resolucao que a referencia nao tem."""
    assert rec.RESOLUCAO_PP == 1.0


def test_o_alarme_de_regressao_e_folgado_mas_existe():
    assert rec.ERRO_MEDIO_MAXIMO_PP > rec.RESOLUCAO_PP
    assert rec.ERRO_MEDIO_MAXIMO_PP <= 3.0


def test_comparar_usa_a_taxa_da_torre_contra_a_arredondada_da_planilha():
    class _Coorte:
        def taxa(self, _evento):
            return 0.056

    ref = [r for r in REF if r.paid_date == date(2026, 9, 4)]
    d = rec.comparar(ref, {date(2026, 9, 4): _Coorte()}, "x")
    # planilha diz 6%, torre diz 5,6% -> 0,4 pp, dentro da resolucao
    assert d.dias_comparados == 1
    assert d.erro_medio_pp == pytest.approx(0.4, abs=0.01)
    assert d.dias_dentro_da_resolucao == 1


def test_dia_sem_correspondente_na_torre_nao_entra_na_media():
    """Somar zero por um dia ausente melhoraria o erro medio artificialmente."""
    d = rec.comparar(REF, {}, "x")
    assert d.dias_comparados == 0
    assert d.erro_acumulado_pp == 0.0
