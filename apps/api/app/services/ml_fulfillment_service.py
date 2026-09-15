"""Superficie "Full Mercado Livre" — Gate FULL-1A.

Le EXCLUSIVAMENTE `marts.fact_ml_fulfillment_daily` e
`marts.fact_ml_fulfillment_listing_daily`. Nenhuma consulta ao Data Mart, a
`raw` ou a API do Mercado Livre acontece durante o request: o custo e a
disponibilidade da fonte nao podem entrar no caminho do usuario, e um endpoint
que consulta a fonte online falha quando a VPN cai.

O QUE ESTE MODULO NAO FAZ
--------------------------
- **Nao fala de estoque.** Nenhum campo de disponibilidade, cobertura ou
  ruptura. "Full" aqui e' modalidade do ENVIO. O FULL-0R mediu correlacao de
  -0,006 entre a variacao de `available_quantity` e as vendas em listings
  exclusivamente Full: o campo nao e' estoque fisico e nao entra aqui.
- **Nao funde `unknown` em `non_full`.** Pedido sem envio tem classe propria, e
  fica FORA do denominador dos shares: ele nao e Full nem nao-Full, e contá-lo
  faria uma lacuna de dado parecer queda operacional.
- **Nao trata `handling`/`delivery` como grao de envio.** Desde o FULL-1A-R os
  dois pertencem a coorte do PEDIDO, a mesma do GMV.
- **Nao preenche ausencia com zero.** Share com denominador zero e' `None`.
- **Nao armazena media pronta.** A fato guarda soma e amostra; a media e'
  derivada aqui, e por isso continua reagregavel entre dias e marcas.
- **Nao rateia GMV de pedido por listing.** A fato de listing ja' chega com
  alocacao determinística vinda de `api.ml_order_line_items`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

FACT_TABLE = "marts.fact_ml_fulfillment_daily"
LISTING_TABLE = "marts.fact_ml_fulfillment_listing_daily"

CLASSE_FULL = "full"
CLASSE_NON_FULL = "non_full"
CLASSE_UNKNOWN = "unknown"
CLASSES = (CLASSE_FULL, CLASSE_NON_FULL, CLASSE_UNKNOWN)

#: Idade maxima da carga para ser chamada `fresh`. Espelha o limiar de 30 h ja'
#: usado em `tiktok_order_discounts_service` e `affiliate_costs_service`, para
#: que as secoes da Torre nao classifiquem "recente" de formas diferentes.
MAX_LOAD_AGE_HOURS = 30

#: Teto de listings devolvidos na oportunidade de migracao. Limite de payload,
#: nao de analise: a fato continua completa para quem consultar direto.
MAX_MIGRATION_ROWS = 50

# --- Limitacoes FIXAS e sanitizadas. Nunca interpolam SQL, DSN, host nem
# --- mensagem de driver.
LIMITACAO_MANUAL = (
    "Carga em modo manual_snapshot: o gate FULL-1A nao integrou Scheduler nem "
    "Airflow. O frescor depende de execucao manual do sync."
)
LIMITACAO_CANCELAMENTO = (
    "Cancelamento madura apos o fechamento do dia: medido em jun-ago/2026, p90 "
    "de 4,85 dias, p99 de 14,62 e maximo de 44,12. Dias recentes tendem a "
    "subestimar o cancelamento."
)
LIMITACAO_UNKNOWN = (
    "Pedidos sem envio correspondente aparecem em fulfillment_class='unknown', "
    "nunca sao somados a nao-Full e ficam fora do denominador dos shares."
)
LIMITACAO_TEMPOS = (
    "handling e delivery pertencem a mesma coorte do pedido (ref_date = criacao "
    "do pedido). sample_count mede a censura: pedido ainda sem despacho ou sem "
    "entrega fica fora da amostra, nunca entra como tempo zero."
)
LIMITACAO_OUTROS_STATUS = (
    "partially_refunded fica fora do GMV por contrato canonico da Torre: o "
    "pedido INTEIRO sai, nao apenas a parcela reembolsada. Esses pedidos sao "
    "contados em other_orders."
)
LIMITACAO_SEM_ESTOQUE = (
    "Esta superficie mede modalidade logistica, nao estoque. Nao ha aqui "
    "disponibilidade, cobertura em dias nem ruptura."
)


class MLFulfillmentUnavailable(RuntimeError):
    """Fato indisponivel. Mensagem ja' sanitizada, sem detalhe de driver."""


# ---------------------------------------------------------------------------
# SQL — todo parametrizado. `:brands` chega como array e e' comparado com
# `= ANY(:brands)`; nenhuma marca e' interpolada no texto.
# ---------------------------------------------------------------------------

_FILTRO = """
    WHERE ref_date BETWEEN :date_from AND :date_to
      AND (:brands IS NULL OR brand = ANY(:brands))
"""

SQL_POR_CLASSE = text(f"""
    SELECT fulfillment_class,
           SUM(paid_gmv)                     AS paid_gmv,
           SUM(paid_orders)                  AS paid_orders,
           SUM(paid_units)                   AS paid_units,
           SUM(eligible_orders)              AS eligible_orders,
           SUM(cancelled_orders)             AS cancelled_orders,
           SUM(other_orders)                 AS other_orders,
           SUM(handling_seconds_sum)         AS handling_seconds_sum,
           SUM(handling_sample_count)        AS handling_sample_count,
           SUM(delivery_seconds_sum)         AS delivery_seconds_sum,
           SUM(delivery_sample_count)        AS delivery_sample_count,
           SUM(unmatched_orders)             AS unmatched_orders,
           SUM(missing_shipping_items)       AS missing_shipping_items
      FROM {FACT_TABLE}
    {_FILTRO}
     GROUP BY fulfillment_class
""")

SQL_POR_TIPO = text(f"""
    SELECT logistic_type_original, fulfillment_class,
           SUM(paid_gmv) AS paid_gmv, SUM(paid_orders) AS paid_orders,
           SUM(paid_units) AS paid_units
      FROM {FACT_TABLE}
    {_FILTRO}
     GROUP BY logistic_type_original, fulfillment_class
     ORDER BY SUM(paid_gmv) DESC
""")

SQL_DIARIO = text(f"""
    SELECT ref_date, fulfillment_class,
           SUM(paid_gmv) AS paid_gmv, SUM(paid_orders) AS paid_orders,
           SUM(paid_units) AS paid_units,
           SUM(eligible_orders) AS eligible_orders,
           SUM(cancelled_orders) AS cancelled_orders
      FROM {FACT_TABLE}
    {_FILTRO}
     GROUP BY ref_date, fulfillment_class
     ORDER BY ref_date, fulfillment_class
""")

SQL_DIAS_PRESENTES = text(f"""
    SELECT COUNT(DISTINCT ref_date) AS dias FROM {FACT_TABLE} {_FILTRO}
""")

SQL_FRESCOR = text(f"""
    SELECT MAX(ingested_at) AS refreshed_at, MAX(ref_date) AS source_max_date,
           COUNT(*) AS linhas
      FROM {FACT_TABLE}
""")

#: Classificacao do listing na JANELA. `bool_or` sobre as classes: o listing e'
#: misto quando vendeu pelos dois modais no periodo, e essa e' a unica leitura
#: correta -- "Full" nao e' atributo estavel do listing.
SQL_LISTINGS = text(f"""
    WITH por_listing AS (
        SELECT brand, item_id,
               BOOL_OR(fulfillment_class = 'full')     AS tem_full,
               BOOL_OR(fulfillment_class = 'non_full') AS tem_non_full,
               SUM(paid_gmv)   FILTER (WHERE fulfillment_class = 'full')     AS gmv_full,
               SUM(paid_gmv)   FILTER (WHERE fulfillment_class = 'non_full') AS gmv_non_full,
               SUM(paid_units) FILTER (WHERE fulfillment_class = 'non_full') AS un_non_full
          FROM {LISTING_TABLE}
        {_FILTRO}
         GROUP BY brand, item_id
    )
    SELECT brand, item_id, tem_full, tem_non_full,
           COALESCE(gmv_full, 0)     AS gmv_full,
           COALESCE(gmv_non_full, 0) AS gmv_non_full,
           COALESCE(un_non_full, 0)  AS un_non_full
      FROM por_listing
""")


def _f(valor) -> Optional[float]:
    return None if valor is None else float(valor)


def _share(num, den) -> Optional[float]:
    """Fracao ou None. NUNCA zero quando o denominador e' zero.

    Zero significaria "nenhum Full medido"; None significa "nao ha o que
    dividir". Confundir os dois faz um periodo sem venda parecer um periodo de
    fracasso total do Full.
    """
    if den is None or float(den) == 0.0:
        return None
    return float(num or 0) / float(den)


def _time_stat(soma, amostra) -> dict:
    """Media derivada de soma/amostra. Amostra zero -> None em todas as escalas."""
    n = int(amostra or 0)
    if n <= 0:
        return {"seconds_avg": None, "hours_avg": None, "days_avg": None,
                "sample_count": 0}
    segundos = float(soma or 0) / n
    return {"seconds_avg": segundos, "hours_avg": segundos / 3600.0,
            "days_avg": segundos / 86400.0, "sample_count": n}


def _freshness(row, agora: datetime) -> dict:
    if row is None or not row["linhas"]:
        return {"freshness_status": "never_loaded", "refreshed_at": None,
                "source_max_date": None, "age_hours": None,
                "load_mode": "manual_snapshot"}
    refreshed = row["refreshed_at"]
    if refreshed is None:
        return {"freshness_status": "unknown", "refreshed_at": None,
                "source_max_date": row["source_max_date"], "age_hours": None,
                "load_mode": "manual_snapshot"}
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=timezone.utc)
    idade = (agora - refreshed).total_seconds() / 3600.0
    return {
        "freshness_status": "fresh" if idade <= MAX_LOAD_AGE_HOURS else "stale",
        "refreshed_at": refreshed,
        "source_max_date": row["source_max_date"],
        "age_hours": idade,
        "load_mode": "manual_snapshot",
    }


def get_ml_fulfillment_block(db: Session, date_from: date, date_to: date,
                             brands: Optional[list[str]] = None,
                             agora: datetime | None = None) -> dict:
    """Monta a resposta completa. Uma leitura por agregacao, tudo na fato."""
    agora = agora or datetime.now(timezone.utc)
    params = {"date_from": date_from, "date_to": date_to,
              "brands": list(brands) if brands else None}

    try:
        por_classe = [dict(m) for m in db.execute(SQL_POR_CLASSE, params).mappings()]
        por_tipo = [dict(m) for m in db.execute(SQL_POR_TIPO, params).mappings()]
        diario = [dict(m) for m in db.execute(SQL_DIARIO, params).mappings()]
        dias = db.execute(SQL_DIAS_PRESENTES, params).scalar() or 0
        listings = [dict(m) for m in db.execute(SQL_LISTINGS, params).mappings()]
        frescor_row = db.execute(SQL_FRESCOR).mappings().first()
    except SQLAlchemyError as exc:
        # Mensagem do driver carrega DSN e host: nunca propagada ao cliente.
        logger.error("fato de fulfillment ML indisponivel: %s", type(exc).__name__)
        raise MLFulfillmentUnavailable(
            "Superficie Full do Mercado Livre indisponivel no momento."
        ) from exc

    idx = {r["fulfillment_class"]: r for r in por_classe}

    gmv_total = sum(Decimal(str(r["paid_gmv"] or 0)) for r in por_classe)
    ped_total = sum(int(r["paid_orders"] or 0) for r in por_classe)
    un_total = sum(int(r["paid_units"] or 0) for r in por_classe)

    # DENOMINADOR DOS SHARES = full + non_full, SEM unknown.
    # `unknown` nao e Full nem nao-Full: e pedido cuja modalidade nao
    # conhecemos. Inclui-lo no denominador faria o share de Full cair por
    # causa de uma lacuna de dado, o que leria como queda operacional.
    # Os totais absolutos acima seguem cobrindo as tres classes.
    classificadas = (CLASSE_FULL, CLASSE_NON_FULL)
    gmv_class = sum(Decimal(str((idx.get(c) or {}).get("paid_gmv") or 0))
                    for c in classificadas)
    ped_class = sum(int((idx.get(c) or {}).get("paid_orders") or 0)
                    for c in classificadas)
    un_class = sum(int((idx.get(c) or {}).get("paid_units") or 0)
                   for c in classificadas)
    full = idx.get(CLASSE_FULL)

    by_class = []
    for classe in CLASSES:
        r = idx.get(classe)
        if r is None:
            # Classe ausente da janela NAO vira linha de zeros: ausencia e'
            # ausencia. O consumidor ve a classe faltar, nao um falso zero.
            continue
        by_class.append({
            "fulfillment_class": classe,
            "paid_gmv": Decimal(str(r["paid_gmv"] or 0)),
            "paid_orders": int(r["paid_orders"] or 0),
            "paid_units": int(r["paid_units"] or 0),
            "eligible_orders": int(r["eligible_orders"] or 0),
            "cancelled_orders": int(r["cancelled_orders"] or 0),
            "other_orders": int(r["other_orders"] or 0),
            "cancellation_rate": _share(r["cancelled_orders"],
                                        r["eligible_orders"]),
            "handling": _time_stat(r["handling_seconds_sum"],
                                   r["handling_sample_count"]),
            "delivery": _time_stat(r["delivery_seconds_sum"],
                                   r["delivery_sample_count"]),
        })

    full_only = mixed = non_full_only = 0
    oportunidades = []
    for l in listings:
        if l["tem_full"] and l["tem_non_full"]:
            mixed += 1
            classe_listing = "mixed"
        elif l["tem_full"]:
            full_only += 1
            continue
        else:
            non_full_only += 1
            classe_listing = "non_full_only"
        if Decimal(str(l["gmv_non_full"] or 0)) > 0:
            oportunidades.append({
                "item_id": l["item_id"], "brand": l["brand"],
                "non_full_gmv": Decimal(str(l["gmv_non_full"] or 0)),
                "non_full_units": int(l["un_non_full"] or 0),
                "full_gmv": Decimal(str(l["gmv_full"] or 0)),
                "listing_class": classe_listing,
            })
    oportunidades.sort(key=lambda o: o["non_full_gmv"], reverse=True)

    frescor = _freshness(frescor_row, agora)
    dias_pedidos = (date_to - date_from).days + 1
    completo = (dias == dias_pedidos) and frescor["freshness_status"] == "fresh"

    limitacoes = [LIMITACAO_SEM_ESTOQUE, LIMITACAO_MANUAL,
                  LIMITACAO_CANCELAMENTO, LIMITACAO_UNKNOWN, LIMITACAO_TEMPOS,
                  LIMITACAO_OUTROS_STATUS]
    if dias != dias_pedidos:
        limitacoes.append(
            f"Janela pedida tem {dias_pedidos} dia(s); a fato cobre {dias}."
        )

    return {
        "date_from": date_from, "date_to": date_to,
        "brands": sorted(brands) if brands else [],
        "share_full_gmv": _share(full["paid_gmv"] if full else 0, gmv_class),
        "share_full_orders": _share(full["paid_orders"] if full else 0, ped_class),
        "share_full_units": _share(full["paid_units"] if full else 0, un_class),
        "paid_gmv_total": gmv_total,
        "paid_orders_total": ped_total,
        "paid_units_total": un_total,
        "by_class": by_class,
        "by_logistic_type": [{
            "logistic_type_original": r["logistic_type_original"],
            "fulfillment_class": r["fulfillment_class"],
            "paid_gmv": Decimal(str(r["paid_gmv"] or 0)),
            "paid_orders": int(r["paid_orders"] or 0),
            "paid_units": int(r["paid_units"] or 0),
        } for r in por_tipo],
        "daily": [{
            "ref_date": r["ref_date"],
            "fulfillment_class": r["fulfillment_class"],
            "paid_gmv": Decimal(str(r["paid_gmv"] or 0)),
            "paid_orders": int(r["paid_orders"] or 0),
            "paid_units": int(r["paid_units"] or 0),
            "eligible_orders": int(r["eligible_orders"] or 0),
            "cancelled_orders": int(r["cancelled_orders"] or 0),
        } for r in diario],
        "listings": {"full_only": full_only, "mixed": mixed,
                     "non_full_only": non_full_only,
                     "total": full_only + mixed + non_full_only},
        "migration_opportunities": oportunidades[:MAX_MIGRATION_ROWS],
        "quality": {
            "unmatched_orders": sum(int(r["unmatched_orders"] or 0)
                                    for r in por_classe),
            "missing_shipping_items": sum(int(r["missing_shipping_items"] or 0)
                                          for r in por_classe),
            "unknown_logistic_type_orders": int(
                (idx.get(CLASSE_UNKNOWN) or {}).get("eligible_orders") or 0),
            "is_complete": completo,
            "limitations": limitacoes,
        },
        "freshness": frescor,
    }
