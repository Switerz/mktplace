"""
Monitor READ-ONLY pos-carga do Bug 8 (marts.fact_shopee_product_monthly).

Roda apos qualquer carga futura do ETL Shopee para confirmar que a correcao
do Bug 8 (merge outer — grupos so-cancelados preservados) continua valendo.
Valida INVARIANTES, nunca snapshots historicos: nenhum numero fixo tipo
"2.471 linhas" ou "53.599 cancelamentos" e' usado como regra, porque cargas
futuras mudam esses totais legitimamente.

Duas camadas:

  1. Invariantes internas do mart (sempre rodam, so' Neon, somente leitura):
     - zero duplicatas na chave unica (ref_month, brand, sku_ref_key, product_name);
     - zero nulos em campos obrigatorios;
     - nenhuma metrica negativa (gmv, units_sold, completed_orders, canceled_orders);
     - linhas so-canceladas (completed=0, canceled>0) coerentes: gmv=0,
       units_sold=0 e cancel_rate_pct=100 — se um dia aparecer uma linha
       so-cancelada com gmv>0, algo upstream esta errado;
     - cancel_rate_pct consistente com canceled/(completed+canceled) em
       todas as linhas (tolerancia de arredondamento);
     - contagem informativa de linhas so-canceladas (se uma carga futura
       zerar essa contagem num mes que a fonte tem cancelamentos isolados,
       a camada 2 pega a divergencia).

  2. Reconciliacao contra a fonte XLSX local (opcional — roda quando o
     diretorio shopee/ existe; pular com --skip-source): reprocessa os
     arquivos em MEMORIA com o proprio _aggregate corrigido do ETL (nenhuma
     escrita em lugar nenhum) e compara, por marca x mes, os agregados de
     gmv / units_sold / completed_orders / canceled_orders contra o Neon.
     Esses somatorios por mes sao invariantes sob a soma de colisoes de
     variation_name (Bug 5), entao a comparacao vale independentemente de
     como as linhas colididas foram consolidadas. Se uma carga futura
     regredir ao left merge (Bug 8), canceled_orders do Neon fica menor que
     o da fonte e este monitor falha.

Garantias operacionais:
  - NUNCA le nem escreve DATAMART_DATABASE_URL (Data Mart/RDS);
  - NUNCA executa ETL, backfill, migration ou sincronizacao;
  - conexao Neon aberta com a sessao explicitamente readonly;
  - URLs sempre sanitizadas (host:porta/database, nunca usuario/senha);
  - exit code 1 em qualquer divergencia, 0 se tudo passar.

Uso:
    python -m pipelines.reconciliation.monitor_bug8_invariants
    python -m pipelines.reconciliation.monitor_bug8_invariants --skip-source
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from pipelines.reconciliation.diagnose_bug8_neon import (  # noqa: E402
    REAL_TABLE,
    _get_neon_url,
    _neon_readonly,
    _sanitize_url,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SHOPEE_SOURCE_DIR = REPO_ROOT / "shopee"

# Tolerancia para comparar percentuais/valores recalculados (arredondamento
# de gravacao, nao de negocio).
RATE_TOLERANCE = 0.01
MONEY_TOLERANCE = 0.01


# ---------------------------------------------------------------------------
# Camada 1 — invariantes internas do mart (so' Neon, somente leitura)
# ---------------------------------------------------------------------------
def check_db_invariants(conn) -> list[str]:
    problems: list[str] = []
    cur = conn.cursor()

    cur.execute(f"""
        SELECT COUNT(*) AS n FROM (
            SELECT ref_month, brand, sku_ref_key, product_name
            FROM marts.{REAL_TABLE}
            GROUP BY ref_month, brand, sku_ref_key, product_name
            HAVING COUNT(*) > 1
        ) d
    """)
    dupes = cur.fetchone()["n"]
    if dupes:
        problems.append(f"{dupes} chave(s) duplicada(s) na chave unica do mart")

    cur.execute(f"""
        SELECT COUNT(*) AS n FROM marts.{REAL_TABLE}
        WHERE ref_month IS NULL OR brand IS NULL OR product_name IS NULL OR sku_ref_key IS NULL
    """)
    nulls = cur.fetchone()["n"]
    if nulls:
        problems.append(f"{nulls} linha(s) com NULL em campo obrigatorio")

    cur.execute(f"""
        SELECT COUNT(*) AS n FROM marts.{REAL_TABLE}
        WHERE gmv < 0 OR units_sold < 0 OR completed_orders < 0 OR canceled_orders < 0
    """)
    negatives = cur.fetchone()["n"]
    if negatives:
        problems.append(f"{negatives} linha(s) com metrica negativa")

    # Linhas so-canceladas devem ser coerentes: sem receita, sem unidades,
    # cancel_rate 100%. Uma linha so-cancelada com gmv>0 indicaria que o
    # ETL somou receita de pedido cancelado — nunca deve acontecer.
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM marts.{REAL_TABLE}
        WHERE completed_orders = 0 AND canceled_orders > 0
          AND (gmv <> 0 OR units_sold <> 0 OR cancel_rate_pct IS DISTINCT FROM 100)
    """)
    incoherent = cur.fetchone()["n"]
    if incoherent:
        problems.append(f"{incoherent} linha(s) so-cancelada(s) incoerente(s) (gmv/units nao-zero ou cancel_rate != 100)")

    # cancel_rate_pct deve refletir canceled/(completed+canceled) em toda linha
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM marts.{REAL_TABLE}
        WHERE (completed_orders + canceled_orders) > 0
          AND ABS(COALESCE(cancel_rate_pct, -1)
                  - ROUND(canceled_orders::numeric / (completed_orders + canceled_orders) * 100, 4)) > %s
    """, (RATE_TOLERANCE,))
    bad_rate = cur.fetchone()["n"]
    if bad_rate:
        problems.append(f"{bad_rate} linha(s) com cancel_rate_pct inconsistente com canceled/(completed+canceled)")

    # Informativo (nao e' falha por si so'): presenca de linhas so-canceladas.
    # Zero aqui, num mes em que a fonte TEM cancelamentos isolados, indicaria
    # regressao ao left merge — a camada 2 (fonte) transforma isso em falha.
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM marts.{REAL_TABLE}
        WHERE completed_orders = 0 AND canceled_orders > 0
    """)
    cancel_only = cur.fetchone()["n"]
    print(f"  info: {cancel_only} linha(s) so-cancelada(s) presentes no mart (Bug 8 preservado se > 0 quando a fonte tiver esses grupos)")

    cur.close()
    return problems


# ---------------------------------------------------------------------------
# Camada 2 — reconciliacao contra a fonte XLSX local (em memoria, sem escrita)
# ---------------------------------------------------------------------------
def _source_aggregates_by_brand_month() -> dict[tuple, dict]:
    """Reprocessa os XLSX locais em memoria com o _aggregate CORRIGIDO do ETL
    e devolve somatorios por (brand, ref_month iso). Nenhuma conexao de banco
    e' aberta aqui; nenhum arquivo e' escrito."""
    from etl.load_shopee_products import BRANDS, _aggregate, _load_brand

    agg_by_key: dict[tuple, dict] = {}
    for brand in BRANDS:
        df = _load_brand(brand)
        if df is None:
            continue
        agg = _aggregate(df)
        agg["ym"] = agg["ref_month"].map(lambda d: d.date().isoformat())
        grouped = agg.groupby(["brand", "ym"]).agg(
            gmv=("gmv", "sum"), units_sold=("units_sold", "sum"),
            completed_orders=("completed_orders", "sum"), canceled_orders=("canceled_orders", "sum"),
        )
        for (b, ym), row in grouped.iterrows():
            agg_by_key[(b, ym)] = {
                "gmv": round(float(row["gmv"]), 2),
                "units_sold": int(row["units_sold"]),
                "completed_orders": int(row["completed_orders"]),
                "canceled_orders": int(row["canceled_orders"]),
            }
    return agg_by_key


def _neon_aggregates_by_brand_month(conn) -> dict[tuple, dict]:
    cur = conn.cursor()
    cur.execute(f"""
        SELECT brand, ref_month, COALESCE(SUM(gmv), 0) AS gmv,
               COALESCE(SUM(units_sold), 0) AS units_sold,
               COALESCE(SUM(completed_orders), 0) AS completed_orders,
               COALESCE(SUM(canceled_orders), 0) AS canceled_orders
        FROM marts.{REAL_TABLE}
        GROUP BY brand, ref_month
    """)
    rows = cur.fetchall()
    cur.close()
    out: dict[tuple, dict] = {}
    for r in rows:
        ref = r["ref_month"]
        ym = ref.isoformat() if hasattr(ref, "isoformat") else ref
        out[(r["brand"], ym)] = {
            "gmv": round(float(r["gmv"]), 2),
            "units_sold": int(r["units_sold"]),
            "completed_orders": int(r["completed_orders"]),
            "canceled_orders": int(r["canceled_orders"]),
        }
    return out


def check_source_reconciliation(neon_aggs: dict[tuple, dict], source_aggs: dict[tuple, dict]) -> list[str]:
    """Compara agregados por marca x mes. Falha se qualquer metrica divergir —
    em particular, canceled_orders do Neon MENOR que o da fonte e' a
    assinatura exata de uma regressao ao left merge (Bug 8)."""
    problems: list[str] = []

    for key in sorted(source_aggs):
        src = source_aggs[key]
        neon = neon_aggs.get(key)
        if neon is None:
            problems.append(f"{key[0]} {key[1]}: existe na fonte XLSX mas nao no Neon")
            continue
        if abs(neon["gmv"] - src["gmv"]) > MONEY_TOLERANCE:
            problems.append(f"{key[0]} {key[1]}: GMV diverge (neon={neon['gmv']} fonte={src['gmv']})")
        if neon["units_sold"] != src["units_sold"]:
            problems.append(f"{key[0]} {key[1]}: units_sold diverge (neon={neon['units_sold']} fonte={src['units_sold']})")
        if neon["completed_orders"] != src["completed_orders"]:
            problems.append(f"{key[0]} {key[1]}: completed_orders diverge (neon={neon['completed_orders']} fonte={src['completed_orders']})")
        if neon["canceled_orders"] != src["canceled_orders"]:
            suffix = " — assinatura de regressao ao left merge (Bug 8)" if neon["canceled_orders"] < src["canceled_orders"] else ""
            problems.append(
                f"{key[0]} {key[1]}: canceled_orders diverge (neon={neon['canceled_orders']} "
                f"fonte={src['canceled_orders']}){suffix}"
            )

    for key in sorted(set(neon_aggs) - set(source_aggs)):
        problems.append(f"{key[0]} {key[1]}: existe no Neon mas nao na fonte XLSX")

    return problems


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    parser = argparse.ArgumentParser(description="Monitor read-only pos-carga do Bug 8 (invariantes, nao snapshots)")
    parser.add_argument("--skip-source", action="store_true",
                        help="Pula a reconciliacao contra os XLSX locais (roda so' as invariantes do mart)")
    args = parser.parse_args()

    neon_url = _get_neon_url()
    print(f"Neon (somente leitura): {_sanitize_url(neon_url)}")

    conn = _neon_readonly(neon_url)
    try:
        print("\n[camada 1] invariantes internas do mart")
        problems = check_db_invariants(conn)

        if args.skip_source:
            print("\n[camada 2] reconciliacao contra fonte XLSX: PULADA (--skip-source)")
        elif not SHOPEE_SOURCE_DIR.is_dir():
            print(f"\n[camada 2] reconciliacao contra fonte XLSX: PULADA (diretorio {SHOPEE_SOURCE_DIR} ausente nesta maquina)")
        else:
            print("\n[camada 2] reconciliacao contra fonte XLSX local (em memoria, sem escrita)")
            source_aggs = _source_aggregates_by_brand_month()
            neon_aggs = _neon_aggregates_by_brand_month(conn)
            print(f"  fonte: {len(source_aggs)} combinacoes marca x mes | neon: {len(neon_aggs)}")
            problems += check_source_reconciliation(neon_aggs, source_aggs)
    finally:
        conn.close()

    if problems:
        print(f"\n!!! {len(problems)} DIVERGENCIA(S):")
        for p in problems:
            print(f"    - {p}")
        return 1

    print("\nTodas as invariantes passaram — Bug 8 continua corrigido nesta carga.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
