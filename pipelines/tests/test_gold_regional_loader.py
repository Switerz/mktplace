"""
Testes de pipelines/ingestion/gold_regional/loader.py — Gate 6A.3 (carga
inicial) e Gate 6C (refresh incremental + CLI).

Usa conexões/cursores psycopg2 falsos — nenhum banco real é tocado. As
respostas de `fetchone()` são escolhidas por SUBSTRING reconhecível de cada
query (mesmo padrão de test_gold_regional_write_conn.py), o que deixa os
testes robustos a pequenos reordenamentos das queries reais.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pipelines.ingestion.gold_regional import loader
from pipelines.ingestion.gold_regional import write_conn as wc


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

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
            raise RuntimeError("falha simulada de execução")
        upper = norm.upper()
        if upper.startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY"):
            self.conn.final_insert_executed = True

    def fetchone(self):
        last = self.conn.executed[-1]
        upper = last.upper()

        if "PG_TRY_ADVISORY_LOCK" in upper:
            return (self.conn.lock_acquired,)
        if "PG_ADVISORY_UNLOCK" in upper:
            return (True,)

        for matcher, value in self.conn.fetchone_responses:
            if matcher(upper):
                return value

        raise AssertionError(f"nenhuma resposta simulada para a query: {last!r}")


class FakeConn:
    def __init__(
        self,
        lock_acquired=True,
        fail_on_substring=None,
        fetchone_responses=None,
        final_insert_rowcount=10,
    ):
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.lock_acquired = lock_acquired
        self.fail_on_substring = fail_on_substring
        self.fetchone_responses = fetchone_responses or []
        self.final_insert_executed = False
        self._final_insert_rowcount = final_insert_rowcount

    def cursor(self):
        return _RowcountAwareCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _RowcountAwareCursor(FakeCursor):
    """Extensão do FakeCursor que expõe `.rowcount` após o INSERT final,
    igual a um cursor psycopg2 real."""

    @property
    def rowcount(self):
        return self.conn._final_insert_rowcount if self.conn.final_insert_executed else 0


class _FakePsycopg2Module:
    def __init__(self, conn):
        self._conn = conn

    def connect(self, url, connect_timeout=15):
        return self._conn


def _exact(pattern):
    """Matcher por igualdade exata (apos normalizar espacos/maiusculas) —
    usado para a query de rowcount, que e uma substring literal de varias
    outras queries com WHERE e por isso nao pode ser reconhecida por
    'contains' sem ambiguidade."""
    return lambda upper: upper == pattern


def _contains(substring):
    return lambda upper: substring in upper


# Respostas "felizes" — staging não-vazio, sem duplicidade/nulos/numerador
# inválido, GMV staging == GMV fonte (Shopee e ML), 0 linhas TikTok.
# A ORDEM importa: matchers mais especificos (WHERE ... =N) antes dos mais
# genericos, e a query de rowcount usa _exact para nunca ser confundida com
# as queries com WHERE que a contem como substring.
_HAPPY_RESPONSES = [
    (_contains("HAVING COUNT(*) > 1"), (0,)),
    (_contains("IS NULL OR MARKETPLACE_ID IS NULL"), (0,)),
    (_contains("UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS"), (0,)),
    (_contains(f"WHERE MARKETPLACE_ID = {loader.SHOPEE_MARKETPLACE_ID}"), (Decimal("1000.00"),)),
    (_contains("SHOPEE_WINNING_FILE"), (Decimal("1000.00"),)),
    (_contains(f"WHERE MARKETPLACE_ID = {loader.ML_MARKETPLACE_ID}"), (Decimal("2000.00"),)),
    (_contains("RAW.ML_ORDERS WHERE STATUS = 'PAID'"), (Decimal("2000.00"),)),
    (_contains(f"WHERE MARKETPLACE_ID = {loader.TIKTOK_MARKETPLACE_ID}"), (0,)),
    (_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (100,)),
]


def _happy_conn(**overrides):
    return FakeConn(fetchone_responses=_HAPPY_RESPONSES, **overrides)


# ---------------------------------------------------------------------------
# Caminho feliz: ordem, commit, resultado
# ---------------------------------------------------------------------------

def test_execute_first_load_ordem_lock_staging_validacao_insert_validacao_commit(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    result = loader.execute_first_load("postgresql://writer@host/db")

    order = [s.upper() for s in fake_conn.executed]

    def idx(substr):
        return next(i for i, s in enumerate(order) if substr in s)

    i_lock = idx("PG_TRY_ADVISORY_LOCK")
    i_staging = idx("CREATE TEMP TABLE")
    i_shopee_insert = idx("SILVER.STG_SHOPEE_ORDER_ITEM_SNAPSHOTS")
    i_ml_insert = idx("RAW.ML_ORDERS")
    i_rowcount_check = idx("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY")
    i_final_insert = idx("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY")
    i_tiktok_check = idx(f"GOLD.MARKETPLACE_REGION_DAILY WHERE MARKETPLACE_ID = {loader.TIKTOK_MARKETPLACE_ID}")
    i_unlock = idx("PG_ADVISORY_UNLOCK")

    # Ordem: lock -> staging -> transform (shopee/ml) -> validacoes (usam o
    # rowcount check) -> insert final -> validacao pos-insert -> unlock.
    assert i_lock < i_staging < i_shopee_insert
    assert i_shopee_insert < i_ml_insert
    assert i_ml_insert < i_rowcount_check < i_final_insert
    assert i_final_insert < i_tiktok_check < i_unlock

    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    assert fake_conn.closed is True
    assert result.rows_inserted == 10
    assert result.tiktok_rows == 0
    assert result.shopee_gmv_staging == result.shopee_gmv_source == Decimal("1000.00")
    assert result.ml_gmv_staging == result.ml_gmv_source == Decimal("2000.00")


# ---------------------------------------------------------------------------
# Rollback em falha de validação (cada checagem isolada)
# ---------------------------------------------------------------------------

def test_execute_first_load_aborta_se_staging_vazio(monkeypatch):
    responses = [(_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (0,))]
    fake_conn = FakeConn(fetchone_responses=responses)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(loader.NothingToLoadError):
        loader.execute_first_load("postgresql://writer@host/db")

    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False
    assert fake_conn.final_insert_executed is False


def test_execute_first_load_aborta_se_duplicidade(monkeypatch):
    responses = [
        (_contains("HAVING COUNT(*) > 1"), (3,)),
        (_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (100,)),
    ]
    fake_conn = FakeConn(fetchone_responses=responses)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(loader.LoadValidationError, match="duplicada"):
        loader.execute_first_load("postgresql://writer@host/db")

    assert fake_conn.rolled_back is True
    assert fake_conn.final_insert_executed is False


def test_execute_first_load_aborta_se_nulos_obrigatorios(monkeypatch):
    responses = [
        (_contains("HAVING COUNT(*) > 1"), (0,)),
        (_contains("IS NULL OR MARKETPLACE_ID IS NULL"), (7,)),
        (_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (100,)),
    ]
    fake_conn = FakeConn(fetchone_responses=responses)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(loader.LoadValidationError, match="nula"):
        loader.execute_first_load("postgresql://writer@host/db")

    assert fake_conn.rolled_back is True
    assert fake_conn.final_insert_executed is False


def test_execute_first_load_aborta_se_numerador_maior_que_denominador(monkeypatch):
    responses = [
        (_contains("HAVING COUNT(*) > 1"), (0,)),
        (_contains("IS NULL OR MARKETPLACE_ID IS NULL"), (0,)),
        (_contains("UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS"), (2,)),
        (_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (100,)),
    ]
    fake_conn = FakeConn(fetchone_responses=responses)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(loader.LoadValidationError, match="numerador > denominador"):
        loader.execute_first_load("postgresql://writer@host/db")

    assert fake_conn.rolled_back is True
    assert fake_conn.final_insert_executed is False


def test_execute_first_load_aborta_se_gmv_shopee_nao_reconcilia(monkeypatch):
    responses = [
        (_contains("HAVING COUNT(*) > 1"), (0,)),
        (_contains("IS NULL OR MARKETPLACE_ID IS NULL"), (0,)),
        (_contains("UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS"), (0,)),
        (_contains(f"WHERE MARKETPLACE_ID = {loader.SHOPEE_MARKETPLACE_ID}"), (Decimal("1000.00"),)),
        (_contains("SHOPEE_WINNING_FILE"), (Decimal("999.00"),)),  # diverge > tolerancia
        (_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (100,)),
    ]
    fake_conn = FakeConn(fetchone_responses=responses)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(loader.LoadValidationError, match="GMV Shopee"):
        loader.execute_first_load("postgresql://writer@host/db")

    assert fake_conn.rolled_back is True
    assert fake_conn.final_insert_executed is False


def test_execute_first_load_aborta_se_gmv_ml_nao_reconcilia(monkeypatch):
    responses = [
        (_contains("HAVING COUNT(*) > 1"), (0,)),
        (_contains("IS NULL OR MARKETPLACE_ID IS NULL"), (0,)),
        (_contains("UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS"), (0,)),
        (_contains(f"WHERE MARKETPLACE_ID = {loader.SHOPEE_MARKETPLACE_ID}"), (Decimal("1000.00"),)),
        (_contains("SHOPEE_WINNING_FILE"), (Decimal("1000.00"),)),
        (_contains(f"WHERE MARKETPLACE_ID = {loader.ML_MARKETPLACE_ID}"), (Decimal("2000.00"),)),
        (_contains("RAW.ML_ORDERS WHERE STATUS = 'PAID'"), (Decimal("1500.00"),)),  # diverge
        (_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (100,)),
    ]
    fake_conn = FakeConn(fetchone_responses=responses)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(loader.LoadValidationError, match="GMV ML"):
        loader.execute_first_load("postgresql://writer@host/db")

    assert fake_conn.rolled_back is True
    assert fake_conn.final_insert_executed is False


def test_execute_first_load_aborta_se_tiktok_inserido(monkeypatch):
    responses = [
        (_contains("HAVING COUNT(*) > 1"), (0,)),
        (_contains("IS NULL OR MARKETPLACE_ID IS NULL"), (0,)),
        (_contains("UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS"), (0,)),
        (_contains(f"WHERE MARKETPLACE_ID = {loader.SHOPEE_MARKETPLACE_ID}"), (Decimal("1000.00"),)),
        (_contains("SHOPEE_WINNING_FILE"), (Decimal("1000.00"),)),
        (_contains(f"WHERE MARKETPLACE_ID = {loader.ML_MARKETPLACE_ID}"), (Decimal("2000.00"),)),
        (_contains("RAW.ML_ORDERS WHERE STATUS = 'PAID'"), (Decimal("2000.00"),)),
        (_contains(f"GOLD.MARKETPLACE_REGION_DAILY WHERE MARKETPLACE_ID = {loader.TIKTOK_MARKETPLACE_ID}"), (1,)),  # simula 1 linha TikTok
        (_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (100,)),
    ]
    fake_conn = FakeConn(fetchone_responses=responses)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(loader.LoadValidationError, match="TikTok"):
        loader.execute_first_load("postgresql://writer@host/db")

    assert fake_conn.rolled_back is True
    # o insert final ja rodou (a checagem TikTok e POS-insert), mas o
    # commit nunca aconteceu -- rollback desfaz tudo.
    assert fake_conn.committed is False


def test_execute_first_load_rollback_em_erro_de_execucao_generico(monkeypatch):
    fake_conn = FakeConn(fail_on_substring="INSERT INTO STG_MARKETPLACE_REGION_DAILY")
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(RuntimeError, match="rollback completo executado"):
        loader.execute_first_load("postgresql://writer@host/db")

    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


# ---------------------------------------------------------------------------
# Advisory lock / sem retry
# ---------------------------------------------------------------------------

def test_execute_first_load_bloqueia_se_advisory_lock_em_uso(monkeypatch):
    fake_conn = FakeConn(lock_acquired=False)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(wc.WritePreflightBlocked, match="advisory lock"):
        loader.execute_first_load("postgresql://writer@host/db")

    assert fake_conn.closed is True
    assert not any("CREATE TEMP TABLE" in s.upper() for s in fake_conn.executed)


def test_execute_first_load_nao_faz_retry_automatico(monkeypatch):
    """Uma unica chamada = uma unica tentativa de conexao/execucao. Nao ha
    loop de retry em execute_first_load."""
    calls = {"n": 0}

    class CountingModule(_FakePsycopg2Module):
        def connect(self, url, connect_timeout=15):
            calls["n"] += 1
            return self._conn

    fake_conn = FakeConn(fail_on_substring="INSERT INTO STG_MARKETPLACE_REGION_DAILY")
    monkeypatch.setattr(loader, "psycopg2", CountingModule(fake_conn))

    with pytest.raises(RuntimeError):
        loader.execute_first_load("postgresql://writer@host/db")

    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Segurança: nunca vaza mensagem nativa do driver
# ---------------------------------------------------------------------------

def test_execute_first_load_erro_generico_nunca_expoe_mensagem_nativa(monkeypatch):
    class FailingConn(FakeConn):
        def cursor(self):
            class _Cur(_RowcountAwareCursor):
                def execute(self_inner, sql, params=None):
                    norm = " ".join(sql.split())
                    self_inner.conn.executed.append(norm)
                    if "INSERT INTO STG_MARKETPLACE_REGION_DAILY" in norm.upper():
                        raise RuntimeError(
                            'connection to server at "prod-db.example.rds.amazonaws.com" '
                            '(10.0.0.5), port 5432 failed: FATAL: password authentication failed for user "postgres"'
                        )
            return _Cur(self)

    fake_conn = FailingConn()
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(RuntimeError) as exc_info:
        loader.execute_first_load("postgresql://writer@host/db")

    msg = str(exc_info.value)
    assert "prod-db.example.rds.amazonaws.com" not in msg
    assert "10.0.0.5" not in msg
    assert "postgres" not in msg


# ---------------------------------------------------------------------------
# Nenhuma constante histórica rígida de GMV
# ---------------------------------------------------------------------------

def test_gmv_da_fonte_e_sempre_recalculado_nunca_uma_constante_fixa():
    """As queries de reconciliacao tem que ser SELECTs sobre a fonte viva
    (silver.stg_shopee_order_item_snapshots / raw.ml_orders), nunca uma
    comparacao contra um literal numerico fixo tipo 21335370.49."""
    assert "silver.stg_shopee_order_item_snapshots" in loader.SQL_SHOPEE_GMV_SOURCE_RECALC.lower()
    assert "raw.ml_orders" in loader.SQL_ML_GMV_SOURCE_RECALC.lower()
    for sql in (loader.SQL_SHOPEE_GMV_SOURCE_RECALC, loader.SQL_ML_GMV_SOURCE_RECALC):
        # nenhum numero de 6+ digitos (uma constante historica de GMV) hardcoded
        import re
        assert not re.search(r"\d{6,}", sql), f"possivel constante numerica fixa em: {sql}"


def test_gmv_reconciliation_tolerance_e_pequena_e_nao_um_gmv_historico():
    assert loader.GMV_RECONCILIATION_TOLERANCE == Decimal("0.01")


# ---------------------------------------------------------------------------
# Multi-item Shopee preservado no SQL (dedup em 2 passos)
# ---------------------------------------------------------------------------

def test_shopee_staging_sql_faz_dedup_em_dois_passos_preservando_multi_item():
    sql_upper = loader.SQL_INSERT_SHOPEE_STAGING.upper()
    assert "DISTINCT ON (BRAND, ORDER_ID)" in sql_upper
    # a tabela fonte precisa aparecer 2x: uma vez para achar o file_id
    # vencedor, outra vez no JOIN de volta que traz TODAS as linhas —
    # um dedup de passo unico so apareceria 1x e perderia unidades de
    # pedidos multi-item.
    occurrences = sql_upper.count("SILVER.STG_SHOPEE_ORDER_ITEM_SNAPSHOTS")
    assert occurrences >= 2, "dedup de passo unico detectado -- perderia unidades multi-item"
    assert "SUM(QUANTITY)" in sql_upper  # soma por linha, nao so 1 linha


# ---------------------------------------------------------------------------
# TikTok nunca inserido
# ---------------------------------------------------------------------------

def test_nenhuma_query_de_staging_menciona_tiktok_ou_seu_marketplace_id():
    for sql in (loader.SQL_INSERT_SHOPEE_STAGING, loader.SQL_INSERT_ML_STAGING):
        assert "tiktok" not in sql.lower()
        assert f"marketplace_id = {loader.TIKTOK_MARKETPLACE_ID}" not in sql.lower().replace(" ", "")


def test_ha_validacao_pos_insert_explicita_de_zero_linhas_tiktok():
    assert str(loader.TIKTOK_MARKETPLACE_ID) in loader.SQL_TIKTOK_ROWS_CHECK
    assert "gold.marketplace_region_daily" in loader.SQL_TIKTOK_ROWS_CHECK.lower()


# ---------------------------------------------------------------------------
# Ausência de SQL destrutivo perigoso
# ---------------------------------------------------------------------------

def test_nenhuma_constante_sql_do_loader_contem_statement_destrutivo():
    """Gate S3 introduziu a ÚNICA exceção sancionada deste módulo:
    `SQL_REFRESH_DELETE` (--refresh-shopee-window), estritamente escopado a
    `marketplace_id = SHOPEE AND date BETWEEN`. Esta constante é a ÚNICA
    permitida a conter `DELETE FROM` — qualquer OUTRA constante `SQL_*` com
    DELETE, TRUNCATE, UPDATE...SET ou DROP continua proibida. Ver
    `test_sql_refresh_delete_e_a_unica_excecao_sancionada` e
    `test_sql_refresh_delete_tem_escopo_restrito_marketplace_e_janela` para
    a validação positiva dessa exceção."""
    import re
    forbidden = re.compile(r"\bDROP\s+(TABLE|SCHEMA|DATABASE|INDEX|VIEW)\b|\bTRUNCATE\b|\bDELETE\s+FROM\b|\bUPDATE\s+\w+\s+SET\b", re.IGNORECASE)
    sql_constants = {
        k: v for k, v in vars(loader).items()
        if k.startswith("SQL_") and isinstance(v, str)
    }
    assert len(sql_constants) >= 8
    for name, sql in sql_constants.items():
        if name == "SQL_REFRESH_DELETE":
            continue
        match = forbidden.search(sql)
        assert not match, f"statement destrutivo suspeito: {match.group(0)!r} em {name}: {sql[:80]}..."


def test_sql_refresh_delete_e_a_unica_excecao_sancionada():
    """Nenhuma OUTRA constante SQL_* do módulo pode conter DELETE — só
    `SQL_REFRESH_DELETE` (Gate S3, --refresh-shopee-window)."""
    import re
    sql_constants = {
        k: v for k, v in vars(loader).items()
        if k.startswith("SQL_") and isinstance(v, str)
    }
    assert "SQL_REFRESH_DELETE" in sql_constants
    delete_constants = [k for k, v in sql_constants.items() if re.search(r"\bDELETE\s+FROM\b", v, re.IGNORECASE)]
    assert delete_constants == ["SQL_REFRESH_DELETE"]


def test_sql_refresh_delete_tem_escopo_restrito_marketplace_e_janela():
    import re
    sql_upper = loader.SQL_REFRESH_DELETE.upper()
    assert "DELETE FROM GOLD.MARKETPLACE_REGION_DAILY" in sql_upper
    assert "MARKETPLACE_ID = %(SHOPEE_MARKETPLACE_ID)S" in sql_upper
    assert "DATE BETWEEN %(DATE_FROM)S AND %(DATE_TO)S" in sql_upper
    # nunca um DELETE sem WHERE, nunca literal de data/id interpolado
    assert not re.search(r"'\d{4}-\d{2}-\d{2}'", loader.SQL_REFRESH_DELETE)


def test_create_temp_table_on_commit_drop_nao_e_falso_positivo_destrutivo():
    """'ON COMMIT DROP' e uma clausula legitima de TEMP TABLE (limpeza
    automatica), nao um DROP TABLE destrutivo -- confirma que a regex do
    teste acima nao acusa isso."""
    assert "ON COMMIT DROP" in loader.SQL_CREATE_STAGING


# =============================================================================
# Gate 6C — refresh incremental (diagnose_incremental_load / execute_incremental_load / CLI)
# =============================================================================

_SQL_MAX_DATE_GOLD_NORM = "SELECT MARKETPLACE_ID, MAX(DATE) AS MAX_DATE FROM GOLD.MARKETPLACE_REGION_DAILY GROUP BY MARKETPLACE_ID"
_SQL_MAX_DATE_ML_NORM = "SELECT MAX(DATE_CREATED::DATE) FROM RAW.ML_ORDERS WHERE STATUS IN ('PAID', 'CANCELLED')"
_SQL_MAX_DATE_SHOPEE_NORM = "SELECT MAX(ORDER_CREATED_AT::DATE) FROM SILVER.STG_SHOPEE_ORDER_ITEM_SNAPSHOTS"


class IncrementalFakeCursor:
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
            raise RuntimeError("falha simulada de execução")
        if norm.upper().startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY"):
            self.conn.final_insert_executed = True

    def fetchone(self):
        last = self.conn.executed[-1]
        upper = last.upper()
        if "PG_TRY_ADVISORY_LOCK" in upper:
            return (self.conn.lock_acquired,)
        if "PG_ADVISORY_UNLOCK" in upper:
            return (True,)
        for matcher, value in self.conn.fetchone_responses:
            if matcher(upper):
                return value
        raise AssertionError(f"nenhuma resposta fetchone simulada para: {last!r}")

    def fetchall(self):
        last = self.conn.executed[-1]
        upper = last.upper()
        for matcher, value in self.conn.fetchall_responses:
            if matcher(upper):
                return value
        raise AssertionError(f"nenhuma resposta fetchall simulada para: {last!r}")

    @property
    def rowcount(self):
        return self.conn._final_insert_rowcount if self.conn.final_insert_executed else 0


class IncrementalFakeConn:
    def __init__(
        self, lock_acquired=True, fail_on_substring=None,
        fetchone_responses=None, fetchall_responses=None, final_insert_rowcount=10,
    ):
        self.executed = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.lock_acquired = lock_acquired
        self.fail_on_substring = fail_on_substring
        self.fetchone_responses = fetchone_responses or []
        self.fetchall_responses = fetchall_responses or []
        self.final_insert_executed = False
        self._final_insert_rowcount = final_insert_rowcount
        self.autocommit = None

    def cursor(self):
        return IncrementalFakeCursor(self)

    def set_session(self, readonly=None, autocommit=None):
        self.readonly = readonly
        self.autocommit = autocommit

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _inc_exact(pattern):
    return lambda upper: upper == pattern


def _inc_contains(substring):
    return lambda upper: substring in upper


def _inc_all(*substrings):
    return lambda upper: all(s in upper for s in substrings)


# ---------------------------------------------------------------------------
# diagnose_incremental_load — somente leitura
# ---------------------------------------------------------------------------

def test_diagnose_incremental_load_no_op_quando_fonte_sem_data_nova(monkeypatch):
    fake_conn = IncrementalFakeConn(
        fetchall_responses=[
            (_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [
                (loader.ML_MARKETPLACE_ID, date(2026, 7, 9)),
                (loader.SHOPEE_MARKETPLACE_ID, date(2026, 5, 31)),
            ]),
        ],
        fetchone_responses=[
            (_inc_exact(_SQL_MAX_DATE_ML_NORM), (date(2026, 7, 9),)),  # igual ao gold -- sem novidade
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), (date(2026, 5, 31),)),  # igual ao gold -- sem novidade
        ],
    )
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    report = loader.diagnose_incremental_load("postgresql://reader@host/db")

    assert report.any_update_needed is False
    assert {m.marketplace: m.will_update for m in report.marketplaces} == {"ml": False, "shopee": False}
    assert fake_conn.closed is True
    # nao deveria ter chamado nenhuma query de COUNT (nao ha novidade nem para checar)
    assert not any("SELECT COUNT(*) FROM (" in s.upper() for s in fake_conn.executed)


def test_diagnose_incremental_load_ml_com_data_nova_shopee_no_op(monkeypatch):
    fake_conn = IncrementalFakeConn(
        fetchall_responses=[
            (_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [
                (loader.ML_MARKETPLACE_ID, date(2026, 7, 9)),
                (loader.SHOPEE_MARKETPLACE_ID, date(2026, 5, 31)),
            ]),
        ],
        fetchone_responses=[
            (_inc_exact(_SQL_MAX_DATE_ML_NORM), (date(2026, 7, 15),)),  # fresco -- 6 dias de novidade
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), (date(2026, 5, 31),)),  # igual ao gold -- sem novidade
            (_inc_all("SELECT COUNT(*) FROM (", "RAW.ML_ORDERS", "ML_JOINED"), (42,)),
        ],
    )
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    report = loader.diagnose_incremental_load("postgresql://reader@host/db")

    assert report.any_update_needed is True
    by_mkt = {m.marketplace: m for m in report.marketplaces}
    assert by_mkt["ml"].will_update is True
    assert by_mkt["ml"].estimated_new_rows == 42
    assert by_mkt["ml"].max_date_gold == date(2026, 7, 9)
    assert by_mkt["ml"].max_date_source == date(2026, 7, 15)
    assert by_mkt["shopee"].will_update is False
    # nunca chamou COUNT para shopee (sem novidade) -- so' 1 query de COUNT no total
    assert sum(1 for s in fake_conn.executed if "SELECT COUNT(*) FROM (" in s.upper()) == 1


def test_diagnose_incremental_load_nunca_abre_conexao_de_escrita(monkeypatch):
    """`conn.set_session(readonly=True, ...)` precisa ser chamado -- garante
    que mesmo um bug no restante da funcao nao conseguiria escrever."""
    fake_conn = IncrementalFakeConn(
        fetchall_responses=[(_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [])],
        fetchone_responses=[
            (_inc_exact(_SQL_MAX_DATE_ML_NORM), (None,)),
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), (None,)),
        ],
    )
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    loader.diagnose_incremental_load("postgresql://reader@host/db")

    assert fake_conn.readonly is True
    assert fake_conn.autocommit is True
    assert fake_conn.committed is False  # autocommit=True -- nunca precisa de commit explicito
    assert not any("CREATE TEMP TABLE" in s.upper() for s in fake_conn.executed)
    assert not any("INSERT INTO" in s.upper() for s in fake_conn.executed)


# ---------------------------------------------------------------------------
# execute_incremental_load — transacional, so' linhas novas
# ---------------------------------------------------------------------------

def _happy_incremental_only_ml_conn(**overrides):
    return IncrementalFakeConn(
        fetchall_responses=[
            (_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [
                (loader.ML_MARKETPLACE_ID, date(2026, 7, 9)),
                (loader.SHOPEE_MARKETPLACE_ID, date(2026, 5, 31)),
            ]),
        ],
        fetchone_responses=[
            (_inc_exact(_SQL_MAX_DATE_ML_NORM), (date(2026, 7, 15),)),
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), (date(2026, 5, 31),)),  # sem novidade -- nunca bloqueia ML
            (_inc_contains("HAVING COUNT(*) > 1"), (0,)),
            (_inc_contains("IS NULL OR MARKETPLACE_ID IS NULL"), (0,)),
            (_inc_contains("UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS"), (0,)),
            (_inc_contains(f"WHERE MARKETPLACE_ID = {loader.ML_MARKETPLACE_ID}"), (Decimal("500.00"),)),
            (_inc_contains("RAW.ML_ORDERS WHERE STATUS = 'PAID' AND DATE_CREATED"), (Decimal("500.00"),)),
            (_inc_contains(f"WHERE MARKETPLACE_ID = {loader.TIKTOK_MARKETPLACE_ID}"), (0,)),
            (_inc_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (30,)),
        ],
        **overrides,
    )


def test_execute_incremental_load_so_carrega_marketplace_com_data_nova_shopee_nao_bloqueia_ml(monkeypatch):
    fake_conn = _happy_incremental_only_ml_conn()
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    result = loader.execute_incremental_load("postgresql://writer@host/db")

    assert result.no_op is False
    assert result.marketplaces_updated == ["ml"]
    assert result.rows_inserted == 10  # final_insert_rowcount default
    assert result.ml_gmv_staging == result.ml_gmv_source == Decimal("500.00")
    assert result.shopee_gmv_staging is None and result.shopee_gmv_source is None
    assert fake_conn.committed is True
    assert fake_conn.rolled_back is False
    # staging so' recebeu insert de ML -- nunca tentou consultar reconciliacao de shopee
    assert not any(_inc_contains(f"WHERE MARKETPLACE_ID = {loader.SHOPEE_MARKETPLACE_ID}")(s.upper()) for s in fake_conn.executed)


def test_execute_incremental_load_no_op_quando_nenhum_marketplace_tem_novidade(monkeypatch):
    fake_conn = IncrementalFakeConn(
        fetchall_responses=[
            (_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [
                (loader.ML_MARKETPLACE_ID, date(2026, 7, 9)),
                (loader.SHOPEE_MARKETPLACE_ID, date(2026, 5, 31)),
            ]),
        ],
        fetchone_responses=[
            (_inc_exact(_SQL_MAX_DATE_ML_NORM), (date(2026, 7, 9),)),
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), (date(2026, 5, 31),)),
        ],
    )
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    result = loader.execute_incremental_load("postgresql://writer@host/db")

    assert result.no_op is True
    assert result.rows_inserted == 0
    assert fake_conn.committed is True  # fecha a transacao de leitura, mas nada foi alterado
    assert not any("CREATE TEMP TABLE" in s.upper() for s in fake_conn.executed)
    assert not any("INSERT INTO STG_MARKETPLACE_REGION_DAILY" in s.upper() for s in fake_conn.executed)
    assert fake_conn.final_insert_executed is False


def test_execute_incremental_load_aborta_se_duplicidade(monkeypatch):
    fake_conn = _happy_incremental_only_ml_conn()
    # sobrescreve a resposta de duplicidade para simular falha
    fake_conn.fetchone_responses = [
        (_inc_contains("HAVING COUNT(*) > 1"), (3,)),
        (_inc_exact(_SQL_MAX_DATE_ML_NORM), (date(2026, 7, 15),)),
        (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), (date(2026, 5, 31),)),
        (_inc_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (30,)),
    ]
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(loader.LoadValidationError, match="duplicada"):
        loader.execute_incremental_load("postgresql://writer@host/db")

    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False
    assert fake_conn.final_insert_executed is False


def test_execute_incremental_load_aborta_se_nulos_obrigatorios(monkeypatch):
    fake_conn = _happy_incremental_only_ml_conn()
    fake_conn.fetchone_responses = [
        (_inc_exact(_SQL_MAX_DATE_ML_NORM), (date(2026, 7, 15),)),
        (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), (date(2026, 5, 31),)),
        (_inc_contains("HAVING COUNT(*) > 1"), (0,)),
        (_inc_contains("IS NULL OR MARKETPLACE_ID IS NULL"), (5,)),
        (_inc_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (30,)),
    ]
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(loader.LoadValidationError, match="nula"):
        loader.execute_incremental_load("postgresql://writer@host/db")

    assert fake_conn.rolled_back is True
    assert fake_conn.final_insert_executed is False


def test_execute_incremental_load_aborta_se_gmv_ml_nao_reconcilia(monkeypatch):
    fake_conn = _happy_incremental_only_ml_conn()
    fake_conn.fetchone_responses = [
        (_inc_exact(_SQL_MAX_DATE_ML_NORM), (date(2026, 7, 15),)),
        (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), (date(2026, 5, 31),)),
        (_inc_contains("HAVING COUNT(*) > 1"), (0,)),
        (_inc_contains("IS NULL OR MARKETPLACE_ID IS NULL"), (0,)),
        (_inc_contains("UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS"), (0,)),
        (_inc_contains(f"WHERE MARKETPLACE_ID = {loader.ML_MARKETPLACE_ID}"), (Decimal("500.00"),)),
        (_inc_contains("RAW.ML_ORDERS WHERE STATUS = 'PAID' AND DATE_CREATED"), (Decimal("400.00"),)),  # diverge
        (_inc_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (30,)),
    ]
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(loader.LoadValidationError, match="GMV ML"):
        loader.execute_incremental_load("postgresql://writer@host/db")

    assert fake_conn.rolled_back is True
    assert fake_conn.final_insert_executed is False


def test_execute_incremental_load_rollback_em_erro_generico_nunca_expoe_mensagem_nativa(monkeypatch):
    fake_conn = _happy_incremental_only_ml_conn(fail_on_substring="CREATE TEMP TABLE")

    class FailingConn(IncrementalFakeConn):
        def cursor(self):
            class _Cur(IncrementalFakeCursor):
                def execute(self_inner, sql, params=None):
                    norm = " ".join(sql.split())
                    self_inner.conn.executed.append(norm)
                    if "CREATE TEMP TABLE" in norm.upper():
                        raise RuntimeError(
                            'connection to server at "prod-db.example.rds.amazonaws.com" '
                            '(10.0.0.5), port 5432 failed: FATAL: password authentication failed for user "postgres"'
                        )
            return _Cur(self)

    failing_conn = FailingConn(
        fetchall_responses=fake_conn.fetchall_responses,
        fetchone_responses=fake_conn.fetchone_responses,
    )
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(failing_conn))

    with pytest.raises(RuntimeError) as exc_info:
        loader.execute_incremental_load("postgresql://writer@host/db")

    msg = str(exc_info.value)
    assert "prod-db.example.rds.amazonaws.com" not in msg
    assert "10.0.0.5" not in msg
    assert "postgres" not in msg
    assert failing_conn.rolled_back is True
    assert failing_conn.committed is False


def test_execute_incremental_load_bloqueia_se_advisory_lock_em_uso(monkeypatch):
    fake_conn = IncrementalFakeConn(lock_acquired=False)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake_conn))

    with pytest.raises(wc.WritePreflightBlocked, match="advisory lock"):
        loader.execute_incremental_load("postgresql://writer@host/db")

    assert fake_conn.closed is True
    assert not any("MAX(DATE)" in s.upper() for s in fake_conn.executed)


def test_execute_incremental_load_nao_faz_retry_automatico(monkeypatch):
    calls = {"n": 0}

    class CountingModule(_FakePsycopg2Module):
        def connect(self, url, connect_timeout=15):
            calls["n"] += 1
            return self._conn

    fake_conn = _happy_incremental_only_ml_conn(fail_on_substring="CREATE TEMP TABLE")
    monkeypatch.setattr(loader, "psycopg2", CountingModule(fake_conn))

    with pytest.raises(RuntimeError):
        loader.execute_incremental_load("postgresql://writer@host/db")

    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Isolamento do caminho incremental: nunca TRUNCATE/DELETE/UPDATE, nunca usa
# execute_first_load() por dentro, filtro de data correto por marketplace
# ---------------------------------------------------------------------------

def test_incremental_filtra_por_max_date_especifico_de_cada_marketplace():
    ml_sql = loader._ml_incremental_select(date(2026, 7, 9))
    shopee_sql = loader._shopee_incremental_select(date(2026, 5, 31))
    assert "> '2026-07-09'::date" in ml_sql
    assert "> '2026-05-31'::date" in shopee_sql
    # cada um so' filtra pela SUA data -- nunca a data do outro marketplace
    assert "2026-05-31" not in ml_sql
    assert "2026-07-09" not in shopee_sql


def test_incremental_sql_nunca_contem_statement_destrutivo():
    import re
    forbidden = re.compile(
        r"\bDROP\s+(TABLE|SCHEMA|DATABASE|INDEX|VIEW)\b|\bTRUNCATE\b|\bDELETE\s+FROM\b|\bUPDATE\s+\w+\s+SET\b",
        re.IGNORECASE,
    )
    sample_date = date(2026, 1, 1)
    sqls = [
        loader._ml_incremental_select(sample_date),
        loader._shopee_incremental_select(sample_date),
        loader._ml_gmv_source_recalc_incremental(sample_date),
        loader._shopee_gmv_source_recalc_incremental(sample_date),
        loader.SQL_MAX_DATE_GOLD_BY_MARKETPLACE,
        loader.SQL_MAX_DATE_SHOPEE_SOURCE,
        loader.SQL_MAX_DATE_ML_SOURCE,
    ]
    for sql in sqls:
        match = forbidden.search(sql)
        assert not match, f"statement destrutivo suspeito: {match.group(0)!r} em {sql[:80]}..."


def test_execute_incremental_load_nao_usa_execute_first_load_internamente():
    """A docstring pode MENCIONAR execute_first_load (documentação/
    comparação) -- o que não pode existir é uma CHAMADA de fato."""
    import inspect
    source = inspect.getsource(loader.execute_incremental_load)
    assert "execute_first_load(" not in source


def test_diagnose_incremental_load_nao_usa_execute_first_load_nem_execute_incremental_load():
    import inspect
    source = inspect.getsource(loader.diagnose_incremental_load)
    assert "execute_first_load" not in source
    assert "execute_incremental_load" not in source


# ---------------------------------------------------------------------------
# CLI — --diagnose nunca escreve; --incremental exige consentimento
# ---------------------------------------------------------------------------

def test_run_diagnose_cli_nao_chama_execute_incremental_load(monkeypatch, capsys):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    monkeypatch.setattr(
        loader, "diagnose_incremental_load",
        lambda read_url: loader.DiagnoseReport(marketplaces=[], any_update_needed=False),
    )

    def boom(*a, **k):
        raise AssertionError("--diagnose nao deveria chamar execute_incremental_load")
    monkeypatch.setattr(loader, "execute_incremental_load", boom)

    rc = loader.run_diagnose_cli()

    assert rc == 0
    out = capsys.readouterr().out
    assert "Precisa atualizar: False" in out


def test_run_diagnose_cli_sem_datamart_url_aborta(monkeypatch):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "")
    monkeypatch.setattr(loader.settings, "datamart_host", "")
    monkeypatch.setattr(loader.settings, "datamart_db", "")

    rc = loader.run_diagnose_cli()

    assert rc == 2


def test_run_incremental_cli_bloqueia_sem_secret_file(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("nao deveria escrever sem secret valido")
    monkeypatch.setattr(loader, "execute_incremental_load", boom)

    rc = loader.run_incremental_cli(secret_path=tmp_path / "nao_existe.local", repo_root=tmp_path)

    assert rc == 2


def test_run_incremental_cli_bloqueia_se_consentimento_errado(tmp_path, monkeypatch):
    secret_path = tmp_path / ".env.gold-write.local"
    secret_path.write_text(
        "DATAMART_GOLD_WRITE_URL=postgresql://writer@host/db\n"
        "I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD=0\n",
        encoding="utf-8",
    )

    def fake_run_git(args, cwd):
        # simula "arquivo IGNORADO pelo git" (check-ignore rc=0) e "arquivo
        # NAO rastreado" (ls-files rc=1) -- os dois guardrails estaticos de
        # localizacao passam, entao load_write_secret chega de fato na
        # checagem de consentimento (I_UNDERSTAND_THIS_WRITES_DATAMART_GOLD),
        # que e o que este teste quer exercitar.
        return type("R", (), {"returncode": 0 if args[0] == "check-ignore" else 1})()
    monkeypatch.setattr(wc, "_run_git", fake_run_git)

    def boom(*a, **k):
        raise AssertionError("nao deveria escrever com consentimento != 1")
    monkeypatch.setattr(loader, "execute_incremental_load", boom)

    rc = loader.run_incremental_cli(secret_path=secret_path, repo_root=tmp_path)

    assert rc == 2


def test_main_diagnose_flag_chama_run_diagnose_cli(monkeypatch):
    monkeypatch.setattr(loader, "run_diagnose_cli", lambda: 0)
    monkeypatch.setattr(loader, "run_incremental_cli", lambda *a, **k: 99)
    assert loader.main(["--diagnose"]) == 0


def test_main_incremental_flag_chama_run_incremental_cli(monkeypatch):
    monkeypatch.setattr(loader, "run_diagnose_cli", lambda: 99)
    monkeypatch.setattr(loader, "run_incremental_cli", lambda *a, **k: 0)
    assert loader.main(["--incremental"]) == 0


def test_main_exige_um_dos_dois_flags():
    with pytest.raises(SystemExit):
        loader.main([])


def test_main_nao_aceita_os_dois_flags_juntos():
    with pytest.raises(SystemExit):
        loader.main(["--diagnose", "--incremental"])
