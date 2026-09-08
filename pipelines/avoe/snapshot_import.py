"""Gate AVH-4A / AVH-4A-R — importador dos snapshots manuais da Avoe.

SEM `--apply`, NAO ESCREVE
--------------------------
O padrao e' dry-run: le' o snapshot, valida manifesto e hashes, monta as linhas
e imprime o relatorio. Nenhuma conexao de escrita e' aberta. Com `--apply` ele
escreve as duas tabelas de snapshot no Neon, dentro de UMA transacao de dados,
sob advisory lock proprio.

APPEND-ONLY
-----------
Nao ha DELETE nem UPDATE nas tabelas de snapshot em nenhum caminho. Reimportar
a mesma captura e' no-op idempotente; importar conteudo de negocio diferente
com o mesmo `captured_at` e' recusado. Um snapshot novo exige `captured_at`
novo e coexiste com os antigos.

IDEMPOTENCIA INDEPENDENTE DO RUN ID (FINDING 1)
-----------------------------------------------
A equivalencia entre o que esta gravado e o que foi lido compara SOMENTE as
colunas de negocio e proveniencia. `import_run_id` e `imported_at` sao
operacionais da execucao e ficam FORA da comparacao: a mesma captura lida numa
segunda execucao, com run id diferente, e' no-op — nao divergencia.

`snapshot_id` E' PROVENIENCIA, NAO OPERACIONAL
----------------------------------------------
Ele entra na comparacao de proposito: um `snapshot_id` diferente sob o mesmo
`captured_at` significa arquivos diferentes carimbados com a mesma captura, e
isso e' conflito, nao repeticao.

AUDITORIA DURAVEL (FINDING 9, alternativa A)
--------------------------------------------
`audit.source_sync_run` e' escrito numa CONEXAO INDEPENDENTE, com commit
proprio: `running` antes da transacao de dados, `success`/`failed` depois. Uma
tentativa revertida deixa rastro, ao contrario do desenho anterior. Se o commit
dos dados ficar INDETERMINADO (excecao no proprio commit), o registro NAO e'
marcado `failed`: tenta-se gravar nele a nota `INDETERMINADO:`, porque afirmar
falha seria afirmar mais do que se sabe. Se essa propria gravacao nao for
confirmada, nem o conteudo da linha e' afirmado.

ZERO RETRY
----------
Falha nao e' repetida automaticamente. O operador le' o erro sanitizado, decide
e roda de novo.

MENSAGEM DE ERRO FAIL-CLOSED (Gate AVH-4A-H1-D1)
------------------------------------------------
`_sanitize_erro` nao ecoa o texto de excecao externa em nenhuma hipotese: de
uma excecao de driver ou biblioteca sobra apenas o nome da classe. So' a
mensagem de `SnapshotImportError`, construida dentro deste modulo, e'
preservada. Nada de DSN, host, usuario, caminho, SQL, `params` ou `DETAIL`
chega a stderr, a `audit.source_sync_run`, a `__cause__`/`__context__` ou ao
traceback — que tambem e' zerado por `_levanta`.

CREDENCIAL
----------
`DATABASE_URL` (Neon) e' o unico segredo lido, e somente com `--apply`. Nenhuma
credencial da Avoe e' lida em ponto algum: o snapshot e' arquivo.

Uso:
    python -m pipelines.avoe.snapshot_import --snapshot-dir <DIR>
    python -m pipelines.avoe.snapshot_import --snapshot-dir <DIR> --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

from pipelines.avoe.snapshot_contract import (
    SnapshotContractError,
    TARGET_TABLE_CHANNELS,
    TARGET_TABLE_TARGETS,
    ReadResult,
    default_run_id,
    read_snapshot,
    sanitize_run_id,
    select_current_version,
)

# Chave propria, distinta da do PMA (913_120_013) e de qualquer outra frente.
ADVISORY_LOCK_KEY = 913_120_041
LOCK_TIMEOUT = "30s"
STATEMENT_TIMEOUT = "120s"
INSERT_PAGE_SIZE = 500

SYNC_SOURCE_NAME = "avoe_manual_snapshot"
STATUS_VALIDOS = frozenset({"running", "success", "failed"})

# Colunas OPERACIONAIS da execucao. Ficam fora da comparacao de equivalencia.
# `imported_at` nao entra no INSERT (tem DEFAULT NOW()); esta listado para que a
# intencao seja legivel e testavel.
OPERATIONAL_COLUMNS = ("import_run_id", "imported_at")

# Colunas de NEGOCIO + PROVENIENCIA. Definem a identidade do snapshot.
TARGET_BUSINESS_COLUMNS = (
    "source", "captured_at", "ref_month", "brand", "brand_key",
    "target_amount", "currency_code", "currency_status", "currency_warning",
    "source_recorded_at", "source_file", "source_file_hash", "snapshot_id",
)
CHANNEL_BUSINESS_COLUMNS = (
    "source", "captured_at", "ref_month", "brand", "channel", "brand_key",
    "channel_source_label", "reported_amount", "is_proxy",
    "definition_status", "definition_warning", "currency_code",
    "currency_status", "days_covered", "first_business_date",
    "last_business_date", "coverage_status", "source_recorded_at",
    "source_file", "source_file_hash", "snapshot_id",
)

# O que efetivamente vai no INSERT: negocio/proveniencia + run id.
TARGET_INSERT_COLUMNS = TARGET_BUSINESS_COLUMNS + ("import_run_id",)
CHANNEL_INSERT_COLUMNS = CHANNEL_BUSINESS_COLUMNS + ("import_run_id",)


class SnapshotImportError(RuntimeError):
    """Falha de importacao. Mensagem sanitizada, sem credencial nem caminho."""


# --------------------------------------------------------------------------
# Maquina de estados da publicacao (Gate AVH-4A-H1)
# --------------------------------------------------------------------------
# Tres estados, e so' tres. O que separa o segundo do terceiro e' a unica coisa
# que o processo realmente sabe: se `commit()` RETORNOU ou LEVANTOU.
#
#   nao_confirmada  falha comprovadamente ANTES do commit -> rollback + failed
#   confirmada      commit retornou                        -> success
#   indeterminada   commit levantou                        -> NUNCA failed,
#                                                             NUNCA success
#
# Uma excecao no commit nao prova que o banco deixou de gravar. Marcar `failed`
# ali afirmaria que nada entrou, e isso nao se sabe; fazer rollback depois dela
# nao desfaz um commit possivelmente aplicado. Por isso o commit vive FORA do
# bloco que faz rollback.
#
# A AUDITORIA e' um eixo independente (Gate AVH-4A-H1-R). Cada combinacao dela
# com o estado dos dados tem seu proprio desfecho e sua propria mensagem:
#
#   audit_start falhou              -> publicacao NAO tentada
#   rollback dos dados levantou     -> reversao NAO confirmada
#   audit_finish(failed) falhou     -> dados revertidos (reversao confirmada),
#                                      auditoria incompleta
#   audit_mark_indeterminate falhou -> indeterminado, auditoria nao confirmada
#   audit_finish(success) falhou    -> publicado, auditoria incompleta
#
# Nenhum desses caminhos pode terminar no handler generico da CLI afirmando
# "rollback aplicado": essa frase so' e' verdadeira quando `publish()` executou
# o rollback e ele RETORNOU. Ver `DESFECHOS` e a matriz em `main`.

PUBLICACAO_NAO_CONFIRMADA = "nao_confirmada"
PUBLICACAO_CONFIRMADA = "confirmada"
PUBLICACAO_INDETERMINADA = "indeterminada"


class AuditoriaInicialIncompleta(SnapshotImportError):
    """`audit_start` falhou. `publish()` NAO chegou a ser chamado.

    Nenhuma transacao de dados foi iniciada, nenhum commit foi tentado e nada
    precisou ser desfeito. Nao existe `sync_run_id` confiavel.
    """


class PublicacaoNaoConfirmada(SnapshotImportError):
    """Falha COMPROVADAMENTE anterior ao commit, com reversao CONFIRMADA.

    `rollback_confirmado` e' SEMPRE True nesta classe e nas suas subclasses:
    ela so' e' levantada depois de o `rollback()` da conexao de dados ter
    retornado. Um rollback que levanta nao chega aqui — ele produz
    `ReversaoNaoConfirmada`, que nao e' subclasse desta justamente para nao
    herdar a garantia de "nada publicado".
    """

    rollback_confirmado = True


class AuditoriaIncompletaSemPublicacao(PublicacaoNaoConfirmada):
    """Falha pre-commit E falha ao gravar `failed` na auditoria.

    Os dados estao seguros pelo mesmo motivo da classe base — o commit nunca
    foi tentado. O que ficou aberto e' so' o registro de auditoria. Isto NAO e'
    commit indeterminado.
    """


class PublicacaoIndeterminada(SnapshotImportError):
    """`commit()` levantou. Nao se sabe se os dados entraram.

    Nao ha rollback e nao ha retry: o proximo passo e' reconciliar em leitura.
    """


class PublicacaoIndeterminadaAuditoriaNaoConfirmada(PublicacaoIndeterminada):
    """Commit indeterminado E falha ao registrar o estado indeterminado.

    Continua sendo indeterminado para todos os efeitos — subclasse de
    proposito. A diferenca e' que nem a nota `INDETERMINADO:` esta garantida no
    `audit.source_sync_run`, entao o rastro tambem nao pode ser assumido.
    """


class ReversaoNaoConfirmada(SnapshotImportError):
    """Falha pre-commit E o proprio `rollback()` levantou (Gate H1-R2).

    Estado distinto de `PublicacaoNaoConfirmada`, e nao subclasse dela: aqui o
    processo NAO pode afirmar que a transacao terminou. O que se sabe e' so'
    que o `commit()` nunca foi tentado. Se a reversao chegou ao servidor, se a
    conexao morreu, ou se a transacao ficou pendurada ate' o servidor derrubar,
    nada disso foi observado.

    O encerramento da conexao e' TENTADO para forcar o fim da transacao, e o
    resultado dessa tentativa fica em `conexao_encerrada`: True se o `close()`
    retornou, False se ele tambem levantou. A mensagem acompanha o atributo —
    nunca se afirma incondicionalmente que a conexao foi encerrada.
    """

    conexao_encerrada = False


class AuditoriaIncompleta(SnapshotImportError):
    """Publicacao CONFIRMADA, auditoria nao finalizada.

    Os dados estao publicados (ou o no-op esta concluido) e a transacao de
    dados ja terminou — isso e' certo. O que falhou foi so' o fechamento do
    registro em `audit.source_sync_run`.

    O estado da LINHA depende de `resultado_auditoria`: se o `UPDATE` foi
    revertido (`revertida`), ela permanece `running`; se a mutacao ficou
    `indeterminada`, nao se afirma em que estado ela ficou. Nao se pode dizer
    que ela permanece `running` nos dois casos.
    """


# --------------------------------------------------------------------------
# Mutacoes de auditoria: quatro resultados possiveis (Gate AVH-4A-H1-R2)
# --------------------------------------------------------------------------
# Uma excecao vinda de `audit_*` NAO diz, sozinha, o que ficou persistido. O
# que separa os casos e' a FASE em que a falha aconteceu e, quando ela e'
# anterior ao commit, o que o rollback devolveu:
#
#   nao_tentada    a mutacao nem chegou a ser emitida
#   confirmada     o commit da auditoria RETORNOU
#   revertida      a mutacao falhou ANTES do commit e o rollback da auditoria
#                  RETORNOU; o conteudo anterior da linha permanece
#   indeterminada  duas origens, e nas duas nada se afirma sobre a linha:
#                    (a) falha pre-commit em que o rollback tambem NAO
#                        retornou;
#                    (b) commit TENTADO que levantou — e ai' e' permanente,
#                        independentemente de qualquer rollback posterior
#
# Isto NAO e' uma transacao distribuida entre dados e auditoria. Sao dois
# recursos independentes, e o processo apenas se recusa a afirmar sobre um o
# que so' observou no outro.

AUDIT_NAO_TENTADA = "nao_tentada"
AUDIT_CONFIRMADA = "confirmada"
AUDIT_REVERTIDA = "revertida"
AUDIT_INDETERMINADA = "indeterminada"


class ResultadoAuditoria:
    """Desfecho observado de UMA mutacao de auditoria.

    `estado` e' um dos quatro `AUDIT_*`. `indeterminada` cobre duas origens
    distintas, e em nenhuma delas o conteudo da linha e' afirmavel:

      (a) a mutacao falhou ANTES do commit e o `rollback()` da conexao de
          auditoria tambem nao retornou;
      (b) o `commit()` da auditoria foi TENTADO e levantou. Este caso e'
          permanente: nenhum rollback e' tentado depois, e um rollback que
          retornasse nao rebaixaria o resultado para `revertida`, porque nao
          desfaz um commit possivelmente aplicado.

    `detalhe` guarda so' texto que passou por `_sanitize_erro`.
    """

    __slots__ = ("mutacao", "estado", "detalhe")

    def __init__(self, mutacao: str, estado: str, detalhe: str = ""):
        self.mutacao = mutacao
        self.estado = estado
        self.detalhe = detalhe

    @property
    def certo(self) -> bool:
        """True so' quando o que ficou na linha e' observavel."""
        return self.estado in (AUDIT_NAO_TENTADA, AUDIT_CONFIRMADA, AUDIT_REVERTIDA)

    def frase(self) -> str:
        """Como falar deste resultado sem afirmar o que nao se observou."""
        if self.estado == AUDIT_NAO_TENTADA:
            return f"{self.mutacao} nao foi tentada"
        if self.estado == AUDIT_CONFIRMADA:
            return f"{self.mutacao} confirmada"
        if self.estado == AUDIT_REVERTIDA:
            return (f"{self.mutacao} falhou e foi revertida na propria conexao de "
                    f"auditoria ({self.detalhe})")
        return (f"{self.mutacao} teve resultado INDETERMINADO ({self.detalhe}); "
                f"o conteudo da linha de auditoria nao pode ser afirmado; "
                f"reconcilie audit.source_sync_run em leitura antes de "
                f"qualquer nova execucao")

    def __repr__(self) -> str:  # pragma: no cover — diagnostico
        return f"ResultadoAuditoria({self.mutacao!r}, {self.estado!r})"


class MutacaoAuditoriaFalhou(SnapshotImportError):
    """Transporta um `ResultadoAuditoria` para quem orquestra."""

    def __init__(self, resultado: ResultadoAuditoria):
        super().__init__(resultado.frase())
        self.resultado = resultado


# Toda excecao da maquina de estados carrega `resultado_auditoria`. O padrao e'
# `nao_tentada`, que e' a verdade para o que `publish()` levanta sozinho, antes
# de qualquer interacao com a auditoria. `apply_with_audit` sobrescreve o
# atributo na instancia assim que souber o desfecho real da mutacao.
AUDITORIA_NAO_TENTADA = ResultadoAuditoria("auditoria", AUDIT_NAO_TENTADA)
SnapshotImportError.resultado_auditoria = AUDITORIA_NAO_TENTADA


def _resultado_de(exc: Exception, mutacao: str) -> ResultadoAuditoria:
    """Normaliza QUALQUER falha de auditoria num `ResultadoAuditoria`.

    As tres funcoes `audit_*` levantam `MutacaoAuditoriaFalhou` ja classificado.
    Uma excecao de outro tipo (um defeito de programacao, por exemplo) nao foi
    classificada por ninguem, entao o unico rotulo honesto e' INDETERMINADA.
    Um so' handler por ponto de chamada, em vez de dois quase iguais.
    """
    if isinstance(exc, MutacaoAuditoriaFalhou):
        return exc.resultado
    return ResultadoAuditoria(mutacao, AUDIT_INDETERMINADA, _sanitize_erro(exc))


def _sobre_a_linha(ra: ResultadoAuditoria, quando_revertida: str,
                   quando_incerta: str) -> str:
    """Escolhe a frase conforme o que a mutacao permite afirmar."""
    return quando_revertida if ra.estado == AUDIT_REVERTIDA else quando_incerta


def _levanta(erro: SnapshotImportError):
    """Levanta `erro` SEM cadeia (Gate AVH-4A-H1-R2, finding 3).

    Nem `__cause__`, nem `__context__`, nem traceback do driver sobrevivem: a
    excecao original de psycopg2 carrega DSN, host, usuario e SQL no texto e
    nos frames, e nada disso pode chegar a stderr, a log ou a
    `traceback.format_exception`. So' a mensagem ja' sanitizada viaja.
    """
    erro.__cause__ = None
    erro.__context__ = None
    erro.__suppress_context__ = True
    erro.__traceback__ = None
    try:
        raise erro
    finally:
        # O proprio `raise` acima reinstala `__context__` com a excecao que
        # estiver em tratamento. Este `finally` roda durante o desempilhamento,
        # antes de qualquer chamador ver o objeto.
        erro.__cause__ = None
        erro.__context__ = None
        erro.__suppress_context__ = True


def _commit_ou_indeterminado(neon_conn, saida: dict, no_op: bool) -> None:
    """Executa o commit e classifica o resultado. Nunca faz rollback."""
    try:
        neon_conn.commit()
    except Exception as exc:
        saida["estado"] = PUBLICACAO_INDETERMINADA
        _levanta(PublicacaoIndeterminada(
            f"commit {'do no-op' if no_op else 'dos dados'} levantou excecao: "
            f"{_sanitize_erro(exc)}. NAO se sabe se a gravacao foi aplicada; "
            f"nenhum rollback foi tentado e nenhum retry sera feito. "
            f"Reconcilie em leitura antes de qualquer nova execucao."))
    saida["estado"] = PUBLICACAO_CONFIRMADA


MENSAGEM_EXTERNA_SUPRIMIDA = "<mensagem externa suprimida>"
MAX_ERRO = 400
MAX_NOME_CLASSE = 64


def _nome_seguro(exc: BaseException) -> str:
    """Nome da classe reduzido a `[A-Za-z0-9_]` e truncado.

    O nome da classe e' a unica coisa que se aproveita de uma excecao externa,
    e mesmo ele passa por allowlist: nada de modulo, caminho ou pontuacao que
    pudesse carregar contexto.
    """
    nome = "".join(c for c in type(exc).__name__
                   if c.isascii() and (c.isalnum() or c == "_"))
    return nome[:MAX_NOME_CLASSE] or "Excecao"


def _sanitize_erro(exc: BaseException) -> str:
    """FAIL-CLOSED: texto de excecao externa NUNCA e' ecoado (Gate H1-D1).

    So' `SnapshotImportError` tem a mensagem preservada, porque ela e'
    construida aqui dentro, a partir de literais deste modulo e de campos que
    ele controla (`sync_run_id`, contagens, nomes de tabela) — e o que ela
    embute de origem externa ja passou por esta funcao.

    Qualquer outra excecao — driver, biblioteca, defeito de programacao —
    contribui apenas com o nome da classe. O texto dela pode trazer DSN em
    qualquer caixa, `host=`/`user=`/`dbname=` em formato key-value, caminho de
    arquivo, SQL, `params`, `DETAIL` de constraint com valor de linha ou
    qualquer outra coisa; nenhuma denylist da conta disso, e uma mensagem
    externa aparentemente inofensiva tambem nao e' repetida, porque essa
    avaliacao nao pode ser feita em tempo de execucao.

    Espaco em branco e' normalizado (inclui `\\n` e `\\r`) e o resultado e'
    truncado em `MAX_ERRO`.
    """
    if isinstance(exc, SnapshotImportError):
        texto = f"{_nome_seguro(exc)}: {exc}"
    else:
        texto = f"{_nome_seguro(exc)}: {MENSAGEM_EXTERNA_SUPRIMIDA}"
    return " ".join(texto.split())[:MAX_ERRO]


# --------------------------------------------------------------------------
# Relatorio
# --------------------------------------------------------------------------

def _fmt(valor: Decimal | None) -> str:
    return "indisponivel (NULL)" if valor is None else f"{valor:,.2f}"


def build_report(resultado: ReadResult, aplicado: dict | None) -> str:
    linhas: list[str] = []
    add = linhas.append

    add("=" * 78)
    add("Gate AVH-4A — importacao de snapshot manual da Avoe")
    add("=" * 78)
    add(f"snapshot_id : {resultado.snapshot_id}")
    add(f"captured_at : {resultado.captured_at.isoformat()}")
    add(f"run_id      : {resultado.stats['run_id']}")
    add("")
    add("ARQUIVOS (allowlist, MANIFEST.sha256 e hash por arquivo conferidos)")
    for nome, f in sorted(resultado.files.items()):
        add(f"  {nome:<32} {f.row_count:>6} linhas  sha256={f.sha256[:16]}...")
    add("")

    add(f"A. METAS -> {TARGET_TABLE_TARGETS}")
    add(f"   lidas: {resultado.stats['targets_lidos']}  "
        f"elegiveis: {resultado.stats['targets_elegiveis']}")
    meses = sorted({r["ref_month"].isoformat() for r in resultado.target_rows})
    marcas = sorted({r["brand"] for r in resultado.target_rows})
    add(f"   competencias: {', '.join(meses) or '(nenhuma)'}")
    add(f"   marcas      : {', '.join(marcas) or '(nenhuma)'}")
    total_meta = sum((r["target_amount"] for r in resultado.target_rows), Decimal("0"))
    add(f"   soma das metas: {_fmt(total_meta)}  "
        f"(moeda BRL ASSUMIDA, nao confirmada pela fonte)")
    sem_key = [r["brand"] for r in resultado.target_rows if r["brand_key"] is None]
    if sem_key:
        add(f"   sem brand_key (sem regra de crosswalk): {', '.join(sorted(set(sem_key)))}")
    add("   coluna de realizado: NENHUMA — o campo `faturamento` da origem foi descartado")
    add("")

    add(f"B. CANAIS ADICIONAIS -> {TARGET_TABLE_CHANNELS}")
    add(f"   lidas: {resultado.stats['channels_lidos']}  "
        f"elegiveis (agregadas): {resultado.stats['channels_elegiveis']}")
    canais = sorted({r["channel"] for r in resultado.channel_rows})
    cmeses = sorted({r["ref_month"].isoformat() for r in resultado.channel_rows})
    cmarcas = sorted({r["brand"] for r in resultado.channel_rows})
    add(f"   canais      : {', '.join(canais) or '(nenhum)'}")
    add(f"   competencias: {', '.join(cmeses) or '(nenhuma)'}")
    add(f"   marcas      : {', '.join(cmarcas) or '(nenhuma)'}")
    nulos = sum(1 for r in resultado.channel_rows if r["reported_amount"] is None)
    parciais = sum(1 for r in resultado.channel_rows if r["coverage_status"] == "partial_month")
    add(f"   reported_amount NULL (indisponivel, nunca 0): {nulos}")
    add(f"   competencias com cobertura parcial: {parciais}")
    add("   is_proxy=TRUE  definition_status=unconfirmed  (por CHECK, nao por convencao)")
    add("   NAO e' GMV oficial e nao pode ser somado a TikTok / ML / Shopee")
    add("")

    add("   por canal x competencia:")
    agrupado: dict[tuple[str, str], list[dict]] = {}
    for r in resultado.channel_rows:
        agrupado.setdefault((r["channel"], r["ref_month"].isoformat()), []).append(r)
    for (canal, mes), grupo in sorted(agrupado.items()):
        soma = sum(
            (r["reported_amount"] for r in grupo if r["reported_amount"] is not None),
            Decimal("0"),
        )
        dias = max(r["days_covered"] for r in grupo)
        add(f"     {canal:<16} {mes}  marcas={len(grupo)}  dias_max={dias:>2}  "
            f"informado={_fmt(soma)}")
    add("")

    reais = [r for r in resultado.rejections if r.get("motivo") != "resumo de descartes"]
    resumo = [r for r in resultado.rejections if r.get("motivo") == "resumo de descartes"]
    add(f"REJEICOES: {len(reais)}")
    for r in reais[:20]:
        det = " ".join(f"{k}={v}" for k, v in r.items() if k != "dataset")
        add(f"  [{r['dataset']}] {det}")
    if len(reais) > 20:
        add(f"  ... e mais {len(reais) - 20}")
    for r in resumo:
        add(f"  [channels] oficiais ignorados={r['canais_oficiais_ignorados']}  "
            f"fora do regime diario={r['linhas_fora_do_regime_diario']}")
    add("")

    add("VERSAO CORRENTE (unidade = CAPTURA, por (source, ref_month); nunca imported_at)")
    correntes_t = select_current_version(resultado.target_rows)
    correntes_c = select_current_version(resultado.channel_rows)
    add(f"  metas correntes neste snapshot : {len(correntes_t)}")
    add(f"  canais correntes neste snapshot: {len(correntes_c)}")
    add("")

    add("ACOES")
    if aplicado is None:
        add(f"  seriam inseridas {len(resultado.target_rows)} linhas em {TARGET_TABLE_TARGETS}")
        add(f"  seriam inseridas {len(resultado.channel_rows)} linhas em {TARGET_TABLE_CHANNELS}")
        add("  nenhum UPDATE, nenhum DELETE (tabelas append-only)")
        add("  ESCRITA: nenhuma (sem --apply). Zero linha gravada.")
    else:
        add(f"  inseridas em {TARGET_TABLE_TARGETS}: {aplicado['targets_inserted']}")
        add(f"  inseridas em {TARGET_TABLE_CHANNELS}: {aplicado['channels_inserted']}")
        if aplicado.get("no_op"):
            add("  NO-OP idempotente: captura ja presente com o mesmo conteudo de negocio.")
        add(f"  sync_run_id: {aplicado.get('sync_run_id')}")
    add("=" * 78)
    return "\n".join(linhas)


# --------------------------------------------------------------------------
# Auditoria — conexao INDEPENDENTE, commit proprio (FINDING 9 / alternativa A)
# --------------------------------------------------------------------------

def _executa_mutacao_auditoria(audit_conn, mutacao: str, corpo):
    """Roda UMA mutacao de auditoria e classifica o desfecho observado.

    A classificacao vem da FASE em que a falha aconteceu, nunca so' do que o
    `rollback()` devolveu (Gate AVH-4A-H1-R3):

      fase `mutacao`  o commit ainda NAO foi tentado. Tenta-se o rollback:
                      se ele retorna, `revertida` (o conteudo anterior
                      permanece); se ele tambem levanta, `indeterminada`.

      fase `commit`   o commit FOI tentado. Se retornou, `confirmada`. Se
                      levantou, `indeterminada` SEMPRE — e nenhum rollback e'
                      tentado depois. E' a mesma regra da transacao de dados:
                      um rollback nao desfaz um commit que pode ter sido
                      aplicado, e o fato de ele retornar nao provaria nada
                      sobre a linha. Reclassificar para `revertida` ali seria
                      afirmar que a mutacao nao pegou, e isso nao se sabe.

    Levanta `MutacaoAuditoriaFalhou` com o `ResultadoAuditoria` classificado,
    sempre sem cadeia (ver `_levanta`).
    """
    fase = "mutacao"
    try:
        cur = audit_conn.cursor()
        try:
            valor = corpo(cur)
        finally:
            try:
                cur.close()
            except Exception:  # pragma: no cover — cursor morto nao muda nada
                pass
        fase = "commit"
        audit_conn.commit()
    except Exception as exc:
        detalhe = _sanitize_erro(exc)
        if fase == "commit":
            _levanta(MutacaoAuditoriaFalhou(ResultadoAuditoria(
                mutacao, AUDIT_INDETERMINADA,
                f"o commit da auditoria foi TENTADO e levantou: {detalhe}; "
                f"nenhum rollback foi tentado depois disso, porque ele nao "
                f"desfaria um commit possivelmente aplicado")))
        try:
            audit_conn.rollback()
        except Exception as exc_rollback:
            _levanta(MutacaoAuditoriaFalhou(ResultadoAuditoria(
                mutacao, AUDIT_INDETERMINADA,
                f"{detalhe}; e o rollback da propria auditoria tambem "
                f"levantou: {_sanitize_erro(exc_rollback)}")))
        _levanta(MutacaoAuditoriaFalhou(
            ResultadoAuditoria(mutacao, AUDIT_REVERTIDA, detalhe)))
    return valor


def audit_start(audit_conn, rows_extracted: int) -> int:
    def _corpo(cur):
        cur.execute(
            """
            INSERT INTO audit.source_sync_run
                (source_name, marketplace_id, loja_id, status, started_at, rows_extracted)
            VALUES (%s, NULL, NULL, 'running', NOW(), %s)
            RETURNING sync_run_id
            """,
            (SYNC_SOURCE_NAME, rows_extracted),
        )
        return cur.fetchone()["sync_run_id"]

    return _executa_mutacao_auditoria(audit_conn, "audit_start", _corpo)


def audit_finish(audit_conn, sync_run_id: int, status: str,
                 rows_loaded: int | None = None,
                 error_message: str | None = None) -> None:
    """Fecha o registro. Exige `rowcount == 1` e status validado."""
    if status not in STATUS_VALIDOS:
        _levanta(SnapshotImportError(f"status de auditoria invalido: {status!r}."))

    def _corpo(cur):
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
            raise SnapshotImportError(
                f"UPDATE de auditoria afetou {cur.rowcount} linhas, esperava 1."
            )
        return None

    _executa_mutacao_auditoria(audit_conn, "audit_finish", _corpo)


def audit_mark_indeterminate(audit_conn, sync_run_id: int, detalhe: str) -> None:
    """Commit de dados indeterminado: NAO marca failed.

    Mantem `running` e grava a mensagem. Afirmar `failed` seria afirmar que
    nada foi gravado, e isso nao se sabe.
    """
    def _corpo(cur):
        cur.execute(
            """
            UPDATE audit.source_sync_run
               SET error_message = %s
             WHERE sync_run_id = %s
            """,
            (f"INDETERMINADO: {detalhe}", sync_run_id),
        )
        if cur.rowcount != 1:
            raise SnapshotImportError(
                f"UPDATE de auditoria afetou {cur.rowcount} linhas, esperava 1."
            )
        return None

    _executa_mutacao_auditoria(audit_conn, "audit_mark_indeterminate", _corpo)


# --------------------------------------------------------------------------
# Escrita dos dados — somente com --apply
# --------------------------------------------------------------------------

def _get_neon_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SnapshotImportError(
            "DATABASE_URL nao definido: o destino dos snapshots e' o Neon."
        )
    return url


def _existing_business_rows(cur, tabela: str, colunas: tuple[str, ...], captured_at) -> set[tuple]:
    """Le SOMENTE as colunas de negocio/proveniencia da captura."""
    cur.execute(
        f"SELECT {', '.join(colunas)} FROM {tabela} "
        f"WHERE source = %s AND captured_at = %s",
        ("avoe_hub", captured_at),
    )
    return {tuple(r[c] for c in colunas) for r in cur.fetchall()}


def publish(neon_conn, resultado: ReadResult, execute_values=None) -> dict:
    """UMA transacao de dados: lock -> idempotencia -> INSERT -> verificacao.

    `execute_values` e' injetavel para permitir contraprova sem psycopg2
    instalado. Em producao ele e' importado tarde, DENTRO do try, para que uma
    falha de import tambem passe pelo rollback.
    """
    saida = {"targets_inserted": 0, "channels_inserted": 0, "no_op": False,
             "sync_run_id": None, "checks": {},
             "estado": PUBLICACAO_NAO_CONFIRMADA}
    cur = neon_conn.cursor()
    try:
        if execute_values is None:
            from psycopg2.extras import execute_values  # noqa: PLC0415 — import tardio

        cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))

        planejado = {
            TARGET_TABLE_TARGETS: (
                TARGET_BUSINESS_COLUMNS, TARGET_INSERT_COLUMNS,
                resultado.target_rows, "targets_inserted",
            ),
            TARGET_TABLE_CHANNELS: (
                CHANNEL_BUSINESS_COLUMNS, CHANNEL_INSERT_COLUMNS,
                resultado.channel_rows, "channels_inserted",
            ),
        }

        # FINDING 1 — equivalencia por colunas de NEGOCIO, sem import_run_id.
        ja_presente = 0
        for tabela, (bcols, _icols, linhas, _) in planejado.items():
            existentes = _existing_business_rows(cur, tabela, bcols, resultado.captured_at)
            if not existentes:
                continue
            ja_presente += 1
            novos = {tuple(r[c] for c in bcols) for r in linhas}
            if existentes != novos:
                raise SnapshotImportError(
                    f"{tabela}: captured_at ja presente com conteudo de negocio "
                    f"DIVERGENTE ({len(existentes)} linhas no destino contra "
                    f"{len(linhas)} na leitura). Tabela append-only: nada sera "
                    f"sobrescrito. Reimportar exige uma captura nova."
                )
        if ja_presente == len(planejado):
            saida["no_op"] = True
            # Mesma regra do caminho normal: o commit fica FORA do try que faz
            # rollback. Excecao aqui e' INDETERMINADA, nunca falha.
            _commit_ou_indeterminado(neon_conn, saida, no_op=True)
            return saida

        for tabela, (_bcols, icols, linhas, chave_saida) in planejado.items():
            if not linhas:
                continue
            execute_values(
                cur,
                f"INSERT INTO {tabela} ({', '.join(icols)}) VALUES %s",
                [tuple(r[c] for c in icols) for r in linhas],
                page_size=INSERT_PAGE_SIZE,
            )
            saida[chave_saida] = cur.rowcount

            cur.execute(
                f"SELECT count(*) AS n FROM {tabela} "
                f"WHERE source = %s AND captured_at = %s",
                ("avoe_hub", resultado.captured_at),
            )
            gravadas = cur.fetchone()["n"]
            if gravadas != len(linhas):
                raise SnapshotImportError(
                    f"{tabela}: {gravadas} linhas no destino para a captura "
                    f"contra {len(linhas)} lidas."
                )
            saida["checks"][tabela] = gravadas
    except PublicacaoIndeterminada:
        raise
    except Exception as exc:
        # Falha COMPROVADAMENTE anterior ao commit. O rollback e' correto, mas
        # ele proprio pode levantar: nesse caso o processo deixa de saber se a
        # transacao terminou, e o desfecho passa a ser outro estado.
        saida["estado"] = PUBLICACAO_NAO_CONFIRMADA
        try:
            neon_conn.rollback()
        except Exception as exc_rollback:
            # H1-R2 finding 1 — estado proprio. Nao dizer "rollback aplicado",
            # nao dizer "dados seguros", nao afirmar "nada gravado" sem
            # qualificar. Encerrar a conexao para forcar o fim da transacao.
            detalhe_rollback = _sanitize_erro(exc_rollback)
            try:
                neon_conn.close()
                encerrada = True
                nota_conexao = ("a conexao de dados foi encerrada para forcar o "
                                "fim da transacao")
            except Exception as exc_close:
                encerrada = False
                nota_conexao = (
                    f"e o encerramento da conexao tambem levantou "
                    f"({_sanitize_erro(exc_close)}), entao nem o fim da "
                    f"transacao foi observado")
            erro_rev = ReversaoNaoConfirmada(
                f"{_sanitize_erro(exc)} | o commit NUNCA foi tentado, mas o "
                f"rollback levantou ({detalhe_rollback}) e a REVERSAO NAO FOI "
                f"CONFIRMADA: {nota_conexao}. O estado final da transacao no "
                f"servidor nao foi observado. Reconcilie em leitura as duas "
                f"tabelas de snapshot para esta captura antes de qualquer nova "
                f"execucao. Nenhum retry sera feito."
            )
            erro_rev.conexao_encerrada = encerrada
            _levanta(erro_rev)
        erro = PublicacaoNaoConfirmada(
            f"{_sanitize_erro(exc)} | commit nunca tentado, nada publicado; "
            f"rollback aplicado e confirmado. Nenhum retry sera feito: corrija "
            f"a causa e rode de novo."
        )
        erro.rollback_confirmado = True
        _levanta(erro)
    finally:
        try:
            cur.close()
        except Exception:  # pragma: no cover — conexao ja encerrada
            pass

    # O commit fica FORA do try/except acima, de proposito. Uma excecao aqui
    # nao prova que o banco deixou de gravar, e um rollback depois dela nao
    # desfaz um commit que pode ter sido aplicado.
    _commit_ou_indeterminado(neon_conn, saida, no_op=False)
    return saida


def _neon_writable(url: str):
    import psycopg2  # noqa: PLC0415 — import tardio, so' com --apply
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    return psycopg2.connect(url, cursor_factory=RealDictCursor)


def rows_extracted_de(resultado: ReadResult) -> int:
    """FINDING 3 — extraidas = linhas LIDAS da origem, nao agregados elegiveis.

    O snapshot de referencia tem 19 linhas de metas e 4.818 diarias de canais:
    4.837 extraidas. As 31 saidas (7 metas + 24 agregados mensais) sao o que se
    CARREGA, e viram `rows_loaded`. Usar 31 nos dois campos escondia a razao de
    agregacao de 4.818 para 24.

    O run 285, ja publicado, foi gravado antes desta correcao e tem 31 nos dois
    campos. Esse registro historico NAO sera reescrito: e' o rastro do que
    aquela execucao declarou. Execucoes futuras usam a semantica daqui.
    """
    return int(resultado.stats["targets_lidos"]) + int(resultado.stats["channels_lidos"])


def apply_with_audit(neon_conn, audit_conn, resultado: ReadResult,
                     execute_values=None) -> dict:
    """Orquestra auditoria duravel + transacao de dados.

    Toda saida de erro daqui e' uma das classes tipadas da maquina de estados.
    Nenhuma excecao crua escapa, justamente para que a CLI nunca precise
    adivinhar o estado no handler generico:

      * `AuditoriaInicialIncompleta` -> `publish()` nem foi chamado;
      * `PublicacaoNaoConfirmada` -> auditoria `failed`, dados nao publicados;
      * `AuditoriaIncompletaSemPublicacao` -> idem, mas o `failed` nao gravou;
      * `ReversaoNaoConfirmada` -> commit nunca tentado, mas o rollback tambem
        nao retornou: o fim da transacao NAO foi observado;
      * `PublicacaoIndeterminada` -> auditoria fica `running` com nota
        INDETERMINADO; NUNCA `failed`, NUNCA `success`;
      * `PublicacaoIndeterminadaAuditoriaNaoConfirmada` -> idem, mas nem a nota
        esta garantida;
      * `AuditoriaIncompleta` -> publicacao confirmada, `success` nao gravou.

    Cada mensagem so' afirma o que foi observado. Quando uma mutacao de
    auditoria termina INDETERMINADA, o texto diz explicitamente que o conteudo
    da linha nao pode ser afirmado, em vez de chutar `running`.

    `KeyboardInterrupt` e `SystemExit` NAO sao capturados em ponto algum: eles
    sobem crus, porque interrupcao do operador nao e' um estado operacional da
    publicacao.
    """
    # FINDING 2 — falha aqui e' anterior a TUDO: nenhuma transacao de dados,
    # nenhum commit, nada a desfazer, e nenhum sync_run_id confiavel.
    try:
        sync_run_id = audit_start(audit_conn, rows_extracted_de(resultado))
    except Exception as exc:
        ra = _resultado_de(exc, "audit_start")
        erro = AuditoriaInicialIncompleta(
            f"auditoria inicial nao pode ser aberta: {ra.frase()}. "
            + _sobre_a_linha(
                ra,
                "A insercao foi revertida na propria conexao de auditoria: "
                "nenhuma linha de run foi criada.",
                "NAO se afirma se a linha de run existe, nem com que status.")
            + f" A PUBLICACAO NAO FOI TENTADA: nenhuma transacao de dados foi "
              f"iniciada, nenhum commit foi tentado e nada precisou ser "
              f"desfeito. Nada foi gravado nas tabelas de snapshot. Nenhum "
              f"retry sera feito."
        )
        erro.resultado_auditoria = ra
        _levanta(erro)

    try:
        aplicado = publish(neon_conn, resultado, execute_values=execute_values)
    except ReversaoNaoConfirmada as exc:
        # H1-R2 finding 1 — a transacao de dados nao teve fim observado. Marcar
        # `failed` afirmaria que ela terminou sem gravar nada; o unico registro
        # honesto e' a nota de indeterminacao.
        _levanta(_enriquece_reversao(audit_conn, sync_run_id, exc))
    except PublicacaoIndeterminada as exc:
        # Nao se sabe se gravou: o registro permanece `running`. Se a propria
        # marcacao falhar, o desfecho EXTERNO continua sendo indeterminado —
        # nunca `failed`, nunca `success`, nunca rollback.
        try:
            audit_mark_indeterminate(audit_conn, sync_run_id, _sanitize_erro(exc))
        except Exception as exc_audit:
            ra = _resultado_de(exc_audit, "audit_mark_indeterminate")
            erro = PublicacaoIndeterminadaAuditoriaNaoConfirmada(
                f"{exc} ALEM DISSO, o registro do estado indeterminado em "
                f"audit.source_sync_run TAMBEM falhou: {ra.frase()}. "
                + _sobre_a_linha(
                    ra,
                    "A nota INDETERMINADO foi revertida e NAO esta na linha",
                    "NAO se afirma se a nota INDETERMINADO ficou ou nao na linha")
                + f" sync_run_id={sync_run_id}, entao nem o rastro da auditoria "
                  f"pode ser assumido. Continua valendo: reconcilie em leitura "
                  f"e nao repita a importacao automaticamente."
            )
            erro.resultado_auditoria = ra
            _levanta(erro)
        exc.resultado_auditoria = ResultadoAuditoria(
            "audit_mark_indeterminate", AUDIT_CONFIRMADA)
        raise
    except Exception as exc:
        # Falha comprovadamente anterior ao commit, com reversao CONFIRMADA —
        # senao teria vindo como `ReversaoNaoConfirmada`.
        try:
            audit_finish(audit_conn, sync_run_id, "failed",
                         rows_loaded=0, error_message=_sanitize_erro(exc))
        except Exception as exc_audit:
            ra = _resultado_de(exc_audit, "audit_finish")
            erro = AuditoriaIncompletaSemPublicacao(
                f"{_sanitize_erro(exc)} | a transacao de dados foi REVERTIDA e "
                f"confirmada: o commit nunca foi tentado e nada foi publicado. "
                f"O que falhou tambem foi a auditoria: {ra.frase()}. "
                + _sobre_a_linha(
                    ra,
                    f"O UPDATE foi revertido na conexao de auditoria, entao o "
                    f"run sync_run_id={sync_run_id} permanece 'running'.",
                    f"NAO se afirma em que estado ficou o run "
                    f"sync_run_id={sync_run_id}.")
                + " Isto NAO e' commit indeterminado. Nenhum retry sera feito."
            )
            erro.rollback_confirmado = True
            erro.resultado_auditoria = ra
            _levanta(erro)
        exc.resultado_auditoria = ResultadoAuditoria("audit_finish", AUDIT_CONFIRMADA)
        raise

    # Estado 2: commit RETORNOU. Daqui para baixo a publicacao esta' confirmada
    # e nenhuma falha de auditoria pode ser descrita como reversao.
    aplicado["sync_run_id"] = sync_run_id
    carregadas = aplicado["targets_inserted"] + aplicado["channels_inserted"]
    nota = "no-op idempotente: captura ja presente" if aplicado["no_op"] else None
    feito = "no-op concluido" if aplicado["no_op"] else "dados publicados"
    try:
        audit_finish(audit_conn, sync_run_id, "success",
                     rows_loaded=carregadas, error_message=nota)
    except Exception as exc:
        ra = _resultado_de(exc, "audit_finish")
        erro = AuditoriaIncompleta(
            f"{feito} (commit confirmado), mas a auditoria nao pode ser "
            f"finalizada: {ra.frase()}. "
            + _sobre_a_linha(
                ra,
                f"O UPDATE foi revertido, entao o registro "
                f"sync_run_id={sync_run_id} permanece 'running'.",
                f"NAO se afirma em que estado ficou o registro "
                f"sync_run_id={sync_run_id}.")
            + " NAO repita automaticamente: reconcilie primeiro em leitura."
        )
        erro.resultado_auditoria = ra
        _levanta(erro)
    return aplicado


def _enriquece_reversao(audit_conn, sync_run_id: int,
                        exc: ReversaoNaoConfirmada) -> ReversaoNaoConfirmada:
    """Anota a reversao nao confirmada na auditoria, sem afirmar demais.

    `failed` esta proibido aqui: ele diria que a transacao terminou sem gravar
    nada, e o fim da transacao e' exatamente o que nao foi observado. A unica
    escrita admissivel e' a nota de indeterminacao — e ela tambem pode falhar.
    """
    try:
        audit_mark_indeterminate(
            audit_conn, sync_run_id,
            f"REVERSAO NAO CONFIRMADA (commit nunca tentado): {exc}")
    except Exception as exc_audit:
        ra = _resultado_de(exc_audit, "audit_mark_indeterminate")
        cauda = (
            f" A auditoria tambem nao ajudou: {ra.frase()}. "
            + _sobre_a_linha(
                ra,
                "A nota foi revertida e NAO esta na linha",
                "NAO se afirma se a nota ficou na linha")
            + f" sync_run_id={sync_run_id}."
        )
    else:
        ra = ResultadoAuditoria("audit_mark_indeterminate", AUDIT_CONFIRMADA)
        cauda = (f" A nota de reversao nao confirmada foi gravada em "
                 f"sync_run_id={sync_run_id}, que permanece 'running'.")
    erro = ReversaoNaoConfirmada(f"{exc}{cauda}")
    erro.conexao_encerrada = exc.conexao_encerrada
    erro.resultado_auditoria = ra
    return erro


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pipelines.avoe.snapshot_import",
        description=(
            "Importa snapshot manual da Avoe (metas mensais e faturamento "
            "informado de canais adicionais). Dry-run por padrao."
        ),
    )
    p.add_argument("--snapshot-dir", required=True, metavar="DIR",
                   help="Diretorio do snapshot, com MANIFEST.json e os JSONL.")
    p.add_argument("--run-id", default=None,
                   help="Identificador da execucao. Gerado se omitido.")
    p.add_argument("--apply", action="store_true",
                   help="Escreve no Neon. Sem esta flag, nada e' gravado.")
    return p


# FINDING 3 — a unica tabela de desfechos da CLI. Cada linha afirma SOMENTE o
# que o estado comprova. Subclasses vem antes das bases: o despacho e' por
# `isinstance` na ordem em que a tupla esta escrita.
#
# A CLI tem NOVE resultados, e so' nove: um sucesso (0), as sete falhas
# tipadas desta tabela e o fallback nao classificado (10). Os exits 2 e 3
# ficam FORA da maquina de estados — sao falha de contrato do snapshot e falha
# de credencial/conexao, ambas anteriores a qualquer transacao.
#
# A frase "rollback aplicado" NAO aparece em rotulo nenhum. Ela e' construida
# dentro de `publish()`, e so' quando o `rollback()` retornou de fato.
DESFECHOS: tuple[tuple[type, int, str], ...] = (
    (AuditoriaInicialIncompleta, 7,
     "AUDITORIA INICIAL INCOMPLETA (publicacao NAO tentada)"),
    (ReversaoNaoConfirmada, 11,
     "REVERSAO NAO CONFIRMADA (commit nunca tentado; fim da transacao nao observado)"),
    (PublicacaoIndeterminadaAuditoriaNaoConfirmada, 9,
     "PUBLICACAO INDETERMINADA (auditoria tambem NAO confirmada)"),
    (PublicacaoIndeterminada, 5,
     "PUBLICACAO INDETERMINADA"),
    (AuditoriaIncompletaSemPublicacao, 8,
     "PUBLICACAO NAO CONFIRMADA, AUDITORIA INCOMPLETA (nada publicado)"),
    (PublicacaoNaoConfirmada, 4,
     "FALHA NA PUBLICACAO (nada gravado)"),
    (AuditoriaIncompleta, 6,
     "AUDITORIA INCOMPLETA (publicacao confirmada)"),
)

# Ultimo recurso. Nao afirma rollback, nao afirma publicacao, nao afirma nada
# sobre a auditoria: diz exatamente que o estado nao foi classificado.
DESFECHO_NAO_CLASSIFICADO = (
    10, "ESTADO NAO CLASSIFICADO (nao se afirma publicacao, reversao nem auditoria)")


def _despacha_desfecho(exc: BaseException) -> tuple[int, str]:
    """Traduz a excecao no par (exit code, rotulo) da tabela `DESFECHOS`."""
    for classe, codigo, rotulo in DESFECHOS:
        if isinstance(exc, classe):
            return codigo, rotulo
    return DESFECHO_NAO_CLASSIFICADO


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = sanitize_run_id(args.run_id) if args.run_id else default_run_id()

    try:
        resultado = read_snapshot(Path(args.snapshot_dir), run_id=run_id)
    except SnapshotContractError as exc:
        print(f"FALHA DE CONTRATO: {exc}", file=sys.stderr)
        return 2

    aplicado = None
    if args.apply:
        try:
            url = _get_neon_url()
            neon_conn = _neon_writable(url)
            audit_conn = _neon_writable(url)
        except SnapshotImportError as exc:
            print(f"FALHA: {exc}", file=sys.stderr)
            return 3
        except Exception as exc:  # pragma: no cover — depende de ambiente
            print(f"FALHA ao conectar no destino: {_sanitize_erro(exc)}", file=sys.stderr)
            return 3
        try:
            aplicado = apply_with_audit(neon_conn, audit_conn, resultado)
        except SnapshotImportError as exc:
            codigo, rotulo = _despacha_desfecho(exc)
            print(f"{rotulo}: {exc}", file=sys.stderr)
            return codigo
        except Exception as exc:
            # FINDING 3 — o handler generico NAO pode afirmar rollback: a
            # excecao pode ter vindo da conexao de auditoria, e nao da de
            # dados. Aqui so' se declara ignorancia.
            codigo, rotulo = DESFECHO_NAO_CLASSIFICADO
            print(f"{rotulo}: {_sanitize_erro(exc)}", file=sys.stderr)
            return codigo
        finally:
            # A conexao de dados pode ja ter sido encerrada por `publish()` no
            # caminho de reversao nao confirmada; fechar de novo nao muda nada.
            for conexao in (neon_conn, audit_conn):
                try:
                    conexao.close()
                except Exception:  # pragma: no cover — conexao ja morta
                    pass

    print(build_report(resultado, aplicado))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
