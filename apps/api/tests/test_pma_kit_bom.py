"""Gate KITS-MAP-2 — a composicao de kit so' vira preco quando esta provada.

O que estes testes travam, em uma frase: a BOM e' recusada inteira quando se
contradiz, so' ponte exata e da mesma marca promove, e um componente sem
referencia inequivoca zera a referencia do KIT em vez de encolhe-la.

POR QUE CADA TRAVA EXISTE
-------------------------
**Duplicata.** `KBB99170|BB02030` aparece DUAS vezes em
`raw.protheus_kit_components`, com a mesma quantidade, e a mesma duplicata
atravessou para `gold.bridge_kit_componente_gobeauty` (medido em 2026-09-25:
4.691 linhas / 4.690 pares na raw; 4.549 / 4.548 na gold). Somar as duas daria
2 unidades onde a fonte quis dizer 1 — e, num kit de 1 SKU, isso muda a faixa
de desconto de 0% para 5%. Colapsar e' a unica leitura que nao inventa produto.

**Quantidade conflitante.** Duplicata com quantidades DIFERENTES nao tem
leitura certa. Escolher uma seria decidir por ordem de chegada; o contrato
recusa a carga e nomeia o par.

**Unidades e nao SKUs.** 85 dos 1.516 kits ativos tem numero de SKUs diferente
do numero de unidades, e 82 deles mudam de faixa de desconto conforme a leitura.
O teste `test_faixa_olha_unidades_nao_skus` fixa o lado certo.

**Fallback por `(marca, source_sku)`.** Quatro dos nove componentes dos seis
kits Kokeshi medidos tem, na planilha B2B, o EAN da geracao ANTIGA de codigo:
`KS03042` guarda `7899459312597` na referencia e `7908790700137` no
`dim_produto`. So' com EAN, **nenhum** dos seis kits tem preco. Com o fallback,
os seis tem. O teste `test_sem_fallback_nenhum_dos_seis_existe` mede essa
diferenca em vez de afirma-la.

**Incompatibilidade.** Se o EAN resolve para uma linha e o SKU para OUTRA, o
componente e' recusado. Nao e' empate a ser desfeito pela precedencia: e' um
defeito de cadastro, e preferir o EAN esconderia o defeito com um numero.

OS SEIS VALORES
---------------
Reconferidos nominalmente contra a fotografia de 2026-09-25 (snapshot de
referencia `pma-ref:20260923T193400Z`, 247 linhas):
R$ 142,38 / 93,33 / 88,16 / 75,81 / 65,36 / 53,01.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services import pma_kit_bom as kb
from app.services.pma_match import ReferenceIndex

HOJE = date(2026, 9, 25)


# ---------------------------------------------------------------- fixtures

def _bom_row(kit, comp, qty=1, **extra):
    linha = {"kit_sku": kit, "component_sku": comp, "qty_per_kit": qty,
             "active": True, "valid_from": None, "valid_to": None}
    linha.update(extra)
    return linha


#: Os nove componentes reais dos seis kits Kokeshi, com o preco sugerido e o
#: GTIN EXATAMENTE como a planilha B2B os traz. Quatro deles (`KS03016`,
#: `KS03019`, `KS03020`, `KS03042`) trazem o EAN da geracao antiga.
REFERENCIA_KOKESHI = [
    {"reference_row_id": "r-KS03015", "brand": "kokeshi", "source_sku": "KS03015",
     "source_gtin": "7908790700076", "suggested_retail_amount": Decimal("29.90")},
    {"reference_row_id": "r-KS03016", "brand": "kokeshi", "source_sku": "KS03016",
     "source_gtin": "7899459300204", "suggested_retail_amount": Decimal("25.90")},
    {"reference_row_id": "r-KS03019", "brand": "kokeshi", "source_sku": "KS03019",
     "source_gtin": "7899459304776", "suggested_retail_amount": Decimal("42.90")},
    {"reference_row_id": "r-KS03020", "brand": "kokeshi", "source_sku": "KS03020",
     "source_gtin": "7899459305230", "suggested_retail_amount": Decimal("49.90")},
    {"reference_row_id": "r-KS02007", "brand": "kokeshi", "source_sku": "KS02007",
     "source_gtin": "7908790700106", "suggested_retail_amount": Decimal("32.90")},
    {"reference_row_id": "r-KS03042", "brand": "kokeshi", "source_sku": "KS03042",
     "source_gtin": "7899459312597", "suggested_retail_amount": Decimal("32.90")},
    {"reference_row_id": "r-KS03043", "brand": "kokeshi", "source_sku": "KS03043",
     "source_gtin": "7908790700120", "suggested_retail_amount": Decimal("35.90")},
    {"reference_row_id": "r-KS03044", "brand": "kokeshi", "source_sku": "KS03044",
     "source_gtin": "7908790700175", "suggested_retail_amount": Decimal("34.90")},
    {"reference_row_id": "r-KS03046", "brand": "kokeshi", "source_sku": "KS03046",
     "source_gtin": "7908790700830", "suggested_retail_amount": Decimal("45.90")},
]

#: O EAN que o `dim_produto` guarda para cada um. Divergente da referencia em
#: quatro casos — e' exatamente essa divergencia que o fallback cobre.
EAN_INTERNO = {
    "KS03015": "7908790700076",
    "KS03016": "7908790700090",
    "KS03019": "7908790700199",
    "KS03020": "7908790700205",
    "KS02007": "7908790700106",
    "KS03042": "7908790700137",
    "KS03043": "7908790700120",
    "KS03044": "7908790700175",
    "KS03046": "7908790700830",
}

#: Os seis kits, com a composicao que `raw.protheus_kit_components` publica.
KITS_KOKESHI = {
    "KKS00006": ["KS02007", "KS03015", "KS03016", "KS03042", "KS03046"],
    "KKS00032": ["KS03042", "KS03043", "KS03044"],
    "KKS00017": ["KS03019", "KS03020"],
    "KKS00010": ["KS03015", "KS03020"],
    "KKS00008": ["KS03016", "KS03019"],
    "KKS00015": ["KS03015", "KS03016"],
}

VALORES_ESPERADOS = {
    "KKS00006": Decimal("142.38"),
    "KKS00032": Decimal("93.33"),
    "KKS00017": Decimal("88.16"),
    "KKS00010": Decimal("75.81"),
    "KKS00008": Decimal("65.36"),
    "KKS00015": Decimal("53.01"),
}


@pytest.fixture
def indice_ref():
    return ReferenceIndex.build(list(REFERENCIA_KOKESHI))


@pytest.fixture
def catalogo():
    return kb.ComponentCatalog.build([
        {"component_sku": sku, "brand": "kokeshi", "ean": ean}
        for sku, ean in EAN_INTERNO.items()
    ])


@pytest.fixture
def bom_kokeshi():
    linhas = [_bom_row(kit, comp)
              for kit, comps in KITS_KOKESHI.items() for comp in comps]
    return kb.KitBomIndex.build(linhas, today=HOJE)


def _ponte(kit, status=kb.BRIDGE_EXACT_ALIAS, brand="kokeshi", kit_brand="kokeshi"):
    return kb.KitBridge(kit_sku=kit, status=status, brand=brand, kit_brand=kit_brand)


# ------------------------------------------------- 1. contrato da BOM

def test_duplicata_identica_colapsa_e_nao_soma():
    """`KBB99170|BB02030`: a fonte repete o par; o contrato conta 1 unidade."""
    bom = kb.KitBomIndex.build(
        [_bom_row("KBB99170", "BB02030", 1), _bom_row("KBB99170", "BB02030", 1)],
        today=HOJE)
    comps = bom.components("KBB99170")
    assert len(comps) == 1
    assert comps[0].qty_per_kit == Decimal(1)
    assert bom.units("KBB99170") == Decimal(1)
    assert ("KBB99170", "BB02030") in bom.collapsed_duplicates


def test_duplicata_colapsada_nao_muda_faixa_de_desconto():
    """Somar a duplicata levaria 1 unidade a 2 — e 0% de desconto a 5%."""
    bom = kb.KitBomIndex.build(
        [_bom_row("KBB99170", "BB02030", 1), _bom_row("KBB99170", "BB02030", 1)],
        today=HOJE)
    assert kb.discount_for_units(bom.units("KBB99170")) == Decimal(0)
    assert kb.discount_for_units(Decimal(2)) == Decimal("0.05")


def test_quantidade_conflitante_recusa_a_carga():
    with pytest.raises(kb.BomContractError) as exc:
        kb.KitBomIndex.build(
            [_bom_row("KBB99170", "BB02030", 1), _bom_row("KBB99170", "BB02030", 2)],
            today=HOJE)
    assert "KBB99170|BB02030" in str(exc.value)


@pytest.mark.parametrize("qty", [0, -1, None, "NaN", "Infinity"])
def test_quantidade_nao_positiva_recusa(qty):
    with pytest.raises(kb.BomContractError):
        kb.KitBomIndex.build([_bom_row("K1", "C1", qty)], today=HOJE)


def test_autorreferencia_recusa():
    with pytest.raises(kb.BomContractError) as exc:
        kb.KitBomIndex.build([_bom_row("K1", "K1", 1)], today=HOJE)
    assert "autorreferencia" in str(exc.value)


def test_ciclo_recusa():
    with pytest.raises(kb.BomContractError) as exc:
        kb.KitBomIndex.build(
            [_bom_row("A", "B"), _bom_row("B", "C"), _bom_row("C", "A")],
            today=HOJE)
    assert "ciclo" in str(exc.value)


def test_bom_de_dois_niveis_sem_ciclo_e_aceita():
    """Componente que tambem e' kit nao e' defeito — ciclo e' que e'."""
    bom = kb.KitBomIndex.build(
        [_bom_row("A", "B"), _bom_row("B", "C")], today=HOJE)
    assert bom.components("A")[0].component_sku == "B"
    assert bom.components("B")[0].component_sku == "C"


def test_inativa_nao_entra():
    bom = kb.KitBomIndex.build([_bom_row("K1", "C1", active=False)], today=HOJE)
    assert bom.components("K1") == ()


def test_fora_de_vigencia_nao_entra():
    futura = _bom_row("K1", "C1", valid_from=date(2026, 11, 24))
    vencida = _bom_row("K2", "C2", valid_to=date(2026, 1, 1))
    bom = kb.KitBomIndex.build([futura, vencida], today=HOJE)
    assert bom.components("K1") == ()
    assert bom.components("K2") == ()


def test_bordas_nulas_sao_abertas():
    """1.850 das 4.691 linhas da fonte tem `valid_from` nulo. Nulo nao exclui."""
    bom = kb.KitBomIndex.build(
        [_bom_row("K1", "C1", valid_from=None, valid_to=None)], today=HOJE)
    assert len(bom.components("K1")) == 1


def test_quantidade_e_preservada():
    bom = kb.KitBomIndex.build([_bom_row("K1", "C1", 3)], today=HOJE)
    assert bom.components("K1")[0].qty_per_kit == Decimal(3)


def test_relacao_sem_chave_recusa():
    with pytest.raises(kb.BomContractError):
        kb.KitBomIndex.build([_bom_row("K1", None)], today=HOJE)


def test_teto_recusa_em_vez_de_truncar(monkeypatch):
    monkeypatch.setattr(kb, "MAX_BOM_ROWS", 2)
    with pytest.raises(kb.BomContractError) as exc:
        kb.KitBomIndex.build(
            [_bom_row("K1", "A"), _bom_row("K1", "B"), _bom_row("K1", "C")],
            today=HOJE)
    assert "teto" in str(exc.value)


# ------------------------------------------------- 2. promocao da ponte

@pytest.mark.parametrize("status", [kb.BRIDGE_EXACT_DIRECT, kb.BRIDGE_EXACT_ALIAS])
def test_ponte_exata_da_mesma_marca_promove(status):
    pode, motivo = kb.is_promotable(_ponte("KKS00008", status))
    assert pode and motivo is None


@pytest.mark.parametrize("status", [
    kb.BRIDGE_CANDIDATE_REVIEW, kb.BRIDGE_AMBIGUOUS,
    kb.BRIDGE_CROSS_BRAND_CONFLICT, kb.BRIDGE_UNMAPPED,
])
def test_status_bloqueado_nao_promove(status):
    pode, motivo = kb.is_promotable(_ponte("KKS00008", status))
    assert not pode
    assert motivo == kb.REASON_BRIDGE_NOT_PROMOTABLE


def test_status_desconhecido_e_bloqueado_por_omissao():
    """Allowlist: status novo nasce bloqueado, nao liberado."""
    pode, motivo = kb.is_promotable(_ponte("KKS00008", "EXACT_ALGUMA_COISA_NOVA"))
    assert not pode
    assert motivo == kb.REASON_BRIDGE_NOT_PROMOTABLE


def test_alias_com_marca_divergente_nao_promove():
    ponte = kb.KitBridge(kit_sku="KAP99018", status=kb.BRIDGE_EXACT_ALIAS,
                         brand="kokeshi", kit_brand="apice")
    pode, motivo = kb.is_promotable(ponte)
    assert not pode
    assert motivo == kb.REASON_BRIDGE_BRAND_MISMATCH


def test_os_sete_kokeshi_40125_40131_seguem_bloqueados(bom_kokeshi, catalogo,
                                                       indice_ref):
    """BOM cadastrada sob `apice` num anuncio Kokeshi: conflito nao promove.

    Enquanto a divergencia nao for resolvida CADASTRALMENTE, nenhum dos sete
    pode virar preco — nem pelo status, nem pela marca.
    """
    for sku_canal in ("40125", "40126", "40127", "40128",
                      "40129", "40130", "40131"):
        ponte = kb.KitBridge(kit_sku=sku_canal,
                             status=kb.BRIDGE_CROSS_BRAND_CONFLICT,
                             brand="kokeshi", kit_brand="apice")
        r = kb.resolve_kit_reference(ponte, bom=bom_kokeshi, catalog=catalogo,
                                     index=indice_ref)
        assert r.amount is None
        assert r.reason == kb.REASON_BRIDGE_NOT_PROMOTABLE


def test_ponte_sem_kit_sku_nao_promove():
    pode, motivo = kb.is_promotable(
        kb.KitBridge(kit_sku=None, status=kb.BRIDGE_EXACT_DIRECT))
    assert not pode


def test_allowlist_tem_exatamente_dois_status():
    assert set(kb.PROMOTABLE_BRIDGE_STATUSES) == {
        kb.BRIDGE_EXACT_DIRECT, kb.BRIDGE_EXACT_ALIAS}
    assert set(kb.PROMOTABLE_BRIDGE_STATUSES) <= set(kb.BRIDGE_STATUSES)


# ------------------------------------------------- 2b. resolvedor de ponte

@pytest.fixture
def indice_ponte():
    return kb.KitBridgeIndex.build(
        brand_code_rows=[{"brand": "kokeshi", "code": "KKS00008",
                          "protheus_sku": "KKS00008"}],
        alias_rows=[{"code": "40010", "protheus_sku": "KKS00008"},
                    {"code": "40019", "protheus_sku": "KKS00015"}],
        kit_brand_rows=[{"protheus_sku": k, "brand": "kokeshi"}
                        for k in KITS_KOKESHI],
    )


def test_ponte_sku_que_ja_e_kit_protheus(bom_kokeshi, indice_ponte):
    p = kb.resolve_bridge("KKS00008", "kokeshi", bom=bom_kokeshi, index=indice_ponte)
    assert p.status == kb.BRIDGE_EXACT_DIRECT
    assert p.method == kb.BRIDGE_METHOD_SKU_IS_KIT


def test_ponte_por_alias(bom_kokeshi, indice_ponte):
    p = kb.resolve_bridge("40010", "kokeshi", bom=bom_kokeshi, index=indice_ponte)
    assert p.status == kb.BRIDGE_EXACT_ALIAS
    assert p.kit_sku == "KKS00008"


def test_ponte_com_marca_divergente_vira_conflito(bom_kokeshi, indice_ponte):
    p = kb.resolve_bridge("40010", "apice", bom=bom_kokeshi, index=indice_ponte)
    assert p.status == kb.BRIDGE_CROSS_BRAND_CONFLICT
    assert kb.is_promotable(p)[0] is False


def test_ponte_com_dois_alvos_e_ambigua(bom_kokeshi):
    idx = kb.KitBridgeIndex.build(
        alias_rows=[{"code": "X", "protheus_sku": "KKS00008"},
                    {"code": "X", "protheus_sku": "KKS00015"}],
        kit_brand_rows=[{"protheus_sku": k, "brand": "kokeshi"}
                        for k in KITS_KOKESHI])
    p = kb.resolve_bridge("X", "kokeshi", bom=bom_kokeshi, index=idx)
    assert p.status == kb.BRIDGE_AMBIGUOUS
    assert kb.is_promotable(p)[0] is False


def test_alvo_fora_da_bom_nao_vira_ponte(bom_kokeshi):
    """Alias apontando para kit sem composicao vigente nao e' ponte util."""
    idx = kb.KitBridgeIndex.build(
        alias_rows=[{"code": "Y", "protheus_sku": "KIT_SEM_BOM"}])
    p = kb.resolve_bridge("Y", "kokeshi", bom=bom_kokeshi, index=idx)
    assert p.status == kb.BRIDGE_UNMAPPED


def test_marca_do_kit_desconhecida_nao_promove(bom_kokeshi):
    """Ausencia de prova bloqueia. Isto e' o fail-closed do gate.

    `40126`-`40129` sao chaves de kit de `apice_sheet`, em codigo Bling,
    anunciadas em loja Kokeshi: o codigo casa e a marca do kit nao existe em
    lugar nenhum. Antes do fecho elas passavam por `EXACT_DIRECT` e so' nao
    viravam preco porque os componentes faltavam no catalogo.
    """
    bom = kb.KitBomIndex.build(
        [_bom_row("40126", "20021"), _bom_row("40126", "20684"),
         _bom_row("40126", "20696")], today=HOJE)
    idx = kb.KitBridgeIndex.build(kit_brand_rows=[])  # nenhuma marca conhecida
    p = kb.resolve_bridge("40126", "kokeshi", bom=bom, index=idx)
    assert p.kit_sku == "40126"
    assert p.status == kb.BRIDGE_CANDIDATE_REVIEW
    assert kb.is_promotable(p) == (False, kb.REASON_BRIDGE_NOT_PROMOTABLE)


def test_marca_da_oferta_ausente_nao_promove(bom_kokeshi, indice_ponte):
    p = kb.resolve_bridge("40010", None, bom=bom_kokeshi, index=indice_ponte)
    assert p.status == kb.BRIDGE_CANDIDATE_REVIEW
    assert kb.is_promotable(p)[0] is False


@pytest.mark.parametrize("oferta,kit", [(None, "kokeshi"), ("kokeshi", None),
                                        (None, None)])
def test_is_promotable_exige_as_duas_marcas(oferta, kit):
    p = kb.KitBridge("KKS00008", kb.BRIDGE_EXACT_DIRECT, oferta, kit)
    pode, motivo = kb.is_promotable(p)
    assert not pode
    assert motivo == kb.REASON_BRIDGE_BRAND_MISMATCH


def test_resolvedor_so_promove_com_marca_provada(bom_kokeshi, indice_ponte):
    entradas = [("KKS00008", "kokeshi"), ("40010", "apice"), ("ZZZ", "kokeshi"),
                (None, "kokeshi"), ("40019", None), ("40010", "kokeshi")]
    for sku, marca in entradas:
        p = kb.resolve_bridge(sku, marca, bom=bom_kokeshi, index=indice_ponte)
        assert p.status in kb.BRIDGE_STATUSES
        if kb.is_promotable(p)[0]:
            assert p.brand is not None and p.kit_brand == p.brand


# ------------------------------------------------- 3. referencia do componente

def test_ean_tem_precedencia(indice_ref, catalogo):
    r = kb.resolve_component_reference("KS03015", Decimal(1),
                                       identity=catalogo.get("KS03015"),
                                       index=indice_ref)
    assert r.method == kb.COMPONENT_MATCH_EAN
    assert r.amount == Decimal("29.90")


def test_fallback_por_marca_e_sku_quando_o_ean_nao_casa(indice_ref, catalogo):
    """`KS03042`: EAN interno `...0137`, EAN da referencia `...2597`."""
    r = kb.resolve_component_reference("KS03042", Decimal(1),
                                       identity=catalogo.get("KS03042"),
                                       index=indice_ref)
    assert r.method == kb.COMPONENT_MATCH_SKU
    assert r.amount == Decimal("32.90")


def test_ean_ambiguo_recusa_e_nao_cai_para_o_sku():
    """Ambiguidade nao escorrega para a chave seguinte."""
    refs = [
        {"reference_row_id": "a", "brand": "rituaria", "source_sku": "X1",
         "source_gtin": "7901128300047", "suggested_retail_amount": Decimal("109.90")},
        {"reference_row_id": "b", "brand": "rituaria", "source_sku": "X2",
         "source_gtin": "7901128300047", "suggested_retail_amount": Decimal("109.01")},
    ]
    idx = ReferenceIndex.build(refs)
    cat = kb.ComponentCatalog.build(
        [{"component_sku": "X1", "brand": "rituaria", "ean": "7901128300047"}])
    r = kb.resolve_component_reference("X1", Decimal(1),
                                       identity=cat.get("X1"), index=idx)
    assert r.amount is None
    assert r.reason == kb.REASON_COMPONENT_REFERENCE_AMBIGUOUS


def test_sku_ambiguo_recusa():
    refs = [
        {"reference_row_id": "a", "brand": "kokeshi", "source_sku": "DUP",
         "source_gtin": None, "suggested_retail_amount": Decimal("10.00")},
        {"reference_row_id": "b", "brand": "kokeshi", "source_sku": "DUP",
         "source_gtin": None, "suggested_retail_amount": Decimal("20.00")},
    ]
    idx = ReferenceIndex.build(refs)
    cat = kb.ComponentCatalog.build([{"component_sku": "DUP", "brand": "kokeshi"}])
    r = kb.resolve_component_reference("DUP", Decimal(1),
                                       identity=cat.get("DUP"), index=idx)
    assert r.reason == kb.REASON_COMPONENT_REFERENCE_AMBIGUOUS


def test_ean_e_sku_apontando_para_linhas_diferentes_recusa():
    """Produtos incompativeis: o EAN diz um, o SKU diz outro."""
    refs = [
        {"reference_row_id": "pelo-ean", "brand": "kokeshi", "source_sku": "OUTRO",
         "source_gtin": "7908790700076", "suggested_retail_amount": Decimal("29.90")},
        {"reference_row_id": "pelo-sku", "brand": "kokeshi", "source_sku": "ALVO",
         "source_gtin": "7899459300204", "suggested_retail_amount": Decimal("99.90")},
    ]
    idx = ReferenceIndex.build(refs)
    cat = kb.ComponentCatalog.build(
        [{"component_sku": "ALVO", "brand": "kokeshi", "ean": "7908790700076"}])
    r = kb.resolve_component_reference("ALVO", Decimal(1),
                                       identity=cat.get("ALVO"), index=idx)
    assert r.amount is None
    assert r.reason == kb.REASON_COMPONENT_REFERENCE_INCOMPATIBLE


def test_ean_e_sku_na_mesma_linha_resolve(indice_ref, catalogo):
    """`KS03015` casa pelas DUAS chaves, na mesma linha. Isso nao e' conflito."""
    r = kb.resolve_component_reference("KS03015", Decimal(1),
                                       identity=catalogo.get("KS03015"),
                                       index=indice_ref)
    assert r.amount == Decimal("29.90")


def test_marca_ausente_recusa(indice_ref):
    cat = kb.ComponentCatalog.build([{"component_sku": "KS03015", "brand": None}])
    r = kb.resolve_component_reference("KS03015", Decimal(1),
                                       identity=cat.get("KS03015"),
                                       index=indice_ref)
    assert r.reason == kb.REASON_COMPONENT_BRAND_MISSING


def test_marca_conflitante_declarada_recusa(indice_ref):
    """`dim_produto.marca_conflitante` ligada: 21 dos 4.705 produtos."""
    cat = kb.ComponentCatalog.build([
        {"component_sku": "KS03015", "brand": "kokeshi",
         "ean": "7908790700076", "marca_conflitante": True}])
    r = kb.resolve_component_reference("KS03015", Decimal(1),
                                       identity=cat.get("KS03015"),
                                       index=indice_ref)
    assert r.reason == kb.REASON_COMPONENT_BRAND_CONFLICT


def test_duas_identidades_divergentes_viram_conflito(indice_ref):
    cat = kb.ComponentCatalog.build([
        {"component_sku": "KS03015", "brand": "kokeshi"},
        {"component_sku": "KS03015", "brand": "by samia"},
    ])
    assert cat.get("KS03015").brand_conflict is True


def test_componente_fora_do_catalogo_recusa(indice_ref):
    cat = kb.ComponentCatalog.build([])
    r = kb.resolve_component_reference("KS03015", Decimal(1),
                                       identity=cat.get("KS03015"),
                                       index=indice_ref)
    assert r.reason == kb.REASON_COMPONENT_NOT_IN_CATALOG


def test_marca_divergente_entre_componente_e_referencia_nao_casa(indice_ref):
    """A chave carrega a marca: `(apice, KS03015)` nao alcanca a linha kokeshi."""
    cat = kb.ComponentCatalog.build(
        [{"component_sku": "KS03015", "brand": "apice", "ean": "7908790700076"}])
    r = kb.resolve_component_reference("KS03015", Decimal(1),
                                       identity=cat.get("KS03015"),
                                       index=indice_ref)
    assert r.reason == kb.REASON_COMPONENT_REFERENCE_MISSING


# ------------------------------------------------- 4. referencia do kit

@pytest.mark.parametrize("kit,esperado", sorted(VALORES_ESPERADOS.items()))
def test_os_seis_valores_medidos(kit, esperado, bom_kokeshi, catalogo, indice_ref):
    r = kb.resolve_kit_reference(_ponte(kit), bom=bom_kokeshi,
                                 catalog=catalogo, index=indice_ref)
    assert r.reason is None, r.reason
    assert r.amount == esperado


def test_kks00006_detalhado(bom_kokeshi, catalogo, indice_ref):
    """O unico dos seis que cai na faixa de 15%, e o que arredonda meio centavo."""
    r = kb.resolve_kit_reference(_ponte("KKS00006"), bom=bom_kokeshi,
                                 catalog=catalogo, index=indice_ref)
    assert r.component_count == 5
    assert r.units == Decimal(5)
    assert r.discount_pct == Decimal("0.15")
    assert r.components_base == Decimal("167.50")
    # 167.50 * 0.85 = 142.375 -> ROUND_HALF_UP -> 142.38
    assert r.amount == Decimal("142.38")


def test_sem_fallback_nenhum_dos_seis_existe(bom_kokeshi, catalogo):
    """Mede o impacto do fallback em vez de afirma-lo.

    Indice montado SO' com a chave de EAN: quatro dos nove componentes ficam
    sem referencia, e isso derruba os seis kits.
    """
    idx = ReferenceIndex.build(list(REFERENCIA_KOKESHI))
    so_ean = ReferenceIndex(idx.by_gtin, {}, idx.captured_at, idx.brands)
    for kit in VALORES_ESPERADOS:
        r = kb.resolve_kit_reference(_ponte(kit), bom=bom_kokeshi,
                                     catalog=catalogo, index=so_ean)
        assert r.amount is None
        assert r.reason == kb.REASON_COMPONENT_REFERENCE_MISSING


def test_faixa_olha_unidades_nao_skus(indice_ref):
    """1 SKU x 3 unidades cai em -10%. Por SKUs distintos cairia em 0%."""
    bom = kb.KitBomIndex.build([_bom_row("K3", "KS03015", 3)], today=HOJE)
    cat = kb.ComponentCatalog.build(
        [{"component_sku": "KS03015", "brand": "kokeshi", "ean": "7908790700076"}])
    r = kb.resolve_kit_reference(
        kb.KitBridge("K3", kb.BRIDGE_EXACT_DIRECT, "kokeshi", "kokeshi"),
        bom=bom, catalog=cat, index=indice_ref)
    assert r.units == Decimal(3)
    assert r.discount_pct == Decimal("0.10")
    assert r.components_base == Decimal("89.70")
    assert r.amount == Decimal("80.73")


@pytest.mark.parametrize("unidades,pct", [
    (Decimal(1), Decimal(0)), (Decimal(2), Decimal("0.05")),
    (Decimal(3), Decimal("0.10")), (Decimal(4), Decimal("0.15")),
    (Decimal(9), Decimal("0.15")),
])
def test_faixas_de_desconto(unidades, pct):
    assert kb.discount_for_units(unidades) == pct


def test_componente_sem_referencia_zera_o_kit_inteiro(bom_kokeshi, indice_ref):
    """Nunca encolhe: um componente ausente vira `None`, nao um kit mais barato."""
    cat = kb.ComponentCatalog.build([
        {"component_sku": sku, "brand": "kokeshi", "ean": ean}
        for sku, ean in EAN_INTERNO.items() if sku != "KS03046"
    ])
    r = kb.resolve_kit_reference(_ponte("KKS00006"), bom=bom_kokeshi,
                                 catalog=cat, index=indice_ref)
    assert r.amount is None
    assert r.components_base is None
    assert r.reason == kb.REASON_COMPONENT_NOT_IN_CATALOG
    # as unidades continuam medidas: o que falta e' o preco, nao a composicao
    assert r.units == Decimal(5)


def test_kit_sem_bom_declara_o_motivo(catalogo, indice_ref):
    bom = kb.KitBomIndex.build([], today=HOJE)
    r = kb.resolve_kit_reference(_ponte("KKS00008"), bom=bom,
                                 catalog=catalogo, index=indice_ref)
    assert r.amount is None
    assert r.reason == kb.REASON_BOM_ABSENT


def test_nulo_nunca_vira_zero(bom_kokeshi, indice_ref):
    cat = kb.ComponentCatalog.build([])
    r = kb.resolve_kit_reference(_ponte("KKS00008"), bom=bom_kokeshi,
                                 catalog=cat, index=indice_ref)
    assert r.amount is None
    assert r.amount != Decimal(0)


def test_todo_none_carrega_motivo_do_vocabulario(bom_kokeshi, catalogo, indice_ref):
    pontes = [
        _ponte("KKS00008", kb.BRIDGE_CANDIDATE_REVIEW),
        _ponte("KKS00008", kb.BRIDGE_UNMAPPED),
        _ponte("NAO_EXISTE", kb.BRIDGE_EXACT_DIRECT),
        kb.KitBridge("KKS00008", kb.BRIDGE_EXACT_ALIAS, "kokeshi", "apice"),
    ]
    for r in kb.resolve_many(pontes, bom=bom_kokeshi, catalog=catalogo,
                             index=indice_ref):
        assert r.amount is None
        assert r.reason in kb.KIT_REFERENCE_REASONS


def test_resolvido_nunca_traz_motivo(bom_kokeshi, catalogo, indice_ref):
    r = kb.resolve_kit_reference(_ponte("KKS00015"), bom=bom_kokeshi,
                                 catalog=catalogo, index=indice_ref)
    assert r.amount is not None and r.reason is None
    assert all(c.reason is None for c in r.components)
