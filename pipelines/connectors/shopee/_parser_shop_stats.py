"""
Parser de arquivos shop-stats xlsx exportados da Shopee.

Complementa o parser de orders (Fase 1) com métricas de funil:
visitantes, taxa de conversão, novos compradores, recompra.

Estrutura da sheet 'Pedido Feito' (sheet ativa):
  Row 0: header (linha de totais)
  Row 1: totais do período (ex: '01/03/2026-31/03/2026')
  Row 2: vazio (separador)
  Row 3: header das linhas de detalhe
  Row 4+: as linhas de detalhe

Dois layouts de detalhe são reconhecidos, e SOMENTE dois (Gate SD1):

  - histórico/diário: coluna 'Data', uma linha por dia ('DD/MM/YYYY');
  - horário: coluna 'Tempo', 24 linhas ('DD/MM/YYYY HH:MM') de um único dia.

No layout horário a representação diária vem da LINHA DE TOTAL DO PERÍODO
(Row 1), nunca de uma hora isolada e nunca da soma de campos não aditivos.
A soma das 24 horas é usada apenas como VALIDAÇÃO dos campos comprovadamente
aditivos — ver `_ADDITIVE_HOURLY_COLS` e a prova agregada do Gate SD1:

  aditivos (soma 24h == total, nas 5 marcas):
      Vendas (BRL), Vendas Sem os Descontos da Shopee, Vendas Canceladas,
      Vendas Devolvidas / Reembolsadas, Pedidos, Pedidos Cancelados,
      Pedidos Devolvidos / Reembolsados, Cliques Por Produto
  razão derivada (nunca somar):
      Vendas por Pedido (= Vendas / Pedidos no total)
  razão não somável e NÃO derivável de Pedidos/Visitantes (usar o total):
      Taxa de Conversão de Pedidos
  únicos deduplicados no dia (soma 24h > total sempre; usar o total):
      Visitantes

Qualquer terceiro layout continua bloqueando (fail-fast), sem fallback.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import openpyxl

from pipelines.common.logging import get_logger
from pipelines.connectors.shopee._numeric import parse_brl_float

logger = get_logger(__name__)

_COL_MAP: dict[str, str] = {
    "Data":                         "date_str",
    "Visitantes":                   "visitors",
    "Taxa de Conversão de Pedidos": "conversion_rate_str",
    "# de compradores":             "unique_buyers",
    "# de novos compradores":       "new_buyers",
    "# de compradores existentes":  "repeat_buyers",
    "Repetir Índice de Compras":    "repeat_buyer_rate_str",

    # Gate R2.1 (Projeto R): shop-stats é a fonte autoritativa do GMV
    # Shopee — reutiliza o parser numérico canônico de _numeric.py, nunca
    # um parser monetário novo.
    "Vendas (BRL)":                        "sales_brl_str",
    "Vendas Canceladas":                   "cancelled_sales_str",
    "Vendas Devolvidas / Reembolsadas":    "refunded_sales_str",
}

# As três colunas financeiras são obrigatórias para calcular o GMV líquido.
# Ausência de qualquer uma delas BLOQUEIA o parsing do arquivo (nunca um
# warning silencioso que deixaria o GMV incompleto).
_FINANCIAL_REQUIRED_KEYS = {"sales_brl_str", "cancelled_sales_str", "refunded_sales_str"}

# --- Layout horário (Gate SD1) -------------------------------------------
# Nomes de coluna que IDENTIFICAM cada layout. 'Tempo' nunca é tratado como
# alias de 'Data': são grãos diferentes (hora x dia) e caminhos distintos.
_DAILY_DATE_COL = "Data"
_HOURLY_DATE_COL = "Tempo"

# Linha 1 da planilha carrega o total do período, sob o mesmo header da
# linha 3 (mesma convenção já usada por shopee_raw/inventory.py).
_PERIOD_TOTAL_ROW = 1

_FINANCIAL_SOURCE_COLS = (
    "Vendas (BRL)",
    "Vendas Canceladas",
    "Vendas Devolvidas / Reembolsadas",
)

# Campos cuja aditividade foi COMPROVADA nas 5 marcas (soma 24h == total).
# Só estes são reconciliados; somar qualquer outro seria inventar semântica.
_ADDITIVE_HOURLY_COLS = _FINANCIAL_SOURCE_COLS + ("Pedidos",)

# Tolerância de arredondamento na reconciliação soma-horária x total.
_HOURLY_SUM_TOLERANCE = 0.02

# O contrato comprovado é o de um dia FECHADO: 24 horas, 00 a 23. Um export
# intradiário (dia corrente, parcial) não é suportado e bloqueia — melhor
# recusar do que publicar um "dia" com cobertura silenciosamente parcial.
_HOURLY_EXPECTED_HOURS = tuple(range(24))

_HOURLY_TS_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})$")
_PERIOD_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s*-\s*(\d{2})/(\d{2})/(\d{4})$")


class ShopeeShopStatsError(ValueError):
    """Erro de contrato do shop-stats: colunas financeiras obrigatórias
    ausentes, valor nulo inesperado numa linha diária válida, ou GMV
    líquido negativo. Sempre bloqueia — nunca produz um GMV incompleto ou
    incorreto silenciosamente."""


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


def _parse_period(val) -> Optional[tuple[date, date]]:
    """'10/08/2026-10/08/2026' → (date, date). Qualquer outro formato → None."""
    if val is None:
        return None
    m = _PERIOD_RE.match(str(val).strip())
    if not m:
        return None
    try:
        d0 = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        d1 = date(int(m.group(6)), int(m.group(5)), int(m.group(4)))
    except ValueError:
        return None
    return d0, d1


def _parse_hourly_ts(val) -> Optional[tuple[date, int]]:
    """'10/08/2026 13:00' → (date, 13). Minuto diferente de 00 → None
    (o contrato comprovado tem sempre HH:00; um minuto qualquer indicaria
    outro grão e deve bloquear em vez de ser silenciosamente truncado)."""
    if val is None:
        return None
    m = _HOURLY_TS_RE.match(str(val).strip())
    if not m or m.group(5) != "00":
        return None
    try:
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None
    hora = int(m.group(4))
    if not 0 <= hora <= 23:
        return None
    return d, hora


def _read_hourly_layout(path: Path, header: tuple, rows: list) -> list[dict]:
    """Layout horário → EXATAMENTE uma linha diária, vinda da linha de total.

    Fail-fast em qualquer desvio do contrato comprovado no Gate SD1. As
    mensagens citam só nome de arquivo e de coluna — nunca valores.
    """
    col_index: dict[str, int] = {}
    for i, name in enumerate(header):
        if name and name not in col_index:
            col_index[name] = i

    faltando = [c for c in _FINANCIAL_SOURCE_COLS if c not in col_index]
    if faltando:
        raise ShopeeShopStatsError(
            f"{path.name}: layout horário sem coluna financeira obrigatória: {sorted(faltando)}"
        )

    def cell(row, name):
        idx = col_index.get(name)
        return row[idx] if idx is not None and idx < len(row) else None

    if len(rows) <= _PERIOD_TOTAL_ROW:
        raise ShopeeShopStatsError(f"{path.name}: layout horário sem linha de total do período")
    periodo = _parse_period(cell(rows[_PERIOD_TOTAL_ROW], _HOURLY_DATE_COL))
    if periodo is None:
        raise ShopeeShopStatsError(
            f"{path.name}: layout horário sem linha de total do período válida "
            f"(coluna {_HOURLY_DATE_COL!r} da linha de total)"
        )
    dia, dia_fim = periodo
    if dia != dia_fim:
        raise ShopeeShopStatsError(
            f"{path.name}: layout horário com total de período cobrindo mais de um dia"
        )
    total_row = rows[_PERIOD_TOTAL_ROW]

    horas: dict[int, tuple] = {}
    for row in rows[4:]:
        if all(v is None for v in row):
            continue
        ts = _parse_hourly_ts(cell(row, _HOURLY_DATE_COL))
        if ts is None:
            raise ShopeeShopStatsError(
                f"{path.name}: linha de detalhe com {_HOURLY_DATE_COL!r} fora do formato "
                f"horário esperado (DD/MM/YYYY HH:00)"
            )
        d, hora = ts
        if d != dia:
            raise ShopeeShopStatsError(
                f"{path.name}: layout horário com mais de uma data nas linhas de detalhe"
            )
        if hora in horas:
            raise ShopeeShopStatsError(
                f"{path.name}: layout horário com hora duplicada ({hora:02d})"
            )
        horas[hora] = row

    if tuple(sorted(horas)) != _HOURLY_EXPECTED_HOURS:
        ausentes = [h for h in _HOURLY_EXPECTED_HOURS if h not in horas]
        raise ShopeeShopStatsError(
            f"{path.name}: layout horário deve ter as 24 horas de um dia fechado; "
            f"faltam {len(ausentes)} hora(s)"
        )

    # Reconciliação: soma das 24 horas == total, SOMENTE nos campos aditivos.
    for coluna in _ADDITIVE_HOURLY_COLS:
        if coluna not in col_index:
            continue
        total = parse_brl_float(cell(total_row, coluna))
        if total is None:
            raise ShopeeShopStatsError(
                f"{path.name}: coluna aditiva {coluna!r} sem valor na linha de total"
            )
        soma = 0.0
        for hora in _HOURLY_EXPECTED_HOURS:
            v = parse_brl_float(cell(horas[hora], coluna))
            if v is None:
                raise ShopeeShopStatsError(
                    f"{path.name}: coluna aditiva {coluna!r} sem valor na hora {hora:02d}"
                )
            soma += v
        if abs(round(soma - total, 2)) > _HOURLY_SUM_TOLERANCE:
            raise ShopeeShopStatsError(
                f"{path.name}: soma das 24 horas não reconcilia com o total do período "
                f"na coluna {coluna!r}"
            )

    sales_brl = parse_brl_float(cell(total_row, "Vendas (BRL)"))
    cancelled_sales_brl = parse_brl_float(cell(total_row, "Vendas Canceladas"))
    refunded_sales_brl = parse_brl_float(cell(total_row, "Vendas Devolvidas / Reembolsadas"))
    if sales_brl is None or cancelled_sales_brl is None or refunded_sales_brl is None:
        raise ShopeeShopStatsError(
            f"{path.name}: dia {dia.isoformat()} tem campo financeiro obrigatório "
            f"ausente na linha de total do período"
        )
    if sales_brl < 0 or cancelled_sales_brl < 0 or refunded_sales_brl < 0:
        raise ShopeeShopStatsError(
            f"{path.name}: dia {dia.isoformat()} tem valor financeiro negativo "
            f"na linha de total do período"
        )

    gmv = round(sales_brl - cancelled_sales_brl - refunded_sales_brl, 2)
    if gmv < 0:
        raise ShopeeShopStatsError(
            f"{path.name}: dia {dia.isoformat()} produziu GMV líquido negativo "
            f"(Vendas Canceladas + Devolvidas/Reembolsadas maior que Vendas (BRL))"
        )

    # Visitantes e taxa de conversão vêm do TOTAL — nunca somados (visitantes
    # são únicos deduplicados no dia; conversão é razão). Colunas de
    # compradores não existem neste layout: ficam None, nunca 0.
    return [{
        "date":                  dia,
        "visitors":              _parse_int(cell(total_row, "Visitantes")),
        "conversion_rate":       _parse_pct(cell(total_row, "Taxa de Conversão de Pedidos")),
        "unique_buyers":         _parse_int(cell(total_row, "# de compradores")),
        "new_buyers":            _parse_int(cell(total_row, "# de novos compradores")),
        "repeat_buyers":         _parse_int(cell(total_row, "# de compradores existentes")),
        "repeat_buyer_rate_pct": _parse_pct(cell(total_row, "Repetir Índice de Compras")),
        "sales_brl":             round(sales_brl, 2),
        "cancelled_sales_brl":   round(cancelled_sales_brl, 2),
        "refunded_sales_brl":    round(refunded_sales_brl, 2),
        "gmv":                   gmv,
    }]


def _read_xlsx(path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    if len(rows) < 5:
        logger.warning("%s: menos de 5 linhas — ignorado", path.name)
        return []

    # Row 3 (índice 3) é o header das linhas de detalhe
    header = rows[3]

    # Roteamento de layout (Gate SD1): 'Tempo' sem 'Data' é o layout horário.
    # O caminho diário abaixo permanece exatamente como estava.
    nomes_header = {h for h in header if h}
    if _HOURLY_DATE_COL in nomes_header and _DAILY_DATE_COL not in nomes_header:
        return _read_hourly_layout(path, header, rows)
    col_index: dict[str, int] = {}
    for i, name in enumerate(header):
        if not name:
            continue
        key = _COL_MAP.get(name)
        if key and key not in col_index:
            col_index[key] = i

    missing = set(_COL_MAP.values()) - set(col_index.keys())
    missing_financial = _FINANCIAL_REQUIRED_KEYS & missing
    if missing_financial:
        # Bloqueia — nunca um warning silencioso que deixaria o GMV
        # incompleto ou inexistente sem que o chamador perceba.
        raise ShopeeShopStatsError(
            f"{path.name}: colunas financeiras obrigatórias ausentes no header "
            f"diário: {sorted(missing_financial)}"
        )
    if "date_str" in missing:
        # Gate SD1: sem coluna de data e sem ser o layout horário, este é um
        # TERCEIRO layout desconhecido. Antes, todas as linhas eram descartadas
        # silenciosamente (0 linhas, só um warning) — uma carga "success" com
        # zero dado. Agora bloqueia explicitamente, como as financeiras.
        raise ShopeeShopStatsError(
            f"{path.name}: header de detalhe sem coluna de data reconhecida "
            f"({_DAILY_DATE_COL!r} para o layout diário, {_HOURLY_DATE_COL!r} para o "
            f"horário) — layout desconhecido, nenhuma linha seria produzida"
        )
    missing_nao_financeiro = missing - _FINANCIAL_REQUIRED_KEYS
    if missing_nao_financeiro:
        logger.warning("%s: colunas ausentes no header diário: %s", path.name, missing_nao_financeiro)

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

        sales_brl = parse_brl_float(_get("sales_brl_str"))
        cancelled_sales_brl = parse_brl_float(_get("cancelled_sales_str"))
        refunded_sales_brl = parse_brl_float(_get("refunded_sales_str"))

        # Linha diária válida (tem data) mas com campo financeiro
        # obrigatório vazio/ausente: bloqueia — nunca vira 0 silencioso
        # (diferente do contrato de Order.all, onde ausência = "sem
        # contribuição"; aqui as 3 colunas são obrigatórias por definição
        # do relatório gerencial da Shopee).
        if sales_brl is None or cancelled_sales_brl is None or refunded_sales_brl is None:
            raise ShopeeShopStatsError(
                f"{path.name}: dia {d.isoformat()} tem campo financeiro obrigatório "
                f"ausente (Vendas (BRL) / Vendas Canceladas / Vendas Devolvidas ou "
                f"Reembolsadas)"
            )

        gmv = round(sales_brl - cancelled_sales_brl - refunded_sales_brl, 2)
        if gmv < 0:
            raise ShopeeShopStatsError(
                f"{path.name}: dia {d.isoformat()} produziu GMV líquido negativo "
                f"(Vendas Canceladas + Devolvidas/Reembolsadas maior que Vendas (BRL))"
            )

        result.append({
            "date":                  d,
            "visitors":              _parse_int(_get("visitors")),
            "conversion_rate":       _parse_pct(_get("conversion_rate_str")),
            "unique_buyers":         _parse_int(_get("unique_buyers")),
            "new_buyers":            _parse_int(_get("new_buyers")),
            "repeat_buyers":         _parse_int(_get("repeat_buyers")),
            "repeat_buyer_rate_pct": _parse_pct(_get("repeat_buyer_rate_str")),
            "sales_brl":             round(sales_brl, 2),
            "cancelled_sales_brl":   round(cancelled_sales_brl, 2),
            "refunded_sales_brl":    round(refunded_sales_brl, 2),
            "gmv":                   gmv,
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
