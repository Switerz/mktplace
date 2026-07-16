"""
Orquestrador que amarra preflight.py a execucao real, e executa uma
sequencia de passos EM PROCESSO (uma etapa so' comeca depois que a
anterior de verdade terminou — nunca depende de intervalo de horario do
Task Scheduler ser maior que o timeout da etapa anterior).

DOIS pipelines nomeados:

  - `full_daily`: as fontes REALMENTE recorrentes (ml, tiktok, regional,
    Produtos ML/TikTok), depois o health check (sempre, mesmo se algo
    antes falhou). E' o unico candidato a rodar sob o Task Scheduler.
  - `shopee_manual_refresh` (Gate C1, 2026-07-16): Shopee (orders, stats,
    ads), Produtos Shopee, o monitor do Bug 8 (so' se o sync Produtos
    Shopee teve SUCCESS nesta mesma execucao) e o health check (sempre,
    por ultimo). Roda so' MANUALMENTE, sob demanda, quando um humano
    confirma que ha' export(s) novo(s) do Shopee para processar — nunca
    agendado.

Motivo da separacao: Shopee e' ingestao MANUAL por natureza (o dado so'
muda quando alguem exporta XLSX/CSV novos e roda a carga) — rodar os
steps Shopee todo dia dentro de `full_daily`, mesmo sem nada novo, nao so'
e' desperdicio (le arquivos grandes so' para descobrir 0 linhas novas)
como e' um risco real: o Gate B6.1c encontrou `daily_shopee_orders`
estourando o timeout individual (900s) processando arquivos Shopee
grandes, derrubando `full_daily` inteiro para FAILED mesmo com ML/TikTok/
regional saudaveis. A causa raiz nao era um bug de codigo nem um timeout
pequeno demais — era um step com cadencia MANUAL rodando dentro de um
pipeline com cadencia DIARIA automatica. Separar os dois pipelines resolve
isso pela raiz, sem precisar aumentar nenhum timeout.

Antes desta separacao (Fase 3A / Gate B1-B6.1d), `full_daily` era um
UNICO pipeline com todas as fontes diarias (ml, tiktok, shopee+stats+ads),
Produtos (ml, tiktok, shopee), Bug 8 e health check — isso substituiu um
desenho ainda mais antigo de 2 pipelines agendados em horarios separados
(`daily_ingestion` @ 06:00 e `produtos_and_monitor` @ 06:35), descartado
porque nao havia garantia de que o primeiro tivesse terminado antes do
segundo comecar. Essa garantia ("um pipeline so' avanca depois que o
anterior de verdade terminou, nunca por horario") continua valendo — so'
que agora aplicada a DOIS pipelines independentes, cada um com seu proprio
lock (ver `scripts/run_task.ps1`), nao mais amarrados um ao outro.

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

Politica de criticidade (Gate B1, 2026-07-15): cada `Step` tem um campo
`critical` (default True). Um step nao-critico (`sync_produtos_shopee` —
bloqueado por `LOCAL_PG_URL` ausente e' um gap manual conhecido, nao uma
falha de execucao) que FAILED/BLOCKED nunca vira exit code != 0 sozinho;
so' rebaixa o resultado para DEGRADED. Isso existe para o pipeline inteiro
nao reportar "falha" so' por causa de um gap Shopee ja conhecido, enquanto
ainda alerta de verdade (exit 1) se ML/TikTok/qualquer step critico
realmente quebrar. Ver `compute_overall_status`. Dentro de
`shopee_manual_refresh`, `daily_shopee_orders`/`daily_shopee_stats`/
`daily_shopee_ads` SAO criticos (Gate C1) — e' uma execucao manual e
deliberada; se falhar de verdade, o operador precisa saber (FAILED), nao
so' ver um DEGRADED silencioso.

Regional (Gate B2, 2026-07-15): `gold_regional_incremental` +
`sync_region_if_needed` estao em `full_daily`, ambos CRITICOS (sem gap
manual conhecido aceito, diferente de sync_produtos_shopee). O sync Neon
so' roda de fato quando o proprio wrapper (via diagnose) constata divergencia
— evita TRUNCATE+INSERT e uma tabela de backup nova todo dia sem necessidade.
O Task Scheduler continua Disabled; a ativacao fica para um gate futuro
(C3), apos um periodo de confianca com execucoes MANUAIS observadas de
`full_daily` ja' sem Shopee (Gate C2).

Uso:
    python -m pipelines.ops.orchestrate --pipeline full_daily
    python -m pipelines.ops.orchestrate --pipeline shopee_manual_refresh
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
    # Gate B1: default True preserva o comportamento de todo step ja
    # existente. Um step critical=False que FAILED/BLOCKED nunca faz o
    # pipeline inteiro reportar FAILED/exit 1 sozinho — so' rebaixa o
    # resultado geral para DEGRADED (ver compute_overall_status).
    critical: bool = True


# DOIS pipelines independentes, cada um sob seu proprio lock externo (ver
# scripts/run_task.ps1) — health_check so' roda depois que TUDO antes, DENTRO
# do mesmo pipeline, realmente terminou (nunca por horario).
PIPELINES: dict[str, tuple[Step, ...]] = {
    # Gate C1 (2026-07-16): so' fontes REALMENTE recorrentes/diarias — Shopee
    # foi removido daqui (ver shopee_manual_refresh, abaixo) porque e'
    # ingestao MANUAL por natureza, e rodar seus steps todo dia sem dado novo
    # e' desperdicio e risco (achado do Gate B6.1c: daily_shopee_orders
    # estourou o timeout individual processando arquivos grandes, derrubando
    # full_daily inteiro mesmo com ML/TikTok/regional saudaveis).
    "full_daily": (
        Step("daily_ml", "pipelines.ingestion.daily_performance", ("--source", "ml", "--mode", "incremental"), timeout_seconds=900, preflight_source="ml_daily"),
        Step("daily_tiktok", "pipelines.ingestion.daily_performance", ("--source", "tiktok", "--mode", "incremental"), timeout_seconds=900, preflight_source="tiktok_daily"),
        # Gate B2 (2026-07-15): regional (Gold incremental + sync Neon
        # condicional), ambos CRITICOS de proposito — diferente de
        # sync_produtos_shopee (Gate B1), aqui nao ha' nenhum gap manual
        # conhecido aceito; um FAILED/BLOCKED real deve reprovar o pipeline
        # (FAILED), nao so' rebaixar para DEGRADED. O periodo de confianca
        # antes de ativar o Task Scheduler vem de execucoes MANUAIS
        # observadas, nao de marcar estes steps como nao-criticos.
        Step("gold_regional_incremental", "pipelines.ingestion.gold_regional.loader", ("--incremental",), timeout_seconds=300, preflight_source="gold_regional_incremental", critical=True),
        # So' roda se gold_regional_incremental teve SUCCESS nesta mesma
        # execucao — nunca dispara um sync Neon baseado em Gold desatualizado
        # ou parcialmente carregado. O proprio wrapper decide, via diagnose,
        # se um sync e' realmente necessario (evita TRUNCATE+INSERT e backup
        # novo todo dia quando Data Mart e Neon ja' estao em paridade).
        Step("sync_region_if_needed", "pipelines.ops.sync_region_if_needed", (), timeout_seconds=120, preflight_source="sync_region_daily", depends_on=("gold_regional_incremental",), critical=True),
        Step("sync_produtos_ml", "pipelines.sync_produtos", ("--source", "ml"), timeout_seconds=600, preflight_source="produtos_ml"),
        Step("sync_produtos_tiktok", "pipelines.sync_produtos", ("--source", "tiktok"), timeout_seconds=600, preflight_source="produtos_tiktok"),
        # Sempre roda por ultimo, mesmo se algo anterior falhou/bloqueou —
        # e' o resumo do estado real, precisa rodar para reportar a falha.
        # always_run + ser o ULTIMO item desta tupla e' o que garante
        # "health check e' comprovadamente o ultimo passo global": nao ha
        # segunda tarefa/segundo lock depois deste, entao nao ha como algo
        # rodar depois dele nesta mesma execucao agendada.
        Step("health_check", "pipelines.ops.health_check", ("--json",), timeout_seconds=180, always_run=True),
    ),
    # Gate C1 (2026-07-16): pipeline MANUAL, sob demanda — nunca agendado no
    # Task Scheduler (nao ha' entrada correspondente em schedule_plan.py
    # PROPOSED_SCHEDULE). O operador roda isto explicitamente so' quando
    # confirma que ha' export(s) Shopee novo(s) para processar.
    "shopee_manual_refresh": (
        # CRITICOS de proposito (diferente do gap manual de sync_produtos_shopee,
        # abaixo): esta e' uma execucao MANUAL e deliberada — se uma dessas
        # 3 fontes falhar de verdade, o operador precisa ver FAILED, nao um
        # DEGRADED silencioso que poderia mascarar a carga incompleta.
        Step("daily_shopee_orders", "pipelines.ingestion.daily_performance", ("--source", "shopee", "--mode", "incremental"), timeout_seconds=900, preflight_source="shopee_daily", critical=True),
        Step("daily_shopee_stats", "pipelines.ingestion.daily_performance", ("--source", "shopee-stats", "--mode", "incremental"), timeout_seconds=900, preflight_source="shopee-stats_daily", critical=True),
        Step("daily_shopee_ads", "pipelines.ingestion.daily_performance", ("--source", "shopee-ads", "--mode", "incremental"), timeout_seconds=900, preflight_source="shopee-ads_daily", critical=True),
        # Nao-critico (Gate B1, preservado aqui): bloqueado por LOCAL_PG_URL
        # ausente e' um gap manual conhecido (docs/runbook_sync_produtos.md),
        # nao uma falha de execucao — nao deve fazer o pipeline inteiro
        # reportar FAILED/exit 1 sozinho, mesmo dentro desta execucao manual.
        Step("sync_produtos_shopee", "pipelines.sync_produtos", ("--source", "shopee"), timeout_seconds=600, preflight_source="produtos_shopee", critical=False),
        # So' roda se sync_produtos_shopee tiver SUCCEEDED de verdade nesta
        # mesma execucao — nunca por o relogio ter passado tempo suficiente.
        Step("monitor_bug8", "pipelines.reconciliation.monitor_bug8_invariants", ("--skip-source",), timeout_seconds=300, depends_on=("sync_produtos_shopee",)),
        # Sempre roda por ultimo — reporta o estado real da carga Shopee que
        # acabou de rodar (inclusive se algo antes falhou/bloqueou).
        Step("health_check", "pipelines.ops.health_check", ("--json",), timeout_seconds=180, always_run=True),
    ),
}

# Soma dos timeouts individuais de full_daily = 900*2 (ml, tiktok) + 300
# (gold_regional_incremental) + 120 (sync_region_if_needed) + 600*2
# (produtos ml, tiktok) + 180 (health_check) = 3600s (~1h). Caiu de 7200s
# para 3600s no Gate C1 (2026-07-16), que removeu os 3 steps Shopee diarios
# (900*3=2700s) + sync_produtos_shopee (600s) + monitor_bug8 (300s) daqui —
# ver PIPELINES["shopee_manual_refresh"]. O timeout EXTERNO (run_with_lock.ps1
# -TimeoutSeconds, ver scripts/run_task.ps1) tem que ser MAIOR que essa
# soma, com margem — senao o lock externo mata o processo pai antes que os
# timeouts internos por step tenham chance de proteger as fontes
# independentes seguintes. Ver RECOMMENDED_EXTERNAL_LOCK_TIMEOUT_SECONDS em
# scripts/run_task.ps1 (duplicado la' de proposito, sem import cruzado,
# para nao dar a schedule_plan.py/run_task.ps1 nenhuma dependencia deste
# modulo Python).
FULL_DAILY_STEP_TIMEOUT_BUDGET_SECONDS = sum(step.timeout_seconds for step in PIPELINES["full_daily"])

# Soma dos timeouts individuais de shopee_manual_refresh = 900*3 (orders,
# stats, ads) + 600 (sync_produtos_shopee) + 300 (monitor_bug8) + 180
# (health_check) = 3780s (~1h03). Pipeline MANUAL, sem Task Scheduler
# correspondente — este numero existe so' para quem quiser rodar via
# scripts/run_task.ps1 -TaskKey shopee_manual_refresh reaproveitando o
# mesmo wrapper de lock/timeout/log (ver Get-TaskDefinitions la').
SHOPEE_MANUAL_REFRESH_STEP_TIMEOUT_BUDGET_SECONDS = sum(step.timeout_seconds for step in PIPELINES["shopee_manual_refresh"])


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


def compute_overall_status(name: str, results: dict[str, str]) -> str:
    """Status geral do pipeline (Gate B1), calculado a partir do `critical`
    de cada Step (relookup em PIPELINES[name] — `results` so' tem status,
    nao o Step inteiro) cruzado com o status de cada step:
      - "FAILED": algum step CRITICO com status FAILED ou BLOCKED.
      - "DEGRADED": nenhum critico falhou/bloqueou, mas algum NAO-CRITICO
        (ex.: sync_produtos_shopee bloqueado por LOCAL_PG_URL) FAILED ou
        BLOCKED.
      - "OK": nenhum FAILED/BLOCKED em nenhum step (criticos ou nao).

    SKIPPED nunca conta como falha aqui, independente de criticidade — um
    step critico pulado porque dependia de um nao-critico bloqueado
    (ex.: monitor_bug8 quando sync_produtos_shopee e' BLOCKED) nao derruba
    o resultado geral sozinho; o que derruba (para DEGRADED) e' o proprio
    sync_produtos_shopee BLOCKED, ja contado como nao-critico."""
    steps = PIPELINES[name]
    critical_by_name = {s.name: s.critical for s in steps}

    has_critical_failure = any(
        critical_by_name.get(step_name, True) and status in ("FAILED", "BLOCKED")
        for step_name, status in results.items()
    )
    if has_critical_failure:
        return "FAILED"

    has_noncritical_failure = any(
        not critical_by_name.get(step_name, True) and status in ("FAILED", "BLOCKED")
        for step_name, status in results.items()
    )
    if has_noncritical_failure:
        return "DEGRADED"

    return "OK"


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    parser = argparse.ArgumentParser(description="Orquestra uma sequencia de cargas com preflight amarrado a cada passo")
    parser.add_argument("--pipeline", required=True, choices=sorted(PIPELINES))
    args = parser.parse_args()

    results = run_pipeline(args.pipeline)
    overall = compute_overall_status(args.pipeline, results)

    steps = PIPELINES[args.pipeline]
    critical_results = {s.name: results[s.name] for s in steps if s.critical}
    noncritical_results = {s.name: results[s.name] for s in steps if not s.critical}

    print(f"\nRESUMO {args.pipeline}:")
    print(f"  CRITICO: {critical_results}")
    print(f"  NAO-CRITICO (esperado, nao derruba o pipeline sozinho): {noncritical_results}")
    print(f"STATUS GERAL: {overall}")

    # Exit 1 so' em FAILED (falha/bloqueio critico). DEGRADED (so' gap
    # nao-critico conhecido, ex.: Shopee manual) e OK retornam exit 0 —
    # o pipeline nao deve "falhar" todo dia por um gap ja conhecido.
    return 1 if overall == "FAILED" else 0


if __name__ == "__main__":
    sys.exit(main())
