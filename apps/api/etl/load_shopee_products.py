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
from urllib.parse import urlsplit

import pandas as pd
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

# Este loader so' deve escrever no PostgreSQL LOCAL (nunca no Neon nem no
# Data Mart/RDS de producao) — exige LOCAL_PG_URL explicitamente, sem
# fallback com credencial hardcoded, e restringe o host a localhost. A
# resolucao e' LAZY (so' dentro de main(), nunca no import do modulo):
# outros scripts (reconcile_bug8_canceled_only.py, monitor_bug8_invariants.py,
# fix_shopee_product_dates.py, diagnose_bug8_neon.py) importam so' as
# funcoes puras deste arquivo (BRANDS, DDL, _aggregate, _load_brand) sem
# precisar de nenhuma conexao — um _get_local_pg_url() eager no topo do
# modulo quebraria esses imports sempre que a variavel nao estivesse
# definida no ambiente de quem so' quer reaproveitar a logica pura.
_ALLOWED_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _sanitize_url(url: str) -> str:
    if not url:
        return "(nao configurado)"
    p = urlsplit(url)
    host = p.hostname or "?"
    port = p.port if p.port is not None else "?"
    db = p.path.lstrip("/") or "?"
    return f"{host}:{port}/{db}"


def _get_local_pg_url() -> str:
    url = os.environ.get("LOCAL_PG_URL", "")
    if not url:
        raise RuntimeError(
            "LOCAL_PG_URL nao definido. Este loader escreve exclusivamente no "
            "PostgreSQL local — a variavel e' exigida explicitamente, sem "
            "fallback com credencial hardcoded, para nunca escrever num banco "
            "nao pretendido (Neon/Data Mart)."
        )
    host = (urlsplit(url).hostname or "").lower()
    if host not in _ALLOWED_LOCAL_HOSTS:
        raise RuntimeError(
            f"LOCAL_PG_URL aponta para um host nao permitido ({_sanitize_url(url)}). "
            f"So' localhost/127.0.0.1/::1 sao aceitos — este loader nunca deve "
            f"escrever num host remoto (Neon/Data Mart)."
        )
    return url


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
    # Os exports Shopee usam "Data de criação do pedido" em formato ISO
    # ("YYYY-MM-DD HH:MM", confirmado em 85/85 arquivos .xlsx do diretório shopee/).
    # dayfirst=True aqui era o bug: para strings ISO, o parser do pandas/dateutil
    # ainda troca os tokens de dia/mês quando dayfirst=True é passado explicitamente,
    # projetando pedidos do dia 1-12 de qualquer mês real (jan-jun/2026) para meses
    # futuros inexistentes (jul-dez/2026). Ver docs/sections/produtos_audit.md.
    df["order_date"] = pd.to_datetime(df["order_date"], format="%Y-%m-%d %H:%M", errors="coerce")
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

    # outer (nao left): um grupo com SOMENTE pedidos "Cancelado" (zero
    # "Concluido") nao existe em agg_completed e seria descartado inteiro
    # pelo left merge, subestimando canceled_orders/cancel_rate_pct (Bug 8,
    # ver docs/sections/produtos_audit.md). gmv/units_sold ficam 0 e
    # unique_buyers fica 0 para esses grupos — nunique() e' calculado so'
    # sobre compradores de pedidos concluidos, nunca sobre cancelados.
    result = agg_completed.merge(agg_canceled, on=grp_cols, how="outer")
    result["gmv"] = result["gmv"].fillna(0.0)
    result["units_sold"] = result["units_sold"].fillna(0).astype(int)
    result["completed_orders"] = result["completed_orders"].fillna(0).astype(int)
    result["unique_buyers"] = result["unique_buyers"].fillna(0).astype(int)
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
    local_pg_url = _get_local_pg_url()
    print(f"PostgreSQL local (destino): {_sanitize_url(local_pg_url)}")
    engine = create_engine(local_pg_url)

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
