"""CX-MKT-3 — LDR do Mercado Livre (atraso de envio), por marca, ultimos N dias.

`GET /api/v1/expedicao/ml/ldr?days=7&brands=` — mesmo formato `brands[]` das
rotas de LDR da Shopee e do TikTok, para a faixa do topo da Expedicao.

REGRA (medida no Data Mart em 03/11/2026)
-----------------------------------------
Prazo = SLA oficial de despacho do ML (`raw.ml_shipments.sla_expected_date`,
lido de `/shipments/{id}/sla`). No Mercado Coleta ele e' o HORARIO AGENDADO da
coleta, e a coleta e' registrada minutos ou horas depois (mediana de +0,5 h):
comparar coleta x prazo ao pe' da letra daria ~80% de "atraso" que o proprio ML
nao marca. O que bate com o `sla_status = 'delayed'` do ML e':

  * base      envios com SLA vencendo no dia (Brasilia), de modalidade com
              coleta (`LOGISTICAS_COM_COLETA`), menos cancelados sem coleta;
  * atrasado  coletado (`picked_up`/`dropped_off`/`in_hub`) DEPOIS do fim do
              dia do prazo, OU marcado `delayed` pelo ML, OU sem coleta com o
              dia ja' fechado;
  * fora      enviado/entregue sem marco de coleta (nao da' para julgar):
              sai da base e e' contado.

Medido: "coletado depois do dia" = `delayed` do ML em 31/10 (15 de 15) e 30/10
(3 de 3). So' dias FECHADOS (ate' D-1). A serie comeca em `INICIO_SERIE`: antes
disso o SLA so' existia para envios presos, e a taxa sairia enviesada.

Calculo Torre, nao a metrica oficial "envios com atraso" da reputacao (60 dias,
na secao Performance). Ausencia de base = `rate = None`, nunca 0.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import text

from app.services.gold_service import ML_BRANDS, TZ_OPERACIONAL

logger = logging.getLogger(__name__)

Consultar = Callable[[str, dict], list[dict]]

#: Primeiro dia com SLA gravado para todos os envios abertos (Fase 3 do
#: `ml_orders_etl`, 03/11/2026). Antes disso so' envios presos tinham SLA.
INICIO_SERIE = date(2026, 11, 3)
JANELA_MAX_DIAS = 30
LOGISTICAS_COM_COLETA = ("cross_docking", "xd_drop_off", "drop_off")
SUBSTATUS_COLETADO = ("picked_up", "dropped_off", "in_hub")

SQL_LDR_ML = """
WITH s AS (
    SELECT DISTINCT ON (shipment_id)
           shipment_id, brand, status, sla_expected_date, sla_status,
           (sla_expected_date AT TIME ZONE 'America/Sao_Paulo')::date AS dia_prazo,
           (SELECT min((e->>'date')::timestamptz)
              FROM jsonb_array_elements(COALESCE(substatus_history, '[]'::jsonb)) e
             WHERE e->>'substatus' = ANY(:coletado)) AS coletado_em
      FROM raw.ml_shipments
     WHERE sla_expected_date IS NOT NULL
       AND brand = ANY(:marcas)
       AND logistic_type = ANY(:logisticas)
       AND sla_expected_date >= (CAST(:inicio AS date))::timestamp AT TIME ZONE 'America/Sao_Paulo'
       AND sla_expected_date < (CAST(:fim AS date) + 1)::timestamp AT TIME ZONE 'America/Sao_Paulo'
     ORDER BY shipment_id, updated_at DESC NULLS LAST
), c AS (
    SELECT *,
           (status = 'cancelled' AND coletado_em IS NULL) AS cancelado_sem_coleta,
           (coletado_em IS NULL AND status IN ('shipped', 'delivered', 'not_delivered')) AS sem_marco
      FROM s
)
SELECT brand AS marca,
       COUNT(*) FILTER (WHERE NOT cancelado_sem_coleta AND NOT sem_marco) AS base,
       COUNT(*) FILTER (WHERE NOT cancelado_sem_coleta AND NOT sem_marco AND (
              coletado_em >= ((dia_prazo + 1)::timestamp AT TIME ZONE 'America/Sao_Paulo')
           OR sla_status = 'delayed'
           OR coletado_em IS NULL)) AS atrasados,
       COUNT(*) FILTER (WHERE sem_marco) AS sem_marco
  FROM c
 GROUP BY brand
"""


def consultar_datamart(sql: str, params: dict) -> list[dict]:
    from app.database import datamart_engine  # import tardio: testes injetam `consultar`

    if datamart_engine is None:
        raise RuntimeError("datamart indisponivel")
    with datamart_engine.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def obter_ldr_ml(*, dias: int = 7, brands: Optional[list[str]] = None,
                 consultar: Optional[Consultar] = None, agora: Optional[datetime] = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    consultar = consultar or consultar_datamart
    fim = agora.astimezone(TZ_OPERACIONAL).date() - timedelta(days=1)
    inicio_pedido = fim - timedelta(days=dias - 1)
    inicio = max(inicio_pedido, INICIO_SERIE)
    marcas = [m for m in ML_BRANDS if brands is None or m in brands]
    base = {
        "channel": "mercadolivre", "from": inicio.isoformat(), "to": fim.isoformat(),
        "requested_days": dias, "series_start": INICIO_SERIE.isoformat(), "brands": [],
    }
    if fim < INICIO_SERIE:
        return {**base, "availability": "unavailable", "unavailable_reason": "series_not_started",
                "mature_days": 0}
    if not marcas:
        return {**base, "availability": "available", "mature_days": (fim - inicio).days + 1}
    try:
        linhas = consultar(SQL_LDR_ML, {
            "marcas": marcas, "inicio": inicio, "fim": fim,
            "logisticas": list(LOGISTICAS_COM_COLETA), "coletado": list(SUBSTATUS_COLETADO),
        })
    except Exception as exc:  # noqa: BLE001 — motivo fixo, texto nunca sai
        logger.warning("ml_ldr: consulta falhou (%s)", type(exc).__name__)
        return {**base, "availability": "unavailable", "unavailable_reason": "source_error", "mature_days": 0}
    marcas_out = []
    for r in linhas:
        b, a = _int(r.get("base")), _int(r.get("atrasados"))
        marcas_out.append({
            "brand": str(r.get("marca")), "base": b, "late": a,
            "rate": (a / b) if b > 0 else None, "without_pickup_mark": _int(r.get("sem_marco")),
        })
    marcas_out.sort(key=lambda x: x["brand"])
    return {**base, "availability": "available", "mature_days": (fim - inicio).days + 1, "brands": marcas_out}
