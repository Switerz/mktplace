"""
Testes da chave de identidade ESTRITA de produto Shopee (Bug 9 em
docs/sections/produtos_audit.md) — substitui a tentativa anterior de
consolidar por similaridade textual dinamica (rejeitada: decidir identidade
por heuristica fuzzy em produção era um risco inaceitavel de juntar produtos
distintos silenciosamente).

Chave definitiva, sem NENHUMA consolidacao entre linhas — e a propria UNIQUE
constraint do mart, nao uma aproximacao dela:
    (ref_month, brand, sku_ref_key, product_name)

`variation_name` NAO faz parte da chave: e um atributo descritivo da linha,
que pode ja ter sido consolidado/sobrescrito rio acima pelo proprio ETL antes
de chegar ao mart (colisoes somadas na carga, Bug 5; grupos so com pedidos
cancelados descartados no merge, Bug 8) — o valor exibido e o que sobrou
dessas decisoes upstream, nunca recalculado por este service.

Cada linha de saida de get_produtos_shopee/_summary e exatamente 1 linha do
mart `marts.fact_shopee_product_monthly` — nunca uma soma de varias. Isso
vale tanto para sku_ref_key vazio quanto para sku_ref_key reaproveitado em
produtos aparentemente distintos (ex.: lescent/LC03034, 2 perfumes com
fragancias diferentes) QUANTO para grupos que uma versao anterior teria
consolidado por serem "apenas diferenca de encoding" (ex.: apice/KIT074,
apice/kit073) — a consequencia aceita e que o mesmo listing com o titulo
editado pelo vendedor durante o mes aparece em 2 linhas em vez de 1.

Estes testes sao best-effort contra o Neon real (somente leitura) e pulam se
o banco nao estiver acessivel.
"""
import pytest
from sqlalchemy import text

from app.services import performance_service as perf_svc

try:
    from app.database import SessionLocal
except Exception:  # pragma: no cover
    SessionLocal = None


@pytest.fixture()
def db():
    if SessionLocal is None:
        pytest.skip("banco nao configurado nesta maquina (DATABASE_URL ausente)")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _find_a_duplicate_group(db) -> tuple[str, int, int, str] | None:
    """Retorna (brand, year, month, sku_ref_key) de um grupo com >1 linha no
    mart (product_name diferente sob o mesmo sku_ref_key), ou None se nao
    houver nenhum no momento."""
    row = db.execute(text("""
        SELECT brand, EXTRACT(YEAR FROM ref_month)::int AS y, EXTRACT(MONTH FROM ref_month)::int AS m, sku_ref_key
        FROM marts.fact_shopee_product_monthly
        WHERE NULLIF(TRIM(sku_ref_key), '') IS NOT NULL
        GROUP BY ref_month, brand, sku_ref_key
        HAVING COUNT(*) > 1
        LIMIT 1
    """)).fetchone()
    return (row.brand, row.y, row.m, row.sku_ref_key) if row else None


def test_sku_duplicado_nunca_e_consolidado_cada_linha_bruta_aparece_isolada(db):
    """Regressao central do Bug 9: NENHUM grupo de sku_ref_key duplicado deve
    ser consolidado, independente de quao parecidos sejam os product_name
    (mesmo os casos que uma versao anterior classificava como "seguros",
    ex.: diferenca so de encoding)."""
    dup = _find_a_duplicate_group(db)
    if dup is None:
        pytest.skip("nenhum sku_ref_key duplicado no mart neste momento")
    brand, year, month, sku_ref_key = dup

    raw_rows = db.execute(text("""
        SELECT product_name, variation_name, gmv, units_sold, completed_orders, canceled_orders, unique_buyers
        FROM marts.fact_shopee_product_monthly
        WHERE ref_month = make_date(:y, :m, 1) AND brand = :brand AND sku_ref_key = :skey
    """), {"y": year, "m": month, "brand": brand, "skey": sku_ref_key}).fetchall()
    assert len(raw_rows) > 1

    result = perf_svc.get_produtos_shopee(
        db, brand=brand, year=year, month=month,
        limit=2000, offset=0, sort_by=None, sort_dir=None, pareto_bucket=None,
    )
    total_group_gmv = sum(float(r.gmv) for r in raw_rows)
    # NUNCA deve existir um item cujo gmv seja a soma do grupo inteiro —
    # isso seria consolidacao indevida.
    if total_group_gmv > 0:
        assert not any(abs(it["gmv"] - total_group_gmv) < 0.01 for it in result["items"]), (
            f"grupo sku_ref_key={sku_ref_key!r} foi consolidado indevidamente "
            f"— soma do grupo ({total_group_gmv}) apareceu como item unico"
        )

    # cada linha bruta do grupo (com gmv>0) deve aparecer isolada, com seu
    # proprio gmv/units/pedidos/unique_buyers preservados exatamente
    for raw in raw_rows:
        if float(raw.gmv) <= 0:
            continue
        match = next((it for it in result["items"] if abs(it["gmv"] - float(raw.gmv)) < 0.01
                      and it["product_name"] == raw.product_name), None)
        assert match is not None, f"linha bruta {raw.product_name!r} (gmv={raw.gmv}) nao apareceu isolada na resposta"
        assert match["units_sold"] == int(raw.units_sold)
        assert match["orders"] == int(raw.completed_orders)
        assert match["canceled_orders"] == int(raw.canceled_orders)
        # unique_buyers NUNCA e anulado pela API — e o valor do proprio ETL
        expected_buyers = int(raw.unique_buyers) if raw.unique_buyers is not None else None
        assert match["unique_buyers"] == expected_buyers


def test_gmv_total_nao_muda_mesmo_sem_consolidacao(db):
    """A chave estrita muda quantas LINHAS aparecem, nunca o GMV total: a
    soma de todos os itens elegiveis (GMV>0) deve bater exatamente com
    SUM(gmv) das linhas brutas do mart para o mesmo escopo."""
    ref_row = db.execute(text("SELECT DISTINCT ref_month FROM marts.fact_shopee_product_monthly ORDER BY ref_month LIMIT 1")).fetchone()
    if ref_row is None:
        pytest.skip("mart Shopee vazio nesta maquina")
    year, month = ref_row.ref_month.year, ref_row.ref_month.month

    raw_gmv = db.execute(text("""
        SELECT SUM(gmv) AS gmv FROM marts.fact_shopee_product_monthly
        WHERE ref_month = make_date(:y, :m, 1) AND gmv > 0
    """), {"y": year, "m": month}).fetchone().gmv

    result = perf_svc.get_produtos_shopee(
        db, brand=None, year=year, month=month,
        limit=5000, offset=0, sort_by=None, sort_dir=None, pareto_bucket=None,
    )
    api_gmv = sum(it["gmv"] for it in result["items"])
    assert abs(api_gmv - float(raw_gmv)) < 0.01

    summary = perf_svc.get_produtos_shopee_summary(db, brand=None, year=year, month=month)
    assert abs(summary["total_gmv"] - float(raw_gmv)) < 0.01


def test_total_summary_e_paginacao_usam_a_mesma_chave(db):
    ref_row = db.execute(text("SELECT DISTINCT ref_month FROM marts.fact_shopee_product_monthly ORDER BY ref_month LIMIT 1")).fetchone()
    if ref_row is None:
        pytest.skip("mart Shopee vazio nesta maquina")
    year, month = ref_row.ref_month.year, ref_row.ref_month.month

    items = perf_svc.get_produtos_shopee(
        db, brand=None, year=year, month=month,
        limit=5000, offset=0, sort_by=None, sort_dir=None, pareto_bucket=None,
    )
    summary = perf_svc.get_produtos_shopee_summary(db, brand=None, year=year, month=month)
    assert items["total"] == summary["eligible_count"]
    assert summary["total_count"] >= summary["eligible_count"]

    seen = set()
    offset = 0
    limit = 37
    while True:
        page = perf_svc.get_produtos_shopee(
            db, brand=None, year=year, month=month,
            limit=limit, offset=offset, sort_by="gmv", sort_dir="desc", pareto_bucket=None,
        )
        if not page["items"]:
            break
        for it in page["items"]:
            key = (it["brand"], it["sku_ref"], it["product_name"], it["variation_name"])
            assert key not in seen, f"chave duplicada entre paginas: {key}"
            seen.add(key)
        offset += limit
        if offset > page["total"] + limit:
            break
    assert len(seen) == items["total"]


def test_bucket_filter_reconcilia_com_summary_para_cada_bucket(db):
    ref_row = db.execute(text("SELECT DISTINCT ref_month FROM marts.fact_shopee_product_monthly ORDER BY ref_month LIMIT 1")).fetchone()
    if ref_row is None:
        pytest.skip("mart Shopee vazio nesta maquina")
    year, month = ref_row.ref_month.year, ref_row.ref_month.month

    summary = perf_svc.get_produtos_shopee_summary(db, brand=None, year=year, month=month)
    for bucket in summary["buckets"]:
        page = perf_svc.get_produtos_shopee(
            db, brand=None, year=year, month=month,
            limit=5000, offset=0, sort_by=None, sort_dir=None, pareto_bucket=bucket["bucket"],
        )
        assert page["total"] == bucket["count"], f"bucket {bucket['bucket']}: total={page['total']} != summary count={bucket['count']}"
        assert all(it["pareto_bucket"] == bucket["bucket"] for it in page["items"])


def test_cardinalidade_do_join_por_marca_e_mes(db):
    """Regressao direta do bug de JOIN com NULL (variation_name na condicao de
    USING derrubava ~metade das linhas). Para CADA combinacao marca x mes
    presente no mart, com a chave brand+sku_ref_key+product_name (SEM
    variation_name, que nunca entra na condicao de JOIN):
      - COUNT(*) em `base` (gmv>0) == COUNT(*) apos JOIN bucketed (== total
        retornado por get_produtos_shopee);
      - nenhum produto (brand, sku_ref_key, product_name) aparece 2x na
        resposta;
      - total da tabela == eligible_count do summary;
      - soma dos buckets == eligible_count;
      - GMV antes do JOIN (SUM bruto do mart) == GMV depois do JOIN (soma
        dos itens retornados).
    """
    combos = db.execute(text("""
        SELECT DISTINCT brand, EXTRACT(YEAR FROM ref_month)::int AS y, EXTRACT(MONTH FROM ref_month)::int AS m
        FROM marts.fact_shopee_product_monthly
        ORDER BY brand, y, m
    """)).fetchall()
    if not combos:
        pytest.skip("mart Shopee vazio nesta maquina")

    for combo in combos:
        brand, year, month = combo.brand, combo.y, combo.m

        base_count_row = db.execute(text("""
            SELECT COUNT(*) AS n, SUM(gmv) AS gmv
            FROM marts.fact_shopee_product_monthly
            WHERE ref_month = make_date(:y, :m, 1) AND brand = :brand AND gmv > 0
        """), {"y": year, "m": month, "brand": brand}).fetchone()
        base_count = int(base_count_row.n)
        base_gmv = float(base_count_row.gmv) if base_count_row.gmv is not None else 0.0

        page = perf_svc.get_produtos_shopee(
            db, brand=brand, year=year, month=month,
            limit=5000, offset=0, sort_by=None, sort_dir=None, pareto_bucket=None,
        )
        after_join_count = page["total"]
        assert after_join_count == base_count, (
            f"{brand} {year}-{month:02d}: linhas em base ({base_count}) != "
            f"linhas apos JOIN bucketed ({after_join_count}) — possivel NULL "
            f"em condicao de JOIN descartando linhas"
        )

        # nenhum produto aparece mais de uma vez (chave sem variation_name)
        keys = [(it["brand"], it["sku_ref"], it["product_name"]) for it in page["items"]]
        assert len(keys) == len(set(keys)), f"{brand} {year}-{month:02d}: produto duplicado na resposta"
        assert len(page["items"]) == after_join_count

        after_join_gmv = sum(it["gmv"] for it in page["items"])
        assert abs(after_join_gmv - base_gmv) < 0.01, (
            f"{brand} {year}-{month:02d}: GMV antes do JOIN ({base_gmv}) != depois ({after_join_gmv})"
        )

        summary = perf_svc.get_produtos_shopee_summary(db, brand=brand, year=year, month=month)
        assert after_join_count == summary["eligible_count"], (
            f"{brand} {year}-{month:02d}: total da tabela ({after_join_count}) != "
            f"eligible_count do summary ({summary['eligible_count']})"
        )
        bucket_sum = sum(b["count"] for b in summary["buckets"])
        assert bucket_sum == summary["eligible_count"], (
            f"{brand} {year}-{month:02d}: soma dos buckets ({bucket_sum}) != "
            f"eligible_count ({summary['eligible_count']})"
        )
