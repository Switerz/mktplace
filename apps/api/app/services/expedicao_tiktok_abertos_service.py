"""EXPEDICAO-TIKTOK-ABERTOS — "quais pedidos do TikTok estao em aberto agora?"

`GET /api/v1/expedicao/tiktok/abertos?brands=&lista=` — o MESMO modelo da
fila da Shopee e do Mercado Livre (vencidos, vence hoje, vence no proximo dia
util, acima de 48 h, com a lista de pedidos), para o TikTok Shop.

POR QUE LE O DATA MART AO VIVO
------------------------------
O TikTok nao publica fila em `marts.expedicao_fila_atual`: o pipeline dele
(`pipelines.expedicao.tiktok_daily`) agrega por coorte. A fotografia dos
pedidos abertos sai aqui direto de `raw.tiktok_shop_orders`, pelo
`datamart_leitura` (com reserva pelo Metabase), como a aba CX ja' faz.

REGRA (medida no Data Mart em 05/11/2026)
-----------------------------------------
  aberto      `order_status` em AWAITING_SHIPMENT (falta a etiqueta/despacho)
              ou AWAITING_COLLECTION (etiqueta pronta, falta a coleta), pago
              nos ultimos `COORTE_DIAS` dias. Nenhum pedido aberto da coorte
              tinha status posterior no log (0 de 23.499): o status da tabela
              esta' vivo. Fora da coorte so' ha' residuo (pedidos de jul/ago
              de conta parada).
  ON_HOLD     retido pelo TikTok antes de liberar o envio: contado a parte,
              fora dos prazos (o vendedor ainda nao pode agir).
  prazo       RECONSTRUIDO da politica, igual a LDR do TikTok: despacho = 1
              dia util apos o pagamento, coleta = 2 dias uteis (feriados
              nacionais). O SLA por pedido do TikTok nao e' ingerido.
  escopo      `MARCAS_FORA_DO_ESCOPO` (gocase) fica fora, como na LDR.

O prazo e' calculado em Python (calendario versionado) e entra na consulta
como lista de texto `pago|vence_despacho|vence_coleta`: a reserva pelo
Metabase so' aceita tipos fechados.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

Consultar = Callable[[str, dict], list[dict]]

CANAL = "tiktokshop"

#: Pagos ha' mais que isto nao entram: residuo de conta parada (medido).
COORTE_DIAS = 30

#: Mesma exclusao da LDR (`pipelines.expedicao.tiktok_daily`).
MARCAS_FORA_DO_ESCOPO: tuple[str, ...] = ("gocase",)

STATUS_DESPACHO = "AWAITING_SHIPMENT"
STATUS_COLETA = "AWAITING_COLLECTION"
STATUS_RETIDO = "ON_HOLD"

#: Dias uteis de prazo por etapa (espelha `PRAZO_DIAS_UTEIS` do pipeline).
PRAZO_DIAS_UTEIS = {"despacho": 1, "coleta": 2}

#: Regra de negocio da Torre: acima disto o pedido pede atencao por idade.
LIMITE_IDADE_H = 48

#: Listas que o popup pode pedir. `all` = todos os abertos.
LISTAS = ("overdue", "due_today", "due_next_day", "over_48h", "awaiting_shipment",
          "awaiting_collection", "all")

#: Lista PAGINADA: cada pagina e' uma consulta com LIMIT/OFFSET sobre uma
#: ordem estavel (prazo, pagamento, pedido). Fica bem abaixo do teto de 2.000
#: linhas da reserva pelo Metabase, entao toda pagina funciona nos dois
#: caminhos. Pede-se uma linha a mais so' para saber se ha' proxima pagina.
TAMANHO_PAGINA = 200
PAGINA_MAX = 1000

#: Total da lista pedida, tirado do resumo (sem consulta extra).
TOTAL_DA_LISTA = {
    "overdue": "overdue", "due_today": "due_today", "due_next_day": "due_next_day",
    "over_48h": "over_48h", "awaiting_shipment": "awaiting_shipment",
    "awaiting_collection": "awaiting_collection", "all": "open",
}

#: Carimbos do TikTok chegam em UTC-3 (medido no pipeline).
BRT = timedelta(hours=-3)

# Feriados NACIONAIS — a MESMA lista de `pipelines.expedicao.tiktok_daily`
# (ha' teste amarrando as duas).
FERIADOS_NACIONAIS: frozenset[date] = frozenset(
    {
        date(2025, 1, 1), date(2025, 3, 3), date(2025, 3, 4), date(2025, 4, 18),
        date(2025, 4, 21), date(2025, 5, 1), date(2025, 6, 19), date(2025, 9, 7),
        date(2025, 10, 12), date(2025, 11, 2), date(2025, 11, 15), date(2025, 11, 20),
        date(2025, 12, 25),
        date(2026, 1, 1), date(2026, 2, 16), date(2026, 2, 17), date(2026, 4, 3),
        date(2026, 4, 21), date(2026, 5, 1), date(2026, 6, 4), date(2026, 9, 7),
        date(2026, 10, 12), date(2026, 11, 2), date(2026, 11, 15), date(2026, 11, 20),
        date(2026, 12, 25),
        date(2027, 1, 1), date(2027, 2, 8), date(2027, 2, 9), date(2027, 3, 26),
        date(2027, 4, 21), date(2027, 5, 1), date(2027, 5, 27), date(2027, 9, 7),
        date(2027, 10, 12), date(2027, 11, 2), date(2027, 11, 15), date(2027, 11, 20),
        date(2027, 12, 25),
    }
)


def dia_util(d: date) -> bool:
    return d.weekday() < 5 and d not in FERIADOS_NACIONAIS


def prazo_de(pago_em: date, dias_uteis: int) -> date:
    """O n-esimo dia util ESTRITAMENTE apos `pago_em` (vence no fim dele)."""
    d, restantes = pago_em, dias_uteis
    while restantes:
        d += timedelta(days=1)
        if dia_util(d):
            restantes -= 1
    return d


def proximo_dia_util(d: date) -> date:
    return prazo_de(d, 1)


def prazos(hoje: date, dias: int = COORTE_DIAS) -> list[str]:
    """`pago|vence_despacho|vence_coleta` para cada dia de pagamento da coorte."""
    saida = []
    for i in range(dias + 1):
        pago = hoje - timedelta(days=i)
        saida.append("|".join((
            pago.isoformat(),
            prazo_de(pago, PRAZO_DIAS_UTEIS["despacho"]).isoformat(),
            prazo_de(pago, PRAZO_DIAS_UTEIS["coleta"]).isoformat(),
        )))
    return saida


# Classificacao em SQL a partir dos prazos ja' calculados. Sem o sinal de
# porcentagem em lugar nenhum (o driver o le como marcador de parametro).
_CTE = """
WITH prazo AS (
    SELECT split_part(p, '|', 1)::date AS pago_em,
           split_part(p, '|', 2)::date AS vence_despacho,
           split_part(p, '|', 3)::date AS vence_coleta
      FROM unnest(CAST(:prazos AS text[])) AS p
), ped AS (
    SELECT o.brand, o.order_id, o.shop_name, o.order_status, o.paid_at,
           CASE WHEN o.order_status = :st_coleta THEN 'coleta'
                WHEN o.order_status = :st_despacho THEN 'despacho'
                ELSE 'retido' END AS etapa,
           CASE WHEN o.order_status = :st_coleta THEN z.vence_coleta
                WHEN o.order_status = :st_despacho THEN z.vence_despacho END AS vence_em
      FROM raw.tiktok_shop_orders o
      JOIN prazo z ON z.pago_em = o.paid_at::date
     WHERE o.order_status = ANY(CAST(:status AS text[]))
       AND o.paid_at IS NOT NULL
       AND o.paid_at >= CAST(:desde AS date)
       AND NOT (o.brand = ANY(CAST(:marcas_fora AS text[])))
       {filtro_marca}
), c AS (
    SELECT *,
           CASE WHEN vence_em IS NULL THEN 'held'
                WHEN vence_em < CAST(:hoje AS date) THEN 'overdue'
                WHEN vence_em = CAST(:hoje AS date) THEN 'due_today'
                WHEN vence_em = CAST(:proximo AS date) THEN 'due_next_day'
                ELSE 'on_time' END AS situacao,
           paid_at < CAST(:corte_48h AS timestamp) AS acima_48h
      FROM ped
)
"""

SQL_RESUMO = _CTE + """
SELECT brand,
       COUNT(*) FILTER (WHERE etapa <> 'retido')                        AS abertos,
       COUNT(*) FILTER (WHERE etapa = 'despacho')                       AS aguardando_despacho,
       COUNT(*) FILTER (WHERE etapa = 'coleta')                         AS aguardando_coleta,
       COUNT(*) FILTER (WHERE etapa = 'retido')                         AS retidos,
       COUNT(*) FILTER (WHERE situacao = 'overdue')                     AS vencidos,
       COUNT(*) FILTER (WHERE situacao = 'overdue' AND etapa = 'despacho') AS vencidos_despacho,
       COUNT(*) FILTER (WHERE situacao = 'overdue' AND etapa = 'coleta')   AS vencidos_coleta,
       COUNT(*) FILTER (WHERE situacao = 'due_today')                   AS vence_hoje,
       COUNT(*) FILTER (WHERE situacao = 'due_next_day')                AS vence_proximo,
       COUNT(*) FILTER (WHERE situacao = 'on_time')                     AS no_prazo,
       COUNT(*) FILTER (WHERE acima_48h AND etapa <> 'retido')          AS acima_48h,
       MIN(paid_at) FILTER (WHERE etapa <> 'retido')                    AS mais_antigo_em
  FROM c
 GROUP BY brand
"""

_FILTRO_LISTA = {
    "overdue": "situacao = 'overdue'",
    "due_today": "situacao = 'due_today'",
    "due_next_day": "situacao = 'due_next_day'",
    "over_48h": "acima_48h AND etapa <> 'retido'",
    "awaiting_shipment": "etapa = 'despacho'",
    "awaiting_collection": "etapa = 'coleta'",
    "all": "etapa <> 'retido'",
}

SQL_LISTA = _CTE + """
SELECT brand, order_id, shop_name, etapa, paid_at, vence_em, situacao, acima_48h
  FROM c
 WHERE {filtro_lista}
 ORDER BY vence_em ASC NULLS LAST, paid_at ASC, order_id ASC
 {limite}
"""


def consultar_datamart(sql: str, params: dict) -> list[dict]:
    from app.services import datamart_leitura  # import tardio: testes injetam `consultar`

    return datamart_leitura.consultar(sql, params)


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None).isoformat(timespec="seconds")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _como_data(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _como_instante(v: Any) -> Optional[datetime]:
    """`paid_at` e' BRT sem fuso; a reserva pelo Metabase pode trazer com fuso."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = (v.astimezone(timezone.utc) + BRT).replace(tzinfo=None)
        return v
    return None


def _pedido(p: dict, agora_brt: datetime) -> dict:
    pago = _como_instante(p.get("paid_at"))
    horas = (agora_brt - pago).total_seconds() / 3600 if pago else None
    return {
        "brand": str(p.get("brand")),
        "order_id": str(p.get("order_id")),
        "shop_name": p.get("shop_name"),
        "stage": p.get("etapa"),
        "paid_at": _iso(pago),
        "deadline": _iso(_como_data(p.get("vence_em"))),
        "situation": p.get("situacao"),
        "over_48h": bool(p.get("acima_48h")),
        "hours_open": round(horas, 1) if horas is not None else None,
    }


CAMPOS_MARCA = (
    "open", "awaiting_shipment", "awaiting_collection", "on_hold", "overdue",
    "overdue_shipment", "overdue_collection", "due_today", "due_next_day",
    "on_time", "over_48h",
)

_DA_LINHA = {
    "open": "abertos", "awaiting_shipment": "aguardando_despacho",
    "awaiting_collection": "aguardando_coleta", "on_hold": "retidos",
    "overdue": "vencidos", "overdue_shipment": "vencidos_despacho",
    "overdue_collection": "vencidos_coleta", "due_today": "vence_hoje",
    "due_next_day": "vence_proximo", "on_time": "no_prazo", "over_48h": "acima_48h",
}


def _parametros(agora_brt: datetime, marcas: list[str]) -> tuple[dict[str, Any], str]:
    hoje = agora_brt.date()
    params: dict[str, Any] = {
        "prazos": prazos(hoje),
        "st_coleta": STATUS_COLETA,
        "st_despacho": STATUS_DESPACHO,
        "status": [STATUS_DESPACHO, STATUS_COLETA, STATUS_RETIDO],
        "desde": hoje - timedelta(days=COORTE_DIAS),
        "marcas_fora": list(MARCAS_FORA_DO_ESCOPO),
        "hoje": hoje,
        "proximo": proximo_dia_util(hoje),
        "corte_48h": (agora_brt - timedelta(hours=LIMITE_IDADE_H)).strftime("%Y-%m-%d %H:%M:%S"),
    }
    filtro_marca = ""
    if marcas:
        params["marcas"] = marcas
        filtro_marca = "AND o.brand = ANY(CAST(:marcas AS text[]))"
    return params, filtro_marca


def obter_abertos(
    *,
    brands: Optional[list[str]] = None,
    lista: Optional[str] = None,
    pagina: int = 1,
    consultar: Optional[Consultar] = None,
    agora: Optional[datetime] = None,
) -> dict:
    agora = agora or datetime.now(timezone.utc)
    consultar = consultar or consultar_datamart
    agora_brt = (agora.astimezone(timezone.utc) + BRT).replace(tzinfo=None)
    hoje = agora_brt.date()
    proximo = proximo_dia_util(hoje)
    marcas = [m for m in (brands or []) if m not in MARCAS_FORA_DO_ESCOPO]

    base: dict[str, Any] = {
        "channel": CANAL,
        "effective_at": agora.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "today": hoje.isoformat(),
        "next_business_day": proximo.isoformat(),
        "cohort_days": COORTE_DIAS,
        "excluded_brands": list(MARCAS_FORA_DO_ESCOPO),
        "deadline_reconstructed": True,
        "deadline_rule": {"despacho": PRAZO_DIAS_UTEIS["despacho"],
                          "coleta": PRAZO_DIAS_UTEIS["coleta"]},
        "totals": {k: 0 for k in CAMPOS_MARCA},
        "brands": [],
        "list": lista,
        "orders": [],
        "page": pagina,
        "page_size": TAMANHO_PAGINA,
        "list_total": 0,
        "has_more": False,
    }
    if brands and not marcas:
        # So' marcas fora do escopo: nada a mostrar, mas a superficie existe.
        return {**base, "availability": "available"}

    params, filtro_marca = _parametros(agora_brt, marcas)

    try:
        linhas = consultar(SQL_RESUMO.format(filtro_marca=filtro_marca), params)
    except Exception as exc:  # noqa: BLE001 — motivo fixo, texto nunca sai
        logger.warning("tiktok_abertos: resumo falhou (%s)", type(exc).__name__)
        return {**base, "availability": "unavailable", "unavailable_reason": "source_error"}

    totais = {k: 0 for k in CAMPOS_MARCA}
    marcas_out = []
    mais_antigo: Optional[datetime] = None
    for r in linhas:
        item: dict[str, Any] = {"brand": str(r.get("brand"))}
        for k in CAMPOS_MARCA:
            item[k] = _int(r.get(_DA_LINHA[k]))
            totais[k] += item[k]
        antigo = _como_instante(r.get("mais_antigo_em"))
        item["oldest_paid_at"] = _iso(antigo)
        if antigo and (mais_antigo is None or antigo < mais_antigo):
            mais_antigo = antigo
        marcas_out.append(item)
    marcas_out.sort(key=lambda x: (-x["overdue"], -x["open"], x["brand"]))
    saida = {**base, "availability": "available", "totals": totais, "brands": marcas_out,
             "oldest_paid_at": _iso(mais_antigo)}

    if not lista:
        return saida
    saida["list_total"] = totais[TOTAL_DA_LISTA[lista]]
    deslocamento = (pagina - 1) * TAMANHO_PAGINA
    try:
        pedidos = consultar(
            SQL_LISTA.format(filtro_marca=filtro_marca, filtro_lista=_FILTRO_LISTA[lista],
                             limite=f"LIMIT {TAMANHO_PAGINA + 1} OFFSET {deslocamento}"),
            params,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiktok_abertos: lista falhou (%s)", type(exc).__name__)
        return {**saida, "orders_unavailable": True}

    pedidos_out = [_pedido(p, agora_brt) for p in pedidos[:TAMANHO_PAGINA]]
    return {**saida, "orders": pedidos_out, "has_more": len(pedidos) > TAMANHO_PAGINA}

