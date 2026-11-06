"""T-0059 — taxa da Shopee na DRE a partir do escrow.

Defeito: desde o `daily_shopee` (22/10/2026) a fato diaria grava `total_fees`
NULL nas lojas Shopee com API (apice, barbours, lescent, rituaria) numa janela
de 60 dias. A DRE lia so' essa coluna: set/out sairam `indisponivel` ("taxa da
Shopee ausente no mart") e ago `parcial` (22 de 31 linhas).

Correcao: o publisher de insumos publica a taxa do escrow por mes
(`shopee_taxa_escrow`, com cobertura em pedidos) e a DRE a usa quando o mart
nao cobre a celula. Aqui se prova, sem banco:
  * a celula sem `total_fees` volta a ter valor MEDIDO, `parcial` com motivo
    quando a cobertura < 1 e `nao_confirmado` quando = 1;
  * o mart completo continua tendo precedencia (kokeshi, meses antigos);
  * uma fonte por celula: mart parcial + escrow -> so' o escrow;
  * ausencia segue ausencia (nunca zero); insumo vencido falha fechado;
  * o publisher e' inerte enquanto o CHECK do Neon nao aceitar o componente.

Numeros sinteticos de proposito.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.schemas.dre import DREResponse
from app.services import dre_service as dre
from pipelines.dre import insumo_publisher as P

SET = date(2026, 9, 1)
HOJE = date(2026, 11, 5)
AGORA = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
PUBLICADO = AGORA - timedelta(hours=6)


def mart(marca="barbours", mes=SET, dias=30, linhas=None, fees=None, fees_n=0, **over):
    """Celula Shopee do mart. Padrao = o estado de set/26: `total_fees` NULL em todas as linhas."""
    linhas = dias if linhas is None else linhas
    row = {
        "mes": mes, "marketplace_id": dre.MKT_POR_CANAL["shopee"], "marca": marca,
        "dias": dias, "linhas": linhas, "gmv": 1_000_000.0,
        "fees": fees, "fees_n": fees_n, "fees_neg_n": 0,
        "ads": 40_000.0, "ads_n": linhas, "ads_neg_n": 0,
        "frete": 130_000.0, "frete_n": linhas, "frete_neg_n": 0,
        "max_date": date(mes.year, mes.month, dias), "ingested_at": None,
    }
    row.update(over)
    return row


def escrow(marca="barbours", mes=SET, valor=206_374.0, cobertura=0.998837, sinal=1,
           publicado_em=None, lote_status="success", motivo="escrow em 12026 de 12040 pedidos nao cancelados"):
    """Linha `shopee_taxa_escrow` de `marts.fact_dre_insumo_mensal`, como o publisher grava."""
    return {
        "competencia": mes, "canal": "shopee", "marca": marca, "componente": "shopee_taxa_escrow",
        "valor": valor, "sinal_origem": None if valor is None else sinal,
        "cobertura": None if valor is None else cobertura, "provisorio_share": None,
        "status_reconciliacao": "ausente_na_origem" if valor is None else "nao_avaliado",
        "motivo": "sem pedido com escrow para a chave na origem" if valor is None else motivo,
        "data_max_origem": datetime(2026, 11, 5, 16, 0, tzinfo=timezone.utc),
        "publicado_em": publicado_em or PUBLICADO, "lote_id": 9, "lote_status": lote_status,
    }


def taxas(m, linhas_insumo=()):
    """Componente `taxas` da celula base Shopee, pelo caminho de `montar_dre`."""
    idx = dre.indexar_taxa_shopee(list(linhas_insumo), AGORA)
    ins = dre.InsumosCelula(mes=m["mes"], canal="shopee", marca=m["marca"], mart=m,
                            taxa_shopee=idx.get((m["mes"], "shopee", m["marca"])))
    return dre.montar_celula_base(ins, HOJE)["componentes"]["taxas"]


# ---------------------------------------------------------------------------
# Servico — o defeito e a precedencia
# ---------------------------------------------------------------------------

def test_mart_sem_total_fees_usa_taxa_medida_do_escrow():
    """O caso de set/out: antes `indisponivel` sem valor; agora o valor medido, `parcial` com motivo."""
    tx = taxas(mart(), [escrow()])
    assert tx["valor"] == 206_374.0
    assert tx["status"] == dre.COBERTURA_PARCIAL
    assert "99.9%" in tx["motivo"] and "12026 de 12040" in tx["motivo"]
    assert tx["fonte"] == dre.FONTE_TAXA_SHOPEE_ESCROW
    assert tx["publicado_em"] == PUBLICADO


def test_escrow_com_cobertura_total_tem_o_mesmo_nivel_da_taxa_do_mart():
    tx = taxas(mart(), [escrow(cobertura=1.0)])
    assert (tx["valor"], tx["status"]) == (206_374.0, dre.NAO_CONFIRMADO)
    assert "C4" in tx["motivo"]


def test_mart_completo_continua_com_precedencia():
    """Kokeshi (arquivo) e meses anteriores ao corte: nada muda."""
    tx = taxas(mart(marca="kokeshi", fees=1_000_000.0, fees_n=30), [escrow(marca="kokeshi")])
    assert (tx["valor"], tx["status"]) == (1_000_000.0, dre.NAO_CONFIRMADO)
    assert tx["fonte"] == "marts.fact_marketplace_daily_performance.total_fees"


def test_mart_parcial_e_escrow_nao_se_misturam():
    """Agosto: 22 de 31 linhas do arquivo + 9 NULL. A celula usa so' o escrow do mes inteiro."""
    m = mart(mes=date(2026, 8, 1), dias=31, fees=150_000.0, fees_n=22)
    tx = taxas(m, [escrow(mes=date(2026, 8, 1), valor=213_195.92)])
    assert tx["valor"] == 213_195.92
    assert tx["fonte"] == dre.FONTE_TAXA_SHOPEE_ESCROW


def test_sem_escrow_publicado_fica_como_antes_nunca_zero():
    tx = taxas(mart(), [])
    assert (tx["valor"], tx["status"], tx["motivo"]) == (None, dre.INDISPONIVEL, "taxa da Shopee ausente no mart")


def test_escrow_ausente_na_origem_nao_vira_zero():
    tx = taxas(mart(marca="kokeshi"), [escrow(marca="kokeshi", valor=None)])
    assert tx["valor"] is None and tx["status"] == dre.INDISPONIVEL


def test_escrow_vencido_falha_fechado():
    tx = taxas(mart(), [escrow(publicado_em=AGORA - timedelta(hours=60))])
    assert (tx["valor"], tx["status"]) == (None, dre.SNAPSHOT_VENCIDO)


def test_lote_de_escrow_nao_concluido_falha_fechado():
    tx = taxas(mart(), [escrow(lote_status="running")])
    assert (tx["valor"], tx["status"]) == (None, dre.SNAPSHOT_AUSENTE)


def test_sinal_publicado_inesperado_nao_e_corrigido():
    tx = taxas(mart(), [escrow(sinal=-1)])
    assert (tx["valor"], tx["status"]) == (None, dre.SINAL_INESPERADO)


def test_montar_dre_serve_a_taxa_do_escrow_e_respeita_o_contrato_pydantic():
    out = dre.montar_dre(
        inicio=SET, fim=date(2026, 9, 30), canais=["shopee"], marcas=["barbours", "kokeshi"],
        mart_rows=[mart(), mart(marca="kokeshi", fees=900_000.0, fees_n=30)], afiliado_rows=[],
        insumos=dre.LeituraInsumos(tabela_existe=True, linhas=[escrow()]), hoje=HOJE, agora=AGORA,
    )
    DREResponse.model_validate(out)
    por_marca = {c["marca"]: c["componentes"]["taxas"] for c in out["por_canal_marca"]}
    assert por_marca["barbours"]["valor"] == 206_374.0
    assert por_marca["kokeshi"]["valor"] == 900_000.0
    # O total soma o que existe e diz que uma parte e' parcial; nao inventa nada.
    assert out["total"]["componentes"]["taxas"]["valor"] == 1_106_374.0


# ---------------------------------------------------------------------------
# Publisher — leitura do escrow vira linha de insumo
# ---------------------------------------------------------------------------

def esperado(marca="barbours", mes=SET, canal="shopee"):
    return {"competencia": mes, "marketplace_id": P.MKT_ID[canal], "marca": marca, "gmv": Decimal(1_000_000)}


def fonte_tx(marca="barbours", mes=SET, pedidos=12040, com=12026, taxa="206374.00", neg=0):
    return {"competencia": mes, "marca": marca, "pedidos": pedidos, "pedidos_com_escrow": com,
            "taxa": None if com == 0 else Decimal(taxa), "parcelas_negativas": neg,
            "atualizado_em": datetime(2026, 11, 5, 13, 0)}


def lote(esp, taxa_shopee):
    return P.montar_lote(esperado=list(esp), cmv=[], contraprova=[], repasse=[], canais=["shopee"],
                         comps=[SET], taxa_shopee=taxa_shopee)


def linha_tx(lt, marca):
    return next(r for r in lt.linhas if (r["marca"], r["componente"]) == (marca, P.TAXA_SHOPEE))


def test_publisher_inerte_sem_o_check_nao_monta_o_componente():
    lt = lote([esperado()], None)
    assert not any(r["componente"] == P.TAXA_SHOPEE for r in lt.linhas)
    # Lote identico ao de antes do T-0059: sem observacao nova, sem recusa.
    assert lt.recusas == [] and lt.observacoes == []
    assert "migration pendente" in P.estado_taxa_shopee(None, ["shopee"])
    assert P.estado_taxa_shopee([], ["shopee"]) == "publicado"
    assert P.estado_taxa_shopee(None, ["ml"]) == "fora do escopo"


def test_publisher_grava_soma_sinal_e_cobertura_do_escrow():
    lt = lote([esperado()], [fonte_tx()])
    r = linha_tx(lt, "barbours")
    assert r["valor"] == Decimal("206374.00") and r["sinal_origem"] == 1
    assert r["cobertura"] == Decimal("0.998837")
    assert r["status_reconciliacao"] == "nao_avaliado"
    assert r["motivo"] == "escrow em 12026 de 12040 pedidos nao cancelados"
    assert lt.recusas == []


def test_publisher_marca_sem_escrow_vira_nulo_explicito():
    lt = lote([esperado(), esperado("kokeshi")], [fonte_tx(), fonte_tx("kokeshi", com=0)])
    for marca in ("kokeshi",):
        r = linha_tx(lt, marca)
        assert r["valor"] is None and r["status_reconciliacao"] == "ausente_na_origem"
    lt2 = lote([esperado("kokeshi")], [])
    assert linha_tx(lt2, "kokeshi")["valor"] is None


def test_publisher_recusa_parcela_negativa_e_chave_repetida():
    assert any("parcela negativa" in x for x in lote([esperado()], [fonte_tx(neg=3)]).recusas)
    assert any("chave repetida" in x for x in lote([esperado()], [fonte_tx(), fonte_tx()]).recusas)


def test_publisher_so_le_o_escrow_quando_o_check_aceita():
    class Conn:
        def __init__(self, definicao):
            self.definicao = definicao

        def execute(self, _sql):
            return self

        def scalar(self):
            return self.definicao

    antigo = "CHECK ((componente = ANY (ARRAY['cmv'::text, 'tiktok_transacoes'::text])))"
    novo = antigo.replace("'cmv'::text", "'cmv'::text, 'shopee_taxa_escrow'::text")
    assert P.check_aceita_taxa_shopee(Conn(antigo)) is False
    assert P.check_aceita_taxa_shopee(Conn(None)) is False
    assert P.check_aceita_taxa_shopee(Conn(novo)) is True


def test_linha_publicada_chega_a_dre_com_o_mesmo_valor():
    """Contrato publisher -> serving: mesmo nome de componente, mesma convencao de sinal."""
    r = linha_tx(lote([esperado()], [fonte_tx()]), "barbours")
    publicada = {**r, "valor": float(r["valor"]), "cobertura": float(r["cobertura"]),
                 "data_max_origem": r["data_max_origem"], "publicado_em": PUBLICADO, "lote_id": 9,
                 "lote_status": "success"}
    tx = taxas(mart(), [publicada])
    assert tx["valor"] == 206_374.0 and tx["status"] == dre.COBERTURA_PARCIAL
