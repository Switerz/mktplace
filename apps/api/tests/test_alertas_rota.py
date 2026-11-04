"""Rota que o gatilho do GoDeploy chama: protegida por segredo, mensagens fixas,
nada e' lido antes de passar pela checagem anterior."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.database import get_db
from app.main import app
from app.routers import alertas as rota
from app.services.alerta_vencidos import Avaliacao
from app.services.slack_dm import ResultadoEnvio, SlackFalhou

URL = "/api/v1/alertas/expedicao-vencidos"
SEGREDO = "segredo-do-gatilho"
CHAVE = "shopee:2026-11-03T13:02:00+00:00"

EM_ALERTA = Avaliacao("shopee", "evento", "entrou", "mensagem do alerta", CHAVE, 0.046)
QUIETO = Avaliacao("shopee", "sem_evento", None, None, None, 0.01)


class _Espiao:
    def __init__(self, devolve=None, levanta=None):
        self.devolve = devolve
        self.levanta = levanta
        self.chamadas: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.chamadas.append(args)
        if self.levanta:
            raise self.levanta
        return self.devolve


@pytest.fixture
def ambiente(monkeypatch):
    """Config completa, banco falso e espioes em `avaliar` e no envio."""
    s = rota.settings
    monkeypatch.setattr(s, "alerta_webhook_secret", SecretStr(SEGREDO))
    monkeypatch.setattr(s, "slack_bot_token", SecretStr("xoxb-token"))
    monkeypatch.setattr(s, "expedicao_alerta_destinatarios", "a@x.com, b@x.com,")
    avaliar = _Espiao(devolve=EM_ALERTA)
    enviar = _Espiao(devolve=ResultadoEnvio(enviados=2, ja_tinham=0))
    monkeypatch.setattr(rota, "avaliar", avaliar)
    monkeypatch.setattr(rota, "enviar_dm_sem_repetir", enviar)
    app.dependency_overrides[get_db] = lambda: object()
    yield TestClient(app), avaliar, enviar
    app.dependency_overrides.pop(get_db, None)


def _post(cliente, token=SEGREDO, **params):
    headers = {} if token is None else {"X-Alerta-Token": token}
    return cliente.post(URL, headers=headers, params=params)


def test_sem_segredo_configurado_503_sem_ler_nada(ambiente, monkeypatch):
    cliente, avaliar, enviar = ambiente
    monkeypatch.setattr(rota.settings, "alerta_webhook_secret", SecretStr(""))
    r = _post(cliente, token="")
    assert r.status_code == 503 and r.json()["detail"] == "Alerta nao configurado."
    assert avaliar.chamadas == [] and enviar.chamadas == []


def test_header_ausente_401(ambiente):
    cliente, avaliar, _ = ambiente
    r = _post(cliente, token=None)
    assert r.status_code == 401 and r.json()["detail"] == "Nao autorizado."
    assert avaliar.chamadas == []


def test_header_errado_401_e_nao_ecoa(ambiente):
    cliente, avaliar, _ = ambiente
    r = _post(cliente, token="errado-xyz")
    assert r.status_code == 401 and "errado-xyz" not in r.text
    assert avaliar.chamadas == []


def test_banco_indisponivel_503(ambiente):
    cliente, avaliar, _ = ambiente
    app.dependency_overrides[get_db] = lambda: None
    r = _post(cliente)
    assert r.status_code == 503 and r.json()["detail"] == "Banco de dados indisponivel."
    assert avaliar.chamadas == []


def test_simular_devolve_mensagem_sem_chamar_slack(ambiente):
    cliente, _, enviar = ambiente
    r = _post(cliente, simular="true")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["mensagem"] == "mensagem do alerta"
    assert (corpo["enviados"], corpo["ja_tinham"]) == (0, 0)
    assert enviar.chamadas == []


def test_simular_funciona_sem_config_do_slack(ambiente, monkeypatch):
    cliente, _, _ = ambiente
    monkeypatch.setattr(rota.settings, "slack_bot_token", SecretStr(""))
    assert _post(cliente, simular="true").status_code == 200


def test_evento_envia_com_a_chave_da_fotografia(ambiente):
    cliente, _, enviar = ambiente
    r = _post(cliente)
    assert r.status_code == 200
    assert r.json() == {"canal": "shopee", "situacao": "evento", "evento": "entrou",
                        "enviados": 2, "ja_tinham": 0}
    ((token, destinatarios, texto, chave),) = enviar.chamadas
    assert (token, destinatarios, texto, chave) == (
        "xoxb-token", ["a@x.com", "b@x.com"], "mensagem do alerta", CHAVE)


def test_sem_evento_nao_chama_slack(ambiente):
    cliente, avaliar, enviar = ambiente
    avaliar.devolve = QUIETO
    r = _post(cliente)
    assert r.status_code == 200 and r.json()["situacao"] == "sem_evento"
    assert enviar.chamadas == []


def test_mesma_fotografia_duas_vezes_so_envia_uma(ambiente):
    cliente, _, enviar = ambiente
    vistas: set[tuple[str, str]] = set()

    def envio(token, destinatarios, texto, chave):
        novos = [d for d in destinatarios if (d, chave) not in vistas]
        vistas.update((d, chave) for d in novos)
        return ResultadoEnvio(enviados=len(novos), ja_tinham=len(destinatarios) - len(novos))

    enviar.devolve = None
    rota.enviar_dm_sem_repetir = envio  # monkeypatch da fixture restaura
    primeira, segunda = _post(cliente).json(), _post(cliente).json()
    assert (primeira["enviados"], primeira["ja_tinham"]) == (2, 0)
    assert (segunda["enviados"], segunda["ja_tinham"]) == (0, 2)


def test_slack_falhou_502_sem_token_no_corpo(ambiente):
    cliente, _, enviar = ambiente
    enviar.levanta = SlackFalhou("chat.postMessage: invalid_auth xoxb-token")
    r = _post(cliente)
    assert r.status_code == 502 and r.json()["detail"] == "Slack recusou o envio."
    assert "xoxb" not in r.text


@pytest.mark.parametrize("faltando", ["slack_bot_token", "expedicao_alerta_destinatarios"])
def test_envio_sem_token_ou_destinatarios_503(ambiente, monkeypatch, faltando):
    cliente, avaliar, _ = ambiente
    vazio = SecretStr("") if faltando == "slack_bot_token" else " , "
    monkeypatch.setattr(rota.settings, faltando, vazio)
    r = _post(cliente)
    assert r.status_code == 503 and r.json()["detail"] == "Envio nao configurado."
    assert avaliar.chamadas == []


def test_get_nao_dispara(ambiente):
    cliente, avaliar, _ = ambiente
    assert cliente.get(URL, headers={"X-Alerta-Token": SEGREDO}).status_code == 405
    assert avaliar.chamadas == []


def test_router_de_expedicao_continua_so_get():
    from app.routers.expedicao import router

    for r in router.routes:
        assert set(r.methods) <= {"GET", "HEAD"}, (r.path, r.methods)
