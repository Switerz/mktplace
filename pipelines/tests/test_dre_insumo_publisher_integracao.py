"""DRE-DATA-BRIDGE-1 — publisher contra PostgreSQL DESCARTAVEL.

Por que servidor real: lock, transacao, rollback e CHECK sao comportamento do
servidor. Um dublê concordaria com o teste.

Dois bancos no mesmo cluster efemero:
  * `alvo`  — faz o papel do Neon: `marts`, `audit` e a migration 025 aplicada;
  * `fonte` — faz o papel do Data Mart: as tres tabelas que o publisher le,
    com as colunas que ele usa.

Nunca toca Neon nem Data Mart: as URLs saem de `postgres_descartavel`. Sem
binarios do PostgreSQL, o arquivo inteiro e' SKIP.

Roda tambem com SQLAlchemy 1.4 (a versao da imagem do Airflow):
    uv run --with "sqlalchemy==1.4.54" ... pytest este_arquivo
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from pipelines.dre import insumo_publisher as P
from pipelines.tests.postgres_descartavel import (
    MOTIVO_SEM_POSTGRES,
    cluster_da_sessao,
    postgres_disponivel,
)
from pipelines.tests.test_dre_insumo_publisher import carregar_migration_025

pytestmark = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

JUL = date(2026, 7, 1)
AGORA = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)

DDL_ALVO = [
    "CREATE SCHEMA marts", "CREATE SCHEMA audit",
    # recorte FIEL da revisao 003 (SERIAL, CHECK de status)
    """CREATE TABLE audit.source_sync_run (
        sync_run_id SERIAL PRIMARY KEY, source_name VARCHAR(100) NOT NULL, marketplace_id INT, loja_id INT,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ,
        status VARCHAR(20) NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed')),
        rows_extracted INT, rows_loaded INT, error_message TEXT, source_min_date DATE, source_max_date DATE)""",
    "CREATE TABLE marts.dim_loja (loja_id INT PRIMARY KEY, brand_key TEXT NOT NULL, ativo BOOLEAN NOT NULL)",
    """CREATE TABLE marts.fact_marketplace_daily_performance (
        date DATE NOT NULL, loja_id INT NOT NULL, marketplace_id INT NOT NULL, gmv NUMERIC,
        UNIQUE (date, loja_id, marketplace_id))""",
    "INSERT INTO marts.dim_loja VALUES (1, 'kokeshi', true), (2, 'barbours', true)",
]

DDL_FONTE = [
    "CREATE SCHEMA gold", "CREATE SCHEMA silver",
    """CREATE TABLE gold.nf_vendas_plataforma_sku_mensal (
        data_mes DATE, marca TEXT, canal TEXT, plataforma TEXT, codigo_sku TEXT, receita_produto NUMERIC,
        custo NUMERIC, custo_provisorio NUMERIC, receita_com_custo NUMERIC, ultima_emissao DATE)""",
    """CREATE TABLE gold.nf_vendas_plataforma_mensal (
        data_mes DATE, marca TEXT, canal TEXT, plataforma TEXT, receita_produto NUMERIC, custo NUMERIC)""",
    """CREATE TABLE silver.stg_tiktok_payments_by_order (
        brand VARCHAR, transaction_type VARCHAR, order_create_time TIMESTAMP, revenue_amount NUMERIC,
        fee_and_tax_amount NUMERIC, shipping_cost_amount NUMERIC, settlement_amount NUMERIC,
        affiliate_commission_amount NUMERIC, affiliate_partner_commission_amount NUMERIC,
        affiliate_ads_commission_amount NUMERIC, updated_at TIMESTAMP)""",
]


def _executar(url, stmts):
    eng = create_engine(url, future=True)
    with eng.begin() as conn:
        for s in stmts:
            conn.execute(text(s))
    eng.dispose()


def _url_db(base: str, nome: str) -> str:
    return base.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture
def bancos():
    """Cria `alvo` e `fonte` do zero para cada teste."""
    with cluster_da_sessao() as base:
        adm = create_engine(base, future=True, isolation_level="AUTOCOMMIT")
        with adm.connect() as conn:
            for nome in ("dre_alvo", "dre_fonte"):
                conn.execute(text(f"DROP DATABASE IF EXISTS {nome} WITH (FORCE)"))
                conn.execute(text(f"CREATE DATABASE {nome}"))
        adm.dispose()
        alvo_url, fonte_url = _url_db(base, "dre_alvo"), _url_db(base, "dre_fonte")
        _executar(alvo_url, DDL_ALVO + carregar_migration_025().UPGRADE_SQL)
        _executar(fonte_url, DDL_FONTE)
        alvo = create_engine(alvo_url, future=True)
        fonte = create_engine(fonte_url, future=True)
        semear(alvo, fonte)
        yield alvo, fonte, alvo_url, fonte_url
        alvo.dispose()
        fonte.dispose()


def semear(alvo, fonte, *, cmv_ml=Decimal(300000), repasse_barbours=True):
    """Julho/2026: kokeshi em ML, TikTok e Shopee; barbours no TikTok. GMV diario constante."""
    with alvo.begin() as c:
        c.execute(text("DELETE FROM marts.fact_marketplace_daily_performance"))
        for dia in range(1, 32):
            d = date(2026, 7, dia)
            for loja, mkt, gmv in ((1, 2, 1_000_000 / 31), (1, 1, 1_000_000 / 31), (1, 3, 1_000_000 / 31),
                                   (2, 1, 500_000 / 31)):
                c.execute(text("INSERT INTO marts.fact_marketplace_daily_performance VALUES (:d, :l, :m, :g)"),
                          {"d": d, "l": loja, "m": mkt, "g": round(gmv, 2)})
    with fonte.begin() as c:
        for t in ("gold.nf_vendas_plataforma_sku_mensal", "gold.nf_vendas_plataforma_mensal",
                  "silver.stg_tiktok_payments_by_order"):
            c.execute(text(f"DELETE FROM {t}"))
        notas = [("kokeshi", "mercadolivre", 1_000_000, cmv_ml), ("kokeshi", "tiktokshop", 990_000, 280_000),
                 ("barbours", "tiktokshop", 500_000, 150_000), ("kokeshi", "shopee", 400_000, 100_000)]
        for marca, plat, rec, custo in notas:
            for sku, frac in (("A", Decimal("0.6")), ("B", Decimal("0.4"))):
                c.execute(text("""INSERT INTO gold.nf_vendas_plataforma_sku_mensal VALUES
                    ('2026-07-01', :m, 'marketplace', :p, :s, :r, :c, 0, :r, '2026-07-31')"""),
                          {"m": marca, "p": plat, "s": sku, "r": Decimal(rec) * frac, "c": Decimal(custo) * frac})
            c.execute(text("INSERT INTO gold.nf_vendas_plataforma_mensal VALUES ('2026-07-01', :m, 'marketplace', :p, :r, :c)"),
                      {"m": marca, "p": plat, "r": rec, "c": custo})
        marcas = ["kokeshi"] + (["barbours"] if repasse_barbours else [])
        for marca in marcas:
            for i in range(10):
                # receita 100, taxa -30 (afiliado -10 dentro), frete -2, repasse = 68
                c.execute(text("""INSERT INTO silver.stg_tiktok_payments_by_order VALUES
                    (:b, 'ORDER', :t, 100, -30, -2, 68, -10, 0, 0, '2026-10-01 03:00')"""),
                          {"b": marca, "t": datetime(2026, 7, 5 + i, 10, 0)})
            # transacao com taxa POSITIVA e sem afiliado (estorno): nao e' violacao
            c.execute(text("""INSERT INTO silver.stg_tiktok_payments_by_order VALUES
                (:b, 'ORDER', '2026-07-20 10:00', 0, 5, 0, 5, 0, 0, 0, '2026-10-01 03:00')"""), {"b": marca})


def linhas(alvo) -> dict:
    with alvo.connect() as c:
        rows = c.execute(text("SELECT competencia, canal, marca, componente, valor, status_reconciliacao, lote_id "
                              "FROM marts.fact_dre_insumo_mensal")).mappings().all()
    return {(r["canal"], r["marca"], r["componente"]): dict(r) for r in rows}


def auditoria(alvo) -> list[dict]:
    with alvo.connect() as c:
        return [dict(r) for r in c.execute(text(
            "SELECT sync_run_id, status, error_message FROM audit.source_sync_run ORDER BY sync_run_id")).mappings()]


def publicar(alvo, fonte, **kw):
    return P.publicar(alvo, fonte, [JUL], ["tiktok", "ml", "shopee"], agora=AGORA, **kw)


# ---------------------------------------------------------------------------

def test_dry_run_nao_escreve(bancos):
    alvo, fonte, *_ = bancos
    resumo, _lote = P.diagnosticar(alvo, fonte, [JUL], ["tiktok", "ml", "shopee"])
    assert resumo["recusas"] == [] and resumo["linhas"] > 0
    assert linhas(alvo) == {} and auditoria(alvo) == []


def test_apply_publica_agregados_com_sinal_e_status(bancos):
    alvo, fonte, *_ = bancos
    res = publicar(alvo, fonte)
    ln = linhas(alvo)
    assert ln[("ml", "kokeshi", "cmv")]["valor"] == Decimal("300000.00")
    assert ln[("ml", "kokeshi", "cmv")]["status_reconciliacao"] == "reconciliado"
    assert ln[("shopee", "kokeshi", "cmv")]["status_reconciliacao"] == "nao_reconciliado"
    assert ln[("tiktok", "kokeshi", "tiktok_taxa_repasse")]["valor"] == Decimal("-295.00")  # 10x(-30) + 5
    assert ln[("tiktok", "kokeshi", "tiktok_afiliado_repasse")]["valor"] == Decimal("-100.00")
    assert ln[("tiktok", "kokeshi", "tiktok_residuo_identidade")]["valor"] == Decimal("0.00")
    assert ln[("tiktok", "kokeshi", "tiktok_tx_afiliado_maior_taxa")]["valor"] == Decimal("0.00")
    assert {r["lote_id"] for r in ln.values()} == {res["lote_id"]}
    assert [a["status"] for a in auditoria(alvo)] == ["success"]


def test_nenhum_dado_bruto_e_publicado(bancos):
    alvo, fonte, *_ = bancos
    publicar(alvo, fonte)
    with alvo.connect() as c:
        colunas = {r[0] for r in c.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='marts' AND table_name='fact_dre_insumo_mensal'"))}
    assert not colunas & {"order_id", "codigo_sku", "chave_nfe", "statement_id", "buyer", "sku"}
    assert {k[2] for k in linhas(alvo)} <= set(carregar_migration_025().COMPONENTES)


def test_idempotente(bancos):
    alvo, fonte, *_ = bancos
    publicar(alvo, fonte)
    primeira = {k: (v["valor"], v["status_reconciliacao"]) for k, v in linhas(alvo).items()}
    publicar(alvo, fonte)
    segunda = {k: (v["valor"], v["status_reconciliacao"]) for k, v in linhas(alvo).items()}
    assert primeira == segunda
    assert [a["status"] for a in auditoria(alvo)] == ["success", "success"]
    assert len({v["lote_id"] for v in linhas(alvo).values()}) == 1  # so' o lote novo sobrevive


def test_snapshot_parcial_vira_nulo_e_nao_preserva_valor_antigo(bancos):
    alvo, fonte, *_ = bancos
    publicar(alvo, fonte)
    assert linhas(alvo)[("tiktok", "barbours", "tiktok_taxa_repasse")]["valor"] is not None
    semear(alvo, fonte, repasse_barbours=False)  # a fonte perdeu o repasse da barbours
    publicar(alvo, fonte)
    r = linhas(alvo)[("tiktok", "barbours", "tiktok_taxa_repasse")]
    assert r["valor"] is None and r["status_reconciliacao"] == "ausente_na_origem"


def test_reconciliacao_recusada_nao_escreve_nada(bancos):
    alvo, fonte, *_ = bancos
    publicar(alvo, fonte)
    antes = linhas(alvo)
    with fonte.begin() as c:  # quebra a identidade do repasse
        c.execute(text("UPDATE silver.stg_tiktok_payments_by_order SET settlement_amount = settlement_amount + 50 "
                       "WHERE brand = 'kokeshi'"))
    with pytest.raises(P.PublicacaoRecusada):
        publicar(alvo, fonte)
    assert linhas(alvo) == antes
    aud = auditoria(alvo)
    assert [a["status"] for a in aud] == ["success", "failed"]
    assert "identidade nao fecha" in aud[-1]["error_message"]


def test_rollback_integral_quando_falha_depois_da_escrita(bancos):
    alvo, fonte, *_ = bancos
    publicar(alvo, fonte)
    antes = linhas(alvo)

    def explode(conn):
        n = conn.execute(text("SELECT count(*) FROM marts.fact_dre_insumo_mensal")).scalar_one()
        assert n > 0  # a escrita aconteceu dentro da transacao...
        raise RuntimeError("falha simulada antes do commit")

    semear(alvo, fonte, cmv_ml=Decimal(310000))
    with pytest.raises(RuntimeError):
        publicar(alvo, fonte, antes_do_commit=explode)
    assert linhas(alvo) == antes  # ...e foi inteira desfeita
    assert [a["status"] for a in auditoria(alvo)] == ["success", "failed"]


def test_origem_indisponivel_nao_escreve(bancos):
    alvo, _, *_ = bancos
    fora = create_engine("postgresql+psycopg2://ninguem@127.0.0.1:1/x", future=True,
                         connect_args={"connect_timeout": 2})
    with pytest.raises(P.FonteIndisponivel) as exc:
        publicar(alvo, fora)
    assert "127.0.0.1" not in str(exc.value)
    assert linhas(alvo) == {}
    assert [a["status"] for a in auditoria(alvo)] == ["failed"]


def test_origem_e_lida_em_transacao_somente_leitura(bancos):
    _, fonte, *_ = bancos
    with fonte.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        with pytest.raises(Exception) as exc:
            conn.execute(text("DELETE FROM gold.nf_vendas_plataforma_mensal"))
        assert "read-only" in str(exc.value)
        conn.rollback()
    # e o publisher usa exatamente esse preambulo
    from pathlib import Path

    assert 'SET TRANSACTION READ ONLY' in Path(P.__file__).read_text(encoding="utf-8")


def test_lock_ocupado_aborta_sem_escrever(bancos):
    alvo, fonte, alvo_url, _ = bancos
    outro = create_engine(alvo_url, future=True)
    with outro.connect() as c:
        c.execute(text(f"SELECT pg_advisory_lock({P.LOCK_KEY})"))
        c.commit()
        with pytest.raises(P.LockOcupado):
            publicar(alvo, fonte)
        c.execute(text(f"SELECT pg_advisory_unlock({P.LOCK_KEY})"))
        c.commit()
    outro.dispose()
    assert linhas(alvo) == {} and auditoria(alvo) == []


def test_migration_ausente_aborta_antes_de_escrever(bancos):
    alvo, fonte, *_ = bancos
    with alvo.begin() as c:
        for s in carregar_migration_025().DOWNGRADE_SQL:
            c.execute(text(s))
    with pytest.raises(P.MigrationAusente):
        publicar(alvo, fonte)
    assert [a["status"] for a in auditoria(alvo)] == ["failed"]


def test_migration_sobe_desce_e_sobe(bancos):
    alvo, *_ = bancos
    m = carregar_migration_025()
    with alvo.begin() as c:
        for s in m.DOWNGRADE_SQL:
            c.execute(text(s))
        assert c.execute(text("SELECT to_regclass('marts.fact_dre_insumo_mensal')")).scalar() is None
        for s in m.UPGRADE_SQL:
            c.execute(text(s))
        assert c.execute(text("SELECT to_regclass('marts.fact_dre_insumo_mensal')")).scalar() is not None


@pytest.mark.parametrize("valor,status,erro", [
    ("NULL", "'reconciliado'", "ck_fdim_ausencia_explicita"),
    ("0", "'ausente_na_origem'", "ck_fdim_ausencia_explicita"),
    ("'NaN'", "'reconciliado'", "ck_fdim_valor_nao_nan"),
])
def test_checks_da_migration_barram_ausencia_disfarcada(bancos, valor, status, erro):
    alvo, *_ = bancos
    with alvo.begin() as c:
        lote = c.execute(text("INSERT INTO audit.source_sync_run (source_name, status) VALUES ('t', 'success') "
                              "RETURNING sync_run_id")).scalar_one()
    with pytest.raises(Exception) as exc:
        with alvo.begin() as c:
            c.execute(text(f"""INSERT INTO marts.fact_dre_insumo_mensal
                (competencia, canal, marca, componente, valor, status_reconciliacao, publicado_em, lote_id, procedencia)
                VALUES ('2026-07-01', 'ml', 'kokeshi', 'cmv', {valor}, {status}, now(), {lote}, 'teste')"""))
    assert erro in str(exc.value)


def test_cli_dry_run_sai_zero_e_nao_escreve(bancos, capsys):
    alvo, fonte, *_ = bancos
    rc = P.main(["--de", "2026-07", "--ate", "2026-07"], engines=lambda: (alvo, fonte), hoje=date(2026, 10, 13))
    assert rc == P.EXIT_OK
    assert '"aplicado": false' in capsys.readouterr().out
    assert linhas(alvo) == {}


def test_cli_codigos_de_saida(bancos):
    alvo, fonte, *_ = bancos
    with fonte.begin() as c:
        c.execute(text("UPDATE gold.nf_vendas_plataforma_mensal SET custo = custo + 10"))
    rc = P.main(["--de", "2026-07", "--ate", "2026-07", "--apply"], engines=lambda: (alvo, fonte),
                hoje=date(2026, 10, 13))
    assert rc == P.EXIT_RECONCILIACAO
    assert linhas(alvo) == {}


# ---------------------------------------------------------------------------
# Universo esperado vazio (revisao estrita #68, item 2)
# ---------------------------------------------------------------------------

def fotografia(alvo) -> str:
    """Impressao digital BYTE A BYTE da fato: todas as colunas, inclusive lote e instante."""
    with alvo.connect() as c:
        return c.execute(text(
            "SELECT coalesce(md5(string_agg(t::text, '|' ORDER BY competencia, canal, marca, componente)), 'vazia') "
            "FROM marts.fact_dre_insumo_mensal t")).scalar_one()


def _apagar_receita(alvo, marketplace_id=None):
    with alvo.begin() as c:
        filtro = "" if marketplace_id is None else f" AND marketplace_id = {int(marketplace_id)}"
        c.execute(text("DELETE FROM marts.fact_marketplace_daily_performance "
                       f"WHERE date BETWEEN '2026-07-01' AND '2026-07-31'{filtro}"))


def test_universo_esperado_vazio_preserva_a_fotografia_byte_a_byte(bancos):
    alvo, fonte, *_ = bancos
    publicar(alvo, fonte)
    antes = fotografia(alvo)
    assert antes != "vazia"
    _apagar_receita(alvo)  # a Torre "perdeu" julho inteiro (carga quebrada, filtro errado...)
    rc = P.main(["--de", "2026-07", "--ate", "2026-07", "--apply"], engines=lambda: (alvo, fonte),
                hoje=date(2026, 10, 13))
    assert rc == P.EXIT_RECONCILIACAO
    assert fotografia(alvo) == antes
    aud = auditoria(alvo)
    assert aud[-1]["status"] == "failed" and "universo esperado vazio" in aud[-1]["error_message"]


def test_canal_que_some_inteiro_preserva_a_fotografia(bancos):
    alvo, fonte, *_ = bancos
    publicar(alvo, fonte)
    antes = fotografia(alvo)
    _apagar_receita(alvo, marketplace_id=2)  # so' o ML de julho sumiu
    with pytest.raises(P.PublicacaoRecusada) as exc:
        publicar(alvo, fonte)
    assert any("2026-07 ml" in m for m in exc.value.motivos)
    assert fotografia(alvo) == antes
    assert auditoria(alvo)[-1]["status"] == "failed"


def test_contraprova_sem_a_guarda_o_lote_seria_apagado(bancos, monkeypatch):
    """O que a implementacao antiga fazia: com a guarda desligada, o mesmo
    cenario APAGA a fotografia e fecha `success`. E' a prova de que e' a guarda
    — e nao outra coisa — que preserva o lote."""
    alvo, fonte, *_ = bancos
    publicar(alvo, fonte)
    assert fotografia(alvo) != "vazia"
    _apagar_receita(alvo)
    monkeypatch.setattr(P, "recusas_de_escopo", lambda *a, **k: [])
    publicar(alvo, fonte)
    assert fotografia(alvo) == "vazia"
    assert auditoria(alvo)[-1]["status"] == "success"


def test_mes_canal_sem_vendas_declarado_publica_e_remove_so_ele(bancos):
    alvo, fonte, *_ = bancos
    publicar(alvo, fonte)
    _apagar_receita(alvo, marketplace_id=3)  # a Shopee de julho legitimamente sem vendas
    res = publicar(alvo, fonte, sem_vendas=frozenset({(JUL, "shopee")}))
    assert res["sem_vendas"] == ["2026-07:shopee"]
    canais = {k[0] for k in linhas(alvo)}
    assert canais == {"tiktok", "ml"}
    assert auditoria(alvo)[-1]["status"] == "success"
