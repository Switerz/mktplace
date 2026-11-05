"""T-0054b: publicador do estoque Full ML em Connection REAL do SQLAlchemy (1.4 e 2.x).

Por que nao dublê: no Airflow (`apache/airflow:3.0.2`, SQLAlchemy 1.4.54) o
publicador quebrou com `AttributeError: 'Connection' object has no attribute
'rollback'` - `Connection.rollback()/commit()` e' API "2.0 style" que a Connection
legada do 1.4 nao tem. Os dubles de `test_publish_ml_full_stock_daily.py` tinham
`rollback()` e passavam nas duas versoes. Aqui engine, Connection, Session e o
proxy do pool sao os do SQLAlchemy instalado; o banco e' SQLite em memoria
(`marts`/`audit` sao bancos ATTACH) e so' o SQL especifico do Postgres e'
traduzido no cursor DBAPI, abaixo do SQLAlchemy.

Os caminhos cobertos sao os que fecham transacao fora de `engine.begin()`:
`publish_run_id_em_uso` e `diagnosticar` (leitura que termina em ROLLBACK),
`audit_start`/`audit_finish` (COMMIT na conexao DBAPI crua do pool) e
`_ler_fonte` (ROLLBACK da Session do Data Mart).

Para provar contra o 1.4 do Airflow, rode este arquivo num venv com
`sqlalchemy==1.4.54` (mais `psycopg2-binary`, `pydantic-settings`,
`python-dotenv`, `pytest`), alem da suite normal (2.x).
"""
from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pipelines import publish_ml_full_stock_daily as pub
from pipelines import sync_ml_full_stock_daily as fonte_mod

REF = date(2026, 10, 20)
PUB = "0123456789abcdef0123456789abcdef"
OUTRO = "fedcba9876543210fedcba9876543210"
ESPERADO = {"system_identifier": "7350000000000000001"}


class _Cursor(sqlite3.Cursor):
    """Cursor DBAPI com o que o publicador usa do psycopg2: `with` e paramstyle `%s`."""

    trilha: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def execute(self, sql, params=()):
        type(self).trilha.append(sql.strip())
        if sql.strip().upper().startswith("SET TRANSACTION"):
            sql = "SELECT 1"    # SQLite nao tem modo de transacao; o que se prova e' como ela fecha
        if isinstance(params, (list, tuple)):     # sem o adaptador de date do sqlite3 (deprecado no 3.12)
            params = tuple(p.isoformat() if isinstance(p, date) else p for p in params)
        return super().execute(sql.replace("%s", "?"), params)


class _Dbapi(sqlite3.Connection):
    def cursor(self, factory=_Cursor):
        return super().cursor(factory)


@pytest.fixture
def banco(monkeypatch):
    """Engine REAL (pool estatico: uma conexao SQLite) + trilha de transacoes e comandos."""
    existentes: set[str] = set()
    trilha: list = []

    def criar():
        c = sqlite3.connect(":memory:", factory=_Dbapi, check_same_thread=False)
        c.execute("ATTACH DATABASE ':memory:' AS marts")
        c.execute("ATTACH DATABASE ':memory:' AS audit")
        c.create_function("to_regclass", 1, lambda nome: nome if nome in existentes else None)
        c.create_function("NOW", 0, lambda: "2026-10-20 12:00:00")
        return c

    engine = create_engine("sqlite://", creator=criar, poolclass=StaticPool)
    with engine.connect():       # 1o connect: a inicializacao do dialeto fica fora da trilha
        pass
    with engine.begin() as conn:
        for t in pub.TABELAS:
            conn.execute(text(f"CREATE TABLE {t} (ref_date TEXT, brand TEXT, source_run_id TEXT)"))
            existentes.add(t)
        conn.execute(text("""CREATE TABLE audit.source_sync_run (
            sync_run_id INTEGER PRIMARY KEY, source_name TEXT, marketplace_id INT, loja_id INT,
            status TEXT, started_at TEXT, finished_at TEXT, rows_extracted INT, rows_loaded INT,
            error_message TEXT)"""))
    for nome in ("begin", "commit", "rollback"):
        event.listen(engine, nome, lambda conn, _n=nome: trilha.append(("tx", _n)))
    _Cursor.trilha = trilha
    monkeypatch.setattr(pub, "local_engine", lambda: engine)
    monkeypatch.setattr(pub, "LocalSession", sessionmaker(bind=engine))
    monkeypatch.setattr(pub, "DataMartSession", sessionmaker(bind=engine))
    monkeypatch.setattr(pub, "conferir_identidade", lambda cur, esperado: trilha.append(("identidade", esperado)))
    yield engine, existentes, trilha
    _Cursor.trilha = []
    engine.dispose()


def _gravar(engine, tabela, *linhas):
    with engine.begin() as conn:
        for ref, brand, sid in linhas:
            conn.execute(text(f"INSERT INTO {tabela} VALUES (:r, :b, :s)"), {"r": str(ref), "b": brand, "s": sid})


def _transacoes(trilha):
    return [x[1] for x in trilha if isinstance(x, tuple) and x[0] == "tx"]


def _assert_leitura_que_reverte(trilha):
    """BEGIN explicito antes de qualquer comando, READ ONLY primeiro, fecho por ROLLBACK, nunca COMMIT."""
    assert trilha[0] == ("tx", "begin") and trilha[1] == "SET TRANSACTION READ ONLY", trilha[:2]
    assert trilha[-1] == ("tx", "rollback")
    assert _transacoes(trilha) == ["begin", "rollback"]


def test_publish_run_id_em_uso_em_connection_real(banco):
    engine, _, trilha = banco
    assert pub.publish_run_id_em_uso(PUB) is False
    _assert_leitura_que_reverte(trilha)
    _gravar(engine, pub.FACT_STATUS, (REF, "barbours", PUB))
    trilha.clear()
    assert pub.publish_run_id_em_uso(PUB) is True
    _assert_leitura_que_reverte(trilha)


def test_publish_run_id_em_uso_reverte_mesmo_com_erro_de_sql(banco):
    engine, existentes, trilha = banco
    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE {pub.FACT_MARCA}"))
    existentes.discard(pub.FACT_MARCA)
    trilha.clear()
    with pytest.raises(Exception, match="no such table"):
        pub.publish_run_id_em_uso(PUB)
    assert _transacoes(trilha) == ["begin", "rollback"]


def test_diagnosticar_em_connection_real(banco):
    engine, _, trilha = banco
    _gravar(engine, pub.FACT_INVENTARIO, (REF, "barbours", PUB), (REF, "barbours", PUB), (REF, "kokeshi", OUTRO))
    _gravar(engine, pub.FACT_MARCA, (REF, "barbours", PUB), (REF, "kokeshi", OUTRO))
    trilha.clear()
    r = pub.diagnosticar(REF, PUB)
    assert r["veredito"] == pub.ESTADO_COMMIT and r["total_linhas_do_run"] == 3
    assert r["linhas_do_run"][pub.FACT_INVENTARIO] == {"barbours": 2}
    assert r["source_run_id_vigente"] == {"barbours": PUB, "kokeshi": OUTRO}
    assert r["vigente_para_as_marcas_do_run"] is True and r["grava"] is False
    _assert_leitura_que_reverte(trilha)


def test_diagnosticar_sem_029_reverte_no_return_antecipado(banco):
    _, existentes, trilha = banco
    existentes.discard(pub.FACT_STATUS)
    r = pub.diagnosticar(REF, PUB)
    assert r["veredito"] == "fato_inexistente"
    _assert_leitura_que_reverte(trilha)


def test_auditoria_commita_na_conexao_crua_do_pool(banco):
    engine, _, trilha = banco
    sid = pub.audit_start(5, ESPERADO)
    # A conexao ja' voltou ao pool, que reverte o que nao foi commitado: a linha so' existe se houve COMMIT.
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status, rows_extracted, marketplace_id FROM audit.source_sync_run "
                                 "WHERE sync_run_id = :s"), {"s": sid}).fetchone() == ("running", 5, 2)
    pub.audit_finish(sid, "success", rows_loaded=5, esperado=ESPERADO)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT status, rows_loaded FROM audit.source_sync_run WHERE sync_run_id = :s"),
                            {"s": sid}).fetchone() == ("success", 5)
    # identidade conferida nas DUAS conexoes, antes do INSERT e do UPDATE
    marcos = [x[0] if isinstance(x, tuple) else x.split()[0] for x in trilha]
    assert marcos.index("identidade") < marcos.index("INSERT")
    assert len([m for m in marcos if m == "identidade"]) == 2
    assert marcos.index("UPDATE") > [i for i, m in enumerate(marcos) if m == "identidade"][1]


def test_auditoria_sem_linha_nao_commita(banco):
    engine, _, _ = banco
    with pytest.raises(RuntimeError, match="exatamente 1 linha"):
        pub.audit_finish(999, "success", rows_loaded=1, esperado=ESPERADO)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM audit.source_sync_run")).scalar() == 0


def test_ler_fonte_reverte_a_session_real(banco, monkeypatch):
    _, _, trilha = banco
    conns = []

    def iniciar(conn):
        conns.append(conn)
        conn.execute(text("SET TRANSACTION READ ONLY"))

    def ler(conn, ref_date):
        conns.append(conn)
        return {"ref": conn.execute(text("SELECT :r"), {"r": str(ref_date)}).scalar()}

    monkeypatch.setattr(fonte_mod, "iniciar_leitura_consistente", iniciar)
    monkeypatch.setattr(fonte_mod, "ler_fonte", ler)
    assert pub._ler_fonte(REF) == {"ref": str(REF)}
    assert len({id(c) for c in conns}) == 1           # mesma conexao, mesma transacao
    _assert_leitura_que_reverte(trilha)
