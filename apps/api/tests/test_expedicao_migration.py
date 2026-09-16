"""Gate EXP-1C — migration 018 e o serving da Expedicao Shopee.

A infraestrutura de teste de migration deste repositorio e' ESTATICA: nenhuma
das suites existentes (`test_s3_migrations`, `test_pma_migration_chain`) sobe
banco descartavel para rodar `upgrade`/`downgrade`. Elas leem o arquivo por AST
e regex, sem importar o modulo. Este arquivo mantem essa convencao para a
CADEIA, mas vai um passo alem para o DDL.

POR QUE ASSERTAR NO SQL EMITIDO, E NAO NO TEXTO DO ARQUIVO
-----------------------------------------------------------
Procurar `"CREATE TABLE"` no texto-fonte tem dois defeitos que ja' custaram
retrabalho nesta frente:

  1. acusa a propria DOCSTRING que explica por que algo nao existe — e obriga a
     documentar menos para o teste passar;
  2. nao enxerga nome gerado por f-string. `ck_efa_{coluna}_finito` num laco
     nunca aparece literalmente no arquivo, entao um `grep` daria falso negativo
     justamente nas constraints geradas em lote.

Por isso `upgrade()` e `downgrade()` sao EXECUTADOS contra um `op` falso que so'
grava o SQL. As assercoes rodam sobre o DDL que o Postgres receberia — que e' o
que de fato importa. Nada toca banco: o `op` falso nao tem conexao.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from unittest import mock

import alembic
import pytest

from pipelines.expedicao import publisher as P
from pipelines.expedicao.contract import (
    FILA_TABLE,
    RUN_TABLE,
    Channel,
    DeadlineStatus,
    FreshnessStatus,
    OperationalAgeStatus,
    RunStatus,
    TimestampQuality,
)

REPO = Path(__file__).resolve().parents[3]
VERSIONS = REPO / "apps" / "api" / "alembic" / "versions"

REVISAO = "018"
DOWN_REVISION = "017"
ARQUIVO = "018_create_expedicao_serving.py"

FILA = FILA_TABLE.split(".")[-1]
RESUMO = RUN_TABLE.split(".")[-1]

#: Tabela declarada no contrato como EVOLUCAO FUTURA — nao pode ser criada aqui.
TABELA_FUTURA = "expedicao_alert_event"


class _OpFalso:
    """Substitui `alembic.op` e apenas grava o SQL. Sem conexao, sem banco."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, comando) -> None:
        self.sql.append(str(comando))


def _emitido(funcao: str) -> list[str]:
    """SQL que `upgrade()`/`downgrade()` emitiriam, sem tocar em banco."""
    gravador = _OpFalso()
    spec = importlib.util.spec_from_file_location("_mig018", VERSIONS / ARQUIVO)
    modulo = importlib.util.module_from_spec(spec)
    # `from alembic import op` resolve no momento do exec_module, entao o patch
    # precisa cobrir a importacao E a chamada. `create=True` porque `alembic.op`
    # so' vira atributo do pacote depois que o submodulo e' importado ao menos
    # uma vez — e a ordem disso varia conforme o que a suite ja' carregou.
    with mock.patch.object(alembic, "op", gravador, create=True):
        spec.loader.exec_module(modulo)
        getattr(modulo, funcao)()
    return gravador.sql


def _ddl_upgrade() -> str:
    return "\n".join(_emitido("upgrade"))


def _ddl_downgrade() -> str:
    return "\n".join(_emitido("downgrade"))


def _create_de(tabela: str) -> str:
    for comando in _emitido("upgrade"):
        if "CREATE TABLE" in comando and tabela in comando:
            return comando
    raise AssertionError(f"CREATE TABLE de {tabela} nao emitido")


def _fonte() -> str:
    return (VERSIONS / ARQUIVO).read_text(encoding="utf-8")


def _literais_sem_docstring(caminho: Path) -> list[str]:
    """Strings do codigo, excluindo docstrings. Comentarios nem chegam ao AST."""
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    docs = set()
    for no in ast.walk(arvore):
        if isinstance(
            no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            texto = ast.get_docstring(no, clean=False)
            if texto is not None:
                docs.add(texto)
    fragmentos = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.JoinedStr):
            for filho in ast.walk(no):
                if filho is not no:
                    fragmentos.add(id(filho))
    saida = []
    for no in ast.walk(arvore):
        if id(no) in fragmentos:
            continue
        if isinstance(no, ast.JoinedStr):
            saida.append(ast.unparse(no))
        elif (
            isinstance(no, ast.Constant)
            and isinstance(no.value, str)
            and no.value not in docs
        ):
            saida.append(no.value)
    return saida


def _migrations() -> dict[str, dict]:
    """revision/down_revision de cada arquivo, por AST e sem importar."""
    achadas: dict[str, dict] = {}
    for caminho in sorted(VERSIONS.glob("*.py")):
        if caminho.name == "__init__.py":
            continue
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        info = {"file": caminho.name, "revision": None, "down_revision": None}
        for no in arvore.body:
            if isinstance(no, ast.Assign):
                for alvo in no.targets:
                    nome = getattr(alvo, "id", None)
                    if nome in ("revision", "down_revision"):
                        info[nome] = (
                            no.value.value
                            if isinstance(no.value, ast.Constant)
                            else None
                        )
        assert info["revision"], caminho.name
        achadas[info["revision"]] = info
    return achadas


# ---------------------------------------------------------------------------
# Cadeia Alembic
# ---------------------------------------------------------------------------
def test_018_existe_e_aponta_para_017():
    migracoes = _migrations()
    assert REVISAO in migracoes, "018 nao encontrada"
    assert migracoes[REVISAO]["down_revision"] == DOWN_REVISION
    assert migracoes[REVISAO]["file"] == ARQUIVO


def test_prefixo_do_arquivo_bate_com_a_revision():
    assert ARQUIVO.startswith(f"{REVISAO}_")


def test_branch_labels_e_depends_on_sao_none():
    """Qualquer um dos dois preenchido ramificaria a arvore."""
    fonte = _fonte()
    assert re.search(r"^branch_labels = None$", fonte, re.M)
    assert re.search(r"^depends_on = None$", fonte, re.M)


def test_cadeia_tem_raiz_unica_e_e_linear():
    """Propriedades DURAVEIS: valem em qualquer head futuro, sem numero fixo.

    O pino LITERAL do head fica em `test_s3_migrations.py` e
    `test_pma_migration_chain.py`, por convencao do repositorio. Um terceiro
    pino aqui so' daria mais um numero para a proxima frente atualizar.
    """
    migracoes = _migrations()
    filhos: dict[str | None, list[str]] = {}
    for rev, info in migracoes.items():
        filhos.setdefault(info["down_revision"], []).append(rev)

    raizes = [r for r, i in migracoes.items() if i["down_revision"] is None]
    assert len(raizes) == 1, raizes

    ramos = {p: f for p, f in filhos.items() if p is not None and len(f) > 1}
    assert not ramos, f"branch na cadeia: {ramos}"

    heads = [r for r in migracoes if r not in filhos]
    assert len(heads) == 1, heads


def test_todo_down_revision_existe_no_diretorio():
    migracoes = _migrations()
    for rev, info in migracoes.items():
        pai = info["down_revision"]
        if pai is not None:
            assert pai in migracoes, f"{rev} aponta para {pai}, ausente"


def test_017_do_pma_nao_foi_alterada():
    """A 018 pousa sobre a 017; nao a edita."""
    migracoes = _migrations()
    assert migracoes["017"]["down_revision"] == "016"
    assert migracoes["017"]["file"] == "017_create_fact_channel_offer_observation.py"


def test_migration_nao_tem_efeito_no_import():
    """So' `def`, atribuicoes e imports no topo — nada executa ao importar."""
    arvore = ast.parse(_fonte())
    for no in arvore.body:
        assert isinstance(
            no, (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef, ast.Expr)
        ), type(no).__name__
        if isinstance(no, ast.Expr):
            assert isinstance(no.value, ast.Constant)  # so' a docstring


# ---------------------------------------------------------------------------
# O que a 018 cria — e o que ela nao cria
# ---------------------------------------------------------------------------
def test_cria_exatamente_as_duas_tabelas_do_contrato():
    ddl = _ddl_upgrade()
    assert ddl.count("CREATE TABLE") == 2
    assert FILA in ddl
    assert RESUMO in ddl


def test_nao_cria_a_tabela_de_evento_reservada_ao_futuro():
    """`expedicao_alert_event` e' evolucao futura; a docstring pode cita-la, o
    DDL nao pode cria-la."""
    assert TABELA_FUTURA not in _ddl_upgrade()


def test_nao_toca_tabela_de_outra_frente():
    ddl = _ddl_upgrade() + _ddl_downgrade()
    for alheia in (
        "fact_ml_fulfillment_daily",
        "fact_ml_fulfillment_listing_daily",
        "fact_channel_offer_observation",
        "fact_marketplace_listing_price_daily",
        "fact_suggested_price_reference_snapshot",
    ):
        assert alheia not in ddl, alheia


def test_nao_usa_if_not_exists_no_create():
    """Silenciar "ja existe" mascararia aplicacao parcial anterior."""
    for comando in _emitido("upgrade"):
        if "CREATE TABLE" in comando:
            assert "IF NOT EXISTS" not in comando.upper()


def test_nao_carrega_nem_semeia_dado():
    """Nenhum DML: cadastrar conta e' gate operacional separado."""
    ddl = _ddl_upgrade().upper()
    for verbo in ("INSERT INTO", "UPDATE ", "DELETE FROM", "COPY ", "SELECT "):
        assert verbo not in ddl, verbo


def test_nao_cadastra_conta_nem_menciona_kokeshi_no_ddl():
    """A Kokeshi segue fora da ingestao Shopee. A docstring explica isso; o DDL
    nao carrega marca nem `external_seller_id` de ninguem."""
    ddl = _ddl_upgrade().lower()
    assert "kokeshi" not in ddl
    for shop_id in ("1609671923", "1579330222", "1593864538", "1457734799"):
        assert shop_id not in ddl


def test_nao_abre_conexao_propria():
    for literal in _literais_sem_docstring(VERSIONS / ARQUIVO):
        for proibido in ("psycopg2", "create_engine", "DATABASE_URL"):
            assert proibido not in literal, proibido
    arvore = ast.parse(_fonte())
    importados = {
        n.module or "" for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)
    } | {
        a.name for n in ast.walk(arvore) if isinstance(n, ast.Import) for a in n.names
    }
    assert importados <= {"alembic"}, importados


def test_nao_tem_coluna_de_pii():
    ddl = _ddl_upgrade().lower()
    for token in ("buyer", "cpf", "cnpj", "recipient", "phone", "telefone",
                  "address", "endereco", "client_name", "dropshipper"):
        assert token not in ddl, token


# ---------------------------------------------------------------------------
# Transcricao do contrato versionado
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tabela, colunas",
    [(FILA, P.FILA_COLUMNS), (RESUMO, P.SUMMARY_COLUMNS)],
)
def test_colunas_batem_com_o_contrato(tabela, colunas):
    """Coluna no publisher sem coluna na tabela quebraria o INSERT em runtime."""
    bloco = _create_de(tabela)
    for coluna in colunas:
        assert re.search(rf"^\s+{coluna}\s+\w", bloco, re.M), f"{tabela}.{coluna}"


@pytest.mark.parametrize(
    "tabela, pk",
    [(FILA, P.FILA_PRIMARY_KEY), (RESUMO, P.SUMMARY_PRIMARY_KEY)],
)
def test_pk_bate_com_o_contrato(tabela, pk):
    assert f"PRIMARY KEY ({', '.join(pk)})" in _create_de(tabela)


def test_chave_da_fila_nao_inclui_brand():
    """Corrigir a marca de uma conta nao pode criar um pedido novo."""
    assert "brand" not in P.FILA_PRIMARY_KEY
    assert (
        "PRIMARY KEY (channel, shop_account, marketplace_order_id)"
        in _create_de(FILA)
    )


@pytest.mark.parametrize(
    "tabela, pk",
    [(FILA, P.FILA_PRIMARY_KEY), (RESUMO, P.SUMMARY_PRIMARY_KEY)],
)
def test_colunas_da_chave_sao_not_null(tabela, pk):
    bloco = _create_de(tabela)
    for coluna in pk:
        assert re.search(rf"^\s+{coluna}\s+\S+\s+NOT NULL", bloco, re.M), coluna


@pytest.mark.parametrize(
    "enum",
    [Channel, DeadlineStatus, OperationalAgeStatus, FreshnessStatus,
     TimestampQuality, RunStatus],
)
def test_dominios_do_contrato_estao_no_ddl(enum):
    """Todo valor do dominio versionado aparece em algum CHECK emitido."""
    ddl = _ddl_upgrade()
    for membro in enum:
        assert f"'{membro.value}'" in ddl, f"{enum.__name__}.{membro.value}"


def test_timestamps_sao_com_fuso():
    """`TIMESTAMP` sem fuso adotaria o TimeZone da sessao, e a mesma execucao
    cairia em horas diferentes conforme o worker."""
    ddl = _ddl_upgrade()
    assert "TIMESTAMPTZ" in ddl
    for bloco in (_create_de(FILA), _create_de(RESUMO)):
        assert not re.search(r"\bTIMESTAMP\b(?!TZ)", bloco)


# ---------------------------------------------------------------------------
# Estados invalidos recusados
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "constraint",
    [
        "ck_efa_prazo_indisponivel",
        "ck_efa_origem_do_prazo",
        "ck_efa_estouro_so_em_overdue",
        "ck_efa_idade_desconhecida",
        "ck_efa_frescor_desconhecido",
        "ck_efa_stalled_derivado",
        "ck_efa_hours_open_finito",
        "ck_efa_hours_overdue_finito",
        "ck_efa_shopee_timestamp",
        "ck_efa_shopee_sem_logistica",
        "ck_err_categorias_somam_backlog",
        "ck_err_stalled_e_uniao",
        "ck_err_hora_truncada_em_utc",
        "ck_err_observacao_dentro_da_hora",
    ],
)
def test_constraint_de_estado_invalido_existe(constraint):
    """Nomes gerados em laco (`ck_efa_{coluna}_finito`) so' aparecem no SQL
    EMITIDO — no texto-fonte eles nao existem."""
    assert constraint in _ddl_upgrade(), constraint


def test_biconditionais_sao_de_duas_pernas():
    """Um CHECK de uma perna so' aceitaria a metade errada — por exemplo status
    'unavailable' com prazo preenchido."""
    ddl = _ddl_upgrade()
    for esquerda, direita in (
        ("(dispatch_deadline IS NULL)", "(deadline_status = 'unavailable')"),
        ("(dispatch_deadline IS NULL)", "(deadline_source = 'unavailable')"),
        ("(deadline_status = 'overdue')", "(hours_overdue IS NOT NULL)"),
        ("(hours_open IS NULL)", "(operational_age_status = 'unknown')"),
        ("(source_ingested_at IS NULL)", "(source_freshness_status = 'unknown')"),
    ):
        assert f"{esquerda} = {direita}" in ddl, (esquerda, direita)


def test_stalled_nunca_e_soma_de_slow_e_zombie():
    """slow e zombie podem coincidir no mesmo pedido: o intervalo da uniao e'
    GREATEST(a,b) <= stalled <= a+b, nunca a igualdade com a soma."""
    ddl = _ddl_upgrade()
    assert "stalled_count >= GREATEST(slow_count, zombie_count)" in ddl
    assert "stalled_count <= slow_count + zombie_count" in ddl
    assert "stalled_count = slow_count + zombie_count" not in ddl


#: Os tres valores que `NUMERIC` aceita e que NAO sao numero. Medido em
#: PostgreSQL 16.14: `'NaN' >= 0` e `'Infinity' >= 0` sao TRUE, e
#: `'Infinity' <> 'NaN'` tambem — logo nem um teto nem uma guarda so' de NaN
#: barram os tres.
NAO_FINITOS = ("NaN", "Infinity", "-Infinity")


@pytest.mark.parametrize("coluna", ["hours_open", "hours_overdue"])
@pytest.mark.parametrize("valor", NAO_FINITOS)
def test_duracao_recusa_valor_nao_finito(coluna, valor):
    """Cada um dos tres e' recusado EXPLICITAMENTE, em cada coluna de duracao."""
    assert f"{coluna} <> '{valor}'::numeric" in _ddl_upgrade(), (coluna, valor)


@pytest.mark.parametrize("coluna", ["hours_open", "hours_overdue"])
def test_duracao_nao_converte_invalido_em_null_ou_zero(coluna):
    """O valor invalido e' RECUSADO, nunca substituido.

    `NULL` em `hours_open` significa "sem marco inicial" e zero significaria
    "aberto agora": converter um valor corrompido para um desses inventaria um
    fato que a fonte nao sustenta.
    """
    ddl = _ddl_upgrade()
    assert f"COALESCE({coluna}" not in ddl
    assert f"{coluna} DEFAULT" not in ddl.upper()
    # A protecao e' CHECK (recusa), nao trigger nem default (correcao).
    assert f"ck_efa_{coluna}_finito CHECK (" in ddl


def test_contagens_do_resumo_sao_nao_negativas():
    ddl = _ddl_upgrade()
    for coluna in ("backlog_count", "overdue_count", "due_within_24h_count",
                   "on_time_count", "deadline_unavailable_count",
                   "over_48h_count", "slow_count", "zombie_count",
                   "stalled_count"):
        assert f"ck_err_{coluna}_nao_negativa" in ddl, coluna
        assert f"CHECK ({coluna} >= 0)" in ddl, coluna


def test_transversais_cabem_no_backlog_e_ficam_fora_da_soma():
    ddl = _ddl_upgrade()
    for coluna in ("over_48h_count", "slow_count", "zombie_count", "stalled_count"):
        assert f"ck_err_{coluna}_cabe_no_backlog" in ddl, coluna
    # A reconciliacao usa SO as quatro categorias mutuamente exclusivas.
    assert re.search(
        r"overdue_count \+ due_within_24h_count\s*\+ on_time_count"
        r" \+ deadline_unavailable_count = backlog_count",
        ddl,
    ), "reconciliacao das quatro categorias ausente"


# ---------------------------------------------------------------------------
# FK contra linha orfa
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fk, tabela", [("fk_efa_brand", FILA), ("fk_err_brand", RESUMO)])
def test_fk_de_marca_impede_linha_orfa(fk, tabela):
    """A marca sempre nasce do registry; a FK torna isso invariante do banco."""
    bloco = _create_de(tabela)
    assert fk in bloco
    assert "FOREIGN KEY (brand) REFERENCES marts.dim_loja (brand_key)" in bloco


# ---------------------------------------------------------------------------
# Indices e downgrade
# ---------------------------------------------------------------------------
def test_indices_sao_os_documentados():
    ddl = _ddl_upgrade()
    esperados = ("idx_efa_acao", "idx_efa_stalled", "idx_efa_over_48h",
                 "idx_err_tendencia")
    for indice in esperados:
        assert indice in ddl, indice
    assert ddl.count("CREATE INDEX") == len(esperados)


def test_downgrade_remove_somente_o_que_o_upgrade_criou():
    ddl = _ddl_downgrade()
    assert ddl.count("DROP TABLE") == 2
    assert ddl.count("DROP INDEX") == 4
    assert FILA in ddl and RESUMO in ddl
    # A dimensao referenciada pelas FKs nao pertence a esta migration.
    for alheia in ("dim_loja", "dim_seller_account"):
        assert alheia not in ddl, alheia


def test_downgrade_desfaz_na_ordem_inversa():
    """Indices antes das tabelas; resumo antes da fila, espelhando o upgrade."""
    comandos = _emitido("downgrade")
    tipos = ["INDEX" if "DROP INDEX" in c else "TABLE" for c in comandos]
    assert tipos == ["INDEX"] * 4 + ["TABLE"] * 2, tipos
    tabelas = [c for c in comandos if "DROP TABLE" in c]
    assert RESUMO in tabelas[0]
    assert FILA in tabelas[1]


# ---------------------------------------------------------------------------
# O pacote operacional continua sem DDL
# ---------------------------------------------------------------------------
def test_pacote_operacional_nao_esconde_ddl_nem_migration():
    """A 018 e' o UNICO lugar onde estas tabelas sao criadas.

    A inspecao vai nos literais de codigo, nao no texto cru: a docstring de
    `audit.py` cita um `ALTER TABLE` numa proposta EXPLICITAMENTE RETIRADA, e um
    grep ingenuo acusaria justamente a documentacao dessa decisao.
    """
    pacote = REPO / "pipelines" / "expedicao"
    for arquivo in sorted(pacote.glob("*.py")):
        for literal in _literais_sem_docstring(arquivo):
            alto = literal.upper()
            for verbo in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE"):
                assert verbo not in alto, f"{arquivo.name}: {verbo}"
        arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                for a in no.names:
                    assert a.name.split(".")[0] != "alembic", arquivo.name
            elif isinstance(no, ast.ImportFrom):
                assert (no.module or "").split(".")[0] != "alembic", arquivo.name
