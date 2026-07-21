"""
Testes de `pipelines/ops/refresh_shopee_window_if_needed.py` — Gate S5.3
(wrapper operacional único: resolve a janela pelo contrato seguro do S5.2 e,
se `resolved`, chama o refresh autoritativo diretamente). Nenhum banco real
é tocado — a resolução e o refresh são mockados na maioria dos testes (este
módulo não tem SQL próprio, então não há nada de banco para fakear aqui além
do que já é testado em test_gold_regional_shopee_batch_window.py e
test_gold_regional_window_refresh.py). Um teste de integração dedicado
prova os DOIS preflights deixando `resolve_shopee_batch_window` rodar de
verdade (com um psycopg2 falso), só mockando o refresh.
"""
from __future__ import annotations

import inspect
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pipelines.ingestion.gold_regional import loader as gold_regional_loader
from pipelines.ingestion.gold_regional import shopee_batch_window
from pipelines.ingestion.gold_regional import window_write_conn
from pipelines.ops import refresh_shopee_window_if_needed as wrapper

_WRITE_URL = "postgresql://writer@host/db"
_READ_URL = "postgresql://read@host/db"
_D_FROM = date(2026, 6, 15)
_D_TO = date(2026, 6, 21)


# ---------------------------------------------------------------------------
# Builders de resultado fakes (nenhum vem de conexão real)
# ---------------------------------------------------------------------------

def _resolved(**overrides):
    defaults = dict(
        outcome="resolved", reason_code=shopee_batch_window.REASON_RESOLVED,
        requested_file_count=3, found_file_count=3, silver_row_count=512,
        date_from=_D_FROM, date_to=_D_TO, window_days=7, refresh_window_valid=True,
    )
    defaults.update(overrides)
    return shopee_batch_window.ShopeeBatchWindowResult(**defaults)


def _blocked_resolve(reason_code, **overrides):
    defaults = dict(
        outcome="blocked", reason_code=reason_code,
        requested_file_count=3, problems=["motivo de bloqueio simulado"],
    )
    defaults.update(overrides)
    return shopee_batch_window.ShopeeBatchWindowResult(**defaults)


def _failed_resolve(**overrides):
    defaults = dict(
        outcome="failed", reason_code=shopee_batch_window.REASON_UNEXPECTED_ERROR,
        requested_file_count=3, problems=["erro de resolução sanitizado"],
    )
    defaults.update(overrides)
    return shopee_batch_window.ShopeeBatchWindowResult(**defaults)


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


class _InconclusivePreflightReport:
    ok = None
    warnings: list = []
    blocking_reasons: list = []
    safe_summary: dict = {}


def _refresh_result(outcome, **overrides):
    defaults = dict(outcome=outcome)
    defaults.update(overrides)
    return gold_regional_loader.ShopeeWindowRefreshResult(**defaults)


def _counting_preflight(report):
    calls = {"n": 0}

    def _fn(*a, **k):
        calls["n"] += 1
        return report
    _fn.calls = calls
    return _fn


# ---------------------------------------------------------------------------
# Fluxo — resolver blocked/failed nunca chama refresh
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason_code", [
    shopee_batch_window.REASON_MISSING_FILE_IDS,
    shopee_batch_window.REASON_EMPTY_BATCH,
    shopee_batch_window.REASON_NULL_ORDER_DATE,
    shopee_batch_window.REASON_REFRESH_WINDOW_INVALID,
    shopee_batch_window.REASON_PREFLIGHT_BLOCKED,
    shopee_batch_window.REASON_INVALID_INPUT,
])
def test_resolver_blocked_refresh_nunca_chamado(monkeypatch, tmp_path, reason_code):
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: _blocked_resolve(reason_code))

    def boom_refresh(*a, **k):
        raise AssertionError("refresh nunca deveria ser chamado com resolver blocked")
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", boom_refresh)

    def boom_preflight(*a, **k):
        raise AssertionError("segundo preflight nunca deveria rodar com resolver blocked")
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", boom_preflight)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "blocked"
    assert result.reason_code == reason_code


def test_resolver_failed_refresh_nunca_chamado(monkeypatch, tmp_path):
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: _failed_resolve())

    def boom_refresh(*a, **k):
        raise AssertionError("refresh nunca deveria ser chamado com resolver failed")
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", boom_refresh)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "failed"
    assert result.reason_code == shopee_batch_window.REASON_UNEXPECTED_ERROR
    assert "erro de resolução sanitizado" in result.problems


def test_resolver_blocked_preserva_reason_code_missing_file_ids_e_problems(monkeypatch, tmp_path):
    resolved_but_missing = _blocked_resolve(
        shopee_batch_window.REASON_MISSING_FILE_IDS,
        found_file_count=1, missing_file_ids=[2, 3],
        problems=["2 de 3 file_id(s) ainda não presentes na Silver"],
        warnings=["aviso preservado"],
    )
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: resolved_but_missing)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "blocked"
    assert result.reason_code == shopee_batch_window.REASON_MISSING_FILE_IDS
    assert result.missing_file_ids == [2, 3]
    assert result.found_file_count == 1
    assert result.problems == ["2 de 3 file_id(s) ainda não presentes na Silver"]
    assert result.warnings == ["aviso preservado"]


# ---------------------------------------------------------------------------
# Fluxo — resolved exige o segundo preflight
# ---------------------------------------------------------------------------

def test_resolved_dispara_segundo_preflight(monkeypatch, tmp_path):
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: _resolved())
    preflight_fn = _counting_preflight(_HappyPreflightReport())
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", preflight_fn)
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", lambda *a, **k: _refresh_result("no_op"))

    wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert preflight_fn.calls["n"] == 1


def test_segundo_preflight_bloqueado_refresh_nunca_chamado(monkeypatch, tmp_path):
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: _resolved())
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", lambda *a, **k: _BlockedPreflightReport())

    def boom_refresh(*a, **k):
        raise AssertionError("refresh nunca deveria ser chamado com segundo preflight bloqueado")
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", boom_refresh)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "blocked"
    assert result.reason_code == wrapper.REASON_PREFLIGHT_BLOCKED
    assert any("rolsuper=true" in p for p in result.problems)


def test_segundo_preflight_inconclusivo_bloqueia(monkeypatch, tmp_path):
    """report.ok is None (inconclusivo) deve bloquear -- nunca equivaler a aprovado."""
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: _resolved())
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", lambda *a, **k: _InconclusivePreflightReport())

    def boom_refresh(*a, **k):
        raise AssertionError("refresh nunca deveria ser chamado com preflight inconclusivo")
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", boom_refresh)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "blocked"
    assert result.reason_code == wrapper.REASON_PREFLIGHT_BLOCKED


def test_segundo_preflight_levanta_excecao_bloqueia_sanitizado(monkeypatch, tmp_path):
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: _resolved())

    def boom_preflight(*a, **k):
        raise RuntimeError('connection to "prod-db.internal" (10.0.0.9) failed for user "postgres"')
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", boom_preflight)

    def boom_refresh(*a, **k):
        raise AssertionError("refresh nunca deveria ser chamado quando o segundo preflight levanta")
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", boom_refresh)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "blocked"
    assert result.reason_code == wrapper.REASON_PREFLIGHT_BLOCKED
    combined = " ".join(result.problems)
    assert "prod-db.internal" not in combined
    assert "10.0.0.9" not in combined
    assert "postgres" not in combined


# ---------------------------------------------------------------------------
# Fluxo — refresh chamado exatamente uma vez, outcomes mapeados sem reinterpretar
# ---------------------------------------------------------------------------

def _happy_setup(monkeypatch, refresh_result):
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: _resolved())
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", lambda *a, **k: _HappyPreflightReport())
    calls = {"n": 0, "args": None}

    def fake_refresh(*a, **k):
        calls["n"] += 1
        calls["args"] = (a, k)
        return refresh_result
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", fake_refresh)
    return calls


def test_resolved_refresh_no_op(monkeypatch, tmp_path):
    calls = _happy_setup(monkeypatch, _refresh_result("no_op"))

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "no_op"
    assert result.reason_code == wrapper.REASON_NO_OP
    assert result.rows_deleted == 0
    assert result.rows_inserted == 0
    assert result.backup_path is None
    assert calls["n"] == 1


def test_resolved_refresh_committed(monkeypatch, tmp_path):
    committed = _refresh_result(
        "committed", rows_deleted=10, rows_inserted=12,
        backup_path=str(tmp_path / "backup.json"), backup_sha256="deadbeef" * 8,
        gold_gmv_before=Decimal("500.00"), gold_gmv_after=Decimal("620.00"),
    )
    calls = _happy_setup(monkeypatch, committed)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "committed"
    assert result.reason_code == wrapper.REASON_COMMITTED
    assert result.rows_deleted == 10
    assert result.rows_inserted == 12
    assert result.backup_path == str(tmp_path / "backup.json")
    assert result.backup_sha256 == "deadbeef" * 8
    assert result.gmv_before == Decimal("500.00")
    assert result.gmv_after == Decimal("620.00")
    assert calls["n"] == 1
    # date_from/date_to/window_days vieram do resultado RESOLVED, nunca recalculados aqui.
    assert result.date_from == _D_FROM
    assert result.date_to == _D_TO
    assert result.window_days == 7


def test_resolved_refresh_blocked(monkeypatch, tmp_path):
    blocked = _refresh_result("blocked", problems=["advisory lock em uso — outra execução em andamento"])
    calls = _happy_setup(monkeypatch, blocked)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "blocked"
    assert result.reason_code == wrapper.REASON_REFRESH_BLOCKED
    assert "advisory lock em uso — outra execução em andamento" in result.problems
    assert calls["n"] == 1


def test_resolved_refresh_failed_preserva_backup_path_e_hash(monkeypatch, tmp_path):
    failed = _refresh_result(
        "failed", problems=["DELETE removeu 3 linha(s), esperado 4"],
        backup_path=str(tmp_path / "backup.json"), backup_sha256="cafef00d" * 8,
    )
    calls = _happy_setup(monkeypatch, failed)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "failed"
    assert result.reason_code == wrapper.REASON_REFRESH_FAILED
    assert result.backup_path == str(tmp_path / "backup.json")
    assert result.backup_sha256 == "cafef00d" * 8
    assert calls["n"] == 1


def test_refresh_levanta_excecao_inesperada_vira_failed_sanitizado(monkeypatch, tmp_path):
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: _resolved())
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", lambda *a, **k: _HappyPreflightReport())

    def boom_refresh(*a, **k):
        raise RuntimeError('connection to "prod-db.internal" (10.0.0.9) failed for user "postgres"')
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", boom_refresh)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "failed"
    assert result.reason_code == wrapper.REASON_UNEXPECTED_ERROR
    combined = " ".join(result.problems)
    assert "prod-db.internal" not in combined
    assert "10.0.0.9" not in combined
    assert "postgres" not in combined


def test_staging_rows_e_gold_rows_before_sempre_none(monkeypatch, tmp_path):
    """Documentado no módulo: execute_shopee_window_refresh não expõe essas
    contagens, e este wrapper nunca fabrica um valor novo via SQL próprio."""
    _happy_setup(monkeypatch, _refresh_result("committed", rows_deleted=1, rows_inserted=1))

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.staging_rows is None
    assert result.gold_rows_before is None


# ---------------------------------------------------------------------------
# Réplica/primary — write_url e read_url nunca se confundem
# ---------------------------------------------------------------------------

def test_resolve_recebe_write_url_e_read_url_na_ordem_certa(monkeypatch, tmp_path):
    captured = {}

    def fake_resolve(write_url, datamart_read_url, file_ids):
        captured["args"] = (write_url, datamart_read_url, list(file_ids))
        return _resolved()
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", fake_resolve)
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", lambda *a, **k: _HappyPreflightReport())
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", lambda *a, **k: _refresh_result("no_op"))

    wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [3, 1, 2], tmp_path / "backup.json")

    assert captured["args"] == (_WRITE_URL, _READ_URL, [1, 2, 3])


def test_execute_refresh_recebe_o_mesmo_write_url_aprovado(monkeypatch, tmp_path):
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: _resolved())
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", lambda *a, **k: _HappyPreflightReport())
    captured = {}

    def fake_refresh(write_url, date_from, date_to, audit_path, **kwargs):
        captured["write_url"] = write_url
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        return _refresh_result("no_op")
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", fake_refresh)

    wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert captured["write_url"] == _WRITE_URL
    assert captured["date_from"] == _D_FROM
    assert captured["date_to"] == _D_TO


def test_segundo_preflight_recebe_write_url_e_read_url(monkeypatch, tmp_path):
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: _resolved())
    captured = {}

    def fake_preflight(write_url, expected_read_url):
        captured["args"] = (write_url, expected_read_url)
        return _HappyPreflightReport()
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", fake_preflight)
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", lambda *a, **k: _refresh_result("no_op"))

    wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert captured["args"] == (_WRITE_URL, _READ_URL)


# ---------------------------------------------------------------------------
# Integração — resolve_shopee_batch_window roda DE VERDADE (só o refresh é
# mockado) para provar que os DOIS preflights realmente acontecem: um de
# dentro da resolução, outro deste wrapper, imediatamente antes do refresh.
# ---------------------------------------------------------------------------

class _FoundIdsCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append(" ".join(sql.split()))

    def fetchall(self):
        return [(1,), (2,), (3,)]

    def fetchone(self):
        return (512, 0, _D_FROM, _D_TO)


class _FoundIdsConn:
    def __init__(self):
        self.executed: list[str] = []
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return _FoundIdsCursor(self)

    def set_session(self, **kwargs):
        pass

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class _FakePsycopg2ForResolve:
    def connect(self, url, connect_timeout=15):
        return _FoundIdsConn()


def test_integracao_dois_preflights_um_da_resolucao_outro_do_wrapper(monkeypatch, tmp_path):
    monkeypatch.setattr(shopee_batch_window, "psycopg2", _FakePsycopg2ForResolve())
    preflight_fn = _counting_preflight(_HappyPreflightReport())
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", preflight_fn)
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", lambda *a, **k: _refresh_result("no_op"))

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "no_op"
    assert preflight_fn.calls["n"] == 2  # 1 dentro de resolve_shopee_batch_window + 1 deste wrapper


def test_integracao_resolucao_real_bloqueia_sem_preflight_aprovado(monkeypatch, tmp_path):
    """Se o preflight (compartilhado pelas duas chamadas) bloquear, a
    resolução já para ali -- o wrapper nunca chega a rodar seu próprio
    segundo preflight nem o refresh, porque resolve_shopee_batch_window já
    retorna blocked."""
    monkeypatch.setattr(shopee_batch_window, "psycopg2", _FakePsycopg2ForResolve())
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", lambda *a, **k: _BlockedPreflightReport())

    def boom_refresh(*a, **k):
        raise AssertionError("refresh nunca deveria ser chamado")
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", boom_refresh)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "blocked"
    assert result.reason_code == shopee_batch_window.REASON_PREFLIGHT_BLOCKED


# ---------------------------------------------------------------------------
# audit_path — mesmas regras do refresh, reaproveitadas (nunca reimplementadas)
# ---------------------------------------------------------------------------

def test_audit_path_relativo_bloqueia_antes_de_qualquer_coisa(monkeypatch):
    def boom_resolve(*a, **k):
        raise AssertionError("não deveria resolver com audit_path relativo")
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", boom_resolve)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], Path("relativo.json"))

    assert result.outcome == "blocked"
    assert result.reason_code == wrapper.REASON_AUDIT_PATH_INVALID


def test_audit_path_dentro_do_repo_bloqueia(monkeypatch, tmp_path):
    def boom_resolve(*a, **k):
        raise AssertionError("não deveria resolver com audit_path dentro do repo")
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", boom_resolve)

    inside = tmp_path / "backup.json"
    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], inside, repo_root=tmp_path)

    assert result.outcome == "blocked"
    assert result.reason_code == wrapper.REASON_AUDIT_PATH_INVALID


def test_audit_path_extensao_errada_bloqueia(tmp_path):
    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.txt")

    assert result.outcome == "blocked"
    assert result.reason_code == wrapper.REASON_AUDIT_PATH_INVALID


def test_audit_path_ja_existente_bloqueia(tmp_path):
    audit_path = tmp_path / "backup.json"
    audit_path.write_text("{}", encoding="utf-8")

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], audit_path)

    assert result.outcome == "blocked"
    assert result.reason_code == wrapper.REASON_AUDIT_PATH_INVALID


def test_audit_path_sha_existente_bloqueia(tmp_path):
    audit_path = tmp_path / "backup.json"
    Path(str(audit_path) + ".sha256").write_text("deadbeef", encoding="utf-8")

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], audit_path)

    assert result.outcome == "blocked"
    assert result.reason_code == wrapper.REASON_AUDIT_PATH_INVALID


def test_no_op_nao_cria_arquivo(monkeypatch, tmp_path):
    _happy_setup(monkeypatch, _refresh_result("no_op"))
    audit_path = tmp_path / "backup.json"

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], audit_path)

    assert result.outcome == "no_op"
    assert result.backup_path is None
    assert not audit_path.exists()
    assert not Path(str(audit_path) + ".sha256").exists()


# ---------------------------------------------------------------------------
# Segurança — regressão estática
# ---------------------------------------------------------------------------

_FORBIDDEN_TOKENS = (
    "diagnose_shopee_window(",
    "execute_shopee_window_restore",
    "sync_region_if_needed",
    "sync_region_daily",
    "import psycopg2",
    "time.sleep(",
    "while True",
    "conn.commit(",
    "cur.execute(",
    "cursor()",
    "LOCK TABLE",
    "pg_try_advisory_lock",
    "_resolve_shopee_batch_window_after_preflight",
)


def _code_only_source():
    """Concatena só o CORPO das funções do módulo — nunca o docstring do
    módulo, que legitimamente cita (em prosa) os símbolos que o código NUNCA
    deve chamar (mesma armadilha já documentada no Gate S5.2: escanear o
    módulo inteiro produz falso positivo quando o docstring explica o que
    NÃO é feito)."""
    names = (
        "refresh_shopee_window_if_needed", "run_cli", "main",
        "_result_to_dict", "_print_json", "_print_human", "_emit", "_decimal_to_str",
    )
    return "\n".join(inspect.getsource(getattr(wrapper, name)) for name in names)


def test_regressao_estatica_modulo_sem_simbolos_proibidos():
    source = _code_only_source()
    for forbidden in _FORBIDDEN_TOKENS:
        assert forbidden not in source, f"símbolo proibido {forbidden!r} encontrado em refresh_shopee_window_if_needed.py"


def test_regressao_estatica_usa_apenas_a_funcao_publica_do_s52():
    source = _code_only_source()
    assert "shopee_batch_window.resolve_shopee_batch_window(" in source
    assert "_resolve_shopee_batch_window_after_preflight" not in source


def test_modulo_nunca_importa_database_url_neon():
    """`DATAMART_DATABASE_URL` (read replica do Data Mart) é legítimo e
    esperado; o que nunca pode aparecer é o `DATABASE_URL`/`settings.database_url`
    do Neon (marts.*) — a automação externa continua sem essa credencial."""
    source = _code_only_source()
    assert "settings.database_url" not in source
    assert "os.environ[\"DATABASE_URL\"]" not in source
    assert "os.environ['DATABASE_URL']" not in source


# ---------------------------------------------------------------------------
# CLI — reusa a função pública, ordem de validação, JSON único, exit codes
# ---------------------------------------------------------------------------

def test_run_cli_valida_file_ids_antes_de_audit_path_e_secret(monkeypatch):
    def boom_audit(*a, **k):
        raise AssertionError("não deveria validar audit_path com file_ids inválidos")
    monkeypatch.setattr(wrapper.gold_regional_loader, "_validate_new_window_audit_path", boom_audit)

    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret com file_ids inválidos")
    monkeypatch.setattr(wrapper.window_write_conn, "load_window_write_secret", boom_secret)

    rc = wrapper.run_cli(["1", "1"], "/tmp/backup.json", as_json=True)

    assert rc == 2


def test_run_cli_valida_audit_path_antes_de_secret(monkeypatch):
    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret com audit_path inválido")
    monkeypatch.setattr(wrapper.window_write_conn, "load_window_write_secret", boom_secret)

    rc = wrapper.run_cli(["1"], "relativo.json", as_json=True)

    assert rc == 2


def test_run_cli_sem_datamart_url_nao_le_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(wrapper.settings, "datamart_database_url", "")
    monkeypatch.setattr(wrapper.settings, "datamart_host", "")
    monkeypatch.setattr(wrapper.settings, "datamart_db", "")

    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret sem DATAMART_DATABASE_URL")
    monkeypatch.setattr(wrapper.window_write_conn, "load_window_write_secret", boom_secret)

    rc = wrapper.run_cli(["1"], str(tmp_path / "backup.json"), as_json=True)

    assert rc == 2


def test_run_cli_usa_a_funcao_publica_e_repassa_resultado(monkeypatch, tmp_path):
    monkeypatch.setattr(wrapper.settings, "datamart_database_url", _READ_URL)
    monkeypatch.setattr(
        wrapper.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": _WRITE_URL, "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(wrapper.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: _WRITE_URL)

    calls = {}

    def fake_wrapper_fn(write_url, datamart_read_url, file_ids, audit_path, **kwargs):
        calls["args"] = (write_url, datamart_read_url, file_ids, audit_path)
        return wrapper.ShopeeWindowRefreshIfNeededResult(outcome="no_op", reason_code=wrapper.REASON_NO_OP)
    monkeypatch.setattr(wrapper, "refresh_shopee_window_if_needed", fake_wrapper_fn)

    audit_path = tmp_path / "backup.json"
    rc = wrapper.run_cli(["1", "2"], str(audit_path), as_json=True)

    assert rc == 0
    assert calls["args"] == (_WRITE_URL, _READ_URL, [1, 2], audit_path)


def _cli_setup(monkeypatch, result):
    monkeypatch.setattr(wrapper.settings, "datamart_database_url", _READ_URL)
    monkeypatch.setattr(
        wrapper.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": _WRITE_URL, "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(wrapper.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: _WRITE_URL)
    monkeypatch.setattr(wrapper, "refresh_shopee_window_if_needed", lambda *a, **k: result)


def test_run_cli_committed_exit_0(monkeypatch, tmp_path):
    _cli_setup(monkeypatch, wrapper.ShopeeWindowRefreshIfNeededResult(outcome="committed", reason_code=wrapper.REASON_COMMITTED, rows_deleted=1, rows_inserted=1))
    rc = wrapper.run_cli(["1"], str(tmp_path / "backup.json"), as_json=True)
    assert rc == 0


def test_run_cli_no_op_exit_0(monkeypatch, tmp_path):
    _cli_setup(monkeypatch, wrapper.ShopeeWindowRefreshIfNeededResult(outcome="no_op", reason_code=wrapper.REASON_NO_OP))
    rc = wrapper.run_cli(["1"], str(tmp_path / "backup.json"), as_json=True)
    assert rc == 0


def test_run_cli_refresh_blocked_exit_3(monkeypatch, tmp_path):
    _cli_setup(monkeypatch, wrapper.ShopeeWindowRefreshIfNeededResult(outcome="blocked", reason_code=wrapper.REASON_REFRESH_BLOCKED, problems=["lock em uso"]))
    rc = wrapper.run_cli(["1"], str(tmp_path / "backup.json"), as_json=True)
    assert rc == 3


def test_run_cli_missing_file_ids_exit_3(monkeypatch, tmp_path):
    _cli_setup(monkeypatch, wrapper.ShopeeWindowRefreshIfNeededResult(outcome="blocked", reason_code=wrapper.REASON_MISSING_FILE_IDS, missing_file_ids=[9]))
    rc = wrapper.run_cli(["1"], str(tmp_path / "backup.json"), as_json=True)
    assert rc == 3


def test_run_cli_refresh_failed_exit_4(monkeypatch, tmp_path):
    _cli_setup(monkeypatch, wrapper.ShopeeWindowRefreshIfNeededResult(outcome="failed", reason_code=wrapper.REASON_REFRESH_FAILED, problems=["rollback completo"]))
    rc = wrapper.run_cli(["1"], str(tmp_path / "backup.json"), as_json=True)
    assert rc == 4


def test_run_cli_preflight_bloqueado_exit_2(monkeypatch, tmp_path):
    _cli_setup(monkeypatch, wrapper.ShopeeWindowRefreshIfNeededResult(outcome="blocked", reason_code=wrapper.REASON_PREFLIGHT_BLOCKED, problems=["rolsuper=true"]))
    rc = wrapper.run_cli(["1"], str(tmp_path / "backup.json"), as_json=True)
    assert rc == 2


def test_run_cli_json_e_documento_unico_parseavel(monkeypatch, tmp_path, capsys):
    _cli_setup(monkeypatch, wrapper.ShopeeWindowRefreshIfNeededResult(
        outcome="committed", reason_code=wrapper.REASON_COMMITTED,
        requested_file_count=2, found_file_count=2, silver_row_count=100,
        date_from=_D_FROM, date_to=_D_TO, window_days=7,
        rows_deleted=3, rows_inserted=4,
        gmv_before=Decimal("10.50"), gmv_after=Decimal("20.75"),
        backup_path=str(tmp_path / "backup.json"), backup_sha256="ab" * 32,
    ))

    rc = wrapper.run_cli(["1", "2"], str(tmp_path / "backup.json"), as_json=True)

    assert rc == 0
    captured = capsys.readouterr()
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["outcome"] == "committed"
    assert doc["date_from"] == "2026-06-15"
    assert doc["date_to"] == "2026-06-21"
    assert doc["gmv_before"] == "10.50"
    assert doc["gmv_after"] == "20.75"
    assert doc["staging_rows"] is None
    assert doc["gold_rows_before"] is None


def test_run_cli_json_nunca_contem_pii_ou_infraestrutura(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(wrapper.settings, "datamart_database_url", _READ_URL)
    monkeypatch.setattr(
        wrapper.window_write_conn, "load_window_write_secret",
        lambda *a, **k: {"DATAMART_GOLD_WINDOW_WRITE_URL": "postgresql://writer:s3cr3t@prod-db.internal:5432/datamart", "I_UNDERSTAND_THIS_DELETES_GOLD_SHOPEE_WINDOW": "1"},
    )
    monkeypatch.setattr(wrapper.window_write_conn, "validate_window_write_guardrails", lambda secret, read_url: "postgresql://writer:s3cr3t@prod-db.internal:5432/datamart")
    monkeypatch.setattr(wrapper, "refresh_shopee_window_if_needed", lambda *a, **k: wrapper.ShopeeWindowRefreshIfNeededResult(
        outcome="blocked", reason_code=wrapper.REASON_MISSING_FILE_IDS,
        requested_file_count=2, missing_file_ids=[999],
        problems=["1 de 2 file_id(s) ainda não presentes na Silver"],
    ))

    rc = wrapper.run_cli(["1", "999"], str(tmp_path / "backup.json"), as_json=True)

    assert rc == 3
    captured = capsys.readouterr()
    combined = (captured.out + captured.err).lower()
    for forbidden in ("order_id", "cpf", "filename", "s3cr3t", "prod-db.internal", "password"):
        assert forbidden not in combined


def test_run_cli_stderr_so_recebe_avisos_e_preflight(monkeypatch, tmp_path, capsys):
    _cli_setup(monkeypatch, wrapper.ShopeeWindowRefreshIfNeededResult(
        outcome="blocked", reason_code=wrapper.REASON_PREFLIGHT_BLOCKED,
        problems=["preflight bloqueado — refresh NÃO executado."],
        warnings=["aviso sanitizado de exemplo"],
    ))

    rc = wrapper.run_cli(["1"], str(tmp_path / "backup.json"), as_json=True)

    assert rc == 2
    captured = capsys.readouterr()
    assert "AVISO: aviso sanitizado de exemplo" in captured.err
    assert "PREFLIGHT: preflight bloqueado" in captured.err
    # stdout permanece só o JSON
    lines = [l for l in captured.out.splitlines() if l.strip()]
    assert len(lines) == 1
    json.loads(lines[0])


# ---------------------------------------------------------------------------
# Gate S5.3.1 (Correção 1) — barreira na API pública para exceção do resolver
# ---------------------------------------------------------------------------

def test_resolver_levanta_excecao_inesperada_vira_failed_sanitizado_na_api_publica(monkeypatch, tmp_path):
    """Chamada DIRETA da API pública -- não via run_cli -- para provar que a
    barreira está dentro de refresh_shopee_window_if_needed, não só na CLI."""
    def boom_resolve(*a, **k):
        raise RuntimeError('connection to "prod-db.internal" (10.0.0.9) failed for user "postgres"')
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", boom_resolve)

    def boom_preflight(*a, **k):
        raise AssertionError("segundo preflight nunca deveria rodar se o resolver levantar")
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", boom_preflight)

    def boom_refresh(*a, **k):
        raise AssertionError("refresh nunca deveria ser chamado se o resolver levantar")
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", boom_refresh)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "failed"
    assert result.reason_code == wrapper.REASON_UNEXPECTED_ERROR
    combined = " ".join(result.problems)
    assert "prod-db.internal" not in combined
    assert "10.0.0.9" not in combined
    assert "postgres" not in combined


# ---------------------------------------------------------------------------
# Gate S5.3.1 (Correção 2) — contrato de "resolved" validado antes do refresh
# ---------------------------------------------------------------------------

def _boom_second_preflight_and_refresh(monkeypatch):
    def boom_preflight(*a, **k):
        raise AssertionError("segundo preflight nunca deveria rodar com contrato resolved inválido")
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", boom_preflight)

    def boom_refresh(*a, **k):
        raise AssertionError("refresh nunca deveria ser chamado com contrato resolved inválido")
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", boom_refresh)


@pytest.mark.parametrize("overrides", [
    dict(date_from=None),
    dict(date_to=None),
    dict(refresh_window_valid=False),
    dict(missing_file_ids=[2]),
    dict(requested_file_count=3, found_file_count=2),
    dict(window_days=0),
    dict(window_days=None),
    dict(reason_code="algo_diferente"),
], ids=[
    "sem_date_from", "sem_date_to", "refresh_window_valid_false", "missing_file_ids_presente",
    "contagens_divergentes", "window_days_zero", "window_days_none", "reason_code_inconsistente",
])
def test_resolved_com_contrato_invalido_bloqueia_sem_segundo_preflight_nem_refresh(monkeypatch, tmp_path, overrides):
    invalid = _resolved(**overrides)
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: invalid)
    _boom_second_preflight_and_refresh(monkeypatch)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "failed"
    assert result.reason_code == wrapper.REASON_RESOLVER_CONTRACT_INVALID
    assert result.problems  # sempre relata o que está inconsistente


def test_resolved_com_date_from_maior_que_date_to_bloqueia_como_contrato_invalido(monkeypatch, tmp_path):
    invalid = _resolved(date_from=_D_TO, date_to=_D_FROM)
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: invalid)
    _boom_second_preflight_and_refresh(monkeypatch)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "failed"
    assert result.reason_code == wrapper.REASON_RESOLVER_CONTRACT_INVALID


def test_resolved_contrato_valido_nao_bloqueia(monkeypatch, tmp_path):
    """Contraprova: o _resolved() default (usado em todo o resto da suíte)
    satisfaz o contrato e não é afetado por esta correção."""
    _happy_setup(monkeypatch, _refresh_result("no_op"))

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "no_op"
    assert result.reason_code != wrapper.REASON_RESOLVER_CONTRACT_INVALID


# ---------------------------------------------------------------------------
# Gate S5.3.1 (Correção 3) — warnings agregados das três etapas, sem duplicar
# ---------------------------------------------------------------------------

class _PreflightReportWithWarnings:
    def __init__(self, ok, warnings, blocking_reasons=None):
        self.ok = ok
        self.warnings = warnings
        self.blocking_reasons = blocking_reasons or []
        self.safe_summary = {}


def test_no_op_agrega_warnings_das_tres_etapas(monkeypatch, tmp_path):
    resolved = _resolved(warnings=["aviso do resolver"])
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: resolved)
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", lambda *a, **k: _PreflightReportWithWarnings(True, ["aviso do preflight"]))
    refresh_result = _refresh_result("no_op", warnings=["aviso do refresh"])
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", lambda *a, **k: refresh_result)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "no_op"
    assert result.warnings == ["aviso do resolver", "aviso do preflight", "aviso do refresh"]


def test_committed_agrega_warnings_das_tres_etapas(monkeypatch, tmp_path):
    resolved = _resolved(warnings=["aviso do resolver"])
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: resolved)
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", lambda *a, **k: _PreflightReportWithWarnings(True, ["aviso do preflight"]))
    refresh_result = _refresh_result("committed", rows_deleted=1, rows_inserted=1, warnings=["aviso do refresh"])
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", lambda *a, **k: refresh_result)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "committed"
    assert result.warnings == ["aviso do resolver", "aviso do preflight", "aviso do refresh"]


def test_segundo_preflight_bloqueado_preserva_warnings_do_resolver_e_do_report(monkeypatch, tmp_path):
    resolved = _resolved(warnings=["aviso do resolver"])
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: resolved)
    monkeypatch.setattr(
        wrapper.window_write_conn, "run_window_preflight",
        lambda *a, **k: _PreflightReportWithWarnings(False, ["aviso do preflight"], blocking_reasons=["rolsuper=true"]),
    )

    def boom_refresh(*a, **k):
        raise AssertionError("refresh nunca deveria ser chamado com segundo preflight bloqueado")
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", boom_refresh)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "blocked"
    assert result.reason_code == wrapper.REASON_PREFLIGHT_BLOCKED
    assert result.warnings == ["aviso do resolver", "aviso do preflight"]


def test_segundo_preflight_levanta_excecao_preserva_warnings_do_resolver(monkeypatch, tmp_path):
    resolved = _resolved(warnings=["aviso do resolver"])
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: resolved)

    def boom_preflight(*a, **k):
        raise RuntimeError("falha simulada")
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", boom_preflight)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.outcome == "blocked"
    assert result.reason_code == wrapper.REASON_PREFLIGHT_BLOCKED
    assert result.warnings == ["aviso do resolver"]


def test_resolver_blocked_preserva_seus_proprios_warnings(monkeypatch, tmp_path):
    blocked = _blocked_resolve(shopee_batch_window.REASON_MISSING_FILE_IDS, warnings=["aviso do resolver bloqueado"])
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: blocked)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.warnings == ["aviso do resolver bloqueado"]


def test_warnings_duplicados_nao_se_repetem(monkeypatch, tmp_path):
    resolved = _resolved(warnings=["aviso repetido"])
    monkeypatch.setattr(wrapper.shopee_batch_window, "resolve_shopee_batch_window", lambda *a, **k: resolved)
    monkeypatch.setattr(wrapper.window_write_conn, "run_window_preflight", lambda *a, **k: _PreflightReportWithWarnings(True, ["aviso repetido"]))
    refresh_result = _refresh_result("no_op", warnings=["aviso repetido"])
    monkeypatch.setattr(wrapper.gold_regional_loader, "execute_shopee_window_refresh", lambda *a, **k: refresh_result)

    result = wrapper.refresh_shopee_window_if_needed(_WRITE_URL, _READ_URL, [1, 2, 3], tmp_path / "backup.json")

    assert result.warnings == ["aviso repetido"]


# ---------------------------------------------------------------------------
# Gate S5.3.1 (Correção 4) — nenhuma função privada do S5.2 é importada
# ---------------------------------------------------------------------------

def test_regressao_estatica_nao_chama_parser_privado_do_s52():
    """Verifica o padrão de CHAMADA (nome imediatamente seguido de `(`), não
    apenas a presença da string -- o docstring do módulo legitimamente cita
    `shopee_batch_window._parse_cli_file_ids` em prosa, explicando que essa
    função privada NUNCA é chamada. `run_cli` agora usa um parser LOCAL
    (`_parse_cli_file_ids`, sem o prefixo de módulo) que delega a validação
    de fato para a função pública `validate_batch_file_ids`."""
    source = inspect.getsource(wrapper)
    assert "shopee_batch_window._parse_cli_file_ids(" not in source
    assert "_resolve_shopee_batch_window_after_preflight(" not in source


def test_run_cli_usa_o_parser_local_e_a_validacao_publica(monkeypatch):
    """`_parse_cli_file_ids` local do wrapper delega para a função pública
    `validate_batch_file_ids` -- confirmado indiretamente: duplicados (regra
    de validate_batch_file_ids, não do parsing textual) continuam
    bloqueando antes de qualquer secret/conexão."""
    def boom_secret(*a, **k):
        raise AssertionError("não deveria ler o secret com duplicados")
    monkeypatch.setattr(wrapper.window_write_conn, "load_window_write_secret", boom_secret)

    rc = wrapper.run_cli(["1", "1"], "/tmp/x.json", as_json=True)

    assert rc == 2


def test_parser_local_converte_string_e_aceita_lista_valida():
    assert wrapper._parse_cli_file_ids(["3", "1", "2"]) == [1, 2, 3]


def test_parser_local_rejeita_nao_inteiro():
    with pytest.raises(shopee_batch_window.BatchWindowInputError, match="inválido"):
        wrapper._parse_cli_file_ids(["abc"])
