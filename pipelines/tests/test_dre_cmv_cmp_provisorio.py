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
