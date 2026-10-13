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
                     offline=True, apply=False, audit_dir=str(isolated.parent / "audit"))
    assert pub.run_dry(args, today=TODAY) == pub.EXIT_OK
    out = capsys.readouterr().out
    assert "NAO lido" in out
    assert "kokeshi:2026-07 | N/D -> " in out
    assert "N/D -> 150.00 | N/D" in out


def test_apply_sem_consentimento_nao_abre_conexao(isolated):
    chamado = []
    args = Namespace(scope=["kokeshi:2026-07"], source_root=str(isolated), offline=False, apply=True,
                     audit_dir=str(isolated.parent / "audit"))
    codigo = pub.run_apply(args, env={}, engine_factory=lambda *a, **k: chamado.append(1),
                           today=TODAY)
    assert codigo == pub.EXIT_REFUSED
    assert chamado == []


def test_apply_de_marca_da_api_e_recusado_antes_de_conectar(isolated):
    chamado = []
    args = Namespace(scope=["apice:2026-07"], source_root=str(isolated), offline=False, apply=True,
                     audit_dir=str(isolated.parent / "audit"))
    env = {pub.CONSENT_ENV: "1", "BACKFILL_NEON_RW_URL": "postgresql://u:p@ep-x.neon.tech/db"}
    assert pub.run_apply(args, env=env, engine_factory=lambda *a, **k: chamado.append(1),
                         today=TODAY) == pub.EXIT_REFUSED
    assert chamado == []


def _codigo_executavel(fonte: str) -> str:
    """Fonte sem comentarios e sem docstrings: so o que de fato executa. O
    comentario que DOCUMENTA os grants proibidos (UPDATE, TRUNCATE) nao conta."""
    sem_comentario = "\n".join(l for l in fonte.splitlines() if not l.strip().startswith("#"))
    return "\n".join(sem_comentario.split('"""')[::2])


def test_o_modulo_nunca_escreve_na_tabela_de_modo():
    fonte = Path(pub.__file__).read_text(encoding="utf-8")
    codigo = _codigo_executavel(fonte)
    assert "def publish_scoped" in codigo and len(codigo) > 5000   # o recorte nao apagou o modulo
    sql = codigo.upper()
    for proibido in ("UPDATE MARTS.SHOPEE_PRODUCT_SOURCE_MODE", "INSERT INTO MARTS.SHOPEE_PRODUCT_SOURCE_MODE",
                     "DELETE FROM MARTS.SHOPEE_PRODUCT_SOURCE_MODE", "UPDATE {MODE_TABLE}",
                     "INSERT INTO {MODE_TABLE}", "TRUNCATE", "UPDATE MARTS.", "UPDATE {TABLE}"):
        assert proibido not in sql, proibido
    assert 'f"DELETE FROM {TABLE} WHERE {pred}"' in codigo
    assert codigo.count("DELETE FROM") == 1


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


# ===========================================================================
# REREVIEW — correcoes do CHANGES_REQUIRED do #76
# ===========================================================================

import json
import subprocess
import sys as _sys

TODAY_OUT = date(2026, 10, 13)


# --- MAJOR 1: competencia futura -------------------------------------------

def test_scope_futuro_e_recusado():
    with pytest.raises(pub.PublishRefused, match="futura"):
        pub.parse_scopes(["kokeshi:2026-11"], today=TODAY_OUT)
    assert pub.parse_scopes(["kokeshi:2026-10"], today=TODAY_OUT) == [("kokeshi", "2026-10")]


def test_janela_de_mes_futuro_e_recusada_e_nunca_vira_fechada():
    # Antes: 20261101_20261130 passava como "mes fechado" (termina no ultimo dia).
    with pytest.raises(pub.PublishRefused, match="FUTURA"):
        pub.assert_full_month_windows("kokeshi", _plan(("20261101", "20261130")),
                                      ["2026-11"], today=TODAY_OUT)
    with pytest.raises(pub.PublishRefused, match="FUTURA"):
        pub.assert_full_month_windows("kokeshi", _plan(("20270101", "20270131")),
                                      ["2027-01"], today=TODAY_OUT)


def test_apply_com_scope_futuro_recusa_antes_de_conectar(isolated):
    chamado = []
    args = Namespace(scope=["kokeshi:2026-11"], source_root=str(isolated), offline=False, apply=True,
                     audit_dir=str(isolated.parent / "audit"))
    env = {pub.CONSENT_ENV: "1", "BACKFILL_NEON_RW_URL": "postgresql://u:p@ep-x.neon.tech/db"}
    assert pub.run_apply(args, env=env, engine_factory=lambda *a, **k: chamado.append(1),
                         today=TODAY_OUT) == pub.EXIT_REFUSED
    assert chamado == []


# --- MAJOR 2: isolamento real ----------------------------------------------

windows_only = pytest.mark.skipif(not _sys.platform.startswith("win"), reason="junction e de Windows")


def _junction(link: Path, alvo: Path) -> None:
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(alvo)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"mklink /J indisponivel: {r.stderr.strip()[:80]}")


@windows_only
def test_junction_da_pasta_da_marca_para_a_pasta_antiga_e_recusada(tmp_path, monkeypatch):
    trabalho = tmp_path / "shopee"
    antigo = trabalho / "kokeshi"
    _export(trabalho, "20260801", "20260831", [_pedido("B1", "2026-08-10 10:00", "SKU1", 1, 10.0)])
    monkeypatch.setattr(loader, "SHOPEE_ROOT", trabalho)
    root = tmp_path / "isolada"
    root.mkdir()
    _junction(root / "kokeshi", antigo)
    assert pub.is_link_like(root / "kokeshi")
    pub.assert_source_root_isolated(root)          # a raiz em si e honesta...
    with pytest.raises(pub.PublishRefused, match="junction|reparse|fora da raiz"):
        pub.list_brand_files(root, "kokeshi")      # ...a pasta da marca nao
    with pytest.raises(pub.PublishRefused):
        pub.prepare([("kokeshi", "2026-08")], root, today=TODAY_OUT)


@windows_only
def test_raiz_junction_e_recusada(tmp_path, monkeypatch):
    trabalho = tmp_path / "shopee"
    trabalho.mkdir()
    monkeypatch.setattr(loader, "SHOPEE_ROOT", trabalho)
    real = tmp_path / "real"
    (real / "kokeshi").mkdir(parents=True)
    _junction(tmp_path / "atalho", real)
    with pytest.raises(pub.PublishRefused, match="symlink/junction"):
        pub.assert_source_root_isolated(tmp_path / "atalho")


def test_arquivo_symlink_para_fora_e_recusado(tmp_path, monkeypatch):
    trabalho = tmp_path / "shopee"
    trabalho.mkdir()
    monkeypatch.setattr(loader, "SHOPEE_ROOT", trabalho)
    fora = tmp_path / "fora.xlsx"
    _xlsx(fora, [_pedido("B1", "2026-08-10 10:00", "SKU1", 1, 10.0)])
    root = tmp_path / "isolada"
    (root / "kokeshi").mkdir(parents=True)
    try:
        os.symlink(fora, root / "kokeshi" / "Order.all.order_creation_date.20260801_20260831.xlsx")
    except (OSError, NotImplementedError):
        pytest.skip("sem privilegio para symlink de arquivo")
    with pytest.raises(pub.PublishRefused, match="link"):
        pub.list_brand_files(root, "kokeshi")


def test_arquivo_estranho_ou_subpasta_na_pasta_isolada_e_recusado(isolated):
    (isolated / "kokeshi" / "Dados+Gerais+de+Anuncios.csv").write_text("x")
    with pytest.raises(pub.PublishRefused, match="nao e export"):
        pub.list_brand_files(isolated, "kokeshi")
    (isolated / "kokeshi" / "Dados+Gerais+de+Anuncios.csv").unlink()
    (isolated / "kokeshi" / "sub").mkdir()
    with pytest.raises(pub.PublishRefused, match="subpasta"):
        pub.list_brand_files(isolated, "kokeshi")


# --- item 6: lista unica e imutavel ---------------------------------------

def test_a_pasta_e_listada_uma_unica_vez_e_o_loader_nao_relista(isolated, monkeypatch):
    chamadas = []
    original = pub.list_brand_files
    monkeypatch.setattr(pub, "list_brand_files", lambda r, b: chamadas.append(b) or original(r, b))

    def proibido(*a, **k):
        raise AssertionError("o loader relistou a pasta")
    monkeypatch.setattr(loader, "_find_xlsx", proibido)
    prep = pub.prepare([("kokeshi", "2026-07"), ("kokeshi", "2026-08")], isolated, today=TODAY)
    assert chamadas == ["kokeshi"]
    assert loader._find_xlsx is proibido               # restaurado depois do preparo
    assert all(len(f.sha256) == 64 for f in prep.files["kokeshi"])


def test_arquivo_alterado_depois_do_hash_e_recusado(isolated):
    files = pub.list_brand_files(isolated, "kokeshi")
    with open(files[0].path, "ab") as fh:
        fh.write(b"\x00")
    with pytest.raises(pub.PublishRefused, match="mudou"):
        pub.assert_files_unchanged(files)


# --- item 7: limites --------------------------------------------------------

def test_limites_de_bytes_arquivos_e_linhas(isolated, monkeypatch):
    monkeypatch.setattr(pub, "MAX_FILE_BYTES", 10)
    with pytest.raises(pub.PublishRefused, match="excede"):
        pub.list_brand_files(isolated, "kokeshi")
    monkeypatch.setattr(pub, "MAX_FILE_BYTES", 8 * 1024 * 1024)
    monkeypatch.setattr(pub, "MAX_FILES_PER_BRAND", 1)
    with pytest.raises(pub.PublishRefused, match="limite"):
        pub.list_brand_files(isolated, "kokeshi")
    monkeypatch.setattr(pub, "MAX_FILES_PER_BRAND", 150)
    monkeypatch.setattr(pub, "MAX_ROWS_PER_FILE", 1)
    with pytest.raises(pub.PublishRefused, match="linhas > limite"):
        pub.prepare([("kokeshi", "2026-07"), ("kokeshi", "2026-08")], isolated, today=TODAY)


def test_limites_cobrem_os_exports_reais_com_folga():
    # medidos em 13/10: 66 arquivos, 122,6 MB, maior parte 1,92 MB
    assert pub.MAX_FILES_PER_BRAND >= 66 * 2
    assert pub.MAX_TOTAL_BYTES >= 122_590_813 * 3
    assert pub.MAX_FILE_BYTES >= 1_920_625 * 4


# --- item 8: datas invalidas, formulas, celula vazia, linha descartada ------

def _so_um_mes(tmp_path, monkeypatch, linhas):
    trabalho = tmp_path / "shopee"
    trabalho.mkdir(exist_ok=True)
    monkeypatch.setattr(loader, "SHOPEE_ROOT", trabalho)
    root = tmp_path / "iso"
    _export(root, "20260801", "20260831", linhas)
    return root


@pytest.mark.parametrize("linha,erro", [
    (_pedido("B1", "2026-13-40 10:00", "SKU1", 1, 10.0), "data"),
    (_pedido("B1", "10/08/2026", "SKU1", 1, 10.0), "data"),
    (_pedido("B1", "2026-09-01 10:00", "SKU1", 1, 10.0), "fora da janela"),
    (["B1", "2026-08-10 10:00", "SKU1", "Produto", None, "1", None, "Concluído", "u"], "vazia"),
    (["B1", "2026-08-10 10:00", "SKU1", "Produto", None, None, "10.00", "Concluído", "u"], "vazia"),
    (["B1", "2026-08-10 10:00", "SKU1", None, None, "1", "10.00", "Concluído", "u"], "vazia"),
    (["B1", "2026-08-10 10:00", "SKU1", "Produto", None, "1", "abc", "Concluído", "u"], "invalido"),
    (["B1", "2026-08-10 10:00", "SKU1", "Produto", None, "1.5", "10.00", "Concluído", "u"], "invalido"),
])
def test_celula_ruim_e_recusada_nunca_vira_zero(tmp_path, monkeypatch, linha, erro):
    root = _so_um_mes(tmp_path, monkeypatch, [_pedido("B0", "2026-08-09 10:00", "SKU1", 1, 10.0), linha])
    with pytest.raises(pub.PublishRefused, match=erro):
        pub.prepare([("kokeshi", "2026-08")], root, today=TODAY_OUT)


def test_formula_sem_valor_e_recusada(tmp_path, monkeypatch):
    root = _so_um_mes(tmp_path, monkeypatch, [
        ["B1", "2026-08-10 10:00", "SKU1", "Produto", None, "1", "=5+5", "Concluído", "u"]])
    with pytest.raises(pub.PublishRefused, match="formula"):
        pub.prepare([("kokeshi", "2026-08")], root, today=TODAY_OUT)


def test_linha_descartada_em_silencio_pela_transformacao_e_recusada(isolated, monkeypatch):
    original = bf.build_staging

    def perde_uma(scopes, root):
        st = original(scopes, root)
        st.rows = st.rows.iloc[1:].reset_index(drop=True)
        return st
    monkeypatch.setattr(bf, "build_staging", perde_uma)
    with pytest.raises(pub.PublishRefused, match="nao reproduz o bruto"):
        pub.prepare([("kokeshi", "2026-07"), ("kokeshi", "2026-08")], isolated, today=TODAY)


# --- item 5: manifesto auditavel -------------------------------------------

def test_manifesto_tem_run_id_hashes_competencias_resultado_e_nunca_dsn(isolated):
    audit = isolated.parent / "audit"
    args = Namespace(scope=["kokeshi:2026-07", "kokeshi:2026-08"], source_root=str(isolated),
                     offline=True, apply=False, audit_dir=str(audit))
    assert pub.run_dry(args, env={"BACKFILL_NEON_RO_URL": "postgresql://u:segredo@h.neon.tech/db"},
                       today=TODAY) == pub.EXIT_OK
    [arq] = list(audit.glob("*.json"))
    m = json.loads(arq.read_text(encoding="utf-8"))
    assert len(m["run_id"]) == 32
    assert m["scopes"] == ["kokeshi:2026-07", "kokeshi:2026-08"]
    assert {f["name"] for f in m["files"]["kokeshi"]} == {p.name for p in (isolated / "kokeshi").iterdir()}
    assert all(len(f["sha256"]) == 64 for f in m["files"]["kokeshi"])
    assert m["result_code"] == 0
    assert set(m) <= pub.MANIFEST_KEYS
    texto = arq.read_text(encoding="utf-8")
    assert "segredo" not in texto and "postgresql://" not in texto


# --- PostgreSQL 16: fora do escopo e source_mode concorrente ----------------

@needs_pg
def test_regressao_que_apaga_junho_faz_rollback(pg, isolated, monkeypatch):
    junho_antes = _snapshot(pg, "ref_month = '2026-06-01'")
    manual_antes = _snapshot(pg, "source = 'manual_export'")
    original = pub.ManualExportExecutor.delete_scope

    def apaga_junho_tambem(self, scopes):
        n = original(self, scopes)
        from sqlalchemy import text
        return n + self.conn.execute(text(
            "DELETE FROM marts.fact_shopee_product_monthly WHERE ref_month = '2026-06-01'")).rowcount
    monkeypatch.setattr(pub.ManualExportExecutor, "delete_scope", apaga_junho_tambem)
    prep = _staging_agosto(isolated)
    with pg.connect() as conn:
        res = pub.publish_scoped(prep.staging, pub.mark_partial(prep.staging, today=TODAY),
                                 executor=pub.ManualExportExecutor(conn, run_id="r3"))
    assert res["code"] == pub.EXIT_ROLLED_BACK, res
    assert "FORA do escopo" in res["erro"]
    assert _snapshot(pg, "ref_month = '2026-06-01'") == junho_antes
    assert _snapshot(pg, "source = 'manual_export'") == manual_antes


@needs_pg
def test_source_mode_trocado_antes_da_mutacao_faz_recusar(pg, isolated):
    from sqlalchemy import text
    manual_antes = _snapshot(pg, "source = 'manual_export'")
    prep = _staging_agosto(isolated)               # o preparo/CLI ja viu manual_export
    with pg.begin() as c:                          # alguem liga a kokeshi na API
        c.execute(text("UPDATE marts.shopee_product_source_mode SET source='api' WHERE brand='kokeshi'"))
    with pg.connect() as conn:
        res = pub.publish_scoped(prep.staging, pub.mark_partial(prep.staging, today=TODAY),
                                 executor=pub.ManualExportExecutor(conn, run_id="r4"))
    assert res["code"] == pub.EXIT_ROLLED_BACK, res
    assert "leitura inicial" in res["erro"] and "procedencia ativa 'api'" in res["erro"]
    assert _snapshot(pg, "source = 'manual_export'") == manual_antes


# ===========================================================================
# SHOPEE-PUBLISHER-3 — privilegio minimo, M04, M15, manifesto e formulas
# ===========================================================================

import zipfile as _zipfile

ROLE = "publicador_min"
ROLE_PWD = "efemero_role"

#: Os grants EXATOS documentados em `publish_shopee_manual_export` — e nada mais.
GRANTS_MINIMOS = [
    "GRANT CONNECT ON DATABASE {db} TO {r}",
    "GRANT USAGE, CREATE ON SCHEMA marts TO {r}",
    "GRANT SELECT, INSERT, DELETE ON marts.fact_shopee_product_monthly TO {r}",
    "GRANT USAGE ON SEQUENCE marts.fact_shopee_product_monthly_id_seq TO {r}",
    "GRANT SELECT ON marts.shopee_product_source_mode TO {r}",
]


@pytest.fixture
def role_min(pg):
    """Role NAO superusuaria com exatamente os grants minimos. Devolve a engine dela."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    url = make_url(PG_URL)
    with pg.begin() as c:
        c.execute(text(f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} LOGIN PASSWORD '{ROLE_PWD}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
            END IF; END $$"""))
        for g in GRANTS_MINIMOS:
            c.execute(text(g.format(db=url.database, r=ROLE)))
    eng = create_engine(url.set(username=ROLE, password=ROLE_PWD))
    yield eng
    eng.dispose()


def _backup_tables(pg):
    from sqlalchemy import text
    with pg.connect() as c:
        return c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'marts' "
                              "AND tablename LIKE '%\\_bkp\\_%'")).scalars().all()


@needs_pg
def test_role_minima_nao_e_superusuaria_e_nao_tem_update(pg, role_min):
    from sqlalchemy import text
    with pg.connect() as c:
        r = c.execute(text("SELECT rolsuper, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname = :r"),
                      {"r": ROLE}).one()
        assert tuple(r) == (False, False, False)
        for tabela in ("marts.shopee_product_source_mode", "marts.fact_shopee_product_monthly"):
            for priv in ("UPDATE", "TRUNCATE"):
                assert c.execute(text("SELECT has_table_privilege(:r, :t, :p)"),
                                 {"r": ROLE, "t": tabela, "p": priv}).scalar() is False, (tabela, priv)


@needs_pg
def test_role_minima_nao_consegue_alterar_o_source_mode(role_min):
    from sqlalchemy import text
    from sqlalchemy.exc import ProgrammingError
    with role_min.connect() as c:
        with pytest.raises(ProgrammingError, match="permission denied"):
            c.execute(text("UPDATE marts.shopee_product_source_mode SET source='api' WHERE brand='kokeshi'"))


@needs_pg
def test_apply_completo_com_a_role_minima(pg, role_min, isolated):
    """O apply inteiro — privilegios, lock, leituras de modo, backup, DELETE,
    INSERT, invariantes e commit — com a role de privilegio minimo."""
    api_antes = _snapshot(pg, "source = 'api'")
    fora_antes = _snapshot(pg, "NOT (brand = 'kokeshi' AND ref_month = '2026-08-01' AND source = 'manual_export')")
    prep = _staging_agosto(isolated)
    with role_min.connect() as conn:
        res = pub.apply_on_connection(conn, prep, run_id="rmin", today=TODAY)
    assert res["code"] == pub.EXIT_OK, res
    assert _snapshot(pg, "source = 'api'") == api_antes
    assert _snapshot(pg, "NOT (brand = 'kokeshi' AND ref_month = '2026-08-01' AND source = 'manual_export')") == fora_antes
    manual = _snapshot(pg, "source = 'manual_export' AND ref_month = '2026-08-01'")
    assert [(r[2], r[4], r[7]) for r in manual] == [("SKU1", Decimal("150.00"), "rmin")]
    from sqlalchemy import text
    with pg.connect() as c:
        assert dict(c.execute(text("SELECT brand, source FROM marts.shopee_product_source_mode")).all()) == \
            {"apice": "api", "kokeshi": "manual_export"}
        assert c.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")).scalar() == 0


@needs_pg
@pytest.mark.parametrize("revogar,esperado", [
    ("REVOKE INSERT ON marts.fact_shopee_product_monthly FROM {r}", "INSERT em marts.fact_shopee_product_monthly"),
    ("REVOKE DELETE ON marts.fact_shopee_product_monthly FROM {r}", "DELETE em marts.fact_shopee_product_monthly"),
    ("REVOKE SELECT ON marts.shopee_product_source_mode FROM {r}", "SELECT em marts.shopee_product_source_mode"),
    ("REVOKE CREATE ON SCHEMA marts FROM {r}", "CREATE no schema marts"),
    ("REVOKE USAGE ON SEQUENCE marts.fact_shopee_product_monthly_id_seq FROM {r}", "USAGE na sequence"),
])
def test_grant_faltando_falha_no_preflight_antes_do_backup(pg, role_min, isolated, revogar, esperado):
    from sqlalchemy import text
    with pg.begin() as c:
        c.execute(text(revogar.format(r=ROLE)))
    manual_antes = _snapshot(pg, "source = 'manual_export'")
    prep = _staging_agosto(isolated)
    with role_min.connect() as conn:
        with pytest.raises(pub.PublishRefused, match=esperado) as e:
            pub.apply_on_connection(conn, prep, run_id="rrev", today=TODAY)
    msg = str(e.value)
    assert ROLE not in msg and "127.0.0.1" not in msg and ROLE_PWD not in msg
    assert _backup_tables(pg) == []                # nenhum backup deixado para tras
    assert _snapshot(pg, "source = 'manual_export'") == manual_antes


@needs_pg
def test_troca_concorrente_durante_a_transacao_e_pega_antes_do_commit(pg, isolated, monkeypatch):
    """Troca do interruptor COMMITADA por outra sessao no meio da transacao de
    mutacao (depois da 1a leitura): a 2a leitura, antes do commit, recusa."""
    from sqlalchemy import create_engine, text
    outro = create_engine(PG_URL)
    original = pub.ManualExportExecutor.insert_rows

    def troca_no_meio(self, rows):
        with outro.begin() as c:
            c.execute(text("UPDATE marts.shopee_product_source_mode SET source='api' WHERE brand='kokeshi'"))
        return original(self, rows)
    monkeypatch.setattr(pub.ManualExportExecutor, "insert_rows", troca_no_meio)
    manual_antes = _snapshot(pg, "source = 'manual_export'")
    api_antes = _snapshot(pg, "source = 'api'")
    prep = _staging_agosto(isolated)
    with pg.connect() as conn:
        res = pub.publish_scoped(prep.staging, pub.mark_partial(prep.staging, today=TODAY),
                                 executor=pub.ManualExportExecutor(conn, run_id="r6"))
    outro.dispose()
    assert res["code"] == pub.EXIT_ROLLED_BACK, res
    assert "leitura pre-commit" in res["erro"]
    assert _snapshot(pg, "source = 'manual_export'") == manual_antes
    assert _snapshot(pg, "source = 'api'") == api_antes


def test_conjunto_de_modo_tem_de_ser_exato():
    pub.assert_modes_exactly_manual({"kokeshi": "manual_export"}, {"kokeshi"}, momento="t")
    for modos, erro in (({}, "ausente"), ({"kokeshi": "api"}, "procedencia ativa 'api'"),
                        ({"kokeshi": "manual_export", "apice": "api"}, "inesperada")):
        with pytest.raises(pub.PublishRefused, match=erro):
            pub.assert_modes_exactly_manual(modos, {"kokeshi"}, momento="t")


def test_o_modulo_nao_usa_lock_de_linha():
    fonte = Path(pub.__file__).read_text(encoding="utf-8")
    codigo = "\n".join(l for l in fonte.splitlines() if not l.strip().startswith("#"))
    executavel = codigo.split('"""')[::2]           # fora das docstrings
    for trecho in executavel:
        for proibido in ('" FOR SHARE', '" FOR UPDATE', "FOR SHARE\"", "FOR UPDATE\""):
            assert proibido not in trecho


# --- M04: arquivo resolvido fora da raiz ------------------------------------

def test_m04_arquivo_que_resolve_fora_da_pasta_e_recusado(isolated, tmp_path, monkeypatch):
    fora = tmp_path / "fora_da_raiz.xlsx"
    _xlsx(fora, [_pedido("Z1", "2026-08-10 10:00", "SKU1", 1, 10.0)])
    alvo = sorted((isolated / "kokeshi").iterdir())[0].name
    real = Path.resolve

    def resolve_controlado(self, *a, **k):
        r = real(self, *a, **k)
        return real(fora, *a, **k) if r.name == alvo else r
    monkeypatch.setattr(Path, "resolve", resolve_controlado)
    assert not pub.is_link_like(isolated / "kokeshi" / alvo)      # nao e link: so o containment pega
    with pytest.raises(pub.PublishRefused, match="resolve fora da pasta"):
        pub.list_brand_files(isolated, "kokeshi")


def test_m04_pasta_da_marca_que_resolve_fora_da_raiz_e_recusada(isolated, tmp_path, monkeypatch):
    outra = tmp_path / "outra_raiz" / "kokeshi"
    outra.mkdir(parents=True)
    real = Path.resolve

    def resolve_controlado(self, *a, **k):
        r = real(self, *a, **k)
        return real(outra, *a, **k) if r == real(isolated / "kokeshi") else r
    monkeypatch.setattr(Path, "resolve", resolve_controlado)
    with pytest.raises(pub.PublishRefused, match="resolve fora da raiz"):
        pub.list_brand_files(isolated, "kokeshi")


# --- M15: arquivo trocado durante o preparo ---------------------------------

def _troca_arquivo_depois_da_staging(monkeypatch, isolated):
    original = bf.build_staging

    def build_e_troca(scopes, root):
        st = original(scopes, root)                 # a staging ja leu os arquivos
        alvo = sorted((isolated / "kokeshi").iterdir())[0]
        _xlsx(alvo, [_pedido("X9", "2026-07-20 10:00", "SKU7", 9, 999.0)])   # outro conteudo
        return st
    monkeypatch.setattr(bf, "build_staging", build_e_troca)


def test_m15_arquivo_trocado_entre_listagem_e_reconferencia_aborta_o_preparo(isolated, monkeypatch):
    _troca_arquivo_depois_da_staging(monkeypatch, isolated)
    with pytest.raises(pub.PublishRefused, match="mudou durante o preparo"):
        pub.prepare([("kokeshi", "2026-07"), ("kokeshi", "2026-08")], isolated, today=TODAY)


def test_m15_no_apply_o_arquivo_trocado_aborta_sem_abrir_conexao(isolated, monkeypatch, tmp_path):
    _troca_arquivo_depois_da_staging(monkeypatch, isolated)
    chamado = []
    args = Namespace(scope=["kokeshi:2026-07", "kokeshi:2026-08"], source_root=str(isolated),
                     offline=False, apply=True, audit_dir=str(tmp_path / "audit_m15"))
    env = {pub.CONSENT_ENV: "1", "BACKFILL_NEON_RW_URL": "postgresql://u:p@ep-x.neon.tech/db"}
    assert pub.run_apply(args, env=env, engine_factory=lambda *a, **k: chamado.append(1),
                         today=TODAY) == pub.EXIT_REFUSED
    assert chamado == []                            # zero conexao, logo zero escrita


# --- Manifesto ---------------------------------------------------------------

def test_audit_dir_e_obrigatorio(isolated):
    with pytest.raises(SystemExit):
        pub.build_parser().parse_args(["--scope", "kokeshi:2026-07", "--source-root", str(isolated)])
    args = Namespace(scope=["kokeshi:2026-07", "kokeshi:2026-08"], source_root=str(isolated),
                     offline=True, apply=False, audit_dir=None)
    assert pub.run_dry(args, today=TODAY) == pub.EXIT_USAGE


def test_audit_dir_dentro_de_worktree_git_e_recusado(isolated, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    for destino in (repo / "audit", repo / "a" / "b"):
        args = Namespace(scope=["kokeshi:2026-07", "kokeshi:2026-08"], source_root=str(isolated),
                         offline=True, apply=False, audit_dir=str(destino))
        assert pub.run_dry(args, today=TODAY) == pub.EXIT_REFUSED
        assert not destino.exists()
    # worktree vinculado: `.git` e ARQUIVO
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: x")
    assert pub.inside_git_worktree(wt / "audit")


def test_o_proprio_repositorio_e_recusado_como_destino(isolated):
    repo_api = Path(pub.__file__).resolve().parents[1]
    args = Namespace(scope=["kokeshi:2026-07", "kokeshi:2026-08"], source_root=str(isolated),
                     offline=True, apply=False, audit_dir=str(repo_api / "publish_audit"))
    assert pub.run_dry(args, today=TODAY) == pub.EXIT_REFUSED
    assert not (repo_api / "publish_audit").exists()


def test_manifesto_sem_gmv_pedido_sku_produto_ou_credencial(isolated, tmp_path):
    audit = tmp_path / "audit_priv"
    args = Namespace(scope=["kokeshi:2026-07", "kokeshi:2026-08"], source_root=str(isolated),
                     offline=True, apply=False, audit_dir=str(audit))
    assert pub.run_dry(args, env={"BACKFILL_NEON_RO_URL": "postgresql://u:segredo@h.neon.tech/db"},
                       today=TODAY) == pub.EXIT_OK
    [arq] = list(audit.glob("*.json"))
    texto = arq.read_text(encoding="utf-8")
    m = json.loads(texto)
    assert set(m) <= pub.MANIFEST_KEYS
    for proibido in ("gmv", "SKU1", "SKU2", "Produto", "A1", "segredo", "neon.tech", "postgresql"):
        assert proibido not in texto, proibido
    assert m["per_scope_rows"]["kokeshi:2026-07"] == {"rows_before": None, "rows_after": 2}
    assert m["started_at"] <= m["finished_at"]


def test_manifesto_com_campo_financeiro_e_recusado():
    with pytest.raises(pub.PublishRefused, match="lista branca"):
        pub.assert_manifest_safe({"run_id": "x", "gmv_total": "1"})
    with pytest.raises(pub.PublishRefused, match="proibido"):
        pub.assert_manifest_safe({"run_id": "x", "scopes": ["gmv"]})


# --- Formulas: deteccao estrutural -----------------------------------------

def _xlsx_com_celula(path: Path, celula_xml: str, ns_extra: str = "") -> Path:
    _xlsx(path, [_pedido("F1", "2026-08-10 10:00", "SKU1", 1, 10.0)])
    tmp = path.with_suffix(".tmp")
    with _zipfile.ZipFile(path) as zin, _zipfile.ZipFile(tmp, "w", _zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            dados = zin.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                xml = dados.decode("utf-8")
                if ns_extra:
                    xml = xml.replace("<worksheet ", f"<worksheet {ns_extra} ", 1)
                xml = xml.replace("</row>", celula_xml + "</row>", 1)
                dados = xml.encode("utf-8")
            zout.writestr(item, dados)
    tmp.replace(path)
    return path


NS_MAIN = 'xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


@pytest.mark.parametrize("celula,ns", [
    ('<c r="Z1"><f>1+1</f></c>', ""),                              # sem cache
    ('<c r="Z1"><f>1+1</f><v>2</v></c>', ""),                      # com valor em cache
    ('<c r="Z1"><x:f>1+1</x:f><x:v>2</x:v></c>', NS_MAIN),         # com namespace/prefixo
    ('<c r="Z1"><f t="shared" ref="Z1:Z2" si="0">1+1</f></c>', ""),   # compartilhada
])
def test_toda_formula_e_detectada(tmp_path, celula, ns):
    p = _xlsx_com_celula(tmp_path / "f.xlsx", celula, ns)
    assert pub.has_formula(p) is True


def test_planilha_sem_formula_nao_e_falso_positivo(tmp_path):
    p = _xlsx_com_celula(tmp_path / "ok.xlsx", '<c r="Z1" t="inlineStr"><is><t>f</t></is></c>')
    assert pub.has_formula(p) is False


def test_formula_com_cache_e_recusada_no_preparo(tmp_path, monkeypatch):
    root = _so_um_mes(tmp_path, monkeypatch, [_pedido("B0", "2026-08-09 10:00", "SKU1", 1, 10.0)])
    alvo = next((root / "kokeshi").iterdir())
    _xlsx_com_celula(alvo, '<c r="Z1"><x:f>1+1</x:f><x:v>2</x:v></c>', NS_MAIN)
    with pytest.raises(pub.PublishRefused, match="formula"):
        pub.prepare([("kokeshi", "2026-08")], root, today=TODAY_OUT)


def test_apply_sem_consentimento_recusa_mesmo_com_credencial_presente(isolated, tmp_path):
    """Sem esta variante, a falta da credencial de escrita mascararia a falta do
    consentimento: as duas recusam, e so uma delas estaria sendo testada."""
    chamado = []
    args = Namespace(scope=["kokeshi:2026-07", "kokeshi:2026-08"], source_root=str(isolated),
                     offline=False, apply=True, audit_dir=str(tmp_path / "audit_c"))
    env = {"BACKFILL_NEON_RW_URL": "postgresql://u:p@ep-x.neon.tech/db"}
    assert pub.run_apply(args, env=env, engine_factory=lambda *a, **k: chamado.append(1),
                         today=TODAY) == pub.EXIT_REFUSED
    assert chamado == []
