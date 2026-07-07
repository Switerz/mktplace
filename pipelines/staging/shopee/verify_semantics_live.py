"""
Verificação READ-ONLY, com valores SINTÉTICOS, das expressões semânticas de
`semantics.py` contra o Postgres real do Data Mart (pglast/sqlglot validam
sintaxe, mas não conseguem confirmar comportamento de calendário/NaN/
Infinity/boolean — isso só o próprio motor do Postgres decide).

Segue a mesma convenção do resto do repositório de NUNCA tocar o banco real
em `pytest` (ver runbook_shopee_raw.md — "todos com conexões falsas"): este
é um script MANUAL, como `preview.py`, não uma suíte automática. Roda
`SELECT <expressão> AS x` com literais fabricados (nunca lê nenhuma tabela,
nunca usa dado real) — seguro mesmo assim, mas mantido fora do pytest para
não exigir DATAMART_DATABASE_URL no CI.

Uso:
    uv run --no-project --with sqlalchemy --with psycopg2-binary \
        python -m pipelines.staging.shopee.verify_semantics_live
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from pipelines.staging.shopee import build_sql, mapping
from pipelines.staging.shopee import semantics as sem

REPO_ROOT = Path(__file__).resolve().parents[3]


def load_datamart_url() -> str:
    for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATAMART_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DATAMART_DATABASE_URL não encontrada no .env")


def _is_invalid_cases() -> list[tuple[str, str, bool]]:
    cases: list[tuple[str, str, bool]] = []

    for v, expect_invalid in [
        ("2026-05-31 10:00", False), ("2026-02-29 10:00", True),
        ("2024-02-29 10:00", False), ("2026-13-01 10:00", True),
        ("2026-05-31 25:00", True), ("2026-05-31 10:60", True),
        ("-", False), ("", False), ("lixo", True),
    ]:
        lit = f"'{v}'" if v else "''"
        cases.append((f"orders_ts({v!r})", sem.orders_ts_is_invalid(lit, blank_placeholder="-"), expect_invalid))

    for v, expect_invalid in [("2026-05-31", False), ("2026-02-30", True), ("", False)]:
        lit = f"'{v}'" if v else "''"
        cases.append((f"iso_date({v!r})", sem.iso_date_is_invalid(lit), expect_invalid))

    for v, expect_invalid in [
        ("31/05/2026", False), ("31/02/2026", True), ("29/02/2024", False),
        ("29/02/2026", True), ("01/13/2026", True), ("01/01/2026-31/01/2026", False),
    ]:
        cases.append((f"br_date({v!r})", sem.br_date_is_invalid(f"'{v}'"), expect_invalid))

    for v, expect_invalid in [
        ("31/05/2026 10:00:00", False), ("31/02/2026 10:00:00", True),
        ("31/05/2026 25:00:00", True), ("31/05/2026 10:00:61", True), ("Ilimitado", False),
    ]:
        cases.append((f"br_ts_seconds({v!r})",
                      sem.br_ts_seconds_is_invalid(f"'{v}'", blank_placeholder="Ilimitado"), expect_invalid))

    for v, expect_invalid in [
        ("""'{"period_start":"2026-01-01","period_end":"2026-03-31"}'::jsonb""", False),
        ("NULL::jsonb", True),
        ("'[]'::jsonb", True),
        ("""'{"period_start":"2026-01-01"}'::jsonb""", True),
        ("""'{"period_start":"2026-13-01","period_end":"2026-03-31"}'::jsonb""", True),
        ("""'{"period_start":"2026-03-31","period_end":"2026-01-01"}'::jsonb""", True),
        ("""'{"period_start":"nao-e-data","period_end":"2026-03-31"}'::jsonb""", True),
    ]:
        cases.append((f"ads_metadata_period({v})", sem.ads_metadata_period_is_invalid(v), expect_invalid))

    for v, expect_invalid in [
        ("1.234,56", False), ("1234,56", False), ("123.45", False),
        ("NaN", True), ("Infinity", True), ("1,234.56", True),
    ]:
        cases.append((f"numeric_br({v!r})", sem.numeric_br_is_invalid(f"'{v}'"), expect_invalid))

    for v, expect_invalid in [
        ("1546.30", False), ("NaN", True), ("nan", True), ("Infinity", True),
        ("-Infinity", True), ("1.234,56", True), ("1,234.56", True), ("", False),
    ]:
        lit = f"'{v}'" if v else "''"
        cases.append((f"numeric_dot({v!r})", sem.numeric_dot_is_invalid(lit), expect_invalid))

    for v, expect_invalid in [
        ("3,84%", False), ("5.34%", False), ("-", False), ("NaN%", True), ("abc", True),
    ]:
        cases.append((f"pct({v!r})", sem.pct_flexible_is_invalid(f"'{v}'"), expect_invalid))

    for v, true_l, false_l, expect_invalid in [
        ("Y", "Y", "N", False), ("N", "Y", "N", False), ("Yes", "Y", "N", True),
        ("Yes", "Yes", "No", False), ("TRUE", "TRUE", "FALSE", False),
        ("true", "TRUE", "FALSE", True),
    ]:
        cases.append((f"bool_pair({v!r},{true_l}/{false_l})",
                      sem.bool_pair_is_invalid(f"'{v}'", true_l, false_l), expect_invalid))
    return cases


def _defense_in_depth_cases() -> list[tuple[str, str]]:
    """Casos onde a contagem prévia (is_invalid) já marcaria a linha como
    inválida, mas que — sem a correção de 2026-07-06 (constant-folding e
    casts nativos permissivos) — silenciosamente PASSARIAM pela expressão
    de valor em vez de estourar. Todos devem lançar erro nativo."""
    return [
        ("orders_ts_value('2026-02-29 10:00')",
         sem.orders_ts_value("'2026-02-29 10:00'", blank_placeholder="-")),
        ("br_date_value('31/02/2026')", sem.br_date_value("'31/02/2026'")),
        ("numeric_dot_value('NaN')", sem.numeric_dot_value("'NaN'")),
        ("numeric_dot_value('Infinity')", sem.numeric_dot_value("'Infinity'")),
        ("numeric_br_value('NaN')", sem.numeric_br_value("'NaN'")),
        ("pct_flexible_value('NaN%')", sem.pct_flexible_value("'NaN%'")),
        ("bool_pair_value('Maybe','Y','N')", sem.bool_pair_value("'Maybe'", "Y", "N")),
        ("bool_pair_value('true','Y','N') -- cast nativo aceitaria 'true'",
         sem.bool_pair_value("'true'", "Y", "N")),
        ("bool_pair_value('1','Yes','No') -- cast nativo aceitaria '1'",
         sem.bool_pair_value("'1'", "Yes", "No")),
        ("int_value('+5') -- cast nativo aceita sinal de mais",
         sem.int_value("'+5'")),
    ]


def _ddl_check_cases() -> list[tuple[str, str, bool]]:
    """Confirma, contra o Postgres real, que o CHECK gerado por
    `build_sql._column_check_sql` (revisão de performance/integridade de
    2026-07-06) rejeita NaN mesmo em colunas SEM `non_negative=True` — o
    achado motivador: `numeric(p,s)` explícito JÁ rejeita ±Infinity
    nativamente (confirmado: `'Infinity'::numeric(14,2)` levanta "numeric
    field overflow"), mas aceita NaN livremente, e `'NaN'::numeric >= 0`
    avalia TRUE — um CHECK ingênuo de `col >= 0` NÃO barra NaN. Por isso
    todo `numeric(...)` ganha `col <> 'NaN'` explícito, com ou sem
    `non_negative`."""
    cases: list[tuple[str, str, bool]] = []

    def _check_expr_for(column: str, sql_type: str) -> str:
        col = next(c for c in mapping.ORDERS.columns if c.column == column)
        assert col.sql_type == sql_type
        raw = build_sql._column_check_sql(col).strip()
        assert raw.startswith("CHECK (") and raw.endswith(")")
        return raw[len("CHECK ("):-1]

    # Coluna numeric COM non_negative=True (ex.: peso — não pode ser negativo nem NaN)
    check_nn = _check_expr_for("sku_total_weight_kg", "numeric(10,3)")
    for value_literal, expect_check_passes in [
        ("1.5", True), ("0", True), ("'NaN'", False), ("-1", False),
    ]:
        expr = check_nn.replace("sku_total_weight_kg", f"({value_literal})::numeric(10,3)")
        cases.append((f"CHECK(peso, {value_literal})", expr, expect_check_passes))

    # Coluna numeric SEM non_negative (ex.: preço — pode ser qualquer valor, menos NaN)
    check_plain = _check_expr_for("original_price", "numeric(14,2)")
    for value_literal, expect_check_passes in [
        ("10.0", True), ("-5.0", True), ("'NaN'", False),
    ]:
        expr = check_plain.replace("original_price", f"({value_literal})::numeric(14,2)")
        cases.append((f"CHECK(preço sem non_negative, {value_literal})", expr, expect_check_passes))

    return cases


def _rejected_any_demo_sql() -> str:
    """Prova via SQL LITERAL (nenhuma tabela real) do princípio por trás de
    `rejected_any_expr` em validations.py: 4 linhas sintéticas —
    (viola motivo1 E motivo2), (só motivo2), (só motivo1), (nenhum) — a
    SOMA das contagens por motivo é 4, mas só 3 linhas são DISTINTAS
    rejeitadas (a 4ª linha não viola nada). Confirma que `count(*) FILTER
    (WHERE a) + count(*) FILTER (WHERE b)` supercontaria a 1ª linha, e que
    `count(*) FILTER (WHERE a OR b)` conta corretamente."""
    return (
        "WITH t(v1, v2) AS (VALUES (1, 1), (0, 1), (1, 0), (0, 0)) "
        "SELECT "
        "count(*) FILTER (WHERE v1 = 1) AS motivo1, "
        "count(*) FILTER (WHERE v2 = 1) AS motivo2, "
        "count(*) FILTER (WHERE v1 = 1 OR v2 = 1) AS rejeitadas_distintas "
        "FROM t"
    )


def main() -> int:
    engine = create_engine(load_datamart_url(), pool_pre_ping=True)
    fails = 0
    with engine.connect().execution_options(postgresql_readonly=True) as conn:
        assert conn.execute(text("SHOW transaction_read_only")).scalar() == "on"

        for label, cond_sql, expected in _is_invalid_cases():
            try:
                got = conn.execute(text(f"SELECT {cond_sql}")).scalar()
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                print(f"  [FAIL] {label}: erro inesperado ao contar -> {exc}")
                fails += 1
                continue
            ok = bool(got) == expected
            print(f"  [{'OK' if ok else 'FAIL'}] {label}: got={got} expected_invalid={expected}")
            fails += 0 if ok else 1

        print("\n-- defesa em profundidade: expressão de VALOR deve ESTOURAR para entrada inválida --")
        for label, val_sql in _defense_in_depth_cases():
            try:
                conn.execute(text(f"SELECT {val_sql}")).scalar()
                print(f"  [FAIL] {label}: não lançou erro (deveria)")
                fails += 1
            except Exception:
                conn.rollback()
                print(f"  [OK] {label}: lançou erro nativo, como esperado")

        print("\n-- CHECK de DDL gerado: deve rejeitar NaN mesmo sem non_negative --")
        for label, check_sql, expect_passes in _ddl_check_cases():
            try:
                got = conn.execute(text(f"SELECT {check_sql}")).scalar()
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                print(f"  [FAIL] {label}: erro inesperado -> {exc}")
                fails += 1
                continue
            ok = bool(got) == expect_passes
            print(f"  [{'OK' if ok else 'FAIL'}] {label}: passa_check={got} esperado={expect_passes}")
            fails += 0 if ok else 1

        print("\n-- rejected_any: OR de motivos != soma de ocorrências (linhas sintéticas literais) --")
        m1, m2, distintas = conn.execute(text(_rejected_any_demo_sql())).one()
        soma_ocorrencias = m1 + m2
        print(f"  motivo1={m1} motivo2={m2} soma_ocorrencias={soma_ocorrencias} "
              f"rejeitadas_distintas={distintas}")
        ok = (m1, m2, distintas) == (2, 2, 3) and soma_ocorrencias != distintas
        print(f"  [{'OK' if ok else 'FAIL'}] soma (4) supercontaria; OR (3) é o valor correto")
        fails += 0 if ok else 1

    total = len(_is_invalid_cases()) + len(_defense_in_depth_cases()) + len(_ddl_check_cases()) + 1
    print(f"\n{total} casos, {fails} falha(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
