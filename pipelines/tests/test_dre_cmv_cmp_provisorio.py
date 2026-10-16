"""DRE-CMV-CMP-1 — CMV provisorio por CMP: regras puras.

Planilhas e vendas SINTETICAS (helpers de `test_dre_cmp_sku`). Prova:
  * CMV = unidades x CMP efetivo na competencia, so' sobre SKU casado;
  * sem casamento => sem CMP (nunca zero); celula sem venda => ausente_na_fonte;
  * vigencia: carrega o ultimo CMP para frente, nunca para tras;
  * conflito entre candidatos fica sem CMP; via GTIN restrita;
  * cobertura parcial medida sobre a receita da fonte;
  * resumo publico sem receita, CMP, CMV ou SKU.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from pipelines.dre import cmp_sku_torre as T
from pipelines.dre import cmv_cmp_provisorio as M
from pipelines.tests.test_dre_cmp_sku import comparativo, lcv, ler_pasta

OUT, MAI = date(2026, 10, 1), date(2026, 5, 1)
NOME_BB = "Comparativo_MesAMes_BB_VAREJO_Consolidado_TodasFiliais_01012025_31072026.xlsx"
NOME_KOK = "Comparativo_MesAMes_KOKESHI_Consolidado_TodasFiliais_01012025_31072026.xlsx"


def v(canal, marca, sku, unidades, receita, *, produto="", ean=None, ambiguo=False):
    return M.LinhaVenda(canal, marca, sku, produto, ean, Decimal(unidades), Decimal(receita), ambiguo)


def resolver(tmp_path, vendas, comp=OUT, cat=None):
    _, ind = ler_pasta(tmp_path)
    return M.classificar(ind, cat or T.Catalogo(), vendas, comp)


def test_cmv_e_unidades_vezes_cmp_com_carry_forward(tmp_path):
    comparativo(tmp_path, [("SP", "A1", "", "SHAMPOO 300ML", [0, 2, 3], 3)])
    (ln,) = resolver(tmp_path, [v("tiktok", "apice", "A1", 10, 500)])
    assert (ln["status"], ln["cmp"], ln["cmv"]) == ("coberto", Decimal("3"), Decimal("30"))
    assert ln["competencia_origem"] == date(2026, 7, 1) and ln["carry_forward"] is True


def test_vigencia_nunca_retroage(tmp_path):
    comparativo(tmp_path, [("SP", "A1", "", "X", [0, 2, 3], 3)])
    (ln,) = resolver(tmp_path, [v("tiktok", "apice", "A1", 10, 500)], comp=MAI)
    assert ln["cmp"] is None and ln["cmv"] is None and ln["status"] == "sem_cmp"


def test_sem_casamento_nunca_vira_zero(tmp_path):
    comparativo(tmp_path, [("SP", "A1", "", "X", [3, 3, 3], 3)])
    linhas = resolver(tmp_path, [v("ml", "apice", "OUTRO", 4, 200)])
    (cel,) = M.agregar(linhas, {("ml", "apice"): Decimal(200)}, {"ml": date(2026, 10, 12)}, OUT)
    assert linhas[0]["cmp"] is None and linhas[0]["cmv"] is None
    assert cel["cmv_provisorio"] is None and cel["cobertura"] == 0
    assert cel["unidades_sem_cmp"] == Decimal(4) and cel["skus_sem_cmp"] == 1


def test_cobertura_parcial_soma_so_o_casado(tmp_path):
    comparativo(tmp_path, [("SP", "A1", "", "X", [3, 3, 3], 3)])
    linhas = resolver(tmp_path, [v("tiktok", "apice", "A1", 10, 300), v("tiktok", "apice", "SEM", 5, 100)])
    (cel,) = M.agregar(linhas, {("tiktok", "apice"): Decimal(400)}, {"tiktok": date(2026, 10, 15)}, OUT)
    assert cel["cmv_provisorio"] == Decimal(30)
    assert cel["receita_coberta"] == Decimal(300) and cel["cobertura"] == Decimal("0.750000")
    assert (cel["skus_cobertos"], cel["skus_sem_cmp"], cel["unidades_sem_cmp"]) == (1, 1, Decimal(5))


def test_conflito_fica_sem_cmp(tmp_path):
    """Mesmo codigo em duas entidades da marca com CMP diferente: nenhuma escolha."""
    comparativo(tmp_path, [("SP", "LC1", "", "X", [5, 5, 5], 5)])
    lcv(tmp_path, [("0301", "LC1", "", 9.0, "OK")])
    linhas = resolver(tmp_path, [v("tiktok", "lescent", "LC1", 10, 300)])
    (cel,) = M.agregar(linhas, {("tiktok", "lescent"): Decimal(300)}, {"tiktok": date(2026, 10, 15)}, OUT)
    assert linhas[0]["status"] == "conflito" and linhas[0]["cmp"] is None
    assert cel["skus_conflito"] == 1 and cel["cmv_provisorio"] is None


def test_via_gtin_com_ean_da_fonte(tmp_path):
    comparativo(tmp_path, [("SP", "20688", "7898652877919", "SHAMPOO CCH 300ML", [6, 6, 6], 6)])
    (ln,) = resolver(tmp_path, [v("shopee", "apice", "SKU-MKT", 2, 80, produto="Shampoo 300ml",
                                  ean="7898652877919")])
    assert (ln["status"], ln["nivel"], ln["confianca"], ln["cmv"]) == ("coberto", T.NIVEL_VIA_GTIN, "alta",
                                                                       Decimal(12))


def test_kokeshi_shopee_sem_fonte_fica_ausente_nunca_zero(tmp_path):
    comparativo(tmp_path, [("SP", "A1", "", "X", [3, 3, 3], 3)])
    cels = M.agregar([], {("shopee", "kokeshi"): Decimal(1000)}, {"shopee": date(2026, 10, 12)}, OUT)
    (cel,) = cels
    assert cel["status"] == "ausente_na_fonte"
    assert all(cel[k] is None for k in ("receita_fonte", "cmv_provisorio", "cobertura", "receita_coberta"))


def test_sku_ambiguo_sem_sku_e_kit_ficam_sem_cmp(tmp_path):
    comparativo(tmp_path, [("SP", "K1", "", "KIT", [3, 3, 3], 3), ("SP", "A1", "", "X", [3, 3, 3], 3)])
    cat = T.montar_catalogo([{"marca": "Ápice", "sku": "K1", "sku_antigo": None, "tipo": "KT", "gtin_ean": None}], [])
    linhas = resolver(tmp_path, [v("tiktok", "apice", "A1", 1, 10, ambiguo=True),
                                 v("tiktok", "apice", None, 1, 10),
                                 v("tiktok", "apice", "K1", 1, 10)], cat=cat)
    assert [ln["status"] for ln in linhas] == ["sku_ambiguo", "sem_sku", "kit_sem_composicao"]
    assert all(ln["cmp"] is None for ln in linhas)


def test_ks03041_continua_sem_cmp(tmp_path):
    comparativo(tmp_path, [("SP", "KS03041", "7908790700687", "SERUM 10ML", [4, 4, 4], 4)], nome=NOME_KOK)
    (ln,) = resolver(tmp_path, [v("tiktok", "kokeshi", "KS03041", 10, 300, produto="Serum 10ml",
                                  ean="7908790700687")])
    assert (ln["status"], ln["cmp"]) == ("origem_a_confirmar", None)


def test_confianca_predominante_desempata_para_a_menor():
    assert M.confianca_predominante({T.NIVEL_GTIN: Decimal(10), T.NIVEL_PROVISORIO: Decimal(10)}) == "baixa"
    assert M.confianca_predominante({T.NIVEL_GTIN: Decimal(30), T.NIVEL_VOLUME: Decimal(10)}) == "alta"
    assert M.confianca_predominante({}) is None


def test_resumo_publico_sem_valor_nem_sku(tmp_path):
    comparativo(tmp_path, [("SP", "A1", "", "X", [3, 3, 3], 3)])
    linhas = resolver(tmp_path, [v("tiktok", "apice", "A1", 10, 300)])
    cels = M.agregar(linhas, {("tiktok", "apice"): Decimal(300)}, {"tiktok": date(2026, 10, 15)}, OUT)
    texto = json.dumps(M.resumo_publico(cels))
    for proibido in ("receita", "cmv", '"cmp"', "A1", "300"):
        assert proibido not in texto


def test_dry_run_nunca_chama_publicar(tmp_path, monkeypatch, capsys):
    pasta = tmp_path / "dre"
    pasta.mkdir()
    comparativo(pasta, [("SP", "A1", "", "X", [3, 3, 3], 3)])
    monkeypatch.setattr(M, "publicar", lambda *a, **k: (_ for _ in ()).throw(AssertionError("escreveu")))
    monkeypatch.setattr(M, "ler_receita_celulas",
                        lambda neon, c, f: ({("tiktok", "apice"): Decimal(300)}, {"tiktok": date(2026, 10, 15)}))
    monkeypatch.setattr(M, "ler_vendas",
                        lambda dm, c, cortes: ([v("tiktok", "apice", "A1", 10, 300)], T.Catalogo(), {}))
    rc = M.main(["--pasta", str(pasta), "--competencia", "2026-10", "--saida", str(tmp_path / "o")],
                engines=lambda: (object(), object()))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["modo"] == "dry-run" and "escrita" not in out
    assert out["celulas"][0]["cobertura_pct"] == 100.0


# ---------------------------------------------------------------------------
# DRE-CMV-CMP-2 — kits pela BOM explicita e margem por produto
# ---------------------------------------------------------------------------

def bom(*linhas):
    """(marca, kit, componente, quantidade[, descricao])."""
    return M.montar_bom([{"marca": m, "kit_codigo": k, "componente_codigo": c, "quantidade": q,
                          "componente_descricao": (x[0] if x else None), "data_carga": date(2026, 9, 30)}
                         for m, k, c, q, *x in linhas])


def kits(tmp_path, vendas, b, regras=M.REGRAS_KIT):
    _, ind = ler_pasta(tmp_path)
    return M.classificar(ind, T.Catalogo(), vendas, OUT, b, regras)


def test_kit_homogeneo_quantidade_vezes_cmp(tmp_path):
    comparativo(tmp_path, [("SP", "C1", "", "SHAMPOO 300ML", [3, 3, 3], 3)])
    (ln,) = kits(tmp_path, [v("ml", "apice", "KIT3", 2, 100)], bom(("apice", "KIT3", "C1", 3)))
    assert (ln["status"], ln["nivel"], ln["kit_tipo"], ln["cmp"]) == ("coberto", M.NIVEL_KIT_HOMOGENEO, "homogeneo",
                                                                      Decimal("9"))
    assert ln["cmv"] == Decimal("18") and ln["margem_bruta_apos_cmp"] == Decimal("82")


def test_kit_misto_soma_os_componentes_com_a_menor_confianca(tmp_path):
    comparativo(tmp_path, [("SP", "C1", "7898652877919", "SHAMPOO 300ML", [3, 3, 3], 3),
                           ("SP", "C2", "", "CONDICIONADOR", [2, 2, 2], 2)])
    b = bom(("apice", "KITX", "C1", 1, "7898652877919 Shampoo 300ml"), ("apice", "KITX", "C2", 2, "Condicionador"))
    (ln,) = kits(tmp_path, [v("tiktok", "apice", "KITX", 1, 50)], b)
    assert (ln["status"], ln["nivel"], ln["cmp"]) == ("coberto", M.NIVEL_KIT_MISTO, Decimal("7"))
    assert ln["confianca"] == "baixa"            # C1 por GTIN (alta), C2 so' por codigo (baixa): vale a menor


def test_kit_com_componente_sem_cmp_fica_sem_custo_nunca_parcial(tmp_path):
    comparativo(tmp_path, [("SP", "C1", "", "X", [3, 3, 3], 3)])
    (ln,) = kits(tmp_path, [v("ml", "apice", "KITX", 1, 50)], bom(("apice", "KITX", "C1", 1), ("apice", "KITX", "ZZ", 1)))
    assert (ln["status"], ln["cmp"], ln["cmv"]) == ("kit_componente_sem_cmp", None, None)
    assert "ZZ:sem_cmp" in ln["motivo"]


def test_bom_e_escopada_pela_marca(tmp_path):
    comparativo(tmp_path, [("SP", "C1", "", "X", [3, 3, 3], 3)])
    (ln,) = kits(tmp_path, [v("ml", "apice", "KITX", 1, 50)], bom(("barbours", "KITX", "C1", 1)))
    assert ln["cmp"] is None and ln["kit_tipo"] == ""


def test_composicao_ambigua_nao_calcula(tmp_path):
    comparativo(tmp_path, [("SP", "C1", "", "X", [3, 3, 3], 3)])
    for b in (bom(("apice", "KITX", "C1", 1), ("apice", "KITX", "C1", 2)),     # mesmo componente, quantidades distintas
              bom(("apice", "KITX", "C1", 1.5)),                               # quantidade fracionaria
              bom(("apice", "KITX", "", 1))):                                  # componente sem codigo
        (ln,) = kits(tmp_path, [v("ml", "apice", "KITX", 1, 50)], b)
        assert (ln["status"], ln["cmp"]) == ("kit_composicao_ambigua", None)


def test_regras_ligadas_separadamente(tmp_path):
    comparativo(tmp_path, [("SP", "C1", "", "X", [3, 3, 3], 3), ("SP", "C2", "", "Y", [2, 2, 2], 2)])
    b = bom(("apice", "H", "C1", 2), ("apice", "MX", "C1", 1), ("apice", "MX", "C2", 1))
    vendas = [v("ml", "apice", "H", 1, 10), v("ml", "apice", "MX", 1, 10)]
    so_h = kits(tmp_path, vendas, b, ("homogeneo",))
    assert [ln["status"] for ln in so_h] == ["coberto", "kit_regra_nao_aplicada"]
    nenhuma = kits(tmp_path, vendas, b, ())
    assert all(ln["cmp"] is None for ln in nenhuma)


def test_cmp_direto_e_bom_divergentes_viram_conflito(tmp_path):
    comparativo(tmp_path, [("SP", "KITX", "", "KIT", [9, 9, 9], 9), ("SP", "C1", "", "X", [3, 3, 3], 3)])
    (ln,) = kits(tmp_path, [v("ml", "apice", "KITX", 1, 50)], bom(("apice", "KITX", "C1", 2)))
    assert (ln["status"], ln["cmp"]) == ("conflito_kit", None)
    (ok,) = kits(tmp_path, [v("ml", "apice", "KITX", 1, 50)], bom(("apice", "KITX", "C1", 3)))
    assert (ok["status"], ok["cmp"]) == ("coberto", Decimal("9"))        # iguais: sem divergencia


def test_bom_nao_contorna_bloqueio_ks_nem_conflito(tmp_path):
    comparativo(tmp_path, [("SP", "C1", "", "X", [3, 3, 3], 3)], nome=NOME_KOK)
    (ks,) = kits(tmp_path, [v("tiktok", "kokeshi", "KS03041", 1, 50)], bom(("kokeshi", "KS03041", "C1", 1)))
    assert (ks["status"], ks["cmp"]) == ("origem_a_confirmar", None)
    (comp_ks,) = kits(tmp_path, [v("tiktok", "kokeshi", "KITK", 1, 50)], bom(("kokeshi", "KITK", "KS03041", 1)))
    assert comp_ks["status"] == "kit_componente_sem_cmp" and "origem_a_confirmar" in comp_ks["motivo"]


def test_conflito_do_codigo_direto_nao_e_resolvido_pela_bom(tmp_path):
    comparativo(tmp_path, [("SP", "LC1", "", "X", [5, 5, 5], 5)])
    lcv(tmp_path, [("0301", "LC1", "", 9.0, "OK"), ("0301", "C9", "", 4.0, "OK")])
    (ln,) = kits(tmp_path, [v("tiktok", "lescent", "LC1", 1, 50)], bom(("lescent", "LC1", "C9", 1)))
    assert (ln["status"], ln["cmp"]) == ("conflito", None)


def test_margem_da_celula_so_com_cobertura_total(tmp_path):
    comparativo(tmp_path, [("SP", "A1", "", "X", [3, 3, 3], 3)])
    linhas = resolver(tmp_path, [v("tiktok", "apice", "A1", 10, 300), v("tiktok", "apice", "SEM", 5, 100)])
    (cel,) = M.agregar(linhas, {("tiktok", "apice"): Decimal(400)}, {"tiktok": date(2026, 10, 15)}, OUT)
    assert cel["margem_bruta_apos_cmp"] is None                       # 75%: sem margem da celula
    assert cel["margem_bruta_parcela_coberta"] == Decimal(270)        # 300 - 10 x 3, rotulada como parcela
    total = resolver(tmp_path, [v("tiktok", "apice", "A1", 10, 300)])
    (cel100,) = M.agregar(total, {("tiktok", "apice"): Decimal(300)}, {"tiktok": date(2026, 10, 15)}, OUT)
    assert cel100["margem_bruta_apos_cmp"] == Decimal(270)


def test_confianca_da_celula_usa_a_confianca_da_linha(tmp_path):
    comparativo(tmp_path, [("SP", "C1", "", "X", [3, 3, 3], 3)])
    linhas = kits(tmp_path, [v("ml", "apice", "K", 1, 50)], bom(("apice", "K", "C1", 1)))
    (cel,) = M.agregar(linhas, {("ml", "apice"): Decimal(50)}, {"ml": date(2026, 10, 15)}, OUT)
    assert cel["confianca"] == "baixa" and cel["receita_kit_homogeneo"] == Decimal(50)


# ---------------------------------------------------------------------------
# DRE-CMV-CMP-6: visao por produto
# ---------------------------------------------------------------------------

def _celulas(linhas, receitas):
    return M.agregar(linhas, receitas, {"tiktok": date(2026, 10, 15), "shopee": date(2026, 10, 12)}, OUT)


def test_visao_produto_direto_tem_todos_os_campos(tmp_path):
    comparativo(tmp_path, [("SP", "A1", "", "X", [3, 3, 3], 3)])
    linhas = resolver(tmp_path, [v("tiktok", "apice", "A1", 10, 200)])
    (p,) = M.visao_produto(linhas, _celulas(linhas, {("tiktok", "apice"): Decimal(200)}), OUT)
    assert set(p) == set(M.CAMPOS_PRODUTO)
    assert (p["calculavel"], p["metodo"], p["cmp"], p["cmv"]) == (True, "direto", Decimal(3), Decimal(30))
    assert p["margem_bruta_apos_cmp"] == Decimal(170) and p["margem_pct"] == Decimal("85.00")
    assert p["competencia"] == OUT and p["cobertura_da_celula"] == Decimal("1.000000") and p["motivo"] is None


def test_visao_produto_sem_cmp_e_nulo_com_motivo_nunca_zero(tmp_path):
    comparativo(tmp_path, [("SP", "A1", "", "X", [3, 3, 3], 3)])
    linhas = resolver(tmp_path, [v("tiktok", "apice", "A1", 10, 200), v("tiktok", "apice", "SEM", 5, 100)])
    ps = M.visao_produto(linhas, _celulas(linhas, {("tiktok", "apice"): Decimal(300)}), OUT)
    (sem,) = [p for p in ps if p["sku"] == "SEM"]
    assert sem["calculavel"] is False and sem["metodo"] is None
    assert all(sem[k] is None for k in ("cmp", "cmv", "margem_bruta_apos_cmp", "margem_pct",
                                        "competencia_origem_cmp", "carry_forward"))
    assert sem["motivo"].startswith("sem_cmp:") and sem["receita"] == Decimal(100)
    assert {p["cobertura_da_celula"] for p in ps} == {Decimal("0.666667")}


def test_visao_produto_kit_pela_bom_e_metodo_kit_bom(tmp_path):
    comparativo(tmp_path, [("SP", "C1", "", "X", [2, 2, 2], 2), ("SP", "C2", "", "Y", [3, 3, 3], 3)])
    _, ind = ler_pasta(tmp_path)
    bom = M.montar_bom([{"marca": "apice", "kit_codigo": "K1", "componente_codigo": c, "quantidade": q,
                         "componente_descricao": "", "data_carga": None} for c, q in (("C1", 2), ("C2", 1))])
    linhas = M.classificar(ind, T.Catalogo(), [v("tiktok", "apice", "K1", 4, 100)], OUT, bom)
    (p,) = M.visao_produto(linhas, _celulas(linhas, {("tiktok", "apice"): Decimal(100)}), OUT)
    assert (p["metodo"], p["cmp"], p["cmv"]) == ("kit_bom", Decimal(7), Decimal(28))


def test_visao_produto_kit_com_componente_sem_cmp_fica_nao_calculavel(tmp_path):
    comparativo(tmp_path, [("SP", "C1", "", "X", [2, 2, 2], 2)])
    _, ind = ler_pasta(tmp_path)
    bom = M.montar_bom([{"marca": "apice", "kit_codigo": "K1", "componente_codigo": c, "quantidade": 1,
                         "componente_descricao": "", "data_carga": None} for c in ("C1", "FALTA")])
    linhas = M.classificar(ind, T.Catalogo(), [v("tiktok", "apice", "K1", 4, 100)], OUT, bom)
    (p,) = M.visao_produto(linhas, _celulas(linhas, {("tiktok", "apice"): Decimal(100)}), OUT)
    assert p["calculavel"] is False and p["cmv"] is None and p["cmp"] is None
    assert p["motivo"].startswith("kit_componente_sem_cmp:") and "FALTA" in p["motivo"]


def test_visao_produto_mostra_celula_ausente_em_vez_de_sumir():
    """Shopee x Kokeshi nao tem venda na fonte de API: aparece como linha propria."""
    cels = M.agregar([], {("shopee", "kokeshi"): Decimal(500)}, {"shopee": date(2026, 10, 12)}, OUT)
    (p,) = M.visao_produto([], cels, OUT)
    assert (p["tipo_linha"], p["canal"], p["marca"], p["calculavel"]) == ("celula_ausente", "shopee", "kokeshi",
                                                                          False)
    assert p["cmv"] is None and p["margem_bruta_apos_cmp"] is None and "ausente_na_fonte" in p["motivo"]


def test_visao_produto_falha_fechada_em_linha_incoerente():
    base = {"canal": "tiktok", "marca": "apice", "sku": "A1", "produto": "", "receita": Decimal(10),
            "unidades": Decimal(1), "nivel": "", "confianca": "", "motivo": "", "competencia_origem": None,
            "carry_forward": False, "margem_bruta_apos_cmp": None}
    for linha in ({**base, "status": "sem_cmp", "cmp": Decimal(1), "cmv": Decimal(1)},
                  {**base, "status": "coberto", "cmp": None, "cmv": None},
                  {**base, "status": "coberto", "cmp": Decimal(1), "cmv": Decimal(1), "nivel": "DEPARA_SUGERIDO"}):
        try:
            M.visao_produto([linha], [], OUT)
        except ValueError:
            continue
        raise AssertionError(f"aceitou linha incoerente: {linha}")


def test_metodo_depara_aprovado_reservado_e_nunca_emitido():
    """Nenhum nivel de casamento produz `depara_aprovado`: de-para sugerido nao entra."""
    assert "depara_aprovado" in M.METODOS
    assert "depara_aprovado" not in M.METODO_POR_NIVEL.values()


# ---------------------------------------------------------------------------
# DRE-CMV-CMP-7: de-para deterministico (cadastro / GTIN -> um unico CMP 0301)
# ---------------------------------------------------------------------------

def ean(prefixo12: str) -> str:
    """EAN-13 sintetico com digito verificador valido."""
    soma = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(prefixo12))
    return prefixo12 + str((10 - soma % 10) % 10)


G1, G2 = ean("200000000001"), ean("200000000002")


def cad(*linhas, marca="Apice"):
    """linhas: (sku, sku_antigo, gtin)."""
    return T.montar_catalogo([{"marca": marca, "sku": s, "sku_antigo": a, "tipo": "PA", "gtin_ean": g}
                              for s, a, g in linhas], [])


def classif(tmp_path, vendas, cat, comp=OUT, depara=True, bom=None):
    _, ind = ler_pasta(tmp_path)
    return M.classificar(ind, cat, vendas, comp, bom, M.REGRAS_KIT, depara)


def test_depara_pelo_codigo_antigo_do_cadastro(tmp_path):
    comparativo(tmp_path, [("SP", "NOVO1", "", "SHAMPOO", [4, 4, 4], 4)])
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100)], cad(("NOVO1", "VELHO1", None)))
    assert (ln["status"], ln["cmp"], ln["nivel"], ln["confianca"]) == ("coberto", Decimal(4), T.NIVEL_DEPARA_CODIGO,
                                                                       "baixa")
    (p,) = M.visao_produto([ln], [], OUT)
    assert p["metodo"] == "depara_deterministico"


def test_depara_por_gtin_sem_volume_tem_confianca_alta(tmp_path):
    """Via GTIN exige volume; o de-para aceita volume ausente e so bloqueia divergencia."""
    comparativo(tmp_path, [("SP", "NOVO1", G1, "SHAMPOO", [4, 4, 4], 4)])
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100, produto="Shampoo")],
                    cad(("VELHO1", None, G1)))
    assert (ln["cmp"], ln["nivel"], ln["confianca"]) == (Decimal(4), T.NIVEL_DEPARA_GTIN, "alta")


def test_depara_com_volume_concordante_tem_confianca_media(tmp_path):
    comparativo(tmp_path, [("SP", "NOVO1", "", "SHAMPOO 300ML", [4, 4, 4], 4)])
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100, produto="Shampoo 300 ml")],
                    cad(("NOVO1", "VELHO1", None)))
    assert (ln["nivel"], ln["confianca"]) == (T.NIVEL_DEPARA_VOLUME, "media")


def test_depara_desligado_mantem_sem_cmp(tmp_path):
    comparativo(tmp_path, [("SP", "NOVO1", "", "SHAMPOO", [4, 4, 4], 4)])
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100)], cad(("NOVO1", "VELHO1", None)),
                    depara=False)
    assert ln["cmp"] is None and ln["status"] == "sem_cmp"


def test_depara_com_dois_candidatos_fica_humano(tmp_path):
    comparativo(tmp_path, [("SP", "NOVO1", "", "SHAMPOO", [4, 4, 4], 4), ("SP", "NOVO2", G1, "SHAMPOO", [5, 5, 5], 5)])
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100)], cad(("NOVO1", "VELHO1", G1)))
    assert ln["cmp"] is None and "depara_mais_de_um_candidato" in ln["motivo"]


def test_depara_com_volume_divergente_fica_humano(tmp_path):
    comparativo(tmp_path, [("SP", "NOVO1", "", "SHAMPOO 500ML", [4, 4, 4], 4)])
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100, produto="Shampoo 300ml")],
                    cad(("NOVO1", "VELHO1", None)))
    assert ln["cmp"] is None and "depara_evidencia_contraditoria" in ln["motivo"]


def test_depara_com_gtin_divergente_fica_humano(tmp_path):
    comparativo(tmp_path, [("SP", "NOVO1", G2, "SHAMPOO", [4, 4, 4], 4)])
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100)], cad(("NOVO1", "VELHO1", G1)))
    assert ln["cmp"] is None and "depara_evidencia_contraditoria" in ln["motivo"]


def test_nf_contradiz_mas_nunca_cria_depara(tmp_path):
    comparativo(tmp_path, [("SP", "NOVO1", "", "SHAMPOO", [4, 4, 4], 4), ("SP", "OUTRO", "", "SHAMPOO", [6, 6, 6], 6)])
    c = cad(("NOVO1", "VELHO1", None))
    c.nf = T.montar_pares_nf([{"marca": "Apice", "codigo_sku": "VELHO1", "sku_canonico": "OUTRO"}])
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100)], c)
    assert ln["cmp"] is None and "depara_nf_aponta_outro_candidato" in ln["motivo"]
    so_nf = cad()
    so_nf.nf = T.montar_pares_nf([{"marca": "Apice", "codigo_sku": "VELHO1", "sku_canonico": "OUTRO"}])
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100)], so_nf)
    assert ln["cmp"] is None and "depara_sem_ligacao_deterministica" in ln["motivo"]


def test_depara_nunca_usa_outra_filial_nem_outra_marca(tmp_path):
    comparativo(tmp_path, [("1301", "NOVO1", "", "SHAMPOO", [4, 4, 4], 4), ("SP", "NOVO2", "", "X", [5, 5, 5], 5)])
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100)], cad(("NOVO1", "VELHO1", None)))
    assert ln["cmp"] is None and "depara_sem_ligacao_deterministica" in ln["motivo"]
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO2", 10, 100)], cad(("NOVO2", "VELHO2", None),
                                                                              marca="Barbours"))
    assert ln["cmp"] is None


def test_depara_candidato_sem_cmp_vigente_fica_sem_valor(tmp_path):
    comparativo(tmp_path, [("SP", "NOVO1", "", "SHAMPOO", [0, 0, 4], 4)])
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100)], cad(("NOVO1", "VELHO1", None)), comp=MAI)
    assert ln["cmp"] is None and "depara_candidato_sem_cmp_0301" in ln["motivo"]


def test_depara_nao_contorna_ks_nem_conflito(tmp_path):
    comparativo(tmp_path, [("SP", "K9", "", "SERUM", [4, 4, 4], 4)], nome=NOME_KOK)
    (ln,) = classif(tmp_path, [v("tiktok", "kokeshi", "KS03041", 10, 100)],
                    cad(("K9", "KS03041", None), marca="Kokeshi"))
    assert ln["status"] == "origem_a_confirmar" and ln["cmp"] is None
    comparativo(tmp_path, [("SP", "LC1", "", "X", [5, 5, 5], 5), ("SP", "LC9", "", "X", [7, 7, 7], 7)], nome=NOME_BB)
    lcv(tmp_path, [("0301", "LC1", "", 9.0, "OK")])
    (ln,) = classif(tmp_path, [v("tiktok", "lescent", "LC1", 10, 100)], cad(("LC9", "LC1", None), marca="Lescent"))
    assert ln["status"] == "conflito" and ln["cmp"] is None


def test_componente_de_kit_por_depara_fecha_o_kit(tmp_path):
    comparativo(tmp_path, [("SP", "C1", "", "X", [2, 2, 2], 2), ("SP", "NOVO2", "", "Y", [3, 3, 3], 3)])
    bom = M.montar_bom([{"marca": "apice", "kit_codigo": "K1", "componente_codigo": c, "quantidade": 1,
                         "componente_descricao": "", "data_carga": None} for c in ("C1", "VELHO2")])
    linhas = classif(tmp_path, [v("tiktok", "apice", "K1", 4, 100)], cad(("NOVO2", "VELHO2", None)), bom=bom)
    (ln,) = linhas
    assert (ln["status"], ln["cmp"], ln["nivel"], ln["confianca"]) == ("coberto", Decimal(5), M.NIVEL_KIT_MISTO,
                                                                       "baixa")
    (cel,) = M.agregar(linhas, {("tiktok", "apice"): Decimal(100)}, {"tiktok": date(2026, 10, 15)}, OUT)
    assert cel["receita_depara"] == 0 and cel["receita_kit_misto"] == Decimal(100)


def test_agregar_mede_receita_por_depara(tmp_path):
    comparativo(tmp_path, [("SP", "NOVO1", "", "SHAMPOO", [4, 4, 4], 4), ("SP", "A2", "", "Y", [2, 2, 2], 2)])
    linhas = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100), v("tiktok", "apice", "A2", 1, 50)],
                     cad(("NOVO1", "VELHO1", None)))
    (cel,) = M.agregar(linhas, {("tiktok", "apice"): Decimal(150)}, {"tiktok": date(2026, 10, 15)}, OUT)
    assert cel["receita_depara"] == Decimal(100) and cel["receita_coberta"] == Decimal(150)


def test_depara_ligado_por_padrao_na_pipeline_oficial(tmp_path):
    comparativo(tmp_path, [("SP", "NOVO1", "", "SHAMPOO", [4, 4, 4], 4)])
    _, ind = ler_pasta(tmp_path)
    (ln,) = M.classificar(ind, cad(("NOVO1", "VELHO1", None)), [v("tiktok", "apice", "VELHO1", 10, 100)], OUT)
    assert ln["cmp"] == Decimal(4)


def test_depara_recusa_gtin_reaproveitado(tmp_path):
    """Mesmo GTIN em produtos de volume incompativel (mesmo fora do escopo): nao casa."""
    comparativo(tmp_path, [("SP", "NOVO1", G1, "SHAMPOO 300ML", [4, 4, 4], 4)])
    comparativo(tmp_path, [("SP", "OUTRO", G1, "SHAMPOO 500ML", [6, 6, 6], 6)], nome=NOME_BB)
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO1", 10, 100)], cad(("VELHO1", None, G1)))
    assert ln["cmp"] is None and "depara_gtin_reutilizado" in ln["motivo"]


def test_depara_nao_usa_candidato_de_entidade_fora_da_marca(tmp_path):
    """Alias do cadastro Apice aponta para um codigo que so existe na BB_VAREJO: nao casa."""
    comparativo(tmp_path, [("SP", "NOVO9", "", "SHAMPOO", [4, 4, 4], 4)], nome=NOME_BB)
    (ln,) = classif(tmp_path, [v("tiktok", "apice", "VELHO9", 10, 100)], cad(("NOVO9", "VELHO9", None)))
    assert ln["cmp"] is None and "depara_sem_ligacao_deterministica" in ln["motivo"]
