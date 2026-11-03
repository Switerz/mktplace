"""Atraso de 7 dias por conta — a regra oficial da Shopee, publicada com a fila.

O QUE E'
--------
    taxa = atrasados / pedidos com prazo nos ultimos 7 dias

Diferente do "% de vencidos" da fila (`vencidos agora / backlog`), que e' uma
fotografia: este numero olha os 7 dias FECHADOS antes do dia da publicacao e
pergunta, de tudo o que tinha de sair nesses dias, quanto saiu atrasado.

A publicacao horaria da fila (`cli.py --apply`) grava por conta, no resumo
`marts.expedicao_refresh_run`, as duas contagens e a janela usada:

    due_7d_count          base (pedidos Shopee / envios ML com prazo na janela)
    late_7d_count         quantos da base atrasaram
    late_7d_window_start  primeiro dia de vencimento (Brasilia) da janela usada
    late_7d_window_end    ultimo dia de vencimento (Brasilia) da janela usada

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
e o resumo grava as quatro colunas NULL: "nao calculado". Zero so' existe em
`late_7d_count`, e so' com base > 0.

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

from pipelines.expedicao import shopee_ldr
from pipelines.expedicao.contract import (
    ATRASO_7D_COLUMNS,
    ML_BACKLOG_ORDER_STATUS,
    ML_BACKLOG_SHIPMENT_STATUS,
    ML_BUSINESS_UTC_OFFSET,
    ML_COLLECTED_SUBSTATUSES,
    ML_SELLER_MANAGED_LOGISTIC_TYPES,
    ML_SLA_STATUS_ATRASADO,
    PII_FORBIDDEN_TOKENS,
)
from pipelines.expedicao.ml_extract import _seller_id_canonico
from pipelines.expedicao.shopee_extract import assert_no_pii_in_sql
from pipelines.expedicao.transform import instante_iso_com_fuso

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
    """As quatro chaves NULL: conta sem calculo."""
    return {c: None for c in COLUNAS}


def valores(due: int, late: int, inicio: date, fim: date) -> dict:
    """As quatro chaves, validadas. Base vazia nao e' publicada como zero."""
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
    }


# ---------------------------------------------------------------------------
# Shopee
# ---------------------------------------------------------------------------
def apurar_shopee(
    pedidos: list[shopee_ldr.Pedido],
    watermarks: dict[str, datetime | None],
    effective_at: datetime,
) -> dict[str, dict]:
    """`shop_id -> as quatro chaves`, so' para contas com base > 0. Funcao pura.

    `watermarks` e' a ultima leitura da fonte POR `shop_id` (a mesma que a
    fila ja' le). A janela de cada conta termina no ultimo dia maduro dela.
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

    fins: dict[str, date | None] = {}
    base: dict[str, int] = {}
    atraso: dict[str, int] = {}
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
        if classe in _BASE_SHOPEE:
            base[p.shop_id] = base.get(p.shop_id, 0) + 1
            if classe in _ATRASO_SHOPEE:
                atraso[p.shop_id] = atraso.get(p.shop_id, 0) + 1

    return {
        shop_id: valores(n, atraso.get(shop_id, 0), desde, fins[shop_id])
        for shop_id, n in sorted(base.items())
    }


def calcular_shopee(
    conn, effective_at: datetime, watermarks: dict[str, datetime | None]
) -> dict[str, dict]:
    """Le uma semana de vencimentos da fonte e apura por `shop_id`."""
    desde, ate = janela(effective_at)
    pedidos = shopee_ldr.ler_pedidos_por_vencimento(conn, desde, ate)
    return apurar_shopee(pedidos, watermarks, effective_at)


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
ML_ATRASO_7D_SQL = f"""
WITH envio AS (
  SELECT o.seller_id                         AS seller_id,
         s.status                            AS shipment_status,
         s.substatus                         AS substatus,
         s.date_cancelled                    AS date_cancelled,
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
  SELECT seller_id, shipment_status, substatus, sla_status, dia
    FROM com_dia
   WHERE dia >= %(desde)s
     AND dia <= %(ate)s
     AND (date_cancelled IS NULL
          OR ((date_cancelled - %(offset_negocio)s) AT TIME ZONE 'UTC') > prazo)
)
SELECT seller_id,
       COUNT(*) AS due,
       COUNT(*) FILTER (
         WHERE sla_status = %(sla_atrasado)s
            OR (shipment_status = %(pronto)s
                AND NOT (COALESCE(substatus, '') = ANY(%(coletados)s))
                AND dia < %(hoje)s)
       ) AS late
  FROM base
 GROUP BY seller_id
"""


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
    """`seller_id -> as quatro chaves`, so' para contas com base > 0."""
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
        "hoje": hoje,
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
        if due:
            saida[seller] = valores(due, _campo(r, "late", 2), inicio, ate)
    return saida


# Barreira aplicada no import, como nos extratores.
for _sql in (ML_SLA_INICIO_SQL, ML_ATRASO_7D_SQL, shopee_ldr.PEDIDOS_POR_VENCIMENTO_SQL):
    assert_no_pii_in_sql(_sql, PII_FORBIDDEN_TOKENS)
