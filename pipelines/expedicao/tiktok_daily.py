"""Expedicao TikTok Shop — LDR (Late Dispatch Rate) e fluxo por data de pagamento.

POR QUE ESTE MODULO NAO REUSA `expedicao_fila_atual`
-----------------------------------------------------
Shopee e Mercado Livre publicam uma FOTOGRAFIA da fila: uma linha por pedido
aguardando expedicao agora. Aqui o grao e' a COORTE (data de pagamento, marca)
e a pergunta e' outra. Forcar a serie na fila destruiria o grao de um dos dois
lados. Shopee e ML ficam intocados.

DUAS LEITURAS, COM DENOMINADORES DIFERENTES — E ISSO IMPORTA
--------------------------------------------------------------
    LDR OPERACIONAL — VENCIMENTOS NA JANELA  (a metrica oficial)
        denominador: pedidos cujo prazo de envio VENCE dentro da janela
        numerador:   enviados apos o SLA + nao enviados com SLA ja vencido

    FLUXO POR DATA DE PAGAMENTO              (a visao pedida pela gestao)
        denominador: pedidos PAGOS em cada dia

As duas nao sao intercambiaveis. Um feriado empurra tres dias de pagamento para
o mesmo vencimento: pela data de pagamento isso vira tres linhas de ~100%; pelo
vencimento vira um unico dia ruim com o volume somado. A segunda e' a que
responde "estamos dentro da regua do TikTok"; a primeira e' a que a operacao
usa para achar onde o fluxo entupiu. O codigo publica as duas, NOMEADAS, e
nunca usa a coorte de pagamento silenciosamente como se fosse o denominador
oficial.

OS DOIS EVENTOS TEM PRAZOS DIFERENTES
--------------------------------------
    RTS  (despacho / etiqueta)      -> 1 dia util
    TTS  (coleta pela transportadora) -> 2 dias uteis

Aplicar 2 dias uteis a etiqueta — como esta revisao corrigiu — subestimava o
atraso da operacao: medido em 01-23/09, a taxa de RTS passa de 0,06% (regra
errada) para 0,61% (regra certa), com dias chegando a 1,94%. Continua dentro da
meta de 4%, mas isso agora e' uma medicao, e nao um artefato de prazo frouxo.

O QUE E' CADA EVENTO, MEDIDO NA FONTE (2026-09-24)
----------------------------------------------------
1. RTS = `max(raw.tiktok_shop_line_items.rts_time)` do pedido, e SOMENTE quando
   TODOS os itens tem `rts_time`. Medido em 175.925 pedidos de 01-24/09:
   `rts_time` parcial dentro do pedido = 0 casos, e `min <> max` = 0 casos
   (spread maximo 0,00 h). Hoje as duas formas dao o mesmo numero; a regra
   estrita existe porque um item sem carimbo significa pedido incompleto, e
   ignora-lo declararia despachado o que nao saiu.

2. TTS = `min(updated_at_tiktok)` do log de status para `IN_TRANSIT`, e so'
   para ele. `DELIVERED`/`COMPLETED` NAO sao usados como fallback: medido,
   `IN_TRANSIT` cobre 171.017 de 172.197 pedidos (99,32%), e o fallback
   resolveria 10 pedidos (0,006%), espalhados por todas as marcas e nao
   concentrados nas afetadas. Aceitar o fallback trocaria um instante de coleta
   por um de entrega, dias depois, sem ganho de cobertura.

O PRAZO OFICIAL DO TIKTOK NAO ESTA NA NOSSA BASE
-------------------------------------------------
A API `get_order_list` v202309 devolve SLA por pedido (`rts_sla_time` e
afins). O `ORDER_COLUMN_MAPPING` da ingestao (repo `goca-se/airflow`,
`src/tiktok/config/constants.py`) nao mapeia nenhum deles — zero ocorrencias.
Logo o prazo aqui e' RECONSTRUIDO da politica (1 e 2 dias uteis), nao o numero
da plataforma, e `PRAZO_E_RECONSTRUIDO` obriga todo consumidor a rotular isso.

A reconstrucao foi RECONCILIADA contra a planilha da gestao (marca `barbours`,
01-23/09): a regra de dias uteis COM feriados nacionais reproduz a coluna de
taxa diaria com erro absoluto acumulado de 5 pp em 23 dias, contra 136 pp sem
feriados e 573 pp em dias corridos.

FUSO
----
Todo carimbo de origem TikTok (`paid_at`, `created_at`, `updated_at_tiktok`,
`rts_time`) esta em UTC-3 (BRT), medido por ancoragem em `now()`: 3,01 h nas
quatro colunas. `detected_at` e' carimbo NOSSO e esta em UTC (0,00 h). Nao ha
conversao a fazer na regra de negocio; o que nao se pode e' comparar
`detected_at` com os outros sem descontar as 3 horas.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum

from pipelines.expedicao.contract import Channel

# ---------------------------------------------------------------------------
# Contrato publicado
# ---------------------------------------------------------------------------
TABELA = "marts.expedicao_tiktok_dispatch_daily"
SOURCE_NAME = "expedicao_tiktok"

#: O prazo NAO vem do TikTok: e' reconstruido da politica de dias uteis.
#: Consumidor que exibir a taxa sem exibir isto esta mentindo por omissao.
PRAZO_E_RECONSTRUIDO = True


class Evento(str, Enum):
    """Os dois marcos da politica, com prazos DIFERENTES."""

    DESPACHO = "despacho"   # RTS — etiqueta pronta
    COLETA = "coleta"       # TTS — transportadora retirou


#: Dias uteis de prazo, por evento, contados ESTRITAMENTE apos o pagamento.
#: Pago numa segunda util: RTS vence no fim de terca, TTS no fim de quarta.
PRAZO_DIAS_UTEIS: dict[Evento, int] = {
    Evento.DESPACHO: 1,
    Evento.COLETA: 2,
}

#: META OFICIAL do TikTok para a LDR. Ultrapassar isto e' estar fora da regua
#: da plataforma, com penalizacao — e' um fato externo, nao uma preferencia.
META_TIKTOK_LDR = 0.04

#: Limiar INTERNO de severidade, muito acima da meta. Serve para achar o dia em
#: que a operacao quebrou, nao para dizer se estamos em conformidade. Os dois
#: numeros tem nomes distintos de proposito: confundi-los faria a tela dizer
#: "dentro da meta" num dia de 9%.
LIMIAR_CRITICO_INTERNO = 0.10

#: Evento padrao da tela. E' a coleta: e' o marco que a plataforma cobra e onde
#: o incidente de setembro aparece.
EVENTO_PADRAO = Evento.COLETA

#: Fuso dos carimbos de origem do TikTok. Medido, nao assumido.
TIKTOK_UTC_OFFSET = timedelta(hours=-3)

#: Janela padrao da tela: 7 dias + hoje.
JANELA_PADRAO_DIAS = 7

#: Uma coorte "em risco" ainda nao venceu, mas vence em ate N dias.
RISCO_DIAS = 1

#: Amostra gratis sai do denominador. `is_sample_order` cobre exatamente
#: `SELLER_FUND_FREE_SAMPLE` + `PLATFORM_SUBSIDY_SAMPLE` (medido: 5.164 + 56 =
#: 5.220 = total de `is_sample_order = true` em 30 dias).
#:
#: `ZERO_LOTTERY` (749 pedidos) NAO e' marcado como amostra e continua no
#: denominador: e' sorteio, nao amostra, e a politica so' fala de amostra.
#: Fica nomeado aqui para que a decisao seja visivel em vez de acidental.
TIPO_SORTEIO = "ZERO_LOTTERY"

#: Status pos-coleta usado SOMENTE para conferir cobertura, nunca como fonte
#: do instante (ver docstring do modulo).
STATUS_POS_COLETA_DIAGNOSTICO = ("IN_TRANSIT", "DELIVERED", "COMPLETED")

#: O instante de coleta vem SO deste status.
STATUS_COLETA = "IN_TRANSIT"


# ---------------------------------------------------------------------------
# Feriados
# ---------------------------------------------------------------------------
# `marts.dim_calendario` tem `is_weekend` mas NAO tem coluna de feriado. Entao
# a lista nacional vive aqui, versionada e testavel. Somente feriados
# NACIONAIS: feriado municipal muda por loja e nao temos a dimensao. A
# consequencia esta declarada — em feriado local a operacao aparece atrasada.
# Preferimos errar acusando um atraso a esconder um.
FERIADOS_NACIONAIS: frozenset[date] = frozenset(
    {
        # 2025 — Pascoa 20/04
        date(2025, 1, 1), date(2025, 3, 3), date(2025, 3, 4), date(2025, 4, 18),
        date(2025, 4, 21), date(2025, 5, 1), date(2025, 6, 19), date(2025, 9, 7),
        date(2025, 10, 12), date(2025, 11, 2), date(2025, 11, 15), date(2025, 11, 20),
        date(2025, 12, 25),
        # 2026 — Pascoa 05/04
        date(2026, 1, 1), date(2026, 2, 16), date(2026, 2, 17), date(2026, 4, 3),
        date(2026, 4, 21), date(2026, 5, 1), date(2026, 6, 4), date(2026, 9, 7),
        date(2026, 10, 12), date(2026, 11, 2), date(2026, 11, 15), date(2026, 11, 20),
        date(2026, 12, 25),
        # 2027 — Pascoa 28/03
        date(2027, 1, 1), date(2027, 2, 8), date(2027, 2, 9), date(2027, 3, 26),
        date(2027, 4, 21), date(2027, 5, 1), date(2027, 5, 27), date(2027, 9, 7),
        date(2027, 10, 12), date(2027, 11, 2), date(2027, 11, 15), date(2027, 11, 20),
        date(2027, 12, 25),
    }
)

#: Data fora disso nao pode ser classificada: `dia_util` levanta em vez de
#: devolver um palpite. Prazo silenciosamente errado e' pior que prazo ausente.
FERIADOS_COBERTURA = (date(2025, 1, 1), date(2027, 12, 31))


class ForaDaCoberturaDeFeriados(ValueError):
    """Data fora do intervalo em que sabemos quais dias sao feriado."""


def dia_util(d: date) -> bool:
    """`True` se `d` e' dia util nacional (seg-sex, nao feriado)."""
    if not FERIADOS_COBERTURA[0] <= d <= FERIADOS_COBERTURA[1]:
        raise ForaDaCoberturaDeFeriados(
            f"{d.isoformat()} esta fora de {FERIADOS_COBERTURA[0].isoformat()}"
            f"..{FERIADOS_COBERTURA[1].isoformat()}; atualize FERIADOS_NACIONAIS"
        )
    return d.weekday() < 5 and d not in FERIADOS_NACIONAIS


def prazo_de(pago_em: date, dias_uteis: int) -> date:
    """O n-esimo dia util ESTRITAMENTE apos `pago_em`.

    Pagar num sabado nao consome prazo: a contagem comeca no proximo dia util.
    O vencimento e' o FIM do dia devolvido — envio no dia devolvido esta no
    prazo.
    """
    if dias_uteis < 1:
        raise ValueError("dias_uteis deve ser >= 1")
    d = pago_em
    restantes = dias_uteis
    while restantes:
        d += timedelta(days=1)
        if dia_util(d):
            restantes -= 1
    return d


def prazo_do_evento(pago_em: date, evento: Evento) -> date:
    """Vencimento do evento, com o prazo da POLITICA daquele evento."""
    return prazo_de(pago_em, PRAZO_DIAS_UTEIS[evento])


def hoje_brt(agora_utc: datetime) -> date:
    """A data corrente no fuso da operacao, a partir de um `now()` aware."""
    if agora_utc.tzinfo is None:
        raise ValueError("agora_utc precisa ser aware")
    return (agora_utc.astimezone(timezone.utc) + TIKTOK_UTC_OFFSET).date()


# ---------------------------------------------------------------------------
# Linha publicada
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CoorteDiaria:
    """Uma (data de pagamento, marca), com os dois eventos lado a lado.

    Cada evento tem seu proprio vencimento, sua propria base e sua propria
    particao de quatro contagens. As particoes de eventos DIFERENTES nunca se
    somam: o mesmo pedido aparece uma vez em cada.

    A base de cada evento ja exclui amostra gratis e pedido cancelado ANTES do
    vencimento daquele evento. Como os vencimentos diferem (1 e 2 dias uteis),
    as duas bases podem diferir — e' esperado e esta coberto por `validar`.
    """

    paid_date: date
    brand: str
    shop_name: str | None

    pedidos_pagos_brutos: int
    amostras_excluidas: int

    despacho_deadline: date
    despacho_base: int
    despacho_cancelado_antes_sla: int
    despacho_no_prazo: int
    despacho_atrasado: int
    despacho_pendente_vencido: int
    despacho_pendente_no_prazo: int

    coleta_deadline: date
    coleta_base: int
    coleta_cancelado_antes_sla: int
    coleta_no_prazo: int
    coleta_atrasada: int
    coleta_pendente_vencida: int
    coleta_pendente_no_prazo: int

    # ---- acesso por evento ----------------------------------------------
    def prazo(self, evento: Evento) -> date:
        return (
            self.despacho_deadline if evento is Evento.DESPACHO
            else self.coleta_deadline
        )

    def base(self, evento: Evento) -> int:
        """Denominador do evento: pagos - amostras - cancelados antes do SLA."""
        return (
            self.despacho_base if evento is Evento.DESPACHO else self.coleta_base
        )

    def atrasados(self, evento: Evento) -> int:
        """Numerador: enviado apos o SLA + nao enviado com SLA vencido."""
        if evento is Evento.DESPACHO:
            return self.despacho_atrasado + self.despacho_pendente_vencido
        return self.coleta_atrasada + self.coleta_pendente_vencida

    def pendentes_vencidos(self, evento: Evento) -> int:
        return (
            self.despacho_pendente_vencido if evento is Evento.DESPACHO
            else self.coleta_pendente_vencida
        )

    def pendentes_no_prazo(self, evento: Evento) -> int:
        return (
            self.despacho_pendente_no_prazo if evento is Evento.DESPACHO
            else self.coleta_pendente_no_prazo
        )

    def madura(self, evento: Evento, hoje: date) -> bool:
        """So' e' comparavel depois que o prazo DAQUELE evento venceu.

        Antes disso a taxa ainda sobe, e exibi-la fechada — sobretudo como
        `0%` — afirma o que ninguem sabe. A coorte de hoje quase sempre esta em
        0% e quase nunca termina em 0%.
        """
        return self.prazo(evento) < hoje

    def em_risco(self, evento: Evento, hoje: date, dias: int = RISCO_DIAS) -> bool:
        """Ainda da' tempo, mas vence ja'. E' a coorte acionavel."""
        if self.madura(evento, hoje):
            return False
        return (self.prazo(evento) - hoje).days <= dias

    def taxa(self, evento: Evento) -> float | None:
        """Taxa da coorte, ou `None` quando nao ha base.

        `None` e nao `0.0`: coorte sem pedido elegivel nao tem taxa, e devolver
        zero a faria parecer perfeita numa media.
        """
        b = self.base(evento)
        return (self.atrasados(evento) / b) if b > 0 else None

    def validar(self) -> None:
        """Fecha o grao. Levanta se as particoes nao somarem a base do evento."""
        campos = {
            "pedidos_pagos_brutos": self.pedidos_pagos_brutos,
            "amostras_excluidas": self.amostras_excluidas,
            "despacho_base": self.despacho_base,
            "coleta_base": self.coleta_base,
        }
        for nome, v in campos.items():
            if v < 0:
                raise ValueError(f"{nome} negativo em {self.brand}/{self.paid_date}")

        for evento, partes, base, canc in (
            (
                Evento.DESPACHO,
                (self.despacho_no_prazo, self.despacho_atrasado,
                 self.despacho_pendente_vencido, self.despacho_pendente_no_prazo),
                self.despacho_base, self.despacho_cancelado_antes_sla,
            ),
            (
                Evento.COLETA,
                (self.coleta_no_prazo, self.coleta_atrasada,
                 self.coleta_pendente_vencida, self.coleta_pendente_no_prazo),
                self.coleta_base, self.coleta_cancelado_antes_sla,
            ),
        ):
            if any(p < 0 for p in partes):
                raise ValueError(
                    f"contagem negativa em {evento.value} "
                    f"{self.brand}/{self.paid_date}"
                )
            if sum(partes) != base:
                raise ValueError(
                    f"{evento.value} nao fecha em {self.brand}/{self.paid_date}: "
                    f"{sum(partes)} != {base}"
                )
            esperado = self.pedidos_pagos_brutos - self.amostras_excluidas - canc
            if base != esperado:
                raise ValueError(
                    f"base de {evento.value} nao bate com as exclusoes em "
                    f"{self.brand}/{self.paid_date}: {base} != {esperado}"
                )
            if self.prazo(evento) <= self.paid_date:
                raise ValueError(
                    f"prazo de {evento.value} nao e' posterior ao pagamento em "
                    f"{self.brand}/{self.paid_date}"
                )

        # O RTS vence ANTES do TTS, sempre: 1 dia util contra 2.
        if self.despacho_deadline > self.coleta_deadline:
            raise ValueError(
                f"prazo de despacho depois do de coleta em "
                f"{self.brand}/{self.paid_date}"
            )


# ---------------------------------------------------------------------------
# Agregacoes — as DUAS leituras
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Ldr:
    """LDR operacional: denominador = vencimentos DENTRO da janela.

    E' a metrica oficial. Nao e' a media das taxas diarias nem a razao sobre
    pedidos pagos: e' a razao sobre os pedidos que tinham obrigacao de sair no
    periodo. Um feriado, que empurra tres dias de pagamento para o mesmo
    vencimento, aparece aqui como UM dia com volume somado — e nao como tres
    linhas de 100% que inflariam qualquer media.
    """

    evento: Evento
    taxa: float | None
    base: int
    atrasados: int
    pendentes_vencidos: int
    pendentes_no_prazo: int
    em_risco: int
    vencimentos_maduros: int
    vencimentos_parciais: int

    @property
    def fora_da_meta(self) -> bool:
        """Acima da meta OFICIAL do TikTok (4%). Nao e' o limiar interno."""
        return self.taxa is not None and self.taxa > META_TIKTOK_LDR

    @property
    def critico_interno(self) -> bool:
        """Acima do limiar INTERNO (10%). Severidade, nao conformidade."""
        return self.taxa is not None and self.taxa >= LIMIAR_CRITICO_INTERNO


def _coortes_que_vencem_em(
    coortes: list[CoorteDiaria], evento: Evento, desde: date, ate: date
) -> list[CoorteDiaria]:
    return [c for c in coortes if desde <= c.prazo(evento) <= ate]


def calcular_ldr(
    coortes: list[CoorteDiaria],
    evento: Evento,
    hoje: date,
    *,
    desde: date,
    ate: date,
) -> Ldr:
    """A LDR da janela, pelo VENCIMENTO. Coorte imatura fica fora da taxa."""
    na_janela = _coortes_que_vencem_em(coortes, evento, desde, ate)
    maduras = [c for c in na_janela if c.madura(evento, hoje)]
    parciais = [c for c in na_janela if not c.madura(evento, hoje)]

    base = sum(c.base(evento) for c in maduras)
    atrasados = sum(c.atrasados(evento) for c in maduras)
    return Ldr(
        evento=evento,
        taxa=(atrasados / base) if base else None,
        base=base,
        atrasados=atrasados,
        pendentes_vencidos=sum(c.pendentes_vencidos(evento) for c in maduras),
        pendentes_no_prazo=sum(c.pendentes_no_prazo(evento) for c in na_janela),
        em_risco=sum(
            c.pendentes_no_prazo(evento) for c in na_janela if c.em_risco(evento, hoje)
        ),
        vencimentos_maduros=len({c.prazo(evento) for c in maduras}),
        vencimentos_parciais=len({c.prazo(evento) for c in parciais}),
    )


def serie_por_vencimento(
    coortes: list[CoorteDiaria], evento: Evento, hoje: date
) -> list[tuple[date, float | None, int, int, bool]]:
    """(vencimento, taxa, base, atrasados, madura) — a serie da LDR.

    Soma numerador e denominador; nunca soma taxas. Consolidar aqui, uma vez,
    evita que cada consumidor reimplemente a soma e caia no erro tentador de
    tirar a media das porcentagens.
    """
    acc: dict[date, list] = {}
    for c in coortes:
        a = acc.setdefault(c.prazo(evento), [0, 0, True])
        a[0] += c.atrasados(evento)
        a[1] += c.base(evento)
        a[2] = a[2] and c.madura(evento, hoje)
    return [
        (d, (atras / base if base else None), base, atras, mad)
        for d, (atras, base, mad) in sorted(acc.items())
    ]


def serie_por_pagamento(
    coortes: list[CoorteDiaria], evento: Evento, hoje: date
) -> list[tuple[date, date, float | None, int, int, bool]]:
    """(pago_em, vence_em, taxa, base, atrasados, madura) — a visao da gestao.

    Mesma forma da planilha do gestor. NAO e' a LDR oficial: o denominador aqui
    e' quem PAGOU no dia, nao quem VENCE no dia.
    """
    acc: dict[date, list] = {}
    for c in coortes:
        a = acc.setdefault(c.paid_date, [0, 0, True, c.prazo(evento)])
        a[0] += c.atrasados(evento)
        a[1] += c.base(evento)
        a[2] = a[2] and c.madura(evento, hoje)
        a[3] = max(a[3], c.prazo(evento))
    return [
        (d, venc, (atras / base if base else None), base, atras, mad)
        for d, (atras, base, mad, venc) in sorted(acc.items())
    ]


def marcas_criticas(
    coortes: list[CoorteDiaria],
    evento: Evento,
    hoje: date,
    *,
    desde: date | None = None,
    ate: date | None = None,
    minimo_pedidos: int = 50,
) -> list[tuple[str, float, int]]:
    """Marcas ordenadas por LDR, so' as maduras e com base suficiente.

    `desde`/`ate` recortam pelo VENCIMENTO, como a LDR faz. Sem esse recorte o
    ranking usaria toda a extracao — que e' propositalmente mais larga que a
    janela, para nao perder coorte empurrada por feriado — e devolveria a taxa
    do incidente antigo como se fosse a de agora.

    `minimo_pedidos` evita o ranking ser liderado por uma marca que teve 3
    pedidos e atrasou 1 — 33% que nao significa nada operacionalmente.
    """
    if desde is not None and ate is not None:
        coortes = _coortes_que_vencem_em(coortes, evento, desde, ate)
    por_marca: dict[str, list[int]] = {}
    for c in coortes:
        if not c.madura(evento, hoje):
            continue
        acc = por_marca.setdefault(c.brand, [0, 0])
        acc[0] += c.atrasados(evento)
        acc[1] += c.base(evento)
    saida = [
        (marca, atras / base, base)
        for marca, (atras, base) in por_marca.items()
        if base >= minimo_pedidos
    ]
    return sorted(saida, key=lambda x: (-x[1], -x[2], x[0]))


def primeiro_dia_do_incidente(
    coortes: list[CoorteDiaria],
    evento: Evento,
    hoje: date,
    *,
    limiar: float = LIMIAR_CRITICO_INTERNO,
) -> date | None:
    """O VENCIMENTO em que a taxa consolidada cruzou `limiar`.

    Pelo vencimento, e nao pela data de pagamento: e' o dia em que a operacao
    de fato falhou. So' olha vencimentos maduros — um parcial que cruza o
    limiar ainda pode recuar quando o resto for coletado.
    """
    for d, taxa, _base, _atras, madura in serie_por_vencimento(coortes, evento, hoje):
        if madura and taxa is not None and taxa >= limiar:
            return d
    return None


def ultimo_dia_do_incidente(
    coortes: list[CoorteDiaria],
    evento: Evento,
    hoje: date,
    *,
    limiar: float = LIMIAR_CRITICO_INTERNO,
) -> date | None:
    ultimo = None
    for d, taxa, _base, _atras, madura in serie_por_vencimento(coortes, evento, hoje):
        if madura and taxa is not None and taxa >= limiar:
            ultimo = d
    return ultimo


# ---------------------------------------------------------------------------
# SQL da fonte (Data Mart, read-only)
# ---------------------------------------------------------------------------
# Um unico SELECT, agregado no banco. O grao publicado e' (dia, marca) e puxar
# 200 mil pedidos para contar em Python so' aumentaria a janela em que a fonte
# muda debaixo da leitura.
#
# `%(prazos)s` chega como uma lista de [pago_em, vence_rts, vence_tts] ja
# calculada em Python. O calendario de dias uteis NAO e' reimplementado em SQL
# de proposito: seria a mesma regra escrita duas vezes, e a versao SQL nao
# teria como levantar `ForaDaCoberturaDeFeriados`.
#
# PERFORMANCE: as derivacoes sao agregadas UMA vez, restritas aos pedidos da
# janela, e ligadas por LEFT JOIN em `order_id` SOZINHO — o banco impoe
# `uk_tiktok_orders UNIQUE (order_id)`, entao `brand` e' funcionalmente
# dependente e acrescenta-lo so' esconde o indice. Com (brand, order_id) o
# planner trocava por hash join sobre 3,2 GB de line items e a extracao nao
# terminava em 5 minutos.
FONTE_SQL = """
WITH prazo(pago_em, vence_rts, vence_tts) AS (
    SELECT (p->>0)::date, (p->>1)::date, (p->>2)::date
    FROM jsonb_array_elements(%(prazos)s::jsonb) p
),
ped AS MATERIALIZED (
    SELECT o.brand, o.order_id, o.shop_name,
           o.paid_at::date AS pago_em,
           coalesce(o.is_sample_order, false) AS amostra,
           o.order_status = 'CANCELLED' AS cancelado
      FROM raw.tiktok_shop_orders o
     WHERE o.paid_at IS NOT NULL
       AND o.paid_at >= %(desde)s::date
       AND o.paid_at <  (%(ate)s::date + 1)
),
-- RTS ESTRITO: o pedido so' esta despachado quando TODOS os itens tem
-- `rts_time`, e o instante e' o ULTIMO deles. Medido, hoje nao ha pedido com
-- carimbo parcial; a regra existe para que um item sem carimbo nunca seja
-- ignorado e o pedido apareca despachado sem ter saido inteiro.
desp AS (
    SELECT li.order_id,
           max(li.rts_time) AS t,
           count(*)                AS itens,
           count(li.rts_time)      AS com_rts
      FROM raw.tiktok_shop_line_items li
      JOIN ped p ON p.order_id = li.order_id
     GROUP BY 1
),
-- TTS: SOMENTE `IN_TRANSIT`. Sem fallback por DELIVERED/COMPLETED — ver
-- docstring do modulo. `detected_at >= desde - 1` troca um Seq Scan de 4,1
-- milhoes de linhas por uma faixa de indice, e e' seguro por causalidade: a
-- coleta acontece depois do pagamento, e `detected_at` e' posterior a coleta.
-- O dia de folga cobre `detected_at` ser UTC enquanto `paid_at` e' UTC-3.
col AS (
    SELECT l.order_id, min(l.updated_at_tiktok) AS t
      FROM raw.tiktok_shop_order_status_log l
      JOIN ped p ON p.order_id = l.order_id
     WHERE l.new_status = %(status_coleta)s
       AND l.updated_at_tiktok IS NOT NULL
       AND l.detected_at >= (%(desde)s::date - 1)
     GROUP BY 1
),
-- Instante do cancelamento. So' existe pelo log: a tabela de pedidos nao tem
-- coluna de cancelamento (verificado no information_schema).
canc AS (
    SELECT l.order_id, min(l.updated_at_tiktok) AS t
      FROM raw.tiktok_shop_order_status_log l
      JOIN ped p ON p.order_id = l.order_id
     WHERE l.new_status = 'CANCELLED'
       AND l.updated_at_tiktok IS NOT NULL
     GROUP BY 1
),
base AS (
    SELECT p.brand, p.shop_name, p.pago_em, p.amostra, p.cancelado,
           z.vence_rts, z.vence_tts,
           CASE WHEN d.com_rts = d.itens THEN d.t END AS despachado_em,
           c.t AS coletado_em,
           k.t::date AS cancelado_em
      FROM ped p
      JOIN prazo z ON z.pago_em = p.pago_em
      LEFT JOIN desp d ON d.order_id = p.order_id
      LEFT JOIN col  c ON c.order_id = p.order_id
      LEFT JOIN canc k ON k.order_id = p.order_id
),
-- Cancelamento ANTES do vencimento sai do denominador daquele evento; DEPOIS
-- permanece (o pedido tinha obrigacao de sair e nao saiu). Cancelado SEM
-- carimbo permanece: medido, 21,6 por cento dos cancelados nao tem linha no
-- log. O sinal de porcentagem NAO pode aparecer aqui: o psycopg2 le esse
-- caractere como marcador de parametro em qualquer ponto da query, inclusive
-- dentro de comentario, e a chamada morre com "argument formats cant be mixed".
-- Excluir todos eles
-- exclui-los todos apagaria atraso real que ninguem poderia auditar.
classificado AS (
    SELECT brand, shop_name, pago_em, amostra,
           vence_rts, vence_tts, despachado_em, coletado_em,
           (cancelado AND cancelado_em IS NOT NULL AND cancelado_em <= vence_rts)
               AS fora_rts,
           (cancelado AND cancelado_em IS NOT NULL AND cancelado_em <= vence_tts)
               AS fora_tts
      FROM base
)
SELECT
    pago_em                                                     AS paid_date,
    brand,
    max(shop_name)                                              AS shop_name,
    vence_rts                                                   AS despacho_deadline,
    vence_tts                                                   AS coleta_deadline,
    count(*)                                                    AS pedidos_pagos_brutos,
    count(*) FILTER (WHERE amostra)                             AS amostras_excluidas,

    count(*) FILTER (WHERE NOT amostra AND fora_rts)            AS despacho_cancelado_antes_sla,
    count(*) FILTER (WHERE NOT amostra AND NOT fora_rts)        AS despacho_base,
    count(*) FILTER (WHERE NOT amostra AND NOT fora_rts AND despachado_em IS NOT NULL
                       AND despachado_em::date <= vence_rts)    AS despacho_no_prazo,
    count(*) FILTER (WHERE NOT amostra AND NOT fora_rts AND despachado_em IS NOT NULL
                       AND despachado_em::date >  vence_rts)    AS despacho_atrasado,
    count(*) FILTER (WHERE NOT amostra AND NOT fora_rts AND despachado_em IS NULL
                       AND vence_rts <  %(hoje)s::date)         AS despacho_pendente_vencido,
    count(*) FILTER (WHERE NOT amostra AND NOT fora_rts AND despachado_em IS NULL
                       AND vence_rts >= %(hoje)s::date)         AS despacho_pendente_no_prazo,

    count(*) FILTER (WHERE NOT amostra AND fora_tts)            AS coleta_cancelado_antes_sla,
    count(*) FILTER (WHERE NOT amostra AND NOT fora_tts)        AS coleta_base,
    count(*) FILTER (WHERE NOT amostra AND NOT fora_tts AND coletado_em IS NOT NULL
                       AND coletado_em::date <= vence_tts)      AS coleta_no_prazo,
    count(*) FILTER (WHERE NOT amostra AND NOT fora_tts AND coletado_em IS NOT NULL
                       AND coletado_em::date >  vence_tts)      AS coleta_atrasada,
    count(*) FILTER (WHERE NOT amostra AND NOT fora_tts AND coletado_em IS NULL
                       AND vence_tts <  %(hoje)s::date)         AS coleta_pendente_vencida,
    count(*) FILTER (WHERE NOT amostra AND NOT fora_tts AND coletado_em IS NULL
                       AND vence_tts >= %(hoje)s::date)         AS coleta_pendente_no_prazo
  FROM classificado
 GROUP BY 1, 2, 4, 5
 ORDER BY 1, 2
"""

#: Carimbo de frescor da fonte. `extracted_at` e' carimbo da NOSSA ingestao e
#: esta em UTC, ao contrario dos carimbos de negocio.
WATERMARK_SQL = """
SELECT max(extracted_at) AS max_extracted_at
  FROM raw.tiktok_shop_orders
 WHERE paid_at >= %(desde)s::date
"""

#: Diagnostico de PROVENIENCIA do instante de coleta. Nao alimenta a metrica:
#: existe para que a tela possa recusar a medicao se um dia o `IN_TRANSIT`
#: deixar de cobrir a carteira. Medido em 2026-09-24: 99,32% de cobertura e
#: 0,006% que so' teriam instante por DELIVERED/COMPLETED.
PROVENIENCIA_SQL = """
WITH ped AS MATERIALIZED (
    SELECT order_id, brand FROM raw.tiktok_shop_orders
     WHERE paid_at >= %(desde)s::date AND paid_at < (%(ate)s::date + 1)
       AND order_status <> 'CANCELLED' AND NOT coalesce(is_sample_order, false)
),
ev AS (
    SELECT l.order_id,
           bool_or(l.new_status = 'IN_TRANSIT') AS it,
           bool_or(l.new_status IN ('DELIVERED', 'COMPLETED')) AS posterior
      FROM raw.tiktok_shop_order_status_log l
      JOIN ped p ON p.order_id = l.order_id
     WHERE l.new_status IN ('IN_TRANSIT', 'DELIVERED', 'COMPLETED')
     GROUP BY 1
)
SELECT p.brand,
       count(*)                                                  AS pedidos,
       count(*) FILTER (WHERE e.it)                              AS com_in_transit,
       count(*) FILTER (WHERE NOT coalesce(e.it, false)
                          AND coalesce(e.posterior, false))       AS so_posterior,
       count(*) FILTER (WHERE e.order_id IS NULL)                AS sem_evento
  FROM ped p LEFT JOIN ev e ON e.order_id = p.order_id
 GROUP BY 1 ORDER BY 2 DESC
"""


def janela_de_vencimento(hoje: date, dias: int) -> tuple[date, date]:
    """A janela fechada [hoje-dias, hoje]. `dias=7` devolve 8 datas."""
    return (hoje - timedelta(days=dias), hoje)


def janela_de_pagamento(hoje: date, dias: int) -> tuple[date, date]:
    """Datas de PAGAMENTO a extrair para cobrir a janela de VENCIMENTO.

    Um vencimento em `hoje - dias` pode vir de um pagamento bem anterior:
    entre uma sexta e o vencimento de terca ha um fim de semana, e com feriado
    o vao cresce. Extrair so' a janela de vencimento perderia justamente as
    coortes empurradas por feriado — que sao as que produzem os dias ruins.
    """
    desde_venc, ate = janela_de_vencimento(hoje, dias)
    folga = max(PRAZO_DIAS_UTEIS.values()) + 7  # 2 uteis + emenda de feriado
    return (desde_venc - timedelta(days=folga), ate)


def prazos_da_janela(hoje: date, dias: int = JANELA_PADRAO_DIAS) -> list[list[str]]:
    """[pago_em, vence_rts, vence_tts] em ISO, prontos para o SQL."""
    desde, ate = janela_de_pagamento(hoje, dias)
    saida = []
    d = desde
    while d <= ate:
        saida.append([
            d.isoformat(),
            prazo_do_evento(d, Evento.DESPACHO).isoformat(),
            prazo_do_evento(d, Evento.COLETA).isoformat(),
        ])
        d += timedelta(days=1)
    return saida


#: Tentativas de leitura na replica. O Data Mart e' um HOT STANDBY: uma
#: consulta longa e' cancelada com `SerializationFailure` quando o primario
#: remove versoes de linha que ela ainda precisaria ver. Nao e' erro nosso e
#: nao adianta corrigir SQL — a leitura seguinte costuma passar. Sem retry o
#: publisher falha de forma intermitente e o operador conclui que o codigo
#: esta quebrado.
TENTATIVAS_LEITURA = 4


def _ler(conn, sql: str, params: dict) -> list[dict]:
    """Executa uma leitura com retry para o cancelamento por recovery."""
    import time  # noqa: PLC0415

    import psycopg2  # noqa: PLC0415

    for tentativa in range(TENTATIVAS_LEITURA):
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        except psycopg2.errors.SerializationFailure:
            if tentativa == TENTATIVAS_LEITURA - 1:
                raise
            # Espera crescente: a janela de conflito com o recovery e' curta,
            # mas insistir no mesmo instante so' repete a colisao.
            time.sleep(2 * (tentativa + 1))
    raise AssertionError("inalcancavel")  # pragma: no cover


def extrair(conn, hoje: date, dias: int = JANELA_PADRAO_DIAS) -> list[CoorteDiaria]:
    """Le a fonte e devolve as coortes ja validadas. Conexao deve ser read-only."""
    import json  # noqa: PLC0415 - so' aqui, para nao pesar o import do modulo

    desde, ate = janela_de_pagamento(hoje, dias)
    linhas = _ler(
        conn,
        FONTE_SQL,
        {
            "prazos": json.dumps(prazos_da_janela(hoje, dias)),
            "status_coleta": STATUS_COLETA,
            "hoje": hoje.isoformat(),
            "desde": desde.isoformat(),
            "ate": ate.isoformat(),
        },
    )

    coortes = []
    for r in linhas:
        c = CoorteDiaria(
            paid_date=r["paid_date"],
            brand=str(r["brand"]),
            shop_name=r["shop_name"],
            pedidos_pagos_brutos=int(r["pedidos_pagos_brutos"]),
            amostras_excluidas=int(r["amostras_excluidas"]),
            despacho_deadline=r["despacho_deadline"],
            despacho_base=int(r["despacho_base"]),
            despacho_cancelado_antes_sla=int(r["despacho_cancelado_antes_sla"]),
            despacho_no_prazo=int(r["despacho_no_prazo"]),
            despacho_atrasado=int(r["despacho_atrasado"]),
            despacho_pendente_vencido=int(r["despacho_pendente_vencido"]),
            despacho_pendente_no_prazo=int(r["despacho_pendente_no_prazo"]),
            coleta_deadline=r["coleta_deadline"],
            coleta_base=int(r["coleta_base"]),
            coleta_cancelado_antes_sla=int(r["coleta_cancelado_antes_sla"]),
            coleta_no_prazo=int(r["coleta_no_prazo"]),
            coleta_atrasada=int(r["coleta_atrasada"]),
            coleta_pendente_vencida=int(r["coleta_pendente_vencida"]),
            coleta_pendente_no_prazo=int(r["coleta_pendente_no_prazo"]),
        )
        c.validar()
        coortes.append(c)
    return coortes


def ler_watermark(conn, hoje: date, dias: int = JANELA_PADRAO_DIAS) -> datetime | None:
    """`max(extracted_at)` da janela, normalizado para UTC aware."""
    desde, _ = janela_de_pagamento(hoje, dias)
    linhas = _ler(conn, WATERMARK_SQL, {"desde": desde.isoformat()})
    bruto = (linhas[0] if linhas else {}).get("max_extracted_at")
    if bruto is None:
        return None
    return bruto.replace(tzinfo=timezone.utc) if bruto.tzinfo is None else bruto


def ler_proveniencia(conn, hoje: date, dias: int = JANELA_PADRAO_DIAS) -> list[dict]:
    """Diagnostico da origem do instante de coleta, por marca."""
    desde, ate = janela_de_pagamento(hoje, dias)
    return _ler(conn, PROVENIENCIA_SQL,
                {"desde": desde.isoformat(), "ate": ate.isoformat()})


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------
COLUNAS = (
    "refresh_batch_id", "channel", "paid_date", "brand", "shop_name",
    "pedidos_pagos_brutos", "amostras_excluidas",
    "despacho_deadline", "despacho_base", "despacho_cancelado_antes_sla",
    "despacho_no_prazo", "despacho_atrasado",
    "despacho_pendente_vencido", "despacho_pendente_no_prazo",
    "despacho_is_mature",
    "coleta_deadline", "coleta_base", "coleta_cancelado_antes_sla",
    "coleta_no_prazo", "coleta_atrasada",
    "coleta_pendente_vencida", "coleta_pendente_no_prazo",
    "coleta_is_mature",
    "effective_at", "source_watermark_at",
)

#: A janela inteira e' reescrita a cada publicacao, e nao so' os dias novos:
#: uma coorte antiga MUDA quando um pedido dela e' finalmente coletado. Manter
#: a linha velha deixaria a taxa historica congelada num valor que deixou de
#: ser verdade. O DELETE e' por (channel, paid_date) dentro da janela.
DELETE_JANELA_SQL = f"""
DELETE FROM {TABELA}
 WHERE channel = %(channel)s
   AND paid_date >= %(desde)s::date
   AND paid_date <= %(ate)s::date
"""


def linhas_para_publicar(
    coortes: list[CoorteDiaria],
    *,
    hoje: date,
    effective_at: datetime,
    watermark: datetime | None,
    batch_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, list[tuple]]:
    """Materializa as tuplas na ordem de `COLUNAS`. Nao toca no banco."""
    if effective_at.tzinfo is None:
        raise ValueError("effective_at precisa ser aware")
    lote = batch_id or uuid.uuid4()
    linhas = []
    for c in coortes:
        c.validar()
        linhas.append(
            (
                str(lote), Channel.TIKTOKSHOP.value, c.paid_date, c.brand, c.shop_name,
                c.pedidos_pagos_brutos, c.amostras_excluidas,
                c.despacho_deadline, c.despacho_base, c.despacho_cancelado_antes_sla,
                c.despacho_no_prazo, c.despacho_atrasado,
                c.despacho_pendente_vencido, c.despacho_pendente_no_prazo,
                c.madura(Evento.DESPACHO, hoje),
                c.coleta_deadline, c.coleta_base, c.coleta_cancelado_antes_sla,
                c.coleta_no_prazo, c.coleta_atrasada,
                c.coleta_pendente_vencida, c.coleta_pendente_no_prazo,
                c.madura(Evento.COLETA, hoje),
                effective_at, watermark,
            )
        )
    return lote, linhas
