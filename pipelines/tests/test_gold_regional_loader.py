"""
Testes de pipelines/ingestion/gold_regional/loader.py — Gate 6A.3 (carga
inicial) e Gate 6C (refresh incremental + CLI).

Usa conexões/cursores psycopg2 falsos — nenhum banco real é tocado. As
respostas de `fetchone()` são escolhidas por SUBSTRING reconhecível de cada
query (mesmo padrão de test_gold_regional_write_conn.py), o que deixa os
testes robustos a pequenos reordenamentos das queries reais.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import re
from decimal import Decimal
from zoneinfo import ZoneInfo

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
_SQL_MAX_DATE_ML_NORM = (
    "SELECT MAX(DATE_CREATED::DATE) AS MAX_GLOBAL, "
    "MAX(DATE_CREATED::DATE) FILTER (WHERE DATE_CREATED::DATE <= %(DATE_TO)S) AS MAX_ELIGIBLE "
    "FROM RAW.ML_ORDERS WHERE STATUS IN ('PAID', 'CANCELLED')"
)
_SQL_MAX_DATE_SHOPEE_NORM = (
    "SELECT MAX(ORDER_CREATED_AT::DATE) AS MAX_GLOBAL, "
    "MAX(ORDER_CREATED_AT::DATE) FILTER (WHERE ORDER_CREATED_AT::DATE <= %(DATE_TO)S) AS MAX_ELIGIBLE "
    "FROM SILVER.STG_SHOPEE_ORDER_ITEM_SNAPSHOTS"
)


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
        # Gate DQ-D2-R: os params importam agora. A consulta de maximos e
        # PARAMETRIZADA por `date_to`, e e' o teto que separa max_global de
        # max_eligible. Um fake que ignora params nao pode provar isso.
        self.conn.executed_params.append(params)
        if self.conn.fail_on_substring and self.conn.fail_on_substring in norm:
            raise RuntimeError("falha simulada de execução")
        if norm.upper().startswith("INSERT INTO GOLD.MARKETPLACE_REGION_DAILY"):
            self.conn.final_insert_executed = True

    def fetchone(self):
        last = self.conn.executed[-1]
        params = self.conn.executed_params[-1]
        upper = last.upper()
        if "PG_TRY_ADVISORY_LOCK" in upper:
            return (self.conn.lock_acquired,)
        if "PG_ADVISORY_UNLOCK" in upper:
            return (True,)
        for matcher, value in self.conn.fetchone_responses:
            if matcher(upper):
                # Resposta CALLABLE = fonte simulada de verdade: recebe os
                # params e calcula os dois maximos como o Postgres calcularia.
                if callable(value):
                    return value(params)
                # Compatibilidade: uma resposta de 1 tupla numa consulta de
                # maximos descreve uma fonte cujo unico dia conhecido e' esse.
                # O elegivel e' entao esse dia se ele cabe no teto, e AUSENCIA
                # se nao cabe - nunca o teto no lugar dele.
                if "MAX_ELIGIBLE" in upper and len(value) == 1:
                    (d,) = value
                    teto = (params or {}).get("date_to")
                    elegivel = d if (d is not None and teto is not None
                                     and d <= teto) else None
                    return (d, elegivel)
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
        self.executed_params = []
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


def _fonte(*datas):
    """Fonte simulada por LISTA REAL de dias, nao por um maximo.

    Devolve um callable que recebe os params do `cur.execute` e calcula os dois
    maximos exatamente como o `MAX(...) FILTER (WHERE ... <= :date_to)` do
    Postgres calcularia. E' o que permite provar a diferenca entre "a fonte tem
    dado em D-1" e "a fonte pula D-1": com `_fonte(5/9, 8/9)` e teto 7/9, o
    elegivel e' 05/09 - e nao 07/09, que e' o que um `min()` inventaria.
    """
    dias = [d for d in datas if d is not None]

    def responder(params):
        teto = (params or {}).get("date_to")
        assert teto is not None, (
            "a consulta de maximos foi executada SEM o parametro date_to: "
            "sem teto nao existe maximo elegivel"
        )
        elegiveis = [d for d in dias if d <= teto]
        return (max(dias) if dias else None,
                max(elegiveis) if elegiveis else None)

    return responder


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
    assert by_mkt["ml"].max_date_source_global == date(2026, 7, 15)
    assert by_mkt["ml"].max_date_source_eligible == date(2026, 7, 15)
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
                    self_inner.conn.executed_params.append(params)
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
    # Gate DQ-D2: `date_to` e' posicional e OBRIGATORIO nos dois selects.
    teto = date(2026, 8, 31)
    ml_sql = loader._ml_incremental_select(date(2026, 7, 9), teto)
    shopee_sql = loader._shopee_incremental_select(date(2026, 5, 31), teto)
    assert "> '2026-07-09'::date" in ml_sql
    assert "> '2026-05-31'::date" in shopee_sql
    # ...e o teto entra de verdade nos DOIS, nao apenas na assinatura.
    assert "<= '2026-08-31'::date" in ml_sql
    assert "<= '2026-08-31'::date" in shopee_sql
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
        loader._ml_incremental_select(sample_date, date(2026, 1, 31)),
        loader._shopee_incremental_select(sample_date, date(2026, 1, 31)),
        # Gate DQ-D2-R2: as duas reconciliacoes passaram a exigir `date_to`.
        loader._ml_gmv_source_recalc_incremental(sample_date, date(2026, 1, 31)),
        loader._shopee_gmv_source_recalc_incremental(sample_date, date(2026, 1, 31)),
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
        lambda read_url, date_to=None: loader.DiagnoseReport(
            marketplaces=[], any_update_needed=False),
    )

    def boom(*a, **k):
        raise AssertionError("--diagnose nao deveria chamar execute_incremental_load")
    monkeypatch.setattr(loader, "execute_incremental_load", boom)

    rc = loader.run_diagnose_cli()

    assert rc == 0
    out = capsys.readouterr().out
    assert "Precisa atualizar: False" in out
    # Gate DQ-D2: o diagnose declara o teto que aplicou.
    assert "teto superior INCLUSIVO" in out


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
    monkeypatch.setattr(loader, "run_diagnose_cli", lambda *a, **k: 0)
    monkeypatch.setattr(loader, "run_incremental_cli", lambda *a, **k: 99)
    assert loader.main(["--diagnose"]) == 0


def test_main_incremental_flag_chama_run_incremental_cli(monkeypatch):
    monkeypatch.setattr(loader, "run_diagnose_cli", lambda *a, **k: 99)
    monkeypatch.setattr(loader, "run_incremental_cli", lambda *a, **k: 0)
    assert loader.main(["--incremental"]) == 0


def test_main_exige_um_dos_dois_flags():
    with pytest.raises(SystemExit):
        loader.main([])


def test_main_nao_aceita_os_dois_flags_juntos():
    with pytest.raises(SystemExit):
        loader.main(["--diagnose", "--incremental"])


# ---------------------------------------------------------------------------
# Gate DQ-D2 — teto superior D-1 no incremental da Gold regional
# ---------------------------------------------------------------------------
# Antes deste gate a fronteira superior era ABERTA: os `incremental_select`
# filtravam so' `date > max_date_gold`. Medido em 08/09/2026, `raw.ml_orders`
# alcancava o dia corrente e a carga teria publicado D0 parcial.

_DQD2_BRT = ZoneInfo("America/Sao_Paulo")


def _agora_brt(ano, mes, dia, hora=12, minuto=0):
    """Instante AWARE em BRT, convertido para UTC — como o processo real ve."""
    return datetime(ano, mes, dia, hora, minuto,
                    tzinfo=_DQD2_BRT).astimezone(timezone.utc)


# --- resolucao e recusa do teto -------------------------------------------

def test_dqd2_teto_default_e_d_menos_1_em_sao_paulo():
    # 08/09 09:00 BRT -> D-1 = 07/09
    assert loader.resolve_incremental_date_to(
        None, _agora_brt(2026, 9, 8, 9, 0)) == date(2026, 9, 7)


def test_dqd2_fronteira_meia_noite_utc_x_brt():
    """21h BRT de 08/09 ja e' 09/09 em UTC. O teto tem de continuar 07/09.

    Este e' o caso que `date.today()` erraria: o processo roda em UTC.
    """
    # 08/09 21:00 BRT == 09/09 00:00 UTC
    tarde = _agora_brt(2026, 9, 8, 21, 0)
    assert tarde.astimezone(timezone.utc).date() == date(2026, 9, 9)
    assert loader.resolve_incremental_date_to(None, tarde) == date(2026, 9, 7)
    # E logo depois da meia-noite BRT do dia 09, o teto vira 08/09.
    assert loader.resolve_incremental_date_to(
        None, _agora_brt(2026, 9, 9, 0, 30)) == date(2026, 9, 8)


def test_dqd2_date_to_igual_a_d_menos_1_e_aceito():
    assert loader.resolve_incremental_date_to(
        date(2026, 9, 7), _agora_brt(2026, 9, 8)) == date(2026, 9, 7)


def test_dqd2_date_to_em_d0_e_RECUSADO():
    with pytest.raises(loader.IncrementalCeilingError) as e:
        loader.resolve_incremental_date_to(date(2026, 9, 8),
                                           _agora_brt(2026, 9, 8))
    assert "ultimo dia fechado" in str(e.value)
    # Recusa em vez de rebaixar em silencio: quem pediu D0 precisa saber.
    assert "2026-09-07" in str(e.value)


def test_dqd2_date_to_no_futuro_e_RECUSADO():
    for alvo in (date(2026, 9, 9), date(2026, 12, 31), date(2099, 1, 1)):
        with pytest.raises(loader.IncrementalCeilingError):
            loader.resolve_incremental_date_to(alvo, _agora_brt(2026, 9, 8))


def test_dqd2_date_to_anterior_e_aceito_para_reprocesso_estreito():
    assert loader.resolve_incremental_date_to(
        date(2026, 8, 31), _agora_brt(2026, 9, 8)) == date(2026, 8, 31)


def test_dqd2_date_to_de_tipo_errado_e_recusado_sem_eco():
    for ruim in ("2026-09-07", 20260907, object()):
        with pytest.raises(loader.IncrementalCeilingError) as e:
            loader.resolve_incremental_date_to(ruim, _agora_brt(2026, 9, 8))
        assert "precisa ser um datetime.date exato" in str(e.value)
        assert str(ruim) not in str(e.value)


# --- o teto e' REAL no SQL, nao apenas na assinatura ----------------------

def test_dqd2_os_dois_selects_aplicam_o_teto_no_SQL():
    """O gate proibe CLI que recebe --date-to e nao aplica em todos os SQL."""
    teto = date(2026, 9, 7)
    ml = loader._ml_incremental_select(date(2026, 9, 2), teto)
    sh = loader._shopee_incremental_select(date(2026, 9, 2), teto)
    for sql in (ml, sh):
        assert "<= '2026-09-07'::date" in sql
        assert "> '2026-09-02'::date" in sql
    # A coluna filtrada e' a MESMA que ja delimitava o piso, nunca outra.
    assert ml.count("date_created::date") >= 2
    assert sh.count("o.order_date") >= 2


def test_dqd2_janela_e_INCLUSIVA_no_teto_e_exclusiva_no_piso():
    sql = loader._ml_incremental_select(date(2026, 9, 2), date(2026, 9, 7))
    assert "> '2026-09-02'::date" in sql      # piso EXCLUSIVO (watermark)
    assert ">= '2026-09-02'::date" not in sql
    assert "<= '2026-09-07'::date" in sql     # teto INCLUSIVO
    assert "< '2026-09-07'::date" not in sql.replace("<= '2026-09-07'", "")


def test_dqd2_teto_e_posicional_obrigatorio_nos_dois_selects():
    """Sem default, esquecer o teto e' TypeError — nao volta a fronteira aberta."""
    for fn in (loader._ml_incremental_select, loader._shopee_incremental_select):
        with pytest.raises(TypeError):
            fn(date(2026, 9, 2))


def test_dqd2_nenhum_select_do_incremental_tem_fronteira_superior_aberta():
    """Varredura: todo SQL do incremental que filtra por data tem os DOIS lados."""
    teto = date(2026, 9, 7)
    for sql in (loader._ml_incremental_select(date(2026, 9, 1), teto),
                loader._shopee_incremental_select(date(2026, 9, 1), teto)):
        norm = " ".join(sql.split())
        assert norm.count("'2026-09-01'::date") >= 1
        assert norm.count("'2026-09-07'::date") >= 1


# --- fonte em D0, destino limitado a D-1 ---------------------------------

def _dqd2_conn(gold_ml, dias_fonte_ml, count_novas=7,
               dias_fonte_shopee=(date(2026, 8, 24),)):
    """Fake do incremental com a fonte ML descrita por LISTA REAL de dias.

    Descrever a fonte por lista, e nao por um maximo, e' o que torna estes
    testes comportamentais: o fake calcula `max_global` e `max_eligible` como o
    Postgres calcularia, e o loader decide sobre o que o banco respondeu.
    """
    return IncrementalFakeConn(
        fetchall_responses=[
            (_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [
                (loader.ML_MARKETPLACE_ID, gold_ml),
                (loader.SHOPEE_MARKETPLACE_ID, date(2026, 8, 24)),
            ]),
        ],
        fetchone_responses=[
            (_inc_exact(_SQL_MAX_DATE_ML_NORM), _fonte(*dias_fonte_ml)),
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), _fonte(*dias_fonte_shopee)),
            (_inc_all("SELECT COUNT(*) FROM (", "RAW.ML_ORDERS"), (count_novas,)),
        ],
    )


def test_dqd2_diagnose_com_fonte_em_D0_usa_o_maximo_ELEGIVEL(monkeypatch):
    """Fonte com dado ATE D0, contendo tambem D-1: o elegivel e' D-1.

    Aqui o teto e o maximo elegivel coincidem — mas por medicao, nao por
    aritmetica. O teste seguinte separa os dois.
    """
    fake = _dqd2_conn(date(2026, 9, 2),
                      [date(2026, 9, 5), date(2026, 9, 6),
                       date(2026, 9, 7), date(2026, 9, 8)])
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    report = loader.diagnose_incremental_load("postgresql://reader@host/db",
                                              date_to=date(2026, 9, 7))
    ml = {m.marketplace: m for m in report.marketplaces}["ml"]
    assert ml.max_date_source_global == date(2026, 9, 8)
    assert ml.max_date_source_eligible == date(2026, 9, 7)
    assert ml.will_update is True
    counts = [s for s in fake.executed if "SELECT COUNT(*) FROM (" in s.upper()]
    assert counts and "<= '2026-09-07'::date" in counts[0]


def test_dqd2_diagnose_nao_promete_carga_quando_a_novidade_esta_toda_acima_do_teto(monkeypatch):
    """Gold em 07/09 e fonte cujo unico dia novo e' 08/09.

    Sem teto no predicado isto diria `will_update=True` e a carga acharia
    staging vazia — transformando a verdade "nada novo" numa falha.
    """
    fake = _dqd2_conn(date(2026, 9, 7), [date(2026, 9, 7), date(2026, 9, 8)])
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    report = loader.diagnose_incremental_load("postgresql://reader@host/db",
                                              date_to=date(2026, 9, 7))
    ml = {m.marketplace: m for m in report.marketplaces}["ml"]
    assert ml.max_date_source_global == date(2026, 9, 8)
    assert ml.max_date_source_eligible == date(2026, 9, 7)
    assert ml.will_update is False
    assert report.any_update_needed is False
    assert not any("SELECT COUNT(*) FROM (" in s.upper() for s in fake.executed)


def test_dqd2_carga_com_novidade_toda_acima_do_teto_e_NO_OP_nao_falha(monkeypatch):
    """Vazio LEGITIMO distinto de falha — o gate exige essa distincao."""
    fake = IncrementalFakeConn(
        fetchall_responses=[
            (_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [
                (loader.ML_MARKETPLACE_ID, date(2026, 9, 7)),
                (loader.SHOPEE_MARKETPLACE_ID, date(2026, 8, 24)),
            ]),
        ],
        fetchone_responses=[
            (_inc_exact(_SQL_MAX_DATE_ML_NORM), (date(2026, 9, 8),)),
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), (date(2026, 8, 24),)),
        ],
    )
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    r = loader.execute_incremental_load("postgresql://w@host/db",
                                        date_to=date(2026, 9, 7))
    assert r.no_op is True
    assert r.date_to == date(2026, 9, 7)
    assert r.rows_inserted == 0
    # NO_OP fecha a transacao limpa, sem staging e sem insert.
    assert fake.committed is True
    assert fake.rolled_back is False
    assert not any("CREATE TEMP TABLE" in s.upper() for s in fake.executed)
    assert fake.final_insert_executed is False


def test_dqd2_carga_recusa_teto_em_D0_antes_de_qualquer_conexao(monkeypatch):
    def nao_conecta(*a, **k):
        raise AssertionError("nao deveria abrir conexao com teto invalido")
    monkeypatch.setattr(loader, "psycopg2", type("M", (), {
        "connect": staticmethod(nao_conecta)})())

    with pytest.raises(loader.IncrementalCeilingError):
        loader.execute_incremental_load("postgresql://w@host/db",
                                        date_to=date(2099, 1, 1))


def test_dqd2_o_teto_chega_ao_SQL_da_carga_de_verdade(monkeypatch):
    fake = _dqd2_conn(date(2026, 9, 2),
                      [date(2026, 9, 6), date(2026, 9, 7), date(2026, 9, 8)])
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))
    try:
        loader.execute_incremental_load("postgresql://w@host/db",
                                        date_to=date(2026, 9, 7))
    except Exception:
        pass  # o fake nao responde a todas as validacoes; o que importa e o SQL
    inserts = [s for s in fake.executed
               if s.upper().startswith("INSERT INTO STG_MARKETPLACE_REGION_DAILY")]
    assert inserts, "nenhum INSERT de staging foi montado"
    for s in inserts:
        assert "<= '2026-09-07'::date" in s


# --- append-only: nada fora da janela e' tocado --------------------------

# ---------------------------------------------------------------------------
# Gate DQ-D2-R — maximo ELEGIVEL real x maximo global
#
# O bug corrigido aqui: `min(max_date_source, teto)`. Com lacuna na fonte esse
# min() ANUNCIA uma data que nao existe. Todos os casos abaixo sao
# comportamentais — descrevem a fonte por lista de dias e deixam o fake
# calcular os maximos como o Postgres calcularia.
# ---------------------------------------------------------------------------

def _dqd2r_diagnose(dias_fonte_ml, gold_ml, teto=date(2026, 9, 7),
                    count_novas=7):
    fake = _dqd2_conn(gold_ml, dias_fonte_ml, count_novas=count_novas)
    return fake


def test_dqd2r_maximo_global_em_D0_com_elegivel_ANTERIOR_ao_teto(monkeypatch):
    """O CASO DO BUG, exatamente como o gate o descreve.

    Teto 07/09. Gold em 05/09. A fonte tem SOMENTE 05/09 e 08/09 — nao existe
    uma unica linha em 06/09 nem em 07/09.

    O codigo antigo fazia `min(08/09, 07/09) = 07/09` e anunciava 07/09 como
    maximo efetivo: uma data sem nenhuma linha na fonte. O correto e' 05/09,
    que e' o maior dia que a fonte REALMENTE tem abaixo do teto — e, sendo
    igual a Gold, nao ha novidade elegivel.
    """
    fake = _dqd2r_diagnose([date(2026, 9, 5), date(2026, 9, 8)],
                           date(2026, 9, 5))
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    report = loader.diagnose_incremental_load("postgresql://reader@host/db",
                                              date_to=date(2026, 9, 7))
    ml = {m.marketplace: m for m in report.marketplaces}["ml"]

    assert ml.max_date_source_global == date(2026, 9, 8)
    # O ponto do gate: NAO e' 07/09.
    assert ml.max_date_source_eligible == date(2026, 9, 5)
    assert ml.max_date_source_eligible != date(2026, 9, 7)
    assert ml.will_update is False
    # E nao houve nem contagem: nada a carregar.
    assert not any("SELECT COUNT(*) FROM (" in s.upper() for s in fake.executed)


def test_dqd2r_lacuna_exatamente_em_D_menos_1(monkeypatch):
    """Fonte com 06/09 e 08/09, teto 07/09: o buraco cai no proprio teto.

    O elegivel e' 06/09. Um `min()` diria 07/09 — o unico dia que a fonte
    garantidamente nao tem.
    """
    fake = _dqd2r_diagnose([date(2026, 9, 4), date(2026, 9, 6), date(2026, 9, 8)],
                           date(2026, 9, 4))
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    report = loader.diagnose_incremental_load("postgresql://reader@host/db",
                                              date_to=date(2026, 9, 7))
    ml = {m.marketplace: m for m in report.marketplaces}["ml"]
    assert ml.max_date_source_global == date(2026, 9, 8)
    assert ml.max_date_source_eligible == date(2026, 9, 6)
    # Ha novidade real (06/09 > 04/09), e ela para em 06/09.
    assert ml.will_update is True


def test_dqd2r_fonte_apenas_em_D0_e_futuro_nao_tem_elegivel(monkeypatch):
    """Ausencia elegivel e' None, nunca o teto. E nao se tenta carga nenhuma."""
    fake = _dqd2r_diagnose([date(2026, 9, 8), date(2026, 9, 9)],
                           date(2026, 9, 3))
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    report = loader.diagnose_incremental_load("postgresql://reader@host/db",
                                              date_to=date(2026, 9, 7))
    ml = {m.marketplace: m for m in report.marketplaces}["ml"]
    assert ml.max_date_source_global == date(2026, 9, 9)
    assert ml.max_date_source_eligible is None
    assert ml.will_update is False
    assert not any("SELECT COUNT(*) FROM (" in s.upper() for s in fake.executed)


def test_dqd2r_carga_com_fonte_apenas_em_D0_e_NO_OP_sem_fabricar_D_menos_1(monkeypatch):
    """Gold VAZIA e fonte so' com D0: zero D-1 fabricado, e NO_OP, nao falha.

    Este e' o cenario mais perigoso do bug antigo: sem Gold, `min_date` e'
    `date.min`, e um efetivo sintetizado em D-1 seria maior que ele — a carga
    entraria, montaria staging vazia e levantaria erro. Ou pior, publicaria uma
    janela que a fonte nao tem.
    """
    fake = IncrementalFakeConn(
        fetchall_responses=[(_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [])],
        fetchone_responses=[
            (_inc_exact(_SQL_MAX_DATE_ML_NORM), _fonte(date(2026, 9, 8))),
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), _fonte(date(2026, 9, 8))),
        ],
    )
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    r = loader.execute_incremental_load("postgresql://w@host/db",
                                        date_to=date(2026, 9, 7))
    assert r.no_op is True
    assert r.date_to == date(2026, 9, 7)
    assert r.rows_inserted == 0
    # Nenhuma staging criada, nenhum INSERT montado.
    assert not any("CREATE TEMP TABLE" in s.upper() for s in fake.executed)
    assert not any(s.upper().startswith("INSERT INTO") for s in fake.executed)


def test_dqd2r_fonte_com_linhas_em_D_menos_1_carrega_normalmente(monkeypatch):
    """O caso saudavel nao pode ter sido quebrado pela correcao."""
    fake = _dqd2r_diagnose([date(2026, 9, 6), date(2026, 9, 7)],
                           date(2026, 9, 5), count_novas=42)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    report = loader.diagnose_incremental_load("postgresql://reader@host/db",
                                              date_to=date(2026, 9, 7))
    ml = {m.marketplace: m for m in report.marketplaces}["ml"]
    assert ml.max_date_source_global == date(2026, 9, 7)
    assert ml.max_date_source_eligible == date(2026, 9, 7)
    assert ml.will_update is True
    assert ml.estimated_new_rows == 42


def test_dqd2r_limite_superior_e_INCLUSIVO_no_sql_dos_dois_marketplaces():
    """Se o teto virasse `<`, D-1 nunca seria publicado."""
    teto = date(2026, 9, 7)
    for sql in (loader._ml_incremental_select(date(2026, 9, 1), teto),
                loader._shopee_incremental_select(date(2026, 9, 1), teto)):
        norm = " ".join(sql.split())
        assert "<= '2026-09-07'::date" in norm
        assert "< '2026-09-07'::date" not in norm.replace("<= '2026-09-07'", "")


def test_dqd2r_nenhuma_linha_acima_do_teto_entra_na_staging(monkeypatch):
    """Duas travas independentes, ambas verificadas aqui.

    1. O maximo ELEGIVEL decide SE carrega;
    2. o SELECT tem `<= teto`, entao nem no marketplace que carrega uma linha
       de D0 pode escorrer para a staging.
    """
    fake = _dqd2_conn(date(2026, 9, 2),
                      [date(2026, 9, 6), date(2026, 9, 7), date(2026, 9, 8)])
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))
    try:
        loader.execute_incremental_load("postgresql://w@host/db",
                                        date_to=date(2026, 9, 7))
    except Exception:
        pass  # o fake nao responde a todas as validacoes; interessa o SQL
    inserts = [s for s in fake.executed
               if s.upper().startswith("INSERT INTO STG_MARKETPLACE_REGION_DAILY")]
    assert inserts
    for s in inserts:
        assert "<= '2026-09-07'::date" in s
        assert "2026-09-08" not in s


def test_dqd2r_datetime_e_recusado_antes_de_qualquer_conexao(monkeypatch):
    """`datetime` HERDA de `date`: o `isinstance` antigo o aceitava, e hora e
    fuso entrariam numa comparacao de dia fechado."""
    def nao_conecta(*a, **k):
        raise AssertionError("nao deveria abrir conexao com date_to invalido")
    monkeypatch.setattr(loader, "psycopg2", type("M", (), {
        "connect": staticmethod(nao_conecta)})())

    agora = _agora_brt(2026, 9, 8)
    for ruim in (datetime(2026, 9, 7),
                 datetime(2026, 9, 7, 23, 59, 59),
                 datetime(2026, 9, 7, tzinfo=timezone.utc)):
        with pytest.raises(loader.IncrementalCeilingError) as e:
            loader.resolve_incremental_date_to(ruim, agora)
        assert "datetime.date exato" in str(e.value)
        assert "datetime" in str(e.value)
        # Nao ecoa o valor recebido.
        assert ruim.isoformat() not in str(e.value)

    # E pelas duas portas de entrada tambem.
    with pytest.raises(loader.IncrementalCeilingError):
        loader.execute_incremental_load("postgresql://w@host/db",
                                        date_to=datetime(2026, 9, 7))
    with pytest.raises(loader.IncrementalCeilingError):
        loader.diagnose_incremental_load("postgresql://r@host/db",
                                         date_to=datetime(2026, 9, 7))

    # `date` exato continua aceito.
    assert loader.resolve_incremental_date_to(date(2026, 9, 7), agora) == date(2026, 9, 7)


def test_dqd2r_diagnose_diferencia_global_de_elegivel_nos_dois_marketplaces(monkeypatch):
    """Os dois campos existem, sao distintos e viajam por marketplace."""
    fake = IncrementalFakeConn(
        fetchall_responses=[
            (_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [
                (loader.ML_MARKETPLACE_ID, date(2026, 9, 1)),
                (loader.SHOPEE_MARKETPLACE_ID, date(2026, 9, 1)),
            ]),
        ],
        fetchone_responses=[
            # ML: buraco em 07/09, dado em 08/09.
            (_inc_exact(_SQL_MAX_DATE_ML_NORM),
             _fonte(date(2026, 9, 3), date(2026, 9, 8))),
            # Shopee: alcanca o teto de verdade.
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM),
             _fonte(date(2026, 9, 6), date(2026, 9, 7))),
            (_inc_all("SELECT COUNT(*) FROM (", "RAW.ML_ORDERS"), (5,)),
            (_inc_contains("SELECT COUNT(*) FROM ("), (9,)),
        ],
    )
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    report = loader.diagnose_incremental_load("postgresql://reader@host/db",
                                              date_to=date(2026, 9, 7))
    by = {m.marketplace: m for m in report.marketplaces}

    assert by["ml"].max_date_source_global == date(2026, 9, 8)
    assert by["ml"].max_date_source_eligible == date(2026, 9, 3)
    assert by["shopee"].max_date_source_global == date(2026, 9, 7)
    assert by["shopee"].max_date_source_eligible == date(2026, 9, 7)
    # Um marketplace com buraco nao contamina o outro.
    assert by["ml"].will_update is True
    assert by["shopee"].will_update is True

    # E o teto viajou como PARAMETRO, nao interpolado na string.
    params_dos_maximos = [
        pr for sql, pr in zip(fake.executed, fake.executed_params)
        if "MAX_ELIGIBLE" in sql.upper()
    ]
    assert params_dos_maximos
    assert all(pr == {"date_to": date(2026, 9, 7)} for pr in params_dos_maximos)
    assert all("2026-09-07" not in sql for sql in fake.executed
               if "MAX_ELIGIBLE" in sql.upper())


def test_dqd2r_no_op_quando_o_elegivel_nao_supera_a_gold(monkeypatch):
    """Elegivel IGUAL a' Gold e' no-op; elegivel MENOR que a Gold tambem.

    O segundo caso acontece de verdade quando a Gold foi carregada com uma
    janela maior e a fonte depois perdeu dias (reprocesso a montante).
    """
    for dias_fonte, gold in (
        ([date(2026, 9, 5), date(2026, 9, 8)], date(2026, 9, 5)),   # igual
        ([date(2026, 9, 4), date(2026, 9, 8)], date(2026, 9, 6)),   # menor
    ):
        fake = IncrementalFakeConn(
            fetchall_responses=[
                (_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [
                    (loader.ML_MARKETPLACE_ID, gold),
                    (loader.SHOPEE_MARKETPLACE_ID, gold),
                ]),
            ],
            fetchone_responses=[
                (_inc_exact(_SQL_MAX_DATE_ML_NORM), _fonte(*dias_fonte)),
                (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), _fonte(*dias_fonte)),
            ],
        )
        monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))
        r = loader.execute_incremental_load("postgresql://w@host/db",
                                            date_to=date(2026, 9, 7))
        assert r.no_op is True, (dias_fonte, gold)
        assert r.rows_inserted == 0
        assert not any("CREATE TEMP TABLE" in s.upper() for s in fake.executed)


# ---------------------------------------------------------------------------
# Gate DQ-D2-R2 — staging e reconciliacao na MESMA janela incremental
#
# O bug corrigido: `_ml_incremental_select`/`_shopee_incremental_select` ja
# limitavam a `min_date < data <= teto`, mas
# `_ml_gmv_source_recalc_incremental`/`_shopee_gmv_source_recalc_incremental`
# tinham fronteira superior ABERTA (`> min_date` e nada mais). Com D0 na fonte,
# o staging ia ate D-1 e a reconciliacao somava D0: divergencia FALSA e
# rollback.
#
# Os testes abaixo NAO devolvem totais previamente iguais. O fake guarda um
# conjunto de linhas (data -> valor) e, para CADA consulta, extrai a janela do
# proprio SQL recebido e soma so' o que cai dentro dela. Se as duas janelas
# divergirem, os totais divergem — como em producao.
# ---------------------------------------------------------------------------

_R2_PISO = re.compile(r"> '(\d{4}-\d{2}-\d{2})'::date")
_R2_TETO = re.compile(r"<= '(\d{4}-\d{2}-\d{2})'::date")


def _r2_janela(sql):
    """Extrai (piso exclusivo, teto inclusivo) do SQL. Teto None = ABERTO."""
    piso = _R2_PISO.search(sql)
    teto = _R2_TETO.search(sql)
    return (date.fromisoformat(piso.group(1)) if piso else None,
            date.fromisoformat(teto.group(1)) if teto else None)


def _r2_soma(linhas, sql):
    """Soma as linhas que caem na janela declarada por ESTE SQL."""
    piso, teto = _r2_janela(sql)
    total = Decimal("0")
    for d, valor in linhas.items():
        if piso is not None and not d > piso:
            continue
        if teto is not None and not d <= teto:
            continue
        total += valor
    return total


class _R2Conn(IncrementalFakeConn):
    """Conn cujo GMV e' CALCULADO, nunca fixado.

    - o total do staging usa a janela do INSERT de staging que foi executado;
    - o total da fonte usa a janela da propria consulta de reconciliacao.
    """

    def __init__(self, linhas_ml, linhas_shopee, gold, **kw):
        self.linhas = {"ml": linhas_ml, "shopee": linhas_shopee}
        self.gold = gold
        self.janela_staging = {}
        super().__init__(**kw)

    def cursor(self):
        return _R2Cursor(self)


class _R2Cursor(IncrementalFakeCursor):
    def execute(self, sql, params=None):
        super().execute(sql, params)
        norm = " ".join(sql.split())
        if norm.upper().startswith("INSERT INTO STG_MARKETPLACE_REGION_DAILY"):
            # Guarda a janela que o staging REALMENTE usou, por marketplace.
            mkt = "ml" if "RAW.ML_ORDERS" in norm.upper() else "shopee"
            self.conn.janela_staging[mkt] = norm

    def fetchone(self):
        last = self.conn.executed[-1]
        upper = last.upper()
        if "PG_TRY_ADVISORY_LOCK" in upper:
            return (self.conn.lock_acquired,)
        if "PG_ADVISORY_UNLOCK" in upper:
            return (True,)

        # --- GMV do STAGING: soma pela janela do INSERT de staging ---
        for mkt, mkt_id in (("shopee", loader.SHOPEE_MARKETPLACE_ID),
                            ("ml", loader.ML_MARKETPLACE_ID)):
            if ("STG_MARKETPLACE_REGION_DAILY" in upper
                    and f"MARKETPLACE_ID = {mkt_id}" in upper
                    and "SUM(GMV)" in upper):
                sql_staging = self.conn.janela_staging.get(mkt)
                if sql_staging is None:
                    return (Decimal("0"),)
                return (_r2_soma(self.conn.linhas[mkt], sql_staging),)

        # --- GMV da FONTE: soma pela janela da propria reconciliacao ---
        if "SHOPEE_PER_ORDER" in upper and "SUM(CASE WHEN ORDER_STATUS" in upper:
            return (_r2_soma(self.conn.linhas["shopee"], last),)
        if "RAW.ML_ORDERS WHERE STATUS = 'PAID'" in upper and "SUM(TOTAL_AMOUNT)" in upper:
            return (_r2_soma(self.conn.linhas["ml"], last),)

        return super().fetchone()


def _r2_conn(linhas_ml, linhas_shopee, gold=date(2026, 9, 5)):
    return _R2Conn(
        linhas_ml, linhas_shopee, gold,
        fetchall_responses=[
            (_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [
                (loader.ML_MARKETPLACE_ID, gold),
                (loader.SHOPEE_MARKETPLACE_ID, gold),
            ]),
        ],
        fetchone_responses=[
            (_inc_exact(_SQL_MAX_DATE_ML_NORM), _fonte(*linhas_ml.keys())),
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), _fonte(*linhas_shopee.keys())),
            (_inc_contains("HAVING COUNT(*) > 1"), (0,)),
            (_inc_contains("IS NULL OR MARKETPLACE_ID IS NULL"), (0,)),
            (_inc_contains("UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS"), (0,)),
            (_inc_all("GOLD.MARKETPLACE_REGION_DAILY",
                      f"MARKETPLACE_ID = {loader.TIKTOK_MARKETPLACE_ID}"), (0,)),
            (_inc_exact("SELECT COUNT(*) FROM STG_MARKETPLACE_REGION_DAILY"), (100,)),
        ],
    )


# Linhas de propriedade: 06/09 e 07/09 dentro do teto, 08/09 fora — e o valor
# de 08/09 e' MATERIAL de proposito (contraprova do caso D).
_R2_LINHAS_ML = {date(2026, 9, 6): Decimal("100.00"),
                 date(2026, 9, 7): Decimal("200.00"),
                 date(2026, 9, 8): Decimal("777777.77")}
_R2_LINHAS_SHOPEE = {date(2026, 9, 6): Decimal("50.00"),
                     date(2026, 9, 7): Decimal("70.00"),
                     date(2026, 9, 8): Decimal("999999.99")}


# --- A) mesma janela nas duas consultas, por marketplace -------------------

def test_dqd2r2_staging_e_reconciliacao_declaram_a_MESMA_janela_ml():
    piso, teto = date(2026, 9, 5), date(2026, 9, 7)
    staging = " ".join(loader._ml_incremental_select(piso, teto).split())
    recalc = " ".join(loader._ml_gmv_source_recalc_incremental(piso, teto).split())
    assert _r2_janela(staging) == (piso, teto)
    assert _r2_janela(recalc) == (piso, teto)
    assert _r2_janela(staging) == _r2_janela(recalc)


def test_dqd2r2_staging_e_reconciliacao_declaram_a_MESMA_janela_shopee():
    piso, teto = date(2026, 9, 5), date(2026, 9, 7)
    staging = " ".join(loader._shopee_incremental_select(piso, teto).split())
    recalc = " ".join(loader._shopee_gmv_source_recalc_incremental(piso, teto).split())
    assert _r2_janela(staging) == (piso, teto)
    assert _r2_janela(recalc) == (piso, teto)
    assert _r2_janela(staging) == _r2_janela(recalc)


def test_dqd2r2_a_reconciliacao_nunca_tem_fronteira_superior_aberta():
    """O defeito exato: `> min_date` e nada mais."""
    piso, teto = date(2026, 9, 5), date(2026, 9, 7)
    for sql in (loader._ml_gmv_source_recalc_incremental(piso, teto),
                loader._shopee_gmv_source_recalc_incremental(piso, teto)):
        _, achado_teto = _r2_janela(" ".join(sql.split()))
        assert achado_teto is not None, (
            "reconciliacao voltou a ter fronteira superior ABERTA: com D0 na "
            "fonte isto produz divergencia falsa e rollback"
        )
        assert achado_teto == teto


# --- B) date_to obrigatorio, sem default -----------------------------------

def test_dqd2r2_as_reconciliacoes_exigem_date_to():
    """Sem default: um default permitiria a volta silenciosa do teto aberto."""
    for fn in (loader._ml_gmv_source_recalc_incremental,
               loader._shopee_gmv_source_recalc_incremental):
        with pytest.raises(TypeError):
            fn(date(2026, 9, 5))


# --- C) caso comportamental por marketplace --------------------------------

def test_dqd2r2_carga_conclui_sem_divergencia_falsa_com_D0_na_fonte(monkeypatch):
    """Gold 05/09; fonte 06, 07 e 08/09; teto 07/09.

    Staging e reconciliacao consideram 06-07/09; 08/09 nao entra em NENHUM dos
    dois totais. Antes da correcao, este teste falhava com LoadValidationError.
    """
    fake = _r2_conn(_R2_LINHAS_ML, _R2_LINHAS_SHOPEE)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    r = loader.execute_incremental_load("postgresql://w@host/db",
                                        date_to=date(2026, 9, 7))

    assert r.no_op is False
    assert r.date_to == date(2026, 9, 7)
    assert sorted(r.marketplaces_updated) == ["ml", "shopee"]

    # Os totais batem E valem exatamente 06/09 + 07/09.
    assert r.ml_gmv_staging == r.ml_gmv_source == Decimal("300.00")
    assert r.shopee_gmv_staging == r.shopee_gmv_source == Decimal("120.00")

    # 08/09 nao participou de nenhum dos quatro totais.
    for total in (r.ml_gmv_staging, r.ml_gmv_source,
                  r.shopee_gmv_staging, r.shopee_gmv_source):
        assert total < Decimal("1000.00")

    assert fake.committed is True
    assert fake.rolled_back is False


def test_dqd2r2_um_marketplace_por_vez_tambem_reconcilia_na_janela(monkeypatch):
    """So' ML com novidade: Shopee parado nao participa e nao bloqueia."""
    parado = {date(2026, 9, 5): Decimal("10.00")}
    fake = _r2_conn(_R2_LINHAS_ML, parado)
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    r = loader.execute_incremental_load("postgresql://w@host/db",
                                        date_to=date(2026, 9, 7))
    assert r.marketplaces_updated == ["ml"]
    assert r.ml_gmv_staging == r.ml_gmv_source == Decimal("300.00")
    assert r.shopee_gmv_staging is None
    assert r.shopee_gmv_source is None
    assert fake.committed is True


# --- D) contraprova: D0 material reprovaria se entrasse --------------------

def test_dqd2r2_contraprova_D0_material_reprovaria_a_reconciliacao():
    """Se o teto sair da reconciliacao, a diferenca e' ordens de grandeza acima
    da tolerancia — o teste C nao passa por coincidencia numerica."""
    piso, teto = date(2026, 9, 5), date(2026, 9, 7)
    for linhas, dentro in ((_R2_LINHAS_ML, Decimal("300.00")),
                           (_R2_LINHAS_SHOPEE, Decimal("120.00"))):
        com_teto = _r2_soma(linhas, f"> '{piso}'::date AND x <= '{teto}'::date")
        sem_teto = _r2_soma(linhas, f"> '{piso}'::date")
        assert com_teto == dentro
        assert sem_teto > dentro
        assert (sem_teto - com_teto) > loader.GMV_RECONCILIATION_TOLERANCE * 1000


# --- F) o teto e' o date_to RESOLVIDO, nunca hoje nem valor sintetizado ----

def test_dqd2r2_o_teto_da_reconciliacao_e_o_date_to_resolvido(monkeypatch):
    """Passa um teto ANTERIOR a D-1 e exige que ele — e nao D-1, nem
    `date.today()`, nem o maximo global da fonte — apareca nas DUAS
    reconciliacoes."""
    escolhido = date(2026, 9, 6)
    fake = _r2_conn(_R2_LINHAS_ML, _R2_LINHAS_SHOPEE, gold=date(2026, 9, 5))
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))

    r = loader.execute_incremental_load("postgresql://w@host/db",
                                        date_to=escolhido)
    assert r.date_to == escolhido

    # `not startswith("INSERT")` e' essencial: o INSERT de staging Shopee
    # contem SHOPEE_PER_ORDER e ORDER_STATUS tambem, e entraria na lista.
    recalcs = [s for s in fake.executed
               if not s.upper().lstrip().startswith("INSERT")
               and (("SHOPEE_PER_ORDER" in s.upper() and "ORDER_STATUS" in s.upper())
                    or ("RAW.ML_ORDERS WHERE STATUS = 'PAID'" in s.upper()
                        and "SUM(TOTAL_AMOUNT)" in s.upper()))]
    assert len(recalcs) == 2
    for s in recalcs:
        assert _r2_janela(" ".join(s.split()))[1] == escolhido
        # nem D-1 do relogio real, nem o maximo global da fonte
        assert "2026-09-07" not in s
        assert "2026-09-08" not in s

    # E os totais param em 06/09.
    assert r.ml_gmv_staging == r.ml_gmv_source == Decimal("100.00")
    assert r.shopee_gmv_staging == r.shopee_gmv_source == Decimal("50.00")


def test_dqd2_incremental_continua_append_only_sem_delete_escopado():
    """O teto nao precisa de DELETE porque a carga nunca apaga.

    Se algum dia um DELETE entrar aqui, ele TEM de ser escopado a janela — e
    este teste falha primeiro, forcando a revisao.
    """
    teto = date(2026, 9, 7)
    for sql in (loader._ml_incremental_select(date(2026, 9, 1), teto),
                loader._shopee_incremental_select(date(2026, 9, 1), teto)):
        upper = " ".join(sql.split()).upper()
        for verbo in ("DELETE", "TRUNCATE", "UPDATE ", "DROP "):
            assert verbo not in upper, verbo


# --- CLI ------------------------------------------------------------------

def test_dqd2_cli_aceita_date_to_no_incremental(monkeypatch):
    vistos = {}
    monkeypatch.setattr(loader, "run_incremental_cli",
                        lambda **k: vistos.update(k) or 0)
    monkeypatch.setattr(loader, "run_diagnose_cli", lambda *a, **k: 99)
    assert loader.main(["--incremental", "--date-to", "2026-09-07"]) == 0
    assert vistos["date_to"] == date(2026, 9, 7)


def test_dqd2_cli_aceita_date_to_no_diagnose(monkeypatch):
    vistos = []
    monkeypatch.setattr(loader, "run_diagnose_cli",
                        lambda *a, **k: vistos.append(a) or 0)
    assert loader.main(["--diagnose", "--date-to", "2026-09-07"]) == 0
    assert vistos == [(date(2026, 9, 7),)]


def test_dqd2_cli_RECUSA_date_from_no_incremental(capsys):
    """O piso do incremental e o watermark da Gold, nao escolha do chamador.

    Aceitar `--date-from` ali criaria janela MEIO aplicada.
    """
    with pytest.raises(SystemExit):
        loader.main(["--incremental", "--date-from", "2026-09-01"])
    err = capsys.readouterr().err
    assert "--date-from" in err
    assert "MAX(date) já carregado" in err


def test_dqd2_cli_do_incremental_recusa_D0_sem_abrir_preflight(monkeypatch, capsys):
    def nao_resolve(*a, **k):
        raise AssertionError("nao deveria resolver o secret com teto invalido")
    monkeypatch.setattr(loader, "_resolve_write_url", nao_resolve)
    monkeypatch.setattr(loader, "last_closed_date",
                        lambda agora=None: date(2026, 9, 7))

    rc = loader.run_incremental_cli(date_to=date(2026, 9, 8))
    assert rc == 2
    err = capsys.readouterr().err
    assert "--incremental bloqueado" in err
    assert "ultimo dia fechado" in err


def test_dqd2_cli_declara_o_teto_aplicado(monkeypatch, capsys):
    monkeypatch.setattr(loader, "last_closed_date",
                        lambda agora=None: date(2026, 9, 7))
    monkeypatch.setattr(loader, "_resolve_write_url",
                        lambda *a, **k: ("postgresql://w@h/db", None))
    monkeypatch.setattr(loader.write_conn, "run_preflight",
                        lambda *a, **k: type("R", (), {
                            "ok": True, "safe_summary": {}, "warnings": [],
                            "blocking_reasons": []})())
    monkeypatch.setattr(loader, "execute_incremental_load",
                        lambda url, date_to=None: loader.IncrementalLoadResult(
                            no_op=True, date_to=date_to))
    rc = loader.run_incremental_cli()
    assert rc == 0
    out = capsys.readouterr().out
    assert "Teto superior INCLUSIVO desta carga: 2026-09-07" in out
    # NO_OP nomeia o teto — "nada novo" e "nada novo ATE o teto" sao coisas
    # diferentes, e a mensagem precisa dizer qual.
    assert "ATE o teto 2026-09-07" in out


# --- orquestrador ---------------------------------------------------------

def test_dqd2_orquestrador_herda_o_teto_por_default():
    """O step do full_daily roda `--incremental` sem argumento de data.

    Ele nao precisa passar `--date-to`: o default JA e' D-1. Este teste fixa
    isso — se o default virar "sem teto", ele falha.
    """
    from pipelines.ops.orchestrate import PIPELINES
    p = PIPELINES["full_daily"]
    steps = getattr(p, "steps", p)
    step = next(s for s in steps if s.name == "gold_regional_incremental")
    assert step.module == "pipelines.ingestion.gold_regional.loader"
    assert "--incremental" in step.args
    # Nao passa data: o teto vem do default, que e' D-1.
    assert "--date-to" not in step.args
    assert "--date-from" not in step.args
    # E o default e' de fato D-1, nao "aberto".
    assert loader.resolve_incremental_date_to(
        None, _agora_brt(2026, 9, 8)) == date(2026, 9, 7)


def test_dqd2_mensagem_de_recusa_nao_vaza_infraestrutura():
    try:
        loader.resolve_incremental_date_to(date(2099, 1, 1),
                                           _agora_brt(2026, 9, 8))
    except loader.IncrementalCeilingError as exc:
        msg = str(exc)
    for proibido in ("postgres", "@", "5432", "password", "senha", "amazonaws",
                     "neon.tech"):
        assert proibido not in msg.lower(), proibido


def test_dqd2_gold_ADIANTE_do_teto_e_no_op_nao_erro():
    """Caso analogo a `date_from > date_to`: o piso (watermark) ja passou do teto.

    No incremental o piso NAO e' parametro — e o MAX(date) por marketplace. Logo
    `date_from > date_to` nao pode acontecer por argumento, mas PODE acontecer
    logicamente quando o gold ja alcancou alem do teto pedido (reprocesso
    estreito com `--date-to` antigo). O certo e' NO_OP, nao erro nem DELETE.
    """
    fake = IncrementalFakeConn(
        fetchall_responses=[
            (_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [
                (loader.ML_MARKETPLACE_ID, date(2026, 9, 7)),
                (loader.SHOPEE_MARKETPLACE_ID, date(2026, 9, 7)),
            ]),
        ],
        fetchone_responses=[
            (_inc_exact(_SQL_MAX_DATE_ML_NORM), (date(2026, 9, 8),)),
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), (date(2026, 9, 8),)),
        ],
    )
    import pipelines.ingestion.gold_regional.loader as _l
    orig = _l.psycopg2
    _l.psycopg2 = _FakePsycopg2Module(fake)
    try:
        # teto de 31/08, mas o gold ja esta em 07/09
        r = _l.execute_incremental_load("postgresql://w@host/db",
                                        date_to=date(2026, 8, 31))
    finally:
        _l.psycopg2 = orig
    assert r.no_op is True
    assert r.rows_inserted == 0
    assert fake.final_insert_executed is False
    # E NENHUM statement destrutivo foi emitido para "ajustar" a janela.
    for s in fake.executed:
        up = s.upper()
        assert "DELETE" not in up and "TRUNCATE" not in up


def test_dqd2_reexecucao_e_idempotente_por_construcao(monkeypatch):
    """Segunda execucao com o mesmo teto nao insere nada.

    A carga e' append-only acima do watermark. Depois da primeira rodada o
    `MAX(date)` do gold ALCANCA o teto, e o predicado de novidade
    `min(fonte, teto) > watermark` fica falso. Idempotencia nao depende de
    upsert nem de DELETE — depende do watermark subir.
    """
    teto = date(2026, 9, 7)
    # Estado APOS a primeira carga: gold == teto, fonte ainda em D0.
    fake = IncrementalFakeConn(
        fetchall_responses=[
            (_inc_exact(_SQL_MAX_DATE_GOLD_NORM), [
                (loader.ML_MARKETPLACE_ID, teto),
                (loader.SHOPEE_MARKETPLACE_ID, teto),
            ]),
        ],
        fetchone_responses=[
            (_inc_exact(_SQL_MAX_DATE_ML_NORM), (date(2026, 9, 8),)),
            (_inc_exact(_SQL_MAX_DATE_SHOPEE_NORM), (date(2026, 9, 8),)),
        ],
    )
    monkeypatch.setattr(loader, "psycopg2", _FakePsycopg2Module(fake))
    r = loader.execute_incremental_load("postgresql://w@host/db", date_to=teto)
    assert r.no_op is True
    assert r.date_to == teto
    assert fake.final_insert_executed is False
