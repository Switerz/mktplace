"""DM do bot da Torre que nao se repete: a chave da fotografia vai em `metadata`
e quem ja' a tem no historico da DM nao recebe de novo."""
from __future__ import annotations

import json

import pytest

from app.services import slack_dm
from app.services.slack_dm import (
    EVENTO_METADATA,
    ResultadoEnvio,
    SlackFalhou,
    enviar_dm_sem_repetir,
)

CHAVE = "shopee:2026-11-03T13:02:00+00:00"


def _msg_com_chave(chave: str) -> dict:
    return {"text": "x", "metadata": {"event_type": EVENTO_METADATA,
                                      "event_payload": {"chave": chave}}}


class _SlackFalso:
    """Responde pela Web API; `historico[email]` e' a lista de mensagens da DM."""

    def __init__(self, historico: dict[str, list[dict]] | None = None,
                 falha: dict[str, str] | None = None):
        self.historico = historico or {}
        self.falha = falha or {}
        self.chamadas: list[tuple[str, dict]] = []

    def __call__(self, token: str, metodo: str, campos: dict) -> dict:
        self.chamadas.append((metodo, dict(campos)))
        if metodo in self.falha:
            raise SlackFalhou(f"{metodo}: {self.falha[metodo]}")
        if metodo == "users.lookupByEmail":
            return {"ok": True, "user": {"id": "U-" + campos["email"]}}
        if metodo == "conversations.open":
            return {"ok": True, "channel": {"id": "D-" + campos["users"]}}
        if metodo == "conversations.history":
            email = campos["channel"].removeprefix("D-U-")
            return {"ok": True, "messages": self.historico.get(email, [])}
        if metodo == "chat.postMessage":
            return {"ok": True}
        raise AssertionError(f"metodo inesperado: {metodo}")

    def posts(self) -> list[dict]:
        return [c for m, c in self.chamadas if m == "chat.postMessage"]


def test_posta_com_metadata_da_chave():
    slack = _SlackFalso()
    r = enviar_dm_sem_repetir("xoxb-t", ["a@x.com"], "oi", CHAVE, chamar=slack)
    assert r == ResultadoEnvio(enviados=1, ja_tinham=0)
    (post,) = slack.posts()
    assert post["channel"] == "D-U-a@x.com" and post["text"] == "oi"
    meta = json.loads(post["metadata"])
    assert meta == {"event_type": EVENTO_METADATA, "event_payload": {"chave": CHAVE}}


def test_historico_e_lido_com_metadata():
    slack = _SlackFalso()
    enviar_dm_sem_repetir("xoxb-t", ["a@x.com"], "oi", CHAVE, chamar=slack)
    (hist,) = [c for m, c in slack.chamadas if m == "conversations.history"]
    assert hist["include_all_metadata"] == "true"


def test_quem_ja_recebeu_esta_chave_nao_recebe_de_novo():
    slack = _SlackFalso({"a@x.com": [_msg_com_chave(CHAVE)]})
    r = enviar_dm_sem_repetir("xoxb-t", ["a@x.com"], "oi", CHAVE, chamar=slack)
    assert r == ResultadoEnvio(enviados=0, ja_tinham=1)
    assert slack.posts() == []


def test_chave_diferente_no_historico_nao_bloqueia():
    slack = _SlackFalso({"a@x.com": [_msg_com_chave("shopee:outra"), {"text": "sem metadata"}]})
    r = enviar_dm_sem_repetir("xoxb-t", ["a@x.com"], "oi", CHAVE, chamar=slack)
    assert r.enviados == 1


def test_metadata_de_outro_evento_nao_bloqueia():
    outro = {"metadata": {"event_type": "outro", "event_payload": {"chave": CHAVE}}}
    slack = _SlackFalso({"a@x.com": [outro]})
    assert enviar_dm_sem_repetir("xoxb-t", ["a@x.com"], "oi", CHAVE, chamar=slack).enviados == 1


def test_reenvio_apos_falha_parcial_so_manda_para_quem_faltou():
    slack = _SlackFalso({"a@x.com": [_msg_com_chave(CHAVE)]})
    r = enviar_dm_sem_repetir("xoxb-t", ["a@x.com", "b@x.com"], "oi", CHAVE, chamar=slack)
    assert r == ResultadoEnvio(enviados=1, ja_tinham=1)
    assert [p["channel"] for p in slack.posts()] == ["D-U-b@x.com"]


def test_falha_ao_ler_historico_nao_posta():
    slack = _SlackFalso(falha={"conversations.history": "missing_scope"})
    with pytest.raises(SlackFalhou):
        enviar_dm_sem_repetir("xoxb-t", ["a@x.com"], "oi", CHAVE, chamar=slack)
    assert slack.posts() == []


def test_ok_false_vira_falha_sem_token(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": false, "error": "invalid_auth"}'

    monkeypatch.setattr(slack_dm.urllib.request, "urlopen", lambda req, timeout: _Resp())
    with pytest.raises(SlackFalhou) as erro:
        enviar_dm_sem_repetir("xoxb-segredo", ["a@x.com"], "oi", CHAVE)
    assert "invalid_auth" in str(erro.value)
    assert "xoxb-segredo" not in str(erro.value)


def test_erro_de_rede_nunca_carrega_o_token(monkeypatch):
    def _quebra(req, timeout):
        raise slack_dm.urllib.error.URLError(OSError("xoxb-segredo"))

    monkeypatch.setattr(slack_dm.urllib.request, "urlopen", _quebra)
    with pytest.raises(SlackFalhou) as erro:
        enviar_dm_sem_repetir("xoxb-segredo", ["a@x.com"], "oi", CHAVE)
    assert "xoxb-segredo" not in str(erro.value)
