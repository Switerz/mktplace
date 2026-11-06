"""CX-MKT-3 — LDR da Shopee calculado direto do Data Mart (RESERVA da faixa do topo).

`GET /api/v1/expedicao/shopee/ldr-datamart?days=7&brands=` — mesmo formato
`brands[]` das outras rotas de LDR.

POR QUE EXISTE
--------------
A fonte oficial da Torre e' a publicacao `marts.expedicao_shopee_ldr_daily`
(DAG `marketplace_tower_expedicao_shopee_ldr_publish`). Enquanto ela nao
publica (em 04/11/2026: papel `expedicao_shopee_ldr_rw` ainda nao criado), a
faixa do topo usa ESTA rota para nao ficar sem a coluna. Quando a publicacao
existir, a tela volta sozinha para ela (`LdrTopo` so' cai aqui se a oficial
responder sem serie).

REGRA (a mesma ratificada em 21/10/2026, confirmada pelo usuario em 04/11)
-------------------------------------------------------------------------
Pedidos PAGOS com `ship_by_date` vencendo nos ultimos N dias FECHADOS
(Brasilia), fora FBS (`fulfilled_by_shopee`), fora cancelado sem coleta, fora
enviado sem marco de coleta. Atrasado = coletado depois do prazo, ou sem
coleta com o prazo vencido (o dia ja' fechou). Fonte: `silver.stg_shopee_orders`
(um pedido por linha), lida pelo Data Mart ou pelo Metabase.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.services.gold_service import TZ_OPERACIONAL

logger = logging.getLogger(__name__)

Consultar = Callable[[str, dict], list[dict]]

SHOPEE_MARCAS: tuple[str, ...] = ("apice", "barbours", "lescent", "rituaria")
JANELA_MAX_DIAS = 30

SQL_LDR_SHOPEE_DM = """
WITH o AS (
    SELECT shop_account, order_status, fulfillment_flag, ship_by_date, pickup_done_time
      FROM silver.stg_shopee_orders
     WHERE pay_time IS NOT NULL AND ship_by_date IS NOT NULL
       AND shop_account = ANY(:marcas)
       AND ship_by_date >= (CAST(:inicio AS date))::timestamp AT TIME ZONE 'America/Sao_Paulo'
       AND ship_by_date < (CAST(:fim AS date) + 1)::timestamp AT TIME ZONE 'America/Sao_Paulo'
), c AS (
    SELECT *,
           COALESCE(fulfillment_flag, '') = 'fulfilled_by_shopee' AS fbs,
           (order_status = 'cancelled' AND pickup_done_time IS NULL) AS cancelado_sem_coleta,
           (pickup_done_time IS NULL
            AND order_status IN ('shipped', 'to_confirm_receive', 'completed', 'to_return')) AS sem_marco
      FROM o
)
SELECT shop_account AS marca,
       COUNT(*) FILTER (WHERE NOT fbs AND NOT cancelado_sem_coleta AND NOT sem_marco) AS base,
       COUNT(*) FILTER (WHERE NOT fbs AND NOT cancelado_sem_coleta AND NOT sem_marco
                          AND (pickup_done_time > ship_by_date OR pickup_done_time IS NULL)) AS atrasados,
       COUNT(*) FILTER (WHERE sem_marco) AS sem_marco
  FROM c
 GROUP BY shop_account
"""


def consultar_datamart(sql: str, params: dict) -> list[dict]:
    from app.services import datamart_leitura  # import tardio: testes injetam `consultar`

    return datamart_leitura.consultar(sql, params)


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def obter_ldr_shopee_dm(*, dias: int = 7, brands: Optional[list[str]] = None,
                        consultar: Optional[Consultar] = None,
                        agora: Optional[datetime] = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    consultar = consultar or consultar_datamart
    fim = agora.astimezone(TZ_OPERACIONAL).date() - timedelta(days=1)
    inicio = fim - timedelta(days=dias - 1)
    marcas = [m for m in SHOPEE_MARCAS if brands is None or m in brands]
    base = {"channel": "shopee", "source": "datamart", "from": inicio.isoformat(),
            "to": fim.isoformat(), "requested_days": dias, "mature_days": dias, "brands": []}
    if not marcas:
        return {**base, "availability": "available"}
    try:
        linhas = consultar(SQL_LDR_SHOPEE_DM, {"marcas": marcas, "inicio": inicio, "fim": fim})
    except Exception as exc:  # noqa: BLE001 — motivo fixo, texto nunca sai
        logger.warning("shopee_ldr_dm: consulta falhou (%s)", type(exc).__name__)
        return {**base, "availability": "unavailable", "unavailable_reason": "source_error"}
    marcas_out = []
    for r in linhas:
        b, a = _int(r.get("base")), _int(r.get("atrasados"))
        marcas_out.append({"brand": str(r.get("marca")), "base": b, "late": a,
                           "rate": (a / b) if b > 0 else None,
                           "without_pickup_mark": _int(r.get("sem_marco"))})
    marcas_out.sort(key=lambda x: x["brand"])
    return {**base, "availability": "available", "brands": marcas_out}
