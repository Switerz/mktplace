"""
Testes da matriz comparativa marca x canal (`channel_rows`/`channel_medians`)
adicionada a get_canais() no Gate 2 (docs/sections/canais_audit.md, secao 14).

Cobre: N/A (TikTok ads/frete) vs Sem dado (ML custo marketplace) vs valor
real; denominador zero/nulo nunca vira 0%; sinal de sinal `sem_dado` so para
metrica aplicavel-porem-ausente; sinais `roas_forte`/`ads_subutilizado`/
`custo_alto`/`frete_alto` calculados por mediana/percentil do canal (so com
>=2 marcas); nenhum campo de desconto/afiliados no payload.
"""
from app.schemas.performance import CanaisResponse
from app.services import performance_service as perf_svc


class _FakeMappingsResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class FakeMappingSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.captured_params = []

    def execute(self, stmt, params=None):
        self.captured_params.append(params)
        rows = self._responses.pop(0)
        return _FakeMappingsResult(rows)


def _row(
    brand_key, marketplace_id, *, gmv=0, orders=0,
    ad_spend=0, ad_revenue=0, ad_spend_n=0,
    total_fees=0, total_fees_n=0,
    seller_shipping_cost=0, seller_shipping_cost_n=0,
    unique_buyers=0, new_buyers=0, repeat_buyers=0,
    visitors=0, canceled_orders=0, avg_conversion_rate=None,
    gmv_video=0, gmv_live=0, gmv_card=0,
):
    return {
        "brand_key": brand_key, "marketplace_id": marketplace_id,
        "gmv": gmv, "gmv_video": gmv_video, "gmv_live": gmv_live, "gmv_card": gmv_card,
        "visitors": visitors, "unique_buyers": unique_buyers, "new_buyers": new_buyers,
        "repeat_buyers": repeat_buyers, "canceled_orders": canceled_orders, "orders": orders,
        "avg_conversion_rate": avg_conversion_rate,
        "ad_spend": ad_spend, "ad_revenue": ad_revenue, "ad_spend_n": ad_spend_n,
        "total_fees": total_fees, "total_fees_n": total_fees_n,
        "seller_shipping_cost": seller_shipping_cost, "seller_shipping_cost_n": seller_shipping_cost_n,
    }


def _by_channel(rows, channel):
    return [r for r in rows if r["channel"] == channel]


def _row_for(rows, brand, channel):
    return next(r for r in rows if r["brand"] == brand and r["channel"] == channel)


# ---------------------------------------------------------------------------
# TikTok: ads/frete N/A (nao aplicavel) — nunca "sem dado"
# ---------------------------------------------------------------------------

def test_tiktok_ads_e_frete_sao_nao_aplicaveis_nao_sem_dado():
    rows = [_row("barbours", perf_svc.TIKTOK_ID, gmv=1000, total_fees=-300, total_fees_n=30)]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "tiktok", 2026, 5)

    tk = _row_for(result["channel_rows"], "barbours", "tiktok")
    assert tk["ads_applicable"] is False
    assert tk["ads_available"] is False
    assert tk["seller_shipping_applicable"] is False
    assert tk["ads_gmv_pct"] is None
    assert tk["roas"] is None
    assert "sem_dado" not in tk["signals"]  # N/A nao e a mesma coisa que Sem dado


def test_tiktok_fees_negativo_vira_abs_e_carrega_aviso_de_base():
    rows = [_row("barbours", perf_svc.TIKTOK_ID, gmv=1000, total_fees=-300, total_fees_n=30)]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "tiktok", 2026, 5)

    tk = _row_for(result["channel_rows"], "barbours", "tiktok")
    assert tk["marketplace_cost_available"] is True
    assert tk["marketplace_cost_pct"] == 30.0  # abs(300)/1000*100
    assert tk["data_warning"] is not None  # base settlement != GMV comercial


# ---------------------------------------------------------------------------
# ML: custo marketplace aplicavel porem sem dado (total_fees sempre NULL)
# ---------------------------------------------------------------------------

def test_ml_custo_marketplace_e_sem_dado_nao_inventado():
    rows = [_row("barbours", perf_svc.ML_ID, gmv=2000, orders=10,
                 ad_spend=100, ad_revenue=1200, ad_spend_n=30,
                 total_fees=0, total_fees_n=0,  # NULL no mart -> COUNT=0
                 seller_shipping_cost=200, seller_shipping_cost_n=30)]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "ml", 2026, 5)

    ml = _row_for(result["channel_rows"], "barbours", "ml")
    assert ml["marketplace_cost_applicable"] is True
    assert ml["marketplace_cost_available"] is False
    assert ml["marketplace_cost_pct"] is None  # nunca inventado como 0%
    assert "sem_dado" in ml["signals"]
    assert ml["data_warning"] is not None

    # Ads e frete SAO confiaveis e disponiveis para ML
    assert ml["ads_available"] is True
    assert ml["roas"] == 12.0  # 1200/100
    assert ml["seller_shipping_available"] is True
    assert ml["seller_shipping_pct"] == 10.0  # 200/2000*100


# ---------------------------------------------------------------------------
# Shopee: custo marketplace disponivel quando total_fees_n > 0
# ---------------------------------------------------------------------------

def test_shopee_custo_marketplace_aparece_quando_total_fees_existe():
    rows = [_row("kokeshi", perf_svc.SHOPEE_ID, gmv=3000, orders=15,
                 ad_spend=150, ad_revenue=2100, ad_spend_n=30,
                 total_fees=750, total_fees_n=30,  # positivo na fonte (sem abs)
                 seller_shipping_cost=90, seller_shipping_cost_n=30)]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "shopee", 2026, 5)

    sh = _row_for(result["channel_rows"], "kokeshi", "shopee")
    assert sh["marketplace_cost_available"] is True
    assert sh["marketplace_cost_pct"] == 25.0  # 750/3000*100, sem abs()
    assert sh["data_warning"] is None  # Shopee nao tem a ressalva de base do TikTok


# ---------------------------------------------------------------------------
# Denominador zero/nulo -> None, nunca 0%
# ---------------------------------------------------------------------------

def test_denominador_zero_vira_none_nunca_zero_pct():
    rows = [_row("lescent", perf_svc.ML_ID, gmv=0, orders=0,
                 ad_spend=50, ad_revenue=0, ad_spend_n=10,
                 seller_shipping_cost=20, seller_shipping_cost_n=10)]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "ml", 2026, 5)

    ml = _row_for(result["channel_rows"], "lescent", "ml")
    assert ml["ads_gmv_pct"] is None  # denominador (gmv) = 0
    assert ml["seller_shipping_pct"] is None  # idem
    assert ml["roas"] == 0.0  # ad_spend=50 (denominador > 0) e ad_revenue=0 -> ROAS zero é real, nao ausencia
    assert ml["acos_pct"] is None  # ACOS = ad_spend/ad_revenue -> denominador (ad_revenue) = 0


def test_roas_none_quando_ad_spend_zero_mas_ads_disponivel():
    # ad_spend_n > 0 (canal populado), mas soma do periodo = 0 (marca nao gastou)
    rows = [_row("lescent", perf_svc.ML_ID, gmv=1000, ad_spend=0, ad_revenue=0, ad_spend_n=10)]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "ml", 2026, 5)

    ml = _row_for(result["channel_rows"], "lescent", "ml")
    assert ml["ads_available"] is True  # tem dado, so que zero
    assert ml["ads_gmv_pct"] == 0.0  # zero real (gastou 0 de fato) - nao e ausencia
    assert ml["roas"] is None  # divisao por ad_spend=0 -> None, nunca 0 ou infinito


# ---------------------------------------------------------------------------
# Sinais de oportunidade — mediana/percentil do canal (so com >=2 marcas)
# ---------------------------------------------------------------------------

def test_sinais_precisam_de_pelo_menos_duas_marcas_no_canal():
    # total_fees_n/seller_shipping_cost_n > 0 para isolar o teste dos sinais
    # de comparacao — sem eles, o "sem_dado" de custo ML (Bug conhecido,
    # sempre ausente no mart) apareceria mesmo sem qualquer comparacao.
    rows = [_row("barbours", perf_svc.ML_ID, gmv=1000, ad_spend=100, ad_revenue=1500, ad_spend_n=10,
                 total_fees=200, total_fees_n=10, seller_shipping_cost=50, seller_shipping_cost_n=10)]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "ml", 2026, 5)

    ml = _row_for(result["channel_rows"], "barbours", "ml")
    assert ml["signals"] == []  # nao ha mediana com 1 marca so — nao compara contra si mesma
    medians = next(m for m in result["channel_medians"] if m["channel"] == "ml")
    assert medians["roas_median"] is None
    assert medians["brands_with_data"] == 1


def test_roas_forte_e_ads_subutilizado_calculados_por_mediana_do_canal():
    rows = [
        # barbours: GMV alto, ads/gmv baixo, ROAS alto -> ads_subutilizado + roas_forte
        _row("barbours", perf_svc.ML_ID, gmv=10_000, ad_spend=100, ad_revenue=1600, ad_spend_n=30),
        # kokeshi: GMV baixo, ads/gmv alto, ROAS baixo -> nenhum dos dois sinais
        _row("kokeshi", perf_svc.ML_ID, gmv=1_000, ad_spend=100, ad_revenue=300, ad_spend_n=30),
    ]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "ml", 2026, 5)

    barbours = _row_for(result["channel_rows"], "barbours", "ml")
    kokeshi = _row_for(result["channel_rows"], "kokeshi", "ml")
    assert "roas_forte" in barbours["signals"]
    assert "ads_subutilizado" in barbours["signals"]
    assert "roas_forte" not in kokeshi["signals"]
    assert "ads_subutilizado" not in kokeshi["signals"]


def test_custo_alto_usa_percentil_75_do_canal():
    rows = [
        _row("apice", perf_svc.SHOPEE_ID, gmv=1000, total_fees=100, total_fees_n=10),   # 10%
        _row("barbours", perf_svc.SHOPEE_ID, gmv=1000, total_fees=200, total_fees_n=10),  # 20%
        _row("kokeshi", perf_svc.SHOPEE_ID, gmv=1000, total_fees=400, total_fees_n=10),   # 40% -> outlier alto
    ]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "shopee", 2026, 5)

    kokeshi = _row_for(result["channel_rows"], "kokeshi", "shopee")
    apice = _row_for(result["channel_rows"], "apice", "shopee")
    assert "custo_alto" in kokeshi["signals"]
    assert "custo_alto" not in apice["signals"]


# ---------------------------------------------------------------------------
# Ordenacao por GMV desc e contrato de resposta (schema)
# ---------------------------------------------------------------------------

def test_channel_rows_ordenados_por_gmv_desc():
    rows = [
        _row("apice", perf_svc.SHOPEE_ID, gmv=500),
        _row("barbours", perf_svc.SHOPEE_ID, gmv=5000),
        _row("kokeshi", perf_svc.SHOPEE_ID, gmv=2000),
    ]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "shopee", 2026, 5)

    gmvs = [r["gmv"] for r in result["channel_rows"]]
    assert gmvs == sorted(gmvs, reverse=True)


def test_resposta_valida_contra_schema_canais_response():
    rows = [
        _row("barbours", perf_svc.TIKTOK_ID, gmv=1000, total_fees=-100, total_fees_n=10),
        _row("barbours", perf_svc.ML_ID, gmv=2000, ad_spend=50, ad_revenue=600, ad_spend_n=10,
             seller_shipping_cost=80, seller_shipping_cost_n=10),
        _row("barbours", perf_svc.SHOPEE_ID, gmv=1500, ad_spend=40, ad_revenue=500, ad_spend_n=10,
             total_fees=300, total_fees_n=10, seller_shipping_cost=60, seller_shipping_cost_n=10),
    ]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "all", 2026, 5)

    validated = CanaisResponse.model_validate(result)
    assert len(validated.channel_rows) == 3
    assert len(validated.channel_medians) == 3


def test_nenhum_campo_de_desconto_ou_afiliado_no_payload():
    rows = [_row("barbours", perf_svc.SHOPEE_ID, gmv=1000, total_fees=100, total_fees_n=10)]
    db = FakeMappingSession([rows])
    result = perf_svc.get_canais(db, "shopee", 2026, 5)

    for row in result["channel_rows"]:
        for key in row:
            assert "discount" not in key.lower()
            assert "desconto" not in key.lower()
            assert "afiliad" not in key.lower()
            assert "affiliate" not in key.lower()
