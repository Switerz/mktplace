from datetime import date, timedelta
from typing import Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps.filters import (
    ResolvedFilters, filters_query, filters_query_default_days, resolve_brands,
)
from app.deps.period import EffectivePeriod, resolve_period, today_brt
from app.schemas.avoe_snapshot import AvoeSnapshotResponse
from app.schemas.executive_summary import ExecutiveSummaryResponse
from app.schemas.monitoramento_preco import MonitoramentoPrecoResponse
from app.schemas.ml_fulfillment import MLFulfillmentResponse
from app.schemas.shopee_fbs import (
    ShopeeFbsResponse,
    ShopeeFbsUnavailableResponse,
)
from app.schemas.shopee_fbs_stock import (
    EstoqueFullResponse,
    EstoqueFullUnavailableResponse,
)
from app.schemas.performance import (
    BrandDetailResponse, BrandsResponse, CanaisResponse, DailyResponse, FinanceiroResponse,
    MonthlyResponse, OverviewResponse, PedidosResponse, ProdutosMLResponse,
    ProdutosMLSummaryResponse, ProdutosTikTokResponse, ProdutosTikTokSummaryResponse,
    ProdutosShopeeResponse, ProdutosShopeeSummaryResponse,
    QualityResponse, TempoRealResponse, TrendResponse,
)
from app.services import avoe_snapshot_service as avoe_svc
from app.services import executive_summary_service
from app.services import gold_service as svc
from app.services import monitoramento_preco_service as mp_svc
from app.services import performance_service as perf_svc
from app.services import pma_match
from app.services.affiliate_costs_service import safe_affiliate_costs_block
from app.services.tiktok_order_discounts_service import (
    safe_tiktok_order_discounts_block,
)
from app.services import shopee_fbs_service as shopee_fbs_svc
from app.services import shopee_fbs_stock_service as fbs_stock_svc
from app.services.ml_fulfillment_service import (
    MLFulfillmentUnavailable,
    get_ml_fulfillment_block,
)

router = APIRouter(prefix="/api/v1/performance", tags=["performance"])

#: Corpo do 503 da superficie Full do ML. Constante, sanitizada e sem detalhe de
#: infraestrutura: o cliente sabe que a secao caiu, nao por que nem onde.
ERRO_ML_FULFILLMENT_INDISPONIVEL = (
    "Superficie Full do Mercado Livre indisponivel no momento."
)

MARKETPLACE_QUERY_DESCRIPTION = (
    "Canal(is) de marketplace: 'all' (padrao), um canal isolado "
    "('tiktok'|'ml'|'shopee') ou combinacao separada por virgula, ex: 'tiktok,ml'."
)


def marketplace_query(marketplace: str = Query("all", description=MARKETPLACE_QUERY_DESCRIPTION)) -> str:
    """Valida o parametro marketplace na borda HTTP e devolve a forma canonica."""
    try:
        return perf_svc.normalize_marketplace_param(marketplace)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _year_month_for_service(period: EffectivePeriod) -> tuple[int, int]:
    """Deriva (year, month) para as assinaturas legadas dos services a partir
    do periodo resolvido. So e usado de fato quando period.ref_month existe
    (branch de compatibilidade calendario); para intervalos personalizados
    o valor e ignorado internamente pelo service."""
    if period.ref_month:
        y, m = period.ref_month.split("-")
        return int(y), int(m)
    return period.start.year, period.start.month


def _validate_sort_by(sort_by: Optional[str], allowlist: dict[str, str]) -> None:
    if sort_by is not None and sort_by not in allowlist:
        raise HTTPException(
            422,
            f"sort_by invalido: {sort_by}. Valores aceitos: {', '.join(sorted(allowlist))}.",
        )


VALID_ML_BRANDS = {"barbours", "kokeshi", "lescent", "rituaria"}  # rituaria incluida em 2026-07-01 (ver docs/backlog.md)
VALID_TK_BRANDS = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
VALID_PARETO_BUCKETS = {"A_top50", "B_next30", "C_next15", "D_tail"}  # compartilhado: ML, TikTok e Shopee
VALID_ML_STATUS = {"sells+advertised", "sells_organic_only", "ad_spend_no_sales", "inactive"}
VALID_ML_VELOCITY = {"high", "medium", "low", "zero"}
VALID_ML_ACTION_SIGNALS = {
    "ACAO: aumentar investimento (ROAS > 15x)",
    "ACAO: considerar pausar ads (ROAS < 3x)",
    "ALERTA: taxa cancelamento alta (> 10%)",
    "OPORTUNIDADE: produto vende organico, considerar ads",
    "REVIEW: spend sem vendas no período de orders",
    "ATENCAO: grande variacao de preco",
}


def _require_db(db: Session) -> Session:
    if db is None:
        raise HTTPException(503, "Banco de dados indisponivel. Verifique DATABASE_URL.")
    return db


def _parse_month(ref_month: Optional[str]) -> tuple[int, int]:
    if ref_month:
        try:
            year, month = ref_month.split("-")
            return int(year), int(month)
        except Exception:
            raise HTTPException(status_code=422, detail="ref_month deve ser YYYY-MM")
    today = date.today()
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


@router.get("/overview", response_model=OverviewResponse)
def overview(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    year, month = _year_month_for_service(filters.period)
    return perf_svc.get_overview(
        _require_db(db), filters.channels, year, month,
        brand_keys=filters.brands, period=filters.period, compare_period=filters.compare_period,
    )


@router.get("/brands", response_model=BrandsResponse)
def brands(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    year, month = _year_month_for_service(filters.period)
    return perf_svc.get_brands(
        _require_db(db), filters.channels, year, month,
        brand_keys=filters.brands, period=filters.period, compare_period=filters.compare_period,
    )


@router.get("/monthly", response_model=MonthlyResponse)
def monthly(
    marketplace: str = Depends(marketplace_query),
    months_back: int = Query(6, ge=1, le=24),
    db: Session = Depends(get_db),
):
    return perf_svc.get_monthly(_require_db(db), marketplace, months_back)


@router.get("/trend", response_model=TrendResponse)
def trend(
    filters: ResolvedFilters = Depends(filters_query),
    granularity: str = Query(
        "auto",
        description=(
            "Granularidade dos buckets: auto (padrao, mantem a regra vigente — "
            "diaria ate 92 dias, mensal acima), day, week (semana ISO, comecando "
            "na segunda-feira) ou month."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Serie de GMV/pedidos no grao do intervalo filtrado — respeita
    channels/brands/date_from/date_to. A soma de `data[].gmv` sempre bate com
    o GMV de /overview para o mesmo escopo (mesma WHERE clause).

    Gate V2-2, extensao ADITIVA: `granularity` (default `auto`) e a serie do
    periodo anterior em `comparison`, presente somente quando `compare=true`.
    Os defaults reproduzem exatamente o contrato anterior."""
    # Allowlist ESTRITA: valor fora dela e' 422, e a string do usuario nunca
    # chega ao SQL — o service resolve a expressao por mapeamento.
    if granularity not in perf_svc.TREND_GRANULARITIES:
        raise HTTPException(
            status_code=422,
            detail=f"granularity deve ser um de: {', '.join(perf_svc.TREND_GRANULARITIES)}",
        )
    return perf_svc.get_trend(
        _require_db(db),
        filters.channels,
        filters.brands,
        filters.period,
        granularity=granularity,
        compare_period=filters.compare_period,
    )


@router.get("/executive-summary", response_model=ExecutiveSummaryResponse)
def executive_summary(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    """Resumo executivo da Gerencial (Gate 2, Fase 1 — docs/sections/
    gerencial_audit.md secao 11): Health/Changes/Risks/DataWarnings.
    Reaproveita get_overview/get_brands/get_quality/get_canais e
    regioes_service.get_summary — nao duplica SQL. Opportunities e Matriz
    Marca x Canal ficam para a Fase 2."""
    return executive_summary_service.get_executive_summary(_require_db(db), filters)


@router.get("/daily", response_model=DailyResponse)
def daily(
    brand: str = Query(...),
    marketplace: str = Depends(marketplace_query),
    days_back: int = Query(60, ge=7, le=365),
    date_from: Optional[date] = Query(None, description="Alternativa a days_back: inicio do intervalo (inclusive)."),
    date_to: Optional[date] = Query(None, description="Alternativa a days_back: fim do intervalo (inclusive)."),
    db: Session = Depends(get_db),
):
    if brand not in VALID_TK_BRANDS:
        raise HTTPException(404, f"Brand '{brand}' nao encontrado.")
    period = (
        resolve_period(date_from=date_from, date_to=date_to, default_days=days_back, today=today_brt())
        if (date_from or date_to) else None
    )
    return perf_svc.get_daily(_require_db(db), brand, marketplace, days_back, period=period)


@router.get("/produtos/ml/summary", response_model=ProdutosMLSummaryResponse)
def produtos_ml_summary(
    brand: Optional[str] = Query(None),
    action_signal: Optional[str] = Query(None),
    product_status: Optional[str] = Query(None),
    revenue_velocity: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Cards A/B/C/D calculados dinamicamente (CTE + window function) sobre o
    MESMO conjunto filtrado da tabela (brand + action_signal + product_status
    + revenue_velocity), exceto o proprio filtro de pareto_bucket — os 4
    cards continuam visiveis mesmo com um bucket selecionado na tabela.
    fact_ml_produto_ranking nao tem competencia mensal; o campo `scope`
    identifica isso explicitamente em vez de um seletor de mes falso.
    """
    if brand and brand not in VALID_ML_BRANDS:
        raise HTTPException(422, f"Brand '{brand}' invalida para ML.")
    if product_status and product_status not in VALID_ML_STATUS:
        raise HTTPException(422, f"product_status '{product_status}' invalido.")
    if revenue_velocity and revenue_velocity not in VALID_ML_VELOCITY:
        raise HTTPException(422, f"revenue_velocity '{revenue_velocity}' invalido.")
    if action_signal and action_signal not in VALID_ML_ACTION_SIGNALS:
        raise HTTPException(422, "action_signal invalido.")
    return perf_svc.get_produtos_ml_summary(_require_db(db), brand, action_signal, product_status, revenue_velocity)


@router.get("/produtos/ml", response_model=ProdutosMLResponse)
def produtos_ml(
    brand: Optional[str] = Query(None),
    pareto_bucket: Optional[str] = Query(None),
    action_signal: Optional[str] = Query(None),
    product_status: Optional[str] = Query(None),
    revenue_velocity: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query(None, description="Coluna de ordenacao (allowlist)."),
    sort_dir: Optional[Literal["asc", "desc"]] = Query(None),
    db: Session = Depends(get_db),
):
    if brand and brand not in VALID_ML_BRANDS:
        raise HTTPException(422, f"Brand '{brand}' invalida para ML.")
    if pareto_bucket and pareto_bucket not in VALID_PARETO_BUCKETS:
        raise HTTPException(422, f"pareto_bucket '{pareto_bucket}' invalido.")
    if product_status and product_status not in VALID_ML_STATUS:
        raise HTTPException(422, f"product_status '{product_status}' invalido.")
    if revenue_velocity and revenue_velocity not in VALID_ML_VELOCITY:
        raise HTTPException(422, f"revenue_velocity '{revenue_velocity}' invalido.")
    if action_signal and action_signal not in VALID_ML_ACTION_SIGNALS:
        raise HTTPException(422, f"action_signal invalido.")
    _validate_sort_by(sort_by, perf_svc.PRODUTOS_ML_SORT_COLUMNS)
    return perf_svc.get_produtos_ml(
        _require_db(db), brand, pareto_bucket, action_signal, product_status, revenue_velocity,
        limit, offset, sort_by, sort_dir,
    )


@router.get("/produtos/tiktok", response_model=ProdutosTikTokResponse)
def produtos_tiktok(
    brand: Optional[str] = Query(None),
    ref_month: Optional[str] = Query(None),
    pareto_bucket: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query(None, description="Coluna de ordenacao (allowlist)."),
    sort_dir: Optional[Literal["asc", "desc"]] = Query(None),
    db: Session = Depends(get_db),
):
    if brand and brand not in VALID_TK_BRANDS:
        raise HTTPException(422, f"Brand '{brand}' invalida para TikTok.")
    if pareto_bucket and pareto_bucket not in VALID_PARETO_BUCKETS:
        raise HTTPException(422, f"pareto_bucket '{pareto_bucket}' invalido.")
    _validate_sort_by(sort_by, perf_svc.PRODUTOS_TIKTOK_SORT_COLUMNS)
    year, month = _parse_month(ref_month)
    return perf_svc.get_produtos_tiktok(
        _require_db(db), brand, year, month, limit, offset, sort_by, sort_dir, pareto_bucket,
    )


@router.get("/produtos/tiktok/summary", response_model=ProdutosTikTokSummaryResponse)
def produtos_tiktok_summary(
    brand: Optional[str] = Query(None),
    ref_month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Cards A/B/C/D dinamicos — mesmos filtros da tabela (brand, ref_month), exceto pareto_bucket."""
    if brand and brand not in VALID_TK_BRANDS:
        raise HTTPException(422, f"Brand '{brand}' invalida para TikTok.")
    year, month = _parse_month(ref_month)
    return perf_svc.get_produtos_tiktok_summary(_require_db(db), brand, year, month)


@router.get("/produtos/shopee", response_model=ProdutosShopeeResponse)
def produtos_shopee(
    brand: Optional[str] = Query(None),
    ref_month: Optional[str] = Query(None),
    pareto_bucket: Optional[str] = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort_by: Optional[str] = Query(None, description="Coluna de ordenacao (allowlist)."),
    sort_dir: Optional[Literal["asc", "desc"]] = Query(None),
    db: Session = Depends(get_db),
):
    _VALID_SHOPEE = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
    if brand and brand not in _VALID_SHOPEE:
        raise HTTPException(422, f"Brand '{brand}' inválida.")
    if pareto_bucket and pareto_bucket not in VALID_PARETO_BUCKETS:
        raise HTTPException(422, f"pareto_bucket '{pareto_bucket}' invalido.")
    _validate_sort_by(sort_by, perf_svc.PRODUTOS_SHOPEE_SORT_COLUMNS)
    year, month = _parse_month(ref_month)
    return perf_svc.get_produtos_shopee(
        _require_db(db), brand, year, month, limit, offset, sort_by, sort_dir, pareto_bucket,
    )


@router.get("/produtos/shopee/summary", response_model=ProdutosShopeeSummaryResponse)
def produtos_shopee_summary(
    brand: Optional[str] = Query(None),
    ref_month: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Cards A/B/C/D dinamicos — mesmos filtros da tabela (brand, ref_month), exceto pareto_bucket."""
    _VALID_SHOPEE = {"apice", "barbours", "kokeshi", "lescent", "rituaria"}
    if brand and brand not in _VALID_SHOPEE:
        raise HTTPException(422, f"Brand '{brand}' inválida.")
    year, month = _parse_month(ref_month)
    return perf_svc.get_produtos_shopee_summary(_require_db(db), brand, year, month)


@router.get("/canais", response_model=CanaisResponse)
def canais(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    year, month = _year_month_for_service(filters.period)
    sessao = _require_db(db)
    resposta = perf_svc.get_canais(
        sessao, filters.channels, year, month,
        brand_keys=filters.brands, period=filters.period, compare_period=filters.compare_period,
    )
    # Bloco ADITIVO de afiliados (§23), composto AQUI e nao dentro de
    # `get_canais`: o corpo historico da resposta e as consultas que o produzem
    # ficam inalterados.
    #
    # ISSO NAO E' ISOLAMENTO DE LATENCIA. A chamada abaixo e' SINCRONA e roda
    # antes da resposta HTTP: `/canais` ganhou trabalho adicional, e o tempo do
    # bloco soma ao tempo da rota. O que fica isolado e' o corpo historico
    # (nenhuma consulta de `get_canais` mudou) e a FALHA (`safe_...` nao levanta
    # por erro esperado de banco — devolve o bloco em `error` e o resto do
    # payload permanece valido).
    #
    # A janela e os ids de canal sao resolvidos UMA UNICA VEZ e reusados pelos
    # dois blocos: duas resolucoes independentes divergiriam caladas.
    inicio, fim = perf_svc.canais_period_bounds(filters.period, year, month)
    mkt_ids = perf_svc.parse_marketplace_param(filters.channels)
    resposta["affiliate_costs"] = safe_affiliate_costs_block(
        sessao, mkt_ids, inicio, fim, brand_keys=filters.brands,
    )
    # Bloco ADITIVO de descontos do pedido TikTok (§28, UE8-I3). Mesmo padrao:
    # composto AQUI, por wrapper seguro, DEPOIS de `affiliate_costs` e sem
    # tocar em nada que ja estava na resposta. Os dois blocos sao independentes
    # — grao, fonte e frescor diferentes — e a falha de um nao afeta o outro.
    resposta["tiktok_order_discounts"] = safe_tiktok_order_discounts_block(
        sessao, mkt_ids, inicio, fim, brand_keys=filters.brands,
    )
    return resposta


@router.get("/financeiro", response_model=FinanceiroResponse)
def financeiro(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    year, month = _year_month_for_service(filters.period)
    return perf_svc.get_financeiro(
        _require_db(db), filters.channels, year, month,
        brand_keys=filters.brands, period=filters.period, compare_period=filters.compare_period,
    )


@router.get("/quality", response_model=QualityResponse)
def quality(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    year, month = _year_month_for_service(filters.period)
    return perf_svc.get_quality(
        _require_db(db), filters.channels, year, month,
        brand_keys=filters.brands, period=filters.period, compare_period=filters.compare_period,
    )


@router.get("/tempo-real", response_model=TempoRealResponse)
def tempo_real(db: Session = Depends(get_db)):
    return svc.get_tempo_real(_require_db(db))


@router.get("/brand-detail", response_model=BrandDetailResponse)
def brand_detail(
    brand: str = Query(...),
    ref_month: Optional[str] = Query(None),
    channels: Optional[str] = Query(
        None,
        description=(
            "A fonte (gold.tiktok_brand_daily) e TikTok-only. Se informado, "
            "precisa incluir 'tiktok' (ou ser 'all') — canais que excluem "
            "TikTok sao rejeitados com 422 em vez de retornar dados de "
            "TikTok como se o filtro tivesse sido aplicado."
        ),
    ),
    db: Session = Depends(get_db),
):
    if brand not in VALID_TK_BRANDS:
        raise HTTPException(404, f"Brand '{brand}' nao encontrada.")
    if channels is not None:
        try:
            canonical = perf_svc.normalize_marketplace_param(channels)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        mkt_ids = perf_svc.parse_marketplace_param(canonical)
        if perf_svc.TIKTOK_ID not in mkt_ids:
            raise HTTPException(
                422,
                "brand-detail so tem dados de TikTok Shop (gold.tiktok_brand_daily); "
                "'channels' precisa incluir 'tiktok' (ou ser omitido/'all').",
            )
    year, month = _parse_month(ref_month)
    return svc.get_brand_detail(_require_db(db), brand, year, month)


@router.get("/pedidos", response_model=PedidosResponse)
def pedidos(
    filters: ResolvedFilters = Depends(filters_query_default_days(30)),
    db: Session = Depends(get_db),
):
    return perf_svc.get_pedidos(
        _require_db(db), filters.period.days,
        marketplace=filters.channels, brand_keys=filters.brands, period=filters.period,
    )


@router.get("/health-datasource")
def datasource_health(db: Session = Depends(get_db)):
    return {
        "active_source": "neon_marts" if db is not None else "unavailable",
        "db_connected": db is not None,
    }


@router.get("/inteligencia")
def inteligencia(
    brands: Optional[str] = Query(
        None,
        description=(
            "Marca(s) (brand_key) separadas por virgula, o MESMO filtro canonico das "
            "demais rotas. Omitido = todas. O escopo ML e derivado da allowlist "
            "ML_BRANDS: marcas validas que nao vendem no Mercado Livre (apice) saem "
            "do escopo ML e devolvem `ml_scope_brands` vazio, sem dado fabricado. "
            "`tk_products` e TikTok e permanece global."
        ),
    ),
    db: Session = Depends(get_db),
):
    sessao = _require_db(db)
    # Valida pela infraestrutura existente, o mesmo `resolve_brands` das demais
    # rotas. A validacao executa uma consulta read-only em `marts.dim_loja`;
    # marca inexistente vira 422 antes de qualquer SQL COMERCIAL de
    # `get_inteligencia`. O servico depois projeta as marcas validadas sobre a
    # allowlist ML (`resolve_ml_scope`, segunda defesa, pura).
    pedidas = resolve_brands(brands, sessao)
    return svc.get_inteligencia(sessao, ml_brands=pedidas)


@router.get("/operacoes")
def operacoes(db: Session = Depends(get_db)):
    return svc.get_operacoes(_require_db(db))


@router.get("/debug/raw-tempo-real")
def debug_raw_tempo_real(db: Session = Depends(get_db)):
    """
    Investiga se raw.tiktok_shop_orders tem dados mais frescos que
    gold.tiktok_shop_hourly para o uso em tempo real.
    """
    return svc.diagnose_raw_tempo_real(_require_db(db))


# ---------------------------------------------------------------------------
# Gate PMA-1A — monitoramento de precos proprios (MVP observacional)
# ---------------------------------------------------------------------------

#: Resposta FIXA para inconsistencia da camada de serving (PMA-1A-R, F7).
#: Nao carrega o texto da excecao: ele descreve estado interno e vai ao log.
ERRO_SERVING_INCONSISTENTE = (
    "Dados de monitoramento de preco indisponiveis: inconsistencia na camada de "
    "serving. Acione o time de dados."
)


@router.get("/monitoramento-preco", response_model=MonitoramentoPrecoResponse)
def monitoramento_preco(
    marketplace: str = Query(
        "ml",
        description=(
            "Canal. Neste MVP somente 'ml': e' o unico canal com fonte de PRECO "
            "ANUNCIADO. Shopee tem apenas preco transacional de export de pedido, "
            "TikTok nao tem preco no catalogo e Amazon nao tem fonte na Torre. "
            "Qualquer outro valor e' recusado com 422."
        ),
    ),
    # SEM `max_length` nestes tres, DELIBERADAMENTE (PMA-1A-R, F6).
    #
    # O validador nativo do FastAPI/Pydantic recusa com 422, mas o corpo do erro
    # INCLUI o valor recusado (`{"input": "<o payload>", "ctx": {"max_length":
    # 120}}`). Foi medido: um `brand` de 200 caracteres voltava com os 200
    # caracteres na resposta. Isso e' exatamente o eco que F6 proibe — e com HTML,
    # DSN ou IP no payload, o eco e' pior que o excesso de tamanho.
    #
    # O teto continua existindo e continua na BORDA: e' aplicado no corpo do
    # endpoint, por `monitoramento_preco_service`, que devolve uma mensagem
    # CONSTANTE. O tamanho maximo aceito esta documentado aqui, na descricao,
    # para nao desaparecer do OpenAPI.
    brand: Optional[str] = Query(
        None,
        description=(
            "Marca(s) separadas por virgula, ou 'all'. "
            "Gate PMA-2C4D3-H3: a marca vale quando esta em "
            "`meta.observed_brands` da fotografia resolvida para o `marketplace` "
            "e a data consultados — nao ha allowlist fixa, porque a lista muda a "
            "cada fotografia. Marca ausente dessa cobertura e' recusada com 422, "
            "inclusive quando e' monitorada: 'nao observada' nao e' zero anuncio, "
            "e servir zero se leria como ausencia de anuncio. As monitoradas que "
            "a fotografia nao devolveu vem em `meta.monitored_unobserved_brands`. "
            "No Mercado Livre, 'apice' e 'yenzah' tem tabela de referencia B2B "
            "mas nao tem catalogo proprio (out_of_scope_no_ml_catalog) e mantem "
            "essa recusa especifica; nos demais canais elas podem ser observadas "
            "e, entao, sao aceitas. Maximo de 120 caracteres."
        ),
    ),
    status: Optional[str] = Query(
        None,
        description=(
            "Status de comparacao, separados por virgula: below_reference, "
            "at_or_above_reference, no_reference, "
            "non_comparable_reference_ambiguous, inactive_listing, "
            "stale_observation. Os KPIs NAO respondem a este filtro — eles "
            "descrevem sempre o conjunto completo, para preservar o denominador. "
            "Maximo de 240 caracteres."
        ),
    ),
    product_query: Optional[str] = Query(
        None,
        description=(
            "Busca por titulo do anuncio, seller_sku, GTIN ou item_id. "
            "Maximo de 120 caracteres."
        ),
    ),
    # Gate PMA-H1 — data OBSERVADA, parametro publico e opcional.
    #
    # Tipado como `str`, nao `date`, pelo MESMO motivo de `ref_date`: o validador
    # nativo do FastAPI devolveria 422 com `{"input": "<o payload>"}`, ecoando a
    # entrada. Como `str`, a validacao acontece no servico e a mensagem e' FIXA.
    observed_date: Optional[str] = Query(
        None,
        description=(
            "Data OBSERVADA do preco anunciado, no formato YYYY-MM-DD. Omitido: "
            "usa a maior observacao disponivel <= D-1 (modo latest) e informa a "
            "defasagem, se houver. Informado: consulta EXATAMENTE aquele dia "
            "(modo selected_date), sem cair para o dia anterior. O teto e' D-1 em "
            "America/Sao_Paulo; D0 e futuro sao recusados com 422. Data valida "
            "sem observacao devolve 200 com estado vazio. As datas realmente "
            "disponiveis vem em meta.available_observed_dates. "
            "IMPORTANTE: numa data historica a referencia usada e' o snapshot PDV "
            "mais recente disponivel HOJE (reference_basis="
            "latest_available_snapshot) — a origem nao declara vigencia "
            "historica, e o resultado pode mudar se uma nova referencia for "
            "importada."
        ),
    ),
    # Gate PMA-2C4A — filtros dos canais novos. SEM `max_length`, pelo mesmo
    # motivo dos tres acima: o validador nativo ecoaria o valor recusado. O teto
    # e' aplicado no servico, com mensagem CONSTANTE.
    shop_account: Optional[str] = Query(
        None,
        description=(
            "Conta(s) de loja separadas por virgula, ou 'all'. So' se aplica a "
            "marketplace=shopee e marketplace=tiktok: a fato do Mercado Livre "
            "nao modela conta de loja, e pedi-lo com marketplace=ml e' recusado "
            "com 422 em vez de ignorado. As contas presentes na fotografia vem "
            "em meta.account_clocks. Maximo de 120 caracteres."
        ),
    ),
    product_type: Optional[str] = Query(
        None,
        description=(
            "Tipo(s) de produto separados por virgula, ou 'all', entre: "
            "kit_confirmed, kit_suspected, no_kit_signal, product_type_unknown. "
            "So' se aplica a marketplace=shopee e marketplace=tiktok, onde o "
            "valor e' MATERIALIZADO na fato pelo publisher; no Mercado Livre ele "
            "e' derivado em tempo de consulta e o filtro e' recusado com 422. "
            "`product_type_unknown` significa SEM SINAL de kit, nunca 'produto "
            "simples confirmado'. Maximo de 120 caracteres."
        ),
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    # Parametro OCULTO, recebido como texto so para poder ser RECUSADO (PMA-1B).
    #
    # Sem ele, o FastAPI ignoraria `?ref_date=...` em silencio e responderia 200,
    # fazendo o consumidor acreditar que o filtro historico foi aplicado. Tipado
    # como `str` de proposito: `date` faria o FastAPI VALIDAR o valor e devolver
    # um 422 proprio — que ecoa a entrada no corpo (o mesmo defeito medido em
    # `max_length` no PMA-1A-R). Como `str`, nada e' interpretado nem convertido,
    # e qualquer conteudo recebe a MESMA mensagem fixa.
    #
    # `include_in_schema=False` mantem o parametro fora do OpenAPI: ele nao e'
    # uma opcao, e' uma armadilha fechada.
    ref_date: Optional[str] = Query(None, include_in_schema=False),
    db: Session = Depends(get_db),
):
    """MVP OBSERVACIONAL do preco anunciado das lojas PROPRIAS no Mercado Livre.

    Compara o PRECO ANUNCIADO proprio contra o PRECO SUGERIDO DE REVENDA (PDV)
    das tabelas B2B. NAO e' fiscalizacao de revendedor, NAO implementa a politica
    de PMA e NAO produz sancao: `policy_status` e'
    `not_applicable_to_own_store_monitoring`.

    A referencia e' PDV, nao PMA — o PDV foi medido como markup aritmetico sobre
    o preco de atacado, com razao que varia por marca, e nao tem vigencia
    declarada (`validity_status = 'missing'`).

    Cobertura `advertised_only`: sem frete, cupom de vitrine, subsidio de
    plataforma nem preco de checkout. Esses quatro campos vem NULOS, nunca zero.
    Como o preco de checkout se compoe de `produto + frete - cupom`, e o frete
    ELEVA enquanto o cupom REDUZ, a direcao liquida do desvio e' INDETERMINADA:
    a comparacao e' PARCIAL e a diferenca pode mudar de valor e de sinal quando
    esses componentes forem considerados.

    DOIS MODOS (Gate PMA-H1). Sem `observed_date`: modo `latest`, a maior
    observacao disponivel com D-1 (America/Sao_Paulo) como teto — e, se ela
    estiver atrasada, os dados APARECEM com a defasagem declarada em
    `meta.lag_days`/`meta.freshness_status`, nunca com os cartoes zerados. Com
    `observed_date=YYYY-MM-DD`: modo `selected_date`, exatamente aquele dia, sem
    cair para o anterior.

    Numa data historica a referencia e' o snapshot PDV mais recente disponivel
    HOJE (`reference_basis = latest_available_snapshot`). A resposta NAO afirma
    que essa referencia valia naquele dia — `validity_status = 'missing'` — e
    diz isso em `meta.comparison_basis_text` e nas `limitations` de cada linha.

    FRESCOR E' TRANSVERSAL: `comparison_status` traz a particao comercial de
    cinco valores que fecha em `monitored_count`, e `freshness_status`
    (fresh/stale/historical/unavailable) viaja em paralelo. `stale_observation`
    saiu da particao e sobrevive apenas como alias depreciado no filtro `status`.

    Sem limiar comercial aprovado nao existe severidade: os unicos fatos sao
    `difference_amount` e `difference_pct`. "Abaixo da referencia" e' um POTENCIAL
    DESVIO DE PRECO que exige REVISAO HUMANA, nunca uma infracao.

    Enviar `ref_date` e' RECUSADO com 422 — nunca ignorado em silencio.

    Le exclusivamente `marts.*` no Neon.
    """
    # PRIMEIRA coisa do corpo: antes de `_require_db`, antes do servico e antes
    # de qualquer consulta. Recusar cedo garante que uma requisicao com filtro
    # historico nao toque o banco.
    if ref_date is not None:
        raise HTTPException(422, mp_svc.ERRO_REF_DATE_NAO_SUPORTADO)

    # Gate PMA-2C1A — portao do canal, TAMBEM antes de `_require_db`.
    #
    # O servico ja' tem a mesma guarda, mas aqui ela precisa vir antes da
    # dependencia de banco por um motivo concreto: `_require_db` levanta 503
    # quando nao ha sessao, e um canal DESLIGADO nao precisa de banco nenhum
    # para responder. Sem este desvio, `marketplace=shopee` viraria erro de
    # infraestrutura em vez do estado `unavailable` estruturado que o contrato
    # promete — e um erro no lugar do estado e' exatamente o que o gate proibiu.
    try:
        canal_pedido = mp_svc.normalize_marketplace(marketplace)
    except mp_svc.MonitoramentoPrecoError as exc:
        raise HTTPException(422, str(exc))
    if not mp_svc.channel_enabled(canal_pedido):
        return mp_svc.unavailable_response(canal_pedido)

    sessao = _require_db(db)
    try:
        return mp_svc.get_monitoramento_preco(
            sessao,
            marketplace=marketplace,
            brand=brand,
            status=status,
            product_query=product_query,
            observed_date=observed_date,
            shop_account=shop_account,
            product_type=product_type,
            limit=limit,
            offset=offset,
        )
    except mp_svc.MonitoramentoPrecoError as exc:
        # Recusa de CONTRATO: erro do cliente. A mensagem e' constante e nao
        # reflete nenhum caractere da entrada recusada.
        raise HTTPException(422, str(exc))
    except pma_match.PmaMatchError:
        # INCONSISTENCIA DA NOSSA CAMADA DE DADOS, nao erro do cliente: NaN num
        # preco, escala acima do teto, `ref_date` em D0 que o sync proibiu
        # publicar, formato interno invalido. Nao e' recuperavel mudando a
        # requisicao, logo nao pode ser 422. A mensagem devolvida e' FIXA — o
        # texto da excecao fica no log do servidor, nunca na resposta.
        raise HTTPException(500, ERRO_SERVING_INCONSISTENTE)


# ---------------------------------------------------------------------------
# Snapshot manual da Avoe — Gate AVH-4B-S Task 1/2
# ---------------------------------------------------------------------------

#: Resposta FIXA para inconsistencia da camada de serving da Avoe. Nao carrega
#: o texto da excecao: ele descreve estado interno e vai ao log do servidor.
ERRO_AVOE_SERVING_INCONSISTENTE = (
    "Snapshot da Avoe indisponivel: inconsistencia na camada de serving. "
    "Acione o time de dados."
)


@router.get("/avoe-snapshot", response_model=AvoeSnapshotResponse)
def avoe_snapshot(db: Session = Depends(get_db)):
    """Ultima captura VALIDA do snapshot manual da Avoe.

    SEM PARAMETRO, de proposito: o contrato e' "a ultima captura valida", e
    filtro por marca, canal ou competencia seria escopo da tela, nao do
    serving. Uma captura mais nova mas invalida e' ignorada em favor da
    anterior que se sustenta.

    FONTE EXTERNA E MANUAL. Nao e' KPI da Torre, nao substitui
    `fact_marketplace_daily_performance` e nao entra em `/overview` nem em
    `/canais`. A propria resposta declara isso em `meta.is_official_torre_source`
    e nos avisos.

    Ausencia de captura valida NAO e' erro: devolve 200 com
    `status = 'unavailable'`, arrays vazios e `unavailable_reason` factual.
    """
    try:
        return avoe_svc.get_avoe_snapshot(db)
    except avoe_svc.AvoeSnapshotError:
        # Inconsistencia da nossa camada, nao erro do cliente: contagem
        # agregada divergindo das linhas lidas, sessao ausente. Nao e'
        # recuperavel mudando a requisicao, logo nao pode ser 422.
        raise HTTPException(500, ERRO_AVOE_SERVING_INCONSISTENTE)


@router.get("/ml-fulfillment", response_model=MLFulfillmentResponse)
def ml_fulfillment(
    filters: ResolvedFilters = Depends(filters_query),
    db: Session = Depends(get_db),
):
    """Modalidade logistica do Mercado Livre: Full, nao-Full e desconhecido.

    Bloco ADITIVO (Gate FULL-1A). NAO mede estoque: nao ha aqui
    disponibilidade, cobertura em dias nem ruptura. "Full" e' a modalidade do
    ENVIO (`logistic_type = 'fulfillment'`), nunca estoque no Full.

    Le apenas as fatos ja' materializadas em `marts`. Nenhuma consulta ao Data
    Mart acontece durante o request.
    """
    try:
        return get_ml_fulfillment_block(
            _require_db(db),
            filters.period.start,
            filters.period.end,
            brands=filters.brands,
        )
    except MLFulfillmentUnavailable:
        # Mensagem FIXA, nunca o texto da excecao. O servico ja' sanitiza, mas
        # ecoar o texto da excecao deixaria o corpo da resposta a merce de
        # qualquer mensagem futura que passasse por ali -- inclusive de driver.
        raise HTTPException(503, ERRO_ML_FULFILLMENT_INDISPONIVEL)


# ---------------------------------------------------------------------------
# Gate FULL-SH-1C — desempenho FBS da Shopee. READ-ONLY, atras de feature flag.
# ---------------------------------------------------------------------------

#: Resposta FIXA quando a fato esta indisponivel. Nao carrega o texto da
#: excecao: ele descreve estado interno e vai ao log do servidor.
ERRO_SHOPEE_FBS_INDISPONIVEL = (
    "Desempenho FBS da Shopee indisponivel: a fato nao pode ser lida agora. "
    "Acione o time de dados."
)
ERRO_SHOPEE_FBS_CONTRATO = (
    "Desempenho FBS da Shopee indisponivel: a fonte devolveu classe fora do "
    "dominio autorizado. Acione o time de dados."
)
MOTIVO_SHOPEE_FBS_DESLIGADO = (
    "Superficie de FBS da Shopee ainda nao habilitada. A fato existe e esta "
    "publicada, mas a cobertura e' PARCIAL (quatro contas da esteira API; "
    "Kokeshi fora) e a ativacao e' decisao de negocio."
)

#: Politica de data PROPRIA desta superficie, e o motivo de nao reusar
#: `resolve_period`: aquele resolvedor barra apenas datas FUTURAS, aceitando
#: D0. Aqui D0 nao pode entrar, porque a fato so' materializa dias FECHADOS --
#: pedir hoje devolveria uma janela vazia que pareceria queda operacional.
#: O endpoint do Full ML NAO e' alterado por este gate.
MAX_RANGE_DAYS_SHOPEE_FBS = 366
DEFAULT_DAYS_SHOPEE_FBS = 30


def _shopee_fbs_periodo(
    date_from: Optional[date],
    date_to: Optional[date],
    hoje: date,
) -> tuple[date, date, date]:
    """Resolve e valida a janela. Devolve (from, to, last_closed).

    Toda recusa e' 422 com mensagem FIXA -- nenhuma delas ecoa o valor
    recebido, para que entrada maliciosa nao volte no corpo da resposta.
    """
    last_closed = hoje - timedelta(days=1)

    if (date_from is None) != (date_to is None):
        raise HTTPException(422, "date_from e date_to devem ser informados juntos.")

    if date_from is None:
        date_to = last_closed
        date_from = last_closed - timedelta(days=DEFAULT_DAYS_SHOPEE_FBS - 1)
        return date_from, date_to, last_closed

    if date_from > date_to:
        raise HTTPException(422, "date_from nao pode ser posterior a date_to.")
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS_SHOPEE_FBS:
        raise HTTPException(
            422,
            f"Intervalo maximo permitido e de {MAX_RANGE_DAYS_SHOPEE_FBS} dias.")
    if date_to > last_closed:
        # Cobre D0 E futuro na MESMA regra: a fato so' publica dia fechado.
        raise HTTPException(
            422,
            "date_to nao pode ser posterior ao ultimo dia fechado (D-1). "
            "Esta superficie publica apenas dias fechados.")
    return date_from, date_to, last_closed


def _shopee_fbs_lista(valores: Optional[list[str]], rotulo: str,
                      dominio: tuple[str, ...]) -> Optional[list[str]]:
    """Normaliza e valida contra o dominio. Valor fora da allowlist e' 422 e
    NUNCA e' ecoado -- a mensagem diz o que era esperado, nao o que veio."""
    if not valores:
        return None
    limpos = [v.strip().lower() for v in valores if v and v.strip()]
    if not limpos:
        return None
    if any(v not in dominio for v in limpos):
        raise HTTPException(
            422,
            f"{rotulo} invalido. Valores aceitos: {', '.join(sorted(dominio))}.")
    return sorted(set(limpos))


@router.get(
    "/shopee-fbs",
    response_model=Union[ShopeeFbsResponse, ShopeeFbsUnavailableResponse],
)
def shopee_fbs(
    date_from: Optional[date] = Query(None, description="Inicio, inclusivo."),
    date_to: Optional[date] = Query(None, description="Fim, inclusivo. Teto D-1."),
    brands: Optional[list[str]] = Query(None, description="Marcas cobertas pela esteira API."),
    accounts: Optional[list[str]] = Query(None, description="Contas da esteira API."),
    db: Session = Depends(get_db),
):
    """Desempenho FBS da Shopee: fulfillment da Shopee contra envio pelo vendedor.

    COBERTURA PARCIAL, e a resposta diz isso. A esteira API cobre apice,
    barbours, lescent e rituaria. **Kokeshi nao esta na API** e nao e' suprida
    por planilha aqui -- o agregado e' "Shopee (cobertura API)", nunca
    "Shopee total".

    O valor e' **GMV BRUTO de pedidos nao cancelados**: inclui `to_return` e
    `unpaid` (publicados tambem em coluna propria) e exclui cancelados. Nao e'
    receita liquida nem realizada.

    Le apenas `marts.fact_shopee_fbs_daily`. Nenhuma consulta ao Data Mart
    acontece durante o request.
    """
    # FLAG PRIMEIRO: com ela desligada nenhuma consulta e' emitida, e nem
    # sequer exigimos sessao de banco. 200 com estado explicito -- "desligado"
    # precisa ser distinguivel de "quebrado".
    if not settings.shopee_fbs_enabled:
        return ShopeeFbsUnavailableResponse(
            scope_label=shopee_fbs_svc.SCOPE_LABEL,
            unavailable_reason=MOTIVO_SHOPEE_FBS_DESLIGADO,
        )

    df, dt, last_closed = _shopee_fbs_periodo(date_from, date_to, today_brt())
    marcas = _shopee_fbs_lista(
        brands, "brands", tuple(shopee_fbs_svc.EXPECTED_BRANDS))
    contas = _shopee_fbs_lista(
        accounts, "accounts", tuple(shopee_fbs_svc.EXPECTED_ACCOUNTS))

    try:
        return shopee_fbs_svc.get_shopee_fbs_block(
            _require_db(db), df, dt,
            brands=marcas, accounts=contas,
            last_closed_date=last_closed,
        )
    except shopee_fbs_svc.ShopeeFbsContractError:
        # Contrato da FONTE quebrado (classe desconhecida). Nao e' erro do
        # cliente: mudar a requisicao nao resolve, logo nao pode ser 422.
        raise HTTPException(503, ERRO_SHOPEE_FBS_CONTRATO)
    except shopee_fbs_svc.ShopeeFbsUnavailable:
        raise HTTPException(503, ERRO_SHOPEE_FBS_INDISPONIVEL)


# ===========================================================================
# Gate FULL-SOURCE-3 — ESTOQUE Full (FBS) da Shopee
# ---------------------------------------------------------------------------
# Superficie NOVA, colada no router JA' registrado de proposito: o PR #45 esta'
# corrigindo `app/main.py`, e registrar um router novo exigiria editar
# exatamente o arquivo que ele toca. O prefixo continua `/api/v1/performance`.
# ===========================================================================

# Textos LIDOS PELO GESTOR: viajam no payload e a tela os imprime como estao,
# entao levam acentuacao correta -- ao contrario dos comentarios deste arquivo.
MOTIVO_ESTOQUE_FULL_DESLIGADO = (
    "Tela de Estoque Full da Shopee ainda não habilitada. A fotografia de "
    "estoque não foi publicada e a ativação é decisão de negócio."
)
ERRO_ESTOQUE_FULL_CONTRATO = (
    "Estoque Full da Shopee indisponível: a fonte devolveu classificação fora "
    "do domínio autorizado. Acione o time de dados."
)

#: Teto de linhas aceito do cliente. O servico ainda aplica o proprio teto;
#: este existe para que um `limite=999999` seja recusado antes do banco.
LIMITE_PADRAO_ESTOQUE_FULL = 500


@router.get(
    "/shopee-fbs-estoque",
    response_model=Union[EstoqueFullResponse, EstoqueFullUnavailableResponse],
)
def shopee_fbs_estoque(
    brands: Optional[list[str]] = Query(
        None, description="Marcas cobertas pela esteira API."),
    accounts: Optional[list[str]] = Query(
        None, description="Contas da esteira API."),
    classificacoes: Optional[list[str]] = Query(
        None, description="Classificacao da Torre. Dominio fechado."),
    busca: Optional[str] = Query(
        None, max_length=120, description="Casa SKU ou nome do produto."),
    somente_acao: bool = Query(
        False, description="So' ruptura e baixo: o que exige acao hoje."),
    limite: int = Query(
        LIMITE_PADRAO_ESTOQUE_FULL, ge=1, le=fbs_stock_svc.MAX_PRODUTOS),
    db: Session = Depends(get_db),
):
    """Estoque fisico no CD da Shopee (FBS), por produto, na ultima fotografia.

    Mede o ESTOQUE, nao o desempenho: `/shopee-fbs` classifica o PEDIDO pela
    modalidade e nao e' tocado aqui. O estoque Full e' a soma de
    `shopee_stock` VENDAVEL, conciliada com o "Total Vendavel" do Seller
    Center. Reservado, estoque do vendedor e o agregado da Shopee viajam como
    CONTEXTO e nunca como estoque Full.

    "Cobertura da Torre" e' calculo NOSSO, com limiares PROVISORIOS -- nao
    reproduz formula da Shopee.

    **Ausencia nunca vira zero.** Flag desligada, fato inexistente (migrations
    020/021 nao aplicadas) e fotografia nunca publicada devolvem 200 com
    `status="unavailable"` e `motivo_tecnico` proprio.

    Le apenas as duas fatos de estoque. Nenhuma consulta ao Data Mart, a `raw`
    ou a' API da Shopee acontece durante o request.
    """
    # FLAG PRIMEIRO: com ela desligada nenhuma consulta e' emitida, e nem
    # sequer exigimos sessao de banco.
    if not settings.shopee_fbs_stock_enabled:
        return EstoqueFullUnavailableResponse(
            scope_label=fbs_stock_svc.SCOPE_LABEL,
            unavailable_reason=MOTIVO_ESTOQUE_FULL_DESLIGADO,
            motivo_tecnico="feature_flag_desligada",
        )

    try:
        return fbs_stock_svc.get_estoque_full_block(
            _require_db(db),
            hoje=today_brt(),
            brands=brands,
            accounts=accounts,
            classificacoes=classificacoes,
            busca=busca,
            somente_acao=somente_acao,
            limite=limite,
        )
    except fbs_stock_svc.FiltroInvalido as invalido:
        # Filtro fora da allowlist. AQUI o 422 e' correto: mudar a requisicao
        # resolve. A mensagem e' montada so' com constantes do servidor -- o
        # valor recebido NAO volta no corpo, para que o endpoint nao sirva de
        # espelho de texto arbitrario.
        raise HTTPException(422, invalido.mensagem_segura)
    except fbs_stock_svc.EstoqueFullIndisponivel as ausente:
        # 🔑 200, nao 503: a tabela faltar e' estado CONHECIDO deste rollout
        # (migrations pendentes), nao falha. A tela precisa desenhar
        # "indisponivel" com explicacao -- e 503 viraria erro generico, que o
        # front tende a mostrar como vazio, isto e', como zero.
        return EstoqueFullUnavailableResponse(
            scope_label=fbs_stock_svc.SCOPE_LABEL,
            unavailable_reason=ausente.mensagem,
            motivo_tecnico=ausente.motivo_tecnico,
            limitacoes=[fbs_stock_svc.LIMITACAO_CARGA_MANUAL],
        )
    except fbs_stock_svc.EstoqueFullContractError:
        # Contrato da FONTE quebrado. Nao e' erro do cliente.
        raise HTTPException(503, ERRO_ESTOQUE_FULL_CONTRATO)
