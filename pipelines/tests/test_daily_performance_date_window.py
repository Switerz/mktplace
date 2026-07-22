"""
Testes focais do Gate R4 Task 2 (Projeto R) — janela exata (--date-from/
--date-to) em pipelines/ingestion/daily_performance.py.

Nenhum teste aqui abre conexão real de banco: `local_session` é
monkeypatchado para uma sessão fake em todos os testes (fixture
`_no_real_db`), e os fetch() dos conectores são monkeypatchados
individualmente onde precisam ser observados.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta

import pytest

from pipelines.ingestion import daily_performance


class _FakeResult:
    def scalar_one(self):
        return 1


class _FakeSession:
    def execute(self, *a, **kw):
        return _FakeResult()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@contextmanager
def _fake_local_session():
    yield _FakeSession()


@pytest.fixture(autouse=True)
def _no_real_db(monkeypatch):
    """Garante que nenhum teste deste arquivo abre conexão real de banco."""
    monkeypatch.setattr(daily_performance, "local_session", _fake_local_session)


# ---------------------------------------------------------------------------
# _resolve_date_window: validação pura, sem I/O nenhum
# ---------------------------------------------------------------------------
def test_resolve_date_window_ambas_ausentes_retorna_none():
    assert daily_performance._resolve_date_window("tiktok", "backfill", None, None) is None


def test_resolve_date_window_janela_valida_tiktok():
    result = daily_performance._resolve_date_window("tiktok", "backfill", "2026-01-01", "2026-05-31")
    assert result == (date(2026, 1, 1), date(2026, 5, 31))


def test_resolve_date_window_janela_valida_shopee_stats():
    result = daily_performance._resolve_date_window("shopee-stats", "backfill", "2026-01-01", "2026-05-31")
    assert result == (date(2026, 1, 1), date(2026, 5, 31))


def test_resolve_date_window_janela_valida_ml():
    result = daily_performance._resolve_date_window("ml", "backfill", "2026-01-01", "2026-05-31")
    assert result == (date(2026, 1, 1), date(2026, 5, 31))


def test_resolve_date_window_apenas_date_from_bloqueia():
    with pytest.raises(ValueError):
        daily_performance._resolve_date_window("tiktok", "backfill", "2026-01-01", None)


def test_resolve_date_window_apenas_date_to_bloqueia():
    with pytest.raises(ValueError):
        daily_performance._resolve_date_window("tiktok", "backfill", None, "2026-05-31")


def test_resolve_date_window_date_from_maior_que_date_to_bloqueia():
    with pytest.raises(ValueError):
        daily_performance._resolve_date_window("tiktok", "backfill", "2026-05-31", "2026-01-01")


def test_resolve_date_window_date_to_futura_bloqueia():
    futura = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        daily_performance._resolve_date_window("tiktok", "backfill", "2026-01-01", futura)


def test_resolve_date_window_modo_incremental_bloqueia():
    with pytest.raises(ValueError):
        daily_performance._resolve_date_window("tiktok", "incremental", "2026-01-01", "2026-05-31")


def test_resolve_date_window_source_nao_autorizada_bloqueia():
    with pytest.raises(ValueError):
        daily_performance._resolve_date_window("shopee-ads", "backfill", "2026-01-01", "2026-05-31")
    with pytest.raises(ValueError):
        daily_performance._resolve_date_window("shopee", "backfill", "2026-01-01", "2026-05-31")


# ---------------------------------------------------------------------------
# run(): combinação inválida bloqueia ANTES de abrir sessão (prova de I/O zero)
# ---------------------------------------------------------------------------
def test_run_bloqueia_antes_de_abrir_sessao_apenas_uma_data(monkeypatch):
    session_opened = {"called": False}

    @contextmanager
    def _tracking_local_session():
        session_opened["called"] = True
        yield _FakeSession()

    monkeypatch.setattr(daily_performance, "local_session", _tracking_local_session)

    with pytest.raises(ValueError):
        daily_performance.run(source="tiktok", mode="backfill", date_from="2026-01-01", date_to=None)

    assert session_opened["called"] is False


def test_run_bloqueia_antes_de_abrir_sessao_modo_incremental_com_datas(monkeypatch):
    session_opened = {"called": False}

    @contextmanager
    def _tracking_local_session():
        session_opened["called"] = True
        yield _FakeSession()

    monkeypatch.setattr(daily_performance, "local_session", _tracking_local_session)

    with pytest.raises(ValueError):
        daily_performance.run(
            source="tiktok", mode="incremental", date_from="2026-01-01", date_to="2026-05-31"
        )

    assert session_opened["called"] is False


def test_run_bloqueia_antes_de_abrir_sessao_source_nao_autorizada(monkeypatch):
    session_opened = {"called": False}

    @contextmanager
    def _tracking_local_session():
        session_opened["called"] = True
        yield _FakeSession()

    monkeypatch.setattr(daily_performance, "local_session", _tracking_local_session)

    with pytest.raises(ValueError):
        daily_performance.run(
            source="shopee-ads", mode="backfill", date_from="2026-01-01", date_to="2026-05-31"
        )

    assert session_opened["called"] is False


def test_run_bloqueia_antes_de_abrir_sessao_date_from_maior_que_date_to(monkeypatch):
    session_opened = {"called": False}

    @contextmanager
    def _tracking_local_session():
        session_opened["called"] = True
        yield _FakeSession()

    monkeypatch.setattr(daily_performance, "local_session", _tracking_local_session)

    with pytest.raises(ValueError):
        daily_performance.run(
            source="tiktok", mode="backfill", date_from="2026-05-31", date_to="2026-01-01"
        )

    assert session_opened["called"] is False


def test_run_bloqueia_antes_de_abrir_sessao_date_to_futura(monkeypatch):
    session_opened = {"called": False}

    @contextmanager
    def _tracking_local_session():
        session_opened["called"] = True
        yield _FakeSession()

    monkeypatch.setattr(daily_performance, "local_session", _tracking_local_session)

    futura = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        daily_performance.run(source="tiktok", mode="backfill", date_from="2026-01-01", date_to=futura)

    assert session_opened["called"] is False


# ---------------------------------------------------------------------------
# run(): fontes aprovadas recebem exatamente a janela solicitada
# ---------------------------------------------------------------------------
def test_run_tiktok_recebe_janela_exata(monkeypatch):
    captured = {}

    def fake_fetch(date_from, date_to):
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        return []

    monkeypatch.setattr(daily_performance.tiktok_connector, "fetch", fake_fetch)

    daily_performance.run(source="tiktok", mode="backfill", date_from="2026-01-01", date_to="2026-05-31")

    assert captured["date_from"] == date(2026, 1, 1)
    assert captured["date_to"] == date(2026, 5, 31)


def test_run_shopee_stats_recebe_janela_exata(monkeypatch):
    captured = {}

    def fake_fetch(date_from, date_to):
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        return []

    monkeypatch.setattr(daily_performance.shopee_connector, "fetch_shop_stats", fake_fetch)

    daily_performance.run(
        source="shopee-stats", mode="backfill", date_from="2026-01-01", date_to="2026-05-31"
    )

    assert captured["date_from"] == date(2026, 1, 1)
    assert captured["date_to"] == date(2026, 5, 31)


def test_run_ml_aceita_janela_exata(monkeypatch):
    captured = {}

    def fake_fetch(date_from, date_to):
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        return []

    monkeypatch.setattr(daily_performance.ml_connector, "fetch", fake_fetch)

    daily_performance.run(source="ml", mode="backfill", date_from="2026-01-01", date_to="2026-05-31")

    assert captured["date_from"] == date(2026, 1, 1)
    assert captured["date_to"] == date(2026, 5, 31)


# ---------------------------------------------------------------------------
# Caminhos existentes (--days / incremental) continuam 100% funcionais
# ---------------------------------------------------------------------------
def test_run_backfill_por_days_continua_funcionando(monkeypatch):
    captured = {}

    def fake_fetch_backfill(days_back):
        captured["days_back"] = days_back
        return []

    monkeypatch.setattr(daily_performance.tiktok_connector, "fetch_backfill", fake_fetch_backfill)

    daily_performance.run(source="tiktok", mode="backfill", days_back=45)

    assert captured["days_back"] == 45


def test_run_incremental_continua_funcionando(monkeypatch):
    captured = {"called": False}

    def fake_fetch_incremental():
        captured["called"] = True
        return []

    monkeypatch.setattr(daily_performance.tiktok_connector, "fetch_incremental", fake_fetch_incremental)

    daily_performance.run(source="tiktok", mode="incremental")

    assert captured["called"] is True
