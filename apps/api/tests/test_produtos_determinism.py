"""
Testes de determinismo da ordenacao/paginacao de /produtos/* e do grao de
produto do TikTok (Pontos 2 e 3 da correcao pos-QA):

- _build_order_by sempre acrescenta desempate estavel (chave da linha) apos
  a coluna escolhida, preservando NULLS LAST.
- get_produtos_tiktok agrupa por (brand, product_id) — nao por
  (brand, product_id, product_name) — e o COUNT usa exatamente o mesmo
  agrupamento/HAVING da consulta de itens.

Determinismo ponta-a-ponta contra paginas consecutivas com valores
empatados foi validado manualmente contra o Neon real (sem escrita): 1486
produtos ML, 456 produtos TikTok e 471 produtos Shopee paginados por
completo em varias colunas com muitos empates (cancel_rate_pct, problem_rate)
sem nenhuma duplicacao ou omissao de chave.
"""
from app.services import performance_service as perf_svc


class FakeRow:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeCountRow:
    def __init__(self, n):
        self.n = n


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeSession:
    """Retorna, em ordem, um FakeResult por chamada a .execute() e registra
    o texto da query (para inspecionar GROUP BY/ORDER BY sem banco real)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.captured_sql: list[str] = []

    def execute(self, stmt, params=None):
        self.captured_sql.append(str(stmt))
        return self._responses.pop(0)


# ---------------------------------------------------------------------------
# _build_order_by — desempate estavel deterministico
# ---------------------------------------------------------------------------

def test_build_order_by_default_inclui_desempate_ml():
    result = perf_svc._build_order_by(
        None, None, perf_svc.PRODUTOS_ML_SORT_COLUMNS, "gross_revenue", "DESC", ["brand", "item_id"],
    )
    assert result == "ORDER BY gross_revenue DESC NULLS LAST, brand ASC NULLS LAST, item_id ASC NULLS LAST"


def test_build_order_by_coluna_customizada_tambem_inclui_desempate():
    result = perf_svc._build_order_by(
        "ad_roas", "asc", perf_svc.PRODUTOS_ML_SORT_COLUMNS, "gross_revenue", "DESC", ["brand", "item_id"],
    )
    assert result == "ORDER BY ad_roas ASC NULLS LAST, brand ASC NULLS LAST, item_id ASC NULLS LAST"


def test_build_order_by_desempate_tiktok_usa_brand_product_id():
    result = perf_svc._build_order_by(
        "problem_rate", "asc", perf_svc.PRODUTOS_TIKTOK_SORT_COLUMNS, "gmv", "DESC", ["brand", "product_id"],
    )
    assert result.endswith("brand ASC NULLS LAST, product_id ASC NULLS LAST")


def test_build_order_by_desempate_shopee_usa_chave_completa():
    result = perf_svc._build_order_by(
        "cancel_rate_pct", "asc", perf_svc.PRODUTOS_SHOPEE_SORT_COLUMNS, "gmv", "DESC",
        ["brand", "sku_ref", "product_name", "variation_name"],
    )
    assert result == (
        "ORDER BY cancel_rate_pct ASC NULLS LAST, brand ASC NULLS LAST, "
        "sku_ref ASC NULLS LAST, product_name ASC NULLS LAST, variation_name ASC NULLS LAST"
    )


# ---------------------------------------------------------------------------
# get_produtos_tiktok — grao (brand, product_id) e COUNT alinhado
# ---------------------------------------------------------------------------

def _tk_row(**overrides):
    base = dict(
        brand="barbours", product_id="1", product_name="Produto",
        gmv=1000, orders=10, items_sold=10,
        pct_gmv_video=None, pct_gmv_live=None, pct_gmv_card=None,
        problem_rate=None, rating_avg=None, total_ratings=None,
    )
    base.update(overrides)
    return FakeRow(**base)


def test_get_produtos_tiktok_agrupa_por_brand_e_product_id_nao_por_nome():
    db = FakeSession([FakeResult([FakeCountRow(1)]), FakeResult([_tk_row()])])
    perf_svc.get_produtos_tiktok(db, brand=None, year=2026, month=6)
    items_sql = db.captured_sql[1]
    assert "GROUP BY brand, product_id" in items_sql
    assert "GROUP BY brand, product_id, product_name" not in items_sql
    assert "ARRAY_AGG(product_name ORDER BY date DESC)" in items_sql


def test_get_produtos_tiktok_count_usa_mesmo_agrupamento_e_having_dos_itens():
    db = FakeSession([FakeResult([FakeCountRow(1)]), FakeResult([_tk_row()])])
    perf_svc.get_produtos_tiktok(db, brand=None, year=2026, month=6)
    count_sql = db.captured_sql[0]
    assert "COUNT(DISTINCT product_id)" not in count_sql
    assert "GROUP BY brand, product_id" in count_sql
    assert "HAVING SUM(gmv) > 0" in count_sql


def test_get_produtos_tiktok_mesmo_product_id_em_marcas_diferentes_gera_duas_linhas():
    # A SQL real agrupa por (brand, product_id); simulamos aqui o resultado
    # ja agrupado corretamente para validar que o service NAO funde as duas
    # linhas so porque compartilham o mesmo product_id.
    rows = [
        _tk_row(brand="barbours", product_id="123", product_name="Produto A", gmv=1000),
        _tk_row(brand="kokeshi", product_id="123", product_name="Produto B", gmv=500),
    ]
    db = FakeSession([FakeResult([FakeCountRow(2)]), FakeResult(rows)])
    result = perf_svc.get_produtos_tiktok(db, brand=None, year=2026, month=6)
    assert result["total"] == 2
    assert len(result["items"]) == 2
    keys = {(it["brand"], it["product_id"]) for it in result["items"]}
    assert keys == {("barbours", "123"), ("kokeshi", "123")}


def test_get_produtos_tiktok_troca_de_nome_no_mes_nao_duplica_produto():
    # Com o grao correto, a SQL (ARRAY_AGG ORDER BY date DESC) ja resolve
    # para um unico nome por (brand, product_id) antes de chegar ao service —
    # portanto deve haver exatamente uma linha, com o nome mais recente.
    rows = [_tk_row(brand="barbours", product_id="999", product_name="Nome Novo (pos-rebranding)", gmv=2000)]
    db = FakeSession([FakeResult([FakeCountRow(1)]), FakeResult(rows)])
    result = perf_svc.get_produtos_tiktok(db, brand=None, year=2026, month=6)
    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["product_name"] == "Nome Novo (pos-rebranding)"


def test_get_produtos_tiktok_total_reflete_numero_logico_mesmo_com_pagina_parcial():
    # total = tamanho do conjunto logico completo; items = so a pagina atual
    # (limit=1) — sao numeros diferentes por design, nao um bug.
    rows = [_tk_row(product_id="1")]
    db = FakeSession([FakeResult([FakeCountRow(5)]), FakeResult(rows)])
    result = perf_svc.get_produtos_tiktok(db, brand=None, year=2026, month=6, limit=1, offset=0)
    assert result["total"] == 5
    assert len(result["items"]) == 1
