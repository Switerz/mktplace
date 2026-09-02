"""Gate PMA-1B — migration 014 e cadeia Alembic linear.

A dependencia que bloqueava o PMA-1A-R foi resolvida: `origin/main` avancou para
`9e4e699`, trazendo a `013` da frente UE8 (`revision="013"`,
`down_revision="012"`). Integrada por fast-forward, a `014` deste gate pousa
sobre ela sem ramificar.

Estes testes travam quatro coisas:
  1. a cadeia continua LINEAR com head unico em `014`;
  2. a `013` da UE8 NAO foi alterada por esta frente;
  3. a `014` cria exatamente os dois objetos do serving de precos, e o downgrade
     remove exatamente os mesmos, na ordem inversa;
  4. a `014` e' TRANSCRICAO do DDL versionado — colunas e constraints conferem
     nos dois arquivos, de modo que um editado sem o outro reprova.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
VERSIONS = REPO / "apps" / "api" / "alembic" / "versions"
DDL_PATH = REPO / "db" / "sql" / "marts" / "pma_listing_price_serving_ddl.sql"

#: Head esperado. Pino LITERAL, como em `test_s3_migrations.py`: forca revisao
#: consciente a cada migration nova, em vez de aceitar qualquer head.
HEAD_ESPERADO = "014"

#: A migration deste gate.
REVISAO_PMA = "014"
ARQUIVO_PMA = "014_create_price_monitoring_serving.py"

#: A revisao da UE8 sobre a qual a nossa pousa. NAO pode ser alterada aqui.
REVISAO_UE8 = "013"
ARQUIVO_UE8 = "013_create_fact_tiktok_order_discounts_daily.py"

TABELAS_PMA = (
    "marts.fact_marketplace_listing_price_daily",
    "marts.fact_suggested_price_reference_snapshot",
)


def _migrations() -> dict[str, dict]:
    """Le revision/down_revision de cada arquivo por AST, sem importar o modulo.

    Importar uma migration executaria seu `import` de topo; desnecessario para
    inspecionar a cadeia, e indesejavel num teste.
    """
    achadas: dict[str, dict] = {}
    for caminho in sorted(VERSIONS.glob("*.py")):
        if caminho.name == "__init__.py":
            continue
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        info = {"file": caminho.name, "revision": None, "down_revision": None}
        for no in arvore.body:
            if not isinstance(no, ast.Assign):
                continue
            for alvo in no.targets:
                nome = getattr(alvo, "id", None)
                if nome in ("revision", "down_revision"):
                    info[nome] = (
                        no.value.value if isinstance(no.value, ast.Constant) else None
                    )
        assert info["revision"], caminho.name
        achadas[info["revision"]] = info
    return achadas


def _fonte_pma() -> str:
    return (VERSIONS / ARQUIVO_PMA).read_text(encoding="utf-8")


def _corpo(funcao: str) -> str:
    """Corpo textual de `upgrade` ou `downgrade` da 014."""
    fonte = _fonte_pma()
    arvore = ast.parse(fonte)
    for no in arvore.body:
        if isinstance(no, ast.FunctionDef) and no.name == funcao:
            linhas = fonte.splitlines(keepends=True)
            return "".join(linhas[no.lineno - 1:no.end_lineno])
    raise AssertionError(f"funcao {funcao} nao encontrada na 014")


def _sql_executado() -> list[str]:
    """Cada string passada a `op.execute(...)`, como statement separado."""
    arvore = ast.parse(_fonte_pma())
    pedacos = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        if getattr(no.func, "attr", None) != "execute":
            continue
        for arg in no.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                pedacos.append(arg.value)
    return pedacos


def _sql_da_014() -> str:
    """Todo o SQL executado pela 014, junto."""
    return "\n".join(_sql_executado())


def _sem_comentario_sql(texto: str) -> str:
    linhas = []
    for ln in texto.splitlines():
        corte = ln.find("--")
        linhas.append(ln if corte < 0 else ln[:corte])
    return "\n".join(linhas)


def _sql_estrutural() -> str:
    """Somente os `CREATE TABLE`, sem os `COMMENT ON` e sem comentario `--`.

    A separacao e' necessaria, nao cosmetica: os textos de `COMMENT ON` citam
    deliberadamente `silver.stg_ml_items`, "razao social, CNPJ, endereco" (para
    declarar que NAO sao persistidos) e "IF NOT EXISTS" (para explicar a
    convencao). Varrer estrutura junto com prosa reprovaria justamente a
    documentacao que o gate pediu para escrever.
    """
    pedacos = [
        _sem_comentario_sql(s)
        for s in _sql_executado()
        if "CREATE TABLE" in s
    ]
    return "\n".join(pedacos)


def _sql_comentarios() -> str:
    """Somente os `COMMENT ON`. E' aqui que a prosa vive."""
    return "\n".join(s for s in _sql_executado() if "COMMENT ON" in s)


# ---------------------------------------------------------------------------
# 1. Cadeia
# ---------------------------------------------------------------------------

def test_diretorio_de_versions_existe():
    assert VERSIONS.is_dir()


def test_toda_migration_declara_revision():
    migracoes = _migrations()
    assert migracoes
    for rev, info in migracoes.items():
        assert isinstance(rev, str) and rev, info


def test_cadeia_e_linear_sem_ramificacao():
    """Nenhum `down_revision` pode ser apontado por duas revisoes."""
    migracoes = _migrations()
    pais: dict[str, list[str]] = {}
    for rev, info in migracoes.items():
        pais.setdefault(info["down_revision"], []).append(rev)
    ramificados = {pai: filhos for pai, filhos in pais.items() if len(filhos) > 1}
    assert not ramificados, ramificados


def test_existe_exatamente_uma_raiz():
    migracoes = _migrations()
    raizes = [r for r, i in migracoes.items() if i["down_revision"] is None]
    assert len(raizes) == 1, raizes


def test_head_unico_e_o_pino_literal():
    migracoes = _migrations()
    apontados = {i["down_revision"] for i in migracoes.values()}
    heads = [r for r in migracoes if r not in apontados]
    assert len(heads) == 1, heads
    assert heads[0] == HEAD_ESPERADO, heads


def test_todo_down_revision_existe_no_diretorio():
    """Nenhuma migration pode depender de revisao ausente."""
    migracoes = _migrations()
    for rev, info in migracoes.items():
        pai = info["down_revision"]
        if pai is None:
            continue
        assert pai in migracoes, (rev, pai)


def test_014_aponta_para_013():
    migracoes = _migrations()
    assert REVISAO_PMA in migracoes
    assert migracoes[REVISAO_PMA]["down_revision"] == REVISAO_UE8
    assert migracoes[REVISAO_PMA]["file"] == ARQUIVO_PMA


def test_013_da_ue8_permanece_intacta():
    """O gate proibe alterar a 013. Aqui isso e' verificado, nao prometido."""
    migracoes = _migrations()
    assert REVISAO_UE8 in migracoes
    assert migracoes[REVISAO_UE8]["down_revision"] == "012"
    assert migracoes[REVISAO_UE8]["file"] == ARQUIVO_UE8
    # E a 013 nao menciona nada do PMA.
    texto = (VERSIONS / ARQUIVO_UE8).read_text(encoding="utf-8").lower()
    for marcador in ("pma", "listing_price", "suggested_price",
                     "monitoramento_preco"):
        assert marcador not in texto, marcador


def test_nomes_de_arquivo_seguem_o_prefixo_numerico():
    for caminho in VERSIONS.glob("*.py"):
        if caminho.name == "__init__.py":
            continue
        assert re.match(r"^\d{3}_[a-z0-9_]+\.py$", caminho.name), caminho.name


def test_prefixo_do_arquivo_bate_com_a_revision():
    for rev, info in _migrations().items():
        assert info["file"][:3] == rev, info


def test_migration_nao_tem_efeito_no_import():
    """Nenhuma migration executa DDL ou abre conexao no nivel do modulo.

    Alembic importa todos os arquivos de `versions/` ao montar a cadeia. Efeito
    colateral no import rodaria em `alembic history`, que nao deveria tocar nada.
    """
    for caminho in sorted(VERSIONS.glob("*.py")):
        if caminho.name == "__init__.py":
            continue
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        for no in arvore.body:
            if isinstance(no, (ast.FunctionDef, ast.ClassDef)):
                continue
            assert isinstance(
                no, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.Expr)
            ), f"{caminho.name}: {type(no).__name__} no topo"
            if isinstance(no, ast.Expr) and not isinstance(no.value, ast.Constant):
                raise AssertionError(f"{caminho.name}: expressao no topo")
            for filho in ast.walk(no):
                if isinstance(filho, ast.Call):
                    nome = (getattr(filho.func, "id", None)
                            or getattr(filho.func, "attr", ""))
                    raise AssertionError(f"{caminho.name}: chamada {nome!r} no topo")


# ---------------------------------------------------------------------------
# 2. A 014 cria exatamente os dois objetos
# ---------------------------------------------------------------------------

def test_014_cria_somente_as_duas_tabelas():
    sql = _sql_estrutural()
    criadas = re.findall(r"CREATE TABLE ([a-z_.]+)", sql)
    assert criadas == list(TABELAS_PMA), criadas


def test_014_nao_cria_indice_secundario():
    assert "CREATE INDEX" not in _sql_estrutural().upper()


def test_014_nao_usa_if_not_exists():
    """Convencao 006-013: colisao tem de falhar alto.

    Varre o SQL ESTRUTURAL. O comentario da funcao diz "Sem IF NOT EXISTS" para
    documentar a convencao, e varrer prosa junto reprovaria a explicacao.
    """
    assert "IF NOT EXISTS" not in _sql_estrutural().upper()


def test_014_nao_le_nem_carrega_dado():
    """Nenhuma leitura, carga ou referencia a schema de origem no SQL executado.

    `GOLD.`/`RAW.`/`SILVER.` sao vetados no ESTRUTURAL: os textos de COMMENT
    citam `silver.stg_ml_items` de proposito, para nomear a procedencia dos
    atributos de estado corrente.
    """
    estrutural = _sql_estrutural().upper()
    for proibido in ("INSERT INTO", "SELECT ", "COPY ", "UPDATE ", "MERGE ",
                     "GOLD.", "RAW.", "SILVER."):
        assert proibido not in estrutural, proibido
    # E em NENHUM statement ha comando de leitura/carga, nem em COMMENT.
    todo = _sql_da_014().upper()
    for proibido in ("INSERT INTO", "COPY ", "MERGE "):
        assert proibido not in todo, proibido


def test_014_nao_abre_conexao_propria():
    fonte = _fonte_pma().lower()
    for proibido in ("psycopg2", "create_engine", "sessionmaker", "connect(",
                     "os.environ", "database_url", "datamart"):
        assert proibido not in fonte, proibido


def test_downgrade_remove_somente_os_dois_na_ordem_inversa():
    corpo = _corpo("downgrade")
    drops = re.findall(r"DROP TABLE IF EXISTS ([a-z_.]+)", corpo)
    assert drops == list(reversed(TABELAS_PMA)), drops
    assert "DROP SCHEMA" not in corpo.upper()
    assert "CASCADE" not in corpo.upper()
    # Nenhuma tabela de outra frente e' tocada.
    for outra in ("fact_tiktok_order_discounts_daily", "dim_loja",
                  "fact_marketplace_daily_performance"):
        assert outra not in corpo, outra


# ---------------------------------------------------------------------------
# 3. Contrato de dados dentro da 014
# ---------------------------------------------------------------------------

def test_pdv_anulavel_com_check_biconditional():
    sql = _sql_estrutural()
    m = re.search(r"suggested_retail_amount NUMERIC\(14,2\)(.*)$", sql, re.M)
    assert m and "NOT NULL" not in m.group(1), m.group(0) if m else "ausente"
    corpo = " ".join(sql.split())
    assert ("quality_status = 'missing_suggested_price' "
            "AND suggested_retail_amount IS NULL") in corpo
    assert "quality_status <> 'missing_suggested_price'" in corpo
    assert "suggested_retail_amount IS NOT NULL" in corpo
    assert "suggested_retail_amount > 0" in corpo


def test_validity_status_somente_missing():
    sql = _sql_estrutural()
    assert "CHECK (validity_status = 'missing')" in sql
    assert "'declared'" not in sql
    assert "'expired'" not in sql


def test_reference_type_nunca_admite_pma():
    sql = _sql_estrutural()
    assert "CHECK (reference_type = 'suggested_retail_pdv')" in sql
    assert "'pma'" not in sql.lower()


def test_semantica_dos_timestamps():
    sql = _sql_estrutural()
    assert re.search(r"price_captured_at\s+TIMESTAMP\s+NOT NULL", sql)
    assert re.search(r"listing_metadata_updated_at\s+TIMESTAMP\b", sql)
    assert re.search(r"synced_at\s+TIMESTAMPTZ\s+NOT NULL", sql)
    assert re.search(r"captured_at\s+TIMESTAMPTZ\s+NOT NULL", sql)
    # O nome antigo, semanticamente falso, nao pode voltar.
    assert "source_updated_at" not in sql
    # E os copiados da fonte nao ganham fuso inventado.
    assert not re.search(r"price_captured_at\s+TIMESTAMPTZ", sql)
    assert not re.search(r"listing_metadata_updated_at\s+TIMESTAMPTZ", sql)


def test_checks_contra_nan_em_todo_valor_monetario():
    sql = _sql_estrutural()
    for coluna in ("advertised_price", "original_price",
                   "suggested_retail_amount", "wholesale_amount"):
        assert re.search(rf"{coluna}\s*<>\s*'NaN'", sql), coluna


def test_as_duas_pks_e_o_grao():
    sql = _sql_estrutural()
    assert "PRIMARY KEY (ref_date, marketplace, seller_id, item_id)" in sql
    assert "PRIMARY KEY (snapshot_id, reference_row_id)" in sql


def test_014_nao_tem_coluna_de_pii():
    """Nenhuma COLUNA cadastral nas duas tabelas.

    A varredura roda no SQL ESTRUTURAL: o texto de COMMENT ENUMERA os campos do
    bloco cadastral ("razao social, CNPJ, I.E., endereco, CEP, telefone,
    e-mail") justamente para declarar que NAO sao lidos nem persistidos. Vetar
    essas palavras na prosa apagaria o aviso.
    """
    estrutural = _sql_estrutural().lower()
    for proibida in ("razao", "cnpj", "cpf", "inscricao", "endereco", "cep",
                     "telefone", "email", "e_mail", "contato", "bairro",
                     "cliente", "comprador"):
        assert proibida not in estrutural, proibida
    # E o aviso segue escrito no COMMENT, onde o DBA o le.
    comentarios = _sql_comentarios().lower()
    assert "zero pii" in comentarios
    assert "nao e'' lido nem" in comentarios


def test_014_nao_tem_dado_de_planilha_nem_preco():
    """Somente estrutura. Nenhum valor monetario nem SKU literal."""
    sql = _sql_estrutural()
    # Nenhum literal numerico com centavos (ex.: 54.90) fora de NUMERIC(14,2).
    sem_tipos = re.sub(r"NUMERIC\(\d+,\d+\)", "", sql)
    assert not re.search(r"\b\d+\.\d{2}\b", sem_tipos), sem_tipos[:200]
    # Nenhum SKU/EAN das planilhas.
    for literal in ("BB03038", "KS06004", "RT01016", "7901128300047",
                    "7908790700922"):
        assert literal not in sql, literal


def test_014_nao_menciona_outros_canais():
    sql = _sql_estrutural().lower()
    assert "'ml'" in sql
    for fora in ("shopee", "tiktok", "amazon", "supabase"):
        assert fora not in sql, fora


# ---------------------------------------------------------------------------
# 4. A 014 e' transcricao do DDL versionado
# ---------------------------------------------------------------------------

def _colunas(sql: str, tabela: str) -> set[str]:
    inicio = sql.index(f"CREATE TABLE {tabela}")
    bloco = sql[inicio:sql.index("PRIMARY KEY", inicio)]
    return set(re.findall(r"^\s{4,}([a-z_]+)\s+[A-Za-z]", bloco, re.M))


def _constraints(sql: str) -> set[str]:
    return set(re.findall(r"CONSTRAINT (\w+)", sql))


def test_colunas_da_014_batem_com_o_ddl_versionado():
    """`db/sql/marts/...ddl.sql` e' a especificacao; a 014 e' a transcricao.

    Editar um sem o outro reprova aqui — que e' o ponto: o DDL foi o artefato
    aplicado e revertido no Postgres descartavel, e a migration precisa ser o
    mesmo objeto.
    """
    da_migration = _sem_comentario_sql(_sql_da_014())
    do_ddl = _sem_comentario_sql(DDL_PATH.read_text(encoding="utf-8"))
    for tabela in TABELAS_PMA:
        assert _colunas(da_migration, tabela) == _colunas(do_ddl, tabela), tabela


def test_constraints_da_014_batem_com_o_ddl_versionado():
    da_migration = _constraints(_sem_comentario_sql(_sql_da_014()))
    do_ddl = _constraints(_sem_comentario_sql(DDL_PATH.read_text(encoding="utf-8")))
    assert da_migration == do_ddl, (da_migration ^ do_ddl)


def test_ddl_versionado_continua_existindo_como_especificacao():
    assert DDL_PATH.is_file()
    texto = DDL_PATH.read_text(encoding="utf-8")
    # Ele nao e' uma migration e nao deve virar uma.
    corpo = _sem_comentario_sql(texto)
    assert "down_revision" not in corpo
    assert "def upgrade" not in corpo
