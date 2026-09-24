"""Gate FULL-SOURCE-1 Fase 2 — publica a fotografia diaria do estoque FBS da Shopee.

ESTOQUE FULL = SUM(stock_info.shopee_stock[].stock)
----------------------------------------------------
Conciliado contra o "Total Vendavel" do Seller Center em 3 produtos normais
(validacao manual de 2026-09-24), com diferenca de 0,7% a 1,5% explicada pelo
intervalo entre as duas fotografias.

O QUE ESTE PIPELINE NAO FAZ
----------------------------
- NAO usa `summary_info.total_available_stock` como estoque Full: ele nao
  reconcilia com composicao nenhuma em 7 dos 301 produtos FBS (desvio de ate'
  174.906 unidades). Entra apenas como coluna de CONTEXTO.
- NAO usa `seller_stock` (deposito do vendedor) nem `reserved_stock` (bloqueio
  por pedido) como estoque Full.
- NAO soma `advance_stock`. Ele e' um OBJETO com `sellable_advance_stock` e
  `in_transit_advance_stock`: estoque do programa de reposicao antecipada,
  incluindo o que ainda esta' A CAMINHO do CD. Somar `in_transit` ao vendavel
  contaria unidade que nao da' para vender hoje, e foi exatamente o "Total
  Vendavel" da tela que conciliamos -- nao um total logistico. Medido em
  2026-09-24: os dois campos valem 0 em todos os 301 produtos FBS, entao incluir
  nao mudaria numero nenhum HOJE; ficam de fora pela semantica, nao pelo valor.
- NAO classifica KIT: o unico kit levado a' validacao nao foi localizado no
  Seller Center, entao a semantica segue nao conciliada. Kit sai com
  `KIT_NAO_CONCILIADO` e fora de qualquer contagem operacional.
- NAO toca `marts.fact_shopee_fbs_daily`: aquela fato mede modalidade do
  PEDIDO, esta mede estoque parado no CD. Universos diferentes.
- NAO le coluna de comprador.

POR QUE UMA FOTOGRAFIA DIARIA
------------------------------
`raw.shopee_products` e' SOBRESCRITA a cada ingestao -- nao ha historico. Cada
execucao grava o estado do dia. Reexecutar no mesmo dia SOBRESCREVE a
fotografia daquele dia (DELETE + INSERT do `ref_date`), nunca duplica.
Consequencia honesta: nao existe backfill. A serie comeca na primeira execucao.
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from psycopg2.extras import execute_values
from sqlalchemy import text

from pipelines.common.db import DataMartSession, LocalSession
from pipelines.common.logging import get_logger

logger = get_logger(__name__)

FACT_LOCATION = "marts.fact_shopee_fbs_stock_location_daily"
FACT_PRODUTO = "marts.fact_shopee_fbs_stock_daily"

#: Fuso operacional da Torre.
OPERATIONAL_TZ = timezone(timedelta(hours=-3))

#: Contas da esteira API da Shopee. Kokeshi NAO esta aqui.
CONTAS_ESPERADAS = ("apice", "barbours", "lescent", "rituaria")

# --------------------------------------------------------------------------- #
# POPULACAO DA DEMANDA                                                         #
# --------------------------------------------------------------------------- #
# Lista EXPLICITA, nao "tudo menos cancelled". Status desconhecido BLOQUEIA a
# carga: um valor novo que entrasse em silencio mudaria a demanda -- e a
# cobertura -- sem ninguem ver.
#
# 🔑 RECONCILIADO com a definicao VIGENTE da Torre para a Shopee, em
# `marts.fact_shopee_fbs_daily` / migration 019: "unico criterio de exclusao:
# order_status = 'cancelled'; to_return e unpaid PERMANECEM, por decisao de
# comparabilidade". Uso a MESMA populacao de proposito -- se eu inventasse aqui
# uma regra diferente, as duas fatos da Torre deixariam de conversar sobre o que
# e' uma venda da Shopee.
#
# CONSEQUENCIA MEDIDA (janela de 28 dias encerrada em 2026-09-24): `to_return`
# responde por 0,67% das unidades e `unpaid` por 0,59%. Juntos inflam a demanda
# -- e portanto REDUZEM a cobertura -- em ~1,3%. E' vies conhecido e aceito, nao
# descuido: mudar isso e' mudar a definicao de venda da Torre inteira, nao desta
# fato.
#: ⚙️ DEMANDA OPERACIONAL -- a unica que alimenta media diaria, cobertura e as
#: sete classificacoes. Allowlist explicita: status fora daqui nao entra nunca.
#:
#: MEDIDO em 180 dias (2026-09-24), sobre as 4 contas: TODOS estes status tem
#: `pay_time` preenchido em 100% dos pedidos --
#:     completed 164.406/164.406 · shipped 2.318/2.318 ·
#:     to_confirm_receive 2.246/2.246 · processed 336/336 ·
#:     to_return 206/206 · ready_to_ship 9/9
#: O contraste com `unpaid` (0 de 159) e' BINARIO, nao inferido.
STATUS_DEMANDA_OPERACIONAL = (
    "completed", "shipped", "to_confirm_receive", "processed",
    "ready_to_ship", "to_return",
)

#: `to_return` PERMANECE na demanda operacional, e a condicao imposta pelo gate
#: esta' satisfeita e medida: 206/206 pedidos (100%) passaram por `pay_time`.
#: A unidade FOI vendida e DEIXOU o estoque -- a devolucao pode reentrar depois,
#: mas no instante da fotografia ela nao esta' la' para ser vendida. Tratar
#: devolucao como nao-demanda subestimaria a velocidade e inflaria a cobertura.
STATUS_COM_PAGAMENTO_COMPROVADO = STATUS_DEMANDA_OPERACIONAL

#: 🚫 FORA da demanda operacional, por motivos diferentes:
#:  - `unpaid`: 0 de 159 pedidos com `pay_time` em 180 dias. Nunca foi pago,
#:    nunca consumiu estoque. Somar isto a' velocidade faria a cobertura parecer
#:    MENOR do que e' e dispararia alerta de ruptura em produto que nao vendeu.
#:  - `cancelled`: venda desfeita.
#: 🔑 Esta e' uma DIVERGENCIA DELIBERADA de `marts.fact_shopee_fbs_daily`
#: (migration 019), que mantem `unpaid` no GMV bruto por comparabilidade
#: historica. Comparabilidade com uma fato de VALOR nao e' motivo para herdar a
#: distorcao numa fonte OPERACIONAL de reposicao: os dois numeros respondem a
#: perguntas diferentes. A demanda no criterio antigo continua publicada como
#: `units_sold_28d_legado_com_unpaid`, para contexto -- e NAO classifica nada.
STATUS_FORA_DA_DEMANDA = ("unpaid", "cancelled")

#: Criterio ANTIGO (migration 019), publicado apenas como contexto.
STATUS_DEMANDA_LEGADA = STATUS_DEMANDA_OPERACIONAL + ("unpaid",)

STATUS_CONHECIDOS = tuple(sorted(STATUS_DEMANDA_OPERACIONAL + STATUS_FORA_DA_DEMANDA))

# --------------------------------------------------------------------------- #
# PARAMETROS DA COBERTURA                                                      #
# --------------------------------------------------------------------------- #
#: Janela de demanda, em dias COMPLETOS. Intervalo [ref_date - N, ref_date):
#: o dia corrente NAO entra, porque ele esta' pela metade e faria a demanda
#: parecer menor do que e' -- inflando a cobertura justo nos itens que mais
#: vendem. REUTILIZADA de `gold.tiktok_inventory_daily` (`dias_janela_venda`).
JANELA_VENDAS_DIAS = 28

#: 🔴 PROVISORIO. Limite de cobertura baixa. Veio de
#: `gold.tiktok_inventory_daily` (`dias_cobertura_baixa = 7`), que o justifica
#: assim: o limite de alerta do painel nao e' exposto pela API, e um numero fixo
#: de UNIDADES significa coisas opostas num SKU que vende 200/dia e num que vende
#: 1/semana. Herdar o parametro do TikTok NAO o valida para a Shopee: nenhum
#: dono de numero ratificou este 7 para este canal.
COBERTURA_BAIXA_DIAS_PROVISORIO = 7

#: 🔴 PROVISORIO. Limite de excesso. NAO tem origem documentada em lugar nenhum
#: -- nem na Shopee, nem no modelo do TikTok, que simplesmente nao classifica
#: excesso. E' ponto de partida da Torre, exposto para ser discutido e nao para
#: ser tratado como verdade.
COBERTURA_EXCESSO_DIAS_PROVISORIO = 90

#: Os dois limites acima sao provisorios: viajam juntos para o log e para o
#: resumo do run, para que nenhum consumidor os leia como ratificados.
LIMITES_PROVISORIOS = {
    "cobertura_baixa_dias_PROVISORIO": COBERTURA_BAIXA_DIAS_PROVISORIO,
    "cobertura_excesso_dias_PROVISORIO": COBERTURA_EXCESSO_DIAS_PROVISORIO,
}

STAGING_LOCATION = "stg_fsfsl"
STAGING_PRODUTO = "stg_fsfs"
STAGING_PAGE_SIZE = 500
TARGET_STATEMENT_TIMEOUT_MS = 180_000

ADVISORY_LOCK_KEY = 916140020
AUDIT_MARKETPLACE_ID = 3
AUDIT_SOURCE = "shopee_fbs_stock_daily"


class ShopeeStockSyncError(RuntimeError):
    """Erro de contrato: bloqueia a publicacao antes de qualquer escrita."""


class ConcurrentRunError(ShopeeStockSyncError):
    """Outra execucao ja' detem o advisory lock."""


_PADROES_PII = (
    re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
)


def sanitizar(exc: BaseException | str) -> str:
    txt = str(exc)
    for p in _PADROES_PII:
        txt = p.sub("<removido>", txt)
    return txt


def _hoje_brt(agora: datetime | None = None) -> date:
    return (agora or datetime.now(timezone.utc)).astimezone(OPERATIONAL_TZ).date()


@dataclass(frozen=True)
class LocationRow:
    ref_date: date
    brand: str
    shop_account: str
    item_id: str
    model_id: str
    location_id: str
    full_stock: int
    is_saleable: bool | None
    is_kit: bool
    item_status: str | None


@dataclass(frozen=True)
class ProdutoRow:
    ref_date: date
    brand: str
    shop_account: str
    item_id: str
    model_id: str
    item_name: str | None
    item_sku: str | None
    item_status: str | None
    is_kit: bool
    full_stock_saleable: int
    full_stock_total: int
    location_count: int
    seller_stock_total: int | None
    reserved_stock: int | None
    summary_available_stock: int | None
    units_sold_28d: int
    days_with_sales_28d: int
    units_sold_28d_legado_com_unpaid: int
    avg_daily_units_28d: Decimal
    cobertura_torre_dias: Decimal | None
    classificacao_torre: str
    vinculo_vendas: str


@dataclass(frozen=True)
class Snapshot:
    ref_date: date
    locations: list[LocationRow]
    produtos: list[ProdutoRow]
    captured_at: datetime
    avisos: list[str]


# --------------------------------------------------------------------------- #
# FONTE                                                                        #
# --------------------------------------------------------------------------- #
#: Estoque por CD. `jsonb_array_elements` expande shopee_stock[] -- e' a UNICA
#: expansao do pipeline, e ela acontece ANTES de qualquer join com vendas.
SQL_LOCATIONS = text("""
    SELECT
        s.brand,
        s.shop_account,
        s.item_id::text                         AS item_id,
        '0'::text                               AS model_id,
        e->>'location_id'                       AS location_id,
        COALESCE((e->>'stock')::bigint, 0)      AS full_stock,
        (e->>'if_saleable')::boolean            AS is_saleable,
        COALESCE(s.is_kit, FALSE)               AS is_kit,
        s.item_status,
        r.ingested_at                           AS captured_at
      FROM raw.shopee_products r
      JOIN silver.stg_shopee_products s
        ON s.shop_account = r.shop_account AND s.item_id = r.item_id
      CROSS JOIN LATERAL jsonb_array_elements(r.stock_info->'shopee_stock') e
     WHERE s.is_fulfillment_by_shopee
       AND r.stock_info IS NOT NULL
       AND s.shop_account = ANY(:contas)
""")

#: Contexto por produto + demanda. A demanda e' PRE-AGREGADA em `vendas` ANTES
#: do LEFT JOIN: sem isso, um produto com N linhas de pedido multiplicaria o
#: estoque. Mesma armadilha ja' evitada em sync_shopee_fbs_daily.
SQL_PRODUTOS = text("""
    WITH vendas AS (
        SELECT i.shop_account,
               i.item_id::text                              AS item_id,
               -- Demanda OPERACIONAL: so' status com pagamento comprovado.
               SUM(i.quantity) FILTER (
                   WHERE o.order_status = ANY(:status_operacional)
               )::bigint                                    AS units_sold,
               COUNT(DISTINCT o.created_date_brt) FILTER (
                   WHERE o.order_status = ANY(:status_operacional)
               )::int                                       AS days_with_sales,
               -- Criterio ANTIGO (019, com unpaid). CONTEXTO: nao classifica.
               SUM(i.quantity) FILTER (
                   WHERE o.order_status = ANY(:status_legado)
               )::bigint                                    AS units_sold_legado
          FROM silver.stg_shopee_order_items i
          JOIN silver.stg_shopee_orders o
            ON o.shop_account = i.shop_account AND o.order_sn = i.order_sn
         -- Janela de dias COMPLETOS: [inicio, ref_date). O dia corrente fica
         -- FORA porque esta' pela metade -- incluir um dia parcial subestima a
         -- demanda e infla a cobertura justo nos itens de maior giro.
         WHERE o.created_date_brt >= :janela_inicio
           AND o.created_date_brt <  :janela_fim
           AND o.order_status = ANY(:status_legado)
           AND o.shop_account = ANY(:contas)
         GROUP BY 1, 2
    )
    SELECT
        s.brand,
        s.shop_account,
        s.item_id::text                         AS item_id,
        '0'::text                               AS model_id,
        s.item_name,
        s.item_sku,
        s.item_status,
        COALESCE(s.is_kit, FALSE)               AS is_kit,
        COALESCE((SELECT SUM((e->>'stock')::bigint)
                    FROM jsonb_array_elements(r.stock_info->'shopee_stock') e
                   WHERE (e->>'if_saleable')::boolean IS TRUE), 0)   AS full_saleable,
        COALESCE((SELECT SUM((e->>'stock')::bigint)
                    FROM jsonb_array_elements(r.stock_info->'shopee_stock') e), 0)
                                                                     AS full_total,
        COALESCE((SELECT COUNT(*)
                    FROM jsonb_array_elements(r.stock_info->'shopee_stock') e), 0)::int
                                                                     AS location_count,
        (SELECT SUM((e->>'stock')::bigint)
           FROM jsonb_array_elements(r.stock_info->'seller_stock') e) AS seller_total,
        (r.stock_info->'summary_info'->>'total_reserved_stock')::bigint  AS reserved,
        (r.stock_info->'summary_info'->>'total_available_stock')::bigint AS summary_disp,
        COALESCE(v.units_sold, 0)               AS units_sold,
        COALESCE(v.days_with_sales, 0)          AS days_with_sales,
        COALESCE(v.units_sold_legado, 0)        AS units_sold_legado,
        (v.item_id IS NOT NULL)                 AS tem_venda,
        r.ingested_at                           AS captured_at
      FROM raw.shopee_products r
      JOIN silver.stg_shopee_products s
        ON s.shop_account = r.shop_account AND s.item_id = r.item_id
      LEFT JOIN vendas v
        ON v.shop_account = s.shop_account AND v.item_id = s.item_id::text
     WHERE s.is_fulfillment_by_shopee
       AND r.stock_info IS NOT NULL
       AND s.shop_account = ANY(:contas)
""")


#: Guarda de contrato: status fora do dominio conhecido BLOQUEIA a carga. Sem
#: isto, um status novo da Shopee entraria em silencio -- ou como demanda que
#: nao existe, ou como demanda que some -- e a cobertura mudaria sem aviso.
SQL_STATUS_DESCONHECIDOS = text("""
    SELECT DISTINCT o.order_status
      FROM silver.stg_shopee_orders o
     WHERE o.created_date_brt >= :janela_inicio
       AND o.created_date_brt <  :janela_fim
       AND o.shop_account = ANY(:contas)
       AND (o.order_status IS NULL OR NOT (o.order_status = ANY(:conhecidos)))
""")


def classificar(is_kit: bool, saleable: int, units: int,
                cobertura: Decimal | None) -> str:
    """Classificacao da Torre. A ORDEM importa e e' deliberada.

    Kit vem primeiro porque sua semantica nao foi conciliada -- nenhuma outra
    regra pode se aplicar a ele. Ruptura exige demanda: estoque zero em produto
    que ninguem compra nao e' ruptura, e' catalogo morto.
    """
    if is_kit:
        return "KIT_NAO_CONCILIADO"
    if units == 0:
        # Sem demanda medida nao ha' cobertura: separar "parado com estoque" de
        # "sem estoque e sem procura" evita alerta falso nos dois sentidos.
        return "SEM_GIRO_CANDIDATO" if saleable > 0 else "SEM_DEMANDA_MEDIDA"
    if saleable == 0:
        return "RUPTURA_CANDIDATA"
    if cobertura is not None and cobertura < COBERTURA_BAIXA_DIAS_PROVISORIO:
        return "BAIXO_CANDIDATO"
    if cobertura is not None and cobertura >= COBERTURA_EXCESSO_DIAS_PROVISORIO:
        return "EXCESSO_CANDIDATO"
    return "SUFICIENTE"


def janela_demanda(ref_date: date) -> tuple[date, date]:
    """[ref_date - N, ref_date): N dias COMPLETOS, sem o dia corrente."""
    return ref_date - timedelta(days=JANELA_VENDAS_DIAS), ref_date


def read_source(conn, ref_date: date) -> Snapshot:
    inicio, fim = janela_demanda(ref_date)
    params = {"contas": list(CONTAS_ESPERADAS)}
    janela = {"janela_inicio": inicio, "janela_fim": fim}

    # Fail-closed ANTES de ler a demanda: status novo bloqueia a carga.
    novos = [r[0] for r in conn.execute(
        SQL_STATUS_DESCONHECIDOS,
        {**params, **janela, "conhecidos": list(STATUS_CONHECIDOS)}).all()]
    if novos:
        raise ShopeeStockSyncError(
            f"order_status desconhecido na janela: {sorted(set(map(str, novos)))}. "
            f"Conhecidos: {list(STATUS_CONHECIDOS)}. A carga para em vez de "
            "decidir sozinha se o status novo e' demanda."
        )

    locs = conn.execute(SQL_LOCATIONS, params).mappings().all()
    prods = conn.execute(SQL_PRODUTOS, {
        **params, **janela,
        "status_operacional": list(STATUS_DEMANDA_OPERACIONAL),
        "status_legado": list(STATUS_DEMANDA_LEGADA)}).mappings().all()

    if not prods:
        raise ShopeeStockSyncError(
            "fonte devolveu zero produtos FBS com stock_info -- recusado antes "
            "de qualquer escrita"
        )

    captured = max(p["captured_at"] for p in prods)
    avisos = [
        "Estoque Full = SUM(shopee_stock[].stock). Conciliado com o 'Total "
        "Vendavel' do Seller Center em 3 produtos normais (0,7%-1,5% de "
        "diferenca, explicada pelo intervalo entre fotografias).",
        "summary_available_stock, seller_stock_total e reserved_stock sao "
        "CONTEXTO: nenhum deles representa estoque Full.",
        f"cobertura_torre_dias e' calculo da Torre sobre {JANELA_VENDAS_DIAS} "
        f"dias COMPLETOS [{inicio} .. {fim}), sem o dia corrente. NAO reproduz "
        "formula da Shopee.",
        f"LIMITES PROVISORIOS, nao ratificados para a Shopee: baixo < "
        f"{COBERTURA_BAIXA_DIAS_PROVISORIO}d (herdado do TikTok) e excesso >= "
        f"{COBERTURA_EXCESSO_DIAS_PROVISORIO}d (sem origem documentada).",
        "KIT sai como KIT_NAO_CONCILIADO e fora de qualquer contagem "
        "operacional: semantica de estoque de kit nao validada. O total geral "
        "de estoque INCLUI kits -- use full_stock_saleable_operacional.",
        f"Demanda OPERACIONAL = {list(STATUS_DEMANDA_OPERACIONAL)} -- todos com "
        "pay_time em 100% dos pedidos (medido em 180 dias). FORA: "
        f"{list(STATUS_FORA_DA_DEMANDA)}.",
        "unpaid NAO entra na demanda operacional: 0 de 159 pedidos com pay_time "
        "em 180 dias. DIVERGE de marts.fact_shopee_fbs_daily de proposito -- a "
        "demanda no criterio antigo fica em units_sold_28d_legado_com_unpaid, "
        "como CONTEXTO, e nao classifica nada.",
        "to_return PERMANECE na demanda: 206/206 pedidos passaram por pay_time; "
        "a unidade foi vendida e deixou o estoque.",
        "advance_stock NAO compoe o vendavel: inclui in_transit, que nao da' "
        "para vender hoje. Medido zero em 2026-09-24 nos 301 produtos.",
    ]

    contas_vistas = {p["shop_account"] for p in prods}
    ausentes = sorted(set(CONTAS_ESPERADAS) - contas_vistas)
    if ausentes:
        avisos.append(f"contas SEM produto FBS nesta fotografia: {ausentes}")

    location_rows = [
        LocationRow(
            ref_date=ref_date, brand=r["brand"], shop_account=r["shop_account"],
            item_id=r["item_id"], model_id=r["model_id"],
            location_id=r["location_id"], full_stock=int(r["full_stock"]),
            is_saleable=r["is_saleable"], is_kit=bool(r["is_kit"]),
            item_status=r["item_status"],
        ) for r in locs
    ]

    produto_rows = []
    for r in prods:
        units = int(r["units_sold"])
        saleable = int(r["full_saleable"])
        total = int(r["full_total"])
        media = (Decimal(units) / Decimal(JANELA_VENDAS_DIAS)) if units else Decimal(0)
        cobertura = (Decimal(saleable) / media) if units else None
        # Dominio de DOIS valores: todo produto desta fato nasce do catalogo
        # (`stg_shopee_products`), entao "fora do catalogo de vendas" era
        # inalcancavel por construcao e foi removido -- estado que nao pode
        # existir nao deve figurar no contrato.
        vinculo = "COM_VENDA" if r["tem_venda"] else "SEM_VENDA_NA_JANELA"
        produto_rows.append(ProdutoRow(
            ref_date=ref_date, brand=r["brand"], shop_account=r["shop_account"],
            item_id=r["item_id"], model_id=r["model_id"],
            item_name=r["item_name"], item_sku=r["item_sku"],
            item_status=r["item_status"], is_kit=bool(r["is_kit"]),
            full_stock_saleable=saleable, full_stock_total=total,
            location_count=int(r["location_count"]),
            seller_stock_total=r["seller_total"], reserved_stock=r["reserved"],
            summary_available_stock=r["summary_disp"],
            units_sold_28d=units, days_with_sales_28d=int(r["days_with_sales"]),
            units_sold_28d_legado_com_unpaid=int(r["units_sold_legado"]),
            avg_daily_units_28d=media, cobertura_torre_dias=cobertura,
            classificacao_torre=classificar(bool(r["is_kit"]), saleable, units, cobertura),
            vinculo_vendas=vinculo,
        ))

    return Snapshot(ref_date=ref_date, locations=location_rows,
                    produtos=produto_rows, captured_at=captured, avisos=avisos)


def validate_contract(snap: Snapshot) -> list[str]:
    """Invariantes que tem de valer ANTES de escrever. Falha alto."""
    erros = []

    chaves_loc = {(r.shop_account, r.item_id, r.model_id, r.location_id)
                  for r in snap.locations}
    if len(chaves_loc) != len(snap.locations):
        erros.append("location: chave duplicada no mesmo ref_date")

    chaves_prod = {(r.shop_account, r.item_id, r.model_id) for r in snap.produtos}
    if len(chaves_prod) != len(snap.produtos):
        erros.append("produto: chave duplicada no mesmo ref_date")

    for r in snap.produtos:
        if r.full_stock_saleable > r.full_stock_total:
            erros.append(f"vendavel > total em {r.shop_account}/{r.item_id}")
        if (r.cobertura_torre_dias is None) != (r.units_sold_28d == 0):
            erros.append(f"cobertura incoerente com demanda em {r.shop_account}")
        if r.is_kit and r.classificacao_torre != "KIT_NAO_CONCILIADO":
            erros.append(f"kit classificado como {r.classificacao_torre}")
        if r.cobertura_torre_dias is not None and not r.cobertura_torre_dias.is_finite():
            erros.append("cobertura nao finita")

    # A soma do grao fino tem de reproduzir o agregado, produto a produto.
    por_produto: dict[tuple, int] = {}
    for r in snap.locations:
        if r.is_saleable is True:
            k = (r.shop_account, r.item_id, r.model_id)
            por_produto[k] = por_produto.get(k, 0) + r.full_stock
    for r in snap.produtos:
        k = (r.shop_account, r.item_id, r.model_id)
        if por_produto.get(k, 0) != r.full_stock_saleable:
            erros.append(
                f"agregado nao reproduz o grao fino em {r.shop_account}: "
                f"{por_produto.get(k, 0)} vs {r.full_stock_saleable}"
            )
    if erros:
        raise ShopeeStockSyncError("; ".join(sorted(set(erros))[:5]))
    return snap.avisos


# --------------------------------------------------------------------------- #
# PUBLICACAO                                                                   #
# --------------------------------------------------------------------------- #
_COLS_LOC = ("ref_date", "brand", "shop_account", "item_id", "model_id",
             "location_id", "full_stock", "is_saleable", "is_kit",
             "item_status", "source_run_id", "source_captured_at")
_COLS_PROD = ("ref_date", "brand", "shop_account", "item_id", "model_id",
              "item_name", "item_sku", "item_status", "is_kit",
              "full_stock_saleable", "full_stock_total", "location_count",
              "seller_stock_total", "reserved_stock", "summary_available_stock",
              "units_sold_28d", "days_with_sales_28d",
              "units_sold_28d_legado_com_unpaid", "avg_daily_units_28d",
              "cobertura_torre_dias", "classificacao_torre", "vinculo_vendas",
              "source_run_id", "source_captured_at")


def _inserir(conn, tabela: str, cols: tuple, linhas: list[tuple]) -> None:
    raw = conn.connection
    with raw.cursor() as cur:
        execute_values(
            cur,
            f"INSERT INTO {tabela} ({', '.join(cols)}) VALUES %s",
            linhas, page_size=STAGING_PAGE_SIZE,
        )


def publish_in_transaction(conn, snap: Snapshot, run_id: str) -> dict:
    """DELETE do ref_date + INSERT nas duas fatos. UMA transacao.

    Reexecutar no mesmo dia sobrescreve a fotografia daquele dia. Nunca duplica
    e nunca toca outro ref_date.
    """
    conn.execute(text(f"SET LOCAL statement_timeout = {TARGET_STATEMENT_TIMEOUT_MS}"))
    p = {"ref_date": snap.ref_date}

    conn.execute(text(f"DELETE FROM {FACT_LOCATION} WHERE ref_date = :ref_date"), p)
    conn.execute(text(f"DELETE FROM {FACT_PRODUTO} WHERE ref_date = :ref_date"), p)

    _inserir(conn, FACT_LOCATION, _COLS_LOC, [(
        r.ref_date, r.brand, r.shop_account, r.item_id, r.model_id,
        r.location_id, r.full_stock, r.is_saleable, r.is_kit, r.item_status,
        run_id, snap.captured_at,
    ) for r in snap.locations])

    _inserir(conn, FACT_PRODUTO, _COLS_PROD, [(
        r.ref_date, r.brand, r.shop_account, r.item_id, r.model_id,
        r.item_name, r.item_sku, r.item_status, r.is_kit,
        r.full_stock_saleable, r.full_stock_total, r.location_count,
        r.seller_stock_total, r.reserved_stock, r.summary_available_stock,
        r.units_sold_28d, r.days_with_sales_28d,
        r.units_sold_28d_legado_com_unpaid, r.avg_daily_units_28d,
        r.cobertura_torre_dias, r.classificacao_torre, r.vinculo_vendas,
        run_id, snap.captured_at,
    ) for r in snap.produtos])

    # Reconciliacao PRE-COMMIT: o que esta' no destino tem de ser exatamente o
    # que foi lido. Diferenca em qualquer direcao aborta a transacao.
    destino = conn.execute(text(f"""
        SELECT COUNT(*) AS n, COALESCE(SUM(full_stock_saleable), 0) AS soma
          FROM {FACT_PRODUTO} WHERE ref_date = :ref_date"""), p).mappings().one()
    esperado_n = len(snap.produtos)
    esperado_soma = sum(r.full_stock_saleable for r in snap.produtos)
    if destino["n"] != esperado_n or int(destino["soma"]) != esperado_soma:
        raise ShopeeStockSyncError(
            f"reconciliacao pre-commit falhou: destino n={destino['n']} "
            f"soma={destino['soma']}; esperado n={esperado_n} soma={esperado_soma}"
        )
    return {"locations_publicadas": len(snap.locations),
            "produtos_publicados": esperado_n,
            "full_stock_saleable_publicado": esperado_soma}


def _conn_lock():
    engine = LocalSession().get_bind()
    return engine.connect().execution_options(isolation_level="AUTOCOMMIT")


def run(apply: bool = False, agora: datetime | None = None) -> dict:
    ref_date = _hoje_brt(agora)
    run_id = uuid.uuid4().hex

    dm = DataMartSession()
    try:
        dm.connection().connection.set_session(readonly=True)
        snap = read_source(dm.connection(), ref_date)
    finally:
        dm.rollback()
        dm.close()

    avisos = validate_contract(snap)
    inicio, fim = janela_demanda(ref_date)

    resumo = {
        "ref_date": str(ref_date),
        "run_id": run_id,
        "applied": False,
        "locations": len(snap.locations),
        "produtos": len(snap.produtos),
        "source_captured_at": str(snap.captured_at),
        # ---------------------------------------------------------------- #
        # O total GERAL inclui kits. Quem opera reposicao precisa do de baixo:
        # apresentar o geral como "estoque operacional" esconde unidades cuja
        # semantica nao foi conciliada.
        # ---------------------------------------------------------------- #
        "full_stock_saleable_total": sum(r.full_stock_saleable for r in snap.produtos),
        "full_stock_saleable_operacional": sum(
            r.full_stock_saleable for r in snap.produtos if not r.is_kit),
        "full_stock_saleable_kits_contexto": sum(
            r.full_stock_saleable for r in snap.produtos if r.is_kit),
        "produtos_operacionais": sum(1 for r in snap.produtos if not r.is_kit),
        "produtos_kit_contexto": sum(1 for r in snap.produtos if r.is_kit),
        "full_stock_total": sum(r.full_stock_total for r in snap.produtos),
        "por_classificacao": {},
        "warnings": avisos,
        "janela_demanda": {
            "inicio_inclusivo": str(inicio),
            "fim_exclusivo": str(fim),
            "dias_completos": JANELA_VENDAS_DIAS,
            "dia_corrente_incluido": False,
        },
        "demanda_operacional_unidades": sum(r.units_sold_28d for r in snap.produtos),
        "demanda_legada_com_unpaid_unidades": sum(
            r.units_sold_28d_legado_com_unpaid for r in snap.produtos),
        "produtos_afetados_por_unpaid": sum(
            1 for r in snap.produtos
            if r.units_sold_28d_legado_com_unpaid != r.units_sold_28d),
        "status_demanda_operacional": list(STATUS_DEMANDA_OPERACIONAL),
        "status_fora_da_demanda": list(STATUS_FORA_DA_DEMANDA),
        "limites_provisorios": dict(LIMITES_PROVISORIOS),
    }
    for r in snap.produtos:
        resumo["por_classificacao"][r.classificacao_torre] = \
            resumo["por_classificacao"].get(r.classificacao_torre, 0) + 1

    if not apply:
        return resumo

    conn_lock = _conn_lock()
    try:
        obtido = conn_lock.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}
        ).scalar()
        if not obtido:
            raise ConcurrentRunError(
                f"advisory lock {ADVISORY_LOCK_KEY} ocupado: outra execucao em curso"
            )
        session = LocalSession()
        try:
            with session.connection().begin():
                publicado = publish_in_transaction(session.connection(), snap, run_id)
            resumo.update(publicado)
            resumo["applied"] = True
        finally:
            session.close()
    finally:
        try:
            conn_lock.execute(text("SELECT pg_advisory_unlock(:k)"),
                              {"k": ADVISORY_LOCK_KEY})
        finally:
            conn_lock.close()
    return resumo


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Publica a fotografia diaria do estoque FBS da Shopee")
    p.add_argument("--apply", action="store_true",
                   help="publica. Sem esta flag o run e' dry-run.")
    args = p.parse_args(argv)
    try:
        res = run(apply=args.apply)
    except ShopeeStockSyncError as exc:
        logger.error("%s", sanitizar(exc))
        return 1
    print(json.dumps(res, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
