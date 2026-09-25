"""Gate PMA-REF-LINK-1 — toda oferta sem referencia declara a SUA causa.

O que estes testes travam, em uma frase: `no_reference` deixou de ser um balde
unico, cada linha carrega exatamente um motivo do vocabulario, e os dois
agregados que o payload publica sobre "sem referencia" reconciliam com os dois
denominadores diferentes que eles servem.

POR QUE ISTO E' UM TESTE E NAO UMA CONVENCAO
--------------------------------------------
Medido em 2026-09-25 sobre a fotografia publicada: as 1.817 ofertas sem
referencia dos tres canais recebiam `reference_missing_for_product`, que se le
como "o produto nao esta na tabela B2B". Para 879 delas isso era falso — 279 sao
de marca que nao tem tabela nenhuma, 600 sao kits sem composicao. A leitura
errada manda a pessoa errada procurar a coisa errada, e nenhum numero da tela
denunciava o engano. Um motivo por linha so' e' garantia se houver um teste que
recuse a linha sem motivo.

AS DUAS SOMAS NAO SAO A MESMA, E ISSO E' DE PROPOSITO
------------------------------------------------------
`non_comparable_reasons` fecha a particao do DENOMINADOR: kits e marcas fora do
escopo de negocio saem do elegivel, entao nao entram nela.
`no_reference_breakdown` explica o CARTAO da tela, que conta toda oferta ativa
sem referencia. No TikTok sao 251 contra 810. Antes deste gate a diferenca de
559 nao tinha nome em lugar nenhum do payload; agora as duas somas sao
verificaveis, cada uma contra o seu total.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.services import monitoramento_preco_service as mp
from app.services import pma_domain as dom
from app.services import pma_match as pm

from tests.test_pma_channel_serving import (  # noqa: E402  (tests/ e pacote)
    HOJE, SessaoFake, oferta, referencia,
)


@pytest.fixture(autouse=True)
def flags_ligadas(monkeypatch):
    """As flags nascem DESLIGADAS; cada teste que precisa delas as liga aqui,
    no processo, jamais na configuracao implantada."""
    monkeypatch.setattr(mp.settings, "pma_shopee_enabled", True)
    monkeypatch.setattr(mp.settings, "pma_tiktok_enabled", True)


def servir(sessao, canal="shopee", **kw) -> dict:
    return mp.get_monitoramento_preco(sessao, marketplace=canal, today=HOJE, **kw)


# ---------------------------------------------------------------------------
# 1. cada causa recebe o SEU motivo
# ---------------------------------------------------------------------------
def test_marca_sem_tabela_b2b_nao_e_produto_ausente_da_tabela():
    """A marca da oferta nao aparece no snapshot: nao ha onde procurar.

    Antes, esta linha dizia `reference_missing_for_product`, que manda conferir
    um cadastro de produto. A acao correta e' publicar a tabela da marca, e e'
    de outra area.
    """
    s = SessaoFake(ofertas=[oferta(brand="lescent", seller_sku="LC01")],
                   referencias=[referencia(brand="barbours")])
    linha = servir(s)["rows"][0]
    assert linha["comparison_status"] == pm.STATUS_NO_REFERENCE
    assert linha["non_comparable_reason"] == dom.REASON_BRAND_WITHOUT_REFERENCE
    assert any("nao possui tabela de referencia B2B" in t
               for t in linha["limitations"])


def test_marca_com_tabela_e_produto_fora_dela_continua_reference_missing():
    """A contraprova: a marca TEM tabela e o produto nao esta nela.

    Sem este teste, o motivo novo poderia engolir o antigo e a tela deixaria de
    distinguir a pauta de Trade da pauta de cadastro.
    """
    s = SessaoFake(ofertas=[oferta(brand="barbours", seller_sku="NAO-EXISTE")],
                   referencias=[referencia(brand="barbours", source_sku="SKU-1")])
    linha = servir(s)["rows"][0]
    assert linha["non_comparable_reason"] == dom.REASON_REFERENCE_MISSING
    assert any("nenhuma linha de referencia casou" in t
               for t in linha["limitations"])


@pytest.mark.parametrize("tipo", [dom.PRODUCT_KIT_CONFIRMED,
                                  dom.PRODUCT_KIT_SUSPECTED])
def test_kit_sem_composicao_declara_kit_composition_missing(tipo):
    """O cabecalho de `pma_domain` promete este rotulo desde o PMA-2B-R2.

    Item 4: "Kits ... recebem `kit_composition_missing`". O serving nunca o
    emitiu, e 600 kits apareciam como produto ausente da tabela B2B.
    """
    s = SessaoFake(ofertas=[oferta(brand="barbours", seller_sku="KIT-1",
                                   product_type=tipo)],
                   referencias=[referencia(brand="barbours")])
    linha = servir(s)["rows"][0]
    assert linha["comparison_status"] == pm.STATUS_NO_REFERENCE
    assert linha["non_comparable_reason"] == dom.REASON_KIT_COMPOSITION_MISSING
    assert any("sem composicao confirmada" in t for t in linha["limitations"])
    assert any("Nenhuma composicao foi estimada" in t
               for t in linha["limitations"])


def test_oferta_sem_gtin_e_sem_sku_nao_e_busca_que_falhou():
    """Sem chave nao houve busca. Chamar isso de ausencia na tabela mandaria
    conferir a tabela quando o defeito esta no anuncio."""
    s = SessaoFake(ofertas=[oferta(brand="barbours", seller_sku=None, gtin=None)],
                   referencias=[referencia(brand="barbours")])
    linha = servir(s)["rows"][0]
    assert linha["non_comparable_reason"] == dom.REASON_NO_MATCH_KEY
    assert any("nao foi possivel" in t for t in linha["limitations"])


def test_sku_vazio_conta_como_ausencia_de_chave():
    """String vazia nao e' uma chave. `normalize_sku_key` ja' a reduz a None, e
    o motivo tem de acompanhar — senao a oferta cai em "produto ausente"."""
    s = SessaoFake(ofertas=[oferta(brand="barbours", seller_sku="   ", gtin=None)],
                   referencias=[referencia(brand="barbours")])
    assert servir(s)["rows"][0]["non_comparable_reason"] == dom.REASON_NO_MATCH_KEY


# ---------------------------------------------------------------------------
# 2. PRECEDENCIA — uma oferta com duas causas verdadeiras recebe UMA
# ---------------------------------------------------------------------------
def test_kit_de_marca_sem_tabela_declara_a_marca_nao_o_kit():
    """Bloqueio mais fundamental primeiro: registrar a BOM nao adiantaria nada
    enquanto a marca nao tiver tabela B2B."""
    s = SessaoFake(ofertas=[oferta(brand="lescent", seller_sku="KIT-9",
                                   product_type=dom.PRODUCT_KIT_CONFIRMED)],
                   referencias=[referencia(brand="barbours")])
    assert (servir(s)["rows"][0]["non_comparable_reason"]
            == dom.REASON_BRAND_WITHOUT_REFERENCE)


def test_kit_sem_chave_declara_o_kit_nao_a_chave():
    """Dar SKU a um kit nao o tornaria comparavel: falta a composicao."""
    s = SessaoFake(ofertas=[oferta(brand="barbours", seller_sku=None, gtin=None,
                                   product_type=dom.PRODUCT_KIT_SUSPECTED)],
                   referencias=[referencia(brand="barbours")])
    assert (servir(s)["rows"][0]["non_comparable_reason"]
            == dom.REASON_KIT_COMPOSITION_MISSING)


def test_ambiguidade_vence_todos_os_motivos_novos():
    """Ambiguidade e' achado, nao ausencia: ela tem de continuar sendo revisao
    humana na tabela de origem, nunca virar "marca sem tabela"."""
    s = SessaoFake(
        ofertas=[oferta(brand="barbours", seller_sku="DUP",
                        product_type=dom.PRODUCT_KIT_CONFIRMED)],
        referencias=[referencia(reference_row_id="R1", source_sku="DUP"),
                     referencia(reference_row_id="R2", source_sku="DUP")],
    )
    linha = servir(s)["rows"][0]
    assert linha["comparison_status"] == pm.STATUS_AMBIGUOUS
    assert linha["non_comparable_reason"] == dom.REASON_AMBIGUOUS


def test_inativo_nao_recebe_motivo_de_nao_comparabilidade():
    """Anuncio fora da vitrine nao tem causa de nao-comparacao a explicar: a
    causa e' nao existir vitrine, e isso ja' e' o status."""
    s = SessaoFake(ofertas=[oferta(brand="lescent", is_active=False)],
                   referencias=[referencia(brand="barbours")])
    linha = servir(s)["rows"][0]
    assert linha["comparison_status"] == pm.STATUS_INACTIVE
    assert linha["non_comparable_reason"] is None


# ---------------------------------------------------------------------------
# 3. CARDINALIDADE — um motivo por linha, sempre, sem orfa
# ---------------------------------------------------------------------------
def _cenario_completo():
    """Uma fotografia com todas as causas ao mesmo tempo."""
    return SessaoFake(
        ofertas=[
            oferta(offer_key="A", brand="barbours", seller_sku="SKU-1"),        # comparavel
            oferta(offer_key="B", brand="barbours", seller_sku="NAO-EXISTE"),   # ausente
            oferta(offer_key="C", brand="lescent", seller_sku="LC01"),          # marca
            oferta(offer_key="D", brand="barbours", seller_sku="KIT-1",
                   product_type=dom.PRODUCT_KIT_CONFIRMED),                     # kit
            oferta(offer_key="E", brand="barbours", seller_sku=None, gtin=None),  # sem chave
            oferta(offer_key="F", brand="barbours", seller_sku="SKU-1",
                   is_active=False),                                            # inativo
            oferta(offer_key="G", brand="gocase", seller_sku="GC1",
                   business_scope=dom.BUSINESS_SCOPE_OUT),                      # fora de escopo
        ],
        referencias=[referencia(brand="barbours", source_sku="SKU-1")],
    )


def test_toda_oferta_sem_referencia_carrega_exatamente_um_motivo_conhecido():
    linhas = servir(_cenario_completo())["rows"]
    sem_ref = [r for r in linhas
               if r["comparison_status"] == pm.STATUS_NO_REFERENCE]
    assert sem_ref, "o cenario precisa produzir linhas sem referencia"
    for r in sem_ref:
        assert r["non_comparable_reason"] in dom.NON_COMPARABLE_REASONS, (
            f"oferta {r['offer_key']} sem motivo atribuido")


def test_nenhum_motivo_e_emitido_fora_do_vocabulario():
    """Guarda contra um literal solto: se alguem introduzir um motivo novo sem
    registra-lo, `_contar_motivos` o descartaria em silencio e a soma quebraria
    sem dizer por que."""
    for r in servir(_cenario_completo())["rows"]:
        assert (r["non_comparable_reason"] is None
                or r["non_comparable_reason"] in dom.NON_COMPARABLE_REASONS)


def test_kit_composition_missing_e_o_mesmo_literal_nos_dois_vocabularios():
    """Duas grafias para o mesmo fato fariam a tela e a metrica discordarem."""
    assert dom.REASON_KIT_COMPOSITION_MISSING == dom.COMPARISON_KIT_MISSING


def test_vocabulario_de_motivos_nao_tem_duplicata():
    assert len(dom.NON_COMPARABLE_REASONS) == len(set(dom.NON_COMPARABLE_REASONS))


# ---------------------------------------------------------------------------
# 4. RECONCILIACAO — as duas somas, contra os dois totais
# ---------------------------------------------------------------------------
def test_breakdown_soma_exatamente_o_cartao_de_sem_referencia():
    saida = servir(_cenario_completo())
    brk = saida["metrics"]["no_reference_breakdown"]
    assert sum(brk.values()) == saida["kpis"]["no_reference_count"]


def test_motivos_fecham_a_particao_do_denominador():
    """`comparable + motivos == eligible`, a prova que o gate PMA-2C1A exige."""
    saida = servir(_cenario_completo())
    m = saida["metrics"]
    assert (m["comparable_offers"] + sum(m["non_comparable_reasons"].values())
            == m["eligible_offers"])


def test_breakdown_inclui_o_que_o_denominador_exclui():
    """O kit e a marca fora do escopo saem do elegivel e por isso NAO estao em
    `non_comparable_reasons`. Se tambem sumissem do breakdown, os 559 do TikTok
    voltariam a nao ter nome."""
    m = servir(_cenario_completo())["metrics"]
    assert m["no_reference_breakdown"][dom.REASON_KIT_COMPOSITION_MISSING] == 1
    assert m["non_comparable_reasons"][dom.REASON_KIT_COMPOSITION_MISSING] == 0
    assert sum(m["no_reference_breakdown"].values()) > sum(
        m["non_comparable_reasons"].values())


def test_particao_do_dominio_aceita_o_vocabulario_ampliado():
    """`assert_partition` soma sobre `NON_COMPARABLE_REASONS`: os motivos novos
    tem de contar la' tambem, senao a particao passaria a acusar falso."""
    dom.assert_partition(
        observed=10, active=8, inactive=2,
        product_type_counts={dom.PRODUCT_NO_KIT_SIGNAL: 6,
                             dom.PRODUCT_KIT_CONFIRMED: 2},
        eligible=6, excluded_by_product_type=2, comparable=3,
        reason_counts={dom.REASON_BRAND_WITHOUT_REFERENCE: 2,
                       dom.REASON_NO_MATCH_KEY: 1},
    )


# ---------------------------------------------------------------------------
# 5. O MERCADO LIVRE NAO MUDA DE COMPORTAMENTO
# ---------------------------------------------------------------------------
def _listing_ml(**over) -> dict:
    base = {
        "ref_date": date(2026, 9, 15), "marketplace": "ml", "brand": "barbours",
        "item_id": "MLB1", "seller_sku": "SKU-1", "gtin": None,
        "listing_title": "Shampoo 300ml",
        "permalink": "https://produto.mercadolivre.com.br/MLB-1-x-_JM",
        "advertised_price": Decimal("50.00"), "original_price": None,
        "currency": "BRL", "listing_status": "active", "catalog_listing": False,
        "price_captured_at": datetime(2026, 9, 15, 12, 0),
        "listing_metadata_updated_at": None,
        "synced_at": datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc),
    }
    base.update(over)
    return base


def test_ml_sem_product_type_na_fato_nunca_recebe_motivo_de_kit():
    """A fato do ML nao modela `product_type`. Ausente tem de se comportar como
    hoje — nunca como kit —, ou o ML herdaria uma classificacao que ninguem
    mediu para ele."""
    s = SessaoFake(listings_ml=[_listing_ml(seller_sku="NAO-EXISTE")],
                   referencias=[referencia(brand="barbours", source_sku="SKU-1")])
    linha = mp.get_monitoramento_preco(s, marketplace="ml", today=HOJE)["rows"][0]
    assert linha["non_comparable_reason"] == dom.REASON_REFERENCE_MISSING


def test_ml_mantem_o_texto_publicado_para_marca_sem_tabela():
    """O texto e' contrato publicado. O que mudou foi a AUTORIDADE que decide
    quando emiti-lo — antes a lista fixa `NO_REFERENCE_BRANDS`, agora as marcas
    do snapshot em maos —, e no ML as duas coincidem."""
    s = SessaoFake(listings_ml=[_listing_ml(brand="lescent", seller_sku="LC01")],
                   referencias=[referencia(brand="barbours", source_sku="SKU-1")])
    linha = mp.get_monitoramento_preco(s, marketplace="ml", today=HOJE)["rows"][0]
    assert linha["non_comparable_reason"] == dom.REASON_BRAND_WITHOUT_REFERENCE
    assert "marca lescent nao possui tabela de referencia B2B" in linha["limitations"]


# ---------------------------------------------------------------------------
# 6. A AUTORIDADE E' O SNAPSHOT CARREGADO, NAO UMA CONSTANTE
# ---------------------------------------------------------------------------
def test_index_so_conta_marca_que_contribuiu_com_linha_indexavel():
    """Uma marca cujas linhas foram todas descartadas por falta de PDV nao tem
    referencia utilizavel. Conta-la faria a oferta cair em "produto ausente da
    tabela" quando a tabela e' que esta vazia para ela."""
    idx = pm.ReferenceIndex.build([
        referencia(brand="barbours", source_sku="SKU-1"),
        referencia(brand="kokeshi", source_sku="KS-1",
                   suggested_retail_amount=None,
                   quality_status=pm.QUALITY_MISSING_PRICE),
    ])
    assert idx.brands == frozenset({"barbours"})


def test_marca_da_referencia_e_normalizada_antes_de_entrar_no_index():
    """A oferta chega com a marca normalizada pelo publisher. Se o index
    guardasse a grafia crua, 'Barbours' e 'barbours' seriam marcas diferentes e
    toda oferta da marca viraria "marca sem tabela"."""
    idx = pm.ReferenceIndex.build([referencia(brand="  Barbours  ")])
    assert idx.brands == frozenset({"barbours"})
