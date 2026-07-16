"""
Gate B2 — wrapper condicional para o orquestrador: roda
pipelines.sync_region_daily.run_diagnose() (somente leitura nos dois lados)
e SO' chama run_sync() se needs_sync=True.

Motivo de existir: pipelines.sync_region_daily --sync sempre faz
TRUNCATE+INSERT e cria uma tabela de backup nova quando a tabela real ja'
tem linhas (marts.fact_marketplace_region_daily_backup_<tag>) — util para
um sync manual ad-hoc, mas indesejavel dentro de full_daily, que roda todo
dia: na maioria dos dias Data Mart e Neon ja' estao em paridade (nada novo
desde o ultimo gold_regional_incremental + sync), e disparar --sync mesmo
assim geraria uma tabela de backup nova por dia sem necessidade nenhuma.
Este wrapper evita isso fazendo o diagnose primeiro e so' escrevendo quando
de fato ha' divergencia.

Guardas preservadas (nao reimplementadas, so' herdadas de sync_region_daily):
  - run_sync() continua exigindo --sync E
    I_UNDERSTAND_THIS_WRITES_NEON_REGION_DAILY=1 — este wrapper nao contorna
    nem afrouxa nenhuma delas; so' decide SE vale a pena chamar run_sync().
  - Sem retry automatico em nenhum caminho (diagnose ou sync).
  - Erros (de diagnose ou de sync) nunca sao propagados com a mensagem nativa
    da excecao — passam por sync_region_daily._sanitize_error_message antes
    de aparecer em stdout/stderr (nunca host/URL/credencial).

Gate B6.1b: main() tambem tenta carregar o consentimento persistente de
pipelines.ops.region_sync_consent (arquivo gitignored
`.env.region-sync.local`) ANTES de chamar run() — necessario para quando
este modulo e' invocado standalone (sem o preflight do orquestrador ja ter
resolvido o consentimento antes). Isso NAO reimplementa nem afrouxa o gate
original de run_sync(): so' garante que, se um consentimento persistente e
valido existir, a variavel de ambiente que run_sync() exige ja esteja
presente em os.environ quando ele for chamado.

Uso:
    python -m pipelines.ops.sync_region_if_needed
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from pipelines import sync_region_daily as srd  # noqa: E402
from pipelines.ops import region_sync_consent  # noqa: E402


class SyncIfNeededError(RuntimeError):
    """Falha ao decidir ou executar o sync condicional — mensagem ja' sanitizada
    (nunca repassa a excecao original sem passar por srd._sanitize_error_message)."""


@dataclass(frozen=True)
class SyncIfNeededResult:
    no_op: bool
    needs_sync: bool
    synced: bool = False
    source_rows: int | None = None
    target_rows_before: int | None = None
    target_rows_after: int | None = None
    backup_table: str | None = None


class _SyncArgs:
    """Objeto minimo com o atributo que srd.run_sync espera — equivalente a
    `argparse.Namespace(sync=True)`, sem depender do parser de CLI real."""
    sync = True


def run(diagnose_fn=None, sync_fn=None) -> SyncIfNeededResult:
    """diagnose_fn/sync_fn sao injetaveis para teste (assinaturas de
    srd.run_diagnose/srd.run_sync); sem eles, chama as funcoes reais do
    modulo sync_region_daily. Nunca faz retry: cada chamada tenta, no
    maximo, um diagnose e um sync."""
    diagnose_fn = diagnose_fn or srd.run_diagnose
    sync_fn = sync_fn or srd.run_sync

    try:
        report = diagnose_fn()
    except Exception as exc:  # noqa: BLE001
        raise SyncIfNeededError(f"diagnose falhou, sync NAO tentado: {srd._sanitize_error_message(exc)}") from exc

    target_rows_before = report["target_agg"]["n"] if report["target_agg"] else None

    if not report["needs_sync"]:
        return SyncIfNeededResult(
            no_op=True,
            needs_sync=False,
            source_rows=report["source_agg"]["n"],
            target_rows_before=target_rows_before,
        )

    try:
        result = sync_fn(_SyncArgs())
    except Exception as exc:  # noqa: BLE001
        raise SyncIfNeededError(f"sync falhou: {srd._sanitize_error_message(exc)}") from exc

    return SyncIfNeededResult(
        no_op=False,
        needs_sync=True,
        synced=True,
        source_rows=report["source_agg"]["n"],
        target_rows_before=target_rows_before,
        target_rows_after=result["real_agg_after"]["n"],
        backup_table=result["backup_table"],
    )


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))
    region_sync_consent.ensure_region_sync_consent()

    try:
        result = run()
    except SyncIfNeededError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1

    if result.no_op:
        print(f"NO_OP: Data Mart e Neon ja' em paridade ({result.source_rows} linha(s)) — nenhuma escrita realizada.")
        return 0

    print(
        f"SYNC realizado: {result.target_rows_before} -> {result.target_rows_after} linha(s) em marts.fact_marketplace_region_daily."
    )
    print(f"Backup preservado: marts.{result.backup_table}" if result.backup_table else "Backup: nao aplicavel (tabela estava vazia antes do sync)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
