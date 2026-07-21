"""Testes focais do Gate R1 — comparador XLSX x Torre (GMV/faturamento)."""
from __future__ import annotations

import csv
import json
from decimal import Decimal

import pytest

from pipelines.reconciliation.reconcile_xlsx_torre_gmv import (
    main,
    reconcile,
    run,
)

REFERENCE_CSV = "docs/reconciliation/xlsx_gmv_reference_jan_maio_2026.csv"
CANDIDATE_CSV = "docs/reconciliation/torre_gmv_baseline_20260721.csv"

TOLERANCE = Decimal("0.01")

BRANDS_5 = ("apice", "barbours", "kokeshi", "lescent", "rituaria")
BRANDS_ML = ("barbours", "kokeshi", "lescent", "rituaria")
MONTHS = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05")


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow(row)


# ---------------------------------------------------------------------------
# Grao e contagem de celulas esperadas nos artefatos do Gate R1
# ---------------------------------------------------------------------------
def _read_reference_rows():
    with open(REFERENCE_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_referencia_tem_70_celulas_no_grao_esperado():
    rows = _read_reference_rows()
    assert len(rows) == 70

    by_marketplace = {}
    for row in rows:
        by_marketplace.setdefault(row["marketplace"], []).append(row)

    assert len(by_marketplace["tiktok"]) == 25
    assert len(by_marketplace["shopee"]) == 25
    assert len(by_marketplace["mercado_livre"]) == 20


def test_apice_ausente_no_mercado_livre():
    rows = _read_reference_rows()
    apice_ml = [r for r in rows if r["marketplace"] == "mercado_livre" and r["brand"] == "apice"]
    assert apice_ml == []


def test_referencia_tem_exatamente_as_70_chaves_esperadas():
    rows = _read_reference_rows()
    keys = {(r["marketplace"], r["brand"], r["month"]) for r in rows}
    assert len(keys) == 70

    expected = set()
    for brand in BRANDS_5:
        for month in MONTHS:
            expected.add(("tiktok", brand, month))
            expected.add(("shopee", brand, month))
    for brand in BRANDS_ML:
        for month in MONTHS:
            expected.add(("mercado_livre", brand, month))

    assert keys == expected
    assert ("mercado_livre", "apice", "2026-01") not in keys


def test_reconciliacao_dos_artefatos_reproduz_numeros_de_controle():
    result, errors = run(REFERENCE_CSV, CANDIDATE_CSV)
    assert errors == []
    assert result is not None
    assert result.total.cells == 70

    channel_by_name = {c.marketplace: c for c in result.channels}

    tiktok = channel_by_name["tiktok"]
    assert tiktok.gmv_reference_total == Decimal("57483610.26")
    assert abs(tiktok.abs_error_total - Decimal("3313285.94")) <= TOLERANCE

    shopee = channel_by_name["shopee"]
    assert shopee.gmv_reference_total == Decimal("19920944.56")
    assert abs(shopee.abs_error_total - Decimal("1260905.49")) <= TOLERANCE

    mercado_livre = channel_by_name["mercado_livre"]
    assert mercado_livre.gmv_reference_total == Decimal("20308625.07")
    assert abs(mercado_livre.abs_error_total - Decimal("2314546.48")) <= TOLERANCE

    assert result.total.gmv_reference_total == Decimal("97713179.89")
    assert abs(result.total.abs_error_total - Decimal("6888737.91")) <= TOLERANCE


# ---------------------------------------------------------------------------
# Calculo Decimal
# ---------------------------------------------------------------------------
def test_calculo_decimal_diferenca_reais_percentual_e_erro_absoluto():
    reference = {("tiktok", "barbours", "2026-01"): Decimal("100.00")}
    candidate = {("tiktok", "barbours", "2026-01"): Decimal("110.00")}

    result = reconcile(reference, candidate)
    cell = result.cells[0]

    assert cell.diff_reais == Decimal("10.00")
    assert cell.diff_pct == Decimal("10.00")
    assert cell.abs_error == Decimal("10.00")
    assert isinstance(cell.diff_reais, Decimal)


def test_diferencas_positivas_e_negativas_nao_se_anulam_no_erro_absoluto():
    reference = {
        ("tiktok", "a", "2026-01"): Decimal("100.00"),
        ("tiktok", "b", "2026-01"): Decimal("100.00"),
    }
    candidate = {
        ("tiktok", "a", "2026-01"): Decimal("110.00"),  # +10
        ("tiktok", "b", "2026-01"): Decimal("90.00"),   # -10
    }

    result = reconcile(reference, candidate)
    channel = result.channels[0]

    # soma ingenua das diferencas seria zero; erro absoluto acumulado deve ser 20
    naive_sum = sum((c.diff_reais for c in result.cells), Decimal("0"))
    assert naive_sum == Decimal("0")
    assert channel.abs_error_total == Decimal("20.00")


# ---------------------------------------------------------------------------
# Contrato invalido: duplicidade, celula ausente, celula extra, nulo/invalido
# ---------------------------------------------------------------------------
def test_duplicidade_no_grao_e_rejeitada(tmp_path):
    ref = tmp_path / "ref.csv"
    cand = tmp_path / "cand.csv"
    _write_csv(
        ref,
        ("marketplace", "brand", "month", "gmv_reference"),
        [
            ("tiktok", "barbours", "2026-01", "100.00"),
            ("tiktok", "barbours", "2026-01", "999.00"),  # duplicada
        ],
    )
    _write_csv(
        cand,
        ("marketplace", "brand", "month", "gmv_actual"),
        [("tiktok", "barbours", "2026-01", "105.00")],
    )

    result, errors = run(str(ref), str(cand))
    assert result is None
    assert any("duplicada" in e for e in errors)


def test_celula_ausente_no_candidato_e_rejeitada(tmp_path):
    ref = tmp_path / "ref.csv"
    cand = tmp_path / "cand.csv"
    _write_csv(
        ref,
        ("marketplace", "brand", "month", "gmv_reference"),
        [
            ("tiktok", "barbours", "2026-01", "100.00"),
            ("tiktok", "kokeshi", "2026-01", "200.00"),
        ],
    )
    _write_csv(
        cand,
        ("marketplace", "brand", "month", "gmv_actual"),
        [("tiktok", "barbours", "2026-01", "105.00")],  # kokeshi ausente
    )

    result, errors = run(str(ref), str(cand))
    assert result is None
    assert any("celula ausente" in e for e in errors)


def test_celula_extra_no_candidato_e_rejeitada(tmp_path):
    ref = tmp_path / "ref.csv"
    cand = tmp_path / "cand.csv"
    _write_csv(
        ref,
        ("marketplace", "brand", "month", "gmv_reference"),
        [("tiktok", "barbours", "2026-01", "100.00")],
    )
    _write_csv(
        cand,
        ("marketplace", "brand", "month", "gmv_actual"),
        [
            ("tiktok", "barbours", "2026-01", "105.00"),
            ("tiktok", "kokeshi", "2026-01", "999.00"),  # extra
        ],
    )

    result, errors = run(str(ref), str(cand))
    assert result is None
    assert any("celula extra" in e for e in errors)


def test_valor_nulo_e_rejeitado(tmp_path):
    ref = tmp_path / "ref.csv"
    cand = tmp_path / "cand.csv"
    _write_csv(
        ref,
        ("marketplace", "brand", "month", "gmv_reference"),
        [("tiktok", "barbours", "2026-01", "")],  # nulo
    )
    _write_csv(
        cand,
        ("marketplace", "brand", "month", "gmv_actual"),
        [("tiktok", "barbours", "2026-01", "105.00")],
    )

    result, errors = run(str(ref), str(cand))
    assert result is None
    assert any("nulo/vazio" in e for e in errors)


def test_valor_invalido_e_rejeitado(tmp_path):
    ref = tmp_path / "ref.csv"
    cand = tmp_path / "cand.csv"
    _write_csv(
        ref,
        ("marketplace", "brand", "month", "gmv_reference"),
        [("tiktok", "barbours", "2026-01", "N/A")],  # invalido
    )
    _write_csv(
        cand,
        ("marketplace", "brand", "month", "gmv_actual"),
        [("tiktok", "barbours", "2026-01", "105.00")],
    )

    result, errors = run(str(ref), str(cand))
    assert result is None
    assert any("invalido" in e for e in errors)


@pytest.mark.parametrize("valor_nao_finito", ["NaN", "Infinity", "-Infinity"])
def test_valor_nao_finito_e_rejeitado(tmp_path, valor_nao_finito):
    ref = tmp_path / "ref.csv"
    cand = tmp_path / "cand.csv"
    _write_csv(
        ref,
        ("marketplace", "brand", "month", "gmv_reference"),
        [("tiktok", "barbours", "2026-01", valor_nao_finito)],
    )
    _write_csv(
        cand,
        ("marketplace", "brand", "month", "gmv_actual"),
        [("tiktok", "barbours", "2026-01", "105.00")],
    )

    result, errors = run(str(ref), str(cand))
    assert result is None
    assert any("nao-finito" in e for e in errors)


def test_gmv_negativo_e_rejeitado(tmp_path):
    ref = tmp_path / "ref.csv"
    cand = tmp_path / "cand.csv"
    _write_csv(
        ref,
        ("marketplace", "brand", "month", "gmv_reference"),
        [("tiktok", "barbours", "2026-01", "-100.00")],
    )
    _write_csv(
        cand,
        ("marketplace", "brand", "month", "gmv_actual"),
        [("tiktok", "barbours", "2026-01", "105.00")],
    )

    result, errors = run(str(ref), str(cand))
    assert result is None
    assert any("negativo" in e for e in errors)


def test_arquivo_inexistente_vira_erro_de_contrato_sem_traceback(tmp_path):
    ref = tmp_path / "nao_existe.csv"
    result, errors = run(str(ref), CANDIDATE_CSV)
    assert result is None
    assert any("falha ao ler CSV" in e for e in errors)


def test_arquivo_ilegivel_vira_erro_de_contrato_sem_traceback(tmp_path):
    ref = tmp_path / "binario_invalido.csv"
    ref.write_bytes(b"\xff\xfe\x00\xff\xff\xfe")  # bytes invalidos em UTF-8
    result, errors = run(str(ref), CANDIDATE_CSV)
    assert result is None
    assert any("falha ao ler CSV" in e for e in errors)


def test_cabecalho_invalido_e_rejeitado(tmp_path):
    ref = tmp_path / "ref.csv"
    cand = tmp_path / "cand.csv"
    _write_csv(
        ref,
        ("marketplace", "brand", "mes", "valor"),  # cabecalho errado
        [("tiktok", "barbours", "2026-01", "100.00")],
    )
    _write_csv(
        cand,
        ("marketplace", "brand", "month", "gmv_actual"),
        [("tiktok", "barbours", "2026-01", "105.00")],
    )

    result, errors = run(str(ref), str(cand))
    assert result is None
    assert any("cabecalho invalido" in e for e in errors)


# ---------------------------------------------------------------------------
# CLI: exit code e saida JSON valida
# ---------------------------------------------------------------------------
def test_cli_exit_code_zero_para_contrato_valido(capsys):
    code = main(["--reference", REFERENCE_CSV, "--candidate", CANDIDATE_CSV, "--json"])
    assert code == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert len(payload["cells"]) == 70
    assert payload["total"]["cells"] == 70


def test_cli_exit_code_nao_zero_para_contrato_invalido(tmp_path, capsys):
    ref = tmp_path / "ref.csv"
    _write_csv(
        ref,
        ("marketplace", "brand", "month", "gmv_reference"),
        [("tiktok", "barbours", "2026-01", "100.00")],
    )
    code = main(["--reference", str(ref), "--candidate", CANDIDATE_CSV])
    assert code != 0
