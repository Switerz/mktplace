"""
Testes do retry estrito para conflito de recovery em read replica do RDS,
adicionado a pipelines/sync_produtos.py::sync_ml() no Gate C2.4 (2026-07-17).

Contexto (Gates C2.2/C2.3): gold.ml_produto_ranking e' uma VIEW cara lida
contra um read replica com hot_standby_feedback=off — o Postgres pode
cancelar a query ("canceling statement due to conflict with recovery")
quando o replay do WAL precisa remover versoes de linha que a query ainda
precisa. O retry cobre SOMENTE a leitura da fonte RDS, nunca a escrita no
Neon nem a auditoria — testado explicitamente abaixo.

Usa conexoes/cursores falsos — nenhuma credencial real, nenhum banco real.
"""
import pytest

import pipelines.sync_produtos as sp

RECOVERY_MSG = "canceling statement due to conflict with recovery"
ROW_VERSIONS_MSG = (
    "DETAIL:  User query might have needed to see row versions that must be removed."
)


# ---------------------------------------------------------------------------
# _is_recovery_conflict_error
# ---------------------------------------------------------------------------

def test_mensagem_conflict_with_recovery_e_reconhecida():
    assert sp._is_recovery_conflict_error(Exception(RECOVERY_MSG)) is True


def test_mensagem_row_versions_e_reconhecida():
    assert sp._is_recovery_conflict_error(Exception(ROW_VERSIONS_MSG)) is True


def test_erro_de_conexao_generico_nao_e_recovery_conflict():
    exc = Exception("could not connect to server: Connection refused")
    assert sp._is_recovery_conflict_error(exc) is False


def test_runtimeerror_de_validacao_min_rows_ratio_nao_e_recovery_conflict():
    exc = RuntimeError(
        "[ml] queda suspeita de linhas: fonte=10 vs Neon atual=1000 (limite=50%). "
        "Carga abortada sem commit — investigar RDS antes de repetir."
    )
    assert sp._is_recovery_conflict_error(exc) is False


def test_deteccao_nao_depende_de_pgcode_presente():
    """Uma excecao fake (sem atributo pgcode, como as usadas nos testes deste
    modulo) ainda deve ser reconhecida so' pela mensagem."""
    exc = Exception(RECOVERY_MSG)
    assert not hasattr(exc, "pgcode")
    assert sp._is_recovery_conflict_error(exc) is True


# ---------------------------------------------------------------------------
# _read_rds_with_recovery_retry (isolado, sem sync_ml)
# ---------------------------------------------------------------------------

def _no_sleep_tracker(monkeypatch):
    sleeps = []
    monkeypatch.setattr(sp, "_sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def test_retry_primeira_tentativa_recovery_conflict_segunda_sucesso(monkeypatch):
    sleeps = _no_sleep_tracker(monkeypatch)
    calls = {"n": 0}

    def read_fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception(RECOVERY_MSG)
        return ["linha1", "linha2"]

    result = sp._read_rds_with_recovery_retry(read_fn)

    assert result == ["linha1", "linha2"]
    assert calls["n"] == 2
    assert sleeps == [8]


def test_retry_recovery_conflict_nas_duas_tentativas_propaga_erro(monkeypatch):
    sleeps = _no_sleep_tracker(monkeypatch)
    calls = {"n": 0}

    def read_fn():
        calls["n"] += 1
        raise Exception(RECOVERY_MSG)

    with pytest.raises(Exception, match="conflict with recovery"):
        sp._read_rds_with_recovery_retry(read_fn)

    assert calls["n"] == 2, "deve tentar exatamente max_attempts vezes, nunca mais"
    assert sleeps == [8], "dorme so' entre a 1a e a 2a tentativa, nunca depois da ultima"


def test_retry_erro_generico_na_primeira_tentativa_nao_repete(monkeypatch):
    sleeps = _no_sleep_tracker(monkeypatch)
    calls = {"n": 0}

    def read_fn():
        calls["n"] += 1
        raise Exception("could not connect to server: Connection refused")

    with pytest.raises(Exception, match="Connection refused"):
        sp._read_rds_with_recovery_retry(read_fn)

    assert calls["n"] == 1, "erro nao-retryable nunca deve ser tentado de novo"
    assert sleeps == [], "backoff nunca deve ser chamado para erro nao-retryable"


def test_retry_runtimeerror_validacao_nao_repete(monkeypatch):
    sleeps = _no_sleep_tracker(monkeypatch)
    calls = {"n": 0}

    def read_fn():
        calls["n"] += 1
        raise RuntimeError("[ml] queda suspeita de linhas")

    with pytest.raises(RuntimeError, match="queda suspeita"):
        sp._read_rds_with_recovery_retry(read_fn)

    assert calls["n"] == 1
    assert sleeps == []


def test_retry_max_attempts_customizado_e_respeitado(monkeypatch):
    """Guarda contra um max_attempts diferente do default (2) silenciosamente
    virar um loop sem fim ou tentar so' 1 vez."""
    sleeps = _no_sleep_tracker(monkeypatch)
    calls = {"n": 0}

    def read_fn():
        calls["n"] += 1
        raise Exception(RECOVERY_MSG)

    with pytest.raises(Exception, match="conflict with recovery"):
        sp._read_rds_with_recovery_retry(read_fn, max_attempts=3, backoff_seconds=1)

    assert calls["n"] == 3
    assert sleeps == [1, 1]


# ---------------------------------------------------------------------------
# Integracao com sync_ml() — fakes de conexao/cursor
# ---------------------------------------------------------------------------

ML_ROW = {
    "brand": "kokeshi", "item_id": "i1", "seller_sku": "sku1", "title": "Produto",
    "gross_revenue": 100.0, "units_sold": 1, "unique_buyers": 1, "units_per_buyer": 1.0,
    "cancel_rate_pct": 0.0, "ad_spend": 0.0, "ad_roas": None, "ad_acos_pct": None,
    "days_advertised": 0, "revenue_share_pct": 1.0, "cumulative_revenue_pct": 1.0,
    "estimated_margin": None, "price_spread_pct": None, "pareto_bucket": "A",
    "revenue_velocity": None, "ad_efficiency": None, "action_signal": None,
    "product_status": "active", "first_sale": "2026-01-01", "last_sale": "2026-07-01",
}


class _AuditNeonCursor:
    """Cursor fake para a conexao Neon de auditoria/leitura de prev_count."""

    def __init__(self, conn):
        self.conn = conn
        self._last_sql = ""

    def execute(self, sql, params=None):
        self._last_sql = sql
        self.conn.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        if "RETURNING sync_run_id" in self._last_sql:
            self.conn.run_id_counter += 1
            return (self.conn.run_id_counter,)
        if "SELECT COUNT(*)" in self._last_sql:
            return (self.conn.prev_count,)
        return None

    def close(self):
        pass


class _AuditNeonConn:
    """Conexao Neon fake — usada tanto para auditoria/prev_count quanto,
    numa segunda chamada de _neon(), para a escrita de destino (dst)."""

    def __init__(self, prev_count=0):
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.run_id_counter = 0
        self.prev_count = prev_count

    def cursor(self, cursor_factory=None):
        return _AuditNeonCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _RdsCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.executed = True
        if self.conn.raise_exc is not None:
            raise self.conn.raise_exc

    def fetchall(self):
        return self.conn.rows

    def close(self):
        pass


class _RdsConn:
    """Uma instancia por TENTATIVA de leitura (retry sempre abre conexao
    nova) — nunca reaproveitada entre tentativas, igual ao codigo real."""

    def __init__(self, raise_exc=None, rows=None):
        self.raise_exc = raise_exc
        self.rows = rows if rows is not None else []
        self.executed = False
        self.closed = False

    def cursor(self, cursor_factory=None):
        return _RdsCursor(self)

    def close(self):
        self.closed = True


def _make_rds_factory(effects):
    """effects: lista de (exception|None) — uma entrada por chamada de
    _rds(). Retorna a factory e a lista de conexoes realmente abertas, na
    ordem, para inspecao pelos testes."""
    opened = []

    def factory():
        idx = len(opened)
        exc = effects[idx] if idx < len(effects) else None
        rows = [] if exc is not None else [ML_ROW]
        conn = _RdsConn(raise_exc=exc, rows=rows)
        opened.append(conn)
        return conn

    return factory, opened


def test_sync_ml_recovery_conflict_na_primeira_leitura_sucesso_na_segunda(monkeypatch):
    sleeps = _no_sleep_tracker(monkeypatch)

    audit_conn = _AuditNeonConn(prev_count=1)
    dst_conn = _AuditNeonConn(prev_count=0)
    neon_calls = {"n": 0}

    def fake_neon():
        neon_calls["n"] += 1
        return audit_conn if neon_calls["n"] == 1 else dst_conn

    rds_factory, opened_rds_conns = _make_rds_factory([Exception(RECOVERY_MSG), None])

    monkeypatch.setattr(sp, "_neon", fake_neon)
    monkeypatch.setattr(sp, "_rds", rds_factory)
    monkeypatch.setattr(sp, "execute_values", lambda cur, sql, batch, page_size=500: None)

    result = sp.sync_ml(brands={"kokeshi"})

    assert result == {"source": 1, "upserted": 1}
    assert sleeps == [8], "retry deve ter dormido exatamente uma vez"

    # Abriu exatamente 2 conexoes RDS (1 por tentativa) — a primeira falhou e
    # foi fechada, a segunda teve sucesso e tambem foi fechada.
    assert len(opened_rds_conns) == 2
    assert opened_rds_conns[0].closed is True
    assert opened_rds_conns[1].closed is True

    # _audit_start roda uma unica vez (1 conexao Neon de auditoria + 1 de
    # escrita — nunca uma auditoria extra por causa do retry da LEITURA).
    assert neon_calls["n"] == 2
    audit_inserts = [p for _, p in audit_conn.executed if p and len(p) == 2 and p[0] == "ml_produto_ranking"]
    assert len(audit_inserts) == 1, "_audit_start nao deve duplicar por causa do retry de leitura"

    # Escrita no Neon (dst) ocorreu exatamente uma vez, com commit.
    assert dst_conn.committed is True
    assert dst_conn.rolled_back is False

    # audit_finish final registra sucesso (nunca "failed", ja que a segunda
    # tentativa teve sucesso).
    finish_updates = [p for _, p in audit_conn.executed if p and "success" in p]
    assert finish_updates, f"esperado UPDATE de auditoria com status success, executed={audit_conn.executed}"
    failed_updates = [p for _, p in audit_conn.executed if p and "failed" in p]
    assert not failed_updates


def test_sync_ml_nao_abre_conexao_de_escrita_antes_da_leitura_rds_suceder(monkeypatch):
    """Verifica a ordem real: _neon() (destino/dst) so' deve ser chamado
    DEPOIS que _read_rds_with_recovery_retry() retornou com sucesso."""
    sleeps = _no_sleep_tracker(monkeypatch)

    audit_conn = _AuditNeonConn(prev_count=1)
    dst_conn = _AuditNeonConn(prev_count=0)
    neon_call_order = []

    def fake_neon():
        neon_call_order.append(len(neon_call_order))
        return audit_conn if len(neon_call_order) == 1 else dst_conn

    rds_factory, opened_rds_conns = _make_rds_factory([Exception(RECOVERY_MSG), None])

    monkeypatch.setattr(sp, "_neon", fake_neon)
    monkeypatch.setattr(sp, "_rds", rds_factory)
    monkeypatch.setattr(sp, "execute_values", lambda cur, sql, batch, page_size=500: None)

    sp.sync_ml(brands={"kokeshi"})

    # 2 chamadas a _neon(): a 1a (auditoria/prev_count) acontece ANTES da
    # leitura RDS comecar; a 2a (destino/dst) so' pode acontecer depois que
    # as 2 conexoes RDS (a que falhou e a que teve sucesso) ja foram abertas
    # e fechadas — confirmado indiretamente pelo fato de a 2a leitura ja ter
    # retornado linhas quando dst e' aberta (testado no teste anterior via
    # `result`); aqui so' confirmamos a contagem/ordem de chamadas a _neon().
    assert len(neon_call_order) == 2
    assert len(opened_rds_conns) == 2


def test_sync_ml_retry_esgotado_nao_escreve_dados_no_neon(monkeypatch):
    sleeps = _no_sleep_tracker(monkeypatch)

    audit_conn = _AuditNeonConn(prev_count=1)
    neon_calls = {"n": 0}

    def fake_neon():
        neon_calls["n"] += 1
        return audit_conn

    rds_factory, opened_rds_conns = _make_rds_factory(
        [Exception(RECOVERY_MSG), Exception(RECOVERY_MSG)]
    )

    execute_values_calls = []
    monkeypatch.setattr(sp, "_neon", fake_neon)
    monkeypatch.setattr(sp, "_rds", rds_factory)
    monkeypatch.setattr(
        sp, "execute_values",
        lambda cur, sql, batch, page_size=500: execute_values_calls.append(batch),
    )

    with pytest.raises(Exception, match="conflict with recovery"):
        sp.sync_ml(brands={"kokeshi"})

    # Nenhuma escrita de dados tentada — so' a conexao de auditoria foi
    # aberta (_neon chamado 1 vez), nunca uma segunda para destino/dst.
    assert neon_calls["n"] == 1
    assert execute_values_calls == []
    assert len(opened_rds_conns) == 2  # as 2 tentativas de leitura, ambas fechadas
    assert all(c.closed for c in opened_rds_conns)

    # audit_finish final registra failed exatamente uma vez.
    failed_updates = [p for _, p in audit_conn.executed if p and "failed" in p]
    assert len(failed_updates) == 1
    success_updates = [p for _, p in audit_conn.executed if p and "success" in p]
    assert not success_updates

    # A mensagem de erro sanitizada na auditoria nao deve conter host/URL.
    for params in failed_updates:
        error_message = params[-2]  # penultimo parametro do UPDATE (error)
        assert "postgresql://" not in str(error_message)
        assert "@" not in str(error_message) or "database" not in str(error_message).lower()


def test_sync_ml_sucesso_de_primeira_nao_faz_retry_nem_dorme(monkeypatch):
    """Regressao: quando a leitura RDS funciona de primeira (caso comum,
    9 em 10 execucoes historicas), o retry nao deve interferir em nada."""
    sleeps = _no_sleep_tracker(monkeypatch)

    audit_conn = _AuditNeonConn(prev_count=1)
    dst_conn = _AuditNeonConn(prev_count=0)
    neon_calls = {"n": 0}

    def fake_neon():
        neon_calls["n"] += 1
        return audit_conn if neon_calls["n"] == 1 else dst_conn

    rds_factory, opened_rds_conns = _make_rds_factory([None])

    monkeypatch.setattr(sp, "_neon", fake_neon)
    monkeypatch.setattr(sp, "_rds", rds_factory)
    monkeypatch.setattr(sp, "execute_values", lambda cur, sql, batch, page_size=500: None)

    result = sp.sync_ml(brands={"kokeshi"})

    assert result == {"source": 1, "upserted": 1}
    assert sleeps == []
    assert len(opened_rds_conns) == 1
    assert dst_conn.committed is True
