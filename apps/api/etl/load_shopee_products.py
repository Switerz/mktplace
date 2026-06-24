"""
ETL: carrega ficheiros XLSX de pedidos Shopee em marts.fact_shopee_product_monthly.

Uso:
    cd apps/api
    python -m etl.load_shopee_products
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/mktplace_control",
)

SHOPEE_ROOT = Path(r"C:\Users\Notebook\Desktop\mktplace\shopee")
BRANDS = ["apice", "barbours", "kokeshi", "lescent", "rituaria"]

# Mapeamento colunas XLSX → nomes internos
COL_MAP = {
    "Data de criação do pedido": "order_date",
    "Nº de referência do SKU principal": "sku_ref",
    "Nome do Produto": "product_name",
    "Nome da variação": "variation_name",
    "Quantidade": "qty",
    "Subtotal do produto": "subtotal",
    "Status do pedido": "status",
    "Nome de usuário (comprador)": "buyer_username",
}

DDL = """
CREATE SCHEMA IF NOT EXISTS marts;

CREATE TABLE IF NOT EXISTS marts.fact_shopee_product_monthly (
    id               SERIAL PRIMARY KEY,
    ref_month        DATE NOT NULL,
    brand            VARCHAR(50) NOT NULL,
    sku_ref          VARCHAR(100),
    sku_ref_key      VARCHAR(100) NOT NULL DEFAULT '',
    product_name     VARCHAR(500) NOT NULL,
    variation_name   VARCHAR(200),
    gmv              NUMERIC(18,2) DEFAULT 0,
    units_sold       BIGINT DEFAULT 0,
    completed_orders BIGINT DEFAULT 0,
    canceled_orders  BIGINT DEFAULT 0,
    cancel_rate_pct  NUMERIC(8,4),
    unique_buyers    BIGINT DEFAULT 0,
    avg_price        NUMERIC(14,2),
    UNIQUE (ref_month, brand, sku_ref_key, product_name)
);
"""

UPSERT_SQL = """
INSERT INTO marts.fact_shopee_product_monthly
    (ref_month, brand, sku_ref, sku_ref_key, product_name, variation_name,
     gmv, units_sold, completed_orders, canceled_orders,
     cancel_rate_pct, unique_buyers, avg_price)
VALUES
    (:ref_month, :brand, :sku_ref, :sku_ref_key, :product_name, :variation_name,
     :gmv, :units_sold, :completed_orders, :canceled_orders,
     :cancel_rate_pct, :unique_buyers, :avg_price)
ON CONFLICT (ref_month, brand, sku_ref_key, product_name)
DO UPDATE SET
    sku_ref          = EXCLUDED.sku_ref,
    variation_name   = EXCLUDED.variation_name,
    gmv              = EXCLUDED.gmv,
    units_sold       = EXCLUDED.units_sold,
    completed_orders = EXCLUDED.completed_orders,
    canceled_orders  = EXCLUDED.canceled_orders,
    cancel_rate_pct  = EXCLUDED.cancel_rate_pct,
    unique_buyers    = EXCLUDED.unique_buyers,
    avg_price        = EXCLUDED.avg_price
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_xlsx(brand_dir: Path) -> list[Path]:
    """Devolve todos os XLSX com 'order' no nome (case-insensitive)."""
    return [
        p for p in brand_dir.glob("*.xlsx")
        if re.search(r"order", p.name, re.IGNORECASE)
    ]


def _clean_numeric(series: pd.Series) -> pd.Series:
    """Converte série que pode ter vírgula decimal para float."""
    return (
        series.astype(str)
        .str.replace(r"\s", "", regex=True)
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0.0)
    )


def _load_brand(brand: str) -> pd.DataFrame | None:
    brand_dir = SHOPEE_ROOT / brand
    files = _find_xlsx(brand_dir)
    if not files:
        print(f"  [{brand}] nenhum ficheiro Order encontrado — ignorado.")
        return None

    frames = []
    for f in sorted(files):
        try:
            df = pd.read_excel(f, dtype=str)
            frames.append(df)
        except Exception as e:
            print(f"  [{brand}] erro a ler {f.name}: {e}")

    if not frames:
        return None

    raw = pd.concat(frames, ignore_index=True)

    # Normalizar nomes de colunas
    raw.columns = [c.strip() for c in raw.columns]

    # Renomear para nomes internos (ignora colunas ausentes)
    rename = {k: v for k, v in COL_MAP.items() if k in raw.columns}
    df = raw.rename(columns=rename)

    # Garantir que colunas obrigatórias existem
    for col in ["order_date", "product_name", "status"]:
        if col not in df.columns:
            print(f"  [{brand}] coluna obrigatória ausente: {col} — ignorado.")
            return None

    for col in ["sku_ref", "variation_name", "qty", "subtotal", "buyer_username"]:
        if col not in df.columns:
            df[col] = None

    # Tipos
    df["order_date"] = pd.to_datetime(df["order_date"], dayfirst=True, errors="coerce")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce").fillna(0).astype(int)
    df["subtotal"] = _clean_numeric(df["subtotal"])
    df["status"] = df["status"].fillna("").str.strip()
    df["brand"] = brand

    # ref_month = primeiro dia do mês
    df["ref_month"] = df["order_date"].dt.to_period("M").dt.to_timestamp()

    # Remover linhas sem data ou produto
    df = df.dropna(subset=["order_date", "product_name", "ref_month"])
    df["product_name"] = df["product_name"].astype(str).str.strip()

    return df


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    grp_cols = ["brand", "ref_month", "sku_ref", "product_name", "variation_name"]

    completed = df[df["status"] == "Concluído"].copy()
    canceled  = df[df["status"] == "Cancelado"].copy()

    agg_completed = (
        completed.groupby(grp_cols, dropna=False)
        .agg(
            gmv=("subtotal", "sum"),
            units_sold=("qty", "sum"),
            completed_orders=("status", "count"),
            unique_buyers=("buyer_username", "nunique"),
        )
        .reset_index()
    )

    agg_canceled = (
        canceled.groupby(grp_cols, dropna=False)
        .agg(canceled_orders=("status", "count"))
        .reset_index()
    )

    result = agg_completed.merge(agg_canceled, on=grp_cols, how="left")
    result["canceled_orders"] = result["canceled_orders"].fillna(0).astype(int)

    total_orders = result["completed_orders"] + result["canceled_orders"]
    result["cancel_rate_pct"] = [
        round(result.loc[i, "canceled_orders"] / total_orders[i] * 100, 4)
        if total_orders[i] > 0 else None
        for i in result.index
    ]
    result["avg_price"] = [
        round(result.loc[i, "gmv"] / result.loc[i, "units_sold"], 2)
        if result.loc[i, "units_sold"] > 0 else None
        for i in result.index
    ]

    # sku_ref_key: substitui NULL por '' para a constraint UNIQUE
    result["sku_ref_key"] = result["sku_ref"].fillna("").astype(str)

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    engine = create_engine(DATABASE_URL)

    with engine.begin() as conn:
        conn.execute(text(DDL))
    print("Schema/tabela verificados.")

    total_inserted = 0

    for brand in BRANDS:
        print(f"\n[{brand}] a carregar...")
        df = _load_brand(brand)
        if df is None:
            continue

        agg = _aggregate(df)
        print(f"  {len(agg)} linhas agregadas.")

        rows_inserted = 0
        with engine.begin() as conn:
            for _, row in agg.iterrows():
                ref_month_val = row["ref_month"]
                if pd.isna(ref_month_val):
                    continue
                params = {
                    "ref_month":        ref_month_val.date().isoformat(),
                    "brand":            brand,
                    "sku_ref":          row["sku_ref"] if pd.notna(row["sku_ref"]) else None,
                    "sku_ref_key":      row["sku_ref_key"],
                    "product_name":     row["product_name"],
                    "variation_name":   row["variation_name"] if pd.notna(row.get("variation_name")) else None,
                    "gmv":              float(row["gmv"]),
                    "units_sold":       int(row["units_sold"]),
                    "completed_orders": int(row["completed_orders"]),
                    "canceled_orders":  int(row["canceled_orders"]),
                    "cancel_rate_pct":  float(row["cancel_rate_pct"]) if row["cancel_rate_pct"] is not None and pd.notna(row["cancel_rate_pct"]) else None,
                    "unique_buyers":    int(row["unique_buyers"]),
                    "avg_price":        float(row["avg_price"]) if row["avg_price"] is not None and pd.notna(row["avg_price"]) else None,
                }
                conn.execute(text(UPSERT_SQL), params)
                rows_inserted += 1

        print(f"  {rows_inserted} linhas inseridas/actualizadas.")
        total_inserted += rows_inserted

    print(f"\nTotal: {total_inserted} linhas carregadas.")


if __name__ == "__main__":
    main()
