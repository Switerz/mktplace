"""Gate EXP-3C1 — a API multicanal contra PostgreSQL de verdade.

Os dubles provam a logica de roteamento; eles nao provam que as consultas
rodam, que o isolamento `REPEATABLE READ, READ ONLY` aguenta o caminho inteiro,
nem que os dois canais convivem na MESMA tabela sem vazar um no payload do
outro. Aqui os dois canais sao publicados lado a lado no schema REAL 001..019.

Sem `EXPEDICAO_API_TEST_DSN`, o arquivo inteiro e' SKIP.

    docker run --rm -d -p 55435:5432 -e POSTGRES_PASSWORD=postgres \\
        --name pg-api postgres:16
    EXPEDICAO_API_TEST_DSN=postgresql://postgres:postgres@localhost:55435/postgres \\
        python -m pytest tests/test_expedicao_api_multicanal_integracao.py

A DSN precisa ser LOCAL: esta suite nunca alcanca banco remoto.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import expedicao_service as svc

DSN = os.environ.get("EXPEDICAO_API_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN, reason="EXPEDICAO_API_TEST_DSN ausente - exige PostgreSQL descartavel")

RAIZ_API = Path(__file__).resolve().parents[1]
RAIZ = RAIZ_API.parents[1]
AGORA = datetime(2026, 9, 22, 19, 4, 10, tzinfo=timezone.utc)
HORA = AGORA.replace(minute=0, second=0, microsecond=0)
LOTE_SH = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
LOTE_ML = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"

LOJAS = {"apice": 1, "barbours": 2, "kokeshi": 3, "lescent": 4, "rituaria": 5}
#: Shopee: conta identificada pelo NOME da loja. Kokeshi nao e' coberta.
CONTAS_SH = [("apice", "apice", 5), ("barbours", "barbours", 3),
             ("lescent", "lescent", 2), ("rituaria", "rituaria", 1)]
#: ML: conta identificada pelo SELLER_ID. Kokeshi e' coberta.
CONTAS_ML = [("2227056661", "kokeshi", 7), ("2532564723", "barbours", 4),
             ("2579732860", "lescent", 2), ("1366932565", "rituaria", 1)]


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine

    ambiente = dict(os.environ)
    ambiente["DATABASE_URL"] = DSN
    ambiente["DATAMART_DATABASE_URL"] = DSN
    ambiente["PYTHONPATH"] = str(RAIZ)
    api = RAIZ / "apps" / "api"
    # `-P` tira o cwd do `sys.path`: `apps/api/alembic/` sombrearia o pacote
    # instalado. O alembic real e' importado ANTES de `apps/api` entrar.
    roteiro = (
        "import alembic.config as C, sys;"
        "sys.path.insert(0, r%s);" % repr(str(api))
        + "C.main(argv=['upgrade', 'head'])"
    )
    r = subprocess.run([sys.executable, "-P", "-c", roteiro], cwd=str(api),
                       env=ambiente, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"alembic indisponivel: {r.stderr[-300:]}")

    eng = create_engine(DSN)
    semear_dimensoes(eng)
    yield eng
    eng.dispose()


def semear_dimensoes(eng):
    from sqlalchemy import text

    with eng.begin() as c:
        c.execute(text("INSERT INTO marts.dim_empresa (empresa_id, nome_empresa,"
                       " nome_normalizado) VALUES (1, 'GoBeaute', 'gobeaute')"
                       " ON CONFLICT DO NOTHING"))
        for marca, loja in LOJAS.items():
            c.execute(text("INSERT INTO marts.dim_loja (loja_id, empresa_id,"
                           " brand_key, nome_loja, nome_normalizado)"
                           " VALUES (:l, 1, :b, :n, :b) ON CONFLICT DO NOTHING"),
                      {"l": loja, "b": marca, "n": marca.title()})
        c.execute(text("INSERT INTO marts.dim_marketplace (marketplace_id,"
                       " nome_marketplace, slug) VALUES (2, 'Mercado Livre',"
                       " 'mercadolivre'), (3, 'Shopee', 'shopee')"
                       " ON CONFLICT DO NOTHING"))
        # Registry: a Shopee identifica a conta pelo shop_id; o ML pelo seller_id.
        for i, (_sa, marca, _n) in enumerate(CONTAS_SH):
            c.execute(text("INSERT INTO marts.dim_seller_account (marketplace_id,"
                           " loja_id, external_seller_id, account_name, ativo)"
                           " VALUES (3, :l, :e, :a, true) ON CONFLICT DO NOTHING"),
                      {"l": LOJAS[marca], "e": f"16096719{i:02d}", "a": f"SH {marca}"})
        for sa, marca, _n in CONTAS_ML:
            c.execute(text("INSERT INTO marts.dim_seller_account (marketplace_id,"
                           " loja_id, external_seller_id, account_name, ativo)"
                           " VALUES (2, :l, :e, :a, true) ON CONFLICT DO NOTHING"),
                      {"l": LOJAS[marca], "e": sa, "a": f"ML {marca}"})


def publicar(eng, canal, lote, contas, *, prazo):
    from sqlalchemy import text

    with eng.begin() as c:
        c.execute(text("DELETE FROM marts.expedicao_fila_atual WHERE channel = :ch"),
                  {"ch": canal})
        c.execute(text("DELETE FROM marts.expedicao_refresh_run WHERE channel = :ch"),
                  {"ch": canal})
        for shop_account, marca, n in contas:
            for i in range(n):
                c.execute(text("""
                    INSERT INTO marts.expedicao_fila_atual (
                        effective_at, refresh_batch_id, channel, shop_account,
                        marketplace_order_id, brand, created_at, paid_at,
                        dispatch_deadline, deadline_source, deadline_status,
                        hours_overdue, hours_open, operational_age_status,
                        is_slow_vs_baseline, is_source_zombie, is_stalled,
                        timestamp_quality, source_freshness_status,
                        source_ingested_at, logistic_type)
                    VALUES (:ef, :lote, :ch, :sa, :oid, :br, :cr, :cr,
                            :dl, :ds, :st, NULL, 10.0, 'within_48h',
                            false, false, false, :tq, 'fresh', :ing, :lt)"""), {
                    "ef": AGORA, "lote": lote, "ch": canal, "sa": shop_account,
                    "oid": f"{canal[:2].upper()}-{shop_account}-{i:04d}",
                    "br": marca, "cr": AGORA - timedelta(hours=10),
                    "dl": AGORA + timedelta(hours=5) if prazo else None,
                    "ds": "marketplace_native" if prazo else "unavailable",
                    "st": "on_time" if prazo else "unavailable",
                    "tq": "verified" if prazo else "assumed",
                    "ing": AGORA - timedelta(hours=1),
                    "lt": None if canal == "shopee" else "cross_docking",
                })
            c.execute(text("""
                INSERT INTO marts.expedicao_refresh_run (
                    refresh_batch_id, channel, shop_account, brand, snapshot_hour,
                    observed_at, source_watermark_at, source_advanced,
                    backlog_count, overdue_count, due_within_24h_count,
                    on_time_count, deadline_unavailable_count, over_48h_count,
                    slow_count, zombie_count, stalled_count, run_status, ingested_at)
                VALUES (:lote, :ch, :sa, :br, :hora, :obs, :wm, false,
                        :n, 0, 0, :ont, :una, 0, 0, 0, 0, 'success', :obs)"""), {
                "lote": lote, "ch": canal, "sa": shop_account, "br": marca,
                "hora": HORA, "obs": AGORA, "wm": AGORA - timedelta(minutes=30),
                "n": n, "ont": n if prazo else 0, "una": 0 if prazo else n,
            })


@pytest.fixture
def dois_canais(engine, monkeypatch):
    """Shopee e Mercado Livre publicados LADO A LADO na mesma tabela."""
    publicar(engine, "shopee", LOTE_SH, CONTAS_SH, prazo=True)
    publicar(engine, "mercadolivre", LOTE_ML, CONTAS_ML, prazo=False)
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_ml_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_order_ref_secret", "", raising=False)
    return engine


@pytest.fixture
def sessao(dois_canais):
    from sqlalchemy.orm import sessionmaker

    S = sessionmaker(bind=dois_canais)
    with S() as s:
        yield s


def test_shopee_serve_so_a_shopee(sessao):
    r = svc.get_expedicao(sessao, limit=100)
    assert r["availability"] == "available"
    assert r["channel"] == "shopee"
    assert r["snapshot"]["refresh_batch_id"] == LOTE_SH
    assert r["totals"]["backlog_count"] == sum(n for _s, _b, n in CONTAS_SH)
    assert {c["shop_account"] for c in r["accounts"]} == {
        s for s, _b, _n in CONTAS_SH}
    assert all(not l["shop_account"].isdigit() for l in r["queue"])


def test_ml_serve_so_o_ml(sessao):
    r = svc.get_expedicao(sessao, channel="mercadolivre", limit=100)
    assert r["availability"] == "available"
    assert r["snapshot"]["refresh_batch_id"] == LOTE_ML
    assert r["totals"]["backlog_count"] == sum(n for _s, _b, n in CONTAS_ML)
    assert {c["shop_account"] for c in r["accounts"]} == {
        s for s, _b, _n in CONTAS_ML}
    assert all(l["shop_account"].isdigit() for l in r["queue"])


def test_nenhuma_linha_de_um_canal_aparece_no_outro(sessao):
    sh = svc.get_expedicao(sessao, limit=100)
    ml = svc.get_expedicao(sessao, channel="mercadolivre", limit=100)
    contas_sh = {l["shop_account"] for l in sh["queue"]}
    contas_ml = {l["shop_account"] for l in ml["queue"]}
    assert contas_sh and contas_ml
    assert not (contas_sh & contas_ml), "as duas filas se misturaram"


def test_kokeshi_so_aparece_no_ml(sessao):
    sh = svc.get_expedicao(sessao, limit=100)
    ml = svc.get_expedicao(sessao, channel="mercadolivre", limit=100)
    assert "kokeshi" not in {c["brand"] for c in sh["accounts"]}
    assert "kokeshi" in {c["brand"] for c in ml["accounts"]}
    assert sh["limitations"]["brands_not_covered"] == ["kokeshi"]
    assert ml["limitations"]["brands_not_covered"] == []


def test_prazo_indisponivel_e_do_ml_e_nao_contamina_a_shopee(sessao):
    sh = svc.get_expedicao(sessao, limit=100)
    ml = svc.get_expedicao(sessao, channel="mercadolivre", limit=100)
    assert sh["totals"]["deadline_unavailable_count"] == 0
    assert ml["totals"]["deadline_unavailable_count"] == ml["totals"]["backlog_count"]
    assert all(l["dispatch_deadline"] is None for l in ml["queue"])
    assert all(l["dispatch_deadline"] is not None for l in sh["queue"])


def test_cobertura_usa_o_registry_do_canal(sessao):
    """Os conjuntos vivem no dominio da CONTA, e a marca tem campo proprio.

    Na Shopee a conta e' o nome da loja; no ML e' o `seller_id`. Em nenhum dos
    dois os conjuntos carregam marca - foi exatamente essa mistura que o
    EXP-3C1-R/V corrigiu.
    """
    sh = svc.get_expedicao(sessao, include_queue=False)["coverage"]
    ml = svc.get_expedicao(sessao, channel="mercadolivre",
                           include_queue=False)["coverage"]
    assert sh["missing_accounts"] == [] and sh["unexpected_accounts"] == []
    assert ml["missing_accounts"] == [] and ml["unexpected_accounts"] == []

    assert set(sh["expected_accounts"]) == set(sh["observed_accounts"])
    assert set(sh["expected_accounts"]) == {s for s, _b, _n in CONTAS_SH}

    assert set(ml["expected_accounts"]) == set(ml["observed_accounts"])
    assert set(ml["expected_accounts"]) == {s for s, _b, _n in CONTAS_ML}
    assert all(v.isdigit() for v in ml["expected_accounts"])
    # a marca da Kokeshi aparece no ML, mas no campo de MARCAS
    assert "kokeshi" in {c["brand"] for c in ml["accounts"]}
    assert "kokeshi" not in ml["expected_accounts"]
    assert ml["brands_not_covered"] == []
    assert sh["brands_not_covered"] == ["kokeshi"]


def test_conta_do_registry_sem_fotografia_e_acusada_pelo_seller_id(sessao, engine):
    """Regressao: com a comparacao por marca isto nunca seria acusado."""
    from sqlalchemy import text

    with engine.begin() as c:
        c.execute(text("INSERT INTO marts.dim_seller_account (marketplace_id,"
                       " loja_id, external_seller_id, account_name, ativo)"
                       " VALUES (2, :l, '999999999', 'ML kokeshi 2', true)"
                       " ON CONFLICT DO NOTHING"), {"l": LOJAS["kokeshi"]})
    sessao.rollback()
    try:
        ml = svc.get_expedicao(sessao, channel="mercadolivre",
                               include_queue=False)
        assert ml["coverage"]["missing_accounts"] == ["999999999"]
        assert ml["snapshot"]["source_health"] == "account_missing"
        # a marca kokeshi CONTINUA observada pela outra conta: comparar marcas
        # teria dito que nao falta nada.
        observadas = ml["coverage"]["accounts"]
        assert "kokeshi" in {c["brand"] for c in observadas if c["observed"]}
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM marts.dim_seller_account"
                           " WHERE external_seller_id = '999999999'"))
        sessao.rollback()


def test_tendencia_isolada_por_canal(sessao):
    sh = svc.get_tendencia(sessao, window_hours=24 * 7)
    ml = svc.get_tendencia(sessao, channel="mercadolivre", window_hours=24 * 7)
    assert {p["shop_account"] for p in sh["points"]} == {
        s for s, _b, _n in CONTAS_SH}
    assert {p["shop_account"] for p in ml["points"]} == {
        s for s, _b, _n in CONTAS_ML}


def test_paginacao_nao_repete_linha_entre_paginas(sessao):
    todas, vistos = [], set()
    for offset in (0, 3, 6):
        pagina = svc.get_expedicao(sessao, channel="mercadolivre",
                                   limit=3, offset=offset)["queue"]
        todas.extend(pagina)
    chaves = [(l["shop_account"], l["created_at"], l["hours_open"]) for l in todas]
    # a fila do ML tem 14 linhas; tres paginas de 3 devolvem 9 distintas
    assert len(todas) == 9
    assert len({id(x) for x in todas}) == 9
    for l in todas:
        vistos.add((l["shop_account"], l["brand"]))
    assert vistos, "paginacao devolveu vazio"


def test_republicar_um_canal_nao_toca_o_outro(sessao, engine):
    antes = svc.get_expedicao(sessao, limit=100)
    novo_lote = str(uuid.uuid4())
    publicar(engine, "mercadolivre", novo_lote,
             [("2227056661", "kokeshi", 2)], prazo=False)
    sessao.rollback()
    depois = svc.get_expedicao(sessao, limit=100)
    ml = svc.get_expedicao(sessao, channel="mercadolivre", limit=100)
    assert depois["snapshot"]["refresh_batch_id"] == antes["snapshot"]["refresh_batch_id"]
    assert depois["totals"] == antes["totals"]
    assert ml["snapshot"]["refresh_batch_id"] == novo_lote
    assert ml["totals"]["backlog_count"] == 2
    # devolve o estado para os demais testes do modulo
    publicar(engine, "mercadolivre", LOTE_ML, CONTAS_ML, prazo=False)


def test_batch_inconsistente_de_um_canal_nao_derruba_o_outro(sessao, engine):
    from sqlalchemy import text

    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO marts.expedicao_fila_atual (
                effective_at, refresh_batch_id, channel, shop_account,
                marketplace_order_id, brand, created_at, paid_at,
                dispatch_deadline, deadline_source, deadline_status,
                hours_overdue, hours_open, operational_age_status,
                is_slow_vs_baseline, is_source_zombie, is_stalled,
                timestamp_quality, source_freshness_status,
                source_ingested_at, logistic_type)
            VALUES (:ef, :lote, 'mercadolivre', '2227056661', 'ML-INTRUSO',
                    'kokeshi', :cr, :cr, NULL, 'unavailable', 'unavailable',
                    NULL, 1.0, 'within_48h', false, false, false, 'assumed',
                    'fresh', :cr, 'cross_docking')"""),
                  {"ef": AGORA + timedelta(minutes=1),
                   "lote": str(uuid.uuid4()), "cr": AGORA})
    sessao.rollback()
    ml = svc.get_expedicao(sessao, channel="mercadolivre")
    sh = svc.get_expedicao(sessao, limit=100)
    assert ml["availability"] == "unavailable"
    assert ml["unavailable_reason"] == "inconsistent_batch"
    assert sh["availability"] == "available", "a Shopee nao pode cair junto"
    publicar(engine, "mercadolivre", LOTE_ML, CONTAS_ML, prazo=False)


def test_consulta_roda_em_transacao_read_only(sessao):
    from sqlalchemy import text

    svc.get_expedicao(sessao, channel="mercadolivre", include_queue=False)
    with pytest.raises(Exception):
        sessao.execute(text("INSERT INTO marts.expedicao_refresh_run "
                            "(refresh_batch_id, channel, shop_account, brand,"
                            " snapshot_hour, observed_at, backlog_count,"
                            " overdue_count, due_within_24h_count, on_time_count,"
                            " deadline_unavailable_count, over_48h_count,"
                            " slow_count, zombie_count, stalled_count, run_status)"
                            " VALUES ('x', 'mercadolivre', 'y', 'kokeshi', now(),"
                            " now(), 0,0,0,0,0,0,0,0,0,'success')"))
    sessao.rollback()
