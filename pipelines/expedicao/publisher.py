"""Publicacao no Neon — lock de sessao, substituicao POR CANAL, resumo por conta.

POR QUE LOCK DE SESSAO E NAO `pg_advisory_xact_lock`
----------------------------------------------------
A leitura acontece no Data Mart (RDS) e a escrita no Neon: duas conexoes, dois
bancos. Um lock transacional no Neon so existe enquanto a transacao existe —
para cobrir tambem a leitura, seria preciso abrir a transacao do Neon ANTES de
ler o Data Mart e deixa-la ociosa durante toda a extracao. Transacao ociosa
segura snapshot do MVCC, atrasa autovacuum e, em conexao pooled, derruba o pool.

Entao: `pg_advisory_lock` (sessao) adquirido em autocommit ANTES da leitura,
liberado no `finally`. A transacao do Neon so nasce na publicacao.

POR QUE A SUBSTITUICAO E POR CANAL
-----------------------------------
Shopee, ML e TikTok terao cadencias diferentes. Um `DELETE` sem filtro apagaria
os outros canais a cada refresh de um deles.

FOTOGRAFIA VAZIA
----------------
Backlog zero com fonte SAUDAVEL e uma fotografia legitima: todos os pedidos
foram expedidos. A publicacao apaga as linhas do canal, insere zero pedidos,
grava um resumo por conta esperada com `backlog_count = 0` e da commit. Tratar
isso como "nao faz nada" preservaria pedidos antigos na tela como se ainda
aguardassem expedicao.

Backlog zero com fonte DOENTE nao prova nada, e apagar a fila nesse caso seria
destruir estado com base em silencio. `publish_channel` exige `source_health` e
recusa antes de qualquer DELETE.

ATOMICIDADE FILA + RESUMO
-------------------------
Os dois vao na MESMA transacao, com o mesmo `refresh_batch_id`. Se fossem
transacoes separadas, uma falha no meio deixaria fila nova com resumo antigo (ou
o contrario) e a tela mostraria um backlog que a tendencia nao explica.
"""
from __future__ import annotations

from contextlib import contextmanager

from pipelines.expedicao.contract import (
    ADVISORY_LOCK_KEYS,
    FILA_TABLE,
    RUN_TABLE,
    Channel,
    SourceHealth,
    SourceUnhealthy,
)

#: Ordem das colunas do INSERT da fila. `brand` aparece DEPOIS de
#: `marketplace_order_id` para deixar visivel que e atributo, nao identidade.
FILA_COLUMNS = (
    "effective_at",
    "refresh_batch_id",
    "channel",
    "shop_account",
    "marketplace_order_id",
    "brand",
    "created_at",
    "paid_at",
    "dispatch_deadline",
    "deadline_source",
    "deadline_status",
    "operational_age_status",
    "is_slow_vs_baseline",
    "is_source_zombie",
    "is_stalled",
    "hours_open",
    "hours_overdue",
    "logistic_type",
    "carrier",
    "source_ingested_at",
    "source_freshness_status",
    "timestamp_quality",
)

#: PK FISICA FUTURA da fila. Espelha `pk_shopee_orders (shop_account, order_sn)`.
#: `brand` NAO entra: corrigir a marca de uma conta nao pode criar outro pedido.
FILA_PRIMARY_KEY = ("channel", "shop_account", "marketplace_order_id")

SUMMARY_COLUMNS = (
    "refresh_batch_id",
    "channel",
    "shop_account",
    "brand",
    "snapshot_hour",
    "observed_at",
    "source_watermark_at",
    "source_advanced",
    "backlog_count",
    "overdue_count",
    "due_within_24h_count",
    "on_time_count",
    "deadline_unavailable_count",
    "over_48h_count",
    "slow_count",
    "zombie_count",
    "stalled_count",
    "run_status",
    "ingested_at",
)

#: PK FISICA FUTURA do resumo.
SUMMARY_PRIMARY_KEY = ("channel", "shop_account", "snapshot_hour")

#: Substituicao SEMPRE restrita ao canal. O teste garante que este e o unico
#: DELETE do modulo e que ele carrega `WHERE channel`.
DELETE_CANAL_SQL = f"DELETE FROM {FILA_TABLE} WHERE channel = %s"

#: Reexecucao na mesma hora: a observacao mais NOVA vence; a mais antiga nao
#: sobrescreve. `DO NOTHING` seria insuficiente — a fonte pode avancar depois da
#: primeira execucao da hora, e a primeira leitura ficaria congelada como se
#: fosse o estado da hora inteira.
#:
#: A clausula `WHERE` torna o resultado independente da ordem de reexecucao.
_RUN = RUN_TABLE.split(".")[-1]
SUMMARY_CONFLICT_SQL = f"""
ON CONFLICT (channel, shop_account, snapshot_hour) DO UPDATE SET
    refresh_batch_id = EXCLUDED.refresh_batch_id,
    brand = EXCLUDED.brand,
    observed_at = EXCLUDED.observed_at,
    source_watermark_at = EXCLUDED.source_watermark_at,
    source_advanced = EXCLUDED.source_advanced,
    backlog_count = EXCLUDED.backlog_count,
    overdue_count = EXCLUDED.overdue_count,
    due_within_24h_count = EXCLUDED.due_within_24h_count,
    on_time_count = EXCLUDED.on_time_count,
    deadline_unavailable_count = EXCLUDED.deadline_unavailable_count,
    over_48h_count = EXCLUDED.over_48h_count,
    slow_count = EXCLUDED.slow_count,
    zombie_count = EXCLUDED.zombie_count,
    stalled_count = EXCLUDED.stalled_count,
    run_status = EXCLUDED.run_status,
    ingested_at = EXCLUDED.ingested_at
WHERE EXCLUDED.observed_at > {_RUN}.observed_at
"""


class LockNotAcquired(RuntimeError):
    """Outro refresh do mesmo canal esta em andamento."""


class IndeterminateCommit(RuntimeError):
    """`COMMIT` falhou sem resposta conclusiva do servidor.

    O estado da publicacao e DESCONHECIDO: pode ter sido aplicada. Retry cego
    duplicaria ou reverteria trabalho as cegas. A operacao para aqui e exige
    leitura de reconciliacao antes de qualquer nova tentativa.
    """


@contextmanager
def channel_lock(neon_conn, channel: Channel, *, blocking: bool = False):
    """Lock de SESSAO no Neon, cobrindo leitura + publicacao.

    Entra e sai em autocommit para nao deixar transacao ociosa. A liberacao vai
    no `finally`: falha na leitura do Data Mart nao pode deixar o canal travado.
    """
    chave = ADVISORY_LOCK_KEYS[channel]
    autocommit_anterior = neon_conn.autocommit
    neon_conn.autocommit = True
    adquirido = False
    try:
        with neon_conn.cursor() as cur:
            if blocking:
                cur.execute("SELECT pg_advisory_lock(%s)", (chave,))
                adquirido = True
            else:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (chave,))
                linha = cur.fetchone()
                # RealDictCursor devolve mapping; cursor padrao devolve tupla.
                adquirido = bool(
                    linha["pg_try_advisory_lock"] if isinstance(linha, dict)
                    else linha[0]
                )
            if not adquirido:
                raise LockNotAcquired(
                    f"refresh de expedicao ja em andamento para {channel.value} "
                    f"(advisory key {chave})"
                )
        yield
    finally:
        if adquirido:
            neon_conn.autocommit = True
            with neon_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (chave,))
        neon_conn.autocommit = autocommit_anterior


def publish_channel(
    neon_conn,
    channel: Channel,
    fila_rows: list[dict],
    summary_rows: list[dict],
    *,
    source_health: SourceHealth,
    refresh_batch_id: str,
    execute_values=None,
) -> tuple[int, int]:
    """Substitui a fila DO CANAL e grava o resumo por conta, numa transacao.

    Devolve `(linhas_fila, contas_registradas)`. `fila_rows` vazio com fonte
    saudavel e uma FOTOGRAFIA VAZIA legitima: o DELETE roda, nenhum INSERT de
    pedido roda, o resumo grava `backlog_count = 0` por conta e o commit
    acontece.

    Fonte nao saudavel levanta `SourceUnhealthy` ANTES de qualquer DELETE.
    """
    if not source_health.can_publish:
        raise SourceUnhealthy(
            f"publicacao recusada para {channel.value}: fonte em "
            f"'{source_health.value}'. A fila anterior foi preservada."
        )

    if not summary_rows:
        # Fonte saudavel SEMPRE produz uma linha por conta esperada. Resumo
        # vazio significa lote montado errado — publicar a fila sem resumo
        # deixaria a tendencia sem a hora correspondente.
        raise ValueError(
            f"publicacao de {channel.value} sem resumo por conta: uma execucao "
            "saudavel grava uma linha por conta esperada, inclusive com zero."
        )

    if execute_values is None:
        from psycopg2.extras import execute_values as _ev  # noqa: PLC0415

        execute_values = _ev

    estranhos = {r["channel"] for r in fila_rows} - {channel.value}
    if estranhos:
        raise ValueError(
            f"publicacao de {channel.value} recebeu linhas de outro canal: "
            f"{sorted(estranhos)}"
        )
    estranhos_resumo = {r["channel"] for r in summary_rows} - {channel.value}
    if estranhos_resumo:
        raise ValueError(
            f"resumo de {channel.value} contem outro canal: {sorted(estranhos_resumo)}"
        )

    # Um unico lote: fila e resumo precisam ser rastreaveis como a MESMA
    # observacao. Lotes diferentes indicam montagem incoerente.
    lotes = {r["refresh_batch_id"] for r in fila_rows} | {
        r["refresh_batch_id"] for r in summary_rows
    }
    if lotes - {refresh_batch_id}:
        raise ValueError(
            f"refresh_batch_id inconsistente no lote: {sorted(lotes)} "
            f"(esperado {refresh_batch_id!r})"
        )

    # Uma conta nao pode publicar pedido com a marca de outra: a marca vem do
    # registry, por conta, e misturar indicaria resolucao errada.
    marca_por_conta = {r["shop_account"]: r["brand"] for r in summary_rows}
    for linha in fila_rows:
        esperada = marca_por_conta.get(linha["shop_account"])
        if esperada is not None and linha["brand"] != esperada:
            raise ValueError(
                f"conta {linha['shop_account']!r} publicou pedido com marca "
                f"{linha['brand']!r}, mas o registry resolve {esperada!r}"
            )

    neon_conn.autocommit = False
    try:
        with neon_conn.cursor() as cur:
            cur.execute(DELETE_CANAL_SQL, (channel.value,))

            if fila_rows:
                execute_values(
                    cur,
                    f"INSERT INTO {FILA_TABLE} ({', '.join(FILA_COLUMNS)}) VALUES %s",
                    [tuple(r[c] for c in FILA_COLUMNS) for r in fila_rows],
                )
            # SEMPRE gravado, inclusive com backlog zero: e a prova de que a
            # fotografia foi tirada naquela hora, por conta.
            execute_values(
                cur,
                f"INSERT INTO {RUN_TABLE} ({', '.join(SUMMARY_COLUMNS)}) VALUES %s "
                + SUMMARY_CONFLICT_SQL,
                [tuple(r[c] for c in SUMMARY_COLUMNS) for r in summary_rows],
            )
    except Exception:
        neon_conn.rollback()
        raise

    try:
        neon_conn.commit()
    except Exception as exc:  # noqa: BLE001 — convertido, sem retry
        raise IndeterminateCommit(
            f"COMMIT do canal {channel.value} sem resposta conclusiva; "
            "reconcilie antes de reexecutar."
        ) from exc

    return len(fila_rows), len(summary_rows)
