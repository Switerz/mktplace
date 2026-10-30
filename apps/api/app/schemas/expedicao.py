"""Gate EXP-2A — schema read-only da Expedicao.

TRES IDADES QUE NAO PODEM SER A MESMA COISA
--------------------------------------------
O contrato do pipeline (docs/expedicao_contrato.md) separa tres medidas que a
API nao pode voltar a misturar:

  `freshness` / `source_age_hours`  a FONTE foi lida recentemente? Vem do
                                    watermark POR CONTA, e so' dele.
  `oldest_row_age_hours`            ha quanto tempo a linha mais velha do
                                    backlog nao e' relida. CONTEXTO: um backlog
                                    legitimo sempre tem pedido antigo.
  `deadline_status` / `over_48h`    o PEDIDO esta atrasado para o cliente.
                                    Situacao operacional, nao frescor.

Confundir as duas primeiras fez as quatro marcas nascerem `fail`/`high` no
primeiro piloto com a fonte a 0,55h de idade (EXP-1E). Alerta sempre vermelho e'
alerta ignorado.

SEM IDENTIFICADOR DE PEDIDO
---------------------------
`marketplace_order_id` (o `order_sn` da Shopee) NAO e' servido. Ver
`ExpedicaoOrderRow.order_ref` e a secao "Politica de identificadores" no
contrato: esta API nao tem autenticacao, e um identificador que permite consultar
o pedido no painel do marketplace nao vai numa rota publica.

NENHUMA MEDIDA AUSENTE VIRA ZERO
--------------------------------
Contagem ausente e' `None`, nunca `0`. Zero afirmaria uma medicao — "nenhum
pedido vencido" — que nao foi feita.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

#: Dominios do contrato do pipeline. Sao os mesmos CHECKs da migration 018;
#: repeti-los aqui e' o que impede a API de aceitar filtro que o banco recusa.
DeadlineStatus = Literal["overdue", "due_within_24h", "on_time", "unavailable"]
OperationalAgeStatus = Literal["within_48h", "over_48h", "unknown"]
FreshnessStatus = Literal["fresh", "stale", "critical", "unknown"]
TimestampQuality = Literal["verified", "assumed", "unknown"]
DeadlineSource = Literal["marketplace_native", "unavailable"]

#: `SourceHealth` do pipeline. So' `healthy` autoriza publicacao; os demais
#: significam que a fotografia servida e a ANTERIOR, preservada de proposito.
SourceHealth = Literal[
    "healthy", "account_missing", "unexpected_account",
    "registry_ambiguous", "watermark_missing", "source_unavailable",
]

#: Como o dado chegou. Um unico valor hoje, e e' contrato: **nao existe
#: agendamento**. Toda fotografia veio de alguem rodando o comando a mao.
LoadMode = Literal["manual_snapshot"]

#: Situacao operacional filtravel. `stalled` e' a UNIAO de lento e zumbi —
#: nunca a soma, porque o mesmo pedido pode ser os dois.
SituacaoOperacional = Literal[
    "overdue", "due_within_24h", "on_time", "deadline_unavailable",
    "over_48h", "stalled", "slow", "zombie",
]

#: Estado do envelope. `unavailable` responde 200 com motivo estruturado; nao e'
#: erro do cliente e nao e' 500.
Availability = Literal["available", "unavailable"]

UNAVAILABLE_DISABLED = "feature_flag_disabled"
#: Canal conhecido, porem ainda nao exposto (flag propria do ML).
UNAVAILABLE_CHANNEL_DISABLED = "channel_disabled"
UNAVAILABLE_NO_SNAPSHOT = "no_snapshot_published"
UNAVAILABLE_INCONSISTENT_BATCH = "inconsistent_batch"


class CoberturaConta(BaseModel):
    """Uma conta esperada pelo registry, com o que a fonte entregou."""

    shop_account: str
    brand: str
    observed: bool = Field(description="A conta apareceu na fotografia publicada?")
    backlog_count: Optional[int] = None
    source_watermark_at: Optional[datetime] = None
    source_age_hours: Optional[float] = None
    source_advanced: Optional[bool] = None


class Cobertura(BaseModel):
    """Conjuntos ESPERADO x OBSERVADO, nunca so' um dos dois.

    Comparar por igualdade de conjuntos e' o que transforma "sumiu uma conta" em
    `missing_accounts` em vez de silencio.
    """

    #: Os quatro conjuntos abaixo estao SEMPRE no dominio da CONTA, no mesmo
    #: formato de `shop_account`: nome da loja na Shopee, `seller_id` no Mercado
    #: Livre. Comparar marca com conta nunca acusaria conta faltando.
    expected_accounts: list[str]
    observed_accounts: list[str]
    missing_accounts: list[str]
    unexpected_accounts: list[str]
    accounts: list[CoberturaConta]
    #: Marcas que a torre acompanha mas que esta FONTE nao cobre. Hoje:
    #: Kokeshi, que existe em `marts.dim_loja` e nao existe em
    #: `raw.shopee_orders` — medido, nao suposto.
    brands_not_covered: list[str]


class FrescorMarca(BaseModel):
    """Frescor da FONTE por marca.

    `freshness` vem da observacao MAIS RECENTE de `expedicao_source_freshness`.
    O historico guarda linhas com a semantica antiga (pior pedido do backlog);
    agregar o pior valor historico ressuscitaria justamente o defeito corrigido.
    """

    brand: str
    freshness: FreshnessStatus
    status: Optional[str] = Field(default=None, description="pass | warn | fail")
    severity: Optional[str] = None
    source_watermark_at: Optional[datetime] = None
    source_age_hours: Optional[float] = None
    #: CONTEXTO. Nao reprova a fonte e nao entra em `freshness`.
    oldest_row_age_hours: Optional[float] = None
    accounts: Optional[int] = None
    observed_at: Optional[datetime] = None
    #: O que a linha realmente mediu. `source_watermark_only` e' a semantica
    #: corrigida; ausente significa linha antiga, que a API nao serve como atual.
    measures: Optional[str] = None
    #: `true` quando NAO havia observacao vigente com a semantica correta e o
    #: estado foi derivado do watermark deste lote. Fica explicito para que
    #: ninguem confunda estado auditado com estado inferido.
    derived_from_batch: bool = False


class ResumoConta(BaseModel):
    """Medidas materializadas por conta. Nada e' recalculado aqui."""

    shop_account: str
    brand: str
    backlog_count: int
    overdue_count: int
    due_within_24h_count: int
    on_time_count: int
    deadline_unavailable_count: int
    over_48h_count: int
    slow_count: int
    zombie_count: int
    #: UNIAO de lento e zumbi. `slow + zombie` contaria a sobreposicao duas vezes.
    stalled_count: int
    run_status: str
    source_watermark_at: Optional[datetime] = None
    source_advanced: bool
    snapshot_hour: datetime


class ResumoCanal(BaseModel):
    """Soma das contas do MESMO batch. Nao e' linha materializada.

    O total do canal nao existe na tabela de proposito: persistido, viraria uma
    quinta linha que nenhum `GROUP BY shop_account` sabe excluir.
    """

    backlog_count: int
    overdue_count: int
    due_within_24h_count: int
    on_time_count: int
    deadline_unavailable_count: int
    over_48h_count: int
    slow_count: int
    zombie_count: int
    stalled_count: int


class OrderRow(BaseModel):
    """Uma linha da fila.

    `order_number` e' o `marketplace_order_id` da fila, servido so' com
    `expedicao_numero_pedido_publico` ligado (decisao de produto de
    30/10/2026, ate' a Torre ter login; ver `config.py`). Desligado, fica nulo.
    Na Shopee e' o numero do pedido; no Mercado Livre a fila e' por envio, entao
    e' o `shipment_id`, NAO o numero da venda.

    `order_ref` e' opaco e existe so' quando `expedicao_order_ref_secret` esta
    configurado; sem segredo fica nulo. Um hash sem chave seria reversivel por
    forca bruta — o espaco de `order_sn` e' pequeno e enumeravel.
    """

    order_number: Optional[str] = None
    order_ref: Optional[str] = None
    shop_account: str
    brand: str
    created_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    dispatch_deadline: Optional[datetime] = None
    deadline_source: DeadlineSource
    deadline_status: DeadlineStatus
    operational_age_status: OperationalAgeStatus
    is_slow_vs_baseline: bool
    is_source_zombie: bool
    is_stalled: bool
    hours_open: Optional[float] = None
    hours_overdue: Optional[float] = None
    logistic_type: Optional[str] = None
    carrier: Optional[str] = None
    source_ingested_at: Optional[datetime] = None
    source_freshness_status: FreshnessStatus
    timestamp_quality: TimestampQuality
    #: Etapa do pedido na fila (T-0015, migration 033): dominio de
    #: `ETAPAS_FILA`. Nula sem a 033 no destino; `nao_publicada` quando a
    #: linha foi gravada pelo publicador antigo.
    etapa: Optional[str] = None


class Paginacao(BaseModel):
    limit: int
    offset: int
    returned: int
    total: int
    has_more: bool


class Snapshot(BaseModel):
    """Identidade da fotografia servida. Tudo do MESMO batch."""

    channel: str
    refresh_batch_id: str
    effective_at: datetime
    snapshot_hour: datetime
    load_mode: LoadMode
    source_health: SourceHealth
    source_advanced: bool
    run_status: Optional[str] = None
    rows_extracted: Optional[int] = None
    rows_loaded: Optional[int] = None
    audit_run_id: Optional[int] = None


class Limitacoes(BaseModel):
    """O que esta resposta NAO diz. Fica no payload, nao so' na documentacao."""

    #: Nao existe agendamento. A fotografia envelhece ate alguem rodar o comando.
    no_automation: bool = True
    load_mode: LoadMode = "manual_snapshot"
    snapshot_age_hours: Optional[float] = None
    #: Kokeshi nao e' coberta por esta fonte.
    brands_not_covered: list[str] = Field(default_factory=list)
    order_identifier_withheld: bool = True
    notes: list[str] = Field(default_factory=list)


class ExpedicaoResponse(BaseModel):
    """Resumo + pagina da fila, sempre do mesmo batch."""

    availability: Availability
    unavailable_reason: Optional[str] = None
    channel: str = "shopee"
    snapshot: Optional[Snapshot] = None
    totals: Optional[ResumoCanal] = None
    accounts: list[ResumoConta] = Field(default_factory=list)
    freshness: list[FrescorMarca] = Field(default_factory=list)
    coverage: Optional[Cobertura] = None
    queue: list[OrderRow] = Field(default_factory=list)
    pagination: Optional[Paginacao] = None
    limitations: Limitacoes = Field(default_factory=Limitacoes)


class PontoTendencia(BaseModel):
    """Um grao horario POR CONTA. Horas diferentes nunca sao somadas."""

    snapshot_hour: datetime
    shop_account: str
    brand: str
    refresh_batch_id: str
    observed_at: datetime
    backlog_count: int
    overdue_count: int
    due_within_24h_count: int
    on_time_count: int
    deadline_unavailable_count: int
    over_48h_count: int
    slow_count: int
    zombie_count: int
    stalled_count: int
    source_watermark_at: Optional[datetime] = None
    source_advanced: bool
    run_status: str


class TendenciaResponse(BaseModel):
    availability: Availability
    unavailable_reason: Optional[str] = None
    channel: str = "shopee"
    #: Janela efetivamente aplicada, que pode ser menor que a pedida.
    window_hours: int
    from_hour: Optional[datetime] = None
    to_hour: Optional[datetime] = None
    points: list[PontoTendencia] = Field(default_factory=list)
    truncated: bool = False
    limitations: Limitacoes = Field(default_factory=Limitacoes)


# ---------------------------------------------------------------------------
# Gate EXP-TK-OPS-1/2 — TikTok Shop: LDR e fluxo por data de pagamento
# ---------------------------------------------------------------------------
UNAVAILABLE_NOT_MIGRATED = "table_not_migrated"
UNAVAILABLE_NO_SERIES = "no_series_published"


class TikTokLdrDia(BaseModel):
    """Um dia de VENCIMENTO. E' o grao da metrica oficial.

    Nao confundir com `TikTokFluxoDia`, que e' por data de PAGAMENTO. Um
    feriado empurra tres datas de pagamento para o mesmo vencimento: aqui isso
    vira uma linha com o volume somado, la' virariam tres linhas de ~100%.
    """

    due_date: date
    base: int
    late: int
    pending_overdue: int
    pending_on_time: int
    #: `None` quando nao ha base. NUNCA 0.0 nesse caso.
    rate: Optional[float]
    is_mature: bool
    #: Acima da META do TikTok (4%): conformidade com a plataforma.
    above_target: bool
    #: Acima do limiar INTERNO (10%): severidade nossa. Sao coisas diferentes.
    is_critical: bool


class TikTokFluxoDia(BaseModel):
    """Uma data de PAGAMENTO. Reproduz as colunas da planilha da gestao.

    NAO e' a LDR: o denominador e' quem pagou no dia, nao quem vence no dia.
    """

    paid_date: date
    deadline_date: date
    paid_orders: int
    excluded_samples: int
    excluded_cancelled: int
    base: int
    shipped_on_time: int
    shipped_late: int
    pending_overdue: int
    pending_on_time: int
    late: int
    rate: Optional[float]
    is_mature: bool


class TikTokLdr(BaseModel):
    """LDR OPERACIONAL — vencimentos na janela. A metrica oficial."""

    from_: date = Field(alias="from")
    to: date
    days: int
    rate: Optional[float]
    base: int
    late: int
    pending_overdue: int
    pending_on_time: int
    pending_at_risk: int
    mature_due_days: int
    partial_due_days: int
    above_target: bool
    internal_critical: bool
    incident_start: Optional[date] = None
    incident_end: Optional[date] = None
    daily: list[TikTokLdrDia] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class TikTokMarcaItem(BaseModel):
    brand: str
    shop_name: Optional[str] = None
    base: int
    late: int
    rate: Optional[float]
    above_target: bool


class TikTokSnapshot(BaseModel):
    refresh_batch_id: str
    effective_at: datetime
    source_watermark_at: Optional[datetime] = None
    today_brt: date


class TikTokAlerta(BaseModel):
    severity: Literal["critical", "warning", "info"]
    code: str
    message: str


class TikTokDispatchResponse(BaseModel):
    availability: Literal["available", "unavailable"]
    unavailable_reason: Optional[str] = None
    channel: Literal["tiktokshop"]
    event: Optional[Literal["despacho", "coleta"]] = None
    #: SEMPRE `true` enquanto o SLA oficial do TikTok nao for ingerido. O prazo
    #: aqui e' reconstruido da politica de dias uteis, e nenhuma tela pode
    #: exibir a taxa como se fosse a medicao de penalizacao da plataforma.
    deadline_is_reconstructed: bool
    #: Os prazos da POLITICA, por evento: RTS 1 dia util, TTS 2.
    sla_business_days: dict[str, int]
    #: `tiktok_ldr` = meta da plataforma (conformidade).
    #: `internal_critical` = limiar nosso (severidade). Nomes distintos de
    #: proposito: confundi-los faria a tela dizer "dentro da meta" num dia
    #: de 9%.
    targets: dict[str, float]
    snapshot: Optional[TikTokSnapshot] = None
    ldr: Optional[TikTokLdr] = None
    payment_flow: list[TikTokFluxoDia] = Field(default_factory=list)
    brands: list[TikTokMarcaItem] = Field(default_factory=list)
    alerts: list[TikTokAlerta] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# EXPEDICAO-SHOPEE-LDR-1 — LDR operacional da Shopee (coleta x prazo Shopee)
# ---------------------------------------------------------------------------
class ShopeeLdrDia(BaseModel):
    """Um dia de VENCIMENTO (`ship_by_date` em Brasilia)."""

    due_date: date
    base: int
    late: int
    shipped_on_time: int
    shipped_late: int
    pending_overdue: int
    pending_on_time: int
    excluded_fbs: int
    excluded_cancelled: int
    shipped_without_pickup: int
    #: `None` sem base OU enquanto o dia nao amadurece. NUNCA 0.0 nesses casos.
    rate: Optional[float]
    is_mature: bool


class ShopeeLdr(BaseModel):
    from_: date = Field(alias="from")
    to: date
    days: int
    #: `None` sem vencimento maduro ou com cobertura insuficiente.
    rate: Optional[float]
    #: Por que a taxa existe e nao e' mostrada (hoje: `coverage_insufficient`).
    rate_hidden_reason: Optional[str] = None
    base: int
    late: int
    shipped_on_time: int
    shipped_late: int
    pending_overdue: int
    pending_on_time: int
    pending_at_risk: int
    mature_due_days: int
    partial_due_days: int
    daily: list[ShopeeLdrDia] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ShopeeLdrExclusoes(BaseModel):
    """Fora da taxa, cada um com contador proprio. Ausencia nunca vira zero na
    taxa: some do DENOMINADOR e aparece aqui."""

    fbs: int
    cancelled_without_pickup: int
    #: Subconjunto de `cancelled_without_pickup`: "Seller did not Ship".
    cancelled_seller_did_not_ship: int
    #: Subconjunto: motivo fora do vocabulario medido (alerta de qualidade).
    cancelled_unknown_reason: int
    shipped_without_pickup: int
    without_deadline: int


class ShopeeLdrCobertura(BaseModel):
    state: Literal["completa", "aviso", "insuficiente", "sem_base"]
    missing_share: Optional[float] = None
    missing: int
    eligible: int
    without_deadline: int
    shipped_without_pickup: int


class ShopeeLdrMarca(BaseModel):
    brand: str
    base: int
    late: int
    rate: Optional[float]
    coverage_state: Literal["completa", "aviso", "insuficiente", "sem_base"]
    excluded_fbs: int
    excluded_cancelled: int
    source_watermark_at: Optional[datetime] = None


class ShopeeLdrSnapshot(BaseModel):
    refresh_batch_id: str
    effective_at: datetime
    source_watermark_at: Optional[datetime] = None
    today_brt: date
    age_hours: float
    #: Idade da leitura MAIS RECENTE da fonte entre as contas. `None` = sem leitura.
    source_age_hours: Optional[float] = None
    #: Publicacao OU fonte mais velha que o limite: a tela diz "fonte vencida".
    stale: bool


class ShopeeLdrAlerta(BaseModel):
    severity: Literal["critical", "warning", "info"]
    code: str
    message: str


class ShopeeFilaContagem(BaseModel):
    """Mesmas medidas da fila publicada (`Totais`), para UMA parcela."""

    backlog_count: int
    overdue_count: int
    due_within_24h_count: int
    on_time_count: int
    deadline_unavailable_count: int
    over_48h_count: int
    slow_count: int
    zombie_count: int
    stalled_count: int


class ShopeeFilaConta(BaseModel):
    shop_account: str
    brand: str
    seller: ShopeeFilaContagem
    fbs: ShopeeFilaContagem


class ShopeeFilaPorFluxo(BaseModel):
    """Fila aberta da Shopee separada por responsabilidade de envio.

    `seller` = fila ACIONAVEL do vendedor; `fbs` = pedidos que a Shopee despacha
    do proprio armazem. `total` existe so' para reconciliar as parcelas.
    """

    effective_at: datetime
    source_watermark_at: Optional[datetime] = None
    stale: bool
    seller: ShopeeFilaContagem
    fbs: ShopeeFilaContagem
    total: ShopeeFilaContagem
    accounts: list[ShopeeFilaConta] = Field(default_factory=list)


class ShopeeLdrResponse(BaseModel):
    availability: Literal["available", "unavailable"]
    unavailable_reason: Optional[str] = None
    channel: Literal["shopee"]
    metric_name: str
    #: SEMPRE `false`: nao e' a taxa que a Shopee usa para penalizacao.
    official: bool
    disclaimer: str
    event: Literal["coleta"]
    thresholds: dict[str, float]
    snapshot: Optional[ShopeeLdrSnapshot] = None
    ldr: Optional[ShopeeLdr] = None
    exclusions: Optional[ShopeeLdrExclusoes] = None
    coverage: Optional[ShopeeLdrCobertura] = None
    brands: list[ShopeeLdrMarca] = Field(default_factory=list)
    alerts: list[ShopeeLdrAlerta] = Field(default_factory=list)
    #: `None` = nada publicado (ausencia), nunca uma fila de zeros.
    queue_by_flow: Optional[ShopeeFilaPorFluxo] = None


# ---------------------------------------------------------------------------
# T-0011 — Jornada de pedidos (contrato jornada v1)
# ---------------------------------------------------------------------------
# Rota ADITIVA: nada acima muda. Nomes em portugues, como no contrato.
#
# A API NUNCA manda percentual: so' `count` e `base`. Ausencia nao e' zero —
# medida que a fonte nao publica sai `None` com `reason`.
JornadaCanal = Literal["shopee", "mercadolivre", "tiktok"]
JornadaStatus = Literal["ok", "partial", "unavailable", "disabled"]


class JornadaEtapa(BaseModel):
    """Etapa EXCLUSIVA. A soma das etapas de uma secao e' a `base` dela."""

    key: str
    label: str
    count: int


class JornadaSobreposicao(BaseModel):
    """Recorte NAO somavel: subconjunto da base (ou da etapa `subconjunto_de`).

    `count = None` com `reason` quando a fonte nao publica a medida — zero
    afirmaria uma medicao que nao houve.
    """

    key: str
    count: Optional[int]
    subconjunto_de: Optional[str] = None
    reason: Optional[str] = None


class JornadaFilaAberta(BaseModel):
    grain: Literal["pedido", "shipment"]
    #: `effective_at` da fotografia publicada.
    snapshot_at: datetime
    freshness: FreshnessStatus
    #: COUNT(*) no servidor, base COMPLETA, com o filtro de marca.
    base: int
    etapas: list[JornadaEtapa]
    #: `sum(etapas.count) == base`, conferido no servidor.
    etapas_fecham: bool
    sobreposicoes: list[JornadaSobreposicao]
    limitacoes: list[str] = Field(default_factory=list)


class JornadaJanela(BaseModel):
    inicio: date
    fim: date
    dias: int


class JornadaForaDaCoorte(BaseModel):
    key: str
    count: int


class JornadaMaturidade(BaseModel):
    dias_maduros: int
    dias_imaturos: int


class JornadaCoorte(BaseModel):
    eixo: Literal["paid_date", "deadline_date"]
    evento: Literal["despacho", "coleta"]
    janela: JornadaJanela
    #: Soma de todas as etapas (= pagos brutos no TikTok; = pagos no eixo Shopee),
    #: somada no servidor de forma INDEPENDENTE das etapas.
    base: int
    etapas: list[JornadaEtapa]
    etapas_fecham: bool
    sobreposicoes: list[JornadaSobreposicao]
    fora_da_coorte: list[JornadaForaDaCoorte] = Field(default_factory=list)
    maturidade: JornadaMaturidade
    #: `max(effective_at)` das linhas da janela (sem filtro de marca), ou `None`.
    publicado_em: Optional[datetime] = None
    #: `max(source_watermark_at)` das linhas da janela com o filtro de marca (o
    #: watermark e' por conta); marca sem linha na janela usa o do canal.
    #: `None` = a publicacao nao registra leitura da fonte.
    fonte_lida_em: Optional[datetime] = None
    #: Mesma regra da rota irma. `/shopee/ldr` (`snapshot.stale`): publicacao
    #: velha, fonte velha OU fonte sem carimbo. `/tiktok` (alerta
    #: `fotografia_velha`): so' a publicacao. Leva o `status` a no minimo
    #: `partial`, e a limitacao diz qual foi a causa.
    desatualizada: bool = False
    limitacoes: list[str] = Field(default_factory=list)


class JornadaResponse(BaseModel):
    channel: JornadaCanal
    status: JornadaStatus
    reason: Optional[str] = None
    #: Marcas aplicadas, normalizadas (minusculas, sem repeticao). `[]` = todas.
    brands_applied: list[str] = Field(default_factory=list)
    generated_at: datetime
    #: `None` quando o canal nao tem fila em grao de pedido (TikTok) ou a fonte
    #: esta ausente.
    fila_aberta: Optional[JornadaFilaAberta] = None
    #: `None` quando o canal nao tem coorte (ML; Shopee sem LDR) ou a fonte
    #: esta ausente.
    coorte: Optional[JornadaCoorte] = None
    #: Limitacoes do CANAL que nao cabem numa secao — sobretudo a de uma secao
    #: `None` (ex.: "o Mercado Livre nao publica prazo de despacho").
    limitacoes: list[str] = Field(default_factory=list)
