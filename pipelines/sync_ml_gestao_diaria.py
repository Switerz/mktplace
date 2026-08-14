"""Gate S1 — sync incremental Data Mart -> Neon de gold.ml_gestao_diaria.

Fonte (somente leitura): Data Mart RDS, DATAMART_DATABASE_URL, view
`gold.ml_gestao_diaria`. Destino (escrita): Neon, DATABASE_URL,
`marts.fact_ml_gestao_diaria` (migration 006).

Reutiliza o padrao de seguranca de `pipelines/sync_region_daily.py` — conexoes
explicitas sem fallback, staging TEMP `ON COMMIT DROP`, validacao antes da
publicacao, transacao unica, erro sanitizado — mas com estrategia DIFERENTE:
aquele faz substituicao integral (`TRUNCATE`+`INSERT`); aqui a publicacao e'
**por janela**, com `DELETE` apenas do intervalo + `INSERT` da staging. Nunca
`TRUNCATE` em sincronizacao incremental.

Por que `DELETE` da janela em vez de puro `ON CONFLICT DO UPDATE`: upsert nao
apaga. Se uma linha desaparecer da fonte dentro da janela, um upsert a deixaria
para tras no destino, indefinidamente. Apagar a janela e reinserir faz o destino
refletir a fonte, inclusive remocoes — e e' o que torna a execucao idempotente.

Fatos da auditoria read-only da fonte (11/08/2026) que moldam este modulo:

- a origem e' uma **VIEW** sobre `raw.ml_*` (5 tabelas), nao uma tabela: cada
  extracao recomputa a view, e nao existe `pg_total_relation_size` da fonte;
- `(ref_date, brand)` e' UNICO (0 duplicados em 1.625 linhas / 472 datas);
- `roas` e' RAZAO com 906 nulos: nao entra em soma de reconciliacao e nao pode
  ser exigida como obrigatoria;
- **99 datas tem cobertura PARCIAL de marcas** (marca sem movimento no dia
  simplesmente nao aparece). Portanto a validacao de cobertura e' por **dia**,
  nunca por (dia x marca): exigir todas as marcas em todo dia reprovaria a
  fonte real todos os dias.

Toda janela termina no **ultimo dia fechado (D-1)**: o dia corrente nunca e'
publicado, porque a view de origem o entrega incompleto e um total parcial gravado
teria de ser corrigido pela execucao seguinte.

Nao ha retry, backoff, agendamento nem loop: repeticao e' decisao de quem chama.
Nada aqui altera `gold_service.py` — `/operacoes` continua lendo a gold ate o S2.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

# ---------------------------------------------------------------------------
# Contrato de colunas — EXPLICITO e versionado (§4.2 do blueprint)
# ---------------------------------------------------------------------------

#: Chave do grao. Provada unica na fonte pela auditoria do Gate S1.
KEY_COLUMNS = ["ref_date", "brand"]

#: Metricas ADITIVAS: podem ser somadas em reconciliacao.
ADDITIVE_COLUMNS = ["gmv", "ad_spend", "ad_revenue", "paid_orders"]

#: Metricas de RAZAO: copiadas como servidas, NUNCA somadas.
RATIO_COLUMNS = ["roas"]

#: Colunas de negocio copiadas da fonte. Uniao exata do que `get_operacoes`
#: consome de `gold.ml_gestao_diaria` (linhas 2326 e 2368 de gold_service.py).
#: A view de origem tem 37 colunas; copiamos 7. Acrescentar coluna aqui exige
#: migration aditiva e revisao de contrato — nao existe "veio de graca".
BUSINESS_COLUMNS = KEY_COLUMNS + ADDITIVE_COLUMNS + RATIO_COLUMNS

#: Colunas geradas no destino. Nunca entram na comparacao de negocio.
AUDIT_COLUMNS = ["synced_at", "source_run_id"]

#: Colunas que nao admitem nulo (a chave e as aditivas). `roas` fica fora de
#: proposito: a fonte a deixa nula em 906 de 1.625 linhas.
REQUIRED_COLUMNS = KEY_COLUMNS + ADDITIVE_COLUMNS

SOURCE_RELATION = "gold.ml_gestao_diaria"
TARGET_TABLE = "marts.fact_ml_gestao_diaria"
STAGING_TABLE_NAME = "sync_ml_gestao_diaria_staging"
STAGING_TABLE_QUALIFIED = f"pg_temp.{STAGING_TABLE_NAME}"

#: Advisory lock especifico desta tabela: duas execucoes concorrentes da mesma
#: janela nunca se sobrepoem. O numero e' arbitrario mas fixo e exclusivo.
ADVISORY_LOCK_KEY = 906_120_006

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

#: Primeira data com dado na fonte, medida na auditoria. Serve de piso ao
#: backfill e de validacao de sanidade das datas pedidas.
SOURCE_MIN_DATE = date(2025, 4, 27)

SOURCE_STATEMENT_TIMEOUT = "60s"
TARGET_STATEMENT_TIMEOUT = "120s"
CONNECT_TIMEOUT_SECONDS = 15

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
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
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    conn.set_session(readonly=True)
    return conn


def _neon_readonly(url: str):
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    conn.set_session(readonly=True)
    return conn


def _neon_writable(url: str):
    """Sem `readonly=True` — usada exclusivamente sob `--apply`. Autocommit fica
    desligado (padrao do psycopg2): toda a escrita roda numa unica transacao com
    commit/rollback explicitos em `publish_window`."""
    return psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=CONNECT_TIMEOUT_SECONDS)


def validate_identifier(name: str) -> str:
    """Nenhum identificador chega ao SQL sem passar por aqui. As constantes deste
    modulo sao fixas; a validacao existe para que uma edicao futura descuidada
    falhe alto em vez de virar injecao."""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"identificador interno falhou na validacao de seguranca: {name!r}")
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

#: Categorias FIXAS. Texto identico ao do modulo de serving do TikTok, para que a
#: mesma falha produza a mesma mensagem nas duas frentes.
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
    """`source_run_id` e' rastreavel e inofensivo: so alfanumerico, `_`, `-` e
    `:`, com tamanho limitado pela coluna."""
    return _RUN_ID_RE.sub("_", raw)[:64]


def default_run_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return sanitize_run_id(f"sync_ml_gestao_diaria:{stamp}")


# ---------------------------------------------------------------------------
# Validacao de janela
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

    O dia corrente NUNCA e' publicado. O contrato do blueprint para series
    diarias e' D-N a D-1, e a razao e' de dado, nao de estilo: a fonte e' uma view
    sobre `raw.ml_*` e o dia em andamento esta incompleto por definicao — publicar
    hoje gravaria um total parcial que a proxima execucao teria de corrigir, e
    qualquer leitura no meio do caminho veria um numero que nao e' o do dia.
    """
    return (today or hoje_operacional()) - timedelta(days=1)


def require_closed_day(today: date | None = None) -> date:
    """`last_closed_date`, recusando o caso em que ainda nao existe dia fechado
    dentro do intervalo que a fonte cobre."""
    fechado = last_closed_date(today)
    if fechado < SOURCE_MIN_DATE:
        raise ValueError(
            f"ainda nao existe dia fechado a partir do primeiro dado da fonte "
            f"({SOURCE_MIN_DATE}): o ultimo dia fechado seria {fechado}."
        )
    return fechado


def validate_window(date_from: date, date_to: date, today: date | None = None) -> tuple[date, date]:
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
        raise ValueError(
            f"date_to ({date_to}) passa do ultimo dia fechado ({fechado})."
        )
    if date_from < SOURCE_MIN_DATE:
        raise ValueError(
            f"date_from ({date_from}) e' anterior ao primeiro dado da fonte ({SOURCE_MIN_DATE})."
        )
    return date_from, date_to


def incremental_window(today: date | None = None, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[date, date]:
    """Janela movel de `lookback_days` dias COMPLETOS, terminando no ultimo dia
    fechado (D-1). Sobreposicao e' deliberada: absorve late-arriving data e
    converge por idempotencia."""
    if lookback_days < MIN_LOOKBACK_DAYS:
        raise ValueError(
            f"lookback_days precisa ser >= {MIN_LOOKBACK_DAYS} (dias fechados): "
            f"recebido {lookback_days}."
        )
    fechado = require_closed_day(today)
    return max(SOURCE_MIN_DATE, fechado - timedelta(days=lookback_days - 1)), fechado


# ---------------------------------------------------------------------------
# Leitura da fonte — lista explicita de colunas, janela exata, zero SELECT *
# ---------------------------------------------------------------------------

def build_source_query() -> str:
    cols = ", ".join(validate_identifier(c) for c in BUSINESS_COLUMNS)
    return (
        f"SELECT {cols} "
        f"FROM {SOURCE_RELATION} "
        f"WHERE ref_date BETWEEN %(date_from)s AND %(date_to)s "
        f"ORDER BY ref_date, brand"
    )


def fetch_source_rows(datamart_conn, date_from: date, date_to: date) -> list[dict]:
    cur = datamart_conn.cursor()
    cur.execute(f"SET statement_timeout = '{SOURCE_STATEMENT_TIMEOUT}'")
    cur.execute(build_source_query(), {"date_from": date_from, "date_to": date_to})
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


# ---------------------------------------------------------------------------
# Reconciliacao — puras, testaveis com listas
# ---------------------------------------------------------------------------

def _num(x) -> float:
    return 0.0 if x is None else float(x)


def aggregates_from_rows(rows: list[dict]) -> dict:
    """Contagem, min/max da data e somas das metricas ADITIVAS.

    `roas` fica de fora de proposito: e' razao. Somar razoes nao tem
    significado, e a soma nem seria estavel (906 nulos na fonte).
    """
    agg = {
        "count": len(rows),
        "min_date": min((r["ref_date"] for r in rows), default=None),
        "max_date": max((r["ref_date"] for r in rows), default=None),
        "distinct_dates": len({r["ref_date"] for r in rows}),
        "distinct_brands": len({r["brand"] for r in rows}),
    }
    for c in ADDITIVE_COLUMNS:
        agg[f"sum_{c}"] = round(sum(_num(r[c]) for r in rows), 2)
    agg["roas_not_null"] = sum(1 for r in rows if r.get("roas") is not None)
    return agg


def duplicates_in_rows(rows: list[dict]) -> int:
    seen, dup = set(), 0
    for r in rows:
        k = tuple(r[c] for c in KEY_COLUMNS)
        if k in seen:
            dup += 1
        seen.add(k)
    return dup


def missing_required(rows: list[dict]) -> dict:
    return {c: sum(1 for r in rows if r.get(c) is None) for c in REQUIRED_COLUMNS}


def date_coverage(rows: list[dict], date_from: date, date_to: date) -> dict:
    """Cobertura por DIA, nao por (dia x marca).

    A auditoria encontrou 99 datas com cobertura parcial de marcas na fonte:
    marca sem movimento no dia nao aparece. Exigir todas as marcas em todo dia
    reprovaria a fonte real. O que precisa existir e' **ao menos uma linha por
    dia** do intervalo.
    """
    presentes = {r["ref_date"] for r in rows}
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


def aggregates_from_table(conn, schema_table: str, date_from: date, date_to: date) -> dict:
    """Mesmos agregados, mas calculados no destino, restritos a janela."""
    sums = ", ".join(f"COALESCE(SUM({validate_identifier(c)}), 0) AS sum_{c}" for c in ADDITIVE_COLUMNS)
    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT COUNT(*) AS count, MIN(ref_date) AS min_date, MAX(ref_date) AS max_date,
               COUNT(DISTINCT ref_date) AS distinct_dates,
               COUNT(DISTINCT brand) AS distinct_brands,
               COUNT(roas) AS roas_not_null, {sums}
        FROM {schema_table}
        WHERE ref_date BETWEEN %(date_from)s AND %(date_to)s
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
        "roas_not_null": int(row["roas_not_null"]),
    }
    for c in ADDITIVE_COLUMNS:
        out[f"sum_{c}"] = round(_num(row[f"sum_{c}"]), 2)
    return out


def compare_aggregates(source: dict, target: dict) -> list[str]:
    """Divergencia e' lista de motivos, nunca tolerancia silenciosa."""
    problemas = []
    for chave in ["count", "min_date", "max_date", "distinct_dates", "distinct_brands", "roas_not_null"]:
        if source.get(chave) != target.get(chave):
            problemas.append(f"{chave}: fonte={source.get(chave)} destino={target.get(chave)}")
    for c in ADDITIVE_COLUMNS:
        k = f"sum_{c}"
        if source.get(k) != target.get(k):
            problemas.append(f"{k}: fonte={source.get(k)} destino={target.get(k)}")
    return problemas


def except_both_ways(conn, table_a: str, table_b: str, date_from: date, date_to: date) -> tuple[int, int]:
    """`EXCEPT` bidirecional **somente nas colunas de negocio**.

    `synced_at`/`source_run_id` ficam fora: sao gerados no destino e sempre
    difeririam, transformando a comparacao em ruido.
    """
    cols = ", ".join(validate_identifier(c) for c in BUSINESS_COLUMNS)
    where = "WHERE ref_date BETWEEN %(date_from)s AND %(date_to)s"
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


def validate_source_rows(rows: list[dict], date_from: date, date_to: date) -> list[str]:
    """Todas as reprovacoes possiveis ANTES de qualquer escrita."""
    problemas = []
    dup = duplicates_in_rows(rows)
    if dup:
        problemas.append(f"{dup} par(es) (ref_date, brand) duplicado(s) na fonte")
    for c, n in missing_required(rows).items():
        if n:
            problemas.append(f"{n} nulo(s) na coluna obrigatoria {c}")
    fora = [r["ref_date"] for r in rows if not (date_from <= r["ref_date"] <= date_to)]
    if fora:
        problemas.append(f"{len(fora)} linha(s) fora da janela pedida")
    cob = date_coverage(rows, date_from, date_to)
    if not cob["complete"]:
        problemas.append(f"cobertura incompleta: {len(cob['missing_days'])} dia(s) sem linha")
    return problemas


# ---------------------------------------------------------------------------
# Staging e publicacao atomica
# ---------------------------------------------------------------------------

def create_staging_table(cur) -> None:
    cur.execute(f"""
        CREATE TEMP TABLE {validate_identifier(STAGING_TABLE_NAME)}
            (LIKE {TARGET_TABLE} INCLUDING DEFAULTS)
        ON COMMIT DROP
    """)


def insert_into_staging(cur, rows: list[dict], run_id: str) -> None:
    if not rows:
        return
    cols = BUSINESS_COLUMNS + ["source_run_id"]
    sql = f"INSERT INTO {STAGING_TABLE_QUALIFIED} ({', '.join(cols)}) VALUES %s"
    batch = [tuple(r[c] for c in BUSINESS_COLUMNS) + (run_id,) for r in rows]
    execute_values(cur, sql, batch, page_size=500)


def publish_window(neon_conn, rows: list[dict], date_from: date, date_to: date, run_id: str) -> dict:
    """UMA transacao: lock -> staging -> validacao -> DELETE da janela -> INSERT
    -> verificacao. Qualquer falha faz `ROLLBACK` integral, e o destino fica
    exatamente como estava. `ON COMMIT DROP` limpa a staging tambem no rollback.
    """
    resultado = {"published": 0, "deleted": 0, "checks": {}}
    cur = neon_conn.cursor()
    try:
        cur.execute(f"SET LOCAL statement_timeout = '{TARGET_STATEMENT_TIMEOUT}'")
        # Serializa execucoes concorrentes da mesma tabela sem bloquear leitura.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))

        create_staging_table(cur)
        insert_into_staging(cur, rows, run_id)

        staging_agg = aggregates_from_table(neon_conn, STAGING_TABLE_QUALIFIED, date_from, date_to)
        source_agg = aggregates_from_rows(rows)
        problemas = compare_aggregates(source_agg, staging_agg)
        if problemas:
            raise RuntimeError("staging divergiu da fonte: " + "; ".join(problemas))

        cur.execute(
            f"DELETE FROM {TARGET_TABLE} WHERE ref_date BETWEEN %(date_from)s AND %(date_to)s",
            {"date_from": date_from, "date_to": date_to},
        )
        resultado["deleted"] = cur.rowcount

        cols = ", ".join(BUSINESS_COLUMNS + AUDIT_COLUMNS)
        cur.execute(f"""
            INSERT INTO {TARGET_TABLE} ({cols})
            SELECT {cols} FROM {STAGING_TABLE_QUALIFIED}
        """)
        resultado["published"] = cur.rowcount

        final_agg = aggregates_from_table(neon_conn, TARGET_TABLE, date_from, date_to)
        problemas = compare_aggregates(source_agg, final_agg)
        if problemas:
            raise RuntimeError("destino divergiu da fonte apos o insert: " + "; ".join(problemas))

        a_not_b, b_not_a = except_both_ways(
            neon_conn, STAGING_TABLE_QUALIFIED, TARGET_TABLE, date_from, date_to
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
# CLI — diagnostico por default, escrita somente com --apply
# ---------------------------------------------------------------------------

def _print_agg(rotulo: str, agg: dict) -> None:
    print(f"  {rotulo}:")
    print(f"    linhas={agg['count']}  datas={agg['distinct_dates']}  marcas={agg['distinct_brands']}")
    print(f"    ref_date: {agg['min_date']} a {agg['max_date']}")
    for c in ADDITIVE_COLUMNS:
        print(f"    sum_{c} = {agg[f'sum_{c}']}")
    print(f"    roas nao-nulos = {agg['roas_not_null']}  (razao, nunca somada)")


def run_diagnose(date_from: date, date_to: date) -> int:
    """Somente leitura, nas duas pontas. Nao abre conexao de escrita."""
    print(f"[diagnose] janela {date_from} a {date_to} — SOMENTE LEITURA")
    dm = _datamart_readonly(_get_datamart_url())
    try:
        rows = fetch_source_rows(dm, date_from, date_to)
    finally:
        dm.close()
    source_agg = aggregates_from_rows(rows)
    _print_agg("fonte (gold.ml_gestao_diaria)", source_agg)

    problemas = validate_source_rows(rows, date_from, date_to)
    cob = date_coverage(rows, date_from, date_to)
    print(f"  cobertura: {cob['covered_days']}/{cob['expected_days']} dias com linha")
    if problemas:
        print("  REPROVADO antes de qualquer escrita:")
        for p in problemas:
            print(f"    - {p}")
        return 1
    print("  validacoes da fonte: OK")

    neon = _neon_readonly(_get_neon_url())
    try:
        cur = neon.cursor()
        cur.execute("SELECT to_regclass(%s) AS t", (TARGET_TABLE,))
        existe = cur.fetchone()["t"] is not None
        cur.close()
        if not existe:
            print(f"  destino {TARGET_TABLE}: NAO EXISTE (migration 006 nao aplicada)")
            return 0
        _print_agg(f"destino ({TARGET_TABLE}, janela)", aggregates_from_table(neon, TARGET_TABLE, date_from, date_to))
    finally:
        neon.close()
    return 0


def run_sync(date_from: date, date_to: date, run_id: str) -> int:
    print(f"[apply] janela {date_from} a {date_to} — run_id={run_id}")
    dm = _datamart_readonly(_get_datamart_url())
    try:
        rows = fetch_source_rows(dm, date_from, date_to)
    finally:
        dm.close()

    problemas = validate_source_rows(rows, date_from, date_to)
    if problemas:
        print("  REPROVADO antes de qualquer escrita:")
        for p in problemas:
            print(f"    - {p}")
        return 1

    neon = _neon_writable(_get_neon_url())
    try:
        res = publish_window(neon, rows, date_from, date_to, run_id)
    finally:
        neon.close()
    print(f"  apagadas na janela: {res['deleted']}   publicadas: {res['published']}")
    _print_agg("fonte", res["checks"]["source"])
    _print_agg("destino", res["checks"]["target"])
    print(f"  EXCEPT bidirecional: {res['checks']['except_both_ways']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Sync incremental gold.ml_gestao_diaria -> marts.fact_ml_gestao_diaria. "
            "Toda janela termina no ultimo dia fechado (D-1): o dia corrente nunca e' "
            "publicado. Sem --apply nada e' escrito. Nao agenda, nao repete e nao tenta "
            "de novo."
        )
    )
    p.add_argument("--date-from", help="inicio da janela, YYYY-MM-DD")
    p.add_argument("--date-to", help="fim da janela, YYYY-MM-DD")
    p.add_argument("--backfill", action="store_true",
                   help=("carga historica: do primeiro dado da fonte ate o ultimo dia "
                         "fechado (D-1). O dia corrente nunca e' publicado."))
    p.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS,
                   help=(f"janela movel de N dias COMPLETOS, terminando no ultimo dia "
                         f"fechado (D-1). Default {DEFAULT_LOOKBACK_DAYS}, minimo "
                         f"{MIN_LOOKBACK_DAYS}."))
    p.add_argument("--apply", action="store_true",
                   help="EXECUTA a publicacao. Sem esta flag, o modo e' diagnostico read-only.")
    p.add_argument("--run-id", help="identificador da execucao; sanitizado antes de gravar")
    return p


def resolve_window_from_args(args, today: date | None = None) -> tuple[date, date]:
    today = today or hoje_operacional()
    if args.backfill:
        if args.date_from or args.date_to:
            raise ValueError("--backfill nao combina com --date-from/--date-to.")
        return validate_window(SOURCE_MIN_DATE, require_closed_day(today), today)
    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            raise ValueError("--date-from e --date-to precisam vir juntos.")
        return validate_window(
            date.fromisoformat(args.date_from), date.fromisoformat(args.date_to), today
        )
    return validate_window(*incremental_window(today, args.lookback_days), today=today)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        date_from, date_to = resolve_window_from_args(args)
        run_id = sanitize_run_id(args.run_id) if args.run_id else default_run_id()
        if not args.apply:
            print("MODO DIAGNOSTICO (sem --apply): nenhuma escrita sera feita.")
            return run_diagnose(date_from, date_to)
        return run_sync(date_from, date_to, run_id)
    except Exception as exc:  # noqa: BLE001 — a mensagem sai sanitizada
        print(f"FALHA: {sanitize_error_message(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
