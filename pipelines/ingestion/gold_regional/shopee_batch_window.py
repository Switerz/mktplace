"""
Gate S5.2 — resolução READ-ONLY e machine-readable da janela Gold Shopee
(`date_from`/`date_to`) a partir dos `file_id`s de um lote Raw/Silver já
concluído, para entregar futuramente ao refresh autoritativo
(`--refresh-shopee-window`, `pipelines/ingestion/gold_regional/loader.py`).

Este módulo NUNCA escreve nada, NUNCA adquire lock, NUNCA cria staging e
NUNCA chama qualquer função de refresh/restore/validação/sync. A única
tabela consultada é `silver.stg_shopee_order_item_snapshots`, com a MESMA
credencial dedicada do refresh (`gold_shopee_window_writer`,
`.env.gold-window-write.local`) — nunca `DATAMART_DATABASE_URL` (read
replica) e nunca a credencial administrativa.

Por que o primary, em sessão read-only, e nunca a réplica (achado do Gate
S5.1b): se a automação acabou de escrever a Silver (via primary) e este
módulo lesse a réplica logo em seguida, a réplica poderia ainda não ter
alcançado esse ponto — o lote pareceria incompleto/vazio quando já está
pronto no primary. Consultar o primary em `readonly=True` elimina esse
risco sem nenhuma chance de escrita: a sessão nunca commita nada (sempre
`ROLLBACK`, inclusive no caminho de sucesso), e o preflight dedicado
(`window_write_conn.run_window_preflight`) já confirma antes de qualquer
query que a credencial aponta para o primary (`pg_is_in_recovery()=false`)
e não tem privilégios além dos documentados.

Como evita uma janela parcial: a lista de `file_id`s solicitada é resolvida
inteira ou não é resolvida — se QUALQUER `file_id` pedido não aparecer
ainda em `silver.stg_shopee_order_item_snapshots`, o resultado é `blocked`
(`missing_file_ids` preenchido) e nenhum `MIN`/`MAX(order_created_at)` é
sequer calculado sobre o subconjunto presente. A mesma regra vale para
lote com zero linhas e para qualquer `order_created_at` nulo encontrado
(defensivo — a coluna é `NOT NULL` no schema atual, mas este módulo nunca
assume a constraint sem conferir).

Semântica de data idêntica à do transform Shopee da Gold (ver
`pipelines/ingestion/gold_regional/loader.py`, `SQL_INSERT_SHOPEE_STAGING`/
`_shopee_incremental_select`/`SQL_SHOPEE_WINDOW_RECALC_ROWS`, todas usando
`order_created_at::date` sem conversão de timezone — `order_created_at` é
`timestamp` sem timezone, BRT nativo, confirmado no Gate 5): este módulo
usa exatamente o mesmo cast `::date`, nunca uma expressão nova. O limite
de tamanho de janela reaproveita `loader._validate_shopee_window`
(`MAX_SHOPEE_WINDOW_DAYS=180`) — nenhum threshold novo é criado aqui.

Este módulo NUNCA lê `raw.shopee_ingestion_file` nem tenta confirmar
`batch_id` — a garantia de que a Silver está reconciliada e de que a lista
de `file_id`s está completa é responsabilidade do runner Silver (externo a
este repositório nesta fase, ver Gate S5.1/S5.1b), que só deve chamar este
componente DEPOIS da própria transação/reconciliação Raw→Silver ter
terminado com sucesso. Se o runner tiver feito uma carga PARCIAL dentro de
um `file_id` que já existe na Silver (algumas linhas de um arquivo
gravadas, outras não), este módulo não tem como detectar isso sem acesso à
Raw — essa garantia pertence ao contrato do runner Silver, não a este
módulo.

Gate S5.2.1 — API pública sempre exige preflight (hardening pré-commit):
`resolve_shopee_batch_window(write_url, datamart_read_url, file_ids)` é o
ÚNICO contrato público deste módulo e SEMPRE roda
`window_write_conn.run_window_preflight(write_url, datamart_read_url)`
antes de qualquer consulta — sem preflight aprovado (`report.ok is True`),
a Silver nunca é consultada. Não existe parâmetro `skip_preflight`/
`preflight_confirmed` nem qualquer outra forma de desarmar essa checagem.
A função `_resolve_shopee_batch_window_after_preflight` é um detalhe de
implementação PRIVADO (prefixo `_`, nunca reexportado, nunca chamado fora
deste módulo) — contém só a transação read-only e as consultas; nunca deve
ser importada/chamada diretamente por um wrapper futuro. Isso fecha o
risco (achado da revisão pré-commit) de um chamador direto passar uma
réplica sem preflight e reintroduzir o falso `missing_file_ids` por
replication lag que a Correção 1 do Gate S5.1b já tinha identificado para
o caminho de escrita — agora vale igualmente para este caminho de leitura.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

import psycopg2

from pipelines.common.config import settings
from pipelines.ingestion.gold_regional import loader as gold_regional_loader
from pipelines.ingestion.gold_regional import window_write_conn
from pipelines.ingestion.gold_regional.window_write_conn import sanitize_error_message

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WINDOW_WRITE_SECRET_PATH = REPO_ROOT / ".env.gold-window-write.local"

# Contrato público explícito (Gate S5.2.1): `_resolve_shopee_batch_window_
# after_preflight` é deliberadamente OMITIDA — é um detalhe de implementação
# privado, nunca uma API a ser importada diretamente.
__all__ = [
    "ShopeeBatchWindowResult",
    "BatchWindowInputError",
    "validate_batch_file_ids",
    "resolve_shopee_batch_window",
    "run_cli",
    "main",
]

# Única tabela consultada por este módulo — nunca raw.*, nunca gold.*, nunca
# marts.* (Neon). Reaproveita o nome já usado por window_write_conn.py.
_SILVER_SOURCE_TABLE = window_write_conn._SILVER_SOURCE_TABLE_QUALIFIED

# Constante documentada para nunca aceitar uma lista de file_ids
# acidentalmente enorme (ex.: colada errada). Lotes reais do scraping
# Shopee têm poucas dezenas de arquivos por execução (ver
# docs/shopee_datamart_operacao_completa.md — piloto real teve 3 arquivos
# por lote); 200 dá folga generosa sem abrir mão do limite.
MAX_BATCH_FILE_IDS = 200

# file_id é `bigint` em silver.stg_shopee_order_item_snapshots (confirmado
# em db/sql/staging/shopee_staging_ddl.sql) — faixa exata do tipo Postgres.
_POSTGRES_BIGINT_MAX = 9223372036854775807

# Vocabulário FECHADO de reason_code — cada um mapeia para exatamente um
# exit code da CLI (ver _REASON_CODE_EXIT_CODE). Nenhum outro valor é
# produzido por este módulo.
REASON_RESOLVED = "resolved"
REASON_INVALID_INPUT = "invalid_input"
REASON_DATAMART_URL_NOT_CONFIGURED = "datamart_url_not_configured"
REASON_SECRET_LOAD_ERROR = "secret_load_error"
REASON_PREFLIGHT_BLOCKED = "preflight_blocked"
REASON_MISSING_FILE_IDS = "missing_file_ids"
REASON_EMPTY_BATCH = "empty_batch"
REASON_NULL_ORDER_DATE = "null_order_date"
# Gate S5.2.1: genérico e verdadeiro de propósito — nunca mapear TODO
# InvalidWindowError para "excede o limite". `loader._validate_shopee_window`
# rejeita três causas distintas (date_from > date_to; date_to no futuro;
# janela > MAX_SHOPEE_WINDOW_DAYS dias) e este módulo nunca tenta adivinhar
# qual delas ocorreu por parsing de mensagem — a causa exata e sanitizada
# fica em `problems`, nunca perdida, mas o reason_code é sempre este mesmo
# valor para as três. Uma data futura NUNCA é classificada como
# "window_exceeds_limit" (nome removido de propósito).
REASON_REFRESH_WINDOW_INVALID = "refresh_window_invalid"
REASON_UNEXPECTED_ERROR = "unexpected_error"

_REASON_CODE_EXIT_CODE = {
    REASON_RESOLVED: 0,
    REASON_INVALID_INPUT: 2,
    REASON_DATAMART_URL_NOT_CONFIGURED: 2,
    REASON_SECRET_LOAD_ERROR: 2,
    REASON_PREFLIGHT_BLOCKED: 2,
    REASON_MISSING_FILE_IDS: 3,
    REASON_EMPTY_BATCH: 3,
    REASON_NULL_ORDER_DATE: 3,
    REASON_REFRESH_WINDOW_INVALID: 3,
    REASON_UNEXPECTED_ERROR: 4,
}

# ---------------------------------------------------------------------------
# SQL — só SELECT, só a tabela Silver, sempre com bind parameter ANY(%s)
# (nunca interpola file_ids na string). Mesma semântica de data do transform
# Shopee da Gold (order_created_at::date, sem conversão de timezone).
# ---------------------------------------------------------------------------

SQL_FOUND_FILE_IDS = f"""
    SELECT DISTINCT file_id FROM {_SILVER_SOURCE_TABLE} WHERE file_id = ANY(%(file_ids)s)
"""

SQL_WINDOW_AGGREGATES = f"""
    SELECT
        COUNT(*) AS row_count,
        COUNT(*) FILTER (WHERE order_created_at IS NULL) AS null_date_count,
        MIN(order_created_at)::date AS date_from,
        MAX(order_created_at)::date AS date_to
    FROM {_SILVER_SOURCE_TABLE}
    WHERE file_id = ANY(%(file_ids)s)
"""


class BatchWindowInputError(ValueError):
    """Entrada de file_ids inválida — detectada ANTES de qualquer secret/
    conexão. Mensagem nunca contém nada além dos próprios file_ids (não são
    considerados sensíveis neste contrato — só identificadores técnicos de
    arquivo, nunca order_id/CPF/filename)."""


@dataclass
class ShopeeBatchWindowResult:
    """Resultado de `resolve_shopee_batch_window` — só contagens/booleans/
    datas/`file_id`s agregados. Nunca linhas individuais, nunca order_id,
    nunca filename, nunca conteúdo de secret."""
    outcome: str  # "resolved" | "blocked" | "failed"
    reason_code: str
    requested_file_count: int = 0
    found_file_count: int = 0
    silver_row_count: int = 0
    null_order_date_count: int = 0
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    window_days: Optional[int] = None
    refresh_window_valid: bool = False
    missing_file_ids: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def validate_batch_file_ids(file_ids: Sequence[int]) -> list[int]:
    """Validação pura (sem I/O): pelo menos um id, tipo inteiro real (nunca
    bool, nunca float), faixa bigint do Postgres, sem duplicados, quantidade
    <= MAX_BATCH_FILE_IDS. Usada tanto pela CLI (após parse de string para
    int) quanto por `resolve_shopee_batch_window` (defesa em profundidade
    para um futuro chamador direto que não passe por essa checagem)."""
    ids = list(file_ids)
    if not ids:
        raise BatchWindowInputError("nenhum file_id informado")
    for v in ids:
        if isinstance(v, bool) or not isinstance(v, int):
            raise BatchWindowInputError(f"file_id inválido (esperado inteiro): {v!r}")
        if v <= 0 or v > _POSTGRES_BIGINT_MAX:
            raise BatchWindowInputError(f"file_id fora da faixa válida (bigint positivo): {v!r}")
    if len(ids) > MAX_BATCH_FILE_IDS:
        raise BatchWindowInputError(
            f"{len(ids)} file_id(s) informados, acima do limite de {MAX_BATCH_FILE_IDS}"
        )
    seen: set[int] = set()
    dupes: set[int] = set()
    for v in ids:
        (dupes if v in seen else seen).add(v)
    if dupes:
        raise BatchWindowInputError(f"file_id(s) duplicado(s): {sorted(dupes)}")
    return sorted(seen)


def _resolve_shopee_batch_window_after_preflight(write_url: str, ids: list[int]) -> ShopeeBatchWindowResult:
    """PRIVADO — detalhe de implementação, nunca reexportado nem chamado
    fora deste módulo. Contém SOMENTE a transação read-only e as consultas.

    Pré-condição que o CHAMADOR (`resolve_shopee_batch_window`, a única
    função pública) já garantiu antes de chegar aqui: `ids` já passou por
    `validate_batch_file_ids` E o preflight dedicado
    (`window_write_conn.run_window_preflight`) já aprovou explicitamente
    `write_url` como o primary com os privilégios esperados. Esta função
    nunca reconfirma nenhuma das duas coisas — não é o lugar certo para
    isso, e não deve ser chamada diretamente sem essa garantia já dada.

    Ordem: connect (protegido) -> `autocommit=False` + `readonly=True` +
    `isolation_level='REPEATABLE READ'` -> confere quais `file_id`s pedidos
    existem na Silver -> se QUALQUER um estiver ausente, bloqueia sem
    calcular nada (nunca janela parcial) -> agrega contagem/nulos/MIN/MAX
    sobre o conjunto completo -> bloqueia se zero linhas ou algum
    `order_created_at` nulo -> reaproveita
    `loader._validate_shopee_window`/`MAX_SHOPEE_WINDOW_DAYS` (nenhum
    threshold novo) para confirmar se a janela cabe no refresh -> SEMPRE
    `ROLLBACK` (nunca há nada para commitar) -> `close` sempre.

    Se o `ROLLBACK` do caminho de sucesso falhar, o resultado NUNCA é
    `resolved` silenciosamente — vira `failed` (não é possível confirmar
    que a sessão encerrou de forma limpa)."""
    requested_file_count = len(ids)
    result: Optional[ShopeeBatchWindowResult] = None

    try:
        conn = psycopg2.connect(write_url, connect_timeout=15)
    except Exception as exc:  # noqa: BLE001
        return ShopeeBatchWindowResult(
            outcome="failed", reason_code=REASON_UNEXPECTED_ERROR,
            requested_file_count=requested_file_count,
            problems=[sanitize_error_message(exc)],
        )

    try:
        conn.set_session(readonly=True, isolation_level="REPEATABLE READ", autocommit=False)
        with conn.cursor() as cur:
            cur.execute(SQL_FOUND_FILE_IDS, {"file_ids": ids})
            found = {row[0] for row in cur.fetchall()}
            missing = sorted(set(ids) - found)

            if missing:
                result = ShopeeBatchWindowResult(
                    outcome="blocked", reason_code=REASON_MISSING_FILE_IDS,
                    requested_file_count=requested_file_count, found_file_count=len(found),
                    missing_file_ids=missing,
                    problems=[f"{len(missing)} de {requested_file_count} file_id(s) ainda não presentes na Silver"],
                )
            else:
                cur.execute(SQL_WINDOW_AGGREGATES, {"file_ids": ids})
                row_count, null_date_count, agg_date_from, agg_date_to = cur.fetchone()
                row_count = int(row_count)
                null_date_count = int(null_date_count)

                if row_count == 0:
                    result = ShopeeBatchWindowResult(
                        outcome="blocked", reason_code=REASON_EMPTY_BATCH,
                        requested_file_count=requested_file_count, found_file_count=len(found),
                        problems=["lote resolvido sem nenhuma linha na Silver"],
                    )
                elif null_date_count > 0:
                    result = ShopeeBatchWindowResult(
                        outcome="blocked", reason_code=REASON_NULL_ORDER_DATE,
                        requested_file_count=requested_file_count, found_file_count=len(found),
                        silver_row_count=row_count, null_order_date_count=null_date_count,
                        problems=[f"{null_date_count} linha(s) com order_created_at nulo — investigar antes de calcular janela"],
                    )
                else:
                    window_days = (agg_date_to - agg_date_from).days + 1
                    try:
                        gold_regional_loader._validate_shopee_window(agg_date_from, agg_date_to)
                        window_ok = True
                        window_problem = None
                    except gold_regional_loader.InvalidWindowError as exc:
                        window_ok = False
                        window_problem = str(exc)

                    if not window_ok:
                        result = ShopeeBatchWindowResult(
                            outcome="blocked", reason_code=REASON_REFRESH_WINDOW_INVALID,
                            requested_file_count=requested_file_count, found_file_count=len(found),
                            silver_row_count=row_count, null_order_date_count=null_date_count,
                            date_from=agg_date_from, date_to=agg_date_to, window_days=window_days,
                            refresh_window_valid=False,
                            problems=[window_problem],
                        )
                    else:
                        result = ShopeeBatchWindowResult(
                            outcome="resolved", reason_code=REASON_RESOLVED,
                            requested_file_count=requested_file_count, found_file_count=len(found),
                            silver_row_count=row_count, null_order_date_count=null_date_count,
                            date_from=agg_date_from, date_to=agg_date_to, window_days=window_days,
                            refresh_window_valid=True,
                        )

        # Rollback do caminho de SUCESSO — este módulo nunca commita nada.
        # Se o próprio rollback falhar, não é seguro afirmar que a sessão
        # encerrou de forma limpa: o resultado nunca vira "resolved"/
        # "blocked" silenciosamente — vira "failed".
        try:
            conn.rollback()
        except Exception as exc:  # noqa: BLE001
            result = ShopeeBatchWindowResult(
                outcome="failed", reason_code=REASON_UNEXPECTED_ERROR,
                requested_file_count=requested_file_count,
                problems=[
                    f"rollback de encerramento falhou ({sanitize_error_message(exc)}) -- não é possível "
                    "confirmar que a sessão de leitura encerrou de forma limpa"
                ],
            )
        return result
    except Exception as exc:  # noqa: BLE001
        result = ShopeeBatchWindowResult(
            outcome="failed", reason_code=REASON_UNEXPECTED_ERROR,
            requested_file_count=requested_file_count,
            problems=[sanitize_error_message(exc)],
        )
        try:
            conn.rollback()
        except Exception as rollback_exc:  # noqa: BLE001
            result.warnings.append(
                f"falha ao executar rollback ({sanitize_error_message(rollback_exc)}) — resultado principal preservado"
            )
        return result
    finally:
        try:
            conn.close()
        except Exception as exc:  # noqa: BLE001
            if result is not None:
                result.warnings.append(
                    f"falha ao fechar a conexão ({sanitize_error_message(exc)}) — resultado principal preservado, sem retry sugerido"
                )


def resolve_shopee_batch_window(
    write_url: str,
    datamart_read_url: str,
    file_ids: Sequence[int],
) -> ShopeeBatchWindowResult:
    """Gate S5.2.1 — ÚNICA API PÚBLICA deste módulo. Resolução autoritativa
    e read-only da janela Shopee a partir de um lote de `file_id`s, com o
    preflight dedicado SEMPRE obrigatório antes de qualquer consulta.

    Ordem: valida a lista (pura, sem I/O; bloqueia ANTES de qualquer
    conexão) -> `window_write_conn.run_window_preflight(write_url,
    datamart_read_url)` -> se o preflight levantar uma exceção inesperada
    OU não aprovar explicitamente (`report.ok` não for `True`), bloqueia
    com `reason_code=preflight_blocked` e NUNCA chama a função privada
    (nenhuma consulta é executada) -> só então
    `_resolve_shopee_batch_window_after_preflight`.

    Não existe parâmetro `skip_preflight`/`preflight_confirmed` nem
    qualquer outro jeito de pular esta checagem — é o único caminho de
    consulta deste módulo, e ele sempre passa por aqui."""
    try:
        ids = validate_batch_file_ids(file_ids)
    except BatchWindowInputError as exc:
        return ShopeeBatchWindowResult(outcome="blocked", reason_code=REASON_INVALID_INPUT, problems=[str(exc)])

    requested_file_count = len(ids)

    try:
        report = window_write_conn.run_window_preflight(write_url, datamart_read_url)
    except Exception as exc:  # noqa: BLE001
        return ShopeeBatchWindowResult(
            outcome="blocked", reason_code=REASON_PREFLIGHT_BLOCKED,
            requested_file_count=requested_file_count,
            problems=[f"falha inesperada no preflight: {sanitize_error_message(exc)}"],
        )

    if not report.ok:
        return ShopeeBatchWindowResult(
            outcome="blocked", reason_code=REASON_PREFLIGHT_BLOCKED,
            requested_file_count=requested_file_count,
            problems=["preflight bloqueado — consulta à Silver não executada."] + list(report.blocking_reasons),
        )

    return _resolve_shopee_batch_window_after_preflight(write_url, ids)


# ---------------------------------------------------------------------------
# CLI — python -m pipelines.ingestion.gold_regional.shopee_batch_window
#   --file-id <id1> --file-id <id2> [--json]
# ---------------------------------------------------------------------------

def _result_to_dict(result: ShopeeBatchWindowResult) -> dict:
    return {
        "outcome": result.outcome,
        "reason_code": result.reason_code,
        "requested_file_count": result.requested_file_count,
        "found_file_count": result.found_file_count,
        "silver_row_count": result.silver_row_count,
        "null_order_date_count": result.null_order_date_count,
        "date_from": result.date_from.isoformat() if result.date_from else None,
        "date_to": result.date_to.isoformat() if result.date_to else None,
        "window_days": result.window_days,
        "refresh_window_valid": result.refresh_window_valid,
        "missing_file_ids": result.missing_file_ids,
        "problems": result.problems,
        "warnings": result.warnings,
    }


def _print_json(result: ShopeeBatchWindowResult) -> None:
    # Único documento JSON no stdout — nada mais é escrito em stdout nesta
    # rodada (avisos/preflight vão sempre para stderr, ver main()).
    print(json.dumps(_result_to_dict(result), ensure_ascii=False, sort_keys=True))


def _print_human(result: ShopeeBatchWindowResult) -> None:
    print("=== Resolução da janela Gold Shopee por file_ids (somente leitura) ===")
    print(f"  outcome: {result.outcome}")
    print(f"  reason_code: {result.reason_code}")
    print(f"  requested_file_count: {result.requested_file_count}")
    print(f"  found_file_count: {result.found_file_count}")
    print(f"  silver_row_count: {result.silver_row_count}")
    print(f"  null_order_date_count: {result.null_order_date_count}")
    print(f"  date_from: {result.date_from}")
    print(f"  date_to: {result.date_to}")
    print(f"  window_days: {result.window_days}")
    print(f"  refresh_window_valid: {result.refresh_window_valid}")
    if result.missing_file_ids:
        print(f"  missing_file_ids: {result.missing_file_ids}")
    for p in result.problems:
        print(f"  problema: {p}")
    for w in result.warnings:
        print(f"  aviso: {w}")


def _emit(result: ShopeeBatchWindowResult, as_json: bool) -> None:
    if as_json:
        _print_json(result)
    else:
        _print_human(result)


def _parse_cli_file_ids(raw_values: list[str]) -> list[int]:
    parsed: list[int] = []
    for raw in raw_values:
        try:
            parsed.append(int(raw))
        except (TypeError, ValueError):
            raise BatchWindowInputError(f"file_id inválido (esperado inteiro): {raw!r}") from None
    return validate_batch_file_ids(parsed)


def run_cli(
    raw_file_ids: list[str],
    as_json: bool,
    secret_path: Path = DEFAULT_WINDOW_WRITE_SECRET_PATH,
    repo_root: Path = REPO_ROOT,
) -> int:
    """Gate S5.2.1 — CLI fina. Ordem: valida file_ids (nunca lê secret/
    conecta se a entrada já estiver errada) -> `DATAMART_DATABASE_URL`
    configurado -> secret dedicado `.env.gold-window-write.local` ->
    guardrails -> `resolve_shopee_batch_window` (a ÚNICA API pública deste
    módulo — ela mesma roda o preflight obrigatório antes de qualquer
    consulta; esta CLI nunca chama `run_window_preflight` nem consulta a
    Silver por conta própria, nem tem nenhum segundo caminho capaz disso).
    Exit code determinado inteiramente por
    `_REASON_CODE_EXIT_CODE[reason_code]` — nunca lógica duplicada."""
    try:
        file_ids = _parse_cli_file_ids(raw_file_ids)
    except BatchWindowInputError as exc:
        result = ShopeeBatchWindowResult(outcome="blocked", reason_code=REASON_INVALID_INPUT, problems=[str(exc)])
        _emit(result, as_json)
        return _REASON_CODE_EXIT_CODE[result.reason_code]

    if not settings.datamart_url:
        result = ShopeeBatchWindowResult(
            outcome="blocked", reason_code=REASON_DATAMART_URL_NOT_CONFIGURED,
            requested_file_count=len(file_ids),
            problems=["DATAMART_DATABASE_URL não configurado — resolução de janela abortada."],
        )
        _emit(result, as_json)
        return _REASON_CODE_EXIT_CODE[result.reason_code]

    try:
        secret = window_write_conn.load_window_write_secret(secret_path, repo_root)
    except window_write_conn.WindowSecretLoadError as exc:
        result = ShopeeBatchWindowResult(
            outcome="blocked", reason_code=REASON_SECRET_LOAD_ERROR,
            requested_file_count=len(file_ids), problems=[str(exc)],
        )
        _emit(result, as_json)
        return _REASON_CODE_EXIT_CODE[result.reason_code]

    try:
        write_url = window_write_conn.validate_window_write_guardrails(secret, settings.datamart_url)
    except window_write_conn.WindowSecretLoadError as exc:
        result = ShopeeBatchWindowResult(
            outcome="blocked", reason_code=REASON_SECRET_LOAD_ERROR,
            requested_file_count=len(file_ids), problems=[str(exc)],
        )
        _emit(result, as_json)
        return _REASON_CODE_EXIT_CODE[result.reason_code]

    try:
        result = resolve_shopee_batch_window(write_url, settings.datamart_url, file_ids)
    except Exception as exc:  # noqa: BLE001
        result = ShopeeBatchWindowResult(
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
        description="Gate S5.2 — resolve a janela Gold Shopee (date_from/date_to) a partir de file_ids da Silver. Somente leitura."
    )
    parser.add_argument(
        "--file-id", dest="file_ids", action="append", default=[], metavar="<bigint>",
        help="file_id da Silver (repetível). Pelo menos um obrigatório.",
    )
    parser.add_argument("--json", action="store_true", help="Saída como um único documento JSON em stdout.")
    args = parser.parse_args(argv)

    try:
        return run_cli(args.file_ids, args.json)
    except Exception as exc:  # noqa: BLE001
        # Barreira final: nenhuma exceção não prevista pode escapar como
        # traceback com mensagem nativa do driver.
        result = ShopeeBatchWindowResult(
            outcome="failed", reason_code=REASON_UNEXPECTED_ERROR,
            problems=[f"falha inesperada e não tratada: {sanitize_error_message(exc)}"],
        )
        _emit(result, args.json)
        return _REASON_CODE_EXIT_CODE[REASON_UNEXPECTED_ERROR]


if __name__ == "__main__":
    sys.exit(main())
