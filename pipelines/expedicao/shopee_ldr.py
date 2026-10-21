"""LDR operacional da Shopee (coleta x prazo Shopee) — EXPEDICAO-SHOPEE-LDR-1.

O QUE ESTE MODULO MEDE, E O QUE NAO MEDE
-----------------------------------------
Entre os pedidos PAGOS e despachados pelo PROPRIO VENDEDOR cujo prazo de envio
publicado pela Shopee (`ship_by_date`) vence num dia, quantos nao tinham sido
COLETADOS (`pickup_done_time`) ate' o prazo.

E' um indicador INTERNO da Torre. NAO e' a taxa de envio atrasado que a Shopee
usa para penalizar a loja: o marco, as exclusoes e a janela da plataforma nao
estao na nossa fonte. Todo texto derivado daqui carrega essa ressalva
(`AVISO_NAO_OFICIAL`).

REGRAS (ratificadas em 21/10/2026, medicao read-only no mesmo dia)
--------------------------------------------------------------------
Precedencia, de cima para baixo — o primeiro que casa decide:

  1. sem `pay_time`                         -> fora do universo (nao pago)
  2. `fulfilled_by_shopee`                  -> FBS: fora da taxa, contador proprio
  3. fluxo fora do vocabulario medido       -> FALHA FECHADA (nada publicado)
  4. cancelado SEM coleta                   -> fora da base, contador proprio
  5. sem `ship_by_date`                     -> sem prazo, contador proprio
  6. status pos-envio SEM `pickup_done_time`-> fora da taxa, reduz cobertura
  7. coletado ate' o prazo                  -> no prazo
  8. coletado depois do prazo               -> atrasado
  9. nao coletado, prazo vencido            -> pendente vencido (conta atraso)
 10. nao coletado, prazo por vencer         -> pendente no prazo

Cancelado COM coleta entra na base pela coleta: o envio aconteceu, o
cancelamento e' posterior (medido: 0,19% dos pedidos em 30 dias).

Por que o cancelado sem coleta sai INTEIRO, inclusive "Seller did not Ship":
a fonte nao guarda o instante do cancelamento, e a decisao ratificada foi
exclui-lo e MOSTRA-LO. Por isso `cancelado_vendedor_nao_enviou` e' publicado a'
parte: quem le a taxa ve, ao lado, quantos pedidos a Shopee cancelou por falta
de envio. Medido na semana 12-18/10: incluir esses pedidos levaria a taxa de
3,35% para 4,04%.

PRAZO E FUSO
------------
`ship_by_date` e' `timestamptz` e, medido, 100% dos valores caem as 23:59 de
Brasilia; o calendario de dias uteis ja' vem aplicado pela Shopee (nenhum
vencimento em fim de semana nem em 12/10). O dia de vencimento e' a data em
Brasilia. O Brasil nao tem horario de verao desde 2019, entao o deslocamento e'
fixo (-3 h) — o mesmo que `tiktok_daily` usa.

MATURIDADE
----------
Um vencimento so' entra na taxa quando (a) o dia ja' terminou e (b) a fonte DA
CONTA foi lida depois de D+1 06:00 BRT. Medido: `ingested_at - update_time`
tem p99 de 6,0 h, entao uma coleta feita as 23:59 de D aparece na fonte ate'
as 06:00 de D+1. Antes disso o pendente pode ser so' atraso de ingestao.

`ingested_at` e' REESCRITO a cada atualizacao do pedido; ele serve de
watermark (quando a conta foi lida pela ultima vez), nunca de "quando a coleta
chegou".
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum

from pipelines.expedicao.contract import (
    SHOPEE_MOTIVO_NAO_ENVIOU,
    SHOPEE_MOTIVOS_CANCELAMENTO_CONHECIDOS,
    Channel,
)

TABELA = "marts.expedicao_shopee_ldr_daily"
TABELA_SEM_PRAZO = "marts.expedicao_shopee_ldr_sem_prazo_daily"
SOURCE_NAME = "expedicao_shopee_ldr"
CANAL = Channel.SHOPEE

NOME_METRICA = "LDR operacional (coleta × prazo Shopee)"
AVISO_NAO_OFICIAL = (
    "Indicador interno da Torre: não é a taxa de envio atrasado que a Shopee "
    "usa para penalização."
)

#: Janela recalculada a cada publicacao: 35 dias de vencimento para tras e 7
#: para a frente. A janela INTEIRA e' reescrita porque um vencimento antigo
#: MUDA quando o pedido dele e' finalmente coletado ou cancelado.
DIAS_PASSADOS = 35
DIAS_FUTUROS = 7

#: Fuso da operacao. Fixo: sem horario de verao desde 2019.
BRT = timezone(timedelta(hours=-3))

#: Atraso de ingestao medido (p99 de ingested_at - update_time = 6,0 h).
MARGEM_MATURIDADE = timedelta(hours=6)

#: Advisory lock PROPRIO. Nao reutiliza a chave da fila Shopee (915_120_015):
#: a LDR rodando nao pode fazer o refresh da fila sair com "lock ocupado".
LOCK_KEY = 918_120_018

FLUXO_VENDEDOR = "fulfilled_by_local_seller"
FLUXO_FBS = "fulfilled_by_shopee"
FLUXOS_CONHECIDOS = frozenset({FLUXO_VENDEDOR, FLUXO_FBS})

STATUS_CANCELADO = frozenset({"CANCELLED", "IN_CANCEL"})
#: O pedido ja' saiu da operacao do vendedor. Sem `pickup_done_time`, falta o
#: marco — nao houve atraso comprovado.
STATUS_POS_ENVIO = frozenset({"SHIPPED", "TO_CONFIRM_RECEIVE", "COMPLETED", "TO_RETURN"})
#: Vocabulario medido em 365 dias (8 status). Status fora dele nao muda a
#: classificacao (que depende da coleta), mas vira alerta de qualidade.
STATUS_CONHECIDOS = frozenset({
    "UNPAID", "READY_TO_SHIP", "PROCESSED", "RETRY_SHIP", "SHIPPED",
    "TO_CONFIRM_RECEIVE", "IN_CANCEL", "CANCELLED", "TO_RETURN", "COMPLETED",
    "INVOICE_PENDING",
})

#: Motivos de cancelamento medidos em 365 dias (texto exato da fonte; a lista
#: mora em `contract.py`). Motivo NAO-NULO fora dela gera alerta de qualidade:
#: ou a Shopee mudou o texto — e `MOTIVO_NAO_ENVIOU` pode ter deixado de casar
#: sem erro —, ou apareceu um caso novo que ninguem classificou. Motivo NULO
#: (410 em 365 dias, todos `system`) e' contado como ausente, sem alerta.
MOTIVO_NAO_ENVIOU = SHOPEE_MOTIVO_NAO_ENVIOU
MOTIVOS_CONHECIDOS = SHOPEE_MOTIVOS_CANCELAMENTO_CONHECIDOS

#: Cobertura: ausencias (sem prazo + enviado sem coleta) sobre a base elegivel.
LIMIAR_AVISO_COBERTURA = 0.02
LIMIAR_OCULTA_TAXA = 0.10


class FluxoDesconhecido(RuntimeError):
    """`fulfillment_flag` fora do vocabulario medido. Nada e' publicado.

    Fechar aqui e' deliberado: um fluxo novo pode ser FBS com outro nome, e
    classifica-lo como vendedor poria pedidos da Shopee na taxa do vendedor.
    """


class Classe(str, Enum):
    NAO_PAGO = "nao_pago"
    FBS = "fbs"
    CANCELADO_SEM_COLETA = "cancelado_sem_coleta"
    SEM_PRAZO = "sem_prazo"
    ENVIADO_SEM_COLETA = "enviado_sem_coleta"
    NO_PRAZO = "no_prazo"
    ATRASADO = "atrasado"
    PENDENTE_VENCIDO = "pendente_vencido"
    PENDENTE_NO_PRAZO = "pendente_no_prazo"


@dataclass(frozen=True)
class Pedido:
    """Uma linha da fonte, so' com o que a classificacao precisa.

    Sem `order_sn` e sem nenhuma coluna de comprador: a publicacao e' de
    contagens, e o identificador do pedido nao tem uso aqui.
    """

    shop_id: str
    shop_account: str
    order_status: str
    fulfillment_flag: str | None
    pay_time: datetime | None
    ship_by_date: datetime | None
    pickup_done_time: datetime | None
    cancel_reason: str | None = None


def _aware(v: datetime | None, rotulo: str) -> datetime | None:
    if v is not None and v.tzinfo is None:
        raise ValueError(f"{rotulo} sem fuso: a fonte e' timestamptz")
    return v


def classificar(p: Pedido, agora: datetime) -> Classe:
    """Classe do pedido pela precedencia do cabecalho. Funcao pura."""
    _aware(agora, "agora")
    pago = _aware(p.pay_time, "pay_time")
    prazo = _aware(p.ship_by_date, "ship_by_date")
    coleta = _aware(p.pickup_done_time, "pickup_done_time")

    if pago is None:
        return Classe.NAO_PAGO
    if p.fulfillment_flag == FLUXO_FBS:
        return Classe.FBS
    if p.fulfillment_flag != FLUXO_VENDEDOR:
        raise FluxoDesconhecido(
            f"fulfillment_flag fora do vocabulario medido: {p.fulfillment_flag!r}")
    if p.order_status in STATUS_CANCELADO and coleta is None:
        return Classe.CANCELADO_SEM_COLETA
    if prazo is None:
        return Classe.SEM_PRAZO
    if coleta is None and p.order_status in STATUS_POS_ENVIO:
        return Classe.ENVIADO_SEM_COLETA
    if coleta is not None:
        return Classe.NO_PRAZO if coleta <= prazo else Classe.ATRASADO
    return Classe.PENDENTE_VENCIDO if prazo < agora else Classe.PENDENTE_NO_PRAZO


def data_brt(instante: datetime) -> date:
    _aware(instante, "instante")
    return instante.astimezone(BRT).date()


def hoje_brt(agora: datetime) -> date:
    return data_brt(agora)


def janela_de_vencimento(hoje: date) -> tuple[date, date]:
    return hoje - timedelta(days=DIAS_PASSADOS), hoje + timedelta(days=DIAS_FUTUROS)


def janela_de_pagamento(hoje: date) -> tuple[date, date]:
    """Pedidos SEM prazo nao tem vencimento: sao agrupados pelo pagamento."""
    return hoje - timedelta(days=DIAS_PASSADOS), hoje


def inicio_do_dia_brt(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=BRT)


def madura(vencimento: date, hoje: date, watermark_conta: datetime | None) -> bool:
    """O vencimento ja' fechou E a fonte da conta foi lida depois da margem.

    Sem watermark a resposta e' `False`: ausencia de leitura nao prova que a
    coleta nao aconteceu.
    """
    if vencimento >= hoje or watermark_conta is None:
        return False
    corte = inicio_do_dia_brt(vencimento + timedelta(days=1)) + MARGEM_MATURIDADE
    return _aware(watermark_conta, "watermark") >= corte


# ---------------------------------------------------------------------------
# Linhas publicadas
# ---------------------------------------------------------------------------
@dataclass
class LinhaVencimento:
    """Um (dia de vencimento em BRT, conta). Fecha por construcao."""

    deadline_date: date
    shop_account: str
    shop_id: str
    brand: str
    pedidos_pagos: int = 0
    excl_fbs: int = 0
    excl_cancelado_sem_coleta: int = 0
    cancelado_vendedor_nao_enviou: int = 0
    cancelado_motivo_desconhecido: int = 0
    excl_enviado_sem_coleta: int = 0
    no_prazo: int = 0
    atrasado: int = 0
    pendente_vencido: int = 0
    pendente_no_prazo: int = 0
    is_mature: bool = False

    @property
    def base(self) -> int:
        return self.no_prazo + self.atrasado + self.pendente_vencido + self.pendente_no_prazo

    def validar(self) -> None:
        """Os mesmos fechamentos que os CHECKs da migration impoem."""
        valores = (
            self.pedidos_pagos, self.excl_fbs, self.excl_cancelado_sem_coleta,
            self.cancelado_vendedor_nao_enviou, self.cancelado_motivo_desconhecido,
            self.excl_enviado_sem_coleta, self.no_prazo, self.atrasado,
            self.pendente_vencido, self.pendente_no_prazo,
        )
        if any(v < 0 for v in valores):
            raise ValueError(f"contagem negativa em {self.shop_account}/{self.deadline_date}")
        esperado = (self.pedidos_pagos - self.excl_fbs - self.excl_cancelado_sem_coleta
                    - self.excl_enviado_sem_coleta)
        if self.base != esperado:
            raise ValueError(
                f"base nao fecha em {self.shop_account}/{self.deadline_date}: "
                f"{self.base} != {esperado}")
        if (self.cancelado_vendedor_nao_enviou + self.cancelado_motivo_desconhecido
                > self.excl_cancelado_sem_coleta):
            raise ValueError(
                f"subconjuntos de cancelamento excedem o total em "
                f"{self.shop_account}/{self.deadline_date}")
        if self.is_mature and self.pendente_no_prazo:
            raise ValueError(
                f"vencimento maduro com pendente no prazo em "
                f"{self.shop_account}/{self.deadline_date}")


@dataclass
class LinhaSemPrazo:
    """Um (dia de pagamento em BRT, conta) para pedidos SEM `ship_by_date`."""

    paid_date: date
    shop_account: str
    shop_id: str
    brand: str
    pedidos_pagos: int = 0
    excl_fbs: int = 0
    excl_cancelado_sem_coleta: int = 0
    sem_prazo: int = 0

    def validar(self) -> None:
        if min(self.pedidos_pagos, self.excl_fbs, self.excl_cancelado_sem_coleta,
               self.sem_prazo) < 0:
            raise ValueError(f"contagem negativa em {self.shop_account}/{self.paid_date}")
        if self.pedidos_pagos != self.excl_fbs + self.excl_cancelado_sem_coleta + self.sem_prazo:
            raise ValueError(
                f"sem-prazo nao fecha em {self.shop_account}/{self.paid_date}")


@dataclass
class Qualidade:
    """O que a apuracao viu e nao cabe numa contagem publicada."""

    motivos_desconhecidos: Counter = field(default_factory=Counter)
    motivos_ausentes: int = 0
    status_desconhecidos: Counter = field(default_factory=Counter)
    contas_fora_do_registry: Counter = field(default_factory=Counter)
    fora_da_janela: int = 0


@dataclass
class Apuracao:
    vencimentos: list[LinhaVencimento]
    sem_prazo: list[LinhaSemPrazo]
    qualidade: Qualidade
    hoje: date
    agora: datetime


def apurar(
    pedidos: list[Pedido],
    *,
    registry: dict[str, str],
    watermarks: dict[str, datetime | None],
    agora: datetime,
) -> Apuracao:
    """Classifica e agrega. Nao toca em banco.

    `registry` mapeia `shop_id` -> marca (autoridade: Neon). Conta fora dele
    NAO e' publicada e vira alerta de qualidade — nunca linha com marca
    inventada.
    """
    _aware(agora, "agora")
    hoje = hoje_brt(agora)
    v_desde, v_ate = janela_de_vencimento(hoje)
    p_desde, p_ate = janela_de_pagamento(hoje)
    q = Qualidade()
    venc: dict[tuple[date, str], LinhaVencimento] = {}
    semp: dict[tuple[date, str], LinhaSemPrazo] = {}

    for p in pedidos:
        classe = classificar(p, agora)
        if classe is Classe.NAO_PAGO:
            continue
        if p.order_status not in STATUS_CONHECIDOS:
            q.status_desconhecidos[p.order_status] += 1
        marca = registry.get(p.shop_id)
        if marca is None:
            q.contas_fora_do_registry[p.shop_id] += 1
            continue

        if p.ship_by_date is None:
            d = data_brt(p.pay_time)
            if not (p_desde <= d <= p_ate):
                q.fora_da_janela += 1
                continue
            linha = semp.setdefault((d, p.shop_account), LinhaSemPrazo(
                paid_date=d, shop_account=p.shop_account, shop_id=p.shop_id, brand=marca))
            linha.pedidos_pagos += 1
            if classe is Classe.FBS:
                linha.excl_fbs += 1
            elif classe is Classe.CANCELADO_SEM_COLETA:
                linha.excl_cancelado_sem_coleta += 1
                _motivo(q, p, None)
            else:  # Classe.SEM_PRAZO, pela precedencia
                linha.sem_prazo += 1
            continue

        d = data_brt(p.ship_by_date)
        if not (v_desde <= d <= v_ate):
            q.fora_da_janela += 1
            continue
        linha = venc.setdefault((d, p.shop_account), LinhaVencimento(
            deadline_date=d, shop_account=p.shop_account, shop_id=p.shop_id, brand=marca))
        linha.pedidos_pagos += 1
        if classe is Classe.FBS:
            linha.excl_fbs += 1
        elif classe is Classe.CANCELADO_SEM_COLETA:
            linha.excl_cancelado_sem_coleta += 1
            _motivo(q, p, linha)
        elif classe is Classe.ENVIADO_SEM_COLETA:
            linha.excl_enviado_sem_coleta += 1
        elif classe is Classe.NO_PRAZO:
            linha.no_prazo += 1
        elif classe is Classe.ATRASADO:
            linha.atrasado += 1
        elif classe is Classe.PENDENTE_VENCIDO:
            linha.pendente_vencido += 1
        else:
            linha.pendente_no_prazo += 1

    for linha in venc.values():
        linha.is_mature = madura(linha.deadline_date, hoje, watermarks.get(linha.shop_id))
        linha.validar()
    for linha in semp.values():
        linha.validar()

    return Apuracao(
        vencimentos=sorted(venc.values(), key=lambda x: (x.deadline_date, x.shop_account)),
        sem_prazo=sorted(semp.values(), key=lambda x: (x.paid_date, x.shop_account)),
        qualidade=q, hoje=hoje, agora=agora,
    )


def _motivo(q: Qualidade, p: Pedido, linha: LinhaVencimento | None) -> None:
    motivo = p.cancel_reason
    if motivo is None or not motivo.strip():
        q.motivos_ausentes += 1
        return
    if motivo not in MOTIVOS_CONHECIDOS:
        q.motivos_desconhecidos[motivo] += 1
        if linha is not None:
            linha.cancelado_motivo_desconhecido += 1
        return
    if motivo == MOTIVO_NAO_ENVIOU and linha is not None:
        linha.cancelado_vendedor_nao_enviou += 1


# ---------------------------------------------------------------------------
# Leitura da fonte (Data Mart, replica read-only)
# ---------------------------------------------------------------------------
#: Colunas EXPLICITAS — nunca `SELECT *`, que traria CPF, endereco e nome do
#: comprador. Lista fechada, travada por teste.
COLUNAS_FONTE = (
    "shop_id", "shop_account", "order_status", "fulfillment_flag",
    "pay_time", "ship_by_date", "pickup_done_time", "cancel_reason",
)

PEDIDOS_SQL = f"""
SELECT {', '.join(COLUNAS_FONTE)}
  FROM raw.shopee_orders
 WHERE pay_time IS NOT NULL
   AND (
        (ship_by_date >= %(venc_desde)s AND ship_by_date < %(venc_ate_excl)s)
     OR (ship_by_date IS NULL
         AND pay_time >= %(pag_desde)s AND pay_time < %(pag_ate_excl)s)
   )
"""

#: Watermark POR CONTA: quando a fonte daquela conta foi lida pela ultima vez.
WATERMARK_SQL = """
SELECT shop_id, MAX(ingested_at) AS max_ingested_at
  FROM raw.shopee_orders
 GROUP BY shop_id
"""

#: Implementacao INDEPENDENTE da mesma regra, em SQL, para a reconciliacao.
#: Nao reaproveita `classificar`: se as duas concordam no MESMO snapshot, a
#: concordancia prova a regra, e nao so' o determinismo de um mesmo codigo.
RECONCILIACAO_VENCIMENTO_SQL = """
WITH base AS (
  SELECT shop_id,
         (ship_by_date AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
         CASE
           WHEN fulfillment_flag = 'fulfilled_by_shopee' THEN 'fbs'
           WHEN order_status IN ('CANCELLED','IN_CANCEL') AND pickup_done_time IS NULL
             THEN 'cancelado_sem_coleta'
           WHEN pickup_done_time IS NULL
                AND order_status IN ('SHIPPED','TO_CONFIRM_RECEIVE','COMPLETED','TO_RETURN')
             THEN 'enviado_sem_coleta'
           WHEN pickup_done_time IS NOT NULL AND pickup_done_time <= ship_by_date
             THEN 'no_prazo'
           WHEN pickup_done_time IS NOT NULL THEN 'atrasado'
           WHEN ship_by_date < %(agora)s THEN 'pendente_vencido'
           ELSE 'pendente_no_prazo'
         END AS classe
    FROM raw.shopee_orders
   WHERE pay_time IS NOT NULL
     AND ship_by_date >= %(venc_desde)s AND ship_by_date < %(venc_ate_excl)s
     AND fulfillment_flag IN ('fulfilled_by_shopee','fulfilled_by_local_seller')
)
SELECT shop_id, dia, classe, count(*) AS n
  FROM base
 GROUP BY shop_id, dia, classe
"""


def janela_sql(hoje: date) -> dict:
    v_desde, v_ate = janela_de_vencimento(hoje)
    p_desde, p_ate = janela_de_pagamento(hoje)
    return {
        "venc_desde": inicio_do_dia_brt(v_desde),
        "venc_ate_excl": inicio_do_dia_brt(v_ate + timedelta(days=1)),
        "pag_desde": inicio_do_dia_brt(p_desde),
        "pag_ate_excl": inicio_do_dia_brt(p_ate + timedelta(days=1)),
    }


def _linhas(cur) -> list[dict]:
    """Aceita cursor de dicionario E de tupla — o fake e o real divergem."""
    nomes = [d[0] for d in cur.description]
    out = []
    for r in cur.fetchall():
        out.append(dict(r) if isinstance(r, dict) else dict(zip(nomes, r)))
    return out


def ler_pedidos(conn, hoje: date) -> list[Pedido]:
    with conn.cursor() as cur:
        cur.execute(PEDIDOS_SQL, janela_sql(hoje))
        linhas = _linhas(cur)
    return [
        Pedido(
            shop_id=str(r["shop_id"]), shop_account=str(r["shop_account"]),
            order_status=str(r["order_status"]), fulfillment_flag=r["fulfillment_flag"],
            pay_time=r["pay_time"], ship_by_date=r["ship_by_date"],
            pickup_done_time=r["pickup_done_time"], cancel_reason=r["cancel_reason"],
        )
        for r in linhas
    ]


def ler_watermarks(conn) -> dict[str, datetime | None]:
    with conn.cursor() as cur:
        cur.execute(WATERMARK_SQL)
        linhas = _linhas(cur)
    out: dict[str, datetime | None] = {}
    for r in linhas:
        wm = r["max_ingested_at"]
        if wm is not None and wm.tzinfo is None:
            wm = wm.replace(tzinfo=timezone.utc)
        out[str(r["shop_id"])] = wm
    return out


def ler_reconciliacao(conn, hoje: date, agora: datetime) -> dict[tuple[str, date], Counter]:
    """Contagem por (conta, dia, classe) pela implementacao SQL."""
    params = {**janela_sql(hoje), "agora": agora}
    with conn.cursor() as cur:
        cur.execute(RECONCILIACAO_VENCIMENTO_SQL, params)
        linhas = _linhas(cur)
    out: dict[tuple[str, date], Counter] = {}
    for r in linhas:
        out.setdefault((str(r["shop_id"]), r["dia"]), Counter())[r["classe"]] += int(r["n"])
    return out


class ReconciliacaoDivergente(RuntimeError):
    """Python e SQL discordam no mesmo snapshot. Nada e' publicado."""


def reconciliar(apuracao: Apuracao, sql: dict[tuple[str, date], Counter],
                registry: dict[str, str]) -> int:
    """Compara a apuracao com a contagem SQL. Devolve o numero de celulas.

    So' entram na comparacao as contas do registry: a apuracao nao publica as
    demais, e compara-las acusaria divergencia que e' so' escopo.
    """
    py: dict[tuple[str, date], Counter] = {}
    for x in apuracao.vencimentos:
        py[(x.shop_id, x.deadline_date)] = Counter({
            "fbs": x.excl_fbs, "cancelado_sem_coleta": x.excl_cancelado_sem_coleta,
            "enviado_sem_coleta": x.excl_enviado_sem_coleta, "no_prazo": x.no_prazo,
            "atrasado": x.atrasado, "pendente_vencido": x.pendente_vencido,
            "pendente_no_prazo": x.pendente_no_prazo,
        })
    sql = {k: v for k, v in sql.items() if k[0] in registry}
    chaves = set(py) | set(sql)
    diferentes = []
    for k in sorted(chaves, key=lambda k: (k[1], k[0])):
        a = +py.get(k, Counter())  # `+` descarta zeros
        b = +sql.get(k, Counter())
        if a != b:
            diferentes.append(f"{k[0]}/{k[1]}: python={dict(a)} sql={dict(b)}")
    if diferentes:
        raise ReconciliacaoDivergente(
            f"{len(diferentes)} celula(s) divergem: " + "; ".join(diferentes[:5]))
    return len(chaves)


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------
COLUNAS = (
    "refresh_batch_id", "channel", "deadline_date", "shop_account", "shop_id", "brand",
    "pedidos_pagos", "excl_fbs", "excl_cancelado_sem_coleta",
    "cancelado_vendedor_nao_enviou", "cancelado_motivo_desconhecido",
    "excl_enviado_sem_coleta", "base", "no_prazo", "atrasado",
    "pendente_vencido", "pendente_no_prazo", "is_mature",
    "effective_at", "source_watermark_at",
)
COLUNAS_SEM_PRAZO = (
    "refresh_batch_id", "channel", "paid_date", "shop_account", "shop_id", "brand",
    "pedidos_pagos", "excl_fbs", "excl_cancelado_sem_coleta", "sem_prazo",
    "effective_at", "source_watermark_at",
)

DELETE_VENCIMENTO_SQL = f"""
DELETE FROM {TABELA}
 WHERE channel = %(channel)s
   AND deadline_date >= %(desde)s::date AND deadline_date <= %(ate)s::date
"""
DELETE_SEM_PRAZO_SQL = f"""
DELETE FROM {TABELA_SEM_PRAZO}
 WHERE channel = %(channel)s
   AND paid_date >= %(desde)s::date AND paid_date <= %(ate)s::date
"""
INSERT_VENCIMENTO_SQL = (
    f"INSERT INTO {TABELA} ({', '.join(COLUNAS)}) VALUES "
    f"({', '.join(['%s'] * len(COLUNAS))})")
INSERT_SEM_PRAZO_SQL = (
    f"INSERT INTO {TABELA_SEM_PRAZO} ({', '.join(COLUNAS_SEM_PRAZO)}) VALUES "
    f"({', '.join(['%s'] * len(COLUNAS_SEM_PRAZO))})")

#: Releitura DENTRO da transacao, antes do commit: o que esta' no banco tem de
#: ser exatamente o que foi calculado. Pega INSERT parcial, trigger ou default
#: inesperado antes de virar dado publicado.
RELEITURA_SQL = f"""
SELECT count(*) AS linhas, coalesce(sum(pedidos_pagos),0) AS pagos,
       coalesce(sum(base),0) AS base, coalesce(sum(atrasado + pendente_vencido),0) AS atraso
  FROM {TABELA}
 WHERE channel = %(channel)s AND refresh_batch_id = %(lote)s
"""


def linhas_para_publicar(
    apuracao: Apuracao, watermarks: dict[str, datetime | None],
    *, batch_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, list[tuple], list[tuple]]:
    lote = batch_id or uuid.uuid4()
    venc = []
    for x in apuracao.vencimentos:
        x.validar()
        venc.append((
            str(lote), CANAL.value, x.deadline_date, x.shop_account, x.shop_id, x.brand,
            x.pedidos_pagos, x.excl_fbs, x.excl_cancelado_sem_coleta,
            x.cancelado_vendedor_nao_enviou, x.cancelado_motivo_desconhecido,
            x.excl_enviado_sem_coleta, x.base, x.no_prazo, x.atrasado,
            x.pendente_vencido, x.pendente_no_prazo, x.is_mature,
            apuracao.agora, watermarks.get(x.shop_id),
        ))
    semp = []
    for x in apuracao.sem_prazo:
        x.validar()
        semp.append((
            str(lote), CANAL.value, x.paid_date, x.shop_account, x.shop_id, x.brand,
            x.pedidos_pagos, x.excl_fbs, x.excl_cancelado_sem_coleta, x.sem_prazo,
            apuracao.agora, watermarks.get(x.shop_id),
        ))
    return lote, venc, semp


def esperado_na_releitura(apuracao: Apuracao) -> dict:
    return {
        "linhas": len(apuracao.vencimentos),
        "pagos": sum(x.pedidos_pagos for x in apuracao.vencimentos),
        "base": sum(x.base for x in apuracao.vencimentos),
        "atraso": sum(x.atrasado + x.pendente_vencido for x in apuracao.vencimentos),
    }


# ---------------------------------------------------------------------------
# Leitura agregada (diagnostico e serving usam a MESMA conta)
# ---------------------------------------------------------------------------
def taxa(atrasados: int, base: int) -> float | None:
    """`None`, nunca `0.0`, quando nao ha base."""
    return (atrasados / base) if base > 0 else None


def cobertura(base: int, sem_prazo: int, enviado_sem_coleta: int) -> tuple[float | None, str]:
    """(fracao ausente, estado). Estado: completa | aviso | insuficiente | sem_base.

    Denominador = base elegivel = base + ausencias. Sem nada elegivel a
    cobertura e' indefinida (`None`), nunca 100%.
    """
    ausentes = sem_prazo + enviado_sem_coleta
    elegivel = base + ausentes
    if elegivel == 0:
        return None, "sem_base"
    f = ausentes / elegivel
    if f > LIMIAR_OCULTA_TAXA:
        return f, "insuficiente"
    if f > LIMIAR_AVISO_COBERTURA:
        return f, "aviso"
    return f, "completa"
