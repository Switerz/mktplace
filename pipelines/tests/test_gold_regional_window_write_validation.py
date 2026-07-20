"""
Testes de `validate_shopee_window_write_path` / `--validate-shopee-window-write-path`
— Gate S4.3a.

Objetivo desta função: exercitar o MESMO caminho de escrita do refresh real
(secret dedicado, preflight, advisory lock, table lock, staging TEMP,
validações estruturais, reconciliação Gold x fonte, fingerprint fora do
escopo) mas ser ESTRUTURALMENTE incapaz de persistir qualquer dado — nunca
publica backup, nunca executa DELETE/INSERT na Gold, nunca chama commit.
A transação SEMPRE termina em ROLLBACK, inclusive quando a janela está
divergente (`would_change_data=True`): a função só REPORTA a divergência,
nunca avança para backup/refresh.

Usa conexões/cursores psycopg2 falsos (respostas por SUBSTRING do SQL,
mesmo padrão de test_gold_regional_window_refresh.py). Nenhum banco real é
tocado.
"""
from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

from pipelines.ingestion.gold_regional import loader


# ---------------------------------------------------------------------------
# Fakes (mesmo padrão de test_gold_regional_window_refresh.py)
# ---------------------------------------------------------------------------

class _Seq:
    """Resposta que muda a cada chamada REPETIDA da MESMA query (o
    fingerprint fora do escopo é executado 2x: antes e depois do
    staging/key-diff). Um valor comum fora de `_Seq` é devolvido sempre
    igual, em qualquer número de chamadas."""

    def __init__(self, *values):
        self.values = values


def _contains(*subs):
    return lambda upper: all(s in upper for s in subs)


def _exact(pattern):
    return lambda upper: upper == pattern


_D_FROM = date(2026, 6, 1)
_D_TO = date(2026, 6, 30)

_ZERO_FINGERPRINT = (0, Decimal("0"), Decimal("0"), 0, 0, 0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), 0, 0, 0, 0)


def _happy_responses(
    staging_agg=(1, Decimal("520.00"), 11),
    dup=(0,), nulls=(0,), bad=(0,), nan_neg=(0,), out_of_scope_staging=(0,),
    gold_agg=(1, Decimal("500.00"), 10),
    key_diff=(0, 0, 0),
    fingerprint=None,
):
    if fingerprint is None:
        fingerprint = _Seq(_ZERO_FINGERPRINT, _ZERO_FINGERPRINT)

    return [
        (_contains("NOT (MARKETPLACE_ID = %(SHOPEE_MARKETPLACE_ID)S AND DATE BETWEEN"), fingerprint),
        (_exact("SELECT COUNT(*), COALESCE(SUM(GMV), 0), COALESCE(SUM(ORDERS), 0) FROM STG_MARKETPLACE_REGION_DAILY"), staging_agg),
        (_contains("HAVING COUNT(*) > 1"), dup),
        (_contains("DATE IS NULL OR MARKETPLACE_ID IS NULL"), nulls),
        (_contains("UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS"), bad),
        (_contains("GMV = 'NAN'"), nan_neg),
        (_contains("MARKETPLACE_ID <> %(SHOPEE_MARKETPLACE_ID)S"), out_of_scope_staging),
        (_contains("COALESCE(SUM(ORDERS), 0) FROM GOLD.MARKETPLACE_REGION_DAILY WHERE MARKETPLACE_ID ="), gold_agg),
        (_contains("FULL OUTER JOIN", "GOLD_WINDOW"), key_diff),
    ]


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.conn.executed.append((norm, params))
        if self.conn.fail_on_substring and self.conn.fail_on_substring in norm.upper():
            raise RuntimeError("falha simulada de execução")

    def _dispatch(self):
        norm, _params = self.conn.executed[-1]
        upper = norm.upper()
        if "PG_TRY_ADVISORY_LOCK" in upper:
            return (self.conn.lock_acquired,)
        if "PG_ADVISORY_UNLOCK" in upper:
            return (True,)
        for matcher, value in self.conn.responses:
            if matcher(upper):
                if isinstance(value, _Seq):
                    idx = self.conn._call_index.get(id(value), 0)
                    self.conn._call_index[id(value)] = idx + 1
                    return value.values[min(idx, len(value.values) - 1)]
                return value
        raise AssertionError(f"nenhuma resposta simulada para a query: {norm!r}")

    def fetchone(self):
        return self._dispatch()

    def fetchall(self):
        return self._dispatch()

    @property
    def rowcount(self):
        return 0


class FakeConn:
    def __init__(self, responses=None, lock_acquired=True, fail_on_substring=None):
        self.executed: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.autocommit = None
        self.lock_acquired = lock_acquired
        self.fail_on_substring = fail_on_substring
        self.responses = responses if responses is not None else _happy_responses()
        self._call_index: dict[int, int] = {}

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakePsycopg2Module:
    def __init__(self, conn):
        self._conn = conn
        self.connect_calls = 0

    def connect(self, url, connect_timeout=15):
        self.connect_calls += 1
        return self._conn


def _happy_conn(**overrides):
    return FakeConn(responses=_happy_responses(), **overrides)


# ---------------------------------------------------------------------------
# Janela inválida — bloqueia ANTES de qualquer conexão
# ---------------------------------------------------------------------------

def test_validate_janela_invalida_bloqueia_antes_de_conectar(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("não deveria conectar com janela inválida")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_TO, _D_FROM)  # invertida
    assert result.outcome == "blocked"
    assert "posterior" in result.problems[0]


# ---------------------------------------------------------------------------
# Advisory lock ocupado — sem tentativa automática
# ---------------------------------------------------------------------------

def test_validate_bloqueia_se_advisory_lock_em_uso(monkeypatch):
    fake_conn = FakeConn(lock_acquired=False)
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "blocked"
    assert any("advisory lock" in p for p in result.problems)
    assert fake_conn.closed is True
    assert not any("CREATE TEMP TABLE" in s.upper() for s, _ in fake_conn.executed)


def test_validate_fecha_conexao_mesmo_se_lock_acquire_falhar(monkeypatch):
    fake_conn = FakeConn()

    def boom_lock(conn):
        raise RuntimeError("falha simulada ao adquirir lock")

    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    monkeypatch.setattr(loader, "try_acquire_advisory_lock", boom_lock)

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "failed"
    assert fake_conn.closed is True
    assert fake_conn.rolled_back is True
    assert not any("PG_ADVISORY_UNLOCK" in s.upper() for s, _ in fake_conn.executed)


# ---------------------------------------------------------------------------
# connect()/autocommit protegidos
# ---------------------------------------------------------------------------

def test_validate_connect_falha_retorna_failed_sanitizado(monkeypatch):
    def boom_connect(url, connect_timeout=15):
        raise RuntimeError(
            'connection to server at "prod-db.example.rds.amazonaws.com" '
            '(10.0.0.5), port 5432 failed: FATAL: password authentication failed for user "postgres"'
        )

    class BoomModule:
        def connect(self, url, connect_timeout=15):
            return boom_connect(url, connect_timeout)

    monkeypatch.setattr(loader, "psycopg2", BoomModule())

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "failed"
    combined = " ".join(result.problems)
    assert "prod-db.example.rds.amazonaws.com" not in combined
    assert "10.0.0.5" not in combined
    assert "postgres" not in combined


def test_validate_autocommit_assignment_falha_fecha_conexao(monkeypatch):
    class ExplodingAutocommitConn(FakeConn):
        def __init__(self, **kw):
            self._autocommit_value = None
            self._armed = False
            super().__init__(**kw)
            self._armed = True

        @property
        def autocommit(self):
            return self._autocommit_value

        @autocommit.setter
        def autocommit(self, value):
            if self._armed:
                raise RuntimeError("falha simulada ao configurar autocommit")
            self._autocommit_value = value

    fake_conn = ExplodingAutocommitConn(responses=_happy_responses())
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "failed"
    assert fake_conn.closed is True


def test_validate_query_falhando_retorna_failed_sanitizado(monkeypatch):
    fake_conn = FakeConn(fail_on_substring="CREATE TEMP TABLE")
    fake_module = FakePsycopg2Module(fake_conn)
    monkeypatch.setattr(loader, "psycopg2", fake_module)

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "failed"
    assert fake_conn.rolled_back is True
    assert fake_module.connect_calls == 1  # sem retry automático


# ---------------------------------------------------------------------------
# Ordem exata: lock -> table lock -> staging -> validações -> diff ->
# fingerprint -> rollback -> unlock -> close
# ---------------------------------------------------------------------------

def test_validate_ordem_operacoes_caminho_reconciliado(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    order = [s.upper() for s, _ in fake_conn.executed]

    def idx(pred):
        return next(i for i, s in enumerate(order) if pred(s))

    i_lock = idx(lambda s: "PG_TRY_ADVISORY_LOCK" in s)
    i_table_lock = idx(lambda s: "SHARE ROW EXCLUSIVE MODE" in s)
    i_fp_before = idx(lambda s: "NOT (MARKETPLACE_ID" in s)
    i_staging_create = idx(lambda s: "CREATE TEMP TABLE" in s)
    i_staging_insert = idx(lambda s: s.startswith("INSERT INTO STG_MARKETPLACE_REGION_DAILY"))
    i_dup = idx(lambda s: "HAVING COUNT(*) > 1" in s)
    i_key_diff = idx(lambda s: "FULL OUTER JOIN" in s)
    i_unlock = idx(lambda s: "PG_ADVISORY_UNLOCK" in s)

    assert i_lock < i_table_lock < i_fp_before < i_staging_create < i_staging_insert
    assert i_staging_insert < i_dup < i_key_diff < i_unlock

    # fingerprint reconferido DEPOIS do key-diff (2ª ocorrência do mesmo padrão)
    fp_indices = [i for i, s in enumerate(order) if "NOT (MARKETPLACE_ID" in s]
    assert len(fp_indices) == 2
    assert fp_indices[0] < i_key_diff < fp_indices[1] < i_unlock

    assert result.outcome == "validated"
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False
    assert fake_conn.closed is True


# ---------------------------------------------------------------------------
# Caminho reconciliado vs. divergente — zero escrita persistente nos dois
# ---------------------------------------------------------------------------

def test_validate_caminho_reconciliado_would_change_false(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(key_diff=(0, 0, 0)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "validated"
    assert result.would_change_data is False
    assert result.structurally_safe_for_refresh is True
    assert fake_conn.committed is False
    assert fake_conn.rolled_back is True
    assert not any(s.upper().startswith("DELETE FROM") for s, _ in fake_conn.executed)
    assert not any(s.upper().startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY") for s, _ in fake_conn.executed)


def test_validate_caminho_divergente_gold_only_continua_sem_escrita(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(key_diff=(1, 0, 0)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "validated"
    assert result.would_change_data is True
    assert result.gold_only_key_count == 1
    assert fake_conn.committed is False
    assert fake_conn.rolled_back is True
    assert not any(s.upper().startswith("DELETE FROM") for s, _ in fake_conn.executed)
    assert not any(s.upper().startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY") for s, _ in fake_conn.executed)


def test_validate_caminho_divergente_source_only_continua_sem_escrita(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(key_diff=(0, 1, 0)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "validated"
    assert result.would_change_data is True
    assert result.source_only_key_count == 1
    assert fake_conn.committed is False
    assert fake_conn.rolled_back is True


def test_validate_caminho_divergente_changed_continua_sem_escrita(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(key_diff=(0, 0, 1)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "validated"
    assert result.would_change_data is True
    assert result.changed_key_count == 1
    assert fake_conn.committed is False
    assert fake_conn.rolled_back is True


def test_validate_would_change_true_nunca_chama_refresh(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(key_diff=(1, 1, 1)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    def boom_refresh(*a, **k):
        raise AssertionError("validate_shopee_window_write_path nunca deveria chamar execute_shopee_window_refresh")
    monkeypatch.setattr(loader, "execute_shopee_window_refresh", boom_refresh)

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "validated"
    assert result.would_change_data is True


# ---------------------------------------------------------------------------
# Cada validação estrutural bloqueia isoladamente, sem key-diff/escrita
# ---------------------------------------------------------------------------

def test_validate_bloqueia_duplicidade_no_staging(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(dup=(2,)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)
    assert result.outcome == "blocked"
    assert any("duplicada" in p for p in result.problems)
    assert fake_conn.rolled_back is True
    assert not any("FULL OUTER JOIN" in s.upper() for s, _ in fake_conn.executed)


def test_validate_bloqueia_nulos_no_staging(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(nulls=(3,)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)
    assert result.outcome == "blocked"
    assert any("nula" in p for p in result.problems)


def test_validate_bloqueia_numerador_maior_que_denominador(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(bad=(1,)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)
    assert result.outcome == "blocked"
    assert any("numerador > denominador" in p for p in result.problems)


def test_validate_bloqueia_nan_ou_negativo_no_staging(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(nan_neg=(1,)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)
    assert result.outcome == "blocked"
    assert any("NaN" in p for p in result.problems)


def test_validate_bloqueia_linha_fora_do_escopo_no_staging(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(out_of_scope_staging=(1,)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)
    assert result.outcome == "blocked"
    assert any("fora do escopo" in p for p in result.problems)


def test_validate_bloqueia_zero_source_risk(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(staging_agg=(0, Decimal("0"), 0), gold_agg=(1, Decimal("500.00"), 10)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)
    assert result.outcome == "blocked"
    assert any("ZERO linhas" in p for p in result.problems)


# ---------------------------------------------------------------------------
# Fingerprint fora do escopo mudou -- falha, mesmo sem nunca ter escrito
# ---------------------------------------------------------------------------

def test_validate_fingerprint_fora_do_escopo_alterado_falha(monkeypatch):
    changed_fp = (1, Decimal("999.00"), 5, 0, 0, 0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), 0, 0, 0, 0)
    fake_conn = FakeConn(responses=_happy_responses(fingerprint=_Seq(_ZERO_FINGERPRINT, changed_fp)))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)
    assert result.outcome == "failed"
    assert any("fora do escopo" in p for p in result.problems)
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


# ---------------------------------------------------------------------------
# Rollback obrigatório no sucesso; falha do rollback nunca reporta "validated"
# ---------------------------------------------------------------------------

def test_validate_rollback_chamado_no_sucesso(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)
    assert result.outcome == "validated"
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


def test_validate_rollback_falhando_no_sucesso_nao_retorna_validated(monkeypatch):
    fake_conn = _happy_conn()

    def boom_rollback():
        raise RuntimeError("connection reset by peer postgresql://u:p@h/db")
    fake_conn.rollback = boom_rollback
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome != "validated"
    assert result.outcome == "failed"
    combined = " ".join(result.problems)
    assert "u:p@h" not in combined
    assert fake_conn.committed is False


def test_validate_release_lock_falha_nao_sugere_retry(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    def boom_release(conn):
        raise RuntimeError("connection lost postgresql://u:p@h/db")
    monkeypatch.setattr(loader, "release_advisory_lock", boom_release)

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "validated"
    assert any("advisory lock" in w for w in result.warnings)
    assert "retry" not in " ".join(result.warnings).lower() or "sem retry" in " ".join(result.warnings).lower()
    assert "u:p@h" not in " ".join(result.warnings)
    assert fake_conn.closed is True


def test_validate_close_falha_nao_sugere_retry(monkeypatch):
    fake_conn = _happy_conn()

    def boom_close():
        raise RuntimeError("close failed")
    fake_conn.close = boom_close
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    assert result.outcome == "validated"
    assert any("fechar" in w for w in result.warnings)
    assert any("sem retry" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Nunca commit, nunca backup, nunca DELETE/INSERT persistente; INSERT só na TEMP
# ---------------------------------------------------------------------------

def test_validate_nunca_chama_commit(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)
    assert fake_conn.committed is False


def test_validate_nunca_chama_backup(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    def boom_backup(*a, **k):
        raise AssertionError("validate_shopee_window_write_path nunca deveria publicar backup")
    monkeypatch.setattr(loader, "_write_window_backup_atomic", boom_backup)

    result = loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)
    assert result.outcome == "validated"


def test_validate_nenhum_delete_ou_insert_persistente(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(key_diff=(1, 1, 1)))  # divergente -- maior risco de "avançar"
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))
    loader.validate_shopee_window_write_path("postgresql://writer@host/db", _D_FROM, _D_TO)

    for sql, _params in fake_conn.executed:
        upper = sql.upper()
        assert not upper.startswith("DELETE FROM")
        if upper.startswith("INSERT INTO"):
            assert upper.startswith("INSERT INTO STG_MARKETPLACE_REGION_DAILY"), (
                f"INSERT fora da staging TEMP detectado: {sql!r}"
            )


# ---------------------------------------------------------------------------
# Regressão estática — símbolos proibidos nunca aparecem no source
# ---------------------------------------------------------------------------

_FORBIDDEN_SYMBOLS = (
    "SQL_REFRESH_DELETE",
    "SQL_INSERT_FINAL",
    "SQL_RESTORE_INSERT_ROW",
    "_write_window_backup_atomic",
    "execute_shopee_window_refresh",
    "execute_shopee_window_restore",
    "conn.commit(",
)


def test_regressao_estatica_funcao_validacao_sem_simbolos_proibidos():
    source = inspect.getsource(loader.validate_shopee_window_write_path)
    for forbidden in _FORBIDDEN_SYMBOLS:
        assert forbidden not in source, f"símbolo proibido {forbidden!r} encontrado em validate_shopee_window_write_path"


def test_regressao_estatica_cli_validacao_sem_simbolos_proibidos():
    source = inspect.getsource(loader.run_validate_shopee_window_write_path_cli)
    for forbidden in _FORBIDDEN_SYMBOLS:
        assert forbidden not in source, f"símbolo proibido {forbidden!r} encontrado em run_validate_shopee_window_write_path_cli"


# ---------------------------------------------------------------------------
# CLI: valida janela ANTES de secret/preflight (PoisonConn)
# ---------------------------------------------------------------------------

class PoisonConnect:
    def __init__(self):
        self.called = False

    def __call__(self, *a, **k):
        self.called = True
        raise RuntimeError("PoisonConnect: não deveria conectar nesta validação")


class PoisonWindowWriteConn:
    def __init__(self):
        self.called = False

    def __call__(self, *a, **k):
        self.called = True
        raise AssertionError("não deveria ler o secret nesta validação")


def test_run_validate_cli_janela_invalida_nao_le_secret_nem_conecta(monkeypatch):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    poison_connect = PoisonConnect()
    poison_secret = PoisonWindowWriteConn()
    monkeypatch.setattr(loader.psycopg2, "connect", poison_connect)
    monkeypatch.setattr(loader.window_write_conn, "load_window_write_secret", poison_secret)

    rc = loader.run_validate_shopee_window_write_path_cli(_D_TO, _D_FROM)  # invertida

    assert rc == 2
    assert poison_connect.called is False
    assert poison_secret.called is False


def test_run_validate_cli_sem_datamart_database_url_nao_le_secret_nem_conecta(monkeypatch):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "")
    monkeypatch.setattr(loader.settings, "datamart_host", "")
    monkeypatch.setattr(loader.settings, "datamart_db", "")
    poison_connect = PoisonConnect()
    poison_secret = PoisonWindowWriteConn()
    monkeypatch.setattr(loader.psycopg2, "connect", poison_connect)
    monkeypatch.setattr(loader.window_write_conn, "load_window_write_secret", poison_secret)

    rc = loader.run_validate_shopee_window_write_path_cli(_D_FROM, _D_TO)

    assert rc == 2
    assert poison_connect.called is False
    assert poison_secret.called is False


def test_run_validate_cli_preflight_bloqueado_impede_a_funcao(monkeypatch):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    monkeypatch.setattr(
        loader.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": "postgresql://writer@host/db", "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(
        loader.window_write_conn, "validate_window_write_guardrails",
        lambda secret, read_url: "postgresql://writer@host/db",
    )

    class BlockedPreflightReport:
        ok = False
        warnings = []
        blocking_reasons = ["rolsuper=true"]
        safe_summary = {}

    monkeypatch.setattr(loader.window_write_conn, "run_window_preflight", lambda *a, **k: BlockedPreflightReport())

    def boom_validate(*a, **k):
        raise AssertionError("validate_shopee_window_write_path nunca deveria rodar com preflight bloqueado")
    monkeypatch.setattr(loader, "validate_shopee_window_write_path", boom_validate)

    rc = loader.run_validate_shopee_window_write_path_cli(_D_FROM, _D_TO)

    assert rc == 2


def test_run_validate_cli_barreira_se_preflight_levantar(monkeypatch, capsys):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    monkeypatch.setattr(
        loader.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": "postgresql://writer@host/db", "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(
        loader.window_write_conn, "validate_window_write_guardrails",
        lambda secret, read_url: "postgresql://writer@host/db",
    )

    def boom_preflight(*a, **k):
        raise RuntimeError(
            'connection to server at "prod-db.example.rds.amazonaws.com" '
            '(10.0.0.5), port 5432 failed for user "postgres"'
        )
    monkeypatch.setattr(loader.window_write_conn, "run_window_preflight", boom_preflight)

    def boom_validate(*a, **k):
        raise AssertionError("validação nunca deveria rodar se o preflight levantou")
    monkeypatch.setattr(loader, "validate_shopee_window_write_path", boom_validate)

    rc = loader.run_validate_shopee_window_write_path_cli(_D_FROM, _D_TO)

    assert rc == 2
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "prod-db.example.rds.amazonaws.com" not in combined
    assert "10.0.0.5" not in combined
    assert "postgres" not in combined
    assert "Traceback" not in combined


# ---------------------------------------------------------------------------
# CLI: saída sanitizada -- só contagens/booleans, nunca host/IP/URL/usuário
# ---------------------------------------------------------------------------

def test_run_validate_cli_sucesso_saida_sanitizada(monkeypatch, capsys):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")

    class HappyPreflightReport:
        ok = True
        warnings = []
        blocking_reasons = []
        safe_summary = {"pg_is_in_recovery": False}

    monkeypatch.setattr(
        loader.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": "postgresql://writer:s3cr3t@prod-db.internal:5432/datamart", "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(
        loader.window_write_conn, "validate_window_write_guardrails",
        lambda secret, read_url: "postgresql://writer:s3cr3t@prod-db.internal:5432/datamart",
    )
    monkeypatch.setattr(loader.window_write_conn, "run_window_preflight", lambda *a, **k: HappyPreflightReport())

    fake_result = loader.ShopeeWindowWriteValidationResult(
        outcome="validated", staging_rows=80, gold_rows=80,
        gold_only_key_count=0, source_only_key_count=0, changed_key_count=0,
        structurally_safe_for_refresh=True, would_change_data=False,
    )
    monkeypatch.setattr(loader, "validate_shopee_window_write_path", lambda *a, **k: fake_result)

    rc = loader.run_validate_shopee_window_write_path_cli(_D_FROM, _D_TO)

    assert rc == 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "prod-db.internal" not in combined
    assert "s3cr3t" not in combined
    assert "writer:s3cr3t" not in combined
    assert "staging_rows=80" in combined
    assert "would_change_data=False" in combined


def test_run_validate_cli_nunca_imprime_order_id_cpf_filename(monkeypatch, capsys):
    """A saída da CLI só contém contagens/booleans -- nunca pode conter
    marcadores de linha individual (order_id/CPF/filename), mesmo que
    problems/warnings citem contagens."""
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")

    class HappyPreflightReport:
        ok = True
        warnings = []
        blocking_reasons = []
        safe_summary = {}

    monkeypatch.setattr(
        loader.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": "postgresql://writer@host/db", "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(loader.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: "postgresql://writer@host/db")
    monkeypatch.setattr(loader.window_write_conn, "run_window_preflight", lambda *a, **k: HappyPreflightReport())

    fake_result = loader.ShopeeWindowWriteValidationResult(
        outcome="blocked", staging_rows=0, gold_rows=10,
        problems=["3 combinação(ões) de chave duplicada(s) no staging"],
    )
    monkeypatch.setattr(loader, "validate_shopee_window_write_path", lambda *a, **k: fake_result)

    rc = loader.run_validate_shopee_window_write_path_cli(_D_FROM, _D_TO)

    assert rc == 3
    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()
    for forbidden in ("order_id", "cpf", "filename", "host=", "user=", "password"):
        assert forbidden not in combined
