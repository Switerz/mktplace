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

Politica de criticidade (Gate B1, 2026-07-15; completada no Gate B4,
2026-07-15; estendida no Gate C1, 2026-07-16): cada fonte de execucao
(`ExpectedSource`) e cada entrada de frescor de dado (`DataFreshnessResult`)
tem um campo `critical` (default True). `fact_marketplace_daily_performance[shopee]`
e a entrada de cadencia manual `fact_shopee_product_monthly` (frescor de
DADO) sao marcadas `critical=False` desde o Gate B1; a entrada de EXECUCAO
`shopee_product_monthly` (rastreio de quando `sync_produtos_shopee` rodou
com sucesso) ganhou `critical=False` no Gate B4, pelo mesmo motivo — sem
isso, o gap manual conhecido de `LOCAL_PG_URL` (que bloqueia
`sync_produtos_shopee` no preflight) fazia o proprio `health_check`
reprovar `ok_critical` todo dia, mesmo com ML/TikTok/regional saudaveis.
No Gate C1, as entradas de EXECUCAO `shopee_daily`/`shopee-stats_daily`/
`shopee-ads_daily` TAMBEM ganharam `critical=False` — desde esse gate, os
steps correspondentes saem de `full_daily` (que roda todo dia) e passam a
viver no pipeline MANUAL `shopee_manual_refresh`
(`orchestrate.py::PIPELINES`), rodado so' sob demanda; sem essa marcacao,
a EXECUCAO delas ficaria sem sucesso registrado por mais de 48h assim que
`full_daily` parasse de roda-las, recriando o mesmo alarme-fadiga. Shopee
e' ingestao manual, sabidamente defasada ate alguem atualizar os exports/
rodar `shopee_manual_refresh`, nao uma falha de pipeline. `build_report()`
devolve DOIS campos:
  - `ok`: visao completa, considerando TODAS as fontes (criticas e
    conhecidas/manuais) — so' para visibilidade/JSON, nunca decide o exit
    code sozinho.
  - `ok_critical`: so' fontes CRITICAS (+ invariantes do Bug 8, sempre
    critico) — e' isso que `main()` usa para o exit code. Um Shopee
    manual defasado nunca faz `python -m pipelines.ops.health_check`
    retornar exit 1 sozinho.

OBSOLESCENCIA DO SNAPSHOT MANUAL DA AVOE (Gate AVH-4C, 2026-09-08)
------------------------------------------------------------------
Dimensao propria, `avoe_snapshot`, e NUNCA critica — nem em `error`. A Avoe e'
fonte externa, de terceiro, com carga manual e sem cadencia acordada com a
Torre; uma exportacao que ninguem fez nao e' quebra de pipeline nosso. O estado
aparece no relatorio e derruba `ok` (visibilidade), jamais `ok_critical`, de
modo que o step `health_check` do `full_daily` nunca falha por causa dela. E' a
mesma politica dos Gates B4/C1 para o Shopee manual, pelo mesmo motivo: evitar
alarme-fadiga sobre um gap conhecido e aceito.

Tres coisas medidas SEPARADAMENTE, porque divergem: a ultima EXECUCAO `success`
do importador, a IDADE da captura na origem (`captured_at` — reimportar o mesmo
arquivo nao a rejuvenesce) e a DISPONIBILIDADE pelo criterio do serving. As
regras de validacao vem importadas de `app.services.avoe_snapshot_service`, nao
reescritas: existir `MAX(captured_at)` nao e' saude, e health check e tela nao
podem discordar sobre o que e' captura valida.

Os limiares (`AVOE_AGING_DAYS`, `AVOE_STALE_DAYS`) sao OPERACIONAIS NOSSOS, nao
SLA da fonte — ver o comentario na definicao deles.

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
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api"))

from pipelines.reconciliation.diagnose_bug8_neon import (  # noqa: E402
    REAL_TABLE,
    _get_neon_url,
    _neon_readonly,
    _sanitize_url,
)
from pipelines.reconciliation.monitor_bug8_invariants import check_db_invariants  # noqa: E402
# UE2-C: o contrato de frescor de afiliados vive no modulo que OWNS o watermark.
# Importar em vez de reimplementar e' o que impede health check e sync de
# divergirem sobre o que significa "fresco".
from pipelines import sync_tiktok_affiliate_cost_order_monthly as sync_afiliados  # noqa: E402
# UE8-I4: mesmo motivo — o nome canonico de auditoria e o teto D-1 vivem no
# modulo do sync. Importar em vez de reescrever e' o que impede health check e
# sync de divergirem sobre qual fonte cobrar e ate que dia.
from pipelines import sync_tiktok_order_discounts_daily as sync_descontos  # noqa: E402
# AVH-4C: as REGRAS de "qual captura da Avoe e' valida" vivem no servico que
# serve a API. Importar as mesmas funcoes puras — em vez de reescrever a
# validacao aqui — e' o que impede health check e tela de discordarem sobre
# disponibilidade. O `sys.path.insert` acima e' o que torna `app.*` importavel.
from app.services import avoe_snapshot_service as avoe_svc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

MARKETPLACE_LABELS = {1: "tiktok", 2: "ml", 3: "shopee"}
DAILY_DATA_FRESHNESS_THRESHOLD_DAYS = 3

# ---------------------------------------------------------------------------
# Obsolescencia do snapshot manual da Avoe (Gate AVH-4C)
# ---------------------------------------------------------------------------
# LIMIARES OPERACIONAIS NOSSOS, NAO SLA.
#
# A Avoe nunca acordou cadencia, janela de atualizacao ou prazo de exportacao
# com a Torre. Nao existe compromisso da fonte para cobrar. Estes numeros sao
# escolha de OPERACAO, para que uma captura esquecida apareca como esquecida em
# vez de passar por atual — e podem ser mudados por decisao nossa, sem negociar
# com ninguem.
#
# `AVOE_STALE_DAYS` e' 30 porque a tela ja usa 30 para o selo "captura antiga"
# (`DIAS_PARA_CAPTURA_ANTIGA` em `apps/web/src/lib/avoe-snapshot-contract.ts`,
# Gate AVH-4B-S). Operacao e tela dizendo "antiga" em dias diferentes seria
# ruido puro. `AVOE_AGING_DAYS` e' o aviso ANTES disso: 14 dias da tempo de
# alguem exportar de novo antes de a tela comecar a alertar o usuario final.
AVOE_AGING_DAYS = 14
AVOE_STALE_DAYS = 30

#: Mensagem FIXA de falha de leitura. Nada de SQL, DSN, host ou texto de driver.
AVOE_ERRO_LEITURA = (
    "nao foi possivel ler o estado do snapshot da Avoe (falha na consulta "
    "read-only); 'nao consegui verificar' nao e' evidencia de que esta atual"
)


@dataclass(frozen=True)
class ExpectedSource:
    source_name: str
    cadence: str  # "daily" | "manual_monthly"
    exec_threshold_hours: float
    # Gate B1: default True preserva o comportamento de toda fonte ja
    # existente. Gate B4 (2026-07-15): critical=False tambem se aplica a
    # `shopee_product_monthly` — a EXECUCAO desse sync so' acontece de fato
    # quando `sync_produtos_shopee` roda (bloqueado por LOCAL_PG_URL
    # ausente, gap manual conhecido desde o Gate B1); ficar sem sucesso
    # registrado e' consequencia direta desse mesmo gap, nao uma quebra nova
    # de pipeline — ver docstring do modulo.
    critical: bool = True


# Lista EXPLICITA e completa das fontes que esperamos ver em
# audit.source_sync_run. Uma fonte fora desta lista nao e' avaliada; uma
# fonte NESTA lista sem nenhuma linha no audit log e' sempre stale=True
# (nunca "ausente e' OK").
EXPECTED_SOURCES: tuple[ExpectedSource, ...] = (
    ExpectedSource("ml_daily", "daily", 30),
    ExpectedSource("tiktok_daily", "daily", 30),
    # Gate C1 (2026-07-16): critical=False nas 3 entradas abaixo. Desde este
    # gate, `daily_shopee_orders`/`daily_shopee_stats`/`daily_shopee_ads`
    # saem de `full_daily` (que roda todo dia) e passam a viver em
    # `orchestrate.py::PIPELINES["shopee_manual_refresh"]` (roda so' quando
    # o operador confirma export Shopee novo). Sem esta marcacao, a
    # EXECUCAO desses 3 steps ficaria sem sucesso registrado por mais de
    # 48h assim que `full_daily` parasse de roda-los todo dia, e
    # `ok_critical` voltaria a False so' por causa desse gap ja' aceito —
    # exatamente o alarme-fadiga que o Gate B4 corrigiu para
    # `shopee_product_monthly`, agora pelo mesmo motivo para estas 3 fontes.
    ExpectedSource("shopee_daily", "daily", 48, critical=False),
    ExpectedSource("shopee-stats_daily", "daily", 48, critical=False),
    ExpectedSource("shopee-ads_daily", "daily", 48, critical=False),
    ExpectedSource("tiktok_product_daily", "daily", 30),
    ExpectedSource("ml_produto_ranking", "daily", 30),
    # Gate B4 (2026-07-15): critical=False. Em teoria a EXECUCAO deste sync
    # roda todo dia (mesmo sem dado novo) independente do DADO upstream (que
    # tem cadencia mensal/manual, ver fetch_data_freshness) — mas, na
    # pratica, essa execucao e' feita por `sync_produtos_shopee`, que fica
    # BLOCKED por `LOCAL_PG_URL` ausente (gap manual conhecido, ja
    # nao-critico em orchestrate.py desde o Gate B1). Sem essa marcacao,
    # o proprio step `health_check` do full_daily reprovava (FAILED) o
    # pipeline inteiro todo dia por causa desse MESMO gap ja aceito,
    # mesmo com ML/TikTok/regional saudaveis (achado do Gate B3).
    ExpectedSource("shopee_product_monthly", "daily", 48, critical=False),
    # Gate S3 (2026-08-18): os dois snapshots de serving que `/inteligencia` e
    # `/brand-detail` passaram a consumir. CRITICOS e com o mesmo contrato de 30h
    # das outras fontes diarias criticas, porque:
    #   - os dois steps correspondentes (`serving_ml_cross_company` e
    #     `serving_tiktok_channel_efficiency`) rodam TODO dia dentro de
    #     `full_daily` e sao `critical=True` no orchestrate;
    #   - sem estas entradas, o sync poderia falhar ou nem executar por dias e o
    #     health check ainda reportaria `ok_critical=true`, deixando as duas telas
    #     defasadas em silencio. Foi exatamente essa a lacuna apontada na revisao
    #     da Task 2/3.
    # O nome e' o MESMO `name` do target em sync_serving_snapshots.SPECS — um
    # teste trava essa igualdade para que CLI, audit log e health check nunca
    # divirjam.
    ExpectedSource("ml_cross_company", "daily", 30),
    ExpectedSource("tiktok_channel_efficiency", "daily", 30),
    # Gate UE2-C Task 2/3 (2026-08-28): custo de afiliado do TikTok. CRITICO e
    # com o mesmo contrato de 30h das outras fontes diarias criticas, porque o
    # step correspondente roda TODO dia em `full_daily` e e' `critical=True`.
    #
    # E' o nome CANONICO — escrito em toda execucao real, qualquer modo. O
    # segundo nome de auditoria (`..._full`) NAO entra aqui de proposito: ele
    # marca o ciclo do full, que e' MENSAL, e cobrar 30h dele reprovaria o
    # pipeline todo dia. A obrigacao mensal e' verificada pelo proprio sync.
    ExpectedSource(sync_afiliados.CANONICAL_AUDIT_SOURCE, "daily", 30),
    # Gate UE8-I4 Task 1/2 (2026-09-08): descontos e subsidios do pedido
    # TikTok. CRITICO e com o mesmo contrato de 30h, porque o step roda TODO
    # dia em `full_daily` e e' `critical=True` la.
    #
    # E' o nome CANONICO, escrito em toda execucao real, qualquer modo. Os dois
    # nomes derivados (`..._full` e `..._backfill`) NAO entram aqui de
    # proposito: marcam ciclos MENSAL e SEMANAL, e cobrar 30h deles reprovaria
    # o pipeline todo dia. Essas obrigacoes duraveis sao verificadas dentro do
    # proprio sync, sob o advisory lock.
    ExpectedSource(sync_descontos.CANONICAL_AUDIT_SOURCE, "daily", 30),
)

@dataclass
class AffiliateWatermarkStatus:
    """Frescor do custo de afiliado — DUAS dimensoes, nunca colapsadas.

    Um job que roda todo dia sobre uma fonte parada nao e' dado fresco, e um
    watermark atual com job morto ha tres dias tambem nao. Por isso as duas
    condicoes aparecem separadas no relatorio, alem do veredito combinado.
    """
    source_name: str
    status: str                 # fresh | stale | unknown
    reason: str
    last_success_at: str | None
    execution_age_hours: float | None
    execution_recent: bool | None
    watermark_date: str | None
    expected_batch_date: str | None
    watermark_current: bool | None
    late_batches: int | None
    escalate: bool
    stale: bool
    critical: bool = True


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
    critical: bool = True


def _now() -> datetime:
    """Ponto unico de leitura do relogio real (UTC). Isolado numa funcao
    pequena para que build_report()/main() possam ser testados com um
    relogio fixo sem monkeypatchar datetime.now() global nem depender de
    freezegun."""
    return datetime.now(timezone.utc)


def fetch_source_statuses(conn, now: datetime | None = None) -> list[SourceStatus]:
    now = now or _now()
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
                    critical=expected.critical,
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
                critical=expected.critical,
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
    # Gate B1: default True preserva o comportamento de toda entrada ja
    # existente. critical=False marca fontes manuais/conhecidas (hoje, so'
    # as derivadas de Shopee) — stale nelas nunca deve decidir o exit code
    # de `main()` sozinho (ver `ok_critical` em build_report()).
    critical: bool = True


def _evaluate_date_freshness(
    label: str, cadence: str, max_value, today: date, threshold_days: float | None, *, critical: bool = True,
) -> DataFreshnessResult:
    if max_value is None:
        return DataFreshnessResult(label, cadence, None, None, threshold_days, True, f"{label}: tabela sem nenhuma linha", critical)

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
            critical,
        )

    if threshold_days is None:
        # cadencia manual/mensal: reporta, nunca marca como stale por si
        # so' (evita falso positivo — ver docstring do modulo).
        return DataFreshnessResult(
            label, cadence, value_date.isoformat(), days_since, None, False,
            f"{label}: cadencia {cadence}, ultimo periodo ha {days_since}d — nao avaliado contra threshold "
            f"(a execucao do sync correspondente e' o que detecta uma quebra real)",
            critical,
        )

    stale = days_since > threshold_days
    reason = (
        f"{label}: dado com {days_since}d, acima do limite de {threshold_days}d"
        if stale
        else f"{label}: dado fresco ({days_since}d, limite {threshold_days}d)"
    )
    return DataFreshnessResult(label, cadence, value_date.isoformat(), days_since, threshold_days, stale, reason, critical)


def fetch_data_freshness(conn, today: date | None = None) -> list[DataFreshnessResult]:
    today = today or _now().date()
    cur = conn.cursor()
    results: list[DataFreshnessResult] = []

    cur.execute(
        "SELECT marketplace_id, MAX(date) AS max_date FROM marts.fact_marketplace_daily_performance GROUP BY marketplace_id"
    )
    daily_rows = {int(r["marketplace_id"]): r["max_date"] for r in cur.fetchall()}
    for mkt_id, label in MARKETPLACE_LABELS.items():
        # Shopee (ingestao manual, exports XLSX/CSV) e' nao-critico: fica
        # defasado ate alguem atualizar os arquivos, nao e' uma falha de
        # pipeline. ML/TikTok continuam criticos.
        results.append(
            _evaluate_date_freshness(
                f"fact_marketplace_daily_performance[{label}]", "daily",
                daily_rows.get(mkt_id), today, DAILY_DATA_FRESHNESS_THRESHOLD_DAYS,
                critical=(label != "shopee"),
            )
        )

    cur.execute("SELECT MAX(date) AS m FROM marts.fact_tiktok_product_daily")
    results.append(_evaluate_date_freshness("fact_tiktok_product_daily", "daily", cur.fetchone()["m"], today, DAILY_DATA_FRESHNESS_THRESHOLD_DAYS))

    cur.execute("SELECT MAX(refreshed_at) AS m FROM marts.fact_ml_produto_ranking")
    results.append(_evaluate_date_freshness("fact_ml_produto_ranking", "daily", cur.fetchone()["m"], today, DAILY_DATA_FRESHNESS_THRESHOLD_DAYS))

    # Gate S3 (2026-08-18) — as duas fatos novas.
    #
    # A EXECUCAO (audit.source_sync_run, acima) e a COBERTURA DO DADO (aqui) sao
    # sinais DIFERENTES e os dois precisam existir: um sync pode executar com
    # sucesso todo dia e ainda assim servir dado que parou de avancar, porque a
    # fonte upstream parou. Um sinal so' nao detecta o outro caso.
    #
    # `fact_ml_cross_company_summary` NAO tem data de negocio: a fonte
    # (gold.ml_cross_company_summary) e' snapshot sem dimensao temporal. Usar
    # `MAX(synced_at)` — o campo de auditoria da propria fotografia — e' o mesmo
    # padrao ja adotado para `fact_ml_produto_ranking` (MAX(refreshed_at)) logo
    # acima, e evita fabricar uma data de negocio que a fonte nao tem.
    #
    # Tabela vazia ou `MAX(...)` NULL cai no primeiro ramo de
    # `_evaluate_date_freshness`, que ja devolve stale=True com "tabela sem
    # nenhuma linha" — ausencia nunca e' convertida em zero nem em "fresco".
    cur.execute("SELECT MAX(synced_at) AS m FROM marts.fact_ml_cross_company_summary")
    results.append(_evaluate_date_freshness(
        "fact_ml_cross_company_summary[synced_at]", "daily", cur.fetchone()["m"],
        today, DAILY_DATA_FRESHNESS_THRESHOLD_DAYS))

    # `fact_tiktok_channel_efficiency_daily` TEM data de negocio diaria. O teto
    # normal e' D-1 (o serving nunca publica D0), entao um dia de defasagem e'
    # esperado e cabe folgado no limite de 3 dias.
    cur.execute("SELECT MAX(date) AS m FROM marts.fact_tiktok_channel_efficiency_daily")
    results.append(_evaluate_date_freshness(
        "fact_tiktok_channel_efficiency_daily", "daily", cur.fetchone()["m"],
        today, DAILY_DATA_FRESHNESS_THRESHOLD_DAYS))

    # Cadencia manual_monthly (Shopee Produtos) — ja nunca vira stale por si
    # so' (threshold_days=None), mas marcada nao-critica tambem para deixar
    # explicito que e' outro ponto de ingestao manual Shopee, consistente
    # com a fonte de daily-performance acima.
    cur.execute(f"SELECT MAX(ref_month) AS m FROM marts.{REAL_TABLE}")
    results.append(_evaluate_date_freshness(f"marts.{REAL_TABLE}[ref_month]", "manual_monthly", cur.fetchone()["m"], today, None, critical=False))

    cur.close()
    return results


def run_bug8_check(conn) -> dict:
    """check_db_invariants imprime uma linha informativa (pensada para o
    CLI standalone de monitor_bug8_invariants) — suprimida aqui para que a
    saida deste modulo (inclusive --json) fique limpa e previsivel."""
    with contextlib.redirect_stdout(io.StringIO()):
        problems = check_db_invariants(conn)
    return {"ok": not problems, "problems": problems}


def fetch_affiliate_watermark_status(conn, now: datetime | None = None
                                     ) -> AffiliateWatermarkStatus:
    """Frescor do custo de afiliado: execucao canonica x avanco da fonte.

    Le `audit.source_sync_run` (execucao) e o `sync_state` (watermark) e delega
    a classificacao para `classify_affiliate_freshness`, no modulo do sync —
    contrato unico, sem copia divergente aqui.

    NAO usa `MAX(ref_month)` da fact como frescor: `ref_month` e' competencia
    comercial e o watermark e' atualizacao tecnica. Um mes comercial pode ficar
    parado por semanas com a fonte perfeitamente em dia, e vice-versa.
    """
    now = now or _now()
    nome = sync_afiliados.CANONICAL_AUDIT_SOURCE
    cur = conn.cursor()

    cur.execute(
        """
        SELECT MAX(finished_at) AS t FROM audit.source_sync_run
        WHERE source_name = %s AND status = 'success'
        """,
        (nome,),
    )
    linha = cur.fetchone()
    ultimo_sucesso = linha["t"] if linha else None

    watermark = None
    try:
        cur.execute(
            f"""
            SELECT last_successful_upper_bound AS w
            FROM {sync_afiliados.SYNC_STATE_TABLE}
            WHERE target_table = %s
            """,  # noqa: S608 — identificador constante do modulo do sync
            (sync_afiliados.TARGET_TABLE,),
        )
        linha = cur.fetchone()
        # `.get`: antes da primeira carga a linha nao existe, e ausencia
        # LEGITIMA de watermark e' `unknown` — nunca um KeyError que derrube o
        # relatorio por uma fonte que ainda nao foi ativada.
        watermark = linha.get("w") if linha else None
    except psycopg2.Error:
        # FAIL-CLOSED. Erro de BANCO ao ler o watermark nao e' "sem watermark":
        # permissao revogada, relacao ausente, schema incompativel e conexao
        # abortada sao problemas que precisam aparecer, nao virar `unknown`
        # silencioso — que e' justamente o estado nao-critico de "ainda nao
        # ativado". Confundir os dois esconderia uma quebra real atras de uma
        # espera legitima.
        cur.close()
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()      # devolve a transacao ao estado utilizavel
        return AffiliateWatermarkStatus(
            source_name=nome,
            status="error",
            # Categoria FIXA: nada de SQL, DSN, host, usuario ou mensagem bruta
            # do driver.
            reason="falha de banco ao ler o watermark do sync de afiliados",
            last_success_at=ultimo_sucesso.isoformat() if ultimo_sucesso else None,
            execution_age_hours=None, execution_recent=None,
            watermark_date=None, expected_batch_date=None,
            watermark_current=None, late_batches=None, escalate=True,
            stale=True, critical=True,
        )
    # Excecao que NAO e' de banco (bug de codigo) propaga: esconder defeito
    # nosso atras de "erro de fonte" e' exatamente o que nao se pode fazer.
    cur.close()

    v = sync_afiliados.classify_affiliate_freshness(ultimo_sucesso, watermark, now)
    return AffiliateWatermarkStatus(
        source_name=nome,
        status=v["status"],
        reason=v["motivo"],
        last_success_at=ultimo_sucesso.isoformat() if ultimo_sucesso else None,
        execution_age_hours=v.get("execution_age_hours"),
        execution_recent=v["execution_recent"],
        watermark_date=str(v["watermark_date"]) if v["watermark_date"] else None,
        expected_batch_date=(str(v["expected_batch_date"])
                             if v["expected_batch_date"] else None),
        watermark_current=v["watermark_current"],
        late_batches=v["late_batches"],
        escalate=bool(v.get("escalate")),
        # `unknown` nao reprova NESTA dimensao — e so' nesta.
        #
        # A ausencia de watermark nao acrescenta uma SEGUNDA reprovacao, mas
        # tambem nao neutraliza a primeira: a execucao continua sendo cobrada
        # pela entrada canonica em `EXPECTED_SOURCES`, que e' `critical=True` e
        # fica `stale` enquanto nao houver sucesso registrado. No pre-piloto
        # real — nenhuma execucao, nenhum watermark — o resultado correto e'
        # `affiliate_watermark.status="unknown"` com `stale=False` E
        # `ok_critical=False`, vindo da dimensao de execucao. Uma rotina ja
        # declarada critica nao pode deixar o health check verde antes da
        # primeira execucao comprovada.
        stale=(v["status"] == sync_afiliados.FRESHNESS_STALE),
    )


#: Quantos dias fechados de atraso a cobertura tolera antes de reprovar.
#: `1` porque a janela do sync SEMPRE termina em D-1: se o job rodou hoje,
#: `source_max_date` e' D-1 e o atraso e' zero. Um dia de folga cobre a
#: execucao de madrugada que atravessa a meia-noite BRT.
DISCOUNTS_COVERAGE_MAX_LAG_DAYS = 1


#: Mensagem FIXA e sanitizada quando a leitura da cobertura falha. Nunca
#: interpola SQL, DSN, host, credencial nem texto de driver.
DISCOUNTS_COVERAGE_ERROR_NOTE = (
    "nao foi possivel ler a auditoria/fato de descontos para avaliar a "
    "cobertura — estado desconhecido, tratado como falha"
)

#: Texto FIXO de competencia ausente. Nao afirma qual e' a causa: as fontes
#: atuais nao distinguem ausencia de vendas de lacuna de ingestao.
DISCOUNTS_COVERAGE_MISSING_NOTE = (
    "O job alcancou a janela esperada, mas nao ha competencia recente na "
    "fato; as fontes atuais nao distinguem ausencia de vendas de lacuna de "
    "ingestao."
)


@dataclass(frozen=True)
class DiscountsCoverageStatus:
    """Cobertura OPERACIONAL dos descontos — dimensao INDEPENDENTE da execucao.

    Tres estados FACTUAIS, nunca uma causa inferida:

      - **`execucao_atrasada`**: o `source_max_date` da ultima execucao
        `success` ficou para tras do ultimo dia fechado. Isso e' verificavel:
        a janela publicada nao avancou;
      - **`competencia_ausente`**: o job alcancou a janela esperada, mas
        `MAX(ref_date)` na fato nao tem competencia recente. **NAO** se afirma
        "fonte parada": com as fontes atuais, ausencia de vendas e lacuna de
        ingestao sao indistinguiveis — e o nome do estado nao pode escolher uma
        das duas;
      - **`error`**: a leitura falhou. FALHA FECHADO (`stale=True`), porque
        "nao consegui verificar" nao e' evidencia de saude.

    `source_max_date` vem de `audit.source_sync_run` (o teto da janela
    publicada), e `fact_max_ref_date` de `MAX(ref_date)` na fato (a competencia
    que de fato existe). NENHUM dos dois e' `source_max_updated_at` ou
    `raw_max_updated_at`: aqueles sao carimbos TECNICOS de quando a linha foi
    tocada, nao competencia comercial, e usa-los aqui trocaria "ate quando ha
    venda medida" por "quando alguem mexeu no registro".

    `unknown` (pre-piloto, sem execucao registrada) NAO reprova nesta dimensao
    — e so' nesta. O resultado correto e' `status="unknown"` com `stale=False`
    AQUI e `ok_critical=False` vindo da dimensao de EXECUCAO, que cobra a
    entrada canonica em `EXPECTED_SOURCES`. Uma rotina ja declarada critica nao
    pode deixar o health check verde antes da primeira execucao. `error` e'
    diferente de `unknown`: ausencia de execucao e' um fato conhecido; falha de
    leitura e' cegueira, e cegueira reprova.
    """
    source_name: str
    #: ok | execucao_atrasada | competencia_ausente | unknown | error
    status: str
    reason: str
    last_success_at: str | None
    source_max_date: str | None
    fact_max_ref_date: str | None
    last_closed_date: str | None
    job_lag_days: int | None
    source_lag_days: int | None
    stale: bool
    critical: bool = True


def fetch_discounts_coverage_status(conn, now: datetime | None = None
                                    ) -> DiscountsCoverageStatus:
    """Cobertura operacional dos descontos. Duas leituras baratas.

    Le a ultima execucao `success` da fonte CANONICA em `audit.source_sync_run`
    e `MAX(ref_date)` da fato. O teto de comparacao e' o ultimo dia fechado
    resolvido pelo MESMO calendario que o sync usa (`last_closed_date`), nunca
    um calculo proprio — dois calendarios divergiriam na fronteira da
    meia-noite BRT.
    """
    now = now or _now()
    nome = sync_descontos.CANONICAL_AUDIT_SOURCE
    fechado = sync_descontos.last_closed_date(now)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT finished_at, source_max_date
              FROM audit.source_sync_run
             WHERE source_name = %s AND status = 'success'
               AND finished_at IS NOT NULL
             ORDER BY finished_at DESC
             LIMIT 1
            """,
            (nome,),
        )
        linha = cur.fetchone()
        # `AS fact_max_ref_date` e' OBRIGATORIO: a conexao de producao usa
        # `RealDictCursor` (ver diagnose_bug8_neon._neon_readonly), e um
        # agregado sem alias viria na chave "max". Indexar por posicao aqui
        # levantaria `KeyError` em toda execucao real.
        cur.execute(
            f"SELECT MAX(ref_date) AS fact_max_ref_date "  # noqa: S608
            f"FROM {sync_descontos.TARGET_TABLE}"
        )
        fact_max = cur.fetchone()["fact_max_ref_date"]
    except psycopg2.Error:
        # FALHA FECHADO. Antes isto devolvia `unknown` com `stale=False`, e o
        # efeito pratico era perverso: o Neon indisponivel — ou a auditoria
        # inacessivel — deixava a dimensao de cobertura VERDE. "Nao consegui
        # verificar" nao e' evidencia de saude; e' cegueira, e cegueira sobre
        # uma rotina critica tem de reprovar.
        #
        # Mensagem FIXA e sanitizada: nada de SQL, DSN, host, credencial ou
        # texto de driver. Bug de codigo (que nao e' `psycopg2.Error`) continua
        # PROPAGANDO — esconder defeito nosso atras de "erro de fonte" e'
        # exatamente o que nao se pode fazer.
        cur.close()
        return DiscountsCoverageStatus(
            source_name=nome, status="error",
            reason=DISCOUNTS_COVERAGE_ERROR_NOTE,
            last_success_at=None, source_max_date=None, fact_max_ref_date=None,
            last_closed_date=fechado.isoformat(), job_lag_days=None,
            source_lag_days=None, stale=True, critical=True,
        )
    cur.close()

    # Acesso por NOME, nunca por posicao — `RealDictCursor` em producao.
    finished_at = linha["finished_at"] if linha else None
    source_max = linha["source_max_date"] if linha else None

    if source_max is None:
        return DiscountsCoverageStatus(
            source_name=nome, status="unknown",
            reason=("sem execucao bem-sucedida com janela registrada — "
                    "cobertura indeterminada (a EXECUCAO e' cobrada a parte)"),
            last_success_at=finished_at.isoformat() if finished_at else None,
            source_max_date=None,
            fact_max_ref_date=fact_max.isoformat() if fact_max else None,
            last_closed_date=fechado.isoformat(), job_lag_days=None,
            source_lag_days=None, stale=False,
        )

    job_lag = (fechado - source_max).days
    fonte_lag = (fechado - fact_max).days if fact_max else None

    if job_lag > DISCOUNTS_COVERAGE_MAX_LAG_DAYS:
        # Estado FACTUAL e verificavel: a janela publicada nao avancou.
        status, motivo, stale = (
            "execucao_atrasada",
            (f"a ultima execucao publicou ate {source_max.isoformat()}, "
             f"{job_lag} dia(s) atras do ultimo dia fechado "
             f"({fechado.isoformat()})"),
            True,
        )
    elif fonte_lag is None:
        status, motivo, stale = (
            "competencia_ausente",
            f"a fato nao tem nenhuma linha. {DISCOUNTS_COVERAGE_MISSING_NOTE}",
            True,
        )
    elif fonte_lag > DISCOUNTS_COVERAGE_MAX_LAG_DAYS:
        # NAO se diz "fonte parada": ausencia de vendas e lacuna de ingestao
        # sao indistinguiveis com as fontes atuais, e o nome do estado nao pode
        # escolher uma das duas causas.
        status, motivo, stale = (
            "competencia_ausente",
            (f"competencia mais recente na fato e' {fact_max.isoformat()}, "
             f"{fonte_lag} dia(s) atras de {fechado.isoformat()}. "
             f"{DISCOUNTS_COVERAGE_MISSING_NOTE}"),
            True,
        )
    else:
        status, motivo, stale = (
            "ok",
            (f"janela publicada ate {source_max.isoformat()} e competencia na "
             f"fato ate {fact_max.isoformat()}, contra {fechado.isoformat()}"),
            False,
        )

    return DiscountsCoverageStatus(
        source_name=nome, status=status, reason=motivo,
        last_success_at=finished_at.isoformat() if finished_at else None,
        source_max_date=source_max.isoformat(),
        fact_max_ref_date=fact_max.isoformat() if fact_max else None,
        last_closed_date=fechado.isoformat(),
        job_lag_days=job_lag, source_lag_days=fonte_lag, stale=stale,
    )


# ---------------------------------------------------------------------------
# Dimensao `avoe_snapshot` — Gate AVH-4C
# ---------------------------------------------------------------------------

def _sql_para_psycopg2(sql: str) -> str:
    """`:nome` (SQLAlchemy) -> `%(nome)s` (psycopg2). Nada mais e' tocado.

    O SQL e' o do SERVICO, importado, nao uma copia: e' isso que impede a
    consulta do health check de divergir da consulta que alimenta a tela.
    Aqui so' a sintaxe de parametro nomeado muda.

    FALHA FECHADO se o SQL de origem ganhar `%` (que o psycopg2 interpretaria
    como placeholder) ou `::` (cast que a regex de `:nome` estragaria). Melhor
    quebrar na hora do que traduzir errado em silencio.
    """
    if "%" in sql:
        raise ValueError("SQL do servico ganhou '%': traducao para psycopg2 insegura")
    if "::" in sql:
        raise ValueError("SQL do servico ganhou '::': traducao para psycopg2 insegura")
    return re.sub(r":([a-z_][a-z0-9_]*)", r"%(\1)s", sql)


@dataclass(frozen=True)
class AvoeSnapshotStatus:
    """Obsolescencia do snapshot MANUAL da Avoe — dimensao propria e NAO critica.

    POR QUE `critical=False` SEMPRE
    -------------------------------
    A Avoe e' fonte externa, de terceiro, com carga manual e sem cadencia
    acordada. Nenhum estado dela — nem `error` — pode reprovar `ok_critical`,
    que e' o que decide o exit code deste processo e, por consequencia, se o
    step `health_check` derruba o `full_daily`. Uma exportacao que ninguem fez
    nao e' quebra de pipeline nosso, e transformar isso em falha diaria seria o
    mesmo alarme-fadiga que os Gates B4 e C1 corrigiram para o Shopee manual.

    O estado APARECE, e aparece como ATENCAO no `ok` geral. So' nao derruba o
    dia.

    O QUE E' MEDIDO, E SEPARADO
    ---------------------------
    Tres coisas que podem divergir, e por isso nao sao colapsadas:

      - `last_success_at`: quando o IMPORTADOR rodou com sucesso pela ultima
        vez. Pode existir sem captura servivel;
      - `captured_at` + `capture_age_days`: a idade do DADO na origem. E' o que
        envelhece, e o importador rodar de novo sobre o mesmo arquivo nao o
        rejuvenesce;
      - `serving_available`: se a captura passa nas MESMAS validacoes que a API
        usa. Existir `MAX(captured_at)` nao basta.

    ESTADOS
    -------
      available_manual_snapshot  captura valida, ate `AVOE_AGING_DAYS` dias;
      aging_manual_snapshot      captura valida, mas passando do limiar operacional;
      stale_manual_snapshot      captura valida e velha (o mesmo limiar da tela);
      unavailable                nenhuma captura passa na validacao do serving;
      error                      a leitura falhou.
    """
    source_name: str
    #: available_manual_snapshot | aging_manual_snapshot | stale_manual_snapshot
    #: | unavailable | error
    status: str
    reason: str
    #: Ultima execucao `success` do importador, independente de captura servivel.
    last_success_at: str | None
    #: Captura que o serving usaria — metas e canais na MESMA `captured_at`.
    captured_at: str | None
    capture_age_days: int | None
    targets_count: int | None
    channel_rows_count: int | None
    sync_run_id: int | None
    #: Como o run foi associado a captura. Divida conhecida: ver AVH-4B-S §13.5.
    sync_run_link_method: str | None
    #: O veredito do SERVING, calculado com as funcoes dele.
    serving_available: bool
    #: Motivo tecnico da indisponibilidade, no vocabulario do serving.
    unavailable_reason: str | None
    aging_threshold_days: int
    stale_threshold_days: int
    stale: bool
    #: NUNCA True. Ver o docstring: a Avoe nao pode derrubar o full_daily.
    critical: bool = False


def _avoe_indisponivel(status: str, reason: str, *, last_success_at=None,
                       unavailable_reason=None) -> AvoeSnapshotStatus:
    """Estado sem captura servivel. Nenhuma contagem inventada — tudo `None`."""
    return AvoeSnapshotStatus(
        source_name=avoe_svc.AUDIT_SOURCE_NAME,
        status=status,
        reason=reason,
        last_success_at=last_success_at,
        captured_at=None,
        capture_age_days=None,
        targets_count=None,
        channel_rows_count=None,
        sync_run_id=None,
        sync_run_link_method=None,
        serving_available=False,
        unavailable_reason=unavailable_reason,
        aging_threshold_days=AVOE_AGING_DAYS,
        stale_threshold_days=AVOE_STALE_DAYS,
        stale=True,
    )


def fetch_avoe_snapshot_status(conn, now: datetime | None = None
                               ) -> AvoeSnapshotStatus:
    """Obsolescencia do snapshot da Avoe. Duas consultas, ambas read-only.

    A escolha da captura usa `avoe_svc._valida_captura` e
    `avoe_svc._associa_run` — as MESMAS funcoes puras que o endpoint usa. Nao
    ha regra reimplementada aqui: se a API considera uma captura invalida, esta
    dimensao considera tambem, por construcao.

    `reported_amount` nao e' lido em ponto algum: o valor informado pela Avoe
    nao e' criterio de saude, e usa-lo transformaria "a agencia digitou zero"
    em "a fonte esta doente".
    """
    now = now or _now()
    cur = conn.cursor()
    try:
        cur.execute(
            _sql_para_psycopg2(avoe_svc.SQL_CANDIDATAS),
            {"source": avoe_svc.SOURCE,
             "max_capturas": avoe_svc.MAX_CAPTURAS_AVALIADAS},
        )
        candidatas = [dict(r) for r in cur.fetchall()]
        cur.execute(
            _sql_para_psycopg2(avoe_svc.SQL_RUNS_AUDITORIA),
            {"source_name": avoe_svc.AUDIT_SOURCE_NAME,
             "max_runs": avoe_svc.MAX_RUNS_AUDITORIA},
        )
        runs = [dict(r) for r in cur.fetchall()]
    except psycopg2.Error:
        # FALHA FECHADO, mas NAO critico: cegueira sobre uma fonte manual e
        # nao canonica vira ATENCAO, nunca exit 1.
        return _avoe_indisponivel("error", AVOE_ERRO_LEITURA)
    finally:
        cur.close()

    sucessos = [r["finished_at"] for r in runs
                if r["status"] == avoe_svc.STATUS_AUDITORIA_CONCLUSIVO
                and r["finished_at"] is not None]
    ultimo_sucesso = max(sucessos).isoformat() if sucessos else None

    if not candidatas:
        return _avoe_indisponivel(
            "unavailable",
            "nenhuma captura publicada nas tabelas de snapshot; a proxima "
            "depende de exportacao manual e de uma execucao do importador",
            last_success_at=ultimo_sucesso,
            unavailable_reason="no_snapshot_published",
        )

    # Da mais nova para a mais antiga, exatamente como o serving: uma captura
    # mais nova e invalida NAO derruba a anterior que se sustenta.
    escolhida = run = None
    motivo = "no_snapshot_published"
    for cand in candidatas:
        falha = avoe_svc._valida_captura(cand)
        if falha is not None:
            motivo = falha
            continue
        associado = avoe_svc._associa_run(cand, runs)
        if associado is None:
            motivo = "audit_run_not_conclusive"
            continue
        escolhida, run = cand, associado
        break

    if escolhida is None:
        return _avoe_indisponivel(
            "unavailable",
            f"nenhuma captura passa na validacao usada pelo serving "
            f"({motivo}); existir MAX(captured_at) nao basta",
            last_success_at=ultimo_sucesso,
            unavailable_reason=motivo,
        )

    idade = (now - escolhida["captured_at"]).days
    metas = int(escolhida["targets_count"])
    canais = int(escolhida["channel_rows_count"])

    if idade >= AVOE_STALE_DAYS:
        status = "stale_manual_snapshot"
        veredito = (f"captura com {idade} dias, acima do limiar operacional de "
                    f"{AVOE_STALE_DAYS}")
    elif idade >= AVOE_AGING_DAYS:
        status = "aging_manual_snapshot"
        veredito = (f"captura com {idade} dias, acima do limiar operacional de "
                    f"{AVOE_AGING_DAYS}")
    else:
        status = "available_manual_snapshot"
        veredito = f"captura com {idade} dias, dentro do limiar operacional"

    return AvoeSnapshotStatus(
        source_name=avoe_svc.AUDIT_SOURCE_NAME,
        status=status,
        reason=(f"{veredito} (nao e' SLA da fonte); {metas} metas e {canais} "
                f"linhas de canal na mesma captura, run #{run['sync_run_id']} "
                f"'{run['status']}'; fonte externa e manual, a proxima captura "
                f"depende de acao humana"),
        last_success_at=ultimo_sucesso,
        captured_at=escolhida["captured_at"].isoformat(),
        capture_age_days=idade,
        targets_count=metas,
        channel_rows_count=canais,
        sync_run_id=int(run["sync_run_id"]),
        sync_run_link_method="audit_time_window",
        serving_available=True,
        unavailable_reason=None,
        aging_threshold_days=AVOE_AGING_DAYS,
        stale_threshold_days=AVOE_STALE_DAYS,
        stale=status != "available_manual_snapshot",
    )


def build_report(conn, now: datetime | None = None) -> dict:
    """`now` e' lido UMA UNICA vez aqui (ou recebido do chamador) e
    repassado para as duas dimensoes de frescor — evita que
    fetch_source_statuses/fetch_data_freshness leiam o relogio em momentos
    ligeiramente diferentes dentro do mesmo relatorio (ex.: um straddle de
    meia-noite UTC poderia fazer as duas dimensoes discordarem sobre "hoje").
    Tambem e' o ponto de injecao de relogio para testes deterministicos."""
    now = now or _now()
    sources = fetch_source_statuses(conn, now=now)
    data_freshness = fetch_data_freshness(conn, today=now.date())
    bug8 = run_bug8_check(conn)
    afiliados = fetch_affiliate_watermark_status(conn, now=now)
    descontos = fetch_discounts_coverage_status(conn, now=now)
    avoe = fetch_avoe_snapshot_status(conn, now=now)

    exec_stale = [s for s in sources if s.stale]
    data_stale = [d for d in data_freshness if d.stale]
    ok = (not exec_stale and not data_stale and bug8["ok"]
          and not afiliados.stale and not descontos.stale and not avoe.stale)

    # Gate B1: ok_critical ignora fontes/entradas critical=False (hoje, so'
    # Shopee) — e' isso que `main()` usa para o exit code. `ok` continua
    # existindo, completo, so' para visibilidade (JSON/log), nunca decide
    # o exit code sozinho.
    exec_stale_critical = [s for s in exec_stale if s.critical]
    data_stale_critical = [d for d in data_stale if d.critical]
    afiliados_critico_stale = afiliados.stale and afiliados.critical
    descontos_critico_stale = descontos.stale and descontos.critical
    # AVH-4C: a Avoe entra pelo MESMO padrao `stale and critical`, e como
    # `critical` e' False por construcao a parcela e' sempre False. Escrever a
    # conjuncao em vez de omitir a fonte e' deliberado: se algum dia alguem
    # marcar a Avoe como critica, o efeito aparece aqui em vez de ficar
    # silenciosamente fora da conta.
    avoe_critico_stale = avoe.stale and avoe.critical
    ok_critical = (not exec_stale_critical and not data_stale_critical
                   and bug8["ok"] and not afiliados_critico_stale
                   and not descontos_critico_stale
                   and not avoe_critico_stale)

    return {
        "ok": ok,
        "ok_critical": ok_critical,
        "sources": [asdict(s) for s in sources],
        "data_freshness": [asdict(d) for d in data_freshness],
        "bug8_invariants": bug8,
        "affiliate_watermark": asdict(afiliados),
        "discounts_coverage": asdict(descontos),
        "avoe_snapshot": asdict(avoe),
    }


def _print_human(report: dict) -> None:
    print("=== Frescor de EXECUCAO por fonte (audit.source_sync_run) ===")
    for s in report["sources"]:
        if not s["stale"]:
            flag = "OK"
        else:
            flag = "ATRASADA-CRITICO" if s["critical"] else "ATRASADA-CONHECIDO"
        print(f"[{flag}] {s['source_name']} (cadencia={s['cadence']}): {s['reason']}")

    print("\n=== Frescor de DADO (MAX direto nas tabelas do Neon) ===")
    for d in report["data_freshness"]:
        if not d["stale"]:
            flag = "OK"
        else:
            flag = "ATRASADO-CRITICO" if d["critical"] else "ATRASADO-CONHECIDO"
        print(f"[{flag}] {d['reason']}")

    d = report["discounts_coverage"]
    print("\n=== Cobertura operacional dos descontos TikTok ===")
    marca = {"ok": "OK", "unknown": "INDETERMINADO",
             "execucao_atrasada": "EXECUCAO-ATRASADA",
             "competencia_ausente": "COMPETENCIA-AUSENTE",
             "error": "LEITURA-FALHOU"}
    print(f"[{marca.get(d['status'], d['status'])}] {d['source_name']}: {d['reason']}")

    a = report["avoe_snapshot"]
    print("\n=== Obsolescencia do snapshot manual da Avoe (nao critico) ===")
    marca_avoe = {
        "available_manual_snapshot": "OK",
        "aging_manual_snapshot": "ENVELHECENDO-CONHECIDO",
        "stale_manual_snapshot": "OBSOLETO-CONHECIDO",
        "unavailable": "INDISPONIVEL-CONHECIDO",
        "error": "LEITURA-FALHOU-CONHECIDO",
    }
    print(f"[{marca_avoe.get(a['status'], a['status'])}] {a['source_name']}: "
          f"{a['reason']}")
    print(f"  limiares OPERACIONAIS (nao SLA da fonte): aviso em "
          f"{a['aging_threshold_days']} dias, obsoleto em "
          f"{a['stale_threshold_days']} dias")
    print("  fonte externa e manual: nunca reprova o status critico nem o "
          "full_daily")

    print("\n=== Invariantes do Bug 8 (Shopee) ===")
    if report["bug8_invariants"]["ok"]:
        print("  OK — nenhuma divergencia")
    else:
        for p in report["bug8_invariants"]["problems"]:
            print(f"  DIVERGENCIA: {p}")

    # "GERAL" inclui fontes conhecidas/manuais (Shopee) — so' visibilidade.
    # "CRITICO" ignora essas fontes e e' o que decide o exit code (Gate B1):
    # um Shopee manual defasado nunca aparece aqui como motivo de atencao.
    print(f"\nSTATUS GERAL (inclui conhecidos/manuais): {'OK' if report['ok'] else 'ATENCAO'}")
    print(f"STATUS CRITICO (decide o exit code): {'OK' if report['ok_critical'] else 'ATENCAO'}")


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

    # Gate B1: exit code decidido por ok_critical (ignora Shopee manual),
    # nao mais por `ok` (que incluiria o gap conhecido de Shopee e faria
    # este processo sair com exit 1 quase todo dia).
    return 0 if report["ok_critical"] else 1


if __name__ == "__main__":
    sys.exit(main())
