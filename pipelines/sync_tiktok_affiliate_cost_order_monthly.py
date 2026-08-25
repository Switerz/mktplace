"""Sync de `marts.fact_tiktok_affiliate_cost_order_monthly` — Gate UE2-B.

Fonte     : `silver.stg_tiktok_payments_by_order` (Data Mart RDS, read-only)
Destino   : `marts.fact_tiktok_affiliate_cost_order_monthly` (Neon)
Grao      : `(ref_month, brand)` — ref_month = mes de `order_create_time`
Contrato  : docs/UNIT_ECONOMICS_SOURCE_CONTRACTS.md 18.8 (unica especificacao
            executavel; divergencia aqui e' defeito deste modulo)

O QUE ESTE MODULO MEDE — E O QUE NAO MEDE
-----------------------------------------
Mede custo de afiliado por coorte de PEDIDO: competencia COMERCIAL. NAO mede
repasse reconhecido, taxa financeira nem statement. As duas competencias nao sao
aproximaveis: a UE1-C mediu 24,6% das LINHAS com `statement_month` diferente do
mes do pedido (a fracao do VALOR nao foi calculada).

Nao publica `affiliate_cost_total`: qual subconjunto dos tres componentes
constitui "custo de afiliado" e' o ponto aberto P2. Nao publica retorno de
afiliado, receita atribuida nem margem.

SERIALIZACAO — LOCK DE SESSAO, ADQUIRIDO ANTES DE QUALQUER LEITURA DE ESTADO
---------------------------------------------------------------------------
Uma execucao com `--apply` adquire `pg_advisory_lock(ADVISORY_LOCK_KEY)` — lock de
SESSAO — numa conexao DEDICADA em autocommit, ANTES de qualquer leitura de estado,
abertura de snapshot ou acesso a dados. Somente depois do lock o watermark e'
lido. A transacao gravavel e' aberta muito depois: so quando a fonte ja foi lida
por inteiro e validada em memoria.

Dois defeitos foram corrigidos aqui, em ordem.

1. O desenho original lia o watermark numa conexao read-only separada e so tomava
   o lock na hora de publicar. Duas execucoes podiam ler o MESMO watermark, abrir
   fotografias diferentes e publicar em qualquer ordem — a mais ANTIGA podendo
   publicar por ultimo e sobrescrever o fato com dados velhos, enquanto o
   `ON CONFLICT` monotonico preservava o watermark NOVO. Watermark novo com fato
   antigo e' o pior estado possivel: o incremental seguinte nunca releria a janela
   perdida.

2. A primeira correcao usou `pg_advisory_xact_lock`, o que amarrou a exclusao
   mutua a uma transacao gravavel aberta desde o inicio. Essa transacao ficava
   OCIOSA durante toda a leitura da fonte, e o destino encerra sessoes ociosas em
   transacao (`idle_in_transaction_session_timeout` medido em 300 s). Reduzir
   `SOURCE_STATEMENT_TIMEOUT` nao resolveria: `statement_timeout` e' POR STATEMENT
   e a leitura da fonte sao sete consultas sequenciais — nao existia teto
   acumulado, apenas a aparencia de um.

Lock de sessao resolve os dois: sobrevive ao fim de qualquer transacao, vive numa
conexao que nunca fica em transacao, e continua excluindo mutuamente porque locks
consultivos de sessao e de transacao compartilham o MESMO espaco de chaves —
`pg_advisory_lock(K)` e `pg_advisory_xact_lock(K)` conflitam entre si, o que
tambem garante que versao antiga e nova nao se sobreponham durante o rollout.

Nenhuma protecao do destino foi desligada para isso: nada de
`idle_in_transaction_session_timeout` alterado, nada de `SET ... = 0`.

O lock e a publicacao vivem na MESMA sessao PostgreSQL. Enquanto a conexao esta
viva o lock permanece; se ela cai, o servidor libera o lock e o processo perde ao
mesmo tempo o unico caminho de escrita — nao existe segunda conexao gravavel neste
modulo. O `WHERE` monotonico e a releitura-com-comparacao
(`assert_watermark_unchanged`, com `FOR UPDATE`) ficam como defesa em profundidade,
nao como mecanismo principal: `FOR UPDATE` protege linha EXISTENTE, e na primeira
carga nao ha linha a travar.

Execucao sem `--apply` nao toma lock algum e nao abre transacao gravavel. Em
`full`, nao toca o destino nem para ler.

FOTOGRAFIA CONSISTENTE
----------------------
Um cutoff por `updated_at` sozinho NAO produz fotografia consistente. Entre a
descoberta das chaves tocadas e o recalculo integral, uma linha da mesma chave
pode receber `updated_at` acima do limite: o recalculo a excluiria, e o agregado
publicado perderia a contribuicao dela — internamente inconsistente, porque a
chave teria sido recalculada sobre um conjunto que nao corresponde a nenhum
instante real da fonte.

Por isso TODAS as leituras da fonte ocorrem dentro de UMA transacao `READ ONLY` +
`REPEATABLE READ`, e o modulo nao assume esse isolamento: ele o VERIFICA em
`assert_snapshot_session` e falha se o servidor nao o concedeu. A fotografia
permanece aberta ate a staging estar materializada E reconciliada.

INCREMENTAL RECALCULA A CHAVE INTEIRA
-------------------------------------
O incremental usa `updated_at` apenas para DESCOBRIR quais `(ref_month, brand)`
mudaram. O valor publicado vem de reler TODAS as transacoes daquela chave. Somar
apenas o delta seria errado: 34,0% das linhas recebem `updated_at` mais de 30
dias depois de `order_create_time`, e a revisao substitui o valor anterior em vez
de acrescentar a ele.

`updated_at` e' watermark TECNICO. Nunca competencia.

ESCOPO DA RECONCILIACAO
-----------------------
No incremental a staging contem SOMENTE as chaves tocadas, enquanto o destino
guarda toda a historia. Comparar staging contra o destino inteiro faria
`destino EXCEPT staging` encontrar, por construcao, todas as linhas historicas
nao tocadas — o criterio reprovaria sempre. Por isso o incremental reconcilia
contra a PROJECAO do destino restrita as chaves da staging, via semijoin por
`(ref_month, brand)`. O `full` continua comparando o conjunto inteiro.

FONTE COMPLETAMENTE VAZIA
-------------------------
`MAX(updated_at) IS NULL` NAO significa fonte vazia: pode ser tabela com linhas e
`updated_at` nulo. Os dois casos sao distinguidos por contagem explicita
(`capture_source_bounds`), e `updated_at` nulo em fonte nao vazia e' FALHA DE
CONTRATO, nao caso vazio.

Fonte de fato vazia:
  incremental -> FALHA. Nao se infere hard delete total de um incremental.
  full        -> esvazia o fato e REMOVE a linha do state, atomicamente. Ausencia
                 de watermark passa a ser representada por ausencia de linha, e a
                 proxima execucao incremental falha exigindo `full`. Nenhum
                 timestamp e' fabricado.

HARD DELETE
-----------
Linha removida da fonte nao tem `updated_at` para ser detectada: o incremental e'
estruturalmente cego a ela. O modo `full` repara, porque reconstroi o destino por
inteiro. Rodar `full` periodicamente nao e' otimizacao — e' requisito de correcao.

SEGURANCA
---------
Sem `--apply` nada e' escrito. Nenhuma credencial, DSN ou topologia entra em log:
mensagens de conexao sao CLASSIFICADAS em categoria fixa, nunca ecoadas. Nenhum
identificador individual (order_id, transaction_id, statement_id) e' impresso.
Nao agenda, nao dorme, nao repete e nao tenta de novo.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

# Allowlist OFICIAL de marcas, reutilizada do conector TikTok. Nao existe uma
# segunda lista aqui de proposito: uma copia divergiria da original no dia em que
# uma marca entrar ou sair.
from pipelines.connectors.tiktok.connector import BRANDS_IN_SCOPE

# Helpers de seguranca reutilizados do sync de serving, em vez de reimplementados.
# Duplicar as regexes de sanitizacao de erro garantiria que as duas copias
# divergissem — e a que ficasse atrasada vazaria topologia.
from pipelines.sync_tiktok_serving import (
    CONNECT_TIMEOUT_SECONDS,
    _get_datamart_url,
    _get_neon_url,
    sanitize_error_message,
    sanitize_run_id,
    validate_identifier,
    validate_qualified,
)

# ---------------------------------------------------------------------------
# Especificacao — LITERAL, fixa e versionada
# ---------------------------------------------------------------------------

SOURCE_TABLE = "silver.stg_tiktok_payments_by_order"
TARGET_TABLE = "marts.fact_tiktok_affiliate_cost_order_monthly"
SYNC_STATE_TABLE = "marts.fact_tiktok_affiliate_cost_order_monthly_sync_state"
STAGING_TABLE = "stg_ftacom_publish"

#: Unico `transaction_type` aceito. Qualquer outro valor — ou NULL — FALHA a
#: execucao (18.8.6). A allowlist e' validada ANTES de ser aplicada como filtro:
#: filtrar primeiro tornaria o guardrail inoperante, porque um tipo novo
#: simplesmente nao seria selecionado e passaria despercebido.
TRANSACTION_TYPE_ALLOWLIST = ("ORDER",)

#: Chaves de `fee_breakdown` que compoem cada coluna de negocio.
COMPONENT_JSON_KEYS = {
    "affiliate_creator_commission": "affiliate_commission_amount_before_pit",
    "affiliate_partner_commission": "affiliate_partner_commission_amount",
    "affiliate_ads_commission": "affiliate_ads_commission_amount",
}

#: Ordem canonica das colunas de negocio.
COMPONENT_COLUMNS = (
    "affiliate_creator_commission",
    "affiliate_partner_commission",
    "affiliate_ads_commission",
)

#: `affiliate_commission_amount` e `affiliate_commission_amount_before_pit` sao a
#: MESMA comissao antes e depois de PIT. Somar as duas conta o mesmo custo duas
#: vezes. Esta chave nao pode aparecer em nenhum SQL deste modulo, e
#: `assert_no_forbidden_component` prova isso em vez de confiar na revisao.
FORBIDDEN_JSON_KEYS = ("affiliate_commission_amount",)

#: Colunas materializadas na staging e no destino, fora as de auditoria.
BUSINESS_COLUMNS = ("ref_month", "brand") + COMPONENT_COLUMNS + (
    "source_row_count",
    "source_max_updated_at",
)

AUDIT_COLUMNS = ("source_run_id",)

#: Chave do grao, usada no semijoin de escopo da reconciliacao incremental.
KEY_COLUMNS = ("ref_month", "brand")

#: Advisory lock EXCLUSIVO deste destino. Duas execucoes deste sync nunca se
#: sobrepoem; outros syncs (907/908 no serving) nao sao bloqueados.
ADVISORY_LOCK_KEY = 912_120_012

#: Teto POR STATEMENT da fonte. Protege a FONTE de uma consulta degenerada; nao
#: tem, e nunca teve, relacao com o timeout do destino. `statement_timeout` e'
#: por statement, e `read_source_snapshot` executa sete consultas sequenciais —
#: reduzir esta constante NAO produziria teto acumulado. Depois de o lock virar
#: de sessao (18.8.9) a leitura da fonte deixou de manter transacao aberta no
#: destino, e este valor voltou a ser exclusivamente um limite da fonte.
SOURCE_STATEMENT_TIMEOUT = "600s"
TARGET_STATEMENT_TIMEOUT = "300s"

#: Espera maxima para adquirir o advisory lock. Finito e fail-fast: `lock_timeout`
#: aborta a espera e o erro sobe. Zero loop, zero retry, zero sleep, zero backoff.
LOCK_TIMEOUT = "30s"
INSERT_PAGE_SIZE = 1000

_RUN_ID_PREFIX = "sync_tiktok_affiliate_cost_order_monthly"

#: Resultados possiveis de `advance_watermark`.
WATERMARK_ADVANCED = "avancado"
WATERMARK_UNCHANGED = "inalterado"

#: Busca a chave proibida na sua forma CITADA. Sem as aspas de fechamento o
#: padrao casaria com `affiliate_commission_amount_before_pit`, que e' legitimo,
#: e o guardrail acusaria falso positivo em toda execucao.
_FORBIDDEN_QUOTED_RE = tuple(
    re.compile(r"'" + re.escape(k) + r"'") for k in FORBIDDEN_JSON_KEYS
)


@dataclass(frozen=True)
class SourceSnapshot:
    """Resultado de UMA fotografia da fonte. Tudo aqui vem do mesmo snapshot."""

    #: `max(updated_at)` capturado DENTRO da fotografia. None SOMENTE quando a
    #: fonte esta comprovadamente vazia (`source_empty`), nunca fabricado.
    cutoff: datetime | None
    #: Limite inferior usado. None em `full` (le a historia inteira).
    lower_bound: datetime | None
    #: Uma linha por `(ref_month, brand)`, ja recalculada por inteiro.
    rows: list[dict]
    #: Totais globais sobre o DETALHE, para a fronteira B.8/B.13.
    detail_totals: dict
    #: Tipos observados na janela, antes de qualquer filtro comercial.
    observed_types: dict
    #: True SOMENTE quando `COUNT(*) = 0` foi medido com sucesso na fonte.
    source_empty: bool = False
    #: Contagens brutas da fonte, prova positiva do caso vazio (18.8.6 / F5).
    source_bounds: dict = field(default_factory=dict)
    #: Provas da fronteira A, para o relatorio.
    boundary_a: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Guardrails estaticos
# ---------------------------------------------------------------------------

def assert_no_forbidden_component(sql: str) -> None:
    """Falha se um SQL deste modulo referenciar chave proibida.

    Chamada em toda montagem de SQL que toca `fee_breakdown`. Existe porque
    "somar `affiliate_commission_amount` junto de `..._before_pit`" e' o erro
    mais facil de cometer numa edicao futura e o mais dificil de notar depois:
    o resultado nao quebra, so fica com o custo de criador dobrado.
    """
    for padrao in _FORBIDDEN_QUOTED_RE:
        if padrao.search(sql):
            raise RuntimeError(
                "SQL referencia chave de fee_breakdown proibida "
                f"({padrao.pattern}): ela e' a mesma comissao antes/depois de PIT "
                "e somar junto de affiliate_commission_amount_before_pit contaria "
                "o mesmo custo duas vezes."
            )


def _component_sql() -> str:
    """Projecao dos tres componentes. `SUM` preserva a semantica de nulo exigida
    pelo contrato: ignora nulos e devolve NULL quando TODAS as linhas sao nulas —
    nunca 0. `COALESCE(...,0)` aqui inventaria medicao.

    `->>` devolve NULL tanto para chave ausente quanto para JSON null; ambos
    significam "chave indisponivel", que e' o mesmo caso.

    Sem `abs()`: o sinal vem da fonte e e' publicado como veio.
    """
    partes = []
    for coluna in COMPONENT_COLUMNS:
        chave = COMPONENT_JSON_KEYS[coluna]
        partes.append(
            f"SUM((fee_breakdown->>'{chave}')::numeric) AS {validate_identifier(coluna)}"
        )
    sql = ",\n               ".join(partes)
    assert_no_forbidden_component(sql)
    return sql


def _filtro_populacao() -> str:
    """Filtro comercial: marca allowlisted + `transaction_type` allowlisted +
    dentro da fotografia. Aplicado SOMENTE depois de `validate_transaction_types`.
    """
    return (
        "brand = ANY(%(brands)s)\n"
        "          AND transaction_type = ANY(%(types)s)\n"
        "          AND updated_at <= %(cutoff)s"
    )


# ---------------------------------------------------------------------------
# Conexoes
# ---------------------------------------------------------------------------

def _datamart_snapshot(url: str):
    """Sessao de LEITURA no Data Mart com snapshot consistente.

    `readonly=True` garante que Raw/Silver/Gold nao possam ser escritas nem por
    acidente. `REPEATABLE READ` e' o que torna a fotografia consistente.
    `autocommit=False` e' explicito: com autocommit ligado cada statement abriria
    sua propria transacao e o snapshot nao existiria.
    """
    conn = psycopg2.connect(
        url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS
    )
    conn.set_session(
        isolation_level="REPEATABLE READ", readonly=True, autocommit=False
    )
    return conn


def _neon_session(url: str):
    """A UNICA conexao com o destino no caminho `--apply`. Comeca em autocommit.

    Sessao unica — e nao duas — e' o que fecha a janela de concorrencia. O
    advisory lock pertence a SESSAO: se a publicacao rodasse em outra conexao,
    seria possivel perder o lock numa (queda) e continuar escrevendo pela outra.
    Com sessao unica isso deixa de existir: se a conexao cair, perde-se o lock E
    o unico caminho de escrita ao mesmo tempo, e a proxima operacao falha.

    Comeca com `autocommit = True`: em autocommit o psycopg2 nao abre transacao
    implicita, entao a sessao nao fica em `idle in transaction` enquanto a fonte
    e' lida, nem exposta ao `idle_in_transaction_session_timeout` do destino
    (medido em 300 s). Depois de o snapshot estar lido e validado, `_run_apply`
    desliga o autocommit NESTA MESMA conexao para abrir a transacao gravavel.

    NAO existe segunda fabrica de conexao gravavel neste modulo, de proposito:
    sem funcao para abri-la, o defeito nao volta por descuido.
    """
    conn = psycopg2.connect(
        url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS
    )
    conn.autocommit = True
    return conn


def _neon_readonly(url: str):
    """Somente para diagnostico (sem `--apply`). Nao toma lock e nao escreve."""
    conn = psycopg2.connect(
        url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS
    )
    conn.set_session(readonly=True)
    return conn


def default_run_id(mode: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return sanitize_run_id(f"{_RUN_ID_PREFIX}:{mode}:{stamp}")


# ---------------------------------------------------------------------------
# Fronteira A — fonte x contrato de entrada, dentro da fotografia
# ---------------------------------------------------------------------------

def assert_snapshot_session(cur) -> dict:
    """Fronteira A.1 — PROVA que a sessao concede o snapshot, em vez de assumir.

    Isolamento e' propriedade da transacao. Se a replica de leitura, um pooler ou
    um `default_transaction_isolation` do servidor rebaixar o nivel, o desenho
    inteiro deixa de valer silenciosamente. Aqui ele falha alto.
    """
    cur.execute(
        "SELECT current_setting('transaction_isolation') AS isolation, "
        "current_setting('transaction_read_only') AS read_only"
    )
    linha = cur.fetchone()
    isolamento = str(linha["isolation"]).lower()
    somente_leitura = str(linha["read_only"]).lower()
    if isolamento != "repeatable read":
        raise RuntimeError(
            "fotografia inconsistente: a transacao da fonte esta em isolamento "
            f"'{isolamento}', e o contrato exige 'repeatable read'. Sem snapshot "
            "consistente o recalculo pode perder a contribuicao de uma linha."
        )
    if somente_leitura != "on":
        raise RuntimeError(
            "transacao da fonte NAO esta read-only: a leitura do Data Mart nunca "
            "pode ter permissao de escrita."
        )
    return {"isolation": isolamento, "read_only": somente_leitura}


def capture_source_bounds(cur) -> dict:
    """Fronteira A.2 + F5 — contagem e teto na MESMA consulta e fotografia.

    `MAX(updated_at) IS NULL` e' AMBIGUO: pode ser tabela vazia ou tabela com
    linhas e `updated_at` nulo. Tratar os dois como "fonte vazia" seria
    catastrofico no `full`, que esvazia o destino quando a fonte esta vazia — uma
    coluna de watermark momentaneamente nula apagaria o fato inteiro.

    Por isso os tres numeros vem juntos, e a decisao e' explicita:
      total = 0                       -> fonte comprovadamente vazia
      total > 0 e nao_nulos < total    -> FALHA de contrato
      total > 0 e max nulo             -> FALHA de contrato
      total > 0, todos nao nulos, max  -> segue

    Sem filtro de marca nem de tipo, de proposito: este e' o teto real do
    snapshot, e e' contra ele que a janela de validacao de tipos e' definida. Um
    teto filtrado deixaria linhas de tipo desconhecido permanentemente acima do
    limite, nunca validadas.
    """
    cur.execute(f"""
        SELECT COUNT(*)              AS total,
               COUNT(updated_at)     AS com_updated_at,
               MAX(updated_at)       AS max_updated_at
        FROM {validate_qualified(SOURCE_TABLE)}
    """)
    linha = dict(cur.fetchone())
    total = int(linha["total"])
    com = int(linha["com_updated_at"])
    mx = linha["max_updated_at"]

    if total == 0:
        # Prova positiva: a consulta EXECUTOU e devolveu zero. Timeout, erro de
        # permissao ou falha de conexao levantam excecao e nunca chegam aqui.
        return {"total": 0, "com_updated_at": 0, "max_updated_at": None,
                "empty": True}
    if com < total:
        raise RuntimeError(
            f"contrato da fonte violado: {total - com} de {total} linha(s) com "
            "updated_at NULO. O watermark tecnico nao pode ter nulo — sem ele a "
            "linha e' invisivel ao incremental e some do fato em silencio. "
            "Execucao abortada sem escrita."
        )
    if mx is None:
        raise RuntimeError(
            f"contrato da fonte violado: {total} linha(s) presentes mas "
            "MAX(updated_at) e NULO. Isto NAO e fonte vazia e nao pode ser "
            "tratado como tal. Execucao abortada sem escrita."
        )
    return {"total": total, "com_updated_at": com, "max_updated_at": mx,
            "empty": False}


def validate_transaction_types(cur, lower_bound: datetime | None,
                               cutoff: datetime) -> dict:
    """Fronteira A.3 / 18.8.6 — validar ANTES de filtrar.

    O desenho anterior filtrava `transaction_type IN ('ORDER')` dentro da propria
    descoberta de chaves, o que tornava o guardrail inoperante: um tipo novo nao
    seria selecionado e passaria despercebido — exatamente o que o guardrail
    deveria impedir.

    Sem filtro de marca: a leitura literal do contrato. Consequencia operacional
    aceita: um tipo novo em marca fora de escopo tambem FALHA a execucao. E' o
    lado seguro — significa que o entendimento da fonte esta desatualizado.

    Nao imprime identificador individual: so o valor do tipo e a contagem.
    """
    sql = f"""
        SELECT transaction_type, COUNT(*) AS n
        FROM {validate_qualified(SOURCE_TABLE)}
        WHERE updated_at <= %(cutoff)s
          AND (%(lower_bound)s::timestamp IS NULL OR updated_at >= %(lower_bound)s)
        GROUP BY transaction_type
    """
    cur.execute(sql, {"cutoff": cutoff, "lower_bound": lower_bound})
    observados = {
        (linha["transaction_type"] if linha["transaction_type"] is not None else "<NULL>"):
            int(linha["n"])
        for linha in cur.fetchall()
    }
    permitidos = set(TRANSACTION_TYPE_ALLOWLIST)
    inesperados = {k: v for k, v in observados.items() if k not in permitidos}
    if inesperados:
        detalhe = "; ".join(f"{k}={v}" for k, v in sorted(inesperados.items()))
        raise RuntimeError(
            "transaction_type fora da allowlist na janela lida: "
            f"{detalhe}. Allowlist={sorted(permitidos)}. A execucao FALHA e o "
            "watermark NAO avanca: um tipo desconhecido pode nao ser custo de "
            "afiliado, e incluir ou excluir por conta propria seria inventar "
            "semantica."
        )
    return observados


def validate_read_population(cur, cutoff: datetime) -> dict:
    """Fronteiras A.4 a A.7 sobre a populacao que sera agregada.

    Roda depois da validacao de tipos e com o filtro comercial aplicado. Se
    `order_create_time` fosse nulo aqui, `ref_month` sairia nulo e violaria o
    `NOT NULL` do destino — a fronteira A tem de barrar antes.

    NAO recebe `lower_bound` de proposito: valida a populacao allowlisted INTEIRA
    dentro da fotografia, que e' superconjunto do que sera recalculado (o
    recalculo le a historia completa das chaves tocadas, sem limite inferior).
    Validar o superconjunto e' mais forte e faz `full` e `incremental` provarem a
    mesma coisa. O custo e' que um nulo numa chave nao tocada tambem reprova a
    execucao — aceito: esse nulo reprovaria a chave assim que ela fosse tocada, e
    falhar cedo e' melhor que falhar depois.
    """
    sql = f"""
        SELECT COUNT(*)                                            AS lidas,
               COUNT(*) FILTER (WHERE transaction_id   IS NULL)    AS nulo_transaction_id,
               COUNT(*) FILTER (WHERE order_create_time IS NULL)   AS nulo_order_create_time,
               COUNT(*) FILTER (WHERE brand            IS NULL)    AS nulo_brand,
               COUNT(*) FILTER (WHERE fee_breakdown    IS NULL)    AS nulo_fee_breakdown,
               COUNT(*) FILTER (WHERE updated_at > %(cutoff)s)     AS fora_da_fotografia,
               COUNT(DISTINCT transaction_id)                      AS transaction_ids_distintos,
               COUNT(DISTINCT brand)                               AS marcas_distintas
        FROM {validate_qualified(SOURCE_TABLE)}
        WHERE {_filtro_populacao()}
    """
    cur.execute(sql, _params(None, cutoff))
    linha = dict(cur.fetchone())

    problemas = []
    for campo in ("transaction_id", "order_create_time", "brand", "fee_breakdown"):
        n = int(linha[f"nulo_{campo}"])
        if n:
            problemas.append(f"{campo} nulo em {n} linha(s)")
    if int(linha["fora_da_fotografia"]):
        # A.7 — nao deveria ser possivel: o filtro tem `updated_at <= cutoff`.
        # Se acusar, o snapshot nao e' o que se acredita.
        problemas.append(
            f"{linha['fora_da_fotografia']} linha(s) com updated_at acima do cutoff"
        )
    lidas = int(linha["lidas"])
    distintos = int(linha["transaction_ids_distintos"])
    if lidas != distintos:
        # A.6 — grao da fonte. Duplicidade aqui dobraria o custo agregado.
        problemas.append(
            f"transaction_id nao e unico: {lidas} linha(s) para {distintos} id(s)"
        )
    if problemas:
        raise RuntimeError("fronteira A reprovou: " + "; ".join(problemas))
    return linha


def discover_touched_keys(cur, lower_bound: datetime | None,
                          cutoff: datetime) -> list[tuple[date, str]]:
    """Chaves `(ref_month, brand)` tocadas na janela. Filtro comercial aplicado
    aqui — depois da validacao de tipos, nunca dentro dela.

    Em `full` (`lower_bound is None`) devolve TODAS as chaves da fonte, e nao um
    subconjunto: e' o que permite ao `full` remover chave que sofreu hard delete.
    """
    sql = f"""
        SELECT DISTINCT DATE_TRUNC('month', order_create_time)::date AS ref_month,
                        brand
        FROM {validate_qualified(SOURCE_TABLE)}
        WHERE {_filtro_populacao()}
          AND (%(lower_bound)s::timestamp IS NULL OR updated_at >= %(lower_bound)s)
        ORDER BY ref_month, brand
    """
    cur.execute(sql, _params(lower_bound, cutoff))
    return [(linha["ref_month"], linha["brand"]) for linha in cur.fetchall()]


def recompute_keys(cur, keys: list[tuple[date, str]], cutoff: datetime) -> list[dict]:
    """Recalculo INTEGRAL das chaves tocadas.

    Nao ha `lower_bound` nesta consulta, e a ausencia e' o ponto: le TODAS as
    transacoes de cada chave dentro da fotografia, nao apenas as alteradas na
    janela. Somar o delta produziria valor errado, porque revisao substitui o
    valor anterior em vez de acrescentar a ele.
    """
    if not keys:
        return []
    meses = [k[0] for k in keys]
    marcas = [k[1] for k in keys]
    sql = f"""
        SELECT DATE_TRUNC('month', order_create_time)::date AS ref_month,
               brand,
               {_component_sql()},
               COUNT(*)        AS source_row_count,
               MAX(updated_at) AS source_max_updated_at
        FROM {validate_qualified(SOURCE_TABLE)}
        WHERE {_filtro_populacao()}
          AND (DATE_TRUNC('month', order_create_time)::date, brand) IN (
                SELECT * FROM unnest(%(meses)s::date[], %(marcas)s::text[])
              )
        GROUP BY ref_month, brand
        ORDER BY ref_month, brand
    """
    assert_no_forbidden_component(sql)
    params = _params(None, cutoff)
    params.update({"meses": meses, "marcas": marcas})
    cur.execute(sql, params)
    return [dict(linha) for linha in cur.fetchall()]


def detail_totals(cur, keys: list[tuple[date, str]], cutoff: datetime) -> dict:
    """Totais globais sobre o DETALHE, sem `GROUP BY`.

    Comparados na fronteira B contra a soma das linhas agregadas. Uma chave
    perdida, duplicada ou contada duas vezes no `GROUP BY` aparece aqui — o que a
    reconciliacao agregado-contra-agregado nao pegaria.
    """
    if not keys:
        return _totais_vazios()
    meses = [k[0] for k in keys]
    marcas = [k[1] for k in keys]
    sql = f"""
        SELECT {_component_sql()},
               COUNT(*) AS source_row_count
        FROM {validate_qualified(SOURCE_TABLE)}
        WHERE {_filtro_populacao()}
          AND (DATE_TRUNC('month', order_create_time)::date, brand) IN (
                SELECT * FROM unnest(%(meses)s::date[], %(marcas)s::text[])
              )
    """
    assert_no_forbidden_component(sql)
    params = _params(None, cutoff)
    params.update({"meses": meses, "marcas": marcas})
    cur.execute(sql, params)
    return dict(cur.fetchone())


def _totais_vazios() -> dict:
    return {c: None for c in COMPONENT_COLUMNS} | {"source_row_count": 0}


def _params(lower_bound: datetime | None, cutoff: datetime) -> dict:
    return {
        "brands": list(BRANDS_IN_SCOPE),
        "types": list(TRANSACTION_TYPE_ALLOWLIST),
        "cutoff": cutoff,
        "lower_bound": lower_bound,
    }


def read_source_snapshot(datamart_conn, lower_bound: datetime | None) -> SourceSnapshot:
    """UMA transacao, UM snapshot, na ordem exigida pela 18.8.3.

    A transacao e' encerrada apenas pelo chamador, e somente depois de a staging
    estar materializada e reconciliada: nenhuma consulta em `READ COMMITTED` pode
    compor o conjunto, porque misturar niveis de isolamento reintroduz o defeito
    original com outra roupa.
    """
    cur = datamart_conn.cursor()
    try:
        cur.execute(f"SET LOCAL statement_timeout = '{SOURCE_STATEMENT_TIMEOUT}'")
        sessao = assert_snapshot_session(cur)                        # A.1
        bounds = capture_source_bounds(cur)                          # A.2 + F5
        if bounds["empty"]:
            return SourceSnapshot(
                cutoff=None, lower_bound=lower_bound, rows=[],
                detail_totals=_totais_vazios(), observed_types={},
                source_empty=True, source_bounds=bounds,
                boundary_a={"sessao": sessao, "source_bounds": bounds},
            )
        cutoff = bounds["max_updated_at"]
        tipos = validate_transaction_types(cur, lower_bound, cutoff)  # A.3 (antes)
        populacao = validate_read_population(cur, cutoff)             # A.4-A.7
        keys = discover_touched_keys(cur, lower_bound, cutoff)
        rows = recompute_keys(cur, keys, cutoff)
        totais = detail_totals(cur, keys, cutoff)

        _assert_brands_in_scope(rows)                                 # A.4, defesa
        _assert_rows_within_cutoff(rows, cutoff)                      # A.7
        return SourceSnapshot(
            cutoff=cutoff,
            lower_bound=lower_bound,
            rows=rows,
            detail_totals=totais,
            observed_types=tipos,
            source_empty=False,
            source_bounds=bounds,
            boundary_a={
                "sessao": sessao,
                "source_bounds": bounds,
                "cutoff": cutoff,
                "lower_bound": lower_bound,
                "tipos_observados": tipos,
                "linhas_lidas": int(populacao["lidas"]),
                "transaction_ids_distintos": int(populacao["transaction_ids_distintos"]),
                "marcas_distintas": int(populacao["marcas_distintas"]),
                "chaves_tocadas": len(keys),
            },
        )
    finally:
        cur.close()


def _assert_brands_in_scope(rows: list[dict]) -> None:
    permitidas = set(BRANDS_IN_SCOPE)
    fora = sorted({r["brand"] for r in rows} - permitidas)
    if fora:
        raise RuntimeError(
            f"marca fora de BRANDS_IN_SCOPE no conjunto agregado: {fora}"
        )


def _assert_rows_within_cutoff(rows: list[dict], cutoff: datetime) -> None:
    for r in rows:
        mx = r.get("source_max_updated_at")
        if mx is not None and mx > cutoff:
            raise RuntimeError(
                "linha agregada com source_max_updated_at acima do cutoff: a "
                "leitura escapou da fotografia."
            )


# ---------------------------------------------------------------------------
# Fronteira B — fonte detalhada x staging agregada
# ---------------------------------------------------------------------------

def _sum_signed(valores: list) -> Decimal | None:
    """Espelha `SUM` do Postgres: ignora nulos, devolve None se TODOS forem nulos.

    Nao usa `sum(..., 0)`: o zero inicial transformaria "componente ausente em
    toda a chave" em "custo medido igual a zero", que e' afirmacao diferente.
    """
    presentes = [v for v in valores if v is not None]
    if not presentes:
        return None
    total = Decimal(0)
    for v in presentes:
        total += Decimal(v)
    return total


def _assert_no_nan(rows: list[dict]) -> None:
    """NaN sobrevive a qualquer comparacao de ordem em Postgres e passaria por um
    `CHECK >= 0`. O destino tem `CHECK (<> 'NaN')`, mas falhar aqui da mensagem
    util em vez de violacao de constraint.
    """
    for r in rows:
        for c in COMPONENT_COLUMNS:
            v = r.get(c)
            if v is not None and Decimal(v).is_nan():
                raise RuntimeError(
                    f"NaN em {c} para ref_month={r['ref_month']}: valor nao "
                    "publicavel, e converter para zero inventaria medicao."
                )


def reconcile_detail_vs_aggregate(rows: list[dict], totais: dict) -> dict:
    """Fronteira B.8, B.9 e B.13 — detalhe contra agregado."""
    problemas = []
    provas = {}
    for c in COMPONENT_COLUMNS:
        esperado = totais.get(c)
        obtido = _sum_signed([r.get(c) for r in rows])
        esperado_d = None if esperado is None else Decimal(esperado)
        if esperado_d != obtido:
            problemas.append(f"{c}: detalhe={esperado_d} agregado={obtido}")
        provas[c] = str(obtido)
    esperado_n = int(totais.get("source_row_count") or 0)
    obtido_n = sum(int(r["source_row_count"] or 0) for r in rows)
    if esperado_n != obtido_n:
        problemas.append(f"source_row_count: detalhe={esperado_n} agregado={obtido_n}")
    provas["source_row_count"] = obtido_n
    if problemas:
        raise RuntimeError("fronteira B reprovou: " + "; ".join(problemas))
    return provas


def assert_staging_within_cutoff(cur, cutoff: datetime) -> datetime | None:
    """Fronteira B.12 — cutoff efetivamente utilizado.

    Igualdade estrita com o cutoff NAO e' invariante, e a diferenca e' legitima:
    o cutoff e' o teto do snapshot sobre a tabela INTEIRA, enquanto a staging
    cobre apenas marcas allowlisted e `transaction_type` allowlisted. Se a linha
    que detem o `max(updated_at)` global pertencer a uma marca fora de escopo, a
    staging fica legitimamente abaixo do cutoff.

    O que E' invariante — e o que de fato prova a fotografia — e' que nada na
    staging esta ACIMA do cutoff. Estar acima significaria leitura fora do
    snapshot. Divergencia registrada na 18.9 do contrato.
    """
    cur.execute(
        f"SELECT MAX(source_max_updated_at) AS mx FROM {validate_identifier(STAGING_TABLE)}"
    )
    mx = cur.fetchone()["mx"]
    if mx is not None and mx > cutoff:
        raise RuntimeError(
            "staging contem updated_at acima do cutoff da fotografia: a leitura "
            "escapou do snapshot."
        )
    return mx


# ---------------------------------------------------------------------------
# Fronteira C — staging x destino, no escopo correto
# ---------------------------------------------------------------------------

def _business_cols_sql() -> str:
    return ", ".join(validate_identifier(c) for c in BUSINESS_COLUMNS)


def _key_join_on(a: str, b: str) -> str:
    return " AND ".join(
        f"{a}.{validate_identifier(k)} = {b}.{validate_identifier(k)}"
        for k in KEY_COLUMNS
    )


def target_scope(mode: str, staging: str, target: str) -> str:
    """Projecao do destino contra a qual a staging e' reconciliada.

    `full`        -> o destino INTEIRO. A staging cobre todas as chaves, entao
                     qualquer linha sobrando no destino e' defeito.
    `incremental` -> SOMENTE as chaves presentes na staging, via semijoin por
                     `(ref_month, brand)`.

    Sem esse escopo o incremental era irreparavel: depois do DELETE+INSERT das
    chaves tocadas o destino continua com todos os outros meses, e
    `destino EXCEPT staging` encontraria, por construcao, cada linha historica
    nao tocada. O criterio reprovaria toda execucao correta.

    O semijoin usa a propria staging como fonte das chaves — nada de lista de
    valores interpolada no texto do SQL.
    """
    if mode == "full":
        return validate_qualified(target)
    return (
        f"(SELECT t.* FROM {validate_qualified(target)} t "
        f"JOIN {validate_identifier(staging)} s ON {_key_join_on('t', 's')})"
    )


def except_both_ways(cur, staging: str, escopo: str) -> tuple[int, int]:
    """Fronteira C.15 — `EXCEPT` bidirecional nas colunas de NEGOCIO.

    `synced_at`/`source_run_id` ficam fora: sao gerados no destino e sempre
    difeririam, transformando a comparacao em ruido em vez de prova.

    `escopo` vem de `target_scope`: destino inteiro no `full`, projecao pelas
    chaves da staging no `incremental`.
    """
    cols = _business_cols_sql()
    cur.execute(
        f"SELECT COUNT(*) AS n FROM "
        f"(SELECT {cols} FROM {staging} "
        f"EXCEPT SELECT {cols} FROM {escopo} AS escopo_a) x"
    )
    staging_menos_destino = int(cur.fetchone()["n"])
    cur.execute(
        f"SELECT COUNT(*) AS n FROM "
        f"(SELECT {cols} FROM {escopo} AS escopo_b "
        f"EXCEPT SELECT {cols} FROM {staging}) x"
    )
    destino_menos_staging = int(cur.fetchone()["n"])
    return staging_menos_destino, destino_menos_staging


def assert_signs_preserved(cur, staging: str, target: str) -> None:
    """Fronteira C.18 — sinal preservado entre staging e destino.

    Implicado pela igualdade exata, mas verificado a parte porque a inversao de
    sinal e' o defeito de maior consequencia deste pipeline: um credito
    apresentado como custo muda a decisao de negocio, e passaria por qualquer
    checagem que comparasse apenas modulos. `SIGN(NULL)` e' NULL, e
    `IS DISTINCT FROM` trata isso sem converter nulo em zero.

    O `JOIN` pelas chaves da staging JA restringe o escopo corretamente nos dois
    modos — no `full` a staging cobre tudo; no `incremental`, so as chaves
    tocadas. Nao precisa de `target_scope`.
    """
    condicoes = " OR ".join(
        f"SIGN(s.{validate_identifier(c)}) IS DISTINCT FROM SIGN(t.{validate_identifier(c)})"
        for c in COMPONENT_COLUMNS
    )
    cur.execute(f"""
        SELECT COUNT(*) AS n
        FROM {staging} s
        JOIN {validate_qualified(target)} t ON {_key_join_on('t', 's')}
        WHERE {condicoes}
    """)
    n = int(cur.fetchone()["n"])
    if n:
        raise RuntimeError(
            f"sinal divergiu entre staging e destino em {n} chave(s): valor "
            "assinado nao pode mudar de sinal na publicacao."
        )


def assert_keys_match(cur, staging: str, escopo: str) -> dict:
    """Fronteira C.16 — conjunto de chaves e cardinalidade.

    Tres provas, porque duas nao bastam no incremental:

    1. `faltando_no_destino` — toda chave da staging foi publicada.
    2. `sobrando_no_destino` — no `full`, chave no destino que a staging nao tem
       (hard delete nao reparado). No `incremental` e' 0 por construcao do
       semijoin, e fica como defesa.
    3. `linhas_no_escopo == linhas_na_staging` — esta e' a que pega DUPLICATA:
       uma linha extra no destino para uma chave TOCADA tem a mesma chave, entao
       nenhum `EXCEPT` de chaves a encontraria. Somente a contagem revela.
    """
    keys = ", ".join(validate_identifier(k) for k in KEY_COLUMNS)
    cur.execute(f"""
        SELECT
          (SELECT COUNT(*) FROM (
              SELECT {keys} FROM {staging}
              EXCEPT SELECT {keys} FROM {escopo} AS e1) a)  AS faltando_no_destino,
          (SELECT COUNT(*) FROM (
              SELECT {keys} FROM {escopo} AS e2
              EXCEPT SELECT {keys} FROM {staging}) b)       AS sobrando_no_destino,
          (SELECT COUNT(*) FROM {escopo} AS e3)             AS linhas_no_escopo,
          (SELECT COUNT(*) FROM {staging})                  AS linhas_na_staging
    """)
    linha = dict(cur.fetchone())
    problemas = []
    if int(linha["faltando_no_destino"]):
        problemas.append(f"faltando_no_destino={linha['faltando_no_destino']}")
    if int(linha["sobrando_no_destino"]):
        problemas.append(f"sobrando_no_destino={linha['sobrando_no_destino']}")
    if int(linha["linhas_no_escopo"]) != int(linha["linhas_na_staging"]):
        problemas.append(
            f"cardinalidade: escopo={linha['linhas_no_escopo']} "
            f"staging={linha['linhas_na_staging']}"
        )
    if problemas:
        raise RuntimeError("chaves divergiram: " + " ".join(problemas))
    return linha


def assert_scope_has_no_nan(cur, escopo: str) -> None:
    """Fronteira C.17 — restrita ao escopo publicado.

    No `incremental`, um NaN numa linha historica nao tocada nao foi introduzido
    por esta execucao e nao pode ser corrigido por ela. Verificar o destino
    inteiro faria toda execucao futura falhar por um defeito antigo, sem que
    houvesse acao possivel.
    """
    condicoes = " OR ".join(
        f"{validate_identifier(c)} = 'NaN'::numeric" for c in COMPONENT_COLUMNS
    )
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {escopo} AS escopo_nan WHERE {condicoes}"
    )
    n = int(cur.fetchone()["n"])
    if n:
        raise RuntimeError(f"NaN publicado no destino em {n} linha(s).")


# ---------------------------------------------------------------------------
# Watermark — lido e escrito sob o lock, com avanco observavel
# ---------------------------------------------------------------------------

def acquire_advisory_lock(lock_conn) -> None:
    """Adquire o advisory lock de SESSAO com espera finita.

    `pg_advisory_lock` (sessao) em vez de `pg_advisory_xact_lock` (transacao):
    o lock de transacao amarrava a exclusao mutua a uma transacao gravavel que
    ficava ociosa durante toda a leitura da fonte, e o destino encerra sessoes
    ociosas em transacao. O lock de sessao sobrevive ao fim de qualquer transacao
    e vive numa conexao em autocommit, que nunca fica ociosa em transacao.

    **Compatibilidade com a versao anterior:** locks consultivos de sessao e de
    transacao compartilham o MESMO espaco de chaves e o mesmo gestor de locks —
    diferem apenas em QUANDO sao liberados. Logo `pg_advisory_lock(K)` e
    `pg_advisory_xact_lock(K)` CONFLITAM entre si, e uma execucao da versao antiga
    e uma da nova nao podem se sobrepor durante o rollout.

    `lock_timeout` e' de sessao (nao `SET LOCAL`) porque em autocommit nao existe
    transacao a que se prender. Nao e' `0` e nao toca
    `idle_in_transaction_session_timeout`: e' um teto finito para a espera, e o
    erro sobe sem retry.
    """
    cur = lock_conn.cursor()
    try:
        cur.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        cur.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        cur.fetchone()
    finally:
        cur.close()


def _restore_autocommit(conn) -> None:
    """Devolve a conexao ao autocommit. NUNCA levanta.

    Roda em `finally`, possivelmente com excecao em voo. Se a conexao caiu, ou
    se por qualquer razao ainda houver transacao aberta, nao ha o que fazer aqui:
    a liberacao do lock seguinte tolera falha, e fechar a conexao libera o lock
    no servidor de qualquer forma.
    """
    try:
        conn.autocommit = True
    except Exception:  # noqa: BLE001 — ver docstring
        pass


def release_advisory_lock(lock_conn) -> bool:
    """Libera o advisory lock de sessao. NUNCA levanta excecao.

    Chamada em `finally`, portanto pode rodar enquanto uma excecao original esta
    em voo: levantar aqui mascararia a causa real da falha. Se a liberacao falhar,
    a queda da conexao libera o lock no proprio servidor — locks consultivos de
    sessao morrem com a sessao.
    """
    try:
        cur = lock_conn.cursor()
        try:
            cur.execute("SELECT pg_advisory_unlock(%s) AS liberado", (ADVISORY_LOCK_KEY,))
            linha = cur.fetchone()
            return bool(linha and linha["liberado"])
        finally:
            cur.close()
    except Exception:  # noqa: BLE001 — ver docstring
        return False


def assert_still_holding_lock(cur) -> None:
    """Prova que ESTA sessao ainda detem o advisory lock.

    Chamada depois de a fonte ter sido lida por inteiro e antes de a transacao
    gravavel ser aberta. Serve a dois propositos:
      1. confirma que a conexao continua utilizavel (se caiu, esta consulta falha);
      2. confirma que o lock nao foi perdido, comparando com `pg_backend_pid()` —
         ou seja, e' posse desta sessao, nao de outra.

    Como a publicacao roda NESTA MESMA sessao, "o lock esta vivo aqui" e "posso
    escrever" passam a ser a mesma condicao. Nao ha caminho para perder o lock e
    seguir escrevendo por outro lugar.
    """
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM pg_locks
        WHERE locktype = 'advisory'
          AND pid = pg_backend_pid()
          AND ((classid::bigint << 32) | objid::bigint) = %(chave)s
        """,
        {"chave": ADVISORY_LOCK_KEY},
    )
    linha = cur.fetchone()
    if not linha or int(linha["n"]) < 1:
        raise RuntimeError(
            "esta sessao NAO detem mais o advisory lock: a exclusao mutua caiu "
            "durante a leitura da fonte. Nada foi publicado."
        )


def assert_watermark_unchanged(cur, observado: datetime | None) -> None:
    """Confirma que o watermark nao mudou entre a leitura sob o lock e a escrita.

    Sob o advisory lock isto deve ser sempre verdade. O guardrail e' defesa em
    profundidade, nao o mecanismo principal.

    ⚠️ SEMANTICA EXATA, corrigindo afirmacao anterior errada. A releitura usa
    `FOR UPDATE`, que trava a LINHA — e portanto so protege quando a linha JA
    EXISTE. Sobre linha INEXISTENTE (primeira carga) `FOR UPDATE` nao trava nada:
    nao ha o que travar, e dois `INSERT` concorrentes seriam serializados apenas
    pela PK, nao por ele.

    Quem protege a primeira carga — e quem de fato protege as duas — e' o
    advisory lock mantido NESTA MESMA sessao. A versao anterior deste modulo usava
    uma conexao separada para publicar e atribuia ao `SELECT` a aquisicao de um
    lock de linha que ele nunca teve (era `SELECT` simples, sem `FOR UPDATE`).
    """
    atual = read_watermark(cur, for_update=True)
    if atual != observado:
        raise RuntimeError(
            "watermark mudou entre a leitura sob o advisory lock e a transacao de "
            "escrita: a exclusao mutua nao valeu (execucao concorrente de outra "
            "versao, ou lock perdido). Nada foi publicado."
        )


def read_watermark(cur, for_update: bool = False) -> datetime | None:
    """Ultimo limite superior BEM-SUCEDIDO.

    Ausencia da LINHA significa BACKFILL INTEGRAL OBRIGATORIO. Nunca "desde o
    inicio de uma janela movel": `updated_at` so existe na fonte a partir de
    2026-03-12 enquanto os pedidos comecam em 2025-06-04, e tratar ausencia como
    janela perderia a historia anterior.

    O schema tem `NOT NULL` em `last_successful_upper_bound`, logo ausencia de
    watermark e' ausencia de LINHA — nunca linha com coluna nula. Sob `--apply`
    esta leitura acontece DEPOIS do advisory lock, e e' a leitura autoritativa.

    `for_update=True` acrescenta `FOR UPDATE` e SO pode ser usado dentro de uma
    transacao. Ele trava a LINHA existente; sobre linha INEXISTENTE nao trava
    nada, porque nao ha tupla a travar. Por isso ele e' defesa complementar, e nao
    o mecanismo de exclusao mutua — esse e' o advisory lock de sessao. O default
    e' `False` porque a leitura autoritativa roda em autocommit, onde `FOR UPDATE`
    nao teria transacao a que se prender.
    """
    trava = " FOR UPDATE" if for_update else ""
    cur.execute(
        f"SELECT last_successful_upper_bound AS wm "
        f"FROM {validate_qualified(SYNC_STATE_TABLE)} "
        f"WHERE source_table = %(src)s AND target_table = %(tgt)s{trava}",
        {"src": SOURCE_TABLE, "tgt": TARGET_TABLE},
    )
    linha = cur.fetchone()
    return None if linha is None else linha["wm"]


def advance_watermark(cur, cutoff: datetime, run_id: str,
                      atual: datetime | None) -> str:
    """Avanca o watermark e DEVOLVE o que aconteceu de fato.

    Chamada somente depois de staging materializada, escrita concluida e todas as
    fronteiras aprovadas — dentro da MESMA transacao da escrita, para que rollback
    reverta os dois juntos. Avancar antes da reconciliacao criaria o pior estado
    possivel: destino errado e watermark dizendo que esta certo, com a janela que
    continha a correcao ja consumida.

    `atual` vem da leitura autoritativa feita sob o lock, no inicio da transacao.
    Nao ha releitura aqui: o lock garante que ninguem mudou o valor no meio.

    Tres desfechos, e nenhum deles e' "declarar sucesso por omissao":
      cutoff  > atual -> UPDATE de exatamente uma linha, devolve 'avancado'
      cutoff == atual -> no-op idempotente, devolve 'inalterado', NAO toca
                         `source_run_id` (o run_id gravado continua sendo o da
                         execucao que de fato moveu o watermark; sobrescrever
                         atribuiria a esta execucao um avanco que nao houve)
      cutoff  < atual -> FALHA. Nunca publica com fotografia mais antiga que o
                         estado ja registrado.

    Toda escrita afeta exatamente uma linha; `rowcount` diferente de 1 falha,
    porque significa que a premissa do lock nao valeu.
    """
    if atual is None:
        cur.execute(
            f"""
            INSERT INTO {validate_qualified(SYNC_STATE_TABLE)}
                (source_table, target_table, last_successful_upper_bound,
                 source_run_id, updated_at)
            VALUES (%(src)s, %(tgt)s, %(wm)s, %(run_id)s, now())
            """,
            {"src": SOURCE_TABLE, "tgt": TARGET_TABLE, "wm": cutoff,
             "run_id": run_id},
        )
        if cur.rowcount != 1:
            raise RuntimeError(
                f"insercao do watermark afetou {cur.rowcount} linha(s), esperado 1."
            )
        return WATERMARK_ADVANCED

    if cutoff < atual:
        raise RuntimeError(
            "fotografia mais antiga que o watermark registrado "
            "(cutoff < ultimo bem-sucedido): publicar aqui sobrescreveria dado "
            "novo com dado velho. Execucao abortada sem escrita."
        )
    if cutoff == atual:
        return WATERMARK_UNCHANGED

    cur.execute(
        f"""
        UPDATE {validate_qualified(SYNC_STATE_TABLE)}
        SET last_successful_upper_bound = %(wm)s,
            source_run_id               = %(run_id)s,
            updated_at                  = now()
        WHERE source_table = %(src)s
          AND target_table = %(tgt)s
          AND last_successful_upper_bound < %(wm)s
        """,
        {"src": SOURCE_TABLE, "tgt": TARGET_TABLE, "wm": cutoff, "run_id": run_id},
    )
    if cur.rowcount != 1:
        # O `< %(wm)s` e' defesa em profundidade sobre a serializacao. Se ele
        # barrou a linha, a premissa do lock nao valeu e nada pode ser publicado.
        raise RuntimeError(
            f"atualizacao do watermark afetou {cur.rowcount} linha(s), esperado 1: "
            "o estado mudou por baixo do advisory lock."
        )
    return WATERMARK_ADVANCED


def delete_watermark(cur) -> int:
    """Remove a linha do state. Usada no `full` com fonte comprovadamente vazia.

    Ausencia de watermark e' representada por ausencia de LINHA, nunca por linha
    com coluna nula — e' o que o `NOT NULL` do schema impoe. Depois disso a
    proxima execucao incremental falha e exige `full`, que e' o comportamento
    correto: nao ha janela valida a partir de "nunca houve sucesso".
    """
    cur.execute(
        f"DELETE FROM {validate_qualified(SYNC_STATE_TABLE)} "
        f"WHERE source_table = %(src)s AND target_table = %(tgt)s",
        {"src": SOURCE_TABLE, "tgt": TARGET_TABLE},
    )
    if cur.rowcount not in (0, 1):
        raise RuntimeError(
            f"remocao do watermark afetou {cur.rowcount} linha(s), esperado 0 ou 1."
        )
    return cur.rowcount


# ---------------------------------------------------------------------------
# Staging e publicacao — dentro da transacao ja bloqueada
# ---------------------------------------------------------------------------

def create_staging(cur) -> None:
    cur.execute(f"""
        CREATE TEMP TABLE {validate_identifier(STAGING_TABLE)}
            (LIKE {validate_qualified(TARGET_TABLE)} INCLUDING DEFAULTS)
        ON COMMIT DROP
    """)


def insert_into_staging(cur, rows: list[dict], run_id: str) -> None:
    if not rows:
        return
    cols = list(BUSINESS_COLUMNS) + list(AUDIT_COLUMNS)
    sql = (
        f"INSERT INTO {validate_identifier(STAGING_TABLE)} "
        f"({', '.join(validate_identifier(c) for c in cols)}) VALUES %s"
    )
    batch = [tuple(r.get(c) for c in BUSINESS_COLUMNS) + (run_id,) for r in rows]
    execute_values(cur, sql, batch, page_size=INSERT_PAGE_SIZE)


def publish_in_transaction(cur, snapshot: SourceSnapshot, mode: str,
                           run_id: str, watermark_atual: datetime | None) -> dict:
    """Publica dentro da transacao que o chamador JA abriu.

    Nao toma advisory lock — o lock e' de SESSAO e vive na conexao dedicada do
    `_run_apply`, adquirido antes de qualquer leitura de estado. Readquirir aqui
    seria redundante e mascararia a ordem correta. Nao comita e nao faz rollback:
    a transacao pertence ao chamador, e um commit aqui quebraria a atomicidade
    entre fato e watermark.

    Pressupoe que `validate_snapshot_in_memory` JA aprovou os dados: as provas que
    nao dependem do banco rodam antes de esta transacao existir, para que dado
    invalido nunca chegue a abrir transacao gravavel no destino.

    `mode` decide o escopo do DELETE, e a diferenca e' semantica, nao otimizacao:
      full        -> apaga o destino inteiro. E' o que repara hard delete, porque
                     chave que deixou de existir na fonte deixa de existir aqui.
      incremental -> apaga somente as chaves recalculadas. Estruturalmente cego a
                     hard delete: linha removida nao tem `updated_at` para ser
                     detectada.
    """
    target = validate_qualified(TARGET_TABLE)
    staging = validate_identifier(STAGING_TABLE)
    resultado = {"mode": mode, "published": 0, "deleted": 0, "checks": {}}

    create_staging(cur)
    insert_into_staging(cur, snapshot.rows, run_id)

    # Fronteira B do lado do banco. A parte em memoria ja rodou em
    # `validate_snapshot_in_memory`, antes desta transacao existir.
    resultado["checks"]["staging_max_updated_at"] = str(
        assert_staging_within_cutoff(cur, snapshot.cutoff)
    )

    if mode == "full":
        cur.execute(f"DELETE FROM {target}")
    else:
        cur.execute(f"""
            DELETE FROM {target} t
            USING {staging} s
            WHERE {_key_join_on('t', 's')}
        """)
    resultado["deleted"] = cur.rowcount

    cols = ", ".join(
        validate_identifier(c) for c in list(BUSINESS_COLUMNS) + list(AUDIT_COLUMNS)
    )
    cur.execute(f"INSERT INTO {target} ({cols}) SELECT {cols} FROM {staging}")
    resultado["published"] = cur.rowcount

    # Fronteira C — staging x destino, no escopo do modo.
    escopo = target_scope(mode, staging, TARGET_TABLE)
    resultado["checks"]["escopo_reconciliacao"] = (
        "destino_inteiro" if mode == "full" else "chaves_da_staging"
    )
    resultado["checks"]["chaves"] = assert_keys_match(cur, staging, escopo)
    a_nao_b, b_nao_a = except_both_ways(cur, staging, escopo)
    if a_nao_b or b_nao_a:
        raise RuntimeError(
            f"EXCEPT bidirecional divergiu: staging-destino={a_nao_b} "
            f"destino-staging={b_nao_a}"
        )
    resultado["checks"]["except_both_ways"] = (a_nao_b, b_nao_a)
    assert_signs_preserved(cur, staging, TARGET_TABLE)
    assert_scope_has_no_nan(cur, escopo)

    # Somente agora, e na MESMA transacao.
    resultado["watermark"] = advance_watermark(
        cur, snapshot.cutoff, run_id, watermark_atual
    )
    if resultado["watermark"] == WATERMARK_ADVANCED:
        resultado["watermark_novo"] = str(snapshot.cutoff)
    return resultado


def wipe_fact(cur) -> dict:
    """Esvazia o fato e REMOVE a linha do state, na transacao do chamador.

    So e' chamada no `full` com fonte comprovadamente vazia (`COUNT(*) = 0`
    medido, nao inferido de `MAX(updated_at) IS NULL`). Nenhum cutoff e'
    fabricado: nao existe `now()` aqui, e nenhum watermark e' gravado. Se a
    remocao do state falhar, o `DELETE` do fato volta junto — a transacao e' uma
    so.
    """
    cur.execute(f"DELETE FROM {validate_qualified(TARGET_TABLE)}")
    apagadas = cur.rowcount
    linhas_state = delete_watermark(cur)
    return {"fato_apagado": apagadas, "state_removido": linhas_state}


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------

def resolve_lower_bound(mode: str, watermark: datetime | None) -> datetime | None:
    """`full` ignora o watermark por definicao. `incremental` sem watermark NAO
    inventa janela: exige backfill integral, e falha dizendo isso.
    """
    if mode == "full":
        return None
    if watermark is None:
        raise RuntimeError(
            "sem watermark bem-sucedido para este par (fonte, destino): a "
            "primeira carga e obrigatoriamente backfill integral. Rode com "
            "--mode full. Tratar ausencia de watermark como janela movel "
            "perderia a historia anterior a 2026-03-12, quando updated_at passou "
            "a existir na fonte."
        )
    return watermark


def validate_snapshot_in_memory(snapshot: SourceSnapshot, mode: str) -> dict:
    """Todas as provas que NAO dependem do destino, antes de abrir transacao nele.

    Roda com a fotografia da fonte ja lida e o destino ainda intocado. Se
    reprovar, nenhuma transacao gravavel chegou a existir — logo zero DDL/DML,
    por construcao e nao por rollback.

    Cobre a fronteira A (feita dentro de `read_source_snapshot`), a decisao de
    fonte vazia e a parte em memoria da fronteira B: NaN e reconciliacao
    detalhe x agregado, que sao funcoes puras sobre dados ja lidos.
    """
    provas = {}
    if snapshot.source_empty:
        assert_empty_source_allowed(mode)
        provas["fonte_vazia"] = True
        return provas
    _assert_no_nan(snapshot.rows)
    provas["fronteira_b"] = reconcile_detail_vs_aggregate(
        snapshot.rows, snapshot.detail_totals
    )
    return provas


def assert_empty_source_allowed(mode: str) -> None:
    """Fonte comprovadamente vazia: so o `full` pode agir.

    O incremental nao pode inferir hard delete total. Ele ve `updated_at` e nada
    mais; "a fonte esvaziou" e "a janela nao tem mudanca" sao indistinguiveis
    para ele, e apagar o fato com base nessa ambiguidade destruiria a historia
    inteira por causa de, por exemplo, um `TRUNCATE` acidental a montante ou uma
    replica apontada para o banco errado.
    """
    if mode != "full":
        raise RuntimeError(
            "fonte comprovadamente VAZIA (COUNT(*) = 0) em modo incremental. O "
            "incremental nao infere hard delete total: 'a fonte esvaziou' e 'a "
            "janela nao mudou' sao indistinguiveis para ele. Nada foi escrito e o "
            "watermark nao mudou. Confirme operacionalmente que a fonte deve "
            "estar vazia e, somente entao, rode --mode full --apply."
        )


def _run_apply(mode: str, run_id: str) -> dict:
    """Execucao com escrita. Lock de SESSAO; transacao gravavel curta e no fim.

    Ordem obrigatoria (18.8.9). UMA SO conexao com o destino, do inicio ao fim:
      1. abre a UNICA conexao do destino, em autocommit
      2. adquire pg_advisory_lock com lock_timeout finito
      3. le o watermark autoritativo sob o lock, SEM transacao aberta
      4. resolve o lower bound
      5. abre a fotografia read-only REPEATABLE READ na fonte
      6. le a fonte inteira dentro da fotografia
      7. valida integralmente em memoria  <- destino ainda intocado
      8. confirma que ESTA sessao continua viva e ainda detem o lock
      9. desliga o autocommit NA MESMA conexao -> transacao gravavel
     10. rele o watermark (FOR UPDATE) e confirma que nao mudou
     11. staging, publicacao, reconciliacao e watermark na mesma transacao
     12. commit unico
     13. devolve a conexao ao autocommit
     14. `finally`: libera o lock de sessao e fecha a conexao

    POR QUE O LOCK VIROU DE SESSAO — E POR QUE A SESSAO E' UMA SO
    -------------------------------------------------------------
    Antes, a exclusao mutua vinha de `pg_advisory_xact_lock` numa transacao
    gravavel aberta no passo 1. Consequencia: essa transacao ficava OCIOSA durante
    toda a leitura da fonte, e o destino encerra sessoes ociosas em transacao
    (`idle_in_transaction_session_timeout` medido em 300 s). Reduzir
    `SOURCE_STATEMENT_TIMEOUT` NAO resolvia: `statement_timeout` e' POR STATEMENT,
    e a leitura da fonte sao sete consultas sequenciais — nao havia teto
    acumulado, so a ilusao de um.

    A correcao seguinte trocou por lock de sessao, mas publicava numa SEGUNDA
    conexao. Isso deixava uma janela: o advisory lock pertence a sessao, entao a
    queda da conexao do lock liberaria o lock no servidor enquanto a outra conexao
    seguia perfeitamente capaz de escrever. O guardrail de releitura do watermark
    nao fechava a janela como se afirmou — `read_watermark` era `SELECT` simples,
    sem `FOR UPDATE`, e portanto nao adquiria lock de linha nenhum; e na primeira
    carga a linha nem existe, de modo que `FOR UPDATE` tambem nao a protegeria.

    Agora e' UMA SO conexao: ela adquire o lock, le o watermark em autocommit,
    espera a leitura da fonte sem transacao aberta e depois desliga o autocommit
    para publicar. Se cair, perde-se o lock E o unico caminho de escrita no mesmo
    instante — nao existe "perder o lock numa conexao e continuar escrevendo por
    outra", e a ausencia inicial da linha de watermark deixa de ser caso especial.
    A janela ociosa segue inexistente, e nenhuma protecao do destino foi desligada.

    A fotografia da fonte permanece aberta ate a staging estar materializada e
    reconciliada (18.8.3 regra 4). Todas as leituras da fonte, porem, terminam no
    passo 6: nada depois disso consulta a fonte.
    """
    relatorio = {"mode": mode, "run_id": run_id, "applied": True}
    neon = _neon_session(_get_neon_url())                                    # 1
    try:
        acquire_advisory_lock(neon)                                          # 2
        relatorio["lock"] = f"pg_advisory_lock({ADVISORY_LOCK_KEY}) de sessao"

        cur = neon.cursor()
        try:
            watermark = read_watermark(cur)                                  # 3
        finally:
            cur.close()
        relatorio["watermark_anterior"] = str(watermark)
        lower_bound = resolve_lower_bound(mode, watermark)                    # 4

        datamart = _datamart_snapshot(_get_datamart_url())                    # 5
        try:
            snapshot = read_source_snapshot(datamart, lower_bound)            # 6
            relatorio["fronteira_a"] = snapshot.boundary_a
            relatorio["chaves"] = len(snapshot.rows)
            relatorio["validacao_em_memoria"] = validate_snapshot_in_memory(  # 7
                snapshot, mode
            )

            # 8 — a conexao continua utilizavel E ainda detem o lock? Se caiu,
            # esta consulta falha aqui, e nao existe segunda conexao para onde
            # escapar: nao ha `_neon_writable` neste modulo.
            cur = neon.cursor()
            assert_still_holding_lock(cur)
            cur.close()

            # 9 — a MESMA sessao passa a transacionar. Nada de segunda conexao.
            neon.autocommit = False
            try:
                cur = neon.cursor()
                cur.execute(
                    f"SET LOCAL statement_timeout = '{TARGET_STATEMENT_TIMEOUT}'"
                )
                cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                assert_watermark_unchanged(cur, watermark)                    # 10

                if snapshot.source_empty:
                    relatorio["limpeza"] = wipe_fact(cur)
                    relatorio["watermark"] = "removido"
                    relatorio["resultado"] = (
                        "fonte comprovadamente vazia; fato esvaziado e state removido"
                    )
                else:
                    relatorio["publicacao"] = publish_in_transaction(         # 11
                        cur, snapshot, mode, run_id, watermark
                    )
                    relatorio["watermark"] = relatorio["publicacao"]["watermark"]
                    # `watermark_novo` nasce dentro de `publicacao`; sem propagar,
                    # `_print_report` (que le do topo) imprimia "avancado para
                    # None" apesar de o valor persistido estar correto. Somente
                    # presente quando o watermark de fato avancou — no-op nao
                    # fabrica valor.
                    if "watermark_novo" in relatorio["publicacao"]:
                        relatorio["watermark_novo"] = (
                            relatorio["publicacao"]["watermark_novo"]
                        )
                    relatorio["resultado"] = "publicado"

                neon.commit()                                                # 12
                cur.close()
            except Exception:
                neon.rollback()
                raise
            finally:
                # 13 — devolve a conexao ao estado em que o unlock roda sem abrir
                # transacao nova. Nunca levanta: rodaria com excecao em voo.
                _restore_autocommit(neon)
        finally:
            # Encerra a fotografia SOMENTE depois de staging e reconciliacao.
            # Read-only: `rollback` e' o encerramento correto, nada a confirmar.
            datamart.rollback()
            datamart.close()
        return relatorio
    finally:
        # 14 — liberado em sucesso e em toda classe de falha. Fechar a conexao
        # tambem libera o lock no servidor, porque ele e' de sessao.
        release_advisory_lock(neon)
        neon.close()


def _run_diagnostic(mode: str, run_id: str) -> dict:
    """Execucao sem escrita. Zero advisory lock, zero DDL/DML, zero staging.

    `full` NAO toca o Neon. Nem para ler.
    ------------------------------------
    `full` ignora o watermark por definicao (`resolve_lower_bound` devolve None
    para ele em qualquer caso), entao ler o state era dependencia indevida: fazia
    o diagnostico do backfill inicial depender de uma tabela que so passa a
    existir DEPOIS da migration. O preflight do primeiro full ficava impossivel
    exatamente quando e' mais necessario.

    A correcao e' estreita: em `full` sem `--apply`, nenhuma conexao com o Neon e'
    aberta, `watermark` e' None por construcao e a unica conexao e' a fotografia
    read-only do Data Mart.

    `incremental` continua lendo o state no Neon em read-only, e continua FALHANDO
    quando a linha ou a tabela nao existe. Nunca cai silenciosamente para `full`:
    tratar "sem state" como "le a historia inteira" transformaria um erro de
    configuracao em backfill acidental.

    A leitura do watermark aqui, quando ocorre, e' read-only e NAO e' autoritativa:
    sem lock ela pode envelhecer no mesmo instante. Serve a diagnostico, e por
    isso nada desta funcao pode escrever.
    """
    relatorio = {"mode": mode, "run_id": run_id, "applied": False}
    if mode == "full":
        watermark = None
        relatorio["watermark_anterior"] = "nao consultado (full ignora watermark)"
        relatorio["neon"] = "nao acessado"
    else:
        neon = _neon_readonly(_get_neon_url())
        try:
            cur = neon.cursor()
            watermark = read_watermark(cur)
            cur.close()
        finally:
            neon.close()
        relatorio["watermark_anterior"] = str(watermark)
        relatorio["neon"] = "lido em read-only"
    lower_bound = resolve_lower_bound(mode, watermark)

    datamart = _datamart_snapshot(_get_datamart_url())
    try:
        snapshot = read_source_snapshot(datamart, lower_bound)
        relatorio["fronteira_a"] = snapshot.boundary_a
        relatorio["chaves"] = len(snapshot.rows)

        # Exatamente as mesmas provas do apply (passo 7), pela mesma funcao: o
        # diagnostico so difere por nao ter passos 8-11.
        provas = validate_snapshot_in_memory(snapshot, mode)
        relatorio["validacao_em_memoria"] = provas
        if snapshot.source_empty:
            relatorio["resultado"] = (
                "fonte comprovadamente vazia; --apply em modo full ESVAZIARIA o "
                "fato e removeria o watermark. Nada foi escrito."
            )
            return relatorio
        relatorio["fronteira_b"] = provas["fronteira_b"]
        relatorio["resultado"] = "diagnostico; nada escrito"
        return relatorio
    finally:
        datamart.rollback()
        datamart.close()


def run(mode: str, run_id: str, apply: bool) -> dict:
    return _run_apply(mode, run_id) if apply else _run_diagnostic(mode, run_id)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sync_tiktok_affiliate_cost_order_monthly",
        description=(
            "Sync de custo de afiliado do TikTok por coorte de PEDIDO: "
            "silver.stg_tiktok_payments_by_order -> "
            "marts.fact_tiktok_affiliate_cost_order_monthly, grao (ref_month, "
            "brand). Competencia COMERCIAL, nao financeira: NAO e taxa de "
            "statement nem repasse reconhecido. Sem --apply nada e escrito. "
            "Nao agenda, nao dorme, nao repete e nao tenta de novo."
        ),
    )
    p.add_argument(
        "--mode", choices=("full", "incremental"), required=True,
        help=(
            "full: le a historia inteira, reconstroi o destino e REPARA hard "
            "delete; obrigatorio na primeira carga e periodicamente. "
            "incremental: usa o watermark para descobrir chaves tocadas e "
            "recalcula cada uma por inteiro; cego a hard delete."
        ),
    )
    p.add_argument(
        "--apply", action="store_true",
        help="EFETIVA a escrita no Neon. Sem esta flag, tudo e somente leitura.",
    )
    p.add_argument("--run-id", help="identificador da execucao (sanitizado)")
    return p


def _print_report(relatorio: dict) -> None:
    print(f"modo................: {relatorio['mode']}")
    print(f"run_id..............: {relatorio['run_id']}")
    print(f"aplicado............: {relatorio['applied']}")
    if "lock" in relatorio:
        print(f"advisory lock.......: {relatorio['lock']}")
    print(f"watermark anterior..: {relatorio.get('watermark_anterior')}")
    fa = relatorio.get("fronteira_a") or {}
    if fa:
        print(f"fronteira A.........: {fa}")
    if "fronteira_b" in relatorio:
        print(f"fronteira B.........: {relatorio['fronteira_b']}")
    if "limpeza" in relatorio:
        print(f"limpeza.............: {relatorio['limpeza']}")
    if "publicacao" in relatorio:
        pub = relatorio["publicacao"]
        print(f"apagadas............: {pub['deleted']}")
        print(f"publicadas..........: {pub['published']}")
        print(f"checks..............: {pub['checks']}")
    # Nunca dizer "advanced_to" quando nada mudou (F6), e nunca anunciar um
    # valor que nao existe: se o watermark avancou mas o valor nao chegou ao
    # relatorio, isso e' defeito do relatorio e e' dito como tal, em vez de
    # imprimir "None" como se fosse o valor persistido.
    estado = relatorio.get("watermark")
    if estado == WATERMARK_ADVANCED:
        novo = relatorio.get("watermark_novo")
        if novo:
            print(f"watermark...........: avancado para {novo}")
        else:
            print("watermark...........: avancado (valor nao propagado ao "
                  "relatorio; consulte sync_state)")
    elif estado == WATERMARK_UNCHANGED:
        print("watermark...........: inalterado (cutoff igual ao registrado)")
    elif estado is not None:
        print(f"watermark...........: {estado}")
    print(f"resultado...........: {relatorio.get('resultado')}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.apply:
        print("MODO DIAGNOSTICO (sem --apply): nenhuma escrita sera feita.")
    run_id = sanitize_run_id(args.run_id) if args.run_id else default_run_id(args.mode)
    try:
        relatorio = run(args.mode, run_id, args.apply)
    except Exception as exc:  # noqa: BLE001 — fronteira do CLI
        print(f"FALHA: {sanitize_error_message(exc)}", file=sys.stderr)
        return 2
    _print_report(relatorio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
