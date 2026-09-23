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
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from pipelines.common.logging import get_logger
from pipelines.connectors.shopee._numeric import ShopeeNumericParseError, parse_brl_float

logger = get_logger(__name__)


class AdsPeriodoInvalido(ValueError):
    """Período do cabeçalho ausente, ilegível ou invertido.

    Antes deste gate o arquivo era descartado com um `logger.warning` e um
    `return []` — silencioso. Um export sem período não tem denominador de
    rateio, e ignorá-lo esconde uma lacuna em vez de declará-la.
    """


class AdsSnapshotAmbiguo(ValueError):
    """Dois exports com o MESMO período disputam a mesma data.

    Não há desempate confiável entre eles — nem `mtime` nem nome de arquivo
    são contrato. Falha fechado em vez de escolher um arbitrariamente.
    """


def _chave_de_ordem(periodo: tuple[date, date]) -> tuple[date, date]:
    """A regra do vencedor, num lugar só: `date_to` primeiro, `date_from` como
    desempate. Espelha `_snapshots.Snapshot.chave_de_ordem` de propósito — mas
    NÃO reaproveita aquele módulo, que modela `Order.all`: multipartes,
    `_part_N_of_M` e janela no NOME. Aqui a janela vem do CABEÇALHO, porque
    `Dados*.csv` não a carrega no nome de forma confiável.
    """
    date_from, date_to = periodo
    return (date_to, date_from)


def deduplicar_ads_por_data(
    lotes: list[tuple[tuple[date, date], str, list[dict]]],
) -> list[dict]:
    """Mantém, para cada data, somente a linha do snapshot vencedor.

    `lotes` chega em qualquer ordem e o resultado não depende dela: a escolha
    é um `max` sobre `_chave_de_ordem`, e a saída sai ordenada por data.

    Nunca SOMA duas linhas concorrentes. Cada linha já é o total do período
    rateado pelos dias daquele período; somar duas leituras do mesmo dia
    contaria a mesma mídia duas vezes.
    """
    por_data: dict[date, list[tuple[tuple[date, date], str, dict]]] = defaultdict(list)
    for periodo, nome_arquivo, linhas in lotes:
        for linha in linhas:
            por_data[linha["date"]].append((periodo, nome_arquivo, linha))

    resultado: list[dict] = []
    for dia in sorted(por_data):
        candidatos = por_data[dia]
        melhor = max(_chave_de_ordem(p) for p, _, _ in candidatos)
        vencedores = [c for c in candidatos if _chave_de_ordem(c[0]) == melhor]
        arquivos = sorted({nome for _, nome, _ in vencedores})
        if len(arquivos) > 1:
            raise AdsSnapshotAmbiguo(
                f"dois exports de Ads com o MESMO período "
                f"{melhor[1]:%Y-%m-%d}..{melhor[0]:%Y-%m-%d} disputam {dia:%Y-%m-%d} "
                f"e não há desempate confiável: {', '.join(arquivos)}"
            )
        resultado.append(vencedores[0][2])
    return resultado


_COL_MAP: dict[str, str] = {
    "Impressões":    "impressions",
    "Cliques":       "clicks",
    "Despesas":      "spend",
    "GMV":           "gmv",
}


def _to_float(val, *, brand: str, file_name: str, ad_index: int, field: str) -> float:
    """Ausente vira 0.0 (contrato de _numeric.py). Não vazio e inválido
    NUNCA vira 0.0: uma NOVA exceção com contexto sanitizado (marca/
    arquivo/índice do anúncio/campo, nunca o valor bruto) propaga —
    fail-fast, interrompe a leitura deste arquivo. Ver _parser.py::_to_float
    para a mesma decisão de design (flag booleana antes do `raise`, para
    __cause__/__context__ ficarem None — a exceção original nunca é
    encadeada)."""
    parse_ok = True
    parsed = None
    try:
        parsed = parse_brl_float(val)
    except ShopeeNumericParseError:
        parse_ok = False

    if not parse_ok:
        raise ShopeeNumericParseError(
            f"valor numérico inválido: brand={brand} arquivo={file_name} anuncio_idx={ad_index} campo={field}"
        ) from None

    return parsed if parsed is not None else 0.0


#: Rótulo do período, já normalizado — a comparação é feita em NFC.
_ROTULO_PERIODO = unicodedata.normalize("NFC", "Período")


def _e_linha_de_periodo(line: str) -> bool:
    """A linha do cabeçalho que declara o período.

    A comparação normaliza para NFC e ignora espaço/BOM à esquerda. Não é
    frescura: até este gate a recusa custava UM arquivo (`logger.warning` +
    `return []`); agora ela derruba a marca inteira, e recusar um cabeçalho
    legítimo por causa de um `í` decomposto seria caro demais.

    O código anterior era `startswith("Período,") or startswith("Período,")` —
    os dois literais eram byte a byte IGUAIS, então o segundo braço nunca
    executava. Prometia tolerância a uma variante e não entregava nenhuma.

    Continua exigindo o rótulo acentuado: `Periodo` sem acento e o rótulo
    entre aspas não são o formato do export, e aceitar aspas mudaria também
    o parsing do VALOR.
    """
    limpo = unicodedata.normalize("NFC", line).lstrip("﻿ \t")
    return limpo.startswith(_ROTULO_PERIODO + ",")


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

    lotes: list[tuple[tuple[date, date], str, list[dict]]] = []
    for f in files:
        periodo, rows = _ler_arquivo_ads(f, brand)
        if periodo is None:
            continue          # header/anúncios ausentes — já registrado em log
        lotes.append((periodo, f.name, rows))
        logger.info("Ads/%s %s: %d dias gerados", brand, f.name, len(rows))

    linhas_antes = sum(len(r) for _, _, r in lotes)
    all_rows = deduplicar_ads_por_data(lotes)
    logger.info(
        "Ads/%s: %d dia(s) de %d arquivo(s) -> %d dia(s) apos desempate "
        "(%d descartado(s) por snapshot perdedor)",
        brand, linhas_antes, len(files), len(all_rows), linhas_antes - len(all_rows),
    )
    return all_rows


def _parse_ads_file(path: Path, brand: str) -> list[dict]:
    """Compatibilidade: só as linhas, sem o período. O caminho oficial é
    `_ler_arquivo_ads`, que devolve o período necessário ao desempate."""
    return _ler_arquivo_ads(path, brand)[1]


def _ler_arquivo_ads(
    path: Path, brand: str,
) -> tuple[Optional[tuple[date, date]], list[dict]]:
    with open(path, encoding="utf-8-sig") as f:
        lines = f.readlines()

    # Extrai período do cabeçalho (linha 5: "Período,DD/MM/AAAA - DD/MM/AAAA")
    date_from, date_to = None, None
    for line in lines[:7]:
        if _e_linha_de_periodo(line):
            _, _, period_str = line.partition(",")
            date_from, date_to = _parse_period(period_str.strip())
            break

    if not date_from or not date_to:
        raise AdsPeriodoInvalido(
            f"período do cabeçalho ausente ou ilegível: brand={brand} "
            f"arquivo={path.name}. Sem período não há denominador de rateio."
        ) from None

    if date_from > date_to:
        raise AdsPeriodoInvalido(
            f"período invertido: brand={brand} arquivo={path.name} "
            f"{date_from:%Y-%m-%d}..{date_to:%Y-%m-%d}"
        ) from None

    num_days = (date_to - date_from).days + 1

    # Encontra linha de header (primeira linha com '#' como primeiro campo)
    header_line_idx = None
    for i, line in enumerate(lines):
        if line.startswith("#,"):
            header_line_idx = i
            break

    if header_line_idx is None:
        logger.warning("%s: header de colunas não encontrado", path.name)
        return None, []

    reader = csv.DictReader(lines[header_line_idx:])

    totals = {"impressions": 0.0, "clicks": 0.0, "spend": 0.0, "gmv": 0.0}
    n_ads = 0
    for row in reader:
        for csv_col, key in _COL_MAP.items():
            totals[key] += _to_float(row.get(csv_col, ""), brand=brand, file_name=path.name, ad_index=n_ads, field=key)
        n_ads += 1

    if n_ads == 0:
        logger.warning("%s: nenhuma linha de anúncio encontrada", path.name)
        return None, []

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

    return (date_from, date_to), result
