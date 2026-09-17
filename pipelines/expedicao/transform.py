"""Transformacao deterministica: linhas da fonte -> fila, historico e execucao.

Funcoes PURAS. Nenhuma abre conexao, le ambiente ou consulta relogio: o instante
entra por `effective_at` em todas as assinaturas. `datetime.now()` e
`date.today()` nao aparecem neste modulo — e o teste de contrato verifica isso
pela arvore sintatica, nao por grep.

O motivo e o mesmo de `pipelines/common/operational_calendar.py`: relogio lido
dentro da funcao torna o resultado irreproduzivel e, pior, adota o fuso do
processo (BRT no notebook, UTC no worker) sem que ninguem perceba o deslocamento
de 3 horas — que e justamente a largura que decide a fronteira das 48 horas.

POR QUE `effective_at` E NAO `refresh_at` (EXP-1A-R)
----------------------------------------------------
As classificacoes dependem do RELOGIO, nao so da fonte. Com a fonte parada, um
pedido ainda atravessa a faixa de 24h, o prazo nativo, as 48h, os 30 dias e o
2 x p50. `effective_at` deixa explicito que o instante e uma ENTRADA que produz
o estado — e por isso cada execucao agendada recomputa tudo.

ORTOGONALIDADE
--------------
`deadline_status`, `operational_age_status`, `is_slow_vs_baseline` e
`is_source_zombie` sao calculados de forma independente. Nenhuma funcao aqui
consulta o resultado da outra para se decidir. `is_stalled` e o unico derivado,
e e apenas o OR das duas anomalias.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pipelines.expedicao.contract import (
    DUE_SOON_WINDOW,
    FRESHNESS_FRESH_LIMIT,
    FRESHNESS_STALE_LIMIT,
    MIN_BASELINE_SAMPLE,
    ML_SOURCE_UTC_OFFSET,
    OPERATIONAL_AGE_LIMIT,
    SLOW_BASELINE_FACTOR,
    SOURCE_ZOMBIE_AGE,
    Channel,
    DeadlineStatus,
    FreshnessStatus,
    OperationalAgeStatus,
    RegistryError,
    RunStatus,
    SellerAccount,
    TimestampQuality,
)

#: Valores de `deadline_source` persistidos na fila.
DEADLINE_SOURCE_NATIVE = "marketplace_native"
DEADLINE_SOURCE_UNAVAILABLE = "unavailable"


def _require_aware(instante: datetime, rotulo: str) -> datetime:
    if instante.tzinfo is None:
        raise ValueError(
            f"{rotulo} sem timezone: a transformacao exige instante com fuso "
            "explicito (um naive seria lido no fuso do processo)."
        )
    return instante


def hours_between(inicio: datetime | None, fim: datetime) -> float | None:
    """Horas de `inicio` ate `fim`. `None` quando o marco nao existe."""
    if inicio is None:
        return None
    return (fim - inicio).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Prazo contratual do marketplace
# ---------------------------------------------------------------------------
def classify_deadline(
    dispatch_deadline: datetime | None, effective_at: datetime
) -> DeadlineStatus:
    """Fronteiras, explicitas:

        prazo is None                                -> UNAVAILABLE
        prazo <  effective_at                        -> OVERDUE
        effective_at <= prazo < effective_at + 24h   -> DUE_WITHIN_24H
        prazo >= effective_at + 24h                  -> ON_TIME

    Prazo exatamente igual a `effective_at` cai em DUE_WITHIN_24H, nao em
    OVERDUE: o instante do vencimento ainda nao passou.
    """
    _require_aware(effective_at, "effective_at")
    if dispatch_deadline is None:
        return DeadlineStatus.UNAVAILABLE
    _require_aware(dispatch_deadline, "dispatch_deadline")
    if dispatch_deadline < effective_at:
        return DeadlineStatus.OVERDUE
    if dispatch_deadline < effective_at + DUE_SOON_WINDOW:
        return DeadlineStatus.DUE_WITHIN_24H
    return DeadlineStatus.ON_TIME


def hours_overdue(
    dispatch_deadline: datetime | None, effective_at: datetime
) -> float | None:
    """Horas de estouro. `None` fora de OVERDUE — nunca zero nem negativo.

    Zero significaria "venceu agora"; `None` significa "nao venceu". Sao coisas
    diferentes e a coluna precisa distinguir.
    """
    if classify_deadline(dispatch_deadline, effective_at) is not DeadlineStatus.OVERDUE:
        return None
    return (effective_at - dispatch_deadline).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Idade operacional — regra de negocio de 48h, independente do prazo
# ---------------------------------------------------------------------------
def classify_age(
    marco: datetime | None, effective_at: datetime
) -> OperationalAgeStatus:
    """`marco` = coalesce(pay_time, create_time).

    Estritamente MAIOR que 48h vira OVER_48H. Exatamente 48h ainda e WITHIN_48H:
    a regra e "mais de dois dias", nao "dois dias ou mais".
    """
    _require_aware(effective_at, "effective_at")
    if marco is None:
        return OperationalAgeStatus.UNKNOWN
    _require_aware(marco, "marco")
    if effective_at - marco > OPERATIONAL_AGE_LIMIT:
        return OperationalAgeStatus.OVER_48H
    return OperationalAgeStatus.WITHIN_48H


def marco_inicial(pay_time: datetime | None, create_time: datetime | None):
    """Pagamento primeiro; criacao como fallback.

    Pedido nao pago nao e backlog de expedicao, mas a Shopee pode trazer
    `pay_time` nulo em estado transitorio — nesses casos a criacao sustenta a
    idade em vez de descartar a linha.
    """
    return pay_time if pay_time is not None else create_time


# ---------------------------------------------------------------------------
# Anomalias — independentes entre si e independentes do prazo
# ---------------------------------------------------------------------------
def is_slow_vs_baseline(
    horas_aberto: float | None, sample_size: int, p50_horas: float | None
) -> bool:
    """`horas_aberto > 2 x p50` da propria conta.

    NAO e SLA e nao substitui as 48h: e anomalia relativa ao historico da
    propria marca. Amostra abaixo de `MIN_BASELINE_SAMPLE` ou p50 ausente/<=0
    devolve False — sem baseline nao se afirma lentidao.
    """
    if horas_aberto is None or p50_horas is None:
        return False
    if sample_size < MIN_BASELINE_SAMPLE or p50_horas <= 0:
        return False
    return horas_aberto > SLOW_BASELINE_FACTOR * p50_horas


def is_source_zombie(horas_aberto: float | None) -> bool:
    """Pedido ativo ha mais de 30 dias.

    Classifica, nao remove. O EXP-0R2 mediu 628 pedidos ML abertos com mais de
    60 dias; eles continuam na fila, marcados.
    """
    if horas_aberto is None:
        return False
    return horas_aberto > SOURCE_ZOMBIE_AGE.total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Freshness do DADO
# ---------------------------------------------------------------------------
def classify_freshness(
    source_ingested_at: datetime | None, effective_at: datetime
) -> FreshnessStatus:
    """fresh <= 8h < stale <= 24h < critical. Carimbo ausente -> UNKNOWN.

    A transformacao apenas CARREGA o estado; nao zera KPI. A decisao de suprimir
    numero e da API, que tem o contexto da tela.
    """
    _require_aware(effective_at, "effective_at")
    if source_ingested_at is None:
        return FreshnessStatus.UNKNOWN
    idade = effective_at - _require_aware(source_ingested_at, "source_ingested_at")
    if idade <= FRESHNESS_FRESH_LIMIT:
        return FreshnessStatus.FRESH
    if idade <= FRESHNESS_STALE_LIMIT:
        return FreshnessStatus.STALE
    return FreshnessStatus.CRITICAL


# ---------------------------------------------------------------------------
# Montagem da fila
# ---------------------------------------------------------------------------
def build_fila_shopee(
    linhas: list[dict],
    registry: dict[str, SellerAccount],
    baselines: dict[str, tuple[int, float | None]],
    effective_at: datetime,
    refresh_batch_id: str = "",
) -> list[dict]:
    """Linhas da fonte -> linhas de `marts.expedicao_fila_atual`.

    `registry` mapeia `external_seller_id` (o `shop_id` da Shopee) para a conta
    resolvida. Conta ausente levanta `RegistryError`: publicar marca adivinhada
    a partir do texto de `raw.shopee_orders.brand` e exatamente o que o contrato
    proibe.

    Nao ha deduplicacao por "versao mais recente": `pk_shopee_orders` e UNIQUE
    em `(shop_account, order_sn)`, entao a fonte ja devolve uma linha por
    pedido. Medido em 15/09/2026, janela de 30 dias: 29.369 linhas para 29.369
    chaves `(shop_account, order_sn)` e 29.369 `order_sn` distintos.
    """
    _require_aware(effective_at, "effective_at")
    saida: list[dict] = []
    for linha in linhas:
        seller_id = str(linha["shop_id"])
        conta = registry.get(seller_id)
        if conta is None:
            raise RegistryError(
                f"conta Shopee {seller_id} ausente do registry; "
                "o refresh nao infere marca por texto."
            )

        criado = linha.get("create_time")
        pago = linha.get("pay_time")
        marco = marco_inicial(pago, criado)
        prazo = linha.get("ship_by_date")
        ingerido = linha.get("ingested_at")

        horas_aberto = hours_between(marco, effective_at)
        amostra, p50 = baselines.get(seller_id, (0, None))
        lento = is_slow_vs_baseline(horas_aberto, amostra, p50)
        zumbi = is_source_zombie(horas_aberto)

        saida.append(
            {
                "effective_at": effective_at,
                "refresh_batch_id": refresh_batch_id,
                "channel": Channel.SHOPEE.value,
                # IDENTIDADE: (channel, shop_account, marketplace_order_id).
                # Espelha `pk_shopee_orders (shop_account, order_sn)`.
                "shop_account": linha["shop_account"],
                "marketplace_order_id": linha["order_sn"],
                # ATRIBUTO, nao identidade: corrigir a marca de uma conta no
                # registry nao pode criar um pedido novo.
                "brand": conta.brand_key,
                "created_at": criado,
                "paid_at": pago,
                "dispatch_deadline": prazo,
                "deadline_source": (
                    DEADLINE_SOURCE_NATIVE if prazo is not None
                    else DEADLINE_SOURCE_UNAVAILABLE
                ),
                "deadline_status": classify_deadline(prazo, effective_at).value,
                "operational_age_status": classify_age(marco, effective_at).value,
                "is_slow_vs_baseline": lento,
                "is_source_zombie": zumbi,
                # Unico campo derivado. As duas causas continuam persistidas em
                # separado para nao perder a segunda quando ambas sao verdade.
                "is_stalled": lento or zumbi,
                "hours_open": horas_aberto,
                "hours_overdue": hours_overdue(prazo, effective_at),
                # Shopee nao expoe modalidade logistica; a coluna existe para o
                # ML (cross_docking x fulfillment) e fica nula aqui.
                "logistic_type": None,
                "carrier": linha.get("shipping_carrier"),
                "source_ingested_at": ingerido,
                "source_freshness_status": classify_freshness(
                    ingerido, effective_at
                ).value,
                # Shopee: todas as colunas de data sao timestamptz.
                "timestamp_quality": TimestampQuality.VERIFIED.value,
            }
        )
    return saida



# ---------------------------------------------------------------------------
# Mercado Livre (EXP-3B1)
# ---------------------------------------------------------------------------
def normalizar_carimbo_ml(bruto: datetime | None) -> datetime | None:
    """Carimbo naive do Mercado Livre -> aware em UTC. PONTO UNICO.

    A fonte grava `timestamp without time zone`, entao a convencao nao esta
    declarada em lugar nenhum do schema. `ML_SOURCE_UTC_OFFSET` carrega a
    evidencia medida (-04:00) e o raciocinio; aqui so' se aplica.

    Existe uma funcao so' para isto de proposito. Espalhar `timedelta(hours=-4)`
    pelo codigo faria a correcao do offset — quando o time confirmar a convencao
    oficial — virar uma cacada por literais, e um lugar esquecido produziria
    duas idades diferentes para o mesmo pedido.

    Carimbo que ja chega com fuso e devolvido convertido, nao reinterpretado:
    se a fonte um dia passar a gravar `timestamptz`, este helper deixa de somar
    offset sozinho em vez de errar por 4 horas.
    """
    if bruto is None:
        return None
    if bruto.tzinfo is not None:
        return bruto.astimezone(timezone.utc)
    return (bruto - ML_SOURCE_UTC_OFFSET).replace(tzinfo=timezone.utc)


def build_fila_ml(
    linhas: list[dict],
    registry: dict[str, SellerAccount],
    effective_at: datetime,
    refresh_batch_id: str = "",
) -> list[dict]:
    """Shipments do ML -> linhas de `marts.expedicao_fila_atual`.

    Tres diferencas em relacao ao Shopee, todas medidas no EXP-3A/3B1:

    1. A IDENTIDADE e o shipment. `marketplace_order_id` recebe `shipment_id`,
       nao `order_id`: 77 pedidos historicos tem mais de um shipment e usar o
       pedido colidiria neles. `(brand, shipment_id)` e UNIQUE na fonte.

    2. NAO HA PRAZO. `dispatch_deadline` sai nulo e `deadline_status` sai
       `unavailable`. Nada aqui deriva prazo de `date_ready_to_ship`, de
       `date_created` nem de SLA fixo — o marketplace nao promete despacho no
       dado que temos, e fabricar a promessa criaria "vencido" imaginario.

    3. O RELOGIO parte de `date_ready_to_ship` (100% preenchido na coorte), que
       e quando a responsabilidade do vendedor comeca. `date_created` inclui o
       tempo de pagamento e processamento do proprio Mercado Livre.

    Sem `baselines`: `is_slow_vs_baseline` exige p50 de duracao ate o despacho
    por conta, e a coorte confiavel do ML (7 dias) nao sustenta a amostra minima
    de 100 do contrato. A flag sai False e `is_stalled` fica valendo so' pelo
    zumbi — declarado, nao silencioso.
    """
    _require_aware(effective_at, "effective_at")
    saida: list[dict] = []
    for linha in linhas:
        seller_id = str(linha["seller_id"])
        conta = registry.get(seller_id)
        if conta is None:
            raise RegistryError(
                f"conta Mercado Livre {seller_id} ausente do registry; "
                "o refresh nao infere marca por texto."
            )

        criado = normalizar_carimbo_ml(linha.get("order_created_at"))
        pronto = normalizar_carimbo_ml(linha.get("date_ready_to_ship"))
        extraido = normalizar_carimbo_ml(linha.get("extracted_at"))

        # O marco do ML e' o `date_ready_to_ship`; a criacao do pedido so'
        # sustenta a idade quando o carimbo de prontidao falta.
        marco = pronto if pronto is not None else criado
        horas_aberto = hours_between(marco, effective_at)
        zumbi = is_source_zombie(horas_aberto)

        saida.append(
            {
                "effective_at": effective_at,
                "refresh_batch_id": refresh_batch_id,
                "channel": Channel.MERCADOLIVRE.value,
                # IDENTIDADE: (channel, shop_account, marketplace_order_id).
                # `shipment_id` NAO e unico sozinho — repete entre marcas em 117
                # casos medidos —, entao a conta faz parte da chave.
                "shop_account": str(linha["seller_id"]),
                "marketplace_order_id": str(linha["shipment_id"]),
                "brand": conta.brand_key,
                "created_at": criado,
                # A fonte nao expoe carimbo de pagamento do pedido; `date_closed`
                # e' fechamento, nao pagamento. Melhor nulo que um proxy errado.
                "paid_at": None,
                "dispatch_deadline": None,
                "deadline_source": DEADLINE_SOURCE_UNAVAILABLE,
                "deadline_status": DeadlineStatus.UNAVAILABLE.value,
                "operational_age_status": classify_age(marco, effective_at).value,
                "is_slow_vs_baseline": False,
                "is_source_zombie": zumbi,
                "is_stalled": zumbi,
                "hours_open": horas_aberto,
                # Sem prazo nao ha atraso contra prazo. Zero aqui seria mentira
                # aritmetica: diria "no prazo, sem atraso" onde nao ha prazo.
                "hours_overdue": None,
                "logistic_type": linha.get("logistic_type"),
                # MODALIDADE de rastreio, nao numero de rastreio: o numero e
                # identificador publicamente rastreavel e a API nao tem auth.
                "carrier": linha.get("tracking_method"),
                "source_ingested_at": extraido,
                "source_freshness_status": classify_freshness(
                    extraido, effective_at
                ).value,
                # Convencao de fuso INFERIDA por medicao, nao declarada pela
                # fonte nem confirmada pela documentacao oficial.
                "timestamp_quality": TimestampQuality.ASSUMED.value,
            }
        )
    return saida


# ---------------------------------------------------------------------------
# Historico por pedido e registro de execucao
# ---------------------------------------------------------------------------
def snapshot_hour(effective_at: datetime) -> datetime:
    """`date_trunc('hour', effective_at)` em UTC.

    Deterministico de proposito: o EXP-0R2 usava o instante livre de execucao
    como chave e prometia 24 snapshots por dia — o que nunca fecharia 24 e
    geraria varias linhas na mesma hora.
    """
    _require_aware(effective_at, "effective_at")
    return effective_at.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )


def _percentil(valores: list[float], q: float) -> float | None:
    """Interpolacao linear, mesma semantica de `percentile_cont` do Postgres.

    Calculado sobre as duracoes individuais. Nunca media de medias: agregar
    medias por marca e depois tirar media delas daria peso igual a marcas de
    volume desigual.
    """
    if not valores:
        return None
    ordenados = sorted(valores)
    if len(ordenados) == 1:
        return ordenados[0]
    pos = q * (len(ordenados) - 1)
    baixo = int(pos)
    alto = min(baixo + 1, len(ordenados) - 1)
    peso = pos - baixo
    return ordenados[baixo] * (1 - peso) + ordenados[alto] * peso



def build_account_summaries(
    fila: list[dict],
    effective_at: datetime,
    *,
    channel: str,
    refresh_batch_id: str,
    accounts: dict[str, tuple[str, str]],
    watermarks: dict[str, datetime | None],
    source_advanced: bool,
    run_status: str = RunStatus.SUCCESS.value,
) -> list[dict]:
    """Resumo HORARIO POR CONTA. Grao: (channel, shop_account, snapshot_hour).

    `accounts` mapeia `external_seller_id -> (shop_account, brand_key)` e vem do
    REGISTRY cruzado com o watermark. Uma linha por conta ESPERADA, sempre —
    inclusive com `backlog_count = 0`. E assim que a fotografia vazia deixa
    prova: a tabela de pedidos nao consegue representar "zero pedidos", mas esta
    consegue.

    POR QUE `stalled_count` NAO E `slow_count + zombie_count`
    ---------------------------------------------------------
    As duas anomalias podem ser verdadeiras no MESMO pedido. Somar contaria a
    sobreposicao duas vezes. `stalled_count` conta pedidos com `is_stalled`,
    que e o OR — nunca a soma.

    O resumo do CANAL nao e materializado: some as contas na API. Persistir um
    total como se fosse uma quinta conta criaria uma linha que nao corresponde a
    nenhuma loja e que qualquer `GROUP BY shop_account` contaria em dobro.
    """
    _require_aware(effective_at, "effective_at")
    hora = snapshot_hour(effective_at)

    por_conta: dict[str, list[dict]] = {}
    for linha in fila:
        por_conta.setdefault(linha["shop_account"], []).append(linha)

    def _conta(linhas: list[dict], campo: str, valor: str) -> int:
        return sum(1 for r in linhas if r[campo] == valor)

    saida: list[dict] = []
    for external_id, (shop_account, brand_key) in sorted(accounts.items()):
        linhas = por_conta.get(shop_account, [])
        saida.append(
            {
                "refresh_batch_id": refresh_batch_id,
                "channel": channel,
                "shop_account": shop_account,
                "brand": brand_key,
                "snapshot_hour": hora,
                "observed_at": effective_at,
                "source_watermark_at": watermarks.get(external_id),
                "source_advanced": source_advanced,
                "backlog_count": len(linhas),
                "overdue_count": _conta(
                    linhas, "deadline_status", DeadlineStatus.OVERDUE.value
                ),
                "due_within_24h_count": _conta(
                    linhas, "deadline_status", DeadlineStatus.DUE_WITHIN_24H.value
                ),
                "on_time_count": _conta(
                    linhas, "deadline_status", DeadlineStatus.ON_TIME.value
                ),
                "deadline_unavailable_count": _conta(
                    linhas, "deadline_status", DeadlineStatus.UNAVAILABLE.value
                ),
                # Dimensoes TRANSVERSAIS: nao entram na soma das quatro acima.
                "over_48h_count": _conta(
                    linhas, "operational_age_status", OperationalAgeStatus.OVER_48H.value
                ),
                "slow_count": sum(1 for r in linhas if r["is_slow_vs_baseline"]),
                "zombie_count": sum(1 for r in linhas if r["is_source_zombie"]),
                "stalled_count": sum(1 for r in linhas if r["is_stalled"]),
                "run_status": run_status,
                "ingested_at": effective_at,
            }
        )
    return saida
