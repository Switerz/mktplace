"""Alertas disparados de fora: hoje, so' o de vencidos da Shopee.

`POST /api/v1/alertas/expedicao-vencidos` e' chamada a cada 15 min pelo app
`torre-alerta-gatilho` no GoDeploy (`infra/godeploy/torre-alerta-gatilho`). A
regra esta' em `app/services/alerta_vencidos.py`; o envio que nao se repete, em
`app/services/slack_dm.py`.

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
from app.services.alerta_vencidos import EVENTO, avaliar
from app.services.slack_dm import SlackFalhou, enviar_dm_sem_repetir

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
