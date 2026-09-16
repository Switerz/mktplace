"""Gate PMA-2C1A — nucleo multicanal: dominio, graos, matching e flags.

Estes testes travam decisoes SEMANTICAS, nao detalhes de implementacao. Cada um
existe porque o gate listou o comportamento oposto como stop-loss.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import pma_domain as dom
from app.services import pma_match as pm

# ---------------------------------------------------------------------------
# Enums e precedencia
# ---------------------------------------------------------------------------


def test_precedencia_tem_as_seis_autoridades_na_ordem_do_gate():
    assert dom.PRODUCT_TYPE_SOURCE_PRECEDENCE == (
        "channel_flag", "internal_catalog", "internal_bom",
        "sku_prefix", "title", "none",
    )


def test_estados_de_produto_sao_os_quatro_autorizados():
    assert set(dom.PRODUCT_TYPES) == {
        "kit_confirmed", "kit_suspected", "no_kit_signal", "product_type_unknown"
    }


def test_flag_nativa_da_shopee_produz_kit_confirmed():
    tipo, fonte = dom.classify_product_type(
        dom.MARKETPLACE_SHOPEE, channel_kit_flag=True)
    assert (tipo, fonte) == (dom.PRODUCT_KIT_CONFIRMED, dom.SOURCE_CHANNEL_FLAG)


@pytest.mark.parametrize("canal", [dom.MARKETPLACE_ML, dom.MARKETPLACE_TIKTOK])
def test_kit_confirmed_e_impossivel_fora_da_shopee(canal):
    """Stop-loss do gate: suspeito nunca pode virar confirmado."""
    with pytest.raises(dom.ProductTypeError):
        dom.classify_product_type(canal, channel_kit_flag=True)


@pytest.mark.parametrize("canal", [dom.MARKETPLACE_ML, dom.MARKETPLACE_TIKTOK])
@pytest.mark.parametrize("kwargs", [
    {"internal_is_kit": True},
    {"internal_has_bom": True},
    {"seller_sku": "KIT044"},
    {"title": "Kit Trio Serum Uniformizador"},
])
def test_sinais_fora_da_shopee_produzem_no_maximo_suspeita(canal, kwargs):
    tipo, _ = dom.classify_product_type(canal, **kwargs)
    assert tipo == dom.PRODUCT_KIT_SUSPECTED
    assert tipo != dom.PRODUCT_KIT_CONFIRMED


def test_cadastro_interno_vence_prefixo_e_titulo():
    _, fonte = dom.classify_product_type(
        dom.MARKETPLACE_TIKTOK, internal_is_kit=True,
        seller_sku="KIT001", title="Kit qualquer")
    assert fonte == dom.SOURCE_INTERNAL_CATALOG


def test_bom_vence_prefixo_e_titulo():
    _, fonte = dom.classify_product_type(
        dom.MARKETPLACE_TIKTOK, internal_is_kit=False, internal_has_bom=True,
        seller_sku="KIT001", title="Kit qualquer")
    assert fonte == dom.SOURCE_INTERNAL_BOM


def test_prefixo_vence_titulo():
    _, fonte = dom.classify_product_type(
        dom.MARKETPLACE_TIKTOK, seller_sku="KIT009", title="Kit qualquer")
    assert fonte == dom.SOURCE_SKU_PREFIX


def test_autoridade_estruturada_falsa_produz_no_kit_signal():
    assert dom.classify_product_type(
        dom.MARKETPLACE_SHOPEE, channel_kit_flag=False)[0] == dom.PRODUCT_NO_KIT_SIGNAL
    assert dom.classify_product_type(
        dom.MARKETPLACE_ML, internal_is_kit=False)[0] == dom.PRODUCT_NO_KIT_SIGNAL


def test_sem_autoridade_estruturada_e_unknown_nao_no_kit_signal():
    """Titulo silencioso NAO prova ausencia de kit."""
    tipo, fonte = dom.classify_product_type(
        dom.MARKETPLACE_ML, seller_sku="RT01002", title="Serum Facial 30ml")
    assert (tipo, fonte) == (dom.PRODUCT_TYPE_UNKNOWN, dom.SOURCE_NONE)


def test_autoridade_indisponivel_difere_de_autoridade_dizendo_nao():
    assert dom.classify_product_type(dom.MARKETPLACE_ML,
                                     internal_is_kit=None)[0] == dom.PRODUCT_TYPE_UNKNOWN
    assert dom.classify_product_type(dom.MARKETPLACE_ML,
                                     internal_is_kit=False)[0] == dom.PRODUCT_NO_KIT_SIGNAL


@pytest.mark.parametrize("titulo,esperado", [
    ("Kit Trio Serum", True), ("KIT 3 itens", True), ("Kits promocionais", True),
    ("Kitchen cleaner", False), ("Serum facial", False), ("", False), (None, False),
])
def test_sinal_textual_usa_limite_de_palavra(titulo, esperado):
    assert dom.title_has_kit_signal(titulo) is esperado


def test_titulo_nunca_participa_do_matching():
    """O matcher nao le titulo — nem por acidente de chave."""
    index = pm.ReferenceIndex.build([
        {"brand": "rituaria", "source_gtin": "7897185070101", "source_sku": "RT01",
         "suggested_retail_amount": Decimal("160.00"), "captured_at": None},
    ])
    r = pm.resolve_match(
        {"brand": "rituaria", "gtin": None, "seller_sku": None,
         "listing_title": "Kit Trio Serum Uniformizador Rituaria"}, index)
    assert r.reference is None
    assert r.method is pm.MATCH_NONE


# ---------------------------------------------------------------------------
# Graos
# ---------------------------------------------------------------------------


def test_grao_do_ml_e_o_item_id():
    assert dom.build_offer_key(dom.MARKETPLACE_ML, item_id="MLB123") == "MLB123"


def test_grao_do_tiktok_e_o_sku_id():
    assert dom.build_offer_key(dom.MARKETPLACE_TIKTOK, sku_id="17311") == "17311"


def test_shopee_sem_variacao_usa_item_id():
    assert dom.build_offer_key(dom.MARKETPLACE_SHOPEE, item_id="900",
                               has_model=False) == "900"


def test_shopee_com_variacao_usa_item_id_dois_pontos_model_id():
    assert dom.build_offer_key(dom.MARKETPLACE_SHOPEE, item_id="900",
                               model_id="7", has_model=True) == "900:7"


def test_pai_sem_variacao_nao_pode_carregar_model_id():
    with pytest.raises(dom.GrainError):
        dom.build_offer_key(dom.MARKETPLACE_SHOPEE, item_id="900",
                            model_id="7", has_model=False)


def test_pai_com_variacao_e_container_e_nao_vira_oferta():
    assert dom.shopee_offer_is_container(True) is True
    assert dom.shopee_offer_is_container(False) is False


def test_chave_de_pai_e_de_modelo_nunca_colidem():
    """A dupla contagem 659+371=1030 e' impossivel por construcao da chave."""
    pais = {dom.build_offer_key(dom.MARKETPLACE_SHOPEE, item_id=str(i),
                                has_model=False) for i in range(100)}
    modelos = {dom.build_offer_key(dom.MARKETPLACE_SHOPEE, item_id=str(i),
                                   model_id="1", has_model=True)
               for i in range(100)}
    assert pais.isdisjoint(modelos)


def test_shopee_exige_has_model_para_decidir_o_grao():
    with pytest.raises(dom.GrainError):
        dom.build_offer_key(dom.MARKETPLACE_SHOPEE, item_id="900")


def test_fotografia_de_referencia_692_igual_321_mais_371():
    """321 pais simples + 371 modelos = 692; os 338 pais com variacao nao contam."""
    pais_simples, pais_com_variacao, modelos = 321, 338, 371
    chaves = set()
    for i in range(pais_simples):
        chaves.add(dom.build_offer_key(dom.MARKETPLACE_SHOPEE,
                                       item_id=f"s{i}", has_model=False))
    for i in range(modelos):
        chaves.add(dom.build_offer_key(dom.MARKETPLACE_SHOPEE,
                                       item_id=f"c{i % pais_com_variacao}",
                                       model_id=str(i), has_model=True))
    assert len(chaves) == 692
    assert pais_simples + pais_com_variacao == 659
    assert len(chaves) != 659 + modelos


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _index():
    return pm.ReferenceIndex.build([
        {"brand": "apice", "source_gtin": "7898652874765", "source_sku": "AP01",
         "suggested_retail_amount": Decimal("49.90"), "captured_at": None},
        {"brand": "apice", "source_gtin": "7898652876912", "source_sku": "AP02",
         "suggested_retail_amount": Decimal("54.90"), "captured_at": None},
    ])


def _internal():
    return pm.InternalProductIndex.build(
        rows=[("apice", "VRTA03", "psk-1"), ("apice", "DUPL", "psk-a"),
              ("apice", "DUPL", "psk-b"), ("apice", "SEMEAN", "psk-2")],
        ean_rows=[("psk-1", "7898652874765"), ("psk-2", "0")],
    )


def test_ml_nao_recebe_indice_interno_e_o_terceiro_metodo_nao_dispara():
    """Ganho medido do terceiro metodo no ML: ZERO. Aqui ele nem executa."""
    r = pm.resolve_match({"brand": "apice", "gtin": None, "seller_sku": "VRTA03"},
                         _index())
    assert r.method is pm.MATCH_NONE
    assert r.reference is None


def test_terceiro_metodo_resolve_quando_o_indice_interno_e_fornecido():
    r = pm.resolve_match({"brand": "apice", "gtin": None, "seller_sku": "VRTA03"},
                         _index(), internal_index=_internal())
    assert r.method == pm.MATCH_INTERNAL
    assert r.quality == pm.QUALITY_TERTIARY
    assert r.reference["source_sku"] == "AP01"


def test_chave_interna_com_dois_produtos_e_ambigua_sem_desempate():
    r = pm.resolve_match({"brand": "apice", "gtin": None, "seller_sku": "DUPL"},
                         _index(), internal_index=_internal())
    assert r.ambiguous is True
    assert r.reference is None


def test_ean_lixo_do_cadastro_nao_vira_match():
    r = pm.resolve_match({"brand": "apice", "gtin": None, "seller_sku": "SEMEAN"},
                         _index(), internal_index=_internal())
    assert r.reference is None
    assert r.ambiguous is False


def test_ordem_ean_depois_sku_depois_produto_interno():
    r = pm.resolve_match({"brand": "apice", "gtin": "7898652876912",
                          "seller_sku": "VRTA03"}, _index(),
                         internal_index=_internal())
    assert r.method == pm.MATCH_GTIN
    assert r.reference["source_sku"] == "AP02"


# ---------------------------------------------------------------------------
# Comparacao, metricas e particao
# ---------------------------------------------------------------------------


def test_preco_nulo_zero_ou_negativo_nunca_vira_conformidade():
    for preco in (None, 0, Decimal("0"), Decimal("-1")):
        status, razao = dom.compare_to_reference(preco, Decimal("10"))
        assert status is None
        assert razao == dom.REASON_INVALID_CHANNEL_PRICE


def test_pdv_invalido_e_recusado_antes_do_preco_do_canal():
    status, razao = dom.compare_to_reference(None, None)
    assert (status, razao) == (None, dom.REASON_INVALID_REFERENCE_PRICE)


def test_nan_e_infinito_nao_viram_conformidade():
    for ruim in (Decimal("NaN"), Decimal("Infinity"), float("nan")):
        assert dom.compare_to_reference(ruim, Decimal("10"))[0] is None


def test_below_e_at_or_above_cobrem_toda_comparacao_valida():
    assert dom.compare_to_reference(Decimal("9"), Decimal("10"))[0] == dom.COMPARISON_BELOW
    assert dom.compare_to_reference(Decimal("10"), Decimal("10"))[0] == dom.COMPARISON_AT_OR_ABOVE
    assert dom.compare_to_reference(Decimal("11"), Decimal("10"))[0] == dom.COMPARISON_AT_OR_ABOVE


def test_elegiveis_descontam_kit_confirmado_e_suspeito():
    assert dom.eligible_offers(active=693, kit_confirmed=0, kit_suspected=354) == 339
    assert dom.eligible_offers(active=591, kit_confirmed=265, kit_suspected=20) == 306


def test_cobertura_com_denominador_zero_e_none_nao_zero():
    assert dom.coverage_rate(0, 0) is None
    assert dom.coverage_rate(0, 10) == 0.0


def test_metricas_de_catalogo_nao_sao_denominador_de_conformidade():
    assert dom.b2b_reach(0, 0) is None
    assert dom.b2b_reach(83, 249) == pytest.approx(83 / 249)


def test_particao_exaustiva_aceita_a_fotografia_do_ml():
    dom.assert_partition(
        observed=862, active=693, inactive=169,
        product_type_counts={"kit_confirmed": 0, "kit_suspected": 354,
                             "no_kit_signal": 0, "product_type_unknown": 339},
        eligible=339, excluded_by_product_type=354, comparable=135,
        reason_counts={dom.REASON_REFERENCE_MISSING: 202,
                       dom.REASON_AMBIGUOUS: 2},
    )


def test_particao_recusa_oferta_que_sumiu():
    with pytest.raises(ValueError):
        dom.assert_partition(
            observed=862, active=693, inactive=168,
            product_type_counts={"product_type_unknown": 693},
            eligible=693, excluded_by_product_type=0, comparable=0,
            reason_counts={dom.REASON_REFERENCE_MISSING: 693})


def test_particao_recusa_oferta_em_dois_estados():
    with pytest.raises(ValueError):
        dom.assert_partition(
            observed=100, active=80, inactive=20,
            product_type_counts={"kit_suspected": 50, "product_type_unknown": 50},
            eligible=30, excluded_by_product_type=50, comparable=30,
            reason_counts={})


# ---------------------------------------------------------------------------
# Contrato temporal e de preco
# ---------------------------------------------------------------------------


def test_shopee_nunca_e_serie_diaria():
    assert dom.observation_mode_for(dom.MARKETPLACE_SHOPEE) == "snapshot_current"
    assert dom.observation_mode_for(dom.MARKETPLACE_SHOPEE) != "daily_series"


def test_ml_e_tiktok_sao_serie_diaria():
    assert dom.observation_mode_for(dom.MARKETPLACE_ML) == "daily_series"
    assert dom.observation_mode_for(dom.MARKETPLACE_TIKTOK) == "daily_series"


def test_tiktok_nao_pode_afirmar_contexto_promocional():
    assert dom.promo_context_for(dom.MARKETPLACE_TIKTOK) == dom.PROMO_UNAVAILABLE
    assert dom.LIST_PRICE_SOURCE[dom.MARKETPLACE_TIKTOK] is None


def test_preco_praticado_de_cada_canal_esta_congelado():
    assert dom.OBSERVED_PRICE_SOURCE == {
        "ml": "advertised_price", "shopee": "current_price", "tiktok": "sale_price"}


def test_campos_inflated_da_shopee_sao_proibidos():
    assert "inflated_current_price" in dom.FORBIDDEN_PRICE_FIELDS
    assert "inflated_original_price" in dom.FORBIDDEN_PRICE_FIELDS
    assert not set(dom.FORBIDDEN_PRICE_FIELDS) & set(dom.OBSERVED_PRICE_SOURCE.values())


def test_gocase_e_denavita_ficam_fora_do_escopo_de_beleza():
    assert dom.business_scope_for("gocase") == dom.BUSINESS_SCOPE_OUT
    assert dom.business_scope_for("denavita") == dom.BUSINESS_SCOPE_OUT
    for marca in ("apice", "barbours", "kokeshi", "lescent", "rituaria"):
        assert dom.business_scope_for(marca) == dom.BUSINESS_SCOPE_IN


def test_lescent_continua_no_escopo_mesmo_sem_referencia():
    """Sem referencia B2B nao e' o mesmo que fora do produto."""
    assert dom.in_beauty_scope("lescent") is True


def test_ml_nao_vive_na_fato_nova():
    assert dom.MARKETPLACE_ML not in dom.CHANNEL_OFFER_MARKETPLACES
    assert set(dom.CHANNEL_OFFER_MARKETPLACES) == {"shopee", "tiktok"}


# ---------------------------------------------------------------------------
# Revisao estrita do dominio  (Gate PMA-2C1A-R, fase 4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ruim", [
    Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"),
    float("nan"), float("inf"), float("-inf"),
    Decimal("-0.01"), Decimal("0"), 0, -1, None, "", "  ", "abc", [], {},
])
def test_preco_de_canal_invalido_nunca_vira_conformidade(ruim):
    status, razao = dom.compare_to_reference(ruim, Decimal("10.00"))
    assert status is None
    assert razao == dom.REASON_INVALID_CHANNEL_PRICE


@pytest.mark.parametrize("ruim", [
    Decimal("NaN"), Decimal("Infinity"), Decimal("0"), Decimal("-5"),
    None, "", "abc", float("nan"),
])
def test_pdv_invalido_nunca_vira_conformidade(ruim):
    status, razao = dom.compare_to_reference(Decimal("10.00"), ruim)
    assert status is None
    assert razao == dom.REASON_INVALID_REFERENCE_PRICE


def test_pdv_zero_nao_divide_por_zero():
    """A diferenca percentual divide pelo PDV: zero teria que explodir."""
    assert dom.difference(Decimal("10"), Decimal("0")) == (None, None)
    assert dom.coverage_rate(5, 0) is None
    assert dom.b2b_reach(5, 0) is None


def test_diferenca_devolve_decimal_e_nao_float():
    delta, pct = dom.difference(Decimal("246.74"), Decimal("160.00"))
    assert isinstance(delta, Decimal)
    assert isinstance(pct, Decimal)
    assert delta == Decimal("86.74")


def test_dinheiro_nao_perde_precisao_como_float():
    """0.1 + 0.2 != 0.3 em float; com Decimal a comparacao fecha."""
    status, _ = dom.compare_to_reference(Decimal("0.30"),
                                         Decimal("0.1") + Decimal("0.2"))
    assert status == dom.COMPARISON_AT_OR_ABOVE
    delta, _ = dom.difference(Decimal("0.30"), Decimal("0.1") + Decimal("0.2"))
    assert delta == Decimal("0.00")


def test_string_vazia_nao_e_tratada_como_valor():
    assert dom.title_has_kit_signal("") is False
    assert dom.sku_has_kit_prefix("") is False
    assert dom.in_beauty_scope("") is False
    assert dom.in_beauty_scope("   ") is False
    with pytest.raises(dom.GrainError):
        dom.build_offer_key(dom.MARKETPLACE_ML, item_id="   ")


def test_marca_fora_do_escopo_nunca_entra_nos_kpis():
    for fora in ("gocase", "denavita", "GOCASE", " Denavita "):
        assert dom.business_scope_for(fora) == dom.BUSINESS_SCOPE_OUT
    assert dom.business_scope_for(None) == dom.BUSINESS_SCOPE_OUT


def test_kit_suspeito_nunca_recebe_conformidade():
    for tipo in dom.PRODUCT_TYPES_EXCLUDED_FROM_COMPARISON:
        assert dom.is_excluded_from_comparison(tipo) is True
    assert dom.COMPARISON_KIT_MISSING not in dom.COMPARABLE_STATUSES


def test_mapas_de_contrato_sao_imutaveis():
    """Um mapa mutavel no nivel de modulo pode ser alterado em runtime."""
    for mapa in (dom.OBSERVED_PRICE_SOURCE, dom.LIST_PRICE_SOURCE,
                 dom._OBSERVATION_MODE_BY_MARKETPLACE,
                 dom._PROMO_CONTEXT_BY_MARKETPLACE):
        with pytest.raises(TypeError):
            mapa["ml"] = "alterado"


def test_classificacao_nao_depende_da_ordem_dos_dados():
    """Mesma entrada, mesma saida, independentemente da ordem de avaliacao."""
    entrada = dict(channel_kit_flag=False, internal_is_kit=True,
                   internal_has_bom=True, seller_sku="KIT1", title="Kit x")
    primeiro = dom.classify_product_type(dom.MARKETPLACE_SHOPEE, **entrada)
    for _ in range(50):
        assert dom.classify_product_type(dom.MARKETPLACE_SHOPEE, **entrada) == primeiro
    assert primeiro == (dom.PRODUCT_KIT_SUSPECTED, dom.SOURCE_INTERNAL_CATALOG)


def test_elegiveis_negativos_sao_recusados():
    with pytest.raises(ValueError):
        dom.eligible_offers(active=10, kit_confirmed=8, kit_suspected=5)


# ---------------------------------------------------------------------------
# Rotulos de preco  (fase 3)
# ---------------------------------------------------------------------------


def test_esta_superficie_nao_mede_preco_de_checkout():
    assert dom.PRICE_LABEL_REALIZED in dom.UNAVAILABLE_PRICE_LABELS
    assert dom.PRICE_LABEL_CHECKOUT in dom.UNAVAILABLE_PRICE_LABELS
    assert dom.PRICE_LABEL_COUPON in dom.UNAVAILABLE_PRICE_LABELS
    assert dom.PRICE_LABEL_SHIPPING in dom.UNAVAILABLE_PRICE_LABELS
    assert dom.PRICE_LABEL_OBSERVED not in dom.UNAVAILABLE_PRICE_LABELS


def test_expressoes_de_checkout_nao_viram_rotulo_nem_coluna():
    """O que importa e' o que o contrato EXPOE, nao o texto que o explica.

    Contar ocorrencias no arquivo reprovaria o proprio comentario que documenta
    a proibicao. A verificacao util e' outra: nenhuma frase banida pode virar
    nome de rotulo, chave de mapa ou coluna do registro.
    """
    from pipelines import channel_offer_sync as cos

    expostos = [
        *dom.UNAVAILABLE_PRICE_LABELS,
        dom.PRICE_LABEL_OBSERVED, dom.PRICE_LABEL_LIST,
        *dom.OBSERVED_PRICE_SOURCE.values(),
        *[v for v in dom.LIST_PRICE_SOURCE.values() if v],
        *cos.RECORD_COLUMNS,
    ]
    for rotulo in expostos:
        baixo = rotulo.lower()
        for termo in ("pago", "liquido", "checkout_amount", "realized_amount"):
            assert termo not in baixo, (rotulo, termo)


def test_os_rotulos_indisponiveis_nao_sao_colunas_do_registro():
    """Eles existem como CONCEITO declarado, nao como campo que finge medir."""
    from pipelines import channel_offer_sync as cos
    for rotulo in dom.UNAVAILABLE_PRICE_LABELS:
        assert rotulo not in cos.RECORD_COLUMNS, rotulo


def test_observed_date_usa_fuso_de_sao_paulo():
    from datetime import datetime, timezone
    assert dom.TIMEZONE_NAME == "America/Sao_Paulo"
    assert str(dom.observed_date_from(
        datetime(2026, 9, 15, 2, 30, tzinfo=timezone.utc))) == "2026-09-14"
    assert str(dom.observed_date_from(
        datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc))) == "2026-09-15"
    assert dom.observed_date_from(None) is None
