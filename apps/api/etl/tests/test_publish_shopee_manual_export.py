"""SHOPEE-PRODUCTS-KOKESHI-3 — publicador direto da procedencia manual_export.

Tres camadas de prova:
  1. portas offline (allowlist, pasta isolada, janela de mes inteiro, ausencia);
  2. ponta a ponta em arquivos: XLSX sinteticos passando pela transformacao
     OFICIAL do loader, sem banco;
  3. integracao contra PostgreSQL REAL (container descartavel), provando que
     linha `api` nao pode ser apagada — inclusive com contraprova que remove o
     filtro de procedencia e ve a transacao ser desfeita.
A camada 3 so roda com PUBLISHER_TEST_PG_URL definido (nunca o Neon: o teste
recusa host que nao seja localhost).
"""
from __future__ import annotations

import os
from argparse import Namespace
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import openpyxl
import pandas as pd
import pytest

from etl import backfill_shopee_products as bf
from etl import load_shopee_products as loader
from etl import publish_shopee_manual_export as pub

TODAY = date(2026, 10, 20)
HEADER = ["ID do pedido", "Data de criação do pedido", "Nº de referência do SKU principal",
          "Nome do Produto", "Nome da variação", "Quantidade", "Subtotal do produto",
          "Status do pedido", "Nome de usuário (comprador)"]


def _xlsx(path: Path, linhas: list[list]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    for l in linhas:
        ws.append(l)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _export(root: Path, ini: str, fim: str, linhas, brand="kokeshi", part=None):
    sufixo = f"_part_{part[0]}_of_{part[1]}" if part else ""
    _xlsx(root / brand / f"Order.all.order_creation_date.{ini}_{fim}{sufixo}.xlsx", linhas)


def _pedido(oid, quando, sku, qtd, valor, status="Concluído"):
    return [oid, quando, sku, f"Produto {sku}", None, str(qtd), f"{valor:.2f}", status, f"u{oid}"]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Pasta de trabalho fake + pasta isolada com julho e agosto de mes inteiro."""
    trabalho = tmp_path / "shopee"
    trabalho.mkdir()
    monkeypatch.setattr(loader, "SHOPEE_ROOT", trabalho)
    root = tmp_path / "reexport"
    _export(root, "20260701", "20260731", [
        _pedido("A1", "2026-07-05 10:00", "SKU1", 2, 100.0),
        _pedido("A2", "2026-07-06 11:00", "SKU2", 1, 50.0),
        _pedido("A3", "2026-07-07 12:00", "SKU1", 1, 40.0, status="Cancelado"),
    ])
    _export(root, "20260801", "20260831", [
        _pedido("B1", "2026-08-10 10:00", "SKU1", 3, 150.0),
    ])
    return root


# ---------------------------------------------------------------------------
# 1. Portas offline
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("brand", ["apice", "barbours", "lescent", "rituaria"])
def test_recusa_toda_marca_fora_da_kokeshi(brand):
    with pytest.raises(pub.PublishRefused, match="fora da allowlist"):
        pub.assert_brands_allowed([(brand, "2026-08")])


def test_aceita_kokeshi():
    pub.assert_brands_allowed([("kokeshi", "2026-08")])


def test_recusa_pasta_de_trabalho_e_qualquer_parentesco(tmp_path, monkeypatch):
    trabalho = tmp_path / "shopee"
    (trabalho / "kokeshi").mkdir(parents=True)
    monkeypatch.setattr(loader, "SHOPEE_ROOT", trabalho)
    for alvo in (trabalho, trabalho / "kokeshi", tmp_path):
        with pytest.raises(pub.PublishRefused, match="isolada"):
            pub.assert_source_root_isolated(alvo)
    pub.assert_source_root_isolated(tmp_path / "outra")


def _plan(*janelas):
    return {f"f{i}.xlsx": j for i, j in enumerate(janelas)}


def test_janela_semanal_e_recusada():
    with pytest.raises(pub.PublishRefused, match="mes inteiro"):
        pub.assert_full_month_windows("kokeshi", _plan(("20260922", "20260929")),
                                      ["2026-09"], today=TODAY)


def test_janela_que_atravessa_meses_e_recusada():
    with pytest.raises(pub.PublishRefused, match="mes inteiro"):
        pub.assert_full_month_windows("kokeshi", _plan(("20260801", "20260902")),
                                      ["2026-08"], today=TODAY)


def test_mes_fechado_exige_ultimo_dia():
    with pytest.raises(pub.PublishRefused, match="ultimo dia"):
        pub.assert_full_month_windows("kokeshi", _plan(("20260901", "20260929")),
                                      ["2026-09"], today=TODAY)


def test_mes_corrente_vai_ate_d_menos_1_nunca_d0():
    ok = pub.assert_full_month_windows("kokeshi", _plan(("20261001", "20261019")),
                                       ["2026-10"], today=TODAY)
    assert ok == {"2026-10": "20261001_20261019"}
    with pytest.raises(pub.PublishRefused, match="D0"):
        pub.assert_full_month_windows("kokeshi", _plan(("20261001", "20261020")),
                                      ["2026-10"], today=TODAY)


def test_semanal_antiga_misturada_a_reexportacao_e_recusada():
    # O cenario real: a semanal 20260828_20260901 venceria a reexportacao nos
    # dias 28-31/08, porque o loader ordena por janela e nao por download.
    with pytest.raises(pub.PublishRefused):
        pub.assert_full_month_windows(
            "kokeshi", _plan(("20260801", "20260831"), ("20260828", "20260901")),
            ["2026-08"], today=TODAY)


def test_falta_e_sobra_de_competencia_sao_recusadas():
    with pytest.raises(pub.PublishRefused, match="falta"):
        pub.assert_full_month_windows("kokeshi", _plan(("20260801", "20260831")),
                                      ["2026-08", "2026-09"], today=TODAY)
    with pytest.raises(pub.PublishRefused, match="nao pedida"):
        pub.assert_full_month_windows(
            "kokeshi", _plan(("20260801", "20260831"), ("20260901", "20260930")),
            ["2026-08"], today=TODAY)


@pytest.mark.parametrize("modos,erro", [
    ({"kokeshi": "api"}, "procedencia ativa 'api'"),
    ({}, "ausente"),
])
def test_marca_que_nao_le_manual_e_recusada(modos, erro):
    with pytest.raises(pub.PublishRefused, match=erro):
        pub.assert_brands_read_manual(modos, {"kokeshi"})


def test_marca_que_le_manual_passa():
    pub.assert_brands_read_manual({"kokeshi": "manual_export", "apice": "api"}, {"kokeshi"})


def test_todo_predicado_de_escrita_filtra_a_procedencia_manual():
    pred, params = pub.scoped_predicate([("kokeshi", "2026-08"), ("kokeshi", "2026-09")])
    assert pred.endswith("AND source = :src")
    assert params["src"] == "manual_export"
    # nenhum outro valor de procedencia pode entrar pelo parametro
    assert [v for k, v in params.items() if k == "src"] == ["manual_export"]


def test_ausencia_nunca_vira_zero_competencia_vazia_ou_so_gmv_zero():
    vazio = bf.Staging(rows=pd.DataFrame(columns=["brand", "ref_month", "gmv"]),
                       scopes=[("kokeshi", "2026-09")])
    with pytest.raises(pub.PublishRefused, match="sem nenhuma linha"):
        pub.assert_scopes_have_value(vazio)
    zero = bf.Staging(rows=pd.DataFrame({"brand": ["kokeshi"], "ref_month": [pd.Timestamp("2026-08-01")],
                                         "gmv": [0.0]}), scopes=[("kokeshi", "2026-08")])
    with pytest.raises(pub.PublishRefused, match="so com GMV zero"):
        pub.assert_scopes_have_value(zero)


def test_so_a_competencia_corrente_e_parcial(isolated):
    # prepare em 01/09 (agosto ja fechado); marcacao como se fosse 25/08,
    # quando agosto ainda era a competencia corrente.
    prep = pub.prepare([("kokeshi", "2026-07"), ("kokeshi", "2026-08")], isolated,
                       today=date(2026, 9, 1))
    rows = pub.mark_partial(prep.staging, today=date(2026, 8, 25))
    por_mes = rows.groupby(pd.to_datetime(rows["ref_month"]).dt.strftime("%Y-%m"))["is_partial"].unique()
    assert list(por_mes["2026-07"]) == [False]
    assert list(por_mes["2026-08"]) == [True]


# ---------------------------------------------------------------------------
# 2. Ponta a ponta em arquivos (transformacao oficial, sem banco)
# ---------------------------------------------------------------------------

def test_prepare_usa_a_transformacao_oficial_do_loader(isolated):
    prep = pub.prepare([("kokeshi", "2026-07"), ("kokeshi", "2026-08")], isolated, today=TODAY)
    rows = prep.staging.rows
    jul = rows[pd.to_datetime(rows["ref_month"]).dt.strftime("%Y-%m") == "2026-07"]
    # regra Concluido do loader: o cancelado nao entra no GMV
    assert bf._dsum(jul, "gmv") == Decimal("150.00")
    assert prep.windows == {"kokeshi": {"2026-07": "20260701_20260731",
                                        "2026-08": "20260801_20260831"}}


def test_semanal_na_pasta_isolada_reprova_antes_de_ler_linha(isolated):
    _export(isolated, "20260818", "20260824", [_pedido("C1", "2026-08-20 10:00", "SKU9", 1, 10.0)])
    with pytest.raises(pub.PublishRefused, match="mes inteiro"):
        pub.prepare([("kokeshi", "2026-07"), ("kokeshi", "2026-08")], isolated, today=TODAY)


def test_partes_incompletas_reprovam_pela_regra_do_loader(isolated):
    _export(isolated, "20260901", "20260930", [_pedido("D1", "2026-09-02 10:00", "SKU1", 1, 10.0)],
            part=(1, 2))
    with pytest.raises(loader.ShopeeSnapshotError, match="parte ausente"):
        pub.prepare([("kokeshi", "2026-07"), ("kokeshi", "2026-08"), ("kokeshi", "2026-09")],
                    isolated, today=TODAY)


def test_dry_run_offline_mostra_nd_e_nunca_zero(isolated, capsys):
    args = Namespace(scope=["kokeshi:2026-07", "kokeshi:2026-08"], source_root=str(isolated),
                     offline=True, apply=False)
    assert pub.run_dry(args, today=TODAY) == pub.EXIT_OK
    out = capsys.readouterr().out
    assert "NAO lido" in out
    assert "kokeshi:2026-07 | N/D -> " in out
    assert "N/D -> 150.00 | N/D" in out


def test_apply_sem_consentimento_nao_abre_conexao(isolated):
    chamado = []
    args = Namespace(scope=["kokeshi:2026-07"], source_root=str(isolated), offline=False, apply=True)
    codigo = pub.run_apply(args, env={}, engine_factory=lambda *a, **k: chamado.append(1),
                           today=TODAY)
    assert codigo == pub.EXIT_REFUSED
    assert chamado == []


def test_apply_de_marca_da_api_e_recusado_antes_de_conectar(isolated):
    chamado = []
    args = Namespace(scope=["apice:2026-07"], source_root=str(isolated), offline=False, apply=True)
    env = {pub.CONSENT_ENV: "1", "BACKFILL_NEON_RW_URL": "postgresql://u:p@ep-x.neon.tech/db"}
    assert pub.run_apply(args, env=env, engine_factory=lambda *a, **k: chamado.append(1),
                         today=TODAY) == pub.EXIT_REFUSED
    assert chamado == []


def test_o_modulo_nunca_escreve_na_tabela_de_modo():
    fonte = Path(pub.__file__).read_text(encoding="utf-8")
    sql = fonte.upper()
    for proibido in ("UPDATE MARTS.SHOPEE_PRODUCT_SOURCE_MODE", "INSERT INTO MARTS.SHOPEE_PRODUCT_SOURCE_MODE",
                     "DELETE FROM MARTS.SHOPEE_PRODUCT_SOURCE_MODE", "UPDATE {MODE_TABLE}",
                     "INSERT INTO {MODE_TABLE}", "TRUNCATE"):
        assert proibido not in sql, proibido
    assert 'f"DELETE FROM {TABLE} WHERE {pred}"' in fonte
    assert fonte.count("DELETE FROM") == 1


# ---------------------------------------------------------------------------
# 3. Integracao contra PostgreSQL real (container descartavel)
# ---------------------------------------------------------------------------

PG_URL = os.environ.get("PUBLISHER_TEST_PG_URL", "")
needs_pg = pytest.mark.skipif(not PG_URL, reason="PUBLISHER_TEST_PG_URL nao definido")

DDL = """
DROP SCHEMA IF EXISTS marts CASCADE;
CREATE SCHEMA marts;
CREATE TABLE marts.fact_shopee_product_monthly (
    id SERIAL PRIMARY KEY,
    ref_month DATE NOT NULL, brand VARCHAR(50) NOT NULL, sku_ref VARCHAR(100),
    sku_ref_key VARCHAR(100) NOT NULL DEFAULT '', product_name VARCHAR(500) NOT NULL,
    variation_name VARCHAR(200), gmv NUMERIC(18,2) DEFAULT 0, units_sold BIGINT DEFAULT 0,
    completed_orders BIGINT DEFAULT 0, canceled_orders BIGINT DEFAULT 0,
    cancel_rate_pct NUMERIC(8,4), unique_buyers BIGINT DEFAULT 0, avg_price NUMERIC(14,2),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source VARCHAR(32) NOT NULL, source_run_id VARCHAR(64), source_captured_at TIMESTAMPTZ,
    is_partial BOOLEAN NOT NULL DEFAULT false,
    CONSTRAINT ck_shopee_prod_source CHECK (source IN ('api','manual_export')),
    CONSTRAINT ck_shopee_prod_api_tem_captura CHECK (source <> 'api' OR source_captured_at IS NOT NULL),
    CONSTRAINT uq_shopee_prod_mes_marca_sku_nome_fonte UNIQUE (ref_month, brand, sku_ref_key, product_name, source)
);
CREATE TABLE marts.shopee_product_source_mode (brand VARCHAR(64) PRIMARY KEY, source VARCHAR(32) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), note VARCHAR(512));
INSERT INTO marts.shopee_product_source_mode (brand, source) VALUES
    ('apice','api'), ('kokeshi','manual_export');
"""

SEED = """
INSERT INTO marts.fact_shopee_product_monthly
    (ref_month, brand, sku_ref, sku_ref_key, product_name, gmv, units_sold, completed_orders, source, source_captured_at)
VALUES
    -- linhas API, inclusive da PROPRIA kokeshi no MESMO mes: o pior caso
    ('2026-08-01','kokeshi','SKU1','SKU1','Produto SKU1', 999.99, 9, 9, 'api', now()),
    ('2026-08-01','apice','AP1','AP1','Produto AP1', 123.45, 1, 1, 'api', now()),
    -- manual antiga da kokeshi no escopo: uma chave que SOME na reexportacao
    ('2026-08-01','kokeshi','VELHO','VELHO','Produto VELHO', 0, 0, 0, 'manual_export', NULL),
    -- manual fora do escopo: nao pode mudar
    ('2026-06-01','kokeshi','SKU1','SKU1','Produto SKU1', 77.00, 1, 1, 'manual_export', NULL);
"""


@pytest.fixture
def pg():
    if not PG_URL:
        pytest.skip("PUBLISHER_TEST_PG_URL nao definido")
    assert (urlsplit(PG_URL).hostname or "") in {"localhost", "127.0.0.1"}, "so banco local descartavel"
    from sqlalchemy import create_engine, text
    eng = create_engine(PG_URL)
    with eng.begin() as c:
        for stmt in DDL.split(";\n"):
            if stmt.strip():
                c.execute(text(stmt))
        c.execute(text(SEED))
    yield eng
    eng.dispose()


def _snapshot(eng, where):
    from sqlalchemy import text
    with eng.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT ref_month, brand, sku_ref_key, product_name, gmv, units_sold, source, "
            f"source_run_id, source_captured_at, ingested_at FROM marts.fact_shopee_product_monthly "
            f"WHERE {where} ORDER BY 1,2,3,4,7"))]


def _staging_agosto(isolated):
    return pub.prepare([("kokeshi", "2026-08")], _so_agosto(isolated), today=TODAY)


def _so_agosto(isolated):
    # pasta isolada so com agosto, para o escopo bater com a pasta
    import shutil
    alvo = isolated.parent / "so_agosto"
    (alvo / "kokeshi").mkdir(parents=True, exist_ok=True)
    for f in (isolated / "kokeshi").glob("*20260801_20260831*"):
        shutil.copy(f, alvo / "kokeshi" / f.name)
    return alvo


@needs_pg
def test_integracao_publica_manual_e_nao_toca_linha_api(pg, isolated):
    api_antes = _snapshot(pg, "source = 'api'")
    fora_antes = _snapshot(pg, "ref_month = '2026-06-01'")
    prep = _staging_agosto(isolated)
    rows = pub.mark_partial(prep.staging, today=TODAY)
    with pg.connect() as conn:
        res = pub.publish_scoped(prep.staging, rows, executor=pub.ManualExportExecutor(conn, run_id="r1"))
    assert res["code"] == pub.EXIT_OK, res
    assert res["apagadas"] == 1 and res["inseridas"] == 1
    assert _snapshot(pg, "source = 'api'") == api_antes          # linha API intacta, byte a byte
    assert _snapshot(pg, "ref_month = '2026-06-01'") == fora_antes
    manual = _snapshot(pg, "source = 'manual_export' AND ref_month = '2026-08-01'")
    assert [(r[2], r[4], r[7]) for r in manual] == [("SKU1", Decimal("150.00"), "r1")]
    from sqlalchemy import text
    with pg.connect() as c:
        bkp = res["backup"]["table"]
        assert c.execute(text(f"SELECT sku_ref_key FROM {bkp}")).scalars().all() == ["VELHO"]
        assert dict(c.execute(text("SELECT brand, source FROM marts.shopee_product_source_mode")).all()) == \
            {"apice": "api", "kokeshi": "manual_export"}


@needs_pg
def test_contraprova_sem_filtro_de_procedencia_a_transacao_e_desfeita(pg, isolated, monkeypatch):
    """Remove o filtro de `source` do predicado: o DELETE passaria a apagar a
    linha API da kokeshi. O invariante de impressao digital tem de pegar isso
    e desfazer tudo — nenhuma linha muda."""
    api_antes = _snapshot(pg, "source = 'api'")
    manual_antes = _snapshot(pg, "source = 'manual_export'")
    sem_filtro = lambda scopes: bf._scope_predicate(scopes)
    monkeypatch.setattr(pub, "scoped_predicate", sem_filtro)
    prep = _staging_agosto(isolated)
    rows = pub.mark_partial(prep.staging, today=TODAY)
    with pg.connect() as conn:
        res = pub.publish_scoped(prep.staging, rows, executor=pub.ManualExportExecutor(conn, run_id="r2"))
    assert res["code"] == pub.EXIT_ROLLED_BACK, res
    assert _snapshot(pg, "source = 'api'") == api_antes
    assert _snapshot(pg, "source = 'manual_export'") == manual_antes
