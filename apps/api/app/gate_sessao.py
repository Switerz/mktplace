"""Gate de sessao da Torre de Controle (login Google).

Middleware ASGI PURO (sem BaseHTTPMiddleware: nao bufferiza streaming). E' a
fronteira real de seguranca: sem cookie `torre_session` valido, a API nega.

Modos (`TORRE_AUTH_MODE`): `off` (nao faz nada), `report` (decide como o
enforce, mas so' loga e deixa passar) e `enforce` (nega). Modo desconhecido
conta como `enforce` (fail-closed). Nenhum token/cookie/chave vai a log ou resposta.
"""
from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.sessao import SEGREDO_MINIMO, SESSION_COOKIE, dominios_permitidos, validar_sessao

logger = logging.getLogger(__name__)

#: Igualdade para os que nao terminam em "/", prefixo para os que terminam.
CAMINHOS_PUBLICOS = ("/health", "/ready", "/api/v1/alertas/", "/api/v1/b2b-revenda/")
SERVICE_KEY_HEADER = "x-torre-service-key"
ATOR_SERVICO = "servico:oraculo-mcp"
HEADER_AUTH_NEGADA = "X-Torre-Auth"
_MODOS = {"off", "report", "enforce"}


@dataclass(frozen=True)
class Decisao:
    liberar: bool
    status: int = 200
    codigo: str = ""
    ator: Optional[str] = None


def _publico(caminho: str) -> bool:
    for p in CAMINHOS_PUBLICOS:
        if p.endswith("/"):
            if caminho.startswith(p):
                return True
        elif caminho == p:
            return True
    return False


def decidir(
    *,
    metodo: str,
    caminho: str,
    cookie_sessao: Optional[str],
    chave_servico: Optional[str],
    modo: str,
    segredo: str,
    dominios: tuple[str, ...],
    chave_servico_esperada: str,
) -> Decisao:
    """Funcao pura: decide liberar ou negar."""
    if modo == "off":
        return Decisao(True)
    if metodo == "OPTIONS" or _publico(caminho):
        return Decisao(True)
    # Modo desconhecido conta como enforce (o chamador ja' trata o report).
    if len(segredo or "") < SEGREDO_MINIMO:
        return Decisao(False, 503, "auth_nao_configurada")
    if (
        chave_servico
        and len(chave_servico_esperada or "") >= SEGREDO_MINIMO
        and hmac.compare_digest(chave_servico.encode("utf-8"), chave_servico_esperada.encode("utf-8"))
    ):
        return Decisao(True, ator=ATOR_SERVICO)
    sessao = validar_sessao(cookie_sessao, segredo, dominios)
    if sessao is not None:
        return Decisao(True, ator=sessao.email)
    return Decisao(False, 401, "sessao_ausente")


def _cookie(headers: list[tuple[bytes, bytes]], nome: str) -> Optional[str]:
    for k, v in headers:
        if k == b"cookie":
            for parte in v.decode("latin-1").split(";"):
                n, _, valor = parte.strip().partition("=")
                if n == nome:
                    return valor
    return None


def _header(headers: list[tuple[bytes, bytes]], nome: str) -> Optional[str]:
    alvo = nome.encode("latin-1")
    for k, v in headers:
        if k.lower() == alvo:
            return v.decode("latin-1")
    return None


async def _negar(send, status: int) -> None:
    if status == 503:
        corpo = {"detail": "Autenticação da Torre não configurada."}
    else:
        corpo = {"detail": "Autenticação obrigatória."}
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(dados)).encode()),
        (b"cache-control", b"no-store"),
    ]
    if status == 401:
        headers.append((HEADER_AUTH_NEGADA.lower().encode(), b"sessao-ausente"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": dados})


class GateDeSessao:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        modo = (settings.torre_auth_mode or "off").strip().lower()
        if modo not in _MODOS:
            modo = "enforce"
        if modo == "off":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        decisao = decidir(
            metodo=scope.get("method", ""),
            caminho=scope.get("path", ""),
            cookie_sessao=_cookie(headers, SESSION_COOKIE),
            chave_servico=_header(headers, SERVICE_KEY_HEADER),
            modo=modo,
            segredo=settings.auth_secret.get_secret_value().strip(),
            dominios=dominios_permitidos(settings.allowed_email_domains),
            chave_servico_esperada=settings.torre_service_key.get_secret_value().strip(),
        )

        if decisao.liberar:
            if decisao.ator:
                scope.setdefault("state", {})["ator"] = decisao.ator
            await self.app(scope, receive, send)
            return

        if modo == "report":
            ua = (_header(headers, "user-agent") or "")[:80]
            logger.warning(
                "auth_report negaria metodo=%s caminho=%s codigo=%s ua=%s",
                scope.get("method", ""), scope.get("path", ""), decisao.codigo, ua,
            )
            await self.app(scope, receive, send)
            return

        await _negar(send, decisao.status)
