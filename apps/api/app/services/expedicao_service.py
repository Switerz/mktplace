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
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, get_args

from sqlalchemy import text

from app.config import settings
from app.schemas.expedicao import (
    UNAVAILABLE_CHANNEL_DISABLED,
    UNAVAILABLE_DISABLED,
    UNAVAILABLE_INCONSISTENT_BATCH,
    UNAVAILABLE_NO_SNAPSHOT,
    DeadlineStatus,
)
from app.services import expedicao_shopee_ldr_service as shopee_ldr_svc
from app.services import expedicao_tiktok_service as tiktok_svc

CHECK_FRESHNESS = "expedicao_source_freshness"

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

#: Marcas que a torre acompanha e que a fonte da SHOPEE nao cobre. Medido no
#: EXP-1E: Kokeshi existe em `marts.dim_loja` e nao existe em
#: `raw.shopee_orders`. No Mercado Livre a Kokeshi E' coberta — por isso a lista
#: e' POR CANAL: publicar a lacuna da Shopee no payload do ML afirmaria um
#: buraco que nao existe.
MARCAS_SEM_COBERTURA = ("kokeshi",)

NOTA_SEM_AGENDAMENTO = (
    "Nenhum agendamento existe: a fotografia so' avanca quando alguem executa"
    " o refresh manualmente."
)
NOTA_SEM_IDENTIFICADOR = (
    "Identificador do pedido nao e' servido: esta API nao tem autenticacao."
)
NOTA_FRESCOR = (
    "freshness mede o watermark da CONTA; oldest_row_age_hours e' contexto e"
    " nao reprova a fonte."
)


@dataclass(frozen=True)
class Canal:
    """Tudo o que muda de um canal para o outro, reunido num lugar so'.

    Sem isto, `channel = 'shopee'` fica espalhado por sete consultas, e basta
    uma ficar para tras para o payload de um canal servir a fila do outro. Com
    o objeto, esquecer o filtro vira erro de argumento, nao dado errado.
    """

    slug: str
    marketplace_id: int
    marcas_sem_cobertura: tuple[str, ...]
    notas: tuple[str, ...]
    #: Coluna do REGISTRY que carrega a mesma identidade que a fotografia
    #: publica em `shop_account`. E' o que permite comparar ESPERADO com
    #: OBSERVADO sem misturar dominios — ver `_identidade_da_conta`.
    identidade_no_registry: str

    @property
    def fonte_auditoria(self) -> str:
        """Nome em `audit.source_sync_run`, como a CLI o grava."""
        return f"expedicao_{self.slug}"


CANAIS: dict[str, Canal] = {
    "shopee": Canal(
        slug="shopee",
        marketplace_id=3,
        # `raw.shopee_orders.shop_account` e' o nome da loja, que coincide com
        # o `brand_key`. O `external_seller_id` do registry e' o `shop_id`
        # numerico, que a fotografia NAO publica.
        identidade_no_registry="brand",
        marcas_sem_cobertura=MARCAS_SEM_COBERTURA,
        notas=(
            NOTA_SEM_AGENDAMENTO,
            "Kokeshi nao e' coberta por esta fonte (ausente em raw.shopee_orders).",
            NOTA_SEM_IDENTIFICADOR,
            NOTA_FRESCOR,
        ),
    ),
    "mercadolivre": Canal(
        slug="mercadolivre",
        marketplace_id=2,
        # A fila do ML publica o `seller_id` em `shop_account`, e o registry o
        # guarda em `external_seller_id`. Sao a MESMA entidade.
        identidade_no_registry="external_seller_id",
        # A Kokeshi E' coberta aqui. Lista vazia, nao a da Shopee.
        marcas_sem_cobertura=(),
        notas=(
            NOTA_SEM_AGENDAMENTO,
            # Medido no EXP-3A: os campos candidatos a prazo estao 0% preenchidos
            # na fonte. Derivar um prazo produziria "vencido" onde o marketplace
            # nunca prometeu nada, e a Torre perderia o direito de ser acreditada.
            "Mercado Livre nao publica prazo de despacho: deadline_status e'"
            " sempre 'unavailable' e a ausencia nao indica atraso.",
            "A unidade operacional do Mercado Livre e' o SHIPMENT, nao o pedido.",
            "A conta e' identificada pelo seller_id; a marca vem do registry.",
            NOTA_SEM_IDENTIFICADOR,
            NOTA_FRESCOR,
        ),
    ),
}

#: Canal historico desta API. Continua sendo o default para nao quebrar quem ja'
#: consome a rota sem informar `channel`.
CANAL_PADRAO = "shopee"

#: Compatibilidade com quem importava os nomes antigos.
CHANNEL = CANAL_PADRAO
MARKETPLACE_ID = CANAIS[CANAL_PADRAO].marketplace_id


def habilitado() -> bool:
    return bool(getattr(settings, "expedicao_api_enabled", False))


def canal_habilitado(canal: Canal) -> bool:
    """A Shopee segue a flag geral; o ML tem a sua, default `false`.

    Sao duas decisoes diferentes: "a Expedicao esta no ar" e "o Mercado Livre
    ja' pode ser mostrado". Amarrar as duas na mesma flag obrigaria a derrubar a
    Shopee para adiar o ML, ou a expor o ML sem querer ao ligar a Expedicao.
    """
    if canal.slug == "mercadolivre":
        return bool(getattr(settings, "expedicao_ml_api_enabled", False))
    return True


def resolver_canal(slug: Optional[str]) -> Canal:
    """Slug -> canal, por ALLOWLIST. `None` cai no canal historico.

    Levanta `KeyError` para slug desconhecido; quem traduz em 422 e' a borda,
    que tambem garante que a entrada do cliente nunca volte ecoada.
    """
    return CANAIS[slug or CANAL_PADRAO]


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


def _indisponivel(canal: Canal, motivo: str, *, tendencia: bool = False) -> dict:
    envelope = {
        "availability": "unavailable",
        "unavailable_reason": motivo,
        "channel": canal.slug,
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
        "limitations": _limitacoes(canal, None),
    }
    if tendencia:
        envelope["window_hours"] = 0
        envelope["points"] = []
        envelope["truncated"] = False
    return envelope


def _limitacoes(canal: Canal, idade_h: Optional[float],
                extras: Optional[list[str]] = None) -> dict:
    notas = list(canal.notas)
    notas.extend(extras or [])
    return {
        "no_automation": True,
        "load_mode": "manual_snapshot",
        "snapshot_age_hours": idade_h,
        "brands_not_covered": list(canal.marcas_sem_cobertura),
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
def _carregar_snapshot(db, canal: Canal) -> tuple[Optional[dict], Optional[str]]:
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
    """, {"canal": canal.slug})[0]

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
    """, {"canal": canal.slug, "batch": cab["batch"]})

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


def _auditoria(db, canal: Canal, effective_at: datetime) -> dict:
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
    """, {"fonte": canal.fonte_auditoria, "instante": effective_at})
    return linhas[0] if linhas else {}


def _frescor(db, canal: Canal) -> list[dict]:
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
    """, {"check": CHECK_FRESHNESS, "tabela": FILA,
          "mkt": canal.marketplace_id})


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


def _identidade_da_conta(canal: Canal, linha_registry: dict) -> str:
    """A identidade da conta, no MESMO dominio que `shop_account` da fotografia.

    Antes, `expected_accounts` trazia MARCAS e `observed_accounts` trazia
    `shop_account`. Na Shopee os dois coincidem por acidente — o nome da loja e'
    o da marca — e a inconsistencia passava despercebida. No Mercado Livre sao
    dominios diferentes: marca de um lado, `seller_id` do outro. Comparar
    conjuntos assim nunca acusaria conta faltando, porque nada nunca se cruza.

    Cada canal declara QUAL coluna do registry carrega a identidade que a
    fotografia publica; aqui so' se le' essa coluna.
    """
    return str(linha_registry[canal.identidade_no_registry])


def _cobertura(db, canal: Canal, resumos: list[dict],
               effective_at: datetime) -> dict:
    """Conjuntos ESPERADO x OBSERVADO, os dois no dominio da CONTA.

    A cobertura por MARCA continua existindo, e em campo proprio:
    `brands_not_covered`. Misturar as duas coisas para preservar compatibilidade
    aparente foi justamente o defeito corrigido aqui.
    """
    esperadas = _linhas(db, """
        SELECT sa.external_seller_id, l.brand_key AS brand, sa.account_name
        FROM marts.dim_seller_account sa
        JOIN marts.dim_loja l ON l.loja_id = sa.loja_id
        WHERE sa.marketplace_id = :mkt AND sa.ativo AND l.ativo
        ORDER BY l.brand_key
    """, {"mkt": canal.marketplace_id})
    esperadas_por_conta = {_identidade_da_conta(canal, r): r for r in esperadas}
    observadas = {str(r["shop_account"]): r for r in resumos}

    contas = []
    for conta in sorted(set(esperadas_por_conta) | set(observadas)):
        r = observadas.get(conta)
        registro = esperadas_por_conta.get(conta)
        contas.append({
            "shop_account": conta,
            # A marca vem do que a fotografia publicou; se a conta nao foi
            # observada, vem do registry. Nunca e' derivada da identidade.
            "brand": r["brand"] if r else (registro or {}).get("brand", conta),
            "observed": r is not None,
            "backlog_count": r["backlog_count"] if r else None,
            "source_watermark_at": r["source_watermark_at"] if r else None,
            "source_age_hours": _horas(r["source_watermark_at"], effective_at)
            if r else None,
            "source_advanced": r["source_advanced"] if r else None,
        })

    return {
        "expected_accounts": sorted(esperadas_por_conta),
        "observed_accounts": sorted(observadas),
        "missing_accounts": sorted(set(esperadas_por_conta) - set(observadas)),
        "unexpected_accounts": sorted(set(observadas) - set(esperadas_por_conta)),
        "accounts": contas,
        # Cobertura de MARCAS vive aqui, e so' aqui.
        "brands_not_covered": list(canal.marcas_sem_cobertura),
    }


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------
def get_expedicao(
    db,
    *,
    channel: Optional[str] = None,
    brands: Optional[list[str]] = None,
    accounts: Optional[list[str]] = None,
    situacoes: Optional[list[str]] = None,
    order_by: str = "criticidade",
    limit: int = LIMIT_PADRAO,
    offset: int = 0,
    include_queue: bool = True,
) -> dict:
    canal = resolver_canal(channel)
    if not habilitado():
        return _indisponivel(canal, UNAVAILABLE_DISABLED)
    if not canal_habilitado(canal):
        # Canal CONHECIDO e ainda nao exposto. Nao e' 422: o consumidor pediu
        # algo valido, e o envelope diz por que nao veio dado.
        return _indisponivel(canal, UNAVAILABLE_CHANNEL_DISABLED)

    _abrir_snapshot(db)
    snap, motivo = _carregar_snapshot(db, canal)
    if snap is None:
        return _indisponivel(canal, motivo or UNAVAILABLE_NO_SNAPSHOT)

    resumos = snap["resumos"]
    effective_at = snap["effective_at"]
    aud = _auditoria(db, canal, effective_at)

    medidas = ("backlog_count", "overdue_count", "due_within_24h_count",
               "on_time_count", "deadline_unavailable_count", "over_48h_count",
               "slow_count", "zombie_count", "stalled_count")
    totais = {m: sum(int(r[m]) for r in resumos) for m in medidas}

    saude = "healthy"
    cobertura = _cobertura(db, canal, resumos, effective_at)
    if cobertura["missing_accounts"]:
        saude = "account_missing"
    elif cobertura["unexpected_accounts"]:
        saude = "unexpected_account"

    idade_snapshot = _horas(effective_at, datetime.now(timezone.utc))

    resposta = {
        "availability": "available",
        "unavailable_reason": None,
        "channel": canal.slug,
        "snapshot": {
            "channel": canal.slug,
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
        "freshness": _monta_frescor(_frescor(db, canal), resumos, effective_at),
        "coverage": cobertura,
        "queue": [],
        "pagination": None,
        "limitations": _limitacoes(canal, idade_snapshot),
    }

    if include_queue:
        fila, total = _pagina_da_fila(
            db, canal, snap["batch"], brands=brands, accounts=accounts,
            situacoes=situacoes, order_by=order_by, limit=limit, offset=offset)
        resposta["queue"] = fila
        resposta["pagination"] = {
            "limit": limit, "offset": offset, "returned": len(fila),
            "total": total, "has_more": offset + len(fila) < total,
        }
    return resposta


def _pagina_da_fila(db, canal: Canal, batch: str, *, brands, accounts, situacoes,
                    order_by: str, limit: int, offset: int) -> tuple[list[dict], int]:
    """Filtros e ordenacao por ALLOWLIST; valores sempre parametrizados.

    `channel` e `refresh_batch_id` entram JUNTOS no filtro: o batch ja' seria
    suficiente hoje, mas um lote com o mesmo id em dois canais serviria a fila
    errada, e o custo de manter o canal explicito e' zero.
    """
    onde = ["channel = :canal", "refresh_batch_id = :batch"]
    params: dict[str, Any] = {"canal": canal.slug, "batch": batch}

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
    # EXPEDICAO-ETAPA-POPUP: a etapa de cada pedido (T-0015) so' com a 033 no
    # destino. Sem ela a coluna nao existe e a linha sai com `etapa` nula —
    # exatamente a resposta de antes, mais um campo opcional.
    sonda = _linhas(db, _COLUNA_ETAPA_SQL, {})
    tem_etapa = bool(sonda) and sonda[0].get("ok") is True
    extra = ", etapa" if tem_etapa else ""
    brutas = _linhas(db, f"""
        SELECT marketplace_order_id, {colunas}{extra}
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
        linha["etapa"] = str(r["etapa"]) if tem_etapa and r["etapa"] is not None else None
        linhas.append(linha)
    return linhas, total


# ---------------------------------------------------------------------------
# Tendencia
# ---------------------------------------------------------------------------
def get_tendencia(db, *, channel: Optional[str] = None,
                  window_hours: int = JANELA_PADRAO_HORAS,
                  brands: Optional[list[str]] = None,
                  accounts: Optional[list[str]] = None) -> dict:
    """Serie horaria POR CONTA.

    Horas diferentes NUNCA sao somadas: cada ponto e' um grao
    `(shop_account, snapshot_hour)` como o pipeline materializou. Somar duas
    horas contaria o mesmo pedido duas vezes — ele continua no backlog.
    """
    canal = resolver_canal(channel)
    if not habilitado():
        return _indisponivel(canal, UNAVAILABLE_DISABLED, tendencia=True)
    if not canal_habilitado(canal):
        return _indisponivel(canal, UNAVAILABLE_CHANNEL_DISABLED, tendencia=True)

    _abrir_snapshot(db)
    janela = max(1, min(int(window_hours), JANELA_MAX_HORAS))
    onde = ["channel = :canal",
            "snapshot_hour >= date_trunc('hour', now()) - make_interval(hours => :janela)"]
    params: dict[str, Any] = {"canal": canal.slug, "janela": janela}
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
        "channel": canal.slug,
        "window_hours": janela,
        "from_hour": min(horas) if horas else None,
        "to_hour": max(horas) if horas else None,
        "points": pontos,
        "truncated": truncado,
        "limitations": _limitacoes(canal, None),
    }


# ---------------------------------------------------------------------------
# T-0011 — Jornada de pedidos (contrato jornada v1)
# ---------------------------------------------------------------------------
# Uma resposta por canal com DUAS secoes de grao diferente, que nunca se somam:
#
#   fila_aberta  fotografia AGORA (`expedicao_fila_atual`), grao pedido/shipment;
#   coorte       agregado por dia (TikTok: pagamento; Shopee: vencimento).
#
# TODA contagem sai de `count`/`sum` no SERVIDOR sobre a base completa, com o
# filtro de marca aplicado tambem aos totais. A soma das etapas e' CONFERIDA
# contra uma base somada de forma independente; se nao fechar, a resposta vira
# `partial` com o motivo e os numeros ficam como estao — consertar a soma
# esconderia justamente o defeito que a conferencia existe para mostrar.
#
# FLAGS (sem flag nova):
#   * `expedicao_api_enabled` para tudo;
#   * ML exige tambem `expedicao_ml_api_enabled` (`canal_habilitado`);
#   * coorte Shopee exige `expedicao_shopee_ldr_api_enabled`
#     (`shopee_ldr_svc.habilitado`); desligada -> coorte `None` + limitacao;
#   * TikTok: `GET /tiktok` NAO tem flag de API (so' confere a tabela com
#     `to_regclass`). Aqui vale a regra geral do contrato — `expedicao_api_enabled`
#     — e nenhuma flag propria, como na `/tiktok`.
JORNADA_CANAIS = ("shopee", "mercadolivre", "tiktok")
JORNADA_DIAS_PADRAO = 7
#: Teto da janela por canal: o mesmo das rotas irmas. O ML nao tem coorte e
#: fica FORA: no ML `days` e' ignorado, nao validado (contrato v1.1 §3.1.6).
JORNADA_DIAS_MAX = {
    "shopee": shopee_ldr_svc.JANELA_MAX_DIAS,
    "tiktok": tiktok_svc.JANELA_MAX_DIAS,
}
JORNADA_EVENTO_PADRAO = "coleta"

#: Dominio de `deadline_status` (o mesmo CHECK da 018). A etapa unica da fila
#: aberta conta as linhas cujo status esta no dominio publicado.
_STATUS_DE_PRAZO = get_args(DeadlineStatus)

#: Pior frescor entre as marcas filtradas. `unknown` fica acima de `stale`: nao
#: da para afirmar que a fonte esta so' atrasada.
_ORDEM_FRESCOR = {"fresh": 0, "stale": 1, "unknown": 2, "critical": 3}

_GRAVIDADE_STATUS = {"ok": 0, "partial": 1, "unavailable": 2}

LIMITACAO_ML_SEM_PRAZO = "o Mercado Livre não publica prazo de despacho"
LIMITACOES_FILA = {
    "shopee": [
        "etapa interna (a preparar × envio organizado) não publicada na fila",
        "FBS não separado na fila aberta",
    ],
    "mercadolivre": [
        "substatus do shipment não publicado na fila",
        "Full (fulfillment) fora da fila por contrato",
    ],
}
#: Limitacao que SAI quando a etapa real esta' publicada (T-0015). E' a
#: primeira de `LIMITACOES_FILA` de cada canal.
LIMITACAO_ETAPA_NAO_PUBLICADA = {
    "shopee": LIMITACOES_FILA["shopee"][0],
    "mercadolivre": LIMITACOES_FILA["mercadolivre"][0],
}
#: Fato do contrato etapa v1, secao 2: a T-0015 NAO muda o backlog.
LIMITACAO_ML_ENTREGUE_NO_BACKLOG = (
    "envios 'entregue à transportadora' ainda contam no backlog: o Mercado Livre "
    "mantém o status ready_to_ship")
LIMITACAO_TIKTOK_SEM_FILA = (
    "o TikTok não publica fila aberta em grão de pedido; só a coorte diária")
LIMITACAO_TIKTOK_PRAZO = (
    "prazo reconstruído da política de dias úteis; não é o SLA oficial do TikTok")
LIMITACAO_SHOPEE_LDR_DESLIGADA = (
    "coorte Shopee (LDR operacional) desligada: expedicao_shopee_ldr_api_enabled")
LIMITACAO_SHOPEE_NAO_OFICIAL = (
    "indicador operacional interno: não é o índice oficial de Account Health "
    "da Shopee")
LIMITACAO_COORTE_IMATURA = "dias imaturos ainda podem mudar"


class _FiltroMarca:
    """O MESMO predicado de marca nos tres canais, sempre no servidor.

    Comparacao case-insensitive: a entrada e' normalizada aqui e a coluna com
    `lower()` no SQL. Lista vazia = todas as marcas (nenhum predicado).
    """

    def __init__(self, marcas: list[str]):
        self.marcas = marcas

    def sql(self, coluna: str = "brand") -> str:
        return f" AND lower({coluna}) = ANY(:marcas)" if self.marcas else ""

    def params(self) -> dict:
        return {"marcas": self.marcas} if self.marcas else {}


def normalizar_marcas(brands: Optional[list[str]]) -> list[str]:
    """Minusculas, sem espaco nas pontas, sem repeticao, ordenadas."""
    return sorted({b.strip().lower() for b in (brands or []) if b and b.strip()})


def jornada_habilitada(channel: str) -> bool:
    """A rota inteira esta ligada para o canal? Nao consulta o banco."""
    if not habilitado():
        return False
    if channel == "mercadolivre":
        return canal_habilitado(CANAIS["mercadolivre"])
    return True


def _tabelas_existem(db, *tabelas: str) -> bool:
    for t in tabelas:
        ok = db.execute(text("SELECT to_regclass(:t) IS NOT NULL AS ok"),
                        {"t": t}).scalar()
        if ok is not True:
            return False
    return True


def _janela_coorte(dias: int, agora: datetime) -> tuple[date, date]:
    """Dias FECHADOS em data de Brasilia: termina ontem, `dias` datas.

    Exemplo do contrato: gerado em 26/10 com `days=7` -> 19/10 a 25/10. O dia
    de hoje fica fora porque ainda nao terminou de acontecer.
    """
    hoje = tiktok_svc._hoje_brt(agora)
    fim = hoje - timedelta(days=1)
    return fim - timedelta(days=dias - 1), fim


def _confere(base: int, etapas: list[dict], sobreposicoes: list[dict],
             secao: str) -> tuple[bool, list[str]]:
    """Soma das etapas == base; sobreposicao cabe no conjunto de que e' parte.

    So' CONFERE. Nunca ajusta numero.
    """
    motivos: list[str] = []
    soma = sum(e["count"] for e in etapas)
    fecham = soma == base
    if not fecham:
        motivos.append(f"{secao}: etapas_nao_fecham (soma {soma} != base {base})")
    por_etapa = {e["key"]: e["count"] for e in etapas}
    for s in sobreposicoes:
        if s["count"] is None:
            continue
        pai = s.get("subconjunto_de")
        teto = por_etapa.get(pai, base) if pai else base
        if s["count"] > teto:
            motivos.append(
                f"{secao}: sobreposicao_{s['key']}_excede ({s['count']} > {teto})")
    return fecham, motivos


# ----- fila aberta (Shopee, ML) --------------------------------------------
_FILA_SOBREPOSICOES = (
    # chave da jornada -> predicado ja' escrito em SITUACAO_SQL (sem duplicar).
    ("vencido", "overdue"),
    ("vence_24h", "due_within_24h"),
    ("sem_prazo", "deadline_unavailable"),
    ("acima_48h", "over_48h"),
    ("travado", "stalled"),
    ("lento", "slow"),
    ("zumbi_fonte", "zombie"),
)
#: Sobreposicoes que dependem de prazo: no ML a fonte nao publica prazo, entao
#: elas saem `None` + motivo, nunca 0.
_SOBREPOSICOES_DE_PRAZO = ("vencido", "vence_24h")

#: Contagens do resumo por conta (018). Fotografia vazia = TODAS zeradas.
_MEDIDAS_RESUMO = ("backlog_count", "overdue_count", "due_within_24h_count",
                   "on_time_count", "deadline_unavailable_count", "over_48h_count",
                   "slow_count", "zombie_count", "stalled_count")


def _fila_vazia_publicada(db, canal: Canal) -> tuple[Optional[dict], Optional[str]]:
    """Fila sem linha: o ULTIMO lote do resumo prova uma fotografia vazia?

    O publicador grava a fotografia vazia como fila limpa + um resumo por conta
    com tudo zerado, na MESMA transacao (`pipelines/expedicao/cli.py`, desfecho
    "fonte saudavel e vazia"). Entao:

    * nenhum resumo do canal          -> nunca publicou: `no_snapshot_published`;
    * ultimo lote todo zerado         -> vazio COMPROVADO: snapshot com 0 linhas;
    * ultimo lote com pedido no resumo -> fila apagada por fora ou lote partido:
      `inconsistent_batch` com o tamanho do buraco. Nunca vira zero.

    O ultimo lote e' o de maior `observed_at`; mais de um lote, hora ou
    instante nesse topo e' a mesma falha fechada de `_carregar_snapshot`.
    """
    resumos = _linhas(db, f"""
        SELECT shop_account, brand, {", ".join(_MEDIDAS_RESUMO)},
               run_status, source_watermark_at, source_advanced, snapshot_hour,
               observed_at, refresh_batch_id
        FROM {RUN}
        WHERE channel = :canal
          AND observed_at = (SELECT max(observed_at) FROM {RUN} WHERE channel = :canal)
        ORDER BY brand, shop_account
    """, {"canal": canal.slug})
    if not resumos:
        return None, UNAVAILABLE_NO_SNAPSHOT
    if (len({r["refresh_batch_id"] for r in resumos}) != 1
            or len({r["snapshot_hour"] for r in resumos}) != 1):
        return None, UNAVAILABLE_INCONSISTENT_BATCH
    pendentes = sum(int(r["backlog_count"]) for r in resumos)
    if any(int(r[m]) for r in resumos for m in _MEDIDAS_RESUMO):
        return None, (f"{UNAVAILABLE_INCONSISTENT_BATCH} (fila vazia, mas o resumo "
                      f"do ultimo lote tem {pendentes} pedido(s) no backlog)")
    return {"batch": resumos[0]["refresh_batch_id"],
            "effective_at": resumos[0]["observed_at"],
            "linhas": 0, "resumos": resumos}, None


#: T-0015 (contrato etapa v1, secoes 3 e 5). Ordem ESTAVEL no fluxo do pedido;
#: o rotulo e' do contrato. `nao_publicada` e' o DEFAULT da 033: linha gravada
#: antes dela ou pelo publicador antigo.
ETAPAS_FILA = (
    ("a_faturar", "A faturar"),
    ("a_imprimir", "A imprimir"),
    ("em_separacao", "Em separação"),
    ("a_preparar", "A preparar"),
    ("aguardando_coleta", "Aguardando coleta"),
    ("entregue_transportadora", "Entregue à transportadora"),
    ("sinalizado_ml", "Sinalizado pelo Mercado Livre"),
    ("outro", "Outro status da fonte"),
    ("nao_publicada", "Etapa ainda não publicada"),
)
ETAPA_NAO_PUBLICADA = "nao_publicada"
_ORDEM_ETAPA = {k: i for i, (k, _) in enumerate(ETAPAS_FILA)}
_ROTULO_ETAPA = dict(ETAPAS_FILA)

#: UM `SELECT` so' (sem subconsulta): o teste de "serving sem escrita" conta
#: um SELECT por consulta.
_COLUNA_ETAPA_SQL = """
    SELECT count(*) > 0 AS ok FROM information_schema.columns
    WHERE table_schema = 'marts' AND table_name = 'expedicao_fila_atual'
      AND column_name = 'etapa'
"""


def _etapas_publicadas(db, canal: Canal, batch: str,
                       filtro: _FiltroMarca) -> Optional[dict[str, int]]:
    """Contagem por `etapa` do lote, no servidor, com o filtro de marca.

    `None` quando a 033 nao esta' no destino. A deteccao roda na MESMA
    transacao REPEATABLE READ da resposta (`_abrir_snapshot`); a contagem cai
    no mesmo snapshot da base, entao a soma fecha com ela.
    """
    if db.execute(text(_COLUNA_ETAPA_SQL)).scalar() is not True:
        return None
    return {
        str(r["etapa"]): int(r["n"])
        for r in _linhas(db, f"""
            SELECT etapa, count(*) AS n
            FROM {FILA}
            WHERE channel = :canal AND refresh_batch_id = :batch{filtro.sql()}
            GROUP BY etapa
        """, {"canal": canal.slug, "batch": batch, **filtro.params()})
    }


def _etapas_reais(por_etapa: dict[str, int]) -> list[dict]:
    """Uma entrada por etapa presente, na ordem do fluxo.

    Valor fora do dominio (impossivel com o CHECK da 033, mas nunca 500) vai
    para o fim, antes de `nao_publicada`, com a propria chave como rotulo.
    """
    fim = _ORDEM_ETAPA[ETAPA_NAO_PUBLICADA]
    chaves = sorted(por_etapa, key=lambda k: (_ORDEM_ETAPA.get(k, fim - 0.5), k))
    return [{"key": k, "label": _ROTULO_ETAPA.get(k, k), "count": por_etapa[k]}
            for k in chaves]


def _fila_aberta(db, channel: str,
                 filtro: _FiltroMarca) -> tuple[Optional[dict], list[str], str]:
    """(secao, motivos, estado). `None` quando a fonte nao serve fotografia."""
    canal = CANAIS[channel]
    if not _tabelas_existem(db, FILA, RUN):
        return None, ["fila_aberta: table_not_migrated"], "unavailable"
    snap, motivo = _carregar_snapshot(db, canal)
    if snap is None and motivo == UNAVAILABLE_NO_SNAPSHOT:
        # Fila sem linha nenhuma: ausencia OU fotografia vazia publicada. Quem
        # desempata e' o resumo do ultimo lote, nunca a fila (`_carregar_snapshot`
        # fica como esta': as rotas irmas dependem dele).
        snap, motivo = _fila_vazia_publicada(db, canal)
    if snap is None:
        return None, [f"fila_aberta: {motivo or UNAVAILABLE_NO_SNAPSHOT}"], "unavailable"

    dominio = ", ".join(f"'{s}'" for s in _STATUS_DE_PRAZO)
    colunas = ",\n               ".join(
        f"count(*) FILTER (WHERE {SITUACAO_SQL[sit]}) AS {chave}"
        for chave, sit in _FILA_SOBREPOSICOES)
    linha = _linhas(db, f"""
        SELECT count(*) AS base,
               count(*) FILTER (WHERE deadline_status IN ({dominio}))
                   AS aguardando_expedicao,
               {colunas}
        FROM {FILA}
        WHERE channel = :canal AND refresh_batch_id = :batch{filtro.sql()}
    """, {"canal": canal.slug, "batch": snap["batch"], **filtro.params()})[0]

    base = int(linha["base"])
    etapas = [{"key": "aguardando_expedicao", "label": "Aguardando expedição",
               "count": int(linha["aguardando_expedicao"])}]
    limitacoes = list(LIMITACOES_FILA[channel])
    etapa_parcial = False

    # T-0015: etapa real so' com a 033 no destino E alguma linha com etapa
    # publicada. Sem a coluna, ou com tudo `nao_publicada`, a secao e' a de
    # antes, byte a byte (etapa unica + limitacao).
    por_etapa = _etapas_publicadas(db, canal, snap["batch"], filtro)
    if por_etapa and any(k != ETAPA_NAO_PUBLICADA for k in por_etapa):
        etapas = _etapas_reais(por_etapa)
        etapa_parcial = ETAPA_NAO_PUBLICADA in por_etapa
        if not etapa_parcial:
            limitacoes.remove(LIMITACAO_ETAPA_NAO_PUBLICADA[channel])
        if channel == "mercadolivre":
            limitacoes.append(LIMITACAO_ML_ENTREGUE_NO_BACKLOG)

    sobreposicoes = []
    for chave, _sit in _FILA_SOBREPOSICOES:
        if channel == "mercadolivre" and chave in _SOBREPOSICOES_DE_PRAZO:
            sobreposicoes.append({"key": chave, "count": None,
                                  "reason": LIMITACAO_ML_SEM_PRAZO})
        else:
            sobreposicoes.append({"key": chave, "count": int(linha[chave])})
    fecham, motivos = _confere(base, etapas, sobreposicoes, "fila_aberta")
    if etapa_parcial:
        # Parte do lote com etapa real e parte sem: numeros servidos como estao,
        # sem reconciliar a forca (contrato etapa v1, secao 5).
        motivos.append("fila_aberta: etapa_parcial")

    resumos = [r for r in snap["resumos"]
               if not filtro.marcas or str(r["brand"]).lower() in filtro.marcas]
    frescores = [f["freshness"] for f in
                 _monta_frescor(_frescor(db, canal), resumos, snap["effective_at"])]
    # Valor fora do dominio (auditoria antiga, typo) vale `unknown`: nem KeyError
    # (500), nem um Literal invalido na resposta.
    frescores = [f if f in _ORDEM_FRESCOR else "unknown" for f in frescores]
    frescor = max(frescores, key=lambda f: _ORDEM_FRESCOR.get(
        f, _ORDEM_FRESCOR["unknown"])) if frescores else "unknown"

    secao = {
        "grain": "shipment" if channel == "mercadolivre" else "pedido",
        "snapshot_at": snap["effective_at"],
        "freshness": frescor,
        "base": base,
        "etapas": etapas,
        "etapas_fecham": fecham,
        "sobreposicoes": sobreposicoes,
        "limitacoes": limitacoes,
    }
    return secao, motivos, "partial" if motivos else "ok"


# ----- coorte (TikTok, Shopee LDR) -----------------------------------------
#: Colunas por evento do TikTok. ALLOWLIST: o evento do cliente so' escolhe uma
#: entrada daqui, nunca vira nome de coluna. Atencao a concordancia
#: (`coleta_atrasada`, `coleta_pendente_vencida`).
_TIKTOK_COLUNAS = {
    "despacho": {
        "cancelado": "despacho_cancelado_antes_sla",
        "no_prazo": "despacho_no_prazo",
        "atrasado": "despacho_atrasado",
        "pend_vencido": "despacho_pendente_vencido",
        "pend_no_prazo": "despacho_pendente_no_prazo",
        "madura": "despacho_is_mature",
    },
    "coleta": {
        "cancelado": "coleta_cancelado_antes_sla",
        "no_prazo": "coleta_no_prazo",
        "atrasado": "coleta_atrasada",
        "pend_vencido": "coleta_pendente_vencida",
        "pend_no_prazo": "coleta_pendente_no_prazo",
        "madura": "coleta_is_mature",
    },
}
_ROTULO_CONCLUIDO = {"despacho": "Despachado", "coleta": "Coletado"}


def _agrega_coorte(db, *, tabela: str, canal: str, eixo: str, colunas: dict,
                   inicio: date, fim: date, filtro: _FiltroMarca) -> dict:
    """Um SELECT: base e etapas somadas SEPARADAMENTE, mais a maturidade.

    `linhas_canal` conta o canal na janela SEM o filtro de marca: distingue
    "nada publicado" (ausencia) de "a marca filtrada nao tem pedido" (zero).
    `dias_publicados` e `publicado_em` usam o MESMO criterio (sem marca): dia
    sem publicacao e publicacao velha sao propriedades da fonte, nao da marca.

    `fonte_lida_em` e' a excecao, e de proposito: o watermark e' POR CONTA (o
    publicador grava o da conta lida), e a `/shopee/ldr` com filtro de marca
    mede so' as linhas da marca. Conta parada nao pode ser escondida pela conta
    fresca de outra marca. So' quando a marca nao tem linha na janela (nada a
    medir dela) vale o watermark do canal.
    """
    sel = ",\n                   ".join(
        f"{expr} AS {nome}" for nome, expr in colunas.items())
    soma = ",\n               ".join(
        f"coalesce(sum({nome}), 0) AS {nome}" for nome in colunas if nome != "madura")
    linha = _linhas(db, f"""
        WITH c AS (
            SELECT {eixo} AS eixo,
                   source_watermark_at AS wm,
                   {sel}
            FROM {tabela}
            WHERE channel = :canal AND {eixo} BETWEEN :inicio AND :fim{filtro.sql()}
        ), dias AS (
            SELECT eixo, bool_and(madura) AS madura FROM c GROUP BY eixo
        )
        SELECT {soma},
               (SELECT count(*) FROM {tabela}
                 WHERE channel = :canal AND {eixo} BETWEEN :inicio AND :fim)
                   AS linhas_canal,
               (SELECT count(DISTINCT {eixo}) FROM {tabela}
                 WHERE channel = :canal AND {eixo} BETWEEN :inicio AND :fim)
                   AS dias_publicados,
               (SELECT max(effective_at) FROM {tabela}
                 WHERE channel = :canal AND {eixo} BETWEEN :inicio AND :fim)
                   AS publicado_em,
               (SELECT count(*) FROM c) AS linhas_marca,
               (SELECT max(wm) FROM c) AS fonte_marca,
               (SELECT max(source_watermark_at) FROM {tabela}
                 WHERE channel = :canal AND {eixo} BETWEEN :inicio AND :fim)
                   AS fonte_canal,
               (SELECT count(*) FROM dias WHERE madura) AS dias_maduros,
               (SELECT count(*) FROM dias WHERE NOT madura) AS dias_imaturos
        FROM c
    """, {"canal": canal, "inicio": inicio, "fim": fim, **filtro.params()})[0]
    # Mesma regra da `/shopee/ldr`: o MAIS RECENTE watermark das linhas lidas;
    # nenhum carimbo nelas -> `None` (fonte sem carimbo), nunca o do canal.
    linha["fonte_lida_em"] = (linha["fonte_marca"] if linha["linhas_marca"]
                              else linha["fonte_canal"])
    return linha


def _coorte_tiktok(db, evento: str, dias: int, agora: datetime,
                   filtro: _FiltroMarca) -> tuple[Optional[dict], list[str], str]:
    if not _tabelas_existem(db, tiktok_svc.TABELA):
        return None, [f"coorte: {tiktok_svc.UNAVAILABLE_NOT_MIGRATED}"], "unavailable"
    inicio, fim = _janela_coorte(dias, agora)
    c = _TIKTOK_COLUNAS[evento]
    a = _agrega_coorte(db, tabela=tiktok_svc.TABELA, canal=tiktok_svc.CANAL,
                       eixo="paid_date", inicio=inicio, fim=fim, filtro=filtro,
                       colunas={
                           "base": "pedidos_pagos_brutos",
                           "fora_amostra": "amostras_excluidas",
                           "cancelado_antes": c["cancelado"],
                           "aguardando": f"{c['pend_vencido']} + {c['pend_no_prazo']}",
                           "concluido": f"{c['no_prazo']} + {c['atrasado']}",
                           "aguardando_vencido": c["pend_vencido"],
                           "concluido_atrasado": c["atrasado"],
                           "madura": c["madura"],
                       })
    if not a["linhas_canal"]:
        return None, [f"coorte: {tiktok_svc.UNAVAILABLE_NO_DATA}"], "unavailable"
    etapas = [
        {"key": "fora_amostra", "label": "Amostra grátis (fora)",
         "count": int(a["fora_amostra"])},
        {"key": "cancelado_antes", "label": "Cancelado antes do prazo",
         "count": int(a["cancelado_antes"])},
        {"key": "aguardando", "label": f"Aguardando {evento}",
         "count": int(a["aguardando"])},
        {"key": "concluido", "label": _ROTULO_CONCLUIDO[evento],
         "count": int(a["concluido"])},
    ]
    return _monta_coorte(a, etapas, eixo="paid_date", evento=evento,
                         inicio=inicio, fim=fim, dias=dias, fora=[],
                         limitacoes=[LIMITACAO_TIKTOK_PRAZO], agora=agora,
                         desatualizada=_tiktok_velha)


def _coorte_shopee(db, dias: int, agora: datetime,
                   filtro: _FiltroMarca) -> tuple[Optional[dict], list[str], str]:
    if not _tabelas_existem(db, shopee_ldr_svc.TABELA, shopee_ldr_svc.TABELA_SEM_PRAZO):
        return None, [f"coorte: {shopee_ldr_svc.UNAVAILABLE_NOT_MIGRATED}"], "unavailable"
    inicio, fim = _janela_coorte(dias, agora)
    a = _agrega_coorte(db, tabela=shopee_ldr_svc.TABELA, canal=shopee_ldr_svc.CANAL,
                       eixo="deadline_date", inicio=inicio, fim=fim, filtro=filtro,
                       colunas={
                           "base": "pedidos_pagos",
                           "fora_fbs": "excl_fbs",
                           "cancelado_antes": "excl_cancelado_sem_coleta",
                           "enviado_sem_registro": "excl_enviado_sem_coleta",
                           "aguardando": "pendente_vencido + pendente_no_prazo",
                           "concluido": "no_prazo + atrasado",
                           "aguardando_vencido": "pendente_vencido",
                           "concluido_atrasado": "atrasado",
                           "madura": "is_mature",
                       })
    if not a["linhas_canal"]:
        return None, [f"coorte: {shopee_ldr_svc.UNAVAILABLE_NO_DATA}"], "unavailable"
    # Pedido sem `ship_by` nao tem dia de vencimento: fica FORA da coorte, na
    # tabela propria, pela data de PAGAMENTO na mesma faixa de datas.
    sem_prazo = _linhas(db, f"""
        SELECT coalesce(sum(sem_prazo), 0) AS n
        FROM {shopee_ldr_svc.TABELA_SEM_PRAZO}
        WHERE channel = :canal AND paid_date BETWEEN :inicio AND :fim{filtro.sql()}
    """, {"canal": shopee_ldr_svc.CANAL, "inicio": inicio, "fim": fim,
          **filtro.params()})[0]["n"]
    etapas = [
        {"key": "fora_fbs", "label": "FBS (despachado pela Shopee)",
         "count": int(a["fora_fbs"])},
        {"key": "cancelado_antes", "label": "Cancelado sem coleta",
         "count": int(a["cancelado_antes"])},
        {"key": "enviado_sem_registro", "label": "Enviado sem registro de coleta",
         "count": int(a["enviado_sem_registro"])},
        {"key": "aguardando", "label": "Aguardando coleta",
         "count": int(a["aguardando"])},
        {"key": "concluido", "label": "Coletado", "count": int(a["concluido"])},
    ]
    return _monta_coorte(a, etapas, eixo="deadline_date", evento="coleta",
                         inicio=inicio, fim=fim, dias=dias,
                         fora=[{"key": "sem_prazo", "count": int(sem_prazo)}],
                         limitacoes=[LIMITACAO_SHOPEE_NAO_OFICIAL], agora=agora,
                         desatualizada=_shopee_ldr_velha)


# ----- frescor da coorte: o MESMO limite das rotas irmas, sem redigitar ------
# Cada regra devolve os SINTOMAS (codigo, limitacao); lista vazia = em dia. O
# codigo vira `coorte: <codigo>` no `reason`, e a limitacao diz qual foi a causa.
def _idade_h(instante: datetime, agora: datetime) -> float:
    return (agora - instante).total_seconds() / 3600.0


def _shopee_ldr_velha(publicado_em: datetime, fonte_lida_em: Optional[datetime],
                      agora: datetime) -> list[tuple[str, str]]:
    """`snapshot.stale` da `/shopee/ldr`, sintoma por sintoma.

    A LDR calcula `stale` dentro de `obter_ldr` (nao ha funcao para chamar):
    `idade > LIMITE or idade_fonte is None or idade_fonte > LIMITE`, com o
    `LIMITE_FOTOGRAFIA_HORAS` importado de la'. Sao os tres ramos abaixo, na
    mesma ordem. Publicacao recente sobre fonte parada E' velha: reprocessar
    dado antigo nao torna o numero atual.
    """
    limite = shopee_ldr_svc.LIMITE_FOTOGRAFIA_HORAS
    sintomas = []
    if _idade_h(publicado_em, agora) > limite:
        sintomas.append(("publicacao_desatualizada",
                         f"publicação de {_brt_texto(publicado_em)} ainda não atualizada"))
    if fonte_lida_em is None:
        sintomas.append(("fonte_sem_carimbo",
                         "a publicação não registra leitura da fonte; não dá para "
                         "afirmar que os números são atuais"))
    elif _idade_h(fonte_lida_em, agora) > limite:
        sintomas.append(("fonte_desatualizada",
                         f"a fonte não recebe dado novo desde "
                         f"{_brt_texto(fonte_lida_em)}; os números não são atuais"))
    return sintomas


#: Entrada NEUTRA para os alertas da `/tiktok`: nenhum alerta de taxa, pendente
#: ou marca dispara, sobra so' o de frescor.
_LDR_NEUTRA = {"above_target": False, "rate": None, "daily": [],
               "pending_overdue": 0, "pending_at_risk": 0}


def _tiktok_fotografia_velha(publicado_em: datetime, agora: datetime) -> bool:
    """Regra do alerta `fotografia_velha` da `/tiktok`, chamada e nao copiada.

    O limite vive como literal dentro de `tiktok_svc._alertas`; redigita-lo
    aqui deixaria as duas rotas discordarem no dia em que ele mudar.
    """
    alertas = tiktok_svc._alertas(ldr=_LDR_NEUTRA, marcas=[], evento="coleta",
                                  effective_at=publicado_em, agora=agora)
    return any(a["code"] == "fotografia_velha" for a in alertas)


def _tiktok_velha(publicado_em: datetime, fonte_lida_em: Optional[datetime],
                  agora: datetime) -> list[tuple[str, str]]:
    """So' a idade da PUBLICACAO, como a `/tiktok`.

    `fonte_lida_em` e' ignorado de proposito: o alerta `fotografia_velha` da
    rota irma nao olha `source_watermark_at` (so' `effective_at`). Considera-lo
    aqui faria a jornada chamar de velha uma coorte que a `/tiktok` serve como
    atual; a lacuna, se for lacuna, e' da rota irma e muda la' primeiro.
    """
    if _tiktok_fotografia_velha(publicado_em, agora):
        return [("publicacao_desatualizada",
                 f"publicação de {_brt_texto(publicado_em)} ainda não atualizada")]
    return []


def _brt_texto(instante: datetime) -> str:
    local = instante.astimezone(timezone.utc) + tiktok_svc.BRT
    return local.strftime("%d/%m/%Y %H:%M") + " (Brasília)"


def _monta_coorte(a: dict, etapas: list[dict], *, eixo: str, evento: str,
                  inicio: date, fim: date, dias: int, fora: list[dict],
                  limitacoes: list[str], agora: datetime,
                  desatualizada) -> tuple[dict, list[str], str]:
    base = int(a["base"])
    limitacoes = list(limitacoes)
    sobreposicoes = [
        {"key": "aguardando_vencido", "count": int(a["aguardando_vencido"]),
         "subconjunto_de": "aguardando"},
        {"key": "concluido_atrasado", "count": int(a["concluido_atrasado"]),
         "subconjunto_de": "concluido"},
    ]
    fecham, motivos = _confere(base, etapas, sobreposicoes, "coorte")
    imaturos = int(a["dias_imaturos"])

    # Dia sem publicacao NAO e' dia de zero pedidos: os totais ficam como
    # estao, mas a resposta deixa de ser `ok` e diz quantos dias faltam.
    faltam = dias - int(a["dias_publicados"])
    if faltam > 0:
        motivos.append(f"coorte: dias_sem_publicacao ({faltam})")
        limitacoes.append(f"{faltam} dia(s) da janela sem publicação; os totais "
                          f"não cobrem a janela inteira")

    publicado_em = a["publicado_em"]
    fonte_lida_em = a["fonte_lida_em"]
    if publicado_em is None:
        sintomas = [("publicacao_desatualizada",
                     "publicação de data desconhecida ainda não atualizada")]
    else:
        sintomas = desatualizada(publicado_em, fonte_lida_em, agora)
    velha = bool(sintomas)
    for codigo, texto in sintomas:
        motivos.append(f"coorte: {codigo}")
        limitacoes.append(texto)

    secao = {
        "eixo": eixo,
        "evento": evento,
        "janela": {"inicio": inicio, "fim": fim, "dias": dias},
        "base": base,
        "etapas": etapas,
        "etapas_fecham": fecham,
        "sobreposicoes": sobreposicoes,
        "fora_da_coorte": fora,
        "maturidade": {"dias_maduros": int(a["dias_maduros"]),
                       "dias_imaturos": imaturos},
        "publicado_em": publicado_em,
        "fonte_lida_em": fonte_lida_em,
        "desatualizada": velha,
        "limitacoes": limitacoes + ([LIMITACAO_COORTE_IMATURA] if imaturos else []),
    }
    return secao, motivos, "partial" if motivos else "ok"


def _agora() -> datetime:
    """Relogio da rota. Funcao propria para o teste HTTP fixar o instante."""
    return datetime.now(timezone.utc)


def get_jornada(
    db,
    *,
    channel: str,
    brands: Optional[list[str]] = None,
    dias: int = JORNADA_DIAS_PADRAO,
    evento: str = JORNADA_EVENTO_PADRAO,
    agora: Optional[datetime] = None,
) -> dict:
    """Jornada do canal: fila aberta + coorte, contadas no servidor.

    A borda ja' validou canal, marcas, janela e evento. Com a rota desligada
    devolve `disabled` SEM tocar em `db` (pode ser `None`).

    `status` e' o pior estado entre as secoes que o canal deveria servir:
    `ok` < `partial` < `unavailable`. A secao ausente sai `None`; a outra
    continua servida. `reason` junta os motivos, prefixados pela secao.
    """
    ref = (agora or _agora()).astimezone(timezone.utc)
    marcas = normalizar_marcas(brands)
    resposta: dict[str, Any] = {
        "channel": channel, "status": "ok", "reason": None,
        "brands_applied": marcas, "generated_at": ref,
        "fila_aberta": None, "coorte": None, "limitacoes": [],
    }
    if not jornada_habilitada(channel):
        resposta["status"] = "disabled"
        resposta["reason"] = (UNAVAILABLE_DISABLED if not habilitado()
                              else UNAVAILABLE_CHANNEL_DISABLED)
        return resposta

    _abrir_snapshot(db)
    filtro = _FiltroMarca(marcas)
    estados: list[str] = []
    motivos: list[str] = []

    if channel in ("shopee", "mercadolivre"):
        fila, m, e = _fila_aberta(db, channel, filtro)
        resposta["fila_aberta"] = fila
        motivos += m
        estados.append(e)
    else:
        resposta["limitacoes"].append(LIMITACAO_TIKTOK_SEM_FILA)

    if channel == "tiktok":
        coorte, m, e = _coorte_tiktok(db, evento, dias, ref, filtro)
        resposta["coorte"] = coorte
        motivos += m
        estados.append(e)
    elif channel == "shopee":
        if shopee_ldr_svc.habilitado():
            coorte, m, e = _coorte_shopee(db, dias, ref, filtro)
            resposta["coorte"] = coorte
            motivos += m
            estados.append(e)
        else:
            # Desligada por decisao, nao por falha: limitacao, sem rebaixar status.
            resposta["limitacoes"].append(LIMITACAO_SHOPEE_LDR_DESLIGADA)
    else:
        resposta["limitacoes"].append(LIMITACAO_ML_SEM_PRAZO)

    resposta["status"] = max(estados, key=_GRAVIDADE_STATUS.__getitem__)
    resposta["reason"] = "; ".join(motivos) or None
    return resposta
