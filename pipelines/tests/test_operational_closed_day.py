"""Gate DQ-D1 — teto do dia fechado (D-1) em todos os caminhos do daily.

Nenhum teste acessa banco. O instante e' sempre injetado: nada aqui depende do
relogio nem do fuso da maquina que roda a suite.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from pipelines.common import operational_calendar as cal
from pipelines.connectors.mercadolivre import connector as ml
from pipelines.connectors.shopee import connector as sh
from pipelines.connectors.tiktok import connector as tk
from pipelines.ingestion import daily_performance as dp

UTC = timezone.utc


# ---------------------------------------------------------------------------
# 1. Fronteira real de fuso
# ---------------------------------------------------------------------------
def test_instante_ja_no_dia_seguinte_em_utc_mas_ainda_no_anterior_em_sao_paulo():
    """02:00 UTC de 29/08 e' 23:00 BRT de 28/08. O dia operacional e' 28,
    e o ultimo fechado e' 27 — nao 28."""
    agora = datetime(2026, 8, 29, 2, 0, tzinfo=UTC)
    assert cal.operational_today(agora) == date(2026, 8, 28)
    assert cal.last_closed_date(agora) == date(2026, 8, 27)


def test_meia_noite_e_um_em_sao_paulo_vira_o_dia():
    agora = datetime(2026, 8, 29, 3, 1, tzinfo=UTC)   # 00:01 BRT de 29/08
    assert cal.operational_today(agora) == date(2026, 8, 29)
    assert cal.last_closed_date(agora) == date(2026, 8, 28)


def test_instante_naive_e_recusado():
    """Um naive datetime seria lido no fuso do processo — exatamente o defeito
    que este modulo existe para evitar."""
    with pytest.raises(ValueError, match="sem timezone"):
        cal.operational_today(datetime(2026, 8, 28, 12, 0))


def test_cutoff_nao_depende_do_fuso_do_processo():
    """O mesmo instante, expresso em tres offsets, produz o mesmo D-1."""
    base = datetime(2026, 8, 29, 2, 0, tzinfo=UTC)
    equivalentes = [base,
                    base.astimezone(timezone(timedelta(hours=-3))),
                    base.astimezone(timezone(timedelta(hours=+9)))]
    assert {cal.last_closed_date(a) for a in equivalentes} == {date(2026, 8, 27)}


# ---------------------------------------------------------------------------
# 2. Janela explicita
# ---------------------------------------------------------------------------
def test_janela_explicita_ate_d1_e_aceita(monkeypatch):
    monkeypatch.setattr(dp, "assert_closed_day",
                        lambda limite, **kw: cal.assert_closed_day(
                            limite, agora=datetime(2026, 8, 28, 15, 0, tzinfo=UTC), **kw))
    j = dp._resolve_date_window("tiktok", "backfill", "2026-08-01", "2026-08-27")
    assert j == (date(2026, 8, 1), date(2026, 8, 27))


def test_janela_explicita_ate_d0_reprova(monkeypatch):
    monkeypatch.setattr(dp, "assert_closed_day",
                        lambda limite, **kw: cal.assert_closed_day(
                            limite, agora=datetime(2026, 8, 28, 15, 0, tzinfo=UTC), **kw))
    with pytest.raises(ValueError, match="ultimo dia fechado"):
        dp._resolve_date_window("tiktok", "backfill", "2026-08-01", "2026-08-28")


def test_janela_futura_reprova(monkeypatch):
    monkeypatch.setattr(dp, "assert_closed_day",
                        lambda limite, **kw: cal.assert_closed_day(
                            limite, agora=datetime(2026, 8, 28, 15, 0, tzinfo=UTC), **kw))
    with pytest.raises(ValueError, match="ultimo dia fechado"):
        dp._resolve_date_window("ml", "backfill", "2026-08-01", "2026-09-15")


def test_janela_d0_reprova_antes_de_qualquer_io(monkeypatch):
    """Nenhuma sessao, audit ou fetch pode ser tocado quando a janela reprova."""
    tocou = []
    monkeypatch.setattr(dp, "local_session",
                        lambda *a, **k: tocou.append("session"))
    monkeypatch.setattr(dp, "_start_sync_run",
                        lambda *a, **k: tocou.append("audit"))
    monkeypatch.setattr(tk, "fetch", lambda *a, **k: tocou.append("fetch"))
    monkeypatch.setattr(cal, "last_closed_date",
                        lambda agora=None: date(2026, 8, 27))
    monkeypatch.setattr(dp, "last_closed_date",
                        lambda agora=None: date(2026, 8, 27))
    with pytest.raises(ValueError):
        dp.run(source="tiktok", mode="backfill",
               date_from="2026-08-01", date_to="2026-08-28")
    assert tocou == [], f"I/O tocado antes da validacao: {tocou}"


# ---------------------------------------------------------------------------
# 3. Incrementais e backfills implicitos terminam em D-1
# ---------------------------------------------------------------------------
FECHADO = date(2026, 8, 27)


@pytest.fixture
def congela(monkeypatch):
    monkeypatch.setattr(cal, "last_closed_date", lambda agora=None: FECHADO)
    return FECHADO


def _captura(monkeypatch, mod, nome):
    vistos = {}

    def fake(date_from, date_to, *a, **k):
        vistos["janela"] = (date_from, date_to)
        return []

    monkeypatch.setattr(mod, nome, fake)
    return vistos


@pytest.mark.parametrize("mod,fn,alvo,dias", [
    (tk, "fetch_incremental", "fetch", 10),
    (tk, "fetch_backfill", "fetch", 90),
    (ml, "fetch_incremental", "fetch", 3),
    (ml, "fetch_backfill", "fetch", 90),
    (sh, "fetch_incremental", "fetch", 3),
    (sh, "fetch_backfill", "fetch", 150),
    (sh, "fetch_shop_stats_incremental", "fetch_shop_stats", 3),
    (sh, "fetch_shop_stats_backfill", "fetch_shop_stats", 150),
    (sh, "fetch_ads_incremental", "fetch_ads", 3),
    (sh, "fetch_ads_backfill", "fetch_ads", 150),
])
def test_todo_caminho_termina_em_d1(monkeypatch, congela, mod, fn, alvo, dias):
    vistos = _captura(monkeypatch, mod, alvo)
    getattr(mod, fn)()
    inicio, fim = vistos["janela"]
    assert fim == FECHADO, f"{mod.__name__}.{fn} terminou em {fim}, esperado D-1"
    assert fim < date(2026, 8, 28), "D0 alcancado"
    assert (fim - inicio).days == dias, "largura inclusiva alterada"


def test_largura_do_lookback_preservada(monkeypatch, congela):
    """O teto desceu um dia; a largura inclusiva e' a mesma de antes."""
    vistos = _captura(monkeypatch, tk, "fetch")
    tk.fetch_incremental(days_back=10)
    inicio, fim = vistos["janela"]
    assert (fim - inicio).days == 10
    assert len({inicio + timedelta(days=i) for i in range(11)}) == 11


def test_piso_do_tiktok_continua_valendo(monkeypatch, congela):
    vistos = _captura(monkeypatch, tk, "fetch")
    tk.fetch_incremental(days_back=3)
    inicio, fim = vistos["janela"]
    assert (fim - inicio).days == tk.MIN_INCREMENTAL_LOOKBACK_DAYS


def test_closed_window_recusa_days_back_invalido():
    for ruim in (-1, "3", 3.0, True):
        with pytest.raises(ValueError):
            cal.closed_window(ruim)


# ---------------------------------------------------------------------------
# 4. Segunda defesa: conector mal-comportado
# ---------------------------------------------------------------------------
def test_conector_devolvendo_d0_e_bloqueado(monkeypatch):
    monkeypatch.setattr(dp, "last_closed_date", lambda agora=None: FECHADO)
    rows = [{"date": date(2026, 8, 26), "loja_id": 1},
            {"date": date(2026, 8, 28), "loja_id": 2}]
    with pytest.raises(dp.ClosedDayViolation, match="2026-08-28"):
        dp._assert_canonical_rows_closed(rows, "tiktok")


def test_segunda_defesa_bloqueia_nao_filtra(monkeypatch):
    """Nenhuma linha pode ser descartada em silencio."""
    monkeypatch.setattr(dp, "last_closed_date", lambda agora=None: FECHADO)
    rows = [{"date": date(2026, 8, 28), "loja_id": 1}]
    with pytest.raises(dp.ClosedDayViolation) as e:
        dp._assert_canonical_rows_closed(rows, "ml")
    assert "descartada silenciosamente" in str(e.value)
    assert len(rows) == 1, "a lista original foi mutada"


def test_segunda_defesa_aceita_tudo_em_d1_ou_antes(monkeypatch):
    monkeypatch.setattr(dp, "last_closed_date", lambda agora=None: FECHADO)
    rows = [{"date": FECHADO, "loja_id": 1},
            {"date": FECHADO - timedelta(days=5), "loja_id": 2},
            {"date": None, "loja_id": 3}]
    dp._assert_canonical_rows_closed(rows, "shopee")


def test_segunda_defesa_roda_antes_do_upsert():
    """A chamada precisa vir antes de qualquer execucao de upsert em run()."""
    import inspect
    src = inspect.getsource(dp.run)
    i_guard = src.index("_assert_canonical_rows_closed")
    i_upsert = src.index("session.execute(upsert_sql")
    assert i_guard < i_upsert


# ---------------------------------------------------------------------------
# 5. Nenhum date.today() sobrou nos caminhos alterados
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mod", [dp, tk, ml, sh, cal])
def test_nenhum_date_today_nos_caminhos_alterados(mod):
    import inspect
    import re
    src = inspect.getsource(mod)
    codigo = "\n".join(l.split("#")[0] for l in src.split("\n"))
    codigo = re.sub(r'""".*?"""', "", codigo, flags=re.S)
    assert "date.today()" not in codigo, f"{mod.__name__} ainda usa date.today()"
