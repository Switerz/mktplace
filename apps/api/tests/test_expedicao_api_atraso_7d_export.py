"""Atraso de 7 dias da Kokeshi calculado pela API (o bot grava direto no Neon).

Validado em 03/11/2026 contra os dados reais do Neon: a API e
`pipelines.expedicao.atraso_7d.apurar_shopee` deram exatamente 40 atrasados
em 2.133 pedidos (27/10 a 02/11). Aqui: a janela e a maturidade sao as do
pipeline, o SQL tem as exclusoes da regra, e a API preenche a conta do export.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import expedicao_atraso_7d_export as K
from app.services import expedicao_service as svc
from pipelines.expedicao import shopee_ldr

UTC = timezone.utc


def test_janela_igual_a_do_pipeline():
    from pipelines.expedicao import atraso_7d

    for h in (0, 2, 3, 9, 12, 23):
        agora = datetime(2026, 11, 3, h, 30, tzinfo=UTC)
        assert K.janela(agora) == atraso_7d.janela(agora), h


@pytest.mark.parametrize("wm_h", [None, 5, 8, 9, 10, 15, 30, 60])
def test_dia_maduro_igual_ao_do_pipeline(wm_h):
    agora = datetime(2026, 11, 3, 19, 0, tzinfo=UTC)
    hoje = agora.astimezone(K.BRT).date()
    desde, ate = K.janela(agora)
    wm = None if wm_h is None else datetime(2026, 11, 1, 0, 0, tzinfo=UTC) + timedelta(hours=wm_h)
    esperado = None
    d = desde
    while d <= ate:
        if shopee_ldr.madura(d, hoje, wm):
            esperado = d
        d += timedelta(days=1)
    assert K.fim_maduro(desde, ate, hoje, wm) == esperado


def test_sql_tem_as_exclusoes_da_regra():
    sql = K.SQL
    assert "pay_time IS NOT NULL" in sql
    assert "fulfilled_by_shopee" in sql and "fulfilled_by_local_seller" in sql
    assert "('CANCELLED', 'IN_CANCEL')" in sql
    assert "('SHIPPED', 'TO_CONFIRM_RECEIVE', 'COMPLETED', 'TO_RETURN')" in sql
    assert "pickup_done_time > ship_by_date" in sql
    assert "ship_by_date < :agora" in sql
    assert "INTERVAL '-03:00'" in sql


class _Linha:
    def __init__(self, d):
        self._d = d

    def mappings(self):
        return self

    def first(self):
        return self._d


class _Db:
    def __init__(self, linha):
        self.linha = linha
        self.params = None

    def execute(self, _sql, params):
        self.params = params
        return _Linha(self.linha)


AGORA = datetime(2026, 11, 3, 19, 30, tzinfo=UTC)
WM = datetime(2026, 11, 3, 19, 20, tzinfo=UTC)


def test_calcular_devolve_as_quatro_chaves():
    db = _Db({"fluxo_desconhecido": 0, "due": 2133, "late": 40})
    r = K.calcular(db, "kokeshi", WM, AGORA)
    assert r == {"due_7d_count": 2133, "late_7d_count": 40,
                 "late_7d_window_start": date(2026, 10, 27),
                 "late_7d_window_end": date(2026, 11, 2)}
    assert db.params["ate"] == date(2026, 11, 2)


def test_sem_base_sem_watermark_ou_fluxo_desconhecido_fica_sem_percentual():
    assert K.calcular(_Db({"fluxo_desconhecido": 0, "due": 0, "late": 0}), "k", WM, AGORA) is None
    assert K.calcular(_Db({"fluxo_desconhecido": 1, "due": 9, "late": 1}), "k", WM, AGORA) is None
    assert K.calcular(_Db({"fluxo_desconhecido": 0, "due": 9, "late": 1}), "k", None, AGORA) is None


def test_juntar_export_preenche_a_conta_sem_calculo(monkeypatch):
    extra = [{"shop_account": "kokeshi", "brand": "kokeshi", "backlog_count": 10,
              "observed_at": AGORA, "refresh_batch_id": "b", "source_watermark_at": WM,
              "due_7d_count": None, "late_7d_count": None,
              "late_7d_window_start": None, "late_7d_window_end": None}]
    monkeypatch.setattr(svc, "_fotografia_export", lambda db, canal, contas: [dict(x) for x in extra])
    monkeypatch.setattr(K, "calcular", lambda db, conta, wm, ef: {
        "due_7d_count": 2133, "late_7d_count": 40,
        "late_7d_window_start": date(2026, 10, 27), "late_7d_window_end": date(2026, 11, 2)})
    snap = {"resumos": [], "effective_at": AGORA, "linhas": 0}
    svc._juntar_export(None, svc.CANAIS["shopee"], snap)
    (k,) = snap["resumos"]
    assert (k["due_7d_count"], k["late_7d_count"]) == (2133, 40)


def test_falha_no_calculo_nao_derruba_a_resposta(monkeypatch):
    extra = [{"shop_account": "kokeshi", "brand": "kokeshi", "backlog_count": 1,
              "observed_at": AGORA, "refresh_batch_id": "b", "source_watermark_at": WM}]
    monkeypatch.setattr(svc, "_fotografia_export", lambda db, canal, contas: [dict(x) for x in extra])

    def explode(*a, **k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(K, "calcular", explode)
    snap = {"resumos": [], "effective_at": AGORA, "linhas": 0}
    svc._juntar_export(None, svc.CANAIS["shopee"], snap)
    assert snap["resumos"][0]["due_7d_count"] is None
