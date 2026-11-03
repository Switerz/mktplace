"""DM pelo bot da Torre no Slack (app em `infra/slack/torre-bot`).

Fica fora de `pipelines.expedicao` de proposito: o pacote da Expedicao proibe
qualquer mencao a e-mail no codigo (defesa contra PII do comprador), e aqui o
endereco e' o da EQUIPE no Slack, usado so' para achar o usuario.

Escopos do bot usados: `users:read.email` (`users.lookupByEmail`) e
`chat:write` (`chat.postMessage`). O token nunca entra em mensagem de erro.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

SLACK_API = "https://slack.com/api"
SLACK_TIMEOUT_SECONDS = 10


class SlackFalhou(RuntimeError):
    pass


def _slack(token: str, metodo: str, campos: dict[str, str]) -> dict:
    """Chamada da Web API. A mensagem de erro nunca leva o token."""
    req = urllib.request.Request(
        f"{SLACK_API}/{metodo}",
        data=urllib.parse.urlencode(campos).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=SLACK_TIMEOUT_SECONDS) as resp:  # noqa: S310
            corpo = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SlackFalhou(f"{metodo}: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise SlackFalhou(f"{metodo}: Slack inacessivel ({type(exc.reason).__name__})") from None
    # A Web API responde 200 com `ok: false` no erro de negocio.
    if not corpo.get("ok"):
        raise SlackFalhou(f"{metodo}: {corpo.get('error', 'erro desconhecido')}")
    return corpo


def enviar_dm(token: str, destinatarios: list[str], texto: str) -> None:
    """Manda `texto` no privado de cada destinatario (e-mail do Slack), pelo bot.

    `chat.postMessage` com o ID do usuario como canal abre a DM com o bot.
    """
    for endereco in destinatarios:
        usuario = _slack(token, "users.lookupByEmail", {"email": endereco})["user"]["id"]
        _slack(token, "chat.postMessage", {"channel": usuario, "text": texto})
