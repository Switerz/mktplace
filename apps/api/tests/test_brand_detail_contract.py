"""Gate S3 — CONTRATO CONGELADO do payload de `/brand-detail`.

Mesmo papel de `test_inteligencia_contract.py`: descrever o payload ANTES da troca
de `gold.*` para `marts.*`, campo a campo, para que a troca seja provada sem
alteracao de contrato. Se alguma expectativa aqui precisar ser editada junto com a
troca, a troca mudou o payload.

O classificador das cinco consultas e' **agnostico a fonte** — discrimina por
marcador de negocio (`cos_pct`, `GROUP BY creator`, `GROUP BY channel`...), nunca
pelo prefixo de schema. Assim o arquivo vale identico nos dois estados.

Fixtures escolhidas para exercitar o que passa despercebido:

- `_r()` devolve `None` quando o valor e' **zero**, nao `0.0` — vale para 20+
  campos escalares e para `ctr_pct`/`cvr_pct` do funil;
- `daily` aplica `_float(...) or None`: dia com GMV zero sai `None`;
- `top_creators` troca criador vazio pelo travessao `—`;
- `top_produtos` idem para `product_name`, e `product_id` sai sempre string;
- `channel_funnel` mapeia `VIDEO/LIVE/PRODUCT_CARD` para rotulos curtos e deixa
  qualquer outro canal passar cru;
- as 14 ponderacoes demograficas sao razoes vindas da fonte, nunca recalculadas
  em Python.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from app.routers import performance as rp
from app.services import gold_service as gs

ANO, MES = 2026, 7
INICIO, FIM = date(2026, 7, 1), date(2026, 7, 31)
MARCA = "kokeshi"


def _classifica(sql: str) -> str:
    """Qual das seis consultas de `get_brand_detail` esta sendo feita."""
    s = " ".join(sql.split()).lower()
    # Gate V3-BE / BE5: a unica consulta que projeta competencia e NAO filtra
    # por mes — e por isso a unica capaz de listar outros meses.
    if "distinct to_char(date" in s:
        return "available_months"
    if "group by channel" in s:
        return "channel_funnel"
    if "group by creator" in s:
        return "creators"
    if "group by product_id" in s:
        return "products"
    if "cos_pct" in s:
        return "monthly"
    if "new_videos_posted" in s and "order by date" in s:
        return "daily"
    raise AssertionError(f"consulta inesperada em get_brand_detail: {s[:140]}")


@pytest.fixture
def fixar(monkeypatch):
    dados: dict[str, list[dict]] = {
        "monthly": [], "daily": [], "creators": [], "products": [], "channel_funnel": [],
        "available_months": [],
    }
    vistas: list[tuple[str, str]] = []

    def fake_query(db, sql):
        tipo = _classifica(sql)
        vistas.append((tipo, " ".join(sql.split())))
        return dados[tipo]

    monkeypatch.setattr(gs, "_query", fake_query)
    return dados, vistas


def _sql_de(vistas, tipo: str) -> str:
    for t, sql in vistas:
        if t == tipo:
            return sql
    raise AssertionError(f"consulta {tipo!r} nao foi executada")


def _chamar(**kwargs):
    return gs.get_brand_detail(object(), kwargs.pop("brand", MARCA),
                              kwargs.pop("year", ANO), kwargs.pop("month", MES))


#: Uma linha mensal completa, com todos os campos que o SQL agrega.
LINHA_MENSAL = {
    "gmv": 1234567.89, "orders": 4321, "customers": 3210,
    "cvr_pct": 2.34567, "cos_pct": 12.3456,
    "pct_video": 45.678, "pct_live": 30.123, "pct_card": 24.199,
    "active_videos": 5000, "new_videos_posted": 250,
    "active_video_creators": 180, "total_views": 9876543,
    "total_lives": 400, "live_creators": 60,
    "gpm": 125.0555, "gmv_per_video": 2000.44, "gmv_per_creator": 3000.55,
    "gmv_per_live": 1500.66, "videos_per_creator": 27.777,
    "fresh_videos": 90, "evergreen_videos": 160,
    "gmv_fresh": 300000.11, "gmv_evergreen": 900000.22, "pct_gmv_fresh": 24.999,
    "viewers_pct_female": 71.11, "viewers_pct_male": 28.89,
    "viewers_pct_18_24": 30.01, "viewers_pct_25_34": 40.02,
    "viewers_pct_35_44": 20.03, "viewers_pct_45_54": 7.04, "viewers_pct_55_plus": 2.9,
    "followers_pct_female": 72.21, "followers_pct_male": 27.79,
    "followers_pct_18_24": 31.11, "followers_pct_25_34": 41.12,
    "followers_pct_35_44": 19.13, "followers_pct_45_54": 6.14,
    "followers_pct_55_plus": 2.4,
}

CAMPOS_TOP_LEVEL = [
    # Gate V3-BE / BE5: `available_months` entra logo depois de `ref_month`,
    # que continua ecoando a competencia PEDIDA.
    "brand", "label", "ref_month", "available_months", "gmv", "orders", "customers", "cvr_pct", "cos_pct",
    "pct_video", "pct_live", "pct_card", "active_videos", "new_videos_posted",
    "active_video_creators", "total_views", "total_lives", "live_creators", "gpm",
    "gmv_per_video", "gmv_per_creator", "gmv_per_live", "videos_per_creator",
    "fresh_videos", "evergreen_videos", "gmv_fresh", "gmv_evergreen", "pct_gmv_fresh",
    "viewers_pct_female", "viewers_pct_male", "viewers_pct_18_24", "viewers_pct_25_34",
    "viewers_pct_35_44", "viewers_pct_45_54", "viewers_pct_55_plus",
    "followers_pct_female", "followers_pct_male", "followers_pct_18_24",
    "followers_pct_25_34", "followers_pct_35_44", "followers_pct_45_54",
    "followers_pct_55_plus",
    "channel_funnel", "daily", "top_creators", "top_produtos",
]

DEMOGRAFICOS = [c for c in CAMPOS_TOP_LEVEL if c.startswith(("viewers_pct", "followers_pct"))]


# ===========================================================================
# Estrutura
# ===========================================================================

def test_b01_chaves_top_level_exatas_e_na_ordem(fixar):
    out = _chamar()
    assert list(out) == CAMPOS_TOP_LEVEL


def test_b02_sao_46_chaves_das_quais_5_listas(fixar):
    """Gate V3-BE: +1 chave e +1 lista, `available_months`."""
    out = _chamar()
    assert len(out) == 46
    listas = [k for k, v in out.items() if isinstance(v, list)]
    assert listas == ["available_months", "channel_funnel", "daily",
                      "top_creators", "top_produtos"]


def test_b03_identificacao_da_marca_e_do_mes(fixar):
    out = _chamar()
    assert out["brand"] == MARCA
    assert out["label"] == gs.BRAND_LABELS.get(MARCA, MARCA.upper())
    assert out["ref_month"] == "2026-07"


@pytest.mark.parametrize("ano,mes,esperado", [
    (2026, 1, "2026-01"), (2026, 7, "2026-07"), (2026, 12, "2026-12"), (2025, 10, "2025-10"),
])
def test_b04_ref_month_formatado_com_zero_a_esquerda(fixar, ano, mes, esperado):
    assert _chamar(year=ano, month=mes)["ref_month"] == esperado


def test_b05_as_seis_consultas_sao_executadas(fixar):
    """Gate V3-BE: +1 consulta, a de `available_months` (BE5)."""
    _, vistas = fixar
    _chamar()
    assert sorted(t for t, _ in vistas) == [
        "available_months", "channel_funnel", "creators", "daily", "monthly",
        "products"]
    assert len(vistas) == 6


def test_b06_fonte_vazia_produz_zeros_e_listas_vazias(fixar):
    out = _chamar()
    assert out["gmv"] == 0.0
    assert out["orders"] == 0
    assert out["customers"] == 0
    assert out["cvr_pct"] is None
    assert out["channel_funnel"] == [] and out["daily"] == []
    assert out["top_creators"] == [] and out["top_produtos"] == []


# ===========================================================================
# Mes-calendario: a janela e' derivada, nao literal
# ===========================================================================

@pytest.mark.parametrize("ano,mes,ini,fim", [
    (2026, 7, "2026-07-01", "2026-07-31"),
    (2026, 2, "2026-02-01", "2026-02-28"),
    (2024, 2, "2024-02-01", "2024-02-29"),   # bissexto
    (2026, 12, "2026-12-01", "2026-12-31"),  # virada de ano
    (2025, 10, "2025-10-01", "2025-10-31"),  # primeiro mes com dado na fonte
])
def test_b07_janela_de_mes_calendario_em_todas_as_consultas(fixar, ano, mes, ini, fim):
    """Todas as consultas DE DADOS seguem cercadas pelo mes calendario. A de
    `available_months` e a unica excecao, e e' deliberada: ela existe para
    descobrir OUTRAS competencias, entao nao pode ser filtrada pelo mes pedido.
    """
    _, vistas = fixar
    _chamar(year=ano, month=mes)
    for tipo, sql in vistas:
        if tipo == "available_months":
            assert ini not in sql and fim not in sql, (
                "available_months nao pode ser cercada pelo mes pedido")
            assert "between" not in sql.lower()
            continue
        assert ini in sql, f"{tipo} sem inicio {ini}"
        assert fim in sql, f"{tipo} sem fim {fim}"


def test_b08_a_marca_entra_em_todas_as_cinco_consultas(fixar):
    _, vistas = fixar
    _chamar(brand="lescent")
    for tipo, sql in vistas:
        assert "lescent" in sql.lower(), f"{tipo} sem filtro de marca"


# ===========================================================================
# Escalares: arredondamento e falsy-para-None
# ===========================================================================

def test_b09_escalares_arredondam_exatamente_como_hoje(fixar):
    dados, _ = fixar
    dados["monthly"] = [dict(LINHA_MENSAL)]
    o = _chamar()
    assert o["gmv"] == 1234567.89
    assert o["orders"] == 4321 and isinstance(o["orders"], int)
    assert o["customers"] == 3210 and isinstance(o["customers"], int)
    assert o["cvr_pct"] == 2.35        # 2 casas
    assert o["cos_pct"] == 12.35       # 2 casas
    assert o["pct_video"] == 45.7      # 1 casa
    assert o["pct_live"] == 30.1
    assert o["pct_card"] == 24.2
    assert o["gpm"] == 125.06          # 2 casas
    assert o["gmv_per_video"] == 2000.44
    assert o["gmv_per_creator"] == 3000.55
    assert o["gmv_per_live"] == 1500.66
    assert o["videos_per_creator"] == 27.8   # 1 casa
    assert o["pct_gmv_fresh"] == 25.0        # 1 casa
    assert o["gmv_fresh"] == 300000.11
    assert o["gmv_evergreen"] == 900000.22


def test_b10_contadores_inteiros_permanecem_inteiros(fixar):
    dados, _ = fixar
    dados["monthly"] = [dict(LINHA_MENSAL)]
    o = _chamar()
    for campo, valor in (("active_videos", 5000), ("new_videos_posted", 250),
                         ("active_video_creators", 180), ("total_views", 9876543),
                         ("total_lives", 400), ("live_creators", 60),
                         ("fresh_videos", 90), ("evergreen_videos", 160)):
        assert o[campo] == valor and isinstance(o[campo], int), campo


CAMPOS_FALSY = ["cvr_pct", "cos_pct", "pct_video", "pct_live", "pct_card", "gpm",
                "gmv_per_video", "gmv_per_creator", "gmv_per_live",
                "videos_per_creator", "pct_gmv_fresh"] + DEMOGRAFICOS


@pytest.mark.parametrize("campo", CAMPOS_FALSY)
def test_b11_zero_vira_none_em_todo_campo_que_passa_por_r(fixar, campo):
    """`_r()` devolve `None` quando o valor arredondado e' zero. Congelado: e'
    o que distingue "sem dado" de "zero medido" no payload atual."""
    dados, _ = fixar
    linha = dict(LINHA_MENSAL)
    linha[campo] = 0
    dados["monthly"] = [linha]
    assert _chamar()[campo] is None, campo


@pytest.mark.parametrize("campo", CAMPOS_FALSY)
def test_b12_none_da_fonte_vira_none_no_payload(fixar, campo):
    dados, _ = fixar
    linha = dict(LINHA_MENSAL)
    linha[campo] = None
    dados["monthly"] = [linha]
    assert _chamar()[campo] is None, campo


def test_b13_as_14_ponderacoes_demograficas_vem_da_fonte_com_1_casa(fixar):
    dados, _ = fixar
    dados["monthly"] = [dict(LINHA_MENSAL)]
    o = _chamar()
    assert len(DEMOGRAFICOS) == 14
    assert o["viewers_pct_female"] == 71.1
    assert o["viewers_pct_male"] == 28.9
    assert o["viewers_pct_18_24"] == 30.0
    assert o["viewers_pct_55_plus"] == 2.9
    assert o["followers_pct_female"] == 72.2
    assert o["followers_pct_55_plus"] == 2.4


def test_b14_ponderacao_demografica_e_feita_no_sql_por_views_weighted(fixar):
    """A media e' ponderada por `viewers_views_weighted`/`followers_views_weighted`
    na fonte — o Python nunca recalcula. Congelar impede que a troca perca o peso."""
    _, vistas = fixar
    _chamar()
    sql = _sql_de(vistas, "monthly").lower()
    assert "viewers_views_weighted" in sql
    assert "followers_views_weighted" in sql
    assert sql.count("viewers_views_weighted") >= 8
    assert sql.count("followers_views_weighted") >= 8


def test_b15_cvr_e_cos_usam_as_regras_atuais_na_fonte(fixar):
    _, vistas = fixar
    _chamar()
    sql = _sql_de(vistas, "monthly").lower()
    assert "visitors > 0" in sql          # CVR ignora dia sem visitante
    assert "abs(sum(total_fees))" in sql  # COS usa o valor absoluto das taxas


# ===========================================================================
# channel_funnel
# ===========================================================================

def test_b16_channel_funnel_campos_rotulos_e_tipos(fixar):
    dados, _ = fixar
    dados["channel_funnel"] = [
        {"channel": "VIDEO", "impressions": 1000, "page_views": 100,
         "items_sold": 10, "gmv": 500.5, "ctr_pct": 10.004, "cvr_pct": 9.996},
        {"channel": "LIVE", "impressions": 2000, "page_views": 200,
         "items_sold": 20, "gmv": 700.25, "ctr_pct": 10.0, "cvr_pct": 10.0},
        {"channel": "PRODUCT_CARD", "impressions": 3000, "page_views": 300,
         "items_sold": 30, "gmv": 900.75, "ctr_pct": 10.0, "cvr_pct": 10.0},
    ]
    funil = _chamar()["channel_funnel"]
    assert [f["channel"] for f in funil] == ["VIDEO", "LIVE", "PRODUCT_CARD"]
    assert [f["label"] for f in funil] == ["Video", "Live", "Card"]
    assert list(funil[0]) == ["channel", "label", "impressions", "page_views",
                              "items_sold", "gmv", "ctr_pct", "cvr_pct"]
    assert funil[0]["impressions"] == 1000 and isinstance(funil[0]["impressions"], int)
    assert funil[0]["ctr_pct"] == 10.0
    assert funil[0]["cvr_pct"] == 10.0


def test_b17_channel_desconhecido_passa_cru_como_rotulo(fixar):
    dados, _ = fixar
    dados["channel_funnel"] = [{"channel": "NOVO_CANAL", "impressions": 1, "page_views": 1,
                                "items_sold": 1, "gmv": 1.0, "ctr_pct": 1.0, "cvr_pct": 1.0}]
    assert _chamar()["channel_funnel"][0]["label"] == "NOVO_CANAL"


def test_b18_channel_funnel_zero_em_ctr_cvr_vira_none(fixar):
    dados, _ = fixar
    dados["channel_funnel"] = [{"channel": "VIDEO", "impressions": 0, "page_views": 0,
                                "items_sold": 0, "gmv": 0.0, "ctr_pct": 0, "cvr_pct": None}]
    f = _chamar()["channel_funnel"][0]
    assert f["ctr_pct"] is None and f["cvr_pct"] is None
    assert f["impressions"] == 0 and f["gmv"] == 0.0   # contadores mantem o zero


def test_b19_channel_funnel_ordenado_por_channel_e_taxas_calculadas_na_fonte(fixar):
    _, vistas = fixar
    _chamar()
    sql = _sql_de(vistas, "channel_funnel").lower()
    assert "order by channel" in sql
    assert "group by channel" in sql
    assert "impressions" in sql and "page_views" in sql and "items_sold" in sql


# ===========================================================================
# daily
# ===========================================================================

def test_b20_daily_campos_e_falsy_para_none(fixar):
    dados, _ = fixar
    dados["daily"] = [
        {"date": "2026-07-01", "gmv": 100.5, "gmv_video": 60.0, "gmv_live": 40.5,
         "gmv_card": 0, "new_videos_posted": 3},
        {"date": "2026-07-02", "gmv": 0, "gmv_video": 0, "gmv_live": 0,
         "gmv_card": 0, "new_videos_posted": 0},
    ]
    d = _chamar()["daily"]
    assert list(d[0]) == ["date", "gmv", "gmv_video", "gmv_live", "gmv_card", "new_videos_posted"]
    assert d[0] == {"date": "2026-07-01", "gmv": 100.5, "gmv_video": 60.0,
                    "gmv_live": 40.5, "gmv_card": None, "new_videos_posted": 3}
    assert d[1] == {"date": "2026-07-02", "gmv": None, "gmv_video": None,
                    "gmv_live": None, "gmv_card": None, "new_videos_posted": None}


def test_b21_daily_data_truncada_em_10_caracteres(fixar):
    dados, _ = fixar
    dados["daily"] = [{"date": "2026-07-05 00:00:00+00:00", "gmv": 1.0, "gmv_video": 1.0,
                       "gmv_live": 1.0, "gmv_card": 1.0, "new_videos_posted": 1}]
    assert _chamar()["daily"][0]["date"] == "2026-07-05"


def test_b22_daily_preserva_a_ordem_da_fonte(fixar):
    dados, vistas = fixar
    dados["daily"] = [
        {"date": "2026-07-01", "gmv": 1.0, "gmv_video": 1.0, "gmv_live": 1.0,
         "gmv_card": 1.0, "new_videos_posted": 1},
        {"date": "2026-07-02", "gmv": 2.0, "gmv_video": 1.0, "gmv_live": 1.0,
         "gmv_card": 1.0, "new_videos_posted": 1},
    ]
    assert [x["date"] for x in _chamar()["daily"]] == ["2026-07-01", "2026-07-02"]
    assert "order by date" in _sql_de(vistas, "daily").lower()


# ===========================================================================
# top_creators e top_produtos — LIMIT 5 cada
# ===========================================================================

def test_b23_top_creators_campos_e_tipos(fixar):
    dados, _ = fixar
    dados["creators"] = [{"creator": "belejapa3", "gmv": 7000.5, "videos": 12, "lives": 3}]
    c = _chamar()["top_creators"][0]
    assert list(c) == ["creator", "gmv", "videos", "lives"]
    assert c == {"creator": "belejapa3", "gmv": 7000.5, "videos": 12, "lives": 3}
    assert isinstance(c["videos"], int) and isinstance(c["lives"], int)


@pytest.mark.parametrize("valor", [None, ""])
def test_b24_creator_vazio_vira_travessao(fixar, valor):
    dados, _ = fixar
    dados["creators"] = [{"creator": valor, "gmv": 1.0, "videos": 0, "lives": 0}]
    assert _chamar()["top_creators"][0]["creator"] == "—"


def test_b25_top_creators_limit_5_e_ordem_por_gmv(fixar):
    _, vistas = fixar
    _chamar()
    sql = _sql_de(vistas, "creators").lower()
    assert "limit 5" in sql
    assert "group by creator" in sql
    assert "order by sum(gmv_total) desc" in sql


def test_b26_top_produtos_campos_e_tipos(fixar):
    dados, _ = fixar
    dados["products"] = [{"product_id": 123456, "product_name": "Kit Z",
                          "gmv": 9000.25, "orders": 300, "videos": 40, "gpm": 15.005}]
    p = _chamar()["top_produtos"][0]
    assert list(p) == ["product_id", "product_name", "gmv", "orders", "videos", "gpm"]
    assert p["product_id"] == "123456" and isinstance(p["product_id"], str)
    assert p["gmv"] == 9000.25
    assert p["orders"] == 300 and isinstance(p["orders"], int)
    assert p["videos"] == 40
    assert p["gpm"] == 15.01   # 2 casas (default de _r)


@pytest.mark.parametrize("campo,valor,esperado", [
    ("product_id", None, ""), ("product_id", "", ""),
    ("product_name", None, "—"), ("product_name", "", "—"),
])
def test_b27_produto_com_identificacao_vazia(fixar, campo, valor, esperado):
    dados, _ = fixar
    linha = {"product_id": 1, "product_name": "x", "gmv": 1.0,
             "orders": 1, "videos": 1, "gpm": 1.0}
    linha[campo] = valor
    dados["products"] = [linha]
    assert _chamar()["top_produtos"][0][campo] == esperado


def test_b28_top_produtos_limit_5_agrupamento_e_gpm_na_fonte(fixar):
    _, vistas = fixar
    _chamar()
    sql = _sql_de(vistas, "products").lower()
    assert "limit 5" in sql
    assert "group by product_id, product_name" in sql
    assert "order by sum(gmv) desc" in sql
    assert "video_views" in sql        # GPM = gmv / video_views * 1000
    assert "active_videos" in sql


def test_b29_gpm_zero_vira_none(fixar):
    dados, _ = fixar
    dados["products"] = [{"product_id": 1, "product_name": "x", "gmv": 0.0,
                          "orders": 0, "videos": 0, "gpm": 0}]
    assert _chamar()["top_produtos"][0]["gpm"] is None


# ===========================================================================
# Router: 404 e 422
# ===========================================================================

def test_b30_brand_invalida_responde_404(monkeypatch):
    monkeypatch.setattr(rp.svc, "get_brand_detail", lambda *a, **k: {})
    with pytest.raises(HTTPException) as e:
        rp.brand_detail(brand="inexistente", ref_month="2026-07", channels=None, db=object())
    assert e.value.status_code == 404


@pytest.mark.parametrize("marca", sorted(rp.VALID_TK_BRANDS))
def test_b31_as_cinco_marcas_oficiais_sao_aceitas(monkeypatch, marca):
    monkeypatch.setattr(rp.svc, "get_brand_detail", lambda *a, **k: {"brand": marca})
    assert rp.brand_detail(brand=marca, ref_month="2026-07",
                           channels=None, db=object())["brand"] == marca


@pytest.mark.parametrize("canais", ["ml", "shopee", "ml,shopee"])
def test_b32_selecao_sem_tiktok_responde_422(monkeypatch, canais):
    monkeypatch.setattr(rp.svc, "get_brand_detail", lambda *a, **k: {})
    with pytest.raises(HTTPException) as e:
        rp.brand_detail(brand=MARCA, ref_month="2026-07", channels=canais, db=object())
    assert e.value.status_code == 422
    assert "tiktok" in str(e.value.detail).lower()


@pytest.mark.parametrize("canais", [None, "all", "tiktok"])
def test_b33_selecao_que_inclui_tiktok_e_aceita(monkeypatch, canais):
    monkeypatch.setattr(rp.svc, "get_brand_detail", lambda *a, **k: {"ok": True})
    assert rp.brand_detail(brand=MARCA, ref_month="2026-07",
                           channels=canais, db=object()) == {"ok": True}


def test_b34_ref_month_malformado_responde_422(monkeypatch):
    monkeypatch.setattr(rp.svc, "get_brand_detail", lambda *a, **k: {})
    with pytest.raises(HTTPException) as e:
        rp.brand_detail(brand=MARCA, ref_month="julho", channels=None, db=object())
    assert e.value.status_code == 422


def test_b35_banco_indisponivel_responde_503(monkeypatch):
    monkeypatch.setattr(rp.svc, "get_brand_detail", lambda *a, **k: {})
    with pytest.raises(HTTPException) as e:
        rp.brand_detail(brand=MARCA, ref_month="2026-07", channels=None, db=None)
    assert e.value.status_code == 503


def test_b36_response_model_permanece_o_atual():
    """O S3 nao cria nem troca `response_model`. Congelado."""
    rota = next(r for r in rp.router.routes
                if getattr(r, "path", "") == "/api/v1/performance/brand-detail")
    from app.schemas.performance import BrandDetailResponse
    assert rota.response_model is BrandDetailResponse


# ===========================================================================
# Ausencia de campo novo
# ===========================================================================

def test_b37_nenhum_campo_novo_ou_removido_com_fonte_completa(fixar):
    dados, _ = fixar
    dados["monthly"] = [dict(LINHA_MENSAL)]
    dados["daily"] = [{"date": "2026-07-01", "gmv": 1.0, "gmv_video": 1.0,
                       "gmv_live": 1.0, "gmv_card": 1.0, "new_videos_posted": 1}]
    dados["creators"] = [{"creator": "c", "gmv": 1.0, "videos": 1, "lives": 1}]
    dados["products"] = [{"product_id": 1, "product_name": "p", "gmv": 1.0,
                          "orders": 1, "videos": 1, "gpm": 1.0}]
    dados["channel_funnel"] = [{"channel": "VIDEO", "impressions": 1, "page_views": 1,
                                "items_sold": 1, "gmv": 1.0, "ctr_pct": 1.0, "cvr_pct": 1.0}]
    dados["available_months"] = [{"mes": "2026-07"}, {"mes": "2026-06"}]
    out = _chamar()
    assert list(out) == CAMPOS_TOP_LEVEL
    assert list(out["channel_funnel"][0]) == ["channel", "label", "impressions",
                                              "page_views", "items_sold", "gmv",
                                              "ctr_pct", "cvr_pct"]
    assert list(out["daily"][0]) == ["date", "gmv", "gmv_video", "gmv_live",
                                     "gmv_card", "new_videos_posted"]
    assert list(out["top_creators"][0]) == ["creator", "gmv", "videos", "lives"]
    assert list(out["top_produtos"][0]) == ["product_id", "product_name", "gmv",
                                            "orders", "videos", "gpm"]


def test_b38_nenhuma_consulta_menciona_shopee_ou_frete(fixar):
    _, vistas = fixar
    _chamar()
    for tipo, sql in vistas:
        baixo = sql.lower()
        assert "shopee" not in baixo, tipo
        assert "shipping" not in baixo and "frete" not in baixo, tipo
