# tests/test_gate_sessao.py — gate de sessao (ASGI puro), tokens sinteticos, sem rede
import logging
import time

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import settings
from app.gate_sessao import GateDeSessao
from app.sessao import SESSION_AUDIENCE, SESSION_ISSUER

SEGREDO = "s" * 32
CHAVE = "k" * 32


def tok(email="joao@gocase.com", segredo=SEGREDO):
    agora = int(time.time())
    return jwt.encode(
        {"email": email, "name": "Joao", "iss": SESSION_ISSUER, "aud": SESSION_AUDIENCE,
         "iat": agora, "exp": agora + 300},
        segredo, algorithm="HS256",
    )


def _app():
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI()

    @app.get("/api/v1/x")
    def x(request: Request):
        return {"ator": getattr(request.state, "ator", None)}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/api/v1/alertas/y")
    def alertas():
        return {"ok": True}

    @app.get("/api/v1/b2b-revenda/z")
    def b2b():
        return {"ok": True}

    @app.get("/api/v1/stream")
    def stream():
        def gen():
            yield "a"
            yield "b"
            yield "c"
        return StreamingResponse(gen())

    app.add_middleware(GateDeSessao)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                       allow_methods=["*"], allow_headers=["*"])
    return app


def _cfg(monkeypatch, modo="enforce", segredo=SEGREDO, chave=CHAVE):
    monkeypatch.setattr(settings, "torre_auth_mode", modo)
    monkeypatch.setattr(settings, "auth_secret", SecretStr(segredo))
    monkeypatch.setattr(settings, "allowed_email_domains", "gocase.com,gobeaute.com.br")
    monkeypatch.setattr(settings, "torre_service_key", SecretStr(chave))
    return TestClient(_app())


@pytest.fixture
def cli(monkeypatch):
    return _cfg(monkeypatch)


@pytest.fixture
def cli_off(monkeypatch):
    return _cfg(monkeypatch, modo="off")


@pytest.fixture
def cli_report(monkeypatch):
    return _cfg(monkeypatch, modo="report")


@pytest.fixture
def cli_modo_typo(monkeypatch):
    return _cfg(monkeypatch, modo="enforcee")


@pytest.fixture
def cli_sem_segredo(monkeypatch):
    return _cfg(monkeypatch, segredo="")


@pytest.fixture
def cli_chave_curta(monkeypatch):
    return _cfg(monkeypatch, chave="curta")


def test_off_libera_sem_cookie(cli_off):
    assert cli_off.get("/api/v1/x").status_code == 200


def test_enforce_sem_cookie_401_com_header(cli):
    r = cli.get("/api/v1/x")
    assert r.status_code == 401
    assert r.headers["X-Torre-Auth"] == "sessao-ausente"
    assert r.headers["Cache-Control"] == "no-store"
    assert r.json() == {"detail": "Autenticação obrigatória."}


def test_enforce_cookie_valido_injeta_ator(cli):
    r = cli.get("/api/v1/x", cookies={"torre_session": tok("Joao@Gocase.com")})
    assert r.json() == {"ator": "joao@gocase.com"}


def test_publicos_passam(cli):
    assert cli.get("/health").status_code == 200
    assert cli.post("/api/v1/alertas/y").status_code == 200
    assert cli.get("/api/v1/b2b-revenda/z").status_code == 200
    assert cli.get("/healthz").status_code in (401, 404)  # prefixo de /health nao vale


def test_options_passa(cli):
    r = cli.options("/api/v1/x", headers={"Origin": "http://localhost:3000",
                                          "Access-Control-Request-Method": "GET"})
    assert r.status_code != 401


def test_chave_servico(cli):
    assert cli.get("/api/v1/x", headers={"x-torre-service-key": "k" * 32}).json() == {"ator": "servico:oraculo-mcp"}
    assert cli.get("/api/v1/x", headers={"x-torre-service-key": "errada"}).status_code == 401


def test_chave_servico_curta_no_servidor_nao_vale(cli_chave_curta):
    assert cli_chave_curta.get("/api/v1/x", headers={"x-torre-service-key": "curta"}).status_code == 401


def test_modo_desconhecido_vira_enforce(cli_modo_typo):
    assert cli_modo_typo.get("/api/v1/x").status_code == 401


def test_segredo_ausente_503(cli_sem_segredo):
    r = cli_sem_segredo.get("/api/v1/x")
    assert r.status_code == 503
    assert r.json() == {"detail": "Autenticação da Torre não configurada."}
    assert "X-Torre-Auth" not in r.headers


def test_report_loga_e_libera(cli_report, caplog):
    with caplog.at_level(logging.WARNING):
        assert cli_report.get("/api/v1/x?token=abc").status_code == 200
    assert "auth_report negaria" in caplog.text and "token=abc" not in caplog.text


def test_stream_passa_inteiro(cli):
    assert cli.get("/api/v1/stream", cookies={"torre_session": tok()}).text == "abc"


def test_nada_sensivel_no_log(cli, caplog):
    t = tok()
    with caplog.at_level(logging.DEBUG):
        cli.get("/api/v1/x", cookies={"torre_session": t})
    assert t not in caplog.text


def test_main_registra_gate_dentro_do_cors():
    from app.main import app
    nomes = [m.cls.__name__ for m in app.user_middleware]
    assert nomes.index("CORSMiddleware") < nomes.index("GateDeSessao")  # user_middleware[0] = mais externo
