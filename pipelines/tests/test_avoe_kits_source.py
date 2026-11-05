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
    """O mesmo caminho de `publish`/`diagnose`: `planejar`, grupo a grupo."""
    return P.planejar(dados, scope=P.Scope("tiktok", HOJE),
                      source_run_id="t", today=HOJE)


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


def _ambas(dados):
    return P.montar_fontes(dados, source="ambas", avoe_kits=AVOE, today=HOJE)


def test_ambas_soma_as_duas_boms():
    dados = _ambas(_dados_protheus())
    linhas = _por_oferta(_plano(dados))
    assert set(linhas) == {"O1", "O2", "O3", "O5"}
    # So' na Avoe: entra pela Avoe.
    assert linhas["O1"]["kit_protheus_sku"] == "AVOE:LESCENT:KIT001"
    assert linhas["O3"]["kit_protheus_sku"] == "AVOE:LESCENT:KIT002"
    # So' no Protheus: continua, com o mesmo valor do modo padrao.
    assert linhas["O5"]["kit_protheus_sku"] == "KPL0009"
    assert linhas["O5"]["kit_reference_amount"] == Decimal("19.00")
    padrao = _por_oferta(_plano(_dados_protheus()))
    assert linhas["O5"] == padrao["O5"]
    prec = dados["avoe_summary"]["precedencia"]
    assert prec["sobrepostas"] == 0
    assert prec["ofertas_pela_avoe"] == 3      # O1, O2, O3
    assert prec["ofertas_pelo_protheus"] == 2  # O4, O5


def _protheus_tambem_tem_kit001(*composicao):
    """O cadastro tambem leva (lescent, KIT001) a um kit Protheus."""
    dados = _dados_protheus()
    for comp, qtd in composicao:
        dados["bom_rows"].append({"kit_sku": "KPL0010", "component_sku": comp,
                                  "qty_per_kit": qtd, "valid_from": None,
                                  "valid_to": None, "active": True})
    dados["bridge_brand_code"].append(
        {"brand": "lescent", "code": "KIT001", "protheus_sku": "KPL0010"})
    dados["kit_brand_rows"].append({"protheus_sku": "KPL0010", "brand": "lescent"})
    return dados


def test_nas_duas_fontes_identica_a_avoe_vence_e_publica():
    dados = _ambas(_protheus_tambem_tem_kit001(("PL0001", 1), ("PL0002", 1)))
    plano = _plano(dados)
    o1 = _por_oferta(plano)["O1"]
    assert o1["kit_protheus_sku"] == "AVOE:LESCENT:KIT001"
    assert o1["status"] == "resolved"
    assert o1["kit_reference_amount"] == Decimal("28.50")
    assert kb.BRIDGE_AMBIGUOUS not in plano.summary["bridge_status"]
    prec = dados["avoe_summary"]["precedencia"]
    assert (prec["sobrepostas"], prec["identicas"], prec["divergentes"]) == (1, 1, 0)
    assert prec["divergentes_lista"] == []


def test_nas_duas_fontes_divergente_a_avoe_vence_e_e_listada():
    # Protheus diz 2x PL0001; a Avoe diz PL0001 + PL0002.
    dados = _ambas(_protheus_tambem_tem_kit001(("PL0001", 2)))
    o1 = _por_oferta(_plano(dados))["O1"]
    assert o1["kit_protheus_sku"] == "AVOE:LESCENT:KIT001"
    assert o1["kit_reference_amount"] == Decimal("28.50")
    prec = dados["avoe_summary"]["precedencia"]
    assert (prec["sobrepostas"], prec["identicas"], prec["divergentes"]) == (1, 0, 1)
    assert prec["divergentes_lista"] == [{
        "offer_key": "O1", "brand": "lescent", "seller_sku": "KIT001",
        "kit_avoe": "AVOE:LESCENT:KIT001", "kit_protheus": "KPL0010"}]


def test_resumo_somado_dos_grupos_fecha_com_as_ofertas():
    plano = _plano(_ambas(_dados_protheus()))
    assert plano.summary["offers_in_photo"] == 5
    assert plano.summary["kit_signal"] == 5
    assert (plano.summary["published_resolved"]
            + plano.summary["published_blocked"]) == len(plano.records) == 4


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


class _ConexaoAberta(Exception):
    """Sinal de que o CLI passou da trava e tentou conectar."""


@pytest.fixture
def cli_sem_banco(monkeypatch, tmp_path):
    """Snapshot valido, DSNs falsas e `_connect` que levanta se for chamado."""
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"kits": AVOE}), encoding="utf-8")
    monkeypatch.setenv(P.ENV_TARGET, "postgresql://falso.invalid/alvo")
    monkeypatch.setenv(P.ENV_SOURCE, "postgresql://falso.invalid/fonte")
    conexoes = []

    def conectar(url, *, readonly):
        conexoes.append((url, readonly))
        raise _ConexaoAberta(url)

    monkeypatch.setattr(P, "_connect", conectar)
    return str(snap), conexoes


def test_apply_com_source_avoe_e_recusado_sem_abrir_conexao(cli_sem_banco,
                                                            capsys):
    snap, conexoes = cli_sem_banco
    assert P.main(["--source", "avoe", "--avoe-snapshot", snap,
                   "--apply"]) == 2
    assert conexoes == []
    assert "--source ambas" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [
    ["--source", "avoe"],               # diagnostico com avoe: permitido
    ["--source", "ambas", "--apply"],   # publicacao com ambas: permitida
    ["--apply"],                        # protheus (padrao): inalterado
])
def test_demais_combinacoes_passam_da_trava(cli_sem_banco, argv):
    snap, conexoes = cli_sem_banco
    with pytest.raises(_ConexaoAberta):
        P.main(argv + ["--avoe-snapshot", snap])
    assert len(conexoes) == 1


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


# --------------------------------------------------------------- de-para (T-0063)
#
# Tudo sintetico: marcas reais (o de-para e' escopado pela marca da Torre), mas
# codigos, SKUs e nomes inventados. O caso real equivalente esta' citado no
# comentario de cada teste, sem numero de negocio.

CATALOGO = [
    {"component_sku": "PL0001", "brand": "lescent", "ean": None,
     "name": "Deo Sintetico 25ml"},
    {"component_sku": "PL0002", "brand": "lescent", "ean": None,
     "name": "Deo Sintetico Outro"},
    {"component_sku": "PL0007", "brand": "lescent", "ean": None,
     "name": "DEO SINTETICO SETE 25ML"},
    {"component_sku": "PL0008", "brand": "lescent", "ean": None,
     "name": "Deo Sintetico Oito"},
    {"component_sku": "PB0001", "brand": "barbours", "ean": None,
     "name": "Body Splash Sintetico 200ml"},
    {"component_sku": "PX0001", "brand": "apice", "ean": None,
     "name": "Ativador Sintetico 1000 ml"},
    {"component_sku": "PX0002", "brand": "apice", "ean": None,
     "name": "Condicionador Sintetico 1L"},
    {"component_sku": "PX0003", "brand": "apice", "ean": None,
     "name": "Shampoo Sintetico 300ml"},
]

PONTE_APICE = [
    {"brand": "apice", "code": "700", "protheus_sku": "PX0001"},
    {"brand": "apice", "code": "701", "protheus_sku": "PX0002"},
    {"brand": "apice", "code": "702", "protheus_sku": "PX0003"},
]


def _dp(marca, codigo, destino, ativo=True):
    return {"marca": marca, "codigo_avoe": codigo, "sku_destino": destino,
            "classe": "A", "evidencia": "sintetico", "aprovado_por": "teste",
            "ativo": ativo}


def _kit_nomeado(marca, sku, *pares):
    """Kit cujos componentes tem nome: `pares` = (codigo, nome)."""
    k = _kit(marca, sku)
    k["componentes"] = [{"sku": c, "ean": None, "nome": n} for c, n in pares]
    return k


def _bom(kits, *, ponte=PONTE, catalogo=CATALOGO, de_para=()):
    return A.build_avoe_bom(kits, brand_code_rows=ponte, catalog_rows=catalogo,
                            de_para_rows=list(de_para))


def test_de_para_traduz_o_que_o_map_nao_traduz():
    bom = _bom([_kit_nomeado("Lescent", "KIT020",
                             ("950", "Deo Sintetico Sete 25ML"))],
               de_para=[_dp("lescent", "950", "PL0007")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT020"] == {"PL0007": 1}
    assert bom.summary["traduzidos_por_origem"] == {A.ORIGEM_DE_PARA: 1}
    assert bom.summary["volume"] == {A.VOLUME_CONCORDA: 1}


def test_trava_a_de_para_igual_ao_map_fica_com_o_map():
    # O map ja' traduz 900 -> PL0001; o de-para repete o mesmo destino.
    bom = _bom([_kit("Lescent", "KIT021", "900")],
               de_para=[_dp("lescent", "900", "PL0001")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT021"] == {"PL0001": 1}
    assert bom.summary["traduzidos_por_origem"] == {A.ORIGEM_MAP: 1}


def test_trava_a_de_para_nao_desempata_map_ambiguo():
    # 905 tem DUAS traducoes no map; o de-para nao escolhe uma delas.
    bom = _bom([_kit("Lescent", "KIT022", "905")],
               de_para=[_dp("lescent", "905", "PL0005")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT022"] == {"AVOE:LESCENT:905": 1}
    assert bom.summary["ambiguos_por_motivo"] == {A.AMBIGUO_MAP_MULTIPLO: 1}


def test_trava_b_map_e_de_para_divergentes_viram_ambiguo():
    bom = _bom([_kit("Lescent", "KIT023", "900", "901")],
               de_para=[_dp("lescent", "900", "PL0008")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT023"] == {
        "AVOE:LESCENT:900": 1, "PL0002": 1}
    assert bom.summary["componentes_ambiguos"] == 1
    assert bom.summary["ambiguos_por_motivo"] == {
        A.AMBIGUO_MAP_DIVERGE_DE_PARA: 1}
    [b] = bom.summary["bloqueios"]
    assert (b["map"], b["de_para"]) == (["PL0001"], ["PL0008"])


def test_de_para_com_dois_destinos_ativos_e_ambiguo():
    bom = _bom([_kit("Lescent", "KIT024", "950")],
               de_para=[_dp("lescent", "950", "PL0007"),
                        _dp("lescent", "950", "PL0008")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT024"] == {"AVOE:LESCENT:950": 1}
    assert bom.summary["ambiguos_por_motivo"] == {A.AMBIGUO_DE_PARA_MULTIPLO: 1}


def test_trava_c_destino_fora_do_catalogo_fica_sem_ponte():
    bom = _bom([_kit("Lescent", "KIT025", "950")],
               de_para=[_dp("lescent", "950", "PL9999")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT025"] == {"AVOE:LESCENT:950": 1}
    assert bom.summary["sem_ponte_por_motivo"] == {
        A.SEM_PONTE_DE_PARA_FORA_DO_CATALOGO: 1}


def test_trava_c_sem_catalogo_nenhum_destino_e_aceito():
    bom = A.build_avoe_bom([_kit("Lescent", "KIT026", "950")],
                           brand_code_rows=PONTE,
                           de_para_rows=[_dp("lescent", "950", "PL0007")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT026"] == {"AVOE:LESCENT:950": 1}


def test_trava_c_destino_de_outra_marca_no_catalogo_fica_sem_ponte():
    # A linha e' Lescent, mas o destino e' um SKU da Barbour's no catalogo.
    bom = _bom([_kit("Lescent", "KIT027", "950")],
               de_para=[_dp("lescent", "950", "PB0001")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT027"] == {"AVOE:LESCENT:950": 1}
    assert bom.summary["sem_ponte_por_motivo"] == {
        A.SEM_PONTE_DE_PARA_OUTRA_MARCA: 1}


def test_trava_d_de_para_de_outra_marca_nao_alcanca_o_componente():
    # Mesmo codigo, kit da Barbour's: a linha Lescent nao vale para ele.
    bom = _bom([_kit("Barbours", "KIT028", "950")],
               de_para=[_dp("lescent", "950", "PL0007")])
    assert _linhas_por_kit(bom)["AVOE:BARBOURS:KIT028"] == {
        "AVOE:BARBOURS:950": 1}
    assert bom.summary["sem_ponte_por_motivo"] == {A.SEM_PONTE_SEM_TRADUCAO: 1}


def test_trava_e_linha_inativa_e_ignorada():
    bom = _bom([_kit("Lescent", "KIT029", "950")],
               de_para=[_dp("lescent", "950", "PL0007", ativo=False)])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT029"] == {"AVOE:LESCENT:950": 1}
    assert bom.summary["de_para_linhas_ativas"] == 0


def test_trava_e_ativo_textual_so_liga_com_true():
    bom = _bom([_kit("Lescent", "KIT030", "950")],
               de_para=[_dp("lescent", "950", "PL0007", ativo="sim")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT030"] == {"AVOE:LESCENT:950": 1}


# --------------------------------------------------------------- linha negativa

def _neg(marca, codigo, ativo=True):
    return dict(_dp(marca, codigo, "", ativo=ativo), classe="NEG", bloquear=True)


def test_negativa_bloqueia_traducao_do_map():
    # Equivalente sintetico de "Mascara" que o map traduz para um
    # Condicionador: a trava de volume nao pega (sem_prova), a negativa pega.
    bom = _bom([_kit("Lescent", "KIT040", "900", "901")],
               de_para=[_neg("lescent", "900")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT040"] == {
        "AVOE:LESCENT:900": 1, "PL0002": 1}
    assert bom.summary["componentes_ambiguos"] == 1
    assert bom.summary["ambiguos_por_motivo"] == {A.AMBIGUO_DE_PARA_BLOQUEIO: 1}
    assert bom.summary["de_para_bloqueios_ativos"] == 1
    assert bom.summary["de_para_linhas_ativas"] == 0
    [b] = bom.summary["bloqueios"]
    assert (b["kit"], b["codigo"], b["motivo"], b["map"]) == (
        "AVOE:LESCENT:KIT040", "900", A.AMBIGUO_DE_PARA_BLOQUEIO, ["PL0001"])


def test_negativa_faz_o_kit_nao_resolver_no_contrato():
    sem = _por_oferta(_plano(P.montar_fontes(
        _dados_protheus(), source="avoe", avoe_kits=AVOE, de_para_rows=[])))
    assert sem["O1"]["status"] == "resolved"
    com = _por_oferta(_plano(P.montar_fontes(
        _dados_protheus(), source="avoe", avoe_kits=AVOE,
        de_para_rows=[_neg("lescent", "900")])))
    assert com["O1"]["status"] == "blocked"
    assert com["O1"]["reason"] == kb.REASON_COMPONENT_NOT_IN_CATALOG
    assert {c["component_sku"] for c in json.loads(com["O1"]["components"])} == {
        "AVOE:LESCENT:900", "PL0002"}


def test_negativa_inativa_e_ignorada_e_o_map_volta_a_valer():
    bom = _bom([_kit("Lescent", "KIT041", "900")],
               de_para=[_neg("lescent", "900", ativo=False)])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT041"] == {"PL0001": 1}
    assert bom.summary["traduzidos_por_origem"] == {A.ORIGEM_MAP: 1}
    assert bom.summary["de_para_bloqueios_ativos"] == 0


def test_negativa_de_uma_marca_nao_afeta_positiva_de_outra():
    # Caso real: o codigo 10009 e' negativo em barbours e positivo em lescent.
    ponte = [{"brand": "barbours", "code": "10009", "protheus_sku": "PB0001"}]
    catalogo = CATALOGO + [{"component_sku": "LC01003", "brand": "lescent",
                            "ean": None, "name": "Deo Sintetico"}]
    bom = _bom([_kit("Barbours", "KIT042", "10009"),
                _kit("Lescent", "KIT042", "10009")],
               ponte=ponte, catalogo=catalogo,
               de_para=[_neg("barbours", "10009"),
                        _dp("lescent", "10009", "LC01003")])
    kits = _linhas_por_kit(bom)
    assert kits["AVOE:BARBOURS:KIT042"] == {"AVOE:BARBOURS:10009": 1}
    assert kits["AVOE:LESCENT:KIT042"] == {"LC01003": 1}
    assert bom.summary["ambiguos_por_motivo"] == {A.AMBIGUO_DE_PARA_BLOQUEIO: 1}
    assert bom.summary["traduzidos_por_origem"] == {A.ORIGEM_DE_PARA: 1}


def test_negativa_vence_positiva_quando_chega_sem_passar_pelo_carregador():
    # O carregador recusa o par; quem chama `build_avoe_bom` direto com as
    # duas linhas recebe bloqueio (fail-closed), nunca a traducao.
    bom = _bom([_kit("Lescent", "KIT043", "950")],
               de_para=[_dp("lescent", "950", "PL0007"), _neg("lescent", "950")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT043"] == {"AVOE:LESCENT:950": 1}
    assert bom.summary["ambiguos_por_motivo"] == {A.AMBIGUO_DE_PARA_BLOQUEIO: 1}


# --------------------------------------------------------------- volume

@pytest.mark.parametrize("nome, esperado", [
    ("Deo 25ML", {("ml", Decimal(25))}),
    ("Condicionador 1L", {("ml", Decimal(1000))}),
    ("Cond 1,5 L", {("ml", Decimal(1500))}),
    ("Ativador 500gr", {("g", Decimal(500))}),
    ("Pote 1kg", {("g", Decimal(1000))}),
    ("Máscara 300 g Marca- G7", {("g", Decimal(300))}),
    # Numero colado a letra, ou unidade seguida de letra: nao e' volume.
    ("SERUM AP3000 N20 Q10", set()),
    ("Kit 3 Lescent", set()),
    ("7900000000000 - Body Splash - C1", set()),
])
def test_volumes_declarados(nome, esperado):
    assert A.volumes_declarados([nome]) == esperado


def test_volume_igual_passa():
    assert A.comparar_volume(["Deo 25ml"], ["DEO 25ML"]) == A.VOLUME_CONCORDA


def test_volume_1l_e_1000ml_sao_iguais():
    assert A.comparar_volume(["Cond 1L"], ["Cond 1000 ml"]) == A.VOLUME_CONCORDA
    assert A.comparar_volume(["Pote 1kg"], ["Pote 1000g"]) == A.VOLUME_CONCORDA


def test_volume_diferente_diverge():
    assert A.comparar_volume(["Shampoo 300ml"], ["Shampoo 1L"]) == A.VOLUME_DIVERGE


def test_volume_ausente_e_sem_prova():
    assert A.comparar_volume(["Mascara"], ["Cond 1L"]) == A.VOLUME_SEM_PROVA
    assert A.comparar_volume(["Cond 1L"], []) == A.VOLUME_SEM_PROVA


def test_volume_g_contra_ml_e_unidade_incompativel():
    assert (A.comparar_volume(["Ativador 500gr"], ["Ativador 1000 ml"])
            == A.VOLUME_UNIDADE_INCOMPATIVEL)
    assert (A.comparar_volume(["Gel 500g"], ["Gel 500ml"])
            == A.VOLUME_UNIDADE_INCOMPATIVEL)


def test_traducao_do_map_com_volume_em_gr_contra_ml_bloqueia():
    # Equivalente sintetico do Ativador "500gr" traduzido para SKU de 1000 ml.
    bom = _bom([_kit_nomeado("Apice", "KIT031",
                             ("700", "Ativador Sintetico 500gr"),
                             ("702", "Shampoo Sintetico 300ml"))],
               ponte=PONTE_APICE)
    assert _linhas_por_kit(bom)["AVOE:APICE:KIT031"] == {
        "AVOE:APICE:700": 1, "PX0003": 1}
    assert bom.summary["ambiguos_por_motivo"] == {A.AMBIGUO_VOLUME_UNIDADE: 1}
    assert bom.summary["volume"] == {A.VOLUME_CONCORDA: 1,
                                     A.VOLUME_UNIDADE_INCOMPATIVEL: 1}


def test_traducao_do_map_com_volume_diferente_bloqueia():
    bom = _bom([_kit_nomeado("Apice", "KIT032",
                             ("702", "Shampoo Sintetico 1000ml"))],
               ponte=PONTE_APICE)
    assert _linhas_por_kit(bom)["AVOE:APICE:KIT032"] == {"AVOE:APICE:702": 1}
    assert bom.summary["ambiguos_por_motivo"] == {A.AMBIGUO_VOLUME_DIVERGE: 1}


def test_mascara_sem_volume_para_condicionador_1l_nao_e_pega_pela_trava():
    # Equivalente sintetico das "Mascara ... Scalp Care" -> Condicionador 1L:
    # a Avoe nao declara volume, entao a trava de volume NAO bloqueia. O tipo
    # de produto diferente precisa de outra prova, fora desta trava.
    bom = _bom([_kit_nomeado("Apice", "KIT033",
                             ("701", "Mascara Sintetica Scalp"))],
               ponte=PONTE_APICE)
    assert _linhas_por_kit(bom)["AVOE:APICE:KIT033"] == {"PX0002": 1}
    assert bom.summary["volume"] == {A.VOLUME_SEM_PROVA: 1}


def test_de_para_tambem_passa_pela_trava_de_volume():
    bom = _bom([_kit_nomeado("Lescent", "KIT034",
                             ("950", "Deo Sintetico 100ml"))],
               de_para=[_dp("lescent", "950", "PL0007")])
    assert _linhas_por_kit(bom)["AVOE:LESCENT:KIT034"] == {"AVOE:LESCENT:950": 1}
    assert bom.summary["ambiguos_por_motivo"] == {A.AMBIGUO_VOLUME_DIVERGE: 1}


def test_volume_julga_a_traducao_com_os_nomes_de_todos_os_kits():
    # A traducao (marca, codigo) -> SKU e' a mesma em todo kit. Um nome que
    # contradiz o destino num kit bloqueia o codigo em TODOS — inclusive no kit
    # cujo nome concorda e no kit em que a Avoe mandou o componente sem nome.
    bom = _bom([_kit_nomeado("Apice", "KIT035",
                             ("702", "Shampoo Sintetico 300ml")),
                _kit_nomeado("Apice", "KIT036",
                             ("702", "Condicionador Sint 1000ml")),
                _kit_nomeado("Apice", "KIT037", ("702", ""))],
               ponte=PONTE_APICE)
    kits = _linhas_por_kit(bom)
    assert kits["AVOE:APICE:KIT035"] == {"AVOE:APICE:702": 1}
    assert kits["AVOE:APICE:KIT036"] == {"AVOE:APICE:702": 1}
    assert kits["AVOE:APICE:KIT037"] == {"AVOE:APICE:702": 1}
    assert bom.summary["ambiguos_por_motivo"] == {A.AMBIGUO_VOLUME_DIVERGE: 3}


# --------------------------------------------------------------- CSV versionado

def test_de_para_versionado_tem_as_positivas_classe_a_e_as_negativas():
    linhas = A.carregar_de_para()
    assert all(r["ativo"] is True for r in linhas)
    positivas = [r for r in linhas if not r["bloquear"]]
    negativas = [r for r in linhas if r["bloquear"]]
    assert all(r["classe"] == "A" for r in positivas)
    assert {(r["marca"], r["codigo_avoe"], r["sku_destino"])
            for r in positivas} == {
        ("barbours", "10041", "BB02027"), ("barbours", "10042", "BB02019"),
        ("lescent", "10009", "LC01003"), ("lescent", "10015", "LC01009"),
        ("lescent", "10016", "LC01010"), ("lescent", "10017", "LC01011"),
        ("lescent", "10018", "LC03012"), ("lescent", "10026", "LC03020"),
        ("lescent", "10027", "LC03021"),
    }
    assert all(r["classe"] == "NEG" and r["sku_destino"] == ""
               for r in negativas)
    assert {(r["marca"], r["codigo_avoe"]) for r in negativas} == {
        ("apice", "20674"), ("apice", "20675"), ("apice", "20016"),
        ("barbours", "10009"), ("barbours", "10134"),
    }
    assert len(positivas) == 9 and len(negativas) == 5
    assert all(r["aprovado_por"] and r["evidencia"] for r in linhas)
    # Cada linha leu exatamente as colunas do cabecalho: nenhuma virgula solta
    # fora das aspas deslocou a evidencia.
    assert all(None not in r for r in linhas)


_CABECALHO = ",".join(A.DE_PARA_COLUNAS)


@pytest.mark.parametrize("conteudo", [
    "marca,codigo,sku\nlescent,1,X\n",                # colunas erradas
    ("marca,codigo_avoe,sku_destino,classe,evidencia,aprovado_por,ativo\n"
     "lescent,1,X,A,e,p,true\n"),                     # sem a coluna bloquear
    f"{_CABECALHO}\nlescnt,1,X,A,e,p,true,false\n",   # marca digitada errada
    f"{_CABECALHO}\nlescent,1,X,A,e,p,sim,false\n",   # ativo fora de true|false
    f"{_CABECALHO}\nlescent,,X,A,e,p,true,false\n",   # codigo vazio
    f"{_CABECALHO}\nlescent,1,X,A,e,p,true,sim\n",    # bloquear fora de true|false
    f"{_CABECALHO}\nlescent,1,X,A,e,p,true,\n",       # bloquear vazio
    f"{_CABECALHO}\nlescent,1,,A,e,p,true,false\n",   # destino vazio sem bloquear
    f"{_CABECALHO}\nlescent,1,,A,e,p,false,false\n",  # idem, mesmo inativa
    f"{_CABECALHO}\nlescent,1,X,NEG,e,p,true,true\n", # bloquear com destino
])
def test_de_para_malformado_e_recusado(tmp_path, conteudo):
    arq = tmp_path / "dp.csv"
    arq.write_text(conteudo, encoding="utf-8")
    with pytest.raises(A.AvoeSourceError, match="de-para"):
        A.carregar_de_para(arq)


def _csv(tmp_path, *linhas):
    arq = tmp_path / "dp.csv"
    arq.write_text("\n".join((_CABECALHO,) + linhas) + "\n", encoding="utf-8")
    return arq


def test_de_para_positiva_e_negativa_ativas_na_mesma_chave_sao_conflito(tmp_path):
    arq = _csv(tmp_path, "lescent,1,X,A,e,p,true,false",
               "lescent,1,,NEG,e,p,true,true")
    with pytest.raises(A.AvoeSourceError, match="linha 3"):
        A.carregar_de_para(arq)


def test_de_para_negativa_antes_da_positiva_tambem_e_conflito(tmp_path):
    arq = _csv(tmp_path, "lescent,1,,NEG,e,p,true,true",
               "lescent,1,X,A,e,p,true,false")
    with pytest.raises(A.AvoeSourceError, match="linha 3"):
        A.carregar_de_para(arq)


def test_de_para_conflito_so_conta_linhas_ativas_e_mesma_chave(tmp_path):
    # Negativa inativa + positiva ativa; e negativa de outra marca no mesmo
    # codigo: nenhum dos dois e' conflito.
    linhas = A.carregar_de_para(_csv(
        tmp_path, "lescent,1,X,A,e,p,true,false",
        "lescent,1,,NEG,e,p,false,true",
        "barbours,1,,NEG,e,p,true,true"))
    assert [(r["marca"], r["ativo"], r["bloquear"]) for r in linhas] == [
        ("lescent", True, False), ("lescent", False, True),
        ("barbours", True, True)]


def test_de_para_versionado_bloqueia_barbours_10009_e_traduz_lescent_10009():
    # O mesmo codigo 10009: o map de barbours traduz (errado) e a negativa
    # bloqueia; em lescent a positiva do CSV traduz.
    ponte = [{"brand": "barbours", "code": "10009", "protheus_sku": "BB02025"}]
    catalogo = [{"component_sku": "BB02025", "brand": "barbours", "ean": None,
                 "name": "Kit de cabelo sintetico"},
                {"component_sku": "LC01003", "brand": "lescent", "ean": None,
                 "name": "Body Splash Sintetico"}]
    bom = A.build_avoe_bom([_kit("Barbours", "KIT044", "10009"),
                            _kit("Lescent", "KIT044", "10009")],
                           brand_code_rows=ponte, catalog_rows=catalogo,
                           de_para_rows=A.carregar_de_para())
    kits = _linhas_por_kit(bom)
    assert kits["AVOE:BARBOURS:KIT044"] == {"AVOE:BARBOURS:10009": 1}
    assert kits["AVOE:LESCENT:KIT044"] == {"LC01003": 1}
    assert bom.summary["de_para_bloqueios_ativos"] == 5
    assert bom.summary["de_para_linhas_ativas"] == 9


def test_de_para_ausente_e_recusado(tmp_path):
    with pytest.raises(A.AvoeSourceError):
        A.carregar_de_para(tmp_path / "nao-existe.csv")


# --------------------------------------------------------------- nao regressao

def test_sem_de_para_com_catalogo_a_bom_e_a_de_antes():
    kits = [_kit("Lescent", "KIT001", "900", "901"),
            _kit("Lescent", "KIT002", "900", "777"),
            _kit("Lescent", "KIT003", "905", "901")]
    antes = A.build_avoe_bom(kits, brand_code_rows=PONTE)
    depois = A.build_avoe_bom(kits, brand_code_rows=PONTE,
                              catalog_rows=_dados_protheus()["catalog_rows"],
                              de_para_rows=[])
    assert antes.bom_rows == depois.bom_rows
    for k in ("componentes_traduzidos", "componentes_sem_ponte",
              "componentes_ambiguos"):
        assert antes.summary[k] == depois.summary[k]


def test_ambas_sem_de_para_e_igual_com_o_csv_versionado_nos_kits_sinteticos():
    # O CSV real nao alcanca os codigos sinteticos: o plano e' o mesmo, e e'
    # o que os testes de `ambas` acima ja' travam.
    sem = P.montar_fontes(_dados_protheus(), source="ambas", avoe_kits=AVOE,
                          today=HOJE, de_para_rows=[])
    com = P.montar_fontes(_dados_protheus(), source="ambas", avoe_kits=AVOE,
                          today=HOJE)
    assert _por_oferta(_plano(sem)) == _por_oferta(_plano(com))
    assert com["avoe_summary"]["de_para_linhas_ativas"] == 9
    assert sem["avoe_summary"]["de_para_linhas_ativas"] == 0


def test_ambas_com_de_para_destrava_o_kit_pelo_contrato():
    dados = _dados_protheus()
    dados["catalog_rows"].append(
        {"component_sku": "PL0007", "brand": "lescent", "ean": None,
         "name": "Deo Sintetico 25ml"})
    dados["references"].append(
        {"reference_row_id": "R7", "brand": "lescent", "source_sku": "PL0007",
         "source_gtin": None, "suggested_retail_amount": "5.00",
         "quality_status": "ok"})
    # KIT002 = 900 + 777; 777 nao tem traducao no map.
    sem = _por_oferta(_plano(P.montar_fontes(
        dados, source="ambas", avoe_kits=AVOE, today=HOJE, de_para_rows=[])))
    assert sem["O3"]["reason"] == kb.REASON_COMPONENT_NOT_IN_CATALOG
    com = _por_oferta(_plano(P.montar_fontes(
        dados, source="ambas", avoe_kits=AVOE, today=HOJE,
        de_para_rows=[_dp("lescent", "777", "PL0007")])))
    assert com["O3"]["status"] == "resolved"
    assert {c["component_sku"] for c in json.loads(com["O3"]["components"])} == {
        "PL0001", "PL0007"}
