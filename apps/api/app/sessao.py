"""Validacao da sessao propria da Torre de Controle (login Google).

O front (Next.js) assina um JWT HS256 no cookie `torre_session`; aqui o FastAPI
so' VALIDA. Mesmo `AUTH_SECRET` na Vercel e no Render.

FAIL-CLOSED: qualquer falha devolve `None` e nunca lanca. Segredo curto conta
como nao configurado. O token nunca e' logado nem ecoado.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import jwt

SESSION_COOKIE = "torre_session"
SESSION_ISSUER = "torre-mktplace"
SESSION_AUDIENCE = "torre-mktplace-api"
SEGREDO_MINIMO = 32
#: Tolerancia de relogio entre Vercel e Render, em segundos.
LEEWAY_SEGUNDOS = 30
DOMINIOS_PADRAO = ("gocase.com", "gobeaute.com.br")


@dataclass(frozen=True)
class Sessao:
    email: str
    nome: Optional[str]


def dominios_permitidos(csv: str) -> tuple[str, ...]:
    """Lista de dominios do CSV (strip + minusculas); vazio -> padrao."""
    itens = tuple(p.strip().lower() for p in (csv or "").split(",") if p.strip())
    return itens or DOMINIOS_PADRAO


def email_permitido(email: Optional[str], dominios: tuple[str, ...]) -> bool:
    """Match EXATO do trecho apos o ultimo `@` (nada de endswith/includes)."""
    if not isinstance(email, str) or "@" not in email:
        return False
    local, _, dominio = email.strip().lower().rpartition("@")
    return bool(local) and dominio in dominios


def validar_sessao(token: Optional[str], segredo: str, dominios: tuple[str, ...]) -> Optional[Sessao]:
    """Sessao valida ou `None`. Nunca lanca."""
    try:
        if not token or not isinstance(segredo, str) or len(segredo) < SEGREDO_MINIMO:
            return None
        claims = jwt.decode(
            token,
            segredo,
            algorithms=["HS256"],
            audience=SESSION_AUDIENCE,
            issuer=SESSION_ISSUER,
            leeway=LEEWAY_SEGUNDOS,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
        email = claims.get("email")
        # Defesa em profundidade: o dominio e' checado de novo na leitura.
        if not email_permitido(email, dominios):
            return None
        nome = claims.get("name")
        return Sessao(email=email.strip().lower(), nome=nome if isinstance(nome, str) else None)
    except Exception:  # noqa: BLE001 — fail-closed, sem vazar detalhe do token
        return None
