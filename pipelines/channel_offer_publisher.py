"""Gate PMA-2C3A — executor auditavel da publicacao multicanal.

    Data Mart (read-only) -> channel_offer_sync -> PublicationPlan -> AQUI -> Neon

`channel_offer_sync` decide O QUE publicar e devolve um `PublicationPlan`
imutavel. Este modulo EXECUTA esse plano, e nada mais: ele nao escolhe escopo,
nao compara relogio e nao le a fonte por conta propria.

A MAQUINA DE ESTADOS TEM CINCO DESFECHOS, E ELES NAO SE CONFUNDEM
-----------------------------------------------------------------
    lock_unavailable  outra execucao detem o lock; nada foi lido nem escrito
    refused           o plano recusou; nenhuma linha foi tocada
    published         COMMIT confirmado pelo servidor
    rolled_back       falha ANTES do commit; a transacao foi desfeita
    indeterminate     o COMMIT foi tentado e levantou

`indeterminate` existe porque uma queda de conexao DEPOIS do COMMIT e'
indistinguivel de uma queda ANTES. Marcar `failed` afirmaria que nada foi
gravado — e isso nao se sabe. Repetir afirmaria que nada foi gravado tambem, e
poderia duplicar. Entao o registro de auditoria PERMANECE `running` com a nota
`INDETERMINADO:`, e um humano reconcilia por leitura.

ORDEM OPERACIONAL, E ELA E' O CONTRATO
---------------------------------------
    1. abre a conexao de destino
    2. `pg_try_advisory_lock(917120017)` — fail-fast, zero espera, zero retry
    3. SO' DEPOIS do lock: le a fonte e monta os snapshots
    4. classifica as contas: executaram / indisponiveis / saudaveis com zero
    5. monta UM `PublicationPlan` imutavel
    6. valida `account_watermark_at` por marketplace e shop_account
    7. DELETE por escopo + INSERT dos registros, na MESMA transacao
    8. reconcilia ANTES do commit
    9. commit
   10. auditoria
   11. libera o lock — em TODOS os desfechos, na MESMA sessao

O passo 3 nao e' detalhe: ler antes do lock permitiria que duas execucoes
lessem a mesma fonte e publicassem em sequencia, a segunda sobrescrevendo a
primeira com dados igualmente velhos. `assert_source_read_after_lock` trava
essa ordem, e um teste a viola de proposito para provar que a guarda pega.

AUDITORIA EM CONEXAO INDEPENDENTE
----------------------------------
Mesma escolha de `pipelines/avoe/snapshot_import.py`: `audit.source_sync_run` e'
escrito numa conexao PROPRIA, com commit proprio. Se fosse a mesma transacao
dos dados, um `failed` seria desfeito junto com o rollback e a tentativa nao
deixaria rastro; e uma falha ao auditar derrubaria dados ja' publicados.

O CHECK da tabela admite apenas `running`, `success` e `failed`. Nao ha status
`indeterminate`, e nao se cria um: `running` ja' significa "nao concluiu", que e'
exatamente o que sabemos. A nota vai em `error_message`.

Falha na auditoria DEPOIS do commit nao muda o fato: os dados estao publicados.
O desfecho continua `published` e o defeito viaja em `audit_complete=False`.

ZERO RETRY
----------
Nenhum desfecho e' repetido automaticamente. O operador le' a mensagem
sanitizada, decide, e roda de novo.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from psycopg2.extras import execute_values

from pipelines import channel_offer_sync as cos

#: Nome do processo em `audit.source_sync_run.source_name`. Segue a convencao
#: dos demais (`shopee_daily`, `tiktok_daily`, `ml_daily`).
AUDIT_SOURCE_NAME = "channel_offer_snapshot"

#: Os tres unicos valores que o CHECK da tabela de auditoria admite.
AUDIT_RUNNING = "running"
AUDIT_SUCCESS = "success"
AUDIT_FAILED = "failed"
AUDIT_STATUSES = (AUDIT_RUNNING, AUDIT_SUCCESS, AUDIT_FAILED)

#: Desfechos da maquina de estados.
STATE_LOCK_UNAVAILABLE = "lock_unavailable"
STATE_REFUSED = "refused"
STATE_PUBLISHED = "published"
STATE_ROLLED_BACK = "rolled_back"
STATE_INDETERMINATE = "indeterminate"
PUBLICATION_STATES = (
    STATE_LOCK_UNAVAILABLE,
    STATE_REFUSED,
    STATE_PUBLISHED,
    STATE_ROLLED_BACK,
    STATE_INDETERMINATE,
)

#: Desfechos em que os dados NAO estao publicados com certeza.
STATES_WITHOUT_CONFIRMED_DATA = (
    STATE_LOCK_UNAVAILABLE, STATE_REFUSED, STATE_ROLLED_BACK,
)

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_LOCKED = 3
EXIT_INDETERMINATE = 4
EXIT_USAGE = 5


class PublisherError(cos.ChannelSyncError):
    """Falha do executor. Mensagem sempre sanitizada."""


class SourceReadBeforeLockError(PublisherError):
    """Tentativa de ler a fonte antes de adquirir o lock."""


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
#: Colunas na ordem de `cos.RECORD_COLUMNS`. Explicitar evita que uma coluna
#: nova entre no INSERT sem passar por revisao.
SQL_INSERT_OFFERS = (
    f"INSERT INTO {cos.TARGET_TABLE} ("
    + ", ".join(cos.RECORD_COLUMNS)
    + ") VALUES %s"
)

#: Reconciliacao ANTES do commit: conta o que a propria transacao enxerga.
SQL_COUNT_SCOPE = f"""
SELECT count(*) AS n
  FROM {cos.TARGET_TABLE}
 WHERE marketplace   = %(marketplace)s
   AND observed_date = %(observed_date)s
   AND shop_account  = %(shop_account)s
"""


@dataclass(frozen=True)
class PublicationOutcome:
    """Desfecho factual da execucao. Nunca afirma mais do que se sabe."""

    state: str
    decision: cos.PublishDecision
    rows_extracted: int = 0
    rows_loaded: int = 0
    scopes_replaced: tuple = ()
    sync_run_id: int | None = None
    #: `False` quando a auditoria nao pode ser concluida. NAO invalida os dados.
    audit_complete: bool = True
    detail: str | None = None

    @property
    def data_published(self) -> bool:
        return self.state == STATE_PUBLISHED

    @property
    def data_state_known(self) -> bool:
        return self.state != STATE_INDETERMINATE


@dataclass
class SourceGate:
    """Guarda de ORDEM: a fonte so' pode ser lida depois do lock.

    Existe como objeto e nao como comentario porque a ordem e' a parte do
    contrato que um refactor quebra sem perceber.
    """

    lock_acquired: bool = False
    reads: list = field(default_factory=list)

    def mark_locked(self) -> None:
        self.lock_acquired = True

    def read(self, rotulo: str, funcao):
        if not self.lock_acquired:
            raise SourceReadBeforeLockError(
                "leitura da fonte tentada antes do advisory lock"
            )
        self.reads.append(rotulo)
        return funcao()


# ---------------------------------------------------------------------------
# Auditoria — conexao INDEPENDENTE, commit proprio
# ---------------------------------------------------------------------------
def audit_start(audit_conn, rows_extracted: int) -> int:
    """Abre o registro como `running`. Commit proprio, antes dos dados."""
    with audit_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit.source_sync_run
                (source_name, marketplace_id, loja_id, status, started_at,
                 rows_extracted)
            VALUES (%s, NULL, NULL, 'running', NOW(), %s)
            RETURNING sync_run_id
            """,
            (AUDIT_SOURCE_NAME, rows_extracted),
        )
        sync_run_id = cur.fetchone()[0]
    audit_conn.commit()
    return sync_run_id


def audit_finish(audit_conn, sync_run_id: int, status: str,
                 rows_loaded: int | None = None,
                 error_message: str | None = None) -> None:
    """Fecha o registro. `rowcount != 1` e' falha: o alvo precisa existir."""
    if status not in AUDIT_STATUSES:
        raise PublisherError("status de auditoria fora do dominio da tabela")
    with audit_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE audit.source_sync_run
               SET status = %s, finished_at = NOW(),
                   rows_loaded = %s, error_message = %s
             WHERE sync_run_id = %s
            """,
            (status, rows_loaded, error_message, sync_run_id),
        )
        if cur.rowcount != 1:
            raise PublisherError("UPDATE de auditoria nao afetou exatamente 1 linha")
    audit_conn.commit()


def audit_mark_indeterminate(audit_conn, sync_run_id: int, detalhe: str) -> None:
    """Commit de dados indeterminado: NAO marca `failed`, NAO fecha o registro.

    `running` continua sendo a verdade — a execucao nao concluiu, e nao se sabe
    se os dados entraram. `failed` afirmaria que nao entraram.
    """
    with audit_conn.cursor() as cur:
        cur.execute(
            """
            UPDATE audit.source_sync_run
               SET error_message = %s
             WHERE sync_run_id = %s
            """,
            (f"INDETERMINADO: {detalhe}"[:4000], sync_run_id),
        )
        if cur.rowcount != 1:
            raise PublisherError("UPDATE de auditoria nao afetou exatamente 1 linha")
    audit_conn.commit()


# ---------------------------------------------------------------------------
# Execucao do plano
# ---------------------------------------------------------------------------
def _linha_para_tupla(registro: dict) -> tuple:
    return tuple(registro[coluna] for coluna in cos.RECORD_COLUMNS)


def execute_plan(target_conn, plan: cos.PublicationPlan) -> tuple[int, dict]:
    """DELETE por escopo + INSERT, na MESMA transacao. NAO comita.

    Quem comita e' `run_publication`, para que o commit e o seu desfecho fiquem
    num unico lugar. Separar o DELETE do INSERT em transacoes distintas abriria
    uma janela em que a tela mostraria a fotografia vazia.

    Devolve `(linhas_inseridas, contagem_por_escopo_reconciliada)`.
    """
    if not plan.decision.allowed:
        raise PublisherError("plano recusado nao pode ser executado")

    with target_conn.cursor() as cur:
        # 1. Apaga SOMENTE os escopos saudaveis. Um escopo ausente daqui —
        #    conta indisponivel ou que nao executou — nao e' tocado, e sua
        #    fotografia anterior permanece.
        for escopo in plan.scopes_to_replace:
            cur.execute(cos.SQL_DELETE_SCOPE, {
                "marketplace": escopo.marketplace,
                "observed_date": escopo.observed_date,
                "shop_account": escopo.shop_account,
            })

        # 2. Insere. Escopo saudavel com zero ofertas passa por aqui sem nada:
        #    o DELETE dele ja' rodou, e o resultado e' `rows_loaded = 0`.
        inseridas = 0
        if plan.records:
            execute_values(
                cur, SQL_INSERT_OFFERS,
                [_linha_para_tupla(r) for r in plan.records],
            )
            inseridas = cur.rowcount if cur.rowcount is not None else 0

        # 3. Reconcilia ANTES do commit, dentro da propria transacao.
        por_escopo = {}
        for escopo in plan.scopes_to_replace:
            cur.execute(SQL_COUNT_SCOPE, {
                "marketplace": escopo.marketplace,
                "observed_date": escopo.observed_date,
                "shop_account": escopo.shop_account,
            })
            por_escopo[escopo] = cur.fetchone()[0]

    esperado = len(plan.records)
    visto = sum(por_escopo.values())
    if visto != esperado:
        raise PublisherError(
            f"reconciliacao pre-commit divergiu: a transacao enxerga {visto} "
            f"linhas nos escopos publicados e o plano tinha {esperado}"
        )
    if inseridas and inseridas != esperado:
        raise PublisherError(
            f"INSERT relatou {inseridas} linhas e o plano tinha {esperado}"
        )
    return esperado, por_escopo


def run_publication(
    *,
    target_conn,
    audit_conn,
    build_plan,
    rows_extracted_of,
    operator_override: bool = False,
) -> PublicationOutcome:
    """Orquestra a maquina de estados. O lock e' liberado em TODOS os desfechos.

    `build_plan(gate)` recebe o `SourceGate` e devolve o `PublicationPlan`. A
    injecao existe para que o teste possa provar a ORDEM: o gate recusa
    qualquer leitura feita antes do lock.
    """
    gate = SourceGate()
    sync_run_id = None

    # --- 2. LOCK, antes de qualquer leitura --------------------------------
    if not cos.try_acquire_publication_lock(target_conn):
        return PublicationOutcome(
            state=STATE_LOCK_UNAVAILABLE,
            decision=cos.PublishDecision(cos.PUBLISH_REFUSE, "lock_unavailable"),
            detail="outra execucao detem o lock; nada foi lido nem escrito",
        )
    gate.mark_locked()

    try:
        # --- 3 a 6. le a fonte, classifica contas, monta e valida o plano ---
        plan = build_plan(gate)
        rows_extracted = int(rows_extracted_of(plan))

        if not plan.decision.allowed:
            # Recusa ANTES de qualquer mutacao. Deixa rastro auditavel: uma
            # tentativa recusada que nao registrasse nada seria indistinguivel
            # de uma execucao que nunca rodou.
            try:
                sync_run_id = audit_start(audit_conn, rows_extracted)
                audit_finish(audit_conn, sync_run_id, AUDIT_FAILED,
                             rows_loaded=0,
                             error_message=f"recusado: {plan.decision.reason}")
                auditoria_ok = True
            except Exception as exc:
                auditoria_ok = False
                _ = cos._sanitize(exc)
            return PublicationOutcome(
                state=STATE_REFUSED, decision=plan.decision,
                rows_extracted=rows_extracted, rows_loaded=0,
                sync_run_id=sync_run_id, audit_complete=auditoria_ok,
                detail=plan.decision.reason,
            )

        sync_run_id = audit_start(audit_conn, rows_extracted)

        # --- 7 e 8. DELETE + INSERT + reconciliacao, tudo pre-commit -------
        try:
            rows_loaded, _por_escopo = execute_plan(target_conn, plan)
        except Exception as exc:
            detalhe = _detalhe(exc)
            try:
                target_conn.rollback()
            except Exception:
                pass
            _auditar_silencioso(audit_conn, sync_run_id, AUDIT_FAILED, 0, detalhe)
            return PublicationOutcome(
                state=STATE_ROLLED_BACK, decision=plan.decision,
                rows_extracted=rows_extracted, rows_loaded=0,
                sync_run_id=sync_run_id, detail=detalhe,
            )

        # --- 9. COMMIT ------------------------------------------------------
        try:
            target_conn.commit()
        except Exception as exc:
            # NAO se tenta rollback aqui: ele nao desfaria um commit possivelmente
            # aplicado, e tentar sugeriria que desfez.
            detalhe = _detalhe(exc)
            auditoria_ok = True
            try:
                audit_mark_indeterminate(audit_conn, sync_run_id, detalhe)
            except Exception:
                auditoria_ok = False
            return PublicationOutcome(
                state=STATE_INDETERMINATE, decision=plan.decision,
                rows_extracted=rows_extracted, rows_loaded=rows_loaded,
                scopes_replaced=plan.scopes_to_replace,
                sync_run_id=sync_run_id, audit_complete=auditoria_ok,
                detail=("o commit foi TENTADO e levantou; nenhum rollback foi "
                        f"tentado depois disso: {detalhe}"),
            )

        # --- 10. auditoria DEPOIS do commit --------------------------------
        # Os dados ja' estao publicados. Uma falha aqui nao os desfaz e nao
        # pode rebaixar o desfecho para `failed`.
        auditoria_ok = True
        try:
            audit_finish(audit_conn, sync_run_id, AUDIT_SUCCESS,
                         rows_loaded=rows_loaded)
        except Exception:
            auditoria_ok = False
        return PublicationOutcome(
            state=STATE_PUBLISHED, decision=plan.decision,
            rows_extracted=rows_extracted, rows_loaded=rows_loaded,
            scopes_replaced=plan.scopes_to_replace,
            sync_run_id=sync_run_id, audit_complete=auditoria_ok,
        )
    finally:
        # --- 11. libera o lock em TODOS os desfechos, na MESMA sessao -------
        try:
            cos.release_publication_lock(target_conn)
        except Exception:
            pass


def _detalhe(exc: BaseException) -> str:
    """Mensagem sanitizada. Do driver sobra so' o nome da classe."""
    if isinstance(exc, cos.ChannelSyncError):
        return str(exc)
    return cos._sanitize(exc)


def _auditar_silencioso(audit_conn, sync_run_id, status, rows_loaded, detalhe):
    """Auditoria de melhor esforco num caminho que ja' esta falhando.

    Se ela tambem falhar, nao ha o que fazer alem de nao piorar: o desfecho
    dos DADOS ja' foi decidido e nao muda por causa do registro.
    """
    try:
        audit_finish(audit_conn, sync_run_id, status,
                     rows_loaded=rows_loaded, error_message=detalhe[:4000])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI operacional
# ---------------------------------------------------------------------------
def build_cli():
    import argparse

    parser = argparse.ArgumentParser(
        prog="channel_offer_publisher",
        description=("Publica a fotografia multicanal. `--apply` exige a "
                     "migration 017 aplicada E a relacao existente."),
    )
    parser.add_argument("--marketplace", choices=list(cos.CHANNEL_MARKETPLACES),
                        required=True)
    parser.add_argument("--apply", action="store_true",
                        help="publica de verdade; sem isso, nada e' escrito")
    parser.add_argument("--operator-override", action="store_true",
                        help=("comando operacional EXPLICITO para a carga "
                              "piloto com a feature flag desligada"))
    return parser


def main(argv=None) -> int:  # pragma: no cover — exercitado por teste de CLI
    args = build_cli().parse_args(argv)
    if not args.apply:
        print(f"dry-run: marketplace={args.marketplace}; nada sera escrito.")
        return EXIT_OK
    print("RECUSADO: a publicacao real depende da migration "
          f"{cos.REQUIRED_MIGRATION} aplicada e da relacao {cos.TARGET_TABLE} "
          "existente. Rode `assert_apply_authorized` contra o destino antes.",
          file=sys.stderr)
    return EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
