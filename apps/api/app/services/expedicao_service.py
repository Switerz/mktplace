"""Gate EXP-2A — serving read-only da Expedicao.

SO' LE O QUE O PIPELINE PUBLICOU
---------------------------------
Nenhuma regra de negocio e' reimplementada aqui. `deadline_status`,
`operational_age_status`, `is_stalled`, `hours_open` e as contagens ja' vem
classificadas de `marts.expedicao_fila_atual` e `marts.expedicao_refresh_run`.
A API NAO reclassifica nada a partir do relogio da requisicao: se o fizesse,
dois consumidores veriam estados diferentes para a mesma fotografia.

O UNICO agregado calculado aqui e' o total do canal, que e' soma de contas do
MESMO batch e nao existe materializado de proposito.

A FOTOGRAFIA E' UMA SO'
------------------------
Fila e resumos vem sempre do mesmo `refresh_batch_id`. Misturar fila nova com
resumo velho mostraria um backlog que a tendencia nao explica — por isso
`_carregar_snapshot` falha FECHADO com `inconsistent_batch` em vez de devolver a
combinacao parcial.

FRESCOR VEM DA OBSERVACAO MAIS RECENTE
---------------------------------------
`audit.data_quality_check` acumula historico, e as linhas de 16/09 18:41 tem a
semantica ANTIGA (pior pedido do backlog), todas `fail`/`high`. Agregar o pior
valor historico ressuscitaria o defeito corrigido no EXP-1F. A consulta usa
`DISTINCT ON (brand) ... ORDER BY check_timestamp DESC`: exatamente uma linha,
a mais recente, por marca.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text

from app.config import settings
from app.schemas.expedicao import (
    UNAVAILABLE_DISABLED,
    UNAVAILABLE_INCONSISTENT_BATCH,
    UNAVAILABLE_NO_SNAPSHOT,
)

CHANNEL = "shopee"
CHECK_FRESHNESS = "expedicao_source_freshness"
#: Shopee. Escopa o check para que nenhuma linha de PMA, Full ou de outra fonte
#: entre no `DISTINCT ON`, por mais recente que seja.
MARKETPLACE_ID = 3

#: Tamanho minimo do segredo do `order_ref`. Abaixo disso a chave nao resiste a
#: busca exaustiva.
SEGREDO_MIN_BYTES = 32

#: 22 caracteres base64url = 132 bits efetivos, acima dos 128 exigidos.
ORDER_REF_CHARS = 22


class SegredoInvalido(RuntimeError):
    """`expedicao_order_ref_secret` configurado com valor fraco demais."""


FILA = "marts.expedicao_fila_atual"
RUN = "marts.expedicao_refresh_run"

#: Colunas servidas da fila. ALLOWLIST, nunca `SELECT *`.
#: `marketplace_order_id` esta deliberadamente FORA — ver `_order_ref`.
FILA_COLUNAS = (
    "shop_account", "brand", "created_at", "paid_at", "dispatch_deadline",
    "deadline_source", "deadline_status", "operational_age_status",
    "is_slow_vs_baseline", "is_source_zombie", "is_stalled", "hours_open",
    "hours_overdue", "logistic_type", "carrier", "source_ingested_at",
    "source_freshness_status", "timestamp_quality",
)

#: Situacao operacional -> predicado SQL. Chaves fixas: a entrada do cliente
#: nunca vira SQL, so' seleciona um predicado ja' escrito.
SITUACAO_SQL = {
    "overdue": "deadline_status = 'overdue'",
    "due_within_24h": "deadline_status = 'due_within_24h'",
    "on_time": "deadline_status = 'on_time'",
    "deadline_unavailable": "deadline_status = 'unavailable'",
    "over_48h": "operational_age_status = 'over_48h'",
    "stalled": "is_stalled",
    "slow": "is_slow_vs_baseline",
    "zombie": "is_source_zombie",
}

#: Ordenacao PADRAO: criticidade operacional, depois prazo mais urgente, e a
#: chave fisica como desempate. Sem o desempate estavel, duas paginas podem
#: repetir ou omitir a mesma linha.
ORDEM_PADRAO = (
    "CASE deadline_status WHEN 'overdue' THEN 0 WHEN 'due_within_24h' THEN 1"
    " WHEN 'on_time' THEN 2 ELSE 3 END",
    "CASE WHEN is_stalled THEN 0 ELSE 1 END",
    "dispatch_deadline ASC NULLS LAST",
    "shop_account ASC",
    "marketplace_order_id ASC",
)

#: Ordenacoes alternativas. ALLOWLIST: o cliente escolhe uma CHAVE, nunca
#: escreve a clausula.
ORDENACOES = {
    "criticidade": ORDEM_PADRAO,
    "deadline": ("dispatch_deadline ASC NULLS LAST", "shop_account ASC",
                 "marketplace_order_id ASC"),
    "oldest": ("hours_open DESC NULLS LAST", "shop_account ASC",
               "marketplace_order_id ASC"),
}

LIMIT_MAX = 500
LIMIT_PADRAO = 100
JANELA_MAX_HORAS = 24 * 14
JANELA_PADRAO_HORAS = 48
PONTOS_MAX = 2000

#: Marcas que a torre acompanha e que ESTA fonte nao cobre. Medido no EXP-1E:
#: Kokeshi existe em `marts.dim_loja` e nao existe em `raw.shopee_orders`.
MARCAS_SEM_COBERTURA = ("kokeshi",)


def habilitado() -> bool:
    return bool(getattr(settings, "expedicao_api_enabled", False))


def _order_ref(order_sn: str) -> Optional[str]:
    """Identificador OPACO do pedido, ou `None`.

    Esta API nao tem autenticacao. `order_sn` permite consultar o pedido no
    painel do marketplace, entao nao vai cru numa rota publica. Um hash SEM
    CHAVE tambem nao resolve: o espaco de `order_sn` e' curto e enumeravel, e
    uma tabela arco-iris o reverte em minutos.

    Com `expedicao_order_ref_secret` configurado devolve HMAC-SHA256 em
    base64url, 132 bits efetivos. Sem segredo devolve `None`: o default e' NAO
    publicar, e a ausencia do identificador nao impede nenhum agregado.

    O QUE `order_ref` NAO E'
    ------------------------
    Nao concede acesso ao pedido, nao substitui autenticacao e nao pode ser
    trocado pelo `order_sn` nesta API — nao existe rota de resolucao reversa.
    Girar o segredo troca TODOS os `order_ref`: ele e' estavel para correlacao
    dentro de uma configuracao, nunca um identificador perene.
    """
    segredo = getattr(settings, "expedicao_order_ref_secret", "") or ""
    if not segredo:
        return None
    bruto = segredo.encode("utf-8")
    if len(bruto) < SEGREDO_MIN_BYTES:
        # Segredo fraco da' a ILUSAO de protecao, que e' pior que nao ter
        # identificador nenhum: o consumidor confia num valor reversivel.
        raise SegredoInvalido(
            f"expedicao_order_ref_secret precisa de ao menos "
            f"{SEGREDO_MIN_BYTES} bytes."
        )
    digest = hmac.new(bruto, order_sn.encode("utf-8"), hashlib.sha256).digest()
    texto = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return texto[:ORDER_REF_CHARS]


def _horas(inicio: Optional[datetime], fim: Optional[datetime]) -> Optional[float]:
    if inicio is None or fim is None:
        return None
    return (fim - inicio).total_seconds() / 3600.0


def _num(valor: Any) -> Optional[float]:
    """NUMERIC -> float, recusando nao finito.

    A migration 018 tem CHECK contra NaN e +/-Infinity, mas JSON nao representa
    nenhum dos tres: se um dia escaparem, viram `None` em vez de payload invalido.
    """
    if valor is None:
        return None
    f = float(valor)
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _indisponivel(motivo: str, *, tendencia: bool = False) -> dict:
    envelope = {
        "availability": "unavailable",
        "unavailable_reason": motivo,
        "channel": CHANNEL,
        # As colecoes vao VAZIAS e as medidas vao NULAS. Colecao ausente do dict
        # deixaria o consumidor decidir o default; total ausente virando zero
        # afirmaria uma medicao que nao houve.
        "snapshot": None,
        "totals": None,
        "accounts": [],
        "freshness": [],
        "coverage": None,
        "queue": [],
        "pagination": None,
        "limitations": _limitacoes(None),
    }
    if tendencia:
        envelope["window_hours"] = 0
        envelope["points"] = []
        envelope["truncated"] = False
    return envelope


def _limitacoes(idade_h: Optional[float], extras: Optional[list[str]] = None) -> dict:
    notas = [
        "Nenhum agendamento existe: a fotografia so' avanca quando alguem executa"
        " o refresh manualmente.",
        "Kokeshi nao e' coberta por esta fonte (ausente em raw.shopee_orders).",
        "Identificador do pedido nao e' servido: esta API nao tem autenticacao.",
        "freshness mede o watermark da CONTA; oldest_row_age_hours e' contexto e"
        " nao reprova a fonte.",
    ]
    notas.extend(extras or [])
    return {
        "no_automation": True,
        "load_mode": "manual_snapshot",
        "snapshot_age_hours": idade_h,
        "brands_not_covered": list(MARCAS_SEM_COBERTURA),
        "order_identifier_withheld": _order_ref("x") is None,
        "notes": notas,
    }


def _linhas(db, sql: str, params: dict) -> list[dict]:
    return [dict(r) for r in db.execute(text(sql), params).mappings()]


def _abrir_snapshot(db) -> None:
    """Uma unica fotografia do BANCO para todas as consultas da resposta.

    O payload e' montado com varias consultas — cabecalho do lote, resumos,
    fila, contagem, auditoria, frescor, registry. Sem isolamento, uma publicacao
    concorrente entre duas delas devolveria fila do lote novo com resumo do
    antigo: um estado que nunca existiu em instante nenhum.

    `REPEATABLE READ` congela o instante na primeira consulta de dados; todas as
    seguintes leem dele. `READ ONLY` e' o cinto: mesmo que alguem acrescente um
    INSERT aqui um dia, o servidor recusa.

    O `rollback()` antes garante que a transacao ainda nao comecou — o PostgreSQL
    exige que `SET TRANSACTION` seja a primeira instrucao dela. Numa sessao
    recem-aberta por `get_db` e' um no-op.
    """
    db.rollback()
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))


# ---------------------------------------------------------------------------
# Fotografia vigente
# ---------------------------------------------------------------------------
def _carregar_snapshot(db) -> tuple[Optional[dict], Optional[str]]:
    """Identidade do batch vigente, ou o motivo de nao servir.

    Falha FECHADA: mais de um batch ou mais de um `effective_at` na fila
    significa que a substituicao por canal nao terminou, e combinar os dois
    devolveria um estado que nunca existiu.
    """
    cab = _linhas(db, f"""
        SELECT count(*) AS linhas,
               count(DISTINCT refresh_batch_id) AS lotes,
               count(DISTINCT effective_at) AS instantes,
               min(refresh_batch_id) AS batch,
               min(effective_at) AS effective_at
        FROM {FILA} WHERE channel = :canal
    """, {"canal": CHANNEL})[0]

    if not cab["linhas"] and not cab["lotes"]:
        return None, UNAVAILABLE_NO_SNAPSHOT
    if cab["lotes"] != 1 or cab["instantes"] != 1:
        return None, UNAVAILABLE_INCONSISTENT_BATCH

    resumos = _linhas(db, f"""
        SELECT shop_account, brand, backlog_count, overdue_count,
               due_within_24h_count, on_time_count, deadline_unavailable_count,
               over_48h_count, slow_count, zombie_count, stalled_count,
               run_status, source_watermark_at, source_advanced, snapshot_hour,
               observed_at
        FROM {RUN} WHERE channel = :canal AND refresh_batch_id = :batch
        ORDER BY brand, shop_account
    """, {"canal": CHANNEL, "batch": cab["batch"]})

    if not resumos:
        # Fila publicada sem o resumo do mesmo lote: os dois vao na MESMA
        # transacao, entao isto so' acontece se alguem escreveu por fora.
        return None, UNAVAILABLE_INCONSISTENT_BATCH
    if len({r["snapshot_hour"] for r in resumos}) != 1:
        return None, UNAVAILABLE_INCONSISTENT_BATCH
    if len({r["observed_at"] for r in resumos}) != 1:
        return None, UNAVAILABLE_INCONSISTENT_BATCH
    if resumos[0]["observed_at"] != cab["effective_at"]:
        return None, UNAVAILABLE_INCONSISTENT_BATCH

    return {"batch": cab["batch"], "effective_at": cab["effective_at"],
            "linhas": cab["linhas"], "resumos": resumos}, None


def _auditoria(db, effective_at: datetime) -> dict:
    """A execucao que produziu ESTE lote.

    `audit.source_sync_run` nao guarda `refresh_batch_id`, entao a ancora e'
    temporal: a PRIMEIRA execucao que terminou em ou depois do `effective_at`
    desta fotografia.

    A ordem dos instantes na CLI e' `effective_at` -> `audit_start` ->
    publicacao -> `audit_finish`. Ou seja, `effective_at` vem ANTES de
    `started_at`, e a execucao produtora e' a primeira a fechar depois dele.
    Execucoes posteriores tambem satisfazem `finished_at >= effective_at`, e por
    isso a ordenacao e' ASCENDENTE: a menor `sync_run_id` que cumpre a condicao
    e' a que gerou este lote.

    Sem essa ancora bastaria uma publicacao concorrente para o payload sair com
    o dado do lote A e o `run_id` da execucao B. Se nada casar, os campos ficam
    NULOS: preenche-los com a execucao mais recente seria atribuir a esta
    fotografia numeros de outra.
    """
    linhas = _linhas(db, """
        SELECT sync_run_id, status, rows_extracted, rows_loaded
        FROM audit.source_sync_run
        WHERE source_name = :fonte
          AND status <> 'running'
          AND finished_at >= :instante
        ORDER BY sync_run_id ASC LIMIT 1
    """, {"fonte": f"expedicao_{CHANNEL}", "instante": effective_at})
    return linhas[0] if linhas else {}


def _frescor(db) -> list[dict]:
    """A observacao MAIS RECENTE por marca. Nunca o pior historico.

    `DISTINCT ON` com `ORDER BY brand, check_timestamp DESC, check_id DESC`
    devolve exatamente uma linha por marca. O `check_id` desempata linhas
    gravadas no mesmo instante — as quatro de um lote compartilham o timestamp
    do `commit`, e sem o desempate a escolha ficaria a cargo do planejador.
    """
    return _linhas(db, """
        SELECT DISTINCT ON (details::jsonb->>'brand')
               details::jsonb->>'brand' AS brand,
               status, severity, check_timestamp, check_id,
               details::jsonb AS detalhe
        FROM audit.data_quality_check
        WHERE check_name = :check
          AND table_name = :tabela
          AND marketplace_id = :mkt
          AND details IS NOT NULL
        ORDER BY details::jsonb->>'brand', check_timestamp DESC, check_id DESC
    """, {"check": CHECK_FRESHNESS, "tabela": FILA, "mkt": MARKETPLACE_ID})


def _monta_frescor(bruto: list[dict], resumos: list[dict],
                   effective_at: datetime) -> list[dict]:
    """Uma linha por marca do batch, cruzando auditoria e resumo.

    O watermark vem do RESUMO do batch servido; a auditoria fornece o veredito.
    Se a linha de auditoria for antiga (sem `measures`), o veredito e' derivado
    do proprio watermark em vez de herdar a classificacao velha — servir
    `critical` de 18:41 como estado atual seria exatamente o defeito corrigido.
    """
    por_marca = {r["brand"]: r for r in bruto}
    saida = []
    for marca in sorted({r["brand"] for r in resumos}):
        contas = [r for r in resumos if r["brand"] == marca]
        carimbos = [c["source_watermark_at"] for c in contas]
        invalido = any(w is None or w > effective_at for w in carimbos)
        watermark = None if invalido else min(carimbos)
        idade = _horas(watermark, effective_at)

        a = por_marca.get(marca) or {}
        det = a.get("detalhe") or {}
        if isinstance(det, str):
            det = json.loads(det)
        atual = det.get("measures") == "source_watermark_only"

        if atual:
            estado = det.get("freshness")
            status, sev = a.get("status"), a.get("severity")
            velha = det.get("oldest_row_age_hours")
            contas_n = det.get("accounts")
            observado = a.get("check_timestamp")
        else:
            # Linha antiga (ou ausente): deriva do watermark deste batch.
            estado = _classificar(idade, watermark)
            status, sev = None, None
            velha = None
            contas_n = len(contas)
            observado = None

        saida.append({
            "brand": marca,
            "freshness": estado,
            "status": status,
            "severity": sev,
            "source_watermark_at": watermark,
            "source_age_hours": idade,
            "oldest_row_age_hours": _num(velha),
            "accounts": contas_n,
            "observed_at": observado,
            "measures": det.get("measures") if atual else None,
            # Marca EXPLICITA de que o estado nao veio da auditoria vigente:
            # foi derivado do watermark deste lote porque a linha e antiga
            # (semantica do EXP-1E) ou nao existe.
            "derived_from_batch": not atual,
        })
    return saida


def _classificar(idade_h: Optional[float], watermark: Optional[datetime]) -> str:
    """Mesmos limites do contrato do pipeline: 8h e 24h, inclusivos.

    Carimbo ausente ou no futuro e' `unknown` — a partir de um instante
    impossivel nao da para afirmar fresco nem velho.
    """
    if watermark is None or idade_h is None or idade_h < 0:
        return "unknown"
    if idade_h <= 8:
        return "fresh"
    if idade_h <= 24:
        return "stale"
    return "critical"


def _cobertura(db, resumos: list[dict], effective_at: datetime) -> dict:
    esperadas = _linhas(db, """
        SELECT sa.external_seller_id, l.brand_key AS brand, sa.account_name
        FROM marts.dim_seller_account sa
        JOIN marts.dim_loja l ON l.loja_id = sa.loja_id
        WHERE sa.marketplace_id = :mkt AND sa.ativo AND l.ativo
        ORDER BY l.brand_key
    """, {"mkt": 3})
    marcas_esperadas = [r["brand"] for r in esperadas]
    observadas = {r["shop_account"]: r for r in resumos}

    # O registry chaveia por `external_seller_id` e a fotografia por
    # `shop_account`. A marca e' o unico elo 1:1 entre os dois nesta camada.
    esperadas_por_marca = {r["brand"]: r for r in esperadas}
    contas = []
    for marca in sorted(set(marcas_esperadas) | {r["brand"] for r in resumos}):
        r = next((x for x in resumos if x["brand"] == marca), None)
        contas.append({
            "shop_account": r["shop_account"] if r else marca,
            "brand": marca,
            "observed": r is not None,
            "backlog_count": r["backlog_count"] if r else None,
            "source_watermark_at": r["source_watermark_at"] if r else None,
            "source_age_hours": _horas(r["source_watermark_at"], effective_at)
            if r else None,
            "source_advanced": r["source_advanced"] if r else None,
        })

    marcas_obs = {r["brand"] for r in resumos}
    faltando = sorted(set(esperadas_por_marca) - marcas_obs)
    inesperadas = sorted(marcas_obs - set(esperadas_por_marca))
    return {
        "expected_accounts": sorted(marcas_esperadas),
        "observed_accounts": sorted(observadas),
        "missing_accounts": faltando,
        "unexpected_accounts": inesperadas,
        "accounts": contas,
        "brands_not_covered": list(MARCAS_SEM_COBERTURA),
    }


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------
def get_expedicao(
    db,
    *,
    brands: Optional[list[str]] = None,
    accounts: Optional[list[str]] = None,
    situacoes: Optional[list[str]] = None,
    order_by: str = "criticidade",
    limit: int = LIMIT_PADRAO,
    offset: int = 0,
    include_queue: bool = True,
) -> dict:
    if not habilitado():
        return _indisponivel(UNAVAILABLE_DISABLED)

    _abrir_snapshot(db)
    snap, motivo = _carregar_snapshot(db)
    if snap is None:
        return _indisponivel(motivo or UNAVAILABLE_NO_SNAPSHOT)

    resumos = snap["resumos"]
    effective_at = snap["effective_at"]
    aud = _auditoria(db, effective_at)

    medidas = ("backlog_count", "overdue_count", "due_within_24h_count",
               "on_time_count", "deadline_unavailable_count", "over_48h_count",
               "slow_count", "zombie_count", "stalled_count")
    totais = {m: sum(int(r[m]) for r in resumos) for m in medidas}

    saude = "healthy"
    cobertura = _cobertura(db, resumos, effective_at)
    if cobertura["missing_accounts"]:
        saude = "account_missing"
    elif cobertura["unexpected_accounts"]:
        saude = "unexpected_account"

    idade_snapshot = _horas(effective_at, datetime.now(timezone.utc))

    resposta = {
        "availability": "available",
        "unavailable_reason": None,
        "channel": CHANNEL,
        "snapshot": {
            "channel": CHANNEL,
            "refresh_batch_id": snap["batch"],
            "effective_at": effective_at,
            "snapshot_hour": resumos[0]["snapshot_hour"],
            "load_mode": "manual_snapshot",
            "source_health": saude,
            # METADADO de frescor. `false` significa "a fonte nao avancou desde
            # a leitura anterior", nao falha: o estado e recomputado por relogio
            # a cada execucao justamente porque a fonte pode ficar parada.
            "source_advanced": any(bool(r["source_advanced"]) for r in resumos),
            "run_status": aud.get("status"),
            "rows_extracted": aud.get("rows_extracted"),
            "rows_loaded": aud.get("rows_loaded"),
            "audit_run_id": aud.get("sync_run_id"),
        },
        "totals": totais,
        "accounts": [
            {**{k: r[k] for k in medidas},
             "shop_account": r["shop_account"], "brand": r["brand"],
             "run_status": r["run_status"],
             "source_watermark_at": r["source_watermark_at"],
             "source_advanced": r["source_advanced"],
             "snapshot_hour": r["snapshot_hour"]}
            for r in resumos
        ],
        "freshness": _monta_frescor(_frescor(db), resumos, effective_at),
        "coverage": cobertura,
        "queue": [],
        "pagination": None,
        "limitations": _limitacoes(idade_snapshot),
    }

    if include_queue:
        fila, total = _pagina_da_fila(
            db, snap["batch"], brands=brands, accounts=accounts,
            situacoes=situacoes, order_by=order_by, limit=limit, offset=offset)
        resposta["queue"] = fila
        resposta["pagination"] = {
            "limit": limit, "offset": offset, "returned": len(fila),
            "total": total, "has_more": offset + len(fila) < total,
        }
    return resposta


def _pagina_da_fila(db, batch: str, *, brands, accounts, situacoes,
                    order_by: str, limit: int, offset: int) -> tuple[list[dict], int]:
    """Filtros e ordenacao por ALLOWLIST; valores sempre parametrizados."""
    onde = ["channel = :canal", "refresh_batch_id = :batch"]
    params: dict[str, Any] = {"canal": CHANNEL, "batch": batch}

    if brands:
        onde.append("brand = ANY(:brands)")
        params["brands"] = list(brands)
    if accounts:
        onde.append("shop_account = ANY(:accounts)")
        params["accounts"] = list(accounts)
    for i, s in enumerate(situacoes or []):
        # `s` ja' foi validado contra SITUACAO_SQL pela borda; o predicado vem
        # do dicionario, nunca da string do cliente.
        onde.append(SITUACAO_SQL[s])

    filtro = " AND ".join(onde)
    total = _linhas(db, f"SELECT count(*) AS n FROM {FILA} WHERE {filtro}",
                    params)[0]["n"]

    ordem = ", ".join(ORDENACOES.get(order_by, ORDEM_PADRAO))
    colunas = ", ".join(FILA_COLUNAS)
    brutas = _linhas(db, f"""
        SELECT marketplace_order_id, {colunas}
        FROM {FILA} WHERE {filtro}
        ORDER BY {ordem}
        LIMIT :limit OFFSET :offset
    """, {**params, "limit": limit, "offset": offset})

    linhas = []
    for r in brutas:
        linha = {k: r[k] for k in FILA_COLUNAS}
        linha["hours_open"] = _num(r["hours_open"])
        linha["hours_overdue"] = _num(r["hours_overdue"])
        linha["order_ref"] = _order_ref(str(r["marketplace_order_id"]))
        linhas.append(linha)
    return linhas, total


# ---------------------------------------------------------------------------
# Tendencia
# ---------------------------------------------------------------------------
def get_tendencia(db, *, window_hours: int = JANELA_PADRAO_HORAS,
                  brands: Optional[list[str]] = None,
                  accounts: Optional[list[str]] = None) -> dict:
    """Serie horaria POR CONTA.

    Horas diferentes NUNCA sao somadas: cada ponto e' um grao
    `(shop_account, snapshot_hour)` como o pipeline materializou. Somar duas
    horas contaria o mesmo pedido duas vezes — ele continua no backlog.
    """
    if not habilitado():
        return _indisponivel(UNAVAILABLE_DISABLED, tendencia=True)

    _abrir_snapshot(db)
    janela = max(1, min(int(window_hours), JANELA_MAX_HORAS))
    onde = ["channel = :canal",
            "snapshot_hour >= date_trunc('hour', now()) - make_interval(hours => :janela)"]
    params: dict[str, Any] = {"canal": CHANNEL, "janela": janela}
    if brands:
        onde.append("brand = ANY(:brands)")
        params["brands"] = list(brands)
    if accounts:
        onde.append("shop_account = ANY(:accounts)")
        params["accounts"] = list(accounts)

    pontos = _linhas(db, f"""
        SELECT snapshot_hour, shop_account, brand, refresh_batch_id, observed_at,
               backlog_count, overdue_count, due_within_24h_count, on_time_count,
               deadline_unavailable_count, over_48h_count, slow_count,
               zombie_count, stalled_count, source_watermark_at, source_advanced,
               run_status
        FROM {RUN} WHERE {" AND ".join(onde)}
        ORDER BY snapshot_hour ASC, brand ASC, shop_account ASC
        LIMIT :teto
    """, {**params, "teto": PONTOS_MAX + 1})

    truncado = len(pontos) > PONTOS_MAX
    if truncado:
        pontos = pontos[:PONTOS_MAX]

    horas = [p["snapshot_hour"] for p in pontos]
    return {
        "availability": "available",
        "unavailable_reason": None,
        "channel": CHANNEL,
        "window_hours": janela,
        "from_hour": min(horas) if horas else None,
        "to_hour": max(horas) if horas else None,
        "points": pontos,
        "truncated": truncado,
        "limitations": _limitacoes(None),
    }
