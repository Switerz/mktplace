"""
MARGEM-REAL-2B — guardrail fail-closed da comissão do Mercado Livre.

POR QUE ISTO EXISTE

O `ON CONFLICT` da fato diária é `DO UPDATE SET total_fees = EXCLUDED.total_fees`,
sem `COALESCE`, e é compartilhado com TikTok e Shopee. Logo, uma ausência
temporária da fonte transacional sobrescreveria com `NULL` uma comissão já
publicada. Três saídas foram descartadas explicitamente:

  * **apagar** o valor publicado — perde dado por causa de um incidente de
    ingestão;
  * **`COALESCE`** — preserva em silêncio um valor que pode ter ficado obsoleto,
    e faz a linha parecer atual quando não é;
  * **zero** — afirma que o marketplace não cobrou nada, o que é diferente de
    não termos observado quanto cobrou.

A quarta saída é esta: **não publicar nada**. Se a fonte não sustenta a
fotografia inteira, a carga aborta antes da primeira escrita e a fato continua
exatamente como estava.

O QUE SE RECONCILIA

Por `(date, brand)`, comparando o que a Gold diz com o que a fonte paga mostra:

  * GMV da Gold  ×  `SUM(total_amount)` dos pedidos `status='paid'`;
  * pedidos da Gold  ×  `COUNT(DISTINCT order_id)` pagos;
  * presença da comissão.

SEM TOLERÂNCIA

A comparação é de igualdade exata. Não é rigor gratuito: medido em jul+ago/2026,
`gold.gmv = SUM(total_amount)` em **248 de 248** células, escala 2 nos dois
lados, maior diferença `0.00`. Uma tolerância aqui seria um número inventado
para acomodar uma divergência que não existe — e esconderia a primeira que
aparecesse.

ZERO COMPROVADO CONTINUA ZERO

Comissão `0` com fonte completa é dado, e atravessa. O que aborta é **ausência
de observação**, nunca um zero medido.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional


class MlFeeReconciliationError(ValueError):
    """A fonte não sustenta a fotografia. Nenhuma linha deve ser publicada."""


#: Quantas células divergentes aparecem na mensagem antes do "… e mais N".
#: A mensagem serve para diagnosticar, não para despejar a janela inteira.
_MAX_AMOSTRA = 5


@dataclass(frozen=True)
class _Divergencia:
    chave: str
    motivo: str

    def __str__(self) -> str:
        return f"{self.chave}: {self.motivo}"


def _dec(valor: Any) -> Optional[Decimal]:
    """Converte para Decimal preservando a escala. `None` continua `None`.

    Não usa `float`: `float(Decimal('1873.07'))` introduz representação binária
    e faria duas quantias idênticas no banco compararem como diferentes.
    """
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _iguais(a: Optional[Decimal], b: Optional[Decimal]) -> bool:
    """Igualdade numérica exata. `Decimal('10.00') == Decimal('10.0')` é True —
    mesma quantia escrita com escalas diferentes não é divergência."""
    if a is None or b is None:
        return False
    # NaN nunca é igual a si mesmo; tratá-lo como divergência é o correto.
    if a.is_nan() or b.is_nan():
        return False
    return a == b


def _tem_atividade(gmv: Optional[Decimal], pedidos: Optional[int]) -> bool:
    """A Gold afirma que houve venda nesta célula?

    Um dia sem venda é legítimo e não exige contrapartida na fonte paga. Um dia
    COM venda exige.
    """
    if gmv is not None and not gmv.is_nan() and gmv != 0:
        return True
    return bool(pedidos)


def _int_ou_none(valor: Any) -> Optional[int]:
    if valor is None:
        return None
    try:
        return int(valor)
    except (ValueError, TypeError):
        return None


def reconciliar(
    rows: Iterable[dict],
    *,
    brands_esperadas: Iterable[str],
    date_from=None,
    date_to=None,
) -> None:
    """Levanta `MlFeeReconciliationError` se a fonte não sustentar a carga.

    Recebe as linhas CRUAS do conector do ML (antes do transform), porque só
    elas carregam `paid_gmv` e `paid_orders_src` — colunas de reconciliação que
    não existem no schema canônico.

    Retorna `None` em silêncio quando tudo reconcilia. Nunca corrige, nunca
    filtra, nunca descarta: ou a fotografia inteira está íntegra, ou não há
    publicação.
    """
    escopo = set(brands_esperadas)
    divergencias: list[_Divergencia] = []
    vistas: dict[tuple, int] = {}

    linhas = list(rows)

    for row in linhas:
        ref_date = row.get("date")
        brand = row.get("brand")
        chave = f"{ref_date} {brand}"

        # --- domínio: chave utilizável ---------------------------------
        if ref_date is None or brand is None:
            divergencias.append(_Divergencia(
                chave, "linha sem data ou sem marca — chave inutilizável"))
            continue

        # --- domínio: marca e janela -----------------------------------
        if brand not in escopo:
            divergencias.append(_Divergencia(
                chave,
                f"marca fora do escopo declarado ({sorted(escopo)}) — a fonte "
                "devolveu linha que esta carga não pediu"))
            continue

        if date_from is not None and ref_date < date_from:
            divergencias.append(_Divergencia(
                chave, f"data anterior ao início da janela ({date_from})"))
            continue
        if date_to is not None and ref_date > date_to:
            divergencias.append(_Divergencia(
                chave, f"data posterior ao fim da janela ({date_to})"))
            continue

        # --- duplicidade de chave --------------------------------------
        vistas[(ref_date, brand)] = vistas.get((ref_date, brand), 0) + 1
        if vistas[(ref_date, brand)] > 1:
            divergencias.append(_Divergencia(
                chave,
                f"chave repetida na fonte ({vistas[(ref_date, brand)]}ª "
                "ocorrência) — o agregado por dia × marca deveria ser único"))
            continue

        gold_gmv = _dec(row.get("gmv"))
        gold_orders = _int_ou_none(row.get("orders"))
        paid_gmv = _dec(row.get("paid_gmv"))
        paid_orders = _int_ou_none(row.get("paid_orders_src"))
        fee = _dec(row.get("marketplace_fee"))

        celula_paga_existe = row.get("paid_orders_src") is not None
        tem_atividade = _tem_atividade(gold_gmv, gold_orders)

        # --- célula da Gold com venda precisa existir na fonte paga -----
        if tem_atividade and not celula_paga_existe:
            divergencias.append(_Divergencia(
                chave,
                f"Gold tem venda (gmv={gold_gmv}, pedidos={gold_orders}) e a "
                "fonte paga não tem a célula — sem observação não se publica"))
            continue

        # --- fonte paga sem contrapartida na Gold ----------------------
        if celula_paga_existe and not tem_atividade:
            divergencias.append(_Divergencia(
                chave,
                f"fonte paga tem venda (gmv={paid_gmv}, pedidos={paid_orders}) "
                "e a Gold não registra atividade"))
            continue

        if not tem_atividade:
            # Dia legitimamente sem venda dos dois lados. Comissão tem de ser
            # ausente: um valor aqui significaria fee sem pedido que o explique.
            if fee is not None:
                divergencias.append(_Divergencia(
                    chave,
                    f"comissão {fee} sem nenhum pedido pago que a explique"))
            continue

        # --- os dois lados existem: têm de bater exatamente ------------
        if not _iguais(gold_gmv, paid_gmv):
            divergencias.append(_Divergencia(
                chave, f"GMV divergente — Gold {gold_gmv} × fonte paga {paid_gmv}"))
            continue

        if gold_orders != paid_orders:
            divergencias.append(_Divergencia(
                chave,
                f"pedidos divergentes — Gold {gold_orders} × fonte paga {paid_orders}"))
            continue

        # --- comissão observada ----------------------------------------
        #
        # Zero é dado e atravessa. `None` com pedidos pagos é ausência de
        # observação: houve venda, mas nenhuma linha de item a explica.
        if fee is None:
            divergencias.append(_Divergencia(
                chave,
                f"comissão ausente com {paid_orders} pedido(s) pago(s) e GMV "
                f"{paid_gmv} — ausência de observação, não zero"))
            continue

        if fee.is_nan():
            divergencias.append(_Divergencia(
                chave, "comissão NaN — valor não utilizável"))
            continue

    if not divergencias:
        return

    amostra = divergencias[:_MAX_AMOSTRA]
    resto = len(divergencias) - len(amostra)
    detalhe = "; ".join(str(d) for d in amostra)
    if resto > 0:
        detalhe += f"; … e mais {resto}"

    raise MlFeeReconciliationError(
        f"Reconciliação da comissão do ML falhou em {len(divergencias)} de "
        f"{len(linhas)} célula(s) dia × marca. Nenhuma linha foi publicada — a "
        f"fato permanece como estava. Divergências: {detalhe}"
    )
