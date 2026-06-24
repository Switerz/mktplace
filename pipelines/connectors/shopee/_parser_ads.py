"""
Parser de arquivos 'Dados Gerais de Anúncios Shopee' (CSV, CPC).

Cada arquivo cobre um período (ex: 01/01/2026 - 31/03/2026) e contém
uma linha por anúncio com totais do período — não há granularidade diária.

Estratégia: agrega todos os anúncios → totais do período → divide pelo
número de dias do período → valor diário médio para cada dia.

Isso é uma aproximação. Fica documentado em source_note no canonical.
Métricas derivadas (ROAS, CTR, CPC, ACOS) são calculadas dos totais
somados, não da média simples das colunas (evita distorção por peso).

Estrutura do CSV:
  Linha 0: "Relatório de Todos os Anúncios CPC - Shopee Brasil"
  Linhas 1-5: metadados (usuário, loja, id, data criação, período)
  Linha 6: vazio
  Linha 7: header
  Linhas 8+: uma por anúncio
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from pipelines.common.logging import get_logger

logger = get_logger(__name__)

_COL_MAP: dict[str, str] = {
    "Impressões":    "impressions",
    "Cliques":       "clicks",
    "Despesas":      "spend",
    "GMV":           "gmv",
}


def _parse_float(val: str) -> float:
    if not val or val.strip() in ("-", ""):
        return 0.0
    s = val.replace("\xa0", "").replace("%", "").replace(",", ".").strip()
    # Remove separador de milhar (ponto antes de 3 dígitos seguido de vírgula/ponto)
    # Formato BR: 1.234,56 → já tratado acima
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_period(period_str: str) -> tuple[Optional[date], Optional[date]]:
    """'01/01/2026 - 31/03/2026' → (date(2026,1,1), date(2026,3,31))"""
    try:
        parts = period_str.strip().split(" - ")
        d_from = datetime.strptime(parts[0].strip(), "%d/%m/%Y").date()
        d_to   = datetime.strptime(parts[1].strip(), "%d/%m/%Y").date()
        return d_from, d_to
    except (ValueError, IndexError):
        return None, None


def parse_brand_ads(data_path: Path, brand: str) -> list[dict]:
    """
    Lê todos os CSVs de ads de uma marca e retorna lista de dicts diários.
    Cada dict representa a média diária dos gastos/métricas do período.
    """
    brand_dir = data_path / brand
    if not brand_dir.exists():
        logger.warning("Pasta não encontrada para brand=%s: %s", brand, brand_dir)
        return []

    files = sorted(brand_dir.glob("Dados*.csv"))
    if not files:
        logger.warning("Nenhum CSV de ads em %s", brand_dir)
        return []

    all_rows: list[dict] = []
    for f in files:
        rows = _parse_ads_file(f, brand)
        all_rows.extend(rows)
        logger.info("Ads/%s %s: %d dias gerados", brand, f.name, len(rows))

    logger.info("Ads/%s: total %d dias de %d arquivos", brand, len(all_rows), len(files))
    return all_rows


def _parse_ads_file(path: Path, brand: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        lines = f.readlines()

    # Extrai período do cabeçalho (linha 5: "Período,DD/MM/AAAA - DD/MM/AAAA")
    date_from, date_to = None, None
    for line in lines[:7]:
        if line.startswith("Período,") or line.startswith("Período,"):
            _, _, period_str = line.partition(",")
            date_from, date_to = _parse_period(period_str.strip())
            break

    if not date_from or not date_to:
        logger.warning("%s: não foi possível extrair o período do cabeçalho", path.name)
        return []

    num_days = (date_to - date_from).days + 1

    # Encontra linha de header (primeira linha com '#' como primeiro campo)
    header_line_idx = None
    for i, line in enumerate(lines):
        if line.startswith("#,"):
            header_line_idx = i
            break

    if header_line_idx is None:
        logger.warning("%s: header de colunas não encontrado", path.name)
        return []

    reader = csv.DictReader(lines[header_line_idx:])

    totals = {"impressions": 0.0, "clicks": 0.0, "spend": 0.0, "gmv": 0.0}
    n_ads = 0
    for row in reader:
        for csv_col, key in _COL_MAP.items():
            totals[key] += _parse_float(row.get(csv_col, ""))
        n_ads += 1

    if n_ads == 0:
        logger.warning("%s: nenhuma linha de anúncio encontrada", path.name)
        return []

    logger.debug(
        "%s: %d anúncios | spend=%.2f gmv=%.2f imp=%.0f clk=%.0f | %d dias",
        path.name, n_ads, totals["spend"], totals["gmv"],
        totals["impressions"], totals["clicks"], num_days,
    )

    # Diários: divide pelo número de dias do período
    daily_spend       = totals["spend"]       / num_days
    daily_gmv         = totals["gmv"]         / num_days
    daily_impressions = totals["impressions"] / num_days
    daily_clicks      = totals["clicks"]      / num_days

    # Métricas derivadas calculadas dos totais (não da média das linhas)
    roas    = round(totals["gmv"]   / totals["spend"], 4)       if totals["spend"]       > 0 else None
    acos    = round(totals["spend"] / totals["gmv"] * 100, 4)   if totals["gmv"]         > 0 else None
    ctr     = round(totals["clicks"] / totals["impressions"] * 100, 4) if totals["impressions"] > 0 else None
    cpc     = round(totals["spend"] / totals["clicks"], 4)      if totals["clicks"]      > 0 else None

    result = []
    current = date_from
    while current <= date_to:
        result.append({
            "date":           current,
            "brand":          brand,
            "ad_spend":       round(daily_spend, 2),
            "ad_revenue":     round(daily_gmv, 2),
            "ad_impressions": int(round(daily_impressions)),
            "ad_clicks":      int(round(daily_clicks)),
            "roas":           roas,
            "acos_pct":       acos,
            "ctr_pct":        ctr,
            "cpc":            cpc,
        })
        current += timedelta(days=1)

    return result
