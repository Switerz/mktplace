"""METAS-1 — metas mensais de GMV (migration 031, servico, rotas).

Sem banco: a regra de alteracao (`planejar`) e' pura, e as rotas sao
exercitadas com o servico substituido. O SQL real e' coberto pelo teste de
integracao opcional em `test_metas_integracao.py`.
"""
from __future__ import annotations

import ast
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.main import app
from app.routers import metas as rota
from app.schemas.metas import Canal, Marca
from app.services import metas_service as svc

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "031_create_meta_gmv_mensal.py"


def _migration():
    """Constantes da 031 sem importar `alembic`: a pasta `apps/api/alembic`
    sombreia o pacote real quando os testes rodam daqui. So' a linha do import
    sai; o resto do modulo executa como esta."""
    fonte = MIGRATION.read_text(encoding="utf-8").replace("from alembic import op\n", "")
    ns: dict = {}
    exec(compile(fonte, str(MIGRATION), "exec"), ns)
    return SimpleNamespace(**ns)


# ---------------------------------------------------------------------------
# Migration x servico x contrato: a mesma lista fechada nos tres
# ---------------------------------------------------------------------------

def test_listas_fechadas_iguais_na_migration_no_servico_e_no_contrato():
    m = _migration()
    assert m.MARCAS == svc.MARCAS
    assert m.CANAIS == svc.CANAIS
    assert get_args(Marca) == svc.MARCAS
    assert get_args(Canal) == svc.CANAIS


def test_ddl_escreve_as_listas_literalmente_no_check():
    ddl = " ".join(_migration().UPGRADE_SQL)
    marcas = re.search(r"ck_mgm_brand CHECK \(brand IN \(([^)]*)\)\)", ddl).group(1)
    canais = re.search(r"ck_mgm_channel CHECK \(channel IN \(([^)]*)\)\)", ddl).group(1)
    assert tuple(x.strip(" '") for x in marcas.split(",")) == svc.MARCAS
    assert tuple(x.strip(" '") for x in canais.split(",")) == svc.CANAIS


def test_migration_encadeia_na_030_e_cria_so_as_duas_tabelas():
    m = _migration()
    assert (m.revision, m.down_revision) == ("031", "030")
    criadas = re.findall(r"CREATE TABLE ([a-z_.]+)", " ".join(m.UPGRADE_SQL))
    assert criadas == ["marts.meta_gmv_mensal", "marts.meta_gmv_mensal_historico"]
    assert "INSERT" not in " ".join(m.UPGRADE_SQL).upper()


def test_teto_do_check_e_o_teto_do_servico():
    ddl = " ".join(_migration().UPGRADE_SQL)
    assert "meta_gmv < 10000000000" in ddl
    assert svc.META_MAXIMA < Decimal("10000000000")
    assert "meta_gmv <> 'NaN'" in ddl


def test_migration_nao_tem_efeito_no_import():
    for no in ast.parse(MIGRATION.read_text(encoding="utf-8")).body:
        assert isinstance(no, (ast.Import, ast.ImportFrom, ast.Assign, ast.FunctionDef, ast.Expr))


# ---------------------------------------------------------------------------
# parse_mes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto,esperado", [
    ("2026-10", date(2026, 10, 1)),
    ("2024-01", date(2024, 1, 1)),
    ("2030-12", date(2030, 12, 1)),
])
def test_parse_mes_valido(texto, esperado):
    assert svc.parse_mes(texto) == esperado


@pytest.mark.parametrize("texto", ["", "2026-13", "2026-00", "2026-1", "26-10", "2026-10-01", "2023-12", "2031-01", "abcd-ef"])
def test_parse_mes_invalido(texto):
    with pytest.raises(ValueError):
        svc.parse_mes(texto)


# ---------------------------------------------------------------------------
# planejar — a regra de quais celulas mudam
# ---------------------------------------------------------------------------

D = Decimal


def test_celula_nova_vira_alteracao_sem_anterior():
    plano = svc.planejar({}, {("kokeshi", "tiktok"): D("1000")})
    assert plano == [svc.Alteracao("kokeshi", "tiktok", None, D("1000.00"))]


def test_valor_igual_ao_centavo_nao_e_alteracao():
    assert svc.planejar({("kokeshi", "ml"): D("1000.00")}, {("kokeshi", "ml"): D("1000")}) == []


def test_mudanca_de_valor_registra_anterior_e_novo():
    plano = svc.planejar({("apice", "shopee"): D("500")}, {("apice", "shopee"): D("750.5")})
    assert plano == [svc.Alteracao("apice", "shopee", D("500.00"), D("750.50"))]


def test_none_remove_meta_existente():
    plano = svc.planejar({("lescent", "ml"): D("10")}, {("lescent", "ml"): None})
    assert plano == [svc.Alteracao("lescent", "ml", D("10.00"), None)]


def test_none_em_celula_sem_meta_nao_faz_nada():
    assert svc.planejar({}, {("lescent", "ml"): None}) == []


def test_zero_e_meta_e_difere_de_sem_meta():
    assert svc.planejar({}, {("rituaria", "ml"): D("0")}) == [
        svc.Alteracao("rituaria", "ml", None, D("0.00"))
    ]


def test_celula_nao_enviada_fica_intocada():
    atuais = {("barbours", "tiktok"): D("1"), ("barbours", "ml"): D("2")}
    plano = svc.planejar(atuais, {("barbours", "ml"): D("3")})
    assert [(a.brand, a.channel) for a in plano] == [("barbours", "ml")]


def test_plano_sai_na_ordem_marcas_x_canais():
    pedidas = {("rituaria", "shopee"): D("1"), ("apice", "ml"): D("1"), ("apice", "tiktok"): D("1")}
    plano = svc.planejar({}, pedidas)
    assert [(a.brand, a.channel) for a in plano] == [
        ("apice", "tiktok"), ("apice", "ml"), ("rituaria", "shopee"),
    ]


# ---------------------------------------------------------------------------
# Rotas — validacao na borda, banco substituido
# ---------------------------------------------------------------------------

class _BancoFalso:
    pass


@pytest.fixture
def cliente(monkeypatch):
    app.dependency_overrides[get_db] = lambda: _BancoFalso()
    rota._limite_cliente.limpar()
    rota._limite_global.limpar()
    chamadas = {}

    def ler(db, mes):
        chamadas["ler"] = mes
        return {"mes": f"{mes:%Y-%m}", "marcas": list(svc.MARCAS), "canais": list(svc.CANAIS),
                "celulas": [{"brand": "kokeshi", "channel": "tiktok", "meta_gmv": 1000.0}],
                "ultima_alteracao": datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc)}

    def salvar(db, mes, pedidas):
        chamadas["salvar"] = (mes, pedidas)
        return ler(db, mes), len(pedidas)

    monkeypatch.setattr(svc, "ler", ler)
    monkeypatch.setattr(svc, "salvar", salvar)
    yield TestClient(app), chamadas
    app.dependency_overrides.pop(get_db, None)


def _put(c, corpo, mes="2026-10"):
    return c.put(f"/api/v1/metas/{mes}", json=corpo)


def test_get_devolve_o_mes_pedido(cliente):
    c, chamadas = cliente
    r = c.get("/api/v1/metas/2026-10")
    assert r.status_code == 200
    assert r.json()["mes"] == "2026-10"
    assert r.json()["celulas"] == [{"brand": "kokeshi", "channel": "tiktok", "meta_gmv": 1000.0}]
    assert chamadas["ler"] == date(2026, 10, 1)


@pytest.mark.parametrize("mes", ["2026-13", "outubro", "2031-01"])
def test_mes_invalido_e_422_no_get_e_no_put(cliente, mes):
    c, _ = cliente
    assert c.get(f"/api/v1/metas/{mes}").status_code == 422
    assert _put(c, {"celulas": [{"brand": "apice", "channel": "ml", "meta_gmv": 1}]}, mes).status_code == 422


def test_put_repassa_celulas_como_decimal_e_none(cliente):
    c, chamadas = cliente
    r = _put(c, {"celulas": [
        {"brand": "apice", "channel": "tiktok", "meta_gmv": 850349.5},
        {"brand": "apice", "channel": "ml", "meta_gmv": None},
    ]})
    assert r.status_code == 200
    assert r.json()["alteracoes"] == 2
    mes, pedidas = chamadas["salvar"]
    assert mes == date(2026, 10, 1)
    assert pedidas == {("apice", "tiktok"): D("850349.5"), ("apice", "ml"): None}


@pytest.mark.parametrize("celula", [
    {"brand": "gocase", "channel": "tiktok", "meta_gmv": 1},
    {"brand": "apice", "channel": "amazon", "meta_gmv": 1},
    {"brand": "apice", "channel": "ml", "meta_gmv": -1},
    {"brand": "apice", "channel": "ml", "meta_gmv": 10000000000},
    {"brand": "apice", "channel": "ml", "meta_gmv": 1.234},
    {"brand": "apice", "channel": "ml", "meta_gmv": "NaN"},
    {"brand": "apice", "channel": "ml", "meta_gmv": 1, "extra": True},
])
def test_celula_invalida_e_422(cliente, celula):
    c, chamadas = cliente
    assert _put(c, {"celulas": [celula]}).status_code == 422
    assert "salvar" not in chamadas


def test_celula_repetida_e_422(cliente):
    c, _ = cliente
    cel = {"brand": "apice", "channel": "ml", "meta_gmv": 1}
    assert _put(c, {"celulas": [cel, cel]}).status_code == 422


def test_corpo_vazio_ou_grande_demais_e_422(cliente):
    c, _ = cliente
    assert _put(c, {"celulas": []}).status_code == 422
    muitas = [{"brand": b, "channel": ch, "meta_gmv": 1} for b in svc.MARCAS for ch in svc.CANAIS]
    assert len(muitas) == 15
    assert _put(c, {"celulas": muitas}).status_code == 200
    assert _put(c, {"celulas": muitas + muitas[:1]}).status_code == 422


def test_falha_de_banco_vira_503_sem_vazar_a_excecao(cliente, monkeypatch):
    c, _ = cliente

    def quebra(*a, **k):
        raise OperationalError("SELECT", {}, Exception("postgresql://user:segredo@host/db"))

    monkeypatch.setattr(svc, "ler", quebra)
    monkeypatch.setattr(svc, "salvar", quebra)
    for r in (c.get("/api/v1/metas/2026-10"),
              _put(c, {"celulas": [{"brand": "apice", "channel": "ml", "meta_gmv": 1}]})):
        assert r.status_code == 503
        assert "segredo" not in r.text
        assert r.json()["detail"] == rota.MSG_FALHA_BANCO


def test_sem_banco_configurado_e_503(cliente):
    c, _ = cliente
    app.dependency_overrides[get_db] = lambda: None
    assert c.get("/api/v1/metas/2026-10").status_code == 503


def test_gravacao_tem_limite_por_cliente(cliente):
    c, _ = cliente
    corpo = {"celulas": [{"brand": "apice", "channel": "ml", "meta_gmv": 1}]}
    for _ in range(rota.LIMITE_POR_CLIENTE):
        assert _put(c, corpo).status_code == 200
    r = _put(c, corpo)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


def test_leitura_nao_consome_o_limite_de_gravacao(cliente):
    c, _ = cliente
    for _ in range(rota.LIMITE_POR_CLIENTE + 5):
        assert c.get("/api/v1/metas/2026-10").status_code == 200
