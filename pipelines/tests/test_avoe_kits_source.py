"""T-0053 — BOM de kits vinda da Avoe, consumida pelo contrato `pma_kit_bom`.

Sem rede e sem banco. Todos os SKUs, EANs e valores sao SINTETICOS: o formato
imita a resposta de `consultar_kit`, os numeros nao sao de negocio.

O que se trava aqui:
- a chave do kit e' escopada por MARCA (`KIT001` existe em varias marcas);
- o componente so' vira SKU Protheus por `(marca, codigo)` EXATO e UNICO —
  nunca por EAN, nunca por codigo de outra marca;
- sem traducao, o componente fica com chave propria e o contrato BLOQUEIA o
  kit com motivo; nada e' inventado nem descartado;
- a quantidade e' a repeticao do componente na lista;
- `--source protheus` (padrao) nao muda nada.
"""
from __future__ import annotations

import json
import urllib.error
from datetime import date
from decimal import Decimal

import pytest

from pipelines import avoe_kits_source as A
from pipelines import kit_reference_publisher as P

#: `kit_reference_publisher` poe `apps/api` no `sys.path` ao ser importado; por
#: isso este import vem DEPOIS dele (mesma convencao do teste do publisher).
from app.services import pma_kit_bom as kb  # noqa: E402,I001

HOJE = date(2030, 1, 15)


def _kit(marca, sku, *componentes, ean=None):
    return {
        "marca": marca, "sku_kit": sku, "nome": f"kit {sku}",
        "categoria": None, "preco_custo": None, "preco_venda": None,
        "componentes": [{"sku": c, "ean": ean, "nome": f"item {c}"}
                        for c in componentes],
        "precos_por_canal": [],
    }


#: Ponte cadastral sintetica `(marca, codigo) -> SKU Protheus`, no formato de
#: `SQL_BRIDGE_BRAND_CODE`. O codigo `900` existe nas DUAS marcas, apontando
#: para produtos diferentes — o caso de colisao entre ERPs.
PONTE = [
    {"brand": "marca_a", "code": "900", "protheus_sku": "PA0001"},
    {"brand": "marca_a", "code": "901", "protheus_sku": "PA0002"},
    {"brand": "lescent", "code": "900", "protheus_sku": "PL0001"},
    {"brand": "lescent", "code": "901", "protheus_sku": "PL0002"},
    {"brand": "lescent", "code": "PL0003", "protheus_sku": "PL0003"},
    # Codigo com duas traducoes: ambiguo, nunca desempatado.
    {"brand": "lescent", "code": "905", "protheus_sku": "PL0005"},
    {"brand": "lescent", "code": "905", "protheus_sku": "PL0006"},
]


@pytest.fixture(autouse=True)
def _marca_sintetica(monkeypatch):
    """Acrescenta uma marca de teste a allowlist, sem tocar as reais."""
    monkeypatch.setitem(A.MARCAS, "marca a", "marca_a")


def _linhas_por_kit(bom):
    out = {}
    for r in bom.bom_rows:
        out.setdefault(r["kit_sku"], {})[r["component_sku"]] = r["qty_per_kit"]
    return out


# --------------------------------------------------------------- traducao

def test_mesmo_sku_em_duas_marcas_gera_dois_kits_distintos():
    bom = A.build_avoe_bom(
        [_kit("Lescent", "KIT001", "900", "901"),
         _kit("Marca A", "KIT001", "900", "901")],
        brand_code_rows=PONTE)
    kits = _linhas_por_kit(bom)
    assert set(kits) == {"AVOE:LESCENT:KIT001", "AVOE:MARCA_A:KIT001"}
    # O codigo `900` virou um produto diferente em cada marca.
    assert kits["AVOE:LESCENT:KIT001"] == {"PL0001": 1, "PL0002": 1}
    assert kits["AVOE:MARCA_A:KIT001"] == {"PA0001": 1, "PA0002": 1}
    # E o contrato aceita a carga: as chaves nao colidem.
    indice = kb.KitBomIndex.build(bom.bom_rows, today=HOJE)
    assert len(indice.components_by_kit) == 2


def test_quantidade_e_a_repeticao_do_componente():
    bom = A.build_avoe_bom([_kit("Lescent", "KIT009", "900", "900", "900")],
                           brand_code_rows=PONTE)
    assert bom.bom_rows == [{
        "kit_sku": "AVOE:LESCENT:KIT009", "component_sku": "PL0001",
        "qty_per_kit": 3, "valid_from": None, "valid_to": None,
        "active": True}]


def test_codigo_de_outra_marca_nao_traduz():
    # `PA0001` so' existe na ponte da marca_a: num kit Lescent ele NAO vira
    # produto da marca_a; fica com chave propria.
    bom = A.build_avoe_bom([_kit("Lescent", "KIT002", "901", "777")],
                           brand_code_rows=PONTE)
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT002"] == {
        "PL0002": 1, "AVOE:LESCENT:777": 1}
    assert bom.summary["componentes_sem_ponte"] == 1


def test_codigo_ambiguo_nao_e_desempatado():
    bom = A.build_avoe_bom([_kit("Lescent", "KIT003", "905", "901")],
                           brand_code_rows=PONTE)
    assert "AVOE:LESCENT:905" in _linhas_por_kit(bom)["AVOE:LESCENT:KIT003"]
    assert bom.summary["componentes_ambiguos"] == 1


def test_ean_nunca_e_criterio_de_traducao():
    # O componente traz um EAN, mas o codigo nao esta na ponte: sem traducao.
    bom = A.build_avoe_bom(
        [_kit("Lescent", "KIT004", "888", "901", ean="7890000000001")],
        brand_code_rows=PONTE)
    assert "AVOE:LESCENT:888" in _linhas_por_kit(bom)["AVOE:LESCENT:KIT004"]


def test_ponte_da_oferta_e_por_marca_e_sku_exatos():
    bom = A.build_avoe_bom([_kit("Lescent", "kit005", "900", "901")],
                           brand_code_rows=PONTE)
    assert bom.brand_code_rows == [
        {"brand": "lescent", "code": "KIT005",
         "protheus_sku": "AVOE:LESCENT:KIT005"}]
    assert bom.kit_brand_rows == [
        {"protheus_sku": "AVOE:LESCENT:KIT005", "brand": "lescent"}]


@pytest.mark.parametrize("kit, motivo", [
    (_kit("Marca Nova", "KIT001", "900"), A.MOTIVO_MARCA_DESCONHECIDA),
    (_kit("Lescent", "Kit 6 Body Splash Lescent's", "900"),
     A.MOTIVO_SKU_INVALIDO),
    (_kit("Lescent", "", "900"), A.MOTIVO_SKU_INVALIDO),
    (_kit("Lescent", "KIT006", "900", ""), A.MOTIVO_COMPONENTE_SEM_SKU),
    (_kit("Lescent", "KIT007"), A.MOTIVO_SEM_COMPONENTES),
])
def test_entrada_invalida_e_recusada_com_motivo(kit, motivo):
    bom = A.build_avoe_bom([kit], brand_code_rows=PONTE)
    assert bom.bom_rows == []
    assert bom.summary["kits_recusados"] == {motivo: 1}


def test_composicao_conflitante_derruba_o_kit():
    bom = A.build_avoe_bom(
        [_kit("Lescent", "KIT010", "900", "901"),
         _kit("Lescent", "KIT010", "900", "PL0003"),
         _kit("Lescent", "KIT011", "900", "901")],
        brand_code_rows=PONTE)
    assert set(_linhas_por_kit(bom)) == {"AVOE:LESCENT:KIT011"}
    assert bom.summary["kits_recusados"] == {A.MOTIVO_COMPOSICAO_CONFLITANTE: 1}


def test_repeticao_identica_do_kit_nao_e_conflito():
    k = _kit("Lescent", "KIT012", "900", "901")
    bom = A.build_avoe_bom([k, dict(k)], brand_code_rows=PONTE)
    assert set(_linhas_por_kit(bom)) == {"AVOE:LESCENT:KIT012"}
    assert bom.summary["kits_recusados"] == {}


# --------------------------------------------------------------- contrato

def _dados_protheus():
    """O que `carregar` devolveria do Data Mart/Neon, reduzido e sintetico."""
    return {
        "observed_date": HOJE,
        "snapshot_id": "snap-teste",
        "reference_captured_at": None,
        "offers": [
            # Resolve: os dois componentes tem referencia B2B.
            {"offer_key": "O1", "brand": "lescent", "shop_account": "lescent",
             "seller_sku": "KIT001", "product_type": "kit_suspected",
             "batch_id": "b1"},
            # Mesmo SKU em loja de outra marca: kit da marca_a, componente sem
            # referencia -> blocked com motivo.
            {"offer_key": "O2", "brand": "marca_a", "shop_account": "marca_a",
             "seller_sku": "KIT001", "product_type": "kit_suspected",
             "batch_id": "b1"},
            # Componente sem traducao -> blocked, nao inventado.
            {"offer_key": "O3", "brand": "lescent", "shop_account": "lescent",
             "seller_sku": "KIT002", "product_type": "kit_confirmed",
             "batch_id": "b1"},
            # SKU que nao existe na Avoe para esta marca -> sem linha.
            {"offer_key": "O4", "brand": "lescent", "shop_account": "lescent",
             "seller_sku": "KIT999", "product_type": "kit_suspected",
             "batch_id": "b1"},
            # Kit do Protheus, alcancado pelo proprio SKU.
            {"offer_key": "O5", "brand": "lescent", "shop_account": "lescent",
             "seller_sku": "KPL0009", "product_type": "kit_confirmed",
             "batch_id": "b1"},
        ],
        "references": [
            {"reference_row_id": "R1", "brand": "lescent", "source_sku": "PL0001",
             "source_gtin": None, "suggested_retail_amount": "10.00",
             "quality_status": "ok"},
            {"reference_row_id": "R2", "brand": "lescent", "source_sku": "PL0002",
             "source_gtin": None, "suggested_retail_amount": "20.00",
             "quality_status": "ok"},
        ],
        "bom_rows": [
            {"kit_sku": "KPL0009", "component_sku": "PL0001", "qty_per_kit": 2,
             "valid_from": None, "valid_to": None, "active": True},
        ],
        "catalog_rows": [
            {"component_sku": "PL0001", "brand": "lescent", "ean": None},
            {"component_sku": "PL0002", "brand": "lescent", "ean": None},
            {"component_sku": "PA0001", "brand": "marca_a", "ean": None},
            {"component_sku": "PA0002", "brand": "marca_a", "ean": None},
        ],
        "bridge_brand_code": list(PONTE),
        "bridge_alias": [],
        "kit_brand_rows": [{"protheus_sku": "KPL0009", "brand": "lescent"}],
    }


AVOE = [
    _kit("Lescent", "KIT001", "900", "901"),
    _kit("Marca A", "KIT001", "900", "901"),
    _kit("Lescent", "KIT002", "900", "777"),
]


def _plano(dados):
    return P.build_plan(
        scope=P.Scope("tiktok", HOJE), offers=dados["offers"],
        references=dados["references"], bom_rows=dados["bom_rows"],
        catalog_rows=dados["catalog_rows"],
        bridge_brand_code=dados["bridge_brand_code"],
        bridge_alias=dados["bridge_alias"],
        kit_brand_rows=dados["kit_brand_rows"], snapshot_id="snap-teste",
        reference_captured_at=None, source_run_id="t", today=HOJE)


def _por_oferta(plano):
    i = {c: n for n, c in enumerate(P.COLUNAS)}
    return {r[i["offer_key"]]: {c: r[i[c]] for c in P.COLUNAS}
            for r in plano.records}


def test_protheus_e_o_padrao_e_nao_muda_nada():
    dados = _dados_protheus()
    assert P.montar_fontes(dados, source="protheus", avoe_kits=AVOE) is dados
    assert P.build_cli().parse_args([]).source == "protheus"


def test_avoe_resolve_pelo_contrato_e_bloqueia_com_motivo():
    dados = P.montar_fontes(_dados_protheus(), source="avoe", avoe_kits=AVOE)
    linhas = _por_oferta(_plano(dados))

    o1 = linhas["O1"]
    assert o1["status"] == "resolved"
    assert o1["kit_protheus_sku"] == "AVOE:LESCENT:KIT001"
    assert o1["bridge_status"] == kb.BRIDGE_EXACT_DIRECT
    assert o1["bridge_method"] == kb.BRIDGE_METHOD_BRAND_CODE
    # (10 + 20) com 2 unidades -> -5%: a faixa e' a do contrato.
    assert o1["kit_reference_amount"] == Decimal("28.50")

    o2 = linhas["O2"]
    assert o2["kit_protheus_sku"] == "AVOE:MARCA_A:KIT001"
    assert o2["status"] == "blocked"
    assert o2["reason"] == kb.REASON_COMPONENT_REFERENCE_MISSING

    o3 = linhas["O3"]
    assert o3["status"] == "blocked"
    assert o3["reason"] == kb.REASON_COMPONENT_NOT_IN_CATALOG
    componentes = {c["component_sku"] for c in json.loads(o3["components"])}
    assert componentes == {"PL0001", "AVOE:LESCENT:777"}

    # Sem kit na Avoe para (lescent, KIT999), e em `avoe` o kit do Protheus
    # sai da BOM: nenhuma das duas ofertas produz linha.
    assert "O4" not in linhas and "O5" not in linhas
    assert dados["avoe_summary"]["kits_aceitos"] == 3


def test_ambas_soma_as_duas_boms():
    dados = P.montar_fontes(_dados_protheus(), source="ambas", avoe_kits=AVOE)
    linhas = _por_oferta(_plano(dados))
    assert set(linhas) == {"O1", "O2", "O3", "O5"}
    assert linhas["O5"]["kit_protheus_sku"] == "KPL0009"
    assert linhas["O5"]["kit_reference_amount"] == Decimal("19.00")


def test_kit_nos_dois_lados_vira_ambiguo_e_nao_publica():
    dados = _dados_protheus()
    # O cadastro tambem leva (lescent, KIT001) a um kit Protheus.
    dados["bom_rows"].append({"kit_sku": "KPL0010", "component_sku": "PL0001",
                              "qty_per_kit": 1, "valid_from": None,
                              "valid_to": None, "active": True})
    dados["bridge_brand_code"].append(
        {"brand": "lescent", "code": "KIT001", "protheus_sku": "KPL0010"})
    dados["kit_brand_rows"].append({"protheus_sku": "KPL0010", "brand": "lescent"})
    plano = _plano(P.montar_fontes(dados, source="ambas", avoe_kits=AVOE))
    assert "O1" not in _por_oferta(plano)
    assert plano.summary["bridge_status"][kb.BRIDGE_AMBIGUOUS] == 1


@pytest.mark.parametrize("kits", [None, []])
def test_fonte_avoe_sem_kits_e_recusada(kits):
    with pytest.raises(P.KitPublisherError):
        P.montar_fontes(_dados_protheus(), source="avoe", avoe_kits=kits)


def test_fonte_avoe_com_todos_os_kits_invalidos_e_recusada():
    with pytest.raises(P.KitPublisherError):
        P.montar_fontes(_dados_protheus(), source="avoe",
                        avoe_kits=[_kit("Marca Nova", "KIT001", "900")])


def test_cli_avoe_sem_snapshot_sai_com_2(monkeypatch):
    monkeypatch.delenv(A.ENV_SNAPSHOT, raising=False)
    assert P.main(["--source", "avoe"]) == 2


def test_cli_avoe_com_snapshot_ausente_sai_com_2(tmp_path):
    assert P.main(["--source", "ambas", "--avoe-snapshot",
                   str(tmp_path / "nao-existe.json")]) == 2


# --------------------------------------------------------------- snapshot

def test_snapshot_valido_e_lido(tmp_path):
    arq = tmp_path / "snap.json"
    arq.write_text(json.dumps({"kits": AVOE}), encoding="utf-8")
    assert len(A.load_snapshot(arq)["kits"]) == 3


@pytest.mark.parametrize("conteudo", ["{", "[]", '{"kits": []}', '{"x": 1}'])
def test_snapshot_invalido_e_recusado(tmp_path, conteudo):
    arq = tmp_path / "snap.json"
    arq.write_text(conteudo, encoding="utf-8")
    with pytest.raises(A.AvoeSourceError):
        A.load_snapshot(arq)


# --------------------------------------------------------------- captura

def _api_falsa(por_termo):
    chamadas = []

    def consultar(tool, args):
        chamadas.append((tool, dict(args)))
        return {"tool": tool, "result": {"kits": por_termo(args)}}
    return consultar, chamadas


def test_captura_refina_termo_que_bate_no_teto():
    universo = [_kit("Lescent", f"KIT0{n:02d}", "900") for n in range(25)]

    def por_termo(args):
        achados = [k for k in universo if args["busca"].upper() in k["sku_kit"]]
        return achados[:A.TETO_RESULTADOS]

    consultar, chamadas = _api_falsa(por_termo)
    snap = A.fetch_snapshot(consultar=consultar, marcas=["lescent"])
    assert len(snap["kits"]) == 25
    assert snap["termos_no_teto"] == []
    termos = [a["busca"] for _, a in chamadas]
    # KIT0 bateu no teto e foi refinado; KIT1 e KIT2 nao.
    assert termos[:3] == ["KIT0", "KIT1", "KIT2"]
    assert "KIT00" in termos and "KIT02" in termos
    assert all(a["marca"] == "Lescent" for _, a in chamadas)


def test_captura_aborta_no_teto_de_chamadas():
    consultar, _ = _api_falsa(
        lambda a: [_kit("Lescent", f"K{n}", "900") for n in range(20)])
    with pytest.raises(A.AvoeSourceError):
        A.fetch_snapshot(consultar=consultar, marcas=["lescent"], max_chamadas=5)


def test_captura_recusa_resposta_sem_kits():
    consultar, _ = _api_falsa(lambda a: None)
    with pytest.raises(A.AvoeSourceError):
        A.fetch_snapshot(consultar=consultar, marcas=["denavita"])


# --------------------------------------------------------------- credencial

CHAVE_FALSA = "chave-sintetica-de-teste-0000"


def test_credencial_le_url_e_chave_do_arquivo(tmp_path):
    arq = tmp_path / "cred.txt"
    arq.write_text("# comentario\nAVOE_API_URL=https://exemplo.invalid/api\n"
                   f"AVOE_API_KEY={CHAVE_FALSA}\n", encoding="utf-8")
    assert A._carregar_credencial(arq) == ("https://exemplo.invalid/api",
                                           CHAVE_FALSA)


def test_credencial_incompleta_e_recusada_sem_expor_valor(tmp_path):
    arq = tmp_path / "cred.txt"
    arq.write_text(f"AVOE_API_KEY={CHAVE_FALSA}\n", encoding="utf-8")
    with pytest.raises(A.AvoeSourceError) as exc:
        A._carregar_credencial(arq)
    assert CHAVE_FALSA not in str(exc.value)


def test_erro_http_nao_carrega_a_chave(monkeypatch):
    def urlopen_falso(req, timeout):
        assert req.get_header("X-api-key") == CHAVE_FALSA
        raise urllib.error.HTTPError(req.full_url, 403, "proibido", {}, None)

    monkeypatch.setattr(A.urllib.request, "urlopen", urlopen_falso)
    with pytest.raises(A.AvoeSourceError) as exc:
        A._post("https://exemplo.invalid/api", CHAVE_FALSA, "consultar_kit",
                {"busca": "KIT0"})
    assert "403" in str(exc.value)
    assert CHAVE_FALSA not in str(exc.value)
    assert exc.value.__cause__ is None


def test_cli_de_captura_sem_credencial_sai_com_2(monkeypatch, tmp_path):
    monkeypatch.delenv(A.ENV_SECRET_FILE, raising=False)
    assert A.main(["--out", str(tmp_path / "s.json")]) == 2
