"""Atraso de 7 dias por conta — a regra oficial da Shopee, publicada com a fila.

O QUE E'
--------
    taxa = atrasados / pedidos com prazo nos ultimos 7 dias

Diferente do "% de vencidos" da fila (`vencidos agora / backlog`), que e' uma
fotografia: este numero olha os 7 dias FECHADOS antes do dia da publicacao e
pergunta, de tudo o que tinha de sair nesses dias, quanto saiu atrasado.

A publicacao horaria da fila (`cli.py --apply`) grava por conta, no resumo
`marts.expedicao_refresh_run`, as contagens e a janela usada:

    due_7d_count          base (pedidos Shopee / envios ML com prazo na janela)
    late_7d_count         quantos da base atrasaram
    late_7d_window_start  primeiro dia de vencimento (Brasilia) da janela usada
    late_7d_window_end    ultimo dia de vencimento (Brasilia) da janela usada
    over_48h_7d_count     quantos da base passaram de 48 h
    stalled_7d_count      quantos da base passaram de 2 x p50 da conta (travados)

JANELA
------
`[hoje - 7, hoje - 1]` em Brasilia, com `hoje` = dia BRT de `effective_at` —
nunca o relogio do processo. A janela publicada e' a EFETIVAMENTE usada:

* Shopee: so' entram vencimentos MADUROS (`shopee_ldr.madura`: o dia fechou e
  a fonte da conta foi lida depois de D+1 06:00). Antes das 06:00 o dia de
  ontem ainda nao amadureceu, e a janela da conta termina em `hoje - 2`. Conta
  com a fonte parada encolhe a janela do mesmo jeito, em vez de ver pendente
  de ingestao como atraso.
* Mercado Livre: o SLA so' existe na fonte desde 03/11/2026; a janela comeca
  no maior entre `hoje - 7` e o dia BRT da PRIMEIRA LEITURA do SLA
  (`sla_extracted_at`). Nao o prazo mais antigo: na primeira leitura vieram
  envios parados com prazo de 13/10 a 31/10 (33, dos quais 25 `delayed`) — so'
  os que sobraram abertos. Contados, levariam a taxa do ML a 75% (medido em
  03/11/2026). Prazo anterior a primeira leitura nao foi observado ao vivo.

AUSENCIA E' NULL, NUNCA ZERO
----------------------------
Conta sem nenhum pedido na base (ou sem dia maduro) nao aparece no resultado,
e o resumo grava as seis colunas NULL: "nao calculado". Zero so' existe nas
contagens, e so' com base > 0.

ACIMA DE 48 H E TRAVADOS (aba "Ultimos 7 dias", 03/11/2026)
----------------------------------------------------------
Sobre a MESMA base de `due_7d_count` (mesma janela, mesmos dias maduros, mesmas
exclusoes), com a duracao de cada pedido:

    duracao = (coleta, ou `effective_at` se ainda nao coletado) - marco

O marco e' o da fila: Shopee `coalesce(pay_time, create_time)`
(`transform.marco_inicial`; na base todo pedido e' pago, entao e' o
`pay_time`); ML `date_ready_to_ship` (naive -04:00), `date_created` do PEDIDO
se faltar — o mesmo de `transform.build_fila_ml`.

* `over_48h_7d_count`: duracao ESTRITAMENTE maior que
  `contract.OPERATIONAL_AGE_LIMIT` (48 h), como `transform.classify_age`.
* `stalled_7d_count`: duracao maior que `SLOW_BASELINE_FACTOR` x p50 da conta
  (`transform.is_slow_vs_baseline`), so' com baseline suficiente
  (`MIN_BASELINE_SAMPLE`, p50 > 0). Sem baseline: NULL. ML: sempre NULL — o ML
  nao tem baseline (`is_slow_vs_baseline` ja' sai False por contrato).

Coleta no ML: a primeira entrada de `substatus_history` (jsonb, datas ISO com
fuso) com substatus `picked_up`/`dropped_off`/`in_hub` ou status
`shipped`/`delivered`; senao `date_shipped` (naive -04:00); senao nao coletado.
Historico ilegivel (nao e' array, ou entrada de coleta com data sem fuso)
deixa o `over_48h_7d_count` da conta NULL, em vez de contar o envio como
aberto ate' agora.

SHOPEE: A MESMA BASE DA LDR OPERACIONAL
---------------------------------------
Nenhuma regra nova: `shopee_ldr.classificar` decide cada pedido. Base =
no prazo + atrasado + pendente vencido + pendente no prazo (este ultimo nao
ocorre em vencimento maduro). Fora: FBS, cancelado sem coleta, sem prazo (nao
tem dia de vencimento) e pos-envio sem coleta. Atrasado = coletado depois do
prazo + pendente vencido.

MERCADO LIVRE: SQL AGREGADO, VEREDITO DO ML
-------------------------------------------
Base: envio seller-managed (`ML_SELLER_MANAGED_LOGISTIC_TYPES`), pedido `paid`,
`sla_expected_date` com dia BRT na janela e nao cancelado ANTES do prazo.
Atrasado: `sla_status = 'delayed'` OU ainda `ready_to_ship` sem coleta com o
prazo num dia ja' fechado. NAO se compara coleta x prazo: medido em
03/11/2026, a coleta e' escaneada ~28 min depois do horario agendado e o ML
da' `on_time` — a comparacao marcaria 396 de 576 envios como atrasados a' toa.
O SQL devolve contagens por conta, nunca linhas de envio.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import logging

from pipelines.expedicao import shopee_ldr
from pipelines.expedicao.contract import (
    ATRASO_7D_COLUMNS,
    MIN_BASELINE_SAMPLE,
    ML_BACKLOG_ORDER_STATUS,
    ML_BACKLOG_SHIPMENT_STATUS,
    ML_BUSINESS_UTC_OFFSET,
    ML_COLLECTED_SUBSTATUSES,
    ML_SELLER_MANAGED_LOGISTIC_TYPES,
    ML_SLA_STATUS_ATRASADO,
    OPERATIONAL_AGE_LIMIT,
    PII_FORBIDDEN_TOKENS,
)
from pipelines.expedicao.ml_extract import _seller_id_canonico
from pipelines.expedicao.shopee_extract import assert_no_pii_in_sql
from pipelines.expedicao.transform import (
    hours_between,
    instante_iso_com_fuso,
    is_slow_vs_baseline,
    marco_inicial,
)

_log = logging.getLogger(__name__)

#: Nomes EXATOS das colunas no resumo (migration 035). A API le estes nomes.
COLUNAS = ATRASO_7D_COLUMNS

DIAS = 7

#: Classes da LDR que formam a base e as que contam atraso.
_BASE_SHOPEE = frozenset({
    shopee_ldr.Classe.NO_PRAZO,
    shopee_ldr.Classe.ATRASADO,
    shopee_ldr.Classe.PENDENTE_VENCIDO,
    shopee_ldr.Classe.PENDENTE_NO_PRAZO,
})
_ATRASO_SHOPEE = frozenset({
    shopee_ldr.Classe.ATRASADO,
    shopee_ldr.Classe.PENDENTE_VENCIDO,
})


def janela(effective_at: datetime) -> tuple[date, date]:
    """`[hoje - 7, hoje - 1]`, `hoje` = dia em Brasilia de `effective_at`."""
    hoje = shopee_ldr.hoje_brt(effective_at)
    return hoje - timedelta(days=DIAS), hoje - timedelta(days=1)


def vazio() -> dict:
    """As seis chaves NULL: conta sem calculo."""
    return {c: None for c in COLUNAS}


def _contagem_opcional(valor, due: int, rotulo: str) -> int | None:
    """`None` continua `None` (nao calculado); numero tem de caber na base."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        raise ValueError(f"contagem de {rotulo} de 7 dias nao pode ser booleana")
    valor = int(valor)
    if not 0 <= valor <= due:
        raise ValueError(f"{rotulo} fora de [0, base]: {valor} de {due}")
    return valor


def valores(due: int, late: int, inicio: date, fim: date,
            over_48h: int | None = None, stalled: int | None = None) -> dict:
    """As seis chaves, validadas. Base vazia nao e' publicada como zero.

    `over_48h` e `stalled` sao opcionais: `None` = nao calculado (ML sem
    baseline, conta Shopee sem amostra, historico ilegivel), nunca zero.
    """
    if isinstance(due, bool) or isinstance(late, bool):
        raise ValueError("contagem de atraso de 7 dias nao pode ser booleana")
    due, late = int(due), int(late)
    if due <= 0:
        raise ValueError("base de 7 dias vazia: publique NULL, nao zero")
    if not 0 <= late <= due:
        raise ValueError(f"atrasados fora de [0, base]: {late} de {due}")
    if inicio > fim:
        raise ValueError(f"janela de 7 dias invertida: {inicio} > {fim}")
    return {
        "due_7d_count": due,
        "late_7d_count": late,
        "late_7d_window_start": inicio,
        "late_7d_window_end": fim,
        "over_48h_7d_count": _contagem_opcional(over_48h, due, "acima de 48 h"),
        "stalled_7d_count": _contagem_opcional(stalled, due, "travados"),
    }


def baseline_suficiente(baseline: tuple[int, float | None] | None) -> bool:
    """A amostra e o p50 sustentam `is_slow_vs_baseline`? Senao, travados NULL.

    Mesmo criterio de `transform.is_slow_vs_baseline`, que devolve False
    nesses casos: aqui o False viraria um zero falso, por isso NULL.
    """
    if baseline is None:
        return False
    amostra, p50 = baseline
    return amostra >= MIN_BASELINE_SAMPLE and p50 is not None and p50 > 0


# ---------------------------------------------------------------------------
# Shopee
# ---------------------------------------------------------------------------
def apurar_shopee(
    pedidos: list[shopee_ldr.Pedido],
    watermarks: dict[str, datetime | None],
    effective_at: datetime,
    baselines: dict[str, tuple[int, float | None]] | None = None,
) -> dict[str, dict]:
    """`shop_id -> as seis chaves`, so' para contas com base > 0. Funcao pura.

    `watermarks` e' a ultima leitura da fonte POR `shop_id` (a mesma que a
    fila ja' le). A janela de cada conta termina no ultimo dia maduro dela.

    `baselines` (`shop_id -> (amostra, p50 em horas)`, o mesmo de
    `shopee_extract.fetch_baselines`) decide os travados; conta sem baseline
    suficiente (ou `baselines` ausente) sai com `stalled_7d_count` NULL.
    """
    hoje = shopee_ldr.hoje_brt(effective_at)
    desde, ate = janela(effective_at)

    def fim_maduro(shop_id: str) -> date | None:
        wm = watermarks.get(shop_id)
        dia, fim = desde, None
        while dia <= ate:
            if shopee_ldr.madura(dia, hoje, wm):
                fim = dia
            dia += timedelta(days=1)
        return fim

    baselines = baselines or {}
    fins: dict[str, date | None] = {}
    base: dict[str, int] = {}
    atraso: dict[str, int] = {}
    acima_48h: dict[str, int] = {}
    travados: dict[str, int] = {}
    for p in pedidos:
        if p.ship_by_date is None:
            continue
        dia = shopee_ldr.data_brt(p.ship_by_date)
        if not desde <= dia <= ate:
            continue
        if p.shop_id not in fins:
            fins[p.shop_id] = fim_maduro(p.shop_id)
        fim = fins[p.shop_id]
        if fim is None or dia > fim:
            continue
        classe = shopee_ldr.classificar(p, effective_at)
        if classe not in _BASE_SHOPEE:
            continue
        base[p.shop_id] = base.get(p.shop_id, 0) + 1
        if classe in _ATRASO_SHOPEE:
            atraso[p.shop_id] = atraso.get(p.shop_id, 0) + 1
        # Marco da fila. `Pedido` nao carrega `create_time`: na base todo
        # pedido e' pago (`NAO_PAGO` sai antes), entao o coalesce e' sempre o
        # `pay_time` — o fallback de criacao nunca seria usado aqui.
        marco = marco_inicial(p.pay_time, None)
        fim_duracao = (p.pickup_done_time if p.pickup_done_time is not None
                       else effective_at)
        if fim_duracao - marco > OPERATIONAL_AGE_LIMIT:
            acima_48h[p.shop_id] = acima_48h.get(p.shop_id, 0) + 1
        amostra, p50 = baselines.get(p.shop_id, (0, None))
        if is_slow_vs_baseline(hours_between(marco, fim_duracao), amostra, p50):
            travados[p.shop_id] = travados.get(p.shop_id, 0) + 1

    return {
        shop_id: valores(
            n, atraso.get(shop_id, 0), desde, fins[shop_id],
            over_48h=acima_48h.get(shop_id, 0),
            stalled=(travados.get(shop_id, 0)
                     if baseline_suficiente(baselines.get(shop_id)) else None),
        )
        for shop_id, n in sorted(base.items())
    }


def calcular_shopee(
    conn,
    effective_at: datetime,
    watermarks: dict[str, datetime | None],
    baselines: dict[str, tuple[int, float | None]] | None = None,
) -> dict[str, dict]:
    """Le uma semana de vencimentos da fonte e apura por `shop_id`.

    `baselines` e' o p50 por conta que a fila ja' leu
    (`shopee_extract.fetch_baselines`); sem ele, travados saem NULL.
    """
    desde, ate = janela(effective_at)
    pedidos = shopee_ldr.ler_pedidos_por_vencimento(conn, desde, ate)
    return apurar_shopee(pedidos, watermarks, effective_at, baselines)


# ---------------------------------------------------------------------------
# Mercado Livre
# ---------------------------------------------------------------------------
#: Texto ISO 8601 COM fuso, o mesmo formato que `transform.instante_iso_com_fuso`
#: aceita. Texto fora dele nao e' convertido no SQL: viraria um instante no
#: fuso da sessao, o erro de 1 a 4 h que a convencao por coluna impede.
_ISO_COM_FUSO_SQL = (
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d{1,6})?[+-]\d{2}(:?\d{2})?$"
)

#: Recorte da fonte por criacao do envio (naive, -04:00), so' para nao varrer
#: o historico inteiro a cada hora. Um envio com prazo na janela foi criado
#: dias antes dele; 45 dias cobrem com folga.
MARGEM_CRIACAO = timedelta(days=45)

_SLA_TEXTO = "to_jsonb(s) ->> 'sla_expected_date'"

#: Primeira LEITURA de SLA na fonte (para "desde 03/11"; ver o docstring) e a
#: contagem de SLA sem fuso. `sla_*` sao lidos por `to_jsonb(s) ->> ...`, como em `ML_BACKLOG_SQL`:
#: o mesmo SQL roda com ou sem as colunas na fonte.
ML_SLA_INICIO_SQL = f"""
WITH sla AS (
  SELECT {_SLA_TEXTO} AS sla_texto,
         to_jsonb(s) ->> 'sla_extracted_at' AS lido_texto
    FROM raw.ml_shipments s
   WHERE s.date_created >= %(criado_desde)s
)
SELECT MIN(CASE WHEN lido_texto ~ '{_ISO_COM_FUSO_SQL}'
                THEN lido_texto::timestamptz END)           AS primeira_leitura,
       COUNT(*) FILTER (WHERE sla_texto !~ '{_ISO_COM_FUSO_SQL}') AS sem_fuso,
       MIN(sla_texto) FILTER (WHERE sla_texto !~ '{_ISO_COM_FUSO_SQL}') AS amostra_sem_fuso
  FROM sla
 WHERE sla_texto IS NOT NULL
"""

#: Contagens por conta (`seller_id`). Fusos POR COLUNA, como em `transform`:
#: `sla_expected_date` e' timestamptz (texto ISO com fuso), `date_cancelled` e'
#: negocio naive em -04:00 (desloca por `ML_BUSINESS_UTC_OFFSET` e le em UTC).
#: O `CASE` garante que o texto so' e' convertido depois de passar no formato.
#: Dia em Brasilia por offset FIXO (-03:00), como `transform._BRT` e
#: `shopee_ldr.BRT`: sem horario de verao desde 2019, e sem depender da base
#: de fusos do servidor.
#:
#: ACIMA DE 48 H (aba "Ultimos 7 dias"), na MESMA base e no MESMO SQL — uma
#: leitura so', entao `over_48h <= due` por construcao:
#:   * marco = `date_ready_to_ship` (naive -04:00), senao `date_created` do
#:     PEDIDO (o `order_created_at` da fila), como `transform.build_fila_ml`;
#:   * coleta = MENOR data de `substatus_history` com substatus coletado ou
#:     status enviado/entregue; senao `date_shipped` (naive -04:00);
#:   * duracao = COALESCE(coleta, agora) - marco; acima de 48 h estritamente.
#: `substatus_history` e' lido por `to_jsonb(s) -> ...`, como o SLA: o SQL roda
#: com ou sem a coluna. Nenhum `CAST` roda sobre texto que nao passou no
#: formato ISO com fuso, e `jsonb_array_elements` so' recebe array: o historico
#: nao derruba a consulta (e com ela o atraso). Historico ilegivel e' CONTADO
#: (`historico_invalido`) e o Python publica NULL nas 48 h daquela conta.
_HIST_COLETA_SQL = """(
           e ->> 'substatus' = ANY(%(coletados)s)
        OR e ->> 'status' = ANY(%(enviados)s))"""
_HIST_ELEMENTOS_SQL = (
    "jsonb_array_elements(CASE WHEN jsonb_typeof(historico) = 'array'"
    " THEN historico ELSE '[]'::jsonb END) e"
)
ML_ATRASO_7D_SQL = f"""
WITH envio AS (
  SELECT o.seller_id                         AS seller_id,
         s.status                            AS shipment_status,
         s.substatus                         AS substatus,
         s.date_cancelled                    AS date_cancelled,
         s.date_ready_to_ship                AS date_ready_to_ship,
         s.date_shipped                      AS date_shipped,
         o.date_created                      AS pedido_criado,
         to_jsonb(s) -> 'substatus_history'  AS historico,
         to_jsonb(s) ->> 'sla_status'        AS sla_status,
         CASE WHEN {_SLA_TEXTO} ~ '{_ISO_COM_FUSO_SQL}'
              THEN ({_SLA_TEXTO})::timestamptz END AS prazo
    FROM raw.ml_shipments s
    JOIN raw.ml_orders o
      ON o.brand = s.brand AND o.order_id = s.order_id
   WHERE s.date_created >= %(criado_desde)s
     AND s.logistic_type = ANY(%(modalidades)s)
     AND o.status = %(order_status)s
),
com_dia AS (
  SELECT envio.*, (prazo AT TIME ZONE INTERVAL '-03:00')::date AS dia
    FROM envio
   WHERE prazo IS NOT NULL
),
base AS (
  SELECT seller_id, shipment_status, substatus, sla_status, dia,
         date_ready_to_ship, date_shipped, pedido_criado, historico
    FROM com_dia
   WHERE dia >= %(desde)s
     AND dia <= %(ate)s
     AND (date_cancelled IS NULL
          OR ((date_cancelled - %(offset_negocio)s) AT TIME ZONE 'UTC') > prazo)
),
duracao AS (
  SELECT base.*,
         COALESCE(
           (date_ready_to_ship - %(offset_negocio)s) AT TIME ZONE 'UTC',
           (pedido_criado - %(offset_negocio)s) AT TIME ZONE 'UTC'
         ) AS marco,
         COALESCE(
           (SELECT MIN(CASE WHEN e ->> 'date' ~ '{_ISO_COM_FUSO_SQL}'
                            THEN (e ->> 'date')::timestamptz END)
              FROM {_HIST_ELEMENTOS_SQL}
             WHERE {_HIST_COLETA_SQL}),
           (date_shipped - %(offset_negocio)s) AT TIME ZONE 'UTC'
         ) AS coleta,
         CASE
           WHEN historico IS NULL OR jsonb_typeof(historico) = 'null' THEN FALSE
           WHEN jsonb_typeof(historico) <> 'array' THEN TRUE
           ELSE EXISTS (
             SELECT 1 FROM {_HIST_ELEMENTOS_SQL}
              WHERE {_HIST_COLETA_SQL}
                AND NOT COALESCE(e ->> 'date' ~ '{_ISO_COM_FUSO_SQL}', FALSE))
         END AS historico_ilegivel
    FROM base
)
SELECT seller_id,
       COUNT(*) AS due,
       COUNT(*) FILTER (
         WHERE sla_status = %(sla_atrasado)s
            OR (shipment_status = %(pronto)s
                AND NOT (COALESCE(substatus, '') = ANY(%(coletados)s))
                AND dia < %(hoje)s)
       ) AS late,
       COUNT(*) FILTER (
         WHERE marco IS NOT NULL
           AND COALESCE(coleta, %(agora)s) - marco > %(limite_idade)s
       ) AS over_48h,
       COUNT(*) FILTER (WHERE historico_ilegivel) AS historico_invalido
  FROM duracao
 GROUP BY seller_id
"""

#: Status do envio que, no historico, marcam a saida do vendedor (alem dos
#: substatus de `ML_COLLECTED_SUBSTATUSES`).
ML_STATUS_ENVIADO = frozenset({"shipped", "delivered"})


def _campo(linha, nome: str, posicao: int):
    return linha[nome] if isinstance(linha, dict) else linha[posicao]


def _criado_desde(effective_at: datetime) -> datetime:
    desde, _ = janela(effective_at)
    return datetime.combine(desde - MARGEM_CRIACAO, time(0, 0))


def inicio_ml(desde: date, primeira_leitura: datetime | None) -> date | None:
    """Maior entre `desde` e o dia BRT da primeira leitura de SLA. `None` sem SLA."""
    if primeira_leitura is None:
        return None
    return max(desde, shopee_ldr.data_brt(primeira_leitura))


def calcular_ml(conn, effective_at: datetime) -> dict[str, dict]:
    """`seller_id -> as seis chaves`, so' para contas com base > 0.

    `stalled_7d_count` sai SEMPRE NULL: o ML nao tem baseline de duracao
    (ver `transform.build_fila_ml`). `over_48h_7d_count` sai NULL na conta
    cujo historico de substatus nao pode ser lido.
    """
    hoje = shopee_ldr.hoje_brt(effective_at)
    desde, ate = janela(effective_at)
    criado_desde = _criado_desde(effective_at)

    with conn.cursor() as cur:
        cur.execute(ML_SLA_INICIO_SQL, {"criado_desde": criado_desde})
        linha = cur.fetchone()
    if linha is None:
        return {}
    if _campo(linha, "sem_fuso", 1):
        # Mesmo parser da fila: levanta com a mensagem de contrato.
        instante_iso_com_fuso(_campo(linha, "amostra_sem_fuso", 2), "sla_expected_date")
    primeiro = _campo(linha, "primeira_leitura", 0)
    if primeiro is not None and primeiro.tzinfo is None:
        raise ValueError("primeira leitura de SLA sem fuso: a coluna e' timestamptz")
    inicio = inicio_ml(desde, primeiro)
    if inicio is None or inicio > ate:
        return {}

    params = {
        "criado_desde": criado_desde,
        "modalidades": sorted(ML_SELLER_MANAGED_LOGISTIC_TYPES),
        "order_status": ML_BACKLOG_ORDER_STATUS,
        "desde": inicio,
        "ate": ate,
        "offset_negocio": ML_BUSINESS_UTC_OFFSET,
        "sla_atrasado": ML_SLA_STATUS_ATRASADO,
        "pronto": ML_BACKLOG_SHIPMENT_STATUS,
        "coletados": sorted(ML_COLLECTED_SUBSTATUSES),
        "enviados": sorted(ML_STATUS_ENVIADO),
        "hoje": hoje,
        "agora": effective_at,
        "limite_idade": OPERATIONAL_AGE_LIMIT,
    }
    with conn.cursor() as cur:
        cur.execute(ML_ATRASO_7D_SQL, params)
        linhas = cur.fetchall() or []

    saida: dict[str, dict] = {}
    for r in linhas:
        seller = _seller_id_canonico(_campo(r, "seller_id", 0))
        if seller in saida:
            raise ValueError(f"seller_id {seller} repetido no agregado de 7 dias")
        due = _campo(r, "due", 1)
        if not due:
            continue
        acima_48h = _campo(r, "over_48h", 3)
        invalidos = _campo(r, "historico_invalido", 4)
        if invalidos:
            _log.warning(
                "atraso de 7 dias do ML, conta %s: %d envio(s) com "
                "substatus_history ilegivel; acima de 48 h fica NULL",
                seller, invalidos,
            )
            acima_48h = None
        # Travados: sempre NULL no ML (sem baseline), nunca zero.
        saida[seller] = valores(due, _campo(r, "late", 2), inicio, ate,
                                over_48h=acima_48h, stalled=None)
    return saida


# Barreira aplicada no import, como nos extratores.
for _sql in (ML_SLA_INICIO_SQL, ML_ATRASO_7D_SQL, shopee_ldr.PEDIDOS_POR_VENCIMENTO_SQL):
    assert_no_pii_in_sql(_sql, PII_FORBIDDEN_TOKENS)
