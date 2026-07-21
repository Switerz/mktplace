"""
Gate S5.3 — wrapper operacional único que recebe os `file_id`s de um lote
Raw/Silver já concluído, resolve a janela pelo contrato seguro do Gate S5.2
(`shopee_batch_window.resolve_shopee_batch_window`) e, só se a janela for
`resolved`, chama o refresh autoritativo (`loader.execute_shopee_window_refresh`)
diretamente contra o primary. Este módulo NÃO tem SQL próprio, NÃO abre
nenhuma conexão, e NÃO reimplementa nenhuma lógica de transação — é
inteiramente uma composição de funções já auditadas em gates anteriores.

Decisões de segurança obrigatórias deste gate (não negociáveis):
  - NUNCA chama `loader.diagnose_shopee_window` — esse diagnóstico roda
    contra a réplica de leitura e serviria apenas para decidir "vale a pena
    tentar o refresh?"; usar esse resultado para decidir `no_op` reintroduz
    exatamente o risco de falso negativo por replication lag que os Gates
    S5.1b/S5.2.1 já eliminaram do caminho de leitura da janela. A decisão
    `no_op` vs. `committed` é feita EXCLUSIVAMENTE por
    `execute_shopee_window_refresh`, que recalcula o key-diff SOB O LOCK,
    contra o primary, na mesma transação da escrita.
  - NUNCA usa o resultado de uma leitura na réplica (`DATAMART_DATABASE_URL`)
    para decidir se o refresh deve rodar. `DATAMART_DATABASE_URL` só entra
    aqui como `datamart_read_url` — usado unicamente pelos dois preflights
    para CONFIRMAR que a `write_url` aponta para o mesmo cluster físico do
    primary (nunca para ler dados de negócio).
  - A janela é sempre resolvida via `resolve_shopee_batch_window` (Gate
    S5.2.1) — a ÚNICA função pública daquele módulo, que já exige preflight
    aprovado e primary confirmado antes de qualquer consulta à Silver. Este
    módulo NUNCA importa nem chama
    `shopee_batch_window._resolve_shopee_batch_window_after_preflight`
    (privada).
  - Dois preflights no caminho de escrita, de propósito, não por engano:
    (1) dentro de `resolve_shopee_batch_window` — protege a RESOLUÇÃO
    (leitura da Silver); (2) chamado de novo por este módulo, imediatamente
    antes de `execute_shopee_window_refresh` — protege a OPERAÇÃO DE
    ESCRITA, o mais perto possível da transação real (o tempo entre os dois
    preflights inclui a consulta de resolução inteira; um privilégio pode
    ter sido revogado nesse intervalo). Em ambos, `report.ok is not True`
    bloqueia — "inconclusivo" (`None`) NUNCA equivale a aprovado, mesmo
    padrão já auditado em `window_write_conn.run_window_preflight`.
  - `execute_shopee_window_refresh` é chamado NO MÁXIMO uma vez por
    execução. Nenhum retry automático em nenhum caminho (resolução,
    preflight ou refresh) — um bloqueio/falha aqui exige nova decisão de
    quem chama, nunca uma segunda tentativa silenciosa.
  - NUNCA chama `execute_shopee_window_restore` nem qualquer `sync_region_*`
    (Neon). A automação externa continua sem `DATABASE_URL` do Neon — este
    módulo nunca lê nem referencia essa variável.
  - `audit_path` continua obrigatório e fornecido pelo chamador neste gate
    (validado com a MESMA função já auditada do refresh,
    `loader._validate_new_window_audit_path` — nunca reimplementada). A
    geração automática de `run_id`/receipt/`audit_path` fica para o Gate
    S5.4, ainda não implementado aqui.

Por que não existe uma função "privada" separada aqui (ao contrário do Gate
S5.2.1): não há necessidade — `refresh_shopee_window_if_needed` é o ÚNICO
ponto de entrada deste módulo e SEMPRE executa os dois preflights, sem
nenhum parâmetro capaz de pular qualquer um dos dois. Duplicar a estrutura
público/privado do S5.2 aqui seria complexidade sem propósito: não há um
caso de uso legítimo para uma variante "sem preflight" deste wrapper.

`committed` vs. `no_op`: ambos são outcomes de SUCESSO de
`execute_shopee_window_refresh` — a diferença é só se a fonte
(`silver.stg_shopee_order_item_snapshots`, transformada) diverge da Gold
atual na janela (`committed`: houve DELETE+INSERT, com backup atômico
publicado ANTES do DELETE) ou já está idêntica chave a chave e campo a
campo (`no_op`: nenhuma escrita, nenhum backup, nenhum `.sha256`). Este
wrapper nunca reinterpreta esses outcomes — só repassa `rows_deleted`/
`rows_inserted`/`backup_path`/`backup_sha256`/GMV exatamente como
`execute_shopee_window_refresh` os devolveu.

`staging_rows`/`gold_rows_before` no contrato de saída deste módulo ficam
SEMPRE `None`: `execute_shopee_window_refresh` (`ShopeeWindowRefreshResult`)
não expõe essas contagens — só `rows_deleted`/`rows_inserted` (o resultado
da operação, não o estado anterior). Calculá-las aqui exigiria SQL novo ou
reaproveitar `validate_shopee_window_write_path`/`diagnose_shopee_window`
(este último proibido acima) — nenhuma das duas opções está no escopo
deste gate. Os campos permanecem no contrato (`ShopeeWindowRefreshIfNeededResult`)
porque foram pedidos explicitamente, mas nunca recebem um valor fabricado.

Gate S5.3.1 — hardening final antes do commit (três correções, todas
dentro de `refresh_shopee_window_if_needed`, sem SQL novo):
  1. `resolve_shopee_batch_window(...)` agora roda dentro de um `try/except`
     próprio: qualquer exceção nativa vira `failed`/`unexpected_error`
     sanitizado, e nem o segundo preflight nem o refresh chegam a ser
     chamados. Antes desta correção, uma exceção ali escaparia da função
     pública inteira (nenhum outro `try/except` neste módulo a envolvia).
  2. `outcome == "resolved"` deixou de ser suficiente por si só: antes de
     extrair `date_from`/`date_to` para o segundo preflight/refresh, este
     módulo confirma explicitamente o contrato completo que um `resolved`
     de verdade promete (`_resolved_contract_problems`) — `reason_code`
     coerente, `date_from`/`date_to` são `date` de verdade e em ordem,
     `refresh_window_valid is True`, `requested_file_count ==
     found_file_count`, `missing_file_ids` vazio, `window_days` inteiro
     positivo. Qualquer invariante violada bloqueia como
     `failed`/`resolver_contract_invalid` — nunca tenta corrigir/inferir um
     valor, e nem o segundo preflight nem o refresh rodam.
  3. O resultado final agora agrega, nesta ordem e sem duplicar entradas
     idênticas, os `warnings` das três etapas que efetivamente rodaram:
     `resolve_result.warnings` sempre; `second_report.warnings` a partir do
     momento em que o segundo preflight retorna (mesmo bloqueado);
     `refresh_result.warnings` só se o refresh chegou a ser chamado. Um
     warning nunca vira sucesso nem falha — é só telemetria preservada.

Nenhuma destas correções toca `loader.py`/`window_write_conn.py`/
`shopee_batch_window.py`, nem introduz retry/SQL/lógica de transação nova.
`run_cli` também parou de importar `shopee_batch_window._parse_cli_file_ids`
(privada daquele módulo) — usa um parser local mínimo
(`_parse_cli_file_ids`, definido abaixo) que só converte string→int e
delega toda a validação de fato para a função pública
`shopee_batch_window.validate_batch_file_ids`.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

from pipelines.common.config import settings
from pipelines.ingestion.gold_regional import loader as gold_regional_loader
from pipelines.ingestion.gold_regional import shopee_batch_window
from pipelines.ingestion.gold_regional import window_write_conn
from pipelines.ingestion.gold_regional.window_write_conn import sanitize_error_message

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WINDOW_WRITE_SECRET_PATH = REPO_ROOT / ".env.gold-window-write.local"

# Único contrato público deste módulo.
__all__ = [
    "ShopeeWindowRefreshIfNeededResult",
    "refresh_shopee_window_if_needed",
    "run_cli",
    "main",
]

# Vocabulário FECHADO de reason_code — cada um mapeia para exatamente um
# exit code (ver _REASON_CODE_EXIT_CODE). Reaproveita, com o MESMO nome, os
# reason_codes de `shopee_batch_window` quando o significado é idêntico
# (nunca reinventa um sinônimo); só cria nomes novos para conceitos que
# pertencem exclusivamente a este wrapper (audit_path, refresh_blocked,
# refresh_failed).
REASON_COMMITTED = "committed"
REASON_NO_OP = "no_op"
REASON_INVALID_INPUT = "invalid_input"
REASON_AUDIT_PATH_INVALID = "audit_path_invalid"
REASON_DATAMART_URL_NOT_CONFIGURED = "datamart_url_not_configured"
REASON_SECRET_LOAD_ERROR = "secret_load_error"
REASON_PREFLIGHT_BLOCKED = "preflight_blocked"
# Reaproveitados literalmente de shopee_batch_window — mesma causa, mesmo nome.
REASON_MISSING_FILE_IDS = shopee_batch_window.REASON_MISSING_FILE_IDS
REASON_EMPTY_BATCH = shopee_batch_window.REASON_EMPTY_BATCH
REASON_NULL_ORDER_DATE = shopee_batch_window.REASON_NULL_ORDER_DATE
REASON_REFRESH_WINDOW_INVALID = shopee_batch_window.REASON_REFRESH_WINDOW_INVALID
# Novos — só existem no resultado de execute_shopee_window_refresh, nunca no
# de resolve_shopee_batch_window.
REASON_REFRESH_BLOCKED = "refresh_blocked"
REASON_REFRESH_FAILED = "refresh_failed"
# Gate S5.3.1 — outcome=="resolved" que não cumpre o contrato completo
# esperado (ver _resolved_contract_problems). Nunca confundido com
# refresh_window_invalid: esta falha é do CONTRATO do resultado de
# resolve_shopee_batch_window, não da janela de datas em si.
REASON_RESOLVER_CONTRACT_INVALID = "resolver_contract_invalid"
REASON_UNEXPECTED_ERROR = "unexpected_error"

_REASON_CODE_EXIT_CODE = {
    REASON_COMMITTED: 0,
    REASON_NO_OP: 0,
    REASON_INVALID_INPUT: 2,
    REASON_AUDIT_PATH_INVALID: 2,
    REASON_DATAMART_URL_NOT_CONFIGURED: 2,
    REASON_SECRET_LOAD_ERROR: 2,
    REASON_PREFLIGHT_BLOCKED: 2,
    REASON_MISSING_FILE_IDS: 3,
    REASON_EMPTY_BATCH: 3,
    REASON_NULL_ORDER_DATE: 3,
    REASON_REFRESH_WINDOW_INVALID: 3,
    REASON_REFRESH_BLOCKED: 3,
    REASON_RESOLVER_CONTRACT_INVALID: 4,
    REASON_REFRESH_FAILED: 4,
    REASON_UNEXPECTED_ERROR: 4,
}


@dataclass
class ShopeeWindowRefreshIfNeededResult:
    """Resultado de `refresh_shopee_window_if_needed`. Nunca linhas
    individuais, nunca order_id, nunca filename, nunca conteúdo de backup —
    só contagens/booleans/datas/GMV agregado, mesmo padrão de
    `ShopeeBatchWindowResult`/`ShopeeWindowRefreshResult`."""
    outcome: str  # "committed" | "no_op" | "blocked" | "failed"
    reason_code: str
    requested_file_count: int = 0
    found_file_count: int = 0
    missing_file_ids: list = field(default_factory=list)
    silver_row_count: int = 0
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    window_days: Optional[int] = None
    # Sempre None neste gate — ver docstring do módulo.
    staging_rows: Optional[int] = None
    gold_rows_before: Optional[int] = None
    rows_deleted: int = 0
    rows_inserted: int = 0
    gmv_before: Optional[Decimal] = None
    gmv_after: Optional[Decimal] = None
    backup_path: Optional[str] = None
    backup_sha256: Optional[str] = None
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _dedup_stable(items: Sequence[str]) -> list[str]:
    """Remove duplicatas EXATAS preservando a ordem da primeira ocorrência —
    nunca reordena, nunca reescreve o texto de um warning."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _merge_warnings(*warning_lists: Sequence[str]) -> list[str]:
    """Agrega warnings de várias etapas, NA ORDEM em que as etapas rodaram,
    sem duplicar entradas idênticas. Nunca transforma um warning em
    problema/outcome — só preserva telemetria."""
    merged: list[str] = []
    for lst in warning_lists:
        merged.extend(lst)
    return _dedup_stable(merged)


def _resolved_contract_problems(r) -> list[str]:
    """Gate S5.3.1 (Correção 2) — `outcome == "resolved"` não é, por si só,
    garantia suficiente para prosseguir: confirma explicitamente o contrato
    completo que um `resolved` de verdade promete, ANTES de extrair
    `date_from`/`date_to` para o segundo preflight/refresh. Nunca corrige
    nem infere um valor — só relata o que está inconsistente; o chamador
    decide bloquear."""
    problems: list[str] = []
    if r.reason_code != shopee_batch_window.REASON_RESOLVED:
        problems.append(f"outcome=resolved mas reason_code inesperado: {r.reason_code!r}")

    valid_dates = isinstance(r.date_from, date) and isinstance(r.date_to, date)
    if not valid_dates:
        problems.append("date_from/date_to ausentes ou não são instâncias de date")
    elif r.date_from > r.date_to:
        problems.append(f"date_from ({r.date_from}) posterior a date_to ({r.date_to})")

    if r.refresh_window_valid is not True:
        problems.append(f"refresh_window_valid não é True: {r.refresh_window_valid!r}")
    if r.requested_file_count != r.found_file_count:
        problems.append(
            f"requested_file_count ({r.requested_file_count}) difere de found_file_count ({r.found_file_count})"
        )
    if r.missing_file_ids:
        problems.append(f"missing_file_ids não está vazio: {list(r.missing_file_ids)}")
    if not isinstance(r.window_days, int) or isinstance(r.window_days, bool) or r.window_days <= 0:
        problems.append(f"window_days não é um inteiro positivo: {r.window_days!r}")
    return problems


def refresh_shopee_window_if_needed(
    write_url: str,
    datamart_read_url: str,
    file_ids: Sequence[int],
    audit_path: Path,
    *,
    repo_root: Path = REPO_ROOT,
) -> ShopeeWindowRefreshIfNeededResult:
    """Único contrato público deste módulo.

    Ordem (não pular nem reordenar nenhum passo):
      1. valida `file_ids` (`shopee_batch_window.validate_batch_file_ids`,
         reaproveitada — não duplicada) e `audit_path`
         (`loader._validate_new_window_audit_path`, reaproveitada — não
         duplicada) ANTES de qualquer I/O;
      2. `resolve_shopee_batch_window(write_url, datamart_read_url,
         file_ids)`, dentro de um `try/except` próprio (Gate S5.3.1) — já
         exige preflight aprovado e primary confirmado internamente
         (PRIMEIRO preflight, protege a resolução); qualquer exceção nativa
         vira `failed`/`unexpected_error` sanitizado, sem chegar ao segundo
         preflight/refresh;
      3. se o resultado não for `resolved`, retorna `blocked`/`failed`
         imediatamente, preservando `reason_code`/`missing_file_ids`/
         `problems`/`warnings` — o refresh NUNCA é chamado;
      4. Gate S5.3.1 (Correção 2): confirma o CONTRATO completo de um
         `resolved` de verdade (`_resolved_contract_problems`) antes de
         extrair `date_from`/`date_to` — qualquer invariante violada
         bloqueia como `failed`/`resolver_contract_invalid`, sem inferir
         nada e sem rodar o segundo preflight/refresh;
      5. roda `window_write_conn.run_window_preflight` de novo (SEGUNDO
         preflight, protege a operação de escrita, o mais perto possível da
         transação real) — `report.ok is not True` bloqueia e o refresh
         NUNCA é chamado;
      6. chama `loader.execute_shopee_window_refresh` EXATAMENTE uma vez;
      7. mapeia o outcome sem reinterpretar métricas: `committed`/`no_op`/
         `blocked`/`failed` do refresh viram o outcome final deste wrapper
         (só o `reason_code` é adaptado ao vocabulário deste módulo).
      8. Gate S5.3.1 (Correção 3): todo resultado agrega, na ordem em que as
         etapas efetivamente rodaram e sem duplicar entradas idênticas, os
         `warnings` de `resolve_result`/`second_report`/`refresh_result` —
         cada um só entra na agregação a partir da etapa em que existiu."""
    try:
        ids = shopee_batch_window.validate_batch_file_ids(file_ids)
    except shopee_batch_window.BatchWindowInputError as exc:
        return ShopeeWindowRefreshIfNeededResult(
            outcome="blocked", reason_code=REASON_INVALID_INPUT, problems=[str(exc)],
        )

    requested_file_count = len(ids)

    audit_problem = gold_regional_loader._validate_new_window_audit_path(audit_path, repo_root)
    if audit_problem:
        return ShopeeWindowRefreshIfNeededResult(
            outcome="blocked", reason_code=REASON_AUDIT_PATH_INVALID,
            requested_file_count=requested_file_count, problems=[audit_problem],
        )

    # Gate S5.3.1 (Correção 1): nenhuma exceção nativa de
    # resolve_shopee_batch_window pode escapar desta função pública.
    try:
        resolve_result = shopee_batch_window.resolve_shopee_batch_window(write_url, datamart_read_url, ids)
    except Exception as exc:  # noqa: BLE001
        return ShopeeWindowRefreshIfNeededResult(
            outcome="failed", reason_code=REASON_UNEXPECTED_ERROR,
            requested_file_count=requested_file_count,
            problems=[
                "falha inesperada e não tratada ao chamar resolve_shopee_batch_window: "
                f"{sanitize_error_message(exc)}"
            ],
        )

    if resolve_result.outcome != "resolved":
        outcome = "failed" if resolve_result.outcome == "failed" else "blocked"
        return ShopeeWindowRefreshIfNeededResult(
            outcome=outcome, reason_code=resolve_result.reason_code,
            requested_file_count=resolve_result.requested_file_count,
            found_file_count=resolve_result.found_file_count,
            missing_file_ids=list(resolve_result.missing_file_ids),
            silver_row_count=resolve_result.silver_row_count,
            date_from=resolve_result.date_from, date_to=resolve_result.date_to,
            window_days=resolve_result.window_days,
            problems=list(resolve_result.problems),
            warnings=_merge_warnings(resolve_result.warnings),
        )

    # Gate S5.3.1 (Correção 2): outcome=="resolved" sozinho não basta.
    contract_problems = _resolved_contract_problems(resolve_result)
    if contract_problems:
        return ShopeeWindowRefreshIfNeededResult(
            outcome="failed", reason_code=REASON_RESOLVER_CONTRACT_INVALID,
            requested_file_count=resolve_result.requested_file_count,
            found_file_count=resolve_result.found_file_count,
            missing_file_ids=list(resolve_result.missing_file_ids),
            silver_row_count=resolve_result.silver_row_count,
            date_from=resolve_result.date_from if isinstance(resolve_result.date_from, date) else None,
            date_to=resolve_result.date_to if isinstance(resolve_result.date_to, date) else None,
            window_days=resolve_result.window_days,
            problems=contract_problems,
            warnings=_merge_warnings(resolve_result.warnings),
        )

    date_from = resolve_result.date_from
    date_to = resolve_result.date_to

    # Segundo preflight — deliberadamente redundante com o de dentro de
    # resolve_shopee_batch_window (ver docstring do módulo). Mesma regra
    # "inconclusivo bloqueia": `report.ok is not True` bloqueia, nunca só
    # `if not report.ok` (que trataria erroneamente None como falso mas
    # ainda assim bloquearia aqui — a comparação explícita documenta a
    # intenção e evita qualquer regressão futura para truthiness solto).
    try:
        second_report = window_write_conn.run_window_preflight(write_url, datamart_read_url)
    except Exception as exc:  # noqa: BLE001
        return ShopeeWindowRefreshIfNeededResult(
            outcome="blocked", reason_code=REASON_PREFLIGHT_BLOCKED,
            requested_file_count=resolve_result.requested_file_count,
            found_file_count=resolve_result.found_file_count,
            silver_row_count=resolve_result.silver_row_count,
            date_from=date_from, date_to=date_to, window_days=resolve_result.window_days,
            problems=[
                "falha inesperada no segundo preflight (imediatamente antes do refresh): "
                f"{sanitize_error_message(exc)}"
            ],
            warnings=_merge_warnings(resolve_result.warnings),
        )

    if second_report.ok is not True:
        return ShopeeWindowRefreshIfNeededResult(
            outcome="blocked", reason_code=REASON_PREFLIGHT_BLOCKED,
            requested_file_count=resolve_result.requested_file_count,
            found_file_count=resolve_result.found_file_count,
            silver_row_count=resolve_result.silver_row_count,
            date_from=date_from, date_to=date_to, window_days=resolve_result.window_days,
            problems=(
                ["segundo preflight (imediatamente antes do refresh) bloqueado — refresh NÃO executado."]
                + list(second_report.blocking_reasons)
            ),
            warnings=_merge_warnings(resolve_result.warnings, second_report.warnings),
        )

    # Refresh autoritativo — chamado EXATAMENTE uma vez, sem retry. Recalcula
    # staging/key-diff sob lock e decide no_op/committed/blocked/failed por
    # conta própria (nunca reaproveita nenhuma decisão calculada acima).
    try:
        refresh_result = gold_regional_loader.execute_shopee_window_refresh(
            write_url, date_from, date_to, audit_path, repo_root=repo_root,
        )
    except Exception as exc:  # noqa: BLE001
        return ShopeeWindowRefreshIfNeededResult(
            outcome="failed", reason_code=REASON_UNEXPECTED_ERROR,
            requested_file_count=resolve_result.requested_file_count,
            found_file_count=resolve_result.found_file_count,
            silver_row_count=resolve_result.silver_row_count,
            date_from=date_from, date_to=date_to, window_days=resolve_result.window_days,
            problems=[
                "falha inesperada e não tratada ao chamar execute_shopee_window_refresh: "
                f"{sanitize_error_message(exc)}"
            ],
            warnings=_merge_warnings(resolve_result.warnings, second_report.warnings),
        )

    common_fields = dict(
        requested_file_count=resolve_result.requested_file_count,
        found_file_count=resolve_result.found_file_count,
        silver_row_count=resolve_result.silver_row_count,
        date_from=date_from, date_to=date_to, window_days=resolve_result.window_days,
        rows_deleted=refresh_result.rows_deleted,
        rows_inserted=refresh_result.rows_inserted,
        gmv_before=refresh_result.gold_gmv_before,
        gmv_after=refresh_result.gold_gmv_after,
        backup_path=refresh_result.backup_path,
        backup_sha256=refresh_result.backup_sha256,
        warnings=_merge_warnings(resolve_result.warnings, second_report.warnings, refresh_result.warnings),
    )

    if refresh_result.outcome == "committed":
        return ShopeeWindowRefreshIfNeededResult(outcome="committed", reason_code=REASON_COMMITTED, **common_fields)
    if refresh_result.outcome == "no_op":
        return ShopeeWindowRefreshIfNeededResult(outcome="no_op", reason_code=REASON_NO_OP, **common_fields)
    if refresh_result.outcome == "blocked":
        return ShopeeWindowRefreshIfNeededResult(
            outcome="blocked", reason_code=REASON_REFRESH_BLOCKED,
            problems=list(refresh_result.problems), **common_fields,
        )
    return ShopeeWindowRefreshIfNeededResult(
        outcome="failed", reason_code=REASON_REFRESH_FAILED,
        problems=list(refresh_result.problems), **common_fields,
    )


# ---------------------------------------------------------------------------
# CLI — python -m pipelines.ops.refresh_shopee_window_if_needed
#   --file-id <id1> --file-id <id2> --audit-path <caminho-absoluto.json> [--json]
# ---------------------------------------------------------------------------

def _decimal_to_str(value: Optional[Decimal]) -> Optional[str]:
    return str(value) if value is not None else None


def _result_to_dict(result: ShopeeWindowRefreshIfNeededResult) -> dict:
    return {
        "outcome": result.outcome,
        "reason_code": result.reason_code,
        "requested_file_count": result.requested_file_count,
        "found_file_count": result.found_file_count,
        "missing_file_ids": result.missing_file_ids,
        "silver_row_count": result.silver_row_count,
        "date_from": result.date_from.isoformat() if result.date_from else None,
        "date_to": result.date_to.isoformat() if result.date_to else None,
        "window_days": result.window_days,
        "staging_rows": result.staging_rows,
        "gold_rows_before": result.gold_rows_before,
        "rows_deleted": result.rows_deleted,
        "rows_inserted": result.rows_inserted,
        "gmv_before": _decimal_to_str(result.gmv_before),
        "gmv_after": _decimal_to_str(result.gmv_after),
        "backup_path": result.backup_path,
        "backup_sha256": result.backup_sha256,
        "problems": result.problems,
        "warnings": result.warnings,
    }


def _print_json(result: ShopeeWindowRefreshIfNeededResult) -> None:
    # Único documento JSON no stdout — nada mais é escrito em stdout nesta
    # rodada (avisos/preflight vão sempre para stderr, ver run_cli()).
    print(json.dumps(_result_to_dict(result), ensure_ascii=False, sort_keys=True))


def _print_human(result: ShopeeWindowRefreshIfNeededResult) -> None:
    print("=== Refresh Shopee por janela (condicional a resolução de file_ids) ===")
    print(f"  outcome: {result.outcome}")
    print(f"  reason_code: {result.reason_code}")
    print(f"  requested_file_count: {result.requested_file_count}")
    print(f"  found_file_count: {result.found_file_count}")
    print(f"  silver_row_count: {result.silver_row_count}")
    print(f"  date_from: {result.date_from}")
    print(f"  date_to: {result.date_to}")
    print(f"  window_days: {result.window_days}")
    print(f"  rows_deleted: {result.rows_deleted}")
    print(f"  rows_inserted: {result.rows_inserted}")
    print(f"  gmv_before: {result.gmv_before}")
    print(f"  gmv_after: {result.gmv_after}")
    if result.backup_path:
        print(f"  backup_path: {result.backup_path}")
        print(f"  backup_sha256: {result.backup_sha256}")
    if result.missing_file_ids:
        print(f"  missing_file_ids: {result.missing_file_ids}")
    for p in result.problems:
        print(f"  problema: {p}")
    for w in result.warnings:
        print(f"  aviso: {w}")


def _emit(result: ShopeeWindowRefreshIfNeededResult, as_json: bool) -> None:
    if as_json:
        _print_json(result)
    else:
        _print_human(result)


def _parse_cli_file_ids(raw_values: list[str]) -> list[int]:
    """Parser mínimo LOCAL — Gate S5.3.1 (Correção 4): nunca reaproveita
    `shopee_batch_window._parse_cli_file_ids` (privada, prefixo `_`, nunca
    exportada por aquele módulo). Só converte string→int; toda a validação
    de fato (faixa, duplicados, limite) é delegada à função pública
    `shopee_batch_window.validate_batch_file_ids` — nada é reimplementado."""
    parsed: list[int] = []
    for raw in raw_values:
        try:
            parsed.append(int(raw))
        except (TypeError, ValueError):
            raise shopee_batch_window.BatchWindowInputError(f"file_id inválido (esperado inteiro): {raw!r}") from None
    return shopee_batch_window.validate_batch_file_ids(parsed)


def run_cli(
    raw_file_ids: list[str],
    audit_path_raw: str,
    as_json: bool,
    secret_path: Path = DEFAULT_WINDOW_WRITE_SECRET_PATH,
    repo_root: Path = REPO_ROOT,
) -> int:
    """CLI fina. Ordem: valida file_ids -> valida audit_path (ambas ANTES
    de ler o secret ou conectar) -> `DATAMART_DATABASE_URL` configurado ->
    secret dedicado `.env.gold-window-write.local` -> guardrails ->
    `refresh_shopee_window_if_needed` (a ÚNICA API pública deste módulo —
    ela mesma roda os dois preflights obrigatórios; esta CLI nunca chama
    `run_window_preflight`/`execute_shopee_window_refresh` por conta
    própria). Exit code determinado inteiramente por
    `_REASON_CODE_EXIT_CODE[reason_code]` — nunca lógica duplicada."""
    try:
        file_ids = _parse_cli_file_ids(raw_file_ids)
    except shopee_batch_window.BatchWindowInputError as exc:
        result = ShopeeWindowRefreshIfNeededResult(outcome="blocked", reason_code=REASON_INVALID_INPUT, problems=[str(exc)])
        _emit(result, as_json)
        return _REASON_CODE_EXIT_CODE[result.reason_code]

    audit_path = Path(audit_path_raw)
    audit_problem = gold_regional_loader._validate_new_window_audit_path(audit_path, repo_root)
    if audit_problem:
        result = ShopeeWindowRefreshIfNeededResult(
            outcome="blocked", reason_code=REASON_AUDIT_PATH_INVALID,
            requested_file_count=len(file_ids), problems=[audit_problem],
        )
        _emit(result, as_json)
        return _REASON_CODE_EXIT_CODE[result.reason_code]

    if not settings.datamart_url:
        result = ShopeeWindowRefreshIfNeededResult(
            outcome="blocked", reason_code=REASON_DATAMART_URL_NOT_CONFIGURED,
            requested_file_count=len(file_ids),
            problems=["DATAMART_DATABASE_URL não configurado — refresh condicional abortado."],
        )
        _emit(result, as_json)
        return _REASON_CODE_EXIT_CODE[result.reason_code]

    try:
        secret = window_write_conn.load_window_write_secret(secret_path, repo_root)
    except window_write_conn.WindowSecretLoadError as exc:
        result = ShopeeWindowRefreshIfNeededResult(
            outcome="blocked", reason_code=REASON_SECRET_LOAD_ERROR,
            requested_file_count=len(file_ids), problems=[str(exc)],
        )
        _emit(result, as_json)
        return _REASON_CODE_EXIT_CODE[result.reason_code]

    try:
        write_url = window_write_conn.validate_window_write_guardrails(secret, settings.datamart_url)
    except window_write_conn.WindowSecretLoadError as exc:
        result = ShopeeWindowRefreshIfNeededResult(
            outcome="blocked", reason_code=REASON_SECRET_LOAD_ERROR,
            requested_file_count=len(file_ids), problems=[str(exc)],
        )
        _emit(result, as_json)
        return _REASON_CODE_EXIT_CODE[result.reason_code]

    try:
        result = refresh_shopee_window_if_needed(write_url, settings.datamart_url, file_ids, audit_path, repo_root=repo_root)
    except Exception as exc:  # noqa: BLE001
        result = ShopeeWindowRefreshIfNeededResult(
            outcome="failed", reason_code=REASON_UNEXPECTED_ERROR,
            requested_file_count=len(file_ids),
            problems=[f"falha inesperada e não tratada: {sanitize_error_message(exc)}"],
        )

    for w in result.warnings:
        print(f"AVISO: {w}", file=sys.stderr)
    if result.reason_code == REASON_PREFLIGHT_BLOCKED:
        for p in result.problems:
            print(f"PREFLIGHT: {p}", file=sys.stderr)

    _emit(result, as_json)
    return _REASON_CODE_EXIT_CODE.get(result.reason_code, 4)


def main(argv: Optional[list[str]] = None) -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=str(REPO_ROOT / ".env"))

    parser = argparse.ArgumentParser(
        description=(
            "Gate S5.3 — resolve a janela Gold Shopee a partir de file_ids da Silver e, "
            "se resolved, executa o refresh autoritativo. Sem retry automático."
        )
    )
    parser.add_argument(
        "--file-id", dest="file_ids", action="append", default=[], metavar="<bigint>",
        help="file_id da Silver (repetível). Pelo menos um obrigatório.",
    )
    parser.add_argument(
        "--audit-path", dest="audit_path", required=True, metavar="<caminho-absoluto.json>",
        help="Destino do backup atômico do refresh — absoluto, .json, fora do repositório, ainda não existente.",
    )
    parser.add_argument("--json", action="store_true", help="Saída como um único documento JSON em stdout.")
    args = parser.parse_args(argv)

    try:
        return run_cli(args.file_ids, args.audit_path, args.json)
    except Exception as exc:  # noqa: BLE001
        # Barreira final: nenhuma exceção não prevista pode escapar como
        # traceback com mensagem nativa do driver.
        result = ShopeeWindowRefreshIfNeededResult(
            outcome="failed", reason_code=REASON_UNEXPECTED_ERROR,
            problems=[f"falha inesperada e não tratada: {sanitize_error_message(exc)}"],
        )
        _emit(result, args.json)
        return _REASON_CODE_EXIT_CODE[REASON_UNEXPECTED_ERROR]


if __name__ == "__main__":
    sys.exit(main())
