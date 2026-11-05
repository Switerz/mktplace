"""DM pelo bot da Torre no Slack (app em `infra/slack/torre-bot`), sem repetir.

Veio de `pipelines/common/slack_dm.py` quando o alerta de vencidos passou a
rodar na API. Fica fora do servico da Expedicao de proposito: la' nao entra
nenhum e-mail (defesa contra PII do comprador), e aqui o endereco e' o da
EQUIPE no Slack, usado so' para achar o usuario.

NAO REPETIR SEM GRAVAR ESTADO
-----------------------------
O cron chama a rota varias vezes sobre a mesma fotografia, e o GoDeploy tenta
de novo apos um 5xx. Cada DM leva `metadata` com a chave da fotografia; antes de
postar, o historico recente da DM e' lido e quem ja' tem a chave e' pulado. Uma
nova tentativa depois de envio parcial so' manda para quem faltou. Se o
historico nao puder ser lido, NADA e' postado: postar sem conferir viraria uma
mensagem a cada 15 min.

Escopos do bot: `users:read.email` (`users.lookupByEmail`), `im:write`
(`conversations.open`), `im:history` (`conversations.history`) e `chat:write`
(`chat.postMessage`). O token nunca entra em mensagem de erro.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

from app.services.slack_blocks import Mensagem

SLACK_API = "https://slack.com/api"
SLACK_TIMEOUT_SECONDS = 10

#: `event_type` da metadata das DMs do alerta de vencidos.
EVENTO_METADATA = "torre_alerta_vencidos"

#: Quantas mensagens recentes da DM sao conferidas antes de postar.
HISTORICO_LIMITE = 20

#: (token, metodo da Web API, campos) -> corpo da resposta.
Chamador = Callable[[str, str, dict[str, str]], dict]


class SlackFalhou(RuntimeError):
    pass


@dataclass(frozen=True)
class ResultadoEnvio:
    enviados: int
    ja_tinham: int


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


def _ja_tem_a_chave(mensagens: list[dict], chave: str) -> bool:
    for m in mensagens:
        meta = m.get("metadata") or {}
        if (meta.get("event_type") == EVENTO_METADATA
                and (meta.get("event_payload") or {}).get("chave") == chave):
            return True
    return False


def _campos_do_post(canal: str, texto: str, blocos: Optional[list[dict]],
                    metadata: str) -> dict[str, str]:
    """`text` vai sempre: e' a notificacao e o que aparece sem blocos."""
    campos = {"channel": canal, "text": texto, "metadata": metadata,
              "unfurl_links": "false", "unfurl_media": "false"}
    if blocos:
        campos["blocks"] = json.dumps(blocos, ensure_ascii=False)
    return campos


def enviar_dm_sem_repetir(
    token: str,
    destinatarios: list[str],
    texto: str,
    chave: str,
    *,
    blocos: Optional[list[dict]] = None,
    chamar: Chamador = _slack,
) -> ResultadoEnvio:
    """Manda `texto` (e `blocos`, quando houver) no privado de cada destinatario (e-mail do Slack) que
    ainda nao recebeu a `chave`.

    Cada destinatario e' independente: uma falha (e-mail de quem saiu da
    empresa, historico ilegivel) nao impede os seguintes. Se algum falhou,
    levanta `SlackFalhou` ao final, para o gatilho tentar de novo; quem ja'
    recebeu e' pulado na nova tentativa. A mensagem do erro nao traz e-mail.
    """
    metadata = json.dumps({"event_type": EVENTO_METADATA, "event_payload": {"chave": chave}})
    enviados = ja_tinham = 0
    falhas: list[str] = []
    for endereco in destinatarios:
        try:
            usuario = chamar(token, "users.lookupByEmail", {"email": endereco})["user"]["id"]
            canal = chamar(token, "conversations.open", {"users": usuario})["channel"]["id"]
            historico = chamar(token, "conversations.history", {
                "channel": canal,
                "limit": str(HISTORICO_LIMITE),
                "include_all_metadata": "true",
            })
            if _ja_tem_a_chave(historico.get("messages") or [], chave):
                ja_tinham += 1
                continue
            chamar(token, "chat.postMessage", _campos_do_post(canal, texto, blocos, metadata))
            enviados += 1
        except SlackFalhou as exc:
            falhas.append(str(exc))
    if falhas:
        raise SlackFalhou(
            f"{len(falhas)} de {len(destinatarios)} destinatario(s) falharam "
            f"(enviados={enviados}, ja_tinham={ja_tinham}): {'; '.join(falhas)}"
        )
    return ResultadoEnvio(enviados=enviados, ja_tinham=ja_tinham)


# ---------------------------------------------------------------------------
# Varios itens por mensagem, cada um com a sua chave (alertas criticos, report)
# ---------------------------------------------------------------------------
#: `event_type` das DMs com itens (`enviar_itens_sem_repetir`).
EVENTO_ITENS = "torre_alerta_itens"

#: Historico conferido para itens: um dia de alertas cabe com folga.
HISTORICO_ITENS_LIMITE = 100


@dataclass(frozen=True)
class Item:
    """Um aviso com identidade propria (`chave`) dentro de uma mensagem.

    `dados` leva o objeto de origem (celula de LDR, leitura de vencidos...)
    para quem monta os blocos; nao entra na igualdade.
    """
    chave: str
    texto: str
    dados: Any = field(default=None, compare=False, repr=False)


def _chaves_ja_enviadas(mensagens: list[dict]) -> set[str]:
    chaves: set[str] = set()
    for m in mensagens:
        meta = m.get("metadata") or {}
        if meta.get("event_type") != EVENTO_ITENS:
            continue
        for c in (meta.get("event_payload") or {}).get("chaves") or []:
            if isinstance(c, str):
                chaves.add(c)
    return chaves


def enviar_itens_sem_repetir(
    token: str,
    destinatarios: list[str],
    itens: list[Item],
    montar: Callable[[list[Item]], Union[str, Mensagem]],
    *,
    chamar: Chamador = _slack,
) -> ResultadoEnvio:
    """Para cada destinatario, manda UMA mensagem com os itens cuja chave ele
    ainda nao recebeu (`montar(novos)` faz o texto). Quem ja' tem todas conta
    em `ja_tinham`.

    Mesmas garantias de `enviar_dm_sem_repetir`: historico ilegivel = nada
    postado para aquela pessoa; falha de um nao bloqueia os outros; no fim,
    `SlackFalhou` se alguem falhou (sem e-mail na mensagem).
    """
    enviados = ja_tinham = 0
    falhas: list[str] = []
    for endereco in destinatarios:
        try:
            usuario = chamar(token, "users.lookupByEmail", {"email": endereco})["user"]["id"]
            canal = chamar(token, "conversations.open", {"users": usuario})["channel"]["id"]
            historico = chamar(token, "conversations.history", {
                "channel": canal,
                "limit": str(HISTORICO_ITENS_LIMITE),
                "include_all_metadata": "true",
            })
            ja = _chaves_ja_enviadas(historico.get("messages") or [])
            novos = [i for i in itens if i.chave not in ja]
            if not novos:
                ja_tinham += 1
                continue
            metadata = json.dumps({"event_type": EVENTO_ITENS,
                                   "event_payload": {"chaves": [i.chave for i in novos]}})
            m = montar(novos)
            texto, blocos = (m.texto, m.blocos) if isinstance(m, Mensagem) else (m, None)
            chamar(token, "chat.postMessage", _campos_do_post(canal, texto, blocos, metadata))
            enviados += 1
        except SlackFalhou as exc:
            falhas.append(str(exc))
    if falhas:
        raise SlackFalhou(
            f"{len(falhas)} de {len(destinatarios)} destinatario(s) falharam "
            f"(enviados={enviados}, ja_tinham={ja_tinham}): {'; '.join(falhas)}"
        )
    return ResultadoEnvio(enviados=enviados, ja_tinham=ja_tinham)
