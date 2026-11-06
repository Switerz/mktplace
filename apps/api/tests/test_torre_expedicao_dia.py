"""A5 (vencem hoje, 9h) e A6 (corte da coleta, 15h30) do bot Torre."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.database import get_db
from app.main import app
from app.routers import alertas as rota
from app.services import torre_expedicao_dia as d
from app.services.slack_dm import ResultadoEnvio

AGORA = datetime(2026, 11, 5, 18, 30, tzinfo=timezone.utc)  # 15h30 BRT
HOJE = date(2026, 11, 5)
AMANHA = date(2026, 11, 6)


class _Db:
    """Devolve, em ordem, o resultado de cada `execute` (lote, resumo, fila)."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)

    def execute(self, sql, params=None):
        linhas = self.respostas.pop(0)

        class R:
            def mappings(self):
                return self

            def all(self):
                return linhas

            def __iter__(self):
                return iter(linhas)

            def scalar(self):
                return linhas

        return R()

    def rollback(self):
        pass


def _r(marca, etapa, status, dia, n, travado=False):
    return {"brand": marca, "etapa": etapa, "deadline_status": status, "dia": dia,
            "travado": travado, "n": n}


LOTE = [{"refresh_batch_id": "b1", "effective_at": AGORA - timedelta(minutes=20)}]
RESUMO = [{"backlog_count": 10, "deadline_unavailable_count": 0}]


def test_ler_fila_separa_hoje_amanha_vencidos_e_travados():
    db = _Db(LOTE, RESUMO, [
        _r("apice", "a_preparar", "due_within_24h", HOJE, 5),
        _r("apice", "aguardando_coleta", "due_within_24h", HOJE, 100, travado=True),
        _r("barbours", "aguardando_coleta", "due_within_24h", HOJE, 300),
        _r("barbours", "aguardando_coleta", "overdue", HOJE - timedelta(days=1), 7),
        _r("barbours", "aguardando_coleta", "on_time", AMANHA, 40),
        _r("barbours", "aguardando_coleta", "on_time", AMANHA + timedelta(days=3), 99),
        _r("lescent", "entregue_transportadora", "due_within_24h", HOJE, 50),  # ja' saiu
    ])
    c = d.ler_fila(db, "shopee", HOJE)
    assert c.total_hoje() == 405 and c.total_hoje(d.NAO_PRONTO) == 5
    assert c.por_marca() == [("barbours", 300), ("apice", 105)]
    assert (c.amanha, c.dia_amanha, c.vencidos, c.travados) == (40, AMANHA, 7, 100)


def test_ler_fila_sem_lote_unico_e_none():
    assert d.ler_fila(_Db([]), "shopee", HOJE) is None
    assert d.ler_fila(_Db(LOTE * 2), "shopee", HOJE) is None


def _canal(canal="shopee", hoje=None, com_etapa=True, idade_min=20, **kw):
    c = d.Canal(canal, AGORA - timedelta(minutes=idade_min), hoje or {}, com_etapa=com_etapa, **kw)
    return c


def _leitura(*canais, indisponiveis=()):
    return d.LeituraDia(HOJE, list(canais), list(indisponiveis))


def _texto(m):
    return json.dumps(m.blocos, ensure_ascii=False)


def test_manha_destaca_o_que_nao_esta_pronto_e_aponta_a_fila():
    shopee = _canal(hoje={"apice": {"a_preparar": 5, "aguardando_coleta": 100},
                          "barbours": {"aguardando_coleta": 300}},
                    amanha=40, dia_amanha=AMANHA, vencidos=7, travados=3)
    m = d.montar(_leitura(shopee), d.MANHA)
    assert m.texto == ":package: Torre: 405 pedidos vencem hoje"
    assert m.blocos[0]["text"]["text"] == ":package: Vencem hoje · qui, 05/11"
    sec = next(b for b in m.blocos if b["type"] == "section")
    t = sec["text"]["text"]
    assert ":red_circle: *5* ainda não estão prontos (a preparar 5)" in t
    assert ":truck: *400* aguardando coleta" in t
    assert t.index("BARBOURS") < t.index("ÁPICE")
    assert sec["accessory"]["url"].endswith("/expedicao?channel=shopee&visao=detalhe")
    assert "vencem sex, 06/11: 40" in _texto(m) and "já vencidos: 7" in _texto(m)


def test_corte_lista_quem_aguarda_coleta_e_quem_nem_esta_pronto():
    ml = _canal("mercadolivre", hoje={"kokeshi": {"a_imprimir": 54, "aguardando_coleta": 48}})
    tk = _canal("tiktokshop", hoje={"barbours": {"aguardando_coleta": 852}}, com_etapa=False)
    m = d.montar(_leitura(ml, tk), d.CORTE)
    assert m.texto == ":alarm_clock: Torre: 954 pedidos vencem hoje e ainda não saíram"
    ml_sec, tk_sec = [b for b in m.blocos if b["type"] == "section"]
    assert "*54* vencem hoje e *ainda não estão prontos*: *KOKESHI* 54" in ml_sec["text"]["text"]
    assert "*48* vencem hoje e aguardam coleta" in ml_sec["text"]["text"]
    # Uma marca so': o botao ja' abre filtrado nela.
    assert tk_sec["accessory"]["url"].endswith("/expedicao?channel=tiktokshop&marca=barbours&visao=detalhe")


def test_nada_vence_hoje_nao_manda():
    assert d.montar(_leitura(_canal()), d.MANHA) is None
    assert d.item(_leitura(), d.CORTE) is None


def test_indisponivel_vira_nota_e_chave_por_dia_e_momento():
    shopee = _canal(hoje={"apice": {"aguardando_coleta": 1}})
    lei = _leitura(shopee, indisponiveis=["TikTok Shop"])
    item, m = d.item(lei, d.MANHA)
    assert item.chave == "expedicao-dia:manha:2026-11-05"
    assert "Sem fotografia recente de: TikTok Shop." in _texto(m)


def test_ler_dia_tira_canal_velho_e_isola_falha(monkeypatch):
    monkeypatch.setattr(d, "ler_fila", lambda db, canal, hoje: (
        _canal(canal, idade_min=60 * 5) if canal == "shopee" else (_ for _ in ()).throw(RuntimeError("x"))))
    monkeypatch.setattr(d, "ler_tiktok", lambda db, hoje: _canal("tiktokshop", com_etapa=False))
    lei = d.ler_dia(_Db(), AGORA)
    assert [c.canal for c in lei.canais] == ["tiktokshop"]
    assert lei.indisponiveis == ["Shopee", "Mercado Livre"]


# --- rota ---------------------------------------------------------------------
SEGREDO = "segredo-do-gatilho"
URL = "/api/v1/alertas/expedicao-dia"


@pytest.fixture
def cliente(monkeypatch):
    s = rota.settings
    monkeypatch.setattr(s, "alerta_webhook_secret", SecretStr(SEGREDO))
    monkeypatch.setattr(s, "slack_bot_token", SecretStr("xoxb-token"))
    monkeypatch.setattr(s, "expedicao_alerta_destinatarios", "a@x.com")
    lei = _leitura(_canal(hoje={"apice": {"aguardando_coleta": 10}}))
    monkeypatch.setattr(rota.torre_expedicao_dia, "ler_dia", lambda db, agora: lei)
    enviados = []
    monkeypatch.setattr(rota, "enviar_itens_sem_repetir",
                        lambda *a: enviados.append(a) or ResultadoEnvio(1, 0))
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app), enviados
    app.dependency_overrides.pop(get_db, None)


def _post(c, **params):
    return c.post(URL, headers={"X-Alerta-Token": SEGREDO}, params=params)


def test_rota_simular_devolve_blocos_sem_enviar(cliente):
    c, enviados = cliente
    corpo = _post(c, momento="manha", simular="true").json()
    assert corpo["vencem_hoje"] == 10 and corpo["mensagem"].startswith(":package:")
    assert corpo["blocos"][0]["type"] == "header" and enviados == []


def test_rota_envia_com_chave_do_dia(cliente):
    c, enviados = cliente
    assert _post(c, momento="corte").json()["enviados"] == 1
    ((_, _, itens, montar),) = enviados
    assert itens[0].chave == "expedicao-dia:corte:2026-11-05"
    assert montar(itens).blocos[0]["type"] == "header"


def test_rota_momento_invalido_422_sem_eco(cliente):
    c, _ = cliente
    r = _post(c, momento="<script>")
    assert r.status_code == 422 and "<script>" not in r.text
