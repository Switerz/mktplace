"""METAS-1 — SQL real do servico de metas contra um PostgreSQL descartavel.

Opcional: so' roda com `METAS_TEST_DATABASE_URL` apontando para um banco
VAZIO e descartavel (o teste cria e derruba o schema `marts` nele). Nunca
aponte para o Neon. Um jeito sem Docker:

    uv run --no-project --with pgserver python -c "import pgserver, tempfile; \
        print(pgserver.get_server(tempfile.mkdtemp()).get_uri())"
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.services import metas_service as svc
from tests.test_metas import _migration

URL = os.environ.get("METAS_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="METAS_TEST_DATABASE_URL ausente")

OUT = date(2026, 10, 1)
D = Decimal


@pytest.fixture
def db():
    eng = create_engine(URL)
    with eng.begin() as con:
        con.execute(text("DROP SCHEMA IF EXISTS marts CASCADE"))
        con.execute(text("CREATE SCHEMA marts"))
        for sql in _migration().UPGRADE_SQL:
            con.execute(text(sql))
    sessao = sessionmaker(bind=eng)()
    yield sessao
    sessao.close()
    with eng.begin() as con:
        con.execute(text("DROP SCHEMA marts CASCADE"))
    eng.dispose()


def _historico(db):
    return db.execute(text(
        "SELECT brand, channel, meta_anterior, meta_nova"
        " FROM marts.meta_gmv_mensal_historico ORDER BY id"
    )).all()


def test_mes_sem_meta_devolve_lista_vazia(db):
    estado = svc.ler(db, OUT)
    assert estado["mes"] == "2026-10"
    assert estado["celulas"] == []
    assert estado["ultima_alteracao"] is None


def test_salvar_inclui_altera_remove_e_registra_historico(db):
    estado, n = svc.salvar(db, OUT, {("kokeshi", "tiktok"): D("4216608"), ("kokeshi", "ml"): D("0")})
    assert n == 2
    assert estado["celulas"] == [
        {"brand": "kokeshi", "channel": "tiktok", "meta_gmv": 4216608.0},
        {"brand": "kokeshi", "channel": "ml", "meta_gmv": 0.0},
    ]
    assert estado["ultima_alteracao"] is not None

    estado, n = svc.salvar(db, OUT, {
        ("kokeshi", "tiktok"): D("4500000.50"),  # altera
        ("kokeshi", "ml"): None,                 # remove
        ("apice", "shopee"): None,               # ja' sem meta: nada
    })
    assert n == 2
    assert estado["celulas"] == [{"brand": "kokeshi", "channel": "tiktok", "meta_gmv": 4500000.5}]
    assert _historico(db) == [
        ("kokeshi", "tiktok", None, D("4216608.00")),
        ("kokeshi", "ml", None, D("0.00")),
        ("kokeshi", "tiktok", D("4216608.00"), D("4500000.50")),
        ("kokeshi", "ml", D("0.00"), None),
    ]


def test_salvar_o_mesmo_valor_nao_gera_historico(db):
    svc.salvar(db, OUT, {("lescent", "ml"): D("966306")})
    _, n = svc.salvar(db, OUT, {("lescent", "ml"): D("966306.00")})
    assert n == 0
    assert len(_historico(db)) == 1


def test_meses_sao_independentes(db):
    svc.salvar(db, OUT, {("apice", "tiktok"): D("1")})
    svc.salvar(db, date(2026, 11, 1), {("apice", "tiktok"): D("2")})
    assert svc.ler(db, OUT)["celulas"][0]["meta_gmv"] == 1.0
    assert svc.ler(db, date(2026, 11, 1))["celulas"][0]["meta_gmv"] == 2.0


@pytest.mark.parametrize("brand,channel,mes,meta", [
    ("gocase", "tiktok", "2026-10-01", 1),
    ("apice", "amazon", "2026-10-01", 1),
    ("apice", "ml", "2026-10-15", 1),
    ("apice", "ml", "2026-10-01", -1),
    ("apice", "ml", "2026-10-01", 10000000000),
])
def test_checks_do_banco_barram_o_que_a_api_ja_barra(db, brand, channel, mes, meta):
    with pytest.raises(IntegrityError):
        db.execute(
            text("INSERT INTO marts.meta_gmv_mensal (mes, brand, channel, meta_gmv)"
                 " VALUES (:m, :b, :c, :v)"),
            {"m": mes, "b": brand, "c": channel, "v": meta},
        )
        db.commit()
    db.rollback()


def test_check_barra_nan(db):
    with pytest.raises(IntegrityError):
        db.execute(text(
            "INSERT INTO marts.meta_gmv_mensal (mes, brand, channel, meta_gmv)"
            " VALUES ('2026-10-01', 'apice', 'ml', 'NaN')"
        ))
        db.commit()
    db.rollback()
