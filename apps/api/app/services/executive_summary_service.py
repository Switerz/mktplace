"""
Resumo executivo da Gerencial (Gate 2, Fase 1 — ver docs/sections/
gerencial_audit.md secao 11). Cobre Health/Changes/Risks/DataWarnings;
Opportunities e Matriz Marca x Canal ficam para a Fase 2.

Composicao deliberada em duas camadas:
- `get_executive_summary()` e um orquestrador fino: chama os services ja
  existentes e maduros (get_overview/get_brands/get_quality/get_canais/
  regioes_service.get_summary), sem duplicar SQL nem reimplementar sinais
  que ja existem em Canais (`custo_alto`, mediana/p75 por canal).
- `_compose_executive_summary()` e pura (nao toca o banco) — recebe os
  dicts ja resolvidos e calcula health/changes/risks/data_warnings. Isso
  permite testar as regras de negocio sem precisar simular 5 queries em
  cadeia por teste.
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps.filters import ResolvedFilters
from app.deps.period import EffectivePeriod, resolve_compare_period
from app.services import performance_service as perf_svc
from app.services import regioes_service as regioes_svc

# ---------------------------------------------------------------------------
# Thresholds (ponto de partida — ver docs/sections/gerencial_audit.md 11.5;
# validar com o gestor contra dados reais antes de considerar definitivo)
# ---------------------------------------------------------------------------

MIN_BRAND_GMV_PREV = 10_000.0   # piso de volume para uma marca entrar em "changes"
MAX_CHANGES_PER_SIDE = 3        # top N altas / top N quedas
DROP_STEEP_PCT = -30.0          # queda de marca abaixo disso escala a change para severity=critical

HEALTH_DROP_WARN_PCT = -10.0    # GMV agregado abaixo disso -> health="attention" (se nao ja critico)
HEALTH_DROP_CRITICAL_PCT = -20.0  # GMV agregado abaixo disso -> health="critical"

CANCEL_ALERT_MULTIPLIER = 1.5   # cancelamento >= mediana do canal * este fator -> risco
MIN_BRANDS_FOR_CANCEL_MEDIAN = 2  # nao compara contra amostra de 1 marca

# Frescor de DADO (MAX(date) da tabela inteira vs. hoje) — NUNCA filtrado
# pelo periodo visualizado. Mesmo conceito de `pipelines/ops/health_check.py`
# (`DAILY_DATA_FRESHNESS_THRESHOLD_DAYS`, tambem =3 para os 3 marketplaces);
# nao importamos o modulo de pipelines dentro da API (camadas separadas),
# so' replicamos a constante. Ver docs/sections/gerencial_audit.md secao 11
# para o achado que motivou a correcao (2026-07-15): a regra antiga usava
# `MAX(ingested_at)` filtrado pelo periodo visualizado, o que acusava
# stale_data falso-positivo para qualquer mes historico fechado (as linhas
# daquele mes nunca sao retocadas por um sync incremental forward-only),
# mesmo com o pipeline saudavel e dado recente disponivel.
DAILY_FRESHNESS_THRESHOLD_DAYS = 3
# O `full_daily` recorrente ja executa `gold_regional_incremental` e
# `sync_region_if_needed`; este alerta significa que `MAX(date)` regional
# passou do limite APESAR desse mecanismo — pode ser ausencia de linhas novas
# na origem ou desatualizacao operacional, nao ausencia de automacao. Mesmo
# limiar de 3d do frescor diario.
REGIONAL_FRESHNESS_THRESHOLD_DAYS = 3

# Tipos de risco que contam como saude COMERCIAL/operacional (Gate G1) — o
# resto (stale_data/low_regional_coverage/missing_data/not_applicable) e'
# "confianca no dado" e nunca entra na saude comercial nem na contagem do
# summary.
COMMERCIAL_RISK_TYPES = ("high_cancel_rate", "high_cost")

_MKT_DISPLAY = {"tiktok": "TikTok Shop", "ml": "Mercado Livre", "shopee": "Shopee"}
_MKT_ID_NAME = {perf_svc.TIKTOK_ID: "tiktok", perf_svc.ML_ID: "ml", perf_svc.SHOPEE_ID: "shopee"}


def _year_month_for_period(period: EffectivePeriod) -> tuple[int, int]:
    """Mesma derivacao do router (`_year_month_for_service`) — necessaria
    porque get_overview/get_brands/get_quality usam (year, month) para achar
    o mes calendario anterior quando period.ref_month esta presente."""
    if period.ref_month:
        y, m = period.ref_month.split("-")
        return int(y), int(m)
    return period.start.year, period.start.month


def get_executive_summary(db: Session, filters: ResolvedFilters, *, now: datetime | None = None) -> dict:
    """`now` injetavel (mesmo padrao de `pipelines/ops/health_check.py._now()`
    e `app.deps.period.today_brt()`) — permite testes deterministicos das
    regras de frescor sem depender do relogio real da maquina."""
    now = now or datetime.now(timezone.utc)
    period = filters.period
    # Health/Changes exigem MoM sempre (nao e opt-in aqui como nos outros
    # endpoints) — se o caller nao pediu compare=true, calculamos o periodo
    # anterior por conta propria.
    # Mesma regra canonica dos outros endpoints (`resolve_compare_period`), para
    # nao existir uma segunda definicao de "periodo anterior" no codigo. Para
    # `period.ref_month` preenchido o resultado e identico ao que
    # overview/brands/quality ja corrigiam localmente; para periodo customizado
    # e a janela deslizante de sempre. Portanto: sem mudanca de numero.
    compare_period = filters.compare_period or resolve_compare_period(period, compare=True)
    year, month = _year_month_for_period(period)

    overview = perf_svc.get_overview(
        db, filters.channels, year, month,
        brand_keys=filters.brands, period=period, compare_period=compare_period,
    )
    brands = perf_svc.get_brands(
        db, filters.channels, year, month,
        brand_keys=filters.brands, period=period, compare_period=compare_period,
    )
    quality = perf_svc.get_quality(
        db, filters.channels, year, month,
        brand_keys=filters.brands, period=period, compare_period=compare_period,
    )
    canais = perf_svc.get_canais(
        db, filters.channels, year, month,
        brand_keys=filters.brands, period=period, compare_period=compare_period,
    )
    regioes_summary = regioes_svc.get_summary(
        db, filters.mkt_ids, filters.brands, period, channels=filters.channels,
    )
    daily_freshness = _fetch_daily_performance_freshness(db, filters.mkt_ids)
    regional_max_date = _fetch_regional_freshness(db, filters.mkt_ids)

    return _compose_executive_summary(
        overview=overview, brands=brands, quality=quality, canais=canais,
        regioes_summary=regioes_summary, channels=filters.channels, mkt_ids=filters.mkt_ids,
        daily_freshness=daily_freshness, regional_max_date=regional_max_date,
        now=now,
    )


# ---------------------------------------------------------------------------
# Frescor de dado — MAX(date) por marketplace, independente do periodo
# visualizado. Consultas leves (1 GROUP BY pequeno cada), no mesmo estilo de
# `performance_service._max_refreshed_at` — nao reaproveitamos aquela funcao
# porque ela mede `ingested_at` filtrado por periodo (o proprio problema que
# esta correcao resolve), nao `date` da tabela inteira.
# ---------------------------------------------------------------------------

def _fetch_daily_performance_freshness(db: Session, mkt_ids: list[int]) -> list[dict]:
    sql = text("""
        SELECT marketplace_id, MAX(date) AS max_date
        FROM marts.fact_marketplace_daily_performance
        WHERE marketplace_id = ANY(:mkt_ids)
        GROUP BY marketplace_id
    """)
    rows = db.execute(sql, {"mkt_ids": mkt_ids}).mappings().all()
    return [
        {"marketplace": _MKT_ID_NAME.get(r["marketplace_id"], str(r["marketplace_id"])), "max_date": r["max_date"]}
        for r in rows
    ]


def _fetch_regional_freshness(db: Session, mkt_ids: list[int]) -> date | None:
    sql = text("""
        SELECT MAX(date) AS max_date
        FROM marts.fact_marketplace_region_daily
        WHERE marketplace_id = ANY(:mkt_ids)
    """)
    row = db.execute(sql, {"mkt_ids": mkt_ids}).mappings().first()
    return row["max_date"] if row else None


# ---------------------------------------------------------------------------
# Composicao pura — sem acesso a banco, testavel com dicts sinteticos
# ---------------------------------------------------------------------------

def _compose_executive_summary(
    *, overview: dict, brands: dict, quality: dict, canais: dict, regioes_summary: dict,
    channels: str, mkt_ids: list[int], daily_freshness: list[dict], regional_max_date,
    now: datetime,
) -> dict:
    cur = overview["current"]
    gmv = cur["gmv"]
    orders = cur["orders"]
    avg_ticket = cur["avg_ticket"]
    gmv_mom_pct = overview.get("gmv_mom_pct")
    no_data = gmv == 0 and orders == 0

    risks = _build_risks(quality, canais, regioes_summary, no_data)
    risks.extend(_build_daily_performance_freshness_risks(daily_freshness, now))
    regional_risk = _build_regional_freshness_risk(regional_max_date, now)
    if regional_risk is not None:
        risks.append(regional_risk)

    changes = _build_changes(brands)
    data_warnings = _build_data_warnings(regioes_summary, mkt_ids)

    # Saude COMERCIAL (Gate G1): so' riscos comerciais/operacionais validos
    # entram (high_cancel_rate, high_cost apos o guardrail). Frescor/cobertura/
    # not_applicable sao "confianca no dado" e NUNCA alteram a saude comercial.
    # missing_data e' excecao de disponibilidade: forca "critical" para a tela
    # nunca dizer "Saudavel" sem dados, mas NAO conta como risco comercial nem
    # entra na contagem do summary (no frontend aparece como "Dados
    # indisponiveis", nao como diagnostico comercial).
    commercial_risks = [r for r in risks if r["type"] in COMMERCIAL_RISK_TYPES]
    has_critical_commercial = any(r["severity"] == "critical" for r in commercial_risks)
    has_warning_commercial = any(r["severity"] == "warning" for r in commercial_risks)

    if no_data:
        status = "critical"
    elif has_critical_commercial or (gmv_mom_pct is not None and gmv_mom_pct <= HEALTH_DROP_CRITICAL_PCT):
        status = "critical"
    elif has_warning_commercial or (gmv_mom_pct is not None and gmv_mom_pct <= HEALTH_DROP_WARN_PCT):
        status = "attention"
    else:
        status = "ok"

    summary = _health_summary(gmv_mom_pct, no_data)

    return {
        "period": {
            "date_from": overview["date_from"],
            "date_to": overview["date_to"],
            "compare_date_from": overview.get("compare_date_from"),
            "compare_date_to": overview.get("compare_date_to"),
            "refreshed_at": overview.get("refreshed_at"),
        },
        "health": {
            "status": status,
            "gmv": gmv,
            "gmv_mom_pct": gmv_mom_pct,
            "orders": orders,
            "avg_ticket": avg_ticket,
            "summary": summary,
        },
        "changes": changes,
        "risks": risks,
        "data_warnings": data_warnings,
        "filters": {"channels": channels, "brands": overview.get("filters", {}).get("brands")},
    }


def _health_summary(gmv_mom_pct: float | None, no_data: bool) -> str:
    """Gate G1 (Task 3, Finding 7): comunica SOMENTE a variacao de GMV frente
    ao periodo anterior, a ausencia de comparacao, ou a indisponibilidade de
    dados. NAO conta atencoes/riscos aqui — as quantidades vivem no proprio
    Pulso, que conhece o agrupamento final (contar riscos brutos aqui
    contradizia o numero agrupado exibido na tela)."""
    if no_data:
        return "Sem dados no período selecionado — não é possível avaliar o desempenho."
    if gmv_mom_pct is None:
        return "Sem comparação disponível para o período."
    if gmv_mom_pct >= 0:
        return f"GMV cresceu {gmv_mom_pct:.1f}% frente ao período anterior."
    return f"GMV caiu {abs(gmv_mom_pct):.1f}% frente ao período anterior."


def _build_changes(brands_resp: dict) -> list[dict]:
    eligible = [
        b for b in brands_resp["brands"]
        if b.get("mom_pct") is not None and (b.get("total_gmv_prev") or 0) >= MIN_BRAND_GMV_PREV
    ]
    growth = sorted((b for b in eligible if b["mom_pct"] > 0), key=lambda b: -b["mom_pct"])[:MAX_CHANGES_PER_SIDE]
    drop = sorted((b for b in eligible if b["mom_pct"] < 0), key=lambda b: b["mom_pct"])[:MAX_CHANGES_PER_SIDE]

    changes: list[dict] = []
    for b in growth:
        changes.append({
            "type": "growth",
            "severity": "info",
            "title": f"{b['label']} cresceu {b['mom_pct']:.1f}% no período",
            "description": (
                f"GMV variou de R$ {b['total_gmv_prev']:.0f} para R$ {b['total_gmv']:.0f} frente ao período anterior."
            ),
            "brand": b["brand"],
            "marketplace": None,
            "metric_value": b["mom_pct"],
            "href": f"/canais?brands={b['brand']}",
            # Gate G1: categoria + referencia (periodo anterior) + diferenca
            "category": "performance",
            "reference_value": b["total_gmv_prev"],
            "reference_kind": "previous_period",
            "delta_abs": b["total_gmv"] - b["total_gmv_prev"],
            "delta_pct": b["mom_pct"],
        })
    for b in drop:
        severity = "critical" if b["mom_pct"] <= DROP_STEEP_PCT else "warning"
        changes.append({
            "type": "drop",
            "severity": severity,
            "title": f"{b['label']} caiu {abs(b['mom_pct']):.1f}% no período",
            "description": (
                f"GMV variou de R$ {b['total_gmv_prev']:.0f} para R$ {b['total_gmv']:.0f} frente ao período anterior."
            ),
            "brand": b["brand"],
            "marketplace": None,
            "metric_value": b["mom_pct"],
            "href": f"/canais?brands={b['brand']}",
            "category": "performance",
            "reference_value": b["total_gmv_prev"],
            "reference_kind": "previous_period",
            "delta_abs": b["total_gmv"] - b["total_gmv_prev"],
            "delta_pct": b["mom_pct"],
        })
    return changes


def _build_risks(quality: dict, canais: dict, regioes_summary: dict, no_data: bool) -> list[dict]:
    risks: list[dict] = []

    if no_data:
        # Excecao de disponibilidade (Gate G1): categoria data_confidence —
        # forca a saude critica (tela nunca diz "Saudavel" sem dados) mas NAO
        # e' contado como risco comercial nem entra no summary comercial.
        risks.append({
            "type": "missing_data",
            "severity": "critical",
            "title": "Sem dados no período selecionado",
            "description": "GMV e pedidos vieram zerados para os filtros aplicados — confira canal, marca e período.",
            "brand": None,
            "marketplace": None,
            # NAO fabricar "0" como metrica (Finding 3): missing_data e'
            # ausencia de dado, nao um valor medido.
            "metric_value": None,
            "href": "/",
            "category": "data_confidence",
        })

    # Cancelamento alto: comparado dentro do MESMO canal (nunca ML vs Shopee —
    # bases normais muito diferentes, ~4% vs ~14% em mai/2026) e so quando ha
    # amostra minima de marcas para calcular uma mediana que faca sentido.
    # TikTok fica de fora: cancel_rate sempre nulo, sem fonte (qualidade_audit.md).
    _append_cancel_risks(risks, quality, "ml", "ml_cancel_rate_pct")
    _append_cancel_risks(risks, quality, "shopee", "shopee_cancel_rate_pct")

    # Custo alto (Gate G1 — guardrail): o sinal `custo_alto` de Canais e'
    # relativo (top-quartil, `custo >= p75`) e DEGENERA quando a distribuicao
    # nao tem dispersao — ex.: custo do TikTok somando 0 para todas as marcas
    # gera cost_vals=[0,0,0,0,0], p75=0 e `0 >= 0` marca 100% das marcas com
    # "0,0%" (falso positivo). Aqui, SO' na camada executiva (a matriz/sinais
    # de Canais ficam intocados), so' emitimos o risco quando ha dispersao
    # real e valor material, reusando mediana/p75 ja calculados por Canais:
    #   custo atual > 0  E  custo atual > mediana do canal  E  custo >= p75.
    # Isso elimina [0,0,0,0,0] e a distribuicao plana positiva [5,5,5,5,5]
    # (nenhum > mediana), preserva um outlier legitimo [5,5,5,5,6] (so' o 6) e
    # NAO inventa nenhum threshold comercial arbitrario.
    cost_ref = {m["channel"]: m for m in canais.get("channel_medians", [])}
    cost_sample: dict[str, int] = {}
    for r in canais.get("channel_rows", []):
        if r.get("marketplace_cost_pct") is not None:
            cost_sample[r["channel"]] = cost_sample.get(r["channel"], 0) + 1
    for row in canais.get("channel_rows", []):
        if "custo_alto" not in row.get("signals", []):
            continue
        ref = cost_ref.get(row["channel"])
        current = row.get("marketplace_cost_pct")
        median = ref.get("marketplace_cost_pct_median") if ref else None
        p75 = ref.get("marketplace_cost_pct_p75") if ref else None
        if current is None or median is None or p75 is None:
            continue
        if not (current > 0 and current > median and current >= p75):
            continue
        note = " Base de custo do TikTok difere de GMV comercial (~5,5%, ver aviso de dados)." if row["channel"] == "tiktok" else ""
        n = cost_sample.get(row["channel"], 0)
        risks.append({
            "type": "high_cost",
            "severity": "warning",
            "title": f"Custo de marketplace alto em {row['label']} ({row['channel_label']})",
            # NUNCA "X% acima do usual" (X seria o proprio custo). Tambem NAO
            # afirma "acima do p75" quando current == p75 (delta 0,0 p.p. seria
            # falso): usa "diferenca vs p75: +N p.p.", sempre verdadeiro
            # (inclui +0,0 quando esta exatamente no p75).
            "description": (
                f"Custo/GMV de {current:.1f}% no canal — mediana {median:.1f}%, p75 {p75:.1f}% "
                f"(diferença vs p75: {current - p75:+.1f} p.p.)."
            ),
            "brand": row["brand"],
            "marketplace": row["channel"],
            "metric_value": current,
            "href": f"/canais?brands={row['brand']}",
            "category": "efficiency_ops",
            "reference_value": p75,
            "reference_kind": "p75",
            "delta_abs": current - p75,
            "confidence_note": (
                f"Mediana {median:.1f}%, p75 {p75:.1f}% entre {n} marca(s) com custo no canal.{note}"
            ),
        })

    level = regioes_summary.get("coverage_level")
    if level in ("low", "partial"):
        uf_fill_pct = regioes_summary.get("uf_fill_pct")
        risks.append({
            "type": "low_regional_coverage",
            "severity": "warning" if level == "low" else "info",
            "title": "Cobertura regional baixa" if level == "low" else "Cobertura regional parcial",
            "description": (
                f"Cobertura de UF em {uf_fill_pct:.1f}% no período (nível: {level})."
                if uf_fill_pct is not None
                else f"Cobertura de UF indisponível no período (nível: {level})."
            ),
            "brand": None,
            "marketplace": None,
            "metric_value": uf_fill_pct,
            "href": "/regioes",
            "category": "data_confidence",
        })

    return risks


def _append_cancel_risks(risks: list[dict], quality: dict, marketplace: str, field: str) -> None:
    rows = quality.get("brands", [])
    values = [(r["brand"], r["label"], r[field]) for r in rows if r.get(field) is not None]
    if len(values) < MIN_BRANDS_FOR_CANCEL_MEDIAN:
        return
    median = statistics.median(v for _, _, v in values)
    if median <= 0:
        return
    threshold = median * CANCEL_ALERT_MULTIPLIER
    display = _MKT_DISPLAY.get(marketplace, marketplace)
    n = len(values)
    for brand, label, rate in values:
        if rate >= threshold:
            risks.append({
                "type": "high_cancel_rate",
                "severity": "warning",
                "title": f"Cancelamento alto em {label} ({display})",
                "description": (
                    f"Cancelamento de {rate:.1f}% vs limite {threshold:.1f}% "
                    f"(mediana {median:.1f}% × {CANCEL_ALERT_MULTIPLIER:g}) do canal."
                ),
                "brand": brand,
                "marketplace": marketplace,
                "metric_value": rate,
                "href": f"/qualidade?brands={brand}",
                # Gate G1: eficiencia/operacao; referencia = limite efetivo
                # (mediana x fator); diferenca em pontos percentuais.
                "category": "efficiency_ops",
                "reference_value": threshold,
                "reference_kind": "threshold",
                "delta_abs": rate - threshold,
                "confidence_note": (
                    f"Mediana {median:.1f}% entre {n} marca(s) do canal; "
                    f"limite = mediana × {CANCEL_ALERT_MULTIPLIER:g}."
                ),
            })


def _build_daily_performance_freshness_risks(freshness_rows: list[dict], now: datetime) -> list[dict]:
    """Um risco por marketplace com `MAX(date)` acima do limite — nunca
    agregado entre marketplaces (`MAX()` combinado mascararia um Shopee
    parado atras de um ML/TikTok em dia). `max_date is None` (nenhuma linha
    para esse marketplace no escopo filtrado) nao gera risco aqui — ausencia
    total de dado no periodo selecionado ja e' coberta por `missing_data`."""
    risks: list[dict] = []
    today = now.date()
    for row in freshness_rows:
        max_date = row["max_date"]
        if max_date is None:
            continue
        staleness_days = (today - max_date).days
        if staleness_days <= DAILY_FRESHNESS_THRESHOLD_DAYS:
            continue
        mkt_name = row["marketplace"]
        display = _MKT_DISPLAY.get(mkt_name, mkt_name)
        risks.append({
            "type": "stale_data",
            "severity": "warning",
            "title": f"{display} sem dados recentes",
            "description": (
                f"{display} sem dados desde {max_date.strftime('%d/%m/%Y')} "
                f"({staleness_days}d, limite de {DAILY_FRESHNESS_THRESHOLD_DAYS}d)."
            ),
            "brand": None,
            "marketplace": mkt_name,
            "metric_value": float(staleness_days),
            "href": "/canais",
            "source": "fact_marketplace_daily_performance",
            "last_date": max_date.isoformat(),
            "threshold_days": DAILY_FRESHNESS_THRESHOLD_DAYS,
            "staleness_days": staleness_days,
            "category": "data_confidence",
        })
    return risks


def _build_regional_freshness_risk(max_date, now: datetime) -> dict | None:
    """`max_date is None` (nenhuma linha regional no escopo — ex.: so' TikTok
    selecionado, que estruturalmente nunca tem cobertura regional) nao gera
    risco; esse caso ja e' coberto pelo data_warning `not_applicable`."""
    if max_date is None:
        return None
    today = now.date()
    staleness_days = (today - max_date).days
    if staleness_days <= REGIONAL_FRESHNESS_THRESHOLD_DAYS:
        return None
    return {
        "type": "stale_data",
        "severity": "warning",
        "title": "Dado regional desatualizado",
        "description": (
            f"Regiões sem dados desde {max_date.strftime('%d/%m/%Y')} "
            f"({staleness_days}d, limite de {REGIONAL_FRESHNESS_THRESHOLD_DAYS}d)."
        ),
        "brand": None,
        "marketplace": None,
        "metric_value": float(staleness_days),
        "href": "/regioes",
        "source": "fact_marketplace_region_daily",
        "last_date": max_date.isoformat(),
        "threshold_days": REGIONAL_FRESHNESS_THRESHOLD_DAYS,
        "staleness_days": staleness_days,
        "category": "data_confidence",
    }


def _build_data_warnings(regioes_summary: dict, mkt_ids: list[int]) -> list[dict]:
    """So cobre avisos estruturais que nao dependem de frescor (staleness ja
    vira risco tipado `stale_data`, nao duplicamos aqui). Margem de Produtos
    fica de fora nesta fase: o resumo executivo ainda nao consulta nenhum
    endpoint de Produtos, entao mencionar a limitacao seria fora de contexto."""
    warnings: list[dict] = []
    if perf_svc.TIKTOK_ID in mkt_ids and "tiktok" in regioes_summary.get("channels_sem_cobertura_regional", []):
        warnings.append({
            "type": "not_applicable",
            "severity": "info",
            "message": "TikTok Shop não possui cobertura regional (UF) em nenhuma fonte mapeada — fica fora da leitura de cobertura.",
            "href": "/regioes",
            "category": "data_confidence",
        })
    return warnings
