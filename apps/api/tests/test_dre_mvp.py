"""DRE-MVP-1 — trava o contrato `docs/dre_contrato_0.md` na API.

O que se prova aqui:
  * sinal: ML/Shopee gravam custo positivo, o repasse TikTok grava negativo;
    sinal inesperado NAO e' corrigido com abs();
  * ausencia != zero em todos os componentes;
  * afiliado TikTok nunca entra na margem (ja esta dentro de taxas);
  * comissao do ML entra uma unica vez;
  * classificacao COMPLETA / PARCIAL / NAO_CALCULAVEL com motivo objetivo;
  * insumo publicado ausente, vencido ou de lote nao concluido falha fechado,
    sem cair para o `total_fees` congelado do mart (DRE-DATA-BRIDGE-1);
  * a rota nao toca o Data Mart;
  * agregacao (canal, marca, canal x marca, total) soma celulas, nunca inventa;
  * filtros de periodo, marca e canal na rota.

Numeros sinteticos de proposito: os valores reais ficam na reconciliacao
read-only do PR, nao num repositorio publico.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from app import database
from app.database import get_db
from app.main import app
from app.routers import dre as dre_router
from app.schemas.dre import DREResponse
from app.services import dre_service as dre
from fastapi.testclient import TestClient

JUL = date(2026, 7, 1)
HOJE = date(2026, 10, 13)  # julho maduro (M+2 = setembro terminou); agosto nao
AGORA = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
PUBLICADO = AGORA - timedelta(hours=6)


# ---------------------------------------------------------------------------
# Fabricas de linhas — dicionarios, como `.mappings().all()` devolve
# ---------------------------------------------------------------------------

def mart(canal="ml", marca="kokeshi", mes=JUL, dias=31, linhas=None, gmv=1_000_000.0,
         fees=150_000.0, fees_n=None, ads=40_000.0, ads_n=None, frete=130_000.0, frete_n=None, **over):
    linhas = dias if linhas is None else linhas
    row = {
        "mes": mes, "marketplace_id": dre.MKT_POR_CANAL[canal], "marca": marca,
        "dias": dias, "linhas": linhas, "gmv": gmv,
        "fees": fees, "fees_n": linhas if fees_n is None else fees_n, "fees_neg_n": 0,
        "ads": ads, "ads_n": linhas if ads_n is None else ads_n, "ads_neg_n": 0,
        "frete": frete, "frete_n": linhas if frete_n is None else frete_n, "frete_neg_n": 0,
        "max_date": date(mes.year, mes.month, dias), "ingested_at": None,
    }
    if canal == "tiktok":
        row.update(fees=-280_000.0, ads=None, ads_n=0, frete=None, frete_n=0)
    row.update(over)
    return row


def _pub(mes, canal, marca, componente, valor, *, sinal=None, status="reconciliado", motivo="ok",
         publicado_em=None, lote_status="success", provisorio=None):
    """Uma linha de `marts.fact_dre_insumo_mensal`, como o publisher grava."""
    return {
        "competencia": mes, "canal": canal, "marca": marca, "componente": componente, "valor": valor,
        "sinal_origem": sinal, "cobertura": None, "provisorio_share": provisorio,
        "status_reconciliacao": "ausente_na_origem" if valor is None else status, "motivo": motivo,
        # Forma REAL da publicacao: data fiscal como meia-noite BRT = 03:00 UTC.
        # Com 00:00 UTC o Pydantic aceitava e escondia o 500 de producao.
        "data_max_origem": datetime(mes.year, mes.month, 28, 3, 0, tzinfo=timezone.utc),
        "publicado_em": publicado_em or PUBLICADO, "lote_id": 7, "lote_status": lote_status,
    }


def cmv(canal="ml", marca="kokeshi", mes=JUL, receita_fiscal=1_000_000.0, custo=300_000.0, prov=None,
        status="reconciliado", **kw):
    """Linhas publicadas de CMV (`cmv` + `cmv_receita_fiscal`). `prov` e' valor absoluto."""
    share = (prov / custo) if (prov and custo) else None
    return [
        _pub(mes, canal, marca, "cmv", custo, sinal=1, status=status, provisorio=share, **kw),
        _pub(mes, canal, marca, "cmv_receita_fiscal", receita_fiscal, status="nao_avaliado", **kw),
    ]


def repasse(marca="kokeshi", mes=JUL, fee_and_tax=-290_000.0, frete=-12_000.0, residuo=0.0, violacoes=0,
            status="reconciliado", **kw):
    """As sete linhas publicadas do repasse TikTok de uma coorte."""
    return [
        _pub(mes, "tiktok", marca, "tiktok_taxa_repasse", fee_and_tax, sinal=-1, status=status, **kw),
        _pub(mes, "tiktok", marca, "tiktok_frete_repasse", frete, sinal=-1, status=status, **kw),
        _pub(mes, "tiktok", marca, "tiktok_afiliado_repasse", -90_000.0, sinal=-1, status=status, **kw),
        _pub(mes, "tiktok", marca, "tiktok_receita_repasse", 1_050_000.0, status=status, **kw),
        _pub(mes, "tiktok", marca, "tiktok_residuo_identidade", residuo, status=status, **kw),
        _pub(mes, "tiktok", marca, "tiktok_tx_afiliado_maior_taxa", float(violacoes), status=status, **kw),
        _pub(mes, "tiktok", marca, "tiktok_transacoes", 1000.0, status=status, **kw),
    ]


def afiliado(marca="kokeshi", mes=JUL, creator=-50_000.0, partner=-30_000.0, ads=-10_000.0):
    return {"mes": mes, "marca": marca, "creator": creator, "partner": partner, "ads": ads}


def celula(canal="ml", m=None, c="padrao", r=None, a=None, hoje=HOJE, agora=AGORA):
    m = m if m is not None else mart(canal)
    c = cmv(canal, marca=m["marca"], mes=m["mes"]) if c == "padrao" else (c or [])
    if canal == "tiktok":
        r = repasse(marca=m["marca"], mes=m["mes"]) if r is None else r
        a = afiliado() if a is None else a
    else:
        r = []
    cmv_idx, rep_idx = dre.indexar_insumos(list(c) + list(r), agora)
    k = (m["mes"], canal, m["marca"])
    ins = dre.InsumosCelula(mes=m["mes"], canal=canal, marca=m["marca"], mart=m, afiliado=a,
                            cmv=cmv_idx.get(k), repasse=rep_idx.get(k))
    return dre.montar_celula_base(ins, hoje)


def montar(mart_rows, afiliado_rows=(), cmv_rows=(), repasse_rows=(), canais=("tiktok", "ml", "shopee"),
           marcas=("kokeshi", "barbours"), inicio=JUL, fim=date(2026, 7, 31), hoje=HOJE, agora=AGORA,
           tabela_existe=True):
    linhas = [x for g in cmv_rows for x in g] + [x for g in repasse_rows for x in g]
    return dre.montar_dre(
        inicio=inicio, fim=fim, canais=list(canais), marcas=list(marcas),
        mart_rows=list(mart_rows), afiliado_rows=list(afiliado_rows),
        insumos=dre.LeituraInsumos(tabela_existe=tabela_existe, linhas=linhas), hoje=hoje, agora=agora,
    )


# ---------------------------------------------------------------------------
# Sinal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("comp,canal,bruto,esperado", [
    ("taxas", "ml", 100.0, 100.0),
    ("taxas", "shopee", 100.0, 100.0),
    ("taxas", "tiktok", -100.0, 100.0),
    ("frete", "tiktok", -7.5, 7.5),
    ("afiliados", "tiktok", -3.0, 3.0),
    ("taxas", "ml", 0.0, 0.0),
])
def test_normalizar_converte_para_magnitude(comp, canal, bruto, esperado):
    assert dre.normalizar_custo(comp, canal, bruto) == (esperado, True)


@pytest.mark.parametrize("comp,canal,bruto", [
    ("taxas", "ml", -100.0), ("taxas", "shopee", -1.0), ("taxas", "tiktok", 100.0), ("cmv", "ml", -5.0),
])
def test_sinal_contrario_nao_e_corrigido_com_abs(comp, canal, bruto):
    assert dre.normalizar_custo(comp, canal, bruto) == (None, False)


def test_nulo_continua_nulo_na_normalizacao():
    assert dre.normalizar_custo("taxas", "ml", None) == (None, True)


def test_dia_negativo_na_comissao_ml_vira_sinal_inesperado_e_tira_a_margem():
    c = celula("ml", m=mart("ml", fees_neg_n=1))
    assert c["componentes"]["taxas"]["status"] == dre.SINAL_INESPERADO
    assert c["componentes"]["taxas"]["valor"] is None
    assert c["classificacao"] == dre.NAO_CALCULAVEL


def test_repasse_tiktok_positivo_vira_sinal_inesperado():
    c = celula("tiktok", m=mart("tiktok"), r=repasse(fee_and_tax=290_000.0))
    assert c["componentes"]["taxas"]["status"] == dre.SINAL_INESPERADO
    assert c["classificacao"] == dre.NAO_CALCULAVEL


def test_ads_negativo_na_shopee_vira_sinal_inesperado():
    c = celula("shopee", m=mart("shopee", ads_neg_n=2), c=cmv("shopee"))
    assert c["componentes"]["ads"]["status"] == dre.SINAL_INESPERADO


# ---------------------------------------------------------------------------
# Ausencia versus zero
# ---------------------------------------------------------------------------

def test_comissao_ml_nao_carregada_e_nula_nao_zero():
    c = celula("ml", m=mart("ml", fees=None, fees_n=0))
    tx = c["componentes"]["taxas"]
    assert tx["valor"] is None and tx["status"] == dre.INDISPONIVEL
    assert c["classificacao"] == dre.NAO_CALCULAVEL
    assert c["margem_contribuicao_conhecida"]["valor"] is None


def test_comissao_ml_medida_zero_e_zero_disponivel():
    c = celula("ml", m=mart("ml", fees=0.0))
    tx = c["componentes"]["taxas"]
    assert tx["valor"] == 0.0 and tx["status"] == dre.DISPONIVEL


def test_comissao_ml_em_parte_dos_dias_e_parcial_e_fica_fora_da_margem():
    c = celula("ml", m=mart("ml", fees_n=5))
    assert c["componentes"]["taxas"]["status"] == dre.COBERTURA_PARCIAL
    assert c["classificacao"] == dre.NAO_CALCULAVEL


def test_ads_ausente_nao_vira_zero_e_entra_em_faltam():
    c = celula("ml", m=mart("ml", ads=None, ads_n=0))
    assert c["componentes"]["ads"]["valor"] is None
    assert c["classificacao"] == dre.PARCIAL
    assert "ads" in c["margem_contribuicao_conhecida"]["faltam"]
    assert "ads" not in c["margem_contribuicao_conhecida"]["componentes_usados"]


def test_impostos_e_devolucoes_sempre_indisponiveis_nunca_zero():
    c = celula("ml")
    for nome in ("impostos", "devolucoes"):
        assert c["componentes"][nome]["valor"] is None
        assert c["componentes"][nome]["status"] == dre.INDISPONIVEL
        assert nome in [a["componente"] for a in c["componentes_ausentes"]]


def test_tiktok_sem_ads_e_ausente_nao_zero():
    c = celula("tiktok", m=mart("tiktok"))
    assert c["componentes"]["ads"]["valor"] is None
    assert c["componentes"]["ads"]["status"] == dre.INDISPONIVEL


# ---------------------------------------------------------------------------
# Afiliado TikTok nunca duplicado
# ---------------------------------------------------------------------------

def test_afiliado_tiktok_nao_entra_na_margem():
    c = celula("tiktok", m=mart("tiktok", gmv=1_000_000.0))
    m = c["margem_contribuicao_conhecida"]
    assert c["componentes"]["afiliados"]["somavel"] is False
    assert c["componentes"]["afiliados"]["valor"] == 90_000.0
    assert "afiliados" not in m["componentes_usados"]
    # receita - taxas do repasse - frete do repasse - cmv
    assert m["valor"] == pytest.approx(1_000_000 - 290_000 - 12_000 - 300_000)


def test_margem_tiktok_nao_muda_quando_o_afiliado_muda():
    a = celula("tiktok", m=mart("tiktok"), a=afiliado(creator=-1.0, partner=0.0, ads=0.0))
    b = celula("tiktok", m=mart("tiktok"), a=afiliado(creator=-200_000.0, partner=-50_000.0, ads=-20_000.0))
    assert a["margem_contribuicao_conhecida"]["valor"] == b["margem_contribuicao_conhecida"]["valor"]


def test_teste_negativo_somar_afiliado_daria_outra_margem():
    c = celula("tiktok", m=mart("tiktok"))
    m = c["margem_contribuicao_conhecida"]["valor"]
    errado = m - c["componentes"]["afiliados"]["valor"]
    assert errado != m  # o teste detectaria a regressao, nao so' passa


def test_afiliado_maior_que_a_taxa_aborta_a_celula():
    c = celula("tiktok", m=mart("tiktok"), r=repasse(fee_and_tax=-50_000.0))
    assert c["classificacao"] == dre.NAO_CALCULAVEL
    assert any("afiliado maior que a taxa" in m for m in c["motivos_classificacao"])


def test_identidade_do_repasse_violada_tira_a_taxa_tiktok():
    c = celula("tiktok", m=mart("tiktok"), r=repasse(residuo=150.0))
    assert c["componentes"]["taxas"]["status"] == dre.NAO_RECONCILIADO
    assert c["classificacao"] == dre.NAO_CALCULAVEL


def test_afiliado_expoe_as_tres_partes_em_magnitude():
    c = celula("tiktok", m=mart("tiktok"))
    assert c["componentes"]["afiliados"]["partes"] == {"creator": 50_000.0, "partner": 30_000.0, "ads": 10_000.0}


# ---------------------------------------------------------------------------
# Comissao ML entra uma unica vez
# ---------------------------------------------------------------------------

def test_comissao_ml_entra_uma_unica_vez():
    c = celula("ml", m=mart("ml", gmv=1_000_000.0, fees=150_000.0, ads=40_000.0, frete=130_000.0))
    m = c["margem_contribuicao_conhecida"]
    assert m["componentes_usados"].count("taxas") == 1
    assert m["componentes_usados"] == ["taxas", "ads", "frete", "cmv"]
    assert m["valor"] == pytest.approx(1_000_000 - 150_000 - 40_000 - 130_000 - 300_000)
    assert c["componentes"]["taxas"]["valor"] == 150_000.0
    assert c["componentes"]["ads"]["valor"] == 40_000.0  # Ads nao carrega comissao
    assert c["componentes"]["frete"]["valor"] == 130_000.0


def test_comissao_ml_vem_de_total_fees():
    c = celula("ml")
    assert c["componentes"]["taxas"]["fonte"].endswith("total_fees")


# ---------------------------------------------------------------------------
# Classificacao
# ---------------------------------------------------------------------------

def test_ml_maduro_com_cmv_reconciliado_e_parcial_nunca_completa():
    c = celula("ml")
    assert c["classificacao"] == dre.PARCIAL
    assert c["motivos_classificacao"] == []
    assert c["completude"] == {"usados": 4, "aplicaveis": 6, "texto": "4 de 6 custos"}


def test_completa_e_inalcancavel_enquanto_impostos_nao_ratificados():
    for canal in ("ml", "tiktok"):
        c = celula(canal, m=mart(canal))
        assert c["classificacao"] != dre.COMPLETA


def test_mes_imaturo_nao_e_calculavel():
    ago = date(2026, 8, 1)
    c = celula("ml", m=mart("ml", mes=ago), c=cmv("ml", mes=ago))
    assert c["classificacao"] == dre.NAO_CALCULAVEL
    assert any("mes imaturo" in m for m in c["motivos_classificacao"])


def test_regra_de_maturacao_m_mais_2():
    assert dre.mes_maduro(JUL, date(2026, 10, 1)) is True
    assert dre.mes_maduro(JUL, date(2026, 9, 30)) is False
    assert dre.mes_maduro(date(2026, 8, 1), date(2026, 10, 31)) is False
    assert dre.mes_maduro(date(2026, 8, 1), date(2026, 11, 1)) is True


def test_shopee_nao_calculavel_por_taxa_sem_contraprova():
    c = celula("shopee", m=mart("shopee"), c=cmv("shopee"))
    assert c["componentes"]["taxas"]["status"] == dre.NAO_CONFIRMADO
    assert c["componentes"]["frete"]["status"] == dre.NAO_CONFIRMADO
    assert c["classificacao"] == dre.NAO_CALCULAVEL


def test_cmv_fora_da_faixa_de_reconciliacao_nao_e_usado():
    c = celula("ml", c=cmv("ml", receita_fiscal=450_000.0))
    assert c["componentes"]["cmv"]["status"] == dre.NAO_RECONCILIADO
    assert c["classificacao"] == dre.NAO_CALCULAVEL


@pytest.mark.parametrize("fiscal,status", [(950_000.0, dre.DISPONIVEL), (1_050_000.0, dre.DISPONIVEL),
                                           (949_000.0, dre.NAO_RECONCILIADO), (1_051_000.0, dre.NAO_RECONCILIADO)])
def test_faixa_de_reconciliacao_do_cmv_e_inclusiva(fiscal, status):
    assert celula("ml", c=cmv("ml", receita_fiscal=fiscal))["componentes"]["cmv"]["status"] == status


def test_cmv_integralmente_provisorio_nao_fecha_o_mes():
    c = celula("ml", c=cmv("ml", custo=300_000.0, prov=300_000.0))
    assert c["componentes"]["cmv"]["status"] == dre.PROVISORIO
    assert c["classificacao"] == dre.NAO_CALCULAVEL


def test_cmv_parcialmente_provisorio_e_usado_com_aviso():
    c = celula("ml", c=cmv("ml", custo=300_000.0, prov=30_000.0))
    assert c["classificacao"] == dre.PARCIAL
    assert any("10% do CMV" in a for a in c["avisos_qualidade"])


def test_receita_com_dias_faltando_nao_e_calculavel():
    c = celula("ml", m=mart("ml", dias=28))
    assert c["componentes"]["receita"]["status"] == dre.COBERTURA_PARCIAL
    assert c["classificacao"] == dre.NAO_CALCULAVEL


def test_duas_lojas_por_dia_cobertura_mede_linhas():
    c = celula("ml", m=mart("ml", dias=31, linhas=62, fees_n=31))
    assert c["componentes"]["taxas"]["status"] == dre.COBERTURA_PARCIAL


# ---------------------------------------------------------------------------
# Insumo publicado: falha fechada, sem fallback para o mart congelado
# ---------------------------------------------------------------------------

def test_sem_insumo_publicado_nao_usa_total_fees_do_mart():
    c = celula("tiktok", m=mart("tiktok", fees=-280_000.0), c=None, r=[])
    assert c["componentes"]["taxas"]["status"] == dre.SNAPSHOT_AUSENTE
    assert c["componentes"]["taxas"]["valor"] is None
    assert c["componentes"]["cmv"]["status"] == dre.SNAPSHOT_AUSENTE
    assert c["classificacao"] == dre.NAO_CALCULAVEL


def test_lote_vencido_nao_e_exibido_como_atual():
    velho = AGORA - dre.INSUMO_IDADE_MAXIMA - timedelta(minutes=1)
    c = celula("ml", c=cmv("ml", publicado_em=velho))
    cm = c["componentes"]["cmv"]
    assert cm["status"] == dre.SNAPSHOT_VENCIDO and cm["valor"] is None
    assert "limite de 48 h" in cm["motivo"]
    assert cm["publicado_em"] == velho
    assert c["classificacao"] == dre.NAO_CALCULAVEL


def test_lote_no_limite_de_idade_ainda_vale():
    no_limite = AGORA - dre.INSUMO_IDADE_MAXIMA
    assert celula("ml", c=cmv("ml", publicado_em=no_limite))["componentes"]["cmv"]["status"] == dre.DISPONIVEL


def test_repasse_vencido_tira_taxa_e_frete_do_tiktok():
    velho = AGORA - timedelta(days=5)
    c = celula("tiktok", m=mart("tiktok"), r=repasse(publicado_em=velho))
    assert c["componentes"]["taxas"]["status"] == dre.SNAPSHOT_VENCIDO
    assert c["componentes"]["frete"]["status"] == dre.SNAPSHOT_VENCIDO


def test_lote_nao_concluido_e_ignorado():
    c = celula("ml", c=cmv("ml", lote_status="failed"))
    assert c["componentes"]["cmv"]["status"] == dre.SNAPSHOT_AUSENTE
    assert "lote" in c["componentes"]["cmv"]["motivo"]


def test_ausente_na_origem_e_indisponivel_nao_zero():
    c = celula("ml", c=cmv("ml", custo=None))
    assert c["componentes"]["cmv"]["status"] == dre.INDISPONIVEL
    assert c["componentes"]["cmv"]["valor"] is None


def test_cmv_publicado_como_nao_reconciliado_nao_e_usado():
    c = celula("ml", c=cmv("ml", status="nao_reconciliado"))
    assert c["componentes"]["cmv"]["status"] == dre.NAO_RECONCILIADO
    assert c["classificacao"] == dre.NAO_CALCULAVEL


def test_razao_e_conferida_de_novo_com_o_gmv_atual():
    # publicado como reconciliado, mas o GMV mudou desde a publicacao
    c = celula("ml", m=mart("ml", gmv=1_300_000.0), c=cmv("ml", receita_fiscal=1_000_000.0))
    assert c["componentes"]["cmv"]["status"] == dre.NAO_RECONCILIADO
    assert "GMV atual" in c["componentes"]["cmv"]["motivo"]


def test_convencao_de_sinal_publicada_diferente_e_recusada():
    linhas = cmv("ml")
    linhas[0]["sinal_origem"] = -1
    assert celula("ml", c=linhas)["componentes"]["cmv"]["status"] == dre.SINAL_INESPERADO


def test_violacao_publicada_de_afiliado_tira_a_taxa():
    c = celula("tiktok", m=mart("tiktok"), r=repasse(violacoes=3))
    assert c["componentes"]["taxas"]["status"] == dre.NAO_RECONCILIADO


def test_componente_publicado_expoe_data_as_of_e_publicado_em():
    c = celula("tiktok", m=mart("tiktok"))
    tx = c["componentes"]["taxas"]
    assert tx["publicado_em"] == PUBLICADO
    assert tx["data_as_of"] == datetime(2026, 7, 28, 3, 0, tzinfo=timezone.utc)
    assert c["data_maxima"]["insumo_publicado_em"] == PUBLICADO


def test_divergencia_mart_vs_repasse_vira_aviso():
    c = celula("tiktok", m=mart("tiktok", fees=-200_000.0))
    assert any("diverge" in a for a in c["avisos_qualidade"])
    assert c["componentes"]["taxas"]["valor"] == 290_000.0  # sempre o repasse


def test_resumo_usa_a_publicacao_mais_antiga_como_referencia():
    """Revisao estrita #68, item 4: conjunto misto nao pode parecer novo."""
    velho, novo = AGORA - timedelta(hours=40), AGORA - timedelta(hours=1)
    res = montar(mart_rows=[mart("ml", "kokeshi"), mart("ml", "barbours")],
                 cmv_rows=[cmv("ml", "kokeshi", publicado_em=velho), cmv("ml", "barbours", publicado_em=novo)],
                 canais=("ml",))
    r = res["fontes"]["insumos_dre"]
    assert r["publicado_em"] == velho
    assert r["publicado_em_mais_antigo"] == velho and r["publicado_em_mais_recente"] == novo
    assert r["estado"] == "ok"


def test_resumo_parcialmente_vencido_expoe_os_dois_extremos():
    velho, novo = AGORA - timedelta(hours=60), AGORA - timedelta(hours=1)
    res = montar(mart_rows=[mart("ml", "kokeshi"), mart("ml", "barbours")],
                 cmv_rows=[cmv("ml", "kokeshi", publicado_em=velho), cmv("ml", "barbours", publicado_em=novo)],
                 canais=("ml",))
    r = res["fontes"]["insumos_dre"]
    assert r["estado"] == "parcialmente_vencido"
    assert (r["publicado_em"], r["publicado_em_mais_recente"]) == (velho, novo)
    kok = next(c for c in res["por_canal_marca"] if c["marca"] == "kokeshi")
    assert kok["componentes"]["cmv"]["status"] == dre.SNAPSHOT_VENCIDO
    ml = res["por_canal"][0]
    assert ml["componentes"]["cmv"]["publicado_em"] == velho  # agregado: a mais antiga


def test_resumo_dos_insumos_no_topo_da_resposta():
    res = montar(mart_rows=[mart("ml")], cmv_rows=[cmv("ml")], canais=("ml",), marcas=("kokeshi",))
    assert res["fontes"]["insumos_dre"]["estado"] == "ok"
    sem_tabela = montar(mart_rows=[mart("ml")], canais=("ml",), marcas=("kokeshi",), tabela_existe=False)
    assert sem_tabela["fontes"]["insumos_dre"]["estado"] == "tabela_ausente"
    cel = sem_tabela["celulas_base"][0]["componentes"]["cmv"]
    assert cel["status"] == dre.SNAPSHOT_AUSENTE and "migration 025" in cel["motivo"]
    velho = montar(mart_rows=[mart("ml")], cmv_rows=[cmv("ml", publicado_em=AGORA - timedelta(days=9))],
                   canais=("ml",), marcas=("kokeshi",))
    assert velho["fontes"]["insumos_dre"]["estado"] == "vencido"


# ---------------------------------------------------------------------------
# Agregacao
# ---------------------------------------------------------------------------

def _dre_ml_tiktok():
    return montar(
        mart_rows=[mart("ml", "kokeshi"), mart("ml", "barbours", gmv=500_000.0, fees=60_000.0),
                   mart("tiktok", "kokeshi")],
        cmv_rows=[cmv("ml", "kokeshi"), cmv("ml", "barbours", receita_fiscal=500_000.0, custo=100_000.0),
                  cmv("tiktok", "kokeshi")],
        repasse_rows=[repasse("kokeshi")], afiliado_rows=[afiliado("kokeshi")],
        canais=("tiktok", "ml"),
    )


def test_agregado_por_canal_soma_as_celulas():
    res = _dre_ml_tiktok()
    ml = next(c for c in res["por_canal"] if c["canal"] == "ml")
    assert ml["componentes"]["receita"]["valor"] == 1_500_000.0
    assert ml["componentes"]["taxas"]["valor"] == 210_000.0
    base = [c for c in res["celulas_base"] if c["canal"] == "ml"]
    assert ml["margem_contribuicao_conhecida"]["valor"] == pytest.approx(
        sum(c["margem_contribuicao_conhecida"]["valor"] for c in base))


def test_agregado_por_marca_soma_margens_e_nao_descarta_ads_do_ml():
    res = _dre_ml_tiktok()
    kok = next(c for c in res["por_marca"] if c["marca"] == "kokeshi")
    base = [c for c in res["celulas_base"] if c["marca"] == "kokeshi"]
    assert kok["margem_contribuicao_conhecida"]["valor"] == pytest.approx(
        sum(c["margem_contribuicao_conhecida"]["valor"] for c in base))
    assert "ads" in kok["margem_contribuicao_conhecida"]["componentes_usados"]
    assert "ads" in kok["margem_contribuicao_conhecida"]["faltam"]  # faltou no TikTok
    assert kok["componentes"]["ads"]["status"] == dre.COBERTURA_PARCIAL


def test_nao_calculavel_contamina_o_agregado():
    res = montar(mart_rows=[mart("ml", "kokeshi"), mart("shopee", "kokeshi")],
                 cmv_rows=[cmv("ml", "kokeshi"), cmv("shopee", "kokeshi")], canais=("ml", "shopee"))
    assert res["total"]["classificacao"] == dre.NAO_CALCULAVEL
    assert res["total"]["margem_contribuicao_conhecida"]["valor"] is None
    assert any("shopee" in m for m in res["total"]["motivos_classificacao"])


def test_marca_sem_canal_nao_vira_linha_zerada():
    res = _dre_ml_tiktok()
    assert ("tiktok", "barbours") not in {(c["canal"], c["marca"]) for c in res["por_canal_marca"]}


def test_varios_meses_somam_e_herdam_a_pior_classificacao():
    ago = date(2026, 8, 1)
    res = montar(mart_rows=[mart("ml", "kokeshi"), mart("ml", "kokeshi", mes=ago)],
                 cmv_rows=[cmv("ml", "kokeshi"), cmv("ml", "kokeshi", mes=ago)],
                 canais=("ml",), marcas=("kokeshi",), fim=date(2026, 8, 31))
    assert res["periodo"]["meses"] == ["2026-07", "2026-08"]
    tot = res["total"]
    assert tot["componentes"]["receita"]["valor"] == 2_000_000.0
    assert tot["classificacao"] == dre.NAO_CALCULAVEL  # agosto imaturo


def test_resposta_valida_no_schema():
    DREResponse.model_validate(_dre_ml_tiktok())


def test_periodo_desalinhado_ao_mes_e_recusado():
    with pytest.raises(ValueError):
        dre.meses_do_periodo(date(2026, 7, 2), date(2026, 7, 31))
    with pytest.raises(ValueError):
        dre.meses_do_periodo(JUL, date(2026, 7, 30))


def test_nada_se_chama_margem_liquida():
    texto = str(_dre_ml_tiktok()).lower()
    assert "liquida" not in texto and "líquida" not in texto and "lucro" not in texto


# ---------------------------------------------------------------------------
# Rota e filtros
# ---------------------------------------------------------------------------

class _Res:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._rows[0] if self._rows else None


class FakeNeon:
    """Roteia pelo texto do SQL e registra os parametros recebidos."""

    def __init__(self, mart_rows=(), afiliado_rows=(), insumos=(), marcas=("barbours", "kokeshi"),
                 tabela_existe=True):
        self.mart_rows, self.afiliado_rows, self.marcas = list(mart_rows), list(afiliado_rows), list(marcas)
        self.insumos, self.tabela_existe = list(insumos), tabela_existe
        self.params: list[dict] = []
        self.sqls: list[str] = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.params.append(params or {})
        self.sqls.append(sql)
        if "to_regclass" in sql:
            return _Res([self.tabela_existe])
        if "fact_dre_insumo_mensal" in sql:
            return _Res([r for r in self.insumos if r["canal"] in params["canais"] and r["marca"] in params["marcas"]])
        if "fact_marketplace_daily_performance" in sql:
            return _Res([r for r in self.mart_rows
                         if r["marketplace_id"] in params["mkt_ids"] and r["marca"] in params["marcas"]])
        if "fact_tiktok_affiliate_cost_order_monthly" in sql:
            return _Res([r for r in self.afiliado_rows if r["marca"] in params["marcas"]])
        if "dim_loja" in sql:
            return _Res(self.marcas)
        raise AssertionError(f"SQL inesperado: {sql[:80]}")


class _DataMartQueExplode:
    """Qualquer uso derruba o teste: a rota nao pode tocar o Data Mart."""

    def __getattr__(self, nome):
        raise AssertionError(f"a rota da DRE tocou o Data Mart ({nome})")


def _insumos_padrao():
    return (cmv("ml", "kokeshi") + cmv("ml", "barbours") + cmv("tiktok", "kokeshi")
            + cmv("shopee", "kokeshi", status="nao_reconciliado") + repasse("kokeshi"))


@pytest.fixture
def cliente(monkeypatch):
    neon = FakeNeon(
        mart_rows=[mart("ml", "kokeshi"), mart("ml", "barbours"), mart("tiktok", "kokeshi"), mart("shopee", "kokeshi")],
        afiliado_rows=[afiliado("kokeshi")], insumos=_insumos_padrao(),
    )
    app.dependency_overrides[get_db] = lambda: neon
    monkeypatch.setattr(database, "datamart_engine", _DataMartQueExplode())
    monkeypatch.setattr(dre_router, "today_brt", lambda: HOJE)

    class _Relogio(datetime):
        @classmethod
        def now(cls, tz=None):
            return AGORA

    monkeypatch.setattr(dre_router, "datetime", _Relogio)
    import app.deps.filters as filtros
    monkeypatch.setattr(filtros, "today_brt", lambda: HOJE)
    yield TestClient(app), neon
    app.dependency_overrides.pop(get_db, None)


def test_rota_mes_inteiro_devolve_os_quatro_recortes(cliente):
    tc, _ = cliente
    r = tc.get("/api/v1/dre", params={"date_from": "2026-07-01", "date_to": "2026-07-31"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] is not None
    assert {c["canal"] for c in body["por_canal"]} == {"tiktok", "ml", "shopee"}
    assert {c["marca"] for c in body["por_marca"]} == {"kokeshi", "barbours"}
    assert len(body["por_canal_marca"]) == 4
    assert body["fontes"]["insumos_dre"]["estado"] == "ok"
    ml = next(c for c in body["por_canal"] if c["canal"] == "ml")
    assert ml["classificacao"] == "PARCIAL"


def test_rota_filtro_de_canal(cliente):
    tc, neon = cliente
    body = tc.get("/api/v1/dre", params={"ref_month": "2026-07", "channels": "ml"}).json()
    assert {c["canal"] for c in body["por_canal"]} == {"ml"}
    p_mart = next(p for p, q in zip(neon.params, neon.sqls) if "fact_marketplace_daily_performance" in q)
    p_ins = next(p for p, q in zip(neon.params, neon.sqls) if "fact_dre_insumo_mensal" in q and "to_regclass" not in q)
    assert p_mart["mkt_ids"] == [dre.MKT_POR_CANAL["ml"]]
    assert p_ins["canais"] == ["ml"]


def test_rota_filtro_de_marca(cliente):
    tc, neon = cliente
    body = tc.get("/api/v1/dre", params={"ref_month": "2026-07", "brands": "barbours"}).json()
    assert {c["marca"] for c in body["por_marca"]} == {"barbours"}
    assert all(p.get("marcas") == ["barbours"] for p in neon.params if "marcas" in p)


def test_rota_periodo_vai_para_a_consulta(cliente):
    tc, neon = cliente
    tc.get("/api/v1/dre", params={"date_from": "2026-06-01", "date_to": "2026-07-31"})
    for p in (p for p in neon.params if "inicio" in p):
        assert (p["inicio"], p["fim"]) == (date(2026, 6, 1), date(2026, 7, 31))


def test_rota_periodo_desalinhado_e_422(cliente):
    tc, _ = cliente
    r = tc.get("/api/v1/dre", params={"date_from": "2026-07-05", "date_to": "2026-07-31"})
    assert r.status_code == 422


def test_rota_sem_periodo_usa_a_competencia_madura_mais_recente(cliente):
    """Revisao estrita #66/#68, item 1: em 13/10/2026 o padrao e' julho, nao
    setembro (o "mes anterior" nunca e' maduro pela regra M+2)."""
    tc, neon = cliente
    body = tc.get("/api/v1/dre").json()
    assert body["periodo"] == {"date_from": "2026-07-01", "date_to": "2026-07-31",
                               "meses": ["2026-07"], "origem": "padrao_competencia_madura"}
    p = next(p for p in neon.params if "inicio" in p)
    assert (p["inicio"], p["fim"]) == (date(2026, 7, 1), date(2026, 7, 31))
    ml = next(c for c in body["por_canal"] if c["canal"] == "ml")
    assert ml["classificacao"] == "PARCIAL"
    assert not any("mes imaturo" in m for m in ml["motivos_classificacao"])


def test_rota_periodo_informado_e_respeitado_e_marcado(cliente):
    tc, _ = cliente
    body = tc.get("/api/v1/dre", params={"ref_month": "2026-07"}).json()
    assert body["periodo"]["origem"] == "informado" and body["periodo"]["meses"] == ["2026-07"]


def test_rota_mes_imaturo_explicito_continua_nao_calculavel(monkeypatch):
    ago = date(2026, 8, 1)
    neon = FakeNeon(mart_rows=[mart("ml", "kokeshi", mes=ago)], insumos=cmv("ml", "kokeshi", mes=ago))
    app.dependency_overrides[get_db] = lambda: neon
    monkeypatch.setattr(database, "datamart_engine", _DataMartQueExplode())
    monkeypatch.setattr(dre_router, "today_brt", lambda: HOJE)
    try:
        body = TestClient(app).get("/api/v1/dre", params={"ref_month": "2026-08", "channels": "ml"}).json()
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert body["periodo"]["meses"] == ["2026-08"] and body["periodo"]["origem"] == "informado"
    ml = body["por_canal"][0]
    assert ml["classificacao"] == "NAO_CALCULAVEL"
    assert any("mes imaturo" in m for m in ml["motivos_classificacao"])


def test_rota_so_um_extremo_do_periodo_continua_422(cliente):
    tc, _ = cliente
    assert tc.get("/api/v1/dre", params={"date_from": "2026-07-01"}).status_code == 422


@pytest.mark.parametrize("hoje,esperado", [
    (date(2026, 10, 13), date(2026, 7, 1)),
    (date(2026, 10, 1), date(2026, 7, 1)),
    (date(2026, 10, 31), date(2026, 7, 1)),
    (date(2026, 11, 1), date(2026, 8, 1)),
    (date(2027, 1, 15), date(2026, 10, 1)),
    (date(2027, 3, 1), date(2026, 12, 1)),
])
def test_competencia_madura_mais_recente(hoje, esperado):
    assert dre.competencia_madura_mais_recente(hoje) == esperado


def test_competencia_padrao_e_coerente_com_a_regra_de_maturidade():
    """Para todo dia de dois anos: o padrao e' maduro e o mes seguinte nao e'."""
    d = date(2026, 1, 1)
    while d < date(2028, 1, 1):
        m = dre.competencia_madura_mais_recente(d)
        assert dre.mes_maduro(m, d), d
        assert not dre.mes_maduro(dre.somar_meses(m, 1), d), d
        d += timedelta(days=1)


def test_outros_endpoints_seguem_com_o_mes_anterior(monkeypatch):
    import app.deps.filters as filtros

    monkeypatch.setattr(filtros, "today_brt", lambda: HOJE)
    r = filtros.filters_query(channels=None, marketplace=None, brands=None, date_from=None, date_to=None,
                              ref_month=None, compare=False, db=None)
    assert (r.period.start, r.period.end) == (date(2026, 9, 1), date(2026, 9, 30))


def test_rota_nunca_toca_o_data_mart(cliente):
    tc, neon = cliente
    r = tc.get("/api/v1/dre", params={"ref_month": "2026-07"})
    assert r.status_code == 200  # o dublê explodiria em qualquer uso
    assert not any("silver." in q or "gold." in q for q in neon.sqls)


def test_modulos_da_dre_nao_referenciam_o_data_mart():
    import inspect

    for mod in (dre, dre_router):
        fonte = inspect.getsource(mod)
        assert "datamart_engine" not in fonte and "get_datamart_db" not in fonte, mod.__name__
        assert "silver." not in fonte and "gold." not in fonte, mod.__name__


def test_rota_sem_migration_responde_e_falha_fechado(monkeypatch):
    neon = FakeNeon(mart_rows=[mart("ml", "kokeshi")], tabela_existe=False)
    app.dependency_overrides[get_db] = lambda: neon
    monkeypatch.setattr(database, "datamart_engine", _DataMartQueExplode())
    try:
        r = TestClient(app).get("/api/v1/dre", params={"ref_month": "2026-07", "channels": "ml"})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 200
    body = r.json()
    assert body["fontes"]["insumos_dre"]["estado"] == "tabela_ausente"
    ml = body["por_canal"][0]
    assert ml["componentes"]["cmv"]["status"] == "snapshot_ausente"
    assert ml["classificacao"] == "NAO_CALCULAVEL"


def test_rota_sem_banco_e_503():
    app.dependency_overrides[get_db] = lambda: None
    try:
        r = TestClient(app).get("/api/v1/dre", params={"ref_month": "2026-07"})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 503



# ---------------------------------------------------------------------------
# Datas atravessando o contrato HTTP (DRE-OPS-1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("instante,esperada", [
    (datetime(2026, 7, 31, 3, 0, tzinfo=timezone.utc), date(2026, 7, 31)),   # meia-noite BRT
    (datetime(2026, 7, 31, 2, 59, tzinfo=timezone.utc), date(2026, 7, 30)),  # ainda dia 30 em BRT
    (date(2026, 7, 31), date(2026, 7, 31)),
    (None, None),
])
def test_data_brt(instante, esperada):
    assert dre.data_brt(instante) == esperada


def test_rota_com_emissao_publicada_as_03h_utc_responde_200(cliente):
    """A forma real da publicacao (03:00 UTC) atravessa o `response_model`.
    Antes da correcao, a resposta inteira era recusada com 500."""
    tc, _ = cliente
    r = tc.get("/api/v1/dre", params={"ref_month": "2026-07"})
    assert r.status_code == 200, r.text[:500]
    ml = next(c for c in r.json()["por_canal"] if c["canal"] == "ml")
    assert ml["data_maxima"]["cmv_ultima_emissao"] == "2026-07-28"
    assert ml["componentes"]["cmv"]["data_as_of"].startswith("2026-07-28T03:00")
