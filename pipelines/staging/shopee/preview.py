"""
Reconciliação PREVIEW (somente leitura) da staging Shopee contra 100% da Raw.

Usa a MESMA fonte de checagens que a transformação transacional
(`pipelines/staging/shopee/validations.py`) — nunca duas listas divergentes
de validação. Revisão de performance: por fonte, UMA query agregada
(`validations.build_merged_row_query`, `count(*) FILTER` por condição)
cobre todas as condições de linha num único scan (antes: um scan por
condição, ~55-60 por fonte), mais só as poucas checagens estruturais que
precisam de scan próprio (`validations.build_scan_checks`). Preview sempre
chama `build_merged_row_query(spec, incremental=False)` — quer reconciliar
100% da Raw a cada execução, não só o delta ainda não staged (isso é
exclusivo da transformação real, que já tem uma tabela staging para
comparar). Esses "1 agregado + N estruturais" scans são só a PRÉ-VALIDAÇÃO
desta fonte — não confundir com o total de scans da execução completa da
transformação real, que ainda soma a leitura+INSERT (passo 5) e o
pós-insert (passo 6); ver docs/staging_shopee_contract.md §13.2.

Contagem exata de linhas rejeitadas: a MESMA query agregada inclui
`rejected_any_expr` (`count(*) FILTER (WHERE (c0) OR (c1) OR ...)`) —
conta LINHAS DISTINTAS que violam qualquer condição, evitando a
supercontagem de uma linha que viola mais de um motivo ao mesmo tempo. As
contagens por motivo continuam existindo só para diagnóstico (qual regra
específica falhou), nunca somadas para virar "total de rejeitadas".

Além das checagens compartilhadas, este script roda o SELECT tipado
completo (todos os casts/regras de `mapping.py`, via `build_sql.build_select`)
sobre 100% da Raw — uma segunda confirmação, independente das contagens de
`validations.py`, de que nenhum valor real quebra a expressão de valor.

Nunca imprime PII nem valores de célula — só contagens, somas agregadas e
datas min/max. A sessão é aberta com execution_options(postgresql_readonly=
True) ANTES da primeira query (mesmo padrão de shopee_raw/reconcile.py).

Este preview NÃO substitui a validação transacional (ver build_sql.py): a
Raw pode mudar entre a execução deste script e uma futura carga real — por
isso a transformação re-executa as mesmas checagens dentro da própria
transação, sob `LOCK TABLE`, antes de qualquer INSERT.

Uso:
    uv run --no-project --with sqlalchemy --with psycopg2-binary \
        python -m pipelines.staging.shopee.preview
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from pipelines.staging.shopee import mapping, semantics, validations
from pipelines.staging.shopee.build_sql import build_select

REPO_ROOT = Path(__file__).resolve().parents[3]

_SUM_COLUMNS = {
    "orders": ["quantity", "product_subtotal", "order_grand_total", "order_amount",
               "commission_fee_net", "service_fee_net", "estimated_shipping_fee"],
    "shop_stats": ["orders_count", "visitors", "sales_brl", "cancelled_sales"],
    "ads": ["impressions", "clicks", "expense", "gmv", "direct_revenue"],
}

_DATE_COLUMNS = {
    "orders": "order_created_at",
    "shop_stats": "stat_date",
    "ads": "started_at",
}

_MONTH_BUCKET_SQL = {
    "orders": (
        "to_char(make_timestamp("
        "(regexp_match(btrim(r.raw_payload ->> 'Data de criação do pedido'), "
        "'^([0-9]{4})-([0-9]{2})-([0-9]{2})'))[1]::integer, "
        "(regexp_match(btrim(r.raw_payload ->> 'Data de criação do pedido'), "
        "'^([0-9]{4})-([0-9]{2})-([0-9]{2})'))[2]::integer, "
        "(regexp_match(btrim(r.raw_payload ->> 'Data de criação do pedido'), "
        "'^([0-9]{4})-([0-9]{2})-([0-9]{2})'))[3]::integer, 0, 0, 0), 'YYYY-MM')"
    ),
    "shop_stats": (
        "CASE WHEN btrim(r.raw_payload ->> 'Data') ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$' "
        "THEN to_char(make_date("
        "(regexp_match(btrim(r.raw_payload ->> 'Data'), "
        "'^[0-9]{2}/[0-9]{2}/([0-9]{4})$'))[1]::integer, "
        "split_part(btrim(r.raw_payload ->> 'Data'), '/', 2)::integer, "
        "split_part(btrim(r.raw_payload ->> 'Data'), '/', 1)::integer), 'YYYY-MM') "
        "ELSE 'period_total' END"
    ),
    "ads": "COALESCE(f.source_metadata ->> 'period_start', '(sem source_metadata)')",
}


def load_datamart_url() -> str:
    env = REPO_ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATAMART_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DATAMART_DATABASE_URL não encontrada no .env")


def run_preview(engine) -> dict:
    """Roda o preview completo. Retorna dict com o relatório (sem PII)."""
    report: dict = {"problems": []}
    with engine.connect().execution_options(postgresql_readonly=True) as conn:
        ro = conn.execute(text("SHOW transaction_read_only")).scalar()
        if ro != "on":
            raise RuntimeError("sessão não está read-only — abortando preview")
        conn.execute(text("SET statement_timeout = '900s'"))

        for spec in mapping.ALL_SPECS:
            src: dict = {}
            st = spec.source_type

            total = conn.execute(text(f"SELECT count(*) FROM {spec.raw_table}")).scalar()
            manifest = conn.execute(text(
                "SELECT count(*) AS files, COALESCE(sum(source_row_count), 0) AS rows "
                "FROM raw.shopee_ingestion_file WHERE source_type = :st"
            ), {"st": st}).one()
            src["raw_rows"] = total
            src["manifest_files"] = manifest.files
            src["manifest_rows"] = int(manifest.rows)
            if total != manifest.rows:
                report["problems"].append(
                    f"{st}: linhas raw ({total}) != soma do manifesto ({manifest.rows})")

            # Checagens COMPARTILHADAS com a transformação (validations.py) —
            # mesma fonte, nunca duas listas divergentes. Revisão de
            # performance: 1 scan agregado (condições de linha) + poucos
            # scans estruturais, não mais um scan por condição.
            #
            # Contagem EXATA de linhas rejeitadas: uma mesma linha pode
            # violar mais de uma condição (ex.: order_id vazio E quantity
            # negativo) — somar as contagens por motivo SUPERCONTA essa
            # linha. `rejected_any_expr` conta, na MESMA query, quantas
            # linhas DISTINTAS violam QUALQUER condição — essa é a fonte de
            # `linhas_rejeitaveis`/`linhas_aceitas`. As contagens por motivo
            # continuam existindo só para DIAGNÓSTICO (qual regra falhou).
            results = {}

            q = validations.build_merged_row_query(spec, incremental=False)
            merged_sql = (
                "SELECT\n  " + ",\n  ".join(q.select_exprs + [q.rejected_any_expr])
                + "\n" + q.from_clause
            )
            row = conn.execute(text(merged_sql)).mappings().one()
            for i, reason in enumerate(q.reasons):
                n = row[f"c{i}"]
                results[reason] = n
                if n:
                    report["problems"].append(f"{reason}: {n}")
            rejected_any = row["rejected_any"]
            if rejected_any:
                report["problems"].append(
                    f"{st}: linhas rejeitadas (distintas, qualquer motivo): {rejected_any}")

            # Checagens estruturais (duplicidade, schema drift) — unidades
            # DIFERENTES de "linha rejeitada" (duplicidade conta linhas
            # participantes de um par duplicado; schema drift conta CHAVES
            # de JSONB, não linhas) — reportadas à parte, nunca somadas a
            # linhas_rejeitaveis/linhas_aceitas.
            structural_problems = 0
            for chk in validations.build_scan_checks(spec):
                n = conn.execute(text(f"SELECT count(*) {chk.body_sql}")).scalar()
                results[chk.reason] = n
                if n:
                    structural_problems += n
                    report["problems"].append(f"{chk.reason}: {n}")

            src["checagens"] = results
            # 1 query agregada (linha) + N checagens estruturais — só a
            # PRÉ-VALIDAÇÃO desta fonte, não a execução completa da futura
            # transformação (que ainda soma leitura+INSERT e pós-insert —
            # ver docs/staging_shopee_contract.md §13.2).
            src["scans_prevalidacao"] = 1 + len(validations.build_scan_checks(spec))
            src["linhas_rejeitaveis"] = rejected_any
            src["linhas_aceitas"] = total - rejected_any
            src["problemas_estruturais"] = structural_problems

            # SELECT tipado completo — segunda confirmação independente,
            # executa TODOS os casts/regras de valor sobre 100% da Raw.
            inner = build_select(spec, incremental=False)
            sums = ", ".join(f"sum({c}) AS sum_{c}" for c in _SUM_COLUMNS[st])
            dc = _DATE_COLUMNS[st]
            typed = conn.execute(text(
                f"SELECT count(*) AS n, {sums}, min({dc}) AS dt_min, max({dc}) AS dt_max "
                f"FROM (\n{inner}\n) t"
            )).mappings().one()
            src["preview_tipado"] = {k: (str(v) if v is not None else None)
                                     for k, v in dict(typed).items()}
            if typed["n"] != total:
                report["problems"].append(
                    f"{st}: SELECT tipado retornou {typed['n']} linhas != {total} da raw")

            # Contagem por brand e por mês/arquivo (grão de conferência de negócio)
            rows = conn.execute(text(
                f"SELECT r.brand, {_MONTH_BUCKET_SQL[st]} AS bucket, count(*) AS n "
                f"FROM {spec.raw_table} r JOIN raw.shopee_ingestion_file f "
                "ON f.file_id = r.file_id GROUP BY 1, 2 ORDER BY 1, 2"
            )).fetchall()
            src["contagem_brand_bucket"] = [
                {"brand": r.brand, "bucket": r.bucket, "n": r.n} for r in rows
            ]

            report[st] = src

        # ads: cobertura do período vindo de source_metadata (manifesto).
        # Diagnóstico via count(*) FILTER sobre a condição COMPARTILHADA de
        # invalidez (não sobre o SELECT tipado): uma linha cujo
        # source_metadata falte/seja inválido é rejeitada ANTES do INSERT
        # pela pré-validação (ver validations.py) — nunca chega a ser
        # persistida com report_period_start/end NULL.
        ads_metadata_invalid = semantics.ads_metadata_period_is_invalid("f.source_metadata")
        per = conn.execute(text(
            "SELECT count(*) AS n, "
            f"count(*) FILTER (WHERE NOT ({ads_metadata_invalid})) AS com_periodo "
            "FROM raw.shopee_ads_export r "
            "LEFT JOIN raw.shopee_ingestion_file f ON f.file_id = r.file_id"
        )).one()
        report["ads"]["linhas_com_periodo_do_manifesto"] = per.com_periodo
        report["ads"]["linhas_sem_periodo_do_manifesto"] = per.n - per.com_periodo

    return report


def print_report(report: dict) -> None:
    for spec in mapping.ALL_SPECS:
        st = spec.source_type
        src = report[st]
        print(f"\n=== {st} -> {spec.staging_table} ===")
        print(f"  raw={src['raw_rows']} manifesto={src['manifest_rows']} "
              f"(arquivos={src['manifest_files']})")
        print(f"  aceitas(exato)={src['linhas_aceitas']} "
              f"rejeitaveis(exato, linhas distintas)={src['linhas_rejeitaveis']} "
              f"| problemas estruturais (dup./schema drift, unidade própria): {src['problemas_estruturais']} "
              f"(scans de pré-validação: {src['scans_prevalidacao']})")
        for reason, n in src["checagens"].items():
            flag = " <-- ATENCAO" if n else ""
            print(f"    - {reason}: {n}{flag}")
        pt = src["preview_tipado"]
        print(f"  SELECT tipado: n={pt['n']} | datas: {pt['dt_min']} -> {pt['dt_max']}")
        for k, v in pt.items():
            if k.startswith("sum_"):
                print(f"    {k}: {v}")
        print("  contagem por brand/bucket:")
        for row in src["contagem_brand_bucket"]:
            print(f"    {row['brand']:<10} {row['bucket']}: {row['n']}")
    print(f"\nads com periodo valido no source_metadata do manifesto: "
          f"{report['ads']['linhas_com_periodo_do_manifesto']} | "
          f"sem (seriam rejeitadas na pre-validacao): {report['ads']['linhas_sem_periodo_do_manifesto']}")
    if report["problems"]:
        print("\nPROBLEMAS ENCONTRADOS:")
        for p in report["problems"]:
            print(f"  - {p}")
    else:
        print("\nReconciliacao preview LIMPA — nenhuma rejeicao, duplicidade ou chave fora do contrato.")


def main() -> int:
    engine = create_engine(load_datamart_url(), pool_pre_ping=True)
    report = run_preview(engine)
    print_report(report)
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
