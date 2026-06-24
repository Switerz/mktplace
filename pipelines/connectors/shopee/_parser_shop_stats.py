"""
Parser de arquivos shop-stats xlsx exportados da Shopee.

Complementa o parser de orders (Fase 1) com métricas de funil:
visitantes, taxa de conversão, novos compradores, recompra.

Estrutura da sheet 'Pedido Feito' (sheet ativa):
  Row 0: header (linha de totais)
  Row 1: totais do período (ex: '01/03/2026-31/03/2026')
  Row 2: vazio (separador)
  Row 3: header das linhas diárias
  Row 4+: uma linha por dia no formato 'DD/MM/YYYY'
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

import openpyxl

from pipelines.common.logging import get_logger

logger = get_logger(__name__)

_COL_MAP: dict[str, str] = {
    "Data":                         "date_str",
    "Visitantes":                   "visitors",
    "Taxa de Conversão de Pedidos": "conversion_rate_str",
    "# de compradores":             "unique_buyers",
    "# de novos compradores":       "new_buyers",
    "# de compradores existentes":  "repeat_buyers",
    "Repetir Índice de Compras":    "repeat_buyer_rate_str",
}


def _parse_int(val) -> Optional[int]:
    if val is None:
        return None
    s = str(val).replace("\xa0", "").replace(".", "").replace(",", "").strip()
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _parse_pct(val) -> Optional[float]:
    """'3,84%' → 3.84"""
    if val is None:
        return None
    s = str(val).replace("%", "").replace(",", ".").strip()
    try:
        return round(float(s), 4)
    except (ValueError, TypeError):
        return None


def _parse_date(val) -> Optional[date]:
    """'01/03/2026' → date(2026, 3, 1). Ranges como '01/03-31/03' retornam None."""
    if val is None:
        return None
    s = str(val).strip()
    if "-" in s:
        return None
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None


def _read_xlsx(path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if len(rows) < 5:
        logger.warning("%s: menos de 5 linhas — ignorado", path.name)
        return []

    # Row 3 (índice 3) é o header das linhas diárias
    header = rows[3]
    col_index: dict[str, int] = {}
    for i, name in enumerate(header):
        if not name:
            continue
        key = _COL_MAP.get(name)
        if key and key not in col_index:
            col_index[key] = i

    missing = set(_COL_MAP.values()) - set(col_index.keys())
    if missing:
        logger.warning("%s: colunas ausentes no header diário: %s", path.name, missing)

    result = []
    for row in rows[4:]:
        if all(v is None for v in row):
            continue

        date_raw = row[col_index["date_str"]] if "date_str" in col_index else None
        d = _parse_date(date_raw)
        if d is None:
            continue

        def _get(key):
            idx = col_index.get(key)
            return row[idx] if idx is not None and idx < len(row) else None

        result.append({
            "date":                  d,
            "visitors":              _parse_int(_get("visitors")),
            "conversion_rate":       _parse_pct(_get("conversion_rate_str")),
            "unique_buyers":         _parse_int(_get("unique_buyers")),
            "new_buyers":            _parse_int(_get("new_buyers")),
            "repeat_buyers":         _parse_int(_get("repeat_buyers")),
            "repeat_buyer_rate_pct": _parse_pct(_get("repeat_buyer_rate_str")),
        })

    return result


def parse_brand_shop_stats(data_path: Path, brand: str) -> list[dict]:
    """
    Lê todos os shop-stats xlsx de uma marca.
    Retorna lista de dicts diários com 'brand' incluso.
    """
    brand_dir = data_path / brand
    if not brand_dir.exists():
        logger.warning("Pasta não encontrada para brand=%s: %s", brand, brand_dir)
        return []

    files = sorted(brand_dir.glob("*.shopee-shop-stats.*.xlsx"))
    if not files:
        logger.warning("Nenhum shop-stats xlsx em %s", brand_dir)
        return []

    all_rows: list[dict] = []
    for f in files:
        logger.debug("Lendo shop-stats %s", f.name)
        rows = _read_xlsx(f)
        for r in rows:
            r["brand"] = brand
        all_rows.extend(rows)

    logger.info("Shop-stats/%s: %d dias de %d arquivos", brand, len(all_rows), len(files))
    return all_rows
