"""Gate FULL-1A — contrato da superficie "Full Mercado Livre".

Nenhum teste toca banco: a sessao e' um fake que responde por trecho de SQL.

POR QUE O FAKE DEVOLVE DICIONARIO
----------------------------------
O servico consome `.mappings()` e indexa por NOME. Um fake que devolvesse tupla
passaria aqui e falharia em producao -- modo de falha ja' registrado neste
repositorio.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.exc import OperationalError

from app.schemas.ml_fulfillment import MLFulfillmentResponse
from app.services.ml_fulfillment_service import (
    LIMITACAO_SEM_ESTOQUE,
    MAX_LOAD_AGE_HOURS,
    MLFulfillmentUnavailable,
    get_ml_fulfillment_block,
)

AGORA = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
D_FROM, D_TO = date(2026, 8, 1), date(2026, 8, 31)


class FakeResult:
    def __init__(self, linhas):
        self._linhas = list(linhas)

    def mappings(self):
        return self

    def first(self):
        return self._linhas[0] if self._linhas else None

    def scalar(self):
        return list(self._linhas[0].values())[0] if self._linhas else None

    def __iter__(self):
        return iter(self._linhas)


class FakeDB:
    def __init__(self, respostas, levantar=False):
        self.respostas = respostas
        self.levantar = levantar
        self.sql_executado: list[str] = []

    def execute(self, sql, params=None):
        texto = str(sql)
        self.sql_executado.append(texto)
        if self.levantar:
            raise OperationalError("SELECT 1", {},
                                   Exception("could not connect to "
                                             "host=neon.tech password=s3cr3t"))
        for chave, linhas in self.respostas.items():
            if chave in texto:
                return FakeResult(linhas)
        return FakeResult([])


def classe(nome, gmv, pedidos, unidades, elegiveis, cancelados, outros=0,
           h_sum=0, h_n=0, d_sum=0, d_n=0, unmatched=0, missing=0):
    return {"fulfillment_class": nome, "paid_gmv": Decimal(str(gmv)),
            "paid_orders": pedidos, "paid_units": unidades,
            "eligible_orders": elegiveis, "cancelled_orders": cancelados,
            "other_orders": outros,
            "handling_seconds_sum": h_sum, "handling_sample_count": h_n,
            "delivery_seconds_sum": d_sum, "delivery_sample_count": d_n,
            "unmatched_orders": unmatched, "missing_shipping_items": missing}


#: Agosto/2026, no grao do pedido pago. Os mesmos numeros das fixtures do sync.
RESPOSTAS_AGOSTO = {
    "GROUP BY fulfillment_class": [
        # Amostras medidas na COORTE DO PEDIDO (FULL-1A-R): 49.117 e 48.728.
        classe("full", "3649773.48", 48411, 49980, 50492, 2060, outros=21,
               h_sum=103212 * 49117, h_n=49117,
               d_sum=228960 * 48728, d_n=48728),
        classe("non_full", "877705.50", 10712, 10936, 11217, 489, outros=16,
               h_sum=258588 * 10887, h_n=10887,
               d_sum=422496 * 10772, d_n=10772),
        # Sem envio: sem despacho e sem entrega, por definicao.
        classe("unknown", "0", 0, 0, 8, 8, outros=0, unmatched=8),
    ],
    "GROUP BY logistic_type_original": [
        {"logistic_type_original": "fulfillment", "fulfillment_class": "full",
         "paid_gmv": Decimal("3649773.48"), "paid_orders": 48411,
         "paid_units": 49980},
        {"logistic_type_original": "cross_docking",
         "fulfillment_class": "non_full", "paid_gmv": Decimal("877705.50"),
         "paid_orders": 10712, "paid_units": 10936},
    ],
    "GROUP BY ref_date": [
        {"ref_date": D_FROM, "fulfillment_class": "full",
         "paid_gmv": Decimal("100"), "paid_orders": 2, "paid_units": 2,
         "eligible_orders": 3, "cancelled_orders": 1},
    ],
    "COUNT(DISTINCT ref_date)": [{"dias": 31}],
    "MAX(ingested_at)": [{"refreshed_at": AGORA - timedelta(hours=2),
                          "source_max_date": D_TO, "linhas": 120}],
    "FROM por_listing": [
        {"brand": "barbours", "item_id": "MLB1", "tem_full": True,
         "tem_non_full": False, "gmv_full": Decimal("500"),
         "gmv_non_full": Decimal("0"), "un_non_full": 0},
        {"brand": "barbours", "item_id": "MLB2", "tem_full": True,
         "tem_non_full": True, "gmv_full": Decimal("300"),
         "gmv_non_full": Decimal("200"), "un_non_full": 4},
        {"brand": "kokeshi", "item_id": "MLB3", "tem_full": False,
         "tem_non_full": True, "gmv_full": Decimal("0"),
         "gmv_non_full": Decimal("900"), "un_non_full": 12},
    ],
}


def bloco(respostas=None, **kw):
    db = FakeDB(respostas if respostas is not None else RESPOSTAS_AGOSTO)
    return get_ml_fulfillment_block(db, D_FROM, D_TO, agora=AGORA, **kw)


# ---------------------------------------------------------------------------
# Contrato tipado
# ---------------------------------------------------------------------------


def test_resposta_valida_contra_o_schema():
    assert MLFulfillmentResponse.model_validate(bloco())


def test_share_full_gmv_e_o_kpi_principal_e_reconcilia():
    """80,61% -- o numero do stakeholder."""
    assert round(bloco()["share_full_gmv"] * 100, 2) == 80.61


def test_shares_auxiliares_no_grao_do_pedido_pago():
    r = bloco()
    assert round(r["share_full_orders"] * 100, 2) == 81.88
    assert round(r["share_full_units"] * 100, 2) == 82.05


def test_unknown_fica_fora_do_denominador_dos_shares():
    """`unknown` nao e Full nem nao-Full.

    Como em agosto ele nao tem pedido pago, o share nao muda -- mas a regra
    precisa estar travada para o dia em que tiver: contar uma LACUNA DE DADO no
    denominador faria o Full parecer ter piorado sem nada ter mudado na operacao.
    """
    r = bloco({"GROUP BY fulfillment_class": [
        classe("full", "800", 8, 8, 8, 0),
        classe("non_full", "200", 2, 2, 2, 0),
        classe("unknown", "1000", 10, 10, 10, 0, unmatched=10),
    ]})
    assert r["share_full_gmv"] == 0.8          # 800/1000, nao 800/2000
    assert r["share_full_orders"] == 0.8
    assert r["share_full_units"] == 0.8
    assert r["paid_gmv_total"] == Decimal("2000")   # total ainda soma tudo


def test_other_orders_e_exposto_e_fecha_a_populacao():
    """paid + cancelled + other = eligible, sempre."""
    for c in bloco()["by_class"]:
        assert (c["paid_orders"] + c["cancelled_orders"] + c["other_orders"]
                == c["eligible_orders"]), c["fulfillment_class"]
    classes = {c["fulfillment_class"]: c for c in bloco()["by_class"]}
    assert classes["full"]["other_orders"] == 21
    assert classes["non_full"]["other_orders"] == 16


def test_amostras_de_tempo_cabem_na_coorte_do_pedido():
    """CONTRAPROVA de grao: amostra maior que a coorte seria grao de envio."""
    for c in bloco()["by_class"]:
        assert c["handling"]["sample_count"] <= c["eligible_orders"]
        assert c["delivery"]["sample_count"] <= c["eligible_orders"]


def test_unknown_nao_tem_amostra_de_tempo():
    """Sem envio nao existe despacho nem entrega."""
    classes = {c["fulfillment_class"]: c for c in bloco()["by_class"]}
    assert classes["unknown"]["handling"]["sample_count"] == 0
    assert classes["unknown"]["handling"]["seconds_avg"] is None
    assert classes["unknown"]["delivery"]["seconds_avg"] is None


def test_gmv_total_reconcilia_ao_centavo():
    assert bloco()["paid_gmv_total"] == Decimal("4527478.98")


# ---------------------------------------------------------------------------
# Ausencia versus zero
# ---------------------------------------------------------------------------


def test_share_com_denominador_zero_e_none_nunca_zero():
    """Periodo sem venda nao pode parecer fracasso total do Full."""
    r = bloco({"GROUP BY fulfillment_class": [
        classe("full", "0", 0, 0, 0, 0)]})
    assert r["share_full_gmv"] is None
    assert r["share_full_orders"] is None
    assert r["share_full_units"] is None


def test_media_de_tempo_com_amostra_zero_e_none():
    r = bloco({"GROUP BY fulfillment_class": [
        classe("full", "100", 1, 1, 1, 0, h_sum=0, h_n=0)]})
    h = r["by_class"][0]["handling"]
    assert h["seconds_avg"] is None and h["hours_avg"] is None
    assert h["sample_count"] == 0


def test_media_de_tempo_e_derivada_de_soma_e_amostra():
    """A fato guarda soma + n; a media sai da divisao, e e' reagregavel."""
    r = bloco({"GROUP BY fulfillment_class": [
        classe("full", "100", 1, 1, 1, 0, h_sum=7200, h_n=2)]})
    h = r["by_class"][0]["handling"]
    assert h["seconds_avg"] == 3600.0
    assert h["hours_avg"] == 1.0
    assert h["sample_count"] == 2


def test_classe_ausente_nao_vira_linha_de_zeros():
    """Ausencia e' ausencia: a classe some da lista, nao aparece zerada."""
    r = bloco({"GROUP BY fulfillment_class": [
        classe("full", "100", 1, 1, 1, 0)]})
    assert [c["fulfillment_class"] for c in r["by_class"]] == ["full"]


def test_resposta_vazia_nao_quebra_e_marca_incompleto():
    r = bloco({})
    assert r["paid_gmv_total"] == 0
    assert r["share_full_gmv"] is None
    assert r["by_class"] == []
    assert r["quality"]["is_complete"] is False
    assert r["freshness"]["freshness_status"] == "never_loaded"


# ---------------------------------------------------------------------------
# Classificacao e unknown
# ---------------------------------------------------------------------------


def test_unknown_aparece_separado_e_nunca_soma_em_non_full():
    r = bloco()
    classes = {c["fulfillment_class"]: c for c in r["by_class"]}
    assert classes["unknown"]["eligible_orders"] == 8
    assert classes["non_full"]["eligible_orders"] == 11217
    assert r["quality"]["unknown_logistic_type_orders"] == 8
    assert r["quality"]["unmatched_orders"] == 8


def test_cancelamento_por_modalidade_reconcilia():
    classes = {c["fulfillment_class"]: c for c in bloco()["by_class"]}
    assert round(classes["full"]["cancellation_rate"] * 100, 4) == 4.0799
    assert round(classes["non_full"]["cancellation_rate"] * 100, 2) == 4.36


def test_composicao_por_logistic_type_preserva_o_rotulo_bruto():
    tipos = [t["logistic_type_original"] for t in bloco()["by_logistic_type"]]
    assert "cross_docking" in tipos


def test_tipo_logistico_extinto_continua_visivel():
    """Serie longa: xd_drop_off nao pode desaparecer do payload."""
    r = bloco({"GROUP BY logistic_type_original": [
        {"logistic_type_original": "xd_drop_off",
         "fulfillment_class": "non_full", "paid_gmv": Decimal("10"),
         "paid_orders": 1, "paid_units": 1}]})
    assert r["by_logistic_type"][0]["logistic_type_original"] == "xd_drop_off"
    assert r["by_logistic_type"][0]["fulfillment_class"] == "non_full"


# ---------------------------------------------------------------------------
# Listings e migracao
# ---------------------------------------------------------------------------


def test_classificacao_de_listings():
    l = bloco()["listings"]
    assert (l["full_only"], l["mixed"], l["non_full_only"]) == (1, 1, 1)
    assert l["total"] == 3


def test_oportunidade_de_migracao_ordena_por_gmv_nao_full():
    ops = bloco()["migration_opportunities"]
    assert [o["item_id"] for o in ops] == ["MLB3", "MLB2"]
    assert ops[0]["listing_class"] == "non_full_only"
    assert ops[1]["listing_class"] == "mixed"
    # Listing so-Full nao e' oportunidade: nao ha o que migrar.
    assert "MLB1" not in [o["item_id"] for o in ops]


# ---------------------------------------------------------------------------
# Qualidade e frescor
# ---------------------------------------------------------------------------


def test_freshness_fresh_dentro_do_limiar():
    f = bloco()["freshness"]
    assert f["freshness_status"] == "fresh"
    assert f["load_mode"] == "manual_snapshot"
    assert f["source_max_date"] == D_TO


def test_freshness_stale_passa_a_marcar_incompleto():
    respostas = dict(RESPOSTAS_AGOSTO)
    respostas["MAX(ingested_at)"] = [{
        "refreshed_at": AGORA - timedelta(hours=MAX_LOAD_AGE_HOURS + 5),
        "source_max_date": D_TO, "linhas": 120}]
    r = bloco(respostas)
    assert r["freshness"]["freshness_status"] == "stale"
    assert r["quality"]["is_complete"] is False


def test_dia_faltante_marca_incompleto_e_registra_limitacao():
    respostas = dict(RESPOSTAS_AGOSTO)
    respostas["COUNT(DISTINCT ref_date)"] = [{"dias": 20}]
    r = bloco(respostas)
    assert r["quality"]["is_complete"] is False
    assert any("20" in l for l in r["quality"]["limitations"])


def test_limitacoes_declaram_que_nao_ha_estoque():
    assert LIMITACAO_SEM_ESTOQUE in bloco()["quality"]["limitations"]


def test_nenhum_campo_do_contrato_fala_de_estoque():
    """Guarda contra o erro que o FULL-0R custou caro para desfazer."""
    campos = set(MLFulfillmentResponse.model_fields)
    proibidos = ("stock", "estoque", "available", "coverage", "cobertura",
                 "rupture", "ruptura")
    for campo in campos:
        assert not any(p in campo.lower() for p in proibidos), campo


# ---------------------------------------------------------------------------
# Erro, isolamento e PII
# ---------------------------------------------------------------------------


def test_erro_de_banco_vira_excecao_sanitizada():
    db = FakeDB({}, levantar=True)
    with pytest.raises(MLFulfillmentUnavailable) as exc:
        get_ml_fulfillment_block(db, D_FROM, D_TO, agora=AGORA)
    msg = str(exc.value)
    for proibido in ("neon.tech", "s3cr3t", "password", "SELECT"):
        assert proibido not in msg


def test_nenhuma_consulta_ao_data_mart_nem_a_raw_no_request():
    """O endpoint le so' as fatos de marts. Fonte online no request faria a
    pagina cair junto com a VPN."""
    db = FakeDB(RESPOSTAS_AGOSTO)
    get_ml_fulfillment_block(db, D_FROM, D_TO, agora=AGORA)
    todo_sql = " ".join(db.sql_executado)
    for proibido in ("api.ml_orders", "api.ml_shipments",
                     "api.ml_order_line_items", "api.ml_item_stock_history",
                     "raw.", "gold."):
        assert proibido not in todo_sql
    assert "marts.fact_ml_fulfillment_daily" in todo_sql


def test_nenhum_identificador_pessoal_no_payload():
    """A fonte tem buyer_nickname, buyer_first_name e receiver_address. Nada
    disso pode atravessar ate' aqui."""
    import json
    texto = json.dumps(bloco(), default=str).lower()
    for proibido in ("buyer", "nickname", "receiver", "first_name",
                     "last_name", "zip_code", "latitude"):
        assert proibido not in texto


def test_filtro_de_marca_e_parametrizado_nao_interpolado():
    db = FakeDB(RESPOSTAS_AGOSTO)
    get_ml_fulfillment_block(db, D_FROM, D_TO,
                             brands=["barbours'; DROP TABLE x--"], agora=AGORA)
    todo_sql = " ".join(db.sql_executado)
    assert "DROP TABLE" not in todo_sql
    assert "= ANY(:brands)" in todo_sql
