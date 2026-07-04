"""
Testes de pipelines/ingestion/shopee_raw/ddl.py — Fase Raw Shopee 2.

Usa conexões psycopg2 falsas — nenhum banco real é tocado.
"""
from __future__ import annotations

import pytest

from pipelines.ingestion.shopee_raw import ddl
from pipelines.ingestion.shopee_raw import write_conn as wc


# --- parse_ddl_statements ------------------------------------------------------

def test_parse_ddl_statements_remove_comentarios_de_linha():
    sql = "-- comentario\nCREATE TABLE x (id int);\n-- outro\n"
    assert ddl.parse_ddl_statements(sql) == ["CREATE TABLE x (id int)"]


def test_parse_ddl_statements_descarta_begin_commit_rollback():
    sql = "BEGIN;\nCREATE TABLE x (id int);\nCOMMIT;\n"
    assert ddl.parse_ddl_statements(sql) == ["CREATE TABLE x (id int)"]


def test_parse_ddl_statements_ponto_e_virgula_dentro_de_string_nao_quebra_statement():
    sql = (
        "COMMENT ON TABLE x IS 'algo (sem granularidade diaria); ainda a mesma frase';\n"
        "CREATE INDEX idx_x ON x (id);\n"
    )
    statements = ddl.parse_ddl_statements(sql)
    assert len(statements) == 2
    assert "sem granularidade diaria" in statements[0]
    assert statements[0].endswith("ainda a mesma frase'")
    assert statements[1] == "CREATE INDEX idx_x ON x (id)"


def test_parse_ddl_statements_comentario_apos_apostrofo_nao_e_cortado():
    # A palavra "isso — motivo" tem um travessão, não um comentário '--',
    # mas o teste cobre também um '--' literal dentro de string, se aparecer.
    sql = "COMMENT ON TABLE x IS 'trecho -- que parece comentario mas nao e';\n"
    statements = ddl.parse_ddl_statements(sql)
    assert len(statements) == 1
    assert "que parece comentario mas nao e" in statements[0]


def test_parse_ddl_statements_arquivo_real_gera_apenas_statements_esperados():
    text = ddl.DEFAULT_DDL_PATH.read_text(encoding="utf-8")
    statements = ddl.parse_ddl_statements(text)
    assert len(statements) > 10
    create_tables = [s for s in statements if s.upper().startswith("CREATE TABLE")]
    assert len(create_tables) == 4
    revokes = [s for s in statements if s.upper().startswith("REVOKE")]
    assert len(revokes) == 4


# --- execute_ddl (psycopg2 falso) ----------------------------------------------

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.conn.executed.append(norm)
        if self.conn.fail_on_substring and self.conn.fail_on_substring in norm:
            raise RuntimeError("falha simulada de execução do DDL")

    def fetchone(self):
        if self.conn.executed[-1].upper().startswith("SELECT PG_TRY_ADVISORY_LOCK"):
            return (self.conn.lock_acquired,)
        return (None,)


class FakeConn:
    def __init__(self, lock_acquired=True, fail_on_substring=None):
        self.executed = []
        self.autocommit = None
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.lock_acquired = lock_acquired
        self.fail_on_substring = fail_on_substring

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_execute_ddl_roda_todos_os_statements_e_commita(monkeypatch, tmp_path):
    ddl_path = tmp_path / "fake_ddl.sql"
    ddl_path.write_text("CREATE TABLE a (id int);\nCREATE INDEX idx_a ON a (id);\n")

    fake_conn = FakeConn()
    monkeypatch.setattr(ddl, "psycopg2", _FakePsycopg2Module(fake_conn))

    statements = ddl.execute_ddl("postgresql://writer@host/db", ddl_path=ddl_path)

    assert statements == ["CREATE TABLE a (id int)", "CREATE INDEX idx_a ON a (id)"]
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    assert fake_conn.closed is True
    assert any("CREATE TABLE a" in s for s in fake_conn.executed)
    assert any("lock_timeout" in s for s in fake_conn.executed)


def test_execute_ddl_rollback_completo_em_falha(monkeypatch, tmp_path):
    ddl_path = tmp_path / "fake_ddl.sql"
    ddl_path.write_text("CREATE TABLE a (id int);\nCREATE TABLE b (id int);\n")

    fake_conn = FakeConn(fail_on_substring="CREATE TABLE b")
    monkeypatch.setattr(ddl, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(RuntimeError, match="rollback completo"):
        ddl.execute_ddl("postgresql://writer@host/db", ddl_path=ddl_path)

    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False
    assert fake_conn.closed is True


def test_execute_ddl_bloqueia_se_advisory_lock_em_uso(monkeypatch, tmp_path):
    ddl_path = tmp_path / "fake_ddl.sql"
    ddl_path.write_text("CREATE TABLE a (id int);\n")

    fake_conn = FakeConn(lock_acquired=False)
    monkeypatch.setattr(ddl, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(wc.WritePreflightBlocked, match="advisory lock"):
        ddl.execute_ddl("postgresql://writer@host/db", ddl_path=ddl_path)

    assert fake_conn.closed is True
    # nao deve ter tentado criar nada
    assert not any(s.upper().startswith("CREATE TABLE") for s in fake_conn.executed)


def test_execute_ddl_erro_nunca_expoe_credencial(monkeypatch, tmp_path):
    ddl_path = tmp_path / "fake_ddl.sql"
    ddl_path.write_text("CREATE TABLE a (id int);\n")

    fake_conn = FakeConn(fail_on_substring="CREATE TABLE")
    monkeypatch.setattr(ddl, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(RuntimeError) as exc_info:
        ddl.execute_ddl("postgresql://writer:S3nh4Secreta@host/db", ddl_path=ddl_path)
    assert "S3nh4Secreta" not in str(exc_info.value)


class _FakePsycopg2Module:
    """Substitui o módulo psycopg2 real dentro de ddl.py (import local)."""

    def __init__(self, conn):
        self._conn = conn

    def connect(self, url, connect_timeout=15):
        return self._conn
