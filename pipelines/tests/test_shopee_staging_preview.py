"""
Testes estruturais do preview de reconciliação da staging Shopee (sem
banco). O preview é 100% somente-leitura e usa a MESMA fonte de checagens
que a transformação (validations.py) — aqui garantimos: (a) nenhum comando
de escrita no módulo, (b) checagem de sessão read-only antes de qualquer
query, (c) que o preview reaproveita validations.build_merged_row_query e
validations.build_scan_checks (não tem sua própria lista de regras), e
(d) que nenhuma condição de checagem seleciona valor de coluna com PII.
"""
from __future__ import annotations

import inspect

from pipelines.staging.shopee import mapping, preview, validations


def test_modulo_nao_contem_comandos_de_escrita():
    """LOCK TABLE fica de fora da varredura genérica: o docstring do módulo
    MENCIONA em prosa que a transformação usa LOCK TABLE (para explicar por
    que o preview não é garantia suficiente) — isso não é um comando
    executado aqui. Uma checagem dedicada abaixo confirma que preview.py
    nunca EXECUTA um LOCK TABLE de verdade."""
    src = inspect.getsource(preview)
    for token in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "TRUNCATE",
                  "CREATE TABLE", "CREATE INDEX", "ALTER TABLE", "COPY "):
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            assert token not in line, f"comando de escrita no preview: {token} em {line!r}"


def test_modulo_nunca_executa_lock_table():
    src = inspect.getsource(preview)
    for line in src.splitlines():
        if "conn.execute" in line or "text(" in line:
            assert "LOCK TABLE" not in line, f"preview.py executaria LOCK TABLE: {line!r}"


def test_run_preview_exige_sessao_readonly_antes_da_primeira_query():
    src = inspect.getsource(preview.run_preview)
    assert "postgresql_readonly=True" in src
    ro_check = src.index("transaction_read_only")
    first_query = src.index("count(*)")
    assert ro_check < first_query, "checagem read-only deve vir antes da primeira query"


def test_preview_reaproveita_validations_build_merged_row_query():
    """O preview NÃO pode ter sua própria lista de condições de rejeição —
    só executa o que validations.build_merged_row_query/build_scan_checks já
    definem (fonte única compartilhada com a transformação), sempre com
    incremental=False (quer reconciliar 100% da Raw, não só o delta)."""
    src = inspect.getsource(preview)
    assert "validations.build_merged_row_query" in src
    assert "incremental=False" in src
    assert "validations.build_scan_checks" in src
    assert "reject_conditions" not in src, "preview não deve mais ter lista própria de regras"


def test_checagens_de_cada_fonte_nao_extraem_valores_de_pii():
    """O nome de um header de PII pode aparecer numa checagem estrutural
    (ex.: a lista de chaves conhecidas do check de schema drift) — isso é
    metadado, não dado. O que nunca pode existir é o padrão de EXTRAÇÃO de
    valor `raw_payload ->> 'HeaderPII'`."""
    pii_headers = ["Nome do destinatário", "Telefone", "CPF do Comprador",
                   "Endereço de entrega", "CEP", "Bairro",
                   "Observação do comprador", "Nome de usuário (comprador)"]
    for spec in mapping.ALL_SPECS:
        checks_sql = [c.condition_sql for c in validations.build_row_conditions(spec)]
        checks_sql += [c.body_sql for c in validations.build_scan_checks(spec)]
        checks_sql.append(validations.post_insert_check(spec).body_sql)
        for sql in checks_sql:
            for h in pii_headers:
                assert f"raw_payload ->> '{h}'" not in sql, f"checagem extrai valor de header PII: {h}"


def test_colunas_de_soma_e_data_existem_no_mapping():
    for spec in mapping.ALL_SPECS:
        cols = {c.column for c in spec.columns}
        for c in preview._SUM_COLUMNS[spec.source_type]:
            assert c in cols, f"coluna de soma inexistente: {c}"
        assert preview._DATE_COLUMNS[spec.source_type] in cols


def test_todas_as_fontes_tem_bucket_de_mes_configurado():
    for spec in mapping.ALL_SPECS:
        assert spec.source_type in preview._MONTH_BUCKET_SQL
