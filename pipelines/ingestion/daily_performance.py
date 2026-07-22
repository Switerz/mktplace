"""
Orquestração principal da ingestão diária de performance.

Uso:
    python -m pipelines.ingestion.daily_performance --source tiktok --mode incremental
    python -m pipelines.ingestion.daily_performance --source ml --mode backfill --days 90
    python -m pipelines.ingestion.daily_performance --source tiktok --mode backfill \\
        --date-from 2026-01-01 --date-to 2026-05-31
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text

from pipelines.common.db import local_session
from pipelines.common.logging import get_logger
from pipelines.connectors.mercadolivre import connector as ml_connector
from pipelines.connectors.shopee import connector as shopee_connector
from pipelines.connectors.tiktok import connector as tiktok_connector
from pipelines.quality import checks as quality
from pipelines.transforms import ml_gestao_diaria as ml_transform
from pipelines.transforms import shopee_ads_daily as shopee_ads_transform
from pipelines.transforms import shopee_orders_daily as shopee_transform
from pipelines.transforms import shopee_shop_stats_daily as shopee_stats_transform
from pipelines.transforms import tiktok_brand_daily as tiktok_transform

logger = get_logger(__name__)

UPSERT_SQL = text("""
    INSERT INTO marts.fact_marketplace_daily_performance (
        date, loja_id, marketplace_id, empresa_id,
        gmv, orders, units_sold, avg_ticket,
        unique_buyers, new_buyers, repeat_buyers, repeat_buyer_rate_pct,
        visitors, conversion_rate,
        canceled_orders, returned_orders, refunded_orders, problem_rate, cancel_rate_pct,
        delivered_orders, avg_delivery_hours, avg_delivery_days,
        ad_spend, ad_revenue, ad_impressions, ad_clicks, roas, acos_pct, ctr_pct, cpc,
        gmv_video, gmv_live, gmv_card,
        total_settlement, total_fees, avg_fee_pct, avg_settlement_pct,
        seller_shipping_cost, shipping_pct_of_gmv,
        target_revenue, target_attainment_pct, projected_month_revenue,
        data_quality_score, source_updated_at
    ) VALUES (
        :date, :loja_id, :marketplace_id, :empresa_id,
        :gmv, :orders, :units_sold, :avg_ticket,
        :unique_buyers, :new_buyers, :repeat_buyers, :repeat_buyer_rate_pct,
        :visitors, :conversion_rate,
        :canceled_orders, :returned_orders, :refunded_orders, :problem_rate, :cancel_rate_pct,
        :delivered_orders, :avg_delivery_hours, :avg_delivery_days,
        :ad_spend, :ad_revenue, :ad_impressions, :ad_clicks, :roas, :acos_pct, :ctr_pct, :cpc,
        :gmv_video, :gmv_live, :gmv_card,
        :total_settlement, :total_fees, :avg_fee_pct, :avg_settlement_pct,
        :seller_shipping_cost, :shipping_pct_of_gmv,
        :target_revenue, :target_attainment_pct, :projected_month_revenue,
        :data_quality_score, :source_updated_at
    )
    ON CONFLICT (date, loja_id, marketplace_id) DO UPDATE SET
        empresa_id              = EXCLUDED.empresa_id,
        gmv                     = EXCLUDED.gmv,
        orders                  = EXCLUDED.orders,
        units_sold              = EXCLUDED.units_sold,
        avg_ticket              = EXCLUDED.avg_ticket,
        unique_buyers           = EXCLUDED.unique_buyers,
        new_buyers              = EXCLUDED.new_buyers,
        repeat_buyers           = EXCLUDED.repeat_buyers,
        repeat_buyer_rate_pct   = EXCLUDED.repeat_buyer_rate_pct,
        visitors                = EXCLUDED.visitors,
        conversion_rate         = EXCLUDED.conversion_rate,
        canceled_orders         = EXCLUDED.canceled_orders,
        returned_orders         = EXCLUDED.returned_orders,
        refunded_orders         = EXCLUDED.refunded_orders,
        problem_rate            = EXCLUDED.problem_rate,
        cancel_rate_pct         = EXCLUDED.cancel_rate_pct,
        delivered_orders        = EXCLUDED.delivered_orders,
        avg_delivery_hours      = EXCLUDED.avg_delivery_hours,
        avg_delivery_days       = EXCLUDED.avg_delivery_days,
        ad_spend                = EXCLUDED.ad_spend,
        ad_revenue              = EXCLUDED.ad_revenue,
        ad_impressions          = EXCLUDED.ad_impressions,
        ad_clicks               = EXCLUDED.ad_clicks,
        roas                    = EXCLUDED.roas,
        acos_pct                = EXCLUDED.acos_pct,
        ctr_pct                 = EXCLUDED.ctr_pct,
        cpc                     = EXCLUDED.cpc,
        gmv_video               = EXCLUDED.gmv_video,
        gmv_live                = EXCLUDED.gmv_live,
        gmv_card                = EXCLUDED.gmv_card,
        total_settlement        = EXCLUDED.total_settlement,
        total_fees              = EXCLUDED.total_fees,
        avg_fee_pct             = EXCLUDED.avg_fee_pct,
        avg_settlement_pct      = EXCLUDED.avg_settlement_pct,
        seller_shipping_cost    = EXCLUDED.seller_shipping_cost,
        shipping_pct_of_gmv     = EXCLUDED.shipping_pct_of_gmv,
        data_quality_score      = EXCLUDED.data_quality_score,
        source_updated_at       = EXCLUDED.source_updated_at,
        ingested_at             = NOW()
""")


PATCH_ADS_SQL = text("""
    INSERT INTO marts.fact_marketplace_daily_performance (
        date, loja_id, marketplace_id, empresa_id,
        ad_spend, ad_revenue, ad_impressions, ad_clicks,
        roas, acos_pct, ctr_pct, cpc,
        source_updated_at
    ) VALUES (
        :date, :loja_id, :marketplace_id, :empresa_id,
        :ad_spend, :ad_revenue, :ad_impressions, :ad_clicks,
        :roas, :acos_pct, :ctr_pct, :cpc,
        NOW()
    )
    ON CONFLICT (date, loja_id, marketplace_id) DO UPDATE SET
        ad_spend       = EXCLUDED.ad_spend,
        ad_revenue     = EXCLUDED.ad_revenue,
        ad_impressions = EXCLUDED.ad_impressions,
        ad_clicks      = EXCLUDED.ad_clicks,
        roas           = EXCLUDED.roas,
        acos_pct       = EXCLUDED.acos_pct,
        ctr_pct        = EXCLUDED.ctr_pct,
        cpc            = EXCLUDED.cpc,
        source_updated_at = NOW(),
        ingested_at    = NOW()
""")

PATCH_SHOP_STATS_SQL = text("""
    INSERT INTO marts.fact_marketplace_daily_performance (
        date, loja_id, marketplace_id, empresa_id,
        visitors, conversion_rate,
        new_buyers, repeat_buyers, repeat_buyer_rate_pct, unique_buyers,
        gmv,
        source_updated_at
    ) VALUES (
        :date, :loja_id, :marketplace_id, :empresa_id,
        :visitors, :conversion_rate,
        :new_buyers, :repeat_buyers, :repeat_buyer_rate_pct, :unique_buyers,
        :gmv,
        NOW()
    )
    ON CONFLICT (date, loja_id, marketplace_id) DO UPDATE SET
        visitors             = EXCLUDED.visitors,
        conversion_rate      = EXCLUDED.conversion_rate,
        new_buyers           = EXCLUDED.new_buyers,
        repeat_buyers        = EXCLUDED.repeat_buyers,
        repeat_buyer_rate_pct = EXCLUDED.repeat_buyer_rate_pct,
        unique_buyers        = EXCLUDED.unique_buyers,
        gmv                  = EXCLUDED.gmv,
        source_updated_at    = NOW(),
        ingested_at          = NOW()
""")
# Gate R2.1 (Projeto R): shop-stats passa a ser a fonte AUTORITATIVA final
# do GMV Shopee. O step `daily_shopee_stats` já roda depois de
# `daily_shopee_orders` em `shopee_manual_refresh` (ordem/criticidade dos
# steps não alterada aqui — ver pipelines/ops/orchestrate.py); como este
# PATCH roda por último e agora sobrescreve `gmv` também, ele é o valor que
# prevalece no upsert final. Este SQL não foi executado nesta correção —
# só o texto foi alterado, para revisão.


def _start_sync_run(session: Any, source_name: str, marketplace_id: int) -> int:
    result = session.execute(
        text("""
            INSERT INTO audit.source_sync_run (source_name, marketplace_id, status, started_at)
            VALUES (:source_name, :marketplace_id, 'running', NOW())
            RETURNING sync_run_id
        """),
        {"source_name": source_name, "marketplace_id": marketplace_id},
    )
    session.commit()
    return result.scalar_one()


def _finish_sync_run(
    session: Any,
    sync_run_id: int,
    status: str,
    rows_extracted: int,
    rows_loaded: int,
    source_min_date,
    source_max_date,
    error_message: str | None = None,
) -> None:
    session.execute(
        text("""
            UPDATE audit.source_sync_run SET
                finished_at     = NOW(),
                status          = :status,
                rows_extracted  = :rows_extracted,
                rows_loaded     = :rows_loaded,
                source_min_date = :source_min_date,
                source_max_date = :source_max_date,
                error_message   = :error_message
            WHERE sync_run_id = :sync_run_id
        """),
        {
            "sync_run_id": sync_run_id,
            "status": status,
            "rows_extracted": rows_extracted,
            "rows_loaded": rows_loaded,
            "source_min_date": source_min_date,
            "source_max_date": source_max_date,
            "error_message": error_message,
        },
    )
    session.commit()


def _log_quality_checks(
    session: Any,
    results: list[quality.CheckResult],
    marketplace_id: int,
) -> None:
    for r in results:
        session.execute(
            text("""
                INSERT INTO audit.data_quality_check
                    (check_name, table_name, marketplace_id, status, severity, failed_rows, details)
                VALUES
                    (:check_name, :table_name, :marketplace_id, :status, :severity, :failed_rows, :details)
            """),
            {
                "check_name": r.name,
                "table_name": "marts.fact_marketplace_daily_performance",
                "marketplace_id": marketplace_id,
                "status": r.status,
                "severity": r.severity,
                "failed_rows": r.failed_rows,
                "details": r.details or None,
            },
        )
    session.commit()


# Gate R4 Task 2 (Projeto R): janela exata (--date-from/--date-to) para
# publicar somente o intervalo já validado (jan-mai), sem alcançar
# jun/jul via --days. Só estas 3 fontes têm regra de GMV aprovada/pendente
# de validação neste projeto; shopee (orders)/shopee-ads não têm janela
# exata aprovada e continuam só no caminho --days/incremental existente.
_DATE_WINDOW_ALLOWED_SOURCES = ("tiktok", "shopee-stats", "ml")


def _resolve_date_window(
    source: str, mode: str, date_from: str | None, date_to: str | None
) -> tuple[date, date] | None:
    """Valida --date-from/--date-to ANTES de qualquer I/O (sessão de
    escrita, audit.source_sync_run, consulta à fonte, UPSERT). Levanta
    ValueError para qualquer combinação inválida. Retorna None quando
    nenhuma janela foi solicitada — nesse caso o caminho --days/incremental
    existente fica 100% inalterado.
    """
    if date_from is None and date_to is None:
        return None
    if date_from is None or date_to is None:
        raise ValueError("--date-from e --date-to devem ser fornecidos juntos.")
    if mode != "backfill":
        raise ValueError("--date-from/--date-to exigem --mode backfill.")
    if source not in _DATE_WINDOW_ALLOWED_SOURCES:
        raise ValueError(
            f"--date-from/--date-to só são suportados para source em "
            f"{_DATE_WINDOW_ALLOWED_SOURCES}, recebido {source!r}."
        )
    parsed_from = date.fromisoformat(date_from)
    parsed_to = date.fromisoformat(date_to)
    if parsed_from > parsed_to:
        raise ValueError(f"--date-from ({parsed_from}) não pode ser posterior a --date-to ({parsed_to}).")
    if parsed_to > date.today():
        raise ValueError(f"--date-to ({parsed_to}) não pode estar no futuro (hoje: {date.today()}).")
    return parsed_from, parsed_to


def run(
    source: str,
    mode: str,
    days_back: int = 3,
    date_from: str | None = None,
    date_to: str | None = None,
) -> None:
    # Validação da janela exata acontece antes de qualquer outra coisa —
    # nenhuma sessão de escrita, audit ou fetch é tocado se ela falhar.
    window = _resolve_date_window(source, mode, date_from, date_to)

    if source == "tiktok":
        marketplace_id = 1
        connector_fetch = tiktok_connector.fetch
        fetch_fn = (
            tiktok_connector.fetch_incremental
            if mode == "incremental"
            else tiktok_connector.fetch_backfill
        )
        transform_fn = tiktok_transform.transform_batch
    elif source == "ml":
        marketplace_id = 2
        connector_fetch = ml_connector.fetch
        fetch_fn = (
            ml_connector.fetch_incremental
            if mode == "incremental"
            else ml_connector.fetch_backfill
        )
        transform_fn = ml_transform.transform_batch
    elif source == "shopee":
        marketplace_id = 3
        connector_fetch = None
        fetch_fn = (
            shopee_connector.fetch_incremental
            if mode == "incremental"
            else shopee_connector.fetch_backfill
        )
        transform_fn = shopee_transform.transform_batch
    elif source == "shopee-stats":
        marketplace_id = 3
        connector_fetch = shopee_connector.fetch_shop_stats
        fetch_fn = (
            shopee_connector.fetch_shop_stats_incremental
            if mode == "incremental"
            else shopee_connector.fetch_shop_stats_backfill
        )
        transform_fn = shopee_stats_transform.transform_batch
    elif source == "shopee-ads":
        marketplace_id = 3
        connector_fetch = None
        fetch_fn = (
            shopee_connector.fetch_ads_incremental
            if mode == "incremental"
            else shopee_connector.fetch_ads_backfill
        )
        transform_fn = shopee_ads_transform.transform_batch
    else:
        raise ValueError(f"source inválido: {source!r}. Use 'tiktok', 'ml', 'shopee', 'shopee-stats' ou 'shopee-ads'.")

    if window is not None:
        logger.info("Iniciando sync: source=%s mode=%s janela=%s..%s", source, mode, window[0], window[1])
    else:
        logger.info("Iniciando sync: source=%s mode=%s", source, mode)

    with local_session() as session:
        sync_run_id = _start_sync_run(session, f"{source}_daily", marketplace_id)

    try:
        if window is not None:
            raw_rows = connector_fetch(window[0], window[1])
        else:
            kwargs = {"days_back": days_back} if mode == "backfill" else {}
            raw_rows = fetch_fn(**kwargs)
        rows_extracted = len(raw_rows)

        canonical_rows = transform_fn(raw_rows)
        logger.info("%d linhas transformadas (de %d extraídas)", len(canonical_rows), rows_extracted)

        check_results = quality.run_all(canonical_rows)
        has_critical = quality.has_critical_failure(check_results)

        with local_session() as session:
            _log_quality_checks(session, check_results, marketplace_id)

        if has_critical:
            critical = [r for r in check_results if r.status == "fail" and r.severity == "critical"]
            msg = "; ".join(f"{r.name}: {r.details}" for r in critical)
            logger.error("Checks críticos falharam — abortando carga: %s", msg)
            with local_session() as session:
                _finish_sync_run(
                    session, sync_run_id, "failed",
                    rows_extracted, 0, None, None, msg,
                )
            return

        rows_loaded = 0
        source_dates = [r["date"] for r in canonical_rows if r.get("date")]
        if source == "shopee-stats":
            upsert_sql = PATCH_SHOP_STATS_SQL
        elif source == "shopee-ads":
            upsert_sql = PATCH_ADS_SQL
        else:
            upsert_sql = UPSERT_SQL

        with local_session() as session:
            for row in canonical_rows:
                session.execute(upsert_sql, row)
                rows_loaded += 1
            _finish_sync_run(
                session, sync_run_id, "success",
                rows_extracted, rows_loaded,
                min(source_dates) if source_dates else None,
                max(source_dates) if source_dates else None,
            )

        logger.info("Sync concluído: %d linhas carregadas", rows_loaded)

    except Exception as exc:
        logger.exception("Erro inesperado durante sync: %s", exc)
        with local_session() as session:
            _finish_sync_run(
                session, sync_run_id, "failed",
                0, 0, None, None, str(exc),
            )
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de ingestão diária de marketplaces")
    parser.add_argument("--source", required=True, choices=["tiktok", "ml", "shopee", "shopee-stats", "shopee-ads"])
    parser.add_argument("--mode", required=True, choices=["incremental", "backfill"])
    parser.add_argument("--days", type=int, default=3,
                        help="Quantos dias para trás (modo incremental=3, backfill=90)")
    parser.add_argument("--date-from", default=None, metavar="YYYY-MM-DD",
                        help="Data inicial exata (só --mode backfill; source em tiktok/shopee-stats/ml)")
    parser.add_argument("--date-to", default=None, metavar="YYYY-MM-DD",
                        help="Data final exata (só --mode backfill; source em tiktok/shopee-stats/ml)")
    args = parser.parse_args()
    run(
        source=args.source,
        mode=args.mode,
        days_back=args.days,
        date_from=args.date_from,
        date_to=args.date_to,
    )
