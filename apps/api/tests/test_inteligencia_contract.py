"""Gate S3 — CONTRATO CONGELADO do payload de `/inteligencia`.

Por que este arquivo existe
---------------------------
O Gate S3 troca a fonte de `/inteligencia` de `gold.*` (Data Mart, VPN) para
`marts.*` (Neon). A unica forma de provar que a troca nao muda o payload e' ter,
ANTES da troca, um teste que descreva o payload atual campo a campo. Depois da
troca este mesmo arquivo tem de continuar verde **sem uma unica alteracao de
expectativa** — se precisar ser editado, a troca mudou o contrato.

Diferenca deliberada em relacao a `test_operacoes_contract.py`
-------------------------------------------------------------
Lá o classificador de consultas casa o nome da tabela (`marts.fact_...`), e por
isso ele teve de ser editado junto com a troca. Aqui o classificador e'
**agnostico a fonte**: discrimina cada uma das sete consultas por um marcador de
NEGOCIO (o filtro de `product_status`, o `GROUP BY`, a coluna exclusiva), nunca
pelo prefixo de schema. Assim o mesmo arquivo roda identico antes e depois.

Determinismo
------------
Nao depende de banco nem de producao. `gold_service._query` e' substituida por um
despachante sobre fixtures fixas, e a data operacional e' congelada — a janela de
30 dias de `tk_products` e' calculada a partir dela.

Os fixtures exercitam as regras que passam despercebidas: o **falsy-para-None**
de todas as razoes (zero vira `None`, nao `0.0`), os tres `LIMIT` distintos, a
degradacao silenciosa do bloco `ltv`, e as listas vazias.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import gold_service as gs

HOJE = date(2026, 8, 18)
#: `tk_products` usa uma janela de 30 dias corridos a partir do dia operacional.
TK30 = HOJE - timedelta(days=30)  # 2026-07-19


def _classifica(sql: str) -> str:
    """Qual das sete consultas de `get_inteligencia` esta sendo feita.

    Discriminadores de NEGOCIO, nunca o schema: e' o que permite este arquivo
    valer antes e depois da troca de `gold.*` para `marts.*`.
    """
    s = " ".join(sql.split()).lower()
    # Gate V3-BE: as duas consultas do `opportunity_map` sao as unicas que
    # calculam a mediana do portfolio. `row_number` separa os destaques dos
    # agregados. Discriminadores de negocio, nao de schema.
    if "percentile_disc" in s and "row_number" in s:
        return "opp_destaques"
    if "percentile_disc" in s:
        return "opp_agregados"
    if "total_buyers" in s and "repeat_buyers" in s:
        return "ltv"
    if "pct_gmv_video" in s:
        return "tk_products"
    if "group by product_status" in s:
        return "signals"
    if "group by brand, pareto_bucket" in s:
        return "pareto"
    if "product_status = 'ad_spend_no_sales'" in s:
        return "urgent"
    if "product_status = 'sells+advertised'" in s:
        return "scale"
    if "product_status = 'sells_organic_only'" in s:
        return "organic"
    raise AssertionError(f"consulta inesperada em get_inteligencia: {s[:140]}")


def _congela_data(monkeypatch) -> None:
    """Congela o dia operacional nas duas formas possiveis.

    Antes da troca `get_inteligencia` usa `date.today()`; depois passa a usar
    `_hoje_operacional()` (fuso America/Sao_Paulo). Congelar as duas mantem este
    arquivo valido nos dois estados, sem editar expectativa.
    """
    class _DataFixa(date):
        @classmethod
        def today(cls):
            return HOJE

    monkeypatch.setattr(gs, "date", _DataFixa)
    if hasattr(gs, "_hoje_operacional"):
        monkeypatch.setattr(gs, "_hoje_operacional", lambda *a, **k: HOJE)


@pytest.fixture
def fixar(monkeypatch):
    """Instala data congelada e despachante de fixtures. Devolve
    (dados, vistas) para o teste ajustar e inspecionar."""
    dados: dict[str, list[dict]] = {
        "signals": [], "urgent": [], "scale": [], "organic": [],
        "pareto": [], "ltv": [], "tk_products": [],
        "opp_agregados": [], "opp_destaques": [],
    }
    vistas: list[tuple[str, str]] = []

    def fake_query(db, sql):
        tipo = _classifica(sql)
        vistas.append((tipo, " ".join(sql.split())))
        return dados[tipo]

    _congela_data(monkeypatch)
    monkeypatch.setattr(gs, "_query", fake_query)
    return dados, vistas


def _base_ltv() -> dict:
    return {"brand": "b", "total_buyers": 1, "repeat_buyers": 1, "repeat_rate_pct": 1.0,
            "avg_customer_ltv": 1.0, "vip_buyers": 1, "one_and_done_buyers": 1,
            "at_risk_or_churned": 1, "overall_roas": 1.0}


def _base_tk() -> dict:
    return {"brand": "a", "product_name": "p", "gmv": 1.0, "orders": 1,
            "avg_pct_video": 1.0, "avg_pct_live": 1.0, "avg_pct_card": 1.0,
            "avg_rating": 1.0}


def _sql_de(vistas, tipo: str) -> str:
    for t, sql in vistas:
        if t == tipo:
            return sql
    raise AssertionError(f"consulta {tipo!r} nao foi executada")


# ===========================================================================
# Estrutura top-level
# ===========================================================================

def test_i01_chaves_top_level_exatas(fixar):
    """Sete blocos originais nas sete PRIMEIRAS posicoes, mais as seis chaves
    aditivas do Gate V3-BE. Treze no total, nesta ordem, nem uma a mais.
    """
    out = gs.get_inteligencia(object())
    assert list(out) == [
        "signals", "urgent", "scale", "organic", "pareto", "ltv", "tk_products",
        "urgent_total_count", "scale_total_count", "organic_total_count",
        "ml_snapshot_refreshed_at", "ml_scope_brands", "opportunity_map",
    ]


BLOCOS_LISTA = ("signals", "urgent", "scale", "organic", "pareto", "ltv", "tk_products")


def test_i02_todos_os_blocos_sao_listas(fixar):
    """Os sete blocos originais seguem listas. As chaves aditivas tem tipos
    proprios e declarados: tres inteiros, um timestamp opcional, uma lista de
    marcas e um dict."""
    out = gs.get_inteligencia(object())
    for k in BLOCOS_LISTA:
        assert isinstance(out[k], list), f"{k} deveria ser lista"
    for k in ("urgent_total_count", "scale_total_count", "organic_total_count"):
        assert isinstance(out[k], int) and not isinstance(out[k], bool)
    assert out["ml_snapshot_refreshed_at"] is None or isinstance(out["ml_snapshot_refreshed_at"], str)
    assert isinstance(out["ml_scope_brands"], list)
    assert isinstance(out["opportunity_map"], dict)


def test_i03_fonte_vazia_produz_sete_listas_vazias(fixar):
    """Fonte vazia: as sete listas vazias, os tres totais em zero, frescor
    `None` e o mapa no estado `empty` — sem nada fabricado.
    """
    out = gs.get_inteligencia(object())
    assert all(out[k] == [] for k in BLOCOS_LISTA)
    assert out["urgent_total_count"] == 0
    assert out["scale_total_count"] == 0
    assert out["organic_total_count"] == 0
    assert out["ml_snapshot_refreshed_at"] is None
    assert out["opportunity_map"]["classification_status"] == "empty"
    assert out["opportunity_map"]["total_count"] == 0


def test_i04_as_sete_consultas_sao_executadas(fixar):
    """Nove consultas: as sete originais mais as duas do `opportunity_map`.
    BE3 e BE4 NAO abrem round-trip — viajam na consulta de `signals`.
    """
    _, vistas = fixar
    gs.get_inteligencia(object())
    assert sorted(t for t, _ in vistas) == [
        "ltv", "opp_agregados", "opp_destaques", "organic", "pareto",
        "scale", "signals", "tk_products", "urgent"]
    assert len(vistas) == 9


# ===========================================================================
# signals
# ===========================================================================

def test_i05_signals_campos_e_tipos(fixar):
    dados, _ = fixar
    dados["signals"] = [
        {"product_status": "sells+advertised", "n_products": 12,
         "gmv": 1000.5, "ad_spend": 100.25, "avg_roas": 9.8765},
    ]
    s = gs.get_inteligencia(object())["signals"][0]
    assert list(s) == ["product_status", "n_products", "gmv", "ad_spend", "avg_roas"]
    assert s == {"product_status": "sells+advertised", "n_products": 12,
                 "gmv": 1000.5, "ad_spend": 100.25, "avg_roas": 9.88}
    assert isinstance(s["n_products"], int)


def test_i06_signals_avg_roas_preserva_zero(fixar):
    """Gate V3-BE: a guarda virou `is not None`. A DEFINICAO de `avg_roas` NAO
    mudou: segue `AVG(CASE WHEN ad_roas > 0 ...)`, que por construcao devolve
    `NULL` ou positivo, nunca zero. Nao confundir esta media de positivos com o
    ROAS individual do produto, que agora preserva zero.
    """
    dados, _ = fixar
    dados["signals"] = [{"product_status": "x", "n_products": 1, "gmv": 0,
                         "ad_spend": 0, "avg_roas": 0}]
    assert gs.get_inteligencia(object())["signals"][0]["avg_roas"] == 0.0


def test_i06b_signals_avg_roas_null_continua_none(fixar):
    dados, _ = fixar
    dados["signals"] = [{"product_status": "x", "n_products": 1, "gmv": 0,
                         "ad_spend": 0, "avg_roas": None}]
    assert gs.get_inteligencia(object())["signals"][0]["avg_roas"] is None


def test_i07_signals_avg_roas_none_vira_none(fixar):
    dados, _ = fixar
    dados["signals"] = [{"product_status": "x", "n_products": 1, "gmv": 0,
                         "ad_spend": 0, "avg_roas": None}]
    assert gs.get_inteligencia(object())["signals"][0]["avg_roas"] is None


def test_i08_signals_agrupa_por_status_e_ordena_por_gmv(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = _sql_de(vistas, "signals").lower()
    assert "group by product_status" in sql
    assert "order by gmv desc" in sql
    assert "product_status is not null" in sql


# ===========================================================================
# urgent / scale / organic — mesma forma, filtros e limites distintos
# ===========================================================================

LINHA_PRODUTO = {
    "brand": "kokeshi", "title": "Produto X", "pareto_bucket": "A",
    "revenue_velocity": "rising", "gmv": 5000.0, "ad_spend": 900.0,
    "ad_roas": 5.5555, "ad_acos_pct": 18.0044, "cancel_rate_pct": 2.3456,
    "revenue_share_pct": 0.123456, "units_sold": 77, "days_advertised": 30,
    "ad_efficiency": "eficiente", "unique_buyers": 70,
}

CAMPOS_PRODUTO = ["brand", "title", "pareto_bucket", "revenue_velocity", "gmv",
                  "ad_spend", "ad_roas", "ad_acos_pct", "cancel_rate_pct",
                  "revenue_share_pct", "units_sold", "days_advertised", "ad_efficiency"]


@pytest.mark.parametrize("bloco", ["urgent", "scale", "organic"])
def test_i09_blocos_de_produto_tem_os_mesmos_campos(fixar, bloco):
    dados, _ = fixar
    dados[bloco] = [dict(LINHA_PRODUTO)]
    linha = gs.get_inteligencia(object())[bloco][0]
    assert list(linha) == CAMPOS_PRODUTO


@pytest.mark.parametrize("bloco", ["urgent", "scale", "organic"])
def test_i10_blocos_de_produto_arredondam_como_hoje(fixar, bloco):
    dados, _ = fixar
    dados[bloco] = [dict(LINHA_PRODUTO)]
    linha = gs.get_inteligencia(object())[bloco][0]
    assert linha["gmv"] == 5000.0
    assert linha["ad_spend"] == 900.0
    assert linha["ad_roas"] == 5.56             # 2 casas
    assert linha["ad_acos_pct"] == 18.0          # 2 casas
    assert linha["cancel_rate_pct"] == 2.35      # 2 casas
    assert linha["revenue_share_pct"] == 0.123   # 3 casas
    assert linha["units_sold"] == 77
    assert linha["days_advertised"] == 30
    assert linha["ad_efficiency"] == "eficiente"


@pytest.mark.parametrize("bloco", ["urgent", "scale", "organic"])
@pytest.mark.parametrize("campo", ["ad_roas", "ad_acos_pct", "cancel_rate_pct",
                                   "revenue_share_pct", "units_sold", "days_advertised"])
def test_i11_zero_numerico_e_preservado_em_todo_campo_opcional(fixar, bloco, campo):
    """Gate V3-BE, Task A: a guarda virou `is not None`. Zero e um VALOR e
    chega ao payload como zero. Antes a serializacao o transformava em `None`,
    e um ROAS zero real (133 produtos na fotografia auditada) chegava a tela
    como indisponibilidade.
    """
    dados, _ = fixar
    linha = dict(LINHA_PRODUTO)
    linha[campo] = 0
    dados[bloco] = [linha]
    obtido = gs.get_inteligencia(object())[bloco][0][campo]
    assert obtido == 0
    assert obtido is not None


@pytest.mark.parametrize("bloco", ["urgent", "scale", "organic"])
@pytest.mark.parametrize("campo", ["ad_roas", "ad_acos_pct", "cancel_rate_pct",
                                   "revenue_share_pct", "units_sold", "days_advertised"])
def test_i11b_null_continua_null_em_todo_campo_opcional(fixar, bloco, campo):
    """Contraprova: `None` na fonte segue `None` no payload. Zero e ausencia
    continuam distinguiveis.
    """
    dados, _ = fixar
    linha = dict(LINHA_PRODUTO)
    linha[campo] = None
    dados[bloco] = [linha]
    assert gs.get_inteligencia(object())[bloco][0][campo] is None


@pytest.mark.parametrize("bloco", ["urgent", "scale", "organic"])
def test_i12_pareto_bucket_e_revenue_velocity_ausentes_saem_none(fixar, bloco):
    dados, _ = fixar
    linha = {k: v for k, v in LINHA_PRODUTO.items()
             if k not in ("pareto_bucket", "revenue_velocity", "ad_efficiency")}
    dados[bloco] = [linha]
    r = gs.get_inteligencia(object())[bloco][0]
    assert r["pareto_bucket"] is None
    assert r["revenue_velocity"] is None
    assert r["ad_efficiency"] is None


def test_i13_urgent_limit_30_filtro_e_ordenacao(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = _sql_de(vistas, "urgent").lower()
    assert "limit 30" in sql
    assert "product_status = 'ad_spend_no_sales'" in sql
    assert "order by ad_spend desc" in sql


def test_i14_scale_limit_20_filtro_roas_e_ordenacao(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = _sql_de(vistas, "scale").lower()
    assert "limit 20" in sql
    assert "product_status = 'sells+advertised'" in sql
    assert "ad_roas >= 8" in sql
    assert "order by ad_roas desc" in sql


def test_i15_organic_limit_20_filtro_e_ordenacao(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = _sql_de(vistas, "organic").lower()
    assert "limit 20" in sql
    assert "product_status = 'sells_organic_only'" in sql
    assert "order by gross_revenue desc" in sql


def test_i16_organic_le_unique_buyers_da_fonte(fixar):
    """`organic` e' a unica das tres que seleciona `unique_buyers` na fonte —
    mesmo nao o expondo no payload. Congelar impede que a troca perca a coluna."""
    _, vistas = fixar
    gs.get_inteligencia(object())
    assert "unique_buyers" in _sql_de(vistas, "organic").lower()
    assert "unique_buyers" not in _sql_de(vistas, "urgent").lower()
    assert "unique_buyers" not in _sql_de(vistas, "scale").lower()


def test_i17_ordem_das_linhas_e_a_da_fonte(fixar):
    """A ordenacao vem do SQL; o Python nao reordena."""
    dados, _ = fixar
    a = dict(LINHA_PRODUTO, title="primeiro")
    b = dict(LINHA_PRODUTO, title="segundo")
    dados["urgent"] = [a, b]
    titulos = [r["title"] for r in gs.get_inteligencia(object())["urgent"]]
    assert titulos == ["primeiro", "segundo"]


# ===========================================================================
# pareto
# ===========================================================================

def test_i18_pareto_campos_tipos_e_agrupamento(fixar):
    dados, vistas = fixar
    dados["pareto"] = [{"brand": "lescent", "pareto_bucket": "B",
                        "n_products": 40, "gmv": 250.75, "ad_spend": 10.5}]
    p = gs.get_inteligencia(object())["pareto"][0]
    assert p == {"brand": "lescent", "pareto_bucket": "B",
                 "n_products": 40, "gmv": 250.75, "ad_spend": 10.5}
    assert isinstance(p["n_products"], int)
    sql = _sql_de(vistas, "pareto").lower()
    assert "group by brand, pareto_bucket" in sql
    assert "order by brand, pareto_bucket" in sql
    assert "pareto_bucket is not null" in sql


# ===========================================================================
# ltv — o unico bloco com degradacao silenciosa
# ===========================================================================

CAMPOS_LTV = ["brand", "total_buyers", "repeat_buyers", "repeat_rate_pct",
              "avg_customer_ltv", "vip_buyers", "one_and_done_buyers",
              "at_risk_or_churned", "overall_roas"]


def test_i19_ltv_campos_e_arredondamento(fixar):
    dados, _ = fixar
    dados["ltv"] = [{"brand": "barbours", "total_buyers": 1000, "repeat_buyers": 250,
                     "repeat_rate_pct": 25.0444, "avg_customer_ltv": 133.339,
                     "vip_buyers": 30, "one_and_done_buyers": 750,
                     "at_risk_or_churned": 120, "overall_roas": 7.1234}]
    r = gs.get_inteligencia(object())["ltv"][0]
    assert list(r) == CAMPOS_LTV
    assert r["total_buyers"] == 1000 and isinstance(r["total_buyers"], int)
    assert r["repeat_rate_pct"] == 25.04
    assert r["avg_customer_ltv"] == 133.34
    assert r["overall_roas"] == 7.12


@pytest.mark.parametrize("campo", ["repeat_rate_pct", "avg_customer_ltv", "vip_buyers",
                                   "one_and_done_buyers", "at_risk_or_churned", "overall_roas"])
def test_i20_ltv_preserva_zero(fixar, campo):
    """Gate V3-BE: recorrencia 0% e LTV 0 sao fatos, nao ausencia."""
    dados, _ = fixar
    base = _base_ltv()
    base[campo] = 0
    dados["ltv"] = [base]
    assert gs.get_inteligencia(object())["ltv"][0][campo] == 0


@pytest.mark.parametrize("campo", ["repeat_rate_pct", "avg_customer_ltv", "vip_buyers",
                                   "one_and_done_buyers", "at_risk_or_churned", "overall_roas"])
def test_i20b_ltv_null_continua_none(fixar, campo):
    dados, _ = fixar
    base = _base_ltv()
    base[campo] = None
    dados["ltv"] = [base]
    assert gs.get_inteligencia(object())["ltv"][0][campo] is None


def test_i21_ltv_ordenado_por_brand(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    assert "order by brand" in _sql_de(vistas, "ltv").lower()


def test_i22_ltv_indisponivel_degrada_para_lista_vazia_sem_derrubar_a_rota(monkeypatch):
    """Comportamento historico: o bloco `ltv` esta dentro de try/except e vira
    `[]` se a fonte falhar. Os outros seis blocos continuam servidos."""
    _congela_data(monkeypatch)

    def fake_query(db, sql):
        if _classifica(sql) == "ltv":
            raise RuntimeError("fonte de ltv indisponivel")
        return []

    monkeypatch.setattr(gs, "_query", fake_query)
    out = gs.get_inteligencia(object())
    assert out["ltv"] == []
    assert list(out)[:7] == [
        "signals", "urgent", "scale", "organic", "pareto", "ltv", "tk_products",
    ]
    assert len(out) == 13


def test_i23_falha_fora_de_ltv_propaga(monkeypatch):
    """Somente `ltv` degrada. Falha em qualquer outro bloco tem de subir — e' o
    que faz a rota responder erro em vez de payload silenciosamente incompleto."""
    _congela_data(monkeypatch)

    def fake_query(db, sql):
        if _classifica(sql) == "signals":
            raise RuntimeError("fonte de signals indisponivel")
        return []

    monkeypatch.setattr(gs, "_query", fake_query)
    with pytest.raises(RuntimeError):
        gs.get_inteligencia(object())


# ===========================================================================
# tk_products
# ===========================================================================

def test_i24_tk_products_campos_e_arredondamento(fixar):
    dados, _ = fixar
    dados["tk_products"] = [{"brand": "apice", "product_name": "Kit Y",
                             "gmv": 9000.0, "orders": 120,
                             "avg_pct_video": 55.55, "avg_pct_live": 30.44,
                             "avg_pct_card": 14.01, "avg_rating": 4.666}]
    r = gs.get_inteligencia(object())["tk_products"][0]
    assert list(r) == ["brand", "product_name", "gmv", "orders",
                       "avg_pct_video", "avg_pct_live", "avg_pct_card", "avg_rating"]
    assert r["gmv"] == 9000.0
    assert r["orders"] == 120 and isinstance(r["orders"], int)
    assert r["avg_pct_video"] == 55.5   # 1 casa; round(55.55,1) e 55.5 em float
    assert r["avg_pct_live"] == 30.4
    assert r["avg_pct_card"] == 14.0
    assert r["avg_rating"] == 4.7


@pytest.mark.parametrize("campo", ["avg_pct_video", "avg_pct_live", "avg_pct_card", "avg_rating"])
def test_i25_tk_products_preserva_zero(fixar, campo):
    """Gate V3-BE: 0% de GMV em video/live/card e um fato do mix, nao ausencia."""
    dados, _ = fixar
    base = _base_tk()
    base[campo] = 0
    dados["tk_products"] = [base]
    assert gs.get_inteligencia(object())["tk_products"][0][campo] == 0


@pytest.mark.parametrize("campo", ["avg_pct_video", "avg_pct_live", "avg_pct_card", "avg_rating"])
def test_i25b_tk_products_null_continua_none(fixar, campo):
    dados, _ = fixar
    base = _base_tk()
    base[campo] = None
    dados["tk_products"] = [base]
    assert gs.get_inteligencia(object())["tk_products"][0][campo] is None


def test_i26_tk_products_limit_25_janela_30_dias_e_gmv_positivo(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = _sql_de(vistas, "tk_products").lower()
    assert "limit 25" in sql
    assert "gmv > 0" in sql
    assert "group by brand, product_name" in sql
    assert "order by sum(gmv) desc" in sql
    assert str(TK30) in sql, f"a janela de 30 dias deveria comecar em {TK30}"


def test_i27_janela_de_30_dias_deriva_do_dia_operacional_congelado(fixar):
    """Prova que a janela nao e' um literal: muda com o dia operacional."""
    _, vistas = fixar
    gs.get_inteligencia(object())
    assert (HOJE - TK30).days == 30


# ===========================================================================
# Filtros de marca — allowlist, nunca marca solta no SQL
# ===========================================================================

def test_i28_blocos_ml_filtram_pela_allowlist_de_ml(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    for tipo in ("signals", "urgent", "scale", "organic", "pareto", "ltv"):
        sql = _sql_de(vistas, tipo).lower()
        for marca in gs.ML_BRANDS:
            assert marca in sql, f"{tipo} nao filtra {marca}"


def test_i29_tk_products_filtra_pela_allowlist_de_tiktok(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    sql = _sql_de(vistas, "tk_products").lower()
    for marca in gs.BRANDS_IN_SCOPE:
        assert marca in sql


def test_i30_nenhuma_marca_fora_das_allowlists_aparece(fixar):
    _, vistas = fixar
    gs.get_inteligencia(object())
    permitidas = set(gs.ML_BRANDS) | set(gs.BRANDS_IN_SCOPE)
    for _, sql in vistas:
        for token in ("shopee", "magalu", "amazon"):
            assert token not in sql.lower()
    assert permitidas  # allowlists nao vazias


# ===========================================================================
# Ausencia de campo novo — a garantia central da troca
# ===========================================================================

def test_i31_nenhum_campo_novo_em_nenhum_bloco(fixar):
    dados, _ = fixar
    dados["signals"] = [{"product_status": "x", "n_products": 1, "gmv": 1.0,
                         "ad_spend": 1.0, "avg_roas": 1.0}]
    dados["urgent"] = [dict(LINHA_PRODUTO)]
    dados["scale"] = [dict(LINHA_PRODUTO)]
    dados["organic"] = [dict(LINHA_PRODUTO)]
    dados["pareto"] = [{"brand": "b", "pareto_bucket": "A", "n_products": 1,
                        "gmv": 1.0, "ad_spend": 1.0}]
    dados["ltv"] = [{"brand": "b", "total_buyers": 1, "repeat_buyers": 1,
                     "repeat_rate_pct": 1.0, "avg_customer_ltv": 1.0, "vip_buyers": 1,
                     "one_and_done_buyers": 1, "at_risk_or_churned": 1, "overall_roas": 1.0}]
    dados["tk_products"] = [{"brand": "b", "product_name": "p", "gmv": 1.0, "orders": 1,
                             "avg_pct_video": 1.0, "avg_pct_live": 1.0,
                             "avg_pct_card": 1.0, "avg_rating": 1.0}]
    out = gs.get_inteligencia(object())
    esperado = {
        "signals": ["product_status", "n_products", "gmv", "ad_spend", "avg_roas"],
        "urgent": CAMPOS_PRODUTO, "scale": CAMPOS_PRODUTO, "organic": CAMPOS_PRODUTO,
        "pareto": ["brand", "pareto_bucket", "n_products", "gmv", "ad_spend"],
        "ltv": CAMPOS_LTV,
        "tk_products": ["brand", "product_name", "gmv", "orders", "avg_pct_video",
                        "avg_pct_live", "avg_pct_card", "avg_rating"],
    }
    for bloco, campos in esperado.items():
        assert list(out[bloco][0]) == campos, f"campo novo/removido em {bloco}"


def test_i32_a_rota_nao_declara_response_model(fixar):
    """Estado de fato desta task: `/inteligencia` nao tem `response_model`, e o
    S3 nao cria um. Congelado para que a criacao seja decisao explicita."""
    from app.routers import performance as rp

    rota = next(r for r in rp.router.routes if getattr(r, "path", "") == "/api/v1/performance/inteligencia")
    assert getattr(rota, "response_model", None) is None
