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
primeira com dados igualmente velhos. `SourceGate` trava essa ordem — toda
leitura passa por `gate.read`, que levanta se `mark_locked` ainda nao foi
chamado — e um mutante que inverte a ordem faz o teste falhar.

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
from datetime import date, datetime, timezone

from psycopg2.extras import execute_values

from pipelines import channel_offer_sync as cos

#: O dominio vem POR `cos`, nao por import direto: e' `channel_offer_sync` que
#: poe `apps/api` no `sys.path`, e um import direto aqui resolveria contra o
#: checkout errado quando este modulo e' carregado primeiro.
dom = cos.dom

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

#: Tamanho da pagina do INSERT.
#:
#: 500 e' uma escolha, nao um acaso: deixa a Shopee (692 ofertas) em 2 paginas e
#: o TikTok (1.208) em 3, mantendo o comando enviado ao servidor num tamanho que
#: ele parseia sem esforco e sem multiplicar idas e voltas.
#:
#: Deliberadamente NAO e' "uma pagina do tamanho da carga". Isso resolveria o
#: numero de hoje e voltaria a quebrar quando o catalogo crescer: um unico
#: comando com dezenas de milhares de tuplas vira problema de memoria no lado do
#: driver e de tempo de parse no servidor. A contagem tem de estar certa em
#: qualquer volume, nao so' enquanto couber numa pagina.
INSERT_PAGE_SIZE = 500

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


def insert_offers_paged(cur, registros) -> int:
    """INSERT paginado com contagem ACUMULADA e conferida pagina a pagina.

    Por que existe (Gate PMA-2C3C): `execute_values` pagina POR DENTRO, com
    `page_size=100` por padrao, e emite um `execute` por pagina. Ao fim da
    chamada `cur.rowcount` vale apenas a ULTIMA pagina. Foi assim que o piloto
    do PMA-2C3B caiu: 692 ofertas viraram 6 paginas de 100 mais uma de 92, a
    guarda leu 92, comparou com 692 e desfez uma transacao que estava correta.
    O mesmo aconteceria com as 1.208 do TikTok (12x100 + 8).

    Aqui a paginacao e' EXPLICITA. Cada chamada recebe uma pagina ja' fatiada e
    `page_size=len(pagina)`, de modo que `execute_values` emita exatamente um
    `execute` e o `rowcount` observado seja, sem ambiguidade, o daquela pagina.
    O total e' a SOMA do que o driver informou — nunca um comprimento de lista
    assumido como sucesso.

    Contrato:
      * lista vazia devolve 0 sem emitir nenhum `execute`;
      * `rowcount` None, -1 ou diferente do tamanho da pagina levanta na hora,
        antes do commit — ausencia de confirmacao nao vira confirmacao;
      * nao chama `commit` nem `rollback`, nao abre conexao: um erro em
        qualquer pagina sobe e derruba a transacao inteira do chamador.
    """
    total = 0
    for inicio in range(0, len(registros), INSERT_PAGE_SIZE):
        pagina = registros[inicio:inicio + INSERT_PAGE_SIZE]
        execute_values(
            cur, SQL_INSERT_OFFERS,
            [_linha_para_tupla(r) for r in pagina],
            page_size=len(pagina),
        )
        informado = cur.rowcount
        if informado != len(pagina):
            raise PublisherError(
                f"o driver informou {informado} linha(s) numa pagina de "
                f"{len(pagina)}; a carga nao pode ser confirmada"
            )
        total += informado
    return total


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

        # 2. Insere em paginas EXPLICITAS, somando o que o driver confirma em
        #    cada uma. Escopo saudavel com zero ofertas passa por aqui sem
        #    nada: o DELETE dele ja' rodou, e o resultado e' `rows_loaded = 0`.
        esperado = len(plan.records)
        inseridas = insert_offers_paged(cur, plan.records)

        # 3. PRIMEIRA defesa: o total CONFIRMADO pelo driver bate com o plano.
        #    Antes esta guarda lia `cur.rowcount` depois de um `execute_values`
        #    paginado por dentro e comparava a ultima pagina com o total; agora
        #    compara soma com soma.
        if inseridas != esperado:
            raise PublisherError(
                f"o INSERT confirmou {inseridas} linhas e o plano tinha "
                f"{esperado}"
            )

        # 4. SEGUNDA defesa, independente da primeira: o que a propria
        #    transacao enxerga nos escopos publicados. Ela cobre o que uma
        #    contagem de driver nao cobre — linha gravada em escopo que nao era
        #    o seu, DELETE que varreu alem do proprio escopo, regra do banco
        #    que redirecionou a linha. As duas so' concordam se a fotografia
        #    estiver inteira E no lugar certo.
        por_escopo = {}
        for escopo in plan.scopes_to_replace:
            cur.execute(SQL_COUNT_SCOPE, {
                "marketplace": escopo.marketplace,
                "observed_date": escopo.observed_date,
                "shop_account": escopo.shop_account,
            })
            por_escopo[escopo] = cur.fetchone()[0]

    visto = sum(por_escopo.values())
    if visto != esperado:
        raise PublisherError(
            f"reconciliacao pre-commit divergiu: a transacao enxerga {visto} "
            f"linhas nos escopos publicados e o plano tinha {esperado}"
        )
    return inseridas, por_escopo


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
# FIACAO OPERACIONAL  (Gate PMA-2C3A-R)
# ---------------------------------------------------------------------------
# O gate anterior entregou o executor mas deixou a CLI recusando `--apply`
# incondicionalmente: nenhum caminho produtivo alcancava `run_publication`.
# Este bloco fecha a fiacao.
#
# TRES CONEXOES, TRES PAPEIS, ZERO FALLBACK
# ------------------------------------------
#     fonte     Data Mart, READ ONLY imposta pelo servidor
#     destino   Neon, GRAVAVEL — e' ela que detem o advisory lock
#     auditoria Neon, conexao PROPRIA com commit proprio
#
# Nenhuma cai para a outra. Se a auditoria nao abrir, a publicacao nao comeca:
# publicar sem rastro seria pior que nao publicar. As credenciais sao as
# canonicas do repositorio (`DATABASE_URL`, `DATAMART_DATABASE_URL`); nenhuma
# variavel nova e' inventada.

EXIT_FAILED = 1

#: Contas do canal na fotografia JA' PUBLICADA. Serve para responder "quem
#: deixou de aparecer": conta que existia e sumiu da fonte nao executou, e a
#: fotografia dela precisa ser preservada, nao apagada.
SQL_PUBLISHED_ACCOUNTS = f"""
SELECT DISTINCT shop_account
  FROM {cos.TARGET_TABLE}
 WHERE marketplace = %(marketplace)s
   AND observed_date = (SELECT max(observed_date)
                          FROM {cos.TARGET_TABLE}
                         WHERE marketplace = %(marketplace)s)
"""

#: Watermark JA' PUBLICADO por escopo. E' contra ele que a regressao e' medida.
SQL_PUBLISHED_WATERMARKS = f"""
SELECT observed_date, shop_account, max(account_watermark_at) AS watermark_at
  FROM {cos.TARGET_TABLE}
 WHERE marketplace = %(marketplace)s
 GROUP BY observed_date, shop_account
"""


@dataclass(frozen=True)
class AccountStates:
    """As quatro situacoes de conta, separadas.

    `healthy_empty` e `unavailable` NAO podem se confundir: a primeira apaga a
    propria fotografia (a conta existe e hoje nao tem oferta), a segunda nao
    apaga nada (nao conseguimos olhar). `did_not_run` e' a terceira: a conta
    existia na fotografia publicada e sumiu da fonte.
    """

    healthy_with_offers: frozenset = frozenset()
    healthy_empty: frozenset = frozenset()
    unavailable: frozenset = frozenset()
    did_not_run: frozenset = frozenset()

    @property
    def healthy(self) -> frozenset:
        """Somente estas entram em `healthy_scopes` e sofrem DELETE."""
        return self.healthy_with_offers | self.healthy_empty

    def as_report(self) -> dict:
        return {
            "healthy_with_offers": sorted(self.healthy_with_offers),
            "healthy_empty": sorted(self.healthy_empty),
            "unavailable": sorted(self.unavailable),
            "did_not_run": sorted(self.did_not_run),
        }


def classify_accounts(*, accounts_in_source, accounts_with_offers,
                      accounts_published, source_available: bool) -> AccountStates:
    """Separa as quatro situacoes. Funcao PURA — nao le banco.

    `source_available=False` joga TODAS as contas conhecidas em `unavailable`:
    sem fonte, nao se sabe nada sobre nenhuma, e nenhuma pode ser apagada.
    """
    fonte = frozenset(accounts_in_source)
    com_oferta = frozenset(accounts_with_offers)
    publicadas = frozenset(accounts_published)

    if not source_available:
        return AccountStates(unavailable=fonte | publicadas)

    desconhecidas = com_oferta - fonte
    if desconhecidas:
        # Oferta de uma conta que o relogio nao conhece: o escopo dela nao teria
        # watermark para validar regressao. Falhar alto e' melhor que publicar
        # sem relogio.
        raise PublisherError("oferta de conta ausente do relogio da fonte")

    return AccountStates(
        healthy_with_offers=com_oferta,
        healthy_empty=fonte - com_oferta,
        # Estava na fotografia publicada e sumiu da fonte: nao executou.
        did_not_run=publicadas - fonte,
    )


def _writable(url: str):
    """Conexao GRAVAVEL de destino. E' ela que detem o advisory lock.

    O lock precisa viver na MESMA sessao que escreve: um lock tomado noutra
    conexao nao protegeria nada. Por isso o destino nao pode ser `_read_only`.
    """
    import psycopg2

    conn = psycopg2.connect(url, connect_timeout=30)
    conn.set_session(readonly=False, autocommit=False)
    return conn


def published_accounts(target_conn, marketplace: str) -> frozenset:
    """Contas da ultima fotografia publicada. Vazio na primeira execucao."""
    with target_conn.cursor() as cur:
        cur.execute(SQL_PUBLISHED_ACCOUNTS, {"marketplace": marketplace})
        return frozenset(linha[0] for linha in cur.fetchall() if linha[0])


def published_watermarks(target_conn, marketplace: str) -> dict:
    """Watermark publicado por escopo, para a guarda de regressao."""
    publicado = {}
    with target_conn.cursor() as cur:
        cur.execute(SQL_PUBLISHED_WATERMARKS, {"marketplace": marketplace})
        for observed_date, shop_account, watermark_at in cur.fetchall():
            escopo = cos.PublicationScope(marketplace, observed_date, shop_account)
            publicado[escopo] = watermark_at
    return publicado


def collect_snapshot(gate: SourceGate, source_conn, marketplace: str,
                     observed_date=None):
    """Le a fonte e monta os registros. SO' roda depois do lock.

    Toda leitura passa por `gate.read`, que levanta se o lock ainda nao foi
    adquirido. A guarda esta aqui, e nao so' no chamador, porque e' aqui que a
    ordem pode ser quebrada por um refactor distraido.
    """
    catalogo = gate.read("catalogo_interno",
                         lambda: cos.load_internal_catalog(source_conn))
    if marketplace == "shopee":
        relogios = gate.read(
            "relogios_shopee",
            lambda: cos.load_shopee_account_clocks(source_conn))
        linhas = gate.read(
            "ofertas_shopee",
            lambda: cos.fetch_shopee_offers(source_conn, catalogo))
        registros = cos.build_shopee_records(linhas, relogios)
        return registros, relogios, frozenset(relogios)

    dia = observed_date or gate.read(
        "snapshot_tiktok", lambda: cos.latest_tiktok_snapshot(source_conn))
    if dia is None:
        raise PublisherError("a fonte do TikTok nao tem snapshot publicado")
    if observed_date is not None and not gate.read(
            "data_existe", lambda: cos.tiktok_snapshot_exists(source_conn, dia)):
        # Data inexistente NAO vira a mais proxima: a pergunta era sobre ESTE dia.
        raise PublisherError("a data pedida nao existe na fonte do TikTok")
    linhas = gate.read(
        "ofertas_tiktok",
        lambda: cos.fetch_tiktok_offers(source_conn, dia, catalogo))
    relogios = {
        cos.canonical_account("tiktok"): cos.AccountClock(
            marketplace="tiktok", account=cos.canonical_account("tiktok"),
            watermark_at=max((l["fetched_at"] for l in linhas
                              if l.get("fetched_at")), default=None),
            rows_seen=len(linhas)),
    }
    registros = cos.build_tiktok_records(linhas, dia, relogios)
    return registros, relogios, frozenset(relogios)


def _incoming_watermarks(registros, relogios, marketplace: str) -> dict:
    """Watermark DA FOTOGRAFIA por escopo. Nunca o `observed_at` da oferta."""
    por_escopo = {}
    for registro in registros:
        escopo = cos.PublicationScope.of(registro)
        por_escopo.setdefault(escopo, registro.get("account_watermark_at"))
    return por_escopo


def _scopes_for_empty_accounts(contas_vazias, marketplace, relogios,
                               observed_date_default):
    """Escopo de conta saudavel SEM oferta.

    Sem isso, uma conta que esvaziou nao teria escopo — e sua fotografia
    antiga sobreviveria passando por atual. O `observed_date` vem do relogio
    da PROPRIA conta.
    """
    escopos, watermarks = set(), {}
    for conta in contas_vazias:
        relogio = relogios.get(conta)
        instante = relogio.watermark_at if relogio else None
        dia = dom.observed_date_from(instante) or observed_date_default
        if dia is None:
            raise PublisherError(
                "conta saudavel sem oferta e sem relogio: escopo indefinido")
        escopo = cos.PublicationScope(marketplace, dia, conta)
        escopos.add(escopo)
        watermarks[escopo] = instante
    return escopos, watermarks


# ---------------------------------------------------------------------------
# CLI operacional
# ---------------------------------------------------------------------------
#: Mensagem CONSTANTE de data invalida. Nao ecoa o texto recebido: o valor do
#: operador nao precisa aparecer em log para ele saber o que digitou.
MSG_DATA_INVALIDA = "data invalida; use exatamente o formato YYYY-MM-DD"


def data_observada(texto):
    """Converte o argumento da CLI em `datetime.date`, na FRONTEIRA.

    Existe porque o argparse entrega TEXTO e o driver devolve `datetime.date`:
    `cos.tiktok_snapshot_exists` compara os dois com `==`, e
    `date(2026, 9, 17) == "2026-09-17"` e' False. O resultado era uma recusa
    ("a data pedida nao existe na fonte") para uma data que existia — medido no
    PMA-2C4D1.

    `strptime` com `%Y-%m-%d` de proposito, e nao `date.fromisoformat`: a
    partir do 3.11 o `fromisoformat` aceita `20260917` e ate' data com hora, e
    o contrato aqui e' UMA forma so'. Nada de timezone, nada de formato
    regional, nada de coercao silenciosa.
    """
    from datetime import datetime as _dt

    if isinstance(texto, date):
        return texto
    try:
        return _dt.strptime(texto, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        import argparse
        raise argparse.ArgumentTypeError(MSG_DATA_INVALIDA)


def build_cli():
    import argparse

    parser = argparse.ArgumentParser(
        prog="channel_offer_publisher",
        description=("Publica a fotografia multicanal de Shopee e TikTok. "
                     "Sem `--apply` nada e' escrito."),
    )
    parser.add_argument("--marketplace", choices=list(cos.CHANNEL_MARKETPLACES),
                        required=True)
    parser.add_argument("--apply", action="store_true",
                        help="publica de verdade; exige a migration 017 aplicada")
    parser.add_argument("--operator-override", action="store_true",
                        help=("comando operacional EXPLICITO para a carga "
                              "piloto com a feature flag desligada"))
    # `type=` roda no parse, ANTES de qualquer conexao, lock ou auditoria: uma
    # data malformada derruba a CLI sem tocar em banco.
    parser.add_argument("--observed-date", default=None, type=data_observada,
                        help="YYYY-MM-DD; sem aproximacao para o dia mais proximo")
    return parser


#: Mensagem de cada desfecho e o codigo de saida correspondente. A tabela e'
#: dado, nao `if` espalhado: assim o teste consegue afirmar a matriz inteira.
OUTCOME_EXIT = {
    STATE_PUBLISHED: EXIT_OK,
    STATE_REFUSED: EXIT_REFUSED,
    STATE_LOCK_UNAVAILABLE: EXIT_LOCKED,
    STATE_ROLLED_BACK: EXIT_FAILED,
    STATE_INDETERMINATE: EXIT_INDETERMINATE,
}


def run_apply(args, *, connect_target=None, connect_audit=None,
              connect_source=None) -> PublicationOutcome:
    """Fluxo operacional completo. As fabricas sao injetaveis SO' para teste.

    Ordem, e ela e' o contrato:

        destino -> precondicao (017 + relacao) -> LOCK -> fonte -> plano ->
        run_publication -> libera tudo em `finally`

    A precondicao roda ANTES do lock porque uma instalacao sem a tabela nao
    deve nem disputar o lock com quem esta publicando de verdade.
    """
    import os

    abrir_destino = connect_target or (lambda: _writable(os.environ["DATABASE_URL"]))
    abrir_auditoria = connect_audit or (lambda: _writable(os.environ["DATABASE_URL"]))
    abrir_fonte = connect_source or (
        lambda: cos._read_only(os.environ["DATAMART_DATABASE_URL"]))

    destino = auditoria = fonte = None
    try:
        destino = abrir_destino()
        # Recusa sanitizada: sem a 017 aplicada ou sem a relacao, nada comeca.
        cos.assert_apply_authorized(destino)

        auditoria = abrir_auditoria()
        fonte = abrir_fonte()

        def build_plan(gate):
            registros, relogios, contas_na_fonte = collect_snapshot(
                gate, fonte, args.marketplace, args.observed_date)
            contas_com_oferta = frozenset(
                r["shop_account"] for r in registros if r.get("shop_account"))
            estados = classify_accounts(
                accounts_in_source=contas_na_fonte,
                accounts_with_offers=contas_com_oferta,
                accounts_published=published_accounts(destino, args.marketplace),
                source_available=True,
            )
            entrando = _incoming_watermarks(registros, relogios, args.marketplace)
            dia_padrao = registros[0]["observed_date"] if registros else None
            vazios, wm_vazios = _scopes_for_empty_accounts(
                estados.healthy_empty, args.marketplace, relogios, dia_padrao)
            entrando.update(wm_vazios)
            saudaveis = set(cos.scopes_of(registros)) | vazios
            print(f"contas: {estados.as_report()}")
            return cos.build_publication_plan(
                marketplace=args.marketplace,
                records=registros,
                healthy_scopes=saudaveis,
                incoming_watermarks=entrando,
                published_watermarks=published_watermarks(destino,
                                                          args.marketplace),
                channel_enabled=False,
                operator_override=bool(args.operator_override),
                source_available=True,
            )

        return run_publication(
            target_conn=destino, audit_conn=auditoria,
            build_plan=build_plan,
            rows_extracted_of=lambda plano: len(plano.records),
        )
    finally:
        # Fecha na ordem inversa da abertura. Cada `close` e' isolado: uma
        # conexao morta nao pode impedir que as outras fechem.
        for conexao in (fonte, auditoria, destino):
            if conexao is None:
                continue
            try:
                conexao.close()
            except Exception:
                pass


def main(argv=None) -> int:
    args = build_cli().parse_args(argv)

    if not args.apply:
        # Modo diagnostico: NAO adquire lock, NAO escreve auditoria e NAO toca
        # na tabela de destino. Ele existe para conferir a fonte sem disputar
        # nada com quem esta publicando.
        print(f"dry-run: marketplace={args.marketplace}; nenhuma conexao de "
              "destino e' aberta, nenhum lock e' adquirido, nada e' escrito.")
        return EXIT_OK

    try:
        desfecho = run_apply(args)
    except cos.ChannelSyncError as exc:
        # Recusa de contrato, ja' sanitizada na origem.
        print(f"RECUSADO: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (KeyboardInterrupt, SystemExit):
        # Propaga DEPOIS do cleanup do `finally` de `run_apply`. Engolir uma
        # interrupcao faria o operador achar que a execucao terminou.
        raise
    except Exception as exc:
        print(f"FALHA: {_detalhe(exc)}", file=sys.stderr)
        return EXIT_FAILED

    _relata(desfecho)
    return OUTCOME_EXIT[desfecho.state]


def _relata(desfecho: PublicationOutcome) -> None:
    """Uma linha honesta por desfecho. Nada de DSN, host, SQL ou traceback."""
    if desfecho.state == STATE_PUBLISHED:
        print(f"PUBLICADO: {desfecho.rows_loaded} ofertas em "
              f"{len(desfecho.scopes_replaced)} escopo(s); "
              f"lidas {desfecho.rows_extracted}.")
        if not desfecho.audit_complete:
            # NAO rebaixa o desfecho: os dados estao publicados. O defeito e'
            # do registro, e precisa de conserto manual.
            print("AVISO: os dados estao publicados, mas a auditoria ficou "
                  "INCOMPLETA. Verifique audit.source_sync_run "
                  f"(sync_run_id={desfecho.sync_run_id}).", file=sys.stderr)
        return
    if desfecho.state == STATE_REFUSED:
        print(f"RECUSADO: {desfecho.decision.reason}; nenhuma linha foi tocada.",
              file=sys.stderr)
        return
    if desfecho.state == STATE_LOCK_UNAVAILABLE:
        print("OCUPADO: outra execucao detem o lock; nada foi lido nem escrito.",
              file=sys.stderr)
        return
    if desfecho.state == STATE_ROLLED_BACK:
        print(f"FALHOU: {desfecho.detail}; a transacao foi desfeita e nenhuma "
              "fotografia mudou.", file=sys.stderr)
        return
    print(f"INDETERMINADO: {desfecho.detail}. NAO reexecute as cegas: "
          "confira audit.source_sync_run "
          f"(sync_run_id={desfecho.sync_run_id}) e a propria tabela antes de "
          "decidir.", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
