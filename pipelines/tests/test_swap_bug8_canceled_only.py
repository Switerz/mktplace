"""
Testes de pipelines/reconciliation/swap_bug8_canceled_only.py (Gate 3 do Bug 8):
preflight bloqueia objeto ausente/drift/staging divergente, falha antes do
commit sempre faz rollback, o INSERT usa lista explicita de colunas, o
script nunca referencia DATABASE_URL/DATAMART_DATABASE_URL, e os nomes de
backup/staging sao constantes fixas — nunca descobertos dinamicamente.

Usa conexoes psycopg2 falsas (RealDictCursor-like: linhas viram dict) —
nenhuma credencial real e' necessaria, nenhum banco e' tocado.
"""
import re
from pathlib import Path

import pytest

import pipelines.reconciliation.swap_bug8_canceled_only as swap

MODULE_PATH = Path(swap.__file__)

LOCAL_URL = "postgresql://postgres:postgres@localhost:5432/mktplace_control"

DEFAULT_AGG = {"n": swap.EXPECTED_STAGING_ROWS, "gmv": swap.EXPECTED_STAGING_GMV,
               "units_sold": 100, "completed_orders": 50, "canceled_orders": swap.EXPECTED_STAGING_CANCELED}


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
            table_name = self._last_params[0]
            return {"exists": 1} if table_name in self.conn.existing_tables else None
        if "EXCEPT" in sql:
            tables = re.findall(r"marts\.(\w+)", sql)
            key = (tables[0], tables[1])
            return {"n": self.conn.except_counts.get(key, 0)}
        if "SUM(gmv)" in sql and "GROUP BY" not in sql:
            tables = re.findall(r"marts\.(\w+)", sql)
            table = tables[0]
            return dict(self.conn.aggregates.get(table, DEFAULT_AGG))
        if "HAVING COUNT(*) > 1" in sql:
            tables = re.findall(r"marts\.(\w+)", sql)
            return {"n": self.conn.dup_null.get(tables[0], (0, 0))[0]}
        if "ref_month IS NULL OR brand IS NULL" in sql:
            tables = re.findall(r"marts\.(\w+)", sql)
            return {"n": self.conn.dup_null.get(tables[0], (0, 0))[1]}
        return None

    def fetchall(self):
        return []

    def close(self):
        pass


class FakeConn:
    def __init__(self, existing_tables=None, except_counts=None, aggregates=None,
                 dup_null=None, raise_on_substring=None):
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.existing_tables = existing_tables if existing_tables is not None else {
            swap.BACKUP_TABLE, swap.STAGING_TABLE, swap.REAL_TABLE,
        }
        self.except_counts = except_counts or {}
        self.aggregates = aggregates or {}
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


def _ok_conn(**overrides):
    return FakeConn(**overrides)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def test_preflight_bloqueia_objeto_ausente():
    conn = _ok_conn(existing_tables={swap.BACKUP_TABLE, swap.REAL_TABLE})  # staging ausente
    with pytest.raises(swap.PreflightError, match="nao encontrado"):
        swap._preflight(conn, LOCAL_URL)


def test_preflight_bloqueia_drift_entre_real_e_backup():
    conn = _ok_conn(except_counts={(swap.REAL_TABLE, swap.BACKUP_TABLE): 3})
    with pytest.raises(swap.PreflightError, match="DIVERGE"):
        swap._preflight(conn, LOCAL_URL)


def test_preflight_bloqueia_staging_com_agregado_divergente():
    conn = _ok_conn(aggregates={swap.STAGING_TABLE: {**DEFAULT_AGG, "n": 9999}})
    with pytest.raises(swap.PreflightError, match="linhas"):
        swap._preflight(conn, LOCAL_URL)


def test_preflight_bloqueia_staging_com_gmv_divergente():
    conn = _ok_conn(aggregates={swap.STAGING_TABLE: {**DEFAULT_AGG, "gmv": 1.0}})
    with pytest.raises(swap.PreflightError, match="gmv"):
        swap._preflight(conn, LOCAL_URL)


def test_preflight_bloqueia_staging_com_duplicatas():
    conn = _ok_conn(dup_null={swap.STAGING_TABLE: (2, 0)})
    with pytest.raises(swap.PreflightError, match="duplicadas"):
        swap._preflight(conn, LOCAL_URL)


def test_preflight_bloqueia_staging_com_nulos():
    conn = _ok_conn(dup_null={swap.STAGING_TABLE: (0, 5)})
    with pytest.raises(swap.PreflightError, match="nulos"):
        swap._preflight(conn, LOCAL_URL)


def test_preflight_passa_no_caminho_feliz():
    conn = _ok_conn()
    swap._preflight(conn, LOCAL_URL)  # nao deve levantar


# ---------------------------------------------------------------------------
# Swap transacional — rollback em falha, commit so' no sucesso
# ---------------------------------------------------------------------------

def test_falha_no_except_pos_insert_faz_rollback_antes_do_commit():
    conn = _ok_conn(except_counts={(swap.REAL_TABLE, swap.STAGING_TABLE): 1})
    with pytest.raises(swap.SwapValidationError):
        swap._swap(conn)
    assert conn.rolled_back is True
    assert conn.committed is False


def test_falha_no_agregado_pos_insert_faz_rollback_antes_do_commit():
    conn = _ok_conn(aggregates={swap.REAL_TABLE: {**DEFAULT_AGG, "canceled_orders": 1}})
    with pytest.raises(swap.SwapValidationError):
        swap._swap(conn)
    assert conn.rolled_back is True
    assert conn.committed is False


def test_falha_no_truncate_faz_rollback():
    conn = _ok_conn(raise_on_substring="TRUNCATE")
    with pytest.raises(RuntimeError, match="falha simulada"):
        swap._swap(conn)
    assert conn.rolled_back is True
    assert conn.committed is False


def test_swap_com_tudo_ok_faz_commit_uma_unica_vez():
    conn = _ok_conn()
    result = swap._swap(conn)
    assert conn.committed is True
    assert conn.rolled_back is False
    assert result["n"] == swap.EXPECTED_STAGING_ROWS


def test_swap_adquire_lock_antes_do_truncate():
    conn = _ok_conn()
    swap._swap(conn)
    kinds = [sql.split()[0].upper() for sql, _ in conn.executed if sql.split()]
    assert "LOCK" in kinds
    assert kinds.index("LOCK") < kinds.index("TRUNCATE")


# ---------------------------------------------------------------------------
# INSERT com lista explicita de colunas
# ---------------------------------------------------------------------------

EXPECTED_COLUMNS = [
    "ref_month", "brand", "sku_ref", "sku_ref_key", "product_name", "variation_name",
    "gmv", "units_sold", "completed_orders", "canceled_orders",
    "cancel_rate_pct", "unique_buyers", "avg_price",
]


def test_insert_usa_lista_explicita_de_colunas_nunca_select_asterisco():
    conn = _ok_conn()
    swap._swap(conn)
    insert_sql = next(sql for sql, _ in conn.executed if sql.strip().upper().startswith("INSERT INTO"))
    assert "SELECT *" not in insert_sql
    assert f"marts.{swap.STAGING_TABLE}" in insert_sql
    for col in EXPECTED_COLUMNS:
        assert col in insert_sql, f"coluna {col!r} ausente do INSERT"


# ---------------------------------------------------------------------------
# Guardas estruturais
# ---------------------------------------------------------------------------

def test_script_nunca_referencia_database_url_ou_datamart_database_url():
    source = MODULE_PATH.read_text(encoding="utf-8")
    read_patterns = [
        r'os\.environ(?:\.get)?\(\s*["\']DATABASE_URL',
        r'os\.getenv\(\s*["\']DATABASE_URL',
        r'os\.environ(?:\.get)?\(\s*["\']DATAMART_DATABASE_URL',
        r'os\.getenv\(\s*["\']DATAMART_DATABASE_URL',
    ]
    for pattern in read_patterns:
        assert not re.search(pattern, source), f"padrao proibido encontrado: {pattern}"


def test_nomes_de_backup_e_staging_sao_constantes_fixas():
    assert swap.BACKUP_TABLE == "fact_shopee_product_monthly_backup_bug8_20260702_150840"
    assert swap.STAGING_TABLE == "fact_shopee_product_monthly_staging_bug8_20260702_150840"


def test_script_nunca_descobre_backup_ou_staging_dinamicamente():
    """Guarda de regressao: o script deve sempre usar os nomes fixos
    (BACKUP_TABLE/STAGING_TABLE) definidos no topo do modulo — nunca uma
    query que escolha 'o mais recente' via LIKE/ORDER BY .. DESC LIMIT 1
    contra information_schema."""
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "LIKE" not in source
    assert "SIMILAR TO" not in source
    assert "DESC LIMIT" not in source
    # a unica query contra information_schema.tables deve ser a checagem de
    # existencia por nome exato (table_name = %s), nao uma busca por padrao
    assert "table_name = %s" in source
