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

import json
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


#: Espera da consulta dos vencidos sem coleta pelo Metabase. Medido em
#: 05/11/2026: ~17 s com o banco folgado e de 50 s a 150 s depois da
#: recuperacao do Data Mart — com 60 s, a lista dava `source_error` a cada
#: tentativa (log do Render, desde 15:10). O resultado fica 10 min em cache,
#: entao so' a primeira abertura do dia paga a espera.
TIMEOUT_COLETA_PERDIDA_S = 170.0


def consultar_datamart_pesado(sql: str, params: dict) -> list[dict]:
    from app.services import datamart_leitura

    return datamart_leitura.consultar(sql, params, timeout_metabase=TIMEOUT_COLETA_PERDIDA_S)


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


# ---------------------------------------------------------------------------
# EXPEDICAO-TIKTOK-COLETA-PERDIDA — "quais pedidos venceram no dia X sem coleta?"
# ---------------------------------------------------------------------------
# Pedido da operacao em 05/11/2026: a transportadora nao veio em 04/11 e o
# TikTok vai punir. Para contestar, a lista traz os pedidos com prazo de COLETA
# no dia, etiqueta pronta ate' o fim do dia e SEM coleta ate' o fim do dia —
# inclusive os coletados DEPOIS, que ja' sairam da fila de abertos.
#
#   pronto      `max(rts_time)` dos itens, so' com TODOS os itens carimbados
#               (regra estrita da LDR). O log de status nao serve: medido, a
#               maioria dos pedidos nao tem a linha AWAITING_COLLECTION.
#   coleta      `min(updated_at_tiktok)` do log para IN_TRANSIT (regra da LDR).
#   cancelado   antes do fim do prazo sai; depois, fica (tinha de ter saido).

#: Ate' quantos dias para tras o dia de prazo pode ser pedido.
COLETA_PERDIDA_DIAS = 30

#: Status ATUAIS do TikTok que a lista aceita como filtro (allowlist).
STATUS_COLETA_PERDIDA = ("ON_HOLD", "AWAITING_SHIPMENT", "AWAITING_COLLECTION", "IN_TRANSIT", "DELIVERED",
                         "COMPLETED", "CANCELLED")

# UMA consulta por dia de prazo, com o resultado inteiro numa linha (json_agg):
# a reserva pelo Metabase corta em 2.000 LINHAS, nao em tamanho de celula, e a
# consulta custa ~15 s (medido em 05/11). Paginar e filtrar em SQL refazia
# esse custo a cada pagina e estourava o tempo do Metabase. Assim o dia e' lido
# uma vez (cache de 10 min do `datamart_leitura`) e marca, status e pagina
# saem da memoria.
#
# Ordem das juncoes, medida: o log (com `detected_at`, que usa o indice) corta
# de ~35 mil para ~6 mil candidatos ANTES de tocar nos itens (3,2 GB).
SQL_COLETA_DIA = """
WITH ped AS MATERIALIZED (
    SELECT o.brand, o.order_id, o.shop_name, o.order_status, o.paid_at
      FROM raw.tiktok_shop_orders o
     WHERE o.paid_at >= CAST(:pago_ini AS date)
       AND o.paid_at <  CAST(:pago_fim AS date) + 1
       AND NOT (o.brand = ANY(CAST(:marcas_fora AS text[])))
), log AS (
    SELECT l.order_id,
           min(l.updated_at_tiktok) FILTER (WHERE l.new_status = 'IN_TRANSIT') AS coletado_em,
           min(l.updated_at_tiktok) FILTER (WHERE l.new_status = 'CANCELLED')  AS cancelado_em
      FROM raw.tiktok_shop_order_status_log l
     WHERE l.detected_at >= CAST(:pago_ini AS date) - 1
       AND l.new_status IN ('IN_TRANSIT', 'CANCELLED')
       AND l.order_id IN (SELECT order_id FROM ped)
     GROUP BY 1
), cand AS MATERIALIZED (
    SELECT p.*, g.coletado_em, g.cancelado_em
      FROM ped p
      LEFT JOIN log g ON g.order_id = p.order_id
     WHERE (g.coletado_em IS NULL OR g.coletado_em >= CAST(:limite AS timestamp))
       AND (g.cancelado_em IS NULL OR g.cancelado_em >= CAST(:limite AS timestamp))
       AND NOT (p.order_status = 'CANCELLED' AND g.cancelado_em IS NULL)
), rts AS (
    SELECT li.order_id, max(li.rts_time) AS t, count(*) AS itens, count(li.rts_time) AS com
      FROM raw.tiktok_shop_line_items li
     WHERE li.order_id IN (SELECT order_id FROM cand)
     GROUP BY 1
)
SELECT json_agg(json_build_object(
           'brand', c.brand, 'order_id', c.order_id, 'shop_name', c.shop_name,
           'order_status', c.order_status, 'paid_at', c.paid_at,
           'pronto_em', CASE WHEN r.com = r.itens THEN r.t END,
           'coletado_em', c.coletado_em)
         ORDER BY c.brand, c.paid_at, c.order_id) AS pedidos
  FROM cand c
  LEFT JOIN rts r ON r.order_id = c.order_id
"""
# A consulta traz TODOS os que venceram sem coleta (a leitura da LDR). O
# recorte "etiqueta pronta ate' o fim do prazo" e' feito em Python, para que
# as duas leituras usem a MESMA consulta (e o mesmo cache).

#: `escopo` da lista: so' com etiqueta pronta no prazo (contestacao da coleta)
#: ou todos os que venceram sem coleta (clique na barra da evolucao, = LDR).
ESCOPOS_COLETA = ("etiqueta_pronta", "todos")


def pagamentos_do_prazo(vencimento: date) -> Optional[tuple[date, date]]:
    """Faixa CONTIGUA de dias de pagamento cuja coleta vence em `vencimento`."""
    dias = [vencimento - timedelta(days=i) for i in range(1, 12)
            if prazo_de(vencimento - timedelta(days=i), PRAZO_DIAS_UTEIS["coleta"]) == vencimento]
    return (min(dias), max(dias)) if dias else None


def dias_de_prazo(hoje: date, n: int = 10) -> list[str]:
    """Os ultimos `n` dias uteis ate' hoje (inclusive), mais recente primeiro."""
    saida, d = [], hoje
    while len(saida) < n:
        if dia_util(d):
            saida.append(d.isoformat())
        d -= timedelta(days=1)
    return saida


def _lista_json(v: Any) -> list[dict]:
    if v is None:
        return []
    if isinstance(v, str):
        v = json.loads(v)
    return [dict(x) for x in v] if isinstance(v, list) else []


#: Status RECONSTRUIDO no fim do dia do prazo. Por definicao da lista, nenhum
#: pedido estava coletado nem cancelado naquele instante; o que resta e' se a
#: etiqueta ja' estava pronta. O log de status nao serve para isto: medido, a
#: maioria dos pedidos nao tem a linha AWAITING_COLLECTION.
STATUS_NO_DIA = ("ON_HOLD", "AWAITING_SHIPMENT", "AWAITING_COLLECTION")


def status_no_dia(p: dict, fim_do_prazo: datetime) -> str:
    """Como o pedido estava no fim do dia do prazo (Brasilia)."""
    pronto = _como_instante(p.get("pronto_em"))
    if pronto is not None and pronto < fim_do_prazo:
        return "AWAITING_COLLECTION"
    # Retido hoje e sem etiqueta: estava retido tambem naquele dia.
    if p.get("order_status") == "ON_HOLD":
        return "ON_HOLD"
    return "AWAITING_SHIPMENT"


def obter_coleta_perdida(
    *,
    vencimento: date,
    brands: Optional[list[str]] = None,
    status: Optional[list[str]] = None,
    status_dia: Optional[list[str]] = None,
    escopo: str = "etiqueta_pronta",
    pagina: int = 1,
    consultar: Optional[Consultar] = None,
    agora: Optional[datetime] = None,
) -> dict:
    agora = agora or datetime.now(timezone.utc)
    consultar = consultar or consultar_datamart_pesado
    agora_brt = (agora.astimezone(timezone.utc) + BRT).replace(tzinfo=None)
    marcas = [m for m in (brands or []) if m not in MARCAS_FORA_DO_ESCOPO]
    faixa = pagamentos_do_prazo(vencimento)
    fim_do_prazo = datetime.combine(vencimento + timedelta(days=1), datetime.min.time())
    base: dict[str, Any] = {
        "channel": CANAL, "deadline": vencimento.isoformat(), "scope": escopo,
        "paid_from": faixa[0].isoformat() if faixa else None,
        "paid_to": faixa[1].isoformat() if faixa else None,
        "deadline_days": dias_de_prazo(agora_brt.date()),
        "excluded_brands": list(MARCAS_FORA_DO_ESCOPO),
        "filters": {"brands": marcas or None, "status": status or None,
                    "status_on_day": status_dia or None},
        "brands": [], "all_brands": [], "status_counts": {}, "list_total": 0,
        "still_waiting": 0, "orders": [],
        "page": pagina, "page_size": TAMANHO_PAGINA, "has_more": False,
    }
    if not faixa or (brands and not marcas):
        return {**base, "availability": "available"}
    params: dict[str, Any] = {
        "pago_ini": faixa[0], "pago_fim": faixa[1],
        "marcas_fora": list(MARCAS_FORA_DO_ESCOPO),
        "limite": fim_do_prazo.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        linhas = consultar(SQL_COLETA_DIA, params)
        todos = _lista_json(linhas[0].get("pedidos")) if linhas else []
    except Exception as exc:  # noqa: BLE001 — motivo fixo, texto nunca sai
        logger.warning("tiktok_coleta_perdida: consulta falhou (%s)", type(exc).__name__)
        return {**base, "availability": "unavailable", "unavailable_reason": "source_error"}
    if escopo == "etiqueta_pronta":
        todos = [p for p in todos
                 if (pronto := _como_instante(p.get("pronto_em"))) is not None and pronto < fim_do_prazo]

    for p in todos:
        p["status_no_dia"] = status_no_dia(p, fim_do_prazo)

    # Marca recorta tudo; cada status recorta a lista e as contagens de um sao
    # feitas com o OUTRO ja' aplicado (o que cada filtro mostra ao lado de cada
    # opcao bate com o que a lista vai mostrar ao escolher).
    todas_marcas = sorted({str(p.get("brand")) for p in todos})
    da_marca = [p for p in todos if not marcas or p.get("brand") in marcas]
    ok_hoje = [p for p in da_marca if not status or p.get("order_status") in status]
    ok_dia = [p for p in da_marca if not status_dia or p["status_no_dia"] in status_dia]
    contagem_status: dict[str, int] = {}
    for p in ok_dia:
        st = str(p.get("order_status"))
        contagem_status[st] = contagem_status.get(st, 0) + 1
    contagem_dia: dict[str, int] = {}
    for p in ok_hoje:
        contagem_dia[p["status_no_dia"]] = contagem_dia.get(p["status_no_dia"], 0) + 1
    filtrados = [p for p in ok_hoje if not status_dia or p["status_no_dia"] in status_dia]

    por_marca: dict[str, dict] = {}
    for p in filtrados:
        b = por_marca.setdefault(str(p.get("brand")), {"brand": str(p.get("brand")), "missed": 0, "still_waiting": 0})
        b["missed"] += 1
        if not p.get("coletado_em"):
            b["still_waiting"] += 1
    marcas_out = sorted(por_marca.values(), key=lambda x: (-x["missed"], x["brand"]))

    ini = (pagina - 1) * TAMANHO_PAGINA
    pedidos = []
    for p in filtrados[ini:ini + TAMANHO_PAGINA]:
        pago = _como_instante(p.get("paid_at"))
        pronto = _como_instante(p.get("pronto_em"))
        coletado = _como_instante(p.get("coletado_em"))
        atraso = ((coletado or agora_brt) - fim_do_prazo).total_seconds() / 3600
        pedidos.append({
            "brand": str(p.get("brand")), "order_id": str(p.get("order_id")),
            "shop_name": p.get("shop_name"), "status": p.get("order_status"),
            "status_on_day": p["status_no_dia"],
            "paid_at": _iso(pago), "ready_at": _iso(pronto), "collected_at": _iso(coletado),
            "hours_late": round(atraso, 1),
        })
    return {
        **base, "availability": "available", "brands": marcas_out,
        "all_brands": todas_marcas, "status_counts": contagem_status,
        "status_on_day_counts": contagem_dia,
        "list_total": len(filtrados),
        "still_waiting": sum(b["still_waiting"] for b in marcas_out),
        "orders": pedidos, "has_more": ini + TAMANHO_PAGINA < len(filtrados),
    }
