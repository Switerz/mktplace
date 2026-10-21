"""Autenticacao das rotas protegidas da API — Gate OCTAPRICE-2.

REUSA o mecanismo que ja' existe: o tenant Auth0 corporativo do Oraculo
(`apps/web/src/server/oracle/oauth.ts`). Nenhum sistema de usuario novo. As
regras sao as mesmas do resource server do MCP, agora do lado do FastAPI:

  - issuer: SO' o host allowlisted abaixo, normalizado para `https://<host>/`;
  - algoritmo: SO' RS256 (nada de `none`, nada de HS256 com chave publica);
  - audience: EXATAMENTE a configurada para esta API;
  - permission: comparada como ELEMENTO completo da uniao dos claims
    `permissions` (RBAC) e `scope` — `b2b_revenda:read:all` nao vale;
  - `exp`, `iat`, `iss`, `aud` e `sub` obrigatorios.

FAIL-CLOSED: configuracao ausente ou invalida NEGA (503), token ausente ou
invalido NEGA (401), permission ausente NEGA (403). A negacao acontece ANTES
de qualquer flag, fonte ou cache.

SIGILO: o token nunca e' logado nem ecoado. Falha registra so' o codigo do
vocabulario fechado e o nome da classe da excecao.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import jwt
from fastapi import Depends, Header, HTTPException

logger = logging.getLogger(__name__)

#: Host UNICO aceito como issuer — o mesmo do Oraculo (`oauth.ts`).
ALLOWED_AUTH_ISSUER_HOSTNAME = "gobeaute-oraculo.us.auth0.com"
ALGORITMOS = ["RS256"]
#: Tolerancia de relogio entre Auth0 e Render, em segundos.
LEEWAY_SEGUNDOS = 30
REALM = "torre-mktplace-api"

#: Resolve a chave publica de um token (JWKS em producao; chave local em teste).
ResolvedorDeChave = Callable[[str], Any]


@dataclass(frozen=True)
class ConfigAuth:
    issuer: str
    audience: str
    jwks_uri: str


@dataclass(frozen=True)
class Identidade:
    sujeito: str
    escopos: frozenset[str]


def normalizar_issuer(bruto: str) -> Optional[str]:
    """`https://<host allowlisted>/`, ou `None`. Estrito de proposito."""
    if not bruto or not bruto.strip():
        return None
    try:
        partes = urlsplit(bruto.strip())
    except ValueError:
        return None
    if partes.scheme != "https" or partes.hostname != ALLOWED_AUTH_ISSUER_HOSTNAME:
        return None
    if partes.port is not None or partes.username or partes.password:
        return None
    if partes.path not in ("", "/") or partes.query or partes.fragment:
        return None
    return f"https://{ALLOWED_AUTH_ISSUER_HOSTNAME}/"


def config_auth(issuer: str, audience: str) -> Optional[ConfigAuth]:
    """Configuracao completa e valida, ou `None` (= negar tudo)."""
    iss = normalizar_issuer(issuer)
    aud = (audience or "").strip()
    if iss is None or not aud.startswith("https://") or any(c.isspace() for c in aud):
        return None
    return ConfigAuth(issuer=iss, audience=aud, jwks_uri=f"{iss}.well-known/jwks.json")


def escopos_efetivos(claims: dict) -> frozenset[str]:
    """Uniao de `permissions` (lista) e `scope` (texto separado por espaco)."""
    escopos: set[str] = set()
    perms = claims.get("permissions")
    if isinstance(perms, list):
        escopos |= {p for p in perms if isinstance(p, str) and p}
    scope = claims.get("scope")
    if isinstance(scope, str):
        escopos |= {s for s in scope.split(" ") if s}
    return frozenset(escopos)


class FalhaAuth(Exception):
    """Negacao com codigo do vocabulario fechado."""

    def __init__(self, status: int, codigo: str):
        super().__init__(codigo)
        self.status = status
        self.codigo = codigo


def extrair_bearer(authorization: Optional[str]) -> str:
    if not authorization:
        raise FalhaAuth(401, "token_ausente")
    esquema, _, token = authorization.strip().partition(" ")
    token = token.strip()
    if esquema.lower() != "bearer" or not token or " " in token:
        raise FalhaAuth(401, "token_invalido")
    return token


def validar_token(token: str, cfg: ConfigAuth, chave_para: ResolvedorDeChave) -> Identidade:
    """Valida assinatura, issuer, audience, validade e claims obrigatorios."""
    try:
        cabecalho = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise FalhaAuth(401, "token_invalido") from None
    if cabecalho.get("alg") not in ALGORITMOS:
        raise FalhaAuth(401, "token_invalido")
    try:
        chave = chave_para(token)
    except Exception as exc:  # noqa: BLE001 — JWKS fora, kid desconhecido...
        logger.warning("auth: chave de assinatura indisponivel (%s)", type(exc).__name__)
        raise FalhaAuth(401, "token_invalido") from None
    try:
        claims = jwt.decode(
            token,
            key=chave,
            algorithms=ALGORITMOS,
            audience=cfg.audience,
            issuer=cfg.issuer,
            leeway=LEEWAY_SEGUNDOS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        logger.info("auth: token recusado (%s)", type(exc).__name__)
        raise FalhaAuth(401, "token_invalido") from None
    sujeito = claims.get("sub")
    if not isinstance(sujeito, str) or not sujeito:
        raise FalhaAuth(401, "token_invalido")
    return Identidade(sujeito=sujeito, escopos=escopos_efetivos(claims))


# ---------------------------------------------------------------------------
# Resolvedor de chave em producao: JWKS do issuer, com cache
# ---------------------------------------------------------------------------
_jwks_lock = threading.Lock()
_jwks: dict[str, jwt.PyJWKClient] = {}


def _resolvedor_jwks(cfg: ConfigAuth) -> ResolvedorDeChave:
    with _jwks_lock:
        cliente = _jwks.get(cfg.jwks_uri)
        if cliente is None:
            # URL derivada do issuer allowlisted — nunca configurada a mao.
            cliente = jwt.PyJWKClient(cfg.jwks_uri, cache_keys=True, lifespan=600, timeout=5)
            _jwks[cfg.jwks_uri] = cliente
    return lambda token: cliente.get_signing_key_from_jwt(token).key


def provedor_resolvedor() -> Callable[[ConfigAuth], ResolvedorDeChave]:
    """Dependencia substituivel em teste (chave local no lugar do JWKS)."""
    return _resolvedor_jwks


# ---------------------------------------------------------------------------
# Dependencia FastAPI
# ---------------------------------------------------------------------------
_CABECALHO_401 = {"WWW-Authenticate": f'Bearer realm="{REALM}", error="invalid_token"'}
_CABECALHO_403 = {"WWW-Authenticate": f'Bearer realm="{REALM}", error="insufficient_scope"'}
#: Mensagens CONSTANTES: nada do token ou da excecao volta ao cliente.
MENSAGENS = {
    "auth_nao_configurada": "Autenticação desta rota não configurada.",
    "token_ausente": "Autenticação obrigatória.",
    "token_invalido": "Token inválido ou expirado.",
    "escopo_insuficiente": "Permissão insuficiente para esta rota.",
}


def exigir_escopo(escopo_da_config: Callable[[], str]):
    """Fabrica de dependencia: exige token valido do Auth0 corporativo E a
    permission devolvida por `escopo_da_config()` (lida a cada request, para
    a configuracao valer sem reiniciar e para teste poder trocar)."""

    def _dependencia(
        authorization: Optional[str] = Header(None),
        resolvedor_de=Depends(provedor_resolvedor),
    ) -> Identidade:
        from app.config import settings

        try:
            cfg = config_auth(settings.api_auth_issuer, settings.api_auth_audience)
            escopo = (escopo_da_config() or "").strip()
            if cfg is None or not escopo or " " in escopo:
                raise FalhaAuth(503, "auth_nao_configurada")
            token = extrair_bearer(authorization)
            identidade = validar_token(token, cfg, resolvedor_de(cfg))
            if escopo not in identidade.escopos:
                raise FalhaAuth(403, "escopo_insuficiente")
            return identidade
        except FalhaAuth as falha:
            cabecalhos = _CABECALHO_403 if falha.status == 403 else _CABECALHO_401 if falha.status == 401 else None
            raise HTTPException(falha.status, MENSAGENS[falha.codigo], headers=cabecalhos) from None

    return _dependencia
