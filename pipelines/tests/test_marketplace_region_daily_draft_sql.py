"""
Validacao ESTATICA (texto/regex, sem conexao de banco) do draft de DDL da
Gold regional (db/sql/gold/marketplace_region_daily_draft.sql).

Objetivo: pegar regressao estrutural no draft (coluna renomeada/removida por
engano, marcador de seguranca "DRAFT" apagado, statement destrutivo
introduzido por acidente) sem executar nada contra um banco real — este
arquivo nunca deve importar sqlalchemy/psycopg2 nem abrir conexao.
"""
from __future__ import annotations

import re
from pathlib import Path

SQL_PATH = Path(__file__).resolve().parents[2] / "db" / "sql" / "gold" / "marketplace_region_daily_draft.sql"


def _read_sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def test_draft_sql_file_existe_e_nao_esta_vazio():
    assert SQL_PATH.exists(), f"Arquivo nao encontrado: {SQL_PATH}"
    content = _read_sql()
    assert len(content.strip()) > 0


def test_draft_sql_tem_marcador_explicito_de_draft_nao_aplicado():
    content = _read_sql()
    assert "DRAFT" in content
    assert "nao aplicado" in content.lower() or "não aplicado" in content.lower()


def test_draft_sql_nao_contem_statement_destrutivo_fora_de_comentario():
    """Nenhuma linha de codigo real (nao-comentario) pode conter DROP,
    TRUNCATE, DELETE ou ALTER — este arquivo e um draft de CREATE TABLE
    apenas. Comentarios (--) podem mencionar essas palavras em prosa."""
    forbidden = re.compile(r"\b(DROP|TRUNCATE|DELETE|ALTER)\b", re.IGNORECASE)
    for line_no, line in enumerate(_read_sql().splitlines(), start=1):
        code_part = line.split("--", 1)[0]
        match = forbidden.search(code_part)
        assert not match, f"Statement destrutivo suspeito na linha {line_no}: {line!r}"


def test_draft_sql_cria_apenas_a_tabela_esperada():
    content = _read_sql()
    create_table_stmts = re.findall(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([\w.]+)", content, re.IGNORECASE)
    assert create_table_stmts == ["gold.marketplace_region_daily"]


def test_draft_sql_tem_as_colunas_de_grao_e_chaves():
    content = _read_sql()
    for col in ("date", "marketplace_id", "loja_id", "uf"):
        assert re.search(rf"^\s*{col}\s+\S", content, re.MULTILINE), f"Coluna de grao ausente: {col}"


def test_draft_sql_tem_numerador_e_denominador_de_cobertura_nao_percentual_pronto():
    """Regressao critica: a versao anterior armazenava so
    shipping_cost_coverage_pct pronto (media incorreta ao agregar). O draft
    atual precisa ter os 4 campos de numerador/denominador como COLUNAS —
    o nome do percentual pode continuar aparecendo em comentarios/docstrings,
    mas nao pode voltar a ser declarado como coluna armazenada."""
    content = _read_sql()
    for col in (
        "uf_known_orders", "uf_eligible_orders",
        "shipping_cost_covered_orders", "shipping_cost_eligible_orders",
    ):
        assert re.search(rf"^\s*{col}\s+INT\b", content, re.MULTILINE | re.IGNORECASE), (
            f"Coluna de cobertura esperada ausente ou com tipo inesperado: {col}"
        )

    # shipping_cost_coverage_pct/uf_fill_pct nao podem estar declarados como
    # COLUNA (ex: "shipping_cost_coverage_pct  NUMERIC(5,2)," dentro do
    # CREATE TABLE) — so podem aparecer em comentarios explicando que sao
    # derivados na API.
    column_decl = re.compile(
        r"^\s*(shipping_cost_coverage_pct|uf_fill_pct)\s+NUMERIC", re.MULTILINE | re.IGNORECASE
    )
    assert not column_decl.search(content), (
        "shipping_cost_coverage_pct/uf_fill_pct nao devem ser colunas armazenadas — "
        "sao derivados na API a partir dos numeradores/denominadores."
    )


def test_draft_sql_check_constraint_uf_inclui_27_siglas_e_bucket_xx():
    content = _read_sql()
    match = re.search(r"chk_region_uf_valida.*?CHECK\s*\(uf\s+IN\s*\((.*?)\)\)", content, re.DOTALL)
    assert match, "CHECK constraint chk_region_uf_valida nao encontrado"
    ufs = re.findall(r"'([A-Z]{2})'", match.group(1))
    assert "XX" in ufs, "Bucket 'XX' (nao identificada) ausente"
    real_ufs = [u for u in ufs if u != "XX"]
    assert len(set(real_ufs)) == 27, f"Esperado 27 siglas de UF (fora XX), encontrado {len(set(real_ufs))}: {sorted(set(real_ufs))}"


def test_draft_sql_tem_check_de_gmv_e_custos_nao_negativos_excluindo_nan():
    content = _read_sql()
    assert "chk_region_gmv_non_negative" in content
    assert "chk_region_shipping_non_negative" in content
    # 'NaN'::numeric >= 0 e TRUE em Postgres — o CHECK precisa excluir NaN
    # explicitamente, nao confiar so em >= 0 (ver
    # feedback_postgres_nan_check_gap na memoria do projeto).
    assert "<> 'NaN'" in content


def test_draft_sql_tem_unique_constraint_de_idempotencia_no_grao_completo():
    content = _read_sql()
    match = re.search(r"CONSTRAINT\s+uq_region_daily\s+UNIQUE\s*\(([^)]+)\)", content, re.IGNORECASE)
    assert match, "UNIQUE constraint de idempotencia (uq_region_daily) nao encontrado"
    cols = {c.strip() for c in match.group(1).split(",")}
    assert cols == {"date", "marketplace_id", "loja_id", "uf"}


def test_draft_sql_documenta_decisao_de_fonte_raw_para_ml():
    """Regressao: a decisao de usar raw.ml_shipments/raw.ml_shipment_costs
    (nao silver.stg_ml_*) no transform precisa continuar descoberta a
    partir do proprio arquivo SQL, nao so na documentacao separada."""
    content = _read_sql().lower()
    assert "raw.ml_shipments" in content
    assert "raw.ml_shipment_costs" in content


def test_draft_sql_documenta_coverage_warning_e_coverage_level_como_derivados():
    """Regressao: coverage_warning/coverage_level tem que estar mencionados
    nos comentarios como conceito derivado na API — e o que evita alguem
    adicionar essas colunas na tabela por engano (ver teste de ausencia de
    coluna percentual armazenada, mesmo raciocinio)."""
    content = _read_sql().lower()
    assert "coverage_warning" in content
    assert "coverage_level" in content
    # Nao podem ser colunas armazenadas -- mesma checagem de regressao do
    # teste de shipping_cost_coverage_pct/uf_fill_pct acima, mas para os
    # dois campos novos desta rodada.
    column_decl = re.compile(r"^\s*(coverage_warning|coverage_level)\s+\S", re.MULTILINE | re.IGNORECASE)
    assert not column_decl.search(_read_sql()), (
        "coverage_warning/coverage_level nao devem ser colunas armazenadas — "
        "sao derivados na API a partir dos numeradores/denominadores."
    )


def test_draft_sql_parenteses_balanceados():
    """Sanidade minima de sintaxe sem depender de um parser SQL completo."""
    content = _read_sql()
    # Ignora parenteses dentro de comentarios de linha para nao dar falso
    # positivo com prosa que mencione parenteses.
    code_only = "\n".join(line.split("--", 1)[0] for line in content.splitlines())
    assert code_only.count("(") == code_only.count(")")
