"""FRESHNESS-SHOPEE-DAILY-1 — `daily_shopee` contra Postgres REAL.

O que um dublê nao prova: o upsert so' das colunas donas, a idempotencia por
conteudo, o rollback da reconciliacao pre-commit, o advisory lock compartilhado
com os carregadores de arquivo e a preservacao do funil/Ads e da kokeshi.
Cluster descartavel; nunca toca Neon nem Data Mart.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from pipelines import sync_shopee_daily_api as D
from pipelines import sync_shopee_daily_api_shadow as SH
from pipelines.ingestion import daily_performance as DP
from pipelines.ingestion.fato_diaria_lock import FatoDiariaLockUnavailable
from pipelines.tests.postgres_descartavel import MOTIVO_SEM_POSTGRES, cluster_da_sessao, postgres_disponivel

pytestmark = pytest.mark.skipif(not postgres_disponivel(), reason=MOTIVO_SEM_POSTGRES)

AGORA = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)   # hoje BRT = 21/10; janela 22/08..20/10
D1, D2, D3 = date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)
FORA = date(2026, 8, 1)
LIGADO = {D.FLAG_HABILITADO: "true"}

DDL = [
    "CREATE SCHEMA marts", "CREATE SCHEMA audit", "CREATE SCHEMA silver",
    """CREATE TABLE audit.source_sync_run (
        sync_run_id SERIAL PRIMARY KEY, source_name VARCHAR(100) NOT NULL, marketplace_id INT, loja_id INT,
        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ,
        status VARCHAR(20) NOT NULL DEFAULT 'running' CHECK (status IN ('running','success','failed')),
        rows_extracted INT, rows_loaded INT, error_message TEXT, source_min_date DATE, source_max_date DATE)""",
    # Subconjunto das colunas reais de marts.fact_marketplace_daily_performance.
    """CREATE TABLE marts.fact_marketplace_daily_performance (
        id SERIAL PRIMARY KEY, date DATE NOT NULL, loja_id INT NOT NULL, marketplace_id INT NOT NULL,
        empresa_id INT, gmv NUMERIC(14,2), orders INT, units_sold INT, avg_ticket NUMERIC(14,2),
        unique_buyers INT, visitors INT, conversion_rate NUMERIC(8,4),
        canceled_orders INT, returned_orders INT, cancel_rate_pct NUMERIC(8,2), delivered_orders INT,
        ad_spend NUMERIC(14,2), total_settlement NUMERIC(14,2), total_fees NUMERIC(14,2),
        avg_fee_pct NUMERIC(8,2), avg_settlement_pct NUMERIC(8,2), seller_shipping_cost NUMERIC(14,2),
        shipping_pct_of_gmv NUMERIC(8,2), new_buyers INT, repeat_buyers INT, repeat_buyer_rate_pct NUMERIC(8,2),
        source_updated_at TIMESTAMPTZ, ingested_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (date, loja_id, marketplace_id))""",
    """CREATE TABLE silver.stg_shopee_orders (
        shop_account TEXT, order_sn TEXT, created_date_brt DATE, order_status TEXT,
        estimated_shipping_fee NUMERIC, ingested_at TIMESTAMPTZ)""",
    """CREATE TABLE silver.stg_shopee_order_items (
        shop_account TEXT, order_sn TEXT, order_item_id BIGINT, quantity INT, item_total NUMERIC)""",
]


@pytest.fixture
def eng(monkeypatch):
    with cluster_da_sessao() as base:
        adm = create_engine(base, future=True, isolation_level="AUTOCOMMIT")
        with adm.connect() as c:
            c.execute(text("DROP DATABASE IF EXISTS shp_daily WITH (FORCE)"))
            c.execute(text("CREATE DATABASE shp_daily"))
        adm.dispose()
        e = create_engine(base.rsplit("/", 1)[0] + "/shp_daily", future=True)
        with e.begin() as c:
            for s in DDL:
                c.execute(text(s))

        def coletar(inicio, fim, hoje, agora):
            with e.connect() as c:
                c.execute(text("SET TRANSACTION READ ONLY"))
                max_ing = SH.validate_frescor(c, SH.CONTAS_API, agora)
                return SH.read_source(c, SH.CONTAS_API, inicio, fim, hoje, agora, max_ing)

        monkeypatch.setattr(D, "coletar_fonte", coletar)
        monkeypatch.setattr(D, "local_engine", lambda: e)
        monkeypatch.setattr(D, "_audit_conn", e.raw_connection)
        monkeypatch.setattr(D, "_lock_connect",
                            lambda: e.connect().execution_options(isolation_level="AUTOCOMMIT"))
        yield e
        e.dispose()


def _pedido(e, conta, sn, d, status, frete, itens, ingested=AGORA):
    with e.begin() as c:
        c.execute(text("INSERT INTO silver.stg_shopee_orders VALUES (:a, :sn, :d, :st, :f, :ing)"),
                  {"a": conta, "sn": sn, "d": d, "st": status, "f": frete, "ing": ingested})
        for i, (q, tot) in enumerate(itens):
            c.execute(text("INSERT INTO silver.stg_shopee_order_items VALUES (:a, :sn, :i, :q, :t)"),
                      {"a": conta, "sn": sn, "i": i, "q": q, "t": tot})


def _semear(e):
    _pedido(e, "apice", "A1", D1, "shipped", 10, [(1, 50), (2, 30)])
    _pedido(e, "apice", "A2", D1, "completed", 5, [(1, 40)])
    _pedido(e, "apice", "A3", D1, "cancelled", 7, [(4, 99)])
    _pedido(e, "barbours", "B1", D1, "completed", 8, [(3, 60)])
    _pedido(e, "lescent", "L1", D2, "completed", 4, [(1, 25)])
    _pedido(e, "rituaria", "R1", D3, "shipped", 6, [(2, 70)])
    _pedido(e, "kokeshi", "K1", D1, "completed", 9, [(1, 15)])      # nunca entra
    _pedido(e, "apice", "A9", FORA, "completed", 1, [(1, 11)])      # fora da janela padrao
    # volume de fundo da apice em D3: a guarda de colapso (80%) mede a JANELA, nao o dia.
    for i in range(10):
        _pedido(e, "apice", f"AF{i}", D3, "completed", 1, [(1, 10)])


def _fato(e, loja=None) -> dict:
    sql = "SELECT * FROM marts.fact_marketplace_daily_performance"
    with e.connect() as c:
        linhas = c.execute(text(sql + (" WHERE loja_id = :l" if loja else "")), {"l": loja}).mappings().all()
    return {(r["date"], r["loja_id"]): dict(r) for r in linhas}


def _linha_arquivo(e, d, loja, **cols):
    base = {"date": d, "loja_id": loja, "marketplace_id": 3, "empresa_id": 1}
    base.update(cols)
    with e.begin() as c:
        c.execute(text(f"INSERT INTO marts.fact_marketplace_daily_performance ({', '.join(base)}) "
                       f"VALUES ({', '.join(':' + k for k in base)})"), base)


def _auditorias(e):
    with e.connect() as c:
        return c.execute(text("SELECT source_name, status, rows_loaded, source_min_date, source_max_date "
                              "FROM audit.source_sync_run ORDER BY sync_run_id")).all()


def test_publica_e_segunda_execucao_identica_nao_grava(eng):
    _semear(eng)
    r1 = D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    assert r1["linhas_gravadas"] == 5 and r1["aplicado"] is True
    a1 = _fato(eng)[(D1, 1)]
    assert (a1["orders"], a1["canceled_orders"], a1["units_sold"], a1["delivered_orders"]) == (2, 1, 4, 1)
    assert a1["gmv"] == Decimal("120.00") and a1["seller_shipping_cost"] == Decimal("15.00")
    assert a1["avg_ticket"] == Decimal("60.00")
    antes = _fato(eng)
    r2 = D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    assert r2["linhas_gravadas"] == 0 and r2["noop"] is True
    assert {k: {c: v for c, v in r.items() if c != "ingested_at"} for k, r in _fato(eng).items()} == \
           {k: {c: v for c, v in r.items() if c != "ingested_at"} for k, r in antes.items()}
    aud = _auditorias(eng)
    assert [a[:3] for a in aud] == [("shopee_daily_api", "success", 5), ("shopee_daily_api", "success", 0)]
    assert aud[0][3:] == (date(2026, 8, 22), date(2026, 10, 20))


def test_cancelamento_tardio_e_capturado_na_janela(eng):
    _semear(eng)
    D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    with eng.begin() as c:
        c.execute(text("UPDATE silver.stg_shopee_orders SET order_status = 'cancelled' WHERE order_sn = 'A2'"))
    r = D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    assert r["linhas_gravadas"] == 1
    a1 = _fato(eng)[(D1, 1)]
    assert (a1["orders"], a1["canceled_orders"], a1["gmv"]) == (1, 2, Decimal("80.00"))


def test_ausencia_nao_vira_zero(eng):
    _semear(eng)
    _linha_arquivo(eng, D2, 1, orders=1, gmv=70, visitors=50)   # apice D2: sem pedido na fonte
    r = D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    fato = _fato(eng)
    assert (fato[(D2, 1)]["orders"], fato[(D2, 1)]["gmv"], fato[(D2, 1)]["visitors"]) == (1, Decimal("70.00"), 50)
    assert r["avisos"] == []
    nova = fato[(D3, 5)]
    for c in ("visitors", "unique_buyers", "ad_spend", *D.COLUNAS_ANULADAS):
        assert nova[c] is None, c
    assert (D2, 2) not in fato                       # barbours sem pedido em D2: nenhuma linha criada


def test_funil_e_ads_do_arquivo_preservados_familia_de_pedidos_trocada(eng):
    _semear(eng)
    _linha_arquivo(eng, D1, 1, gmv=800, orders=3, visitors=100, conversion_rate=0.05, unique_buyers=40,
                   ad_spend=20, total_fees=5, total_settlement=700, returned_orders=1)
    D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    a1 = _fato(eng)[(D1, 1)]
    assert (a1["gmv"], a1["orders"]) == (Decimal("120.00"), 2)
    assert (a1["visitors"], a1["unique_buyers"], a1["ad_spend"]) == (100, 40, Decimal("20.00"))
    assert (a1["total_fees"], a1["total_settlement"], a1["returned_orders"]) == (None, None, None)


def test_kokeshi_sem_api_nunca_e_tocada(eng):
    _semear(eng)
    _linha_arquivo(eng, D1, 3, gmv=1000, orders=50, visitors=900)
    D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    kok = _fato(eng, loja=3)
    assert list(kok) == [(D1, 3)]
    assert (kok[(D1, 3)]["gmv"], kok[(D1, 3)]["orders"]) == (Decimal("1000.00"), 50)


def test_loja_sem_dado_na_fonte_aborta_sem_escrever_nem_auditar(eng):
    _semear(eng)
    with eng.begin() as c:
        c.execute(text("DELETE FROM silver.stg_shopee_orders WHERE shop_account = 'rituaria'"))
    with pytest.raises(SH.ShopeeDailyShadowError, match="rituaria"):
        D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    assert _fato(eng) == {} and _auditorias(eng) == []


def test_fonte_velha_aborta(eng):
    _semear(eng)
    with eng.begin() as c:
        c.execute(text("UPDATE silver.stg_shopee_orders SET ingested_at = :v WHERE shop_account = 'lescent'"),
                  {"v": datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)})
    with pytest.raises(SH.ShopeeDailyShadowError, match="lescent"):
        D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    assert _fato(eng) == {}


def test_janela_movel_e_backfill_explicito(eng):
    _semear(eng)
    D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    assert (FORA, 1) not in _fato(eng)
    D.run(apply=True, desde=FORA, agora=AGORA, ambiente=LIGADO)
    assert _fato(eng)[(FORA, 1)]["orders"] == 1


def test_reconciliacao_pre_commit_divergente_faz_rollback_e_audita_falha(eng):
    _semear(eng)
    with eng.begin() as c:
        c.execute(text("""CREATE FUNCTION marts.adultera() RETURNS trigger AS $$
                          BEGIN NEW.gmv := NEW.gmv + 1; RETURN NEW; END $$ LANGUAGE plpgsql"""))
        c.execute(text("""CREATE TRIGGER t BEFORE INSERT OR UPDATE ON marts.fact_marketplace_daily_performance
                          FOR EACH ROW EXECUTE FUNCTION marts.adultera()"""))
    with pytest.raises(D.ShopeeDailyApiError, match="pre-commit"):
        D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    assert _fato(eng) == {}
    assert [a[:2] for a in _auditorias(eng)] == [("shopee_daily_api", "failed")]


def test_lock_da_fato_shopee_ocupado_nao_le_nem_escreve(eng):
    _semear(eng)
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as outro:
        assert outro.execute(text("SELECT pg_try_advisory_lock(918130003)")).scalar()
        with pytest.raises(FatoDiariaLockUnavailable):
            D.run(apply=True, agora=AGORA, ambiente=LIGADO)
        outro.execute(text("SELECT pg_advisory_unlock(918130003)"))
    assert _fato(eng) == {} and _auditorias(eng) == []


def test_shop_stats_sem_gmv_preserva_o_gmv_da_api(eng):
    _semear(eng)
    D.run(apply=True, agora=AGORA, ambiente=LIGADO)
    with eng.begin() as c:
        c.execute(DP.PATCH_SHOP_STATS_SEM_GMV_SQL, {
            "date": D1, "loja_id": 1, "marketplace_id": 3, "empresa_id": 1, "visitors": 321,
            "conversion_rate": 0.1, "new_buyers": 1, "repeat_buyers": 1, "repeat_buyer_rate_pct": 50,
            "unique_buyers": 2})
    a1 = _fato(eng)[(D1, 1)]
    assert (a1["gmv"], a1["visitors"]) == (Decimal("120.00"), 321)
    assert D.run(apply=True, agora=AGORA, ambiente=LIGADO)["linhas_gravadas"] == 0
