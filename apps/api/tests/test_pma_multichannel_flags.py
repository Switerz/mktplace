"""Gate PMA-2C1A — feature flags, envelope indisponivel e transicao do ML.

A garantia central aqui e' NEGATIVA: com as flags desligadas, nenhuma consulta
e' emitida. Por isso a sessao falsa EXPLODE se alguem tentar usa-la — um teste
que apenas verificasse o payload passaria mesmo se o servico consultasse uma
tabela inexistente e engolisse o erro.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services import monitoramento_preco_service as mp
from app.services import pma_domain as dom


class SessaoProibida:
    """Qualquer toque no banco e' falha de teste, nao excecao capturavel."""

    def execute(self, *a, **k):  # pragma: no cover - so' roda se houver defeito
        raise AssertionError("o servico consultou o banco com a flag desligada")

    def __getattr__(self, nome):  # pragma: no cover
        raise AssertionError(f"o servico tocou a sessao ({nome}) indevidamente")


# ---------------------------------------------------------------------------
# Flags desligadas
# ---------------------------------------------------------------------------


def test_as_tres_flags_nascem_desligadas():
    assert settings.pma_shopee_enabled is False
    assert settings.pma_tiktok_enabled is False
    assert settings.pma_ml_metric_v2_enabled is False


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_canal_desligado_nao_consulta_a_tabela_inexistente(canal):
    resposta = mp.get_monitoramento_preco(SessaoProibida(), marketplace=canal)
    assert resposta["meta"]["availability"] == "unavailable"
    assert resposta["meta"]["unavailable_reason"] == dom.UNAVAILABLE_CHANNEL_DISABLED


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_canal_desligado_devolve_200_estruturado_e_nao_erro(canal):
    r = mp.get_monitoramento_preco(SessaoProibida(), marketplace=canal)
    assert r["rows"] == []
    assert r["total_count"] == 0
    assert r["returned_count"] == 0
    assert r["truncated"] is False


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_canal_desligado_nunca_cai_para_o_ml(canal):
    r = mp.get_monitoramento_preco(SessaoProibida(), marketplace=canal)
    assert r["meta"]["marketplace"] == canal
    assert r["meta"]["marketplace"] != "ml"


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_cobertura_indisponivel_e_none_nunca_zero(canal):
    """Zero por cento afirmaria uma medicao; None diz que nao ha o que medir."""
    m = mp.get_monitoramento_preco(SessaoProibida(), marketplace=canal)["metrics"]
    assert m["coverage_rate"] is None
    assert m["b2b_reach"] is None
    # Contagem zero e' VERDADE (nao ha linha alguma); taxa zero seria mentira.
    assert m["comparable_offers"] == 0
    assert m["monitored_offers"] == 0


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_kpis_mantem_a_forma_publicada_mesmo_no_envelope_indisponivel(canal):
    """O consumidor nao deve precisar de dois parsers conforme a flag."""
    k = mp.get_monitoramento_preco(SessaoProibida(), marketplace=canal)["kpis"]
    assert set(k) == {
        "monitored_count", "comparable_count", "below_reference_count",
        "at_or_above_reference_count", "no_reference_count",
        "ambiguous_reference_count", "inactive_count", "fresh_count",
        "stale_count", "historical_count",
    }
    assert all(v == 0 for v in k.values())


def test_envelope_indisponivel_avisa_que_nada_foi_consultado():
    avisos = mp.get_monitoramento_preco(
        SessaoProibida(), marketplace="shopee")["meta"]["warnings"]
    assert avisos == [mp.AVISO_CANAL_INDISPONIVEL]
    assert "0%" in mp.AVISO_CANAL_INDISPONIVEL


def test_shopee_desligada_ja_declara_snapshot_current():
    m = mp.get_monitoramento_preco(SessaoProibida(), marketplace="shopee")["meta"]
    assert m["observation_mode"] == "snapshot_current"
    assert m["observation_mode"] != "daily_series"


def test_tiktok_desligado_ja_declara_ausencia_de_contexto_promocional():
    m = mp.get_monitoramento_preco(SessaoProibida(), marketplace="tiktok")["meta"]
    assert m["promo_context"] == dom.PROMO_UNAVAILABLE


def test_data_ausente_no_canal_desligado_nao_inventa_observacao():
    m = mp.get_monitoramento_preco(SessaoProibida(), marketplace="shopee")["meta"]
    assert m["observed_date"] is None
    assert m["observed_ref_date"] is None
    assert m["available_observed_dates"] == []


# ---------------------------------------------------------------------------
# Borda do parametro
# ---------------------------------------------------------------------------


def test_canal_desconhecido_continua_recusado():
    with pytest.raises(mp.MonitoramentoPrecoError):
        mp.normalize_marketplace("amazon")


def test_recusa_nao_ecoa_a_entrada():
    try:
        mp.normalize_marketplace("<script>alert(1)</script>")
    except mp.MonitoramentoPrecoError as exc:
        assert "script" not in str(exc)
        assert "alert" not in str(exc)
    else:  # pragma: no cover
        raise AssertionError("deveria ter recusado")


def test_os_tres_canais_do_dominio_sao_reconhecidos():
    for canal in ("ml", "shopee", "tiktok"):
        assert mp.normalize_marketplace(canal) == canal


def test_ausencia_de_parametro_continua_sendo_ml():
    assert mp.normalize_marketplace(None) == "ml"
    assert mp.normalize_marketplace("") == "ml"


# ---------------------------------------------------------------------------
# Transicao do ML
# ---------------------------------------------------------------------------

#: Congelado em 2026-09-14 sobre `pma-ref:20260915T172047Z`, medido pela
#: implementacao real. A troca de v1 para v2 e' evolucao DELIBERADA da metrica.
BASELINE_ML = {
    "observed": 862,
    "v1": {"comparable": 139, "below": 18, "at_or_above": 121},
    "v2": {"comparable": 135, "below": 18, "at_or_above": 117},
}

#: Os quatro anuncios da Rituaria que migram para `kit_composition_missing`.
#: Todos compartilham o GTIN de um COMPONENTE, e por isso eram comparados ao
#: PDV de uma unidade. Travados por id para que a mudanca seja auditavel.
ITENS_QUE_MUDAM_EM_V2 = (
    ("MLB4641567444", "MLTRIOUNIF", "Kit Trio Serum Uniformizador Rituaria"),
    ("MLB6256109188", "MLTRIOUNIF", "Kit Trio Serum Uniformizador Rituaria"),
    ("MLB4193695049", "MLAVANRENO", "Kit Rituaria Mousse Limpeza Serum"),
    ("MLB6256108744", "MLAVANRENO", "Kit Rituaria Mousse Limpeza Serum"),
)


def test_ml_permanece_em_v1_com_a_flag_desligada():
    assert mp.active_metric_version("ml") == dom.METRIC_VERSION_V1


def test_canais_novos_ja_nascem_em_v2():
    """Nao ha metrica publicada de Shopee/TikTok para preservar."""
    assert mp.active_metric_version("shopee") == dom.METRIC_VERSION_V2
    assert mp.active_metric_version("tiktok") == dom.METRIC_VERSION_V2


def test_v2_retira_exatamente_quatro_comparaveis():
    assert (BASELINE_ML["v1"]["comparable"]
            - BASELINE_ML["v2"]["comparable"]) == len(ITENS_QUE_MUDAM_EM_V2)


def test_v2_nao_altera_o_abaixo_da_referencia():
    """As quatro saem de `at_or_above`; nenhuma estava abaixo."""
    assert BASELINE_ML["v1"]["below"] == BASELINE_ML["v2"]["below"] == 18
    assert (BASELINE_ML["v1"]["at_or_above"]
            - BASELINE_ML["v2"]["at_or_above"]) == len(ITENS_QUE_MUDAM_EM_V2)


def test_baseline_do_ml_fecha_nas_duas_versoes():
    for versao in ("v1", "v2"):
        b = BASELINE_ML[versao]
        assert b["below"] + b["at_or_above"] == b["comparable"]


@pytest.mark.parametrize("item_id,sku,titulo", ITENS_QUE_MUDAM_EM_V2)
def test_os_quatro_anuncios_sao_suspeitos_e_nunca_confirmados(item_id, sku, titulo):
    tipo, fonte = dom.classify_product_type("ml", seller_sku=sku, title=titulo)
    assert tipo == dom.PRODUCT_KIT_SUSPECTED
    assert tipo != dom.PRODUCT_KIT_CONFIRMED
    assert fonte == dom.SOURCE_TITLE
    assert dom.is_excluded_from_comparison(tipo) is True


def test_os_quatro_sao_de_dois_skus_com_o_mesmo_ean_de_componente():
    """Achado de qualidade: dois SKUs distintos carregam o mesmo GTIN."""
    assert len({sku for _, sku, _ in ITENS_QUE_MUDAM_EM_V2}) == 2
    assert len({iid for iid, _, _ in ITENS_QUE_MUDAM_EM_V2}) == 4


def test_kits_permanecem_visiveis_e_nao_somem_do_monitoramento():
    """`observed` nao muda entre v1 e v2: kits saem da CONFORMIDADE, nao da tela."""
    assert BASELINE_ML["observed"] == 862
    assert dom.COMPARISON_KIT_MISSING in dom.COMPARISON_STATUSES
    assert dom.COMPARISON_KIT_MISSING not in dom.COMPARABLE_STATUSES


# ---------------------------------------------------------------------------
# Borda HTTP
# ---------------------------------------------------------------------------


def _client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


URL = "/api/v1/performance/monitoramento-preco"


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_rota_devolve_200_estruturado_sem_banco_disponivel(canal):
    """Canal desligado nao precisa de sessao: o portao vem antes de `_require_db`.

    Sem este desvio a rota respondia 503/500 — erro de infraestrutura no lugar
    do estado `unavailable` que o contrato promete.
    """
    r = _client().get(f"{URL}?marketplace={canal}")
    assert r.status_code == 200, r.text[:300]
    corpo = r.json()
    assert corpo["meta"]["availability"] == "unavailable"
    assert corpo["meta"]["marketplace"] == canal
    assert corpo["rows"] == []


@pytest.mark.parametrize("canal", ["shopee", "tiktok"])
def test_rota_nunca_devolve_500_para_canal_reconhecido(canal):
    assert _client().get(f"{URL}?marketplace={canal}").status_code != 500


def test_rota_recusa_canal_desconhecido_sem_ecoar():
    r = _client().get(f"{URL}?marketplace=amazon")
    assert r.status_code == 422
    assert "amazon" not in r.text


def test_rota_declara_frescor_indisponivel_e_nao_fresco():
    m = _client().get(f"{URL}?marketplace=shopee").json()["meta"]
    assert m["freshness_status"] == "unavailable"
    assert m["freshness_status"] != "fresh"


# ---------------------------------------------------------------------------
# Numerador e denominador da MESMA versao
# ---------------------------------------------------------------------------

#: Medido contra o banco real em 2026-09-14. A taxa de cada versao usa numerador
#: E denominador dela propria. Misturar (139 sobre 339 = 41%) produziria uma
#: taxa que nao pertence a nenhuma versao e que ninguem reconciliaria depois.
COBERTURA_POR_VERSAO = {
    dom.METRIC_VERSION_V1: (139, 693),
    dom.METRIC_VERSION_V2: (135, 339),
}


@pytest.mark.parametrize("versao,esperado", list(COBERTURA_POR_VERSAO.items()))
def test_taxa_de_cobertura_nao_mistura_versoes(versao, esperado):
    comparaveis, elegiveis = esperado
    assert dom.coverage_rate(comparaveis, elegiveis) == pytest.approx(
        comparaveis / elegiveis)


def test_a_mistura_de_versoes_produziria_taxa_de_nenhuma_delas():
    """Contraprova explicita do defeito que este desenho evita."""
    v1_num, v1_den = COBERTURA_POR_VERSAO[dom.METRIC_VERSION_V1]
    _, v2_den = COBERTURA_POR_VERSAO[dom.METRIC_VERSION_V2]
    misturada = dom.coverage_rate(v1_num, v2_den)
    assert misturada != dom.coverage_rate(*COBERTURA_POR_VERSAO[dom.METRIC_VERSION_V1])
    assert misturada != dom.coverage_rate(*COBERTURA_POR_VERSAO[dom.METRIC_VERSION_V2])


def test_kits_permanecem_no_monitoramento_nas_duas_versoes():
    """`observed` nao muda: kits saem da conformidade, nunca da tela."""
    assert BASELINE_ML["observed"] == 862
    v1_num, v1_den = COBERTURA_POR_VERSAO[dom.METRIC_VERSION_V1]
    v2_num, v2_den = COBERTURA_POR_VERSAO[dom.METRIC_VERSION_V2]
    assert v1_den - v2_den == 354  # kit_suspected saiu do denominador
    assert v1_num - v2_num == 4    # e apenas 4 deles eram comparaveis


# ---------------------------------------------------------------------------
# Contrato UNICO de product_type no ML  (Gate PMA-2C1A-R, fase 1)
# ---------------------------------------------------------------------------

#: O contrato publicado e' o da camada que SERVE. A API le so' `marts.*` e nao
#: consulta o Data Mart, entao para o ML nao existe autoridade estruturada.
#: Uma medicao offline com o cadastro interno dava 290/49 com 5 comparaveis
#: `unknown`; esse recorte nao e' servivel e foi DESCARTADO do contrato.
CONTRATO_PRODUCT_TYPE_ML = {
    dom.PRODUCT_KIT_CONFIRMED: 0,
    dom.PRODUCT_KIT_SUSPECTED: 354,
    dom.PRODUCT_NO_KIT_SIGNAL: 0,
    dom.PRODUCT_TYPE_UNKNOWN: 339,
}
FILA_DE_REVISAO_ML = 339
COMPARAVEIS_UNKNOWN_ML = 135


def test_contrato_de_product_type_do_ml_soma_as_ativas():
    assert sum(CONTRATO_PRODUCT_TYPE_ML.values()) == 693


def test_ml_nao_produz_no_kit_signal_na_camada_de_serving():
    """Sem cadastro interno nao ha como AFIRMAR ausencia de kit."""
    assert CONTRATO_PRODUCT_TYPE_ML[dom.PRODUCT_NO_KIT_SIGNAL] == 0
    tipo, fonte = dom.classify_product_type(
        "ml", seller_sku="RT01002", title="Serum Facial 30ml")
    assert tipo == dom.PRODUCT_TYPE_UNKNOWN
    assert fonte == dom.SOURCE_NONE


def test_a_fila_de_revisao_tem_339_ofertas_e_nao_5():
    """CONTRAPROVA: restabelecer 'apenas cinco unknown comparaveis' falha."""
    assert FILA_DE_REVISAO_ML == CONTRATO_PRODUCT_TYPE_ML[dom.PRODUCT_TYPE_UNKNOWN]
    assert COMPARAVEIS_UNKNOWN_ML == 135
    assert COMPARAVEIS_UNKNOWN_ML != 5, (
        "o recorte offline (5 comparaveis unknown) nao e' servivel e nao pode "
        "voltar ao contrato"
    )


def test_a_medicao_offline_descartada_nao_pode_reaparecer():
    """Os numeros do cadastro interno nao sao o contrato publicado."""
    descartado = {dom.PRODUCT_NO_KIT_SIGNAL: 290, dom.PRODUCT_TYPE_UNKNOWN: 49}
    assert CONTRATO_PRODUCT_TYPE_ML[dom.PRODUCT_NO_KIT_SIGNAL] != descartado[
        dom.PRODUCT_NO_KIT_SIGNAL]
    assert CONTRATO_PRODUCT_TYPE_ML[dom.PRODUCT_TYPE_UNKNOWN] != descartado[
        dom.PRODUCT_TYPE_UNKNOWN]


def test_unknown_permanece_elegivel_pela_politica_aprovada():
    assert dom.is_excluded_from_comparison(dom.PRODUCT_TYPE_UNKNOWN) is False
    assert dom.PRODUCT_TYPE_UNKNOWN not in dom.PRODUCT_TYPES_EXCLUDED_FROM_COMPARISON


def test_unknown_nao_e_produto_simples_confirmado():
    """O rotulo da UI e 'sem sinal de kit'. `no_kit_signal` e' outro estado."""
    assert dom.PRODUCT_TYPE_UNKNOWN != dom.PRODUCT_NO_KIT_SIGNAL
    assert dom.PRODUCT_TYPE_UNKNOWN == "product_type_unknown"


def test_kpis_v2_permanecem_apos_a_correcao_do_contrato():
    elegiveis = dom.eligible_offers(
        active=693,
        kit_confirmed=CONTRATO_PRODUCT_TYPE_ML[dom.PRODUCT_KIT_CONFIRMED],
        kit_suspected=CONTRATO_PRODUCT_TYPE_ML[dom.PRODUCT_KIT_SUSPECTED],
    )
    assert elegiveis == 339
    assert BASELINE_ML["observed"] == 862
    assert BASELINE_ML["v2"] == {"comparable": 135, "below": 18, "at_or_above": 117}
