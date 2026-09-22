"""
Gate SH-AUTO-1 — exclusão mútua dos escritores de
`marts.fact_marketplace_daily_performance`.

Nenhum teste deste arquivo abre conexão real: o contextmanager recebe a
conexão por `connect=`. A prova com PostgreSQL de verdade — duas conexões
disputando a mesma chave — está em `test_fato_diaria_lock_concorrencia.py`.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from pipelines.ingestion import fato_diaria_lock as L
from pipelines.tests.conexoes_lock import ConexaoDeLock


# ---------------------------------------------------------------------------
# Contrato: os valores que o runner do Airflow vai copiar
# ---------------------------------------------------------------------------
def test_chaves_congeladas():
    """Trava os literais. Mudar uma chave aqui sem mudar no consumidor do
    Airflow produz dois locks distintos e ZERO exclusão — falha que nenhum
    teste de um repositório só pega."""
    assert L.FATO_DIARIA_ADVISORY_LOCK_KEYS == {
        1: 918130001,
        2: 918130002,
        3: 918130003,
    }


def test_chave_deriva_da_base_declarada():
    for marketplace_id, chave in L.FATO_DIARIA_ADVISORY_LOCK_KEYS.items():
        assert chave == L.FATO_DIARIA_LOCK_BASE + marketplace_id


def test_chaves_nao_colidem_com_as_ja_usadas_no_repositorio():
    """A colisão silenciosa é o pior caso: dois propósitos diferentes
    serializando um ao outro, cada um atribuindo a lentidão ao próprio código."""
    from pipelines.expedicao import contract as expedicao

    alheias = set(expedicao.KNOWN_FOREIGN_LOCK_KEYS) | set(
        expedicao.ADVISORY_LOCK_KEYS.values()
    )
    nossas = set(L.FATO_DIARIA_ADVISORY_LOCK_KEYS.values())
    assert nossas.isdisjoint(alheias)


def test_os_tres_writers_shopee_compartilham_a_chave():
    """O ponto inteiro do gate: orders, stats e ads disputam a MESMA linha, logo
    precisam da MESMA chave. Chave por source seria cerimônia sem exclusão."""
    from pipelines.ingestion import daily_performance as dp

    ids = {}
    for source in ("shopee", "shopee-stats", "shopee-ads"):
        ids[source] = _marketplace_id_do_source(dp, source)
    assert set(ids.values()) == {3}
    chaves = {L.lock_key_for(i) for i in ids.values()}
    assert chaves == {918130003}


def test_exit_code_e_ex_tempfail():
    assert L.EXIT_CODE_LOCK_UNAVAILABLE == 75


def test_marketplaces_distintos_nao_se_excluem():
    """Deliberado: a chave da tabela é (date, loja_id, marketplace_id) e uma
    loja pertence a um marketplace só, então ML e Shopee nunca disputam linha."""
    chaves = list(L.FATO_DIARIA_ADVISORY_LOCK_KEYS.values())
    assert len(chaves) == len(set(chaves))


# ---------------------------------------------------------------------------
# lock_key_for: fail-closed
# ---------------------------------------------------------------------------
def test_lock_key_for_marketplace_desconhecido_levanta():
    with pytest.raises(ValueError, match="nao tem chave de advisory lock"):
        L.lock_key_for(99)


def test_lock_key_for_rejeita_bool():
    """`True` é `int` em Python e viraria `lock_key_for(1)` — o lock do TikTok
    concedido a quem passou um booleano por engano."""
    with pytest.raises(ValueError, match="precisa ser int"):
        L.lock_key_for(True)


def test_lock_key_for_rejeita_nao_inteiro():
    with pytest.raises(ValueError, match="precisa ser int"):
        L.lock_key_for("3")


# ---------------------------------------------------------------------------
# Lock livre
# ---------------------------------------------------------------------------
def test_lock_livre_cede_controle_e_usa_a_chave_certa():
    conn = ConexaoDeLock(livre=True)
    executou = False
    with L.fato_diaria_lock(3, connect=lambda: conn) as chave:
        executou = True
        assert chave == 918130003
    assert executou
    assert conn.chaves_usadas == [918130003, 918130003]


def test_lock_livre_e_liberado_no_fim():
    conn = ConexaoDeLock(livre=True)
    with L.fato_diaria_lock(3, connect=lambda: conn):
        assert not conn.executou("pg_advisory_unlock")
    assert conn.executou("pg_advisory_unlock")
    assert conn.fechada


def test_usa_try_e_nunca_a_variante_que_espera():
    """`pg_advisory_lock` (bloqueante) transformaria disputa em espera silenciosa
    até o timeout do step — diagnóstico errado para o que é concorrência."""
    conn = ConexaoDeLock(livre=True)
    with L.fato_diaria_lock(3, connect=lambda: conn):
        pass
    aquisicoes = [s for s in conn.sqls if "advisory_lock" in s and "unlock" not in s]
    assert aquisicoes
    for sql in aquisicoes:
        assert "pg_try_advisory_lock" in sql


# ---------------------------------------------------------------------------
# Lock ocupado
# ---------------------------------------------------------------------------
def test_lock_ocupado_levanta_antes_de_ceder_controle():
    conn = ConexaoDeLock(livre=False)
    entrou = False
    with pytest.raises(L.FatoDiariaLockUnavailable) as exc:
        with L.fato_diaria_lock(3, connect=lambda: conn):
            entrou = True
    assert not entrou, "o corpo não pode executar com o lock ocupado"
    assert exc.value.chave == 918130003
    assert exc.value.marketplace_id == 3


def test_lock_ocupado_nao_tenta_liberar_o_que_nao_adquiriu():
    """Liberar lock alheio é pior que não liberar: `pg_advisory_unlock` do
    processo B não derruba o lock de A, mas um unlock cego mascararia o erro
    de contagem no dia em que derrubasse."""
    conn = ConexaoDeLock(livre=False)
    with pytest.raises(L.FatoDiariaLockUnavailable):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            pass
    assert not conn.executou("pg_advisory_unlock")


def test_lock_ocupado_fecha_a_conexao():
    conn = ConexaoDeLock(livre=False)
    with pytest.raises(L.FatoDiariaLockUnavailable):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            pass
    assert conn.fechada


def test_lock_ocupado_nao_faz_retry():
    conn = ConexaoDeLock(livre=False)
    with pytest.raises(L.FatoDiariaLockUnavailable):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            pass
    tentativas = [s for s in conn.sqls if "pg_try_advisory_lock" in s]
    assert len(tentativas) == 1


def test_mensagem_diz_que_nada_foi_escrito():
    conn = ConexaoDeLock(livre=False)
    with pytest.raises(L.FatoDiariaLockUnavailable) as exc:
        with L.fato_diaria_lock(3, connect=lambda: conn):
            pass
    texto = str(exc.value)
    assert "nada foi escrito" in texto
    assert "nenhuma linha de auditoria" in texto


# ---------------------------------------------------------------------------
# Cleanup sob exceção — a lição do publisher de expedição
# ---------------------------------------------------------------------------
def test_excecao_no_corpo_libera_o_lock_e_preserva_a_excecao():
    conn = ConexaoDeLock(livre=True)

    class ErroDoCorpo(RuntimeError):
        pass

    with pytest.raises(ErroDoCorpo):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            raise ErroDoCorpo("upsert quebrou")
    assert conn.executou("pg_advisory_unlock")
    assert conn.fechada


def test_unlock_que_falha_nao_substitui_a_excecao_original():
    """Commit indeterminado que saísse daqui como "erro ao liberar lock" seria
    lido no runbook como "nada foi publicado" — afirmação falsa e cara."""
    conn = ConexaoDeLock(livre=True, erro_unlock=RuntimeError("conexao morreu"))

    class CommitIndeterminado(RuntimeError):
        pass

    with pytest.raises(CommitIndeterminado):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            raise CommitIndeterminado("commit sem resposta")
    assert conn.fechada, "a conexão fecha mesmo com o unlock falhando — é ela que garante a liberação"


def test_unlock_que_falha_no_caminho_feliz_nao_quebra_o_run():
    conn = ConexaoDeLock(livre=True, erro_unlock=RuntimeError("conexao morreu"))
    with L.fato_diaria_lock(3, connect=lambda: conn):
        pass
    assert conn.fechada


def test_close_que_falha_nao_mascara_a_excecao_original():
    class ConexaoQueNaoFecha(ConexaoDeLock):
        def close(self):
            raise RuntimeError("close falhou")

    conn = ConexaoQueNaoFecha(livre=True)

    class ErroDoCorpo(RuntimeError):
        pass

    with pytest.raises(ErroDoCorpo):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            raise ErroDoCorpo("o que importa")


# ---------------------------------------------------------------------------
# Leitura da resposta do banco
# ---------------------------------------------------------------------------
def test_aceita_tupla_que_e_o_que_o_runtime_devolve():
    conn = ConexaoDeLock(livre=True, modo="tupla")
    with L.fato_diaria_lock(3, connect=lambda: conn):
        pass


def test_aceita_mapping():
    conn = ConexaoDeLock(livre=True, modo="mapping")
    with L.fato_diaria_lock(3, connect=lambda: conn):
        pass


def test_mapping_com_false_tambem_bloqueia():
    conn = ConexaoDeLock(livre=False, modo="mapping")
    with pytest.raises(L.FatoDiariaLockUnavailable):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            pass


def test_sem_linha_levanta_em_vez_de_assumir_sucesso():
    """Silêncio do banco não é "sim". Assumir aquisição publicaria sem exclusão."""
    conn = ConexaoDeLock(sem_linha=True)
    with pytest.raises(RuntimeError, match="nao devolveu linha"):
        with L.fato_diaria_lock(3, connect=lambda: conn):
            pass


# ---------------------------------------------------------------------------
# Mutações — provas de que cada peça é necessária
# ---------------------------------------------------------------------------
def test_mutacao_chave_trocada_quebra_a_exclusao_entre_os_writers_shopee():
    """Contraprova: se stats usasse uma chave diferente de orders, os dois
    entrariam ao mesmo tempo. O teste falha se alguém 'melhorar' a chave para
    ser por source."""
    original = dict(L.FATO_DIARIA_ADVISORY_LOCK_KEYS)
    try:
        L.FATO_DIARIA_ADVISORY_LOCK_KEYS[3] = 999999999
        conn = ConexaoDeLock(livre=True)
        with L.fato_diaria_lock(3, connect=lambda: conn):
            pass
        assert conn.chaves_usadas == [999999999, 999999999]
        assert 918130003 not in conn.chaves_usadas
    finally:
        L.FATO_DIARIA_ADVISORY_LOCK_KEYS.clear()
        L.FATO_DIARIA_ADVISORY_LOCK_KEYS.update(original)
    assert L.lock_key_for(3) == 918130003


def test_mutacao_chave_removida_falha_fechado():
    original = dict(L.FATO_DIARIA_ADVISORY_LOCK_KEYS)
    try:
        del L.FATO_DIARIA_ADVISORY_LOCK_KEYS[3]
        with pytest.raises(ValueError):
            L.lock_key_for(3)
    finally:
        L.FATO_DIARIA_ADVISORY_LOCK_KEYS.clear()
        L.FATO_DIARIA_ADVISORY_LOCK_KEYS.update(original)


def test_contrato_documenta_a_forma_de_um_argumento():
    """A forma de dois argumentos ocupa outro espaço de chaves no PostgreSQL:
    um runner que usasse `pg_try_advisory_lock(918130, 3)` não se excluiria com
    este lock e passaria em qualquer teste que não ponha os dois frente a frente."""
    doc = L.__doc__ or ""
    assert "918130003" in doc
    assert "UM argumento" in doc
    fonte = inspect.getsource(L.fato_diaria_lock)
    assert "pg_try_advisory_lock(:chave)" in fonte


# ---------------------------------------------------------------------------
# Posição do lock dentro de run() — por AST, não por execução
# ---------------------------------------------------------------------------
def _corpo_do_run() -> ast.FunctionDef:
    fonte = Path("pipelines/ingestion/daily_performance.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    for no in arvore.body:
        if isinstance(no, ast.FunctionDef) and no.name == "run":
            return no
    raise AssertionError("run() não encontrada")


def _linha_da_chamada(no: ast.AST, nome: str) -> int | None:
    for filho in ast.walk(no):
        if isinstance(filho, ast.Call):
            alvo = filho.func
            if isinstance(alvo, ast.Name) and alvo.id == nome:
                return filho.lineno
            if isinstance(alvo, ast.Attribute) and alvo.attr == nome:
                return filho.lineno
    return None


def test_lock_vem_antes_da_auditoria_e_da_leitura():
    """A posição é metade do contrato. Lock depois de `_start_sync_run` deixaria
    uma linha `running` órfã por tentativa recusada; depois do fetch, a
    fotografia já teria sido lida em concorrência."""
    run = _corpo_do_run()
    lock = _linha_da_chamada(run, "fato_diaria_lock")
    audit = _linha_da_chamada(run, "_start_sync_run")
    assert lock is not None and audit is not None
    assert lock < audit, "o lock precisa vir antes de abrir a auditoria"


def test_auditoria_e_upsert_estao_dentro_do_with_do_lock():
    """Mutação alvo: mover o `with` para depois da leitura, ou fechá-lo antes do
    UPSERT. Os dois deixariam o teste acima passar e a exclusão inexistente."""
    run = _corpo_do_run()
    withs = [
        n for n in ast.walk(run)
        if isinstance(n, ast.With)
        and any(
            isinstance(i.context_expr, ast.Call)
            and isinstance(i.context_expr.func, ast.Name)
            and i.context_expr.func.id == "fato_diaria_lock"
            for i in n.items
        )
    ]
    assert len(withs) == 1, "deve existir exatamente um bloco de lock em run()"
    bloco = withs[0]
    fim = max(
        getattr(n, "lineno", bloco.lineno) for n in ast.walk(bloco)
    )
    for nome in ("_start_sync_run", "_finish_sync_run", "_log_quality_checks"):
        linha = _linha_da_chamada(bloco, nome)
        assert linha is not None, f"{nome} precisa estar dentro do bloco de lock"
        assert bloco.lineno < linha <= fim


def _marketplace_id_do_source(dp_module, source: str) -> int:
    """Lê o `marketplace_id` que run() atribui para um source, por AST."""
    fonte = Path("pipelines/ingestion/daily_performance.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    run = next(
        n for n in arvore.body if isinstance(n, ast.FunctionDef) and n.name == "run"
    )
    for no in ast.walk(run):
        if not isinstance(no, ast.If):
            continue
        teste = no.test
        if (
            isinstance(teste, ast.Compare)
            and isinstance(teste.left, ast.Name)
            and teste.left.id == "source"
            and isinstance(teste.comparators[0], ast.Constant)
            and teste.comparators[0].value == source
        ):
            for stmt in no.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and isinstance(stmt.targets[0], ast.Name)
                    and stmt.targets[0].id == "marketplace_id"
                ):
                    return stmt.value.value
    raise AssertionError(f"marketplace_id de {source!r} não encontrado")
