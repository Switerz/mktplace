"""Blocos do Slack (Block Kit) para as DMs do bot "Torre".

Cada mensagem tem dois corpos:
  * `texto`: uma linha so', e' o que aparece na notificacao e onde nao ha
    blocos (busca, leitores de tela). Obrigatorio pela API quando ha blocos.
  * `blocos`: o que a pessoa le no Slack. Titulo (`header`), secoes curtas com
    um botao que abre a tela certa da Torre (`section` + `accessory`), notas
    pequenas (`context`) e os atalhos no fim (`actions`).

Limites do Slack respeitados aqui: titulo de ate' 150 caracteres, secao de ate'
3000, botao de ate' 75, no maximo 50 blocos por mensagem.

Links: a Torre le canal e marca da URL, entao o botao ja' abre filtrado.
  Expedicao      /expedicao?channel=<shopee|mercadolivre|tiktokshop>&marca=<marca>&visao=detalhe
  Performance    /performance-contas?canal=<shopee|mercado_livre|tiktok>&brands=<marca>
  Metas          /metas
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

URL_TORRE = "https://mktplace-ten.vercel.app"

#: Canal da Expedicao -> canal da tela de Performance (nomes diferentes nas duas).
CANAL_PERFORMANCE = {"shopee": "shopee", "mercadolivre": "mercado_livre", "tiktokshop": "tiktok"}

#: Ponto do canal, o mesmo em todas as mensagens.
EMOJI_CANAL = {"shopee": ":large_orange_circle:", "mercadolivre": ":large_yellow_circle:",
               "tiktokshop": ":black_circle:"}

MAX_BLOCOS = 50
MAX_TITULO = 150
MAX_SECAO = 3000


@dataclass
class Mensagem:
    texto: str
    blocos: list[dict] = field(default_factory=list)


def _url(caminho: str, **params: Optional[str]) -> str:
    qs = urlencode({k: v for k, v in params.items() if v})
    return f"{URL_TORRE}{caminho}" + (f"?{qs}" if qs else "")


def url_expedicao(canal: Optional[str] = None, marca: Optional[str] = None,
                  detalhe: bool = False) -> str:
    return _url("/expedicao", channel=canal, marca=marca, visao="detalhe" if detalhe else None)


def url_performance(canal: Optional[str] = None, marca: Optional[str] = None) -> str:
    return _url("/performance-contas", canal=CANAL_PERFORMANCE.get(canal or ""), brands=marca)


def url_metas() -> str:
    return _url("/metas")


def _corta(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def titulo(texto: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": _corta(texto, MAX_TITULO),
                                       "emoji": True}}


def botao(rotulo: str, url: str, estilo: Optional[str] = None) -> dict:
    b = {"type": "button", "text": {"type": "plain_text", "text": _corta(rotulo, 75), "emoji": True},
         "url": url}
    if estilo in ("primary", "danger"):
        b["style"] = estilo
    return b


def secao(md: str, com_botao: Optional[dict] = None) -> dict:
    s = {"type": "section", "text": {"type": "mrkdwn", "text": _corta(md, MAX_SECAO)}}
    if com_botao:
        s["accessory"] = com_botao
    return s


def campos(*pares: tuple[str, str]) -> dict:
    """Secao em duas colunas: cada par vira "*rotulo*\\nvalor" (ate' 10)."""
    return {"type": "section",
            "fields": [{"type": "mrkdwn", "text": f"*{r}*\n{v}"} for r, v in pares[:10]]}


def nota(*md: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": _corta(t, MAX_SECAO)}
                                            for t in md if t][:10]}


def divisor() -> dict:
    return {"type": "divider"}


def atalhos(*botoes: dict) -> dict:
    return {"type": "actions", "elements": list(botoes)[:25]}


def limitar(blocos: list[dict]) -> list[dict]:
    """Nunca passa de 50 blocos: o resto vira uma nota dizendo que ha' mais."""
    if len(blocos) <= MAX_BLOCOS:
        return blocos
    return blocos[: MAX_BLOCOS - 1] + [nota("_Há mais itens: abra a Torre para ver todos._")]
