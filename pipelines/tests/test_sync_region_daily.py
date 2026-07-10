"""
Testes de pipelines/sync_region_daily.py — Gate 6B.1.

Usa conexoes psycopg2 falsas — nenhum banco real (Data Mart nem Neon) e'
tocado. As tabelas Neon sao simuladas como listas de dicts em memoria
(FakeConn.tables), e as queries SQL relevantes sao interpretadas por
substring/regex — o suficiente para exercitar toda a logica de
validacao/transacao de do_sync sem um parser SQL real.
"""
from __future__ import annotations

import re

import pytest

from pipelines import sync_region_daily as srd


# ---------------------------------------------------------------------------
# Fixtures de dados
# ---------------------------------------------------------------------------
def _row(date="2026-01-01", marketplace_id=3, loja_id=1, uf="SP",
         gmv=100.0, orders=1, units_sold=1, canceled_orders=0, returned_orders=0,
         seller_shipping_cost=None, buyer_shipping_fee=10.0, estimated_shipping_fee=10.0,
         reverse_shipping_fee=None, uf_known_orders=1, uf_eligible_orders=1,
         shipping_cost_covered_orders=0, shipping_cost_eligible_orders=1,
         source_updated_at=None):
    return {
        "date": date, "marketplace_id": marketplace_id, "loja_id": loja_id, "uf": uf,
        "gmv": gmv, "orders": orders, "units_sold": units_sold,
        "canceled_orders": canceled_orders, "returned_orders": returned_orders,
        "seller_shipping_cost": seller_shipping_cost, "buyer_shipping_fee": buyer_shipping_fee,
        "estimated_shipping_fee": estimated_shipping_fee, "reverse_shipping_fee": reverse_shipping_fee,
        "uf_known_orders": uf_known_orders, "uf_eligible_orders": uf_eligible_orders,
        "shipping_cost_covered_orders": shipping_cost_covered_orders,
        "shipping_cost_eligible_orders": shipping_cost_eligible_orders,
        "source_updated_at": source_updated_at,
    }


SAMPLE_ROWS = [
    _row(date="2026-01-01", marketplace_id=3, loja_id=1, uf="SP", gmv=100.0, orders=2),
    _row(date="2026-01-01", marketplace_id=2, loja_id=2, uf="RJ", gmv=250.5, orders=3,
         uf_known_orders=2, uf_eligible_orders=3, shipping_cost_covered_orders=1, shipping_cost_eligible_orders=3),
]


# ---------------------------------------------------------------------------
# Funcoes puras — sem qualquer conexao
# ---------------------------------------------------------------------------
def test_aggregates_from_rows_soma_corretamente():
    agg = srd.aggregates_from_rows(SAMPLE_ROWS)
    assert agg["n"] == 2
    assert agg["gmv"] == 350.5
    assert agg["orders"] == 5
    assert agg["uf_known_orders"] == 3
    assert agg["uf_eligible_orders"] == 4


def test_aggregates_from_rows_trata_none_como_zero():
    rows = [_row(seller_shipping_cost=None, buyer_shipping_fee=None)]
    agg = srd.aggregates_from_rows(rows)
    assert agg["seller_shipping_cost"] == 0.0
    assert agg["buyer_shipping_fee"] == 0.0


def test_aggregates_from_rows_lista_vazia():
    agg = srd.aggregates_from_rows([])
    assert agg["n"] == 0
    assert agg["gmv"] == 0.0


def test_rows_with_numerator_over_denominator_detecta_uf():
    rows = [_row(uf_known_orders=5, uf_eligible_orders=2)]
    assert srd.rows_with_numerator_over_denominator(rows) == 1


def test_rows_with_numerator_over_denominator_detecta_shipping():
    rows = [_row(shipping_cost_covered_orders=5, shipping_cost_eligible_orders=2)]
    assert srd.rows_with_numerator_over_denominator(rows) == 1


def test_rows_with_numerator_over_denominator_ok_retorna_zero():
    assert srd.rows_with_numerator_over_denominator(SAMPLE_ROWS) == 0


def test_validate_identifier_aceita_nome_seguro():
    assert srd._validate_identifier("fact_marketplace_region_daily_backup_20260709_120000") \
        == "fact_marketplace_region_daily_backup_20260709_120000"


@pytest.mark.parametrize("bad", [
    "Fact_Region",       # maiuscula
    "1_fact_region",     # comeca com digito
    "fact-region",       # hifen
    "fact region",       # espaco
    "fact;drop table x", # tentativa de injecao
])
def test_validate_identifier_rejeita_nome_inseguro(bad):
    with pytest.raises(ValueError):
        srd._validate_identifier(bad)


def test_sanitize_error_message_remove_credencial():
    exc = RuntimeError("connection to postgresql://writer:S3nh4Secreta@dbhost:5432/db failed")
    sanitized = srd._sanitize_error_message(exc)
    assert "S3nh4Secreta" not in sanitized
    assert "writer" not in sanitized


def test_sanitize_error_message_trunca_tamanho():
    exc = RuntimeError("x" * 10_000)
    assert len(srd._sanitize_error_message(exc)) <= 500


# ---------------------------------------------------------------------------
# Fake Neon connection — simula tabelas como listas de dicts em memoria e
# interpreta as poucas formas de SQL emitidas por sync_region_daily.py.
# ---------------------------------------------------------------------------
def _row_tuple(row: dict) -> tuple:
    return tuple(row[c] for c in srd.BUSINESS_COLUMNS)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.conn.executed.append(norm)
        upper = norm.upper()

        if self.conn.fail_on_substring and self.conn.fail_on_substring in norm:
            raise RuntimeError("falha simulada de execucao")

        if upper.startswith("SET LOCAL"):
            return
        if upper.startswith("LOCK TABLE"):
            if self.conn.lock_should_fail:
                raise RuntimeError("nao foi possivel adquirir o lock (simulado)")
            return
        if upper.startswith("CREATE TEMP TABLE"):
            self.conn.tables[srd.STAGING_TABLE_QUALIFIED] = []
            return
        if upper.startswith("SELECT EXISTS"):
            schema, table = params
            key = f"{schema}.{table}"
            self._result = {"exists": key in self.conn.tables}
            return
        if "COALESCE(SUM(" in upper and upper.startswith("SELECT COUNT(*) AS N"):
            table = norm.split(" FROM ", 1)[1].strip()
            rows = self.conn.tables.get(table, [])
            agg = {"n": len(rows)}
            for col in srd.SUM_COLUMNS:
                if col in ("orders", "units_sold", "canceled_orders", "returned_orders",
                           "uf_known_orders", "uf_eligible_orders",
                           "shipping_cost_covered_orders", "shipping_cost_eligible_orders"):
                    agg[col] = sum(srd._int(r[col]) for r in rows)
                else:
                    agg[col] = round(sum(srd._num(r[col]) for r in rows), 2)
            self._result = agg
            return
        if "GROUP BY DATE, MARKETPLACE_ID, LOJA_ID, UF HAVING COUNT(*) > 1" in upper:
            table = re.search(r"FROM \(\s*SELECT date, marketplace_id, loja_id, uf FROM (\S+)", norm).group(1)
            rows = self.conn.tables.get(table, [])
            keys = [(r["date"], r["marketplace_id"], r["loja_id"], r["uf"]) for r in rows]
            dupes = len(keys) - len(set(keys))
            self._result = {"n": max(dupes, 0)}
            return
        if "WHERE DATE IS NULL OR MARKETPLACE_ID IS NULL" in upper:
            table = norm.split(" FROM ", 1)[1].split(" WHERE")[0].strip()
            rows = self.conn.tables.get(table, [])
            nulls = sum(1 for r in rows if r["date"] is None or r["marketplace_id"] is None
                        or r["loja_id"] is None or r["uf"] is None)
            self._result = {"n": nulls}
            return
        if "EXCEPT SELECT" in upper:
            m = re.search(r"FROM (\S+) EXCEPT SELECT .+ FROM (\S+)\)", norm)
            table_a, table_b = m.group(1), m.group(2)
            set_a = {_row_tuple(r) for r in self.conn.tables.get(table_a, [])}
            set_b = {_row_tuple(r) for r in self.conn.tables.get(table_b, [])}
            self._result = {"n": len(set_a - set_b)}
            return
        if upper.startswith("CREATE TABLE MARTS.") and " AS SELECT * FROM " in upper:
            backup_table = norm.split(" ")[2]
            source_table = norm.split(" AS SELECT * FROM ", 1)[1].strip()
            self.conn.tables[backup_table] = [dict(r) for r in self.conn.tables.get(source_table, [])]
            return
        if upper.startswith("TRUNCATE TABLE"):
            table = norm.split(" ", 2)[2].strip()
            self.conn.tables[table] = []
            return
        if upper.startswith("INSERT INTO") and " SELECT " in upper and " FROM " in upper:
            dest_table = norm.split(" ", 2)[2].split(" (")[0].strip()
            source_table = norm.rsplit(" FROM ", 1)[1].strip()
            self.conn.tables[dest_table] = [dict(r) for r in self.conn.tables.get(source_table, [])]
            return
        if upper.startswith("INSERT INTO AUDIT.SOURCE_SYNC_RUN"):
            self.conn.next_run_id += 1
            self._result = {"sync_run_id": self.conn.next_run_id}
            self.conn.audit_runs[self.conn.next_run_id] = {"status": "running"}
            return
        if upper.startswith("UPDATE AUDIT.SOURCE_SYNC_RUN"):
            run_id = params[-1]
            self.conn.audit_runs.setdefault(run_id, {})
            self.conn.audit_runs[run_id].update({
                "status": params[0], "rows_extracted": params[1], "rows_loaded": params[2],
                "source_min_date": params[3], "source_max_date": params[4], "error_message": params[5],
            })
            return

        raise AssertionError(f"query nao reconhecida pelo fake: {norm!r}")

    def fetchone(self):
        return self._result

    def close(self):
        pass


class FakeConn:
    def __init__(self, initial_tables=None, lock_should_fail=False, fail_on_substring=None):
        self.tables = initial_tables or {}
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.lock_should_fail = lock_should_fail
        self.fail_on_substring = fail_on_substring
        self.next_run_id = 0
        self.audit_runs = {}

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _fake_execute_values(cur, sql, batch, page_size=500):
    """Substitui psycopg2.extras.execute_values: extrai a tabela destino e a
    lista de colunas do INSERT, e grava as linhas na tabela fake do conn."""
    norm = " ".join(sql.split())
    m = re.match(r"INSERT INTO (\S+) \(([^)]+)\)", norm)
    table, cols_str = m.group(1), m.group(2)
    cols = [c.strip() for c in cols_str.split(",")]
    rows = [dict(zip(cols, tup)) for tup in batch]
    cur.conn.tables.setdefault(table, [])
    cur.conn.tables[table].extend(rows)


@pytest.fixture(autouse=True)
def _patch_execute_values(monkeypatch):
    monkeypatch.setattr(srd, "execute_values", _fake_execute_values)


# ---------------------------------------------------------------------------
# do_sync — caminho feliz
# ---------------------------------------------------------------------------
def test_do_sync_primeira_carga_tabela_real_vazia():
    conn = FakeConn(initial_tables={srd.REAL_TABLE: []})
    result = srd.do_sync(SAMPLE_ROWS, conn, tag="20260709_120000")

    assert conn.committed is True
    assert conn.rolled_back is False
    assert result["backup_table"] is None  # tabela estava vazia, sem backup
    assert result["real_agg_after"]["n"] == 2
    assert result["real_agg_after"]["gmv"] == 350.5
    assert len(conn.tables[srd.REAL_TABLE]) == 2
    # staging TEMP nao sobrevive fora da transacao simulada, mas o fake nao
    # dropa automaticamente — o importante e' que a tabela real foi populada
    # corretamente a partir dela.


def test_do_sync_cria_backup_quando_tabela_real_ja_tem_linhas():
    existing = [_row(date="2025-12-01", marketplace_id=3, loja_id=1, uf="MG", gmv=999.0)]
    conn = FakeConn(initial_tables={srd.REAL_TABLE: list(existing)})

    result = srd.do_sync(SAMPLE_ROWS, conn, tag="20260709_130000")

    assert result["backup_table"] == "fact_marketplace_region_daily_backup_20260709_130000"
    backup_key = f"marts.{result['backup_table']}"
    assert conn.tables[backup_key] == existing
    assert len(conn.tables[srd.REAL_TABLE]) == 2  # substituida pela nova carga, nao somada


def test_do_sync_recusa_lista_vazia_sem_tocar_no_banco():
    conn = FakeConn(initial_tables={srd.REAL_TABLE: []})
    with pytest.raises(srd.SyncValidationError, match="0 linhas"):
        srd.do_sync([], conn)
    assert conn.executed == []
    assert conn.committed is False


def test_do_sync_recusa_numerador_maior_que_denominador():
    bad_rows = [_row(uf_known_orders=9, uf_eligible_orders=1)]
    conn = FakeConn(initial_tables={srd.REAL_TABLE: []})
    with pytest.raises(srd.SyncValidationError, match="numerador > denominador"):
        srd.do_sync(bad_rows, conn)
    assert conn.rolled_back is True
    assert conn.committed is False


def test_do_sync_rollback_completo_se_lock_falha():
    conn = FakeConn(initial_tables={srd.REAL_TABLE: []}, lock_should_fail=True)
    with pytest.raises(RuntimeError, match="lock"):
        srd.do_sync(SAMPLE_ROWS, conn)
    assert conn.rolled_back is True
    assert conn.committed is False
    # nada foi truncado/inserido na tabela real
    assert conn.tables[srd.REAL_TABLE] == []


def test_do_sync_rollback_se_falha_apos_truncate():
    conn = FakeConn(initial_tables={srd.REAL_TABLE: []}, fail_on_substring="TRUNCATE TABLE")
    with pytest.raises(RuntimeError):
        srd.do_sync(SAMPLE_ROWS, conn)
    assert conn.rolled_back is True
    assert conn.committed is False


def test_do_sync_nunca_referencia_datamart_url():
    """Guarda-corpo: do_sync so' recebe uma conexao Neon ja aberta -- nunca
    le DATAMART_DATABASE_URL nem abre conexao com a fonte."""
    import inspect
    src = inspect.getsource(srd.do_sync)
    assert "DATAMART" not in src


# ---------------------------------------------------------------------------
# Validacao staging vs fonte via injecao de discrepancia
# ---------------------------------------------------------------------------
def test_do_sync_detecta_staging_divergente_da_fonte(monkeypatch):
    """Simula um bug de insercao: apos o INSERT na staging, uma linha some
    (execute_values falho). staging_agg != source_agg deve abortar."""
    def _lossy_execute_values(cur, sql, batch, page_size=500):
        _fake_execute_values(cur, sql, batch[:-1], page_size)  # perde a ultima linha

    monkeypatch.setattr(srd, "execute_values", _lossy_execute_values)
    conn = FakeConn(initial_tables={srd.REAL_TABLE: []})
    with pytest.raises(srd.SyncValidationError, match="staging diverge da fonte"):
        srd.do_sync(SAMPLE_ROWS, conn)
    assert conn.rolled_back is True


# ---------------------------------------------------------------------------
# Auditoria (audit.source_sync_run)
# ---------------------------------------------------------------------------
def test_audit_start_finish_ciclo_sucesso():
    conn = FakeConn()
    run_id = srd._audit_start(conn, "marketplace_region_daily")
    assert conn.committed is True
    srd._audit_finish(conn, run_id, "success", 100, 100, "2026-01-01", "2026-01-31")
    assert conn.audit_runs[run_id]["status"] == "success"
    assert conn.audit_runs[run_id]["error_message"] is None


def test_audit_finish_registra_falha_sanitizada():
    conn = FakeConn()
    run_id = srd._audit_start(conn, "marketplace_region_daily")
    exc = RuntimeError("erro em postgresql://user:pass@host/db")
    srd._audit_finish(conn, run_id, "failed", 0, 0, error=srd._sanitize_error_message(exc))
    assert "pass" not in conn.audit_runs[run_id]["error_message"]
    assert conn.audit_runs[run_id]["status"] == "failed"


# ---------------------------------------------------------------------------
# run_sync — gating (flag + variavel de ambiente)
# ---------------------------------------------------------------------------
class _Args:
    def __init__(self, sync=False):
        self.sync = sync


def test_run_sync_recusa_sem_flag(monkeypatch):
    monkeypatch.delenv("I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY", raising=False)
    with pytest.raises(RuntimeError, match="--sync"):
        srd.run_sync(_Args(sync=False))


def test_run_sync_recusa_sem_variavel_de_ambiente(monkeypatch):
    monkeypatch.delenv("I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY", raising=False)
    with pytest.raises(RuntimeError, match="I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY"):
        srd.run_sync(_Args(sync=True))


def test_run_sync_prossegue_com_guardas_completas(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY", "1")

    class _DatamartCloseable:
        def close(self):
            pass

    datamart_conn = _DatamartCloseable()
    neon_conn = FakeConn(initial_tables={srd.REAL_TABLE: []})
    audit_conn = FakeConn()

    def fake_fetch(conn):
        assert conn is datamart_conn
        return list(SAMPLE_ROWS)

    monkeypatch.setattr(srd, "fetch_source_rows", fake_fetch)

    result = srd.run_sync(
        _Args(sync=True),
        connect_fn=lambda: (datamart_conn, neon_conn, audit_conn),
        tag_fn=lambda: "20260709_140000",
    )

    assert neon_conn.committed is True
    assert result["real_agg_after"]["n"] == 2
    assert audit_conn.audit_runs[1]["status"] == "success"
    assert audit_conn.audit_runs[1]["rows_extracted"] == 2


def test_run_sync_registra_falha_e_propaga(monkeypatch):
    monkeypatch.setenv("I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY", "1")
    neon_conn = FakeConn(initial_tables={srd.REAL_TABLE: []}, lock_should_fail=True)
    audit_conn = FakeConn()

    class _DatamartCloseable:
        def close(self):
            pass

    monkeypatch.setattr(srd, "fetch_source_rows", lambda conn: list(SAMPLE_ROWS))

    with pytest.raises(RuntimeError):
        srd.run_sync(
            _Args(sync=True),
            connect_fn=lambda: (_DatamartCloseable(), neon_conn, audit_conn),
            tag_fn=lambda: "20260709_150000",
        )

    assert audit_conn.audit_runs[1]["status"] == "failed"
    assert audit_conn.audit_runs[1]["error_message"] is not None


# ---------------------------------------------------------------------------
# run_diagnose — somente leitura
# ---------------------------------------------------------------------------
def test_run_diagnose_reporta_tabela_ausente():
    neon_conn = FakeConn(initial_tables={})

    class _DatamartCloseable:
        def close(self):
            pass

    def fake_connect():
        return _DatamartCloseable(), neon_conn

    import pipelines.sync_region_daily as mod
    original_fetch = mod.fetch_source_rows
    mod.fetch_source_rows = lambda conn: list(SAMPLE_ROWS)
    try:
        report = mod.run_diagnose(connect_fn=fake_connect)
    finally:
        mod.fetch_source_rows = original_fetch

    assert report["target_exists"] is False
    assert report["target_agg"] is None
    assert report["needs_sync"] is True
    assert neon_conn.closed is True


def test_run_diagnose_reporta_precisao_de_sync_quando_agregados_batem():
    neon_conn = FakeConn(initial_tables={"marts.fact_marketplace_region_daily": list(SAMPLE_ROWS)})

    class _DatamartCloseable:
        def close(self):
            pass

    def fake_connect():
        return _DatamartCloseable(), neon_conn

    import pipelines.sync_region_daily as mod
    original_fetch = mod.fetch_source_rows
    mod.fetch_source_rows = lambda conn: list(SAMPLE_ROWS)
    try:
        report = mod.run_diagnose(connect_fn=fake_connect)
    finally:
        mod.fetch_source_rows = original_fetch

    assert report["target_exists"] is True
    assert report["needs_sync"] is False


# ---------------------------------------------------------------------------
# Ausencia de PII / operacao destrutiva fora do alvo
# ---------------------------------------------------------------------------
def test_business_columns_nao_inclui_colunas_de_pii():
    """As colunas de negocio sincronizadas nunca incluem PII -- este e' o
    contrato real que a API/joins usam, nao uma varredura de texto no
    docstring (que legitimamente MENCIONA cpf/order_id/filename para
    explicar que eles nunca devem aparecer aqui)."""
    for pii_col in ("cpf", "nome", "buyer_name", "endereco", "order_id", "filename", "email", "telefone"):
        assert pii_col not in [c.lower() for c in srd.BUSINESS_COLUMNS]


def test_modulo_nao_tem_nenhum_drop_table_ou_truncate_sql_de_outra_tabela():
    """Unica instrucao SQL destrutiva permitida em todo o modulo e' o
    TRUNCATE TABLE da propria REAL_TABLE dentro de do_sync -- garante que
    nenhuma outra tabela (dimensoes, outras facts) e' truncada/dropada.
    Busca apenas por instrucoes SQL reais (TRUNCATE TABLE/DROP TABLE), nao
    pela palavra solta em comentarios/docstrings explicativos."""
    import inspect
    src = inspect.getsource(srd)
    assert "DROP TABLE" not in src.upper()
    truncate_lines = [l for l in src.splitlines() if "TRUNCATE TABLE" in l.upper()]
    assert len(truncate_lines) == 1
    assert "REAL_TABLE" in truncate_lines[0]


def test_idempotencia_rodar_do_sync_duas_vezes_com_mesma_fonte_da_mesmo_resultado():
    conn = FakeConn(initial_tables={srd.REAL_TABLE: []})
    result1 = srd.do_sync(SAMPLE_ROWS, conn, tag="20260709_160000")
    result2 = srd.do_sync(SAMPLE_ROWS, conn, tag="20260709_161000")

    assert result1["real_agg_after"] == result2["real_agg_after"]
    assert len(conn.tables[srd.REAL_TABLE]) == 2
