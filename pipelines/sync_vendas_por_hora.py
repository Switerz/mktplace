"""VENDAS-POR-HORA-1 — vendas por HORA (BRT) de ML, TikTok e Shopee, no Neon.

POR QUE EXISTE
--------------
A fato diaria (`marts.fact_marketplace_daily_performance`) guarda UM numero por
dia, e a DAG do dia corrente o reescreve de hora em hora: o "ate' 12h" se perde
quando chega o "ate' 13h". E cada canal entra com um corte diferente (ML ~:30,
TikTok ~:35, Shopee ~:15). O "Hoje" da Gerencial precisa de um corte UNICO —
os tres canais ate' a mesma hora cheia — e de "ontem ate' a mesma hora" para
comparar. Esta tabela guarda as vendas por hora; a API soma ate' o corte.

O QUE MEDE — a MESMA regra de cada canal na fato diaria
------------------------------------------------------
Por (dia BRT, hora BRT, canal, loja), pedido a pedido, no Data Mart:

  ML      `api.ml_orders`, `status = 'paid'`, GMV = `total_amount`. A regra de
          `connectors/mercadolivre` (o `paid_gmv` que reconcilia com a Gold).
  TikTok  `raw.tiktok_shop_orders` deduplicada por `order_id` (ultima versao),
          status da allowlist comercial, GMV = `total_amount`. A regra de
          `connectors/tiktok`.
  Shopee  `silver.stg_shopee_orders` + `_order_items`, status <> cancelled,
          GMV = soma de `item_total`. A regra de `sync_shopee_daily_api`, so'
          nas lojas com API.

Medido em 03/11/2026 (26/10 a 02/11): a soma por dia destas regras bate com a
fato publicada — Shopee e TikTok exatos; ML exato de 30/10 em diante (antes,
ate' 4 pedidos de status mudado depois da ultima publicacao).

🔴 O RELOGIO DO ML E' UTC-4
---------------------------
`api.ml_orders.date_created` e' `timestamp without time zone` no fuso do
Mercado Livre (-04:00), UMA HORA atras de Brasilia: em 03/11/2026 as 15:19 BRT
o ultimo pedido tinha `date_created` 13:52 e `extracted_at` (UTC) 17:53, i.e.
14:53 BRT. Aqui o ML soma `+ 1 hour` antes de tirar dia e hora. A Gold diaria
NAO faz isso (corta o dia a meia-noite de -04:00, 01:00 BRT): o total do dia
do ML aqui difere da fato pelos pedidos da faixa 00:00-00:59 BRT. TikTok
(`created_at`) ja' esta' em BRT; a Shopee (`create_time`) tem fuso.

A KOKESHI NA SHOPEE NAO ENTRA
-----------------------------
Ela chega por arquivo exportado, sem hora de pedido. A API mostra a ausencia;
nao vira zero.

ATE' ONDE A FONTE CHEGOU (`..._fonte`)
--------------------------------------
Hora sem pedido nao tem linha, entao ausencia de linha NAO prova que a hora
fechou. Por canal, grava-se `fonte_ate`: ate' quando a fonte comprovadamente
tem dado. ML e TikTok: o pedido mais recente (fontes de 15 e 5 min). Shopee: a
ultima carga do Data Mart menos `FOLGA_SHOPEE` (a DAG de pedidos traz o que foi
criado ate' o inicio dela, e a carga termina minutos depois). So' sao gravadas
as horas inteiramente anteriores a `fonte_ate`; o corte padronizado da API e' o
MENOR `fonte_ate` dos canais do filtro, arredondado para baixo na hora.

JANELA
------
Hoje e os `DIAS_JANELA - 1` dias anteriores, regravados a cada execucao: o
status amadurece (cancelamento) e "ontem" e "semana passada" sao lidos daqui.
A gravacao e' DELETE + INSERT da janela, numa transacao so', com advisory lock.

USO
---
    python -m pipelines.sync_vendas_por_hora            # dry-run: le e resume
    python -m pipelines.sync_vendas_por_hora --apply    # grava no Neon
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text

from pipelines.common.db import datamart_query, local_engine
from pipelines.common.logging import get_logger
from pipelines.connectors.mercadolivre.connector import BRANDS_IN_SCOPE as ML_BRANDS
from pipelines.connectors.tiktok.connector import (
    BRANDS_IN_SCOPE as TIKTOK_BRANDS,
    COMMERCIAL_ORDER_STATUSES as TIKTOK_STATUS_COMERCIAIS,
)
from pipelines.sync_shopee_daily_api_shadow import CONTAS_API as SHOPEE_BRANDS

logger = get_logger(__name__)

TABELA = "marts.fact_marketplace_hourly_sales"
FONTES = "marts.fact_marketplace_hourly_sales_fonte"
BRT = timezone(timedelta(hours=-3))
DIAS_JANELA = 8
#: A carga de 03/11/2026 13:00 BRT buscou na API a partir de ~13:05 e gravou
#: ate' 13:13: a carga termina ~8 min depois do inicio da busca. 10 min de folga
#: nunca declaram fechada uma hora que a busca nao cobriu, desde que a busca nao
#: atravesse a hora cheia — o schedule (minuto 0 ou 15) garante.
FOLGA_SHOPEE = timedelta(minutes=10)
ADVISORY_LOCK_KEY = 917140036

TIKTOK, ML, SHOPEE = 1, 2, 3


class VendasPorHoraError(RuntimeError):
    """Falha de contrato ou de publicacao."""


# --------------------------------------------------------------------------- #
# Fonte (Data Mart, so' leitura)                                               #
# --------------------------------------------------------------------------- #

ML_SQL = """
SELECT (date_created + interval '1 hour')::date               AS dia,
       extract(hour FROM date_created + interval '1 hour')::int AS hora,
       brand,
       SUM(total_amount)                                        AS gmv,
       COUNT(DISTINCT order_id)                                 AS pedidos
FROM api.ml_orders
WHERE status = 'paid'
  AND brand IN :brands
  AND date_created >= CAST(:inicio AS timestamp) - interval '1 hour'
  AND date_created <  CAST(:fim_exclusivo AS timestamp) - interval '1 hour'
GROUP BY 1, 2, 3
"""

ML_FONTE_SQL = """
SELECT max(date_created) + interval '1 hour' AS fonte_ate
FROM api.ml_orders
WHERE brand IN :brands
  AND date_created >= CAST(:inicio AS timestamp) - interval '1 hour'
"""

TIKTOK_SQL = """
WITH r AS (
    SELECT DISTINCT ON (order_id) order_id, brand, order_status, total_amount, created_at
    FROM raw.tiktok_shop_orders
    WHERE brand IN :brands
      AND created_at >= :inicio
      AND created_at <  :fim_exclusivo
    ORDER BY order_id, updated_at DESC NULLS LAST, id DESC
)
SELECT created_at::date                AS dia,
       extract(hour FROM created_at)::int AS hora,
       brand,
       SUM(total_amount)               AS gmv,
       COUNT(*)                        AS pedidos
FROM r
WHERE order_status IN :status_comerciais
GROUP BY 1, 2, 3
"""

TIKTOK_FONTE_SQL = """
SELECT max(created_at) AS fonte_ate
FROM raw.tiktok_shop_orders
WHERE brand IN :brands
  AND created_at >= :inicio
"""

SHOPEE_SQL = """
WITH o AS (
    SELECT order_sn, brand, order_status, create_time AT TIME ZONE 'America/Sao_Paulo' AS criado_brt
    FROM silver.stg_shopee_orders
    WHERE brand IN :brands
      AND create_time >= CAST(:inicio AS timestamp) AT TIME ZONE 'America/Sao_Paulo'
      AND create_time <  CAST(:fim_exclusivo AS timestamp) AT TIME ZONE 'America/Sao_Paulo'
      AND lower(order_status) <> 'cancelled'
),
i AS (
    SELECT order_sn, SUM(item_total) AS total
    FROM silver.stg_shopee_order_items
    WHERE order_sn IN (SELECT order_sn FROM o)
    GROUP BY 1
)
SELECT o.criado_brt::date                AS dia,
       extract(hour FROM o.criado_brt)::int AS hora,
       o.brand,
       COALESCE(SUM(i.total), 0)         AS gmv,
       COUNT(*)                          AS pedidos
FROM o LEFT JOIN i USING (order_sn)
GROUP BY 1, 2, 3
"""

SHOPEE_FONTE_SQL = """
SELECT max(ingested_at) AS fonte_ate
FROM silver.stg_shopee_orders
WHERE brand IN :brands
"""


@dataclass(frozen=True)
class Canal:
    marketplace_id: int
    nome: str
    sql: str
    fonte_sql: str
    brands: tuple
    extra: dict


CANAIS = (
    Canal(ML, "mercadolivre", ML_SQL, ML_FONTE_SQL, tuple(ML_BRANDS), {}),
    Canal(TIKTOK, "tiktok", TIKTOK_SQL, TIKTOK_FONTE_SQL, tuple(TIKTOK_BRANDS),
          {"status_comerciais": tuple(TIKTOK_STATUS_COMERCIAIS)}),
    Canal(SHOPEE, "shopee", SHOPEE_SQL, SHOPEE_FONTE_SQL, tuple(SHOPEE_BRANDS), {}),
)


def para_brt(momento: datetime | None, canal: Canal) -> datetime | None:
    """`fonte_ate` como datetime com fuso BRT.

    Sem fuso, o valor ja' vem em BRT (ML depois do `+ 1 hour`, TikTok). A
    Shopee vem com fuso e perde `FOLGA_SHOPEE`.
    """
    if momento is None:
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=BRT)
    momento = momento.astimezone(BRT)
    if canal.marketplace_id == SHOPEE:
        momento -= FOLGA_SHOPEE
    return momento


def ler_canal(canal: Canal, inicio: date, fim_exclusivo: date) -> tuple[list[dict], datetime | None]:
    """Linhas por (dia, hora, brand) e `fonte_ate` do canal. Ponto de injecao dos testes."""
    params = {"brands": canal.brands, "inicio": inicio, "fim_exclusivo": fim_exclusivo, **canal.extra}
    linhas = datamart_query(canal.sql, params)
    fonte = datamart_query(canal.fonte_sql, {"brands": canal.brands, "inicio": inicio})
    return linhas, para_brt(fonte[0]["fonte_ate"] if fonte else None, canal)


# --------------------------------------------------------------------------- #
# Montagem (pura)                                                              #
# --------------------------------------------------------------------------- #

def janela(hoje: date) -> tuple[date, date]:
    """[inicio, fim_exclusivo): hoje e os `DIAS_JANELA - 1` dias anteriores."""
    return hoje - timedelta(days=DIAS_JANELA - 1), hoje + timedelta(days=1)


def hora_fechada(dia: date, hora: int, fonte_ate: datetime) -> bool:
    """A hora [hora, hora+1) acabou antes de `fonte_ate`?"""
    fim_da_hora = datetime(dia.year, dia.month, dia.day, tzinfo=BRT) + timedelta(hours=hora + 1)
    return fim_da_hora <= fonte_ate


def montar(canal: Canal, linhas: list[dict], fonte_ate: datetime | None,
           lojas: dict[str, int]) -> list[dict]:
    """Linhas da tabela: so' horas fechadas, so' marcas com loja cadastrada.

    Marca do escopo sem loja em `dim_loja` aborta: sumir com ela seria publicar
    um total menor sem aviso.
    """
    if fonte_ate is None:
        raise VendasPorHoraError(f"{canal.nome}: a fonte nao tem nenhum pedido na janela")
    faltando = sorted(set(canal.brands) - set(lojas))
    if faltando:
        raise VendasPorHoraError(f"{canal.nome}: marca sem loja em dim_loja: {faltando}")
    somas: dict[tuple, list] = defaultdict(lambda: [Decimal("0"), 0])
    for r in linhas:
        dia, hora = r["dia"], int(r["hora"])
        if isinstance(dia, datetime):
            dia = dia.date()
        if not hora_fechada(dia, hora, fonte_ate):
            continue
        chave = (dia, hora, canal.marketplace_id, lojas[r["brand"]])
        somas[chave][0] += Decimal(str(r["gmv"] or 0))
        somas[chave][1] += int(r["pedidos"] or 0)
    return [{"dia": d, "hora": h, "marketplace_id": m, "loja_id": l,
             "gmv": g.quantize(Decimal("0.01"), ROUND_HALF_UP), "pedidos": p}
            for (d, h, m, l), (g, p) in sorted(somas.items())]


# --------------------------------------------------------------------------- #
# Publicacao (Neon)                                                            #
# --------------------------------------------------------------------------- #

def ler_lojas(conn) -> dict[str, int]:
    return {r[0]: r[1] for r in conn.execute(text("SELECT brand_key, loja_id FROM marts.dim_loja"))}


def publicar_em_transacao(conn, linhas: list[dict], fontes: dict[int, datetime],
                          inicio: date, fim_exclusivo: date) -> int:
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY})
    conn.execute(text(f"DELETE FROM {TABELA} WHERE dia >= :inicio AND dia < :fim "
                      f"AND marketplace_id = ANY(:canais)"),
                 {"inicio": inicio, "fim": fim_exclusivo, "canais": sorted(fontes)})
    if linhas:
        conn.execute(text(f"INSERT INTO {TABELA} (dia, hora, marketplace_id, loja_id, gmv, pedidos) "
                          f"VALUES (:dia, :hora, :marketplace_id, :loja_id, :gmv, :pedidos)"), linhas)
    conn.execute(text(f"""
        INSERT INTO {FONTES} (marketplace_id, fonte_ate, publicado_em)
        VALUES (:m, :f, now())
        ON CONFLICT (marketplace_id) DO UPDATE
           SET fonte_ate = EXCLUDED.fonte_ate, publicado_em = EXCLUDED.publicado_em
    """), [{"m": m, "f": f} for m, f in sorted(fontes.items())])
    return len(linhas)


def run(apply: bool = False, agora: datetime | None = None) -> dict:
    agora_brt = (agora or datetime.now(timezone.utc)).astimezone(BRT)
    hoje = agora_brt.date()
    inicio, fim_exclusivo = janela(hoje)
    with local_engine().connect() as conn:
        lojas = ler_lojas(conn)

    linhas: list[dict] = []
    fontes: dict[int, datetime] = {}
    por_canal = {}
    for canal in CANAIS:
        brutas, fonte_ate = ler_canal(canal, inicio, fim_exclusivo)
        montadas = montar(canal, brutas, fonte_ate, lojas)
        if not montadas:
            raise VendasPorHoraError(f"{canal.nome}: nenhuma hora fechada na janela")
        linhas += montadas
        fontes[canal.marketplace_id] = fonte_ate
        de_hoje = [r for r in montadas if r["dia"] == hoje]
        por_canal[canal.nome] = {
            "fonte_ate": fonte_ate.isoformat(),
            "linhas": len(montadas),
            "hoje_horas_fechadas": len({r["hora"] for r in de_hoje}),
            "hoje_gmv": str(sum((r["gmv"] for r in de_hoje), Decimal("0"))),
            "hoje_pedidos": sum(r["pedidos"] for r in de_hoje),
        }

    resumo = {"hoje_brt": str(hoje), "janela": [str(inicio), str(fim_exclusivo - timedelta(days=1))],
              "canais": por_canal, "linhas": len(linhas), "modo": "apply" if apply else "dry-run"}
    if apply:
        with local_engine().begin() as conn:
            resumo["linhas_gravadas"] = publicar_em_transacao(conn, linhas, fontes, inicio, fim_exclusivo)
    return resumo


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Vendas por hora (BRT) de ML, TikTok e Shopee no Neon")
    p.add_argument("--apply", action="store_true", help="grava no Neon. Sem esta flag o run e' dry-run.")
    args = p.parse_args(argv)
    try:
        res = run(apply=args.apply)
    except VendasPorHoraError as exc:
        logger.error("%s", exc)
        return 1
    print(json.dumps(res, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
