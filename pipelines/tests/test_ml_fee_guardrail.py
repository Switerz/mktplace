"""
MARGEM-REAL-2B — contraprovas do guardrail fail-closed da comissão do ML.

O upsert da fato não tem `COALESCE` e é compartilhado com TikTok e Shopee, então
a proteção contra apagar uma comissão publicada não está no SQL: está nesta
barreira, que roda antes da primeira escrita e aborta a publicação inteira
quando a fonte não a sustenta.

Cada teste abaixo é uma contraprova: monta uma fotografia defeituosa e exige que
ela NÃO passe. Um guardrail sem contraprova é uma função que ninguém sabe se
dispara.

A prova de que a fato realmente fica intacta quando isso acontece está em
`test_ml_fee_guardrail_carga.py`, contra PostgreSQL real.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pipelines.quality.ml_fee_reconciliation import (
    MlFeeReconciliationError,
    reconciliar,
)

BRANDS = ("barbours", "kokeshi", "lescent", "rituaria")
D = date(2026, 7, 10)


def _celula(**over) -> dict:
    """Uma célula íntegra: Gold e fonte paga batendo, comissão observada."""
    row = {
        "date": D,
        "brand": "kokeshi",
        "gmv": Decimal("1000.00"),
        "orders": 10,
        "marketplace_fee": Decimal("170.00"),
        "fee_orders": 10,
        "paid_gmv": Decimal("1000.00"),
        "paid_orders_src": 10,
    }
    row.update(over)
    return row


def _reconciliar(rows, **kw):
    kw.setdefault("brands_esperadas", BRANDS)
    return reconciliar(rows, **kw)


# ---------------------------------------------------------------------------
# 1. Fonte completa → passa
# ---------------------------------------------------------------------------

def test_1_fonte_completa_reconcilia_e_libera_a_carga():
    _reconciliar([_celula()])   # não levanta


def test_1b_varias_celulas_integras_liberam():
    rows = [
        _celula(brand="kokeshi"),
        _celula(brand="barbours", gmv=Decimal("500.00"), paid_gmv=Decimal("500.00"),
                orders=5, paid_orders_src=5, marketplace_fee=Decimal("70.00")),
    ]
    _reconciliar(rows)


def test_1c_escala_diferente_na_mesma_quantia_nao_e_divergencia():
    """`Decimal('1000.0')` e `Decimal('1000.00')` são a mesma quantia.
    Divergência é de valor, não de como o driver formatou."""
    _reconciliar([_celula(gmv=Decimal("1000.0"), paid_gmv=Decimal("1000.00"))])


def test_1d_dia_legitimamente_sem_venda_nao_aborta():
    """Gold sem atividade e fonte paga sem célula: coerente. Não há o que
    publicar de comissão, e isso não é defeito."""
    _reconciliar([_celula(gmv=Decimal("0.00"), orders=0, marketplace_fee=None,
                          fee_orders=None, paid_gmv=None, paid_orders_src=None)])


# ---------------------------------------------------------------------------
# 2. Célula paga ausente → aborta
# ---------------------------------------------------------------------------

def test_2_celula_paga_ausente_aborta():
    """O cenário que motivou o guardrail: a Gold tem o dia, a fonte
    transacional não — e sem a barreira o NULL apagaria a comissão publicada."""
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(paid_gmv=None, paid_orders_src=None,
                              marketplace_fee=None, fee_orders=None)])
    assert "fonte paga não tem a célula" in str(exc.value)
    assert "Nenhuma linha foi publicada" in str(exc.value)


def test_2b_uma_celula_ruim_no_meio_aborta_o_lote_inteiro():
    """Fail-closed é sobre a fotografia, não sobre a linha: publicar as boas e
    descartar a ruim deixaria a fato meio velha e meio nova, sem aviso."""
    rows = [
        _celula(brand="kokeshi"),
        _celula(brand="barbours", paid_gmv=None, paid_orders_src=None,
                marketplace_fee=None),
        _celula(brand="lescent"),
    ]
    with pytest.raises(MlFeeReconciliationError):
        _reconciliar(rows)


# ---------------------------------------------------------------------------
# 3. GMV divergente → aborta
# ---------------------------------------------------------------------------

def test_3_gmv_divergente_aborta():
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(paid_gmv=Decimal("999.99"))])
    assert "GMV divergente" in str(exc.value)


def test_3b_divergencia_de_um_centavo_aborta_sem_tolerancia():
    """Medido: a igualdade é exata em 248/248 células. Uma tolerância aqui
    seria um número inventado — e esconderia a primeira divergência real."""
    with pytest.raises(MlFeeReconciliationError):
        _reconciliar([_celula(gmv=Decimal("1000.00"), paid_gmv=Decimal("1000.01"))])


# ---------------------------------------------------------------------------
# 4. Pedidos divergentes → aborta
# ---------------------------------------------------------------------------

def test_4_pedidos_divergentes_abortam():
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(paid_orders_src=9)])
    assert "pedidos divergentes" in str(exc.value)


def test_4b_gmv_igual_com_pedidos_diferentes_ainda_aborta():
    """Os dois lados têm de bater, não apenas o dinheiro."""
    with pytest.raises(MlFeeReconciliationError):
        _reconciliar([_celula(orders=10, paid_orders_src=11)])


# ---------------------------------------------------------------------------
# 5. Comissão ausente → aborta
# ---------------------------------------------------------------------------

def test_5_comissao_ausente_com_pedidos_pagos_aborta():
    """Houve venda e nenhuma linha de item a explica: é ausência de
    observação, e ausência não vira zero nem passa adiante."""
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(marketplace_fee=None, fee_orders=None)])
    assert "ausência de observação, não zero" in str(exc.value)


def test_5b_comissao_nan_aborta():
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(marketplace_fee=Decimal("NaN"))])
    assert "NaN" in str(exc.value)


def test_5c_comissao_sem_pedido_que_a_explique_aborta():
    """O inverso: fee existe num dia que a Gold diz não ter tido venda."""
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(gmv=Decimal("0.00"), orders=0,
                              paid_gmv=None, paid_orders_src=None,
                              marketplace_fee=Decimal("170.00"))])
    assert "sem nenhum pedido pago que a explique" in str(exc.value)


# ---------------------------------------------------------------------------
# 6. Zero comprovado → grava zero
# ---------------------------------------------------------------------------

def test_6_comissao_zero_com_fonte_completa_atravessa():
    """Zero MEDIDO é dado. O guardrail distingue "cobrou nada" de "não
    observamos" — e só o segundo aborta."""
    _reconciliar([_celula(marketplace_fee=Decimal("0.00"))])


def test_6b_zero_com_fonte_completa_nao_e_confundido_com_ausencia():
    rows = [_celula(marketplace_fee=Decimal("0"))]
    _reconciliar(rows)
    assert rows[0]["marketplace_fee"] == Decimal("0")


# ---------------------------------------------------------------------------
# Domínio e duplicidade
# ---------------------------------------------------------------------------

def test_linha_fora_do_escopo_de_marca_aborta():
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(brand="azbuy")])
    assert "fora do escopo" in str(exc.value)


def test_linha_fora_da_janela_aborta():
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(date=date(2026, 8, 15))],
                     date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
    assert "posterior ao fim da janela" in str(exc.value)


def test_linha_antes_da_janela_aborta():
    with pytest.raises(MlFeeReconciliationError):
        _reconciliar([_celula(date=date(2026, 6, 30))],
                     date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))


def test_chave_duplicada_aborta():
    """O agregado por dia × marca deveria ser único; duas linhas para a mesma
    chave significam fan-out no join, e a soma sairia dobrada."""
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(), _celula()])
    assert "chave repetida" in str(exc.value)


def test_linha_sem_chave_aborta():
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(brand=None)])
    assert "chave inutilizável" in str(exc.value)


def test_fonte_paga_com_venda_e_gold_sem_atividade_aborta():
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(gmv=Decimal("0.00"), orders=0)])
    assert "Gold não registra atividade" in str(exc.value)


# ---------------------------------------------------------------------------
# Mensagem
# ---------------------------------------------------------------------------

def test_mensagem_conta_divergencias_e_limita_a_amostra():
    rows = [
        _celula(brand=b, date=date(2026, 7, d), paid_gmv=Decimal("1.00"))
        for d, b in enumerate(["kokeshi", "barbours", "lescent", "rituaria"] * 2, start=1)
    ]
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar(rows)
    msg = str(exc.value)
    assert "8 de 8 célula(s)" in msg
    assert "e mais 3" in msg   # 5 na amostra, 3 no resto


def test_mensagem_diz_que_nada_foi_publicado():
    """Quem lê o log precisa saber, sem abrir o código, que a fato não mudou."""
    with pytest.raises(MlFeeReconciliationError) as exc:
        _reconciliar([_celula(paid_orders_src=7)])
    assert "a fato permanece como estava" in str(exc.value)


# ---------------------------------------------------------------------------
# 11. Exclusões contabilizadas no diagnóstico
# ---------------------------------------------------------------------------

def test_11_diagnostico_contabiliza_os_pedidos_excluidos():
    """Excluir sem contar é descartar em silêncio.

    A exclusão de `cancelled` e `partially_refunded` é o que faz numerador e
    denominador viverem na mesma população — mas quem lê precisa saber quanto
    ficou de fora. O diagnóstico registra as contagens medidas, e este teste
    trava esse registro contra uma edição distraída.

    A prova de que a exclusão de fato acontece está em
    `test_ml_comissao_sql.py::test_reconciliacao_exclui_cancelado_dos_DOIS_lados`.
    """
    from pathlib import Path

    doc = Path(__file__).resolve().parents[2] / "docs" / "margem_real_1_diagnostico.md"
    texto = doc.read_text(encoding="utf-8")

    assert "5.934" in texto, "contagem de cancelados sumiu do diagnóstico"
    assert "443.208" in texto, "valor dos cancelados sumiu do diagnóstico"
    assert "partially_refunded" in texto or "parcialmente reembolsados" in texto
    assert "10.032" in texto, "valor dos parcialmente reembolsados sumiu"


def test_11b_diagnostico_registra_a_decisao_do_ml_total_cost_pct():
    """Os três indicadores da próxima rodada precisam estar nomeados, senão a
    decisão de não mexer no KPI vira esquecimento em vez de escolha."""
    from pathlib import Path

    doc = Path(__file__).resolve().parents[2] / "docs" / "margem_real_1_diagnostico.md"
    texto = doc.read_text(encoding="utf-8")

    assert "marketplace_fee_pct" in texto
    assert "ads_frete_pct" in texto
    assert "known_cost_pct" in texto
