"""
Testes das guardas de seguranca adicionadas a pipelines/sync_produtos.py:
validacao de origem/destino, rollback em falha, e abortar sync quando a
fonte retorna um volume de linhas suspeito (queda >50% vs Neon atual).

Usa conexoes psycopg2 falsas — nenhuma credencial real e' necessaria.
"""
import pytest

import pipelines.sync_produtos as sp


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._last_sql = ""

    def execute(self, sql, params=None):
        self._last_sql = sql
        self.conn.executed.append((" ".join(sql.split()), params))
        if self.conn.raise_on_execute and "INSERT INTO marts.fact_" not in sql and "SELECT" not in sql.upper():
            raise RuntimeError("erro simulado de execucao")

    def fetchone(self):
        if "RETURNING sync_run_id" in self._last_sql:
            self.conn.run_id_counter += 1
            return (self.conn.run_id_counter,)
        if "SELECT COUNT(*)" in self._last_sql:
            return (self.conn.prev_count,)
        if "MAX(date)" in self._last_sql:
            return (self.conn.max_date,)
        return None

    def fetchall(self):
        return self.conn.fetchall_result

    def close(self):
        pass


class FakeConn:
    def __init__(self, prev_count=0, max_date=None, fetchall_result=None):
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.run_id_counter = 0
        self.prev_count = prev_count
        self.max_date = max_date
        self.fetchall_result = fetchall_result or []
        self.raise_on_execute = False

    def cursor(self, cursor_factory=None):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _tiktok_row(**overrides):
    row = {
        "date": "2026-06-25", "brand": "kokeshi", "product_id": "p1", "product_name": "Produto",
        "gmv": 10.0, "orders": 1, "items_sold": 1,
        "gmv_video": 0, "gmv_live": 0, "gmv_product_card": 0,
        "items_sold_video": 0, "items_sold_live": 0, "items_sold_product_card": 0,
        "pct_gmv_video": None, "pct_gmv_live": None, "pct_gmv_card": None,
        "canceled": 0, "refunded": 0, "returned": 0, "problem_rate": None,
        "rating_avg": None, "total_ratings": None,
    }
    row.update(overrides)
    return row


def test_assert_distinct_targets_bloqueia_destino_igual_a_fonte(monkeypatch):
    monkeypatch.setattr(sp, "NEON_URL", "postgresql://mesmo-host/db")
    monkeypatch.setattr(sp, "RDS_URL", "postgresql://mesmo-host/db")
    monkeypatch.setattr(sp, "LOCAL_URL", "postgresql://outro-host/db")
    with pytest.raises(RuntimeError, match="banco errado"):
        sp._assert_distinct_targets()


def test_assert_distinct_targets_permite_hosts_diferentes(monkeypatch):
    monkeypatch.setattr(sp, "NEON_URL", "postgresql://neon-host/db")
    monkeypatch.setattr(sp, "RDS_URL", "postgresql://rds-host/db")
    monkeypatch.setattr(sp, "LOCAL_URL", "postgresql://local-host/db")
    sp._assert_distinct_targets()  # nao deve levantar


def test_sync_shopee_sem_linhas_novas_registra_sucesso_com_zero(monkeypatch):
    audit_conn = FakeConn()
    local_conn = FakeConn(fetchall_result=[])

    monkeypatch.setattr(sp, "_neon", lambda: audit_conn)
    monkeypatch.setattr(sp, "_local", lambda: local_conn)

    result = sp.sync_shopee(full=True, brands={"apice"})

    assert result == {"source": 0, "upserted": 0}
    # audit deve registrar sucesso com 0 linhas, nao falha
    statuses = [p[0] for _, p in audit_conn.executed if p and "success" in p]
    assert statuses, f"esperado status 'success' na auditoria, executed={audit_conn.executed}"


def test_sync_ml_queda_suspeita_de_linhas_aborta_e_registra_falha(monkeypatch):
    # Neon ja tem 1000 linhas; RDS agora so' retorna 10 -> queda > 50%, deve abortar.
    audit_conn = FakeConn(prev_count=1000)
    rds_conn = FakeConn(fetchall_result=[{"brand": "kokeshi", "item_id": str(i)} for i in range(10)])

    monkeypatch.setattr(sp, "_neon", lambda: audit_conn)
    monkeypatch.setattr(sp, "_rds", lambda: rds_conn)

    with pytest.raises(RuntimeError, match="queda suspeita"):
        sp.sync_ml(brands={"kokeshi"})

    statuses = [p[0] for _, p in audit_conn.executed if p and "failed" in p]
    assert statuses, f"esperado status 'failed' na auditoria, executed={audit_conn.executed}"
    assert audit_conn.committed  # audit_start e audit_finish sempre commitam a auditoria


def test_sync_tiktok_rollback_ao_falhar_upsert(monkeypatch):
    neon_conns = []

    def fake_neon():
        # 1a chamada = conexao de auditoria/consulta MAX(date); 2a = conexao de escrita (dst)
        c = FakeConn(max_date=None, fetchall_result=[])
        neon_conns.append(c)
        return c

    rds_conn = FakeConn(fetchall_result=[_tiktok_row(), _tiktok_row(product_id="p2")])

    monkeypatch.setattr(sp, "_neon", fake_neon)
    monkeypatch.setattr(sp, "_rds", lambda: rds_conn)

    def failing_execute_values(cur, sql, batch, page_size=500):
        raise RuntimeError("falha simulada no upsert")

    monkeypatch.setattr(sp, "execute_values", failing_execute_values)

    with pytest.raises(RuntimeError, match="falha simulada"):
        sp.sync_tiktok(days=7, full=False, brands={"kokeshi"})

    assert len(neon_conns) == 2, "esperado: 1 conexao de auditoria/consulta + 1 de escrita (dst)"
    audit_conn, dst_conn = neon_conns
    assert dst_conn.rolled_back is True
    assert dst_conn.committed is False
    failed_updates = [p for _, p in audit_conn.executed if p and "failed" in p]
    assert failed_updates, f"esperado UPDATE de auditoria com status failed, executed={audit_conn.executed}"


def test_sync_tiktok_sem_linhas_novas_nao_levanta_erro(monkeypatch):
    audit_conn = FakeConn(max_date=None)
    rds_conn = FakeConn(fetchall_result=[])

    monkeypatch.setattr(sp, "_neon", lambda: audit_conn)
    monkeypatch.setattr(sp, "_rds", lambda: rds_conn)

    result = sp.sync_tiktok(days=7, full=False, brands={"kokeshi"})

    assert result == {"source": 0, "upserted": 0}
