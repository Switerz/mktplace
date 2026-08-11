"""
Gate SD1 — aliases dos dois rótulos que a Shopee traduziu no export de Ads
de 10/08/2026, nas 5 marcas:

    'Add to Cart'       -> 'Adicionar ao carrinho'
    'Add to Cart Rate'  -> 'Taxa de adição ao carrinho'

Prova agregada do gate (comparando CADA marca com o seu PRÓPRIO template
histórico): nenhuma outra mudança de coluna, ordem, tipo, formato numérico,
moeda, data ou grão. 'Segmentação de Público' NÃO mudou — sempre existiu só
no template de uma marca e continua exatamente assim.

Os nomes em inglês são preservados: arquivos antigos continuam lidos igual.
Alias estreito e explícito — sem fuzzy matching, sem normalização.
"""
from __future__ import annotations

import pytest

from pipelines.staging.shopee import build_sql, mapping

EN_ADD = "Add to Cart"
EN_RATE = "Add to Cart Rate"
PT_ADD = "Adicionar ao carrinho"
PT_RATE = "Taxa de adição ao carrinho"
SEGMENTACAO = "Segmentação de Público"


def _col(nome: str) -> mapping.StagingColumn:
    for c in mapping.ADS.columns:
        if c.column == nome:
            return c
    raise AssertionError(f"coluna {nome!r} não existe no contrato de ads")


# ---------------------------------------------------------------------------
# 1-3. Ambos os nomes são aceitos e alimentam o MESMO campo canônico
# ---------------------------------------------------------------------------
def test_add_to_cart_aceita_ingles_e_portugues():
    assert _col("add_to_cart").source_keys == (EN_ADD, PT_ADD)


def test_add_to_cart_rate_aceita_ingles_e_portugues():
    assert _col("add_to_cart_rate_pct").source_keys == (EN_RATE, PT_RATE)


def test_nome_historico_em_ingles_vem_primeiro_na_precedencia():
    # COALESCE resolve na ordem: o layout histórico continua tendo prioridade.
    assert _col("add_to_cart").source_keys[0] == EN_ADD
    assert _col("add_to_cart_rate_pct").source_keys[0] == EN_RATE


@pytest.mark.parametrize("coluna", ["add_to_cart", "add_to_cart_rate_pct"])
def test_um_unico_campo_canonico_para_os_dois_idiomas(coluna):
    expr = build_sql.column_expression(_col(coluna))
    assert expr.startswith("COALESCE(")
    # os dois nomes aparecem na MESMA expressão, resolvendo para uma só coluna
    for chave in _col(coluna).source_keys:
        assert f"'{chave}'" in expr


def test_sql_gerado_usa_coalesce_com_os_dois_nomes():
    sql = build_sql.render_transform_file()
    assert f"->> '{EN_ADD}'" in sql
    assert f"->> '{PT_ADD}'" in sql
    assert f"->> '{EN_RATE}'" in sql
    assert f"->> '{PT_RATE}'" in sql
    assert "AS add_to_cart," in sql
    assert "AS add_to_cart_rate_pct," in sql


# ---------------------------------------------------------------------------
# 4. 'Segmentação de Público': nullable, sem valor fabricado, ausência aceita
# ---------------------------------------------------------------------------
def test_segmentacao_de_publico_continua_mapeada_e_nullable():
    col = _col("audience_segmentation")
    assert col.source_keys == (SEGMENTACAO,)
    assert col.nullable is True


def test_segmentacao_de_publico_nao_e_obrigatoria_nem_fabricada():
    """Ausência é legítima: 4 das 5 marcas nunca tiveram essa coluna, e o
    contrato não exige nem inventa valor — a coluna simplesmente fica NULL."""
    col = _col("audience_segmentation")
    assert col.non_negative is False
    assert col.rule == "text_null_placeholder"
    expr = build_sql.column_expression(col)
    # nenhum DEFAULT/placeholder fabricado na expressão
    assert "COALESCE(" not in expr


def test_segmentacao_de_publico_segue_no_allowlist_de_chaves():
    # continua conhecida pelo contrato: não é drift quando o arquivo a traz
    assert SEGMENTACAO in mapping.covered_keys(mapping.ADS)


# ---------------------------------------------------------------------------
# 5. Alias parcial ou coluna desconhecida continua bloqueando
# ---------------------------------------------------------------------------
def test_chave_desconhecida_nao_esta_coberta_e_dispara_drift():
    cobertas = mapping.covered_keys(mapping.ADS)
    assert "Adicionar ao Carrinho (v2)" not in cobertas
    assert "Add to Cart Rate (novo)" not in cobertas
    assert "Qualquer Coluna Nova" not in cobertas


def test_allowlist_de_drift_no_sql_cobre_exatamente_os_quatro_nomes():
    sql = build_sql.render_transform_file()
    trecho = sql.split("WHERE f.source_type = 'ads'")[1]
    for chave in (EN_ADD, PT_ADD, EN_RATE, PT_RATE, SEGMENTACAO):
        assert f"'{chave}'" in trecho, chave


def test_nao_ha_normalizacao_fuzzy_no_contrato():
    """As chaves são literais exatas: nenhuma regra de lower/replace/ilike
    que pudesse casar um rótulo futuro por acidente e esconder drift."""
    for coluna in ("add_to_cart", "add_to_cart_rate_pct"):
        expr = build_sql.column_expression(_col(coluna))
        assert "lower(" not in expr.lower()
        assert "ilike" not in expr.lower()
        assert "similar to" not in expr.lower()


# ---------------------------------------------------------------------------
# 6. Parsing numérico permanece idêntico
# ---------------------------------------------------------------------------
def test_regra_numerica_preservada_apenas_prefixada_por_coalesce():
    assert _col("add_to_cart").rule == "coalesce:int_null_placeholder"
    assert _col("add_to_cart_rate_pct").rule == "coalesce:pct_flexible"


def test_tipo_e_nao_negatividade_inalterados():
    add = _col("add_to_cart")
    rate = _col("add_to_cart_rate_pct")
    assert add.sql_type == "integer"
    assert add.non_negative is True
    assert rate.sql_type == "numeric(8,2)"
    assert rate.non_negative is True


def test_ddl_de_ads_nao_mudou_de_forma():
    """O alias é só de leitura do JSONB: nenhuma coluna, tipo ou constraint
    nova na tabela — por isso o DDL gerado não muda."""
    ddl = build_sql.render_ddl_file()
    assert "add_to_cart          integer" in ddl or "add_to_cart " in ddl
    assert PT_ADD not in ddl
    assert PT_RATE not in ddl
