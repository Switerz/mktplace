"""
UE-F1A — contrato do mix de conteudo do TikTok em `get_canais`.

O que estes testes travam: `tiktok_video_pct`/`live_pct`/`card_pct` se fecham
sobre a PROPRIA base de conteudo (`gmv_video + gmv_live + gmv_card`), nunca
sobre `tiktok_gmv` (o GMV comercial canonico).

Por que isso importa (Gate UE0, docs/UNIT_ECONOMICS_ATTRIBUTION_AUDIT.md secao
4.2): a quebra video/live/card e' passthrough da `gold.tiktok_brand_daily`, que
a calcula sobre o valor antigo (~`total_amount`), enquanto `gmv` no mart e'
`SUM(sub_total)` da Raw com allowlist de status. Em jan-jun/2026 a soma dos tres
componentes deu R$ 70.414.835,49 contra um GMV canonico de R$ 65.898.900,23 —
os tres percentuais somavam **106,85%** e a barra de atribuicao afirmava uma
particao que nao existe.

As categorias particionam exatamente a base de conteudo. O defeito era de
denominador, nao de dado: nada e' reconciliado, rateado nem corrigido na origem.

Cobre tambem os dois campos diagnosticos aditivos
(`tiktok_content_gmv_base`, `tiktok_content_gmv_divergence_pct`) e a garantia de
que nada fora de `/canais` mudou.
"""
import inspect

from app.schemas.performance import CanaisBrandRow, CanaisKpis, CanaisResponse
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


def _brand(result, brand):
    return next(r for r in result["brands"] if r["brand"] == brand)


def _canais(rows, marketplace="tiktok"):
    return perf_svc.get_canais(FakeMappingSession([rows]), marketplace, 2026, 5)


# ---------------------------------------------------------------------------
# Caso canonico do contrato: base > GMV comercial
# ---------------------------------------------------------------------------

def test_mix_fecha_100_sobre_a_base_de_conteudo_nao_sobre_o_gmv_comercial():
    """60+30+20 de conteudo com GMV comercial 100: base 110, mix sobre 110."""
    rows = [_row("barbours", perf_svc.TIKTOK_ID, gmv=100,
                 gmv_video=60, gmv_live=30, gmv_card=20)]
    b = _brand(_canais(rows), "barbours")

    assert b["tiktok_content_gmv_base"] == 110.0
    assert b["tiktok_gmv"] == 100.0  # GMV comercial preservado, intocado

    # percentuais sobre 110, nao sobre 100
    assert b["tiktok_video_pct"] == 54.5   # 60/110
    assert b["tiktok_live_pct"] == 27.3    # 30/110
    assert b["tiktok_card_pct"] == 18.2    # 20/110

    # fecham em 100% (dentro do arredondamento de 1 decimal)
    soma = b["tiktok_video_pct"] + b["tiktok_live_pct"] + b["tiktok_card_pct"]
    assert abs(soma - 100.0) <= 0.1

    # o denominador antigo daria 60/30/20 e somaria 110% — nunca mais
    assert b["tiktok_video_pct"] != 60.0
    assert soma <= 100.1

    # divergencia de linhagem: (110-100)/100 = +10%
    assert b["tiktok_content_gmv_divergence_pct"] == 10.0


def test_componentes_totalizando_menos_que_o_gmv_comercial():
    """Base menor que o GMV comercial: mix ainda fecha 100%, divergencia negativa."""
    rows = [_row("kokeshi", perf_svc.TIKTOK_ID, gmv=200,
                 gmv_video=50, gmv_live=30, gmv_card=20)]
    b = _brand(_canais(rows), "kokeshi")

    assert b["tiktok_content_gmv_base"] == 100.0
    assert b["tiktok_video_pct"] == 50.0
    assert b["tiktok_live_pct"] == 30.0
    assert b["tiktok_card_pct"] == 20.0
    assert b["tiktok_video_pct"] + b["tiktok_live_pct"] + b["tiktok_card_pct"] == 100.0

    # (100-200)/200 = -50%
    assert b["tiktok_content_gmv_divergence_pct"] == -50.0


def test_base_igual_ao_gmv_comercial_tem_divergencia_zero():
    """Divergencia 0% e' um resultado legitimo: as duas linhagens batem."""
    rows = [_row("lescent", perf_svc.TIKTOK_ID, gmv=100,
                 gmv_video=50, gmv_live=30, gmv_card=20)]
    b = _brand(_canais(rows), "lescent")

    assert b["tiktok_content_gmv_base"] == 100.0
    assert b["tiktok_content_gmv_divergence_pct"] == 0.0  # zero medido, nao ausencia


# ---------------------------------------------------------------------------
# Semantica de indisponibilidade: nunca fabricar 0%
# ---------------------------------------------------------------------------

def test_base_zero_zera_nada_e_devolve_none():
    """Sem base de conteudo nao ha mix: None, nunca 0%."""
    rows = [_row("rituaria", perf_svc.TIKTOK_ID, gmv=5000,
                 gmv_video=0, gmv_live=0, gmv_card=0)]
    b = _brand(_canais(rows), "rituaria")

    assert b["tiktok_video_pct"] is None
    assert b["tiktok_live_pct"] is None
    assert b["tiktok_card_pct"] is None
    assert b["tiktok_content_gmv_base"] is None
    assert b["tiktok_content_gmv_divergence_pct"] is None
    # o GMV comercial continua existindo e nao e' afetado
    assert b["tiktok_gmv"] == 5000.0


def test_gmv_comercial_zero_com_base_positiva_mantem_o_mix_e_anula_a_divergencia():
    """O mix nao depende do GMV comercial; a divergencia sim (falta denominador)."""
    rows = [_row("apice", perf_svc.TIKTOK_ID, gmv=0,
                 gmv_video=60, gmv_live=30, gmv_card=10)]
    b = _brand(_canais(rows), "apice")

    assert b["tiktok_content_gmv_base"] == 100.0
    assert b["tiktok_video_pct"] == 60.0
    assert b["tiktok_live_pct"] == 30.0
    assert b["tiktok_card_pct"] == 10.0
    # sem GMV comercial nao ha o que reconciliar — None, nunca 0%
    assert b["tiktok_content_gmv_divergence_pct"] is None


def test_componente_individual_zero_nao_invalida_os_outros():
    """Card ausente nao contamina video/live: contrato de componente preservado."""
    rows = [_row("barbours", perf_svc.TIKTOK_ID, gmv=100,
                 gmv_video=75, gmv_live=25, gmv_card=0)]
    b = _brand(_canais(rows), "barbours")

    assert b["tiktok_content_gmv_base"] == 100.0
    assert b["tiktok_video_pct"] == 75.0
    assert b["tiktok_live_pct"] == 25.0
    # componente zero segue None (contrato existente `or None`), mas o pct dele
    # e' 0.0 medido — nao None, porque a base existe
    assert b["tiktok_gmv_card"] is None
    assert b["tiktok_card_pct"] == 0.0
    assert b["tiktok_video_pct"] + b["tiktok_live_pct"] + b["tiktok_card_pct"] == 100.0


# ---------------------------------------------------------------------------
# Total/KPIs: soma de componentes, nunca media de percentuais
# ---------------------------------------------------------------------------

def test_total_soma_componentes_em_vez_de_mediar_percentuais_das_marcas():
    """Duas marcas de tamanhos muito diferentes separam as duas contas.

    marca A: video 90 de base 100 -> 90,0%
    marca B: video  1 de base  10 -> 10,0%
    media simples dos percentuais = 50,0%  (ERRADO)
    soma dos componentes = 91/110 = 82,7%  (CORRETO)
    """
    rows = [
        _row("barbours", perf_svc.TIKTOK_ID, gmv=100, gmv_video=90, gmv_live=10, gmv_card=0),
        _row("lescent", perf_svc.TIKTOK_ID, gmv=10, gmv_video=1, gmv_live=9, gmv_card=0),
    ]
    result = _canais(rows)

    a = _brand(result, "barbours")
    b = _brand(result, "lescent")
    assert a["tiktok_video_pct"] == 90.0
    assert b["tiktok_video_pct"] == 10.0

    k = result["kpis"]
    assert k["tiktok_content_gmv_base"] == 110.0
    assert k["tiktok_video_pct"] == 82.7   # 91/110, nao (90+10)/2
    assert k["tiktok_video_pct"] != 50.0
    assert k["tiktok_live_pct"] == 17.3    # 19/110
    assert k["tiktok_card_pct"] == 0.0

    soma = k["tiktok_video_pct"] + k["tiktok_live_pct"] + k["tiktok_card_pct"]
    assert abs(soma - 100.0) <= 0.1

    # divergencia do total tambem sai das somas: (110-110)/110 = 0%
    assert k["tiktok_content_gmv_divergence_pct"] == 0.0


def test_total_divergencia_sai_das_somas_nao_da_media_das_marcas():
    rows = [
        _row("barbours", perf_svc.TIKTOK_ID, gmv=100, gmv_video=60, gmv_live=30, gmv_card=20),
        _row("kokeshi", perf_svc.TIKTOK_ID, gmv=100, gmv_video=20, gmv_live=10, gmv_card=10),
    ]
    k = _canais(rows)["kpis"]

    # base total 110+40 = 150; GMV total 200 -> (150-200)/200 = -25%
    assert k["tiktok_content_gmv_base"] == 150.0
    assert k["tiktok_gmv"] == 200.0
    assert k["tiktok_content_gmv_divergence_pct"] == -25.0


# ---------------------------------------------------------------------------
# Nao fabricar base onde nao ha TikTok
# ---------------------------------------------------------------------------

def test_canal_sem_tiktok_nao_fabrica_base_nem_divergencia():
    rows = [
        _row("barbours", perf_svc.ML_ID, gmv=2000, unique_buyers=100, new_buyers=40,
             repeat_buyers=60, ad_spend=50, ad_revenue=600, ad_spend_n=10),
        _row("barbours", perf_svc.SHOPEE_ID, gmv=1500, unique_buyers=80, visitors=1000,
             orders=90, canceled_orders=10, ad_spend=40, ad_revenue=500, ad_spend_n=10),
    ]
    result = _canais(rows, marketplace="all")

    b = _brand(result, "barbours")
    # a marca nao tem bloco TikTok: as chaves nao existem no dict do servico
    assert "tiktok_content_gmv_base" not in b
    assert "tiktok_video_pct" not in b

    k = result["kpis"]
    assert k["tiktok_content_gmv_base"] is None
    assert k["tiktok_content_gmv_divergence_pct"] is None
    assert k["tiktok_video_pct"] is None
    assert k["tiktok_live_pct"] is None
    assert k["tiktok_card_pct"] is None

    # e o schema preenche os campos ausentes com None, sem 0%
    validated = CanaisResponse.model_validate(result)
    vb = next(r for r in validated.brands if r.brand == "barbours")
    assert vb.tiktok_content_gmv_base is None
    assert vb.tiktok_content_gmv_divergence_pct is None
    assert vb.tiktok_video_pct is None


# ---------------------------------------------------------------------------
# Nao-regressao: ML e Shopee intactos
# ---------------------------------------------------------------------------

def test_campos_de_ml_e_shopee_permanecem_identicos():
    rows = [
        _row("barbours", perf_svc.TIKTOK_ID, gmv=100, gmv_video=60, gmv_live=30, gmv_card=20),
        _row("barbours", perf_svc.ML_ID, gmv=2000, unique_buyers=100, new_buyers=40,
             repeat_buyers=60),
        _row("barbours", perf_svc.SHOPEE_ID, gmv=1500, unique_buyers=80, new_buyers=30,
             repeat_buyers=50, visitors=1000, orders=90, canceled_orders=10),
    ]
    result = _canais(rows, marketplace="all")
    b = _brand(result, "barbours")

    # ML: inalterado
    assert b["ml_gmv"] == 2000.0
    assert b["ml_unique_buyers"] == 100
    assert b["ml_repeat_buyer_rate_pct"] == 60.0
    assert b["ml_gmv_per_buyer"] == 20.0

    # Shopee: inalterado
    assert b["shopee_gmv"] == 1500.0
    assert b["shopee_unique_buyers"] == 80
    assert b["shopee_new_buyer_pct"] == 37.5
    assert b["shopee_gmv_per_buyer"] == 18.75
    assert b["shopee_cancel_rate_pct"] == 10.0   # 10/(90+10)
    assert b["shopee_conversion_rate"] == 8.0    # 80/1000

    # nenhum campo de conteudo vazou para ML/Shopee
    for key in b:
        if key.startswith(("ml_", "shopee_")):
            assert "content_gmv" not in key


def test_nenhum_campo_existente_foi_removido_ou_renomeado():
    """Os dois campos novos sao ADITIVOS: o contrato anterior segue inteiro."""
    antigos_kpis = {
        "tiktok_gmv", "tiktok_gmv_video", "tiktok_gmv_live", "tiktok_gmv_card",
        "tiktok_video_pct", "tiktok_live_pct", "tiktok_card_pct",
        "tiktok_visitors", "tiktok_customers", "tiktok_conversion_rate",
        "ml_unique_buyers", "ml_new_buyers", "ml_repeat_buyers", "ml_new_buyer_pct",
        "ml_repeat_buyer_rate_pct", "ml_gmv_per_buyer",
        "shopee_gmv", "shopee_unique_buyers", "shopee_new_buyers", "shopee_repeat_buyers",
        "shopee_new_buyer_pct", "shopee_repeat_buyer_rate_pct", "shopee_gmv_per_buyer",
        "shopee_visitors", "shopee_conversion_rate",
    }
    assert antigos_kpis <= set(CanaisKpis.model_fields)
    novos = set(CanaisKpis.model_fields) - antigos_kpis
    assert novos == {"tiktok_content_gmv_base", "tiktok_content_gmv_divergence_pct"}

    novos_row = set(CanaisBrandRow.model_fields) - {
        "brand", "label", "tiktok_gmv", "tiktok_gmv_video", "tiktok_gmv_live",
        "tiktok_gmv_card", "tiktok_video_pct", "tiktok_live_pct", "tiktok_card_pct",
        "tiktok_visitors", "tiktok_customers", "tiktok_conversion_rate",
        "ml_gmv", "ml_unique_buyers", "ml_new_buyers", "ml_repeat_buyers",
        "ml_repeat_buyer_rate_pct", "ml_gmv_per_buyer",
        "shopee_gmv", "shopee_unique_buyers", "shopee_new_buyers", "shopee_repeat_buyers",
        "shopee_new_buyer_pct", "shopee_repeat_buyer_rate_pct", "shopee_gmv_per_buyer",
        "shopee_cancel_rate_pct", "shopee_visitors", "shopee_conversion_rate",
    }
    assert novos_row == {"tiktok_content_gmv_base", "tiktok_content_gmv_divergence_pct"}


def test_response_model_preserva_e_serializa_os_dois_campos_aditivos():
    rows = [_row("barbours", perf_svc.TIKTOK_ID, gmv=100,
                 gmv_video=60, gmv_live=30, gmv_card=20)]
    result = _canais(rows)

    validated = CanaisResponse.model_validate(result)
    assert validated.kpis.tiktok_content_gmv_base == 110.0
    assert validated.kpis.tiktok_content_gmv_divergence_pct == 10.0

    vb = next(r for r in validated.brands if r.brand == "barbours")
    assert vb.tiktok_content_gmv_base == 110.0
    assert vb.tiktok_content_gmv_divergence_pct == 10.0
    assert vb.tiktok_video_pct == 54.5

    dumped = validated.model_dump()
    assert dumped["kpis"]["tiktok_content_gmv_base"] == 110.0
    assert dumped["kpis"]["tiktok_content_gmv_divergence_pct"] == 10.0
    assert dumped["brands"][0]["tiktok_content_gmv_base"] == 110.0
    assert dumped["brands"][0]["tiktok_content_gmv_divergence_pct"] == 10.0


# ---------------------------------------------------------------------------
# Contraprova estrutural: tk_gmv nao e' mais denominador do mix
# ---------------------------------------------------------------------------

def test_mix_e_invariante_ao_gmv_comercial():
    """A prova comportamental: mesmos componentes, GMV comercial diferente ->
    percentuais IDENTICOS. Se `tk_gmv` ainda fosse o denominador, mudariam."""
    comp = {"gmv_video": 60, "gmv_live": 30, "gmv_card": 20}
    b1 = _brand(_canais([_row("barbours", perf_svc.TIKTOK_ID, gmv=100, **comp)]), "barbours")
    b2 = _brand(_canais([_row("barbours", perf_svc.TIKTOK_ID, gmv=99999, **comp)]), "barbours")

    for campo in ("tiktok_video_pct", "tiktok_live_pct", "tiktok_card_pct",
                  "tiktok_content_gmv_base"):
        assert b1[campo] == b2[campo], campo

    # so a divergencia reage ao GMV comercial — e' esse o papel dela
    assert b1["tiktok_content_gmv_divergence_pct"] != b2["tiktok_content_gmv_divergence_pct"]


def test_get_canais_nao_usa_mais_o_gmv_comercial_como_denominador_do_mix():
    """Contraprova de codigo-fonte: os padroes antigos nao existem mais."""
    src = inspect.getsource(perf_svc.get_canais)

    for antigo in (
        "_pct(tk_vid, tk_gmv)", "_pct(tk_live, tk_gmv)", "_pct(tk_card, tk_gmv)",
        "_pct(tk_vid_t, tk_gmv_t)", "_pct(tk_live_t, tk_gmv_t)", "_pct(tk_card_t, tk_gmv_t)",
    ):
        assert antigo not in src, f"denominador antigo reintroduzido: {antigo}"

    # e os novos denominadores estao no lugar
    assert "_pct(tk_vid, tk_content_base)" in src
    assert "_pct(tk_live, tk_content_base)" in src
    assert "_pct(tk_card, tk_content_base)" in src
    assert "_pct(tk_vid_t, tk_content_base_t)" in src
    assert "_pct(tk_live_t, tk_content_base_t)" in src
    assert "_pct(tk_card_t, tk_content_base_t)" in src


def test_divergencia_nao_tem_constante_hardcoded():
    """6,85% foi a medicao de jan-jun/2026, nao uma constante do contrato.

    O invariante real e' "nenhum literal numerico de divergencia no modulo",
    nao "a string nao aparece": citar a medicao em comentario/docstring e'
    documentacao legitima e desejavel. Por isso o teste tokeniza o fonte e
    olha so os tokens NUMBER, ignorando COMMENT e STRING.
    """
    import io
    import tokenize

    src = inspect.getsource(perf_svc)
    proibidos = {"6.85", "1.0685", "106.85", "0.0685"}
    literais = [
        (tok.start[0], tok.string)
        for tok in tokenize.generate_tokens(io.StringIO(src).readline)
        if tok.type == tokenize.NUMBER and tok.string in proibidos
    ]
    assert not literais, f"constante de divergencia hardcoded: {literais}"

    # e nenhuma tolerancia/ajuste para forcar 100% contra o GMV comercial
    corpo = inspect.getsource(perf_svc._tk_content_divergence).lower()
    for termo in ("tolerance", "tolerancia", "clamp", "round(1", "abs("):
        assert termo not in corpo, termo


# ---------------------------------------------------------------------------
# Escopo: nada fora de /canais mudou
# ---------------------------------------------------------------------------

def test_campos_de_conteudo_nao_vazaram_para_produtos_ou_brand_detail():
    """Os dois campos novos existem so no contrato de Canais."""
    from app.schemas import performance as sch

    permitidos = {"CanaisKpis", "CanaisBrandRow"}
    for nome in dir(sch):
        modelo = getattr(sch, nome)
        campos = getattr(modelo, "model_fields", None)
        if not isinstance(campos, dict):
            continue
        vazou = {c for c in campos if "content_gmv" in c}
        if nome in permitidos:
            assert vazou == {"tiktok_content_gmv_base", "tiktok_content_gmv_divergence_pct"}
        else:
            assert not vazou, f"{nome} recebeu campo de mix de conteudo: {vazou}"


def test_outros_servicos_nao_calculam_mix_de_conteudo():
    """gold_service e metabase_service seguem fora do escopo desta correcao."""
    from app.services import gold_service, metabase_service

    for mod in (gold_service, metabase_service):
        src = inspect.getsource(mod)
        assert "tiktok_content_gmv_base" not in src
        assert "tiktok_content_gmv_divergence_pct" not in src
        assert "tk_content_base" not in src
