"""Gate FULL-1A/-1A-R — EXECUCAO do sync: ordem, lock, auditoria, commit.

Nenhum teste toca banco real. As fabricas de sessao/conexao sao injetadas.

POR QUE OS FAKES DEVOLVEM DICIONARIO
-------------------------------------
O codigo de producao consome `.mappings()` e indexa por NOME. Um fake que
devolvesse tupla passaria nos testes e falharia em producao -- modo de falha ja'
registrado neste repositorio.

POR QUE OS FAKES REGISTRAM A ORDEM GLOBAL
------------------------------------------
Varios testes desta rodada sao sobre ORDEM (lock antes de tudo, EXCEPT antes do
commit, transacao gravavel so' depois da fonte). Um fake que so' registrasse "o
que" aconteceu, sem "quando", nao conseguiria provar nenhuma delas. Por isso as
conexoes compartilham um relogio logico (`Trace`).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from pipelines import sync_ml_fulfillment_daily as mod
from pipelines.sync_ml_fulfillment_daily import (
    ADVISORY_LOCK_KEY,
    AUDIT_SOURCE,
    AUDIT_SOURCE_BACKFILL,
    AUDIT_SOURCE_FULL,
    CLASSE_FULL,
    MODE_AUTO,
    MODE_BACKFILL,
    MODE_DIAGNOSTIC,
    MODE_FULL,
    MODE_INCREMENTAL,
    ROTULO_FULL,
    ConcurrentRunError,
    MLFulfillmentSyncError,
    Row,
    SourceSnapshot,
    SourceUnavailableError,
    Window,
    build_parser,
    main,
    run_apply,
    run_dry,
)

AGORA = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fakes com relogio logico compartilhado
# ---------------------------------------------------------------------------


class Trace:
    """Relogio logico: prova ORDEM, nao so' ocorrencia."""

    def __init__(self):
        self.eventos: list[tuple[int, str, str]] = []

    def marcar(self, origem: str, texto: str) -> None:
        self.eventos.append((len(self.eventos), origem, texto))

    def indice(self, agulha: str, origem: str | None = None) -> int:
        for i, o, t in self.eventos:
            if agulha in t and (origem is None or o == origem):
                return i
        raise AssertionError(f"evento nao encontrado: {agulha!r} ({origem})")

    def existe(self, agulha: str) -> bool:
        return any(agulha in t for _, _, t in self.eventos)


class FakeResult:
    def __init__(self, linhas, rowcount=None):
        self._linhas = list(linhas)
        # `rowcount` explicito: um UPDATE real devolve linhas AFETADAS, nao
        # linhas retornadas. Amarrar os dois faria `audit_finish` -- que exige
        # rowcount == 1 -- falhar contra um fake que em producao funcionaria.
        self.rowcount = len(self._linhas) if rowcount is None else rowcount

    def mappings(self):
        return self

    def one(self):
        return self._linhas[0]

    def first(self):
        return self._linhas[0] if self._linhas else None

    def scalar(self):
        return list(self._linhas[0].values())[0] if self._linhas else None

    def scalar_one(self):
        return list(self._linhas[0].values())[0]

    def __iter__(self):
        return iter(self._linhas)


class CursorDBAPIFiel:
    """Cursor DBAPI fiel o bastante para `execute_values` REAL dirigi-lo.

    `psycopg2.extras.execute_values` usa exatamente tres coisas do cursor:
    `connection.encoding` (para codificar o SQL), `mogrify(template, args)` por
    LINHA e `execute(sql)` por PAGINA. Este fake implementa as tres -- e o
    `mogrify` usa `psycopg2.extensions.adapt`, a adaptacao de verdade, nao uma
    interpolacao caseira.

    Por que nao um lambda permissivo: um stub que engolisse tudo provaria apenas
    que a funcao foi chamada. Aqui `execute_values` roda de verdade, e cada
    `execute` registrado E' uma pagina -- e' assim que a contagem de round-trips
    vira prova.
    """

    def __init__(self, conexao):
        self.connection = conexao
        self.paginas: list[bytes] = []
        self.linhas_mogrificadas = 0
        self.falhar_na_pagina: int | None = None

    def mogrify(self, template, args):
        import psycopg2.extensions as ext
        self.linhas_mogrificadas += 1
        partes = []
        for a in args:
            partes.append(b"NULL" if a is None else ext.adapt(a).getquoted())
        corpo = b"(" + b",".join(partes) + b")"
        assert template.count(b"%s") if isinstance(template, bytes) else True
        return corpo

    def execute(self, sql, args=None):
        if self.falhar_na_pagina is not None and \
                len(self.paginas) == self.falhar_na_pagina:
            raise RuntimeError(
                f"falha injetada na pagina {self.falhar_na_pagina} "
                "em postgresql://u:p@10.0.0.1/db")
        self.paginas.append(sql)

    # `execute_values` nunca chama estes, mas um cursor de verdade os tem.
    def close(self):
        pass


class ConexaoDBAPIFiel:
    """Conexao DBAPI minima: so' o que `execute_values` consulta."""

    encoding = "UTF8"

    def __init__(self):
        self.cursores: list[CursorDBAPIFiel] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        c = CursorDBAPIFiel(self)
        self.cursores.append(c)
        return c

    # Existem para que um commit/rollback indevido do helper seja DETECTADO,
    # nao silenciosamente ignorado.
    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeConn:
    def __init__(self, origem, trace, respostas=None, falhar_em=None):
        self.origem = origem
        self.trace = trace
        self.executed: list[tuple[str, object]] = []
        self.respostas = respostas or {}
        self.falhar_em = falhar_em
        self.closed = 0
        # `.connection` e' por onde `_inserir_em_lote` obtem o cursor DBAPI da
        # MESMA transacao. Um fake sem isso nao exercitaria o caminho real.
        self.connection = ConexaoDBAPIFiel()

    def execute(self, sql, params=None):
        texto = str(sql)
        self.executed.append((texto, params))
        self.trace.marcar(self.origem, texto)
        if self.falhar_em and self.falhar_em in texto:
            raise RuntimeError("falha injetada em postgresql://u:p@10.0.0.1/db")
        for chave, linhas in self.respostas.items():
            if chave in texto:
                return FakeResult(linhas)
        if texto.lstrip().upper().startswith(("UPDATE", "DELETE", "INSERT")):
            return FakeResult([], rowcount=1)
        return FakeResult([])

    def close(self):
        self.closed += 1
        self.trace.marcar(self.origem, "<<CLOSE>>")


class FakeSession:
    def __init__(self, conn):
        self._conn = conn
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0
        self.commit_levanta = False

    def connection(self):
        return self._conn

    def commit(self):
        if self.commit_levanta:
            raise RuntimeError("commit indeterminado em host=neon.tech")
        self.commits += 1
        self._conn.trace.marcar(self._conn.origem, "<<COMMIT>>")

    def rollback(self):
        self.rollbacks += 1
        self._conn.trace.marcar(self._conn.origem, "<<ROLLBACK>>")

    def close(self):
        self.closed += 1


RESPOSTAS_PADRAO = {
    # Lock concedido por padrao. Os testes de disputa sobrescrevem para False.
    "pg_try_advisory_lock": [{"obtido": True}],
    "RETURNING sync_run_id": [{"sync_run_id": 77}],
    "AS linhas": [{"linhas": 0, "gmv": 0, "pedidos": 0, "unidades": 0}],
    "so_staging": [{"so_staging": 0, "so_destino": 0}],
    "MAX(finished_at)": [{"ultimo": AGORA}],
}


class Ambiente:
    """Monta as tres conexoes (lock, publicacao, auditoria) e a fonte."""

    def __init__(self, respostas_extra=None, falhar_pub=None,
                 falhar_audit=None, falhar_lock=None):
        self.trace = Trace()
        r = dict(RESPOSTAS_PADRAO)
        r.update(respostas_extra or {})
        self.conn_lock = FakeConn("lock", self.trace, r, falhar_em=falhar_lock)
        self.conn_pub = FakeConn("pub", self.trace, r, falhar_em=falhar_pub)
        self.conn_audit = FakeConn("audit", self.trace, r,
                                   falhar_em=falhar_audit)
        self.conn_dm = FakeConn("dm", self.trace, r)
        self.s_pub = FakeSession(self.conn_pub)
        self.s_audit = FakeSession(self.conn_audit)
        self.s_dm = FakeSession(self.conn_dm)
        # Ordem de criacao no run_apply: auditoria primeiro, publicacao depois.
        self._neon = iter([self.s_audit, self.s_pub])

    def lock_factory(self):
        return self.conn_lock

    def neon_factory(self):
        return next(self._neon)

    def dm_factory(self):
        return self.s_dm

    def kwargs(self):
        return {"dm_factory": self.dm_factory, "neon_factory": self.neon_factory,
                "lock_factory": self.lock_factory}


def snapshot_valido(window=None) -> SourceSnapshot:
    return SourceSnapshot(
        window=window or Window(date(2026, 8, 1), date(2026, 8, 31)),
        rows=[Row(ref_date=date(2026, 8, 1), brand="barbours",
                  fulfillment_class=CLASSE_FULL,
                  logistic_type_original=ROTULO_FULL,
                  eligible_orders=10, paid_orders=9, cancelled_orders=1,
                  other_orders=0, paid_gmv=Decimal("1000"), paid_units=9)],
        listing_rows=[],
        preflight={"pedidos": 10, "pedidos_pagos": 9, "envios_duplicados": 0,
                   "pedidos_sem_line_item": 0,
                   "pedidos_alocacao_divergente": 0, "status_distintos": 2},
        source_alive=True,
    )


@pytest.fixture
def stub_read_source(monkeypatch):
    def _aplicar(snapshot=None, trace=None, levantar=None):
        alvo = snapshot if snapshot is not None else snapshot_valido()

        def fake(conn, window):
            if trace is not None:
                trace.marcar("dm", "<<READ_SOURCE>>")
            if levantar is not None:
                raise levantar
            alvo.window = window
            return alvo
        monkeypatch.setattr(mod, "read_source", fake)
        return alvo
    return _aplicar


# ---------------------------------------------------------------------------
# Dry-run: read-only de verdade
# ---------------------------------------------------------------------------


def test_dry_run_nao_abre_conexao_gravavel_nem_para_o_lock(stub_read_source,
                                                           monkeypatch):
    """Sem --apply o destino nao e' tocado nem para auditoria nem para lock."""
    stub_read_source()
    usados = []
    monkeypatch.setattr(mod, "LocalSession",
                        lambda: usados.append("neon") or None)
    monkeypatch.setattr(mod, "_default_lock_connection",
                        lambda: usados.append("lock") or None)
    trace = Trace()
    dm = FakeSession(FakeConn("dm", trace))
    saida = run_dry(MODE_INCREMENTAL, AGORA, dm_factory=lambda: dm)

    assert saida["applied"] is False
    assert usados == []
    assert dm.commits == 0 and dm.rollbacks == 1 and dm.closed == 1


def test_dry_run_recusa_auto(stub_read_source):
    stub_read_source()
    trace = Trace()
    with pytest.raises(MLFulfillmentSyncError, match="auto exige --apply"):
        run_dry(MODE_AUTO, AGORA,
                dm_factory=lambda: FakeSession(FakeConn("dm", trace)))


def test_dry_run_reporta_shares_e_preflight(stub_read_source):
    stub_read_source()
    trace = Trace()
    saida = run_dry(MODE_DIAGNOSTIC, AGORA,
                    dm_factory=lambda: FakeSession(FakeConn("dm", trace)))
    assert saida["share_full_gmv"] == 1.0
    assert saida["preflight"]["pedidos_alocacao_divergente"] == 0
    assert saida["rows"] == 1


# ---------------------------------------------------------------------------
# ORDEM — as contraprovas centrais desta revisao
# ---------------------------------------------------------------------------


def test_ordem_completa_da_execucao(stub_read_source):
    """CONTRAPROVA de ordem, ponta a ponta.

    lock -> auditoria running -> leitura da fonte -> transacao gravavel
         -> DELETE -> INSERT -> EXCEPT -> COMMIT -> auditoria success
    """
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    t = amb.trace

    i_lock = t.indice("pg_try_advisory_lock", "lock")
    i_audit_start = t.indice("INSERT INTO audit.source_sync_run", "audit")
    i_read = t.indice("<<READ_SOURCE>>", "dm")
    i_delete = t.indice("DELETE FROM marts.fact_ml_fulfillment_daily", "pub")
    i_insert = t.indice("INSERT INTO marts.fact_ml_fulfillment_daily", "pub")
    i_except = t.indice("so_staging", "pub")
    i_commit = t.indice("<<COMMIT>>", "pub")
    i_audit_end = t.indice("UPDATE audit.source_sync_run", "audit")

    assert i_lock < i_audit_start < i_read < i_delete < i_insert
    assert i_insert < i_except < i_commit < i_audit_end


def test_except_acontece_ANTES_do_commit(stub_read_source):
    """CONTRAPROVA: EXCEPT depois do commit nao seria reconciliacao.

    Seria relatorio de estrago -- a divergencia ja' estaria publicada e o
    rollback prometido seria impossivel. Este teste falha se alguem mover o
    EXCEPT para depois.
    """
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert amb.trace.indice("so_staging", "pub") < \
        amb.trace.indice("<<COMMIT>>", "pub")


def test_transacao_gravavel_so_nasce_depois_da_leitura_da_fonte(
        stub_read_source):
    """CONTRAPROVA de transacao ociosa.

    Nenhum statement na conexao de publicacao pode preceder a leitura da fonte:
    se preceder, a transacao do Neon ficou aberta esperando o Data Mart.
    """
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())

    i_read = amb.trace.indice("<<READ_SOURCE>>", "dm")
    primeiros_pub = [i for i, o, _ in amb.trace.eventos if o == "pub"]
    assert primeiros_pub, "a publicacao nao executou nada"
    assert min(primeiros_pub) > i_read


def test_lock_cobre_decisao_leitura_e_publicacao(stub_read_source):
    """O lock e' o primeiro evento e o unlock e' o ultimo do ciclo."""
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    run_apply(MODE_AUTO, AGORA, **amb.kwargs())
    t = amb.trace

    i_lock = t.indice("pg_try_advisory_lock", "lock")
    i_decide = t.indice("MAX(finished_at)", "lock")
    i_unlock = t.indice("pg_advisory_unlock", "lock")

    assert i_lock == 0                       # antes de tudo
    assert i_lock < i_decide                 # cobre a decisao do modo
    assert i_unlock > t.indice("<<READ_SOURCE>>", "dm")
    assert i_unlock > t.indice("<<COMMIT>>", "pub")


def test_lock_e_liberado_no_sucesso(stub_read_source):
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert amb.trace.existe("pg_advisory_unlock")
    assert amb.conn_lock.closed == 1


def test_lock_e_liberado_em_falha_pre_commit(stub_read_source):
    amb = Ambiente(falhar_pub="INSERT INTO marts")
    stub_read_source(trace=amb.trace)
    with pytest.raises(MLFulfillmentSyncError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert amb.trace.existe("pg_advisory_unlock")
    assert amb.conn_lock.closed == 1


def test_lock_e_liberado_quando_a_fonte_levanta(stub_read_source):
    amb = Ambiente()
    stub_read_source(trace=amb.trace,
                     levantar=SourceUnavailableError("fonte fora"))
    with pytest.raises(MLFulfillmentSyncError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert amb.trace.existe("pg_advisory_unlock")
    assert amb.conn_lock.closed == 1


def test_lock_nao_e_reobtido_silenciosamente(stub_read_source):
    """Uma unica aquisicao por execucao."""
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    locks = [t for t, _ in amb.conn_lock.executed if "pg_try_advisory_lock" in t]
    assert len(locks) == 1


def test_lock_que_falha_nao_tenta_unlock(stub_read_source):
    """Unlock de lock nunca obtido liberaria o lock de OUTRA execucao."""
    amb = Ambiente(falhar_lock="pg_try_advisory_lock")
    stub_read_source(trace=amb.trace)
    with pytest.raises(MLFulfillmentSyncError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert not amb.trace.existe("pg_advisory_unlock")


def test_decisao_do_modo_acontece_sob_o_lock(stub_read_source):
    """Decidir antes do lock deixaria duas execucoes escolherem `full` juntas."""
    amb = Ambiente(respostas_extra={"MAX(finished_at)": [{"ultimo": None}]})
    stub_read_source(trace=amb.trace)
    res = run_apply(MODE_AUTO, AGORA, **amb.kwargs())
    assert res["effective_mode"] == MODE_FULL
    assert amb.trace.indice("pg_try_advisory_lock", "lock") < \
        amb.trace.indice("MAX(finished_at)", "lock")


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------


def test_auditoria_registra_success_com_modo_efetivo(stub_read_source):
    amb = Ambiente(respostas_extra={"MAX(finished_at)": [{"ultimo": None}]})
    stub_read_source(trace=amb.trace)
    run_apply(MODE_AUTO, AGORA, **amb.kwargs())

    fontes = [p["fonte"] for t, p in amb.conn_audit.executed
              if "INSERT INTO audit.source_sync_run" in t]
    # `auto` virou `full`: a auditoria registra o TRABALHO, nao o argumento.
    assert set(fontes) == {AUDIT_SOURCE, AUDIT_SOURCE_FULL}
    updates = [p for t, p in amb.conn_audit.executed
               if "UPDATE audit.source_sync_run" in t]
    assert all(u["status"] == "success" for u in updates)


@pytest.mark.parametrize("modo,extra", [
    (MODE_BACKFILL, AUDIT_SOURCE_BACKFILL), (MODE_FULL, AUDIT_SOURCE_FULL)])
def test_backfill_e_full_abrem_duas_linhas(stub_read_source, modo, extra):
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    run_apply(modo, AGORA, **amb.kwargs())
    fontes = [p["fonte"] for t, p in amb.conn_audit.executed
              if "INSERT INTO audit.source_sync_run" in t]
    assert set(fontes) == {AUDIT_SOURCE, extra}


def test_auditoria_usa_os_limites_da_janela_pedida(stub_read_source):
    """`source_min_date`/`max_date` dizem o que foi VARRIDO, nao o que existia."""
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    inserts = [p for t, p in amb.conn_audit.executed
               if "INSERT INTO audit.source_sync_run" in t]
    assert inserts[0]["min_date"] == date(2026, 8, 30)
    assert inserts[0]["max_date"] == date(2026, 9, 14)


def test_rows_extracted_e_loaded_nao_confundem_graos(stub_read_source):
    """extracted = pedidos elegiveis; loaded = linhas da fato agregada."""
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    res = run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert res["rows_extracted"] == 10       # pedidos elegiveis
    assert res["rows_loaded"] == 1           # linhas agregadas
    updates = [p for t, p in amb.conn_audit.executed
               if "UPDATE audit.source_sync_run" in t]
    assert updates[0]["extracted"] == 10 and updates[0]["loaded"] == 1


def test_auditoria_nunca_escreve_em_tabela_de_fato(stub_read_source):
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    sql_audit = " ".join(t for t, _ in amb.conn_audit.executed)
    assert "marts.fact_ml_fulfillment" not in sql_audit


def test_publicacao_nunca_escreve_em_auditoria(stub_read_source):
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    sql_pub = " ".join(t for t, _ in amb.conn_pub.executed)
    assert "audit.source_sync_run" not in sql_pub


def test_obrigacao_duravel_so_conta_success_finalizado():
    """`running` e `failed` nao podem fazer o full do mes parecer feito."""
    sql = str(mod.SQL_ULTIMO_SUCESSO)
    assert "status = 'success'" in sql
    assert "finished_at IS NOT NULL" in sql


# ---------------------------------------------------------------------------
# Falha, rollback e commit indeterminado
# ---------------------------------------------------------------------------


def test_falha_pre_commit_faz_rollback_e_audita_failed(stub_read_source):
    amb = Ambiente(falhar_pub="INSERT INTO marts")
    stub_read_source(trace=amb.trace)
    with pytest.raises(MLFulfillmentSyncError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert amb.s_pub.commits == 0
    updates = [p for t, p in amb.conn_audit.executed
               if "UPDATE audit.source_sync_run" in t]
    assert updates and updates[0]["status"] == "failed"


def test_mensagem_de_erro_e_sanitizada_antes_da_auditoria(stub_read_source):
    amb = Ambiente(falhar_pub="INSERT INTO marts")
    stub_read_source(trace=amb.trace)
    with pytest.raises(MLFulfillmentSyncError) as exc:
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert "10.0.0.1" not in str(exc.value)
    updates = [p for t, p in amb.conn_audit.executed
               if "UPDATE audit.source_sync_run" in t]
    assert "10.0.0.1" not in (updates[0]["erro"] or "")


def test_commit_indeterminado_nao_faz_rollback_nem_promete_retry(
        stub_read_source):
    """Se o commit levantou, o servidor PODE ter efetivado."""
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    amb.s_pub.commit_levanta = True
    with pytest.raises(MLFulfillmentSyncError) as exc:
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())

    msg = str(exc.value)
    assert "ESTADO INDETERMINADO" in msg
    assert "nao reexecute automaticamente" in msg
    assert amb.s_pub.rollbacks == 0
    assert "neon.tech" not in msg
    # E o lock ainda assim e' liberado.
    assert amb.trace.existe("pg_advisory_unlock")


def test_commit_indeterminado_nao_marca_failed(stub_read_source):
    """CONTRAPROVA: marcar `failed` diria que nada foi publicado -- e' falso."""
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    amb.s_pub.commit_levanta = True
    with pytest.raises(MLFulfillmentSyncError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    updates = [p for t, p in amb.conn_audit.executed
               if "UPDATE audit.source_sync_run" in t]
    assert not any(u["status"] == "failed" for u in updates)


def test_auditoria_que_falha_apos_commit_nao_vira_failed(stub_read_source):
    """CONTRAPROVA central da Fase 1.

    A publicacao ESTA commitada. Marcar `failed` faria o `auto` da proxima
    execucao refazer trabalho sobre dado bom, e mentiria sobre o que houve.
    """
    amb = Ambiente(falhar_audit="UPDATE audit.source_sync_run")
    stub_read_source(trace=amb.trace)

    res = run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())

    assert amb.s_pub.commits == 1                       # publicou
    assert res["applied"] is True                       # e diz que publicou
    assert res["audit_status"] == "nao_registrada_apos_commit"
    updates = [p for t, p in amb.conn_audit.executed
               if "UPDATE audit.source_sync_run" in t]
    assert not any(u["status"] == "failed" for u in updates)
    assert amb.s_pub.rollbacks == 0                     # nada de rollback pos-commit


# ---------------------------------------------------------------------------
# Fonte vazia x fonte indisponivel
# ---------------------------------------------------------------------------


def test_fonte_indisponivel_nao_chega_perto_do_delete(stub_read_source):
    """CONTRAPROVA: falha de fonte nao pode apagar janela publicada."""
    amb = Ambiente()
    stub_read_source(trace=amb.trace,
                     levantar=SourceUnavailableError("fonte fora do ar"))
    with pytest.raises(SourceUnavailableError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert not amb.trace.existe("DELETE FROM marts")
    assert amb.s_pub.commits == 0


def test_janela_vazia_com_destino_vazio_publica_no_op(stub_read_source):
    """Fonte saudavel, zero pedidos, destino vazio: no-op auditavel."""
    vazio = SourceSnapshot(window=Window(date(2026, 8, 1), date(2026, 8, 31)),
                           rows=[], preflight={}, source_alive=True)
    amb = Ambiente()
    stub_read_source(vazio, trace=amb.trace)
    res = run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert res["empty_window"] is True
    assert res["rows_loaded"] == 0
    assert amb.s_pub.commits == 1
    assert not amb.trace.existe("DELETE FROM marts")   # nao ha o que apagar


def test_janela_vazia_com_destino_populado_e_recusada(stub_read_source):
    """CONTRAPROVA: leitura vazia NAO apaga historico ja' publicado."""
    vazio = SourceSnapshot(window=Window(date(2026, 8, 1), date(2026, 8, 31)),
                           rows=[], preflight={}, source_alive=True)
    amb = Ambiente(respostas_extra={
        "AS linhas": [{"linhas": 42, "gmv": 1000, "pedidos": 9, "unidades": 9}]})
    stub_read_source(vazio, trace=amb.trace)
    with pytest.raises(MLFulfillmentSyncError, match="nao apaga historico"):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert amb.s_pub.commits == 0


def test_except_divergente_derruba_a_transacao(stub_read_source):
    amb = Ambiente(respostas_extra={
        "so_staging": [{"so_staging": 3, "so_destino": 0}]})
    stub_read_source(trace=amb.trace)
    with pytest.raises(MLFulfillmentSyncError, match="EXCEPT bidirecional"):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert amb.s_pub.commits == 0


def test_idempotencia_do_incremental(stub_read_source):
    saidas = []
    for _ in range(2):
        amb = Ambiente()
        stub_read_source(snapshot_valido(), trace=amb.trace)
        r = run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
        saidas.append((r["rows"], r["paid_gmv_total"], r["share_full_gmv"]))
    assert saidas[0] == saidas[1]


def test_no_op_quando_nada_muda(stub_read_source):
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    assert run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())["no_op"] is True


def test_delete_sempre_limitado_a_janela(stub_read_source):
    amb = Ambiente()
    snap = stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    deletes = [(t, p) for t, p in amb.conn_pub.executed if "DELETE FROM" in t]
    assert len(deletes) == 2                 # fato agregada + listing
    for _, params in deletes:
        assert params["date_from"] == snap.window.date_from
        assert params["date_to"] == snap.window.date_to


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_recusa_janela_explicita_com_apply(capsys):
    assert main(["--mode", "full", "--apply",
                 "--date-from", "2026-08-01", "--date-to", "2026-08-02"]) == 1
    assert "janela explicita" in capsys.readouterr().out


def test_cli_recusa_diagnostic_com_apply(capsys):
    assert main(["--mode", "diagnostic", "--apply"]) == 1
    assert "nao escreve" in capsys.readouterr().out


def test_cli_default_e_read_only():
    args = build_parser().parse_args([])
    assert args.mode == MODE_DIAGNOSTIC
    assert args.apply is False


# ---------------------------------------------------------------------------
# Concorrencia — o gate FULL-1B-L
# ---------------------------------------------------------------------------


def _ambiente_lock(valor_obtido):
    """Ambiente cujo `pg_try_advisory_lock` devolve `valor_obtido`."""
    return Ambiente(respostas_extra={
        "pg_try_advisory_lock": [{"obtido": valor_obtido}]})


def test_primeira_execucao_adquire_o_lock_e_segue(stub_read_source):
    amb = _ambiente_lock(True)
    stub_read_source(trace=amb.trace)
    res = run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert res["applied"] is True
    assert amb.trace.existe("pg_try_advisory_lock")
    assert amb.trace.existe("pg_advisory_unlock")


def test_segunda_execucao_falha_imediatamente(stub_read_source):
    """CONTRAPROVA central: lock negado termina a execucao na hora."""
    amb = _ambiente_lock(False)
    stub_read_source(trace=amb.trace)
    with pytest.raises(ConcurrentRunError) as exc:
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    msg = str(exc.value)
    assert "ja' detem o lock" in msg
    assert "retry" in msg.lower() or "Sem espera" in msg


def test_lock_negado_nao_le_a_fonte(stub_read_source):
    amb = _ambiente_lock(False)
    stub_read_source(trace=amb.trace)
    with pytest.raises(ConcurrentRunError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert not amb.trace.existe("<<READ_SOURCE>>")
    assert amb.conn_dm.executed == []


def test_lock_negado_nao_abre_auditoria(stub_read_source):
    amb = _ambiente_lock(False)
    stub_read_source(trace=amb.trace)
    with pytest.raises(ConcurrentRunError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert amb.conn_audit.executed == []
    assert not amb.trace.existe("audit.source_sync_run")


def test_lock_negado_nao_publica(stub_read_source):
    amb = _ambiente_lock(False)
    stub_read_source(trace=amb.trace)
    with pytest.raises(ConcurrentRunError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert amb.conn_pub.executed == []
    assert amb.s_pub.commits == 0
    assert not amb.trace.existe("DELETE FROM marts")
    assert not amb.trace.existe("INSERT INTO marts")


def test_lock_negado_nao_tenta_unlock(stub_read_source):
    """Unlock de chave nunca adquirida liberaria o lock de OUTRA execucao."""
    amb = _ambiente_lock(False)
    stub_read_source(trace=amb.trace)
    with pytest.raises(ConcurrentRunError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert not amb.trace.existe("pg_advisory_unlock")


def test_lock_negado_so_executa_o_try_lock(stub_read_source):
    """Prova agregada: UM statement em toda a execucao."""
    amb = _ambiente_lock(False)
    stub_read_source(trace=amb.trace)
    with pytest.raises(ConcurrentRunError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    sql = [t for t, _ in amb.conn_lock.executed]
    assert len(sql) == 1 and "pg_try_advisory_lock" in sql[0]


@pytest.mark.parametrize("ambiguo", [None, False, 0, "", "f"])
def test_valor_ambiguo_nunca_e_tratado_como_lock_adquirido(
        stub_read_source, ambiguo):
    """`is not True`, nao `if not obtido`.

    Conexao encerrada devolve None; um driver exotico poderia devolver 0 ou 'f'.
    Nenhum deles pode virar posse do lock.
    """
    amb = _ambiente_lock(ambiguo)
    stub_read_source(trace=amb.trace)
    with pytest.raises(ConcurrentRunError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert not amb.trace.existe("pg_advisory_unlock")


def test_booleano_e_realmente_inspecionado(stub_read_source):
    """CONTRAPROVA: disparar o SELECT sem ler o retorno.

    Se o codigo ignorasse o resultado, `False` seguiria adiante como se tivesse
    adquirido o lock -- e este teste passaria a falhar.
    """
    amb_ok = _ambiente_lock(True)
    stub_read_source(trace=amb_ok.trace)
    assert run_apply(MODE_INCREMENTAL, AGORA, **amb_ok.kwargs())["applied"]

    amb_nao = _ambiente_lock(False)
    stub_read_source(trace=amb_nao.trace)
    with pytest.raises(ConcurrentRunError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb_nao.kwargs())


def test_erro_durante_a_aquisicao_nao_chama_unlock(stub_read_source):
    """Excecao no proprio try-lock: a chave nunca foi nossa."""
    amb = Ambiente(falhar_lock="pg_try_advisory_lock")
    stub_read_source(trace=amb.trace)
    with pytest.raises(MLFulfillmentSyncError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert not amb.trace.existe("pg_advisory_unlock")


def test_erro_posterior_chama_unlock_exatamente_uma_vez(stub_read_source):
    amb = Ambiente(falhar_pub="INSERT INTO marts")
    stub_read_source(trace=amb.trace)
    with pytest.raises(MLFulfillmentSyncError):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    unlocks = [t for t, _ in amb.conn_lock.executed
               if "pg_advisory_unlock" in t]
    assert len(unlocks) == 1


def test_a_mesma_conexao_que_adquiriu_e_a_que_libera(stub_read_source):
    """Advisory lock de sessao pertence a CONEXAO: unlock de outra nao funciona."""
    amb = _ambiente_lock(True)
    stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    origens_lock = {o for _, o, t in amb.trace.eventos
                    if "pg_try_advisory_lock" in t}
    origens_unlock = {o for _, o, t in amb.trace.eventos
                      if "pg_advisory_unlock" in t}
    assert origens_lock == origens_unlock == {"lock"}
    # E nenhuma outra conexao mexeu em advisory lock.
    for conn in (amb.conn_pub, amb.conn_audit, amb.conn_dm):
        assert not any("advisory" in t for t, _ in conn.executed)


def test_lock_permanece_ate_o_fim_da_operacao(stub_read_source):
    amb = _ambiente_lock(True)
    stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    t = amb.trace
    i_unlock = t.indice("pg_advisory_unlock", "lock")
    assert i_unlock > t.indice("<<COMMIT>>", "pub")
    assert i_unlock > t.indice("UPDATE audit.source_sync_run", "audit")


def test_keyboardinterrupt_libera_o_lock_e_propaga(stub_read_source):
    """Ctrl+C no meio da carga nao pode deixar a chave presa.

    `KeyboardInterrupt` nao passa pelo `except Exception`, so' pelo `finally`.
    """
    amb = _ambiente_lock(True)
    stub_read_source(trace=amb.trace,
                     levantar=KeyboardInterrupt("ctrl+c"))
    with pytest.raises(KeyboardInterrupt):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert amb.trace.existe("pg_advisory_unlock")
    assert amb.conn_lock.closed == 1


def test_systemexit_libera_o_lock_e_propaga(stub_read_source):
    amb = _ambiente_lock(True)
    stub_read_source(trace=amb.trace, levantar=SystemExit(3))
    with pytest.raises(SystemExit):
        run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    assert amb.trace.existe("pg_advisory_unlock")


def test_cli_devolve_exit_code_1_na_disputa(monkeypatch, capsys):
    """Exit code nao zero, mensagem sanitizada, sem DSN."""
    def falso_apply(*a, **kw):
        raise ConcurrentRunError(
            "outra execucao de ml_fulfillment_daily ja' detem o lock 916140016. "
            "Nada foi lido, auditado ou publicado.")
    monkeypatch.setattr(mod, "run_apply", falso_apply)

    assert main(["--mode", "incremental", "--apply"]) == 1
    saida = capsys.readouterr().out
    assert '"status": "failed"' in saida
    assert "ja' detem o lock" in saida
    for proibido in ("postgresql://", "password", "host=", "10.0.0."):
        assert proibido not in saida


# ---------------------------------------------------------------------------
# Contraprova REAL: duas conexoes disputando a chave num PostgreSQL de verdade
# ---------------------------------------------------------------------------
#
# Os testes acima usam fakes e provam o CONTRATO do modulo. Eles nao provam que
# `pg_try_advisory_lock` se comporta como assumimos -- para isso e' preciso um
# servidor.
#
# Requer `FULL_LOCK_TEST_DSN` apontando para um PostgreSQL DESCARTAVEL. Sem a
# variavel, o teste e' pulado: nunca cai em banco de producao por engano, e
# nenhuma credencial fica escrita aqui.
#
# ESCOPO: SOMENTE advisory locks. Nenhum SELECT em tabela, nenhum INSERT,
# nenhum DDL. Advisory lock vive na memoria do servidor e some quando a sessao
# fecha -- nao toca dado de ninguem.
#
# NAO PODE PENDURAR: so' a variante `try` e' usada, e ela retorna na hora. O
# `statement_timeout` de 5 s e' um cinto de seguranca redundante, para o caso de
# alguem introduzir a variante bloqueante aqui um dia.

CHAVE_TESTE = 916140017   # vizinha da de producao, nunca a mesma


def _dsn_local():
    import os
    return os.environ.get("FULL_LOCK_TEST_DSN", "").strip()


requer_pg = pytest.mark.skipif(
    not _dsn_local(),
    reason="defina FULL_LOCK_TEST_DSN apontando para um PostgreSQL descartavel "
           "para rodar a contraprova real de advisory lock",
)


@pytest.fixture
def conexoes_reais():
    """Duas conexoes independentes, em autocommit, com timeout curto."""
    psycopg2 = pytest.importorskip("psycopg2")
    abertas = []

    def abrir():
        c = psycopg2.connect(_dsn_local(), connect_timeout=5)
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 5000")
        abertas.append(c)
        return c

    try:
        yield abrir
    finally:
        for c in abertas:
            try:
                # Fechar a sessao ja' libera todo advisory lock dela; o unlock
                # explicito abaixo e' para o caso de o teste ter falhado no meio.
                with c.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock_all()")
            except Exception:
                pass
            try:
                c.close()
            except Exception:
                pass


def _try_lock(conn, chave=CHAVE_TESTE):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (chave,))
        return cur.fetchone()[0]


def _unlock(conn, chave=CHAVE_TESTE):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (chave,))
        return cur.fetchone()[0]


@requer_pg
def test_real_segunda_conexao_recebe_false_imediatamente(conexoes_reais):
    """A prova que os fakes nao podem dar: o servidor recusa na hora."""
    import time

    a = conexoes_reais()
    b = conexoes_reais()

    assert _try_lock(a) is True, "a primeira conexao deveria adquirir"

    inicio = time.monotonic()
    obtido_b = _try_lock(b)
    decorrido = time.monotonic() - inicio

    assert obtido_b is False, "a segunda conexao NAO pode adquirir"
    # Fail-fast de verdade: a variante bloqueante ficaria presa ate' o unlock.
    assert decorrido < 2.0, f"demorou {decorrido:.2f}s -- isso e' espera"


@requer_pg
def test_real_terceira_conexao_adquire_apos_o_unlock(conexoes_reais):
    """O lock precisa ser liberavel -- senao a primeira falha trava a fila."""
    a = conexoes_reais()
    b = conexoes_reais()

    assert _try_lock(a) is True
    assert _try_lock(b) is False
    assert _unlock(a) is True
    assert _try_lock(b) is True, "apos o unlock a fila deveria destravar"


@requer_pg
def test_real_unlock_de_chave_nao_adquirida_nao_engana(conexoes_reais):
    """Por que `if lock_obtido` existe no finally.

    `pg_advisory_unlock` de uma chave que a sessao nunca pegou devolve `false` e
    emite WARNING. Chamar isso as cegas transformaria "nunca tive o lock" em uma
    operacao aparentemente bem-sucedida.
    """
    a = conexoes_reais()
    assert _unlock(a) is False


@requer_pg
def test_real_o_lock_pertence_a_conexao_que_o_adquiriu(conexoes_reais):
    """Advisory lock de sessao e' da CONEXAO: outra nao consegue libera-lo."""
    a = conexoes_reais()
    b = conexoes_reais()

    assert _try_lock(a) is True
    assert _unlock(b) is False, "b nao detem o lock; nao deveria liberar"
    assert _try_lock(b) is False, "o lock de a continua de pe"
    assert _unlock(a) is True


# ---------------------------------------------------------------------------
# Carga em lote das stagings — Gate FULL-1C-H2
# ---------------------------------------------------------------------------
#
# O piloto FULL-1C-R morreu publicando 76.014 linhas uma a uma. Medido contra
# Postgres local: `executemany(4.000)` custa 0,94x o laco explicito de
# `execute()` -- e' o mesmo laco. Com RTT de 173 ms ao Neon, 76.014 round-trips
# = 3,7 h. `execute_values` com paginas de 500 faz 153.

from pipelines.sync_ml_fulfillment_daily import (   # noqa: E402
    STAGING_PAGE_SIZE,
    _inserir_em_lote,
    _STAGINGS_PERMITIDAS,
)


class ConnParaLote:
    """Conexao SQLAlchemy minima: so' expoe `.connection`."""

    def __init__(self, falhar_na_pagina=None):
        self.connection = ConexaoDBAPIFiel()
        self._falhar = falhar_na_pagina

    def cursor_usado(self):
        return self.connection.cursores[-1]

    def preparar(self):
        # `_inserir_em_lote` cria o cursor; para injetar falha, interceptamos.
        orig = self.connection.cursor

        def cursor():
            c = orig()
            c.falhar_na_pagina = self._falhar
            return c
        self.connection.cursor = cursor
        return self


COLS3 = ("a", "b", "c")


def linhas(n, valor=1):
    return [(date(2026, 8, 1), f"item{i}", Decimal(str(valor))) for i in range(n)]


def test_page_size_e_500_e_documentado():
    assert STAGING_PAGE_SIZE == 500


@pytest.mark.parametrize("n,paginas_esperadas", [
    (0, 0),                    # lista vazia
    (1, 1),                    # uma linha
    (499, 1),
    (500, 1),                  # batch exato
    (501, 2),                  # batch + 1
    (1000, 2),
    (3649, 8),                 # fato agregada real
    (76014, 153),              # fato de listing real
])
def test_numero_de_paginas_e_teto_por_lote(n, paginas_esperadas):
    """CONTRAPROVA do gargalo: round-trips caem de O(linhas) para O(paginas)."""
    conn = ConnParaLote()
    escritas = _inserir_em_lote(conn, "stg_fmfd", COLS3, linhas(n))
    assert escritas == n
    if n == 0:
        assert conn.connection.cursores == []      # nem cursor e' aberto
        return
    cur = conn.cursor_usado()
    assert len(cur.paginas) == paginas_esperadas
    assert cur.linhas_mogrificadas == n


def test_76014_linhas_no_maximo_153_paginas():
    """O teto que o gate fixou para page_size 500."""
    conn = ConnParaLote()
    _inserir_em_lote(conn, "stg_fmfld", COLS3, linhas(76014))
    assert len(conn.cursor_usado().paginas) <= 153


def test_ganho_de_round_trips_contra_o_caminho_antigo():
    """Antes: ~1 round-trip por linha. Depois: 1 por pagina."""
    conn = ConnParaLote()
    n = 76014
    _inserir_em_lote(conn, "stg_fmfld", COLS3, linhas(n))
    paginas = len(conn.cursor_usado().paginas)
    assert n / paginas >= 400, "o lote nao esta amortizando o round-trip"


def test_helper_nunca_faz_commit_nem_rollback():
    """Quem controla a transacao e' `publish_in_transaction`, nao o helper."""
    conn = ConnParaLote()
    _inserir_em_lote(conn, "stg_fmfd", COLS3, linhas(1200))
    assert conn.connection.commits == 0
    assert conn.connection.rollbacks == 0


def test_staging_fora_da_lista_e_recusada():
    """O nome da tabela vai para o SQL; a lista fechada e' o que protege."""
    conn = ConnParaLote()
    with pytest.raises(MLFulfillmentSyncError, match="staging nao permitida"):
        _inserir_em_lote(conn, "stg_fmfd; DROP TABLE x", COLS3, linhas(1))
    assert conn.connection.cursores == []
    assert set(_STAGINGS_PERMITIDAS) == {"stg_fmfd", "stg_fmfld"}


def test_aridade_incorreta_e_recusada_antes_do_driver():
    conn = ConnParaLote()
    ruins = [(date(2026, 8, 1), "x", Decimal("1")), (date(2026, 8, 1), "y")]
    with pytest.raises(MLFulfillmentSyncError, match="aridade|valor"):
        _inserir_em_lote(conn, "stg_fmfd", COLS3, ruins)
    assert conn.connection.cursores == []      # nada chegou ao driver


@pytest.mark.parametrize("ruim", [
    Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"),
    float("nan"), float("inf"), float("-inf"),
])
def test_nan_e_infinito_sao_recusados_antes_do_driver(ruim):
    """NaN atravessa `>= 0` em silencio no Postgres -- barrar aqui da erro util."""
    conn = ConnParaLote()
    dados = [(date(2026, 8, 1), "x", ruim)]
    with pytest.raises(MLFulfillmentSyncError, match="nao finito"):
        _inserir_em_lote(conn, "stg_fmfd", COLS3, dados)
    assert conn.connection.cursores == []


def test_decimal_finito_e_aceito():
    conn = ConnParaLote()
    assert _inserir_em_lote(conn, "stg_fmfd", COLS3,
                            [(date(2026, 8, 1), "x", Decimal("0"))]) == 1


def test_null_e_preservado_como_null():
    conn = ConnParaLote()
    _inserir_em_lote(conn, "stg_fmfd", COLS3, [(None, "x", None)])
    corpo = b"".join(conn.cursor_usado().paginas)
    assert corpo.count(b"NULL") == 2


def test_tipos_reais_passam_por_adaptacao_do_driver():
    """Decimal, date e str viram literais via `adapt`, nao concatenacao."""
    conn = ConnParaLote()
    _inserir_em_lote(conn, "stg_fmfd", COLS3,
                     [(date(2026, 8, 1), "barbours", Decimal("10.55"))])
    corpo = b"".join(conn.cursor_usado().paginas)
    assert b"'2026-08-01'" in corpo
    assert b"'barbours'" in corpo
    assert b"10.55" in corpo


def test_valores_nunca_sao_interpolados_no_texto_do_sql():
    """O SQL montado por nos tem UM `%s`; os valores vem do driver."""
    import inspect
    fonte = inspect.getsource(_inserir_em_lote)
    assert 'VALUES %s' in fonte
    assert "f\"INSERT INTO {staging}" in fonte or 'INSERT INTO {staging}' in fonte
    # Nenhum valor de linha aparece formatado no SQL.
    assert 'linhas[' not in fonte.split('sql =')[1].split('\n')[0]


def test_falha_na_primeira_pagina_propaga_e_nao_vira_retry():
    conn = ConnParaLote(falhar_na_pagina=0).preparar()
    with pytest.raises(RuntimeError):
        _inserir_em_lote(conn, "stg_fmfd", COLS3, linhas(1500))
    assert len(conn.cursor_usado().paginas) == 0
    assert conn.connection.commits == 0


def test_falha_em_pagina_intermediaria_propaga():
    conn = ConnParaLote(falhar_na_pagina=2).preparar()
    with pytest.raises(RuntimeError):
        _inserir_em_lote(conn, "stg_fmfd", COLS3, linhas(2000))
    # Parou na pagina 2: duas escreveram, a transacao inteira sera desfeita
    # por quem controla (publish_in_transaction).
    assert len(conn.cursor_usado().paginas) == 2
    assert conn.connection.rollbacks == 0      # nao e' o helper que desfaz


def test_as_duas_stagings_usam_a_mesma_conexao(stub_read_source):
    """Uma transacao so': ambas passam pela conexao de publicacao."""
    amb = Ambiente()
    stub_read_source(trace=amb.trace)
    run_apply(MODE_INCREMENTAL, AGORA, **amb.kwargs())
    # O cursor DBAPI sai de `conn_pub.connection`, nunca de outra.
    assert len(amb.conn_pub.connection.cursores) >= 1
    for c in (amb.conn_lock, amb.conn_audit, amb.conn_dm):
        assert c.connection.cursores == []


def test_ordem_das_colunas_bate_com_o_insert_final():
    """`_COLS` alimenta staging, INSERT final e EXCEPT -- nao podem divergir."""
    assert mod._LISTA == ", ".join(mod._COLS)
    assert mod._LISTA_L == ", ".join(mod._COLS_L)
    assert len(mod._COLS) == 18
    assert len(mod._COLS_L) == 9


# ---------------------------------------------------------------------------
# Lote contra PostgreSQL REAL (descartavel) — Gate FULL-1C-H2
# ---------------------------------------------------------------------------
#
# Os testes acima usam cursor fiel e provam a CONTAGEM de paginas. Estes provam
# que os tipos sobrevivem a ida e volta por um servidor de verdade.
#
# Reutiliza `FULL_LOCK_TEST_DSN` -- mesmo Postgres local descartavel. Sem a
# variavel, pula. NUNCA aponta para Neon ou Data Mart.
#
# ESCOPO: tabela TEMP, `ON COMMIT DROP`, sempre com rollback ao final.


@pytest.fixture
def conn_real():
    """Connection SQLAlchemy em transacao, com a staging temporaria criada."""
    if not _dsn_local():
        pytest.skip("defina FULL_LOCK_TEST_DSN para a prova em Postgres real")
    sa = pytest.importorskip("sqlalchemy")
    dsn = _dsn_local()
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+psycopg2://", 1)
    eng = sa.create_engine(dsn)
    conn = eng.connect()
    conn.execute(sa.text("""
        CREATE TEMP TABLE stg_fmfd (
            a DATE, b TEXT, c NUMERIC, d BIGINT, e BOOLEAN, f TIMESTAMP
        ) ON COMMIT DROP
    """))
    try:
        yield conn
    finally:
        conn.rollback()        # nada e' commitado, nunca
        conn.close()
        eng.dispose()


COLS_REAL = ("a", "b", "c", "d", "e", "f")


@requer_pg
def test_real_tipos_sobrevivem_a_ida_e_volta(conn_real):
    import sqlalchemy as sa
    linha = (date(2026, 8, 1), "barbours", Decimal("3649773.48"), 48411, True,
             datetime(2026, 8, 1, 12, 34, 56))
    assert _inserir_em_lote(conn_real, "stg_fmfd", COLS_REAL, [linha]) == 1

    r = conn_real.execute(sa.text("SELECT a,b,c,d,e,f FROM stg_fmfd")).one()
    assert r[0] == date(2026, 8, 1)
    assert r[1] == "barbours"
    assert r[2] == Decimal("3649773.48")      # centavo preservado
    assert r[3] == 48411
    assert r[4] is True
    assert r[5] == datetime(2026, 8, 1, 12, 34, 56)


@requer_pg
def test_real_null_chega_como_null(conn_real):
    import sqlalchemy as sa
    _inserir_em_lote(conn_real, "stg_fmfd", COLS_REAL,
                     [(None, "x", None, None, None, None)])
    n = conn_real.execute(sa.text(
        "SELECT count(*) FROM stg_fmfd WHERE a IS NULL AND c IS NULL "
        "AND d IS NULL AND e IS NULL AND f IS NULL")).scalar()
    assert n == 1


@requer_pg
def test_real_volume_com_paginacao(conn_real):
    """4.000 linhas em 8 paginas, todas gravadas."""
    import sqlalchemy as sa
    dados = [(date(2026, 8, 1), f"m{i}", Decimal("1.01"), i, i % 2 == 0,
              datetime(2026, 8, 1)) for i in range(4000)]
    assert _inserir_em_lote(conn_real, "stg_fmfd", COLS_REAL, dados) == 4000
    assert conn_real.execute(sa.text("SELECT count(*) FROM stg_fmfd")).scalar() == 4000
    soma = conn_real.execute(sa.text("SELECT sum(c) FROM stg_fmfd")).scalar()
    assert soma == Decimal("4040.00")          # 4000 x 1,01, sem perda


@requer_pg
def test_real_helper_fica_na_transacao_da_connection(conn_real):
    """PROVA transacional: o rollback da Connection desfaz o lote."""
    import sqlalchemy as sa
    _inserir_em_lote(conn_real, "stg_fmfd", COLS_REAL,
                     [(date(2026, 8, 1), "x", Decimal("1"), 1, True,
                       datetime(2026, 8, 1))])
    assert conn_real.execute(sa.text("SELECT count(*) FROM stg_fmfd")).scalar() == 1
    conn_real.rollback()
    # Apos o rollback a TEMP some junto (ON COMMIT DROP so' cai no commit, mas o
    # rollback desfaz a criacao dela nesta transacao): consultar tem de falhar.
    with pytest.raises(Exception):
        conn_real.execute(sa.text("SELECT count(*) FROM stg_fmfd")).scalar()


@requer_pg
def test_real_falha_de_tipo_propaga_do_driver(conn_real):
    """Erro do banco sobe intacto, sem virar retry."""
    with pytest.raises(Exception) as exc:
        _inserir_em_lote(conn_real, "stg_fmfd", COLS_REAL,
                         [(date(2026, 8, 1), "x", Decimal("1"),
                           "nao-e-bigint", True, datetime(2026, 8, 1))])
    assert "retry" not in str(exc.value).lower()
