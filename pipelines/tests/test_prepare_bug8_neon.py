"""
Testes de pipelines/reconciliation/diagnose_bug8_neon.py::do_prepare_neon /
run_prepare (Gate 4A.2): criacao de backup + staging SOMENTE no Neon, com
revalidacao sob lock contra condicao de corrida.

Usa conexoes psycopg2 falsas — nenhum banco real e' tocado, nenhuma
credencial real e' necessaria. TAG e' fixa para que os nomes gerados
(backup_name/staging_name) sejam previsiveis nos testes.

Ordem real de operacoes em do_prepare_neon, refletida nos fixtures abaixo:
  1. existencia de backup_name/staging_name (recusa se ja existir)
  2. LOCK TABLE na tabela real
  3. REVALIDACAO sob o lock (run_diagnose: Neon real vs. backup local
     pre-fix, 13 colunas + agregados por marca x mes) — aborta aqui se
     algo mudou desde o diagnostico inicial de run_prepare
  4. CREATE TABLE do backup Neon + reconciliacao backup vs. real
  5. CREATE TABLE da staging Neon + INSERT a partir da staging local
  6. reconciliacao staging Neon vs. staging local vs. numeros esperados
  7. duplicatas/nulos na staging Neon
  8. COMMIT (ou ROLLBACK em qualquer excecao acima)
"""
import re
import types
from datetime import date
from pathlib import Path

import pytest

import pipelines.reconciliation.diagnose_bug8_neon as diag

MODULE_PATH = Path(diag.__file__)

TAG = "20990101_000000"
BACKUP_NAME = f"{diag.REAL_TABLE}_backup_bug8_neon_{TAG}"
STAGING_NAME = f"{diag.REAL_TABLE}_staging_bug8_neon_{TAG}"

HAPPY_REAL_AGG = {"n": 2, "gmv": 150.0, "units_sold": 10, "completed_orders": 8, "canceled_orders": 2}
HAPPY_LOCAL_AGG = {"n": 2, "gmv": 150.0, "units_sold": 10, "completed_orders": 8, "canceled_orders": 2}


def _biz_rows(n=2, gmv_each=75.0, ref_month=date(2026, 1, 1), brand="kokeshi", offset=0):
    rows = []
    for i in range(offset, offset + n):
        rows.append(dict(
            ref_month=ref_month, brand=brand, sku_ref=f"SKU{i}", sku_ref_key=f"SKU{i}",
            product_name=f"Produto {i}", variation_name=None, gmv=gmv_each, units_sold=5,
            completed_orders=4, canceled_orders=1, cancel_rate_pct=20.0, unique_buyers=3, avg_price=15.0,
        ))
    return rows


def _local_rows(n=2):
    return _biz_rows(n=n)


# ---------------------------------------------------------------------------
# Conexoes falsas
# ---------------------------------------------------------------------------

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
            return dict(self.conn.aggregates.get(tables[0], HAPPY_REAL_AGG))
        if "HAVING COUNT(*) > 1" in sql:
            tables = re.findall(r"marts\.(\w+)", sql)
            return {"n": self.conn.dup_null.get(tables[0], (0, 0))[0]}
        if "ref_month IS NULL OR brand IS NULL" in sql:
            tables = re.findall(r"marts\.(\w+)", sql)
            return {"n": self.conn.dup_null.get(tables[0], (0, 0))[1]}
        return None

    def fetchall(self):
        # SELECT simples das 13 colunas de negocio (usado por run_diagnose
        # via _fetch_business_rows) — so' a tabela real e' lida assim do
        # lado Neon (backup/staging so' sao lidos via agregados depois de
        # criados).
        tables = re.findall(r"marts\.(\w+)", self._last_sql)
        table = tables[0] if tables else None
        if table == diag.REAL_TABLE:
            return self.conn.business_rows
        return []

    def close(self):
        pass


class FakeNeonConn:
    def __init__(self, business_rows=None, existing_tables=None, except_counts=None,
                 aggregates=None, dup_null=None, raise_on_substring=None):
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.business_rows = business_rows if business_rows is not None else _biz_rows()
        self.existing_tables = existing_tables if existing_tables is not None else set()
        self.except_counts = except_counts if except_counts is not None else {
            (diag.REAL_TABLE, BACKUP_NAME): 0, (BACKUP_NAME, diag.REAL_TABLE): 0,
        }
        self.aggregates = aggregates if aggregates is not None else {
            diag.REAL_TABLE: HAPPY_REAL_AGG, BACKUP_NAME: HAPPY_REAL_AGG, STAGING_NAME: HAPPY_LOCAL_AGG,
        }
        self.dup_null = dup_null or {}
        self.raise_on_substring = raise_on_substring

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakeLocalCursor:
    def __init__(self, conn):
        self.conn = conn
        self._last_sql = ""

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.conn.executed.append(norm)
        self._last_sql = norm

    def fetchall(self):
        tables = re.findall(r"marts\.(\w+)", self._last_sql)
        table = tables[0] if tables else None
        return self.conn.rows_by_table.get(table, [])

    def close(self):
        pass


class FakeLocalConn:
    """rows_by_table deve cobrir diag.BACKUP_TABLE (lido pela revalidacao
    via run_diagnose) e diag.LOCAL_STAGING_TABLE (lido para copiar ao
    Neon)."""
    def __init__(self, rows_by_table):
        self.rows_by_table = rows_by_table
        self.executed = []
        self.closed = False

    def cursor(self, cursor_factory=None):
        return FakeLocalCursor(self)

    def close(self):
        self.closed = True


def _default_local_conn(backup_rows=None, staging_rows=None):
    return FakeLocalConn({
        diag.BACKUP_TABLE: backup_rows if backup_rows is not None else _biz_rows(),
        diag.LOCAL_STAGING_TABLE: staging_rows if staging_rows is not None else _local_rows(),
    })


def _fake_execute_values(cur, sql, batch, page_size=500):
    cur.execute(sql, batch)


@pytest.fixture(autouse=True)
def _patch_execute_values(monkeypatch):
    monkeypatch.setattr(diag, "execute_values", _fake_execute_values)
    # numeros pequenos e deterministicos, para nao precisar de 2471 linhas reais
    monkeypatch.setattr(diag, "EXPECTED_STAGING_ROWS", HAPPY_LOCAL_AGG["n"])
    monkeypatch.setattr(diag, "EXPECTED_STAGING_GMV", HAPPY_LOCAL_AGG["gmv"])
    monkeypatch.setattr(diag, "EXPECTED_STAGING_CANCELED", HAPPY_LOCAL_AGG["canceled_orders"])


def _happy_neon_conn(**overrides):
    return FakeNeonConn(**overrides)


# ---------------------------------------------------------------------------
# Caminho feliz
# ---------------------------------------------------------------------------

def test_caminho_feliz_cria_backup_e_staging_e_faz_commit_uma_vez():
    neon = _happy_neon_conn()
    local = _default_local_conn()

    result = diag.do_prepare_neon(neon, local, TAG)

    assert result["backup_table"] == BACKUP_NAME
    assert result["staging_table"] == STAGING_NAME
    assert neon.committed is True
    assert neon.rolled_back is False


def test_caminho_feliz_continua_funcionando_apos_a_revalidacao_sob_lock():
    """Regressao: a revalidacao adicionada nao deve quebrar o fluxo normal
    quando Neon e backup local pre-fix realmente estao identicos."""
    neon = _happy_neon_conn()
    local = _default_local_conn()

    result = diag.do_prepare_neon(neon, local, TAG)

    assert result["backup_table"] == BACKUP_NAME
    assert result["staging_table"] == STAGING_NAME
    assert neon.committed is True
    assert neon.rolled_back is False
    # a revalidacao de fato rodou (leu as 13 colunas da tabela real)
    assert any(
        sql.upper().startswith("SELECT") and f"marts.{diag.REAL_TABLE}" in sql and "SUM(" not in sql.upper()
        for sql, _ in neon.executed
    )


def test_commit_so_acontece_apos_todas_as_reconciliacoes():
    """commit() nao e' uma linha de SQL — verificamos que as reconciliacoes
    (revalidacao, EXCEPT, agregados, duplicatas/nulos, todas via SELECT)
    foram executadas e que o commit so' e' sinalizado no final."""
    neon = _happy_neon_conn()
    local = _default_local_conn()
    diag.do_prepare_neon(neon, local, TAG)

    kinds = [sql.split()[0].upper() for sql, _ in neon.executed if sql.split()]
    assert kinds.count("SELECT") >= 5  # existencia x2, revalidacao x1, agregados x2+, EXCEPT x2, duplicatas/nulos x2
    assert neon.committed is True
    assert neon.rolled_back is False


# ---------------------------------------------------------------------------
# Objeto existente impede sobrescrita
# ---------------------------------------------------------------------------

def test_objeto_backup_existente_impede_criacao_sem_tentar_criar():
    neon = FakeNeonConn(existing_tables={BACKUP_NAME})
    local = _default_local_conn()

    with pytest.raises(diag.PrepareValidationError, match="ja existe"):
        diag.do_prepare_neon(neon, local, TAG)

    assert neon.rolled_back is True
    assert not any("CREATE TABLE" in sql.upper() for sql, _ in neon.executed)


def test_objeto_staging_existente_impede_criacao():
    neon = FakeNeonConn(existing_tables={STAGING_NAME})
    local = _default_local_conn()

    with pytest.raises(diag.PrepareValidationError, match="ja existe"):
        diag.do_prepare_neon(neon, local, TAG)

    assert neon.rolled_back is True


# ---------------------------------------------------------------------------
# Condicao de corrida: Neon muda entre o diagnostico inicial e o lock
# ---------------------------------------------------------------------------

def test_neon_muda_entre_diagnostico_e_lock_aborta_prepare():
    """Simula: run_prepare rodou o diagnostico inicial (limpo, fora desta
    funcao), mas ANTES do lock ser adquirido aqui dentro, uma linha nova
    chegou ao Neon real (nao existe no backup local pre-fix). A
    revalidacao sob o lock deve pegar isso e abortar."""
    neon_rows_com_dado_novo = _biz_rows(n=3)  # 1 linha a mais do que o backup local
    neon = FakeNeonConn(business_rows=neon_rows_com_dado_novo)
    local = _default_local_conn(backup_rows=_biz_rows(n=2))

    with pytest.raises(diag.PrepareValidationError, match="revalidacao sob lock"):
        diag.do_prepare_neon(neon, local, TAG)

    assert neon.rolled_back is True
    assert neon.committed is False


def test_nenhum_create_table_ocorre_quando_revalidacao_falha():
    neon = FakeNeonConn(business_rows=_biz_rows(n=3))
    local = _default_local_conn(backup_rows=_biz_rows(n=2))

    with pytest.raises(diag.PrepareValidationError, match="revalidacao sob lock"):
        diag.do_prepare_neon(neon, local, TAG)

    assert not any("CREATE TABLE" in sql.upper() for sql, _ in neon.executed)


def test_lock_ocorre_antes_da_revalidacao_mesmo_quando_ela_bloqueia():
    neon = FakeNeonConn(business_rows=_biz_rows(n=3))
    local = _default_local_conn(backup_rows=_biz_rows(n=2))

    with pytest.raises(diag.PrepareValidationError, match="revalidacao sob lock"):
        diag.do_prepare_neon(neon, local, TAG)

    kinds = [sql.split()[0].upper() for sql, _ in neon.executed if sql.split()]
    assert "LOCK" in kinds, "o lock deve ser adquirido mesmo que a revalidacao subsequente bloqueie"


def test_revalidacao_com_drift_de_valor_tambem_aborta():
    """Nao so' linhas novas — um valor diferente na MESMA chave (drift)
    tambem deve ser pego pela revalidacao."""
    neon = FakeNeonConn(business_rows=_biz_rows(n=2, gmv_each=999.0))
    local = _default_local_conn(backup_rows=_biz_rows(n=2, gmv_each=75.0))

    with pytest.raises(diag.PrepareValidationError, match="revalidacao sob lock"):
        diag.do_prepare_neon(neon, local, TAG)

    assert neon.rolled_back is True


def test_revalidacao_ocorre_antes_do_primeiro_create_table():
    neon = _happy_neon_conn()
    local = _default_local_conn()
    diag.do_prepare_neon(neon, local, TAG)

    idx_revalidacao = next(
        i for i, (sql, _) in enumerate(neon.executed)
        if sql.upper().startswith("SELECT") and f"marts.{diag.REAL_TABLE}" in sql and "SUM(" not in sql.upper()
    )
    idx_primeiro_create = next(i for i, (sql, _) in enumerate(neon.executed) if sql.upper().startswith("CREATE TABLE"))
    assert idx_revalidacao < idx_primeiro_create


# ---------------------------------------------------------------------------
# Diagnostico inicial com drift impede prepare (nivel run_prepare, antes de
# sequer conectar/travar o Neon para o preparo)
# ---------------------------------------------------------------------------

def test_diagnostico_inicial_com_drift_impede_prepare_sem_conectar_ao_neon():
    connect_called = []

    def fake_connect():
        connect_called.append(True)
        return _happy_neon_conn(), _default_local_conn()

    def fake_diagnose_com_drift():
        return {"problems": ["3 chave(s) com drift"]}

    args = types.SimpleNamespace(prepare=True)
    import os
    os.environ["I_UNDERSTAND_THIS_TOUCHES_NEON"] = "1"
    os.environ["DATABASE_URL"] = "postgresql://fake:fake@fake-host/fakedb"
    try:
        with pytest.raises(RuntimeError, match="diagnostico encontrou"):
            diag.run_prepare(args, diagnose_fn=fake_diagnose_com_drift, connect_fn=fake_connect, tag_fn=lambda: TAG)
    finally:
        del os.environ["I_UNDERSTAND_THIS_TOUCHES_NEON"]
        del os.environ["DATABASE_URL"]

    assert connect_called == [], "nao deveria conectar ao Neon quando o diagnostico inicial encontra drift"


# ---------------------------------------------------------------------------
# Rollback em cada falha (apos a revalidacao passar)
# ---------------------------------------------------------------------------

def test_rollback_se_backup_divergir_da_tabela_real():
    neon = FakeNeonConn(aggregates={
        diag.REAL_TABLE: HAPPY_REAL_AGG, BACKUP_NAME: {**HAPPY_REAL_AGG, "gmv": 1.0}, STAGING_NAME: HAPPY_LOCAL_AGG,
    })
    local = _default_local_conn()
    with pytest.raises(diag.PrepareValidationError, match="backup diverge"):
        diag.do_prepare_neon(neon, local, TAG)
    assert neon.rolled_back is True
    assert neon.committed is False


def test_rollback_se_except_backup_vs_real_nao_for_zero():
    neon = FakeNeonConn(except_counts={(diag.REAL_TABLE, BACKUP_NAME): 1, (BACKUP_NAME, diag.REAL_TABLE): 0})
    local = _default_local_conn()
    with pytest.raises(diag.PrepareValidationError, match="EXCEPT nao-zero"):
        diag.do_prepare_neon(neon, local, TAG)
    assert neon.rolled_back is True
    assert neon.committed is False


def test_rollback_se_staging_neon_divergir_da_staging_local():
    neon = FakeNeonConn(aggregates={
        diag.REAL_TABLE: HAPPY_REAL_AGG, BACKUP_NAME: HAPPY_REAL_AGG,
        STAGING_NAME: {**HAPPY_LOCAL_AGG, "canceled_orders": 999},
    })
    local = _default_local_conn()
    with pytest.raises(diag.PrepareValidationError, match="staging Neon diverge"):
        diag.do_prepare_neon(neon, local, TAG)
    assert neon.rolled_back is True
    assert neon.committed is False


def test_rollback_se_staging_neon_nao_bater_com_numeros_esperados():
    neon = FakeNeonConn(aggregates={
        diag.REAL_TABLE: HAPPY_REAL_AGG, BACKUP_NAME: HAPPY_REAL_AGG,
        STAGING_NAME: HAPPY_LOCAL_AGG,
    })
    local = _default_local_conn()
    original = diag.EXPECTED_STAGING_CANCELED
    diag.EXPECTED_STAGING_CANCELED = 12345
    try:
        with pytest.raises(diag.PrepareValidationError, match="nao confere com os numeros esperados"):
            diag.do_prepare_neon(neon, local, TAG)
    finally:
        diag.EXPECTED_STAGING_CANCELED = original
    assert neon.rolled_back is True
    assert neon.committed is False


def test_rollback_se_staging_neon_tiver_duplicatas():
    neon = FakeNeonConn(dup_null={STAGING_NAME: (2, 0)})
    local = _default_local_conn()
    with pytest.raises(diag.PrepareValidationError, match="duplicadas"):
        diag.do_prepare_neon(neon, local, TAG)
    assert neon.rolled_back is True
    assert neon.committed is False


def test_rollback_se_staging_neon_tiver_nulos():
    neon = FakeNeonConn(dup_null={STAGING_NAME: (0, 3)})
    local = _default_local_conn()
    with pytest.raises(diag.PrepareValidationError, match="nulos"):
        diag.do_prepare_neon(neon, local, TAG)
    assert neon.rolled_back is True
    assert neon.committed is False


def test_rollback_se_staging_local_estiver_vazia():
    neon = _happy_neon_conn()
    local = _default_local_conn(staging_rows=[])
    with pytest.raises(diag.PrepareValidationError, match="vazia"):
        diag.do_prepare_neon(neon, local, TAG)
    assert neon.rolled_back is True
    assert neon.committed is False


def test_rollback_se_execute_falhar_durante_lock():
    neon = FakeNeonConn(raise_on_substring="LOCK TABLE")
    local = _default_local_conn()
    with pytest.raises(RuntimeError, match="falha simulada"):
        diag.do_prepare_neon(neon, local, TAG)
    assert neon.rolled_back is True
    assert neon.committed is False


# ---------------------------------------------------------------------------
# Tabela real nunca recebe DML / INSERT usa colunas explicitas
# ---------------------------------------------------------------------------

def test_tabela_real_nunca_recebe_dml():
    """A tabela real e' lida livremente (SELECT, LOCK, e como fonte de um
    CREATE TABLE ... AS SELECT para o backup) — o que NUNCA pode acontecer
    e' ela ser o ALVO de um INSERT/UPDATE/DELETE."""
    neon = _happy_neon_conn()
    local = _default_local_conn()
    diag.do_prepare_neon(neon, local, TAG)

    forbidden_patterns = [
        rf"INSERT\s+INTO\s+marts\.{re.escape(diag.REAL_TABLE)}\b",
        rf"UPDATE\s+marts\.{re.escape(diag.REAL_TABLE)}\b",
        rf"DELETE\s+FROM\s+marts\.{re.escape(diag.REAL_TABLE)}\b",
    ]
    for sql, _ in neon.executed:
        for pattern in forbidden_patterns:
            assert not re.search(pattern, sql, re.IGNORECASE), f"instrucao proibida contra a tabela real: {sql}"


def test_insert_na_staging_usa_lista_explicita_de_colunas():
    neon = _happy_neon_conn()
    local = _default_local_conn()
    diag.do_prepare_neon(neon, local, TAG)

    insert_sql = next(sql for sql, _ in neon.executed if sql.strip().upper().startswith("INSERT INTO"))
    assert "SELECT *" not in insert_sql
    assert f"marts.{STAGING_NAME}" in insert_sql
    for col in diag.BUSINESS_COLUMNS:
        assert col in insert_sql, f"coluna {col!r} ausente do INSERT"


def test_lock_e_adquirido_antes_do_create_table_backup():
    neon = _happy_neon_conn()
    local = _default_local_conn()
    diag.do_prepare_neon(neon, local, TAG)

    kinds = [(sql.split()[0].upper(), sql) for sql, _ in neon.executed if sql.split()]
    lock_idx = next(i for i, (k, s) in enumerate(kinds) if k == "LOCK")
    create_idx = next(i for i, (k, s) in enumerate(kinds) if k == "CREATE" and BACKUP_NAME in s)
    assert lock_idx < create_idx


# ---------------------------------------------------------------------------
# Credenciais nunca aparecem
# ---------------------------------------------------------------------------

def test_run_prepare_nunca_imprime_credenciais(monkeypatch, capsys):
    monkeypatch.setenv("I_UNDERSTAND_THIS_TOUCHES_NEON", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql://segredouser:S3nhaSecreta@ep-fake.neon.tech:5432/db")
    args = types.SimpleNamespace(prepare=True)

    def fake_diagnose_limpo():
        return {"problems": []}

    def fake_connect():
        return _happy_neon_conn(), _default_local_conn()

    diag.run_prepare(args, diagnose_fn=fake_diagnose_limpo, connect_fn=fake_connect, tag_fn=lambda: TAG)

    out = capsys.readouterr().out
    assert "S3nhaSecreta" not in out
    assert "segredouser" not in out
    assert "ep-fake.neon.tech" in out  # host sanitizado deve aparecer


# ---------------------------------------------------------------------------
# Guardas estruturais adicionais (identificador seguro, sem comando destrutivo)
# ---------------------------------------------------------------------------

def test_validate_identifier_aceita_nomes_gerados_pelo_modulo():
    assert diag._validate_identifier(BACKUP_NAME) == BACKUP_NAME
    assert diag._validate_identifier(STAGING_NAME) == STAGING_NAME


@pytest.mark.parametrize("bad_name", [
    "Fact_Shopee",  # maiuscula
    "fact-shopee",  # hifen
    "1fact_shopee",  # comeca com numero
    "fact shopee",  # espaco
    "fact_shopee; DROP",  # tentativa de injecao
])
def test_validate_identifier_recusa_nomes_inseguros(bad_name):
    with pytest.raises(ValueError, match="falhou na validacao"):
        diag._validate_identifier(bad_name)


def test_nenhum_comando_destrutivo_no_modulo_apos_gate_4a2():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for word in ("DROP", "DELETE", "UPDATE"):
        assert not re.search(rf"\b{word}\b", source, re.IGNORECASE), f"palavra proibida encontrada: {word}"
    forbidden = "".join(["t", "r", "u", "n", "c", "a", "t", "e"])
    assert forbidden not in source.lower()
