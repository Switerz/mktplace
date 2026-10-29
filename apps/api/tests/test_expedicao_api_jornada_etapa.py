"""T-0015 — etapa do pedido na `fila_aberta` de `GET /api/v1/expedicao/jornada`.

PostgreSQL REAL descartavel, nos moldes de `test_expedicao_api_jornada.py`.

COMO A COLUNA `etapa` CHEGA AO BANCO DE TESTE
---------------------------------------------
`alembic upgrade head` REAL (001..033), com o `alembic.ini` e o `versions/`
do repositorio. Assim o DDL testado e' o da migration, nao uma transcricao
dele no teste: se a 033 mudar o tipo, o default ou o CHECK, este arquivo ve'
a mudanca.

Dois bancos, de proposito: `com_coluna` (`upgrade head`) e `sem_coluna`
(`upgrade 032`, a cadeia inteira menos a 033). O caso "sem coluna" compara a resposta INTEIRA
dos dois para provar que nada mudou para quem ainda nao tem a 033.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.schemas.expedicao import JornadaResponse
from app.services import expedicao_service as svc
from tests.test_expedicao_api_jornada import (
    AGORA,
    LOJAS,
    LOTE_ML,
    LOTE_SH,
    PG,
    _app,
    _publicar_fila,
    migrar,
    pedido,
)


# ---------------------------------------------------------------------------
# Bancos
# ---------------------------------------------------------------------------
def _criar(base: str, nome: str, *, alvo: str):
    adm = create_engine(base, isolation_level="AUTOCOMMIT")
    with adm.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {nome} WITH (FORCE)"))
        c.execute(text(f"CREATE DATABASE {nome}"))
    adm.dispose()
    url = base.rsplit("/", 1)[0] + "/" + nome
    migrar(url, alvo=alvo)
    eng = create_engine(url)
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
    return eng


@pytest.fixture(scope="module")
def bancos():
    if not PG.postgres_disponivel():
        pytest.skip(PG.MOTIVO_SEM_POSTGRES)
    with PG.cluster_da_sessao() as base:
        com = _criar(base, "jornada_t0015_com", alvo="head")
        sem = _criar(base, "jornada_t0015_sem", alvo="032")
        yield {"com": com, "sem": sem}
        com.dispose()
        sem.dispose()


@pytest.fixture
def ligado(monkeypatch):
    monkeypatch.setattr(svc.settings, "expedicao_api_enabled", True, raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_ml_api_enabled", True, raising=False)
    # Coorte Shopee fora: o `status` passa a refletir so' a fila aberta, que e'
    # o que esta tarefa muda. A coorte tem os testes dela no arquivo irmao.
    monkeypatch.setattr(svc.settings, "expedicao_shopee_ldr_api_enabled", False,
                        raising=False)
    monkeypatch.setattr(svc.settings, "expedicao_order_ref_secret", "", raising=False)


def _publicar(eng, linhas_por_canal: dict[str, list[dict]]):
    """Fila limpa e republicada; sem a coluna, a chave `etapa` sai da linha."""
    tem = _tem_coluna(eng)
    with eng.begin() as c:
        c.execute(text("DELETE FROM marts.expedicao_fila_atual"))
        c.execute(text("DELETE FROM marts.expedicao_refresh_run"))
    for canal, linhas in linhas_por_canal.items():
        if not tem:
            linhas = [{k: v for k, v in x.items() if k != "etapa"} for x in linhas]
        _publicar_fila(eng, canal, LOTE_SH if canal == "shopee" else LOTE_ML, linhas)


def _tem_coluna(eng) -> bool:
    with eng.connect() as c:
        return bool(c.execute(text(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema="
            "'marts' AND table_name='expedicao_fila_atual' AND column_name='etapa'"
        )).scalar())


def jornada(eng, channel, **kw):
    kw.setdefault("agora", AGORA)
    with sessionmaker(bind=eng)() as s:
        r = svc.get_jornada(s, channel=channel, **kw)
        s.rollback()
    JornadaResponse.model_validate(r)
    return r


def por_sql(eng, canal, marcas=None) -> dict[str, int]:
    """Contagem independente, direto na tabela, para conferir a rota."""
    filtro = " AND lower(brand) = ANY(:m)" if marcas else ""
    with eng.connect() as c:
        return {k: n for k, n in c.execute(text(
            "SELECT etapa, count(*) FROM marts.expedicao_fila_atual "
            f"WHERE channel = :c{filtro} GROUP BY etapa"),
            {"c": canal, **({"m": marcas} if marcas else {})}).all()}


def contagens(fila) -> dict[str, int]:
    return {e["key"]: e["count"] for e in fila["etapas"]}


# ---------------------------------------------------------------------------
# Massa: etapas reais nos dois canais, duas marcas por canal
# ---------------------------------------------------------------------------
def _sh(conta, marca, oid, etapa, **kw):
    return {**pedido("shopee", conta, marca, oid, **kw), "etapa": etapa}


def _ml(conta, marca, oid, etapa, **kw):
    return {**pedido("mercadolivre", conta, marca, oid, status="unavailable", **kw),
            "etapa": etapa}


SHOPEE_REAL = (
    [_sh("apice", "apice", f"AP-{i}", "aguardando_coleta") for i in range(3)]
    + [_sh("apice", "apice", "AP-9", "a_preparar", status="overdue")]
    + [_sh("barbours", "barbours", f"BA-{i}", "aguardando_coleta") for i in range(2)]
)
ML_REAL = (
    [_ml("2532564723", "barbours", f"MB-{i}", "entregue_transportadora")
     for i in range(2)]
    + [_ml("2532564723", "barbours", "MB-9", "a_imprimir")]
    + [_ml("2227056661", "kokeshi", "MK-0", "a_faturar"),
       _ml("2227056661", "kokeshi", "MK-1", "em_separacao"),
       _ml("2227056661", "kokeshi", "MK-2", "em_separacao", horas=72.0),
       _ml("2227056661", "kokeshi", "MK-3", "sinalizado_ml"),
       _ml("2227056661", "kokeshi", "MK-4", "aguardando_coleta")]
)

LIM_SH = svc.LIMITACAO_ETAPA_NAO_PUBLICADA["shopee"]
LIM_ML = svc.LIMITACAO_ETAPA_NAO_PUBLICADA["mercadolivre"]
ORDEM = [k for k, _ in svc.ETAPAS_FILA]


# ---------------------------------------------------------------------------
# Com coluna e etapas reais
# ---------------------------------------------------------------------------
def test_etapas_reais_nos_dois_canais_fecham_com_a_base(bancos, ligado):
    eng = bancos["com"]
    _publicar(eng, {"shopee": SHOPEE_REAL, "mercadolivre": ML_REAL})

    sh = jornada(eng, "shopee")
    assert sh["status"] == "ok" and sh["reason"] is None
    f = sh["fila_aberta"]
    assert f["base"] == 6
    assert [e["key"] for e in f["etapas"]] == ["a_preparar", "aguardando_coleta"]
    assert contagens(f) == por_sql(eng, "shopee") == {
        "a_preparar": 1, "aguardando_coleta": 5}
    assert sum(contagens(f).values()) == f["base"] and f["etapas_fecham"]
    assert LIM_SH not in f["limitacoes"]
    assert "FBS não separado na fila aberta" in f["limitacoes"]
    assert svc.LIMITACAO_ML_ENTREGUE_NO_BACKLOG not in f["limitacoes"]
    # Sobreposicoes inalteradas: o vencido continua contado.
    assert next(s for s in f["sobreposicoes"] if s["key"] == "vencido")["count"] == 1

    ml = jornada(eng, "mercadolivre")
    assert ml["status"] == "ok" and ml["reason"] is None
    f = ml["fila_aberta"]
    assert f["base"] == 8
    assert [e["key"] for e in f["etapas"]] == [
        "a_faturar", "a_imprimir", "em_separacao", "aguardando_coleta",
        "entregue_transportadora", "sinalizado_ml"]
    assert contagens(f) == por_sql(eng, "mercadolivre")
    assert sum(contagens(f).values()) == f["base"] and f["etapas_fecham"]
    rotulos = {e["key"]: e["label"] for e in f["etapas"]}
    assert rotulos["entregue_transportadora"] == "Entregue à transportadora"
    assert rotulos["em_separacao"] == "Em separação"
    assert LIM_ML not in f["limitacoes"]
    assert svc.LIMITACAO_ML_ENTREGUE_NO_BACKLOG in f["limitacoes"]
    assert "Full (fulfillment) fora da fila por contrato" in f["limitacoes"]
    # Coorte do ML continua nula e as sobreposicoes de prazo continuam None.
    assert ml["coorte"] is None
    assert next(s for s in f["sobreposicoes"] if s["key"] == "vencido")["count"] is None
    assert next(s for s in f["sobreposicoes"] if s["key"] == "acima_48h")["count"] == 1


@pytest.mark.parametrize("canal,marcas,esperado", [
    ("shopee", ["Barbours"], {"aguardando_coleta": 2}),
    ("shopee", ["APICE"], {"a_preparar": 1, "aguardando_coleta": 3}),
    ("mercadolivre", ["barbours"], {"a_imprimir": 1, "entregue_transportadora": 2}),
    ("mercadolivre", ["Kokeshi"], {"a_faturar": 1, "em_separacao": 2,
                                   "aguardando_coleta": 1, "sinalizado_ml": 1}),
])
def test_filtro_de_marca_vale_para_as_etapas(bancos, ligado, canal, marcas, esperado):
    eng = bancos["com"]
    _publicar(eng, {"shopee": SHOPEE_REAL, "mercadolivre": ML_REAL})
    f = jornada(eng, canal, brands=marcas)["fila_aberta"]
    assert contagens(f) == esperado
    assert contagens(f) == por_sql(eng, canal, [m.lower() for m in marcas])
    assert f["base"] == sum(esperado.values())
    # Ordem estavel do fluxo, qualquer que seja o recorte.
    chaves = [e["key"] for e in f["etapas"]]
    assert chaves == sorted(chaves, key=ORDEM.index)


def test_valor_outro_e_listado_antes_de_nao_publicada_e_fecha(bancos, ligado):
    eng = bancos["com"]
    linhas = SHOPEE_REAL + [_sh("barbours", "barbours", "BA-X", "outro")]
    _publicar(eng, {"shopee": linhas})
    r = jornada(eng, "shopee")
    f = r["fila_aberta"]
    assert r["status"] == "ok"
    assert f["etapas"][-1] == {"key": "outro", "label": "Outro status da fonte",
                               "count": 1}
    assert sum(contagens(f).values()) == f["base"] == 7


def test_rota_http_serve_as_etapas(bancos, ligado, monkeypatch):
    from fastapi.testclient import TestClient

    from app.database import get_db

    eng = bancos["com"]
    _publicar(eng, {"mercadolivre": ML_REAL})
    app = _app()
    monkeypatch.setattr(svc, "_agora", lambda: AGORA)
    S = sessionmaker(bind=eng)

    def _db():
        with S() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    try:
        r = TestClient(app).get("/api/v1/expedicao/jornada",
                                params={"channel": "mercadolivre",
                                        "brands": "barbours"})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 200
    f = r.json()["fila_aberta"]
    assert {e["key"]: e["count"] for e in f["etapas"]} == {
        "a_imprimir": 1, "entregue_transportadora": 2}


# ---------------------------------------------------------------------------
# Sem coluna, ou so' `nao_publicada`: a resposta de hoje
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("canal", ["shopee", "mercadolivre"])
def test_sem_coluna_a_resposta_e_a_de_hoje(bancos, ligado, canal):
    massa = {"shopee": SHOPEE_REAL, "mercadolivre": ML_REAL}
    _publicar(bancos["sem"], massa)
    r = jornada(bancos["sem"], canal)
    f = r["fila_aberta"]
    assert r["status"] == "ok"
    assert f["etapas"] == [{"key": "aguardando_expedicao",
                            "label": "Aguardando expedição", "count": f["base"]}]
    assert f["limitacoes"] == svc.LIMITACOES_FILA[canal]

    # Com a coluna mas tudo `nao_publicada` (janela entre a 033 e a proxima
    # publicacao): a resposta INTEIRA e' igual a' do banco sem a coluna.
    tudo_default = {k: [{**x, "etapa": "nao_publicada"} for x in v]
                    for k, v in massa.items()}
    _publicar(bancos["com"], tudo_default)
    assert jornada(bancos["com"], canal) == r


def test_fila_vazia_com_coluna_continua_base_zero(bancos, ligado):
    """Sem linha nenhuma nao ha' etapa real: etapa unica com zero, como hoje."""
    eng = bancos["com"]
    _publicar(eng, {"mercadolivre": ML_REAL})
    with eng.begin() as c:
        c.execute(text("DELETE FROM marts.expedicao_fila_atual"))
        c.execute(text(
            "UPDATE marts.expedicao_refresh_run SET backlog_count = 0, "
            "deadline_unavailable_count = 0, over_48h_count = 0"))
    f = jornada(eng, "mercadolivre")["fila_aberta"]
    assert f["base"] == 0
    assert [e["key"] for e in f["etapas"]] == ["aguardando_expedicao"]
    assert f["limitacoes"] == svc.LIMITACOES_FILA["mercadolivre"]


# ---------------------------------------------------------------------------
# Mistura: parte com etapa real, parte `nao_publicada`
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("canal", ["shopee", "mercadolivre"])
def test_mistura_vira_partial_sem_reconciliar_a_forca(bancos, ligado, canal):
    eng = bancos["com"]
    massa = {"shopee": SHOPEE_REAL, "mercadolivre": ML_REAL}[canal]
    misturada = [{**x, "etapa": "nao_publicada"} if i % 3 == 0 else x
                 for i, x in enumerate(massa)]
    _publicar(eng, {canal: misturada})
    r = jornada(eng, canal)
    f = r["fila_aberta"]
    assert r["status"] == "partial"
    assert r["reason"] == "fila_aberta: etapa_parcial"
    assert f["etapas"][-1]["key"] == "nao_publicada"
    assert f["etapas"][-1]["label"] == "Etapa ainda não publicada"
    assert f["etapas"][-1]["count"] == sum(1 for i in range(len(massa)) if i % 3 == 0)
    assert contagens(f) == por_sql(eng, canal)
    assert sum(contagens(f).values()) == f["base"] == len(massa)
    assert f["etapas_fecham"] is True
    # Parte do lote ainda sem etapa: a limitacao antiga continua valendo.
    assert svc.LIMITACAO_ETAPA_NAO_PUBLICADA[canal] in f["limitacoes"]


def test_bancos_de_teste_sao_os_esperados(bancos):
    """Trava a premissa: um banco com a 033, outro sem."""
    assert _tem_coluna(bancos["com"]) is True
    assert _tem_coluna(bancos["sem"]) is False
    with bancos["com"].connect() as c:
        definicao = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_efa_dominio_etapa'")).scalar()
    for chave in ORDEM:
        assert f"'{chave}'" in definicao


def test_ordem_das_etapas_e_o_dominio_do_contrato():
    """A lista de etapas da API e' o dominio do CHECK, na ordem do fluxo."""
    assert ORDEM == ["a_faturar", "a_imprimir", "em_separacao", "a_preparar",
                     "aguardando_coleta", "entregue_transportadora",
                     "sinalizado_ml", "outro", "nao_publicada"]
    assert len(set(ORDEM)) == 9
