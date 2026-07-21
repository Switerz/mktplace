"""
Comparador GMV/faturamento: XLSX (referência) vs Torre (candidato/real).

Gate R1 do Projeto R — reconciliação XLSX x Torre, jan-mai/2026.

Le dois CSVs no grao (marketplace, brand, month):

    marketplace,brand,month,gmv_reference   (referencia XLSX)
    marketplace,brand,month,gmv_actual      (candidato/real, ex.: baseline Torre)

Valida esquema, grao, celulas ausentes/extras, duplicidade, nulos/invalidos,
valores nao-finitos (NaN/Infinity) e GMV negativo antes de comparar. Calcula,
por celula, diferenca em reais, diferenca percentual e erro absoluto; por
canal e total, GMV de referencia, erro absoluto acumulado e erro percentual
sobre a referencia.

Nao conecta nem escreve em nenhum banco. Nao possui abstracao alem do
necessario para esta reconciliacao (marketplace x brand x month -> gmv).

Uso:
    python -m pipelines.reconciliation.reconcile_xlsx_torre_gmv \\
        --reference docs/reconciliation/xlsx_gmv_reference_jan_maio_2026.csv \\
        --candidate docs/reconciliation/torre_gmv_baseline_20260721.csv

    python -m pipelines.reconciliation.reconcile_xlsx_torre_gmv \\
        --reference ... --candidate ... --json

Exit code:
    0  contrato valido (mesmo que existam diferencas de valor)
    2  contrato invalido (esquema, grao, duplicidade, celula ausente/extra,
       nulo, valor invalido/nao-finito/negativo, ou falha de leitura do CSV)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, DecimalException

REQUIRED_COLUMNS = {
    "reference": ("marketplace", "brand", "month", "gmv_reference"),
    "candidate": ("marketplace", "brand", "month", "gmv_actual"),
}

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

Cell = tuple[str, str, str]  # (marketplace, brand, month)


@dataclass
class LoadResult:
    values: dict[Cell, Decimal]
    errors: list[str] = field(default_factory=list)


def _load_csv(path: str, kind: str) -> LoadResult:
    value_col = REQUIRED_COLUMNS[kind][-1]
    errors: list[str] = []
    values: dict[Cell, Decimal] = {}
    seen_count: dict[Cell, int] = {}

    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header = tuple(reader.fieldnames or ())
            if header != REQUIRED_COLUMNS[kind]:
                errors.append(
                    f"{path}: cabecalho invalido. Esperado {REQUIRED_COLUMNS[kind]}, "
                    f"recebido {header}."
                )
                return LoadResult(values={}, errors=errors)

            for line_no, row in enumerate(reader, start=2):
                marketplace = (row["marketplace"] or "").strip()
                brand = (row["brand"] or "").strip()
                month = (row["month"] or "").strip()
                raw_value = row[value_col]

                if not marketplace or not brand or not month:
                    errors.append(f"{path}:{line_no}: chave de grao incompleta ({row}).")
                    continue

                if not MONTH_RE.match(month):
                    errors.append(f"{path}:{line_no}: month invalido '{month}' (esperado YYYY-MM).")
                    continue

                key = (marketplace, brand, month)
                seen_count[key] = seen_count.get(key, 0) + 1

                if raw_value is None or str(raw_value).strip() == "":
                    errors.append(f"{path}:{line_no}: {value_col} nulo/vazio para {key}.")
                    continue

                try:
                    value = Decimal(str(raw_value).strip())
                except DecimalException:
                    errors.append(f"{path}:{line_no}: {value_col} invalido '{raw_value}' para {key}.")
                    continue

                if not value.is_finite():
                    errors.append(
                        f"{path}:{line_no}: {value_col} nao-finito ('{raw_value}', NaN/Infinity "
                        f"nao sao GMV valido) para {key}."
                    )
                    continue

                if value < 0:
                    errors.append(f"{path}:{line_no}: {value_col} negativo ({value}) para {key}.")
                    continue

                if key in values:
                    # duplicidade sera reportada uma vez, abaixo, usando seen_count
                    continue
                values[key] = value
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        errors.append(f"{path}: falha ao ler CSV ({exc.__class__.__name__}: {exc}).")
        return LoadResult(values={}, errors=errors)

    duplicates = sorted(k for k, n in seen_count.items() if n > 1)
    for key in duplicates:
        errors.append(f"{path}: celula duplicada no grao (marketplace, brand, month) = {key}.")

    return LoadResult(values=values, errors=errors)


@dataclass
class CellDiff:
    marketplace: str
    brand: str
    month: str
    gmv_reference: Decimal
    gmv_actual: Decimal
    diff_reais: Decimal
    diff_pct: Decimal | None
    abs_error: Decimal


@dataclass
class ChannelSummary:
    marketplace: str
    gmv_reference_total: Decimal
    abs_error_total: Decimal
    pct_error: Decimal | None
    cells: int


@dataclass
class ReconciliationResult:
    cells: list[CellDiff]
    channels: list[ChannelSummary]
    total: ChannelSummary


def reconcile(reference: dict[Cell, Decimal], candidate: dict[Cell, Decimal]) -> ReconciliationResult:
    cells: list[CellDiff] = []
    for key in sorted(reference):
        marketplace, brand, month = key
        ref_value = reference[key]
        actual_value = candidate[key]
        diff_reais = actual_value - ref_value
        diff_pct = (diff_reais / ref_value * Decimal("100")) if ref_value != 0 else None
        cells.append(
            CellDiff(
                marketplace=marketplace,
                brand=brand,
                month=month,
                gmv_reference=ref_value,
                gmv_actual=actual_value,
                diff_reais=diff_reais,
                diff_pct=diff_pct,
                abs_error=abs(diff_reais),
            )
        )

    channels: list[ChannelSummary] = []
    for marketplace in sorted({c.marketplace for c in cells}):
        subset = [c for c in cells if c.marketplace == marketplace]
        ref_total = sum((c.gmv_reference for c in subset), Decimal("0"))
        err_total = sum((c.abs_error for c in subset), Decimal("0"))
        pct = (err_total / ref_total * Decimal("100")) if ref_total != 0 else None
        channels.append(
            ChannelSummary(
                marketplace=marketplace,
                gmv_reference_total=ref_total,
                abs_error_total=err_total,
                pct_error=pct,
                cells=len(subset),
            )
        )

    ref_grand_total = sum((c.gmv_reference for c in cells), Decimal("0"))
    err_grand_total = sum((c.abs_error for c in cells), Decimal("0"))
    pct_grand = (err_grand_total / ref_grand_total * Decimal("100")) if ref_grand_total != 0 else None
    total = ChannelSummary(
        marketplace="TOTAL",
        gmv_reference_total=ref_grand_total,
        abs_error_total=err_grand_total,
        pct_error=pct_grand,
        cells=len(cells),
    )

    return ReconciliationResult(cells=cells, channels=channels, total=total)


def _validate_grain(reference: dict[Cell, Decimal], candidate: dict[Cell, Decimal]) -> list[str]:
    errors: list[str] = []
    ref_keys = set(reference)
    cand_keys = set(candidate)

    missing = sorted(ref_keys - cand_keys)
    extra = sorted(cand_keys - ref_keys)

    for key in missing:
        errors.append(f"celula ausente no candidato (presente na referencia): {key}.")
    for key in extra:
        errors.append(f"celula extra no candidato (ausente na referencia): {key}.")

    return errors


def run(reference_path: str, candidate_path: str) -> tuple[ReconciliationResult | None, list[str]]:
    ref_result = _load_csv(reference_path, "reference")
    cand_result = _load_csv(candidate_path, "candidate")

    errors = list(ref_result.errors) + list(cand_result.errors)
    if errors:
        return None, errors

    grain_errors = _validate_grain(ref_result.values, cand_result.values)
    if grain_errors:
        return None, grain_errors

    return reconcile(ref_result.values, cand_result.values), []


def _fmt_brl(value: Decimal) -> str:
    return f"R$ {value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _fmt_pct(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


def render_text(result: ReconciliationResult) -> str:
    lines = ["=== Reconciliacao GMV/Faturamento — XLSX x Torre ===", ""]
    lines.append(f"{'marketplace':<15}{'brand':<12}{'month':<10}"
                 f"{'ref':>16}{'atual':>16}{'diff_R$':>14}{'diff_%':>10}")
    for c in result.cells:
        lines.append(
            f"{c.marketplace:<15}{c.brand:<12}{c.month:<10}"
            f"{c.gmv_reference:>16.2f}{c.gmv_actual:>16.2f}"
            f"{c.diff_reais:>14.2f}{_fmt_pct(c.diff_pct):>10}"
        )

    lines.append("")
    lines.append("--- Por canal ---")
    for ch in result.channels:
        lines.append(
            f"{ch.marketplace:<15} celulas={ch.cells:<4} "
            f"ref={_fmt_brl(ch.gmv_reference_total):>20} "
            f"erro_abs={_fmt_brl(ch.abs_error_total):>20} "
            f"erro_%={_fmt_pct(ch.pct_error)}"
        )

    lines.append("")
    lines.append("--- Total ---")
    t = result.total
    lines.append(
        f"celulas={t.cells} ref={_fmt_brl(t.gmv_reference_total)} "
        f"erro_abs={_fmt_brl(t.abs_error_total)} erro_%={_fmt_pct(t.pct_error)}"
    )
    return "\n".join(lines)


def render_json(result: ReconciliationResult) -> str:
    def cell_to_dict(c: CellDiff) -> dict:
        return {
            "marketplace": c.marketplace,
            "brand": c.brand,
            "month": c.month,
            "gmv_reference": str(c.gmv_reference),
            "gmv_actual": str(c.gmv_actual),
            "diff_reais": str(c.diff_reais),
            "diff_pct": None if c.diff_pct is None else str(c.diff_pct),
            "abs_error": str(c.abs_error),
        }

    def channel_to_dict(ch: ChannelSummary) -> dict:
        return {
            "marketplace": ch.marketplace,
            "cells": ch.cells,
            "gmv_reference_total": str(ch.gmv_reference_total),
            "abs_error_total": str(ch.abs_error_total),
            "pct_error": None if ch.pct_error is None else str(ch.pct_error),
        }

    payload = {
        "cells": [cell_to_dict(c) for c in result.cells],
        "channels": [channel_to_dict(ch) for ch in result.channels],
        "total": channel_to_dict(result.total),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconciliacao GMV/Faturamento — XLSX (referencia) x Torre (candidato)."
    )
    parser.add_argument("--reference", required=True, help="CSV de referencia (gmv_reference).")
    parser.add_argument("--candidate", required=True, help="CSV candidato/real (gmv_actual).")
    parser.add_argument("--json", action="store_true", help="Emite saida em JSON em vez de texto.")
    args = parser.parse_args(argv)

    result, errors = run(args.reference, args.candidate)
    if errors:
        for e in errors:
            print(f"ERRO DE CONTRATO: {e}", file=sys.stderr)
        return 2

    assert result is not None
    print(render_json(result) if args.json else render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
