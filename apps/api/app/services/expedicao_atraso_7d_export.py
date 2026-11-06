"""Atraso de 7 dias da Kokeshi (export do Seller Centre), calculado pela API.

POR QUE AQUI
------------
O % de vencidos da Expedicao e' a regra oficial da Shopee: atrasados ÷
pedidos com prazo nos ultimos 7 dias fechados (EXPEDICAO-ATRASO-7D). Para as
contas do Data Mart quem calcula e' o pipeline (`pipelines/expedicao/atraso_7d.py`).
A Kokeshi chega por outro caminho: o bot dela grava DIRETO no Neon o
historico de pedidos do export (`marts.expedicao_shopee_export_pedido`), a
fila e o resumo — sem rodar o nosso codigo. Sem isto, a Kokeshi ficaria sem %
para sempre (pedido do usuario, 03/11/2026: "todos os casos devem ter %").

A REGRA E' A MESMA, NAO UMA PARECIDA
------------------------------------
Espelho de `pipelines.expedicao.shopee_ldr.classificar` + `madura` e de
`atraso_7d.apurar_shopee`; o teste
`test_expedicao_api_atraso_7d_export.py::test_paridade_com_o_pipeline` gera
pedidos e compara as contagens com as do pipeline. Mudou la', tem de mudar
aqui (o teste quebra).

  base  = pago, do vendedor (nao FBS), nao "cancelado sem coleta", com
          prazo, nao "pos-envio sem coleta", prazo num dia MADURO da janela;
  atraso = coletado depois do prazo, ou sem coleta com o prazo vencido.

Fluxo fora do vocabulario (o pipeline levanta) aqui deixa a conta SEM % —
falha fechada, nunca um numero com pedido desconhecido dentro.

Janela: `[hoje - 7, hoje - 1]` em Brasilia, `hoje` = dia da fotografia; o
fim e' o ultimo dia MADURO da conta (lido depois de D+1 06:00 pela hora do
download do export, `source_watermark_at`).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import text

PEDIDO_EXPORT = "marts.expedicao_shopee_export_pedido"

BRT = timezone(timedelta(hours=-3))
MARGEM_MATURIDADE = timedelta(hours=6)
DIAS_DA_JANELA = 7
#: Recorte por criacao (indice `idx_esep_conta_criacao`): pedido com prazo na
#: janela foi criado poucos dias antes dele.
MARGEM_CRIACAO = timedelta(days=30)

#: Limiar de idade e regra de lentidao, os mesmos de `pipelines.expedicao.contract`.
LIMITE_48H_HORAS = 48
FATOR_LENTO = 2.0
AMOSTRA_MINIMA = 100
JANELA_BASELINE = timedelta(days=30)

FLUXO_VENDEDOR = "fulfilled_by_local_seller"
FLUXO_FBS = "fulfilled_by_shopee"
STATUS_CANCELADO = ("CANCELLED", "IN_CANCEL")
STATUS_POS_ENVIO = ("SHIPPED", "TO_CONFIRM_RECEIVE", "COMPLETED", "TO_RETURN")

#: Contagem por conta, numa consulta. `dia` = dia do prazo em Brasilia, por
#: offset FIXO (-03:00), como o pipeline.
SQL = f"""
WITH baseline AS (
  -- Espelho de `shopee_export.baseline`: 30 dias de criacao, com coleta e
  -- marco, status conhecido e fora de CANCELLED; p50 em horas.
  SELECT count(*) AS amostra,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY
           EXTRACT(EPOCH FROM pickup_done_time - coalesce(pay_time, create_time)) / 3600.0
         ) AS p50
    FROM {PEDIDO_EXPORT}
   WHERE shop_account = :conta
     AND create_time >= :baseline_desde AND create_time < :agora
     AND pickup_done_time IS NOT NULL
     AND coalesce(pay_time, create_time) IS NOT NULL
     AND order_status IS NOT NULL AND order_status <> 'CANCELLED'
),
p AS (
  SELECT pay_time, create_time, fulfillment_flag, order_status, ship_by_date, pickup_done_time,
         (ship_by_date AT TIME ZONE INTERVAL '-03:00')::date AS dia
    FROM {PEDIDO_EXPORT}
   WHERE shop_account = :conta
     AND create_time >= :criado_desde
     AND ship_by_date IS NOT NULL
),
na_janela AS (
  SELECT * FROM p
   WHERE dia >= :desde AND dia <= :ate
     AND pay_time IS NOT NULL
     AND fulfillment_flag IS DISTINCT FROM '{FLUXO_FBS}'
),
classe AS (
  SELECT fulfillment_flag,
         EXTRACT(EPOCH FROM coalesce(pickup_done_time, :agora)
                            - coalesce(pay_time, create_time)) / 3600.0 AS horas,
         NOT (order_status IN {STATUS_CANCELADO} AND pickup_done_time IS NULL)
         AND NOT (pickup_done_time IS NULL AND order_status IN {STATUS_POS_ENVIO})
           AS base,
         (pickup_done_time IS NOT NULL AND pickup_done_time > ship_by_date)
         OR (pickup_done_time IS NULL AND ship_by_date < :agora)
           AS atraso
    FROM na_janela
)
SELECT count(*) FILTER (WHERE fulfillment_flag IS DISTINCT FROM '{FLUXO_VENDEDOR}')
         AS fluxo_desconhecido,
       count(*) FILTER (WHERE base) AS due,
       count(*) FILTER (WHERE base AND atraso) AS late,
       count(*) FILTER (WHERE base AND horas > {LIMITE_48H_HORAS}) AS over_48h,
       CASE WHEN (SELECT amostra FROM baseline) >= {AMOSTRA_MINIMA}
            THEN count(*) FILTER (WHERE base AND horas > {FATOR_LENTO} * (SELECT p50 FROM baseline))
       END AS stalled
  FROM classe
"""


def janela(effective_at: datetime) -> tuple[date, date]:
    hoje = effective_at.astimezone(BRT).date()
    return hoje - timedelta(days=DIAS_DA_JANELA), hoje - timedelta(days=1)


def fim_maduro(desde: date, ate: date, hoje: date,
               watermark: Optional[datetime]) -> Optional[date]:
    """Ultimo dia da janela ja' maduro para a conta (`shopee_ldr.madura`)."""
    if watermark is None:
        return None
    if watermark.tzinfo is None:
        watermark = watermark.replace(tzinfo=timezone.utc)
    fim, dia = None, desde
    while dia <= ate:
        corte = datetime.combine(dia + timedelta(days=1), time(0, 0), tzinfo=BRT)
        if dia < hoje and watermark >= corte + MARGEM_MATURIDADE:
            fim = dia
        dia += timedelta(days=1)
    return fim


def calcular(db, shop_account: str, watermark: Optional[datetime],
             effective_at: datetime) -> Optional[dict]:
    """As quatro chaves do atraso de 7 dias da conta, ou None (sem calculo)."""
    hoje = effective_at.astimezone(BRT).date()
    desde, ate = janela(effective_at)
    fim = fim_maduro(desde, ate, hoje, watermark)
    if fim is None:
        return None
    criado_desde = datetime.combine(desde, time(0, 0), tzinfo=BRT) - MARGEM_CRIACAO
    linha = db.execute(text(SQL), {
        "conta": shop_account, "criado_desde": criado_desde,
        "desde": desde, "ate": fim, "agora": effective_at,
        "baseline_desde": effective_at - JANELA_BASELINE,
    }).mappings().first()
    if not linha or int(linha["fluxo_desconhecido"] or 0) > 0:
        return None
    due = int(linha["due"] or 0)
    if due == 0:
        return None
    stalled = linha.get("stalled")
    return {"due_7d_count": due, "late_7d_count": int(linha["late"] or 0),
            "over_48h_7d_count": int(linha.get("over_48h") or 0),
            "stalled_7d_count": None if stalled is None else int(stalled),
            "late_7d_window_start": desde, "late_7d_window_end": fim}
