"""CLI do refresh de expedicao.

    --diagnose   le a fonte e imprime agregados. Read-only, sem tocar no Neon.
    --apply      publica. NAO executado nos Gates EXP-1A / EXP-1A-R.

`--diagnose` NAO abre conexao gravavel e NAO registra em `audit.source_sync_run`:
diagnostico que aparece no audit log seria lido pelo health check como fonte
publicada com sucesso. Mesma regra de `sync_serving_snapshots`.

`--apply` esta implementado mas e estruturalmente inerte enquanto a migration do
contrato nao existir: ele para com mensagem explicita porque as tabelas de
destino nao existem.

SEM NO_OP POR WATERMARK (EXP-1A-R)
-----------------------------------
A versao anterior pulava a publicacao quando nenhuma conta avancava. Isso estava
errado: `deadline_status`, `operational_age_status`, `is_slow_vs_baseline`,
`is_source_zombie` e `is_stalled` dependem do RELOGIO, nao so da fonte. Com a
fonte parada, um pedido ainda atravessa a faixa de 24h, o prazo nativo, as 48h,
os 30 dias e o 2 x p50 — e a fila publicada ficaria descrevendo um estado que
deixou de existir.

Comportamento atual: **toda execucao agendada recomputa e publica**. Sao cerca
de mil linhas; nao ha otimizacao a fazer que valha a chance de exibir estado
velho como se fosse atual. O watermark continua medido e auditado, mas como
METADADO DE FRESCOR (`source_advanced`), nunca como condicao para pular.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipelines.expedicao import audit as audit_mod
from pipelines.expedicao import shopee_extract, transform
from pipelines.expedicao.contract import (
    FILA_TABLE,
    MARKETPLACE_ID,
    RUN_TABLE,
    Channel,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

EXIT_OK = 0
EXIT_FALHA = 1


def source_advanced(
    atual: dict[str, datetime | None], anterior: dict[str, datetime | None]
) -> bool:
    """Alguma conta avancou desde a ultima observacao?

    METADADO DE FRESCOR. Nao decide publicacao — decidia, e era o defeito 1.
    Serve para a auditoria responder "a fonte se mexeu?" sem que a resposta
    interfira em "o estado foi recomputado?" (que agora e sempre sim).

    Conta nova com carimbo conta como avanco. Carimbo nulo nao conta.
    """
    for conta, carimbo in atual.items():
        if carimbo is None:
            continue
        previo = anterior.get(conta)
        if previo is None or carimbo > previo:
            return True
    return False


def _agora_utc() -> datetime:
    """Unico ponto do pacote que le relogio. Sempre com fuso explicito.

    O instante e capturado UMA vez por execucao e injetado como `effective_at`
    em todas as funcoes de classificacao; nenhuma delas chama relogio.
    """
    return datetime.now(timezone.utc)


def diagnose(conn, effective_at: datetime) -> dict:
    """Agregados da fonte. Sem ID de pedido, sem PII, sem escrita.

    Nao resolve registry: `marts.dim_seller_account` vive no Neon, e o
    diagnostico nao abre conexao la. Por isso as contas aparecem pelo
    `external_seller_id`, nunca pelo texto `brand` da fonte.
    """
    backlog = shopee_extract.fetch_backlog(conn)
    watermarks = shopee_extract.fetch_watermarks(conn)
    baselines = shopee_extract.fetch_baselines(conn, effective_at)

    por_conta: dict[str, dict] = {}
    for linha in backlog:
        conta = str(linha["shop_id"])
        bucket = por_conta.setdefault(
            conta,
            {
                "aguardando": 0,
                "overdue": 0,
                "due_within_24h": 0,
                "on_time": 0,
                "unavailable": 0,
                "over_48h": 0,
                "source_zombie": 0,
            },
        )
        bucket["aguardando"] += 1
        bucket[
            transform.classify_deadline(linha.get("ship_by_date"), effective_at).value
        ] += 1
        marco = transform.marco_inicial(linha.get("pay_time"), linha.get("create_time"))
        horas = transform.hours_between(marco, effective_at)
        if transform.classify_age(marco, effective_at).value == "over_48h":
            bucket["over_48h"] += 1
        if transform.is_source_zombie(horas):
            bucket["source_zombie"] += 1

    return {
        "effective_at": effective_at,
        "por_conta": por_conta,
        "watermarks": {w.external_seller_id: w.max_ingested_at for w in watermarks},
        "baselines": baselines,
    }


def format_diagnose(resultado: dict) -> str:
    """Saida legivel. Agregados por CONTA — nenhum `order_sn` impresso."""
    linhas = [
        "DIAGNOSTICO expedicao/shopee (read-only, nada publicado)",
        f"  effective_at: {resultado['effective_at'].isoformat()}",
        "",
        "  conta            aguard  overdue  due24h  on_time  s/prazo  >48h  zumbi",
    ]
    total = {
        "aguardando": 0, "overdue": 0, "due_within_24h": 0,
        "on_time": 0, "unavailable": 0, "over_48h": 0, "source_zombie": 0,
    }
    for conta, b in sorted(resultado["por_conta"].items()):
        for k in total:
            total[k] += b[k]
        linhas.append(
            f"  {conta:<15} {b['aguardando']:>6} {b['overdue']:>8} "
            f"{b['due_within_24h']:>7} {b['on_time']:>8} {b['unavailable']:>8} "
            f"{b['over_48h']:>5} {b['source_zombie']:>6}"
        )
    linhas.append(
        f"  {'TOTAL':<15} {total['aguardando']:>6} {total['overdue']:>8} "
        f"{total['due_within_24h']:>7} {total['on_time']:>8} "
        f"{total['unavailable']:>8} {total['over_48h']:>5} "
        f"{total['source_zombie']:>6}"
    )
    soma_prazo = (
        total["overdue"] + total["due_within_24h"]
        + total["on_time"] + total["unavailable"]
    )
    linhas.append("")
    linhas.append(
        f"  conferencia: as 4 categorias de prazo somam {soma_prazo} "
        f"= aguardando ({total['aguardando']}) "
        f"{'OK' if soma_prazo == total['aguardando'] else 'DIVERGENTE'}"
    )
    linhas.append(
        f"  >48h ({total['over_48h']}) e transversal: nao entra nessa soma."
    )
    linhas.append("")
    linhas.append("  watermark por conta (metadado de frescor, nao decide publicacao):")
    for conta, carimbo in sorted(resultado["watermarks"].items()):
        idade = (
            f"{(resultado['effective_at'] - carimbo).total_seconds() / 3600:.2f}h"
            if carimbo is not None else "sem carimbo"
        )
        linhas.append(f"    {conta:<15} {carimbo} (idade {idade})")
    linhas.append("")
    linhas.append("  baseline p50 (horas ate pickup, 30d, CANCELLED excluido):")
    for conta, (n, p50) in sorted(resultado["baselines"].items()):
        p50_txt = f"{p50:.2f}h" if p50 is not None else "sem p50"
        suficiente = "usavel" if n >= 100 else "amostra insuficiente"
        linhas.append(f"    {conta:<15} n={n:<6} p50={p50_txt:<10} ({suficiente})")
    return "\n".join(linhas)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="expedicao",
        description=(
            "Refresh de expedicao por canal. Sem --apply, apenas diagnostico "
            "read-only."
        ),
    )
    p.add_argument(
        "--channel", default=Channel.SHOPEE.value,
        choices=[Channel.SHOPEE.value],
        help="canal a processar; hoje somente shopee",
    )
    p.add_argument(
        "--diagnose", action="store_true",
        help="le a fonte e imprime agregados; nao abre conexao gravavel",
    )
    p.add_argument(
        "--apply", action="store_true",
        help="publica no Neon. Requer as tabelas do contrato (migration pendente).",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    # `parse_args` ANTES de carregar `.env`: `--help` e argumento invalido saem
    # por SystemExit aqui, sem ler segredo, abrir conexao ou tocar auditoria.
    args = build_parser().parse_args(argv)

    if args.apply and args.diagnose:
        print("--apply e --diagnose sao mutuamente exclusivos.", file=sys.stderr)
        return EXIT_FALHA
    if not args.apply and not args.diagnose:
        print("informe --diagnose ou --apply.", file=sys.stderr)
        return EXIT_FALHA

    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    canal = Channel(args.channel)
    effective_at = _agora_utc()

    if args.diagnose:
        print("MODO DIAGNOSTICO: nenhuma escrita, nenhuma conexao gravavel.")
        try:
            return _run_diagnose(canal, effective_at)
        except Exception as exc:  # noqa: BLE001 — fronteira do CLI
            print(
                f"FALHA (diagnose/{canal.value}): "
                f"{audit_mod.sanitize_error_message(exc)}",
                file=sys.stderr,
            )
            return EXIT_FALHA

    print(
        "MODO APPLY nao habilitado: as tabelas "
        f"{FILA_TABLE} e {RUN_TABLE} ainda nao existem "
        "(migration pendente, deliberadamente fora deste gate).",
        file=sys.stderr,
    )
    return EXIT_FALHA


def _run_diagnose(canal: Channel, effective_at: datetime) -> int:
    import os  # noqa: PLC0415

    import psycopg2  # noqa: PLC0415
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    url = os.environ.get("DATAMART_DATABASE_URL", "")
    if not url:
        print("DATAMART_DATABASE_URL nao configurada.", file=sys.stderr)
        return EXIT_FALHA

    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=10)
    # READ-ONLY na propria sessao: mesmo que algum SQL futuro tente escrever, o
    # servidor recusa. Nao depende de disciplina de quem edita a query.
    conn.set_session(readonly=True, autocommit=True)
    try:
        resultado = diagnose(conn, effective_at)
    finally:
        conn.close()

    print(format_diagnose(resultado))
    print()
    print(f"  marketplace_id (audit): {MARKETPLACE_ID[canal]}")
    print("  nenhuma linha publicada; nenhum registro em audit.source_sync_run.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
