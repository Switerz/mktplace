"""DM com varios itens (alertas criticos e report): cada item tem a sua chave, e
quem ja' recebeu uma chave nao a recebe de novo."""
from __future__ import annotations

import json

import pytest

from app.services.slack_dm import (
    EVENTO_ITENS,
    EVENTO_METADATA,
    Item,
    ResultadoEnvio,
    SlackFalhou,
    enviar_itens_sem_repetir,
)


class _SlackFalso:
    def __init__(self, historico=None, falha=None):
        self.historico = historico or {}
        self.falha = falha or {}
        self.chamadas: list[tuple[str, dict]] = []

    def __call__(self, token, metodo, campos):
        self.chamadas.append((metodo, dict(campos)))
        if metodo in self.falha:
            raise SlackFalhou(f"{metodo}: {self.falha[metodo]}")
        if metodo == "users.lookupByEmail":
            return {"ok": True, "user": {"id": "U-" + campos["email"]}}
        if metodo == "conversations.open":
            return {"ok": True, "channel": {"id": "D-" + campos["users"]}}
        if metodo == "conversations.history":
            return {"ok": True, "messages": self.historico.get(campos["channel"].removeprefix("D-U-"), [])}
        if metodo == "chat.postMessage":
            return {"ok": True}
        raise AssertionError(metodo)

    def posts(self):
        return [c for m, c in self.chamadas if m == "chat.postMessage"]


def _msg(evento, payload):
    return {"text": "x", "metadata": {"event_type": evento, "event_payload": payload}}


def _juntar(itens):
    return " | ".join(i.texto for i in itens)


ITENS = [Item("ldr:mercadolivre:rituaria:2026-11-05", "A"),
         Item("saude:mercadolivre:kokeshi:ml_claims:2026-11-04", "B")]


def test_posta_todos_com_as_chaves_na_metadata():
    slack = _SlackFalso()
    r = enviar_itens_sem_repetir("xoxb-t", ["a@x.com"], ITENS, _juntar, chamar=slack)
    assert r == ResultadoEnvio(enviados=1, ja_tinham=0)
    (post,) = slack.posts()
    assert post["text"] == "A | B"
    assert json.loads(post["metadata"]) == {
        "event_type": EVENTO_ITENS, "event_payload": {"chaves": [i.chave for i in ITENS]}}


def test_item_ja_recebido_sai_da_mensagem():
    slack = _SlackFalso({"a@x.com": [_msg(EVENTO_ITENS, {"chaves": [ITENS[0].chave]})]})
    enviar_itens_sem_repetir("xoxb-t", ["a@x.com"], ITENS, _juntar, chamar=slack)
    assert slack.posts()[0]["text"] == "B"


def test_todos_ja_recebidos_nao_posta():
    slack = _SlackFalso({"a@x.com": [_msg(EVENTO_ITENS, {"chaves": [i.chave for i in ITENS]})]})
    r = enviar_itens_sem_repetir("xoxb-t", ["a@x.com"], ITENS, _juntar, chamar=slack)
    assert r == ResultadoEnvio(enviados=0, ja_tinham=1) and slack.posts() == []


def test_metadata_de_outro_evento_nao_conta():
    slack = _SlackFalso({"a@x.com": [_msg(EVENTO_METADATA, {"chave": ITENS[0].chave})]})
    enviar_itens_sem_repetir("xoxb-t", ["a@x.com"], ITENS, _juntar, chamar=slack)
    assert slack.posts()[0]["text"] == "A | B"


def test_historico_ilegivel_nao_posta_e_levanta_sem_email():
    slack = _SlackFalso(falha={"conversations.history": "missing_scope"})
    with pytest.raises(SlackFalhou) as erro:
        enviar_itens_sem_repetir("xoxb-t", ["a@x.com", "b@x.com"], ITENS, _juntar, chamar=slack)
    assert slack.posts() == [] and "a@x.com" not in str(erro.value)


def test_um_destinatario_com_falha_nao_bloqueia_o_outro():
    class Parcial(_SlackFalso):
        def __call__(self, token, metodo, campos):
            if metodo == "users.lookupByEmail" and campos["email"] == "saiu@x.com":
                raise SlackFalhou("users.lookupByEmail: users_not_found")
            return super().__call__(token, metodo, campos)

    slack = Parcial()
    with pytest.raises(SlackFalhou):
        enviar_itens_sem_repetir("xoxb-t", ["saiu@x.com", "b@x.com"], ITENS, _juntar, chamar=slack)
    assert len(slack.posts()) == 1
