"""T-0011 — `GET /api/v1/expedicao/jornada` contra PostgreSQL REAL descartavel.

O schema nasce das MIGRATIONS reais (`alembic upgrade head`, 001..030), num
cluster proprio subido por `pipelines/tests/postgres_descartavel.py`. Nada aqui
alcanca Neon, Data Mart ou qualquer banco configurado: a URL sai do helper.

Sem binarios do PostgreSQL o arquivo inteiro e' SKIP com motivo. Na maquina de
desenvolvimento, a partir de uma worktree (que nao carrega `.local/`):

    PMA_TEST_PG_BIN=C:/Users/Notebook/Desktop/mktplace/.local/postgres16/pgsql/bin \\
        python -m pytest -q tests/test_expedicao_api_jornada.py

O que estes testes travam (contrato jornada v1):
  * toda contagem e' agregada no servidor sobre a base COMPLETA (acima de 500
    linhas, sem depender de `limit`/`offset` da lista);
  * o filtro de marca vale para os totais e e' insensivel a caixa, igual nos
    tres canais;
  * etapas exclusivas fecham com a base; se nao fecharem, `partial` sem ajuste;
  * ausencia nao e' zero; flag desligada nao toca o banco; 030 ausente vira
    `unavailable`; ML tem coorte `null`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.schemas.expedicao import JornadaResponse
from app.services import expedicao_service as svc

RAIZ_API = Path(__file__).resolve().parents[1]
RAIZ = RAIZ_API.parents[1]


def _carregar_helper():
    """Importa o helper pelo CAMINHO: `pipelines` nao esta no sys.path da API."""
    caminho = RAIZ / "pipelines" / "tests" / "postgres_descartavel.py"
    spec = importlib.util.spec_from_file_location("_pg_descartavel_jornada", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: SEM `pytestmark` de modulo: so' os testes que pedem `engine` (direta ou via
#: `sessao`/`cliente`) pulam sem Postgres. Flag desligada, 422, OpenAPI e
#: `_confere` rodam sempre.
PG = _carregar_helper()

#: 26/10/2026 12:00 em Brasilia. Janela padrao (7 dias fechados): 19/10 a 25/10.
AGORA = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)
HORA = AGORA.replace(minute=0, second=0, microsecond=0)
LOTE_SH = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
LOTE_ML = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
LOTE_UUID = uuid.UUID("cccccccc-3333-4333-8333-cccccccccccc")

LOJAS = {"apice": 1, "barbours": 2, "kokeshi": 3, "lescent": 4, "rituaria": 5}


# ---------------------------------------------------------------------------
# Banco: cluster da sessao + migrations reais
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def engine():
    if not PG.postgres_disponivel():
        pytest.skip(PG.MOTIVO_SEM_POSTGRES)
    with PG.cluster_da_sessao() as base:
        adm = create_engine(base, isolation_level="AUTOCOMMIT")
        with adm.connect() as c:
            c.execute(text("DROP DATABASE IF EXISTS jornada_t0011 WITH (FORCE)"))
            c.execute(text("CREATE DATABASE jornada_t0011"))
        adm.dispose()
        url = base.rsplit("/", 1)[0] + "/jornada_t0011"

        ambiente = dict(os.environ)
        ambiente["DATABASE_URL"] = url
        ambiente["DATAMART_DATABASE_URL"] = url
        ambiente["PYTHONPATH"] = str(RAIZ)
        # `-P` tira o cwd do sys.path: `apps/api/alembic/` (as migrations)
        # sombrearia o pacote `alembic` instalado. Mesmo roteiro do teste
        # multicanal de integracao.
        roteiro = (
            "import alembic.config as C, sys;"
            "sys.path.insert(0, r%s);" % repr(str(RAIZ_API))
            + "C.main(argv=['upgrade', 'head'])"
        )
        r = subprocess.run([sys.executable, "-P", "-c", roteiro], cwd=str(RAIZ_API),
                           env=ambiente, capture_output=True, text=True)
        assert r.returncode == 0, f"alembic upgrade head falhou: {r.stderr[-800:]}"

        eng = create_engine(url)
        _semear(eng)
        yield eng
        eng.dispose()


def _semear(eng):
    with eng.begin() as c:
        c.execute(text("INSERT INTO marts.dim_empresa (empresa_id, nome_empresa,"
                       " nome_normalizado) VALUES (1, 'GoBeaute', 'gobeaute')"))
        for marca, loja in LOJAS.items():
            c.execute(text("INSERT INTO marts.dim_loja (loja_id, empresa_id,"
                           " brand_key, nome_loja, nome_normalizado)"
                           " VALUES (:l, 1, :b, :n, :b)"),
                      {"l": loja, "b": marca, "n": marca.title()})
        c.execute(text("INSERT INTO marts.dim_marketplace (marketplace_id,"
                       " nome_marketplace, slug) VALUES (2, 'Mercado Livre',"
                       " 'mercadolivre'), (3, 'Shopee', 'shopee')"))
    _publicar_fila(eng, "shopee", LOTE_SH, FILA_SHOPEE)
    _publicar_fila(eng, "mercadolivre", LOTE_ML, FILA_ML)
    _inserir(eng, "marts.expedicao_tiktok_dispatch_daily", TIKTOK)
    _inserir(eng, "marts.expedicao_shopee_ldr_daily", LDR)
    _inserir(eng, "marts.expedicao_shopee_ldr_sem_prazo_daily", SEM_PRAZO)


def _inserir(eng, tabela: str, linhas: list[dict]):
    cols = list(linhas[0])
    with eng.begin() as c:
        c.execute(text(f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES "
                       f"({', '.join(':' + k for k in cols)})"), linhas)


# ---------------------------------------------------------------------------
# Fila aberta: linhas coerentes com TODOS os CHECKs da 018
# ---------------------------------------------------------------------------
def pedido(canal, conta, marca, oid, *, status="on_time", horas=10.0,
           lento=False, zumbi=False) -> dict:
    prazo = {
        "overdue": AGORA - timedelta(hours=2),
        "due_within_24h": AGORA + timedelta(hours=5),
        "on_time": AGORA + timedelta(hours=48),
        "unavailable": None,
    }[status]
    return {
        "channel": canal, "shop_account": conta, "marketplace_order_id": oid,
        "brand": marca, "effective_at": AGORA, "refresh_batch_id":
            LOTE_SH if canal == "shopee" else LOTE_ML,
        "created_at": AGORA - timedelta(hours=10), "paid_at": AGORA - timedelta(hours=10),
        "dispatch_deadline": prazo,
        "deadline_source": "unavailable" if prazo is None else "marketplace_native",
        "deadline_status": status,
        "operational_age_status": ("unknown" if horas is None
                                   else "over_48h" if horas > 48 else "within_48h"),
        "is_slow_vs_baseline": lento, "is_source_zombie": zumbi,
        "is_stalled": lento or zumbi,
        "hours_open": horas,
        "hours_overdue": 2.0 if status == "overdue" else None,
        "logistic_type": None if canal == "shopee" else "cross_docking",
        "carrier": None,
        "source_ingested_at": AGORA - timedelta(hours=1),
        "source_freshness_status": "fresh",
        "timestamp_quality": "verified" if canal == "shopee" else "assumed",
    }


def _fila_shopee() -> list[dict]:
    p: list[dict] = []
    # apice: 520 linhas, mais que o LIMIT_MAX (500) da lista paginada.
    for i in range(520):
        if i < 10:
            st = "overdue"
        elif i < 30:
            st = "due_within_24h"
        elif i < 40:
            st = "unavailable"       # dispatch_deadline NULO -> sem_prazo
        else:
            st = "on_time"
        if 100 <= i < 130:
            horas = 60.0             # acima de 48 h
        elif 130 <= i < 135:
            horas = None             # hours_open NULO -> idade `unknown`
        else:
            horas = 10.0
        p.append(pedido("shopee", "apice", "apice", f"AP-{i:04d}", status=st,
                        horas=horas, lento=i in (200, 201, 202, 203),
                        zumbi=i in (203, 204, 205)))
    # barbours: DUAS contas da mesma marca. `DUP-1` existe nas duas: sao duas
    # linhas da PK (conta, pedido), nao duplicata.
    for i in range(6):
        p.append(pedido("shopee", "barbours", "barbours", f"BA-{i:04d}"))
    p.append(pedido("shopee", "barbours", "barbours", "DUP-1"))
    for i in range(4):
        p.append(pedido("shopee", "barbours_2", "barbours", f"B2-{i:04d}"))
    p.append(pedido("shopee", "barbours_2", "barbours", "DUP-1", status="overdue"))
    for i in range(3):
        p.append(pedido("shopee", "lescent", "lescent", f"LE-{i:04d}"))
    return p


FILA_SHOPEE = _fila_shopee()
FILA_ML = (
    [pedido("mercadolivre", "2532564723", "barbours", f"ML-B-{i}", status="unavailable")
     for i in range(4)]
    + [pedido("mercadolivre", "2227056661", "kokeshi", f"ML-K-{i}", status="unavailable",
              horas=72.0 if i == 0 else 5.0)
       for i in range(6)]
)


def _publicar_fila(eng, canal, lote, linhas):
    _inserir(eng, "marts.expedicao_fila_atual", linhas)
    contas: dict[str, list[dict]] = {}
    for x in linhas:
        contas.setdefault(x["shop_account"], []).append(x)
    resumos = []
    for conta, xs in contas.items():
        def n(pred):
            return sum(1 for x in xs if pred(x))
        resumos.append({
            "refresh_batch_id": lote, "channel": canal, "shop_account": conta,
            "brand": xs[0]["brand"], "snapshot_hour": HORA, "observed_at": AGORA,
            "source_watermark_at": AGORA - timedelta(minutes=30),
            "source_advanced": True, "backlog_count": len(xs),
            "overdue_count": n(lambda x: x["deadline_status"] == "overdue"),
            "due_within_24h_count": n(lambda x: x["deadline_status"] == "due_within_24h"),
            "on_time_count": n(lambda x: x["deadline_status"] == "on_time"),
            "deadline_unavailable_count": n(lambda x: x["deadline_status"] == "unavailable"),
            "over_48h_count": n(lambda x: x["operational_age_status"] == "over_48h"),
            "slow_count": n(lambda x: x["is_slow_vs_baseline"]),
            "zombie_count": n(lambda x: x["is_source_zombie"]),
            "stalled_count": n(lambda x: x["is_stalled"]),
            "run_status": "success", "ingested_at": AGORA,
        })
    _inserir(eng, "marts.expedicao_refresh_run", resumos)


# ---------------------------------------------------------------------------
# TikTok: coorte por data de pagamento x marca (CHECKs da 020)
# ---------------------------------------------------------------------------
def tk(d: date, marca: str, *, brutos: int, amostras: int, cancel: int,
       atrasado: int, pend_venc: int, pend_prazo: int) -> dict:
    """Mesmos numeros nos dois eventos, exceto 1 pedido que e' no prazo no
    despacho e atrasado na coleta — prova que o evento escolhe as colunas."""
    base = brutos - amostras - cancel
    no_prazo = base - atrasado - pend_venc - pend_prazo
    assert no_prazo >= 1
    return {
        "channel": "tiktokshop", "paid_date": d, "brand": marca, "shop_name": marca,
        "pedidos_pagos_brutos": brutos, "amostras_excluidas": amostras,
        "despacho_deadline": d + timedelta(days=1), "despacho_base": base,
        "despacho_cancelado_antes_sla": cancel, "despacho_no_prazo": no_prazo,
        "despacho_atrasado": atrasado, "despacho_pendente_vencido": pend_venc,
        "despacho_pendente_no_prazo": pend_prazo,
        "despacho_is_mature": d <= date(2026, 10, 24),
        "coleta_deadline": d + timedelta(days=2), "coleta_base": base,
        "coleta_cancelado_antes_sla": cancel, "coleta_no_prazo": no_prazo - 1,
        "coleta_atrasada": atrasado + 1, "coleta_pendente_vencida": pend_venc,
        "coleta_pendente_no_prazo": pend_prazo,
        "coleta_is_mature": d <= date(2026, 10, 23),
        "refresh_batch_id": LOTE_UUID, "effective_at": AGORA - timedelta(hours=1),
        "source_watermark_at": AGORA - timedelta(hours=2),
    }


def _tiktok() -> list[dict]:
    linhas = []
    for dia in range(17, 27):                       # 17/10 a 26/10
        d = date(2026, 10, dia)
        pend = 3 if dia >= 24 else 0
        linhas.append(tk(d, "apice", brutos=100 + dia, amostras=2, cancel=3,
                         atrasado=4, pend_venc=1 if dia >= 24 else 0, pend_prazo=pend))
        # Caixa MISTA na coluna (a 020 nao tem FK de marca): o filtro tem de
        # achar `Barbours` com `barbours`.
        linhas.append(tk(d, "Barbours", brutos=50, amostras=0, cancel=1,
                         atrasado=2, pend_venc=0, pend_prazo=pend))
    return linhas


TIKTOK = _tiktok()


# ---------------------------------------------------------------------------
# Shopee LDR (030): vencimento x conta, mais "sem prazo" por pagamento
# ---------------------------------------------------------------------------
def ldr(d: date, conta: str, marca: str, *, pagos: int, fbs: int, cancel: int,
        sem_coleta: int, atrasado: int, pend_venc: int, pend_prazo: int) -> dict:
    base = pagos - fbs - cancel - sem_coleta
    madura = d <= date(2026, 10, 24)
    if madura:
        pend_prazo = 0                              # CHECK: madura sem pendente no prazo
    return {
        "channel": "shopee", "deadline_date": d, "shop_account": conta, "shop_id": conta,
        "brand": marca, "pedidos_pagos": pagos, "excl_fbs": fbs,
        "excl_cancelado_sem_coleta": cancel, "cancelado_vendedor_nao_enviou": 0,
        "cancelado_motivo_desconhecido": 0, "excl_enviado_sem_coleta": sem_coleta,
        "base": base, "no_prazo": base - atrasado - pend_venc - pend_prazo,
        "atrasado": atrasado, "pendente_vencido": pend_venc,
        "pendente_no_prazo": pend_prazo, "is_mature": madura,
        "refresh_batch_id": LOTE_UUID, "effective_at": AGORA - timedelta(hours=1),
        "source_watermark_at": AGORA - timedelta(hours=2),
    }


def _ldr() -> list[dict]:
    linhas = []
    for dia in range(18, 27):                       # 18/10 a 26/10
        d = date(2026, 10, dia)
        linhas.append(ldr(d, "apice", "apice", pagos=40, fbs=5, cancel=2, sem_coleta=1,
                          atrasado=3, pend_venc=0, pend_prazo=4))
        # Duas contas da marca barbours, somadas no servidor.
        linhas.append(ldr(d, "barbours", "barbours", pagos=30, fbs=4, cancel=1,
                          sem_coleta=0, atrasado=2, pend_venc=1, pend_prazo=2))
        linhas.append(ldr(d, "barbours_2", "barbours", pagos=20, fbs=0, cancel=0,
                          sem_coleta=1, atrasado=1, pend_venc=0, pend_prazo=1))
    return linhas


LDR = _ldr()
SEM_PRAZO = [
    {"channel": "shopee", "paid_date": date(2026, 10, dia), "shop_account": conta,
     "shop_id": conta, "brand": marca, "pedidos_pagos": 3 + n, "excl_fbs": 1,
     "excl_cancelado_sem_coleta": 2, "sem_prazo": n,
     "refresh_batch_id": LOTE_UUID, "effective_at": AGORA - timedelta(hours=1),
     "source_watermark_at": AGORA - timedelta(hours=2)}
    for dia in (18, 19, 25, 26)
    for conta, marca, n in (("apice", "apice", 1), ("barbours_2", "barbours", 2))
]


# ---------------------------------------------------------------------------
# Acesso: servico direto e rota HTTP
# ---------------------------------------------------------------------------
@pytest.fixture
def ligado(monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_ml_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_shopee_ldr_api_enabled", True,
                        raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_order_ref_secret", "", raising=False)


@pytest.fixture
def sessao(engine):
    with sessionmaker(bind=engine)() as s:
        yield s


def jornada(sessao, channel, **kw):
    kw.setdefault("agora", AGORA)
    r = svc.get_jornada(sessao, channel=channel, **kw)
    sessao.rollback()
    # Toda resposta tem de caber no schema publicado.
    JornadaResponse.model_validate(r)
    return r


def _app():
    """App com SO' o router da Expedicao — o mesmo objeto que `app.main` monta.

    `app.main` importa `app.auth` (pyjwt) por causa do B2B; a rota da jornada
    nao depende disso, e o teste nao deve pular por dependencia alheia.
    """
    from fastapi import FastAPI

    from app.routers import expedicao

    app = FastAPI()
    app.include_router(expedicao.router)
    return app


@pytest.fixture
def cliente(engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db
    app = _app()
    # A rota usa o relogio; o teste fixa o instante para nao depender do dia.
    monkeypatch.setattr(svc, "_agora", lambda: AGORA)

    S = sessionmaker(bind=engine)

    def _db():
        with S() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def etapa(secao, chave):
    return next(e["count"] for e in secao["etapas"] if e["key"] == chave)


def sobre(secao, chave):
    return next(s for s in secao["sobreposicoes"] if s["key"] == chave)


def conta_sql(engine, sql, **params):
    with engine.connect() as c:
        return c.execute(text(sql), params).scalar()


# ---------------------------------------------------------------------------
# Flag desligada: sem tocar o banco
# ---------------------------------------------------------------------------
class _BancoQueExplode:
    def __getattr__(self, nome):
        raise AssertionError(f"a rota tocou o banco com a flag desligada ({nome})")


@pytest.mark.parametrize("canal", ["shopee", "mercadolivre", "tiktok"])
def test_flag_geral_desligada_responde_disabled_sem_tocar_o_banco(canal, monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", False, raising=False)
    r = svc.get_jornada(_BancoQueExplode(), channel=canal, brands=["Apice"], agora=AGORA)
    assert r["status"] == "disabled"
    assert r["fila_aberta"] is None and r["coorte"] is None
    assert r["brands_applied"] == ["apice"]


def test_flag_desligada_na_rota_nao_abre_sessao(monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db
    app = _app()

    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_ml_api_enabled", False, raising=False)

    def _db():
        yield _BancoQueExplode()

    app.dependency_overrides[get_db] = _db
    try:
        r = TestClient(app).get("/api/v1/expedicao/jornada",
                                params={"channel": "mercadolivre"})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"
    assert r.json()["reason"] == "channel_disabled"


# ---------------------------------------------------------------------------
# Validacao na borda (422)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("params", [
    {},                                                   # canal obrigatorio
    {"channel": "tiktokshop"},                            # fora da allowlist
    {"channel": "<script>"},
    {"channel": "shopee", "brands": ",".join(f"m{i}" for i in range(51))},
    {"channel": "shopee", "days": 0},
    {"channel": "shopee", "days": 36},
    {"channel": "tiktok", "days": 0},
    {"channel": "tiktok", "days": 91},
    {"channel": "tiktok", "event": "entrega"},
    {"channel": "shopee", "event": "despacho"},          # Shopee e' sempre coleta
])
def test_entrada_invalida_vira_422_sem_eco(params, monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db
    app = _app()

    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", False, raising=False)

    def _db():
        yield _BancoQueExplode()

    app.dependency_overrides[get_db] = _db
    try:
        r = TestClient(app).get("/api/v1/expedicao/jornada", params=params)
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 422
    assert "<script>" not in r.text


def test_tiktok_aceita_90_dias_e_50_marcas(monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db
    app = _app()

    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", False, raising=False)

    def _db():
        yield _BancoQueExplode()

    app.dependency_overrides[get_db] = _db
    try:
        r = TestClient(app).get("/api/v1/expedicao/jornada", params={
            "channel": "tiktok", "days": 90, "event": "despacho",
            "brands": ",".join(f"m{i}" for i in range(50))})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


@pytest.mark.parametrize("dias", [0, 36, 999, -5])
def test_ml_ignora_days_em_vez_de_validar(dias, monkeypatch):
    """Contrato v1.1 §3.1.6: o ML nao tem coorte, entao `days` nao e' validado."""
    from fastapi.testclient import TestClient

    from app.database import get_db
    app = _app()

    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", False, raising=False)

    def _db():
        yield _BancoQueExplode()

    app.dependency_overrides[get_db] = _db
    try:
        r = TestClient(app).get("/api/v1/expedicao/jornada",
                                params={"channel": "mercadolivre", "days": dias})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"


def test_regras_de_frescor_vem_das_rotas_irmas():
    """Mesmos limites da `/shopee/ldr` (6 h) e do alerta da `/tiktok` (12 h),
    sem o numero redigitado aqui: a prova e' pelas bordas."""
    lim = svc.shopee_ldr_svc.LIMITE_FOTOGRAFIA_HORAS
    no_limite = AGORA - timedelta(hours=lim)
    passou = AGORA - timedelta(hours=lim, minutes=1)

    def codigos(*args):
        return [c for c, _ in svc._shopee_ldr_velha(*args, AGORA)]

    # Publicacao e fonte medidas com o MESMO limite, cada uma no seu sintoma.
    assert codigos(no_limite, no_limite) == []
    assert codigos(passou, no_limite) == ["publicacao_desatualizada"]
    assert codigos(no_limite, passou) == ["fonte_desatualizada"]
    assert codigos(no_limite, None) == ["fonte_sem_carimbo"]
    assert codigos(passou, None) == ["publicacao_desatualizada", "fonte_sem_carimbo"]
    assert not svc._tiktok_fotografia_velha(AGORA - timedelta(hours=12), AGORA)
    assert svc._tiktok_fotografia_velha(AGORA - timedelta(hours=12, minutes=1), AGORA)
    # A `/tiktok` nao olha o watermark: a jornada tambem nao.
    assert svc._tiktok_velha(AGORA - timedelta(hours=1), None, AGORA) == []
    assert svc._tiktok_velha(AGORA - timedelta(hours=1),
                             AGORA - timedelta(days=3), AGORA) == []


def test_openapi_lista_a_rota():
    app = _app()

    spec = app.openapi()
    assert "/api/v1/expedicao/jornada" in spec["paths"]
    esquemas = spec["components"]["schemas"]
    for nome in ("JornadaResponse", "JornadaFilaAberta", "JornadaCoorte",
                 "JornadaEtapa", "JornadaSobreposicao"):
        assert nome in esquemas


# ---------------------------------------------------------------------------
# Fila aberta (Shopee)
# ---------------------------------------------------------------------------
def test_base_completa_acima_de_500_independe_da_paginacao(sessao, ligado):
    r = jornada(sessao, "shopee")
    fila = r["fila_aberta"]
    assert r["status"] == "ok", r["reason"]
    assert fila["base"] == len(FILA_SHOPEE) == 535
    # A lista paginada do endpoint antigo nunca devolve mais que 500, e o total
    # da jornada nao depende dela.
    for limit, offset in ((1, 0), (500, 0), (500, 500)):
        pag = svc.get_expedicao(sessao, limit=limit, offset=offset)["pagination"]
        sessao.rollback()
        assert pag["total"] == fila["base"]
        assert pag["returned"] <= 500


def test_fila_shopee_etapa_unica_e_sobreposicoes_contadas(sessao, ligado):
    fila = jornada(sessao, "shopee")["fila_aberta"]
    assert fila["grain"] == "pedido"
    assert fila["snapshot_at"] == AGORA
    assert fila["freshness"] == "fresh"
    assert [e["key"] for e in fila["etapas"]] == ["aguardando_expedicao"]
    assert etapa(fila, "aguardando_expedicao") == fila["base"]
    assert fila["etapas_fecham"] is True
    # 10 vencidos do apice + DUP-1 da barbours_2
    assert sobre(fila, "vencido")["count"] == 11
    assert sobre(fila, "vence_24h")["count"] == 20
    assert sobre(fila, "travado")["count"] == 6        # uniao, nao 4 + 3
    assert sobre(fila, "lento")["count"] == 4
    assert sobre(fila, "zumbi_fonte")["count"] == 3
    assert "FBS não separado na fila aberta" in fila["limitacoes"]


def test_nulos_prazo_nulo_vira_sem_prazo_e_horas_nulas_nao_sao_acima_48h(sessao, ligado):
    fila = jornada(sessao, "shopee", brands=["apice"])["fila_aberta"]
    assert fila["base"] == 520
    # dispatch_deadline NULO -> sem_prazo, contado (nao some da base)
    assert sobre(fila, "sem_prazo")["count"] == 10
    # hours_open NULO -> idade `unknown`: fora de acima_48h, dentro da base
    assert sobre(fila, "acima_48h")["count"] == 30
    nulos = conta_sql(sessao.get_bind(),
                      "SELECT count(*) FROM marts.expedicao_fila_atual "
                      "WHERE channel='shopee' AND hours_open IS NULL")
    assert nulos == 5


def test_duplicata_de_pedido_nao_conta_duas_vezes(engine, sessao, ligado):
    # A PK (channel, shop_account, marketplace_order_id) recusa a duplicata.
    with pytest.raises(IntegrityError):
        _inserir(engine, "marts.expedicao_fila_atual",
                 [pedido("shopee", "barbours", "barbours", "DUP-1")])
    # O mesmo order id em DUAS contas sao duas linhas da PK: a contagem e' por
    # linha da PK, igual a `count(DISTINCT (conta, pedido))`.
    distintos = conta_sql(engine, """
        SELECT count(DISTINCT (shop_account, marketplace_order_id))
        FROM marts.expedicao_fila_atual WHERE channel = 'shopee'
          AND lower(brand) = 'barbours'""")
    fila = jornada(sessao, "shopee", brands=["barbours"])["fila_aberta"]
    assert fila["base"] == distintos == 12


def test_multiplas_contas_da_mesma_marca_somadas_no_servidor(sessao, ligado):
    fila = jornada(sessao, "shopee", brands=["barbours"])["fila_aberta"]
    assert fila["base"] == 7 + 5
    assert sobre(fila, "vencido")["count"] == 1       # so' o DUP-1 da barbours_2


def test_filtro_de_marca_vale_nos_totais_e_ignora_caixa(sessao, ligado):
    a = jornada(sessao, "shopee", brands=["BARBOURS"])
    b = jornada(sessao, "shopee", brands=[" Barbours ", "barbours"])
    assert a["brands_applied"] == b["brands_applied"] == ["barbours"]
    assert a["fila_aberta"]["base"] == b["fila_aberta"]["base"] == 12
    assert a["fila_aberta"]["base"] != jornada(sessao, "shopee")["fila_aberta"]["base"]


def test_marca_inexistente_e_base_zero_sem_erro(sessao, ligado):
    for canal in ("shopee", "mercadolivre", "tiktok"):
        r = jornada(sessao, canal, brands=["NaoExiste"])
        assert r["status"] == "ok", (canal, r["reason"])
        assert r["brands_applied"] == ["naoexiste"]
        secoes = [s for s in (r["fila_aberta"], r["coorte"]) if s is not None]
        assert secoes
        for s in secoes:
            assert s["base"] == 0
            assert all(e["count"] == 0 for e in s["etapas"])
            assert s["etapas_fecham"] is True


# ---------------------------------------------------------------------------
# Mercado Livre: shipment, sem prazo, coorte nula
# ---------------------------------------------------------------------------
def test_ml_coorte_nula_com_limitacao_e_prazo_ausente_nao_vira_zero(sessao, ligado):
    r = jornada(sessao, "mercadolivre")
    assert r["status"] == "ok", r["reason"]
    assert r["coorte"] is None
    assert "o Mercado Livre não publica prazo de despacho" in r["limitacoes"]
    fila = r["fila_aberta"]
    assert fila["grain"] == "shipment"
    assert fila["base"] == len(FILA_ML) == 10
    for chave in ("vencido", "vence_24h"):
        s = sobre(fila, chave)
        assert s["count"] is None and s["reason"]
    assert sobre(fila, "sem_prazo")["count"] == 10
    assert sobre(fila, "acima_48h")["count"] == 1
    # Marca: a mesma regra case-insensitive do resto.
    assert jornada(sessao, "mercadolivre", brands=["KOKESHI"])["fila_aberta"]["base"] == 6


# ---------------------------------------------------------------------------
# TikTok: coorte por data de pagamento
# ---------------------------------------------------------------------------
def test_tiktok_coorte_particao_bate_com_as_colunas(engine, sessao, ligado):
    for evento, sufixos in (
        ("coleta", ("coleta_cancelado_antes_sla", "coleta_no_prazo", "coleta_atrasada",
                    "coleta_pendente_vencida", "coleta_pendente_no_prazo")),
        ("despacho", ("despacho_cancelado_antes_sla", "despacho_no_prazo",
                      "despacho_atrasado", "despacho_pendente_vencido",
                      "despacho_pendente_no_prazo")),
    ):
        r = jornada(sessao, "tiktok", evento=evento)
        assert r["status"] == "ok", r["reason"]
        assert r["fila_aberta"] is None
        c = r["coorte"]
        assert (c["eixo"], c["evento"]) == ("paid_date", evento)
        assert c["janela"] == {"inicio": date(2026, 10, 19), "fim": date(2026, 10, 25),
                               "dias": 7}
        cancel, no_prazo, atrasado, pv, pnp = (
            conta_sql(engine, f"SELECT sum({col}) FROM marts.expedicao_tiktok_dispatch_daily"
                              " WHERE paid_date BETWEEN '2026-10-19' AND '2026-10-25'")
            for col in sufixos)
        brutos = conta_sql(engine, "SELECT sum(pedidos_pagos_brutos) FROM "
                                   "marts.expedicao_tiktok_dispatch_daily WHERE paid_date"
                                   " BETWEEN '2026-10-19' AND '2026-10-25'")
        amostras = conta_sql(engine, "SELECT sum(amostras_excluidas) FROM "
                                     "marts.expedicao_tiktok_dispatch_daily WHERE paid_date"
                                     " BETWEEN '2026-10-19' AND '2026-10-25'")
        assert c["base"] == brutos
        assert etapa(c, "fora_amostra") == amostras
        assert etapa(c, "cancelado_antes") == cancel
        assert etapa(c, "aguardando") == pv + pnp
        assert etapa(c, "concluido") == no_prazo + atrasado
        assert sum(e["count"] for e in c["etapas"]) == c["base"]
        assert c["etapas_fecham"] is True
        assert sobre(c, "aguardando_vencido")["count"] == pv
        assert sobre(c, "concluido_atrasado")["count"] == atrasado
        assert sobre(c, "concluido_atrasado")["subconjunto_de"] == "concluido"
    # Os dois eventos leem colunas diferentes (1 pedido/dia/marca muda de lado).
    col = jornada(sessao, "tiktok", evento="coleta")["coorte"]
    desp = jornada(sessao, "tiktok", evento="despacho")["coorte"]
    assert sobre(col, "concluido_atrasado")["count"] == \
        sobre(desp, "concluido_atrasado")["count"] + 14


def test_tiktok_maturidade_por_dia(sessao, ligado):
    c = jornada(sessao, "tiktok", evento="coleta")["coorte"]
    # 19..23 maduros na coleta; 24 e 25 imaturos.
    assert c["maturidade"] == {"dias_maduros": 5, "dias_imaturos": 2}
    assert "dias imaturos ainda podem mudar" in c["limitacoes"]
    d = jornada(sessao, "tiktok", evento="despacho")["coorte"]
    assert d["maturidade"] == {"dias_maduros": 6, "dias_imaturos": 1}


def test_tiktok_filtro_de_marca_case_insensitive_na_coluna(sessao, ligado):
    a = jornada(sessao, "tiktok", brands=["barbours"])["coorte"]
    b = jornada(sessao, "tiktok", brands=["BARBOURS"])["coorte"]
    assert a == b
    assert a["base"] == 50 * 7


def test_janela_em_data_de_brasilia_na_fronteira_do_dia(sessao, ligado):
    # 26/10 02:59 UTC = 25/10 23:59 em Brasilia: hoje ainda e' 25/10.
    antes = jornada(sessao, "tiktok", agora=datetime(2026, 10, 26, 2, 59,
                                                     tzinfo=timezone.utc))["coorte"]
    # 26/10 03:00 UTC = 26/10 00:00 em Brasilia: o dia virou.
    depois = jornada(sessao, "tiktok", agora=datetime(2026, 10, 26, 3, 0,
                                                      tzinfo=timezone.utc))["coorte"]
    assert (antes["janela"]["inicio"], antes["janela"]["fim"]) == (
        date(2026, 10, 18), date(2026, 10, 24))
    assert (depois["janela"]["inicio"], depois["janela"]["fim"]) == (
        date(2026, 10, 19), date(2026, 10, 25))
    # A janela andou um dia: sai 18/10 (apice 118 + Barbours 50), entra 25/10
    # (apice 125 + Barbours 50).
    assert depois["base"] - antes["base"] == (125 + 50) - (118 + 50)


def test_tiktok_etapas_que_nao_fecham_viram_partial_sem_ajuste(engine, sessao, ligado):
    tabela = "marts.expedicao_tiktok_dispatch_daily"
    restricoes = ("ck_expedicao_tiktok_dispatch_daily_coleta_base_bate",
                  "ck_expedicao_tiktok_dispatch_daily_coleta_fecha")
    with engine.begin() as c:
        for nome in restricoes:
            c.execute(text(f"ALTER TABLE {tabela} DROP CONSTRAINT {nome}"))
        linha = tk(date(2026, 10, 20), "quebrada", brutos=20, amostras=1, cancel=1,
                   atrasado=1, pend_venc=0, pend_prazo=0)
        linha["coleta_no_prazo"] += 3               # 3 pedidos a mais que a base
        linha["coleta_base"] += 3
    _inserir(engine, tabela, [linha])
    try:
        r = jornada(sessao, "tiktok", brands=["quebrada"])
        c = r["coorte"]
        assert r["status"] == "partial"
        assert c["etapas_fecham"] is False
        assert "etapas_nao_fecham" in r["reason"]
        # Numeros como vieram: base 20, etapas somam 23. Nada foi "consertado".
        assert c["base"] == 20
        assert sum(e["count"] for e in c["etapas"]) == 23
    finally:
        with engine.begin() as c:
            c.execute(text(f"DELETE FROM {tabela} WHERE brand = 'quebrada'"))
            c.execute(text(f"ALTER TABLE {tabela} ADD CONSTRAINT {restricoes[0]} CHECK ("
                           "coleta_base = pedidos_pagos_brutos - amostras_excluidas"
                           " - coleta_cancelado_antes_sla)"))
            c.execute(text(f"ALTER TABLE {tabela} ADD CONSTRAINT {restricoes[1]} CHECK ("
                           "coleta_no_prazo + coleta_atrasada + coleta_pendente_vencida"
                           " + coleta_pendente_no_prazo = coleta_base)"))


def test_sobreposicao_maior_que_a_etapa_e_partial():
    fecham, motivos = svc._confere(
        10, [{"key": "aguardando", "count": 4}, {"key": "concluido", "count": 6}],
        [{"key": "aguardando_vencido", "count": 5, "subconjunto_de": "aguardando"}],
        "coorte")
    assert fecham is True
    assert motivos and "aguardando_vencido" in motivos[0]


# ---------------------------------------------------------------------------
# Shopee: coorte LDR (030)
# ---------------------------------------------------------------------------
def test_shopee_coorte_ldr_soma_contas_e_separa_sem_prazo(sessao, ligado):
    r = jornada(sessao, "shopee", brands=["Barbours"])
    assert r["status"] == "ok", r["reason"]
    c = r["coorte"]
    assert (c["eixo"], c["evento"]) == ("deadline_date", "coleta")
    assert c["janela"]["inicio"] == date(2026, 10, 19)
    assert c["janela"]["fim"] == date(2026, 10, 25)
    # 7 dias x (30 + 20) pagos, as duas contas da marca somadas.
    assert c["base"] == 7 * 50
    assert [e["key"] for e in c["etapas"]] == [
        "fora_fbs", "cancelado_antes", "enviado_sem_registro", "aguardando", "concluido"]
    assert etapa(c, "fora_fbs") == 7 * 4
    assert etapa(c, "cancelado_antes") == 7 * 1
    assert etapa(c, "enviado_sem_registro") == 7 * 1
    assert sum(e["count"] for e in c["etapas"]) == c["base"]
    assert c["etapas_fecham"] is True
    # sem_prazo fica FORA da coorte e nao entra na soma: pagos em 19/10 e 25/10
    # (18 e 26 estao fora da janela), so' a conta barbours_2 (2 por dia).
    assert c["fora_da_coorte"] == [{"key": "sem_prazo", "count": 4}]
    assert c["maturidade"] == {"dias_maduros": 6, "dias_imaturos": 1}
    assert any("Account Health" in x for x in c["limitacoes"])


def test_shopee_ldr_flag_desligada_coorte_nula_com_limitacao(sessao, ligado, monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_shopee_ldr_api_enabled", False,
                        raising=False)
    r = jornada(sessao, "shopee")
    assert r["status"] == "ok"
    assert r["coorte"] is None
    assert r["fila_aberta"]["base"] == 535
    assert any("expedicao_shopee_ldr_api_enabled" in x for x in r["limitacoes"])


def test_tabela_030_ausente_vira_unavailable_sem_500(engine, sessao, ligado):
    with engine.begin() as c:
        c.execute(text("ALTER TABLE marts.expedicao_shopee_ldr_daily "
                       "RENAME TO expedicao_shopee_ldr_daily_fora"))
    try:
        r = jornada(sessao, "shopee")
        assert r["status"] == "unavailable"
        assert r["reason"] == "coorte: table_not_migrated"
        assert r["coorte"] is None
        # A fila aberta, que tem fonte, continua servida.
        assert r["fila_aberta"]["base"] == 535
    finally:
        with engine.begin() as c:
            c.execute(text("ALTER TABLE marts.expedicao_shopee_ldr_daily_fora "
                           "RENAME TO expedicao_shopee_ldr_daily"))


# ---------------------------------------------------------------------------
# Reconciliacao nos tres canais + exemplo de resposta pela rota HTTP
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("canal", ["shopee", "mercadolivre", "tiktok"])
def test_reconciliacao_soma_das_etapas_igual_a_base_pela_rota(canal, cliente, ligado):
    r = cliente.get("/api/v1/expedicao/jornada", params={"channel": canal})
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["status"] == "ok", corpo["reason"]
    secoes = [s for s in (corpo["fila_aberta"], corpo["coorte"]) if s]
    assert secoes
    for s in secoes:
        assert sum(e["count"] for e in s["etapas"]) == s["base"]
        assert s["etapas_fecham"] is True
        # Nunca percentual: so' contagens inteiras e base.
        assert "rate" not in json.dumps(s) and "percent" not in json.dumps(s)
    # Exemplo para o relatorio (`pytest -s -k exemplo` mostra).
    print(f"\n=== exemplo {canal} ===\n" + json.dumps(corpo, ensure_ascii=False, indent=1))


# ---------------------------------------------------------------------------
# Revisao do 42c3b79: dia sem publicacao, frescor da coorte, frescor invalido
# ---------------------------------------------------------------------------
def test_coorte_publicada_e_recente_nao_e_desatualizada(sessao, ligado):
    for canal in ("shopee", "tiktok"):
        c = jornada(sessao, canal)["coorte"]
        assert c["publicado_em"] == AGORA - timedelta(hours=1)
        assert c["desatualizada"] is False


def test_dia_sem_publicacao_no_meio_da_janela_vira_partial(engine, sessao, ligado,
                                                           cliente):
    tabela = "marts.expedicao_tiktok_dispatch_daily"
    buraco = date(2026, 10, 22)
    removidas = [x for x in TIKTOK if x["paid_date"] == buraco]
    completa = jornada(sessao, "tiktok")["coorte"]
    with engine.begin() as c:
        c.execute(text(f"DELETE FROM {tabela} WHERE paid_date = :d"), {"d": buraco})
    try:
        r = jornada(sessao, "tiktok")
        c = r["coorte"]
        assert r["status"] == "partial"
        assert r["reason"] == "coorte: dias_sem_publicacao (1)"
        assert any("1 dia(s) da janela sem publicação" in x for x in c["limitacoes"])
        # Numeros mantidos: so' sai o que nao foi publicado, nada e' inventado.
        assert c["base"] == completa["base"] - sum(x["pedidos_pagos_brutos"]
                                                   for x in removidas)
        assert c["etapas_fecham"] is True
        # O filtro de marca nao esconde o buraco: e' propriedade da fonte.
        assert jornada(sessao, "tiktok", brands=["apice"])["status"] == "partial"
        corpo = cliente.get("/api/v1/expedicao/jornada",
                            params={"channel": "tiktok"}).json()
        assert corpo["status"] == "partial"
        print("\n=== exemplo tiktok partial ===\n"
              + json.dumps(corpo, ensure_ascii=False, indent=1))
    finally:
        _inserir(engine, tabela, removidas)
    assert jornada(sessao, "tiktok")["status"] == "ok"


def test_shopee_coorte_desatualizada_acima_do_limite_da_ldr(sessao, ligado):
    # Publicada 1 h antes de AGORA; +6 h = 7 h, acima do limite de 6 h. Ainda
    # 26/10 em Brasilia, entao a janela nao muda.
    r = jornada(sessao, "shopee", agora=AGORA + timedelta(hours=6))
    c = r["coorte"]
    assert c["desatualizada"] is True
    assert r["status"] == "partial"
    assert "coorte: publicacao_desatualizada" in r["reason"]
    assert "publicação de 26/10/2026 11:00 (Brasília) ainda não atualizada" \
        in c["limitacoes"]
    # Dentro do limite: nao e' desatualizada.
    assert jornada(sessao, "shopee", agora=AGORA + timedelta(hours=4))["coorte"][
        "desatualizada"] is False


def test_tiktok_coorte_desatualizada_pelo_alerta_da_rota_irma(sessao, ligado):
    # 12 h 30 depois da publicacao (> 12 h do alerta `fotografia_velha`);
    # 26/10 23:30 em Brasilia, mesma janela.
    agora = AGORA + timedelta(hours=11, minutes=30)
    r = jornada(sessao, "tiktok", agora=agora)
    assert r["coorte"]["desatualizada"] is True
    assert r["status"] == "partial"
    # 11 h depois da publicacao: abaixo do limite da /tiktok, ainda que acima
    # do da LDR — cada canal usa o limite da SUA rota irma.
    r = jornada(sessao, "tiktok", agora=AGORA + timedelta(hours=10))
    assert r["coorte"]["desatualizada"] is False
    assert r["status"] == "ok"


def test_frescor_fora_do_dominio_vira_unknown_e_nao_500(engine, sessao, ligado):
    with engine.begin() as c:
        c.execute(text("""
            INSERT INTO audit.data_quality_check (check_name, table_name,
                marketplace_id, status, severity, details)
            VALUES (:chk, 'marts.expedicao_fila_atual', 3, 'pass', 'low', :det)"""),
                  {"chk": svc.CHECK_FRESHNESS, "det": json.dumps(
                      {"brand": "lescent", "measures": "source_watermark_only",
                       "freshness": "esquisito"})})
    try:
        fila = jornada(sessao, "shopee", brands=["lescent"])["fila_aberta"]
        assert fila["freshness"] == "unknown"
    finally:
        with engine.begin() as c:
            c.execute(text("DELETE FROM audit.data_quality_check WHERE check_name = :chk"),
                      {"chk": svc.CHECK_FRESHNESS})


# ---------------------------------------------------------------------------
# Revisao R16 (HIGH): frescor da FONTE na coorte Shopee, paridade com a LDR
# ---------------------------------------------------------------------------
WM_ORIGINAL = AGORA - timedelta(hours=2)
LDR_TAB = "marts.expedicao_shopee_ldr_daily"
TIKTOK_TAB = "marts.expedicao_tiktok_dispatch_daily"


@pytest.fixture
def watermark(engine):
    """Reescreve `source_watermark_at` de uma tabela de coorte e restaura."""
    tocadas: set[str] = set()

    def aplicar(tabela: str, valor, marca=None):
        tocadas.add(tabela)
        onde = " WHERE lower(brand) = :m" if marca else ""
        with engine.begin() as c:
            c.execute(text(f"UPDATE {tabela} SET source_watermark_at = :v{onde}"),
                      {"v": valor, "m": marca})

    yield aplicar
    with engine.begin() as c:
        for tabela in tocadas:
            c.execute(text(f"UPDATE {tabela} SET source_watermark_at = :v"),
                      {"v": WM_ORIGINAL})


def ldr_irma(sessao, **kw):
    kw.setdefault("agora", AGORA)
    r = svc.shopee_ldr_svc.obter_ldr(sessao, **kw)
    sessao.rollback()
    return r


def test_coorte_expoe_fonte_lida_em(sessao, ligado):
    for canal in ("shopee", "tiktok"):
        c = jornada(sessao, canal)["coorte"]
        assert c["fonte_lida_em"] == WM_ORIGINAL
        assert c["desatualizada"] is False


def test_shopee_publicacao_recente_sobre_fonte_velha_e_desatualizada(
        sessao, ligado, watermark, cliente):
    """Reproducao do revisor: publicacao de 1 h, watermark de 48 h."""
    watermark(LDR_TAB, AGORA - timedelta(hours=48))
    r = jornada(sessao, "shopee")
    c = r["coorte"]
    assert c["publicado_em"] == AGORA - timedelta(hours=1)
    assert c["fonte_lida_em"] == AGORA - timedelta(hours=48)
    assert c["desatualizada"] is True
    assert r["status"] == "partial"
    assert r["reason"] == "coorte: fonte_desatualizada"
    assert ("a fonte não recebe dado novo desde 24/10/2026 12:00 (Brasília); "
            "os números não são atuais") in c["limitacoes"]
    # A publicacao em si nao e' velha: o motivo nao pode dizer que e'.
    assert not any(x.startswith("publicação de") for x in c["limitacoes"])
    # Numeros intactos; a fila aberta (outra fonte) segue servida.
    assert c["etapas_fecham"] is True and c["base"] == 7 * 90
    assert r["fila_aberta"]["base"] == 535

    irma = ldr_irma(sessao)["snapshot"]
    assert irma["stale"] is True and irma["source_age_hours"] == 48

    corpo = cliente.get("/api/v1/expedicao/jornada", params={"channel": "shopee"}).json()
    assert corpo["coorte"]["desatualizada"] is True
    print("\n=== exemplo shopee fonte velha, publicacao recente ===\n"
          + json.dumps(corpo, ensure_ascii=False, indent=1))


def test_shopee_fonte_sem_carimbo_e_desatualizada(sessao, ligado, watermark):
    watermark(LDR_TAB, None)
    r = jornada(sessao, "shopee")
    c = r["coorte"]
    assert c["fonte_lida_em"] is None
    assert c["desatualizada"] is True
    assert r["status"] == "partial"
    assert r["reason"] == "coorte: fonte_sem_carimbo"
    assert any("não registra leitura da fonte" in x for x in c["limitacoes"])
    irma = ldr_irma(sessao)["snapshot"]
    assert irma["stale"] is True and irma["source_age_hours"] is None


def test_shopee_publicacao_e_fonte_velhas_dao_os_dois_motivos(sessao, ligado, watermark):
    watermark(LDR_TAB, AGORA - timedelta(hours=30))
    r = jornada(sessao, "shopee", agora=AGORA + timedelta(hours=6))
    assert r["reason"] == "coorte: publicacao_desatualizada; coorte: fonte_desatualizada"
    assert r["coorte"]["desatualizada"] is True


@pytest.mark.parametrize("idade_fonte", [
    timedelta(hours=2), timedelta(hours=6), timedelta(hours=6, minutes=1),
    timedelta(hours=48), None,
], ids=["fonte2h", "fonte6h", "fonte6h01", "fonte48h", "fonte_nula"])
@pytest.mark.parametrize("depois", [timedelta(0), timedelta(hours=6)],
                         ids=["pub1h", "pub7h"])
def test_paridade_com_a_rota_ldr_sobre_velha(idade_fonte, depois, sessao, ligado,
                                             watermark):
    """As duas rotas, no MESMO banco e no MESMO instante, concordam sobre "velha"."""
    agora = AGORA + depois
    watermark(LDR_TAB, None if idade_fonte is None else agora - idade_fonte)
    c = jornada(sessao, "shopee", agora=agora)["coorte"]
    irma = ldr_irma(sessao, agora=agora)["snapshot"]
    assert c["desatualizada"] is irma["stale"]
    assert c["publicado_em"] == irma["effective_at"]


def test_paridade_com_a_ldr_no_filtro_de_marca(sessao, ligado, watermark):
    """Watermark e' por conta: a conta fresca do apice nao esconde a barbours."""
    watermark(LDR_TAB, AGORA - timedelta(hours=48), marca="barbours")
    so_barbours = jornada(sessao, "shopee", brands=["Barbours"])
    assert so_barbours["coorte"]["desatualizada"] is True
    assert so_barbours["coorte"]["fonte_lida_em"] == AGORA - timedelta(hours=48)
    assert ldr_irma(sessao, brands=["Barbours"])["snapshot"]["stale"] is True
    # Sem filtro, as duas rotas usam o watermark mais recente: em dia.
    assert jornada(sessao, "shopee")["coorte"]["desatualizada"] is False
    assert ldr_irma(sessao)["snapshot"]["stale"] is False
    # Marca sem linha na janela: nada a medir dela, vale o do canal.
    c = jornada(sessao, "shopee", brands=["naoexiste"])["coorte"]
    assert c["fonte_lida_em"] == WM_ORIGINAL and c["desatualizada"] is False


def test_tiktok_mantem_a_regra_da_rota_irma_que_ignora_watermark(sessao, ligado,
                                                                watermark):
    """A `/tiktok` so' olha `effective_at` no alerta `fotografia_velha`."""
    for valor in (AGORA - timedelta(hours=48), None):
        watermark(TIKTOK_TAB, valor)
        r = jornada(sessao, "tiktok")
        assert r["coorte"]["fonte_lida_em"] == valor
        assert r["coorte"]["desatualizada"] is False
        assert r["status"] == "ok", r["reason"]
        irma = svc.tiktok_svc.obter_serie(sessao, agora=AGORA)
        sessao.rollback()
        assert not any(a["code"] == "fotografia_velha" for a in irma["alerts"])


# ---------------------------------------------------------------------------
# Revisao R16 (MEDIUM): fila vazia comprovada x ausencia de publicacao
# ---------------------------------------------------------------------------
FILA_TAB = "marts.expedicao_fila_atual"
RUN_TAB = "marts.expedicao_refresh_run"
LOTE_ML_VAZIO = "dddddddd-4444-4444-8444-dddddddddddd"


def _resumo_ml_zerado(conta: str, marca: str) -> dict:
    return {"refresh_batch_id": LOTE_ML_VAZIO, "channel": "mercadolivre",
            "shop_account": conta, "brand": marca,
            "snapshot_hour": HORA + timedelta(hours=1),
            "observed_at": AGORA + timedelta(hours=1),
            "source_watermark_at": AGORA + timedelta(minutes=30),
            "source_advanced": True, **{m: 0 for m in svc._MEDIDAS_RESUMO},
            "run_status": "success", "ingested_at": AGORA + timedelta(hours=1)}


@pytest.fixture
def ml_sem_fila(engine):
    """Apaga a fila do ML (o DELETE do publicador) e restaura tudo no fim."""
    with engine.begin() as c:
        resumos = [dict(r) for r in c.execute(text(
            f"SELECT * FROM {RUN_TAB} WHERE channel = 'mercadolivre'")).mappings()]
        c.execute(text(f"DELETE FROM {FILA_TAB} WHERE channel = 'mercadolivre'"))
    yield
    with engine.begin() as c:
        c.execute(text(f"DELETE FROM {RUN_TAB} WHERE channel = 'mercadolivre'"))
    _inserir(engine, RUN_TAB, resumos)
    _inserir(engine, FILA_TAB, FILA_ML)


def test_fila_vazia_comprovada_vira_base_zero_ok(engine, sessao, ligado, ml_sem_fila,
                                                 cliente):
    """Desfecho legitimo do publicador: fila limpa + resumos zerados no lote."""
    _inserir(engine, RUN_TAB, [_resumo_ml_zerado("2532564723", "barbours"),
                               _resumo_ml_zerado("2227056661", "kokeshi")])
    r = jornada(sessao, "mercadolivre")
    assert r["status"] == "ok", r["reason"]
    fila = r["fila_aberta"]
    assert fila["base"] == 0
    assert fila["etapas"] == [{"key": "aguardando_expedicao",
                               "label": "Aguardando expedição", "count": 0}]
    assert fila["etapas_fecham"] is True
    assert fila["snapshot_at"] == AGORA + timedelta(hours=1)
    assert fila["freshness"] == "fresh"
    # Medida que o ML nao publica continua `None`, nunca 0.
    assert sobre(fila, "vencido")["count"] is None
    assert sobre(fila, "acima_48h")["count"] == 0
    assert jornada(sessao, "mercadolivre", brands=["kokeshi"])["fila_aberta"]["base"] == 0
    corpo = cliente.get("/api/v1/expedicao/jornada",
                        params={"channel": "mercadolivre"}).json()
    assert corpo["status"] == "ok"
    print("\n=== exemplo mercadolivre fila vazia comprovada ===\n"
          + json.dumps(corpo, ensure_ascii=False, indent=1))


def test_sem_lote_nenhum_continua_unavailable(engine, sessao, ligado, ml_sem_fila):
    with engine.begin() as c:
        c.execute(text(f"DELETE FROM {RUN_TAB} WHERE channel = 'mercadolivre'"))
    r = jornada(sessao, "mercadolivre")
    assert r["status"] == "unavailable"
    assert r["reason"] == "fila_aberta: no_snapshot_published"
    assert r["fila_aberta"] is None


def test_fila_vazia_com_resumo_nao_zerado_nao_vira_zero(sessao, ligado, ml_sem_fila):
    """Fila apagada e ultimo resumo com 10 pedidos: incoerente, nunca base 0."""
    r = jornada(sessao, "mercadolivre")
    assert r["status"] == "unavailable"
    assert r["fila_aberta"] is None
    assert r["reason"] == ("fila_aberta: inconsistent_batch (fila vazia, mas o resumo "
                           "do ultimo lote tem 10 pedido(s) no backlog)")


def test_fila_vazia_com_dois_lotes_no_topo_falha_fechada(engine, sessao, ligado,
                                                         ml_sem_fila):
    a = _resumo_ml_zerado("2532564723", "barbours")
    b = _resumo_ml_zerado("2227056661", "kokeshi")
    b["refresh_batch_id"] = "eeeeeeee-5555-4555-8555-eeeeeeeeeeee"
    _inserir(engine, RUN_TAB, [a, b])
    r = jornada(sessao, "mercadolivre")
    assert r["status"] == "unavailable"
    assert r["reason"] == "fila_aberta: inconsistent_batch"


def test_rota_principal_nao_mudou_com_fila_vazia(engine, sessao, ligado, ml_sem_fila):
    """`_carregar_snapshot` ficou como estava: a `/` segue sem fotografia."""
    _inserir(engine, RUN_TAB, [_resumo_ml_zerado("2532564723", "barbours"),
                               _resumo_ml_zerado("2227056661", "kokeshi")])
    r = svc.get_expedicao(sessao, channel="mercadolivre")
    sessao.rollback()
    assert r["availability"] == "unavailable"
    assert r["unavailable_reason"] == "no_snapshot_published"
