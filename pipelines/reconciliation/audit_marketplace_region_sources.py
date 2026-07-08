"""
Auditoria read-only das fontes usadas pelo design da Gold regional
(gold.marketplace_region_daily, draft): equivalencia de snapshots Shopee e
cobertura ML (shipping_id -> shipment -> shipment_cost).

Reusa o mesmo mecanismo de conexao ja validado em pipelines/common/db.py
(SettingsConfigDict(env_file=".env") resolvido a partir do cwd — por isso
este modulo, como os demais em pipelines/, deve ser executado a partir da
RAIZ do repositorio: `python -m pipelines.reconciliation.audit_marketplace_region_sources`,
nunca a partir de apps/api/).

Regras de seguranca (nao negociaveis):
  - toda conexao e aberta com `postgresql_readonly=True` + `SET
    default_transaction_read_only = on`; nunca executa DDL/DML.
  - nunca imprime/retorna CPF, nome, telefone, endereco, CEP, order_id ou
    filename — as funcoes de query so retornam agregados (contagens, somas,
    listas de (marca, mes, bucket) etc.), nunca a linha bruta.
  - a logica de CLASSIFICACAO (equivalencia de snapshot, bucket de
    cobertura) e pura — recebe dicionarios ja agregados, sem tocar o banco —
    para poder ser testada com conexoes falsas (ver
    pipelines/tests/test_audit_marketplace_region_sources.py).

Uso:
    python -m pipelines.reconciliation.audit_marketplace_region_sources
    python -m pipelines.reconciliation.audit_marketplace_region_sources --only shopee
    python -m pipelines.reconciliation.audit_marketplace_region_sources --only ml
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from pipelines.common.db import _datamart_engine  # noqa: F401 -- reexportado para testes


class NotReadOnlyError(RuntimeError):
    """Levantado quando a engine/conexao fornecida nao pode ser confirmada
    como read-only — nunca prosseguir com uma auditoria em conexao de
    escrita."""


def get_readonly_datamart_engine() -> Engine:
    """Retorna a engine do Data Mart (pipelines.common.db), com o mesmo
    mecanismo de configuracao ja validado no restante de pipelines/. Levanta
    RuntimeError sanitizado (sem credenciais) se nao estiver configurada."""
    from pipelines.common.db import _datamart_engine as engine

    if engine is None:
        raise RuntimeError("Data Mart nao configurado: defina DATAMART_DATABASE_URL ou DATAMART_*.")
    return engine


def check_readonly_connection(engine: Engine) -> dict[str, bool]:
    """Confirma SELECT 1=1 e pg_is_in_recovery() numa conexao explicitamente
    marcada como read-only. Nunca imprime host/usuario/senha — so booleans."""
    with engine.connect() as conn:
        conn = conn.execution_options(postgresql_readonly=True)
        conn.execute(text("SET default_transaction_read_only = on"))
        one_eq_one = bool(conn.execute(text("SELECT 1 = 1")).scalar())
        in_recovery = bool(conn.execute(text("SELECT pg_is_in_recovery()")).scalar())
    return {"select_1_eq_1": one_eq_one, "pg_is_in_recovery": in_recovery}


def _readonly_conn(engine: Engine):
    """Abre uma conexao e RECUSA segui-la se o Postgres nao confirmar o modo
    read-only — nunca confia so na intencao (`postgresql_readonly=True`),
    verifica o `current_setting` de volta antes de liberar qualquer query."""
    conn = engine.connect()
    conn = conn.execution_options(postgresql_readonly=True)
    conn.execute(text("SET default_transaction_read_only = on"))
    confirmed = conn.execute(text("SELECT current_setting('transaction_read_only')")).scalar()
    if confirmed != "on":
        conn.close()
        raise NotReadOnlyError(
            "Conexao nao confirmada como read-only (transaction_read_only != 'on') — auditoria abortada."
        )
    return conn


# ---------------------------------------------------------------------------
# Gate 4A — Shopee: equivalencia de snapshots sobrepostos (>1 file_id)
# ---------------------------------------------------------------------------

SHOPEE_ITEM_FIELDS = ("n_linhas", "sku_multiset_sig", "soma_quantity", "soma_returned_quantity")
SHOPEE_FINANCIAL_FIELDS = (
    "soma_product_subtotal", "order_amount", "order_grand_total",
    "buyer_paid_shipping_fee", "estimated_shipping_fee", "reverse_shipping_fee",
    "soma_transaction_fee", "soma_commission_fee_gross", "soma_commission_fee_net",
    "soma_service_fee_gross", "soma_service_fee_net",
)
SHOPEE_STATUS_FIELDS = ("order_status", "return_refund_status")
SHOPEE_DATE_FIELDS = ("delivered_date", "cancel_completed_date")
SHOPEE_GEO_FIELDS = ("delivery_city", "delivery_state")


def classify_shopee_order_snapshots(snapshots: list[dict[str, Any]]) -> str:
    """Classifica um pedido com >1 snapshot (file_id) numa categoria de
    diferenca, comparando os campos agregados (grao pedido x arquivo) —
    funcao pura, sem acesso a banco (testavel com fixtures).

    `snapshots` e a lista de aggregate-rows do pedido (uma por file_id), com
    as chaves definidas em SHOPEE_*_FIELDS mais "order_amount" (usado como
    proxy de GMV pelo chamador).
    """
    def _varies(fields: Iterable[str]) -> bool:
        return any(len({s.get(f) for s in snapshots}) > 1 for f in fields)

    diff_itens = _varies(SHOPEE_ITEM_FIELDS)
    diff_financeiro = _varies(SHOPEE_FINANCIAL_FIELDS)
    diff_status = _varies(SHOPEE_STATUS_FIELDS)
    diff_datas = _varies(SHOPEE_DATE_FIELDS)
    diff_geo = _varies(SHOPEE_GEO_FIELDS)

    n_categorias = sum([diff_itens, diff_financeiro, diff_status, diff_datas, diff_geo])
    if n_categorias == 0:
        return "exatamente_equivalente"
    if n_categorias > 1:
        return "multiplas_diferencas"
    if diff_itens:
        return "diferenca_de_itens_sku_qtd"
    if diff_financeiro:
        return "diferenca_financeira"
    if diff_status:
        return "diferenca_de_status"
    if diff_datas:
        return "diferenca_de_datas"
    return "diferenca_geografica"


_SHOPEE_SIGNATURE_SQL = """
WITH per_order_file AS (
    SELECT
        brand, order_id, file_id,
        COUNT(*) AS n_linhas,
        SUM(quantity) AS soma_quantity,
        SUM(returned_quantity) AS soma_returned_quantity,
        SUM(product_subtotal) AS soma_product_subtotal,
        MAX(order_amount) AS order_amount,
        MAX(order_grand_total) AS order_grand_total,
        MAX(buyer_paid_shipping_fee) AS buyer_paid_shipping_fee,
        MAX(estimated_shipping_fee) AS estimated_shipping_fee,
        MAX(reverse_shipping_fee) AS reverse_shipping_fee,
        SUM(transaction_fee) AS soma_transaction_fee,
        SUM(commission_fee_gross) AS soma_commission_fee_gross,
        SUM(commission_fee_net) AS soma_commission_fee_net,
        SUM(service_fee_gross) AS soma_service_fee_gross,
        SUM(service_fee_net) AS soma_service_fee_net,
        MAX(order_status) AS order_status,
        MAX(return_refund_status) AS return_refund_status,
        MAX(delivered_date) AS delivered_date,
        MAX(cancel_completed_date) AS cancel_completed_date,
        MAX(delivery_city) AS delivery_city,
        MAX(delivery_state) AS delivery_state,
        string_agg(sku_ref || ':' || COALESCE(variation_name, '') || ':' || quantity::text,
                    '|' ORDER BY sku_ref, variation_name, quantity) AS sku_multiset_sig
    FROM silver.stg_shopee_order_item_snapshots
    GROUP BY brand, order_id, file_id
),
overlap_orders AS (
    SELECT brand, order_id FROM per_order_file GROUP BY brand, order_id HAVING COUNT(*) > 1
)
SELECT pof.* FROM per_order_file pof
JOIN overlap_orders oo ON oo.brand = pof.brand AND oo.order_id = pof.order_id
"""


@dataclass
class ShopeeSnapshotAuditResult:
    total_pedidos_overlap: int = 0
    categorias: dict[str, dict[str, float]] = field(default_factory=dict)  # categoria -> {pedidos, gmv}


def audit_shopee_snapshot_equivalence(engine: Engine) -> ShopeeSnapshotAuditResult:
    """Gate 4A: classifica todo pedido Shopee com overlap de file_id.
    So retorna agregados (nunca order_id)."""
    with _readonly_conn(engine) as conn:
        rows = [dict(r) for r in conn.execute(text(_SHOPEE_SIGNATURE_SQL)).mappings().all()]

    by_order: dict[tuple[str, Any], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_order[(r["brand"], r["order_id"])].append(r)

    result = ShopeeSnapshotAuditResult(total_pedidos_overlap=len(by_order))
    for snaps in by_order.values():
        cat = classify_shopee_order_snapshots(snaps)
        bucket = result.categorias.setdefault(cat, {"pedidos": 0, "gmv": 0.0})
        bucket["pedidos"] += 1
        bucket["gmv"] += float(max((s.get("order_amount") or 0) for s in snaps))
    return result


def audit_shopee_file_id_monotonicity(engine: Engine) -> dict[str, Any]:
    """Confirma que MAX(file_id) por pedido coincide com MAX(raw_ingested_at)
    — pre-requisito para usar file_id como criterio de "snapshot mais
    recente" (ver docstring do modulo)."""
    sql = """
        WITH per_order_file AS (
            SELECT brand, order_id, file_id, MAX(raw_ingested_at) AS ingested_at
            FROM silver.stg_shopee_order_item_snapshots
            GROUP BY brand, order_id, file_id
        ),
        overlap AS (
            SELECT brand, order_id FROM per_order_file GROUP BY brand, order_id HAVING COUNT(*) > 1
        ),
        ranked AS (
            SELECT pof.brand, pof.order_id, pof.file_id, pof.ingested_at,
                   pof.file_id = MAX(pof.file_id) OVER (PARTITION BY pof.brand, pof.order_id) AS is_max_file_id,
                   pof.ingested_at = MAX(pof.ingested_at) OVER (PARTITION BY pof.brand, pof.order_id) AS is_max_ingested_at
            FROM per_order_file pof
            JOIN overlap o ON o.brand = pof.brand AND o.order_id = pof.order_id
        )
        SELECT COUNT(*) FROM (
            SELECT brand, order_id FROM ranked
            GROUP BY brand, order_id
            HAVING COUNT(*) FILTER (WHERE is_max_file_id AND NOT is_max_ingested_at) > 0
                OR COUNT(*) FILTER (WHERE is_max_ingested_at AND NOT is_max_file_id) > 0
        ) x
    """
    file_level_sql = """
        SELECT COUNT(*) FROM (
            SELECT file_id, ingested_at,
                   LAG(file_id) OVER (ORDER BY ingested_at) AS prev_file_id
            FROM raw.shopee_ingestion_file
        ) t WHERE prev_file_id IS NOT NULL AND file_id < prev_file_id
    """
    with _readonly_conn(engine) as conn:
        pedidos_inconsistentes = conn.execute(text(sql)).scalar()
        inversoes_arquivo = conn.execute(text(file_level_sql)).scalar()
    return {
        "pedidos_onde_max_file_id_diverge_de_max_ingested_at": pedidos_inconsistentes,
        "inversoes_file_id_vs_ingested_at_no_nivel_arquivo": inversoes_arquivo,
        "monotonico": pedidos_inconsistentes == 0 and inversoes_arquivo == 0,
    }


def audit_shopee_dedup_reconciliation(engine: Engine) -> dict[str, Any]:
    """Reconciliacao antes/depois da regra de dedup (MAX(file_id) por
    pedido, mantendo todas as linhas do arquivo vencedor)."""
    sql = """
        WITH por_pedido_arquivo AS (
            SELECT brand, order_id, file_id, MAX(order_amount) AS order_amount
            FROM silver.stg_shopee_order_item_snapshots
            GROUP BY brand, order_id, file_id
        ),
        antes AS (SELECT SUM(order_amount) AS gmv, COUNT(*) AS n FROM por_pedido_arquivo),
        depois AS (
            SELECT SUM(order_amount) AS gmv, COUNT(*) AS n FROM (
                SELECT DISTINCT ON (brand, order_id) brand, order_id, order_amount
                FROM por_pedido_arquivo ORDER BY brand, order_id, file_id DESC
            ) t
        )
        SELECT 'antes' AS versao, gmv, n FROM antes
        UNION ALL SELECT 'depois', gmv, n FROM depois
    """
    with _readonly_conn(engine) as conn:
        rows = conn.execute(text(sql)).mappings().all()
    by_versao = {r["versao"]: {"gmv": float(r["gmv"]), "pedidos_ou_combinacoes": r["n"]} for r in rows}
    return by_versao


# ---------------------------------------------------------------------------
# Gate 4B — ML: cardinalidade e cobertura shipping_id -> shipment -> cost
# ---------------------------------------------------------------------------

COVERAGE_BUCKETS = (
    "shipping_id_ausente", "shipment_ausente", "shipment_sem_uf",
    "shipment_cost_ausente", "completo",
)


def classify_ml_order_coverage(shipping_id: Any, shipment_found: bool, has_uf: bool, cost_found: bool) -> str:
    """Classificacao pura (sem banco) de um pedido ML num bucket de
    cobertura — usada tanto pela query agregada quanto pelos testes."""
    if shipping_id is None:
        return "shipping_id_ausente"
    if not shipment_found:
        return "shipment_ausente"
    if not has_uf:
        return "shipment_sem_uf"
    if not cost_found:
        return "shipment_cost_ausente"
    return "completo"


def audit_ml_cardinality(engine: Engine, schema: str = "silver", orders_table: str = "stg_ml_orders",
                          shipments_table: str = "stg_ml_shipments", costs_table: str = "stg_ml_shipment_costs") -> dict[str, Any]:
    """Gate 4B, pre-requisito: confirma unicidade de chave e ausencia de
    join ambiguo/multiplicador ANTES de qualquer classificacao de cobertura."""
    sql = f"""
        SELECT
            (SELECT COUNT(*) FROM {schema}.{orders_table}) AS total_pedidos,
            (SELECT COUNT(*) FROM (SELECT brand, order_id FROM {schema}.{orders_table} GROUP BY brand, order_id HAVING COUNT(*) > 1) t) AS pedidos_duplicados,
            (SELECT COUNT(*) FROM {schema}.{orders_table} WHERE shipping_id IS NULL) AS pedidos_sem_shipping_id,
            (SELECT COUNT(*) FROM {schema}.{shipments_table}) AS total_shipments,
            (SELECT COUNT(*) FROM (SELECT brand, shipment_id FROM {schema}.{shipments_table} GROUP BY brand, shipment_id HAVING COUNT(*) > 1) t) AS shipments_duplicados,
            (SELECT COUNT(*) FROM {schema}.{costs_table}) AS total_costs,
            (SELECT COUNT(*) FROM (SELECT brand, shipment_id FROM {schema}.{costs_table} GROUP BY brand, shipment_id HAVING COUNT(*) > 1) t) AS costs_duplicados,
            (SELECT COUNT(*) FROM (
                SELECT o.brand, o.order_id, COUNT(sh.shipment_id) AS n
                FROM {schema}.{orders_table} o
                LEFT JOIN {schema}.{shipments_table} sh ON sh.brand = o.brand AND sh.shipment_id = o.shipping_id
                WHERE o.shipping_id IS NOT NULL
                GROUP BY o.brand, o.order_id HAVING COUNT(sh.shipment_id) > 1
            ) t) AS pedidos_com_join_ambiguo,
            (SELECT SUM(total_amount) FROM {schema}.{orders_table} WHERE status = 'paid') AS gmv_antes_do_join,
            (SELECT SUM(o.total_amount) FROM {schema}.{orders_table} o
                LEFT JOIN {schema}.{shipments_table} sh ON sh.brand = o.brand AND sh.shipment_id = o.shipping_id
                LEFT JOIN {schema}.{costs_table} sc ON sc.brand = o.brand AND sc.shipment_id = o.shipping_id
                WHERE o.status = 'paid') AS gmv_depois_do_join
    """
    with _readonly_conn(engine) as conn:
        row = dict(conn.execute(text(sql)).mappings().first())
    row["gmv_multiplicado_pelo_join"] = row["gmv_antes_do_join"] != row["gmv_depois_do_join"]
    return row


def audit_ml_coverage_by_brand_month(engine: Engine, schema: str = "silver",
                                      orders_table: str = "stg_ml_orders",
                                      shipments_table: str = "stg_ml_shipments",
                                      costs_table: str = "stg_ml_shipment_costs") -> list[dict[str, Any]]:
    """Gate 4B: classifica cada pedido pago em um bucket de cobertura, por
    marca e mes — nunca retorna order_id, so contagens agregadas."""
    sql = f"""
        WITH o AS (
            SELECT brand, order_id, status, shipping_id, total_amount,
                   date_trunc('month', date_created)::date AS mes
            FROM {schema}.{orders_table} WHERE status = 'paid'
        ),
        sh AS (SELECT brand, shipment_id, receiver_state FROM {schema}.{shipments_table}),
        sc AS (SELECT brand, shipment_id FROM {schema}.{costs_table})
        SELECT
            o.brand, o.mes,
            CASE
                WHEN o.shipping_id IS NULL THEN 'shipping_id_ausente'
                WHEN sh.shipment_id IS NULL THEN 'shipment_ausente'
                WHEN sh.receiver_state IS NULL THEN 'shipment_sem_uf'
                WHEN sc.shipment_id IS NULL THEN 'shipment_cost_ausente'
                ELSE 'completo'
            END AS bucket,
            COUNT(*) AS pedidos,
            SUM(o.total_amount) AS gmv
        FROM o
        LEFT JOIN sh ON sh.brand = o.brand AND sh.shipment_id = o.shipping_id
        LEFT JOIN sc ON sc.brand = o.brand AND sc.shipment_id = o.shipping_id
        GROUP BY o.brand, o.mes, bucket
        ORDER BY o.brand, o.mes, bucket
    """
    with _readonly_conn(engine) as conn:
        rows = conn.execute(text(sql)).mappings().all()
    return [dict(r) for r in rows]


def audit_ml_staleness_vs_raw(engine: Engine) -> dict[str, Any]:
    """Compara silver.stg_ml_* com raw.ml_* para medir quanto da lacuna de
    cobertura e por atraso de sincronizacao (silver desatualizado) vs lacuna
    real na origem — ambos schemas sao lidos read-only."""
    sql = """
        SELECT
            (SELECT COUNT(*) FROM raw.ml_shipments) AS shipments_raw,
            (SELECT COUNT(*) FROM silver.stg_ml_shipments) AS shipments_silver,
            (SELECT COUNT(*) FROM raw.ml_shipments r
                WHERE NOT EXISTS (SELECT 1 FROM silver.stg_ml_shipments s WHERE r.brand=s.brand AND r.shipment_id=s.shipment_id)
            ) AS shipments_em_raw_ausentes_em_silver,
            (SELECT COUNT(*) FROM raw.ml_orders r
                JOIN silver.stg_ml_orders s ON r.brand=s.brand AND r.order_id=s.order_id
                WHERE r.status IS DISTINCT FROM s.status
            ) AS pedidos_com_status_divergente_raw_vs_silver
    """
    with _readonly_conn(engine) as conn:
        row = dict(conn.execute(text(sql)).mappings().first())
    return row


def audit_ml_staleness_impact_by_brand(engine: Engine) -> list[dict[str, Any]]:
    """Isola o efeito do atraso de silver vs raw: mantem a populacao de
    pedidos fixa (silver.stg_ml_orders, status='paid') e so varia o lado
    shipment/cost entre silver e raw, medindo cobertura e GMV recuperavel
    por marca — usada para decidir se a Gold deve ler de silver ou raw (ver
    docs/regional_design_draft.md secao 1.2c)."""
    sql = """
        WITH o AS (
            SELECT brand, order_id, shipping_id, total_amount
            FROM silver.stg_ml_orders WHERE status = 'paid'
        ),
        sh_silver AS (SELECT brand, shipment_id, receiver_state FROM silver.stg_ml_shipments),
        sc_silver AS (SELECT brand, shipment_id FROM silver.stg_ml_shipment_costs),
        sh_raw AS (SELECT brand, shipment_id, receiver_state FROM raw.ml_shipments),
        sc_raw AS (SELECT brand, shipment_id FROM raw.ml_shipment_costs),
        classificado AS (
            SELECT
                o.brand, o.total_amount,
                CASE
                    WHEN o.shipping_id IS NULL THEN 'shipping_id_ausente'
                    WHEN shs.shipment_id IS NULL THEN 'shipment_ausente'
                    WHEN shs.receiver_state IS NULL THEN 'shipment_sem_uf'
                    WHEN scs.shipment_id IS NULL THEN 'shipment_cost_ausente'
                    ELSE 'completo'
                END AS bucket_silver,
                CASE
                    WHEN o.shipping_id IS NULL THEN 'shipping_id_ausente'
                    WHEN shr.shipment_id IS NULL THEN 'shipment_ausente'
                    WHEN shr.receiver_state IS NULL THEN 'shipment_sem_uf'
                    WHEN scr.shipment_id IS NULL THEN 'shipment_cost_ausente'
                    ELSE 'completo'
                END AS bucket_raw
            FROM o
            LEFT JOIN sh_silver shs ON shs.brand = o.brand AND shs.shipment_id = o.shipping_id
            LEFT JOIN sc_silver scs ON scs.brand = o.brand AND scs.shipment_id = o.shipping_id
            LEFT JOIN sh_raw shr ON shr.brand = o.brand AND shr.shipment_id = o.shipping_id
            LEFT JOIN sc_raw scr ON scr.brand = o.brand AND scr.shipment_id = o.shipping_id
        )
        SELECT
            brand,
            COUNT(*) AS total_pedidos_pagos,
            ROUND(100.0 * COUNT(*) FILTER (WHERE bucket_silver = 'completo') / COUNT(*), 2) AS pct_completo_silver,
            ROUND(100.0 * COUNT(*) FILTER (WHERE bucket_raw = 'completo') / COUNT(*), 2) AS pct_completo_raw,
            SUM(total_amount) FILTER (WHERE bucket_silver != 'completo' AND bucket_raw = 'completo') AS gmv_recuperado_se_usar_raw
        FROM classificado
        GROUP BY brand
        ORDER BY brand
    """
    with _readonly_conn(engine) as conn:
        rows = conn.execute(text(sql)).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Gate 4C — timezone dos timestamps naive (ML/Shopee)
# ---------------------------------------------------------------------------

def audit_order_hour_of_day_histogram(engine: Engine, schema_table: str, column: str) -> list[dict[str, Any]]:
    """Distribuicao por hora do dia de uma coluna `timestamp without time
    zone` de pedidos — usada para inferir se o timestamp ja esta em horario
    local (padrao de compra com vale de madrugada) ou UTC (padrao
    deslocado +3h). Ver docs/regional_design_draft.md secao 1.2d para a
    interpretacao. Nunca retorna a linha do pedido, so a contagem por hora."""
    sql = f"""
        SELECT EXTRACT(hour FROM {column})::int AS hora, COUNT(*) AS n
        FROM {schema_table} WHERE {column} IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """
    with _readonly_conn(engine) as conn:
        rows = conn.execute(text(sql)).fetchall()
    return [{"hora": r[0], "pedidos": r[1]} for r in rows]


def audit_clock_skew_hint(engine: Engine, max_timestamp_table: str, max_timestamp_column: str) -> dict[str, Any]:
    """Compara `now()` em UTC/BRT com o MAX() de um timestamp naive numa
    tabela viva (ingestao continua) — quanto mais proximo de um dos dois
    relogios, mais provavel que essa seja a timezone real do dado. So faz
    sentido para tabelas com ingestao recente/continua (ver ressalva na
    secao 1.2d do design doc)."""
    sql = f"""
        SELECT
            now() AT TIME ZONE 'UTC' AS agora_utc,
            now() AT TIME ZONE 'America/Sao_Paulo' AS agora_brt,
            (SELECT MAX({max_timestamp_column}) FROM {max_timestamp_table}) AS max_timestamp
    """
    with _readonly_conn(engine) as conn:
        row = dict(conn.execute(text(sql)).mappings().first())
    row["gap_para_utc_segundos"] = abs((row["agora_utc"] - row["max_timestamp"]).total_seconds())
    row["gap_para_brt_segundos"] = abs((row["agora_brt"] - row["max_timestamp"]).total_seconds())
    row["mais_provavel"] = "BRT" if row["gap_para_brt_segundos"] < row["gap_para_utc_segundos"] else "UTC"
    return row


# ---------------------------------------------------------------------------
# Contrato de API — coverage_level/coverage_warning (funcoes puras, sem
# banco; ver docs/regional_design_draft.md secao 6 para os thresholds).
# ---------------------------------------------------------------------------

COVERAGE_LEVEL_THRESHOLDS = (
    (80.0, "alta"),
    (50.0, "media"),
)


def classify_coverage_level(min_coverage_pct: float | None) -> str:
    """Classifica o pior dos dois percentuais de cobertura (uf_fill_pct,
    shipping_cost_coverage_pct) num nivel qualitativo — funcao pura, os
    thresholds sao constantes de codigo (COVERAGE_LEVEL_THRESHOLDS), nao
    armazenados no banco, para poderem mudar sem migration."""
    if min_coverage_pct is None:
        return "sem_cobertura"
    for threshold, level in COVERAGE_LEVEL_THRESHOLDS:
        if min_coverage_pct >= threshold:
            return level
    return "baixa"


def coverage_warning(level: str) -> bool:
    """`coverage_warning` e sempre o inverso de `coverage_level == "alta"` —
    nunca um boolean independente do nivel (evita os dois campos divergirem
    por engano em algum caminho de codigo)."""
    return level != "alta"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=["shopee", "ml", "connection", "timezone"], default=None)
    args = parser.parse_args(argv)

    engine = get_readonly_datamart_engine()

    conn_check = check_readonly_connection(engine)
    print(f"Conexao read-only: {conn_check}")
    if not conn_check["select_1_eq_1"] or not conn_check["pg_is_in_recovery"]:
        print("ABORT: conexao nao confirmada como read-only/replica.")
        return 2

    if args.only in (None, "shopee"):
        print("\n=== Gate 4A — Shopee: equivalencia de snapshots ===")
        result = audit_shopee_snapshot_equivalence(engine)
        print(f"Pedidos com overlap de file_id: {result.total_pedidos_overlap}")
        for cat, d in sorted(result.categorias.items(), key=lambda x: -x[1]["pedidos"]):
            print(f"  {cat}: {d['pedidos']} pedidos, GMV R$ {d['gmv']:.2f}")
        print(f"Monotonicidade file_id/raw_ingested_at: {audit_shopee_file_id_monotonicity(engine)}")
        print(f"Reconciliacao dedup: {audit_shopee_dedup_reconciliation(engine)}")

    if args.only in (None, "ml"):
        print("\n=== Gate 4B — ML: cardinalidade e cobertura ===")
        print(f"Cardinalidade: {audit_ml_cardinality(engine)}")
        print(f"Staleness silver vs raw: {audit_ml_staleness_vs_raw(engine)}")
        print(f"Impacto silver vs raw por marca: {audit_ml_staleness_impact_by_brand(engine)}")
        rows = audit_ml_coverage_by_brand_month(engine)
        print(f"Linhas de cobertura por marca/mes/bucket: {len(rows)} (ver docs/regional_design_draft.md para o detalhe)")

    if args.only == "timezone":
        print("\n=== Timezone hint — ML orders (date_created) ===")
        print(audit_clock_skew_hint(engine, "raw.ml_orders", "date_created"))
        print(audit_order_hour_of_day_histogram(engine, "raw.ml_orders", "date_created"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
