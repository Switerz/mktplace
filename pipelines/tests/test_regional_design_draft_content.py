"""
Validacao ESTATICA (texto, sem banco) do design doc da Gold regional
(docs/regional_design_draft.md) — garante que decisoes/regras criticas nao
desaparecam silenciosamente numa edicao futura do documento. Nao substitui
revisao humana de prosa, so pega regressao grosseira (secao removida,
palavra-chave apagada).
"""
from __future__ import annotations

from pathlib import Path

DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "regional_design_draft.md"


def _read_doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def test_doc_existe():
    assert DOC_PATH.exists(), f"Documento nao encontrado: {DOC_PATH}"


def test_doc_marca_status_como_draft_nao_aplicado():
    content = _read_doc().lower()
    assert "draft" in content
    assert "não aplicado" in content or "nao aplicado" in content


def test_doc_documenta_tiktok_como_sem_cobertura_regional():
    content = _read_doc().lower()
    assert "sem_cobertura" in content
    assert "tiktok" in content


def test_doc_documenta_decisao_barbours_opcao_a_tomada():
    content = _read_doc()
    assert "Opção A" in content or "Opcao A" in content
    assert "barbours" in content.lower()
    # A decisao precisa estar marcada como tomada, nao so como recomendacao
    # em aberto -- regressao especifica desta rodada (Sessao 5).
    assert "DECIDIDA" in content.upper()


def test_doc_documenta_coverage_warning_e_coverage_level():
    content = _read_doc().lower()
    assert "coverage_warning" in content
    assert "coverage_level" in content


def test_doc_documenta_regra_de_numerador_denominador_nao_percentual_pronto():
    content = _read_doc().lower()
    assert "uf_known_orders" in content
    assert "uf_eligible_orders" in content
    assert "shipping_cost_covered_orders" in content
    assert "shipping_cost_eligible_orders" in content


def test_doc_documenta_regra_de_ranking_com_aviso_de_cobertura_baixa():
    content = _read_doc().lower()
    assert "ranking" in content
    assert "coverage_warning" in content


def test_doc_documenta_fonte_ml_raw_nao_silver():
    content = _read_doc().lower()
    assert "raw.ml_shipments" in content
    assert "silver.stg_ml_shipments" in content


def test_doc_documenta_timezone_brt_confirmado():
    content = _read_doc().lower()
    assert "america/sao_paulo" in content or "brt" in content


def test_doc_nunca_afirma_gold_aplicada():
    """Guarda-corpo simples: o documento nao pode conter uma frase que
    afirme a Gold como aplicada -- ela deve permanecer draft ate o Gate 6."""
    content = _read_doc().lower()
    assert "gold aplicada com sucesso" not in content
    assert "ddl executado no data mart" not in content
