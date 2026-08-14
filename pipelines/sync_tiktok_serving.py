"""Gate S2 — sync incremental Data Mart -> Neon das duas fatos TikTok de /operacoes.

Fonte (somente leitura): Data Mart RDS, DATAMART_DATABASE_URL.
Destino (escrita): Neon, DATABASE_URL.

    gold.tiktok_brand_daily    -> marts.fact_tiktok_brand_content_daily  (migration 007)
    gold.tiktok_creator_daily  -> marts.fact_tiktok_creator_daily        (migration 008)

POR QUE UM MODULO PARA DUAS TABELAS
-----------------------------------
As duas tem mecanica identica (janela, staging, DELETE+INSERT, reconciliacao) e
diferem apenas em relacao de origem, destino, chave e lista de colunas. Duas
copias quase iguais de 400 linhas envelheceriam divergindo; um framework generico
seria abstracao prematura. O meio-termo e' UM modulo com DUAS `TableSpec`
literais e fixas: nada e' descoberto em runtime, cada tabela publica em
**transacao propria** com **advisory lock proprio**, e acrescentar uma terceira
tabela exige escrever a spec a mao — nao existe registro dinamico.

O QUE ESTE MODULO NAO FAZ
-------------------------
- nao altera `gold_service.py`: `/operacoes` e `/brand-detail` continuam lendo a
  gold. A troca de fonte e' a Task 3/3 do Gate S2;
- nao muda a definicao de GMV. Copia exatamente o que a Gold serve. A decisao de
  incluir frete e' frente SEPARADA (docs/tiktok_gmv_com_frete_decisao.md) e mexe
  em `pipelines/connectors/tiktok/connector.py`, nao aqui;
- nao escreve em Gold, Raw ou Silver: a conexao com o Data Mart e' aberta
  `readonly=True`;
- nao agenda, nao repete e nao tenta de novo. Sem retry, backoff, sleep ou loop:
  repeticao e' decisao de quem chama. Nenhuma dependencia de Airflow.

ESTRATEGIA DE PUBLICACAO
------------------------
`DELETE` da janela + `INSERT` da staging, nunca `TRUNCATE` e nunca apenas
`ON CONFLICT DO UPDATE`. A razao e' de correcao: upsert **nao apaga**. Se uma
linha desaparecer da fonte dentro da janela — dia reprocessado, criador removido,
marca sem movimento — o upsert a deixaria orfa no destino para sempre. Apagar a
janela e reinserir faz o destino refletir a fonte inclusive nas REMOCOES, e e' o
que torna a execucao idempotente.

Toda janela termina no **ultimo dia fechado (D-1)**: o dia corrente nunca e'
publicado, porque esta incompleto por definicao e um total parcial gravado teria
de ser corrigido pela execucao seguinte.

MINIMIZACAO DE DADOS: SOMENTE AS CINCO MARCAS OFICIAIS
------------------------------------------------------
A Gold contem marcas alem das cinco autorizadas, e nenhuma delas tem consumidor
na Torre: `/brand-detail` recusa marca fora da lista e `/operacoes` filtra por
ela. Copiar o excedente nao serviria a nenhuma tela e ampliaria sem necessidade a
superficie de dado pessoal, porque `creator` e' handle publico potencialmente
identificavel. O filtro e' PARAMETRIZADO (`brand = ANY(%(brands)s)`) e a lista
vem de `pipelines.connectors.tiktok.connector.BRANDS_IN_SCOPE` — a allowlist
oficial, reutilizada e nao recriada.

FATOS DA AUDITORIA READ-ONLY (12/08/2026, JA COM A ALLOWLIST) QUE MOLDAM ESTE MODULO
------------------------------------------------------------------------------------
Janela historica ate 10/08/2026, cinco marcas:

- as duas origens sao TABELAS (nao views), sem PK nem UNIQUE fisico: o grao e'
  convencao na Gold e passa a ser restricao no destino;
- `(date, brand)` unico em 1.546 linhas (310 datas); `(date, brand, creator)`
  unico em 184.252 linhas (308 datas, 22.074 criadores distintos); zero
  duplicidade, zero nulo em obrigatoria e zero NaN em ambas;
- as duas tabelas Gold NAO sao carregadas em sincronia. Em 12/08/2026,
  `tiktok_brand_daily` ja tinha 11/08 e `tiktok_creator_daily` nao — a validacao
  de cobertura recusa a janela nesse caso, em vez de publicar um dia vazio. Quem
  opera precisa alinhar a janela ao menor `MAX(date)` das duas fontes;
- cobertura da fonte: ~10,2 meses (desde 05/10/2025) e ~10,1 meses (desde
  07/10/2025). **Menos que os 13 meses de referencia** — o piso de 13 meses vale
  "quando a fonte possuir essa cobertura", e hoje ela nao possui, entao o
  backfill leva TODO o historico disponivel e o diagnostico declara o deficit;
- `total_fees` e' TAXA e tem 1.529 valores negativos de 1.546: entra na
  reconciliacao como aditiva, mas jamais como nao-negativa;
- `total_live_minutes` tem 2 valores negativos na origem (03/04 e 06/05/2026),
  defeito de dado da ingestao TikTok. Copiado como servido: corrigir a origem nao
  e' tarefa da camada de serving;
- 14 colunas de demografia sao 100% nulas e `visitors`/`customers` sao nulas em
  68,6%/48,2%: sao opcionais e ficam fora da exigencia de nao-nulo;
- as colunas de percentual sao RAZOES: copiadas como servidas e NUNCA somadas.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

# Allowlist OFICIAL de marcas, reutilizada do conector TikTok. Nao existe uma
# segunda lista neste modulo de proposito: uma terceira copia divergiria da
# primeira no dia em que uma marca entrar ou sair, e o sync passaria a publicar
# um conjunto que nenhum consumidor autoriza.
from pipelines.connectors.tiktok.connector import BRANDS_IN_SCOPE

# ---------------------------------------------------------------------------
# Especificacao das tabelas — LITERAL, fixa e versionada
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableSpec:
    """Contrato de uma tabela de serving. Tudo explicito, nada descoberto em runtime."""

    name: str
    source_relation: str
    target_table: str
    staging_name: str
    key_columns: list[str]
    #: Metricas somaveis. Entram na reconciliacao por agregado.
    additive_columns: list[str]
    #: Razoes/percentuais. Copiadas como servidas e NUNCA somadas.
    ratio_columns: list[str]
    #: Colunas que a fonte deixa nula: ficam fora da exigencia de nao-nulo.
    optional_columns: list[str]
    #: Colunas sem CHECK de nao-negatividade, por serem negativas na origem.
    signed_columns: list[str]
    advisory_lock_key: int
    source_min_date: date
    date_column: str = "date"

    @property
    def business_columns(self) -> list[str]:
        return self.key_columns + self.additive_columns + self.ratio_columns

    @property
    def required_columns(self) -> list[str]:
        return [c for c in self.business_columns if c not in self.optional_columns]

    @property
    def staging_qualified(self) -> str:
        return f"pg_temp.{self.staging_name}"


BRAND_SPEC = TableSpec(
    name="brand",
    source_relation="gold.tiktok_brand_daily",
    target_table="marts.fact_tiktok_brand_content_daily",
    staging_name="sync_tiktok_brand_content_staging",
    key_columns=["date", "brand"],
    additive_columns=[
        "gmv", "orders", "gmv_video", "gmv_live", "gmv_card",
        "gmv_fresh", "gmv_evergreen", "total_fees",
        "visitors", "customers",
        "active_videos", "new_videos_posted", "active_video_creators",
        "total_views", "fresh_videos", "evergreen_videos",
        "total_lives", "total_live_minutes", "live_creators",
        "viewers_views_weighted", "followers_views_weighted",
    ],
    ratio_columns=[
        "viewers_pct_female", "viewers_pct_male",
        "viewers_pct_age_18_24", "viewers_pct_age_25_34", "viewers_pct_age_35_44",
        "viewers_pct_age_45_54", "viewers_pct_age_55_plus",
        "followers_pct_female", "followers_pct_male",
        "followers_pct_age_18_24", "followers_pct_age_25_34", "followers_pct_age_35_44",
        "followers_pct_age_45_54", "followers_pct_age_55_plus",
    ],
    # 14 percentuais 100% nulos + funil parcialmente nulo
    optional_columns=[
        "visitors", "customers",
        "viewers_pct_female", "viewers_pct_male",
        "viewers_pct_age_18_24", "viewers_pct_age_25_34", "viewers_pct_age_35_44",
        "viewers_pct_age_45_54", "viewers_pct_age_55_plus",
        "followers_pct_female", "followers_pct_male",
        "followers_pct_age_18_24", "followers_pct_age_25_34", "followers_pct_age_35_44",
        "followers_pct_age_45_54", "followers_pct_age_55_plus",
    ],
    signed_columns=["total_fees", "total_live_minutes"],
    advisory_lock_key=907_120_007,
    source_min_date=date(2025, 10, 5),
)

CREATOR_SPEC = TableSpec(
    name="creator",
    source_relation="gold.tiktok_creator_daily",
    target_table="marts.fact_tiktok_creator_daily",
    staging_name="sync_tiktok_creator_staging",
    key_columns=["date", "brand", "creator"],
    additive_columns=[
        "gmv_total", "gmv_video", "gmv_live",
        "views_video", "videos_count", "lives_count",
    ],
    ratio_columns=[],
    optional_columns=[],
    signed_columns=[],
    advisory_lock_key=908_120_008,
    source_min_date=date(2025, 10, 7),
)

SPECS = {BRAND_SPEC.name: BRAND_SPEC, CREATOR_SPEC.name: CREATOR_SPEC}

AUDIT_COLUMNS = ["synced_at", "source_run_id"]

#: Marcas autorizadas. NAO e' uma lista nova: e' a MESMA tupla do conector,
#: apenas com nome local. Um teste garante a identidade por `is`.
#:
#: Minimizacao de dados: a Gold tem marcas alem destas, e nenhuma delas tem
#: consumidor autorizado na Torre — `/brand-detail` recusa marca fora da lista e
#: `/operacoes` filtra por ela. Copiar o excedente para o Neon nao serviria a
#: nenhuma tela e ampliaria sem necessidade a superficie de dado pessoal, ja que
#: `creator` e' handle publico potencialmente identificavel.
ALLOWED_BRANDS = BRANDS_IN_SCOPE

#: Janela default do modo incremental, em dias COMPLETOS terminando no ultimo dia
#: fechado (D-1).
#:
#: Eram 7 dias, dimensionados por HIPOTESE ("late-arriving data"). A medicao do
#: Gate S2 Task 3/3 mostrou que a hipotese era curta: comparando Gold x Marts
#: dentro de uma janela coberta pelas duas, a fonte reafirmou valores de dias ja
#: fechados ate **68 dias** para tras (`ml_gestao_diaria`) e **27**
#: (`tiktok_brand_daily`). Um lookback de 7 corrigiria a ponta e deixaria deriva
#: PERMANENTE nas datas mais antigas — invisivel a qualquer checagem por
#: `MAX(date)`, porque a data maxima estaria correta e os valores nao.
#:
#: 90 dias cobrem o horizonte medido com folga. NAO e' garantia eterna: e' a
#: rotina. Reafirmacao mais antiga que 90 dias exige backfill historico periodico
#: (politica registrada em docs/SERVING_AIRFLOW_PLAN.md), nao um numero maior aqui.
DEFAULT_LOOKBACK_DAYS = 90

#: Piso contratual da janela incremental. Menor que isto nao absorve nem o
#: late-arriving data mais banal, entao e' recusado mesmo quando explicito.
#: Valores entre o piso e o default continuam validos por `--lookback-days`, para
#: reprocessamento pontual sob decisao humana.
MIN_LOOKBACK_DAYS = 7

#: Piso de historico desejado, em meses. Vale "quando a fonte possuir essa
#: cobertura": hoje as duas fontes tem ~10 meses, e o deficit e' DECLARADO em vez
#: de silenciosamente ignorado.
MIN_HISTORY_MONTHS = 13

SOURCE_STATEMENT_TIMEOUT = "120s"
TARGET_STATEMENT_TIMEOUT = "300s"
CONNECT_TIMEOUT_SECONDS = 15
INSERT_PAGE_SIZE = 1000

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_QUALIFIED_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}\.[a-z][a-z0-9_]{0,62}$")
_RUN_ID_RE = re.compile(r"[^A-Za-z0-9_:-]")


# ---------------------------------------------------------------------------
# Seguranca: conexoes explicitas, identificadores e erros
# ---------------------------------------------------------------------------

def _get_neon_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL (Neon) nao definido. Este script exige a variavel "
            "explicita, sem fallback, para nunca conectar a um banco nao pretendido."
        )
    return url


def _get_datamart_url() -> str:
    url = os.environ.get("DATAMART_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATAMART_DATABASE_URL nao definido. Este script exige a variavel "
            "explicita, sem fallback, para nunca conectar a um banco nao pretendido."
        )
    return url


def _datamart_readonly(url: str):
    """Sessao de LEITURA no Data Mart. `readonly=True` e' o que garante que
    nenhuma Gold/Raw/Silver possa ser escrita nem por acidente."""
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    conn.set_session(readonly=True)
    return conn


def _neon_readonly(url: str):
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    conn.set_session(readonly=True)
    return conn


def _neon_writable(url: str):
    """Sem `readonly=True` — usada exclusivamente sob `--apply`. Autocommit fica
    desligado (padrao do psycopg2): a escrita de cada tabela roda numa unica
    transacao com commit/rollback explicitos em `publish_window`."""
    return psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS)


def validate_identifier(name: str) -> str:
    """Nenhum identificador chega ao SQL sem passar por aqui. As constantes deste
    modulo sao fixas; a validacao existe para que uma edicao futura descuidada
    falhe alto em vez de virar injecao."""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"identificador interno falhou na validacao de seguranca: {name!r}")
    return name


def validate_qualified(name: str) -> str:
    if not _QUALIFIED_RE.match(name):
        raise ValueError(f"nome qualificado falhou na validacao de seguranca: {name!r}")
    return name


# ---------------------------------------------------------------------------
# Sanitizacao de erro — categorias fixas, zero topologia
# ---------------------------------------------------------------------------
# A versao anterior removia apenas `usuario:senha@` e preservava o resto. Isso
# nao bastava: a mensagem nativa do libpq e' da forma
# `connection to server at "<host>" (<ip>), port <porta> failed: ...`, e um
# timeout de VPN escrevia hostname e IP privado no log de execucao — topologia
# interna vazando para quem lesse o log, sem nenhum ganho diagnostico.
#
# A regra agora e' a inversa: mensagem de conexao NUNCA e' ecoada. Ela e'
# CLASSIFICADA numa categoria fixa, escolhida para dizer o que o operador precisa
# fazer (checar credencial, checar regra de acesso, checar VPN) sem revelar onde
# o banco fica. Mensagens de diagnostico sem topologia — validacao, constraint,
# timeout de statement — continuam preservadas, porque sao uteis e inofensivas.

#: Limite maximo da mensagem devolvida.
MAX_ERRO_CHARS = 500

#: Categorias FIXAS. Texto identico ao do modulo de serving da gestao diaria ML,
#: para que a mesma falha produza a mesma mensagem nas duas frentes.
ERRO_AUTENTICACAO = "falha de autenticacao no banco: credencial recusada pelo servidor."
ERRO_PG_HBA = "conexao recusada por regra de acesso do servidor (pg_hba.conf)."
ERRO_INALCANCAVEL = "servidor inalcancavel ou timeout de conexao (verifique a VPN)."
ERRO_RECUSADA = "conexao recusada pelo servidor (porta fechada ou servico parado)."
ERRO_CONEXAO = "falha de conexao com o banco."

_AUTENTICACAO_RE = re.compile(
    r"password authentication failed"
    r"|authentication failed"
    r"|no password supplied"
    r"|password supplied is not"
    r"|role\s+\"[^\"]*\"\s+does not exist",
    re.I,
)
_PG_HBA_RE = re.compile(r"pg_hba\.conf|no pg_hba entry", re.I)
_INALCANCAVEL_RE = re.compile(
    r"timed out"
    r"|timeout expired"
    r"|could not translate host name"
    r"|name or service not known"
    r"|temporary failure in name resolution"
    r"|no route to host"
    r"|network is unreachable"
    r"|host is unreachable",
    re.I,
)
_RECUSADA_RE = re.compile(r"connection refused|couldn't connect to server", re.I)
_CONEXAO_RE = re.compile(
    r"could not connect"
    r"|connection to server"
    r"|server closed the connection"
    r"|connection has been closed"
    r"|terminating connection"
    r"|database\s+\"[^\"]*\"\s+does not exist",
    re.I,
)

#: Marcadores de topologia. Se qualquer um aparecer, o texto original NUNCA e'
#: ecoado, mesmo que nenhuma categoria especifica case.
_IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
#: IPv6 exige `::` ou pelo menos 5 grupos. Sem isso, um horario como `10:20:30`
#: seria confundido com endereco e apagaria uma mensagem legitima.
_IPV6_RE = re.compile(
    r"""(?<![\w:])(
        (?:[0-9a-f]{1,4}:){4,7}[0-9a-f]{1,4}
      | (?:[0-9a-f]{1,4}:){1,7}:(?:[0-9a-f]{1,4}:?){0,7}
      | ::(?:[0-9a-f]{1,4}:?)+
    )(?![\w:])""",
    re.I | re.X,
)
_TOPOLOGIA_RE = re.compile(
    r"server\s+at"
    r"|postgres(?:ql)?://"
    r"|\b(?:host|hostaddr|user|password|dbname|port|passfile|sslcert|sslkey)\s*="
    r"|\bport\s+\d+",
    re.I,
)
_CREDENCIAL_URI_RE = re.compile(r"//[^/\s@]+:[^/\s@]+@")


def _classificar_erro_conexao(texto: str) -> str | None:
    """Categoria fixa para falha de conexao conhecida, ou `None` se nao for uma."""
    if _AUTENTICACAO_RE.search(texto):
        return ERRO_AUTENTICACAO
    if _PG_HBA_RE.search(texto):
        return ERRO_PG_HBA
    if _INALCANCAVEL_RE.search(texto):
        return ERRO_INALCANCAVEL
    if _RECUSADA_RE.search(texto):
        return ERRO_RECUSADA
    if _CONEXAO_RE.search(texto):
        return ERRO_CONEXAO
    return None


def tem_topologia(texto: str) -> bool:
    """DSN, hostname, IPv4/IPv6, porta, usuario, senha ou nome de database."""
    return bool(
        _TOPOLOGIA_RE.search(texto)
        or _IPV4_RE.search(texto)
        or _IPV6_RE.search(texto)
    )


def sanitize_error_message(exc: Exception) -> str:
    """Mensagem segura para log: categoria fixa quando ha conexao ou topologia.

    Usa `str(exc)` de proposito, nunca `repr(exc)` (que carrega os argumentos da
    excecao) e nunca `exc.__cause__`/traceback: a cadeia de excecoes do psycopg2
    guarda a mensagem nativa completa, e reproduzi-la anularia esta funcao.
    """
    texto = str(exc)
    categoria = _classificar_erro_conexao(texto)
    if categoria is not None:
        return categoria
    if tem_topologia(texto):
        return ERRO_CONEXAO
    # Sem topologia e sem cara de conexao: e' diagnostico util (validacao,
    # constraint, timeout de statement). Preserva, com a redacao de credencial
    # mantida como defesa em profundidade.
    return _CREDENCIAL_URI_RE.sub("//<redacted>@", texto)[:MAX_ERRO_CHARS]


def sanitize_run_id(raw: str) -> str:
    return _RUN_ID_RE.sub("_", raw)[:64]


def default_run_id(spec: TableSpec, now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return sanitize_run_id(f"sync_tiktok_{spec.name}:{stamp}")


# ---------------------------------------------------------------------------
# Validacao de janela — regra unica de D-1
# ---------------------------------------------------------------------------

#: Fuso do negocio. O dia operacional e' o dia no Brasil, nao o do processo: este
#: modulo roda hoje numa maquina Windows, amanha num worker que pode estar em UTC.
#: Sem isso, entre 21h e 00h no Brasil (00h-03h UTC) o processo ja teria virado o
#: dia e publicaria uma janela deslocada. `zoneinfo` e' biblioteca padrao.
TZ_OPERACIONAL = ZoneInfo("America/Sao_Paulo")


def hoje_operacional(agora: datetime | None = None) -> date:
    """Data corrente em America/Sao_Paulo, independente do fuso do processo."""
    return (agora or datetime.now(timezone.utc)).astimezone(TZ_OPERACIONAL).date()


def last_closed_date(today: date | None = None) -> date:
    """Ultimo dia FECHADO: `today - 1`. Regra unica de toda a janela deste modulo.

    O dia corrente NUNCA e' publicado. A razao e' de dado, nao de estilo: o dia em
    andamento esta incompleto por definicao, e publicar um total parcial obrigaria
    a proxima execucao a corrigi-lo — qualquer leitura no meio veria um numero que
    nao e' o do dia.
    """
    return (today or hoje_operacional()) - timedelta(days=1)


def require_closed_day(spec: TableSpec, today: date | None = None) -> date:
    fechado = last_closed_date(today)
    if fechado < spec.source_min_date:
        raise ValueError(
            f"ainda nao existe dia fechado a partir do primeiro dado de "
            f"{spec.source_relation} ({spec.source_min_date}): o ultimo dia "
            f"fechado seria {fechado}."
        )
    return fechado


def months_before(reference: date, months: int) -> date:
    """`reference` menos `months` meses, por aritmetica de calendario.

    Escrito a mao de proposito: acrescentar `dateutil` so para isto seria
    dependencia nova sem justificativa.
    """
    total = (reference.year * 12 + reference.month - 1) - months
    ano, mes = divmod(total, 12)
    mes += 1
    dia = min(reference.day, [31, 29 if ano % 4 == 0 and (ano % 100 != 0 or ano % 400 == 0) else 28,
                              31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1])
    return date(ano, mes, dia)


def history_deficit_days(spec: TableSpec, today: date | None = None) -> int:
    """Quantos dias faltam para a fonte cobrir `MIN_HISTORY_MONTHS`. Zero ou
    negativo significa cobertura suficiente. Serve para DECLARAR o deficit, nunca
    para reprovar: o piso de 13 meses vale quando a fonte o possui."""
    fechado = last_closed_date(today)
    piso = months_before(fechado, MIN_HISTORY_MONTHS)
    return (spec.source_min_date - piso).days


def validate_window(spec: TableSpec, date_from: date, date_to: date,
                    today: date | None = None) -> tuple[date, date]:
    """Janela fechada e sã. Recusa invertida, futura e o **dia corrente**."""
    today = today or hoje_operacional()
    fechado = last_closed_date(today)
    if not isinstance(date_from, date) or not isinstance(date_to, date):
        raise ValueError("date_from e date_to precisam ser datas.")
    if date_from > date_to:
        raise ValueError(f"janela invertida: date_from ({date_from}) > date_to ({date_to}).")
    if date_to == today:
        raise ValueError(
            f"dia corrente recusado: o dado de {today} ainda esta incompleto. "
            f"A janela vai no maximo ate o ultimo dia fechado ({fechado})."
        )
    if date_to > today:
        raise ValueError(
            f"janela futura: date_to ({date_to}) e' posterior a hoje ({today}). "
            f"O maximo e' o ultimo dia fechado ({fechado})."
        )
    if date_to > fechado:
        raise ValueError(f"date_to ({date_to}) passa do ultimo dia fechado ({fechado}).")
    if date_from < spec.source_min_date:
        raise ValueError(
            f"date_from ({date_from}) e' anterior ao primeiro dado de "
            f"{spec.source_relation} ({spec.source_min_date})."
        )
    return date_from, date_to


def incremental_window(spec: TableSpec, today: date | None = None,
                       lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[date, date]:
    """Janela movel de `lookback_days` dias COMPLETOS, terminando em D-1.

    A sobreposicao entre execucoes e' DELIBERADA: absorve late-arriving data e
    converge por idempotencia, porque cada execucao reescreve a janela inteira.
    """
    if lookback_days < MIN_LOOKBACK_DAYS:
        raise ValueError(
            f"lookback_days precisa ser >= {MIN_LOOKBACK_DAYS} (dias fechados): "
            f"recebido {lookback_days}."
        )
    fechado = require_closed_day(spec, today)
    return max(spec.source_min_date, fechado - timedelta(days=lookback_days - 1)), fechado


def backfill_window(spec: TableSpec, today: date | None = None) -> tuple[date, date]:
    """Todo o historico disponivel, do primeiro dado da fonte ate D-1."""
    return validate_window(spec, spec.source_min_date, require_closed_day(spec, today), today)


# ---------------------------------------------------------------------------
# Leitura da fonte — lista explicita de colunas, janela exata, zero SELECT *
# ---------------------------------------------------------------------------

def build_source_query(spec: TableSpec) -> str:
    """Colunas explicitas, janela parametrizada e marca parametrizada.

    Nenhum valor da allowlist e' interpolado no texto do SQL: `brand = ANY(%(brands)s)`
    manda a lista como PARAMETRO. Interpolar `IN ('a','b')` a mao funcionaria hoje
    e viraria injecao no dia em que a lista vier de fora do codigo.
    """
    cols = ", ".join(validate_identifier(c) for c in spec.business_columns)
    dcol = validate_identifier(spec.date_column)
    order = ", ".join(validate_identifier(c) for c in spec.key_columns)
    return (
        f"SELECT {cols} "
        f"FROM {validate_qualified(spec.source_relation)} "
        f"WHERE {dcol} BETWEEN %(date_from)s AND %(date_to)s "
        f"AND brand = ANY(%(brands)s) "
        f"ORDER BY {order}"
    )


def source_params(date_from: date, date_to: date) -> dict:
    """Parametros da leitura da fonte. A allowlist vai como lista de parametro."""
    return {"date_from": date_from, "date_to": date_to, "brands": list(ALLOWED_BRANDS)}


def fetch_source_rows(datamart_conn, spec: TableSpec, date_from: date, date_to: date) -> list[dict]:
    cur = datamart_conn.cursor()
    cur.execute(f"SET statement_timeout = '{SOURCE_STATEMENT_TIMEOUT}'")
    cur.execute(build_source_query(spec), source_params(date_from, date_to))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


# ---------------------------------------------------------------------------
# Reconciliacao — funcoes puras, testaveis com listas
# ---------------------------------------------------------------------------

def _dec(value) -> Decimal:
    """Converte para `Decimal` SEM passar por `float` em nenhum caminho.

    Por que isto importa: `float` tem 53 bits de mantissa, e somar ~197 mil
    valores monetarios em ponto flutuante acumula erro de representacao. O
    Postgres soma `NUMERIC` em decimal exato, entao a reconciliacao compararia
    dois numeros calculados em aritmeticas diferentes e poderia divergir por
    centavos sem que nada estivesse errado no dado — ou, pior, esconder uma
    divergencia real dentro da margem de erro.

    Nulo vira `Decimal("0")` apenas nas somas, onde o contrato vigente do
    endpoint ja trata nulo como zero (`COALESCE(..., 0)`). Isso NAO afeta a
    contagem de nao-nulos das razoes nem a validacao de obrigatorias.
    """
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError(f"booleano nao e' metrica reconciliavel: {value!r}")
    if isinstance(value, int):
        return Decimal(value)
    # float e str: `Decimal(str(x))` preserva a representacao decimal do valor
    # recebido. Nunca `Decimal(float)`, que herdaria o erro binario.
    return Decimal(str(value))


def _is_nan(value) -> bool:
    """NaN e' pior que nulo: passa por CHECK `>= 0` no Postgres e contamina
    qualquer soma. Detectado sem converter para `float`."""
    if isinstance(value, Decimal):
        return value.is_nan()
    if isinstance(value, float):
        return value != value
    if isinstance(value, str):
        try:
            return Decimal(value).is_nan()
        except InvalidOperation:
            return False
    return False


def aggregates_from_rows(spec: TableSpec, rows: list[dict]) -> dict:
    """Contagem, min/max da data e somas das metricas ADITIVAS.

    As colunas de RAZAO ficam de fora de proposito: somar percentual nao tem
    significado, e a soma nem seria estavel (sao 100% nulos na fonte hoje).
    """
    dcol = spec.date_column
    agg = {
        "count": len(rows),
        "min_date": min((r[dcol] for r in rows), default=None),
        "max_date": max((r[dcol] for r in rows), default=None),
        "distinct_dates": len({r[dcol] for r in rows}),
        "distinct_brands": len({r["brand"] for r in rows}),
    }
    for c in spec.additive_columns:
        # Soma decimal exata, SEM arredondar. Arredondar antes de comparar
        # esconderia divergencia real dentro da tolerancia.
        agg[f"sum_{c}"] = sum((_dec(r[c]) for r in rows), Decimal("0"))
    for c in spec.ratio_columns:
        agg[f"nn_{c}"] = sum(1 for r in rows if r.get(c) is not None)
    return agg


def duplicates_in_rows(spec: TableSpec, rows: list[dict]) -> int:
    seen, dup = set(), 0
    for r in rows:
        k = tuple(r[c] for c in spec.key_columns)
        if k in seen:
            dup += 1
        seen.add(k)
    return dup


def missing_required(spec: TableSpec, rows: list[dict]) -> dict:
    return {c: sum(1 for r in rows if r.get(c) is None) for c in spec.required_columns}


def nan_in_rows(spec: TableSpec, rows: list[dict]) -> dict:
    """NaN e' pior que nulo: passa por CHECK `>= 0` no Postgres e contamina
    qualquer soma. Reprovado na fonte, antes de qualquer escrita."""
    out = {}
    for c in spec.additive_columns + spec.ratio_columns:
        n = sum(1 for r in rows if _is_nan(r.get(c)))
        if n:
            out[c] = n
    return out


def negatives_in_rows(spec: TableSpec, rows: list[dict]) -> dict:
    """Negativos nas aditivas que NAO sao assinadas. `total_fees` e
    `total_live_minutes` ficam fora: a origem as tem negativas e o contrato desta
    task e' copiar exatamente."""
    out = {}
    for c in spec.additive_columns:
        if c in spec.signed_columns:
            continue
        n = sum(1 for r in rows
                if r.get(c) is not None and not _is_nan(r[c]) and _dec(r[c]) < 0)
        if n:
            out[c] = n
    return out


def foreign_brands_in_rows(rows: list[dict]) -> set[str]:
    """Marcas fora da allowlist oficial. Defesa em profundidade: a query da fonte
    ja filtra por parametro, e isto reprova a fotografia caso o filtro seja
    removido por engano numa edicao futura."""
    return {r["brand"] for r in rows if r.get("brand") not in ALLOWED_BRANDS}


def date_coverage(spec: TableSpec, rows: list[dict], date_from: date, date_to: date) -> dict:
    """Cobertura por DIA, nunca por (dia x marca) nem (dia x criador).

    Marca sem movimento no dia simplesmente nao aparece na fonte, e criador sem
    postagem tambem nao. Exigir o produto cartesiano reprovaria a fonte real todo
    dia. O que precisa existir e' ao menos uma linha por dia do intervalo.
    """
    dcol = spec.date_column
    presentes = {r[dcol] for r in rows}
    esperados, d = [], date_from
    while d <= date_to:
        esperados.append(d)
        d += timedelta(days=1)
    faltando = [d for d in esperados if d not in presentes]
    return {
        "expected_days": len(esperados),
        "covered_days": len(presentes & set(esperados)),
        "missing_days": faltando,
        "complete": not faltando,
    }


def validate_source_rows(spec: TableSpec, rows: list[dict],
                         date_from: date, date_to: date) -> list[str]:
    """Todas as reprovacoes possiveis ANTES de qualquer escrita."""
    problemas = []
    dup = duplicates_in_rows(spec, rows)
    if dup:
        chave = ", ".join(spec.key_columns)
        problemas.append(f"{dup} chave(s) ({chave}) duplicada(s) na fonte")
    for c, n in missing_required(spec, rows).items():
        if n:
            problemas.append(f"{n} nulo(s) na coluna obrigatoria {c}")
    for c, n in nan_in_rows(spec, rows).items():
        problemas.append(f"{n} NaN na coluna {c}")
    for c, n in negatives_in_rows(spec, rows).items():
        problemas.append(f"{n} valor(es) negativo(s) na coluna {c}")
    estranhas = foreign_brands_in_rows(rows)
    if estranhas:
        # Quantidade, nunca os nomes: identificar as marcas excluidas nao e'
        # necessario para diagnosticar, e o relatorio nao precisa expo-las.
        problemas.append(
            f"{len(estranhas)} marca(s) fora da allowlist oficial na fotografia"
        )
    dcol = spec.date_column
    fora = [r[dcol] for r in rows if not (date_from <= r[dcol] <= date_to)]
    if fora:
        problemas.append(f"{len(fora)} linha(s) fora da janela pedida")
    cob = date_coverage(spec, rows, date_from, date_to)
    if not cob["complete"]:
        problemas.append(f"cobertura incompleta: {len(cob['missing_days'])} dia(s) sem linha")
    return problemas


def aggregates_from_table(conn, spec: TableSpec, schema_table: str,
                          date_from: date, date_to: date) -> dict:
    """Mesmos agregados, calculados no banco e restritos a janela."""
    dcol = validate_identifier(spec.date_column)
    sums = ", ".join(
        f"COALESCE(SUM({validate_identifier(c)}), 0) AS sum_{c}" for c in spec.additive_columns
    )
    nns = "".join(
        f", COUNT({validate_identifier(c)}) AS nn_{c}" for c in spec.ratio_columns
    )
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT COUNT(*) AS count, MIN({dcol}) AS min_date, MAX({dcol}) AS max_date,
               COUNT(DISTINCT {dcol}) AS distinct_dates,
               COUNT(DISTINCT brand) AS distinct_brands,
               {sums}{nns}
        FROM {schema_table}
        WHERE {dcol} BETWEEN %(date_from)s AND %(date_to)s
        """,
        {"date_from": date_from, "date_to": date_to},
    )
    row = dict(cur.fetchone())
    cur.close()
    out = {
        "count": int(row["count"]),
        "min_date": row["min_date"],
        "max_date": row["max_date"],
        "distinct_dates": int(row["distinct_dates"]),
        "distinct_brands": int(row["distinct_brands"]),
    }
    for c in spec.additive_columns:
        # `_dec` normaliza os dois lados: o Postgres devolve `Decimal` para
        # SUM(NUMERIC) e SUM(BIGINT), e `int` para SUM(INTEGER). Sem arredondar,
        # para que a comparacao seja de valor exato.
        out[f"sum_{c}"] = _dec(row[f"sum_{c}"])
    for c in spec.ratio_columns:
        out[f"nn_{c}"] = int(row[f"nn_{c}"])
    return out


def compare_aggregates(spec: TableSpec, source: dict, target: dict) -> list[str]:
    """Divergencia e' lista de motivos, nunca tolerancia silenciosa."""
    problemas = []
    for chave in ("count", "min_date", "max_date", "distinct_dates", "distinct_brands"):
        if source.get(chave) != target.get(chave):
            problemas.append(f"{chave}: fonte={source.get(chave)} destino={target.get(chave)}")
    for c in spec.additive_columns:
        k = f"sum_{c}"
        if source.get(k) != target.get(k):
            problemas.append(f"{k}: fonte={source.get(k)} destino={target.get(k)}")
    for c in spec.ratio_columns:
        k = f"nn_{c}"
        if source.get(k) != target.get(k):
            problemas.append(f"{k}: fonte={source.get(k)} destino={target.get(k)}")
    return problemas


def except_both_ways(conn, spec: TableSpec, table_a: str, table_b: str,
                     date_from: date, date_to: date) -> tuple[int, int]:
    """`EXCEPT` bidirecional **somente nas colunas de negocio**.

    `synced_at`/`source_run_id` ficam fora: sao gerados no destino e sempre
    difeririam, transformando a comparacao em ruido. Isso tambem e' o que torna a
    contraprova de idempotencia possivel — comparar a linha inteira mediria a
    auditoria, nao o negocio.
    """
    cols = ", ".join(validate_identifier(c) for c in spec.business_columns)
    dcol = validate_identifier(spec.date_column)
    where = f"WHERE {dcol} BETWEEN %(date_from)s AND %(date_to)s"
    params = {"date_from": date_from, "date_to": date_to}
    cur = conn.cursor()
    cur.execute(
        f"SELECT COUNT(*) AS n FROM ("
        f"SELECT {cols} FROM {table_a} {where} EXCEPT SELECT {cols} FROM {table_b} {where}) x",
        params,
    )
    a_not_b = int(cur.fetchone()["n"])
    cur.execute(
        f"SELECT COUNT(*) AS n FROM ("
        f"SELECT {cols} FROM {table_b} {where} EXCEPT SELECT {cols} FROM {table_a} {where}) x",
        params,
    )
    b_not_a = int(cur.fetchone()["n"])
    cur.close()
    return a_not_b, b_not_a


# ---------------------------------------------------------------------------
# Staging e publicacao atomica
# ---------------------------------------------------------------------------

def create_staging_table(cur, spec: TableSpec) -> None:
    cur.execute(f"""
        CREATE TEMP TABLE {validate_identifier(spec.staging_name)}
            (LIKE {validate_qualified(spec.target_table)} INCLUDING DEFAULTS)
        ON COMMIT DROP
    """)


def insert_into_staging(cur, spec: TableSpec, rows: list[dict], run_id: str) -> None:
    if not rows:
        return
    cols = spec.business_columns + ["source_run_id"]
    sql = f"INSERT INTO {spec.staging_qualified} ({', '.join(cols)}) VALUES %s"
    batch = [tuple(r[c] for c in spec.business_columns) + (run_id,) for r in rows]
    execute_values(cur, sql, batch, page_size=INSERT_PAGE_SIZE)


def publish_window(neon_conn, spec: TableSpec, rows: list[dict],
                   date_from: date, date_to: date, run_id: str) -> dict:
    """UMA transacao por tabela: lock -> staging -> validacao -> DELETE da janela
    -> INSERT -> verificacao. Qualquer falha faz `ROLLBACK` integral e o destino
    fica exatamente como estava. `ON COMMIT DROP` limpa a staging tambem no
    rollback.

    O advisory lock e' EXCLUSIVO desta tabela (`spec.advisory_lock_key`): as duas
    fatos do S2 podem publicar em paralelo sem se bloquear, e duas execucoes da
    MESMA tabela nunca se sobrepoem.
    """
    resultado = {"table": spec.target_table, "published": 0, "deleted": 0, "checks": {}}
    dcol = validate_identifier(spec.date_column)
    target = validate_qualified(spec.target_table)
    cur = neon_conn.cursor()
    try:
        cur.execute(f"SET LOCAL statement_timeout = '{TARGET_STATEMENT_TIMEOUT}'")
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (spec.advisory_lock_key,))

        create_staging_table(cur, spec)
        insert_into_staging(cur, spec, rows, run_id)

        staging_agg = aggregates_from_table(neon_conn, spec, spec.staging_qualified, date_from, date_to)
        source_agg = aggregates_from_rows(spec, rows)
        problemas = compare_aggregates(spec, source_agg, staging_agg)
        if problemas:
            raise RuntimeError("staging divergiu da fonte: " + "; ".join(problemas))

        cur.execute(
            f"DELETE FROM {target} WHERE {dcol} BETWEEN %(date_from)s AND %(date_to)s",
            {"date_from": date_from, "date_to": date_to},
        )
        resultado["deleted"] = cur.rowcount

        cols = ", ".join(spec.business_columns + AUDIT_COLUMNS)
        cur.execute(f"""
            INSERT INTO {target} ({cols})
            SELECT {cols} FROM {spec.staging_qualified}
        """)
        resultado["published"] = cur.rowcount

        final_agg = aggregates_from_table(neon_conn, spec, target, date_from, date_to)
        problemas = compare_aggregates(spec, source_agg, final_agg)
        if problemas:
            raise RuntimeError("destino divergiu da fonte apos o insert: " + "; ".join(problemas))

        a_not_b, b_not_a = except_both_ways(
            neon_conn, spec, spec.staging_qualified, target, date_from, date_to
        )
        if a_not_b or b_not_a:
            raise RuntimeError(
                f"EXCEPT bidirecional divergiu: staging-destino={a_not_b} destino-staging={b_not_a}"
            )

        resultado["checks"] = {
            "aggregates_match": True,
            "except_both_ways": (a_not_b, b_not_a),
            "source": source_agg,
            "target": final_agg,
        }
        neon_conn.commit()
        return resultado
    except Exception:
        neon_conn.rollback()
        raise
    finally:
        cur.close()


# ---------------------------------------------------------------------------
# Modos de execucao
# ---------------------------------------------------------------------------

def _print_agg(rotulo: str, spec: TableSpec, agg: dict, indent: str = "    ") -> None:
    """Formata para leitura humana. NAO altera o valor usado na validacao: le do
    dicionario e imprime, sem escrever de volta nem arredondar o `Decimal`."""
    print(f"{indent}{rotulo}")
    print(f"{indent}  linhas={agg['count']}  datas={agg['distinct_dates']}  marcas={agg['distinct_brands']}")
    print(f"{indent}  {spec.date_column}: {agg['min_date']} a {agg['max_date']}")
    for c in spec.additive_columns:
        # `:f` evita notacao cientifica sem tocar no valor original
        print(f"{indent}  sum_{c} = {agg[f'sum_{c}']:f}")
    if spec.ratio_columns:
        nn = sum(agg[f"nn_{c}"] for c in spec.ratio_columns)
        print(f"{indent}  razoes nao-nulas (total das {len(spec.ratio_columns)} colunas) = {nn}"
              f"  (razao, nunca somada)")


def diagnose(spec: TableSpec, date_from: date, date_to: date, today: date) -> int:
    """Somente leitura em AMBOS os bancos. Nenhuma escrita, nenhuma staging."""
    print(f"[diagnose:{spec.name}] {spec.source_relation} -> {spec.target_table}")
    print(f"  janela {date_from} a {date_to}  (SOMENTE LEITURA)")
    print(f"  allowlist oficial: {len(ALLOWED_BRANDS)} marcas (filtro parametrizado na fonte)")
    deficit = history_deficit_days(spec, today)
    if deficit > 0:
        print(f"  cobertura historica: a fonte comeca em {spec.source_min_date}, "
              f"{deficit} dia(s) DEPOIS do piso de {MIN_HISTORY_MONTHS} meses. "
              f"O piso vale quando a fonte o possui; aqui o backfill leva todo o "
              f"historico disponivel.")
    else:
        print(f"  cobertura historica: >= {MIN_HISTORY_MONTHS} meses, OK.")

    src = _datamart_readonly(_get_datamart_url())
    try:
        rows = fetch_source_rows(src, spec, date_from, date_to)
    finally:
        src.close()

    _print_agg(f"fonte ({spec.source_relation}):", spec, aggregates_from_rows(spec, rows))
    cob = date_coverage(spec, rows, date_from, date_to)
    print(f"    cobertura: {cob['covered_days']}/{cob['expected_days']} dias com linha")
    problemas = validate_source_rows(spec, rows, date_from, date_to)
    print(f"    validacoes da fonte: {'OK' if not problemas else '; '.join(problemas)}")

    neon = _neon_readonly(_get_neon_url())
    try:
        cur = neon.cursor()
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS existe", (spec.target_table,))
        existe = cur.fetchone()["existe"]
        cur.close()
        if not existe:
            print(f"    destino {spec.target_table}: NAO EXISTE (migration nao aplicada)")
        else:
            _print_agg(f"destino ({spec.target_table}, janela):", spec,
                       aggregates_from_table(neon, spec, spec.target_table, date_from, date_to))
    finally:
        neon.close()
    return 0 if not problemas else 2


def apply_window(spec: TableSpec, date_from: date, date_to: date, run_id: str) -> int:
    """Leitura da fonte, depois publicacao. A fonte e' fechada ANTES de abrir a
    conexao de escrita: a publicacao usa a fotografia em memoria, e nenhuma
    conexao de leitura da Gold fica aberta durante a transacao de escrita."""
    print(f"[apply:{spec.name}] janela {date_from} a {date_to}  run_id={run_id}")
    src = _datamart_readonly(_get_datamart_url())
    try:
        rows = fetch_source_rows(src, spec, date_from, date_to)
    finally:
        src.close()

    problemas = validate_source_rows(spec, rows, date_from, date_to)
    if problemas:
        raise RuntimeError("fonte reprovada, nada foi escrito: " + "; ".join(problemas))

    neon = _neon_writable(_get_neon_url())
    try:
        resultado = publish_window(neon, spec, rows, date_from, date_to, run_id)
    finally:
        neon.close()

    print(f"  apagadas na janela: {resultado['deleted']}   publicadas: {resultado['published']}")
    _print_agg("fonte:", spec, resultado["checks"]["source"])
    _print_agg("destino:", spec, resultado["checks"]["target"])
    print(f"  EXCEPT bidirecional: {resultado['checks']['except_both_ways']}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sync_tiktok_serving",
        description=(
            "Sync incremental das duas fatos TikTok de serving: "
            "gold.tiktok_brand_daily -> marts.fact_tiktok_brand_content_daily e "
            "gold.tiktok_creator_daily -> marts.fact_tiktok_creator_daily. "
            "Toda janela termina no ultimo dia fechado (D-1): o dia corrente nunca "
            "e' publicado. Sem --apply nada e' escrito. Nao agenda, nao repete e "
            "nao tenta de novo."
        ),
    )
    p.add_argument("--table", choices=("brand", "creator", "all"), default="all",
                   help="qual fato sincronizar (default: all, cada uma em transacao propria)")
    p.add_argument("--backfill", action="store_true",
                   help=("carga historica: do primeiro dado da fonte ate o ultimo dia "
                         "fechado (D-1). O dia corrente nunca e' publicado."))
    p.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                   help=(f"janela movel de N dias COMPLETOS, terminando no ultimo dia "
                         f"fechado (D-1). Default {DEFAULT_LOOKBACK_DAYS}, minimo "
                         f"{MIN_LOOKBACK_DAYS}."))
    p.add_argument("--date-from", help="inicio explicito da janela (YYYY-MM-DD)")
    p.add_argument("--date-to", help="fim explicito da janela (YYYY-MM-DD), no maximo D-1")
    p.add_argument("--apply", action="store_true",
                   help="EFETIVA a escrita no Neon. Sem esta flag, tudo e' somente leitura.")
    p.add_argument("--run-id", help="identificador da execucao (sanitizado)")
    return p


def resolve_window_from_args(spec: TableSpec, args: argparse.Namespace,
                             today: date | None = None) -> tuple[date, date]:
    today = today or hoje_operacional()
    if args.backfill:
        if args.date_from or args.date_to:
            raise ValueError("--backfill nao combina com --date-from/--date-to.")
        return backfill_window(spec, today)
    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            raise ValueError("--date-from e --date-to precisam vir juntos.")
        return validate_window(spec, date.fromisoformat(args.date_from),
                               date.fromisoformat(args.date_to), today)
    return incremental_window(spec, today, args.lookback_days)


def selected_specs(args: argparse.Namespace) -> list[TableSpec]:
    if args.table == "all":
        return [BRAND_SPEC, CREATOR_SPEC]
    return [SPECS[args.table]]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.apply:
        print("MODO DIAGNOSTICO (sem --apply): nenhuma escrita sera feita.")
    codigo = 0
    for spec in selected_specs(args):
        try:
            date_from, date_to = resolve_window_from_args(spec, args)
            if args.apply:
                run_id = sanitize_run_id(args.run_id) if args.run_id else default_run_id(spec)
                codigo = apply_window(spec, date_from, date_to, run_id) or codigo
            else:
                codigo = diagnose(spec, date_from, date_to, hoje_operacional()) or codigo
        except Exception as exc:  # noqa: BLE001 — fronteira do CLI
            print(f"FALHA ({spec.name}): {sanitize_error_message(exc)}", file=sys.stderr)
            return 2
    return codigo


if __name__ == "__main__":
    raise SystemExit(main())
