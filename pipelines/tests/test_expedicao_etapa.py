"""T-0015 - etapa do pedido na fila aberta (contrato etapa v1).

Dominio fechado, mapeamento fonte -> etapa e valor desconhecido -> `outro`.
Nenhum banco aqui: os testes contra PostgreSQL descartavel ficam em
`test_expedicao_etapa_integracao.py`.
"""
from __future__ import annotations

import pytest

from pipelines.expedicao.contract import (
    ETAPA_FONTE_MAX_LEN,
    ETAPA_SOURCE_COLUMN,
    ML_ALLOWED_SOURCE_COLUMNS,
    ML_ETAPA_POR_SUBSTATUS,
    PII_FORBIDDEN_TOKENS,
    SHOPEE_ALLOWED_SOURCE_COLUMNS,
    SHOPEE_ETAPA_POR_ORDER_STATUS,
    Channel,
    Etapa,
    etapa_da_fonte,
)

#: Os 9 valores da secao 3 do contrato, transcritos A MAO de proposito: se o
#: enum mudar sem o contrato mudar junto, este teste acusa.
DOMINIO_DO_CONTRATO = {
    "a_preparar",
    "aguardando_coleta",
    "a_faturar",
    "a_imprimir",
    "em_separacao",
    "entregue_transportadora",
    "sinalizado_ml",
    "outro",
    "nao_publicada",
}

#: Tabela da secao 3, linha a linha.
SHOPEE_ESPERADO = {
    "READY_TO_SHIP": "a_preparar",
    "PROCESSED": "aguardando_coleta",
}
ML_ESPERADO = {
    "ready_for_pickup": "aguardando_coleta",
    "invoice_pending": "a_faturar",
    "ready_to_print": "a_imprimir",
    "in_packing_list": "em_separacao",
    "ready_to_pack": "em_separacao",
    "packed": "em_separacao",
    "picked_up": "entregue_transportadora",
    "dropped_off": "entregue_transportadora",
    "in_hub": "entregue_transportadora",
    "delayed": "sinalizado_ml",
    "stale": "sinalizado_ml",
}

#: Substatus do ML VENDEDOR medidos na fonte (secao 1). Todos tem de cair numa
#: etapa real: nenhum valor medido pode virar `outro`.
ML_VENDEDOR_MEDIDO = {
    "in_hub", "picked_up", "ready_for_pickup", "in_packing_list", "dropped_off",
    "ready_to_print", "invoice_pending", "stale", "delayed",
}
SHOPEE_MEDIDO = {"PROCESSED", "READY_TO_SHIP"}


def test_dominio_da_etapa_e_fechado_e_igual_ao_contrato():
    assert {e.value for e in Etapa} == DOMINIO_DO_CONTRATO


def test_mapeamento_shopee_e_exatamente_o_do_contrato():
    assert {k: v.value for k, v in SHOPEE_ETAPA_POR_ORDER_STATUS.items()} == (
        SHOPEE_ESPERADO
    )


def test_mapeamento_ml_e_exatamente_o_do_contrato():
    assert {k: v.value for k, v in ML_ETAPA_POR_SUBSTATUS.items()} == ML_ESPERADO


@pytest.mark.parametrize(
    "canal,medidos",
    [(Channel.SHOPEE, SHOPEE_MEDIDO), (Channel.MERCADOLIVRE, ML_VENDEDOR_MEDIDO)],
)
def test_todo_valor_medido_tem_etapa_real(canal, medidos):
    for bruto in medidos:
        etapa, fonte = etapa_da_fonte(canal, bruto)
        assert etapa not in ("outro", "nao_publicada"), bruto
        assert fonte == bruto


def test_mapeamento_nunca_produz_nao_publicada():
    """`nao_publicada` e' so' o DEFAULT da coluna, para linhas antigas."""
    for mapa in (SHOPEE_ETAPA_POR_ORDER_STATUS, ML_ETAPA_POR_SUBSTATUS):
        assert Etapa.NAO_PUBLICADA not in mapa.values()
        assert Etapa.OUTRO not in mapa.values()


@pytest.mark.parametrize(
    "canal,bruto",
    [
        (Channel.SHOPEE, "SHIPPED"),
        (Channel.SHOPEE, "processed"),  # grafia diferente e' contrato diferente
        (Channel.MERCADOLIVRE, "in_warehouse"),  # substatus do Full
        (Channel.MERCADOLIVRE, "substatus_novo_do_ml"),
        (Channel.MERCADOLIVRE, "READY_FOR_PICKUP"),
    ],
)
def test_valor_desconhecido_vira_outro_e_preserva_o_bruto(canal, bruto):
    assert etapa_da_fonte(canal, bruto) == ("outro", bruto)


@pytest.mark.parametrize("canal", [Channel.SHOPEE, Channel.MERCADOLIVRE])
def test_valor_nulo_vira_outro_sem_fonte(canal):
    assert etapa_da_fonte(canal, None) == ("outro", None)


def test_bruto_longo_e_cortado_na_largura_da_coluna_e_nao_bloqueia():
    bruto = "x" * (ETAPA_FONTE_MAX_LEN + 25)
    etapa, fonte = etapa_da_fonte(Channel.MERCADOLIVRE, bruto)
    assert etapa == "outro"
    assert fonte == bruto[:ETAPA_FONTE_MAX_LEN]
    assert ETAPA_FONTE_MAX_LEN == 40


def test_tiktok_nao_tem_etapa_de_fila():
    with pytest.raises(ValueError, match="tiktokshop"):
        etapa_da_fonte(Channel.TIKTOKSHOP, "AWAITING_SHIPMENT")


def test_coluna_da_etapa_ja_esta_na_lista_fechada_da_fonte():
    """A etapa nao amplia o SELECT: a coluna que a decide ja' era extraida."""
    assert ETAPA_SOURCE_COLUMN == {
        Channel.SHOPEE: "order_status",
        Channel.MERCADOLIVRE: "substatus",
    }
    assert ETAPA_SOURCE_COLUMN[Channel.SHOPEE] in SHOPEE_ALLOWED_SOURCE_COLUMNS
    assert ETAPA_SOURCE_COLUMN[Channel.MERCADOLIVRE] in ML_ALLOWED_SOURCE_COLUMNS


def test_nenhum_valor_do_dominio_ou_da_fonte_parece_pii():
    textos = (
        DOMINIO_DO_CONTRATO
        | set(SHOPEE_ETAPA_POR_ORDER_STATUS)
        | set(ML_ETAPA_POR_SUBSTATUS)
        | {"etapa", "etapa_fonte"}
    )
    for texto in textos:
        for token in PII_FORBIDDEN_TOKENS:
            assert token not in texto.lower(), (texto, token)


# ---------------------------------------------------------------------------
# Transformacao: etapa/etapa_fonte por linha (T-0015)
# ---------------------------------------------------------------------------
from collections import Counter  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from pipelines.expedicao import ml_extract, publisher, transform  # noqa: E402
from pipelines.expedicao.contract import SellerAccount  # noqa: E402

AGORA = datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)
#: Os mesmos instantes, naive, como o ML grava (UTC na ingestao).
AGORA_NAIVE = AGORA.replace(tzinfo=None)

REGISTRY_SHOPEE = {"111": SellerAccount("111", 1, "kokeshi")}
REGISTRY_ML = {"2532564723": SellerAccount("2532564723", 2, "barbours")}

#: O que a transformacao acrescenta as colunas da 018. Igualdade, nao
#: continencia: campo novo na linha so' passa se for declarado aqui.
CHAVES_DA_LINHA = set(publisher.FILA_COLUMNS) | {"etapa", "etapa_fonte"}


def linha_shopee(order_sn: str, order_status) -> dict:
    return {
        "shop_account": "loja_kokeshi",
        "order_sn": order_sn,
        "shop_id": 111,
        "order_status": order_status,
        "create_time": AGORA - timedelta(hours=5),
        "pay_time": AGORA - timedelta(hours=4),
        "ship_by_date": AGORA + timedelta(hours=30),
        "pickup_done_time": None,
        "shipping_carrier": "SPX",
        "ingested_at": AGORA - timedelta(minutes=20),
    }


def linha_ml(shipment_id: str, substatus, logistic_type="cross_docking") -> dict:
    return {
        "seller_id": 2532564723,
        "brand": "barbours",
        "shipment_id": shipment_id,
        "order_id": f"o{shipment_id}",
        "shipment_status": "ready_to_ship",
        "substatus": substatus,
        "logistic_type": logistic_type,
        "order_status": "paid",
        "order_created_at": AGORA_NAIVE - timedelta(hours=10),
        "date_created": AGORA_NAIVE - timedelta(hours=10),
        "date_ready_to_ship": AGORA_NAIVE - timedelta(hours=9),
        "date_shipped": None,
        "date_cancelled": None,
        "tracking_method": "Normal",
        "extracted_at": AGORA_NAIVE - timedelta(minutes=30),
    }


def test_shopee_calcula_etapa_por_order_status():
    fila = transform.build_fila_shopee(
        [linha_shopee("a", "READY_TO_SHIP"), linha_shopee("b", "PROCESSED"),
         linha_shopee("c", "STATUS_NOVO")],
        REGISTRY_SHOPEE, {}, AGORA, "lote",
    )
    assert [(r["etapa"], r["etapa_fonte"]) for r in fila] == [
        ("a_preparar", "READY_TO_SHIP"),
        ("aguardando_coleta", "PROCESSED"),
        ("outro", "STATUS_NOVO"),
    ]


def test_ml_calcula_etapa_por_substatus_que_chega_do_extrator():
    """O `substatus` do SELECT do ML chega ate a transformacao."""
    assert "substatus" in ml_extract.ML_SELECT_COLUMNS
    linhas = [linha_ml(str(i), s) for i, s in enumerate(sorted(ML_ESPERADO))]
    linhas.append(linha_ml("99", "substatus_novo"))
    linhas.append(linha_ml("98", None))
    fila = transform.build_fila_ml(linhas, REGISTRY_ML, AGORA, "lote")
    obtido = {r["marketplace_order_id"]: (r["etapa"], r["etapa_fonte"]) for r in fila}
    for i, s in enumerate(sorted(ML_ESPERADO)):
        assert obtido[str(i)] == (ML_ESPERADO[s], s)
    assert obtido["99"] == ("outro", "substatus_novo")
    assert obtido["98"] == ("outro", None)


def test_linha_da_fila_tem_exatamente_as_colunas_declaradas():
    """Barreira de PII/colunas: so' `etapa` e `etapa_fonte` foram acrescentadas.

    `etapa_fonte` e' o `order_status`/`substatus` bruto - codigo de estado do
    marketplace, nao dado de comprador - e ja' estava na lista fechada da fonte.
    """
    shopee = transform.build_fila_shopee(
        [linha_shopee("a", "PROCESSED")], REGISTRY_SHOPEE, {}, AGORA, "lote"
    )
    ml = transform.build_fila_ml(
        [linha_ml("1", "in_hub")], REGISTRY_ML, AGORA, "lote"
    )
    assert set(shopee[0]) == CHAVES_DA_LINHA
    assert set(ml[0]) == CHAVES_DA_LINHA


def test_full_do_ml_continua_fora_e_a_soma_por_etapa_fecha():
    """Reconciliacao em memoria: soma por etapa == linhas da fila do canal."""
    candidatos = [
        linha_ml("f1", "in_warehouse", "fulfillment"),
        linha_ml("f2", "in_packing_list", "fulfillment"),
        linha_ml("f3", "packed", "fulfillment"),
        linha_ml("s1", "in_hub", "cross_docking"),
        linha_ml("s2", "ready_to_print", "xd_drop_off"),
        linha_ml("s3", "in_packing_list", "drop_off"),
        linha_ml("s4", "invoice_pending", "self_service"),
        linha_ml("s5", "nao_mapeado", "cross_docking"),
    ]
    fila_bruta, diag = ml_extract.classify_candidates(candidatos, AGORA)
    assert diag["fulfillment_excluded_count"] == 3
    fila = transform.build_fila_ml(fila_bruta, REGISTRY_ML, AGORA, "lote")

    ids = {r["marketplace_order_id"] for r in fila}
    assert ids == {"s1", "s2", "s3", "s4", "s5"}
    assert all(r["logistic_type"] != "fulfillment" for r in fila)
    assert "in_warehouse" not in {r["etapa_fonte"] for r in fila}

    por_etapa = Counter(r["etapa"] for r in fila)
    assert sum(por_etapa.values()) == len(fila) == diag["queue_count"]
    assert por_etapa == Counter({
        "entregue_transportadora": 1, "a_imprimir": 1, "em_separacao": 1,
        "a_faturar": 1, "outro": 1,
    })


# ---------------------------------------------------------------------------
# Publicador: lista explicita e deteccao (sem banco; o banco real fica em
# test_expedicao_etapa_integracao.py)
# ---------------------------------------------------------------------------
import ast  # noqa: E402
from pathlib import Path  # noqa: E402


def test_insert_nunca_deriva_colunas_das_chaves_da_linha():
    """`records[0].keys()` deixaria coluna nova entrar sem controle."""
    arvore = ast.parse(Path(publisher.__file__).read_text(encoding="utf-8"))
    chamadas = [
        ast.unparse(no.func) for no in ast.walk(arvore)
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)
        and no.func.attr == "keys"
    ]
    assert chamadas == []


def test_deteccao_consulta_information_schema_da_fila():
    sql = publisher.COLUNAS_ETAPA_SQL.lower()
    assert "information_schema.columns" in sql
    assert "table_schema = %s" in sql and "table_name = %s" in sql


class _CursorColunas:
    def __init__(self, linhas):
        self.linhas, self.params = linhas, None

    def execute(self, sql, params=None):
        self.params = params

    def fetchall(self):
        return self.linhas


@pytest.mark.parametrize(
    "linhas,esperado",
    [
        ([], ()),
        ([("etapa",), ("etapa_fonte",)], ("etapa", "etapa_fonte")),
        ([{"column_name": "etapa_fonte"}, {"column_name": "etapa"}],
         ("etapa", "etapa_fonte")),
        ([("etapa",)], ("etapa",)),
        # `etapa_fonte` sem `etapa` nao e' a 033: nada e' escrito.
        ([("etapa_fonte",)], ()),
    ],
)
def test_colunas_de_etapa_no_destino(linhas, esperado):
    cur = _CursorColunas(linhas)
    assert publisher.colunas_de_etapa_no_destino(cur) == esperado
    assert cur.params == ("marts", "expedicao_fila_atual", ["etapa", "etapa_fonte"])


def test_contagem_por_etapa_soma_as_linhas():
    linhas = [{"etapa": "outro"}, {"etapa": "a_imprimir"}, {"etapa": "outro"}, {}]
    contagem = publisher.contagem_por_etapa(linhas)
    assert contagem == {"outro": 2, "a_imprimir": 1, "nao_publicada": 1}
    assert sum(contagem.values()) == len(linhas)


def _cenario_apply(colunas_etapa):
    from pipelines.tests.test_expedicao_apply_orquestracao import Cenario

    cenario = Cenario()
    cenario.colunas_etapa = colunas_etapa
    assert cenario.rodar() == 0
    sql_fila = [s for s in cenario.target.sqls if "INSERT INTO marts.expedicao_fila_atual" in s]
    return cenario, sql_fila


def test_apply_sem_033_escreve_exatamente_como_antes():
    cenario, sql_fila = _cenario_apply([])
    assert len(sql_fila) == 1
    assert sql_fila[0] == (
        f"INSERT INTO marts.expedicao_fila_atual ({', '.join(publisher.FILA_COLUMNS)}) "
        "VALUES %s"
    )
    assert all(len(t) == len(publisher.FILA_COLUMNS) for t in cenario.enviadas["insert_fila"])


def test_apply_com_033_escreve_etapa_e_etapa_fonte():
    cenario, sql_fila = _cenario_apply(["etapa", "etapa_fonte"])
    colunas = publisher.FILA_COLUMNS + ("etapa", "etapa_fonte")
    assert sql_fila == [
        f"INSERT INTO marts.expedicao_fila_atual ({', '.join(colunas)}) VALUES %s"
    ]
    enviadas = cenario.enviadas["insert_fila"]
    # O backlog do cenario e' todo PROCESSED.
    assert {t[-2:] for t in enviadas} == {("aguardando_coleta", "PROCESSED")}


def test_etapa_fora_do_dominio_e_recusada_antes_do_delete():
    from pipelines.expedicao.contract import SourceHealth

    class ConnQueNaoPodeSerUsada:
        def cursor(self):  # pragma: no cover - o teste exige que nao chegue aqui
            raise AssertionError("tocou o banco antes de validar o lote")

    fila = transform.build_fila_shopee(
        [linha_shopee("a", "PROCESSED")], REGISTRY_SHOPEE, {}, AGORA, "lote"
    )
    fila[0]["etapa"] = "despachado"
    resumo = transform.build_account_summaries(
        fila, AGORA, channel="shopee", refresh_batch_id="lote",
        accounts={"111": ("loja_kokeshi", "kokeshi")},
        watermarks={"111": AGORA}, source_advanced=True,
    )
    with pytest.raises(ValueError, match="etapa fora do dominio"):
        publisher.publish_channel(
            ConnQueNaoPodeSerUsada(), Channel.SHOPEE, fila, resumo,
            source_health=SourceHealth.HEALTHY, refresh_batch_id="lote",
            expected_accounts=frozenset({"loja_kokeshi"}),
            execute_values=lambda *a, **k: None,
        )
