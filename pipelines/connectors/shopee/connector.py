"""
Conector Shopee â€” fonte: arquivos xlsx/csv locais (backfill manual).

Diferente de TikTok/ML (que lÃªem de um Data Mart remoto), este conector
lÃª os arquivos exportados da Shopee armazenados em SHOPEE_DATA_PATH.

Subpasta esperada por brand: {SHOPEE_DATA_PATH}/{brand}/Order.all*.xlsx

Brands no escopo:
  apice, barbours, kokeshi, lescent, rituaria
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from pipelines.common.config import settings
from pipelines.common.logging import get_logger
from pipelines.connectors.shopee._parser import parse_brand
from pipelines.connectors.shopee._parser_ads import parse_brand_ads
from pipelines.connectors.shopee._parser_shop_stats import parse_brand_shop_stats

logger = get_logger(__name__)

BRANDS_IN_SCOPE = ("apice", "barbours", "kokeshi", "lescent", "rituaria")


def fetch(date_from: date, date_to: date) -> list[dict]:
    """
    LÃª todos os arquivos de todas as brands e filtra pelo perÃ­odo.
    Retorna lista de dicts diÃ¡rios prontos para o transform.
    """
    data_path = Path(settings.shopee_data_path)
    if not data_path.exists():
        raise FileNotFoundError(
            f"SHOPEE_DATA_PATH nÃ£o encontrado: {data_path}. "
            "Configure a variÃ¡vel de ambiente SHOPEE_DATA_PATH."
        )

    logger.info(
        "Shopee: lendo arquivos em %s, perÃ­odo %s â†’ %s",
        data_path, date_from, date_to,
    )

    all_rows: list[dict] = []
    for brand in BRANDS_IN_SCOPE:
        brand_rows = parse_brand(data_path, brand)
        filtered = [r for r in brand_rows if date_from <= r["date"] <= date_to]
        logger.info("Shopee/%s: %d dias no perÃ­odo solicitado", brand, len(filtered))
        all_rows.extend(filtered)

    logger.info("Shopee: total %d linhas diÃ¡rias em %d brands", len(all_rows), len(BRANDS_IN_SCOPE))
    return all_rows


def fetch_incremental(days_back: int = 3) -> list[dict]:
    """Ãšltimos N dias â€” para sync diÃ¡rio incremental."""
    today = date.today()
    return fetch(today - timedelta(days=days_back), today)


def fetch_backfill(days_back: int = 150) -> list[dict]:
    """Backfill histÃ³rico â€” padrÃ£o 150 dias (~5 meses, cobrindo jan-mai 2026)."""
    today = date.today()
    return fetch(today - timedelta(days=days_back), today)


# --- Shop stats (Fase 2: funil â€” visitantes, conversÃ£o, novos compradores) ---

def fetch_shop_stats(date_from: date, date_to: date) -> list[dict]:
    """LÃª shop-stats de todas as brands e filtra pelo perÃ­odo."""
    data_path = Path(settings.shopee_data_path)
    if not data_path.exists():
        raise FileNotFoundError(
            f"SHOPEE_DATA_PATH nÃ£o encontrado: {data_path}. "
            "Configure a variÃ¡vel de ambiente SHOPEE_DATA_PATH."
        )

    logger.info(
        "Shopee shop-stats: lendo %s, perÃ­odo %s â†’ %s",
        data_path, date_from, date_to,
    )

    all_rows: list[dict] = []
    for brand in BRANDS_IN_SCOPE:
        brand_rows = parse_brand_shop_stats(data_path, brand)
        filtered = [r for r in brand_rows if date_from <= r["date"] <= date_to]
        logger.info("Shopee shop-stats/%s: %d dias no perÃ­odo", brand, len(filtered))
        all_rows.extend(filtered)

    logger.info("Shopee shop-stats: total %d linhas em %d brands", len(all_rows), len(BRANDS_IN_SCOPE))
    return all_rows


def fetch_shop_stats_incremental(days_back: int = 3) -> list[dict]:
    today = date.today()
    return fetch_shop_stats(today - timedelta(days=days_back), today)


def fetch_shop_stats_backfill(days_back: int = 150) -> list[dict]:
    today = date.today()
    return fetch_shop_stats(today - timedelta(days=days_back), today)


# --- Ads (Fase 3: spend, revenue, impressÃµes, clicks â€” mÃ©dias diÃ¡rias do perÃ­odo) ---

def fetch_ads(date_from: date, date_to: date) -> list[dict]:
    """LÃª CSVs de ads de todas as brands e filtra pelo perÃ­odo."""
    data_path = Path(settings.shopee_data_path)
    if not data_path.exists():
        raise FileNotFoundError(
            f"SHOPEE_DATA_PATH nÃ£o encontrado: {data_path}. "
            "Configure a variÃ¡vel de ambiente SHOPEE_DATA_PATH."
        )

    logger.info("Shopee ads: lendo %s, perÃ­odo %s â†’ %s", data_path, date_from, date_to)

    all_rows: list[dict] = []
    for brand in BRANDS_IN_SCOPE:
        brand_rows = parse_brand_ads(data_path, brand)
        filtered = [r for r in brand_rows if date_from <= r["date"] <= date_to]
        logger.info("Shopee ads/%s: %d dias no perÃ­odo", brand, len(filtered))
        all_rows.extend(filtered)

    logger.info("Shopee ads: total %d linhas em %d brands", len(all_rows), len(BRANDS_IN_SCOPE))
    return all_rows


def fetch_ads_incremental(days_back: int = 3) -> list[dict]:
    today = date.today()
    return fetch_ads(today - timedelta(days=days_back), today)


def fetch_ads_backfill(days_back: int = 150) -> list[dict]:
    today = date.today()
    return fetch_ads(today - timedelta(days=days_back), today)


