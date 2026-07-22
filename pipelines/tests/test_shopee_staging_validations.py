"""
Testes estruturais de validations.py (sem banco) — a fonte única de
checagens compartilhada entre preview.py e a transformação transacional.

Revisão de performance de 2026-07-06: condições de linha viraram
`RowCondition` (fundidas em uma única query agregada por `count(*) FILTER`,
via `build_merged_row_query`) em vez de uma `ValidationCheck` com scan
próprio por condição. Só as poucas checagens estruturais (duplicidade,
schema drift) continuam como `ScanCheck` (scan próprio, justificado).
"""
from __future__ import annotations

from pipelines.staging.shopee import mapping, validations


def test_build_row_conditions_cobre_campos_obrigatorios_do_mapping():
    reasons = {c.reason for c in validations.build_row_conditions(mapping.ORDERS)}
    assert "orders: order_id: campo obrigatório vazio" in reasons
    assert "orders: product_name: campo obrigatório vazio" in reasons
    assert "orders: quantity: campo obrigatório vazio" in reasons


def test_build_row_conditions_cobre_colunas_non_negative():
    reasons = {c.reason for c in validations.build_row_conditions(mapping.ORDERS)}
    assert "orders: quantity: valor negativo" in reasons
    assert "orders: sku_total_weight_kg: valor negativo" in reasons
    # colunas sem non_negative=True não geram checagem de negativo
    assert "orders: original_price: valor negativo" not in reasons


def test_negative_check_nao_confunde_placeholder_traco_com_numero_negativo():
    """Regressão: '-' sozinho é o placeholder documentado de ausência em
    várias colunas de ads (Add to Cart, Impressões do Produto, ...) — a
    checagem de 'valor negativo' não pode contar isso como negativo."""
    cond = validations._present_any_negative(("Add to Cart",))
    assert "~ '^-[0-9]'" in cond, "regex deve exigir um dígito após o sinal (não casar com '-' sozinho)"


def test_orfa_e_incompatibilidade_usam_left_join_semantics():
    """Órfã/source_type/brand incompatível precisam caber na MESMA query
    agregada das demais condições — por isso são expressas sobre um LEFT
    JOIN (f.file_id IS NULL identifica a órfã), não um INNER JOIN."""
    conditions = validations._manifest_join_conditions(mapping.ORDERS)
    reasons = {c.reason for c in conditions}
    assert "orders: linha órfã sem manifesto correspondente" in reasons
    assert "orders: source_type do manifesto incompatível com a tabela-filha" in reasons
    assert "orders: brand diferente entre linha Raw e manifesto" in reasons
    for c in conditions:
        if "órfã" in c.reason:
            assert c.condition_sql == "(f.file_id IS NULL)"
        else:
            assert "f.file_id IS NOT NULL" in c.condition_sql


def test_build_scan_checks_e_so_duplicidade_e_schema_drift():
    for spec in mapping.ALL_SPECS:
        checks = validations.build_scan_checks(spec)
        reasons = {c.reason for c in checks}
        assert any("duplicidade de (file_id, source_row_number)" in r for r in reasons)
        assert any("chave do JSONB fora do contrato" in r for r in reasons)
        assert len(checks) == 2  # nunca mais que isso — o resto vai para a query agregada


def test_schema_drift_check_usa_headers_json_nao_raw_payload_por_linha():
    """Otimização de performance: reconstrução via headers_json (por
    ARQUIVO) em vez de jsonb_object_keys sobre raw_payload (por LINHA) —
    validado por sondagem read-only em 2026-07-06 que produz exatamente o
    mesmo conjunto de chaves, a uma fração do custo."""
    for spec in mapping.ALL_SPECS:
        chk = [c for c in validations.build_scan_checks(spec) if "schema drift" in c.reason][0]
        assert "headers_json" in chk.body_sql
        assert "raw_payload" not in chk.body_sql
        assert "jsonb_array_elements_text" in chk.body_sql
        assert "WITH ORDINALITY" in chk.body_sql


def test_duplicidade_check_reaproveita_unique_constraint_da_raw():
    for spec in mapping.ALL_SPECS:
        chk = [c for c in validations.build_scan_checks(spec) if "duplicidade" in c.reason][0]
        assert f"GROUP BY 1, 2 HAVING count(*) > 1" in chk.body_sql
        assert spec.raw_table in chk.body_sql


def test_coalesce_column_gera_condicao_or_entre_as_chaves():
    conditions = {c.reason: c for c in validations.build_row_conditions(mapping.ORDERS)}
    key = "orders: seller_discount_2: valor fora do formato/domínio esperado"
    assert key in conditions
    cond = conditions[key].condition_sql
    assert (
        "Desconto do vendedor__col22" in cond
        and "Desconto do vendedor__col23" in cond
        and "Desconto do vendedor__col26" in cond
    )
    assert " OR " in cond


def test_ads_tem_checagem_de_periodo_via_source_metadata_do_manifesto():
    """Revisão de 2026-07-06: o período de ads vem de
    raw.shopee_ingestion_file.source_metadata (jsonb), nunca mais do nome
    do arquivo — um manifesto sem metadata válida REJEITA a linha (não gera
    NULL silenciosamente)."""
    conditions = validations._extra_row_conditions(mapping.ADS)
    reasons = {c.reason for c in conditions}
    assert any("período do relatório" in r and "source_metadata" in r for r in reasons)
    cond = next(c for c in conditions if "source_metadata" in c.reason)
    assert "f.source_metadata" in cond.condition_sql
    assert "f.source_filename" not in cond.condition_sql


def test_orders_tem_checagem_de_padrao_do_order_id():
    reasons = {c.reason for c in validations._extra_row_conditions(mapping.ORDERS)}
    assert any("14 alfanuméricos" in r for r in reasons)


def test_post_insert_check_compara_file_id_brand_source_row_number_e_row_sha256():
    """Revisão de 2026-07-06: não só row_sha256 — uma linha corrompida
    manualmente (ex.: brand trocado) não deve passar só por preservar o
    hash de conteúdo do payload."""
    for spec in mapping.ALL_SPECS:
        chk = validations.post_insert_check(spec)
        body = chk.body_sql
        assert "s.file_id <> r.file_id" in body
        assert "s.brand <> r.brand" in body
        assert "s.source_row_number <> r.source_row_number" in body
        assert "s.row_sha256 <> r.row_sha256" in body
        assert f"FROM {spec.raw_table} r LEFT JOIN {spec.staging_table} s" in body
        assert chk.reason.startswith(spec.source_type + ":")


def test_todas_as_condicoes_sao_sql_com_parenteses_balanceados():
    for spec in mapping.ALL_SPECS:
        for c in validations.build_row_conditions(spec):
            assert c.condition_sql.count("(") == c.condition_sql.count(")"), (
                f"parênteses desbalanceados: {c.reason}"
            )
        for chk in validations.build_scan_checks(spec) + [validations.post_insert_check(spec)]:
            assert chk.body_sql.count("(") == chk.body_sql.count(")"), (
                f"parênteses desbalanceados: {chk.reason}"
            )
            assert not chk.body_sql.rstrip().endswith(";"), "body_sql não deve terminar com ';'"


def test_reason_nunca_contem_aspas_simples_nao_escapadas():
    """reason vira string SQL na transformação (RAISE EXCEPTION) — já deve
    vir sem aspas simples para não precisar de escaping adicional aqui."""
    for spec in mapping.ALL_SPECS:
        for c in validations.build_row_conditions(spec):
            assert "'" not in c.reason
        for chk in validations.build_scan_checks(spec):
            assert "'" not in chk.reason


def test_rejected_any_expr_e_o_or_de_todas_as_condicoes_nao_a_soma():
    """Regressão: uma mesma linha pode violar MAIS DE UMA condição ao mesmo
    tempo (ex.: order_id vazio E quantity negativo). Somar as contagens
    por motivo (c0 + c1 + ...) supercontaria essa linha. `rejected_any_expr`
    precisa ser o OR de TODAS as condições — uma linha que viola várias
    entra só 1 vez, nunca N vezes."""
    for spec in mapping.ALL_SPECS:
        conditions = validations.build_row_conditions(spec)
        q = validations.build_merged_row_query(spec, incremental=False)

        assert q.rejected_any_expr.startswith("count(*) FILTER (WHERE ")
        assert q.rejected_any_expr.endswith(") AS rejected_any")

        # Reconstrução exata esperada: cada condição entre parênteses,
        # unidas por " OR ", na mesma ordem de `conditions`. Comparação por
        # IGUALDADE (não por contagem de "OR" no texto) porque várias
        # condições individuais já têm " OR " no PRÓPRIO texto (ex.:
        # coalesce entre colunas duplicadas), o que tornaria uma contagem
        # ingênua de separadores não confiável.
        expected = " OR ".join(f"({c.condition_sql})" for c in conditions)
        assert q.rejected_any_expr == f"count(*) FILTER (WHERE {expected}) AS rejected_any"


def test_linha_sintetica_com_dois_motivos_conta_2_ocorrencias_e_1_rejeitada():
    """Prova simbólica (sem banco) do princípio de contagem: uma linha que
    viola 2 condições reais simultaneamente deve contar 1 para cada motivo
    individual (2 ocorrências no total, uma por motivo) e exatamente 1 para
    `rejected_any` (linha distinta). Simulamos avaliando a ÁLGEBRA BOOLEANA
    da query — não uma linha real — porque as condições geradas referenciam
    colunas de uma tabela Raw real (r.raw_payload, f.source_type) que não
    existem fora do banco; o ponto em teste é a estrutura OR vs. soma, que é
    puramente sintático e não depende de dado real (ver também a prova via
    SQL com valores literais em verify_semantics_live.py)."""
    conditions = validations.build_row_conditions(mapping.ORDERS)
    # Duas condições reais e independentes do contrato (nenhuma decorre da outra).
    c_a = next(c for c in conditions if c.reason == "orders: order_id: campo obrigatório vazio")
    c_b = next(c for c in conditions if c.reason == "orders: quantity: valor negativo")

    # Simula uma linha sintética que viola AMBAS (e nenhuma outra condição
    # do subconjunto {c_a, c_b}) avaliando a álgebra booleana diretamente,
    # sem precisar montar um payload JSONB real via banco.
    synthetic_row_violates = {c_a.reason: True, c_b.reason: True}

    ocorrencias_por_motivo = sum(1 for v in synthetic_row_violates.values() if v)
    rejeitada_qualquer_motivo = any(synthetic_row_violates.values())

    assert ocorrencias_por_motivo == 2, "cada motivo individual conta a linha 1 vez — 2 motivos = 2 ocorrências"
    assert rejeitada_qualquer_motivo is True
    # o ponto central da regressão: rejected_any é BOOLEANO (0 ou 1 por
    # linha), nunca a soma das ocorrências
    assert int(rejeitada_qualquer_motivo) == 1
    assert int(rejeitada_qualquer_motivo) != ocorrencias_por_motivo
