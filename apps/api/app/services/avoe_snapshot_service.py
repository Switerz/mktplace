"""Gate AVH-4B-S Task 1/2 — servico read-only do snapshot manual da Avoe.

LE EXCLUSIVAMENTE `marts.*` E `audit.*` NO NEON
----------------------------------------------
Nenhuma consulta deste modulo menciona `gold.`, `raw.` ou `silver.`: o backend
no Render nao alcanca o Data Mart. As duas tabelas de snapshot e
`audit.source_sync_run` vivem no Neon, que e' a conexao padrao (`get_db`).

SOMENTE LEITURA
---------------
Nao existe INSERT, UPDATE, DELETE, CREATE nem TRUNCATE aqui. Nenhuma migration
acompanha este gate, e o snapshot 285 nao e' tocado.

QUATRO CONSULTAS, NO MAXIMO
---------------------------
    1. candidatas a captura, com os agregados de validacao das duas tabelas;
    2. runs de auditoria desta fonte;
    3. metas da captura escolhida;
    4. canais da captura escolhida.

As duas ultimas so' rodam quando existe captura valida. Nao ha consulta por
linha, por marca nem por canal: zero N+1. O numero de consultas nao depende do
volume de dados.

ULTIMA CAPTURA *VALIDA*, NAO SIMPLESMENTE A ULTIMA
--------------------------------------------------
As candidatas vem ordenadas da mais nova para a mais antiga, e o servico
escolhe a PRIMEIRA que passa em todas as validacoes. Uma captura mais nova mas
invalida (publicacao parcial, mistura de imports, grao duplicado, run de
auditoria nao conclusivo) e' IGNORADA em favor da anterior que se sustenta.
Isso e' deliberado: servir a mais nova so' porque e' a mais nova entregaria
dado que o proprio importador nao confirmou.

LIGACAO COM A AUDITORIA E' TEMPORAL, E ISSO E' LIMITACAO MEDIDA
---------------------------------------------------------------
`audit.source_sync_run` NAO tem coluna de ligacao com as tabelas de snapshot:
nao ha `captured_at`, `snapshot_id` nem `import_run_id` la'. Verificado no Neon
em 2026-09-08. A unica associacao possivel sem migration e' a janela
[`started_at`, `finished_at`] do run contra o `imported_at` das linhas.

O servico e' FAIL-CLOSED sobre isso: aceita a captura apenas quando EXATAMENTE
UM run `success` cobre a janela. Zero runs (caso de um run que ficou `running`,
com `finished_at` nulo) ou mais de um run derrubam a captura para
`audit_run_not_conclusive`. Assim um snapshot cujo run esta `running`, `failed`
ou indeterminado nunca e' servido — e o metodo da associacao viaja na resposta,
em `sync_run_link_method`, para nao passar por estrutural.

ZERO MISTURA ENTRE CAPTURAS
---------------------------
Metas e canais sao lidos com o MESMO `captured_at` e o mesmo `source`, e a
captura so' e' aceita se as duas tabelas concordarem no `captured_at`, no
`snapshot_id` e no `import_run_id`. Uma linha de outra captura nao tem como
entrar na resposta.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

TARGET_TABLE = "marts.proxy_avoe_brand_monthly_target_snapshot"
CHANNEL_TABLE = "marts.proxy_avoe_extra_channel_monthly_snapshot"

#: Valor unico de `source` nas duas tabelas, garantido por CHECK na migration.
SOURCE = "avoe_hub"

#: `source_name` do run em `audit.source_sync_run`, escrito pelo importador.
AUDIT_SOURCE_NAME = "avoe_manual_snapshot"

#: Quantas capturas mais recentes entram na avaliacao. A tabela e' append-only e
#: alimentada por exportacao humana; dez capturas cobrem meses de operacao. O
#: teto existe para que a consulta seja O(1) em vez de crescer com o historico.
MAX_CAPTURAS_AVALIADAS = 10

#: Teto de runs de auditoria lidos. Ordenados do mais novo para o mais antigo.
MAX_RUNS_AUDITORIA = 200

STATUS_AUDITORIA_CONCLUSIVO = "success"

AVISO_FONTE_EXTERNA = (
    "Fonte externa e manual: estes numeros vem de exportacao humana do Avoe Hub, "
    "sistema de terceiro sem contrato de dados. Nao sao KPI da Torre e nao "
    "substituem nenhuma metrica canonica."
)
AVISO_MOEDA_ASSUMIDA = (
    "Moeda ASSUMIDA como BRL: a origem nao declara moeda. Confirmacao pendente "
    "com a Avoe."
)
AVISO_CANAL_PROXY = (
    "Faturamento de canal e' PROXY com definicao nao confirmada: a Avoe nao "
    "documentou se o valor e' bruto, liquido, com ou sem frete."
)
AVISO_SEM_REALIZADO = (
    "Esta fonte nao traz realizado. Nao ha atingimento, margem nem variacao "
    "calculada: cruzar meta com realizado exige contrato proprio."
)
AVISO_LIGACAO_TEMPORAL = (
    "O sync_run_id foi associado a captura por janela de tempo da auditoria, nao "
    "por chave: audit.source_sync_run nao tem coluna de ligacao com as tabelas "
    "de snapshot."
)
AVISO_SEM_AUTOMACAO = (
    "Sem automacao: nenhum agendamento atualiza estas tabelas. A proxima captura "
    "depende de exportacao manual e de uma execucao explicita do importador."
)

NOTAS_LIMITACAO = [
    AVISO_SEM_AUTOMACAO,
    AVISO_LIGACAO_TEMPORAL,
    "Metas vigentes a partir de 2026-08-01; canais adicionais a partir de "
    "2026-06-01. Competencias anteriores foram recusadas na ingestao por escala "
    "incompativel medida no AVH-3B.",
    "Canais oficiais (TikTok, Mercado Livre, Shopee) nao existem nesta fonte por "
    "construcao: a allowlist da tabela os exclui, o que impede soma acidental "
    "com o GMV oficial.",
    "Ausencia de valor chega como null e nunca como zero. Zero informado chega "
    "como zero.",
]


class AvoeSnapshotError(RuntimeError):
    """Inconsistencia da camada de serving. Mensagem fixa na borda HTTP."""


def _levanta_limpo(erro: AvoeSnapshotError):
    """Levanta sem cadeia: nada de driver em `__cause__`, `__context__` ou tb.

    Mesma disciplina do importador (Gate AVH-4A-H1-D1). A excecao do psycopg2
    carrega DSN, host, usuario, parametros e SQL no proprio texto e nos frames;
    o `finally` existe porque o `raise` reinstala `__context__` com a excecao
    em tratamento.
    """
    erro.__cause__ = None
    erro.__context__ = None
    erro.__suppress_context__ = True
    erro.__traceback__ = None
    try:
        raise erro
    finally:
        erro.__cause__ = None
        erro.__context__ = None
        erro.__suppress_context__ = True


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

#: Consulta 1 — candidatas a captura com os agregados de validacao.
#:
#: `FULL OUTER JOIN` de proposito: uma captura presente em so' uma das tabelas
#: e' publicacao incompleta, e precisa APARECER para ser recusada, em vez de
#: desaparecer silenciosamente num INNER JOIN.
SQL_CANDIDATAS = f"""
WITH capturas AS (
    SELECT captured_at FROM {TARGET_TABLE}  WHERE source = :source
    UNION
    SELECT captured_at FROM {CHANNEL_TABLE} WHERE source = :source
),
recentes AS (
    SELECT captured_at FROM capturas ORDER BY captured_at DESC LIMIT :max_capturas
),
metas AS (
    SELECT r.captured_at,
           count(t.brand)                     AS n,
           count(DISTINCT t.snapshot_id)      AS snapshots,
           count(DISTINCT t.import_run_id)    AS imports,
           count(DISTINCT (t.ref_month, t.brand)) AS grao,
           min(t.imported_at)                 AS imp_min,
           max(t.imported_at)                 AS imp_max,
           min(t.snapshot_id)                 AS snapshot_id,
           min(t.currency_code)               AS currency_code,
           min(t.currency_status)             AS currency_status,
           count(DISTINCT t.currency_code)    AS moedas,
           count(DISTINCT t.currency_status)  AS status_moeda
      FROM recentes r
      LEFT JOIN {TARGET_TABLE} t
             ON t.source = :source AND t.captured_at = r.captured_at
     GROUP BY r.captured_at
),
canais AS (
    SELECT r.captured_at,
           count(c.brand)                     AS n,
           count(DISTINCT c.snapshot_id)      AS snapshots,
           count(DISTINCT c.import_run_id)    AS imports,
           count(DISTINCT (c.ref_month, c.brand, c.channel)) AS grao,
           min(c.imported_at)                 AS imp_min,
           max(c.imported_at)                 AS imp_max,
           min(c.snapshot_id)                 AS snapshot_id
      FROM recentes r
      LEFT JOIN {CHANNEL_TABLE} c
             ON c.source = :source AND c.captured_at = r.captured_at
     GROUP BY r.captured_at
)
SELECT m.captured_at,
       m.n            AS targets_count,
       m.snapshots    AS target_snapshots,
       m.imports      AS target_imports,
       m.grao         AS target_grao,
       m.imp_min      AS target_imp_min,
       m.imp_max      AS target_imp_max,
       m.snapshot_id  AS target_snapshot_id,
       m.currency_code,
       m.currency_status,
       m.moedas,
       m.status_moeda,
       c.n            AS channel_rows_count,
       c.snapshots    AS channel_snapshots,
       c.imports      AS channel_imports,
       c.grao         AS channel_grao,
       c.imp_min      AS channel_imp_min,
       c.imp_max      AS channel_imp_max,
       c.snapshot_id  AS channel_snapshot_id
  FROM metas m
  FULL OUTER JOIN canais c ON c.captured_at = m.captured_at
 ORDER BY coalesce(m.captured_at, c.captured_at) DESC
"""

#: Consulta 2 — runs de auditoria desta fonte, do mais novo para o mais antigo.
SQL_RUNS_AUDITORIA = """
SELECT sync_run_id, status, started_at, finished_at
  FROM audit.source_sync_run
 WHERE source_name = :source_name
 ORDER BY sync_run_id DESC
 LIMIT :max_runs
"""

#: Consulta 3 — metas da captura escolhida. `source` E `captured_at` ligados.
SQL_METAS = f"""
SELECT brand, brand_key, ref_month, target_amount,
       currency_code, currency_status, currency_warning, source_recorded_at
  FROM {TARGET_TABLE}
 WHERE source = :source AND captured_at = :captured_at
 ORDER BY ref_month, brand
"""

#: Consulta 4 — canais da captura escolhida.
SQL_CANAIS = f"""
SELECT brand, brand_key, channel, channel_source_label, ref_month,
       reported_amount, is_proxy, definition_status, definition_warning,
       currency_code, currency_status, days_covered,
       first_business_date, last_business_date, coverage_status,
       source_recorded_at
  FROM {CHANNEL_TABLE}
 WHERE source = :source AND captured_at = :captured_at
 ORDER BY ref_month, channel, brand
"""


def _rows(db: Session, sql: str, params: dict) -> list[dict]:
    """Executa e materializa. Falha de banco nao vaza texto externo.

    Fica so' o nome da classe da excecao; a borda HTTP nem isso repassa,
    devolvendo mensagem fixa. Sem este guard, um erro de driver subiria cru
    ate' o FastAPI, com DSN e SQL no log do handler.
    """
    try:
        return [dict(r) for r in db.execute(text(sql), params).mappings()]
    except Exception as exc:
        _levanta_limpo(AvoeSnapshotError(
            f"falha ao consultar a camada de snapshot da Avoe "
            f"({type(exc).__name__})"))


# ---------------------------------------------------------------------------
# Serializacao
# ---------------------------------------------------------------------------

def _dinheiro(valor) -> float | None:
    """`Decimal` -> `float`. `None` continua `None`: ausencia nunca vira zero."""
    if valor is None:
        return None
    return float(valor)


def _iso(valor) -> str | None:
    """Data ou timestamp -> ISO 8601. `None` continua `None`."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    return str(valor)


def _agora() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Validacao da captura
# ---------------------------------------------------------------------------

def _valida_captura(cand: dict) -> str | None:
    """Devolve o motivo de recusa, ou `None` se a captura se sustenta.

    Fail-closed: qualquer sinal de publicacao parcial, mistura de imports ou
    grao duplicado recusa a captura inteira. Nao existe recuperacao parcial —
    servir metade de uma captura seria servir um numero que nao existiu.
    """
    if cand.get("captured_at") is None:
        return "capture_incomplete"

    metas = cand.get("targets_count") or 0
    canais = cand.get("channel_rows_count") or 0

    # Publicacao incompleta: uma das duas tabelas nao recebeu esta captura.
    if metas == 0 or canais == 0:
        return "targets_and_channels_capture_mismatch"

    # Mistura de imports ou de arquivos sob o mesmo `captured_at`.
    if cand["target_snapshots"] != 1 or cand["channel_snapshots"] != 1:
        return "capture_mixes_multiple_imports"
    if cand["target_imports"] != 1 or cand["channel_imports"] != 1:
        return "capture_mixes_multiple_imports"
    if cand["target_snapshot_id"] != cand["channel_snapshot_id"]:
        return "capture_mixes_multiple_imports"

    # Grao duplicado: a PK impede no banco, mas a checagem e' barata e a
    # resposta precisa falhar fechada se a premissa mudar.
    if cand["target_grao"] != metas or cand["channel_grao"] != canais:
        return "duplicate_grain_in_capture"

    # Moeda tem de ser unica na captura para poder ser declarada no meta.
    if cand["moedas"] != 1 or cand["status_moeda"] != 1:
        return "serving_inconsistent"

    return None


def _associa_run(cand: dict, runs: list[dict]) -> dict | None:
    """Associa a captura a EXATAMENTE UM run `success` pela janela de tempo.

    Ver o docstring do modulo: nao existe chave de ligacao. Zero ou multiplos
    casamentos devolvem `None`, e a captura e' recusada.

    Um run com `finished_at` nulo — o caso de quem ficou `running`, incluindo o
    commit indeterminado — nao casa com nada, de proposito.
    """
    inicio = min(cand["target_imp_min"], cand["channel_imp_min"])
    fim = max(cand["target_imp_max"], cand["channel_imp_max"])
    casados = [
        r for r in runs
        if r["status"] == STATUS_AUDITORIA_CONCLUSIVO
        and r["started_at"] is not None
        and r["finished_at"] is not None
        and r["started_at"] <= inicio
        and r["finished_at"] >= fim
    ]
    if len(casados) != 1:
        return None
    return casados[0]


def _idade_dias(captured_at, agora: datetime) -> int | None:
    if captured_at is None:
        return None
    return (agora - captured_at).days


# ---------------------------------------------------------------------------
# Montagem da resposta
# ---------------------------------------------------------------------------

def _limitacoes() -> dict:
    return {
        "manual_snapshot": True,
        "automated_refresh": False,
        "channel_amount_definition_confirmed": False,
        "currency_confirmed": False,
        "provides_realized_amount": False,
        "provides_attainment_or_margin": False,
        "replaces_canonical_torre_kpi": False,
        "notes": list(NOTAS_LIMITACAO),
    }


def _indisponivel(motivo: str, agora: datetime) -> dict:
    """HTTP 200 com estado vazio. Nenhum zero inventado, nenhuma lista falsa."""
    return {
        "meta": {
            "status": "unavailable",
            "source": SOURCE,
            "source_kind": "external_manual_snapshot",
            "is_official_torre_source": False,
            "captured_at": None,
            "snapshot_id": None,
            "sync_run_id": None,
            "sync_run_status": None,
            "sync_run_link_method": None,
            "targets_count": 0,
            "channel_rows_count": 0,
            "target_ref_months": [],
            "channel_ref_months": [],
            "currency": None,
            "currency_status": None,
            "refreshed_at": agora.isoformat(),
            "captured_age_days": None,
            "unavailable_reason": motivo,
            "warnings": [AVISO_FONTE_EXTERNA, AVISO_SEM_AUTOMACAO],
        },
        "targets": [],
        "extra_channels": [],
        "limitations": _limitacoes(),
    }


def _linha_meta(r: dict) -> dict:
    return {
        "brand": r["brand"],
        "brand_key": r["brand_key"],
        "ref_month": _iso(r["ref_month"]),
        "target_amount": _dinheiro(r["target_amount"]),
        "currency_code": r["currency_code"],
        "currency_status": r["currency_status"],
        "currency_warning": r["currency_warning"],
        "source_recorded_at": _iso(r["source_recorded_at"]),
    }


def _linha_canal(r: dict) -> dict:
    return {
        "brand": r["brand"],
        "brand_key": r["brand_key"],
        "channel": r["channel"],
        "channel_source_label": r["channel_source_label"],
        "ref_month": _iso(r["ref_month"]),
        "reported_amount": _dinheiro(r["reported_amount"]),
        "is_proxy": bool(r["is_proxy"]),
        "definition_status": r["definition_status"],
        "definition_warning": r["definition_warning"],
        "currency_code": r["currency_code"],
        "currency_status": r["currency_status"],
        "days_covered": int(r["days_covered"]),
        "first_business_date": _iso(r["first_business_date"]),
        "last_business_date": _iso(r["last_business_date"]),
        "coverage_status": r["coverage_status"],
        "source_recorded_at": _iso(r["source_recorded_at"]),
    }


def get_avoe_snapshot(db: Session) -> dict:
    """A ultima captura VALIDA da Avoe, ou estado indisponivel explicito.

    Levanta `AvoeSnapshotError` somente para inconsistencia real da camada de
    serving — o que a borda HTTP traduz em 500 com mensagem fixa. Ausencia de
    dado NAO e' erro: e' 200 com `status = 'unavailable'`.
    """
    agora = _agora()

    if db is None:
        raise AvoeSnapshotError("sessao de banco indisponivel")

    candidatas = _rows(db, SQL_CANDIDATAS, {
        "source": SOURCE,
        "max_capturas": MAX_CAPTURAS_AVALIADAS,
    })
    if not candidatas:
        return _indisponivel("no_snapshot_published", agora)

    runs = _rows(db, SQL_RUNS_AUDITORIA, {
        "source_name": AUDIT_SOURCE_NAME,
        "max_runs": MAX_RUNS_AUDITORIA,
    })

    # Da mais nova para a mais antiga: a primeira que passa e' a servida. Uma
    # captura mais nova e invalida e' ignorada, nao serve de motivo global.
    escolhida = None
    motivo_ultimo = "no_snapshot_published"
    run_escolhido = None
    for cand in candidatas:
        motivo = _valida_captura(cand)
        if motivo is not None:
            motivo_ultimo = motivo
            continue
        run = _associa_run(cand, runs)
        if run is None:
            motivo_ultimo = "audit_run_not_conclusive"
            continue
        escolhida, run_escolhido = cand, run
        break

    if escolhida is None:
        return _indisponivel(motivo_ultimo, agora)

    captured_at = escolhida["captured_at"]
    params = {"source": SOURCE, "captured_at": captured_at}
    metas = [_linha_meta(r) for r in _rows(db, SQL_METAS, params)]
    canais = [_linha_canal(r) for r in _rows(db, SQL_CANAIS, params)]

    # As contagens agregadas e as linhas lidas tem de bater. Divergencia aqui
    # significa escrita concorrente durante a leitura: fail-closed.
    if len(metas) != escolhida["targets_count"]:
        raise AvoeSnapshotError("contagem de metas divergiu entre agregado e linhas")
    if len(canais) != escolhida["channel_rows_count"]:
        raise AvoeSnapshotError("contagem de canais divergiu entre agregado e linhas")

    avisos = [AVISO_FONTE_EXTERNA, AVISO_SEM_REALIZADO, AVISO_CANAL_PROXY,
              AVISO_SEM_AUTOMACAO, AVISO_LIGACAO_TEMPORAL]
    if escolhida["currency_status"] != "confirmed":
        avisos.insert(1, AVISO_MOEDA_ASSUMIDA)

    return {
        "meta": {
            "status": "available",
            "source": SOURCE,
            "source_kind": "external_manual_snapshot",
            "is_official_torre_source": False,
            "captured_at": _iso(captured_at),
            "snapshot_id": escolhida["target_snapshot_id"],
            "sync_run_id": int(run_escolhido["sync_run_id"]),
            "sync_run_status": run_escolhido["status"],
            "sync_run_link_method": "audit_time_window",
            "targets_count": int(escolhida["targets_count"]),
            "channel_rows_count": int(escolhida["channel_rows_count"]),
            "target_ref_months": sorted({m["ref_month"] for m in metas}),
            "channel_ref_months": sorted({c["ref_month"] for c in canais}),
            "currency": escolhida["currency_code"],
            "currency_status": escolhida["currency_status"],
            "refreshed_at": agora.isoformat(),
            "captured_age_days": _idade_dias(captured_at, agora),
            "unavailable_reason": None,
            "warnings": avisos,
        },
        "targets": metas,
        "extra_channels": canais,
        "limitations": _limitacoes(),
    }
