"""Gate PMA-1A — contraprovas do DDL da camada de serving.

O DDL vive em `db/sql/marts/pma_listing_price_serving_ddl.sql` e NAO e' uma
migration Alembic: a revisao `013` esta reservada pela frente UE8 e nao esta em
`origin/main` (ver `test_pma_migration_chain.py`). Este arquivo trava o CONTEUDO
do DDL para que a conversao em migration no PMA-1B seja transcricao, nao
reinterpretacao.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DDL_PATH = REPO / "db" / "sql" / "marts" / "pma_listing_price_serving_ddl.sql"

LISTING = "marts.fact_marketplace_listing_price_daily"
REFERENCE = "marts.fact_suggested_price_reference_snapshot"


def _ddl() -> str:
    return DDL_PATH.read_text(encoding="utf-8")


def _sem_comentario() -> str:
    """DDL sem as linhas de comentario `--`: varre o que o banco executaria."""
    linhas = []
    for ln in _ddl().splitlines():
        corte = ln.find("--")
        linhas.append(ln if corte < 0 else ln[:corte])
    return "\n".join(linhas)


def _bloco(tabela: str) -> str:
    texto = _sem_comentario()
    inicio = texto.index(f"CREATE TABLE {tabela}")
    fim = texto.index(");", inicio)
    return texto[inicio:fim]


# ---------------------------------------------------------------------------
# 1. Existencia e fail-fast
# ---------------------------------------------------------------------------

def test_ddl_existe():
    assert DDL_PATH.is_file()


def test_duas_tabelas_e_somente_duas():
    criacoes = re.findall(r"CREATE TABLE ([a-z_.]+)", _sem_comentario())
    assert criacoes == [LISTING, REFERENCE], criacoes


def test_fail_fast_sem_if_not_exists():
    """Convencao das migrations existentes: reaplicar deve falhar alto."""
    assert "IF NOT EXISTS" not in _sem_comentario().upper()


def test_nao_e_uma_migration_alembic():
    """E' SQL puro. O comentario EXPLICA a colisao de revisao; o corpo nao a tem.

    A varredura roda sobre `_sem_comentario()` de proposito: o cabecalho do
    arquivo cita `revision`/`down_revision` para documentar por que a `013` esta
    reservada, e uma varredura sobre o texto bruto reprovaria justamente a
    explicacao que o gate pediu para registrar.
    """
    corpo = _sem_comentario()
    assert "down_revision" not in corpo
    assert "def upgrade" not in corpo
    assert "def downgrade" not in corpo
    assert "revision" not in corpo
    assert "import " not in corpo
    # E o cabecalho de comentario documenta a dependencia.
    cabecalho = _ddl()
    assert "013" in cabecalho and "unit-economics-audit" in cabecalho
    assert "origin/main" in cabecalho


def test_nenhuma_leitura_ou_carga_no_ddl():
    """DDL nao le nem carrega dado. Somente estrutura."""
    corpo = _sem_comentario().upper()
    for proibido in ("INSERT INTO", "SELECT ", "COPY ", "UPDATE ", "MERGE "):
        assert proibido not in corpo, proibido


def test_downgrade_limitado_aos_objetos_novos():
    texto = _ddl()
    drops = re.findall(r"DROP TABLE ([a-z_.]+);", texto)
    assert set(drops) == {LISTING, REFERENCE}, drops
    # Ordem inversa da criacao.
    assert drops.index(REFERENCE) < drops.index(LISTING)
    # E nenhum DROP de objeto preexistente.
    assert "DROP SCHEMA" not in texto.upper()
    for outro in ("dim_loja", "fact_marketplace_daily_performance"):
        assert outro not in texto


# ---------------------------------------------------------------------------
# 2. Grao e chaves
# ---------------------------------------------------------------------------

def test_pk_do_fato_de_preco_e_o_grao_do_gate():
    bloco = _bloco(LISTING)
    assert "PRIMARY KEY (ref_date, marketplace, seller_id, item_id)" in bloco


def test_pk_do_snapshot_permite_append_only():
    bloco = _bloco(REFERENCE)
    assert "PRIMARY KEY (snapshot_id, reference_row_id)" in bloco


def test_variation_id_nao_existe_no_fato():
    """A fonte tem `variation_id` = 0 em 100% das linhas: coluna constante nao entra."""
    assert "variation_id" not in _bloco(LISTING)


def test_colunas_exigidas_pelo_gate_no_fato_de_preco():
    bloco = _bloco(LISTING)
    for coluna in ("ref_date", "marketplace", "brand", "seller_id", "item_id",
                   "seller_sku", "gtin", "listing_title", "permalink",
                   "advertised_price", "original_price", "currency",
                   "listing_status", "catalog_listing",
                   "price_captured_at", "listing_metadata_updated_at",
                   "synced_at", "source_run_id"):
        assert re.search(rf"^\s+{coluna}\s", bloco, re.M), coluna
    # O nome antigo, semanticamente falso, nao pode voltar (F2).
    assert "source_updated_at" not in bloco


def test_colunas_exigidas_pelo_gate_no_snapshot():
    bloco = _bloco(REFERENCE)
    for coluna in ("snapshot_id", "captured_at", "brand", "reference_row_id",
                   "source_sku", "source_gtin", "product_name",
                   "wholesale_amount", "suggested_retail_amount",
                   "reference_type", "validity_status", "quality_status",
                   "quality_notes", "source_file_hash", "synced_at",
                   "source_run_id"):
        assert re.search(rf"^\s+{coluna}\s", bloco, re.M), coluna


# ---------------------------------------------------------------------------
# 3. CHECKs financeiros e de dominio
# ---------------------------------------------------------------------------

def test_todo_valor_monetario_proibe_nan():
    """'NaN'::numeric >= 0 e' TRUE em Postgres: o CHECK de sinal nao basta."""
    corpo = _sem_comentario()
    for coluna in ("advertised_price", "original_price",
                   "suggested_retail_amount", "wholesale_amount"):
        assert re.search(rf"{coluna}\s*<>\s*'NaN'", corpo), coluna


def test_preco_anunciado_e_nao_negativo():
    bloco = _bloco(LISTING)
    assert "advertised_price >= 0" in bloco
    assert re.search(r"original_price IS NULL\s*\n?\s*OR \(original_price >= 0", bloco)


def test_preco_de_referencia_e_estritamente_positivo_quando_existe():
    """Referencia igual a zero nao e' referencia."""
    bloco = _bloco(REFERENCE)
    assert "suggested_retail_amount > 0" in bloco
    assert "wholesale_amount > 0" in bloco


def test_suggested_retail_amount_e_anulavel():
    """F1: linha sem PDV sobrevive no snapshot como NULO, sem NOT NULL."""
    bloco = _bloco(REFERENCE)
    m = re.search(r"^\s+suggested_retail_amount\s+numeric\(14,2\)(.*)$", bloco, re.M)
    assert m, "coluna suggested_retail_amount nao encontrada"
    assert "NOT NULL" not in m.group(1), m.group(0)


def test_check_de_pdv_e_biconditional_com_quality_status():
    """F1: `missing_suggested_price` <=> valor NULO, nos DOIS sentidos.

    Um CHECK que exigisse so um dos sentidos deixaria passar defeito: linha `ok`
    com valor NULO viraria referencia fantasma; linha `missing` com valor
    mentiria sobre a planilha.
    """
    bloco = _bloco(REFERENCE)
    check = re.search(
        r"CONSTRAINT ck_fsprs_suggested_retail_amount\s*CHECK \((.*?)\n\s*\),",
        bloco, re.S,
    )
    assert check, "CHECK de suggested_retail_amount nao encontrado"
    corpo = " ".join(check.group(1).split())
    # Sentido 1: missing => NULO  (espacos ja colapsados por `" ".join(split())`)
    assert ("quality_status = 'missing_suggested_price' "
            "AND suggested_retail_amount IS NULL") in corpo
    # Sentido 2: nao-missing => valor presente, positivo e nao-NaN
    assert "quality_status <> 'missing_suggested_price'" in corpo
    assert "suggested_retail_amount IS NOT NULL" in corpo
    assert "suggested_retail_amount > 0" in corpo
    assert "suggested_retail_amount <> 'NaN'" in corpo
    assert " OR " in corpo


def test_wholesale_amount_e_independente_do_diagnostico():
    """A planilha pode ter atacado sem PDV: os dois nao se amarram."""
    bloco = _bloco(REFERENCE)
    check = re.search(
        r"CONSTRAINT ck_fsprs_wholesale_amount\s*CHECK \((.*?)\),", bloco, re.S
    )
    assert check
    assert "quality_status" not in check.group(1)


def test_marketplace_restrito_a_ml():
    assert "CHECK (marketplace = 'ml')" in _bloco(LISTING)


def test_moeda_restrita_a_brl():
    assert "CHECK (currency = 'BRL')" in _bloco(LISTING)


def test_dominio_de_listing_status():
    bloco = _bloco(LISTING)
    for estado in ("active", "paused", "under_review", "inactive"):
        assert f"'{estado}'" in bloco, estado


def test_reference_type_so_admite_pdv_nunca_pma():
    bloco = _bloco(REFERENCE)
    assert "CHECK (reference_type = 'suggested_retail_pdv')" in bloco
    assert "'pma'" not in bloco.lower()


def test_validity_status_admite_somente_missing():
    """F5: o banco nao pode aceitar um estado de vigencia que nao consegue provar.

    A tabela nao tem `valid_from` nem `valid_to`; aceitar `declared`/`expired`
    convidava a preenche-los por engano.
    """
    bloco = _bloco(REFERENCE)
    assert "CHECK (validity_status = 'missing')" in bloco
    assert "'declared'" not in bloco
    assert "'expired'" not in bloco


def test_nao_existe_coluna_de_vigencia():
    bloco = _bloco(REFERENCE).lower()
    for coluna in ("valid_from", "valid_to", "vigencia", "effective_from"):
        assert coluna not in bloco, coluna


def test_dominio_de_quality_status():
    bloco = _bloco(REFERENCE)
    for estado in ("ok", "ambiguous_duplicate_sku", "ambiguous_duplicate_gtin",
                   "ambiguous_duplicate_both", "missing_suggested_price"):
        assert f"'{estado}'" in bloco, estado


def test_allowlist_de_marcas_por_tabela():
    """Fato de preco: 4 marcas com catalogo ML. Snapshot: 5 marcas com planilha."""
    fato = _bloco(LISTING)
    for marca in ("barbours", "kokeshi", "lescent", "rituaria"):
        assert f"'{marca}'" in fato, marca
    for fora in ("apice", "yenzah"):
        assert f"'{fora}'" not in fato, fora

    snap = _bloco(REFERENCE)
    for marca in ("apice", "barbours", "kokeshi", "rituaria", "yenzah"):
        assert f"'{marca}'" in snap, marca


def test_gtin_do_snapshot_recusa_14_digitos():
    """DUN de caixa nunca pode ocupar a coluna que decide match primario."""
    bloco = _bloco(REFERENCE)
    assert "source_gtin         varchar(13)" in bloco
    assert re.search(r"source_gtin ~ '\^\[0-9\]\{8\}\$\|\^\[0-9\]\{12,13\}\$'", bloco)


def test_gtin_do_fato_aceita_o_que_o_anuncio_declara():
    """No lado da OBSERVACAO guardamos o que existe; a regra de DUN e' da referencia."""
    bloco = _bloco(LISTING)
    assert re.search(r"gtin ~ '\^\[0-9\]\{8,14\}\$'", bloco)


def test_strings_de_identidade_nao_podem_ser_vazias():
    corpo = _sem_comentario()
    for coluna in ("item_id", "source_run_id", "source_sku", "product_name",
                   "snapshot_id"):
        assert re.search(rf"btrim\({coluna}\) <> ''", corpo), coluna


def test_hash_e_hexadecimal_de_64():
    bloco = _bloco(REFERENCE)
    assert re.search(r"source_file_hash ~ '\^\[0-9a-f\]\{64\}\$'", bloco)
    assert re.search(r"reference_row_id ~ '\^\[0-9a-f\]\{64\}\$'", bloco)


# ---------------------------------------------------------------------------
# 4. Nulos: onde sao permitidos e por que
# ---------------------------------------------------------------------------

def test_colunas_anulaveis_do_fato_sao_apenas_as_declaradas():
    bloco = _bloco(LISTING)
    anulaveis = []
    for linha in bloco.splitlines():
        m = re.match(r"\s+([a-z_]+)\s+[a-z0-9(),\s]+$", linha)
        if m and "NOT NULL" not in linha and "CONSTRAINT" not in linha:
            anulaveis.append(m.group(1))
    assert set(anulaveis) == {
        "seller_sku", "gtin", "original_price", "catalog_listing",
        # `listing_metadata_updated_at` e' anulavel; `price_captured_at` NAO e',
        # porque e' o unico timestamp que descreve a observacao do preco (F2).
        "listing_metadata_updated_at",
    }, anulaveis


def test_price_captured_at_e_obrigatorio_e_metadata_e_anulavel():
    bloco = _bloco(LISTING)
    assert re.search(r"^\s+price_captured_at\s+timestamp\s+NOT NULL", bloco, re.M)
    m = re.search(r"^\s+listing_metadata_updated_at\s+timestamp(.*)$", bloco, re.M)
    assert m and "NOT NULL" not in m.group(1)


def test_timestamps_copiados_da_fonte_nao_ganham_fuso_inventado():
    """A Silver tem `timestamp without time zone` e nao declara seu relogio.

    Converter para `timestamptz` exigiria escolher um fuso — inventar dado. O que
    NOS geramos (`synced_at`) e' `timestamptz`; o que copiamos, `timestamp`.
    """
    bloco = _bloco(LISTING)
    assert re.search(r"price_captured_at\s+timestamp\s+NOT NULL", bloco)
    assert not re.search(r"price_captured_at\s+timestamptz", bloco)
    assert not re.search(r"listing_metadata_updated_at\s+timestamptz", bloco)
    assert re.search(r"synced_at\s+timestamptz\s+NOT NULL", bloco)


def test_snapshot_nao_tem_coluna_de_dado_cadastral():
    """ZERO PII: nenhuma coluna de cliente, CNPJ, endereco ou contato."""
    bloco = _bloco(REFERENCE).lower()
    for proibida in ("razao", "cnpj", "cpf", "inscricao", "endereco", "cep",
                     "telefone", "email", "e_mail", "contato", "bairro",
                     "cidade", "estado", "cliente", "comprador"):
        assert proibida not in bloco, proibida


def test_fato_nao_tem_coluna_de_frete_cupom_ou_checkout():
    """Coluna inexistente nao pode ser lida como zero."""
    bloco = _bloco(LISTING).lower()
    for proibida in ("shipping", "coupon", "cupom", "subsidy", "subsidio",
                     "checkout", "frete"):
        assert proibida not in bloco, proibida


# ---------------------------------------------------------------------------
# 5. Indices minimos
# ---------------------------------------------------------------------------

def test_nenhum_indice_secundario():
    """F9: todo indice removido, porque nenhum SQL do serving o consumia.

    As cinco consultas de `monitoramento_preco_service` sao servidas por um
    PREFIXO de uma das duas PKs. Indice que nenhum SQL usa custa escrita, espaco
    e VACUUM sem devolver nada.
    """
    corpo = _sem_comentario()
    assert not re.findall(r"CREATE INDEX", corpo), corpo


def test_a_remocao_de_cada_indice_esta_justificada_por_escrito():
    """O DDL nomeia cada indice removido e a razao — nao remove em silencio."""
    texto = _ddl()
    for removido in ("ix_fmlpd_ref_date", "ix_fmlpd_marketplace_ref_date",
                     "ix_fmlpd_brand_ref_date", "ix_fmlpd_brand_gtin",
                     "ix_fmlpd_brand_seller_sku", "ix_fsprs_snapshot_id",
                     "ix_fsprs_captured_at", "ix_fsprs_brand_gtin",
                     "ix_fsprs_brand_sku"):
        assert removido in texto, removido
    # E declara que nao houve EXPLAIN, em vez de fingir medicao.
    assert "NAO HOUVE `EXPLAIN`" in texto
    # E registra o unico caso sabidamente nao indexavel por btree.
    assert "pg_trgm" in texto


# ---------------------------------------------------------------------------
# 6. Documentacao da semantica no proprio banco
# ---------------------------------------------------------------------------

def test_comentario_nega_explicitamente_a_equivalencia_com_pma():
    texto = _ddl()
    trecho = texto[texto.index("COMMENT ON TABLE " + REFERENCE):]
    trecho = trecho[:trecho.index(";")]
    assert "NAO E'' PMA" in trecho or "NAO E' PMA" in trecho
