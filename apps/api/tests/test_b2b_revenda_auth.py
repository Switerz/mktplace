"""Gate OCTAPRICE-2 — autenticacao das rotas `b2b-revenda`.

Sem rede: tokens assinados com chave RSA LOCAL (tests/auth_testkit.py) e
JWKS trocado por essa chave. A Torre B2B e' a fonte falsa do contrato.

O que estes testes existem para impedir:
  1. agregado B2B chegar a quem nao se autenticou (nem `unavailable` chega);
  2. token forjado passar (outra chave, HS256, `none`, issuer/audience alheios);
  3. permission parecida valer (`b2b_revenda:read:all`, `oracle:read`);
  4. falta de configuracao abrir a rota (tem que FECHAR);
  5. token ou segredo aparecer em resposta ou log.
"""
from __future__ import annotations

import base64
import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import auth
from app.config import settings
from app.main import app
from app.routers import b2b_revenda as rota
from app.services import b2b_revenda_fonte as fonte_mod
from app.services.b2b_revenda_fonte import FonteComCache
from tests import auth_testkit as kit
from tests.test_b2b_revenda_contract import AGORA, BASE, LIDO, ROTAS, FonteFalsa, Relogio

CHAVE_FONTE = "chave-ficticia-da-fonte-NAO-PODE-VAZAR"


@pytest.fixture()
def configurado(monkeypatch):
    kit.configurar(monkeypatch)
    monkeypatch.setattr(rota, "_agora", lambda: AGORA)
    yield
    kit.limpar()


@pytest.fixture()
def falsa():
    return FonteFalsa()


@pytest.fixture()
def com_fonte(falsa):
    cache = FonteComCache(falsa, 1200, monotonico=Relogio(), agora=lambda: LIDO)
    app.dependency_overrides[rota.provedor_fonte] = lambda: (lambda: cache)
    yield
    app.dependency_overrides.pop(rota.provedor_fonte, None)


@pytest.fixture()
def fonte_proibida():
    def fabrica():
        raise AssertionError("a fonte nao pode ser tocada sem autenticacao")

    app.dependency_overrides[rota.provedor_fonte] = lambda: fabrica
    yield
    app.dependency_overrides.pop(rota.provedor_fonte, None)


@pytest.fixture()
def ligado(monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_enabled", True)
    monkeypatch.setattr(settings, "b2b_revenda_canais_kpi", "Mercado Livre,Shopee")


def _get(caminho, headers=None):
    return TestClient(app).get(BASE + caminho, headers=headers or {})


def _nenhum_agregado(r):
    corpo = r.text
    for campo in ("kpis", "linhas", "frescor", "status", "motivo_tecnico"):
        assert f'"{campo}"' not in corpo, campo


# ---------------------------------------------------------------------------
# 1. Nao autenticado: recusado, sem tocar flag nem fonte
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("caminho", ROTAS)
def test_sem_token_e_401_com_www_authenticate(caminho, configurado, ligado, fonte_proibida):
    r = _get(caminho)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith('Bearer realm="torre-mktplace-api"')
    assert r.json() == {"detail": auth.MENSAGENS["token_ausente"]}
    _nenhum_agregado(r)


def test_toda_rota_do_prefixo_exige_autenticacao(configurado, ligado, fonte_proibida):
    paths = [p for p in app.openapi()["paths"] if p.startswith(BASE)]
    assert len(paths) == len(ROTAS)
    for p in paths:
        assert TestClient(app).get(p).status_code == 401, p


@pytest.mark.parametrize("valor", [
    "Basic dXN1YXJpbzpzZW5oYQ==",
    "Bearer",
    "Bearer ",
    "Bearer a b",
    "Token abc",
    "Bearer nao-e-um-jwt",
])
def test_cabecalho_malformado_e_401(valor, configurado, ligado, fonte_proibida):
    assert _get("/resumo", {"Authorization": valor}).status_code == 401


def _alg_none(**kw) -> str:
    claims = json.loads(base64.urlsafe_b64decode(kit.token(**kw).split(".")[1] + "=="))
    cab = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=")
    corpo = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    return f"{cab.decode()}.{corpo.decode()}."


@pytest.mark.parametrize("forjado", [
    lambda: kit.assinado_por_outra_chave(),
    lambda: kit.token(alg="HS256"),
    lambda: _alg_none(),
    lambda: kit.token(iss="https://outro-tenant.us.auth0.com/"),
    lambda: kit.token(iss=f"https://{auth.ALLOWED_AUTH_ISSUER_HOSTNAME}"),  # sem barra final
    lambda: kit.token(aud="https://mktplace-gobeaute.vercel.app/api/mcp"),  # token do MCP
    lambda: kit.token(exp_em=-3600),
    lambda: kit.token(sem=("sub",)),
    lambda: kit.token(sem=("exp",)),
    lambda: kit.token(sem=("iat",)),
    lambda: kit.token(sub=""),
], ids=["outra_chave", "hs256", "alg_none", "issuer_alheio", "issuer_sem_barra",
        "audience_do_mcp", "expirado", "sem_sub", "sem_exp", "sem_iat", "sub_vazio"])
def test_token_forjado_ou_invalido_e_401(forjado, configurado, ligado, fonte_proibida):
    r = _get("/resumo", kit.cabecalho(forjado()))
    assert r.status_code == 401
    assert r.json() == {"detail": auth.MENSAGENS["token_invalido"]}
    _nenhum_agregado(r)


@pytest.mark.parametrize("escopo,permissions", [
    (None, None),
    ("oracle:read", None),
    ("b2b_revenda:read:all", None),
    ("b2b_revenda:reader", None),
    ("openid profile", ["oracle:read"]),
])
def test_sem_a_permission_exata_e_403(escopo, permissions, configurado, ligado, fonte_proibida):
    r = _get("/resumo", kit.cabecalho(kit.token(escopo=escopo, permissions=permissions)))
    assert r.status_code == 403
    assert 'error="insufficient_scope"' in r.headers["www-authenticate"]
    _nenhum_agregado(r)


def test_falha_do_jwks_e_401_e_nao_abre(configurado, ligado, fonte_proibida):
    def resolvedor_quebrado(cfg):
        def _r(tok):
            raise ConnectionError("jwks fora do ar")
        return _r

    app.dependency_overrides[auth.provedor_resolvedor] = lambda: resolvedor_quebrado
    assert _get("/resumo", kit.cabecalho()).status_code == 401


# ---------------------------------------------------------------------------
# 2. Configuracao ausente ou invalida FECHA (503), mesmo com token valido
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("issuer,audience,escopo", [
    ("", kit.AUDIENCE, kit.ESCOPO),
    (kit.ISSUER, "", kit.ESCOPO),
    ("https://outro-tenant.us.auth0.com/", kit.AUDIENCE, kit.ESCOPO),
    ("http://gobeaute-oraculo.us.auth0.com/", kit.AUDIENCE, kit.ESCOPO),
    (kit.ISSUER, "http://mktplace-api.onrender.com/api/v1/b2b-revenda", kit.ESCOPO),
    (kit.ISSUER, kit.AUDIENCE, ""),
    (kit.ISSUER, kit.AUDIENCE, "b2b_revenda:read oracle:read"),
])
def test_configuracao_ausente_ou_invalida_e_503(issuer, audience, escopo, configurado, ligado, fonte_proibida, monkeypatch):
    monkeypatch.setattr(settings, "api_auth_issuer", issuer)
    monkeypatch.setattr(settings, "api_auth_audience", audience)
    monkeypatch.setattr(settings, "b2b_revenda_required_scope", escopo)
    r = _get("/resumo", kit.cabecalho())
    assert r.status_code == 503
    assert r.json() == {"detail": auth.MENSAGENS["auth_nao_configurada"]}
    _nenhum_agregado(r)


def test_padrao_de_fabrica_e_fechado():
    from app.config import Settings

    s = Settings(_env_file=None)
    assert s.api_auth_issuer == "" and s.api_auth_audience == ""
    assert auth.config_auth(s.api_auth_issuer, s.api_auth_audience) is None
    assert s.b2b_revenda_enabled is False


# ---------------------------------------------------------------------------
# 3. Autenticado: passa e cai no comportamento do contrato
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("escopo,permissions", [
    ("b2b_revenda:read", None),
    ("openid b2b_revenda:read profile", None),
    (None, ["b2b_revenda:read"]),
])
def test_autenticado_com_permission_recebe_dados(escopo, permissions, configurado, ligado, com_fonte):
    r = _get("/resumo", kit.cabecalho(kit.token(escopo=escopo, permissions=permissions)))
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["kpis"]["anuncios"] == 1437


def test_audience_em_lista_contendo_a_nossa_e_aceita(configurado, ligado, com_fonte):
    tok = kit.token(aud=[kit.AUDIENCE, f"{kit.ISSUER}userinfo"])
    assert _get("/frescor", kit.cabecalho(tok)).status_code == 200


@pytest.mark.parametrize("caminho", ROTAS)
def test_autenticado_com_flag_desligada_e_unavailable(caminho, configurado, fonte_proibida, monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_enabled", False)
    r = _get(caminho, kit.cabecalho())
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable" and r.json()["motivo_tecnico"] == "feature_flag_desligada"


def test_autenticado_com_segredo_ausente_e_credencial_ausente(configurado, ligado, monkeypatch):
    monkeypatch.setattr(fonte_mod, "_singleton", None)
    monkeypatch.setattr(settings, "b2b_revenda_supabase_url", "https://exemplo.supabase.co")
    monkeypatch.setattr(settings, "b2b_revenda_supabase_key", SecretStr(""))
    r = _get("/resumo", kit.cabecalho())
    assert r.status_code == 200 and r.json()["motivo_tecnico"] == "credencial_ausente"


@pytest.mark.parametrize("motivo", ["fonte_inacessivel", "fonte_lenta", "permissao_negada"])
def test_autenticado_com_fonte_indisponivel_e_unavailable(motivo, configurado, ligado, com_fonte, falsa):
    falsa.falhar["resumo"] = motivo
    r = _get("/resumo", kit.cabecalho())
    assert r.status_code == 200
    assert r.json()["status"] == "unavailable" and r.json()["motivo_tecnico"] == motivo
    assert "kpis" not in r.json()


# ---------------------------------------------------------------------------
# 4. Sigilo: token e segredo nunca em resposta nem log
# ---------------------------------------------------------------------------
def test_token_e_segredo_nunca_em_resposta_nem_log(configurado, ligado, com_fonte, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(settings, "b2b_revenda_supabase_key", SecretStr(CHAVE_FONTE))
    bom, ruim, forjado = kit.token(), kit.assinado_por_outra_chave(), kit.token(escopo="oracle:read")
    respostas = [
        _get("/resumo", kit.cabecalho(bom)),
        _get("/resumo", kit.cabecalho(ruim)),
        _get("/resumo", kit.cabecalho(forjado)),
        _get("/resumo"),
    ]
    assert [r.status_code for r in respostas] == [200, 401, 403, 401]
    for tok in (bom, ruim, forjado):
        assert tok not in caplog.text
        for r in respostas:
            assert tok not in r.text and tok not in str(r.headers)
    assert CHAVE_FONTE not in caplog.text
    assert all(CHAVE_FONTE not in r.text for r in respostas)


def test_rotas_publicas_continuam_como_estavam(configurado):
    # A protecao e' do prefixo b2b-revenda; o resto da API nao muda neste PR.
    assert TestClient(app).get("/health").status_code == 200


# ---------------------------------------------------------------------------
# 5. Regras puras
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bruto,esperado", [
    ("https://gobeaute-oraculo.us.auth0.com/", "https://gobeaute-oraculo.us.auth0.com/"),
    ("https://gobeaute-oraculo.us.auth0.com", "https://gobeaute-oraculo.us.auth0.com/"),
    (" https://gobeaute-oraculo.us.auth0.com/ ", "https://gobeaute-oraculo.us.auth0.com/"),
    ("http://gobeaute-oraculo.us.auth0.com/", None),
    ("https://gobeaute-oraculo.us.auth0.com.evil.com/", None),
    ("https://evil.com/gobeaute-oraculo.us.auth0.com/", None),
    ("https://gobeaute-oraculo.us.auth0.com:8443/", None),
    ("https://u:p@gobeaute-oraculo.us.auth0.com/", None),
    ("https://gobeaute-oraculo.us.auth0.com/x", None),
    ("", None),
])
def test_normalizar_issuer(bruto, esperado):
    assert auth.normalizar_issuer(bruto) == esperado


def test_escopos_efetivos_unem_permissions_e_scope():
    claims = {"permissions": ["a", 1, "", "b"], "scope": "c  d"}
    assert auth.escopos_efetivos(claims) == frozenset({"a", "b", "c", "d"})
    assert auth.escopos_efetivos({}) == frozenset()


def test_jwks_derivado_do_issuer_nunca_configurado():
    cfg = auth.config_auth(kit.ISSUER, kit.AUDIENCE)
    assert cfg.jwks_uri == f"{kit.ISSUER}.well-known/jwks.json"
    assert not hasattr(settings, "api_auth_jwks_uri")
