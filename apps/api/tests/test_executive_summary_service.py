"""
Testes de app.services.executive_summary_service (Gate 2, Fase 1 — ver
docs/sections/gerencial_audit.md secao 11). Nenhum banco real tocado.

A maior parte dos testes exercita `_compose_executive_summary()` (funcao
pura, recebe dicts ja resolvidos) — cobre as regras de negocio de
Health/Changes/Risks/DataWarnings sem precisar simular a cadeia de queries
dos 5 services reaproveitados. Um teste de fumaca cobre `get_executive_summary()`
fim a fim com uma sessao falsa que devolve conjuntos vazios, garantindo que a
orquestracao (chamar os 5 services e compor o resultado) nao quebra e que o
caminho "sem dados" funciona de ponta a ponta.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.deps.filters import ResolvedFilters
from app.deps.period import EffectivePeriod
from app.schemas.executive_summary import ExecutiveSummaryResponse
from app.services import executive_summary_service as svc
from app.services.performance_service import ML_ID, SHOPEE_ID, TIKTOK_ID

NOW = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures sinteticas — shape identico ao retorno real dos services
# ---------------------------------------------------------------------------

def _overview(gmv=100_000.0, orders=500, avg_ticket=200.0, gmv_mom_pct=5.0, refreshed_at="2026-07-13T06:00:00+00:00"):
    return {
        "ref_month": "2026-06",
        "marketplace": "all",
        "current": {"gmv": gmv, "orders": orders, "avg_ticket": avg_ticket},
        "previous": {"gmv": 0.0, "orders": 0, "avg_ticket": 0.0},
        "gmv_mom_pct": gmv_mom_pct,
        "date_from": date(2026, 6, 1),
        "date_to": date(2026, 6, 30),
        "compare_date_from": date(2026, 5, 1),
        "compare_date_to": date(2026, 5, 31),
        "filters": {"channels": "all", "brands": None},
        "refreshed_at": refreshed_at,
    }


def _brand_row(brand, label, total_gmv, total_gmv_prev, mom_pct):
    return {
        "brand": brand, "label": label,
        "total_gmv": total_gmv, "total_gmv_prev": total_gmv_prev, "mom_pct": mom_pct,
    }


def _brands(rows, refreshed_at="2026-07-13T06:00:00+00:00"):
    return {"ref_month": "2026-06", "brands": rows, "refreshed_at": refreshed_at}


def _quality(brand_rows, refreshed_at="2026-07-13T06:00:00+00:00"):
    return {"ref_month": "2026-06", "marketplace": "all", "kpis": {}, "brands": brand_rows, "refreshed_at": refreshed_at}


def _canais(channel_rows, refreshed_at="2026-07-13T06:00:00+00:00"):
    return {"ref_month": "2026-06", "marketplace": "all", "kpis": {}, "brands": [], "channel_rows": channel_rows,
            "channel_medians": [], "refreshed_at": refreshed_at}


def _regioes_summary(coverage_level="ok", uf_fill_pct=95.0, channels_sem_cobertura=None, refreshed_at="2026-07-13T06:00:00+00:00"):
    return {
        "gmv": 0, "orders": 0, "units_sold": 0, "ufs_com_venda": 0,
        "uf_known_orders": 0, "uf_eligible_orders": 0, "uf_fill_pct": uf_fill_pct,
        "shipping_cost_covered_orders": 0, "shipping_cost_eligible_orders": 0,
        "shipping_cost_coverage_pct": None, "seller_shipping_cost": None,
        "coverage_level": coverage_level, "coverage_warning": coverage_level in ("low", "partial"),
        "refreshed_at": refreshed_at,
        "channels_sem_cobertura_regional": channels_sem_cobertura or [],
    }


def _compose(*, overview=None, brands=None, quality=None, canais=None, regioes_summary=None, channels="all", mkt_ids=None, now=NOW):
    return svc._compose_executive_summary(
        overview=overview or _overview(),
        brands=brands or _brands([]),
        quality=quality or _quality([]),
        canais=canais or _canais([]),
        regioes_summary=regioes_summary or _regioes_summary(),
        channels=channels,
        mkt_ids=mkt_ids if mkt_ids is not None else [TIKTOK_ID, ML_ID, SHOPEE_ID],
        now=now,
    )


# ---------------------------------------------------------------------------
# Schema — o resultado sempre deve validar contra ExecutiveSummaryResponse
# ---------------------------------------------------------------------------

def test_resultado_valida_contra_schema_pydantic():
    result = _compose()
    ExecutiveSummaryResponse(**result)  # nao deve levantar


def test_resultado_com_riscos_e_changes_tambem_valida_contra_schema():
    result = _compose(
        brands=_brands([_brand_row("kokeshi", "KOKESHI", 50_000, 20_000, 150.0)]),
        quality=_quality([
            {"brand": "a", "label": "A", "ml_cancel_rate_pct": 4.0},
            {"brand": "b", "label": "B", "ml_cancel_rate_pct": 20.0},
        ]),
        regioes_summary=_regioes_summary(coverage_level="low", uf_fill_pct=30.0),
    )
    ExecutiveSummaryResponse(**result)


# ---------------------------------------------------------------------------
# Health status
# ---------------------------------------------------------------------------

def test_health_ok_sem_queda_e_sem_risco():
    result = _compose(overview=_overview(gmv_mom_pct=5.0))
    assert result["health"]["status"] == "ok"


def test_health_attention_em_queda_moderada():
    result = _compose(overview=_overview(gmv_mom_pct=-12.0))
    assert result["health"]["status"] == "attention"


def test_health_critical_em_queda_forte():
    result = _compose(overview=_overview(gmv_mom_pct=-25.0))
    assert result["health"]["status"] == "critical"


def test_health_critical_quando_sem_dado_no_periodo():
    result = _compose(overview=_overview(gmv=0.0, orders=0, gmv_mom_pct=None))
    assert result["health"]["status"] == "critical"
    assert any(r["type"] == "missing_data" for r in result["risks"])


def test_health_attention_por_risco_warning_mesmo_sem_queda_de_gmv():
    result = _compose(
        overview=_overview(gmv_mom_pct=2.0),
        regioes_summary=_regioes_summary(coverage_level="low", uf_fill_pct=10.0),
    )
    assert result["health"]["status"] == "attention"


def test_health_summary_texto_reflete_queda_e_contagem_de_riscos():
    result = _compose(
        overview=_overview(gmv_mom_pct=-25.0),
        regioes_summary=_regioes_summary(coverage_level="low", uf_fill_pct=10.0),
    )
    assert "caiu" in result["health"]["summary"]
    assert "risco" in result["health"]["summary"]


def test_health_summary_sem_comparacao_disponivel():
    result = _compose(overview=_overview(gmv_mom_pct=None))
    assert "sem comparação" in result["health"]["summary"]


# ---------------------------------------------------------------------------
# Changes — top 3 altas / top 3 quedas, com piso de volume
# ---------------------------------------------------------------------------

def test_changes_vazio_quando_nenhuma_marca_tem_comparacao():
    brands = _brands([
        _brand_row("kokeshi", "KOKESHI", 50_000, 0, None),
        _brand_row("barbours", "BARBOURS", 30_000, 0, None),
    ])
    result = _compose(brands=brands)
    assert result["changes"] == []


def test_changes_ignora_marca_abaixo_do_piso_de_volume():
    brands = _brands([_brand_row("kokeshi", "KOKESHI", 500, 100, 400.0)])  # total_gmv_prev=100 < piso
    result = _compose(brands=brands)
    assert result["changes"] == []


def test_changes_top3_altas_e_top3_quedas_respeitando_limite():
    rows = [_brand_row(f"b{i}", f"B{i}", 50_000, 40_000, 10.0 + i) for i in range(5)]
    rows += [_brand_row(f"d{i}", f"D{i}", 20_000, 40_000, -10.0 - i) for i in range(5)]
    result = _compose(brands=_brands(rows))
    growths = [c for c in result["changes"] if c["type"] == "growth"]
    drops = [c for c in result["changes"] if c["type"] == "drop"]
    assert len(growths) == 3
    assert len(drops) == 3
    # maior alta primeiro
    assert growths[0]["brand"] == "b4"
    # maior queda primeiro
    assert drops[0]["brand"] == "d4"


def test_changes_href_aponta_para_canais_com_marca():
    result = _compose(brands=_brands([_brand_row("kokeshi", "KOKESHI", 50_000, 20_000, 150.0)]))
    assert result["changes"][0]["href"] == "/canais?brands=kokeshi"


def test_changes_queda_muito_forte_vira_severity_critical():
    result = _compose(brands=_brands([_brand_row("kokeshi", "KOKESHI", 10_000, 50_000, -80.0)]))
    drop = result["changes"][0]
    assert drop["type"] == "drop"
    assert drop["severity"] == "critical"


def test_changes_queda_moderada_fica_warning():
    result = _compose(brands=_brands([_brand_row("kokeshi", "KOKESHI", 40_000, 50_000, -20.0)]))
    drop = result["changes"][0]
    assert drop["severity"] == "warning"


# ---------------------------------------------------------------------------
# Risks — nao inventam dado ausente
# ---------------------------------------------------------------------------

def test_risco_cancelamento_alto_dispara_acima_da_mediana_do_canal():
    quality = _quality([
        {"brand": "a", "label": "A", "ml_cancel_rate_pct": 4.0},
        {"brand": "b", "label": "B", "ml_cancel_rate_pct": 4.0},
        {"brand": "c", "label": "C", "ml_cancel_rate_pct": 20.0},
    ])
    result = _compose(quality=quality)
    risks = [r for r in result["risks"] if r["type"] == "high_cancel_rate"]
    assert len(risks) == 1
    assert risks[0]["brand"] == "c"
    assert risks[0]["marketplace"] == "ml"
    assert risks[0]["href"] == "/qualidade?brands=c"


def test_risco_cancelamento_nao_dispara_com_amostra_insuficiente():
    quality = _quality([{"brand": "a", "label": "A", "ml_cancel_rate_pct": 90.0}])
    result = _compose(quality=quality)
    assert not any(r["type"] == "high_cancel_rate" for r in result["risks"])


def test_risco_cancelamento_nao_compara_ml_com_shopee():
    # ML baixo e estavel (nao dispara); Shopee com 1 marca alta mas amostra
    # insuficiente para essa marketplace (tambem nao dispara) — nunca compara
    # o valor do Shopee contra a mediana do ML.
    quality = _quality([
        {"brand": "a", "label": "A", "ml_cancel_rate_pct": 4.0},
        {"brand": "b", "label": "B", "ml_cancel_rate_pct": 4.0},
        {"brand": "a", "label": "A", "shopee_cancel_rate_pct": 20.0},
    ])
    result = _compose(quality=quality)
    assert result["risks"] == [] or not any(r["marketplace"] == "shopee" for r in result["risks"])


def test_risco_custo_alto_reaproveita_sinal_de_canais():
    canais = _canais([{
        "brand": "kokeshi", "label": "KOKESHI", "channel": "shopee", "channel_label": "Shopee",
        "gmv": 10_000, "orders": 100, "marketplace_cost_pct": 35.0, "signals": ["custo_alto"],
    }])
    result = _compose(canais=canais)
    risks = [r for r in result["risks"] if r["type"] == "high_cost"]
    assert len(risks) == 1
    assert risks[0]["brand"] == "kokeshi"
    assert risks[0]["href"] == "/canais?brands=kokeshi"


def test_risco_custo_alto_ignora_linha_sem_sinal():
    canais = _canais([{
        "brand": "kokeshi", "label": "KOKESHI", "channel": "shopee", "channel_label": "Shopee",
        "gmv": 10_000, "orders": 100, "marketplace_cost_pct": 10.0, "signals": [],
    }])
    result = _compose(canais=canais)
    assert not any(r["type"] == "high_cost" for r in result["risks"])


def test_risco_custo_alto_tiktok_menciona_aviso_de_base_diferente():
    canais = _canais([{
        "brand": "kokeshi", "label": "KOKESHI", "channel": "tiktok", "channel_label": "TikTok Shop",
        "gmv": 10_000, "orders": 100, "marketplace_cost_pct": 35.0, "signals": ["custo_alto"],
    }])
    result = _compose(canais=canais)
    risk = next(r for r in result["risks"] if r["type"] == "high_cost")
    assert "TikTok" in risk["description"]


def test_risco_cobertura_regional_baixa():
    result = _compose(regioes_summary=_regioes_summary(coverage_level="low", uf_fill_pct=20.0))
    risks = [r for r in result["risks"] if r["type"] == "low_regional_coverage"]
    assert len(risks) == 1
    assert risks[0]["severity"] == "warning"
    assert risks[0]["href"] == "/regioes"


def test_risco_cobertura_regional_ok_nao_gera_risco():
    result = _compose(regioes_summary=_regioes_summary(coverage_level="ok", uf_fill_pct=95.0))
    assert not any(r["type"] == "low_regional_coverage" for r in result["risks"])


def test_risco_dado_defasado_dispara_acima_do_limite():
    overview = _overview(refreshed_at="2026-07-10T00:00:00+00:00")  # ~3.5 dias antes de NOW
    result = _compose(overview=overview)
    risks = [r for r in result["risks"] if r["type"] == "stale_data"]
    assert any(r["href"] == "/" for r in risks)


def test_risco_dado_defasado_nao_dispara_sem_refreshed_at():
    overview = _overview(refreshed_at=None)
    result = _compose(overview=overview)
    assert not any(r["type"] == "stale_data" and r["href"] == "/" for r in result["risks"])


def test_risco_missing_data_apenas_quando_gmv_e_orders_zerados():
    result = _compose(overview=_overview(gmv=0.0, orders=0))
    assert any(r["type"] == "missing_data" for r in result["risks"])
    result_ok = _compose(overview=_overview(gmv=100.0, orders=1))
    assert not any(r["type"] == "missing_data" for r in result_ok["risks"])


# ---------------------------------------------------------------------------
# Data warnings
# ---------------------------------------------------------------------------

def test_data_warning_tiktok_sem_cobertura_regional_quando_selecionado():
    result = _compose(
        mkt_ids=[TIKTOK_ID, ML_ID],
        regioes_summary=_regioes_summary(channels_sem_cobertura=["tiktok"]),
    )
    assert any(w["type"] == "not_applicable" for w in result["data_warnings"])


def test_data_warning_tiktok_ausente_quando_canal_nao_selecionado():
    result = _compose(
        mkt_ids=[ML_ID, SHOPEE_ID],
        regioes_summary=_regioes_summary(channels_sem_cobertura=[]),
    )
    assert result["data_warnings"] == []


# ---------------------------------------------------------------------------
# Filtros ecoados
# ---------------------------------------------------------------------------

def test_filtros_sao_ecoados_no_resultado():
    overview = _overview()
    overview["filters"] = {"channels": "ml,shopee", "brands": ["kokeshi"]}
    result = _compose(overview=overview, channels="ml,shopee")
    assert result["filters"] == {"channels": "ml,shopee", "brands": ["kokeshi"]}


# ---------------------------------------------------------------------------
# Orquestracao fim a fim (fumaca) — sessao falsa que devolve vazio para
# qualquer query, robusta a mudancas no numero/ordem de queries internas
# dos services reaproveitados.
# ---------------------------------------------------------------------------

class _EmptyResult:
    def mappings(self):
        return self

    def all(self):
        return []

    def first(self):
        return None


class FakeEmptySession:
    def execute(self, stmt, params=None):
        return _EmptyResult()


def test_get_executive_summary_fim_a_fim_sem_dados():
    period = EffectivePeriod(start=date(2026, 6, 1), end=date(2026, 6, 30), ref_month="2026-06")
    filters = ResolvedFilters(
        channels="all", mkt_ids=[TIKTOK_ID, ML_ID, SHOPEE_ID], brands=None,
        period=period, compare_period=None,
    )
    result = svc.get_executive_summary(FakeEmptySession(), filters)
    ExecutiveSummaryResponse(**result)
    assert result["health"]["status"] == "critical"
    assert any(r["type"] == "missing_data" for r in result["risks"])
    assert result["changes"] == []
