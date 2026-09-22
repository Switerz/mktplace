"""
Gate SH-AUTO-1-R/V — provas exigidas pela revisão terminal do PR #25.

Dois findings do próprio PR motivaram metade deste arquivo:

  🔴 ALTO — `close()` numa conexão pooled NÃO libera o lock. Medido em
     22/09/2026 (PostgreSQL 16, SQLAlchemy 2.0.54, o `QueuePool` de
     `pipelines/common/db.py`): com o lock tomado e sem unlock explícito, a
     contagem em `pg_locks` continua 1 depois do `close()` e a sessão segue
     viva em `pg_stat_activity`. A versão original do módulo afirmava o
     contrário no docstring. Corrigido com conferência do retorno do unlock e
     `invalidate()` quando ele não confirma.

  🟡 MÉDIO — a consulta de diagnóstico não filtrava `objsubid`. Medido:
     `pg_try_advisory_lock(918130003)` e `pg_try_advisory_lock(0, 918130003)`
     projetam para o MESMO `(classid << 32) | objid`, e a contagem devolvia 2
     para dois locks que não se excluem. Num incidente apontaria a sessão
     errada.

O resto são as provas que o gate de revisão pediu e que o PR não tinha:
literalidade das chaves, domínio de `bigint`, `BaseException`, posição do
UPSERT e autocommit.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from pipelines.ingestion import fato_diaria_lock as L
from pipelines.tests.conexoes_lock import ConexaoDeLock
from pipelines.tests.test_fato_diaria_lock import (
    _corpo_do_run,
    _marketplace_id_do_source,
)

FONTE_LOCK = Path("pipelines/ingestion/fato_diaria_lock.py")


def _bloco_do_lock(run: ast.FunctionDef) -> ast.With:
    blocos = [
        n for n in ast.walk(run)
        if isinstance(n, ast.With)
        and any(
            isinstance(i.context_expr, ast.Call)
            and isinstance(i.context_expr.func, ast.Name)
            and i.context_expr.func.id == "fato_diaria_lock"
            for i in n.items
        )
    ]
    assert len(blocos) == 1, "deve existir exatamente um bloco de lock em run()"
    return blocos[0]


# ---------------------------------------------------------------------------
# Chaves: literais estáveis, dentro do domínio de bigint
# ---------------------------------------------------------------------------
def test_chaves_sao_literais_sem_fonte_instavel():
    """Nenhuma chave pode depender de `hash()` (randomizado por processo desde o
    PEP 456), PID, hostname, uuid ou relógio: duas esteiras calculariam valores
    diferentes e o lock não trancaria nada — sem erro em nenhuma delas."""
    fonte = FONTE_LOCK.read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    # 🔑 `Assign` E `AnnAssign`: a constante é declarada com anotação
    # (`: dict[int, int] = {...}`), e um parser que só olhasse `Assign` passaria
    # vazio sem encontrar nada — falha silenciosa, não erro.
    atribuicao = next(
        n for n in arvore.body
        if (
            (isinstance(n, ast.Assign)
             and isinstance(n.targets[0], ast.Name)
             and n.targets[0].id == "FATO_DIARIA_ADVISORY_LOCK_KEYS")
            or (isinstance(n, ast.AnnAssign)
                and isinstance(n.target, ast.Name)
                and n.target.id == "FATO_DIARIA_ADVISORY_LOCK_KEYS")
        )
    )
    assert isinstance(atribuicao.value, ast.Dict)
    for chave, valor in zip(atribuicao.value.keys, atribuicao.value.values):
        assert isinstance(chave, ast.Constant) and isinstance(chave.value, int)
        assert isinstance(valor, ast.Constant), "a chave tem de ser literal, não expressão"
        assert isinstance(valor.value, int)

    for termo in ("hash(", "getpid", "gethostname", "uuid", "time.time", "random"):
        assert termo not in fonte, f"fonte instável no módulo do lock: {termo}"


def test_chaves_dentro_do_dominio_de_bigint():
    """`pg_advisory_lock` recebe `bigint` com sinal; estourar o domínio faria o
    PostgreSQL recusar a chamada em runtime, não no teste."""
    for chave in L.FATO_DIARIA_ADVISORY_LOCK_KEYS.values():
        assert 0 < chave <= 2 ** 63 - 1


def test_modulo_nao_usa_a_forma_de_dois_argumentos():
    """Inspeciona o SQL EXECUTÁVEL, não o texto do arquivo.

    O docstring do módulo cita `pg_try_advisory_lock(int, int)` de propósito, para
    advertir contra ela. Um teste que varresse o arquivo inteiro reprovaria a
    própria advertência — então a varredura é sobre as constantes passadas a
    `text(...)`, que é o que o banco recebe.
    """
    import re

    arvore = ast.parse(FONTE_LOCK.read_text(encoding="utf-8"))
    sqls = [
        no.args[0].value
        for no in ast.walk(arvore)
        if isinstance(no, ast.Call)
        and isinstance(no.func, ast.Name)
        and no.func.id == "text"
        and no.args
        and isinstance(no.args[0], ast.Constant)
        and isinstance(no.args[0].value, str)
    ]
    assert sqls, "nenhum SQL encontrado — o teste deixou de olhar onde importa"
    chamadas = [
        m for sql in sqls
        for m in re.finditer(r"pg_(?:try_)?advisory_(?:unlock|lock)\(([^)]*)\)", sql)
    ]
    assert chamadas, "o módulo precisa chamar as funções de advisory lock"
    for m in chamadas:
        assert "," not in m.group(1), (
            f"chamada com dois argumentos ({m.group(0)}) ocupa outro espaço de "
            f"chaves e não se excluiria com o contrato"
        )
        assert m.group(1).startswith(":"), "a chave tem de vir por bind param"


# ---------------------------------------------------------------------------
# 🔴 O finding alto: cleanup invalida a conexão quando o unlock não confirma
# ---------------------------------------------------------------------------
def test_unlock_que_devolve_false_invalida_a_conexao():
    conn = ConexaoDeLock(livre=True, unlock_false=True)
    with L.fato_diaria_lock(3, connect=lambda: conn):
        pass
    assert conn.invalidada, (
        "unlock não confirmado tem de invalidar a conexão: `close()` sozinho "
        "devolve ao pool com a sessão viva e o lock sobrevive"
    )
    assert conn.fechada


def test_unlock_que_levanta_invalida_a_conexao():
    conn = ConexaoDeLock(livre=True, erro_unlock=RuntimeError("conexao morreu"))
    with L.fato_diaria_lock(3, connect=lambda: conn):
        pass
    assert conn.invalidada
    assert conn.fechada


def test_unlock_confirmado_nao_invalida_a_conexao():
    """Invalidar sempre jogaria fora uma conexão boa a cada execução."""
    conn = ConexaoDeLock(livre=True)
    with L.fato_diaria_lock(3, connect=lambda: conn):
        pass
    assert not conn.invalidada
    assert conn.fechada


def test_lock_ocupado_nao_invalida_a_conexao():
    conn = ConexaoDeLock(livre=False)
    with pytest.raises(L.FatoDiariaLockUnavailable):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            pass
    assert not conn.invalidada
    assert conn.fechada


def test_invalidate_que_falha_nao_mascara_a_excecao_original():
    class ConexaoTeimosa(ConexaoDeLock):
        def invalidate(self):
            raise RuntimeError("invalidate falhou")

    conn = ConexaoTeimosa(livre=True, unlock_false=True)

    class ErroDoCorpo(RuntimeError):
        pass

    with pytest.raises(ErroDoCorpo):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            raise ErroDoCorpo("o que importa")


def test_modulo_declara_que_close_nao_basta():
    """A afirmação original — "o fechamento da conexão garante a liberação" —
    era falsa sob pooling. O documento não pode voltar a dizê-la."""
    fonte = FONTE_LOCK.read_text(encoding="utf-8")
    assert "invalidate()" in fonte
    assert "NAO LIBERA O LOCK" in fonte or "NAO BASTA" in fonte


# ---------------------------------------------------------------------------
# BaseException: o finally roda, e o sinal propaga inalterado
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "excecao", [KeyboardInterrupt, SystemExit, BaseException],
)
def test_baseexception_libera_o_lock_e_propaga(excecao):
    """`KeyboardInterrupt` e `SystemExit` não são `Exception`. O `finally` roda
    mesmo assim — e o sinal tem de propagar, nunca virar erro de lock."""
    conn = ConexaoDeLock(livre=True)
    with pytest.raises(excecao):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            raise excecao()
    assert conn.executou("pg_advisory_unlock")
    assert conn.fechada


def test_baseexception_com_unlock_quebrado_ainda_propaga_o_sinal():
    conn = ConexaoDeLock(livre=True, erro_unlock=RuntimeError("morreu"))
    with pytest.raises(KeyboardInterrupt):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            raise KeyboardInterrupt
    assert conn.invalidada


# ---------------------------------------------------------------------------
# Posição: o UPSERT também está dentro do with
# ---------------------------------------------------------------------------
def test_o_upsert_esta_dentro_do_bloco_de_lock():
    """`_start_sync_run` dentro do bloco não basta: é o
    `session.execute(upsert_sql, row)` que publica. Mutação alvo: fechar o
    `with` logo antes do laço de escrita."""
    bloco = _bloco_do_lock(_corpo_do_run())
    upserts = [
        n for n in ast.walk(bloco)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "execute"
        and any(isinstance(a, ast.Name) and a.id == "upsert_sql" for a in n.args)
    ]
    assert upserts, "a chamada que publica precisa estar dentro do bloco de lock"


def test_a_escolha_do_sql_tambem_esta_dentro_do_bloco():
    """A seleção de `upsert_sql` por source decide o que será escrito; fora do
    lock ela pertenceria a um instante não protegido."""
    bloco = _bloco_do_lock(_corpo_do_run())
    alvos = [
        n for n in ast.walk(bloco)
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "upsert_sql"
    ]
    assert len(alvos) >= 3


def test_nenhuma_execucao_de_sql_fora_do_bloco_de_lock():
    """Contraprova estrutural: depois da resolução do source, `run()` não pode
    ter nenhuma execução de SQL fora do `with`."""
    run = _corpo_do_run()
    bloco = _bloco_do_lock(run)
    dentro = {id(n) for n in ast.walk(bloco)}
    fora = [
        n.lineno for n in ast.walk(run)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("execute", "commit")
        and id(n) not in dentro
    ]
    assert not fora, f"execução de SQL fora do bloco de lock nas linhas {fora}"


def test_a_leitura_da_fonte_esta_dentro_do_bloco():
    """`fetch_fn`/`connector_fetch` são a leitura que determina a fotografia."""
    bloco = _bloco_do_lock(_corpo_do_run())
    nomes = {
        n.func.id for n in ast.walk(bloco)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert {"fetch_fn", "connector_fetch"} <= nomes


# ---------------------------------------------------------------------------
# Os cinco caminhos, um a um
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "source,marketplace_id",
    [("shopee", 3), ("shopee-stats", 3), ("shopee-ads", 3), ("ml", 2), ("tiktok", 1)],
)
def test_os_cinco_caminhos_resolvem_para_a_chave_esperada(source, marketplace_id):
    from pipelines.ingestion import daily_performance as dp

    assert _marketplace_id_do_source(dp, source) == marketplace_id
    assert L.lock_key_for(marketplace_id) == 918130000 + marketplace_id


def test_conexao_de_producao_e_aberta_em_autocommit():
    """Requisito: autocommit ANTES da aquisição. Sem ele a conexão deixaria uma
    transação ociosa segurando snapshot do MVCC durante toda a extração —
    exatamente o que o lock de sessão existe para evitar.

    Medido em 22/09/2026: com `AUTOCOMMIT` o servidor reporta a sessão como
    `idle` depois de um SELECT; sem ele, `idle in transaction`."""
    fonte = inspect.getsource(L._default_connect)
    assert 'isolation_level="AUTOCOMMIT"' in fonte
    assert "execution_options" in fonte


def test_a_aquisicao_e_a_primeira_coisa_que_toca_a_conexao():
    """Mutação alvo: enfiar qualquer consulta antes do `pg_try_advisory_lock` —
    ela rodaria fora da exclusão."""
    conn = ConexaoDeLock(livre=True)
    with L.fato_diaria_lock(3, connect=lambda: conn):
        pass
    assert "pg_try_advisory_lock" in conn.sqls[0]


# ---------------------------------------------------------------------------
# Mensagem: sem DSN, sem host, sem credencial
# ---------------------------------------------------------------------------
def test_excecao_nao_expoe_topologia_nem_credencial():
    conn = ConexaoDeLock(livre=False)
    with pytest.raises(L.FatoDiariaLockUnavailable) as exc:
        with L.fato_diaria_lock(3, connect=lambda: conn):
            pass
    texto = str(exc.value)
    for proibido in ("postgresql://", "@", "senha", "password", "neon", "amazonaws",
                     "5432", "host="):
        assert proibido not in texto.lower(), f"a mensagem vaza {proibido!r}"
    assert "918130003" in texto, "mas a chave disputada precisa aparecer"


def test_excecao_e_tipada_e_nao_se_confunde_com_erro_de_dados():
    """Um chamador Python tem de distinguir "outra esteira publicava" de "os
    dados estavam errados" — só o primeiro é seguro de repetir depois."""
    assert issubclass(L.FatoDiariaLockUnavailable, RuntimeError)
    assert not issubclass(L.FatoDiariaLockUnavailable, ValueError)
    from pipelines.ingestion.daily_performance import ClosedDayViolation

    assert not issubclass(L.FatoDiariaLockUnavailable, ClosedDayViolation)
    conn = ConexaoDeLock(livre=False)
    with pytest.raises(L.FatoDiariaLockUnavailable) as exc:
        with L.fato_diaria_lock(3, connect=lambda: conn):
            pass
    assert exc.value.marketplace_id == 3
    assert exc.value.chave == 918130003
