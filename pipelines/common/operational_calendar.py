"""Calendario operacional da Torre — definicao UNICA do dia fechado.

POR QUE ISTO EXISTE
-------------------
Em 28/08/2026 o `full_daily` publicou nove linhas parciais do dia corrente
(cinco TikTok, quatro ML) em `marts.fact_marketplace_daily_performance`. A causa
nao foi um bug de uma fonte: os caminhos incrementais dos conectores terminavam
a janela em `date.today()`, ou seja em D0. Um dia aberto tem pedidos ainda
entrando e status ainda maturando; publicado como fechado, ele vira um numero
que muda sozinho — e, no TikTok, ainda reescreve dias na definicao nova enquanto
o historico esta na antiga.

`date.today()` tem DOIS defeitos aqui, e o segundo e' o pior:

  1. e' D0, nao D-1;
  2. usa o fuso do PROCESSO. O modulo roda hoje num Windows em BRT e amanha num
     worker em UTC. Entre 21h e 00h no Brasil (00h-03h UTC) `date.today()` num
     worker UTC ja devolveria o dia seguinte, deslocando a janela inteira sem
     que ninguem percebesse.

Por isso o dia operacional e' derivado SEMPRE de America/Sao_Paulo, com
`zoneinfo` (biblioteca padrao, zero dependencia nova).

CONTRATO
--------
    operational_today = data atual em America/Sao_Paulo
    last_closed_date  = operational_today - 1 dia
    date_to <= last_closed_date

Este modulo e' a fonte unica dessa regra para a camada de ingestao. NAO importar
helper de `apps/api`: a camada de pipelines nao depende da camada de API.

NOTA DE CONSOLIDACAO: a mesma regra ja existe, replicada, em
`sync_ml_gestao_diaria`, `sync_tiktok_serving` e `ops/refresh_tiktok_daily_contract`.
Este modulo passa a ser a definicao canonica; migrar os tres para ca e' desejavel
mas NAO foi feito nesta rodada, para nao misturar um hotfix de teto de data com
refatoracao dos modulos que carregam a definicao comercial.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

#: Fuso em que a Torre e' operada. Explicito de proposito: ver docstring.
OPERATIONAL_TZ = ZoneInfo("America/Sao_Paulo")


def operational_today(agora: datetime | None = None) -> date:
    """Data corrente em America/Sao_Paulo, independente do fuso do processo.

    `agora` sem timezone e' recusado: um naive datetime seria interpretado no
    fuso do processo, que e' exatamente o que este modulo existe para evitar.
    """
    if agora is None:
        agora = datetime.now(timezone.utc)
    elif agora.tzinfo is None:
        raise ValueError(
            "instante sem timezone: o dia operacional exige fuso explicito "
            "(um naive datetime seria lido no fuso do processo)."
        )
    return agora.astimezone(OPERATIONAL_TZ).date()


def last_closed_date(agora: datetime | None = None) -> date:
    """Ultimo dia FECHADO em America/Sao_Paulo: `operational_today - 1`.

    Teto de TODA janela diaria publicada. O dia corrente nunca e' publicado.
    """
    return operational_today(agora) - timedelta(days=1)


def assert_closed_day(limite: date, agora: datetime | None = None,
                      rotulo: str = "date_to") -> date:
    """Valida `limite <= last_closed_date` e devolve o proprio limite.

    Levanta `ValueError` com a razao explicita. Usado ANTES de qualquer I/O:
    sessao de escrita, registro de audit ou consulta a fonte.
    """
    fechado = last_closed_date(agora)
    if limite > fechado:
        raise ValueError(
            f"{rotulo}={limite.isoformat()} e' posterior ao ultimo dia fechado "
            f"({fechado.isoformat()}) em America/Sao_Paulo. O dia corrente esta "
            "aberto: seus pedidos ainda entram e seus status ainda maturam, "
            "entao publica-lo como fechado gravaria um numero que muda sozinho."
        )
    return limite


def closed_window(days_back: int, agora: datetime | None = None) -> tuple[date, date]:
    """Janela INCLUSIVA de `days_back + 1` dias terminando em D-1.

    Preserva a LARGURA que os conectores já usavam — o unico deslocamento e' do
    teto: antes `(today - days_back, today)`, agora `(D-1 - days_back, D-1)`.
    Nenhum off-by-one novo: a contagem de dias inclusiva e' identica.
    """
    if not isinstance(days_back, int) or isinstance(days_back, bool):
        raise ValueError(f"days_back precisa ser inteiro: recebido {days_back!r}.")
    if days_back < 0:
        raise ValueError(f"days_back nao pode ser negativo: {days_back}.")
    fim = last_closed_date(agora)
    return fim - timedelta(days=days_back), fim
