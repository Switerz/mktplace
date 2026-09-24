"""Expedicao TikTok Shop — serie diaria de atraso de despacho por data de pagamento.

POR QUE ESTE MODULO NAO REUSA `expedicao_fila_atual`
-----------------------------------------------------
Shopee e Mercado Livre publicam uma FOTOGRAFIA da fila atual: uma linha por
pedido aguardando expedicao agora. A pergunta do TikTok e' outra — "qual foi a
taxa de atraso da COORTE que pagou no dia D" — e o grao e' (data de pagamento,
marca), nao (conta, pedido). Forcar essa serie na fila destruiria o grao de um
dos dois lados. Por isso: tabela propria, publisher proprio, e Shopee/ML
intocados (Gate EXP-TK-OPS-1).

O QUE E' "POSTADO" — DOIS EVENTOS DISTINTOS, AMBOS MEDIDOS
-----------------------------------------------------------
Medido em 2026-09-24 contra a fonte real. Nao ha um unico instante de
"postagem": ha dois, separados por ~2 dias, e confundi-los troca o dono do
problema.

1. DESPACHO (etiqueta) = `min(raw.tiktok_shop_line_items.rts_time)`.
   E' o instante em que o vendedor confirma "pronto para envio" e a etiqueta
   nasce. Discriminador PERFEITO, medido em 60 dias de pedidos pagos:

       AWAITING_COLLECTION / IN_TRANSIT / DELIVERED / COMPLETED -> 100,0% preenchido
       AWAITING_SHIPMENT / ON_HOLD                              ->   0,0% preenchido

   E' tambem, comprovadamente, a transicao para `AWAITING_COLLECTION`: a
   mediana de (log.updated_at_tiktok - rts_time) para esse status e' 0,00 h.
   Ou seja, `rts_time` nao e' uma aproximacao do evento — e' o evento.

2. COLETA (transportadora) = `min(updated_at_tiktok)` no log de status para
   `IN_TRANSIT`/`DELIVERED`/`COMPLETED`. E' quando a transportadora de fato
   retira. Mediana de 44,93 h DEPOIS do despacho. Presente em 99,94% dos
   pedidos pagos nao-cancelados (104.132 de 104.197 medidos em 04-15/09).

Os dois sao publicados lado a lado de proposito. Na janela medida a taxa por
DESPACHO ficou em ~0,03% enquanto a taxa por COLETA chegou a 88,9% num unico
dia: a operacao etiquetou no prazo e a coleta nao aconteceu. Publicar so' uma
das duas responderia a pergunta errada.

O PRAZO OFICIAL DO TIKTOK NAO ESTA NA NOSSA BASE
-------------------------------------------------
A API `get_order_list` v202309 devolve campos de SLA por pedido
(`rts_sla_time` e afins). O `ORDER_COLUMN_MAPPING` da ingestao (repo
`goca-se/airflow`, `src/tiktok/config/constants.py`) NAO mapeia nenhum deles —
verificado por busca direta, zero ocorrencias. Logo o prazo usado aqui e' uma
RECONSTRUCAO da regra de 2 dias uteis descrita pela gestao, e nao o numero da
plataforma. Todo consumidor precisa exibir isso rotulado (ver
`PRAZO_E_RECONSTRUIDO`). Enquanto o campo oficial nao for ingerido, esta
metrica e' um PROXY fiel da regra, nao a medicao da penalizacao.

FUSO
----
Todo carimbo de origem TikTok (`paid_at`, `created_at`, `updated_at_tiktok`,
`rts_time`) esta em UTC-3 (BRT). Medido por ancoragem em `now()`: a distancia
entre `now() AT TIME ZONE 'UTC'` e o carimbo mais recente de cada coluna deu
3,01 h nas quatro. `detected_at` e' carimbo NOSSO e esta em UTC (0,00 h).
Nao ha conversao a fazer: a regra de negocio e' local por definicao, e a fonte
ja entrega local. O que NAO se pode fazer e' comparar `detected_at` com os
outros sem descontar as 3 horas.
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

#: O prazo NAO vem do TikTok: e' reconstruido da regra de 2 dias uteis.
#: Consumidor que exibir a taxa sem exibir isto esta mentindo por omissao.
PRAZO_E_RECONSTRUIDO = True

#: Dias uteis de prazo para despachar, contados ESTRITAMENTE apos a data de
#: pagamento. Pago numa segunda util -> vence no fim da quarta util.
PRAZO_DIAS_UTEIS = 2

#: Fuso dos carimbos de origem do TikTok. Medido, nao assumido (ver docstring).
TIKTOK_UTC_OFFSET = timedelta(hours=-3)

#: Janela padrao da tela: 7 dias + hoje.
JANELA_PADRAO_DIAS = 7

#: Uma coorte "em risco" e' a que ainda nao venceu mas vence em ate N dias.
#: Serve para responder "quantos pedidos ainda podem ser tratados antes de
#: vencer" — a unica das perguntas do gate que e' acionavel.
RISCO_DIAS = 1


class Evento(str, Enum):
    """Os dois instantes que competem pelo nome "postado"."""

    DESPACHO = "despacho"
    COLETA = "coleta"


#: Status do pedido que NAO entram em numerador nem denominador. Pedido
#: cancelado nao tem obrigacao de despacho. Medido: incluir ou excluir muda a
#: taxa da janela de 0,03% para 0,03% (imaterial hoje), mas a exclusao e' a
#: leitura correta da regra e evita que um pico de cancelamento mascare atraso.
STATUS_EXCLUIDOS = frozenset({"CANCELLED"})

#: Status em que o pedido ja foi coletado pela transportadora.
STATUS_POS_COLETA = ("IN_TRANSIT", "DELIVERED", "COMPLETED")


# ---------------------------------------------------------------------------
# Feriados
# ---------------------------------------------------------------------------
# `marts.dim_calendario` existe e tem `is_weekend`, mas NAO tem coluna de
# feriado — conferido no DDL da migration 002. Entao a lista nacional vive
# aqui, versionada e testavel, em vez de virar um `WHERE` improvisado no SQL.
# Somente feriados NACIONAIS: feriado municipal muda por loja e nao temos a
# dimensao para isso. Consequencia declarada: em feriado local a operacao
# aparece como atrasada. E' preferivel errar para o lado de acusar atraso do
# que esconder um.
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

#: Ate onde a lista de feriados vai. Data fora disso nao pode ser classificada:
#: `dia_util` levanta em vez de devolver um palpite. Prazo silenciosamente
#: errado e' pior que prazo ausente.
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


def prazo_de(pago_em: date, dias_uteis: int = PRAZO_DIAS_UTEIS) -> date:
    """Data limite de despacho: o n-esimo dia util ESTRITAMENTE apos `pago_em`.

    Pagar num sabado nao consome prazo: a contagem comeca no proximo dia util.
    O vencimento e' o FIM do dia devolvido — um despacho no dia devolvido esta
    no prazo.
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
    """Uma (data de pagamento, marca). Grao da tabela publicada.

    As quatro contagens de cada evento particionam `pedidos_pagos` — sao
    exclusivas e exaustivas, e `validar` prova isso. Nunca some contagens de
    eventos DIFERENTES: um mesmo pedido aparece uma vez em despacho e uma vez
    em coleta.
    """

    paid_date: date
    brand: str
    shop_name: str | None
    deadline_date: date
    pedidos_pagos: int
    cancelados: int
    despacho_no_prazo: int
    despacho_atrasado: int
    despacho_pendente_vencido: int
    despacho_pendente_no_prazo: int
    coleta_no_prazo: int
    coleta_atrasada: int
    coleta_pendente_vencida: int
    coleta_pendente_no_prazo: int

    def madura(self, hoje: date) -> bool:
        """Uma coorte so' e' comparavel depois que o prazo dela venceu.

        Antes disso a taxa ainda pode subir, e mostra-la como numero fechado
        (em especial como `0%`) afirma o que ninguem sabe. E' exatamente o
        caso de "hoje": a coorte de hoje quase sempre tem taxa 0 e quase nunca
        terminara em 0.
        """
        return self.deadline_date < hoje

    def em_risco(self, hoje: date, dias: int = RISCO_DIAS) -> bool:
        """Ainda da' tempo, mas vence ja'. E' a coorte acionavel."""
        return not self.madura(hoje) and (self.deadline_date - hoje).days <= dias

    def atrasados(self, evento: Evento) -> int:
        """Numerador do evento: ja' atrasou + venceu sem acontecer."""
        if evento is Evento.DESPACHO:
            return self.despacho_atrasado + self.despacho_pendente_vencido
        return self.coleta_atrasada + self.coleta_pendente_vencida

    def taxa(self, evento: Evento) -> float | None:
        """Taxa da coorte, ou `None` quando nao ha base.

        `None` e nao `0.0`: coorte sem pedido pago nao tem taxa, e devolver
        zero a faria parecer perfeita numa media.
        """
        if self.pedidos_pagos <= 0:
            return None
        return self.atrasados(evento) / self.pedidos_pagos

    def validar(self) -> None:
        """Fecha o grao. Levanta se as particoes nao somarem o total."""
        if self.pedidos_pagos < 0 or self.cancelados < 0:
            raise ValueError(f"contagem negativa em {self.brand}/{self.paid_date}")
        soma_despacho = (
            self.despacho_no_prazo + self.despacho_atrasado
            + self.despacho_pendente_vencido + self.despacho_pendente_no_prazo
        )
        soma_coleta = (
            self.coleta_no_prazo + self.coleta_atrasada
            + self.coleta_pendente_vencida + self.coleta_pendente_no_prazo
        )
        if soma_despacho != self.pedidos_pagos:
            raise ValueError(
                f"despacho nao fecha em {self.brand}/{self.paid_date}: "
                f"{soma_despacho} != {self.pedidos_pagos}"
            )
        if soma_coleta != self.pedidos_pagos:
            raise ValueError(
                f"coleta nao fecha em {self.brand}/{self.paid_date}: "
                f"{soma_coleta} != {self.pedidos_pagos}"
            )
        if self.deadline_date <= self.paid_date:
            raise ValueError(
                f"prazo {self.deadline_date} nao e' posterior ao pagamento "
                f"{self.paid_date} em {self.brand}"
            )


# ---------------------------------------------------------------------------
# Agregacao de janela
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TaxaJanela:
    """As DUAS leituras da janela, porque a fonte nao distingue qual e' a oficial.

    A gestao descreveu a janela como media das taxas diarias. Medido na janela
    de 2026-09-24, as duas leituras deram 0,03% e diferiram em -0,00 pp: os
    dados atuais NAO discriminam entre elas. Elas divergem quando o volume
    diario varia muito (a media trata um dia de 100 pedidos como um dia de
    10.000), entao publicar so' uma seria escolher sem prova.

    `razao_dos_totais` e' o headline por ser robusta a dia de baixo volume;
    `media_das_diarias` vai junto, rotulada, para reconciliar com a planilha.
    """

    evento: Evento
    razao_dos_totais: float | None
    media_das_diarias: float | None
    coortes_maduras: int
    coortes_parciais: int
    pedidos_pagos: int
    atrasados: int

    @property
    def divergencia_pp(self) -> float | None:
        """Quanto as duas leituras discordam, em pontos percentuais."""
        if self.razao_dos_totais is None or self.media_das_diarias is None:
            return None
        return (self.razao_dos_totais - self.media_das_diarias) * 100.0


def agregar_janela(
    coortes: list[CoorteDiaria], evento: Evento, hoje: date, *, somente_maduras: bool = True
) -> TaxaJanela:
    """Fecha a janela para um evento.

    `somente_maduras=True` por padrao: incluir coortes que ainda nao venceram
    dilui a taxa para baixo com pedidos que ainda tem prazo, o que faz um
    incidente em curso parecer menor do que e'.
    """
    maduras = [c for c in coortes if c.madura(hoje)]
    parciais = [c for c in coortes if not c.madura(hoje)]
    base = maduras if somente_maduras else coortes

    pagos = sum(c.pedidos_pagos for c in base)
    atrasados = sum(c.atrasados(evento) for c in base)
    razao = atrasados / pagos if pagos else None

    # "Media das taxas DIARIAS" e' por DIA, nao por coorte (dia x marca).
    # Tirar a media das coortes cruas da outro numero — medido: 5,24% contra
    # 2,25% na mesma janela — porque cada marca vira um ponto e as marcas
    # pequenas pesam igual as grandes. Como a tela publica as duas leituras
    # justamente para reconciliar com a planilha da gestao, o CLI e a tela
    # precisam calcular a MESMA coisa.
    diarias = [
        taxa for _dia, taxa, _pagos, _mad in serie_por_dia(base, evento, hoje)
        if taxa is not None
    ]
    media = sum(diarias) / len(diarias) if diarias else None

    return TaxaJanela(
        evento=evento,
        razao_dos_totais=razao,
        media_das_diarias=media,
        coortes_maduras=len(maduras),
        coortes_parciais=len(parciais),
        pedidos_pagos=pagos,
        atrasados=atrasados,
    )


def marcas_criticas(
    coortes: list[CoorteDiaria], evento: Evento, hoje: date, *, minimo_pedidos: int = 50
) -> list[tuple[str, float, int]]:
    """Marcas ordenadas por taxa, so' as maduras e com base suficiente.

    `minimo_pedidos` evita o ranking ser liderado por uma marca que teve 3
    pedidos e atrasou 1 — 33% que nao significa nada operacionalmente.
    """
    por_marca: dict[str, list[int]] = {}
    for c in coortes:
        if not c.madura(hoje):
            continue
        acc = por_marca.setdefault(c.brand, [0, 0])
        acc[0] += c.atrasados(evento)
        acc[1] += c.pedidos_pagos
    saida = [
        (marca, atras / pagos, pagos)
        for marca, (atras, pagos) in por_marca.items()
        if pagos >= minimo_pedidos
    ]
    return sorted(saida, key=lambda x: (-x[1], -x[2], x[0]))


def serie_por_dia(
    coortes: list[CoorteDiaria], evento: Evento, hoje: date
) -> list[tuple[date, float | None, int, bool]]:
    """Consolida as marcas: (data, taxa, pedidos_pagos, madura), ordenado.

    A tabela publicada tem grao (dia, marca); quase toda leitura da tela e' no
    dia consolidado. Consolidar aqui, uma vez, evita que cada consumidor
    reimplemente a soma — e evita o erro de somar TAXAS em vez de somar
    numerador e denominador, que e' o jeito errado e o mais tentador.
    """
    acc: dict[date, list] = {}
    for c in coortes:
        a = acc.setdefault(c.paid_date, [0, 0, True])
        a[0] += c.atrasados(evento)
        a[1] += c.pedidos_pagos
        a[2] = a[2] and c.madura(hoje)
    return [
        (d, (atras / pagos if pagos else None), pagos, mad)
        for d, (atras, pagos, mad) in sorted(acc.items())
    ]


def primeiro_dia_do_incidente(
    coortes: list[CoorteDiaria], evento: Evento, hoje: date, *, limiar: float = 0.10
) -> date | None:
    """A data de pagamento em que a taxa CONSOLIDADA cruzou `limiar`.

    Responde "quando o atraso comecou" sem que alguem tenha de ler a tabela
    inteira. Duas decisoes que mudam a resposta:

    - consolida as marcas antes de comparar. Iterar as coortes (dia x marca)
      cruas devolveria o primeiro dia em que QUALQUER marca passou do limiar,
      que e' varios dias antes do dia em que a carteira passou — medido:
      04/09 (uma marca em 14%) contra 06/09 (carteira em 34,9%);
    - so' olha coortes maduras: uma coorte parcial cruzando o limiar ainda
      pode recuar quando o resto dela for coletado.
    """
    for d, taxa, _pagos, madura in serie_por_dia(coortes, evento, hoje):
        if madura and taxa is not None and taxa >= limiar:
            return d
    return None


# ---------------------------------------------------------------------------
# SQL da fonte (Data Mart, read-only)
# ---------------------------------------------------------------------------
# Um unico SELECT, agregado no banco. Nao trazemos linha de pedido para o
# processo: o grao publicado e' (dia, marca) e puxar 200 mil pedidos para
# contar em Python so' aumentaria a janela em que a fonte pode mudar debaixo
# da leitura.
#
# `%(prazos)s` chega como uma lista de tuplas (pago_em, vence_em) ja calculada
# em Python por `prazo_de`. O calendario de dias uteis NAO e' reimplementado em
# SQL de proposito: seria a mesma regra escrita duas vezes, e a versao SQL nao
# teria como levantar `ForaDaCoberturaDeFeriados`.
#
# PERFORMANCE: as duas derivacoes sao agregadas UMA vez, restritas aos pedidos
# da janela, e ligadas por LEFT JOIN. A primeira versao deste SQL usava
# subquery correlacionada por pedido (dois lookups por linha, ~200 mil linhas
# em 22 dias) e nao terminava em 5 minutos contra a replica. Publisher que
# demora minutos segura o lock e amplia a janela em que a fonte muda debaixo
# da leitura.
FONTE_SQL = """
WITH prazo(pago_em, vence_em) AS (
    SELECT (p->>0)::date, (p->>1)::date
    FROM jsonb_array_elements(%(prazos)s::jsonb) p
),
ped AS MATERIALIZED (
    SELECT o.brand, o.order_id, o.shop_name,
           o.paid_at::date AS pago_em,
           o.order_status = ANY(%(excluidos)s) AS excluido
      FROM raw.tiktok_shop_orders o
     WHERE o.paid_at IS NOT NULL
       AND o.paid_at >= %(desde)s::date
       AND o.paid_at <  (%(ate)s::date + 1)
),
-- O join e' por `order_id` SOZINHO, e nao por (brand, order_id): o banco
-- impoe `uk_tiktok_orders UNIQUE (order_id)`, entao `brand` e' funcionalmente
-- dependente e acrescenta-lo so' esconde o indice. As tabelas filhas sao
-- indexadas exatamente assim — `idx_tiktok_items_order (order_id)` e
-- `uk_tiktok_status_log (order_id, new_status)`. Com (brand, order_id) o
-- planner trocava por hash join sobre 3,2 GB de line items e a extracao nao
-- terminava em 5 minutos.
desp AS (
    SELECT li.order_id, min(li.rts_time) AS t
      FROM raw.tiktok_shop_line_items li
      JOIN ped p ON p.order_id = li.order_id
     WHERE li.rts_time IS NOT NULL
     GROUP BY 1
),
-- `detected_at >= desde - 1 dia` nao muda o resultado e troca um Seq Scan de
-- 4,1 milhoes de linhas por uma faixa de `idx_tiktok_status_log_detected`.
-- E' seguro por causalidade: a coleta acontece DEPOIS do pagamento, e
-- `detected_at` e' posterior a coleta. Um pedido pago a partir de `desde` nao
-- pode ter coleta detectada antes disso. O dia de folga cobre o fato de
-- `detected_at` ser UTC enquanto `paid_at` e' UTC-3.
col AS (
    SELECT l.order_id, min(l.updated_at_tiktok) AS t
      FROM raw.tiktok_shop_order_status_log l
      JOIN ped p ON p.order_id = l.order_id
     WHERE l.new_status = ANY(%(pos_coleta)s)
       AND l.updated_at_tiktok IS NOT NULL
       AND l.detected_at >= (%(desde)s::date - 1)
     GROUP BY 1
)
SELECT
    z.pago_em                                                   AS paid_date,
    p.brand,
    max(p.shop_name)                                            AS shop_name,
    z.vence_em                                                  AS deadline_date,
    count(*) FILTER (WHERE NOT excluido)                        AS pedidos_pagos,
    count(*) FILTER (WHERE excluido)                            AS cancelados,
    count(*) FILTER (WHERE NOT excluido AND despachado_em IS NOT NULL
                       AND despachado_em::date <= z.vence_em)   AS despacho_no_prazo,
    count(*) FILTER (WHERE NOT excluido AND despachado_em IS NOT NULL
                       AND despachado_em::date >  z.vence_em)   AS despacho_atrasado,
    count(*) FILTER (WHERE NOT excluido AND despachado_em IS NULL
                       AND z.vence_em <  %(hoje)s::date)        AS despacho_pendente_vencido,
    count(*) FILTER (WHERE NOT excluido AND despachado_em IS NULL
                       AND z.vence_em >= %(hoje)s::date)        AS despacho_pendente_no_prazo,
    count(*) FILTER (WHERE NOT excluido AND coletado_em IS NOT NULL
                       AND coletado_em::date <= z.vence_em)     AS coleta_no_prazo,
    count(*) FILTER (WHERE NOT excluido AND coletado_em IS NOT NULL
                       AND coletado_em::date >  z.vence_em)     AS coleta_atrasada,
    count(*) FILTER (WHERE NOT excluido AND coletado_em IS NULL
                       AND z.vence_em <  %(hoje)s::date)        AS coleta_pendente_vencida,
    count(*) FILTER (WHERE NOT excluido AND coletado_em IS NULL
                       AND z.vence_em >= %(hoje)s::date)        AS coleta_pendente_no_prazo
  FROM (
        SELECT p.brand, p.shop_name, p.pago_em, p.excluido,
               d.t AS despachado_em, c.t AS coletado_em
          FROM ped p
          LEFT JOIN desp d ON d.order_id = p.order_id
          LEFT JOIN col  c ON c.order_id = p.order_id
       ) p
  JOIN prazo z ON z.pago_em = p.pago_em
 GROUP BY 1, 2, 4
 ORDER BY 1, 2
"""

#: Carimbo de frescor da fonte. `extracted_at` e' carimbo da NOSSA ingestao e
#: esta em UTC, ao contrario dos carimbos de negocio.
WATERMARK_SQL = """
SELECT max(extracted_at) AS max_extracted_at
  FROM raw.tiktok_shop_orders
 WHERE paid_at >= %(desde)s::date
"""


def _janela(hoje: date, dias: int) -> tuple[date, date]:
    """A janela fechada [hoje-dias, hoje]. `dias=7` devolve 8 coortes."""
    return (hoje - timedelta(days=dias), hoje)


def prazos_da_janela(hoje: date, dias: int = JANELA_PADRAO_DIAS) -> list[tuple[str, str]]:
    """Os pares (pago_em, vence_em) da janela, em ISO, prontos para o SQL."""
    desde, ate = _janela(hoje, dias)
    saida = []
    d = desde
    while d <= ate:
        saida.append((d.isoformat(), prazo_de(d).isoformat()))
        d += timedelta(days=1)
    return saida


def extrair(conn, hoje: date, dias: int = JANELA_PADRAO_DIAS) -> list[CoorteDiaria]:
    """Le a fonte e devolve as coortes ja validadas. Conexao deve ser read-only."""
    import json  # noqa: PLC0415 - so' aqui, para nao pesar o import do modulo

    desde, ate = _janela(hoje, dias)
    prazos = prazos_da_janela(hoje, dias)
    with conn.cursor() as cur:
        cur.execute(
            FONTE_SQL,
            {
                "prazos": json.dumps(prazos),
                "pos_coleta": list(STATUS_POS_COLETA),
                "excluidos": list(STATUS_EXCLUIDOS),
                "hoje": hoje.isoformat(),
                "desde": desde.isoformat(),
                "ate": ate.isoformat(),
            },
        )
        linhas = [dict(r) for r in cur.fetchall()]

    coortes = []
    for r in linhas:
        c = CoorteDiaria(
            paid_date=r["paid_date"],
            brand=str(r["brand"]),
            shop_name=r["shop_name"],
            deadline_date=r["deadline_date"],
            pedidos_pagos=int(r["pedidos_pagos"]),
            cancelados=int(r["cancelados"]),
            despacho_no_prazo=int(r["despacho_no_prazo"]),
            despacho_atrasado=int(r["despacho_atrasado"]),
            despacho_pendente_vencido=int(r["despacho_pendente_vencido"]),
            despacho_pendente_no_prazo=int(r["despacho_pendente_no_prazo"]),
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
    desde, _ = _janela(hoje, dias)
    with conn.cursor() as cur:
        cur.execute(WATERMARK_SQL, {"desde": desde.isoformat()})
        row = cur.fetchone()
    bruto = (dict(row) if row else {}).get("max_extracted_at")
    if bruto is None:
        return None
    return bruto.replace(tzinfo=timezone.utc) if bruto.tzinfo is None else bruto


# ---------------------------------------------------------------------------
# Publicacao
# ---------------------------------------------------------------------------
COLUNAS = (
    "refresh_batch_id", "channel", "paid_date", "brand", "shop_name",
    "deadline_date", "is_mature", "pedidos_pagos", "cancelados",
    "despacho_no_prazo", "despacho_atrasado", "despacho_pendente_vencido",
    "despacho_pendente_no_prazo", "coleta_no_prazo", "coleta_atrasada",
    "coleta_pendente_vencida", "coleta_pendente_no_prazo",
    "effective_at", "source_watermark_at",
)

#: A janela inteira e' reescrita a cada publicacao, e nao so' os dias novos:
#: uma coorte antiga MUDA quando um pedido dela e' finalmente coletado. Manter
#: a linha velha deixaria a taxa historica congelada num valor que nunca
#: existiu depois. O DELETE e' por (channel, paid_date) dentro da janela.
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
                c.deadline_date, c.madura(hoje), c.pedidos_pagos, c.cancelados,
                c.despacho_no_prazo, c.despacho_atrasado, c.despacho_pendente_vencido,
                c.despacho_pendente_no_prazo, c.coleta_no_prazo, c.coleta_atrasada,
                c.coleta_pendente_vencida, c.coleta_pendente_no_prazo,
                effective_at, watermark,
            )
        )
    return lote, linhas
