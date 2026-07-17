"""
Testes de `diagnose_shopee_window` / `--diagnose-shopee-window` — Gate S2.

Escopo deste gate: SOMENTE o modo read-only. Nenhum teste aqui deve exercitar
DELETE/INSERT/staging/secret de escrita — isso é Gate S3, ainda não
implementado. Usa conexões/cursores psycopg2 falsos — nenhum banco real é
tocado (mesmo padrão de test_gold_regional_loader.py, fakes locais e
independentes, sem importar as classes fake daquele arquivo).
"""
from __future__ import annotations

import inspect
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

from pipelines.ingestion.gold_regional import loader


# ---------------------------------------------------------------------------
# Fakes — só fetchone() é usado por diagnose_shopee_window (cada query é um
# agregado de uma linha só, nunca fetchall).
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
        self.conn.executed.append((norm, params))

    def fetchone(self):
        norm, _params = self.conn.executed[-1]
        upper = norm.upper()
        for matcher, value in self.conn.fetchone_responses:
            if matcher(upper):
                return value
        raise AssertionError(f"nenhuma resposta simulada para a query: {norm!r}")


class FakeConn:
    def __init__(self, fetchone_responses=None):
        self.executed: list[tuple[str, dict]] = []
        self.closed = False
        self.readonly = None
        self.autocommit = None
        self.isolation_level = None
        self.committed = False
        self.rolled_back = False
        self.cursors_handed_out = 0
        self.fetchone_responses = fetchone_responses or []

    def cursor(self):
        self.cursors_handed_out += 1
        return FakeCursor(self)

    def set_session(self, readonly=None, isolation_level=None, autocommit=None):
        self.readonly = readonly
        self.isolation_level = isolation_level
        self.autocommit = autocommit

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakePsycopg2Module:
    def __init__(self, conn):
        self._conn = conn
        self.connect_calls = 0

    def connect(self, url, connect_timeout=15):
        self.connect_calls += 1
        return self._conn


def _contains(*substrings):
    return lambda upper: all(s in upper for s in substrings)


# Marcadores únicos e mutuamente exclusivos das 6 queries que
# diagnose_shopee_window executa (matcher por substring, order-independent —
# cada marcador é exclusivo de uma única query):
#   1. agregados atuais da Gold  -> COUNT(*), COALESCE(SUM(GMV)...) FROM GOLD
#   2. agregados recalc da fonte -> COALESCE(SUM(GMV)...) FROM SHOPEE_WINDOW_RECALC
#   3. duplicidade               -> HAVING COUNT(*) > 1
#   4. nulos obrigatórios        -> DATE IS NULL OR MARKETPLACE_ID IS NULL
#   5. numerador > denominador   -> UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS
#   6. key-diff (Gate S2.1)      -> FULL OUTER JOIN
def _happy_responses(
    gold=(5, Decimal("1000.00"), 100),
    recalc=(7, Decimal("1200.00"), 110),
    dup=(0,),
    nulls=(0,),
    bad=(0,),
    key_diff=(0, 0, 0),  # gold_only, source_only, changed
):
    return [
        (_contains("COALESCE(SUM(GMV), 0), COALESCE(SUM(ORDERS), 0) FROM GOLD.MARKETPLACE_REGION_DAILY"), gold),
        (_contains("COALESCE(SUM(GMV), 0), COALESCE(SUM(ORDERS), 0) FROM SHOPEE_WINDOW_RECALC"), recalc),
        (_contains("HAVING COUNT(*) > 1"), dup),
        (_contains("DATE IS NULL OR MARKETPLACE_ID IS NULL"), nulls),
        (_contains("UF_KNOWN_ORDERS > UF_ELIGIBLE_ORDERS"), bad),
        (_contains("FULL OUTER JOIN"), key_diff),
    ]


def _happy_conn(**overrides):
    return FakeConn(fetchone_responses=_happy_responses(**overrides))


_D_FROM = date(2026, 6, 1)
_D_TO = date(2026, 6, 30)


# ---------------------------------------------------------------------------
# Caminho feliz — cálculo de deltas, rows_to_delete/insert, flags
# ---------------------------------------------------------------------------

def test_diagnose_shopee_window_caminho_feliz_calcula_deltas_e_impacto(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.date_from == _D_FROM
    assert report.date_to == _D_TO
    assert report.gold_rows == 5
    assert report.gold_gmv == Decimal("1000.00")
    assert report.gold_orders == 100
    assert report.recalculated_rows == 7
    assert report.recalculated_gmv == Decimal("1200.00")
    assert report.recalculated_orders == 110
    assert report.rows_to_delete == 5
    assert report.rows_to_insert == 7
    assert report.gmv_delta == Decimal("200.00")
    assert report.orders_delta == 10
    assert report.overlaps_existing_gold_data is True
    assert report.zero_source_risk is False
    assert report.duplicate_key_count == 0
    assert report.null_required_count == 0
    assert report.numerator_over_denominator_count == 0
    assert fake_conn.closed is True


def test_diagnose_shopee_window_sem_sobreposicao_quando_gold_vazia(monkeypatch):
    fake_conn = _happy_conn(gold=(0, Decimal("0"), 0))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.overlaps_existing_gold_data is False
    assert report.zero_source_risk is False  # gold=0 e recalc>0 -- nao e risco, e so' janela nova
    assert report.rows_to_delete == 0
    assert report.rows_to_insert == 7


# ---------------------------------------------------------------------------
# Alerta de risco: fonte recalculada zerada com Gold tendo linhas
# ---------------------------------------------------------------------------

def test_diagnose_shopee_window_sinaliza_risco_quando_gold_tem_linhas_e_fonte_zera(monkeypatch):
    fake_conn = _happy_conn(gold=(5, Decimal("1000.00"), 100), recalc=(0, Decimal("0"), 0))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.zero_source_risk is True
    assert report.recalculated_rows == 0
    assert report.rows_to_insert == 0
    assert report.rows_to_delete == 5


def test_diagnose_shopee_window_nao_sinaliza_risco_quando_ambos_zerados(monkeypatch):
    fake_conn = _happy_conn(gold=(0, Decimal("0"), 0), recalc=(0, Decimal("0"), 0))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.zero_source_risk is False


# ---------------------------------------------------------------------------
# Duplicidade / nulos / numerador>denominador reportados quando simulados
# ---------------------------------------------------------------------------

def test_diagnose_shopee_window_reporta_duplicidade_simulada(monkeypatch):
    fake_conn = _happy_conn(dup=(3,))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.duplicate_key_count == 3


def test_diagnose_shopee_window_reporta_nulos_simulados(monkeypatch):
    fake_conn = _happy_conn(nulls=(4,))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.null_required_count == 4


def test_diagnose_shopee_window_reporta_numerador_maior_que_denominador_simulado(monkeypatch):
    fake_conn = _happy_conn(bad=(2,))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.numerator_over_denominator_count == 2


# ---------------------------------------------------------------------------
# Validações de janela — nunca chegam a abrir conexão
# ---------------------------------------------------------------------------

def test_diagnose_shopee_window_rejeita_date_from_maior_que_date_to(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("nao deveria conectar com janela invalida")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    with pytest.raises(loader.InvalidWindowError, match="posterior"):
        loader.diagnose_shopee_window("postgresql://reader@host/db", date(2026, 6, 30), date(2026, 6, 1))


def test_diagnose_shopee_window_rejeita_data_futura(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("nao deveria conectar com data futura")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    futuro = date(2099, 1, 1)
    with pytest.raises(loader.InvalidWindowError, match="futuro"):
        loader.diagnose_shopee_window("postgresql://reader@host/db", futuro, futuro)


def test_diagnose_shopee_window_rejeita_janela_maior_que_180_dias(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("nao deveria conectar com janela grande demais")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    date_from = date(2025, 1, 1)
    date_to = date_from + timedelta(days=200)  # ainda no passado (nao dispara o guard de data futura)
    with pytest.raises(loader.InvalidWindowError, match="excede o máximo"):
        loader.diagnose_shopee_window("postgresql://reader@host/db", date_from, date_to)


def test_diagnose_shopee_window_aceita_janela_de_exatos_180_dias(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    date_from = date(2026, 1, 1)
    date_to = date_from + timedelta(days=179)  # 180 dias inclusive
    report = loader.diagnose_shopee_window("postgresql://reader@host/db", date_from, date_to)

    assert report.date_to == date_to


# ---------------------------------------------------------------------------
# Somente leitura: readonly=True, nunca toca secret/conexão de escrita
# ---------------------------------------------------------------------------

def test_diagnose_shopee_window_usa_sessao_readonly(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert fake_conn.readonly is True


def test_diagnose_shopee_window_nunca_referencia_secret_ou_conexao_de_escrita():
    source = inspect.getsource(loader.diagnose_shopee_window)
    for forbidden in (
        "load_write_secret", "DEFAULT_WRITE_SECRET_PATH", "_resolve_write_url",
        "open_write_connection", "try_acquire_advisory_lock", ".env.gold-write.local",
    ):
        assert forbidden not in source, f"diagnose_shopee_window nao deveria referenciar {forbidden!r}"


def test_diagnose_shopee_window_nunca_executa_ddl_dml(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    forbidden = re.compile(r"\bINSERT\b|\bDELETE\b|\bUPDATE\b|\bTRUNCATE\b|\bDROP\b|\bCREATE\s+TABLE\b|\bCREATE\s+TEMP\b", re.IGNORECASE)
    for sql, _params in fake_conn.executed:
        assert not forbidden.search(sql), f"statement de escrita encontrado no diagnose: {sql[:120]!r}"


# ---------------------------------------------------------------------------
# Gate S2.1 — snapshot consistente (transação read-only REPEATABLE READ)
# ---------------------------------------------------------------------------

def test_diagnose_shopee_window_snapshot_transacional_readonly_repeatable_read(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert fake_conn.readonly is True
    assert fake_conn.autocommit is False
    assert fake_conn.isolation_level == "REPEATABLE READ"


def test_diagnose_shopee_window_todas_consultas_na_mesma_conexao(monkeypatch):
    fake_conn = _happy_conn()
    fake_module = FakePsycopg2Module(fake_conn)
    monkeypatch.setattr(loader, "psycopg2", fake_module)

    loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    # uma única conexão aberta para todas as 6 consultas do snapshot
    assert fake_module.connect_calls == 1
    # as 6 consultas do diagnose foram todas registradas na mesma conn
    assert len(fake_conn.executed) == 6


def test_diagnose_shopee_window_rollback_no_sucesso(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    # transação só de leitura: rollback explícito no sucesso, nunca commit
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False
    assert fake_conn.closed is True


def test_diagnose_shopee_window_rollback_e_close_em_falha_de_consulta(monkeypatch):
    # fonte responde a tudo menos ao key-diff -> ultima consulta estoura
    class FailingFakeCursor(FakeCursor):
        def fetchone(self):
            norm, _ = self.conn.executed[-1]
            if "FULL OUTER JOIN" in norm.upper():
                raise RuntimeError("falha simulada no key-diff")
            return super().fetchone()

    class FailingFakeConn(FakeConn):
        def cursor(self):
            self.cursors_handed_out += 1
            return FailingFakeCursor(self)

    fake_conn = FailingFakeConn(fetchone_responses=_happy_responses())
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    with pytest.raises(RuntimeError, match="falha simulada"):
        loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False
    assert fake_conn.closed is True


def test_diagnose_shopee_window_fecha_conexao_mesmo_se_set_session_falhar(monkeypatch):
    """Revisão pós-Gate S2.1: `set_session()` roda DENTRO do try — se ela
    falhar (antes de qualquer cursor/query), a conexão ainda precisa ser
    fechada e a exceção original precisa ser propagada intacta."""
    class SetSessionFailsConn(FakeConn):
        def set_session(self, readonly=None, isolation_level=None, autocommit=None):
            raise RuntimeError("falha simulada em set_session")

    fake_conn = SetSessionFailsConn(fetchone_responses=_happy_responses())
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    with pytest.raises(RuntimeError, match="falha simulada em set_session"):
        loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert fake_conn.closed is True
    assert fake_conn.executed == []  # nenhuma consulta chegou a ser executada
    assert fake_conn.cursors_handed_out == 0


# ---------------------------------------------------------------------------
# Gate S2.1 — comparação exata por chave (FULL OUTER JOIN + IS DISTINCT FROM)
# ---------------------------------------------------------------------------

def test_key_diff_gold_e_fonte_identicas_would_change_data_false(monkeypatch):
    fake_conn = _happy_conn(key_diff=(0, 0, 0))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.gold_only_key_count == 0
    assert report.source_only_key_count == 0
    assert report.changed_key_count == 0
    assert report.would_change_data is False


def test_key_diff_chave_so_na_gold(monkeypatch):
    fake_conn = _happy_conn(key_diff=(3, 0, 0))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.gold_only_key_count == 3
    assert report.would_change_data is True


def test_key_diff_chave_so_na_fonte(monkeypatch):
    fake_conn = _happy_conn(key_diff=(0, 4, 0))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.source_only_key_count == 4
    assert report.would_change_data is True


def test_key_diff_mesma_chave_com_gmv_diferente(monkeypatch):
    fake_conn = _happy_conn(key_diff=(0, 0, 2))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.changed_key_count == 2
    assert report.would_change_data is True


def test_key_diff_redistribuicao_uf_com_totais_iguais(monkeypatch):
    """Cenario critico: pedidos migram de uf=XX para uf=SP. GMV/orders/linhas
    TOTAIS continuam iguais (agregados nao mudam), mas a comparacao por chave
    detecta: a chave de XX some (gold_only) e a de SP surge (source_only)."""
    fake_conn = _happy_conn(
        gold=(5, Decimal("1000.00"), 100),
        recalc=(5, Decimal("1000.00"), 100),  # totais IDENTICOS
        key_diff=(1, 1, 0),                    # mas 1 chave so' na gold + 1 so' na fonte
    )
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.gmv_delta == Decimal("0.00")
    assert report.orders_delta == 0
    assert report.gold_only_key_count == 1
    assert report.source_only_key_count == 1
    assert report.would_change_data is True  # detectou apesar dos totais iguais


def test_sql_key_diff_usa_full_outer_join_e_is_distinct_from_todos_os_campos():
    sql_upper = loader.SQL_SHOPEE_WINDOW_KEY_DIFF.upper()
    assert "FULL OUTER JOIN" in sql_upper
    # chave do join
    for key_col in ("DATE", "MARKETPLACE_ID", "LOJA_ID", "UF"):
        assert f"G.{key_col} = S.{key_col}" in sql_upper
    # todos os 13 campos de negocio comparados com IS DISTINCT FROM
    business_cols = [
        "gmv", "orders", "units_sold", "canceled_orders", "returned_orders",
        "seller_shipping_cost", "buyer_shipping_fee", "estimated_shipping_fee", "reverse_shipping_fee",
        "uf_known_orders", "uf_eligible_orders",
        "shipping_cost_covered_orders", "shipping_cost_eligible_orders",
    ]
    for col in business_cols:
        assert f"G.{col.upper()} IS DISTINCT FROM S.{col.upper()}" in sql_upper
    # campos tecnicos NUNCA entram na comparacao
    assert "IS DISTINCT FROM S.INGESTED_AT" not in sql_upper
    assert "IS DISTINCT FROM S.ID" not in sql_upper
    assert "IS DISTINCT FROM S.SOURCE_UPDATED_AT" not in sql_upper


def test_key_diff_mantem_dedup_arquivo_vencedor():
    """A comparacao por chave recalcula a fonte com o MESMO dedup — precisa
    conter DISTINCT ON e file_id DESC dentro do proprio SQL do key-diff."""
    sql_upper = loader.SQL_SHOPEE_WINDOW_KEY_DIFF.upper()
    assert "DISTINCT ON (BRAND, ORDER_ID)" in sql_upper
    assert "ORDER BY BRAND, ORDER_ID, FILE_ID DESC" in sql_upper


# ---------------------------------------------------------------------------
# Gate S2.1 — structurally_safe_for_refresh
# ---------------------------------------------------------------------------

def test_structurally_safe_true_quando_sem_bloqueios(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.structurally_safe_for_refresh is True


def test_structurally_unsafe_por_zero_source_risk(monkeypatch):
    fake_conn = _happy_conn(gold=(5, Decimal("1000.00"), 100), recalc=(0, Decimal("0"), 0))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.zero_source_risk is True
    assert report.structurally_safe_for_refresh is False


def test_structurally_unsafe_por_duplicidade(monkeypatch):
    fake_conn = _happy_conn(dup=(2,))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.structurally_safe_for_refresh is False


def test_structurally_unsafe_por_nulos(monkeypatch):
    fake_conn = _happy_conn(nulls=(1,))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.structurally_safe_for_refresh is False


def test_structurally_unsafe_por_numerador_maior_denominador(monkeypatch):
    fake_conn = _happy_conn(bad=(1,))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.structurally_safe_for_refresh is False


def test_janela_reconciliada_e_estruturalmente_sa_sao_independentes(monkeypatch):
    """would_change_data=False (reconciliada) NAO implica inseguranca — as
    duas dimensoes sao ortogonais."""
    fake_conn = _happy_conn(
        gold=(5, Decimal("1000.00"), 100),
        recalc=(5, Decimal("1000.00"), 100),
        key_diff=(0, 0, 0),
    )
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    report = loader.diagnose_shopee_window("postgresql://reader@host/db", _D_FROM, _D_TO)

    assert report.would_change_data is False
    assert report.structurally_safe_for_refresh is True


def test_nenhuma_constante_sql_gate_s21_contem_ddl_dml():
    """SQL_SHOPEE_WINDOW_KEY_DIFF (Gate S2.1) e demais constantes de janela
    nunca contem INSERT/UPDATE/DELETE/TRUNCATE/DDL."""
    forbidden = re.compile(
        r"\bINSERT\s+INTO\b|\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b|\bTRUNCATE\b|"
        r"\bDROP\s+(TABLE|SCHEMA|DATABASE|INDEX|VIEW)\b|\bCREATE\s+(TABLE|TEMP|INDEX|VIEW)\b|\bALTER\s+TABLE\b",
        re.IGNORECASE,
    )
    match = forbidden.search(loader.SQL_SHOPEE_WINDOW_KEY_DIFF)
    assert not match, f"statement destrutivo/DDL no key-diff: {match and match.group(0)!r}"


def test_key_diff_usa_is_distinct_from_nunca_comparacao_ingenua_de_igualdade():
    """NULL vs. 0 (ex.: seller_shipping_cost NULL na Gold, 0 recalculado, ou
    vice-versa) so' e' detectado por IS DISTINCT FROM — um `<>`/`!=` retorna
    NULL nesse caso e a mudanca passaria despercebida. O key-diff nunca pode
    comparar os campos de negocio com `<>`/`=`/`!=` puro."""
    predicate = loader._WINDOW_CHANGED_PREDICATE.upper()
    assert "IS DISTINCT FROM" in predicate
    # nenhum operador de igualdade ingenuo entre g.<campo> e s.<campo>
    assert not re.search(r"G\.\w+\s*(<>|!=|=)\s*S\.\w+", predicate)
    # um IS DISTINCT FROM por campo de negocio (13)
    assert predicate.count("IS DISTINCT FROM") == len(loader._WINDOW_BUSINESS_COLUMNS) == 13


# ---------------------------------------------------------------------------
# Gate S2.1 — CLI: exit codes por estado estrutural
# ---------------------------------------------------------------------------

def test_cli_retorna_zero_quando_seguro_e_ha_mudanca(monkeypatch, capsys):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    fake_conn = _happy_conn(key_diff=(1, 2, 3))  # would_change_data=True, mas estruturalmente seguro
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    rc = loader.run_diagnose_shopee_window_cli(_D_FROM, _D_TO)

    out = capsys.readouterr().out
    assert rc == 0
    assert "would_change_data=True" in out
    assert "structurally_safe_for_refresh=True" in out
    assert "gold_only_key_count=1" in out
    assert "source_only_key_count=2" in out
    assert "changed_key_count=3" in out


def test_cli_retorna_zero_quando_seguro_sem_mudanca(monkeypatch, capsys):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    fake_conn = _happy_conn(
        gold=(5, Decimal("1000.00"), 100), recalc=(5, Decimal("1000.00"), 100), key_diff=(0, 0, 0),
    )
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    rc = loader.run_diagnose_shopee_window_cli(_D_FROM, _D_TO)

    out = capsys.readouterr().out
    assert rc == 0  # janela reconciliada NAO e' erro
    assert "would_change_data=False" in out
    assert "structurally_safe_for_refresh=True" in out


def test_cli_retorna_quatro_quando_estruturalmente_inseguro(monkeypatch, capsys):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    fake_conn = _happy_conn(dup=(2,))  # duplicidade -> structurally_safe_for_refresh=False
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    rc = loader.run_diagnose_shopee_window_cli(_D_FROM, _D_TO)

    assert rc == 4
    combined = capsys.readouterr()
    assert "structurally_safe_for_refresh=False" in combined.out
    assert "ESTRUTURALMENTE INSEGURA" in combined.err


def test_cli_retorna_quatro_quando_zero_source_risk(monkeypatch, capsys):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    fake_conn = _happy_conn(gold=(5, Decimal("1000.00"), 100), recalc=(0, Decimal("0"), 0))
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    rc = loader.run_diagnose_shopee_window_cli(_D_FROM, _D_TO)

    assert rc == 4


# ---------------------------------------------------------------------------
# SQL — mesma lógica de dedup vigente, filtro por janela via bind parameter
# ---------------------------------------------------------------------------

def test_sql_recalc_mantem_dedup_arquivo_vencedor_com_file_id_desc():
    sql_upper = loader.SQL_SHOPEE_WINDOW_RECALC_ROWS.upper()
    assert "DISTINCT ON (BRAND, ORDER_ID)" in sql_upper
    assert "ORDER BY BRAND, ORDER_ID, FILE_ID DESC" in sql_upper
    # a fonte precisa aparecer 2x: achar o file_id vencedor + JOIN de volta
    # trazendo todas as linhas de SKU (preserva multi-item) -- mesmo
    # requisito já testado para a carga em test_gold_regional_loader.py
    occurrences = sql_upper.count("SILVER.STG_SHOPEE_ORDER_ITEM_SNAPSHOTS")
    assert occurrences >= 2, "dedup de passo unico detectado -- perderia unidades multi-item"
    assert "SUM(QUANTITY)" in sql_upper


def test_sql_recalc_filtra_por_order_created_at_date_e_janela_via_bind_param():
    sql = loader.SQL_SHOPEE_WINDOW_RECALC_ROWS
    sql_upper = sql.upper()
    assert "ORDER_CREATED_AT)::DATE AS ORDER_DATE" in sql_upper
    assert "ORDER_DATE BETWEEN %(DATE_FROM)S AND %(DATE_TO)S" in sql_upper
    # nunca literal de data hardcoded (bind parameter, nao string formatada)
    assert not re.search(r"BETWEEN\s+'\d{4}-\d{2}-\d{2}'", sql)


def test_sql_gold_aggregates_filtra_por_marketplace_shopee_e_janela():
    sql_upper = loader.SQL_SHOPEE_WINDOW_GOLD_AGGREGATES.upper()
    assert f"MARKETPLACE_ID = {loader.SHOPEE_MARKETPLACE_ID}" in sql_upper
    assert "DATE BETWEEN %(DATE_FROM)S AND %(DATE_TO)S" in sql_upper
    assert "GOLD.MARKETPLACE_REGION_DAILY" in sql_upper


def test_nenhum_sql_destrutivo_nas_constantes_de_janela_shopee():
    forbidden = re.compile(r"\bDROP\s+(TABLE|SCHEMA|DATABASE|INDEX|VIEW)\b|\bTRUNCATE\b|\bDELETE\s+FROM\b|\bUPDATE\s+\w+\s+SET\b|\bINSERT\s+INTO\b|\bCREATE\s+TABLE\b", re.IGNORECASE)
    window_sql_constants = [
        v for k, v in vars(loader).items()
        if k.startswith("SQL_SHOPEE_WINDOW") and isinstance(v, str)
    ]
    assert len(window_sql_constants) >= 5
    for sql in window_sql_constants:
        match = forbidden.search(sql)
        assert not match, f"statement destrutivo/de escrita suspeito: {match.group(0)!r} em {sql[:80]}..."


# ---------------------------------------------------------------------------
# CLI — --diagnose-shopee-window exige --date-from/--date-to, valida formato
# ---------------------------------------------------------------------------

def test_cli_diagnose_shopee_window_exige_date_from_e_date_to():
    with pytest.raises(SystemExit):
        loader.main(["--diagnose-shopee-window"])


def test_cli_diagnose_shopee_window_exige_date_to_mesmo_com_date_from():
    with pytest.raises(SystemExit):
        loader.main(["--diagnose-shopee-window", "--date-from", "2026-06-01"])


def test_cli_diagnose_shopee_window_rejeita_formato_invalido():
    with pytest.raises(SystemExit):
        loader.main(["--diagnose-shopee-window", "--date-from", "01/06/2026", "--date-to", "2026-06-30"])


def test_cli_nao_aceita_diagnose_e_diagnose_shopee_window_juntos():
    with pytest.raises(SystemExit):
        loader.main(["--diagnose", "--diagnose-shopee-window", "--date-from", "2026-06-01", "--date-to", "2026-06-30"])


def test_main_diagnose_shopee_window_chama_run_diagnose_shopee_window_cli(monkeypatch):
    captured = {}

    def fake_run(date_from, date_to):
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        return 0

    monkeypatch.setattr(loader, "run_diagnose_shopee_window_cli", fake_run)

    rc = loader.main(["--diagnose-shopee-window", "--date-from", "2026-06-01", "--date-to", "2026-06-30"])

    assert rc == 0
    assert captured["date_from"] == date(2026, 6, 1)
    assert captured["date_to"] == date(2026, 6, 30)


def test_main_diagnose_flag_ainda_funciona_apos_adicionar_terceira_flag(monkeypatch):
    monkeypatch.setattr(loader, "run_diagnose_cli", lambda: 0)
    assert loader.main(["--diagnose"]) == 0


# ---------------------------------------------------------------------------
# run_diagnose_shopee_window_cli — sem DATAMART_DATABASE_URL, janela inválida
# ---------------------------------------------------------------------------

def test_run_diagnose_shopee_window_cli_sem_datamart_url_aborta(monkeypatch):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "")
    monkeypatch.setattr(loader.settings, "datamart_host", "")
    monkeypatch.setattr(loader.settings, "datamart_db", "")

    rc = loader.run_diagnose_shopee_window_cli(_D_FROM, _D_TO)

    assert rc == 2


def test_run_diagnose_shopee_window_cli_janela_invalida_retorna_2(monkeypatch):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")

    def boom(*a, **k):
        raise AssertionError("nao deveria conectar com janela invalida")
    monkeypatch.setattr(loader.psycopg2, "connect", boom)

    rc = loader.run_diagnose_shopee_window_cli(date(2026, 6, 30), date(2026, 6, 1))

    assert rc == 2


def test_run_diagnose_shopee_window_cli_caminho_feliz_imprime_relatorio(monkeypatch, capsys):
    monkeypatch.setattr(loader.settings, "datamart_database_url", "postgresql://read@host/db")
    fake_conn = _happy_conn()
    monkeypatch.setattr(loader, "psycopg2", FakePsycopg2Module(fake_conn))

    rc = loader.run_diagnose_shopee_window_cli(_D_FROM, _D_TO)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Gold atual" in out
    assert "Recalculado fonte" in out
    assert "rows_to_delete=5" in out
    assert "rows_to_insert=7" in out
