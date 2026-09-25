"""Gate KITS-PMA-3 — a API serve a referencia de kit DERIVADA dos componentes.

O que estes testes travam, em uma frase: a referencia derivada so' e' aplicada
a oferta EXATA para a qual foi publicada, sobre o snapshot B2B EXATO com que
foi calculada, e quando e' aplicada o kit deixa de ser "sem composicao" e volta
para o denominador — com a aritmetica fechando.

A API NAO CALCULA COMPOSICAO
-----------------------------
Nenhum teste daqui monta BOM. O valor chega pronto de
`marts.fact_kit_reference_daily`, publicado pelo pipeline. Se algum dia alguem
mover o calculo para ca', `test_api_nunca_toca_o_data_mart` e
`test_nao_ha_consulta_de_bom_no_serving` ficam vermelhos.

O QUE MUDA E O QUE NAO MUDA
----------------------------
Muda: `suggested_retail_amount`, `difference_*`, `comparison_status`,
`match_method`, `non_comparable_reason` (some), `eligible_offers`,
`comparable_offers`, `kit_reference_derived`.

NAO muda: `product_type` — a oferta continua sendo kit, e continua contada em
`kit_confirmed`/`kit_suspected`. A tela precisa poder dizer "este e' um kit E
tem preco", nao escolher entre as duas coisas.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services import monitoramento_preco_service as mp
from app.services import pma_domain as dom
from app.services import pma_match as pm

from tests.test_pma_channel_serving import (  # noqa: E402
    CARGA, D0, HOJE, SessaoFake, oferta, referencia,
)

SNAPSHOT = "snap-1"


def referencia_da_marca(**over) -> dict:
    """Uma linha B2B de Kokeshi que NAO casa com o SKU do kit.

    Existe para que a marca seja CONHECIDA pelo indice. Sem ela a precedencia
    de motivos devolve `brand_without_b2b_reference` — que e' verdade, mas e'
    outra pauta — e o teste mediria a coisa errada.
    """
    base = dict(brand="kokeshi", reference_row_id="R-KOKESHI",
                source_sku="KS03015", source_gtin=None,
                product_name="Hidratante Facial Pele de Porcelana",
                suggested_retail_amount=Decimal("29.90"))
    base.update(over)
    return referencia(**base)


def kit_publicado(**over) -> dict:
    """Uma linha de `marts.fact_kit_reference_daily`, como o publisher grava."""
    base = {
        "marketplace": "tiktok", "observed_date": D0, "offer_key": "KIT-1",
        "brand": "kokeshi", "shop_account": "kokeshi", "seller_sku": "40010",
        "kit_protheus_sku": "KKS00008", "bridge_method": "alias_code_exact",
        "component_count": 2, "total_units": Decimal("2.0000"),
        "components_base_amount": Decimal("68.80"),
        "discount_pct": Decimal("0.0500"),
        "kit_reference_amount": Decimal("65.36"),
        "reference_snapshot_id": SNAPSHOT,
        "reference_captured_at": "2026-09-02T12:00:00+00:00",
    }
    base.update(over)
    return base


def oferta_kit(**over) -> dict:
    base = dict(
        marketplace="tiktok", offer_key="KIT-1", brand="kokeshi",
        shop_account="kokeshi", seller_sku="40010",
        listing_title="Kit Olhos de Gueixa + Oleo de Rosa Mosqueta",
        product_type=dom.PRODUCT_KIT_SUSPECTED,
        product_type_source=dom.SOURCE_SKU_PREFIX,
        observed_price=Decimal("59.90"),
    )
    base.update(over)
    return oferta(**base)


def servir(sessao, canal="tiktok"):
    return mp.get_monitoramento_preco(sessao, marketplace=canal, today=HOJE)


def _linha(saida, item_id="KIT-1"):
    return next(r for r in saida["rows"] if r["item_id"] == item_id)


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    monkeypatch.setattr(mp.settings, "pma_shopee_enabled", True)
    monkeypatch.setattr(mp.settings, "pma_tiktok_enabled", True)


# ------------------------------------------------- 1. aplicacao da referencia

def test_kit_com_referencia_derivada_vira_comparavel():
    s = SessaoFake(ofertas=[oferta_kit()],
                   referencias=[referencia_da_marca()],
                   kit_references=[kit_publicado()])
    linha = _linha(servir(s))
    assert linha["suggested_retail_amount"] == 65.36
    assert linha["comparison_status"] == pm.STATUS_BELOW  # 59,90 < 65,36
    assert linha["non_comparable_reason"] is None
    assert linha["match_method"] == pm.MATCH_KIT_DERIVED
    assert linha["match_quality"] == pm.QUALITY_KIT_DERIVED


def test_a_diferenca_usa_o_mesmo_calculo_das_ofertas_comuns():
    s = SessaoFake(ofertas=[oferta_kit()], referencias=[referencia_da_marca()],
                   kit_references=[kit_publicado()])
    linha = _linha(servir(s))
    # 59,90 - 65,36 = -5,46 ; -5,46 / 65,36 * 100 = -8,3537%
    assert linha["difference_amount"] == -5.46
    assert linha["difference_pct"] == -8.3537


def test_procedencia_viaja_na_linha():
    s = SessaoFake(ofertas=[oferta_kit()], referencias=[referencia_da_marca()],
                   kit_references=[kit_publicado()])
    linha = _linha(servir(s))
    assert linha["kit_protheus_sku"] == "KKS00008"
    assert linha["kit_bridge_method"] == "alias_code_exact"
    assert linha["kit_component_count"] == 2
    assert linha["kit_total_units"] == 2.0
    assert linha["kit_components_base_amount"] == 68.80
    assert linha["kit_discount_pct"] == 0.05


def test_product_type_continua_kit():
    """Ganhar preco nao deixa de ser kit. A tela precisa das duas coisas."""
    s = SessaoFake(ofertas=[oferta_kit()], referencias=[referencia_da_marca()],
                   kit_references=[kit_publicado()])
    saida = servir(s)
    assert _linha(saida)["product_type"] == dom.PRODUCT_KIT_SUSPECTED
    assert saida["metrics"]["kit_suspected"] == 1


def test_kit_acima_da_referencia():
    s = SessaoFake(ofertas=[oferta_kit(observed_price=Decimal("80.00"))],
                   kit_references=[kit_publicado()])
    assert _linha(servir(s))["comparison_status"] == pm.STATUS_AT_OR_ABOVE


# ------------------------------------------------- 2. recusas

@pytest.mark.parametrize("campo,valor", [
    ("offer_key", "OUTRA-OFERTA"),
    ("brand", "barbours"),
    ("shop_account", "outra-conta"),
    ("seller_sku", "99999"),
])
def test_referencia_nao_vaza_para_outra_oferta(campo, valor):
    """Qualquer um dos quatro campos divergindo impede a aplicacao."""
    s = SessaoFake(ofertas=[oferta_kit()],
                   referencias=[referencia_da_marca()],
                   kit_references=[kit_publicado(**{campo: valor})])
    linha = _linha(servir(s))
    assert linha["suggested_retail_amount"] is None
    assert linha["non_comparable_reason"] == dom.REASON_KIT_COMPOSITION_MISSING


def test_snapshot_incompativel_e_recusado():
    """Referencia calculada sobre planilha B2B ANTIGA nao e' exibida."""
    s = SessaoFake(ofertas=[oferta_kit()],
                   referencias=[referencia_da_marca()],
                   kit_references=[kit_publicado(
                       reference_snapshot_id="snap-ANTIGO")])
    linha = _linha(servir(s))
    assert linha["suggested_retail_amount"] is None
    assert linha["match_method"] != pm.MATCH_KIT_DERIVED
    assert linha["non_comparable_reason"] == dom.REASON_KIT_COMPOSITION_MISSING


def test_fotografia_de_outro_dia_e_recusada():
    s = SessaoFake(ofertas=[oferta_kit()],
                   referencias=[referencia_da_marca()],
                   kit_references=[kit_publicado(
                       observed_date=date(2026, 9, 10))])
    assert _linha(servir(s))["suggested_retail_amount"] is None


def test_sem_linha_publicada_o_kit_segue_sem_referencia():
    """O comportamento de ANTES desta frente, preservado inteiro."""
    s = SessaoFake(ofertas=[oferta_kit()], referencias=[referencia_da_marca()],
                   kit_references=[])
    linha = _linha(servir(s))
    assert linha["suggested_retail_amount"] is None
    assert linha["comparison_status"] == pm.STATUS_NO_REFERENCE
    assert linha["non_comparable_reason"] == dom.REASON_KIT_COMPOSITION_MISSING


def test_inativo_vence_a_referencia_derivada():
    """Precedencia preservada: sem vitrine publica nao ha o que comparar."""
    s = SessaoFake(ofertas=[oferta_kit(is_active=False)],
                   kit_references=[kit_publicado()])
    linha = _linha(servir(s))
    assert linha["comparison_status"] == pm.STATUS_INACTIVE
    assert linha["suggested_retail_amount"] is None


def test_referencia_b2b_direta_tem_precedencia_sobre_a_derivada():
    """Se o SKU do kit esta na planilha, a planilha e' a autoridade."""
    s = SessaoFake(
        ofertas=[oferta_kit()],
        referencias=[referencia(brand="kokeshi", source_sku="40010",
                                reference_row_id="R-DIRETA",
                                suggested_retail_amount=Decimal("99.00"))],
        kit_references=[kit_publicado()])
    linha = _linha(servir(s))
    assert linha["suggested_retail_amount"] == 99.00
    assert linha["match_method"] == pm.MATCH_SKU
    assert linha["kit_protheus_sku"] is None


def test_nulo_nunca_vira_zero():
    s = SessaoFake(ofertas=[oferta_kit()], referencias=[referencia_da_marca()],
                   kit_references=[])
    linha = _linha(servir(s))
    assert linha["suggested_retail_amount"] is None
    assert linha["difference_amount"] is None
    assert linha["difference_pct"] is None


# ------------------------------------------------- 3. KPIs e breakdown

def _cenario_misto():
    """Um kit COM referencia derivada, um kit SEM, e um produto comum."""
    return SessaoFake(
        ofertas=[
            oferta_kit(),
            oferta_kit(offer_key="KIT-2", seller_sku="40015",
                       listing_title="Kit sem referencia"),
            oferta(marketplace="tiktok", offer_key="SIMPLES-1",
                   brand="barbours", shop_account="barbours",
                   seller_sku="SKU-1", observed_price=Decimal("50.00")),
        ],
        referencias=[referencia(), referencia_da_marca()],
        kit_references=[kit_publicado()])


def test_denominador_sobe_exatamente_pelos_kits_derivados():
    s = _cenario_misto()
    m = servir(s)["metrics"]
    # 3 ativas: 2 kits + 1 comum. Sem esta frente o elegivel seria 1.
    assert m["active_offers"] == 3
    assert m["kit_suspected"] == 2
    assert m["kit_reference_derived"] == 1
    assert m["eligible_offers"] == 2
    assert (m["eligible_offers"]
            == m["active_offers"] - m["kit_confirmed"] - m["kit_suspected"]
            + m["kit_reference_derived"])


def test_particao_do_denominador_continua_fechando():
    s = _cenario_misto()
    m = servir(s)["metrics"]
    assert (m["comparable_offers"] + sum(m["non_comparable_reasons"].values())
            == m["eligible_offers"])
    assert (m["below_reference"] + m["at_or_above_reference"]
            == m["comparable_offers"])


def test_balde_de_kit_sem_referencia_encolhe():
    com = servir(_cenario_misto())["metrics"]
    sem = servir(SessaoFake(
        ofertas=_cenario_misto().ofertas,
        referencias=[referencia(), referencia_da_marca()],
        kit_references=[]))["metrics"]
    assert (sem["no_reference_breakdown"][dom.REASON_KIT_COMPOSITION_MISSING]
            - com["no_reference_breakdown"][dom.REASON_KIT_COMPOSITION_MISSING]
            == 1)
    assert com["comparable_offers"] - sem["comparable_offers"] == 1


def test_breakdown_continua_somando_o_cartao():
    s = _cenario_misto()
    saida = servir(s)
    assert (sum(saida["metrics"]["no_reference_breakdown"].values())
            == saida["kpis"]["no_reference_count"])


def test_kit_derivado_nao_conta_como_produto_b2b_distinto():
    """Um kit nao e' produto da planilha: `distinct_b2b_products` nao o conta."""
    s = SessaoFake(ofertas=[oferta_kit()], referencias=[referencia_da_marca()],
                   kit_references=[kit_publicado()])
    assert servir(s)["metrics"]["distinct_b2b_products"] == 0


def test_kit_derivado_fora_de_escopo_nao_entra_no_denominador():
    s = SessaoFake(
        ofertas=[oferta_kit(business_scope=dom.BUSINESS_SCOPE_OUT)],
        referencias=[referencia_da_marca()],
        kit_references=[kit_publicado()])
    m = servir(s)["metrics"]
    assert m["kit_reference_derived"] == 0
    assert m["eligible_offers"] == 0


# ------------------------------------------------- 4. isolamento

def test_api_nunca_toca_o_data_mart():
    """O Render nao alcanca o Data Mart. O serving so' le' tabelas do Neon."""
    s = _cenario_misto()
    servir(s)
    proibidas = ("raw.protheus_kit_components", "gold.dim_produto_gobeauty",
                 "gold.map_produto_codigo_gobeauty",
                 "silver.gobeaute_produto_cadastro", "gold.", "silver.", "raw.")
    for texto, _ in s.executadas:
        for proibida in proibidas:
            assert proibida not in texto, texto[:160]


def test_nao_ha_consulta_de_bom_no_serving():
    s = _cenario_misto()
    servir(s)
    assert any("fact_kit_reference_daily" in t for t, _ in s.executadas)
    for termo in ("kit_sku", "component_sku", "qty_per_kit"):
        assert not any(termo in t for t, _ in s.executadas)


def test_uma_unica_consulta_de_kit_por_requisicao():
    """Nem por linha, nem por marca: uma so'."""
    s = _cenario_misto()
    servir(s)
    assert sum(1 for t, _ in s.executadas
               if "fact_kit_reference_daily" in t) == 1


def test_ml_nao_consulta_a_fato_de_kit():
    s = SessaoFake(listings_ml=[])
    mp.get_monitoramento_preco(s, marketplace="ml", today=HOJE)
    assert "fact_kit_reference_daily" not in s.tabelas


def test_produto_comum_permanece_identico():
    comum = oferta(marketplace="tiktok", offer_key="SIMPLES-1",
                   brand="barbours", shop_account="barbours",
                   seller_sku="SKU-1", observed_price=Decimal("50.00"))
    com = servir(SessaoFake(ofertas=[comum], referencias=[referencia()],
                            kit_references=[kit_publicado()]))
    sem = servir(SessaoFake(ofertas=[comum], referencias=[referencia()],
                            kit_references=[]))
    a, b = _linha(com, "SIMPLES-1"), _linha(sem, "SIMPLES-1")
    assert a == b
    assert a["match_method"] == pm.MATCH_SKU
    assert a["kit_protheus_sku"] is None


# ------------------------------------------------- 5. contrato do schema

def test_o_payload_atravessa_o_schema():
    """Gate PMA-OPS-2 — valor novo no dominio derruba a PAGINA se o schema o
    desconhece. O teste passa pelo Pydantic, nao so' pelo servico."""
    from app.schemas.monitoramento_preco import MonitoramentoPrecoResponse

    s = _cenario_misto()
    modelo = MonitoramentoPrecoResponse.model_validate(servir(s))
    linha = next(r for r in modelo.rows if r.item_id == "KIT-1")
    assert linha.match_method == pm.MATCH_KIT_DERIVED
    assert linha.match_quality == pm.QUALITY_KIT_DERIVED
    assert linha.kit_protheus_sku == "KKS00008"
    assert modelo.metrics.kit_reference_derived == 1


def test_schema_cobre_todo_o_dominio_de_metodo_e_qualidade():
    """O que derrubou a tela no PMA-OPS-2 foi um Literal redigitado."""
    from app.schemas.monitoramento_preco import MatchMethod, MatchQuality
    import typing

    assert set(typing.get_args(MatchMethod)) == set(pm.MATCH_METHODS)
    assert set(typing.get_args(MatchQuality)) == set(pm.MATCH_QUALITIES)
