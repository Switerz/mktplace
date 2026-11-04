"""Leitura do Data Mart com reserva pelo Metabase (`datamart_leitura`).

Impede: Metabase usado sem recusa de permissao; erro comum virar chamada ao
Metabase; parametro sem valor ou tipo estranho virar SQL pela metade; cast
`::tipo` ser confundido com parametro; aspas quebrarem o literal.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.config import settings
from app.services import datamart_leitura as dl


class Recusado(Exception):
    """Imita o ProgrammingError do SQLAlchemy com InsufficientPrivilege."""
    def __init__(self):
        super().__init__("(psycopg2.errors.InsufficientPrivilege) permission denied for schema raw")


def _troca(monkeypatch, direto, metabase=None):
    chamadas = []
    monkeypatch.setattr(dl, "_ler_direto", direto)

    def mb(sql):
        chamadas.append(sql)
        return metabase if metabase is not None else [{"captured_date": "2026-11-04T00:00:00Z",
                                                        "captured_at": "2026-11-04T10:10:00Z",
                                                        "seller_reputation": '{"level_id": "5_green"}',
                                                        "base": 99}]
    monkeypatch.setattr(dl, "_ler_metabase", mb)
    return chamadas


def test_leitura_direta_quando_funciona(monkeypatch):
    chamadas = _troca(monkeypatch, lambda sql, p: [{"x": 1}])
    monkeypatch.setattr(settings, "metabase_api_key", "chave")
    assert dl.consultar("SELECT 1", {}) == [{"x": 1}]
    assert chamadas == []


def test_permissao_negada_com_chave_vai_pelo_metabase_e_normaliza(monkeypatch):
    def recusa(sql, p):
        raise Recusado()
    chamadas = _troca(monkeypatch, recusa)
    monkeypatch.setattr(settings, "metabase_api_key", "chave")
    linhas = dl.consultar("SELECT * FROM raw.t WHERE brand = ANY(:marcas) AND d >= :inicio",
                          {"marcas": ["kokeshi"], "inicio": date(2026, 11, 3)})
    assert chamadas == ["SELECT * FROM raw.t WHERE brand = ANY(ARRAY['kokeshi']::text[]) AND d >= DATE '2026-11-03'"]
    assert linhas[0]["captured_date"] == date(2026, 11, 4)
    assert linhas[0]["captured_at"] == datetime(2026, 11, 4, 10, 10, tzinfo=timezone.utc)
    assert linhas[0]["seller_reputation"] == {"level_id": "5_green"}
    assert linhas[0]["base"] == 99


def test_sem_engine_com_chave_vai_pelo_metabase(monkeypatch):
    def sem_engine(sql, p):
        raise RuntimeError("datamart indisponivel")
    chamadas = _troca(monkeypatch, sem_engine)
    monkeypatch.setattr(settings, "metabase_api_key", "chave")
    dl.consultar("SELECT 1", {})
    assert chamadas == ["SELECT 1"]


def test_permissao_negada_sem_chave_sobe_o_erro(monkeypatch):
    def recusa(sql, p):
        raise Recusado()
    chamadas = _troca(monkeypatch, recusa)
    monkeypatch.setattr(settings, "metabase_api_key", "")
    with pytest.raises(Recusado):
        dl.consultar("SELECT 1", {})
    assert chamadas == []


def test_outro_erro_nao_vai_pelo_metabase(monkeypatch):
    def quebra(sql, p):
        raise ValueError("syntax error at or near")
    chamadas = _troca(monkeypatch, quebra)
    monkeypatch.setattr(settings, "metabase_api_key", "chave")
    with pytest.raises(ValueError):
        dl.consultar("SELECT 1", {})
    assert chamadas == []


def test_cast_do_postgres_nao_e_parametro():
    sql = "SELECT (d AT TIME ZONE 'America/Sao_Paulo')::date, '{}'::jsonb, :x"
    assert dl.renderizar(sql, {"x": 1}) == "SELECT (d AT TIME ZONE 'America/Sao_Paulo')::date, '{}'::jsonb, 1"


@pytest.mark.parametrize("valor,esperado", [
    ("unpaid order", "'unpaid order'"),
    ("d'agua", "'d''agua'"),
    (7, "7"),
    (0.5, "0.5"),
    (date(2026, 11, 3), "DATE '2026-11-03'"),
    (datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc), "TIMESTAMPTZ '2026-11-03T12:00:00+00:00'"),
    ([], "ARRAY[]::text[]"),
    (["a", "b"], "ARRAY['a','b']::text[]"),
])
def test_literal(valor, esperado):
    assert dl.literal(valor) == esperado


@pytest.mark.parametrize("valor", [None, True, float("nan"), {"a": 1}, [1, 2], "x\x00y"])
def test_literal_recusa_tipo_fora_da_lista(valor):
    with pytest.raises(dl.ParametroInvalido):
        dl.literal(valor)


def test_parametro_sem_valor_e_erro():
    with pytest.raises(dl.ParametroInvalido):
        dl.renderizar("SELECT :faltando", {})


def test_sql_real_dos_servicos_renderiza_completo():
    """Toda consulta dos servicos de Performance e LDR do ML vira SQL sem `:nome` sobrando."""
    from app.services import ml_ldr_service as ml
    from app.services import performance_oficial_service as po
    casos = [
        (ml.SQL_LDR_ML, {"marcas": ["kokeshi"], "inicio": date(2026, 11, 3), "fim": date(2026, 11, 3),
                         "logisticas": ["cross_docking"], "coletado": ["picked_up"]}),
        (po.SQL_EXISTE, {"tabela": "raw.ml_seller_reputation_snapshot"}),
        (po.SQL_ML, {"marcas": ["kokeshi"]}),
        (po.SQL_SHOPEE, {"marcas": ["apice"]}),
        (po.SQL_SHOPEE_METRICAS, {"marcas": ["apice"]}),
    ]
    for sql, params in casos:
        saida = dl.renderizar(sql, params)
        assert not dl._PARAM.search(saida), sql[:40]


class OperationalError(Exception):
    """Mesmo nome da classe do SQLAlchemy/psycopg2 (falha de conexao)."""


def test_falha_de_conexao_com_chave_vai_pelo_metabase(monkeypatch):
    def cai(sql, p):
        raise OperationalError("could not connect to server")
    chamadas = _troca(monkeypatch, cai)
    monkeypatch.setattr(settings, "metabase_api_key", "chave")
    dl.consultar("SELECT :x", {"x": 1})
    assert chamadas == ["SELECT 1"]


def test_falha_de_conexao_sem_chave_sobe_o_erro(monkeypatch):
    def cai(sql, p):
        raise OperationalError("could not connect to server")
    chamadas = _troca(monkeypatch, cai)
    monkeypatch.setattr(settings, "metabase_api_key", "")
    with pytest.raises(OperationalError):
        dl.consultar("SELECT 1", {})
    assert chamadas == []

