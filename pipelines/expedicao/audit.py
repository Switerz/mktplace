"""Auditoria do refresh de expedicao — sobre o schema que EXISTE.

SCHEMA REAL (migration 003, inspecionado antes de escrever este modulo)
----------------------------------------------------------------------
    audit.source_sync_run(
        sync_run_id, source_name, marketplace_id, loja_id, started_at,
        finished_at, status CHECK IN ('running','success','failed'),
        rows_extracted, rows_loaded, error_message, source_min_date,
        source_max_date)

    audit.data_quality_check(
        check_id, check_name, table_name, marketplace_id, loja_id,
        check_timestamp, status CHECK IN ('pass','fail','warn'),
        severity CHECK IN ('critical','high','medium','low'),
        failed_rows, details)

`source_sync_run` NAO tem coluna `details`. Consequencia: `error_message` fica
EXCLUSIVAMENTE para erro sanitizado. Escrever metadado ali transformaria a
coluna que o health check usa para detectar falha num campo de recado — e toda
execucao bem-sucedida passaria a parecer ter erro.

`run_mode` NAO E MAIS NECESSARIA (revisto no EXP-1A-R)
------------------------------------------------------
O EXP-1A propunha `ALTER TABLE audit.source_sync_run ADD COLUMN run_mode` para
distinguir NO_OP de "publicou zero linhas". **Essa proposta esta retirada.**

Depois da correcao, NAO EXISTE MAIS NO_OP por watermark: toda execucao agendada
recomputa e publica. Entao `rows_loaded = 0` passou a ter um unico significado —
fotografia vazia publicada — e a ambiguidade que justificava a coluna deixou de
existir. Adicionar coluna para preservar um conceito que foi removido seria
carregar a cicatriz de um defeito ja corrigido.

O que `source_sync_run` nao expressa (contagem por bucket, contas esperadas x
observadas, saude da fonte, `effective_at`) vai para `data_quality_check.details`,
que tem o campo no contrato real — e tambem para
`marts.expedicao_refresh_run`, que e dado de produto, nao de auditoria.

FALHA DE AUDITORIA DEPOIS DO COMMIT
-----------------------------------
Se a publicacao deu commit e so a auditoria falhou, o dado ESTA publicado.
Marcar `failed` seria mentir para o health check, que pararia de confiar numa
fonte saudavel. A linha permanece `running` (estado honesto: "comecou e nao se
sabe como terminou") e o erro sai sanitizado pelo canal operacional.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from pipelines.expedicao.contract import (
    FILA_TABLE,
    Channel,
    FreshnessStatus,
    SourceHealth,
)

#: Mesma defesa de `pipelines/ingestion/shopee_raw/write_conn.py`: nenhuma DSN
#: com credencial pode vazar para log ou para `error_message`.
_CREDENTIAL_IN_MESSAGE_RE = re.compile(r"//[^/\s:@]+:[^/\s@]+@")

#: `data_quality_check.status` por estado de freshness.
_FRESHNESS_TO_STATUS = {
    FreshnessStatus.FRESH.value: ("pass", "low"),
    FreshnessStatus.STALE.value: ("warn", "medium"),
    FreshnessStatus.CRITICAL.value: ("fail", "high"),
    FreshnessStatus.UNKNOWN.value: ("warn", "medium"),
}

CHECK_OBSERVATION = "expedicao_observacao"
CHECK_FRESHNESS = "expedicao_source_freshness"


class AuditAfterCommitError(RuntimeError):
    """Auditoria falhou DEPOIS do commit da publicacao.

    Sinaliza que o dado esta publicado e o rastro esta incompleto. Quem captura
    nao deve reverter nem remarcar a execucao como falha.
    """


def sanitize_error_message(exc: BaseException) -> str:
    """Remove `usuario:senha@` antes de a mensagem virar log ou coluna."""
    return _CREDENTIAL_IN_MESSAGE_RE.sub("//<redacted>@", str(exc))


def source_name_for(channel: Channel) -> str:
    """Nome estavel por canal: um canal parado e identificavel sozinho."""
    return f"expedicao_{channel.value}"


def audit_start(conn, source_name: str, marketplace_id: int) -> int:
    """INSERT `running` com commit proprio.

    Conexao SEPARADA da transacao de dados, pelo mesmo motivo de
    `sync_serving_snapshots`: se a publicacao sofrer rollback, o registro da
    tentativa precisa sobreviver.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit.source_sync_run
                (source_name, marketplace_id, status, started_at)
            VALUES (%s, %s, 'running', NOW())
            RETURNING sync_run_id
            """,
            (source_name, marketplace_id),
        )
        # `RETURNING` com RealDictCursor devolve mapping, nao tupla. Acesso por
        # NOME de proposito: fake que devolve tupla passaria no teste e quebraria
        # em producao.
        linha = cur.fetchone()
        run_id = linha["sync_run_id"] if isinstance(linha, dict) else linha[0]
    conn.commit()
    return int(run_id)


def audit_finish(
    conn,
    run_id: int,
    status: str,
    rows_extracted: int | None = None,
    rows_loaded: int | None = None,
    error: str | None = None,
) -> None:
    """UPDATE final. `error` SO para erro sanitizado, nunca para metadado.

    `rows_loaded = 0` com `status='success'` significa, sem ambiguidade,
    fotografia vazia publicada.
    """
    if status not in ("success", "failed"):
        raise ValueError(f"status invalido para source_sync_run: {status!r}")
    if status == "success" and error is not None:
        raise ValueError(
            "error_message em execucao bem-sucedida: a coluna e exclusiva de "
            "erro sanitizado e nao pode carregar metadado."
        )
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE audit.source_sync_run SET
                finished_at = NOW(), status = %s,
                rows_extracted = %s, rows_loaded = %s, error_message = %s
            WHERE sync_run_id = %s
            """,
            (status, rows_extracted, rows_loaded, error, run_id),
        )
    conn.commit()


def record_observation(
    conn,
    channel: Channel,
    marketplace_id: int,
    effective_at: datetime,
    *,
    source_health: SourceHealth,
    backlog_count: int,
    expected_accounts: frozenset[str],
    observed_accounts: frozenset[str],
    watermarks: dict[str, datetime | None],
    source_advanced: bool,
    published: bool,
    accounts_recorded: int = 0,
) -> None:
    """Registra a OBSERVACAO da execucao, publicada ou recusada.

    Substitui o antigo `record_no_op`. Note `source_advanced`: ele continua
    sendo medido e registrado como metadado de frescor, mas NAO decide mais se
    a fotografia e publicada — essa era a causa do defeito 1.

    `empty_photograph` distingue explicitamente "fonte saudavel observou zero
    pendencias" de "fonte doente, nada publicado".
    """
    empty = source_health.can_publish and backlog_count == 0
    detalhe = json.dumps(
        {
            "channel": channel.value,
            "effective_at": effective_at.isoformat(),
            "source_health": source_health.value,
            "source_advanced": source_advanced,
            "published": published,
            "empty_photograph": empty,
            "backlog_count": backlog_count,
            # `accounts_recorded` deve igualar o numero de contas esperadas numa
            # execucao saudavel — inclusive na fotografia vazia.
            "accounts_recorded": accounts_recorded,
            "expected_accounts": sorted(expected_accounts),
            "observed_accounts": sorted(observed_accounts),
            "missing_accounts": sorted(expected_accounts - observed_accounts),
            "unexpected_accounts": sorted(observed_accounts - expected_accounts),
            "watermarks": {
                k: (v.isoformat() if v is not None else None)
                for k, v in sorted(watermarks.items())
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    status, severity = (
        ("pass", "low") if source_health.can_publish else ("fail", "critical")
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit.data_quality_check
                (check_name, table_name, marketplace_id, status, severity,
                 failed_rows, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                CHECK_OBSERVATION,
                FILA_TABLE,
                marketplace_id,
                status,
                severity,
                0 if source_health.can_publish
                else len(expected_accounts ^ observed_accounts),
                detalhe,
            ),
        )
    conn.commit()


def record_freshness(
    conn,
    marketplace_id: int,
    por_marca: dict[str, tuple[str, int, datetime | None]],
) -> None:
    """Uma linha POR MARCA.

    Agregado global esconderia conta parada atras de conta atualizada — o
    defeito que deixou a planilha de expedicao 43 dias defasada sem alarme.
    `loja_id` fica nulo aqui: a resolucao para `marts.dim_loja` acontece na
    publicacao e a marca ja identifica a linha em `details`.
    """
    for marca, (estado, linhas, watermark) in sorted(por_marca.items()):
        status, severity = _FRESHNESS_TO_STATUS.get(estado, ("warn", "medium"))
        detalhe = json.dumps(
            {
                "brand": marca,
                "freshness": estado,
                "open_orders": linhas,
                "source_watermark": watermark.isoformat() if watermark else None,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit.data_quality_check
                    (check_name, table_name, marketplace_id, status, severity,
                     failed_rows, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    CHECK_FRESHNESS,
                    FILA_TABLE,
                    marketplace_id,
                    status,
                    severity,
                    0 if status == "pass" else linhas,
                    detalhe,
                ),
            )
    conn.commit()


def finish_after_commit(
    conn, run_id: int, rows_extracted: int, rows_loaded: int
) -> None:
    """Fecha a auditoria de uma publicacao JA COMMITADA.

    Falha aqui vira `AuditAfterCommitError` e NAO remarca a execucao: o dado
    esta publicado, e `failed` seria informacao errada. A linha fica `running`,
    que e o estado honesto de "nao se sabe como terminou".
    """
    try:
        audit_finish(conn, run_id, "success", rows_extracted, rows_loaded)
    except Exception as exc:  # noqa: BLE001 — fronteira; convertido e propagado
        raise AuditAfterCommitError(
            "publicacao COMMITADA e auditoria incompleta "
            f"(sync_run_id={run_id}): {sanitize_error_message(exc)}"
        ) from exc
