"""
Testes de `pipelines/ingestion/gold_regional/shopee_batch_window.py` — Gate
S5.2 / S5.2.1 (resolução read-only da janela Gold Shopee a partir de
file_ids, com preflight obrigatório na API pública).

Usa conexões/cursores psycopg2 falsos (respostas por SUBSTRING do SQL,
mesmo padrão de test_gold_regional_window_write_validation.py). Nenhum
banco real é tocado. Nenhuma escrita é exercitada em nenhum teste (o
próprio módulo é estruturalmente incapaz de escrever).

Gate S5.2.1: `resolve_shopee_batch_window` (pública) SEMPRE roda
`window_write_conn.run_window_preflight` antes de qualquer consulta.
`_resolve_shopee_batch_window_after_preflight` (privada) contém só a
transação/consultas e assume que o preflight já aprovou — por isso os
testes de comportamento de consulta (achado/ausente/vazio/nulo/janela)
chamam a função PRIVADA diretamente (sem precisar simular preflight), e um
bloco separado testa exclusivamente o gate de preflight da função pública.
"""
from __future__ import annotations

import inspect
import json
from datetime import date

import pytest

from pipelines.ingestion.gold_regional import shopee_batch_window as sbw


# ---------------------------------------------------------------------------
# Fakes (mesmo padrão de test_gold_regional_window_write_validation.py)
# ---------------------------------------------------------------------------

def _contains(*subs):
    return lambda upper: all(s in upper for s in subs)


_D_FROM = date(2026, 6, 15)
_D_TO = date(2026, 6, 21)
_READ_URL = "postgresql://read@host/db"
_WRITE_URL = "postgresql://writer@host/db"


def _happy_responses(found_file_ids=None, row_count=512, null_count=0, date_from=_D_FROM, date_to=_D_TO):
    if found_file_ids is None:
        found_file_ids = [(1,), (2,), (3,)]
    return [
        (_contains("SELECT DISTINCT FILE_ID FROM"), found_file_ids),
        (_contains("NULL_DATE_COUNT"), (row_count, null_count, date_from, date_to)),
    ]


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
        if self.conn.fail_on_substring and self.conn.fail_on_substring in norm.upper():
            raise RuntimeError("falha simulada de execução")

    def _dispatch(self):
        norm, _params = self.conn.executed[-1]
        upper = norm.upper()
        for matcher, value in self.conn.responses:
            if matcher(upper):
                return value
        raise AssertionError(f"nenhuma resposta simulada para a query: {norm!r}")

    def fetchone(self):
        return self._dispatch()

    def fetchall(self):
        return self._dispatch()


class FakeConn:
    def __init__(self, responses=None, fail_on_substring=None):
        self.executed: list[tuple[str, dict]] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.readonly = None
        self.isolation_level = None
        self.autocommit = None
        self.fail_on_substring = fail_on_substring
        self.responses = responses if responses is not None else _happy_responses()

    def cursor(self):
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


def _happy_conn(**overrides):
    return FakeConn(responses=_happy_responses(), **overrides)


class _HappyPreflightReport:
    ok = True
    warnings: list = []
    blocking_reasons: list = []
    safe_summary: dict = {}


class _BlockedPreflightReport:
    ok = False
    warnings: list = []
    blocking_reasons = ["rolsuper=true"]
    safe_summary: dict = {}


# ---------------------------------------------------------------------------
# validate_batch_file_ids — validação pura, sem I/O
# ---------------------------------------------------------------------------

def test_validate_rejeita_lista_vazia():
    with pytest.raises(sbw.BatchWindowInputError, match="nenhum file_id"):
        sbw.validate_batch_file_ids([])


def test_validate_rejeita_duplicados():
    with pytest.raises(sbw.BatchWindowInputError, match="duplicado"):
        sbw.validate_batch_file_ids([1, 2, 2])


def test_validate_rejeita_tipo_invalido():
    with pytest.raises(sbw.BatchWindowInputError, match="inválido"):
        sbw.validate_batch_file_ids([1, "2"])  # type: ignore[list-item]


def test_validate_rejeita_bool_como_int():
    with pytest.raises(sbw.BatchWindowInputError, match="inválido"):
        sbw.validate_batch_file_ids([1, True])  # type: ignore[list-item]


def test_validate_rejeita_nao_positivo():
    with pytest.raises(sbw.BatchWindowInputError, match="faixa válida"):
        sbw.validate_batch_file_ids([0])
    with pytest.raises(sbw.BatchWindowInputError, match="faixa válida"):
        sbw.validate_batch_file_ids([-1])


def test_validate_rejeita_acima_do_limite():
    with pytest.raises(sbw.BatchWindowInputError, match="acima do limite"):
        sbw.validate_batch_file_ids(list(range(1, sbw.MAX_BATCH_FILE_IDS + 2)))


def test_validate_ok_ordena_sem_alterar_valores():
    """Duplicados já são REJEITADOS acima (levantam) — esta entrada não tem
    nenhum, então o único comportamento exercitado aqui é a ordenação."""
    assert sbw.validate_batch_file_ids([3, 1, 2]) == [1, 2, 3]


# ---------------------------------------------------------------------------
# _resolve_shopee_batch_window_after_preflight (PRIVADA) — comportamento de
# consulta, assumindo preflight já aprovado. Nenhum teste aqui precisa
# simular preflight: é exatamente por isso que a lógica de consulta vive
# numa função separada da checagem de preflight.
# ---------------------------------------------------------------------------

def test_after_preflight_um_file_id_resolvido(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(found_file_ids=[(1,)]))
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1])

    assert result.outcome == "resolved"
    assert result.reason_code == sbw.REASON_RESOLVED
    assert result.requested_file_count == 1
    assert result.found_file_count == 1
    assert result.date_from == _D_FROM
    assert result.date_to == _D_TO
    assert result.refresh_window_valid is True
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False


def test_after_preflight_multiplos_file_ids_min_max_conjunto(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "resolved"
    assert result.requested_file_count == 3
    assert result.found_file_count == 3
    assert result.date_from == _D_FROM
    assert result.date_to == _D_TO
    assert result.window_days == (_D_TO - _D_FROM).days + 1


def test_after_preflight_todos_ausentes_bloqueia_sem_calcular_janela(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(found_file_ids=[]))
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "blocked"
    assert result.reason_code == sbw.REASON_MISSING_FILE_IDS
    assert result.missing_file_ids == [1, 2, 3]
    assert result.date_from is None and result.date_to is None
    assert not any("NULL_DATE_COUNT" in s.upper() for s, _ in fake_conn.executed)


def test_after_preflight_lote_parcialmente_presente_bloqueia_sem_calcular_janela(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(found_file_ids=[(1,), (3,)]))
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "blocked"
    assert result.reason_code == sbw.REASON_MISSING_FILE_IDS
    assert result.missing_file_ids == [2]
    assert result.found_file_count == 2
    assert result.date_from is None
    assert not any("NULL_DATE_COUNT" in s.upper() for s, _ in fake_conn.executed)


def test_after_preflight_lote_zero_linhas_bloqueia(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(row_count=0))
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "blocked"
    assert result.reason_code == sbw.REASON_EMPTY_BATCH


def test_after_preflight_data_obrigatoria_nula_bloqueia(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(null_count=2))
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "blocked"
    assert result.reason_code == sbw.REASON_NULL_ORDER_DATE
    assert result.null_order_date_count == 2


# ---------------------------------------------------------------------------
# Janela inválida — reason_code genérico refresh_window_invalid (Gate
# S5.2.1: nunca window_exceeds_limit para TODA causa; data futura não pode
# ser confundida com janela grande demais).
# ---------------------------------------------------------------------------

def test_after_preflight_janela_valida_dentro_do_limite(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "resolved"
    assert result.refresh_window_valid is True


def test_after_preflight_janela_acima_de_180_dias_bloqueia_generico(monkeypatch):
    wide_from = date(2026, 1, 1)
    wide_to = date(2026, 8, 1)  # > 180 dias
    assert (wide_to - wide_from).days + 1 > sbw.gold_regional_loader.MAX_SHOPEE_WINDOW_DAYS
    fake_conn = FakeConn(responses=_happy_responses(date_from=wide_from, date_to=wide_to))
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "blocked"
    assert result.reason_code == sbw.REASON_REFRESH_WINDOW_INVALID
    assert result.refresh_window_valid is False
    assert result.date_from == wide_from
    assert result.date_to == wide_to


def test_after_preflight_data_futura_bloqueia_generico_nao_window_exceeds_limit(monkeypatch):
    """Uma janela com date_to no futuro é rejeitada por
    `loader._validate_shopee_window` por um motivo DIFERENTE de "excede
    180 dias" — o reason_code tem que ser o mesmo genérico
    (`refresh_window_invalid`), nunca um nome que sugira "grande demais"."""
    today = date.today()
    future_to = date(today.year + 1, 1, 1)
    fake_conn = FakeConn(responses=_happy_responses(date_from=today, date_to=future_to))
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "blocked"
    assert result.reason_code == sbw.REASON_REFRESH_WINDOW_INVALID
    assert result.reason_code != "window_exceeds_limit"
    assert any("futuro" in p for p in result.problems)


# ---------------------------------------------------------------------------
# Query parametrizada / só a tabela Silver / nunca Raw-Gold-Neon
# ---------------------------------------------------------------------------

def test_after_preflight_usa_bind_parameters_nunca_interpola_ids(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [123456789, 2])

    for sql, params in fake_conn.executed:
        assert "123456789" not in sql
        assert params is not None and "file_ids" in params


def test_after_preflight_so_consulta_tabela_silver_esperada(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    for sql, _params in fake_conn.executed:
        upper = sql.upper()
        assert "SILVER.STG_SHOPEE_ORDER_ITEM_SNAPSHOTS" in upper
        assert "RAW." not in upper
        assert "GOLD." not in upper
        assert "MARTS." not in upper


def test_regressao_estatica_sql_constants_nunca_referenciam_raw_ou_gold():
    """`raw.shopee_ingestion_file` pode aparecer em PROSA (o docstring do
    módulo explica por que esta tabela nunca é consultada) — o que nunca
    pode aparecer é dentro das constantes SQL de fato executadas."""
    for name in ("SQL_FOUND_FILE_IDS", "SQL_WINDOW_AGGREGATES"):
        sql_text = getattr(sbw, name).upper()
        assert "RAW." not in sql_text, f"{name} referencia schema raw"
        assert "GOLD." not in sql_text, f"{name} referencia schema gold"
        assert "MARTS." not in sql_text, f"{name} referencia schema marts (Neon)"
        assert "SILVER.STG_SHOPEE_ORDER_ITEM_SNAPSHOTS" in sql_text


# ---------------------------------------------------------------------------
# Sessão read-only REPEATABLE READ + rollback/close (função privada)
# ---------------------------------------------------------------------------

def test_after_preflight_sessao_readonly_repeatable_read(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert fake_conn.readonly is True
    assert fake_conn.isolation_level == "REPEATABLE READ"
    assert fake_conn.autocommit is False


def test_after_preflight_rollback_no_sucesso(monkeypatch):
    fake_conn = _happy_conn()
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "resolved"
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False
    assert fake_conn.closed is True


def test_after_preflight_rollback_best_effort_na_falha(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(), fail_on_substring="NULL_DATE_COUNT")
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "failed"
    assert result.reason_code == sbw.REASON_UNEXPECTED_ERROR
    assert fake_conn.rolled_back is True
    assert fake_conn.committed is False
    assert fake_conn.closed is True


def test_after_preflight_close_sempre_mesmo_se_set_session_falhar(monkeypatch):
    class ExplodingSetSessionConn(FakeConn):
        def set_session(self, readonly=None, isolation_level=None, autocommit=None):
            raise RuntimeError("falha simulada ao configurar sessão")

    fake_conn = ExplodingSetSessionConn(responses=_happy_responses())
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "failed"
    assert fake_conn.closed is True


def test_after_preflight_excecao_original_nunca_mascarada_por_falha_de_rollback(monkeypatch):
    fake_conn = FakeConn(responses=_happy_responses(), fail_on_substring="NULL_DATE_COUNT")

    def boom_rollback():
        raise RuntimeError("connection reset postgresql://u:p@h/db")
    fake_conn.rollback = boom_rollback
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "failed"
    assert any("falha simulada de execução" in p or "RuntimeError" in p for p in result.problems)
    assert any("falha ao executar rollback" in w for w in result.warnings)
    combined = " ".join(result.problems + result.warnings)
    assert "u:p@h" not in combined


def test_after_preflight_connect_falha_retorna_failed_sanitizado(monkeypatch):
    def boom_connect(url, connect_timeout=15):
        raise RuntimeError(
            'connection to server at "prod-db.example.rds.amazonaws.com" '
            '(10.0.0.5), port 5432 failed: FATAL: password authentication failed for user "postgres"'
        )

    class BoomModule:
        def connect(self, url, connect_timeout=15):
            return boom_connect(url, connect_timeout)

    monkeypatch.setattr(sbw, "psycopg2", BoomModule())

    result = sbw._resolve_shopee_batch_window_after_preflight(_WRITE_URL, [1, 2, 3])

    assert result.outcome == "failed"
    combined = " ".join(result.problems)
    assert "prod-db.example.rds.amazonaws.com" not in combined
    assert "10.0.0.5" not in combined
    assert "postgres" not in combined


# ---------------------------------------------------------------------------
# resolve_shopee_batch_window (PÚBLICA) — o preflight é OBRIGATÓRIO aqui.
# ---------------------------------------------------------------------------

def test_publica_entrada_invalida_bloqueia_antes_de_qualquer_coisa(monkeypatch):
    def boom_preflight(*a, **k):
        raise AssertionError("não deveria rodar preflight com file_ids inválidos")
    monkeypatch.setattr(sbw.window_write_conn, "run_window_preflight", boom_preflight)

    def boom_connect(*a, **k):
        raise AssertionError("não deveria conectar com file_ids inválidos")
    monkeypatch.setattr(sbw, "psycopg2", type("M", (), {"connect": staticmethod(boom_connect)}))

    result = sbw.resolve_shopee_batch_window(_WRITE_URL, _READ_URL, [1, 1])

    assert result.outcome == "blocked"
    assert result.reason_code == sbw.REASON_INVALID_INPUT


def test_publica_preflight_bloqueado_nao_conecta_nem_consulta(monkeypatch):
    monkeypatch.setattr(sbw.window_write_conn, "run_window_preflight", lambda *a, **k: _BlockedPreflightReport())

    def boom_connect(*a, **k):
        raise AssertionError("não deveria conectar com preflight bloqueado")
    monkeypatch.setattr(sbw, "psycopg2", type("M", (), {"connect": staticmethod(boom_connect)}))

    result = sbw.resolve_shopee_batch_window(_WRITE_URL, _READ_URL, [1, 2, 3])

    assert result.outcome == "blocked"
    assert result.reason_code == sbw.REASON_PREFLIGHT_BLOCKED
    assert any("rolsuper=true" in p for p in result.problems)


def test_publica_preflight_levanta_excecao_bloqueia_com_mensagem_sanitizada(monkeypatch):
    def boom_preflight(*a, **k):
        raise RuntimeError(
            'connection to server at "prod-db.example.rds.amazonaws.com" '
            '(10.0.0.5), port 5432 failed for user "postgres"'
        )
    monkeypatch.setattr(sbw.window_write_conn, "run_window_preflight", boom_preflight)

    def boom_connect(*a, **k):
        raise AssertionError("não deveria conectar quando o preflight levanta")
    monkeypatch.setattr(sbw, "psycopg2", type("M", (), {"connect": staticmethod(boom_connect)}))

    result = sbw.resolve_shopee_batch_window(_WRITE_URL, _READ_URL, [1, 2, 3])

    assert result.outcome == "blocked"
    assert result.reason_code == sbw.REASON_PREFLIGHT_BLOCKED
    combined = " ".join(result.problems)
    assert "prod-db.example.rds.amazonaws.com" not in combined
    assert "10.0.0.5" not in combined
    assert "postgres" not in combined


def test_publica_preflight_aprovado_consulta_de_verdade(monkeypatch):
    monkeypatch.setattr(sbw.window_write_conn, "run_window_preflight", lambda *a, **k: _HappyPreflightReport())
    fake_conn = _happy_conn()
    monkeypatch.setattr(sbw, "psycopg2", FakePsycopg2Module(fake_conn))

    result = sbw.resolve_shopee_batch_window(_WRITE_URL, _READ_URL, [1, 2, 3])

    assert result.outcome == "resolved"
    assert fake_conn.executed  # a consulta de fato rodou
    assert fake_conn.rolled_back is True


def test_publica_nunca_tem_flag_para_pular_preflight():
    """Regressão de contrato: a assinatura pública não pode ganhar nenhum
    parâmetro capaz de desarmar o preflight."""
    sig = inspect.signature(sbw.resolve_shopee_batch_window)
    for forbidden in ("skip_preflight", "preflight_confirmed", "skip_preflight_check"):
        assert forbidden not in sig.parameters


def test_privada_nao_e_reexportada_como_publica():
    assert "_resolve_shopee_batch_window_after_preflight" not in sbw.__all__
    assert "resolve_shopee_batch_window" in sbw.__all__
    # ainda existe (é usada internamente), só não é o contrato público.
    assert hasattr(sbw, "_resolve_shopee_batch_window_after_preflight")


# ---------------------------------------------------------------------------
# CLI — usa só a função pública segura; nenhum caminho consulta antes do
# preflight; JSON único e parseável.
# ---------------------------------------------------------------------------

def test_run_cli_usa_a_funcao_publica_segura(monkeypatch):
    monkeypatch.setattr(sbw.settings, "datamart_database_url", _READ_URL)
    monkeypatch.setattr(
        sbw.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": _WRITE_URL, "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(sbw.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: _WRITE_URL)

    calls = {}

    def fake_resolve(write_url, datamart_read_url, file_ids):
        calls["args"] = (write_url, datamart_read_url, file_ids)
        return sbw.ShopeeBatchWindowResult(outcome="resolved", reason_code=sbw.REASON_RESOLVED, requested_file_count=len(file_ids), found_file_count=len(file_ids))

    monkeypatch.setattr(sbw, "resolve_shopee_batch_window", fake_resolve)

    rc = sbw.run_cli(["1", "2"], as_json=True)

    assert rc == 0
    assert calls["args"] == (_WRITE_URL, _READ_URL, [1, 2])


def test_run_cli_nao_chama_preflight_nem_conecta_por_conta_propria(monkeypatch):
    """A CLI não pode ter nenhum segundo caminho capaz de consultar sem
    preflight — ela nunca deve chamar run_window_preflight/psycopg2.connect
    diretamente; só a função pública faz isso."""
    monkeypatch.setattr(sbw.settings, "datamart_database_url", _READ_URL)
    monkeypatch.setattr(
        sbw.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": _WRITE_URL, "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(sbw.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: _WRITE_URL)

    def boom_preflight(*a, **k):
        raise AssertionError("a CLI nunca deveria chamar run_window_preflight diretamente")
    monkeypatch.setattr(sbw.window_write_conn, "run_window_preflight", boom_preflight)

    def boom_connect(*a, **k):
        raise AssertionError("a CLI nunca deveria conectar diretamente")
    monkeypatch.setattr(sbw, "psycopg2", type("M", (), {"connect": staticmethod(boom_connect)}))

    fake_result = sbw.ShopeeBatchWindowResult(outcome="resolved", reason_code=sbw.REASON_RESOLVED, requested_file_count=1, found_file_count=1)
    monkeypatch.setattr(sbw, "resolve_shopee_batch_window", lambda *a, **k: fake_result)

    rc = sbw.run_cli(["1"], as_json=True)

    assert rc == 0


def test_run_cli_janela_invalida_nao_le_secret_nem_conecta(monkeypatch):
    monkeypatch.setattr(sbw.settings, "datamart_database_url", _READ_URL)

    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret com file_ids inválidos")
    monkeypatch.setattr(sbw.window_write_conn, "load_window_write_secret", boom_secret)

    rc = sbw.run_cli(["1", "1"], as_json=True)

    assert rc == 2


def test_run_cli_formato_invalido_bloqueia_antes_de_tudo(monkeypatch):
    monkeypatch.setattr(sbw.settings, "datamart_database_url", _READ_URL)

    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret com formato inválido")
    monkeypatch.setattr(sbw.window_write_conn, "load_window_write_secret", boom_secret)

    rc = sbw.run_cli(["abc"], as_json=True)

    assert rc == 2


def test_run_cli_sem_datamart_url_nao_le_secret(monkeypatch):
    monkeypatch.setattr(sbw.settings, "datamart_database_url", "")
    monkeypatch.setattr(sbw.settings, "datamart_host", "")
    monkeypatch.setattr(sbw.settings, "datamart_db", "")

    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret sem DATAMART_DATABASE_URL")
    monkeypatch.setattr(sbw.window_write_conn, "load_window_write_secret", boom_secret)

    rc = sbw.run_cli(["1"], as_json=True)

    assert rc == 2


def test_run_cli_json_e_documento_unico_parseavel(monkeypatch, capsys):
    monkeypatch.setattr(sbw.settings, "datamart_database_url", _READ_URL)
    monkeypatch.setattr(
        sbw.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": _WRITE_URL, "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(sbw.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: _WRITE_URL)

    fake_result = sbw.ShopeeBatchWindowResult(
        outcome="resolved", reason_code=sbw.REASON_RESOLVED,
        requested_file_count=1, found_file_count=1, silver_row_count=10,
        date_from=_D_FROM, date_to=_D_TO, window_days=7, refresh_window_valid=True,
    )
    monkeypatch.setattr(sbw, "resolve_shopee_batch_window", lambda *a, **k: fake_result)

    rc = sbw.run_cli(["1"], as_json=True)

    assert rc == 0
    captured = capsys.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["outcome"] == "resolved"
    assert doc["date_from"] == "2026-06-15"
    assert doc["date_to"] == "2026-06-21"


def test_run_cli_json_nunca_contem_pii_ou_infraestrutura(monkeypatch, capsys):
    monkeypatch.setattr(sbw.settings, "datamart_database_url", _READ_URL)
    monkeypatch.setattr(
        sbw.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": "postgresql://writer:s3cr3t@prod-db.internal:5432/datamart", "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(sbw.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: "postgresql://writer:s3cr3t@prod-db.internal:5432/datamart")

    fake_result = sbw.ShopeeBatchWindowResult(
        outcome="blocked", reason_code=sbw.REASON_MISSING_FILE_IDS,
        requested_file_count=2, found_file_count=1, missing_file_ids=[999],
        problems=["1 de 2 file_id(s) ainda não presentes na Silver"],
    )
    monkeypatch.setattr(sbw, "resolve_shopee_batch_window", lambda *a, **k: fake_result)

    rc = sbw.run_cli(["1", "999"], as_json=True)

    assert rc == 3
    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()
    for forbidden in ("order_id", "cpf", "filename", "s3cr3t", "prod-db.internal", "password"):
        assert forbidden not in combined


def test_run_cli_preflight_bloqueado_via_funcao_publica_reporta_exit_2(monkeypatch, capsys):
    monkeypatch.setattr(sbw.settings, "datamart_database_url", _READ_URL)
    monkeypatch.setattr(
        sbw.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": _WRITE_URL, "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(sbw.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: _WRITE_URL)
    monkeypatch.setattr(sbw.window_write_conn, "run_window_preflight", lambda *a, **k: _BlockedPreflightReport())

    rc = sbw.run_cli(["1", "2"], as_json=True)

    assert rc == 2
    captured = capsys.readouterr()
    doc = json.loads(captured.out.strip())
    assert doc["outcome"] == "blocked"
    assert doc["reason_code"] == sbw.REASON_PREFLIGHT_BLOCKED


# ---------------------------------------------------------------------------
# Regressão estática — nenhuma referência a escrita/refresh/restore/sync
# ---------------------------------------------------------------------------

_FORBIDDEN_TOKENS = (
    "conn.commit(",
    "INSERT INTO",
    "DELETE FROM",
    "TRUNCATE",
    "CREATE TABLE",
    "CREATE TEMP",
    "ALTER TABLE",
    "DROP TABLE",
    "execute_shopee_window_refresh",
    "execute_shopee_window_restore",
    "run_sync",
    "sync_region",
)


def test_regressao_estatica_modulo_sem_simbolos_proibidos():
    source = inspect.getsource(sbw)
    for forbidden in _FORBIDDEN_TOKENS:
        assert forbidden not in source, f"símbolo proibido {forbidden!r} encontrado em shopee_batch_window.py"
