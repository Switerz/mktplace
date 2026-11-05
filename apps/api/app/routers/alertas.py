"""Alertas do bot "Torre", disparados pelos crons do GoDeploy.

Mapa completo: `docs/torre_bot_alertas.md`. Todas chamadas pelo app
`torre-alerta-gatilho` (`infra/godeploy/torre-alerta-gatilho`):

  POST /api/v1/alertas/expedicao-vencidos  A1, a cada 15 min (`alerta_vencidos.py`)
  POST /api/v1/alertas/criticos            A2 + A3, a cada 15 min (`torre_alertas.py`)
  POST /api/v1/alertas/report-diario       R1, as 8h de Brasilia (`torre_report.py`)

O envio que nao se repete mora em `app/services/slack_dm.py`.

Fica fora de `app/routers/expedicao.py`, que e' SOMENTE GET por contrato.

Ordem das checagens: nada e' lido antes de a anterior passar.
  1. segredo nao configurado -> 503 (rota desligada);
  2. `X-Alerta-Token` ausente ou diferente -> 401;
  3. banco indisponivel -> 503;
  4. sem `simular` e sem token do Slack ou destinatarios -> 503;
  5. Slack recusou -> 502 (o GoDeploy tenta de novo; quem ja' recebeu e' pulado).
Mensagens FIXAS: nada da entrada nem da excecao volta na resposta.
"""
from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services import torre_alertas, torre_report
from app.services.alerta_vencidos import EVENTO, avaliar
from app.services.slack_dm import (
    Item,
    SlackFalhou,
    enviar_dm_sem_repetir,
    enviar_itens_sem_repetir,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/alertas", tags=["alertas"])


def _autorizado(recebido: Optional[str]) -> bool:
    esperado = settings.alerta_webhook_secret.get_secret_value()
    return bool(recebido) and hmac.compare_digest(
        recebido.encode("utf-8"), esperado.encode("utf-8"))


@router.post("/expedicao-vencidos")
def disparar_alerta_vencidos(
    simular: bool = Query(False, description="Avalia e devolve a mensagem sem enviar."),
    x_alerta_token: Optional[str] = Header(None, alias="X-Alerta-Token"),
    db: Session = Depends(get_db),
) -> dict:
    if not settings.alerta_webhook_secret.get_secret_value():
        raise HTTPException(503, "Alerta nao configurado.")
    if not _autorizado(x_alerta_token):
        raise HTTPException(401, "Nao autorizado.")
    if db is None:
        raise HTTPException(503, "Banco de dados indisponivel.")
    token = settings.slack_bot_token.get_secret_value()
    destinatarios = settings.expedicao_alerta_destinatarios_list
    if not simular and not (token and destinatarios):
        raise HTTPException(503, "Envio nao configurado.")

    a = avaliar(db, agora=datetime.now(timezone.utc))
    corpo = {"canal": a.canal, "situacao": a.situacao, "evento": a.evento,
             "enviados": 0, "ja_tinham": 0}
    if simular:
        corpo["mensagem"] = a.mensagem
        return corpo
    if a.situacao != EVENTO:
        return corpo
    try:
        r = enviar_dm_sem_repetir(token, destinatarios, a.mensagem, a.chave)
    except SlackFalhou as exc:
        # `SlackFalhou` nunca carrega o token; mesmo assim so' o metodo/erro vai ao log.
        logger.warning("alerta de vencidos: Slack falhou (%s)", exc)
        raise HTTPException(502, "Slack recusou o envio.") from None
    corpo["enviados"], corpo["ja_tinham"] = r.enviados, r.ja_tinham
    logger.info("alerta de vencidos: %s, enviados=%d, ja_tinham=%d",
                a.evento, r.enviados, r.ja_tinham)
    return corpo


def _conferir(x_alerta_token: Optional[str], db, simular: bool) -> tuple[str, list[str]]:
    """Checagens 1 a 4 do docstring, na mesma ordem. Devolve (token, destinatarios)."""
    if not settings.alerta_webhook_secret.get_secret_value():
        raise HTTPException(503, "Alerta nao configurado.")
    if not _autorizado(x_alerta_token):
        raise HTTPException(401, "Nao autorizado.")
    if db is None:
        raise HTTPException(503, "Banco de dados indisponivel.")
    token = settings.slack_bot_token.get_secret_value()
    destinatarios = settings.expedicao_alerta_destinatarios_list
    if not simular and not (token and destinatarios):
        raise HTTPException(503, "Envio nao configurado.")
    return token, destinatarios


def _enviar(nome: str, token: str, destinatarios: list[str], itens: list[Item], montar) -> dict:
    try:
        r = enviar_itens_sem_repetir(token, destinatarios, itens, montar)
    except SlackFalhou as exc:
        logger.warning("%s: Slack falhou (%s)", nome, exc)
        raise HTTPException(502, "Slack recusou o envio.") from None
    logger.info("%s: itens=%d, enviados=%d, ja_tinham=%d", nome, len(itens), r.enviados, r.ja_tinham)
    return {"enviados": r.enviados, "ja_tinham": r.ja_tinham}


@router.post("/criticos")
def disparar_alertas_criticos(
    simular: bool = Query(False, description="Avalia e devolve a mensagem sem enviar."),
    x_alerta_token: Optional[str] = Header(None, alias="X-Alerta-Token"),
    db: Session = Depends(get_db),
) -> dict:
    """A2 (LDR acima do teto) e A3 (performance oficial da Kokeshi)."""
    token, destinatarios = _conferir(x_alerta_token, db, simular)
    a = torre_alertas.avaliar_criticos(db, agora=datetime.now(timezone.utc))
    corpo = {"itens": len(a.itens), "chaves": [i.chave for i in a.itens],
             "canais_indisponiveis": a.canais_indisponiveis,
             "saude_indisponivel": a.saude_indisponivel, "enviados": 0, "ja_tinham": 0}
    if simular:
        corpo["mensagem"] = torre_alertas.montar_mensagem_criticos(a.itens) if a.itens else None
        return corpo
    if not a.itens:
        return corpo
    corpo.update(_enviar("alertas criticos", token, destinatarios, a.itens,
                         torre_alertas.montar_mensagem_criticos))
    return corpo


@router.post("/report-diario")
def disparar_report_diario(
    simular: bool = Query(False, description="Monta e devolve o report sem enviar."),
    x_alerta_token: Optional[str] = Header(None, alias="X-Alerta-Token"),
    db: Session = Depends(get_db),
) -> dict:
    """R1: resumo de ontem (GMV, avancos, problemas). Uma vez por dia por pessoa."""
    token, destinatarios = _conferir(x_alerta_token, db, simular)
    try:
        dados = torre_report.ler_dados(db, agora=datetime.now(timezone.utc))
    except Exception as exc:  # noqa: BLE001 — motivo fixo; o texto nunca sai
        logger.warning("report diario: leitura falhou (%s)", type(exc).__name__)
        raise HTTPException(503, "Dados indisponiveis.") from None
    rep = torre_report.compor_report(dados)
    texto = torre_report.montar_mensagem(rep)
    corpo = {"dia": rep.ontem.isoformat(), "avancos": len(rep.avancos),
             "problemas": len(rep.problemas), "enviados": 0, "ja_tinham": 0}
    if simular:
        corpo["mensagem"] = texto
        return corpo
    item = Item(torre_report.chave_do_report(rep), texto)
    corpo.update(_enviar("report diario", token, destinatarios, [item], lambda _novos: texto))
    return corpo
