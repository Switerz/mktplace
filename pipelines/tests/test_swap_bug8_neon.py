"""
Testes de pipelines/reconciliation/swap_bug8_neon.py (Gate 4B): swap da
tabela real no Neon a partir do backup/staging fixos ja auditados no Gate
4A.2.

Usa conexoes psycopg2 falsas — nenhum banco real e' tocado, nenhuma
credencial real e' necessaria. FakeNeonConn simula o efeito do TRUNCATE+
INSERT trocando os agregados retornados para a tabela real (antes =
espelha o backup; depois = espelha a staging), sem manter linhas de fato.

Ordem real de operacoes em do_swap_neon:
  1. LOCK TABLE ... IN ACCESS EXCLUSIVE MODE
  2. preflight completo SOB o lock: existencia dos 3 objetos, real==backup
     Neon (agregados + EXCEPT bidirecional), staging valida (agregados
     esperados, duplicatas, nulos), ausencia de foreign keys de terceiros
  3. TRUNCATE so' da tabela real (sem CASCADE/RESTART IDENTITY)
  4. INSERT com as 13 colunas de negocio explicitas a partir da staging
  5. EXCEPT bidirecional real vs. staging (pos-INSERT) + agregados
  6. COMMIT (ou ROLLBACK em qualquer excecao de 2 a 5)
"""
import re
import types
from pathlib import Path

import pytest

import pipelines.reconciliation.swap_bug8_neon as swap

MODULE_PATH = Path(swap.__file__)

REAL = swap.REAL_TABLE
BACKUP = swap.BACKUP_NEON_NAME
STAGING = swap.STAGING_NEON_NAME

HAPPY_BACKUP_AGG = {"n": 2, "gmv": 100.0, "units_sold": 8, "completed_orders": 6, "canceled_orders": 1}
HAPPY_STAGING_AGG = {"n": 2, "gmv": 150.0, "units_sold": 10, "completed_orders": 8, "canceled_orders": 2}


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._last_sql = ""
        self._last_params = None

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.conn.executed.append((norm, params))
        self._last_sql = norm
        self._last_params = params
        if self.conn.raise_on_substring and self.conn.raise_on_substring in norm:
            raise RuntimeError("falha simulada de execucao")
        if norm.upper().startswith("INSERT INTO") and f"marts.{REAL}" in norm:
            self.conn.inserted = True

    def fetchone(self):
        sql = self._last_sql
        if "information_schema.tables" in sql:
            name = self._last_params[0]
            return {"exists": 1} if name in self.conn.existing_tables else None
        if "EXCEPT" in sql:
            tables = re.findall(r"marts\.(\w+)", sql)
            return {"n": self.conn.except_counts.get((tables[0], tables[1]), 0)}
        if "SUM(gmv)" in sql:
            tables = re.findall(r"marts\.(\w+)", sql)
            table = tables[0]
            if table == REAL:
                return dict(self.conn.real_agg_after if self.conn.inserted else self.conn.real_agg_before)
            if table == BACKUP:
                return dict(self.conn.backup_agg)
            if table == STAGING:
                return dict(self.conn.staging_agg)
            return dict(HAPPY_STAGING_AGG)
        if "HAVING COUNT(*) > 1" in sql:
            tables = re.findall(r"marts\.(\w+)", sql)
            return {"n": self.conn.dup_null.get(tables[0], (0, 0))[0]}
        if "ref_month IS NULL OR brand IS NULL" in sql:
            tables = re.findall(r"marts\.(\w+)", sql)
            return {"n": self.conn.dup_null.get(tables[0], (0, 0))[1]}
        return None

    def fetchall(self):
        if "FOREIGN KEY" in self._last_sql:
            return self.conn.foreign_keys
        return []

    def close(self):
        pass


class FakeNeonConn:
    def __init__(self, existing_tables=None, except_counts=None, real_agg_before=None,
                 real_agg_after=None, backup_agg=None, staging_agg=None, dup_null=None,
                 foreign_keys=None, raise_on_substring=None):
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.inserted = False
        self.existing_tables = existing_tables if existing_tables is not None else {REAL, BACKUP, STAGING}
        self.except_counts = except_counts if except_counts is not None else {
            (REAL, BACKUP): 0, (BACKUP, REAL): 0, (REAL, STAGING): 0, (STAGING, REAL): 0,
        }
        self.real_agg_before = real_agg_before if real_agg_before is not None else HAPPY_BACKUP_AGG
        self.real_agg_after = real_agg_after if real_agg_after is not None else HAPPY_STAGING_AGG
        self.backup_agg = backup_agg if backup_agg is not None else HAPPY_BACKUP_AGG
        self.staging_agg = staging_agg if staging_agg is not None else HAPPY_STAGING_AGG
        self.dup_null = dup_null or {}
        self.foreign_keys = foreign_keys or []
        self.raise_on_substring = raise_on_substring

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _patch_expected(monkeypatch):
    monkeypatch.setattr(swap, "EXPECTED_STAGING_ROWS", HAPPY_STAGING_AGG["n"])
    monkeypatch.setattr(swap, "EXPECTED_STAGING_GMV", HAPPY_STAGING_AGG["gmv"])
    monkeypatch.setattr(swap, "EXPECTED_STAGING_CANCELED", HAPPY_STAGING_AGG["canceled_orders"])


def _happy_conn(**overrides):
    return FakeNeonConn(**overrides)


# ---------------------------------------------------------------------------
# Caminho feliz / ordem das operacoes
# ---------------------------------------------------------------------------

def test_caminho_feliz_faz_swap_e_commit_uma_vez():
    neon = _happy_conn()
    result = swap.do_swap_neon(neon)

    assert result["backup_table"] == BACKUP
    assert result["staging_table"] == STAGING
    assert result["real_agg_after"] == HAPPY_STAGING_AGG
    assert neon.committed is True
    assert neon.rolled_back is False


def test_ordem_lock_revalidacao_truncate_insert_validacao_commit():
    neon = _happy_conn()
    swap.do_swap_neon(neon)

    kinds = [sql.split()[0].upper() for sql, _ in neon.executed if sql.split()]
    lock_idx = kinds.index("LOCK")
    truncate_idx = kinds.index("TRUNCATE")
    insert_idx = kinds.index("INSERT")

    assert lock_idx < truncate_idx < insert_idx
    # entre o LOCK e o TRUNCATE, houve pelo menos uma consulta de
    # revalidacao (SELECT) — preflight completo sob o lock
    assert any(k == "SELECT" for k in kinds[lock_idx + 1:truncate_idx])
    # apos o INSERT, houve mais SELECTs (EXCEPT + agregados pos-insert)
    assert any(k == "SELECT" for k in kinds[insert_idx + 1:])
    assert neon.committed is True


def test_timeouts_locais_sao_definidos_antes_do_access_exclusive_lock():
    neon = _happy_conn()
    swap.do_swap_neon(neon)

    stmts = [sql for sql, _ in neon.executed]
    lock_timeout_idx = next(i for i, s in enumerate(stmts) if "lock_timeout" in s)
    statement_timeout_idx = next(i for i, s in enumerate(stmts) if "statement_timeout" in s)
    lock_idx = next(i for i, s in enumerate(stmts) if s.upper().startswith("LOCK TABLE"))

    assert "SET LOCAL" in stmts[lock_timeout_idx].upper()
    assert "SET LOCAL" in stmts[statement_timeout_idx].upper()
    assert lock_timeout_idx < lock_idx
    assert statement_timeout_idx < lock_idx


def test_timeout_ao_adquirir_o_lock_causa_rollback_sem_truncate_ou_insert():
    """Simula o Postgres cancelando o LOCK TABLE por lock_timeout — o
    mesmo caminho de excecao generico de do_swap_neon deve fazer rollback
    e propagar, sem NUNCA alcancar TRUNCATE/INSERT."""
    neon = FakeNeonConn(raise_on_substring="LOCK TABLE")
    with pytest.raises(RuntimeError, match="falha simulada"):
        swap.do_swap_neon(neon)

    assert neon.rolled_back is True
    assert neon.committed is False
    assert not any(sql.upper().startswith("TRUNCATE") for sql, _ in neon.executed)
    assert not any(sql.upper().startswith("INSERT") for sql, _ in neon.executed)
    # os timeouts ainda foram configurados antes da tentativa de lock falhar
    assert any("lock_timeout" in sql for sql, _ in neon.executed)
    assert any("statement_timeout" in sql for sql, _ in neon.executed)


def test_nenhuma_nova_tentativa_automatica_apos_timeout_no_lock():
    """do_swap_neon nao deve tentar o LOCK TABLE mais de uma vez, mesmo
    apos uma falha — sem retry embutido."""
    neon = FakeNeonConn(raise_on_substring="LOCK TABLE")
    with pytest.raises(RuntimeError):
        swap.do_swap_neon(neon)

    lock_attempts = [sql for sql, _ in neon.executed if sql.upper().startswith("LOCK TABLE")]
    assert len(lock_attempts) == 1, f"esperada exatamente 1 tentativa de LOCK, encontrada {len(lock_attempts)}"


def test_erro_de_timeout_no_main_e_reportado_sanitizado(monkeypatch, capsys):
    """Verifica a camada externa (main -> run_swap_neon): mesmo uma
    excecao que NAO seja RuntimeError (como um erro de timeout do driver)
    e' capturada, revertida a montante, e reportada sem credenciais."""
    class FakeLockTimeoutError(Exception):
        pass

    def fake_run_swap_neon(args):
        raise FakeLockTimeoutError(
            "canceling statement due to lock timeout; dsn=postgresql://user:S3nhaSecreta@ep-fake.neon.tech/db"
        )

    monkeypatch.setattr(swap, "run_swap_neon", fake_run_swap_neon)
    monkeypatch.setattr(swap.sys, "argv", ["swap_bug8_neon.py", "--swap-neon"])

    exit_code = swap.main()

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "S3nhaSecreta" not in out
    assert "user:" not in out
    assert "sem nova tentativa" in out


def test_truncate_ocorre_apenas_uma_vez_so_na_tabela_real_sem_cascade():
    neon = _happy_conn()
    swap.do_swap_neon(neon)

    truncates = [sql for sql, _ in neon.executed if sql.upper().startswith("TRUNCATE")]
    assert len(truncates) == 1
    assert f"marts.{REAL}" in truncates[0]
    assert BACKUP not in truncates[0]
    assert STAGING not in truncates[0]
    assert "CASCADE" not in truncates[0].upper()
    assert "RESTART IDENTITY" not in truncates[0].upper()


def test_insert_usa_lista_explicita_de_colunas_a_partir_da_staging():
    neon = _happy_conn()
    swap.do_swap_neon(neon)

    insert_sql = next(sql for sql, _ in neon.executed if sql.strip().upper().startswith("INSERT INTO"))
    assert "SELECT *" not in insert_sql
    assert f"marts.{REAL}" in insert_sql
    assert f"marts.{STAGING}" in insert_sql
    for col in swap.BUSINESS_COLUMNS:
        assert col in insert_sql, f"coluna {col!r} ausente do INSERT"


# ---------------------------------------------------------------------------
# Drift bloqueia
# ---------------------------------------------------------------------------

def test_drift_agregado_real_vs_backup_bloqueia_antes_do_truncate():
    neon = FakeNeonConn(real_agg_before={**HAPPY_BACKUP_AGG, "gmv": 1.0})
    with pytest.raises(swap.SwapPreflightError, match="diverge do backup Neon"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert neon.committed is False
    assert not any(sql.upper().startswith("TRUNCATE") for sql, _ in neon.executed)


def test_drift_except_real_vs_backup_bloqueia_antes_do_truncate():
    neon = FakeNeonConn(except_counts={(REAL, BACKUP): 1, (BACKUP, REAL): 0, (REAL, STAGING): 0, (STAGING, REAL): 0})
    with pytest.raises(swap.SwapPreflightError, match="EXCEPT nao-zero"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert not any(sql.upper().startswith("TRUNCATE") for sql, _ in neon.executed)


# ---------------------------------------------------------------------------
# FK bloqueia
# ---------------------------------------------------------------------------

def test_foreign_key_de_terceiros_bloqueia_antes_do_truncate():
    neon = FakeNeonConn(foreign_keys=[
        {"ref_schema": "raw", "ref_table": "algum_pedido", "ref_column": "shopee_product_id"},
    ])
    with pytest.raises(swap.SwapPreflightError, match="foreign key"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert neon.committed is False
    assert not any(sql.upper().startswith("TRUNCATE") for sql, _ in neon.executed)


def test_sem_foreign_key_nao_bloqueia():
    neon = FakeNeonConn(foreign_keys=[])
    result = swap.do_swap_neon(neon)
    assert neon.committed is True
    assert result["real_agg_after"] == HAPPY_STAGING_AGG


# ---------------------------------------------------------------------------
# Staging invalida bloqueia
# ---------------------------------------------------------------------------

def test_staging_com_numero_de_linhas_errado_bloqueia():
    neon = FakeNeonConn(staging_agg={**HAPPY_STAGING_AGG, "n": 9999})
    with pytest.raises(swap.SwapPreflightError, match="nao confere com os numeros esperados"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert not any(sql.upper().startswith("TRUNCATE") for sql, _ in neon.executed)


def test_staging_com_gmv_errado_bloqueia():
    neon = FakeNeonConn(staging_agg={**HAPPY_STAGING_AGG, "gmv": 1.0})
    with pytest.raises(swap.SwapPreflightError, match="nao confere com os numeros esperados"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True


def test_staging_com_duplicatas_bloqueia():
    neon = FakeNeonConn(dup_null={STAGING: (3, 0)})
    with pytest.raises(swap.SwapPreflightError, match="duplicadas"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert not any(sql.upper().startswith("TRUNCATE") for sql, _ in neon.executed)


def test_staging_com_nulos_bloqueia():
    neon = FakeNeonConn(dup_null={STAGING: (0, 4)})
    with pytest.raises(swap.SwapPreflightError, match="nulos"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert not any(sql.upper().startswith("TRUNCATE") for sql, _ in neon.executed)


# ---------------------------------------------------------------------------
# Objeto ausente bloqueia
# ---------------------------------------------------------------------------

def test_objeto_ausente_bloqueia():
    neon = FakeNeonConn(existing_tables={REAL, BACKUP})  # staging ausente
    with pytest.raises(swap.SwapPreflightError, match="nao encontrado"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert not any(sql.upper().startswith("TRUNCATE") for sql, _ in neon.executed)


# ---------------------------------------------------------------------------
# Rollback em cada falha (pos-insert e execucao)
# ---------------------------------------------------------------------------

def test_rollback_se_except_real_vs_staging_apos_insert_nao_for_zero():
    neon = FakeNeonConn(except_counts={(REAL, BACKUP): 0, (BACKUP, REAL): 0, (REAL, STAGING): 1, (STAGING, REAL): 0})
    with pytest.raises(swap.SwapPreflightError, match="diverge da staging Neon apos o INSERT"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert neon.committed is False
    # o TRUNCATE e o INSERT ja tinham rodado — o rollback e' o que desfaz
    assert any(sql.upper().startswith("TRUNCATE") for sql, _ in neon.executed)
    assert any(sql.upper().startswith("INSERT") for sql, _ in neon.executed)


def test_rollback_se_agregado_real_apos_insert_divergir_da_staging():
    neon = FakeNeonConn(real_agg_after={**HAPPY_STAGING_AGG, "canceled_orders": 999})
    with pytest.raises(swap.SwapPreflightError, match="agregados divergem apos o INSERT"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert neon.committed is False


def test_rollback_se_execute_falhar_durante_o_lock():
    neon = FakeNeonConn(raise_on_substring="LOCK TABLE")
    with pytest.raises(RuntimeError, match="falha simulada"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert neon.committed is False


def test_rollback_se_execute_falhar_durante_o_truncate():
    neon = FakeNeonConn(raise_on_substring="TRUNCATE TABLE")
    with pytest.raises(RuntimeError, match="falha simulada"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert neon.committed is False


def test_rollback_se_execute_falhar_durante_o_insert():
    neon = FakeNeonConn(raise_on_substring="INSERT INTO")
    with pytest.raises(RuntimeError, match="falha simulada"):
        swap.do_swap_neon(neon)
    assert neon.rolled_back is True
    assert neon.committed is False


# ---------------------------------------------------------------------------
# Guardas de run_swap_neon
# ---------------------------------------------------------------------------

def test_run_swap_bloqueado_sem_flag(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_REPLACES_NEON_DATA", "1")
    args = types.SimpleNamespace(swap_neon=False)
    with pytest.raises(RuntimeError, match="--swap-neon"):
        swap.run_swap_neon(args)


def test_run_swap_bloqueado_sem_variavel_de_ambiente(monkeypatch):
    monkeypatch.delenv("I_UNDERSTAND_THIS_REPLACES_NEON_DATA", raising=False)
    args = types.SimpleNamespace(swap_neon=True)
    with pytest.raises(RuntimeError, match="I_UNDERSTAND_THIS_REPLACES_NEON_DATA"):
        swap.run_swap_neon(args)


def test_run_swap_bloqueado_sem_database_url(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_REPLACES_NEON_DATA", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    args = types.SimpleNamespace(swap_neon=True)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        swap.run_swap_neon(args)


def test_run_swap_recusa_se_diagnostico_encontrar_problema_sem_conectar(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_REPLACES_NEON_DATA", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@fake-host/fakedb")
    args = types.SimpleNamespace(swap_neon=True)
    connect_called = []

    def fake_connect():
        connect_called.append(True)
        return _happy_conn()

    def fake_diagnose_com_problema():
        return {"problems": ["drift simulado"]}

    with pytest.raises(RuntimeError, match="diagnostico encontrou"):
        swap.run_swap_neon(args, diagnose_fn=fake_diagnose_com_problema, connect_fn=fake_connect)

    assert connect_called == [], "nao deveria conectar para o swap quando o diagnostico encontra problema"


def test_run_swap_caminho_feliz_com_diagnostico_limpo(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_REPLACES_NEON_DATA", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@fake-host/fakedb")
    args = types.SimpleNamespace(swap_neon=True)

    def fake_diagnose_limpo():
        return {"problems": []}

    def fake_connect():
        return _happy_conn()

    result = swap.run_swap_neon(args, diagnose_fn=fake_diagnose_limpo, connect_fn=fake_connect)
    assert result["real_agg_after"] == HAPPY_STAGING_AGG


# ---------------------------------------------------------------------------
# Credenciais nunca aparecem
# ---------------------------------------------------------------------------

def test_run_swap_nunca_imprime_credenciais(monkeypatch, capsys):
    monkeypatch.setenv("I_UNDERSTAND_THIS_REPLACES_NEON_DATA", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://segredouser:S3nhaSecreta@ep-fake.neon.tech:5432/db")
    args = types.SimpleNamespace(swap_neon=True)

    def fake_diagnose_limpo():
        return {"problems": []}

    def fake_connect():
        return _happy_conn()

    swap.run_swap_neon(args, diagnose_fn=fake_diagnose_limpo, connect_fn=fake_connect)

    out = capsys.readouterr().out
    assert "S3nhaSecreta" not in out
    assert "segredouser" not in out
    assert "ep-fake.neon.tech" in out


# ---------------------------------------------------------------------------
# Guardas estruturais
# ---------------------------------------------------------------------------

def test_backup_e_staging_sao_nomes_fixos_conhecidos():
    assert swap.BACKUP_NEON_NAME == "fact_shopee_product_monthly_backup_bug8_neon_20260702_232445"
    assert swap.STAGING_NEON_NAME == "fact_shopee_product_monthly_staging_bug8_neon_20260702_232445"


def test_nunca_referencia_datamart_database_url():
    source = MODULE_PATH.read_text(encoding="utf-8")
    read_patterns = [
        r'os\.environ(?:\.get)?\(\s*["\']DATAMART_DATABASE_URL',
        r'os\.getenv\(\s*["\']DATAMART_DATABASE_URL',
    ]
    for pattern in read_patterns:
        assert not re.search(pattern, source), f"padrao proibido encontrado: {pattern}"


def test_nenhum_drop_delete_update_no_modulo():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for word in ("DROP", "DELETE", "UPDATE"):
        assert not re.search(rf"\b{word}\b", source, re.IGNORECASE), f"palavra proibida encontrada: {word}"


def test_truncate_e_executado_como_sql_exatamente_uma_vez_no_codigo_fonte():
    """A palavra aparece varias vezes em prosa (docstrings explicando a
    guarda) — o que importa e' que a INSTRUCAO SQL real ('TRUNCATE TABLE
    marts....') apareca exatamente uma vez, nao uma segunda ocorrencia
    introduzida por engano."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = "".join(["t", "r", "u", "n", "c", "a", "t", "e"])
    pattern = re.compile(forbidden + r"\s+table\s+marts\.", re.IGNORECASE)
    occurrences = len(pattern.findall(source))
    assert occurrences == 1, f"esperada exatamente 1 instrucao SQL de esvaziamento no codigo-fonte, encontrada {occurrences}"


def test_backup_e_staging_nunca_sao_alvo_de_truncate_ou_insert():
    """STAGING pode aparecer como FONTE do INSERT (SELECT ... FROM
    marts.STAGING) — o que nunca pode acontecer e' BACKUP ou STAGING serem
    o ALVO de um TRUNCATE ou de um INSERT INTO."""
    neon = _happy_conn()
    swap.do_swap_neon(neon)
    for sql, _ in neon.executed:
        upper = sql.upper()
        if upper.startswith("TRUNCATE"):
            assert BACKUP not in sql and STAGING not in sql, f"TRUNCATE nao deveria mencionar backup/staging: {sql}"
        if upper.startswith("INSERT INTO"):
            target = re.match(r"INSERT INTO marts\.(\w+)", sql).group(1)
            assert target == REAL
            assert target != BACKUP
            assert target != STAGING
