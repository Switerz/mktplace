"""Rotas novas do bot: /alertas/criticos e /alertas/report-diario. Mesmas
protecoes da rota de vencidos: segredo, mensagens fixas, simular sem enviar."""
from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.database import get_db
from app.main import app
from app.routers import alertas as rota
from app.services import torre_alertas as ta
from app.services import torre_report as tr
from app.services.slack_dm import Item, ResultadoEnvio, SlackFalhou

SEGREDO = "segredo-do-gatilho"
CRITICOS = "/api/v1/alertas/criticos"
REPORT = "/api/v1/alertas/report-diario"


class _Espiao:
    def __init__(self, devolve=None, levanta=None):
        self.devolve, self.levanta, self.chamadas = devolve, levanta, []

    def __call__(self, *args, **kwargs):
        self.chamadas.append(args)
        if self.levanta:
            raise self.levanta
        return self.devolve


ITENS = [Item("ldr:mercadolivre:rituaria:2026-11-05", "RITUÁRIA acima do teto")]
DADOS = tr.Dados(date(2026, 11, 4), {("kokeshi", "ml", date(2026, 11, 4)): 100_000.0}, {},
                 ta.LeituraLdr(), ta.LeituraSaude())


@pytest.fixture
def ambiente(monkeypatch):
    s = rota.settings
    monkeypatch.setattr(s, "alerta_webhook_secret", SecretStr(SEGREDO))
    monkeypatch.setattr(s, "slack_bot_token", SecretStr("xoxb-token"))
    monkeypatch.setattr(s, "expedicao_alerta_destinatarios", "a@x.com")
    avaliar = _Espiao(devolve=ta.AvaliacaoCriticos(ITENS, ["Shopee"], False))
    ler = _Espiao(devolve=DADOS)
    enviar = _Espiao(devolve=ResultadoEnvio(enviados=1, ja_tinham=0))
    monkeypatch.setattr(rota.torre_alertas, "avaliar_criticos", avaliar)
    monkeypatch.setattr(rota.torre_report, "ler_dados", ler)
    monkeypatch.setattr(rota, "enviar_itens_sem_repetir", enviar)
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app), avaliar, ler, enviar
    app.dependency_overrides.pop(get_db, None)


def _post(cliente, url, token=SEGREDO, **params):
    headers = {} if token is None else {"X-Alerta-Token": token}
    return cliente.post(url, headers=headers, params=params)


@pytest.mark.parametrize("url", [CRITICOS, REPORT])
def test_sem_segredo_configurado_503_e_nao_le(ambiente, monkeypatch, url):
    cliente, avaliar, ler, _ = ambiente
    monkeypatch.setattr(rota.settings, "alerta_webhook_secret", SecretStr(""))
    assert _post(cliente, url).json()["detail"] == "Alerta nao configurado."
    assert avaliar.chamadas == [] and ler.chamadas == []


@pytest.mark.parametrize("url", [CRITICOS, REPORT])
def test_segredo_errado_401_e_nao_ecoa(ambiente, url):
    cliente, avaliar, ler, _ = ambiente
    r = _post(cliente, url, token="errado-xyz")
    assert r.status_code == 401 and "errado-xyz" not in r.text
    assert avaliar.chamadas == [] and ler.chamadas == []


@pytest.mark.parametrize("url", [CRITICOS, REPORT])
def test_sem_token_do_slack_503_mas_simular_funciona(ambiente, monkeypatch, url):
    cliente, *_ = ambiente
    monkeypatch.setattr(rota.settings, "slack_bot_token", SecretStr(""))
    assert _post(cliente, url).json()["detail"] == "Envio nao configurado."
    assert _post(cliente, url, simular="true").status_code == 200


def test_criticos_simular_devolve_mensagem_sem_enviar(ambiente):
    cliente, _, _, enviar = ambiente
    corpo = _post(cliente, CRITICOS, simular="true").json()
    assert corpo["itens"] == 1 and corpo["chaves"] == [ITENS[0].chave]
    assert corpo["canais_indisponiveis"] == ["Shopee"]
    assert corpo["mensagem"].startswith(":rotating_light: Torre:")
    assert "RITUÁRIA acima do teto" in json.dumps(corpo["blocos"], ensure_ascii=False)
    assert enviar.chamadas == []


def test_criticos_envia_itens(ambiente):
    cliente, _, _, enviar = ambiente
    corpo = _post(cliente, CRITICOS).json()
    assert (corpo["enviados"], corpo["ja_tinham"]) == (1, 0)
    ((token, destinatarios, itens, montar),) = enviar.chamadas
    assert (token, destinatarios, itens) == ("xoxb-token", ["a@x.com"], ITENS)
    assert montar is ta.montar_mensagem_criticos


def test_criticos_sem_itens_nao_chama_slack(ambiente):
    cliente, avaliar, _, enviar = ambiente
    avaliar.devolve = ta.AvaliacaoCriticos([], [], False)
    assert _post(cliente, CRITICOS).json()["itens"] == 0
    assert enviar.chamadas == []


def test_criticos_slack_falhou_502(ambiente):
    cliente, _, _, enviar = ambiente
    enviar.levanta = SlackFalhou("chat.postMessage: invalid_auth")
    r = _post(cliente, CRITICOS)
    assert r.status_code == 502 and r.json()["detail"] == "Slack recusou o envio."


def test_report_simular_devolve_texto(ambiente):
    cliente, _, _, enviar = ambiente
    corpo = _post(cliente, REPORT, simular="true").json()
    assert corpo["dia"] == "2026-11-04"
    assert corpo["mensagem"].startswith(":sunrise: *Bom dia! Ontem (qua, 04/11): R$ 100,0 mil")
    assert enviar.chamadas == []


def test_report_envia_com_a_chave_do_dia(ambiente):
    cliente, _, _, enviar = ambiente
    assert _post(cliente, REPORT).json()["enviados"] == 1
    ((_, _, itens, montar),) = enviar.chamadas
    (item,) = itens
    msg = montar(itens)
    assert item.chave == "report:2026-11-04" and msg.texto.startswith(":sunrise: Bom dia!")
    assert msg.blocos[0]["type"] == "header"


def test_report_leitura_falhou_503_sem_texto_da_excecao(ambiente):
    cliente, _, ler, _ = ambiente
    ler.levanta = RuntimeError("postgres://usuario:senha@host")
    r = _post(cliente, REPORT)
    assert r.status_code == 503 and r.json()["detail"] == "Dados indisponiveis."
    assert "senha" not in r.text
