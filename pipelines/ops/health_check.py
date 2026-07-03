"""
Health check READ-ONLY de frescor: consulta `audit.source_sync_run` e o
`MAX(data/refreshed_at/ref_month)` direto nas tabelas do Neon — nunca
depende do Data Mart/RDS para avaliar o estado final. Roda tambem as
invariantes do Bug 8 (reaproveitando
`monitor_bug8_invariants.check_db_invariants`, sem duplicar).

Duas dimensoes de frescor, deliberadamente SEPARADAS (podem divergir: um
job pode "ter sucesso" processando zero linhas novas enquanto a fonte
upstream parou de produzir dado):

  1. Frescor de EXECUCAO (`fetch_source_statuses`) — baseado em
     `audit.source_sync_run`: quando cada fonte ESPERADA rodou pela ultima
     vez com sucesso. Usa uma lista EXPLICITA de fontes esperadas
     (`EXPECTED_SOURCES`) — uma fonte que nunca apareceu no audit log e'
     BLOCKED/ATENCAO, nunca "ausente do relatorio e portanto OK" (bug do
     desenho anterior: so' iterava `DISTINCT source_name`, entao uma fonte
     sem historico nenhum simplesmente nao aparecia e nao contava contra o
     status geral).

  2. Frescor de DADO (`fetch_data_freshness`) — baseado no
     MAX(date/refreshed_at/ref_month) real das tabelas do Neon,
     efetivamente comparado contra um threshold em dias (nao so'
     exibido). Fontes de cadencia `manual_monthly` (Shopee Produtos: o
     dado so' avanca quando um humano roda o loader manual com novos
     exports XLSX) sao reportadas mas NUNCA fazem o status geral falhar
     so' por isso — evita falso positivo de "MAX(ref_month) esta a 2
     meses" quando isso e' normal para essa fonte. A EXECUCAO do sync
     Shopee Produtos (que roda todo dia, com ou sem dado novo) continua
     cobertaa pela dimensao 1 e pega uma quebra real do pipeline.

Thresholds centralizados em EXPECTED_SOURCES e DAILY_DATA_FRESHNESS_THRESHOLD_DAYS
— nunca espalhados pelo corpo das funcoes.

Nenhuma escrita em nenhum banco. Nenhum alerta externo (e-mail/WhatsApp/
webhook) — so' saida para o operador e exit code para automacao externa.
O JSON traz um campo `reason` por fonte/tabela explicando a causa do
status, nao so' os numeros.

Uso:
    python -m pipelines.ops.health_check
    python -m pipelines.ops.health_check --json
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from pipelines.reconciliation.diagnose_bug8_neon import (  # noqa: E402
    REAL_TABLE,
    _get_neon_url,
    _neon_readonly,
    _sanitize_url,
)
from pipelines.reconciliation.monitor_bug8_invariants import check_db_invariants  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

MARKETPLACE_LABELS = {1: "tiktok", 2: "ml", 3: "shopee"}
DAILY_DATA_FRESHNESS_THRESHOLD_DAYS = 3


@dataclass(frozen=True)
class ExpectedSource:
    source_name: str
    cadence: str  # "daily" | "manual_monthly"
    exec_threshold_hours: float


# Lista EXPLICITA e completa das fontes que esperamos ver em
# audit.source_sync_run. Uma fonte fora desta lista nao e' avaliada; uma
# fonte NESTA lista sem nenhuma linha no audit log e' sempre stale=True
# (nunca "ausente e' OK").
EXPECTED_SOURCES: tuple[ExpectedSource, ...] = (
    ExpectedSource("ml_daily", "daily", 30),
    ExpectedSource("tiktok_daily", "daily", 30),
    ExpectedSource("shopee_daily", "daily", 48),
    ExpectedSource("shopee-stats_daily", "daily", 48),
    ExpectedSource("shopee-ads_daily", "daily", 48),
    ExpectedSource("tiktok_product_daily", "daily", 30),
    ExpectedSource("ml_produto_ranking", "daily", 30),
    # A EXECUCAO deste sync roda todo dia (mesmo sem dado novo) — so' o
    # DADO upstream (XLSX + loader manual) tem cadencia mensal/manual, ver
    # fetch_data_freshness.
    ExpectedSource("shopee_product_monthly", "daily", 48),
)


@dataclass
class SourceStatus:
    source_name: str
    cadence: str
    last_status: str | None
    last_started_at: str | None
    last_finished_at: str | None
    last_success_at: str | None
    hours_since_success: float | None
    threshold_hours: float
    execution_stale: bool
    last_run_failed: bool
    stale: bool
    last_error: str | None
    reason: str


def fetch_source_statuses(conn, now: datetime | None = None) -> list[SourceStatus]:
    now = now or datetime.now(timezone.utc)
    cur = conn.cursor()
    out: list[SourceStatus] = []

    for expected in EXPECTED_SOURCES:
        cur.execute(
            """
            SELECT started_at, finished_at, status, error_message
            FROM audit.source_sync_run
            WHERE source_name = %s
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (expected.source_name,),
        )
        last = cur.fetchone()

        if last is None:
            out.append(
                SourceStatus(
                    source_name=expected.source_name,
                    cadence=expected.cadence,
                    last_status=None,
                    last_started_at=None,
                    last_finished_at=None,
                    last_success_at=None,
                    hours_since_success=None,
                    threshold_hours=expected.exec_threshold_hours,
                    execution_stale=True,
                    last_run_failed=False,
                    stale=True,
                    last_error=None,
                    reason="nenhuma execucao registrada para esta fonte esperada",
                )
            )
            continue

        cur.execute(
            """
            SELECT MAX(finished_at) AS t FROM audit.source_sync_run
            WHERE source_name = %s AND status = 'success'
            """,
            (expected.source_name,),
        )
        last_success = cur.fetchone()["t"]

        hours_since = None
        if last_success is not None:
            hours_since = round((now - last_success).total_seconds() / 3600, 1)
            execution_stale = hours_since > expected.exec_threshold_hours
        else:
            execution_stale = True

        # last_run_failed e' avaliado SEPARADO de execution_stale: uma
        # falha na ultima execucao tem que virar atencao SEMPRE, mesmo que
        # exista um sucesso anterior ainda dentro do threshold — senao um
        # job quebrado (mas com um sucesso "velho" recente o bastante) fica
        # mascarado de OK ate o threshold de frescor de execucao estourar
        # por conta propria, o que pode levar dias.
        last_run_failed = last["status"] == "failed"

        if last_success is None:
            reason = "nenhuma execucao com sucesso registrada"
        elif execution_stale:
            reason = f"ultimo sucesso ha {hours_since}h, acima do limite de {expected.exec_threshold_hours}h"
        else:
            reason = f"ultimo sucesso ha {hours_since}h, dentro do limite de {expected.exec_threshold_hours}h"
        if last_run_failed:
            reason = f"ultima execucao FALHOU ({(last['error_message'] or '')[:100]}); {reason}"

        out.append(
            SourceStatus(
                source_name=expected.source_name,
                cadence=expected.cadence,
                last_status=last["status"],
                last_started_at=last["started_at"].isoformat() if last["started_at"] else None,
                last_finished_at=last["finished_at"].isoformat() if last["finished_at"] else None,
                last_success_at=last_success.isoformat() if last_success else None,
                hours_since_success=hours_since,
                threshold_hours=expected.exec_threshold_hours,
                execution_stale=execution_stale,
                last_run_failed=last_run_failed,
                stale=execution_stale or last_run_failed,
                last_error=(last["error_message"][:200] if last.get("error_message") else None),
                reason=reason,
            )
        )
    cur.close()
    return out


@dataclass
class DataFreshnessResult:
    label: str
    cadence: str
    max_value: str | None
    days_since: float | None
    threshold_days: float | None
    stale: bool
    reason: str


def _evaluate_date_freshness(label: str, cadence: str, max_value, today: date, threshold_days: float | None) -> DataFreshnessResult:
    if max_value is None:
        return DataFreshnessResult(label, cadence, None, None, threshold_days, True, f"{label}: tabela sem nenhuma linha")

    value_date = max_value.date() if hasattr(max_value, "date") else max_value
    days_since = (today - value_date).days

    if days_since < 0:
        # Data no futuro NUNCA e' "fresca" — e' um erro de qualidade
        # (parsing de data errado, fuso horario, relogio da fonte), nao um
        # sinal positivo. Vale para QUALQUER cadencia, inclusive
        # manual/mensal — ver Bug 3 (ref_month projetado para meses futuros
        # inexistentes por causa de um bug de parsing, docs/sections/produtos_audit.md).
        return DataFreshnessResult(
            label, cadence, value_date.isoformat(), days_since, threshold_days, True,
            f"{label}: data no FUTURO ({value_date.isoformat()}, {-days_since}d a frente de hoje) — "
            f"erro de qualidade (parsing/fuso), nunca tratado como fresco",
        )

    if threshold_days is None:
        # cadencia manual/mensal: reporta, nunca marca como stale por si
        # so' (evita falso positivo — ver docstring do modulo).
        return DataFreshnessResult(
            label, cadence, value_date.isoformat(), days_since, None, False,
            f"{label}: cadencia {cadence}, ultimo periodo ha {days_since}d — nao avaliado contra threshold "
            f"(a execucao do sync correspondente e' o que detecta uma quebra real)",
        )

    stale = days_since > threshold_days
    reason = (
        f"{label}: dado com {days_since}d, acima do limite de {threshold_days}d"
        if stale
        else f"{label}: dado fresco ({days_since}d, limite {threshold_days}d)"
    )
    return DataFreshnessResult(label, cadence, value_date.isoformat(), days_since, threshold_days, stale, reason)


def fetch_data_freshness(conn, today: date | None = None) -> list[DataFreshnessResult]:
    today = today or datetime.now(timezone.utc).date()
    cur = conn.cursor()
    results: list[DataFreshnessResult] = []

    cur.execute(
        "SELECT marketplace_id, MAX(date) AS max_date FROM marts.fact_marketplace_daily_performance GROUP BY marketplace_id"
    )
    daily_rows = {int(r["marketplace_id"]): r["max_date"] for r in cur.fetchall()}
    for mkt_id, label in MARKETPLACE_LABELS.items():
        results.append(
            _evaluate_date_freshness(
                f"fact_marketplace_daily_performance[{label}]", "daily",
                daily_rows.get(mkt_id), today, DAILY_DATA_FRESHNESS_THRESHOLD_DAYS,
            )
        )

    cur.execute("SELECT MAX(date) AS m FROM marts.fact_tiktok_product_daily")
    results.append(_evaluate_date_freshness("fact_tiktok_product_daily", "daily", cur.fetchone()["m"], today, DAILY_DATA_FRESHNESS_THRESHOLD_DAYS))

    cur.execute("SELECT MAX(refreshed_at) AS m FROM marts.fact_ml_produto_ranking")
    results.append(_evaluate_date_freshness("fact_ml_produto_ranking", "daily", cur.fetchone()["m"], today, DAILY_DATA_FRESHNESS_THRESHOLD_DAYS))

    cur.execute(f"SELECT MAX(ref_month) AS m FROM marts.{REAL_TABLE}")
    results.append(_evaluate_date_freshness(f"marts.{REAL_TABLE}[ref_month]", "manual_monthly", cur.fetchone()["m"], today, None))

    cur.close()
    return results


def run_bug8_check(conn) -> dict:
    """check_db_invariants imprime uma linha informativa (pensada para o
    CLI standalone de monitor_bug8_invariants) — suprimida aqui para que a
    saida deste modulo (inclusive --json) fique limpa e previsivel."""
    with contextlib.redirect_stdout(io.StringIO()):
        problems = check_db_invariants(conn)
    return {"ok": not problems, "problems": problems}


def build_report(conn) -> dict:
    sources = fetch_source_statuses(conn)
    data_freshness = fetch_data_freshness(conn)
    bug8 = run_bug8_check(conn)

    exec_stale = [s for s in sources if s.stale]
    data_stale = [d for d in data_freshness if d.stale]
    ok = not exec_stale and not data_stale and bug8["ok"]

    return {
        "ok": ok,
        "sources": [asdict(s) for s in sources],
        "data_freshness": [asdict(d) for d in data_freshness],
        "bug8_invariants": bug8,
    }


def _print_human(report: dict) -> None:
    print("=== Frescor de EXECUCAO por fonte (audit.source_sync_run) ===")
    for s in report["sources"]:
        flag = "ATRASADA" if s["stale"] else "OK"
        print(f"[{flag}] {s['source_name']} (cadencia={s['cadence']}): {s['reason']}")

    print("\n=== Frescor de DADO (MAX direto nas tabelas do Neon) ===")
    for d in report["data_freshness"]:
        flag = "ATRASADO" if d["stale"] else "OK"
        print(f"[{flag}] {d['reason']}")

    print("\n=== Invariantes do Bug 8 (Shopee) ===")
    if report["bug8_invariants"]["ok"]:
        print("  OK — nenhuma divergencia")
    else:
        for p in report["bug8_invariants"]["problems"]:
            print(f"  DIVERGENCIA: {p}")

    print(f"\nSTATUS GERAL: {'OK' if report['ok'] else 'ATENCAO'}")


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    parser = argparse.ArgumentParser(description="Health check read-only de frescor (Neon + Bug 8)")
    parser.add_argument("--json", action="store_true", help="Saida estruturada em JSON para automacao")
    args = parser.parse_args()

    neon_url = _get_neon_url()
    if not args.json:
        print(f"Neon (somente leitura): {_sanitize_url(neon_url)}\n")

    conn = _neon_readonly(neon_url)
    try:
        report = build_report(conn)
    finally:
        conn.close()

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        _print_human(report)

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
