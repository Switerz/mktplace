"""Gate FULL-SH-1C-R/V — consistencia transacional da resposta Shopee FBS.

A resposta e' montada com SETE consultas. Sem controle de isolamento, o
PostgreSQL usa READ COMMITTED e cada statement enxerga um snapshot NOVO --
enquanto o pipeline publica com DELETE + INSERT da janela numa transacao.
Uma republicacao que commite no meio da montagem faria `totals` vir de um
instante e `daily` de outro, e FBS + seller deixaria de fechar o total.

O teste de contrato trava a PROPRIEDADE no codigo. O teste de integracao
prova o COMPORTAMENTO contra um PostgreSQL real, e so' roda quando ha um
disponivel (`TEST_PG_DSN`); caso contrario e' pulado, nunca falseado.
"""
from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from app.services import shopee_fbs_service as svc

RAIZ = Path(__file__).resolve().parents[1]
SERVICE_SRC = io.open(RAIZ / "app" / "services" / "shopee_fbs_service.py",
                      encoding="utf-8").read()


# ---------------------------------------------------------------------------
# Contrato: a propriedade esta no codigo e nao pode sumir num refactor
# ---------------------------------------------------------------------------

def test_resposta_fixa_um_snapshot_para_todas_as_consultas():
    sql = str(svc.SQL_SNAPSHOT_COERENTE)
    assert "REPEATABLE READ" in sql, (
        "sem REPEATABLE READ cada consulta pega um snapshot novo")
    assert "READ ONLY" in sql, (
        "READ ONLY e' defesa em profundidade neste caminho de leitura")
    assert "SET TRANSACTION" in sql


def test_snapshot_e_o_primeiro_comando_da_transacao():
    """`SET TRANSACTION` so' e' aceito ANTES de qualquer query."""
    corpo = SERVICE_SRC.split("def get_shopee_fbs_block")[1]
    i_set = corpo.index("SQL_SNAPSHOT_COERENTE")
    for consulta in ("SQL_POR_CLASSE", "SQL_POR_MARCA", "SQL_POR_CONTA",
                     "SQL_DIARIO", "SQL_ESCOPO", "SQL_LIMITES_FATO",
                     "SQL_CONTAS_OBSERVADAS"):
        assert i_set < corpo.index("db.execute(" + consulta), (
            f"{consulta} nao pode ser executada antes de fixar o snapshot")
    # E o rollback garante que a transacao comece limpa.
    assert corpo.index("db.rollback()") < i_set


def test_snapshot_nao_altera_configuracao_global():
    """Escopo e' a TRANSACAO: nada de SET SESSION nem ALTER DATABASE."""
    assert "SET SESSION" not in SERVICE_SRC.upper()
    assert "ALTER DATABASE" not in SERVICE_SRC.upper()
    assert "ALTER ROLE" not in SERVICE_SRC.upper()
    # `SET TRANSACTION` (sem SESSION) vale so' para a transacao corrente.
    assert "SET TRANSACTION" in str(svc.SQL_SNAPSHOT_COERENTE)


def test_todas_as_consultas_da_resposta_estao_na_mesma_transacao():
    """Nenhum commit/rollback intermediario parte o snapshot ao meio."""
    corpo = SERVICE_SRC.split("def get_shopee_fbs_block")[1]
    depois_do_set = corpo.split("SQL_SNAPSHOT_COERENTE", 1)[1]
    assert "db.commit()" not in depois_do_set
    # O unico rollback permitido e' o que PRECEDE o SET.
    assert "db.rollback()" not in depois_do_set


# ---------------------------------------------------------------------------
# Integracao: comportamento contra PostgreSQL real
# ---------------------------------------------------------------------------

DSN = os.getenv("TEST_PG_DSN")
pg = pytest.mark.skipif(not DSN, reason="TEST_PG_DSN nao configurado")

DDL = """
CREATE SCHEMA IF NOT EXISTS marts;
DROP TABLE IF EXISTS marts.fact_shopee_fbs_daily;
CREATE TABLE marts.fact_shopee_fbs_daily (
  ref_date date, brand text, shop_account text, fbs_class text,
  created_orders bigint, eligible_orders bigint, cancelled_orders bigint,
  to_return_orders bigint, unpaid_orders bigint,
  gross_gmv numeric, gross_units bigint, to_return_gmv numeric,
  unpaid_gmv numeric, handling_seconds_sum bigint,
  handling_sample_count bigint, source_max_ingested_at timestamptz,
  ingested_at timestamptz default now(), source_run_id varchar(64));
INSERT INTO marts.fact_shopee_fbs_daily VALUES
 ('2026-08-01','barbours','barbours','fbs',10,10,0,0,0,1000,10,0,0,3600,5,now(),now(),'r1'),
 ('2026-08-01','barbours','barbours','seller',5,5,0,0,0,500,5,0,0,1800,3,now(),now(),'r1');
"""


@pg
def test_integracao_republicacao_concorrente_nao_parte_a_resposta():
    """Republica a janela NO MEIO da montagem e exige resposta coerente."""
    import psycopg2

    prep = psycopg2.connect(DSN)
    prep.autocommit = True
    prep.cursor().execute(DDL)
    prep.close()

    leitor = psycopg2.connect(DSN)
    leitor.autocommit = False
    c = leitor.cursor()
    c.execute(str(svc.SQL_SNAPSHOT_COERENTE))

    TOTAL = "select coalesce(sum(gross_gmv),0) from marts.fact_shopee_fbs_daily"
    c.execute(TOTAL)
    antes = c.fetchone()[0]

    # O pipeline publica e COMMITA enquanto a resposta e' montada.
    pub = psycopg2.connect(DSN)
    pub.autocommit = True
    pub.cursor().execute(
        "update marts.fact_shopee_fbs_daily set gross_gmv = gross_gmv * 2")
    pub.close()

    c.execute(TOTAL)
    depois = c.fetchone()[0]
    c.execute("select coalesce(sum(gross_gmv),0) from marts.fact_shopee_fbs_daily "
              "where ref_date = '2026-08-01'")
    diario = c.fetchone()[0]
    c.execute("show transaction_isolation")
    nivel = c.fetchone()[0]
    leitor.rollback()
    leitor.close()

    assert nivel == "repeatable read"
    assert antes == depois == diario, (
        f"resposta partida entre snapshots: {antes} / {depois} / {diario}")


@pg
def test_integracao_transacao_read_only_recusa_escrita():
    """Defesa em profundidade: escrita neste caminho e' recusada pelo banco."""
    import psycopg2

    prep = psycopg2.connect(DSN)
    prep.autocommit = True
    prep.cursor().execute(DDL)
    prep.close()

    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    c = conn.cursor()
    c.execute(str(svc.SQL_SNAPSHOT_COERENTE))
    with pytest.raises(psycopg2.Error) as e:
        c.execute("update marts.fact_shopee_fbs_daily set gross_gmv = 1")
    assert "read-only" in str(e.value).lower()
    conn.rollback()
    conn.close()
