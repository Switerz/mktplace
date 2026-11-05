"""EXPEDICAO-TIKTOK-ABERTOS — pedidos do TikTok em aberto (consulta injetada)."""
import json
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from app.services import expedicao_tiktok_abertos_service as svc

# Quinta 05/11/2026, 13:00 UTC = 10:00 em Brasilia.
AGORA = datetime(2026, 11, 5, 13, 0, tzinfo=timezone.utc)


def test_feriados_iguais_aos_do_pipeline():
    from pipelines.expedicao import tiktok_daily

    assert svc.FERIADOS_NACIONAIS == tiktok_daily.FERIADOS_NACIONAIS
    assert svc.PRAZO_DIAS_UTEIS == {e.value: n for e, n in tiktok_daily.PRAZO_DIAS_UTEIS.items()}
    assert svc.MARCAS_FORA_DO_ESCOPO == tiktok_daily.MARCAS_FORA_DO_ESCOPO


def test_prazo_pula_fim_de_semana_e_feriado():
    # Pago sexta 30/10: segunda 02/11 e' Finados -> despacho terca 03/11.
    assert svc.prazo_de(date(2026, 10, 30), 1) == date(2026, 11, 3)
    assert svc.prazo_de(date(2026, 10, 30), 2) == date(2026, 11, 4)
    assert svc.proximo_dia_util(date(2026, 11, 5)) == date(2026, 11, 6)
    assert "2026-11-05|2026-11-06|2026-11-09" in svc.prazos(date(2026, 11, 5))


def test_resumo_soma_marcas_e_ordena_por_vencidos():
    chamadas = []

    def consultar(sql, params):
        chamadas.append((sql, params))
        return [
            {"brand": "apice", "abertos": 10, "aguardando_despacho": 2, "aguardando_coleta": 8,
             "retidos": 1, "vencidos": 3, "vencidos_despacho": 1, "vencidos_coleta": 2,
             "vence_hoje": 4, "vence_proximo": 2, "no_prazo": 1, "acima_48h": 3,
             "mais_antigo_em": datetime(2026, 11, 1, 9, 0)},
            {"brand": "kokeshi", "abertos": 20, "aguardando_despacho": 5, "aguardando_coleta": 15,
             "retidos": 0, "vencidos": 7, "vencidos_despacho": 0, "vencidos_coleta": 7,
             "vence_hoje": 5, "vence_proximo": 5, "no_prazo": 3, "acima_48h": 7,
             "mais_antigo_em": datetime(2026, 10, 31, 9, 0)},
        ]

    r = svc.obter_abertos(consultar=consultar, agora=AGORA)
    assert r["availability"] == "available"
    assert r["today"] == "2026-11-05" and r["next_business_day"] == "2026-11-06"
    assert r["totals"]["open"] == 30 and r["totals"]["overdue"] == 10
    assert r["totals"]["on_hold"] == 1
    assert [b["brand"] for b in r["brands"]] == ["kokeshi", "apice"]
    assert r["oldest_paid_at"] == "2026-10-31T09:00:00"
    assert r["orders"] == []
    assert len(chamadas) == 1
    sql, params = chamadas[0]
    assert "raw.tiktok_shop_orders" in sql and "%" not in sql
    assert params["marcas_fora"] == ["gocase"]
    assert params["corte_48h"] == "2026-11-03 10:00:00"


def test_lista_devolve_pedidos_com_idade():
    def consultar(sql, params):
        if "LIMIT" in sql:
            assert "situacao = 'overdue'" in sql
            assert params["marcas"] == ["kokeshi"]
            return [{"brand": "kokeshi", "order_id": "5800001", "shop_name": "Kokeshi",
                     "etapa": "coleta", "paid_at": datetime(2026, 11, 2, 10, 0),
                     "vence_em": date(2026, 11, 4), "situacao": "overdue", "acima_48h": True}]
        return []

    r = svc.obter_abertos(brands=["kokeshi"], lista="overdue", consultar=consultar, agora=AGORA)
    assert r["orders"] == [{
        "brand": "kokeshi", "order_id": "5800001", "shop_name": "Kokeshi", "stage": "coleta",
        "paid_at": "2026-11-02T10:00:00", "deadline": "2026-11-04", "situation": "overdue",
        "over_48h": True, "hours_open": 72.0,
    }]
    assert r["has_more"] is False and r["page"] == 1 and r["page_size"] == 200


def test_paginacao_pede_uma_linha_a_mais_e_desloca():
    vistos = []

    def consultar(sql, params):
        if "LIMIT" in sql:
            vistos.append(sql)
            return [{"brand": "apice", "order_id": str(i), "etapa": "coleta",
                     "paid_at": datetime(2026, 11, 3, 9, 0), "vence_em": date(2026, 11, 5),
                     "situacao": "due_today", "acima_48h": False} for i in range(201)]
        return [{"brand": "apice", "abertos": 900, "vence_hoje": 650}]

    r = svc.obter_abertos(lista="due_today", pagina=3, consultar=consultar, agora=AGORA)
    assert "LIMIT 201 OFFSET 400" in vistos[0]
    assert "order_id ASC" in vistos[0]
    assert len(r["orders"]) == 200 and r["has_more"] is True
    assert r["list_total"] == 650 and r["page"] == 3


def test_falha_da_fonte_vira_indisponivel_nunca_zero():
    def consultar(sql, params):
        raise RuntimeError("boom")

    r = svc.obter_abertos(consultar=consultar, agora=AGORA)
    assert r["availability"] == "unavailable"
    assert r["unavailable_reason"] == "source_error"


def test_so_marca_fora_do_escopo_nao_consulta():
    def consultar(sql, params):  # pragma: no cover - nao pode ser chamada
        raise AssertionError("nao deveria consultar")

    r = svc.obter_abertos(brands=["gocase"], consultar=consultar, agora=AGORA)
    assert r["availability"] == "available" and r["brands"] == []


def test_rota_valida_lista(monkeypatch):
    from app.main import app
    from app.routers import tiktok_abertos

    monkeypatch.setattr(tiktok_abertos.svc, "obter_abertos",
                        lambda **kw: {"ok": True, **{k: v for k, v in kw.items()}})
    cliente = TestClient(app)
    assert cliente.get("/api/v1/expedicao/tiktok/abertos?lista=xpto").status_code in (401, 422)


def test_pagamentos_do_prazo_de_coleta():
    # Coleta vence 04/11 (qua): pagos de sex 30/10 a seg 02/11 (Finados).
    assert svc.pagamentos_do_prazo(date(2026, 11, 4)) == (date(2026, 10, 30), date(2026, 11, 2))
    assert svc.pagamentos_do_prazo(date(2026, 11, 7)) is None  # sabado
    assert svc.dias_de_prazo(date(2026, 11, 5), 3) == ["2026-11-05", "2026-11-04", "2026-11-03"]


def test_coleta_perdida_uma_consulta_filtra_e_pagina_em_memoria():
    chamadas = []
    pedidos = [
        {"brand": "apice", "order_id": "1", "shop_name": "Apice", "order_status": "IN_TRANSIT",
         "paid_at": "2026-10-31T09:00:00", "pronto_em": "2026-11-03T15:00:00",
         "coletado_em": "2026-11-05T08:00:00"},
        {"brand": "kokeshi", "order_id": "2", "shop_name": "Kokeshi", "order_status": "AWAITING_COLLECTION",
         "paid_at": "2026-11-01T09:00:00", "pronto_em": "2026-11-03T10:00:00", "coletado_em": None},
        {"brand": "kokeshi", "order_id": "3", "shop_name": "Kokeshi", "order_status": "AWAITING_COLLECTION",
         "paid_at": "2026-11-02T09:00:00", "pronto_em": "2026-11-04T10:00:00", "coletado_em": None},
    ]

    def consultar(sql, params):
        chamadas.append((sql, params))
        return [{"pedidos": json.dumps(pedidos)}]  # a reserva pelo Metabase devolve texto

    r = svc.obter_coleta_perdida(vencimento=date(2026, 11, 4), consultar=consultar, agora=AGORA)
    assert r["availability"] == "available"
    assert r["list_total"] == 3 and r["still_waiting"] == 2
    assert [b["brand"] for b in r["brands"]] == ["kokeshi", "apice"]
    assert r["all_brands"] == ["apice", "kokeshi"]
    assert r["status_counts"] == {"IN_TRANSIT": 1, "AWAITING_COLLECTION": 2}
    assert r["orders"][0]["collected_at"] == "2026-11-05T08:00:00"
    assert r["orders"][0]["hours_late"] == 8.0
    sql, params = chamadas[0]
    assert params["limite"] == "2026-11-05 00:00:00"
    assert params["pago_ini"] == date(2026, 10, 30) and params["pago_fim"] == date(2026, 11, 2)
    assert "raw.tiktok_shop_line_items" in sql and "json_agg" in sql and "%" not in sql

    # Marca recorta tudo; status recorta a lista e mantem as contagens.
    r = svc.obter_coleta_perdida(vencimento=date(2026, 11, 4), brands=["kokeshi"],
                                 status=["AWAITING_COLLECTION"], consultar=consultar, agora=AGORA)
    assert [p["order_id"] for p in r["orders"]] == ["2", "3"]
    assert r["status_counts"] == {"AWAITING_COLLECTION": 2}
    assert r["all_brands"] == ["apice", "kokeshi"]
    # A SQL e os parametros nao mudam com filtro nem pagina: o cache serve tudo.
    assert len({c[0] for c in chamadas}) == 1
    assert all(c[1] == chamadas[0][1] for c in chamadas)


def test_escopo_todos_inclui_sem_etiqueta_no_prazo():
    pedidos = [
        {"brand": "apice", "order_id": "1", "order_status": "AWAITING_COLLECTION",
         "paid_at": "2026-10-31T09:00:00", "pronto_em": "2026-11-03T15:00:00", "coletado_em": None},
        # etiqueta so' depois do prazo: fora de "etiqueta_pronta", dentro de "todos" (LDR)
        {"brand": "apice", "order_id": "2", "order_status": "IN_TRANSIT",
         "paid_at": "2026-10-31T09:00:00", "pronto_em": "2026-11-05T10:00:00",
         "coletado_em": "2026-11-05T18:00:00"},
        {"brand": "apice", "order_id": "3", "order_status": "AWAITING_SHIPMENT",
         "paid_at": "2026-11-01T09:00:00", "pronto_em": None, "coletado_em": None},
    ]

    def consultar(sql, params):
        return [{"pedidos": pedidos}]

    pronta = svc.obter_coleta_perdida(vencimento=date(2026, 11, 4), consultar=consultar, agora=AGORA)
    todos = svc.obter_coleta_perdida(vencimento=date(2026, 11, 4), escopo="todos",
                                     consultar=consultar, agora=AGORA)
    assert [p["order_id"] for p in pronta["orders"]] == ["1"]
    assert [p["order_id"] for p in todos["orders"]] == ["1", "2", "3"]
    assert todos["status_counts"] == {"AWAITING_COLLECTION": 1, "IN_TRANSIT": 1, "AWAITING_SHIPMENT": 1}
