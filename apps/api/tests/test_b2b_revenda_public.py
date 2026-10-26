"""Gate B2B-REVENDA-ENTREGA-PUBLICA-1 — modo `public_readonly`.

O que estes testes existem para impedir:
  1. a rota abrir sem o valor EXATO (padrao e erro de digitacao FECHAM);
  2. o modo publico furar a flag, a allowlist de filtros ou o contrato sem
     dado de cliente;
  3. volume sem limite chegar a fonte (rate limit por cliente e global);
  4. segredo aparecer em resposta ou log.
"""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings, settings
from app.main import app
from app.rate_limit import JanelaDeslizante
from app.routers import b2b_revenda as rota
from app.services.b2b_revenda_fonte import FonteComCache
from tests.test_b2b_revenda_contract import AGORA, BASE, LIDO, ROTAS, FonteFalsa, Relogio

CHAVE_FONTE = "chave-ficticia-da-fonte-NAO-PODE-VAZAR"


@pytest.fixture(autouse=True)
def _limpo(monkeypatch):
    monkeypatch.setattr(rota, "_limites", {})
    monkeypatch.setattr(rota, "_agora", lambda: AGORA)
    # Autenticacao NAO configurada: no modo autenticado, tudo fecha em 503.
    monkeypatch.setattr(settings, "api_auth_issuer", "")
    monkeypatch.setattr(settings, "api_auth_audience", "")
    yield


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
def publico(monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_access_mode", "public_readonly")
    monkeypatch.setattr(settings, "b2b_revenda_enabled", True)
    monkeypatch.setattr(settings, "b2b_revenda_canais_kpi", "Mercado Livre,Shopee")


def _get(caminho, **kw):
    return TestClient(app).get(BASE + caminho, **kw)


# ---------------------------------------------------------------------------
# 1. So' o valor exato abre
# ---------------------------------------------------------------------------
def test_padrao_e_autenticado():
    assert Settings(_env_file=None).b2b_revenda_access_mode == "authenticated"


@pytest.mark.parametrize("modo", ["authenticated", "", "public", "publico", "public-readonly", "public_read", "readonly"])
def test_valor_diferente_de_public_readonly_continua_fechado(modo, monkeypatch, com_fonte, falsa):
    monkeypatch.setattr(settings, "b2b_revenda_access_mode", modo)
    monkeypatch.setattr(settings, "b2b_revenda_enabled", True)
    r = _get("/resumo")
    assert r.status_code == 503  # auth nao configurada = fecha
    assert falsa.chamadas == []


@pytest.mark.parametrize("modo", ["public_readonly", " PUBLIC_READONLY ", "Public_ReadOnly"])
def test_public_readonly_normaliza_caixa_e_espacos(modo, monkeypatch, com_fonte):
    monkeypatch.setattr(settings, "b2b_revenda_access_mode", modo)
    monkeypatch.setattr(settings, "b2b_revenda_enabled", True)
    monkeypatch.setattr(settings, "b2b_revenda_canais_kpi", "Mercado Livre,Shopee")
    assert _get("/resumo").status_code == 200


# ---------------------------------------------------------------------------
# 2. Modo publico: dados sem token, mas com todas as outras travas
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("caminho", ROTAS)
def test_publico_responde_sem_token(caminho, publico, com_fonte):
    r = _get(caminho)
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("caminho", ROTAS)
def test_publico_com_flag_desligada_e_unavailable_sem_fonte(caminho, publico, monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_enabled", False)

    def proibida():
        raise AssertionError("fonte nao pode ser tocada com a flag desligada")

    app.dependency_overrides[rota.provedor_fonte] = lambda: proibida
    try:
        r = _get(caminho)
    finally:
        app.dependency_overrides.pop(rota.provedor_fonte, None)
    assert r.status_code == 200
    assert r.json()["motivo_tecnico"] == "feature_flag_desligada"


def test_publico_mantem_allowlist_de_filtros(publico, com_fonte, falsa):
    assert _get("/resumo", params={"canais": "Amazon"}).status_code == 422
    assert _get("/resumo", params={"severidades": "x"}).status_code == 422
    assert falsa.chamadas == []


def test_publico_ignora_filtro_de_cliente_e_vendedor(publico, com_fonte, falsa):
    r = _get("/resumo", params={"vendedores": "X", "clientes": "123", "cnpjs": "1"})
    assert r.status_code == 200
    for chamada in falsa.chamadas:
        if len(chamada) > 1:
            assert not hasattr(chamada[1], "vendedores") and not hasattr(chamada[1], "cnpjs")


def test_publico_so_tem_get(publico):
    paths = app.openapi()["paths"]
    assert all(set(paths[p]) == {"get"} for p in paths if p.startswith(BASE))
    assert TestClient(app).post(BASE + "/resumo").status_code == 405


def test_publico_sem_credencial_da_fonte_e_unavailable(publico, monkeypatch):
    from app.services import b2b_revenda_fonte as fonte_mod

    monkeypatch.setattr(fonte_mod, "_singleton", None)
    monkeypatch.setattr(settings, "b2b_revenda_supabase_url", "")
    monkeypatch.setattr(settings, "b2b_revenda_supabase_key", SecretStr(""))
    r = _get("/resumo")
    assert r.status_code == 200 and r.json()["motivo_tecnico"] == "credencial_ausente"


# ---------------------------------------------------------------------------
# 3. Rate limit
# ---------------------------------------------------------------------------
def test_limite_por_cliente_devolve_429_com_retry_after(publico, com_fonte, monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_rate_limit_per_minute", 3)
    h = {"X-Forwarded-For": "203.0.113.7"}
    assert [_get("/frescor", headers=h).status_code for _ in range(3)] == [200, 200, 200]
    r = _get("/frescor", headers=h)
    assert r.status_code == 429
    assert r.json() == {"detail": rota.MSG_LIMITE}
    assert 1 <= int(r.headers["retry-after"]) <= 60
    # Outro cliente nao e' afetado.
    assert _get("/frescor", headers={"X-Forwarded-For": "198.51.100.9"}).status_code == 200


def test_limite_usa_o_ultimo_ip_do_x_forwarded_for(publico, com_fonte, monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_rate_limit_per_minute", 2)
    # O cliente forja o PRIMEIRO elemento; o proxy anexa o real no fim.
    for forjado in ("1.1.1.1", "2.2.2.2"):
        assert _get("/frescor", headers={"X-Forwarded-For": f"{forjado}, 203.0.113.7"}).status_code == 200
    assert _get("/frescor", headers={"X-Forwarded-For": "3.3.3.3, 203.0.113.7"}).status_code == 429


def test_limite_global_protege_a_fonte(publico, com_fonte, monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_rate_limit_global_per_minute", 10)
    codigos = [_get("/frescor", headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code for i in range(12)]
    assert codigos[:10] == [200] * 10 and codigos[10:] == [429, 429]


def test_janela_deslizante_libera_depois_da_janela():
    t = [1000.0]
    lim = JanelaDeslizante(2, 60.0, relogio=lambda: t[0])
    assert lim.verificar("a").permitido and lim.verificar("a").permitido
    negado = lim.verificar("a")
    assert not negado.permitido and negado.retry_after_s == 60
    t[0] += 60.1
    assert lim.verificar("a").permitido


def test_janela_tem_teto_de_chaves():
    lim = JanelaDeslizante(5, 60.0, max_chaves=3)
    for i in range(10):
        lim.verificar(f"ip-{i}")
    assert len(lim._hits) == 3


def test_modo_autenticado_nao_aplica_limite_publico(monkeypatch):
    monkeypatch.setattr(settings, "b2b_revenda_rate_limit_per_minute", 1)
    for _ in range(3):
        assert _get("/frescor").status_code == 503  # fecha por auth, nao por 429


# ---------------------------------------------------------------------------
# 4. Sigilo
# ---------------------------------------------------------------------------
def test_publico_nunca_expoe_segredo(publico, com_fonte, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(settings, "b2b_revenda_supabase_key", SecretStr(CHAVE_FONTE))
    monkeypatch.setattr(settings, "b2b_revenda_supabase_url", "https://projeto-privado.supabase.co")
    for caminho in ROTAS:
        r = _get(caminho)
        assert CHAVE_FONTE not in r.text and "projeto-privado" not in r.text
        assert CHAVE_FONTE not in str(r.headers)
    assert CHAVE_FONTE not in caplog.text and "projeto-privado" not in caplog.text
