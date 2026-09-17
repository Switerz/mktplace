"""Barreiras estruturais do pacote de expedicao.

Estes testes nao exercitam logica de negocio: eles impedem que uma edicao futura
reintroduza um defeito ja diagnosticado (PII no SELECT, relogio dentro da
transformacao, DELETE global, NO_OP por watermark, historico integral por
pedido, marca dentro da identidade, chave de lock colidindo com outra frente).
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from pipelines.expedicao import audit, cli, contract, publisher, shopee_extract, transform
from pipelines.expedicao.contract import (
    ADVISORY_LOCK_KEYS,
    ALERT_EVENT_TABLE_FUTURE,
    FILA_TABLE,
    KNOWN_FOREIGN_LOCK_KEYS,
    MARKETPLACE_ID,
    MIN_BASELINE_SAMPLE,
    OPERATIONAL_AGE_LIMIT,
    PII_FORBIDDEN_TOKENS,
    REGISTRY_SQL,
    RUN_TABLE,
    SHOPEE_ALLOWED_SOURCE_COLUMNS,
    SLOW_BASELINE_FACTOR,
    Channel,
    DeadlineStatus,
    FreshnessStatus,
    OperationalAgeStatus,
    RunStatus,
    SourceHealth,
    TimestampQuality,
)

PACOTE = Path(transform.__file__).parent


# ---------------------------------------------------------------------------
# Helpers de inspecao estrutural
# ---------------------------------------------------------------------------
# Um `grep` no texto do arquivo acusaria a propria docstring que EXPLICA o
# defeito — e obrigaria a documentar menos para o teste passar. A analise vai na
# arvore sintatica: importa o que o codigo faz, nao o que ele descreve.
def _arvore(modulo) -> ast.Module:
    return ast.parse(Path(modulo.__file__).read_text(encoding="utf-8"))


def _docstrings(arvore: ast.Module) -> set[str]:
    encontradas = set()
    for no in ast.walk(arvore):
        if isinstance(
            no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            texto = ast.get_docstring(no, clean=False)
            if texto is not None:
                encontradas.add(texto)
    return encontradas


def literais_de_texto(modulo) -> list[str]:
    """Strings do codigo, sem docstrings. Comentarios nem chegam ao AST.

    f-string vira `JoinedStr` e seus pedacos literais aparecem como `Constant`
    separados: `f"DELETE FROM {T} WHERE channel = %s"` se quebraria em
    `'DELETE FROM '` e `' WHERE channel = %s'`. Por isso a f-string e devolvida
    inteira, pelo `unparse`, e seus fragmentos sao suprimidos.
    """
    arvore = _arvore(modulo)
    docs = _docstrings(arvore)

    fragmentos: set[int] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.JoinedStr):
            for filho in ast.walk(no):
                if filho is not no:
                    fragmentos.add(id(filho))

    saida: list[str] = []
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


def chamadas_de_relogio(modulo) -> list[str]:
    """Chamadas reais a relogio: `datetime.now`, `date.today`, `utcnow`."""
    achadas = []
    for no in ast.walk(_arvore(modulo)):
        if isinstance(no, ast.Call):
            nome = ast.unparse(no.func)
            if nome.endswith(("datetime.now", "date.today", "utcnow")):
                achadas.append(nome)
    return achadas


def nomes_definidos(modulo) -> set[str]:
    return {
        no.name
        for no in ast.walk(_arvore(modulo))
        if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


MODULOS = [cli, publisher, shopee_extract, transform]


# ---------------------------------------------------------------------------
# PII
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sql",
    [
        shopee_extract.SHOPEE_BACKLOG_SQL,
        shopee_extract.SHOPEE_WATERMARK_SQL,
        shopee_extract.SHOPEE_BASELINE_SQL,
        REGISTRY_SQL,
    ],
)
def test_sql_nao_contem_termo_de_pii(sql):
    shopee_extract.assert_no_pii_in_sql(sql, PII_FORBIDDEN_TOKENS)


def test_select_da_shopee_e_lista_fechada():
    # Igualdade, nao continencia: coluna nova so passa se for declarada.
    assert set(shopee_extract.SHOPEE_SELECT_COLUMNS) == set(
        SHOPEE_ALLOWED_SOURCE_COLUMNS
    )


def test_select_nao_usa_asterisco():
    # `SELECT *` traria buyer_cpf_id, recipient_address e o resto do comprador.
    assert "*" not in shopee_extract.SHOPEE_BACKLOG_SQL


def _codigo_sem_comentario(caminho) -> str:
    """Fonte do modulo SEM comentarios e SEM docstrings, em minusculas.

    A versao anterior varria o texto cru e por isso reprovava o comentario que
    EXPLICA a defesa — dizer "nunca `SELECT *`, que traria receiver_address"
    virava violacao. Pior: para conviver com isso, `shopee_extract.py` e
    `audit.py` ficaram isentos por arquivo INTEIRO, entao uma coluna de
    comprador de verdade no codigo deles passaria batido.

    Varrendo so' o codigo, a explicacao e' livre e a isencao por arquivo deixa
    de ser necessaria: sobra apenas `contract.py`, que declara a denylist.
    """
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    for no in ast.walk(arvore):
        # Docstring e o primeiro Expr/Constant str do corpo; remove-lo evita
        # que a prosa do modulo entre na varredura.
        corpo = getattr(no, "body", None)
        if isinstance(corpo, list) and corpo:
            primeiro = corpo[0]
            if (
                isinstance(primeiro, ast.Expr)
                and isinstance(primeiro.value, ast.Constant)
                and isinstance(primeiro.value.value, str)
            ):
                corpo.pop(0)
    # `ast.unparse` reconstroi so' os nos: comentarios nunca sao nos.
    return ast.unparse(arvore).lower()


def test_nenhum_modulo_do_pacote_menciona_pii():
    for arquivo in sorted(PACOTE.glob("*.py")):
        # O proprio contrato declara a denylist; ele e a excecao legitima.
        if arquivo.name == "contract.py":
            continue
        codigo = _codigo_sem_comentario(arquivo)
        achados = sorted(t for t in PII_FORBIDDEN_TOKENS if t in codigo)
        assert not achados, f"{arquivo.name} menciona PII no CODIGO: {achados}"


def test_colunas_publicadas_nao_tem_campo_de_comprador():
    for coluna in publisher.FILA_COLUMNS + publisher.SUMMARY_COLUMNS:
        for token in PII_FORBIDDEN_TOKENS:
            assert token not in coluna.lower(), f"coluna {coluna} parece PII"


def test_chave_nao_carrega_pii():
    for coluna in publisher.FILA_PRIMARY_KEY + publisher.SUMMARY_PRIMARY_KEY:
        for token in PII_FORBIDDEN_TOKENS:
            assert token not in coluna.lower()


# ---------------------------------------------------------------------------
# Chave da fila — EXP-1A-R2
# ---------------------------------------------------------------------------
def test_pk_da_fila_espelha_a_chave_da_origem():
    """`pk_shopee_orders` e UNIQUE em `(shop_account, order_sn)`."""
    assert publisher.FILA_PRIMARY_KEY == (
        "channel", "shop_account", "marketplace_order_id",
    )


def test_marca_nao_faz_parte_da_identidade():
    """Corrigir a marca de uma conta nao pode criar um pedido novo."""
    assert "brand" not in publisher.FILA_PRIMARY_KEY
    # Mas continua sendo publicada como atributo.
    assert "brand" in publisher.FILA_COLUMNS


def test_pk_do_resumo_e_por_conta_e_hora():
    assert publisher.SUMMARY_PRIMARY_KEY == ("channel", "shop_account", "snapshot_hour")
    assert "brand" not in publisher.SUMMARY_PRIMARY_KEY


# ---------------------------------------------------------------------------
# Determinismo temporal
# ---------------------------------------------------------------------------
def test_transform_nao_le_relogio():
    """`datetime.now()` / `date.today()` tornariam o resultado irreproduzivel e
    adotariam o fuso do processo (BRT no notebook, UTC no worker)."""
    assert chamadas_de_relogio(transform) == []


def test_extract_nao_le_relogio():
    assert chamadas_de_relogio(shopee_extract) == []


def test_baseline_termina_no_instante_injetado():
    sql = shopee_extract.SHOPEE_BASELINE_SQL.lower()
    assert "%(effective_at)s" in sql
    assert "now()" not in sql


def test_relogio_so_no_cli():
    assert len(chamadas_de_relogio(cli)) == 1


@pytest.mark.parametrize(
    "func",
    [
        transform.classify_deadline,
        transform.classify_age,
        transform.classify_freshness,
        transform.build_fila_shopee,
        transform.build_account_summaries,
        transform.snapshot_hour,
    ],
)
def test_funcoes_recebem_effective_at(func):
    assert "effective_at" in inspect.signature(func).parameters


# ---------------------------------------------------------------------------
# NO_OP por watermark nao pode voltar (EXP-1A-R)
# ---------------------------------------------------------------------------
def test_nao_existe_no_op_por_watermark():
    definidos = nomes_definidos(cli)
    assert "decide_run_mode" not in definidos
    assert "RunMode" not in definidos
    assert "source_advanced" in definidos


def test_nenhum_modulo_declara_no_op():
    for modulo in MODULOS:
        for literal in literais_de_texto(modulo):
            assert "no_op" not in literal.lower(), (
                f"{Path(modulo.__file__).name} carrega literal de NO_OP: {literal!r}"
            )


# ---------------------------------------------------------------------------
# Historico integral removido (EXP-1A-R2)
# ---------------------------------------------------------------------------
def test_historico_integral_por_pedido_nao_existe():
    """~1.000 pedidos/hora dariam ~8,8 milhoes de linhas/ano copiando o mesmo
    estado. A auditoria individual futura registra TRANSICOES, nao copias."""
    assert not hasattr(transform, "build_historico")
    assert not hasattr(publisher, "HISTORICO_COLUMNS")
    assert not hasattr(publisher, "HISTORICO_CONFLICT_SQL")


def test_nenhuma_referencia_ativa_a_fila_historico():
    for arquivo in sorted(PACOTE.glob("*.py")):
        texto = arquivo.read_text(encoding="utf-8")
        assert "expedicao_fila_historico" not in texto, arquivo.name


def test_tabela_de_evento_de_alerta_fica_como_evolucao_futura():
    assert ALERT_EVENT_TABLE_FUTURE == "marts.expedicao_alert_event"
    # Declarada no contrato, mas NAO usada por publisher nem transform.
    for modulo in (publisher, transform):
        assert "expedicao_alert_event" not in Path(
            modulo.__file__
        ).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Schema — o pacote nunca cria nem altera estrutura
# ---------------------------------------------------------------------------
#: Verbos de DDL que o pacote nunca pode executar. O refresh publica DADO em
#: tabelas que a migration criou; ele nunca cria as proprias tabelas.
DDL_PROIBIDO = (
    "create table",
    "alter table",
    "drop table",
    "create schema",
    "drop schema",
    "create index",
    "drop index",
    "truncate",
)

#: Operacoes de DML que o pacote esta contratado a executar. Qualquer verbo
#: fora desta lista num literal SQL e ampliacao de escopo silenciosa.
DML_CONTRATADO = (
    "select",
    "insert into",
    "delete from",
    "on conflict",
    "do update set",
)

#: Nomes de funcao que so fazem sentido para quem versiona schema.
ALEMBIC_PROIBIDO = {"upgrade", "downgrade", "stamp", "revision", "op"}

#: Modulos de PRODUCAO do pacote (os testes ficam de fora).
MODULOS_PRODUCAO = [contract, shopee_extract, transform, publisher, audit, cli]


def _sql_literais(modulo) -> list[str]:
    """Literais que parecem SQL executavel — docstrings e comentarios fora.

    `literais_de_texto` ja exclui docstrings pelo AST, e comentarios nem chegam
    a virar no de AST. Um `grep` ingenuo acusaria a propria docstring que
    EXPLICA por que o DDL nao existe aqui, e obrigaria a documentar menos para
    o teste passar.
    """
    candidatos = []
    for literal in literais_de_texto(modulo):
        baixo = literal.lower()
        if any(v in baixo for v in (*DML_CONTRATADO, *DDL_PROIBIDO)):
            candidatos.append(literal)
    return candidatos


@pytest.mark.parametrize("modulo", MODULOS_PRODUCAO, ids=lambda m: Path(m.__file__).name)
def test_pacote_expedicao_nao_executa_ddl_ou_alembic(modulo):
    """Invariante DURAVEL: o pacote publica dado, nunca versiona schema.

    Substitui um teste anterior que fixava o head do Alembic em `015`. Aquela
    premissa era transitoria — quebrou legitimamente quando a frente Full
    integrou a `016` — e afirmava algo sobre o REPOSITORIO INTEIRO, que outras
    frentes tem todo o direito de mudar. Prender a ausencia de `017`/`018` teria
    o mesmo defeito, so que com validade mais longa.

    O que e realmente invariante e o comportamento DESTE pacote: ele nao importa
    Alembic, nao chama `upgrade`/`downgrade`/`stamp`, nao executa DDL e nao cria
    schema sozinho. Isso continua verdadeiro em qualquer head futuro.

    A ausencia de migration NESTE PR e verificada na entrega, por
    `git diff --name-only origin/main...HEAD`, nao por teste permanente.
    """
    arvore = _arvore(modulo)
    nome = Path(modulo.__file__).name

    # 1. Nenhum import de Alembic.
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            for alias in no.names:
                assert not alias.name.split(".")[0] == "alembic", (
                    f"{nome} importa alembic"
                )
        elif isinstance(no, ast.ImportFrom):
            assert not (no.module or "").split(".")[0] == "alembic", (
                f"{nome} importa de alembic"
            )

    # 2. Nenhuma chamada a verbo de versionamento de schema.
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call):
            alvo = ast.unparse(no.func)
            ultimo = alvo.split(".")[-1]
            assert ultimo not in ALEMBIC_PROIBIDO, (
                f"{nome} chama {alvo!r}, que versiona schema"
            )
            assert not alvo.startswith("command."), (
                f"{nome} chama {alvo!r} (alembic.command)"
            )

    # 3. Nenhum DDL nas constantes SQL executaveis.
    for literal in _sql_literais(modulo):
        baixo = literal.lower()
        achados = sorted(v for v in DDL_PROIBIDO if v in baixo)
        assert not achados, f"{nome} carrega DDL executavel {achados}: {literal[:80]!r}"


def test_publisher_so_executa_o_dml_contratado():
    """O publisher toca exatamente o DML do contrato: um DELETE por canal, os
    INSERTs e o UPSERT do resumo. Nada alem disso."""
    for literal in _sql_literais(publisher):
        baixo = literal.lower()
        assert any(v in baixo for v in DML_CONTRATADO), (
            f"SQL fora do DML contratado: {literal[:80]!r}"
        )


def test_cli_nao_cria_tabela_automaticamente():
    """`--apply` para quando as tabelas nao existem; nunca as cria.

    Auto-criacao transformaria uma migration revisada num efeito colateral de
    execucao — e o schema de producao passaria a nascer em runtime.
    """
    for literal in _sql_literais(cli):
        baixo = literal.lower()
        assert not any(v in baixo for v in DDL_PROIBIDO), (
            f"CLI carrega DDL: {literal[:80]!r}"
        )
    nomes = nomes_definidos(cli)
    for suspeito in ("create_tables", "ensure_schema", "criar_tabelas", "migrate"):
        assert suspeito not in nomes, f"CLI define {suspeito}"


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------
def test_chaves_de_lock_nao_colidem_com_outras_frentes():
    for canal, chave in ADVISORY_LOCK_KEYS.items():
        assert chave not in KNOWN_FOREIGN_LOCK_KEYS, f"{canal} colide: {chave}"


def test_cada_canal_tem_chave_distinta():
    chaves = list(ADVISORY_LOCK_KEYS.values())
    assert len(chaves) == len(set(chaves))


def test_lock_e_de_sessao_e_nao_transacional():
    sqls = literais_de_texto(publisher)
    assert any("pg_try_advisory_lock" in s for s in sqls)
    assert any("pg_advisory_unlock" in s for s in sqls)
    assert not any("pg_advisory_xact_lock" in s for s in sqls)


# ---------------------------------------------------------------------------
# Publicacao por canal
# ---------------------------------------------------------------------------
def test_delete_e_sempre_restrito_ao_canal():
    assert "WHERE channel = %s" in publisher.DELETE_CANAL_SQL
    deletes = [s for s in literais_de_texto(publisher) if "DELETE FROM" in s.upper()]
    assert len(deletes) == 1, f"mais de um DELETE no publisher: {deletes}"
    assert "WHERE channel" in deletes[0]


def test_resumo_so_sobrescreve_observacao_mais_nova():
    sql = publisher.SUMMARY_CONFLICT_SQL
    assert "ON CONFLICT (channel, shop_account, snapshot_hour)" in sql
    assert "WHERE EXCLUDED.observed_at >" in sql
    # `DO NOTHING` congelaria a primeira leitura da hora.
    assert "DO UPDATE" in sql
    assert "DO NOTHING" not in sql


def test_resumo_tem_todos_os_campos_do_contrato():
    obrigatorios = {
        "refresh_batch_id", "channel", "shop_account", "brand", "snapshot_hour",
        "observed_at", "source_watermark_at", "source_advanced", "backlog_count",
        "overdue_count", "due_within_24h_count", "on_time_count",
        "deadline_unavailable_count", "over_48h_count", "slow_count",
        "zombie_count", "stalled_count", "run_status", "ingested_at",
    }
    assert set(publisher.SUMMARY_COLUMNS) == obrigatorios


def test_fila_carrega_o_lote():
    assert "refresh_batch_id" in publisher.FILA_COLUMNS
    assert "refresh_batch_id" in publisher.SUMMARY_COLUMNS


# ---------------------------------------------------------------------------
# Dominios
# ---------------------------------------------------------------------------
def test_dominios_completos():
    assert {s.value for s in DeadlineStatus} == {
        "overdue", "due_within_24h", "on_time", "unavailable",
    }
    assert {s.value for s in OperationalAgeStatus} == {
        "within_48h", "over_48h", "unknown",
    }
    assert {s.value for s in FreshnessStatus} == {
        "fresh", "stale", "critical", "unknown",
    }
    assert {s.value for s in RunStatus} == {"success", "failed"}
    assert TimestampQuality.VERIFIED.value == "verified"


def test_saude_da_fonte_cobre_os_sete_estados():
    # `source_stale` entrou no EXP-3B1-R/V, por causa do Mercado Livre: a conta
    # existe e tem carimbo, mas o carimbo caiu fora da coorte de confiabilidade.
    # No Shopee isso seria apenas frescor ruim; no ML o filtro de coorte REMOVE
    # as linhas, entao publicar apagaria a fila anterior por silencio da fonte.
    assert {s.value for s in SourceHealth} == {
        "healthy",
        "account_missing",
        "unexpected_account",
        "registry_ambiguous",
        "watermark_missing",
        "source_unavailable",
        "source_stale",
    }


def test_so_healthy_autoriza_publicacao():
    assert SourceHealth.HEALTHY.can_publish is True
    for doente in SourceHealth:
        if doente is not SourceHealth.HEALTHY:
            assert doente.can_publish is False


def test_anomalias_sao_flags_separadas():
    """Um `stalled_reason` singular obrigaria a escolher uma causa."""
    assert "is_slow_vs_baseline" in publisher.FILA_COLUMNS
    assert "is_source_zombie" in publisher.FILA_COLUMNS
    assert "is_stalled" in publisher.FILA_COLUMNS
    assert "stalled_reason" not in publisher.FILA_COLUMNS
    # No resumo as tres contagens sao independentes.
    for c in ("slow_count", "zombie_count", "stalled_count"):
        assert c in publisher.SUMMARY_COLUMNS


def test_limite_de_48h_nao_depende_do_p50():
    assert OPERATIONAL_AGE_LIMIT.total_seconds() == 48 * 3600
    assert SLOW_BASELINE_FACTOR == 2.0
    assert MIN_BASELINE_SAMPLE >= 1
    texto = Path(transform.__file__).read_text(encoding="utf-8")
    corpo = texto.split("def classify_age")[1].split("\ndef ")[0]
    assert "BASELINE" not in corpo.upper()


def test_marketplace_id_segue_a_dimensao_do_neon():
    """Conferido em `marts.dim_marketplace` por leitura read-only."""
    assert MARKETPLACE_ID[Channel.TIKTOKSHOP] == 1
    assert MARKETPLACE_ID[Channel.MERCADOLIVRE] == 2
    assert MARKETPLACE_ID[Channel.SHOPEE] == 3


def test_nomes_de_tabela_no_schema_marts():
    for tabela in (FILA_TABLE, RUN_TABLE, ALERT_EVENT_TABLE_FUTURE):
        assert tabela.startswith("marts.")


def test_backlog_nao_tem_corte_temporal():
    sql = shopee_extract.SHOPEE_BACKLOG_SQL.lower()
    assert "create_time >" not in sql
    assert "interval" not in sql


def test_baseline_exclui_cancelados():
    """46 de 23.755 (0,19%) tinham pickup preenchido e status cancelado."""
    assert "CANCELLED" in shopee_extract.BASELINE_EXCLUDED_STATUSES
    assert "excluded_statuses" in shopee_extract.SHOPEE_BASELINE_SQL
    # TO_RETURN NAO e excluido: o despacho aconteceu e a devolucao e posterior.
    assert "TO_RETURN" not in shopee_extract.BASELINE_EXCLUDED_STATUSES


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_registry_le_as_duas_dimensoes_e_respeita_ativo():
    sql = REGISTRY_SQL.lower()
    assert "marts.dim_seller_account" in sql
    assert "marts.dim_loja" in sql
    assert "sa.ativo and l.ativo" in sql
    assert "marketplace_id = %(marketplace_id)s" in sql


def test_shop_ids_nao_estao_no_codigo_de_producao():
    """As contas vivem no registry. Hardcode faria o codigo divergir do Neon."""
    for arquivo in sorted(PACOTE.glob("*.py")):
        texto = arquivo.read_text(encoding="utf-8")
        for shop_id in ("1609671923", "1579330222", "1593864538", "1457734799"):
            assert shop_id not in texto, f"{arquivo.name} hardcoda {shop_id}"
