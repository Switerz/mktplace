"""
Parser de arquivos Order.all*.xlsx exportados da Shopee.

Cada arquivo contém uma planilha "orders" com uma linha por SKU por pedido.
Brands diferentes têm pequenas variações no schema (colunas extras), então
o mapeamento é por nome — nunca por posição.

Aggregation em dois níveis:
  1. Linhas de SKU → resumo por order_id
  2. Resumos de pedido → métrica diária por brand
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import openpyxl

from pipelines.common.logging import get_logger

logger = get_logger(__name__)

# Mapeamento nome PT-BR da coluna → chave interna
# Primeira ocorrência de cada nome no header é usada (trata duplicatas como "Desconto do vendedor")
_COL_MAP: dict[str, str] = {
    "ID do pedido":                        "order_id",
    "Status do pedido":                    "status",
    "Status da Devolução / Reembolso":     "return_status",
    "Data de criação do pedido":           "order_date",
    "Quantidade":                          "qty",
    "Subtotal do produto":                 "subtotal",
    "Total global":                        "total_global",
    "Taxa de comissão líquida":            "commission_net",
    "Taxa de serviço líquida":             "service_fee_net",
    "Valor estimado do frete":             "freight_est",
    "Nome de usuário (comprador)":         "buyer_username",
}

_STATUS_CANCELLED = "Cancelado"
_STATUS_COMPLETED = "Concluído"


def _parse_float(val) -> float:
    if val is None:
        return 0.0
    s = str(val).replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    if not s or s in ("-", ""):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    s = str(val).strip()
    if len(s) >= 10:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


def _read_xlsx(path: Path) -> list[dict]:
    """Lê um arquivo Order.all xlsx. Retorna lista de dicts com chaves internas."""
    # read_only=True é incompatível com esses xlsx exportados da Shopee (retorna só 1 coluna)
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if not rows:
        return []

    header = rows[0]

    # Índice por chave interna, primeira ocorrência de cada nome PT-BR
    col_index: dict[str, int] = {}
    for i, cell_name in enumerate(header):
        if not cell_name:
            continue
        int_key = _COL_MAP.get(cell_name)
        if int_key and int_key not in col_index:
            col_index[int_key] = i

    missing = set(_COL_MAP.values()) - set(col_index.keys())
    if missing:
        logger.warning("%s: colunas ausentes no header: %s", path.name, missing)

    result: list[dict] = []
    for row in rows[1:]:
        if all(v is None for v in row):
            continue
        result.append(
            {key: (row[idx] if idx < len(row) else None) for key, idx in col_index.items()}
        )

    return result


def _aggregate_daily(rows: list[dict], brand: str) -> list[dict]:
    """
    Nível 1: colapsa linhas de SKU em um dict por order_id.
      - SKU-level (subtotal, qty): somados — cada linha tem seu próprio valor.
      - Order-level (total_global, commission_net, service_fee_net, freight_est):
        max() — a Shopee repete o mesmo valor em todas as linhas SKU do pedido.
      - Descritivos (status, buyer, date): primeira ocorrência não-nula.

    Nível 2: agrega pedidos por data de criação → métricas diárias.
    """
    # Nível 1 — SKU lines → order summary
    orders: dict[str, dict] = {}
    for row in rows:
        oid = row.get("order_id")
        if not oid:
            continue

        if oid not in orders:
            orders[oid] = {
                "order_id":       oid,
                "order_date":     _parse_date(row.get("order_date")),
                "status":         row.get("status") or "",
                "return_status":  row.get("return_status") or "",
                "buyer_username": row.get("buyer_username") or "",
                # SKU-level (acumular por soma)
                "subtotal": 0.0,
                "qty":      0.0,
                # Order-level (repetido em cada linha SKU — usar max para pegar 1 vez)
                "total_global":   0.0,
                "commission_net": 0.0,
                "service_fee_net": 0.0,
                "freight_est":    0.0,
            }

        o = orders[oid]

        # Descritivos: primeira não-nula ganha
        if not o["order_date"]:
            o["order_date"] = _parse_date(row.get("order_date"))
        if not o["status"]:
            o["status"] = row.get("status") or ""
        if not o["return_status"]:
            o["return_status"] = row.get("return_status") or ""
        if not o["buyer_username"]:
            o["buyer_username"] = row.get("buyer_username") or ""

        # SKU-level: soma
        o["subtotal"] += _parse_float(row.get("subtotal"))
        o["qty"]      += _parse_float(row.get("qty"))

        # Order-level: max (a Shopee repete o mesmo valor em todas as linhas SKU
        # de um pedido; max() captura o valor real descartando os zeros)
        for field in ("total_global", "commission_net", "service_fee_net", "freight_est"):
            v = _parse_float(row.get(field))
            if v > o[field]:
                o[field] = v

    # Nível 2 — pedidos → diário
    by_date: dict[date, list[dict]] = defaultdict(list)
    for o in orders.values():
        d = o["order_date"]
        if d is not None:
            by_date[d].append(o)

    results: list[dict] = []
    for d in sorted(by_date.keys()):
        day = by_date[d]

        cancelled = [o for o in day if o["status"] == _STATUS_CANCELLED]
        returned  = [o for o in day if o["return_status"] and o["status"] != _STATUS_CANCELLED]
        active    = [o for o in day if o["status"] != _STATUS_CANCELLED]
        completed = [o for o in active if o["status"] == _STATUS_COMPLETED]

        total_placed = len(day)
        orders_count = len(active)
        if orders_count == 0:
            continue

        gmv              = sum(o["subtotal"]       for o in active)
        units_sold       = int(sum(o["qty"]        for o in active))
        total_settlement = sum(o["total_global"]   for o in active)
        total_fees       = sum(o["commission_net"] + o["service_fee_net"] for o in active)
        seller_shipping  = sum(o["freight_est"]    for o in active)
        unique_buyers    = len({o["buyer_username"] for o in active if o["buyer_username"]})

        avg_ticket          = round(gmv / orders_count, 2)
        cancel_rate         = round(len(cancelled) / total_placed * 100, 2) if total_placed else 0.0
        avg_fee_pct         = round(total_fees / gmv * 100, 2) if gmv else 0.0
        avg_settlement_pct  = round(total_settlement / gmv * 100, 2) if gmv else 0.0
        shipping_pct        = round(seller_shipping / gmv * 100, 2) if gmv else 0.0

        results.append({
            "date":                 d,
            "brand":                brand,
            "gmv":                  round(gmv, 2),
            "orders":               orders_count,
            "units_sold":           units_sold,
            "avg_ticket":           avg_ticket,
            "unique_buyers":        unique_buyers,
            "canceled_orders":      len(cancelled),
            "returned_orders":      len(returned),
            "cancel_rate_pct":      cancel_rate,
            "delivered_orders":     len(completed),
            "total_settlement":     round(total_settlement, 2),
            "total_fees":           round(total_fees, 2),
            "avg_fee_pct":          avg_fee_pct,
            "avg_settlement_pct":   avg_settlement_pct,
            "seller_shipping_cost": round(seller_shipping, 2),
            "shipping_pct_of_gmv":  shipping_pct,
        })

    return results


def parse_brand(data_path: Path, brand: str) -> list[dict]:
    """
    Lê todos os Order.all*.xlsx de uma marca.
    Retorna lista de dicts diários: {date, brand, gmv, orders, ...}.
    """
    brand_dir = data_path / brand
    if not brand_dir.exists():
        logger.warning("Pasta não encontrada para brand=%s: %s", brand, brand_dir)
        return []

    files = sorted(brand_dir.glob("Order.all*.xlsx"))
    if not files:
        logger.warning("Nenhum Order.all*.xlsx em %s", brand_dir)
        return []

    all_rows: list[dict] = []
    for f in files:
        logger.debug("Lendo %s", f.name)
        all_rows.extend(_read_xlsx(f))

    logger.info(
        "Brand=%s: %d linhas de SKU de %d arquivos",
        brand, len(all_rows), len(files),
    )
    return _aggregate_daily(all_rows, brand)
