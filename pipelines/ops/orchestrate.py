"""
Orquestrador que amarra preflight.py a execucao real, e executa uma
sequencia de passos EM PROCESSO (uma etapa so' comeca depois que a
anterior de verdade terminou — nunca depende de intervalo de horario do
Task Scheduler ser maior que o timeout da etapa anterior).

Um UNICO pipeline nomeado, `full_daily`: todas as fontes diarias (ml,
tiktok, shopee, shopee-stats, shopee-ads), depois Produtos (ml, tiktok,
shopee), depois o monitor do Bug 8 (so' se o sync Produtos Shopee teve
SUCCESS nesta mesma execucao) e por ultimo o health check (sempre, mesmo
se algo antes falhou). Isso substitui o desenho anterior de 2 pipelines
agendados em horarios separados (`daily_ingestion` @ 06:00 e
`produtos_and_monitor` @ 06:35): com 2 tarefas independentes no Task
Scheduler, nao havia garantia de que a primeira tivesse terminado antes da
segunda comecar (a primeira podia durar ate seu timeout total e a segunda
comecava por horario, nao por dependencia real) — o health check da
segunda tarefa podia rodar ANTES do fim real da primeira. Com um unico
pipeline sob um unico lock, o health check so' roda depois que TODOS os
passos anteriores (inclusive os diarios) realmente terminaram.

Para cada step:
  1. Se houver `preflight_source`, roda preflight.run_preflight(...) —
     aprovado -> segue; bloqueado -> o step vira BLOCKED e o comando real
     NUNCA e' executado (nada de "so guardar o resultado sem usar").
  2. Se o step depende de outros (`depends_on`) e algum deles nao teve
     status SUCCESS, o step vira SKIPPED (nunca executa) — a menos que
     `always_run=True` (usado pelo health check, que deve rodar mesmo se
     algo antes falhou, para reportar o estado real).
  3. Executa via subprocess.run com cwd=REPO_ROOT explicito (nunca herda o
     cwd de quem chamou), com um TIMEOUT INDIVIDUAL por step
     (`Step.timeout_seconds`) — uma fonte travada (ex.: ML preso numa
     query longa no RDS) NUNCA consome o timeout global inteiro nem trava
     as fontes independentes seguintes (TikTok/Shopee continuam rodando
     normalmente). Se o timeout individual estourar, `subprocess.run`
     mata o processo e levanta `subprocess.TimeoutExpired`, capturado
     aqui — o step vira FAILED (tentamos e nao terminou a tempo,
     distinto de BLOCKED = preflight impediu de tentar) e a orquestracao
     segue para o proximo step.
  4. Propaga o exit code exato do comando real quando ele termina dentro
     do timeout.

Nunca escreve em nenhum banco (isso e' responsabilidade dos proprios
modulos executados, cada um com suas guardas). Nunca referencia
DATAMART_DATABASE_URL diretamente (so' os checks de preflight, que fazem
SELECT 1). Nunca ativa Task Scheduler.

Uso:
    python -m pipelines.ops.orchestrate --pipeline full_daily
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from pipelines.ops.preflight import run_preflight  # noqa: E402


@dataclass(frozen=True)
class Step:
    name: str
    module: str
    args: tuple[str, ...]
    timeout_seconds: int
    preflight_source: str | None = None
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    always_run: bool = False


# Pipeline unico: todas as fontes diarias, depois Produtos, depois
# monitor/health check — sequenciais SOB O MESMO lock externo (ver
# schedule_plan.py), o que garante que health_check so' roda depois que
# TUDO antes realmente terminou (nunca por horario).
PIPELINES: dict[str, tuple[Step, ...]] = {
    "full_daily": (
        Step("daily_ml", "pipelines.ingestion.daily_performance", ("--source", "ml", "--mode", "incremental"), timeout_seconds=900, preflight_source="ml_daily"),
        Step("daily_tiktok", "pipelines.ingestion.daily_performance", ("--source", "tiktok", "--mode", "incremental"), timeout_seconds=900, preflight_source="tiktok_daily"),
        Step("daily_shopee_orders", "pipelines.ingestion.daily_performance", ("--source", "shopee", "--mode", "incremental"), timeout_seconds=900, preflight_source="shopee_daily"),
        Step("daily_shopee_stats", "pipelines.ingestion.daily_performance", ("--source", "shopee-stats", "--mode", "incremental"), timeout_seconds=900, preflight_source="shopee-stats_daily"),
        Step("daily_shopee_ads", "pipelines.ingestion.daily_performance", ("--source", "shopee-ads", "--mode", "incremental"), timeout_seconds=900, preflight_source="shopee-ads_daily"),
        Step("sync_produtos_ml", "pipelines.sync_produtos", ("--source", "ml"), timeout_seconds=600, preflight_source="produtos_ml"),
        Step("sync_produtos_tiktok", "pipelines.sync_produtos", ("--source", "tiktok"), timeout_seconds=600, preflight_source="produtos_tiktok"),
        Step("sync_produtos_shopee", "pipelines.sync_produtos", ("--source", "shopee"), timeout_seconds=600, preflight_source="produtos_shopee"),
        # So' roda se sync_produtos_shopee tiver SUCCEEDED de verdade nesta
        # mesma execucao — nunca por o relogio ter passado tempo suficiente.
        Step("monitor_bug8", "pipelines.reconciliation.monitor_bug8_invariants", ("--skip-source",), timeout_seconds=300, depends_on=("sync_produtos_shopee",)),
        # Sempre roda por ultimo, mesmo se algo anterior falhou/bloqueou —
        # e' o resumo do estado real, precisa rodar para reportar a falha.
        # always_run + ser o ULTIMO item desta tupla e' o que garante
        # "health check e' comprovadamente o ultimo passo global": nao ha
        # segunda tarefa/segundo lock depois deste, entao nao ha como algo
        # rodar depois dele nesta mesma execucao agendada.
        Step("health_check", "pipelines.ops.health_check", ("--json",), timeout_seconds=180, always_run=True),
    ),
}

# Soma dos timeouts individuais de full_daily = 900*5 + 600*3 + 300 + 180
# = 6780s (~1h53). O timeout EXTERNO (run_with_lock.ps1 -TimeoutSeconds,
# ver scripts/run_task.ps1) tem que ser MAIOR que essa soma, com margem —
# senao o lock externo mata o processo pai antes que os timeouts internos
# por step tenham chance de proteger as fontes independentes seguintes.
# Ver RECOMMENDED_EXTERNAL_LOCK_TIMEOUT_SECONDS em scripts/run_task.ps1
# (duplicado la' de proposito, sem import cruzado, para nao dar a
# schedule_plan.py/run_task.ps1 nenhuma dependencia deste modulo Python).
FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS = sum(step.timeout_seconds for step in PIPELINES["full_daily"])


def _default_executor(step: Step) -> int:
    cmd = [sys.executable, "-m", step.module, *step.args]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), timeout=step.timeout_seconds)
    return proc.returncode


def run_pipeline(name: str, executor=None, preflight_fn=None) -> dict[str, str]:
    """Retorna {step_name: status} onde status em
    {"SUCCESS","FAILED","BLOCKED","SKIPPED"}. executor/preflight_fn sao
    injetaveis para teste (nunca chamam subprocess/banco de verdade nos
    testes)."""
    steps = PIPELINES.get(name)
    if steps is None:
        raise ValueError(f"pipeline desconhecido: {name!r}. Opcoes: {sorted(PIPELINES)}")

    executor = executor or _default_executor
    preflight_fn = preflight_fn or run_preflight

    results: dict[str, str] = {}
    for step in steps:
        if not step.always_run and step.depends_on:
            if any(results.get(dep) != "SUCCESS" for dep in step.depends_on):
                results[step.name] = "SKIPPED"
                print(f"[SKIPPED] {step.name} (depende de {step.depends_on}, nao concluido com SUCCESS)")
                continue

        if step.preflight_source is not None:
            ok, checks = preflight_fn(step.preflight_source)
            for c in checks:
                print(f"  [{'OK' if c.ok else 'BLOCKED'}] {c.detail}")
            if not ok:
                results[step.name] = "BLOCKED"
                print(f"[BLOCKED] {step.name} — comando NAO executado.")
                continue

        print(f"[RUN] {step.name} -> python -m {step.module} {' '.join(step.args)} (cwd={REPO_ROOT}, timeout={step.timeout_seconds}s)")
        try:
            rc = executor(step)
        except subprocess.TimeoutExpired:
            # Timeout INDIVIDUAL deste step — o processo ja foi morto por
            # subprocess.run. Marca FAILED (tentamos e nao terminou a
            # tempo) e segue para o proximo step: uma fonte travada nunca
            # consome o timeout global nem trava fontes independentes.
            results[step.name] = "FAILED"
            print(f"[FAILED] {step.name} — timeout individual de {step.timeout_seconds}s estourado; processo morto, seguindo para o proximo passo.")
            continue

        results[step.name] = "SUCCESS" if rc == 0 else "FAILED"
        print(f"[{results[step.name]}] {step.name} (exit code {rc})")

    return results


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    parser = argparse.ArgumentParser(description="Orquestra uma sequencia de cargas com preflight amarrado a cada passo")
    parser.add_argument("--pipeline", required=True, choices=sorted(PIPELINES))
    args = parser.parse_args()

    results = run_pipeline(args.pipeline)

    print(f"\nRESUMO {args.pipeline}: {results}")
    any_failed_or_blocked = any(v in ("FAILED", "BLOCKED") for v in results.values())
    return 1 if any_failed_or_blocked else 0


if __name__ == "__main__":
    sys.exit(main())
