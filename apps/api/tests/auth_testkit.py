"""Kit de teste da autenticacao (Gate OCTAPRICE-2). Sem rede.

Gera um par RSA LOCAL, emite tokens no formato do Auth0 corporativo e troca o
resolvedor de JWKS por essa chave. Nenhum token real, nenhum tenant tocado.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app import auth
from app.config import settings
from app.main import app

ISSUER = f"https://{auth.ALLOWED_AUTH_ISSUER_HOSTNAME}/"
AUDIENCE = "https://mktplace-api.onrender.com/api/v1/b2b-revenda"
ESCOPO = "b2b_revenda:read"

_CHAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OUTRA_CHAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(
    *,
    escopo: Optional[str] = ESCOPO,
    permissions: Optional[list[str]] = None,
    iss: str = ISSUER,
    aud: Any = AUDIENCE,
    sub: Optional[str] = "google-oauth2|teste",
    exp_em: int = 300,
    alg: str = "RS256",
    chave=None,
    sem: tuple[str, ...] = (),
) -> str:
    agora = int(time.time())
    claims: dict[str, Any] = {"iss": iss, "aud": aud, "sub": sub, "iat": agora, "exp": agora + exp_em}
    if escopo is not None:
        claims["scope"] = escopo
    if permissions is not None:
        claims["permissions"] = permissions
    for c in sem:
        claims.pop(c, None)
    if alg == "HS256":
        return jwt.encode(claims, "segredo-simetrico-de-teste-com-32-bytes!", algorithm="HS256")
    return jwt.encode(claims, chave or _CHAVE, algorithm=alg, headers={"kid": "teste"})


def assinado_por_outra_chave(**kw) -> str:
    return token(chave=_OUTRA_CHAVE, **kw)


def configurar(monkeypatch, *, issuer: str = ISSUER, audience: str = AUDIENCE) -> None:
    """Auth configurada como em producao, com a chave publica LOCAL no lugar do JWKS."""
    monkeypatch.setattr(settings, "api_auth_issuer", issuer)
    monkeypatch.setattr(settings, "api_auth_audience", audience)
    monkeypatch.setattr(settings, "b2b_revenda_required_scope", ESCOPO)
    publica = _CHAVE.public_key()
    app.dependency_overrides[auth.provedor_resolvedor] = lambda: (lambda cfg: (lambda tok: publica))


def limpar() -> None:
    app.dependency_overrides.pop(auth.provedor_resolvedor, None)


def cabecalho(tok: Optional[str] = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok or token()}"}
