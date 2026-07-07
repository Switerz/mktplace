"""
Testes do contrato da staging Shopee (Fase Staging 1 — draft, sem banco).

Cobrem: cobertura 100% das chaves reais do raw_payload (inventário read-only
de 2026-07-04), exclusão de PII do SQL gerado, unicidade/convenção dos nomes,
sincronismo dos artefatos SQL gerados, e as garantias transacionais exigidas
na revisão de 2026-07-06 (advisory lock, LOCK TABLE, validação fail-fast
antes do INSERT, ON CONFLICT, ausência de ORDER BY, CHECKs de domínio).
"""
from __future__ import annotations

import re

from pipelines.ingestion.shopee_raw import pii
from pipelines.staging.shopee import build_sql, mapping, validations

# Chaves REAIS observadas em 100% da Raw (inventário sanitizado 2026-07-04).
ORDERS_RAW_KEYS = {
    "Ajuste por pagamento via PIX",
    "Ajuste por participação em ação comercial",
    "Bairro",
    "Cancelar Motivo",
    "CEP",
    "Cidade",
    "Cidade__col58",
    "Cidade__col59",
    "Código do Cupom",
    "Coin Cashback Voucher Amount Sponsored by Seller",
    "Compensar Moedas Shopee",
    "CPF do Comprador",
    "Cupom",
    "Cupom do vendedor",
    "Data da Finalização do Cancelamento",
    "Data de criação do pedido",
    "Data prevista de envio",
    "Desconto da Leve Mais por Menos do vendedor",
    "Desconto de Frete Aproximado",
    "Desconto do vendedor",
    "Desconto do vendedor__col23",
    "Desconto do vendedor__col26",
    "Desconto Shopee da Leve Mais por Menos",
    "Domestic Delivered Date",
    "Endereço de entrega",
    "Hora completa do pedido",
    "Hora do pagamento do pedido",
    "Hot Listing",
    "ID do pedido",
    "Incentivo de cupom",
    "Incentivo Shopee para ação comercial",
    "Indicador da Leve Mais por Menos",
    "Método de envio",
    "Nº de referência do SKU principal",
    "Nome da variação",
    "Nome de usuário (comprador)",
    "Nome do destinatário",
    "Nome do Produto",
    "Nota",
    "Número de produtos pedidos",
    "Número de rastreamento",
    "Número de referência SKU",
    "Observação do comprador",
    "Opção de envio",
    "País",
    "Pedido FBS",
    "Peso total do pedido",
    "Peso total SKU",
    "Preço acordado",
    "Preço original",
    "Quantidade",
    "Returned quantity",
    "Shopee Owned",
    "Status da Devolução / Reembolso",
    "Status do pedido",
    "Subtotal do produto",
    "Taxa de comissão bruta",
    "Taxa de comissão líquida",
    "Taxa de envio pagas pelo comprador",
    "Taxa de Envio Reversa",
    "Taxa de serviço bruta",
    "Taxa de serviço líquida",
    "Taxa de transação",
    "Telefone",
    "Tempo de Envio",
    "Tipo de pedido",
    "Total descontado Cartão de Crédito",
    "Total global",
    "UF",
    "Valor estimado do frete",
    "Valor Total",
}

SHOP_STATS_RAW_KEYS = {
    "Cliques Por Produto",
    "Data",
    "# de compradores",
    "# de compradores em potencial",
    "# de compradores existentes",
    "# de novos compradores",
    "Pedidos",
    "Pedidos Cancelados",
    "Pedidos Devolvidos / Reembolsados",
    "Repetir Índice de Compras",
    "Taxa de Conversão de Pedidos",
    "Vendas (BRL)",
    "Vendas Canceladas",
    "Vendas Devolvidas / Reembolsadas",
    "Vendas por Pedido",
    "Vendas Sem os Descontos da Shopee",
    "Visitantes",
}

ADS_RAW_KEYS = {
    "#",
    "ACOS",
    "ACOS Direto",
    "Add to Cart",
    "Add to Cart Rate",
    "Cliques",
    "Cliques de Produtos",
    "Conversões",
    "Conversões Diretas",
    "Criativo",
    "CTR",
    "CTR do Produto",
    "Custo por Conversão",
    "Custo por Conversão Direta",
    "Data de Encerramento",
    "Data de Início",
    "Despesas",
    "GMV",
    "ID do produto",
    "Impressões",
    "Impressões do Produto",
    "Itens Vendidos",
    "Itens Vendidos Diretos",
    "Método de Lance",
    "Nome do Anúncio",
    "Posicionamento",
    "Receita direta",
    "ROAS",
    "ROAS Direto",
    "Segmentação de Público",
    "Status",
    "Taxa de Conversão",
    "Taxa de Conversão Direta",
    "Tipos de Anúncios",
    "Voucher Amount",
    "Vouchered Sales",
}

RAW_KEYS = {
    "orders": ORDERS_RAW_KEYS,
    "shop_stats": SHOP_STATS_RAW_KEYS,
    "ads": ADS_RAW_KEYS,
}

# Headers que NUNCA podem aparecer no SQL gerado (PII direta / quase-
# identificadores / texto livre — minimização por desenho).
#
# "CPF do Comprador" NÃO está nesta lista: é a ÚNICA exceção aprovada por
# decisão de negócio explícita (revisão de 2026-07-06) — mapeado para
# `buyer_cpf` (PII_DIRETA, ver mapping.ORDERS.comment). Todo outro header
# PII_DIRETA/quase-identificador/texto-livre continua proibido de extração
# de valor no SQL gerado.
FORBIDDEN_IN_SQL = [
    "Nome do destinatário",
    "Telefone",
    "Endereço de entrega",
    "CEP",
    "Bairro",
    "Observação do comprador",
    "Nome de usuário (comprador)",
    "'Nota'",  # com aspas para não colidir com substring de outros headers
]


def test_cobertura_completa_das_chaves_reais():
    """Toda chave real do raw_payload está mapeada OU explicitamente excluída;
    nenhuma chave inventada no contrato."""
    for spec in mapping.ALL_SPECS:
        expected = RAW_KEYS[spec.source_type]
        covered = mapping.covered_keys(spec)
        assert covered == expected, (
            f"{spec.source_type}: faltam {sorted(expected - covered)}; "
            f"sobram {sorted(covered - expected)}"
        )


def test_mapeadas_e_excluidas_sao_disjuntas():
    for spec in mapping.ALL_SPECS:
        overlap = mapping.mapped_keys(spec) & mapping.excluded_keys(spec)
        assert not overlap, f"{spec.source_type}: chaves ao mesmo tempo mapeadas e excluídas: {overlap}"


def test_nomes_de_coluna_unicos_e_snake_case():
    for spec in mapping.ALL_SPECS:
        names = [c.column for c in spec.columns]
        assert len(names) == len(set(names)), f"{spec.staging_table}: colunas duplicadas"
        for n in names:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", n), f"coluna fora do padrão snake_case: {n}"


def test_pii_direta_do_catalogo_raw_esta_excluida_exceto_cpf_aprovado():
    """Todo header classificado como PII_DIRETA no catálogo da Raw está fora
    das colunas selecionadas — EXCETO 'CPF do Comprador', mapeado para
    `buyer_cpf` por decisão de negócio explícita (revisão de 2026-07-06,
    ver mapping.ORDERS.comment). Nenhum outro PII_DIRETA pode ser
    selecionado sem uma decisão equivalente."""
    pii_headers = {h for h, rule in pii.ORDERS_PII_CATALOG.items()
                   if rule.classification == pii.PII_DIRETA}
    approved_exception = {"CPF do Comprador"}
    selected = {k for c in mapping.ORDERS.columns for k in c.source_keys}
    unexpected = (pii_headers & selected) - approved_exception
    assert not unexpected, f"headers de PII direta selecionados sem aprovação: {unexpected}"
    assert "CPF do Comprador" in selected, "buyer_cpf deveria mapear 'CPF do Comprador'"


def test_buyer_cpf_e_marcado_como_pii_direta_e_sem_indice():
    col = next(c for c in mapping.ORDERS.columns if c.column == "buyer_cpf")
    assert col.pii_class == mapping.PII_DIRETA
    assert col.rule == "text_null_blank"
    assert col.source_keys == ("CPF do Comprador",)
    assert col.non_negative is False
    # nenhuma coluna buyer_cpf em extra_ddl (sem índice dedicado)
    assert not any("buyer_cpf" in stmt for stmt in mapping.ORDERS.extra_ddl)


def test_comentario_da_tabela_orders_nao_afirma_mais_sem_pii_direta():
    """Regressão: o comentário antigo dizia 'Sem PII direta' — agora que
    buyer_cpf existe, essa afirmação seria falsa."""
    comment_lower = mapping.ORDERS.comment.lower()
    assert "sem pii direta" not in comment_lower
    assert "contem pii direta" in comment_lower or "contém pii direta" in comment_lower
    assert "buyer_cpf" in comment_lower


def test_buyer_key_foi_removida():
    """Revisão de 2026-07-06: uma coluna permanentemente NULL não entrega
    valor — buyer_key foi removida até HMAC/segredo/rotação serem aprovados."""
    names = {c.column for c in mapping.ORDERS.columns}
    assert "buyer_key" not in names


def test_orders_table_renomeada_para_deixar_grao_de_snapshot_explicito():
    """A tabela de orders NÃO é canônica (sem dedup de negócio) — o nome
    deixa isso explícito, ao contrário de silver.stg_ml_orders/tiktok_orders
    (que são deduplicadas por chave de negócio)."""
    assert mapping.ORDERS.staging_table == "silver.stg_shopee_order_item_snapshots"
    comment_lower = mapping.ORDERS.comment.lower()
    assert "nao e uma tabela canonica" in comment_lower or "não é uma tabela canônica" in mapping.ORDERS.comment.lower()
    assert "gold" in comment_lower


def test_sql_gerado_nao_referencia_pii():
    """A DDL nunca deve mencionar um header de PII (só itera colunas
    mapeadas). A transformação tem UM lugar legítimo onde o NOME de headers
    excluídos aparece: a checagem de "chave do JSONB fora do contrato"
    precisa saber quais chaves são conhecidas (mapeadas OU excluídas) para
    não as marcar como schema drift — mas isso é só o nome da chave, nunca
    o VALOR da célula. O que nunca pode aparecer é o padrão de EXTRAÇÃO de
    valor `raw_payload ->> 'Header'` para um header de PII."""
    transform = build_sql.render_transform_file()
    ddl = build_sql.render_ddl_file()
    for forbidden in FORBIDDEN_IN_SQL:
        value_access = f"raw_payload ->> {forbidden}" if forbidden.startswith("'") else f"raw_payload ->> '{forbidden}'"
        assert value_access not in transform, f"transformação EXTRAI VALOR de {forbidden!r}"
        assert value_access not in ddl, f"DDL EXTRAI VALOR de {forbidden!r}"
    assert "raw_payload ->>" not in ddl, "DDL não deve acessar raw_payload de forma alguma"


def test_cpf_do_comprador_e_a_unica_excecao_aprovada_no_sql_gerado():
    """Prova positiva da exceção deliberada: buyer_cpf EXTRAI valor de 'CPF
    do Comprador' na transformação (nunca na DDL, que só declara colunas).
    Este teste nunca lê nem imprime um valor real — só confirma a presença
    do PADRÃO de extração no texto SQL gerado a partir de dados sintéticos
    de mapping.py."""
    transform = build_sql.render_transform_file()
    ddl = build_sql.render_ddl_file()
    assert "raw_payload ->> 'CPF do Comprador'" in transform
    assert "raw_payload ->> 'CPF do Comprador'" not in ddl


def test_sem_select_estrela():
    transform = build_sql.render_transform_file()
    assert "SELECT *" not in transform
    assert "select *" not in transform.lower()


def test_insert_lista_colunas_explicitas_e_todas_tem_alias():
    for spec in mapping.ALL_SPECS:
        stmt = build_sql.build_transform(spec)
        first_col = spec.columns[0].column
        assert f"INSERT INTO {spec.staging_table} (" in stmt
        assert f"\n    {first_col}," in stmt
        for col in spec.columns:
            assert f" AS {col.column}" in stmt, f"{spec.staging_table}: falta alias {col.column}"


def test_transformacao_e_incremental_com_anti_join_e_on_conflict():
    for spec in mapping.ALL_SPECS:
        stmt = build_sql.build_transform(spec)
        assert "WHERE NOT EXISTS (" in stmt, "anti-join de idempotência ausente"
        assert f"FROM {spec.staging_table} s WHERE s.raw_id = r.id" in stmt
        assert "ON CONFLICT (raw_id) DO NOTHING" in stmt
        assert "ORDER BY" not in stmt, "ORDER BY não é necessário num INSERT idempotente por raw_id"


def test_ddl_e_transform_com_mesmas_colunas_na_mesma_ordem():
    for spec in mapping.ALL_SPECS:
        ddl = build_sql.build_ddl(spec)
        cols_ddl = re.findall(r"^    ([a-z][a-z0-9_]*)\s", ddl, flags=re.MULTILINE)
        assert cols_ddl[-1] == "staging_built_at"
        assert cols_ddl[:-1] == [c.column for c in spec.columns]
        stmt = build_sql.build_transform(spec)
        insert_list = stmt.split("INSERT INTO", 1)[1].split(")", 1)[0]
        cols_insert = re.findall(r"([a-z][a-z0-9_]*)", insert_list.split("(", 1)[1])
        assert cols_insert == [c.column for c in spec.columns]


def test_arquivos_gerados_em_sincronia_com_mapping():
    assert build_sql.DDL_PATH.exists() and build_sql.TRANSFORM_PATH.exists()
    assert build_sql.DDL_PATH.read_text(encoding="utf-8") == build_sql.render_ddl_file()
    assert build_sql.TRANSFORM_PATH.read_text(encoding="utf-8") == build_sql.render_transform_file()


def test_ddl_marca_draft_e_nao_e_executavel_por_engano():
    ddl = build_sql.render_ddl_file()
    assert "DRAFT — NÃO EXECUTADO" in ddl
    assert "DROP " not in ddl and "TRUNCATE" not in ddl and "DELETE " not in ddl


def test_ddl_tem_check_de_nao_negatividade_para_colunas_marcadas():
    """Para coluna numeric não-negativa, o CHECK vem combinado com a
    rejeição de NaN (`CHECK (col <> 'NaN' AND col >= 0)`) — não mais um
    CHECK isolado de `>= 0`."""
    for spec in mapping.ALL_SPECS:
        ddl = build_sql.build_ddl(spec)
        for col in spec.columns:
            if col.non_negative:
                assert f"{col.column} >= 0" in ddl, f"falta '{col.column} >= 0' no CHECK"


def test_ddl_tem_check_anti_nan_em_toda_coluna_numeric():
    """Achado de 2026-07-06: numeric(p,s) aceita NaN nativamente e
    'NaN' >= 0 avalia TRUE — CHECK(col >= 0) sozinho NÃO rejeita NaN. Toda
    coluna `numeric(...)` precisa do CHECK explícito `<> 'NaN'`."""
    for spec in mapping.ALL_SPECS:
        ddl = build_sql.build_ddl(spec)
        for col in spec.columns:
            if col.sql_type.startswith("numeric"):
                assert f"{col.column} <> 'NaN'" in ddl, f"falta CHECK anti-NaN em {col.column}"


def test_ddl_tem_fk_fisica_raw_id_e_file_id():
    for spec in mapping.ALL_SPECS:
        ddl = build_sql.build_ddl(spec)
        assert f"FOREIGN KEY (raw_id) REFERENCES {spec.raw_table} (id)" in ddl
        assert "FOREIGN KEY (file_id) REFERENCES raw.shopee_ingestion_file (file_id)" in ddl


def test_ddl_shop_stats_tem_check_de_ordem_de_periodo():
    ddl = build_sql.build_ddl(mapping.SHOP_STATS)
    assert "period_start <= period_end" in ddl


def test_ddl_ads_tem_checks_de_periodo_e_ended_after_started():
    ddl = build_sql.build_ddl(mapping.ADS)
    assert "report_period_start <= report_period_end" in ddl
    assert "ended_at IS NULL OR ended_at >= started_at" in ddl
    # Explicitamente SEM teto de 100% para ACOS/afins (pedido da revisão).
    assert "acos_pct <= 100" not in ddl and "acos_pct<=100" not in ddl


def test_ddl_ads_report_period_e_not_null():
    """Revisão de 2026-07-06: o período de ads vem de source_metadata do
    manifesto, sem fallback silencioso — um manifesto sem metadata válida
    REJEITA a linha na pré-validação, então a coluna nunca fica NULL na
    staging e o contrato reflete isso com NOT NULL (não mais nullable)."""
    for col_name in ("report_period_start", "report_period_end"):
        col = next(c for c in mapping.ADS.columns if c.column == col_name)
        assert col.nullable is False
    ddl = build_sql.build_ddl(mapping.ADS)
    for col_name in ("report_period_start", "report_period_end"):
        assert re.search(rf"{col_name}\s+date NOT NULL", ddl), f"{col_name} deveria ser NOT NULL no DDL gerado"


def test_transacao_tem_advisory_lock_lock_table_e_ordem_correta():
    transform = build_sql.render_transform_file()
    i_begin = transform.index("BEGIN;")
    i_lock_adv = transform.index("pg_advisory_xact_lock(")
    i_lock_table = transform.index("LOCK TABLE ")
    i_first_do = transform.index("DO $$")
    i_first_insert = transform.index("INSERT INTO")
    i_commit = transform.rindex("COMMIT;")
    # Ordem exigida: BEGIN -> advisory lock -> LOCK TABLE -> validações (DO) -> INSERT -> COMMIT
    assert i_begin < i_lock_adv < i_lock_table < i_first_do < i_first_insert < i_commit


def test_lock_table_cobre_raw_e_staging_em_uma_unica_instrucao_alfabetica():
    """Revisão de performance/integridade: as 3 tabelas staging entram no
    MESMO LOCK TABLE das 4 tabelas Raw/manifesto, numa única instrução
    (nunca instruções separadas — evita janela de lock parcial), em ordem
    alfabética fixa (evita deadlock entre execuções concorrentes)."""
    transform = build_sql.render_transform_file()
    lock_lines = [l for l in transform.splitlines() if l.startswith("LOCK TABLE")]
    assert len(lock_lines) == 1, "deve haver exatamente UMA instrução LOCK TABLE"
    lock_line = lock_lines[0]
    expected_tables = [
        "raw.shopee_ingestion_file", "raw.shopee_order_item_export",
        "raw.shopee_shop_stats_export", "raw.shopee_ads_export",
        "silver.stg_shopee_order_item_snapshots", "silver.stg_shopee_shop_stats",
        "silver.stg_shopee_ads",
    ]
    for tbl in expected_tables:
        assert tbl in lock_line, f"{tbl} ausente do LOCK TABLE"
    assert "IN SHARE MODE" in lock_line
    # ordem alfabética fixa dentro da própria instrução
    positions = [lock_line.index(tbl) for tbl in sorted(expected_tables)]
    assert positions == sorted(positions), "tabelas não estão em ordem alfabética no LOCK TABLE"


def test_existe_bloco_de_validacao_pos_insert():
    transform = build_sql.render_transform_file()
    assert "row_sha256" in transform
    assert transform.count("DO $$") >= 2, "esperado pelo menos 1 bloco pré-insert e 1 pós-insert"
    i_last_insert = transform.rindex("INSERT INTO")
    i_last_do = transform.rindex("DO $$")
    assert i_last_do > i_last_insert, "validação pós-insert deve vir depois do último INSERT"


def test_mensagens_de_excecao_nunca_referenciam_raw_payload():
    """RAISE EXCEPTION deve conter só motivo/contagem — nunca acessar
    raw_payload diretamente na string de mensagem (só nas contagens)."""
    transform = build_sql.render_transform_file()
    for line in transform.splitlines():
        if "RAISE EXCEPTION" in line:
            assert "raw_payload" not in line


def test_toda_regra_do_mapping_esta_registrada():
    for spec in mapping.ALL_SPECS:
        for col in spec.columns:
            rule = col.rule
            if rule.startswith("prov:"):
                continue
            if rule.startswith("coalesce:"):
                rule = rule.removeprefix("coalesce:")
            from pipelines.staging.shopee import rules_registry as rr
            resolved, _params = rr.resolve(rule)
            assert resolved is not None, f"regra não registrada: {col.rule} ({col.column})"


def test_grao_preservado_sem_agregacao():
    for spec in mapping.ALL_SPECS:
        stmt = build_sql.build_transform(spec)
        assert "GROUP BY" not in stmt
        assert "DISTINCT" not in stmt


def test_validations_row_conditions_e_scan_checks_nao_ficam_vazios():
    for spec in mapping.ALL_SPECS:
        conditions = validations.build_row_conditions(spec)
        assert len(conditions) > 10
        for c in conditions:
            assert c.reason.startswith(spec.source_type + ":")

        scan_checks = validations.build_scan_checks(spec)
        assert len(scan_checks) == 2  # duplicidade + schema drift
        for chk in scan_checks:
            assert chk.reason.startswith(spec.source_type + ":")
            assert "FROM" in chk.body_sql
            assert chk.justification  # toda checagem de scan próprio precisa de justificativa


def test_numero_de_scans_por_fonte_e_limitado():
    """Revisão de performance de 2026-07-06: no máximo poucos scans por
    tabela (1 query agregada de linha + checagens estruturais com scan
    próprio), nunca um scan por coluna/regra. Limite generoso de 5 para
    deixar espaço a checagens estruturais futuras sem exigir nova revisão."""
    MAX_SCANS_PER_SOURCE = 5
    for spec in mapping.ALL_SPECS:
        n_scans = 1 + len(validations.build_scan_checks(spec))  # 1 query agregada + scans próprios
        assert n_scans <= MAX_SCANS_PER_SOURCE, (
            f"{spec.source_type}: {n_scans} scans (limite {MAX_SCANS_PER_SOURCE})"
        )
        # a query agregada precisa cobrir MUITAS condições num único scan —
        # não pode ter "encolhido" de volta para poucas condições soltas
        n_conditions = len(validations.build_row_conditions(spec))
        assert n_conditions > 10, (
            f"{spec.source_type}: query agregada cobre só {n_conditions} condições — "
            "esperado >10 (pouco proveito de merge com tão poucas)"
        )


def test_query_agregada_usa_count_filter_nao_um_select_por_condicao():
    for spec in mapping.ALL_SPECS:
        q = validations.build_merged_row_query(spec, incremental=False)
        assert len(q.select_exprs) == len(q.reasons) > 10
        for expr in q.select_exprs:
            assert expr.startswith("count(*) FILTER (WHERE ")
        # UM único FROM/JOIN — nunca um FROM por condição
        assert q.from_clause.count("FROM ") == 1
        assert q.from_clause.count("JOIN ") == 1
