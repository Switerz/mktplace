"""Registry (Neon) e extracao read-only da Shopee (Data Mart).

CONTRATO DA FONTE
-----------------
    raw.shopee_orders
    backlog: order_status IN ('PROCESSED','READY_TO_SHIP')
             AND pickup_done_time IS NULL
    chave:   (shop_account, order_sn)  -- pk_shopee_orders, UNIQUE

SEM CORTE TEMPORAL NO BACKLOG. Medimos, no ML, 628 pedidos ainda abertos com
mais de 60 dias; um `WHERE create_time >= ...` os apagaria da fila sem deixar
rastro. As janelas de 30 dias aparecem SOMENTE no baseline estatistico.

POR QUE O PLANO NAO VARRE A REPLICA
-----------------------------------
`EXPLAIN (ANALYZE, BUFFERS)` do predicado de backlog, medido em 15/09/2026:

    Index Scan using ix_shopee_orders_status on shopee_orders
      (cost=0.42..3389.69 rows=170) (actual rows=933 loops=1)
      Buffers: shared hit=1512 read=288
    Execution Time: 152.201 ms

1.800 buffers de uma tabela de 431 MB. A estimativa (170) ficou 5,5x abaixo do
real (933) — estatisticas defasadas na replica; nao muda o plano, mas ninguem
deve tomar o `rows=` do EXPLAIN como contagem.
"""
from __future__ import annotations

from datetime import datetime

from pipelines.expedicao.contract import (
    REGISTRY_SQL,
    SHOPEE_ALLOWED_SOURCE_COLUMNS,
    ExtractionResult,
    SellerAccount,
    SourceHealth,
    SourceWatermark,
)

#: Status que representam pedido pago e ainda nao despachado pelo seller.
#: Vocabulario completo medido na fonte: 8 status distintos.
SHOPEE_BACKLOG_STATUSES = ("PROCESSED", "READY_TO_SHIP")

#: Colunas extraidas, na ordem do SELECT.
SHOPEE_SELECT_COLUMNS = (
    "shop_account",
    "order_sn",
    "shop_id",
    "order_status",
    "create_time",
    "pay_time",
    "ship_by_date",
    "pickup_done_time",
    "shipping_carrier",
    "ingested_at",
)

#: Backlog. Sem cutoff. Colunas explicitas — nunca `SELECT *`, que traria
#: `buyer_cpf_id`, `recipient_address` e todo o resto do comprador.
SHOPEE_BACKLOG_SQL = """
SELECT
    shop_account,
    order_sn,
    shop_id,
    order_status,
    create_time,
    pay_time,
    ship_by_date,
    pickup_done_time,
    shipping_carrier,
    ingested_at
FROM raw.shopee_orders
WHERE order_status = ANY(%(statuses)s)
  AND pickup_done_time IS NULL
"""

#: Watermark POR CONTA. O maximo global esconderia uma conta parada atras de
#: outra atualizada — foi assim que a planilha de expedicao ficou 43 dias
#: defasada sem ninguem perceber.
SHOPEE_WATERMARK_SQL = """
SELECT
    shop_account,
    shop_id,
    MAX(ingested_at) AS max_ingested_at
FROM raw.shopee_orders
GROUP BY shop_account, shop_id
"""

#: Status excluidos do baseline. `CANCELLED` com `pickup_done_time` preenchido
#: existe de verdade: medimos 46 de 23.755 (0,19%) em 30 dias — pedido
#: despachado e cancelado depois. O tempo ate o pickup desses pedidos nao
#: representa a operacao normal e contaminaria o p50 de `is_slow_vs_baseline`.
#:
#: `TO_RETURN` NAO e excluido: o despacho aconteceu normalmente e a devolucao e
#: posterior — o tempo ate o pickup continua sendo tempo de expedicao valido.
BASELINE_EXCLUDED_STATUSES = ("CANCELLED",)

#: p50 por conta sobre as duracoes individuais — nao e media de medias.
#: A janela termina em `%(effective_at)s`, nunca em `now()`.
SHOPEE_BASELINE_SQL = """
SELECT
    shop_id,
    COUNT(*) AS sample_size,
    percentile_cont(0.5) WITHIN GROUP (
        ORDER BY EXTRACT(EPOCH FROM (pickup_done_time - COALESCE(pay_time, create_time))) / 3600.0
    ) AS p50_hours
FROM raw.shopee_orders
WHERE pickup_done_time IS NOT NULL
  AND COALESCE(pay_time, create_time) IS NOT NULL
  AND NOT (order_status = ANY(%(excluded_statuses)s))
  AND create_time >= %(effective_at)s::timestamptz - INTERVAL '30 days'
  AND create_time < %(effective_at)s::timestamptz
GROUP BY shop_id
"""


def _require_aware(instante: datetime, rotulo: str) -> datetime:
    """Recusa datetime naive.

    Mesma disciplina de `pipelines/common/operational_calendar.py`: um naive
    seria lido no fuso do PROCESSO, que roda em BRT no notebook e em UTC no
    worker. A diferenca de 3 horas move a fronteira das 48h sem aviso.
    """
    if instante.tzinfo is None:
        raise ValueError(
            f"{rotulo} sem timezone: o refresh exige instante com fuso "
            "explicito (um naive seria lido no fuso do processo)."
        )
    return instante


# ---------------------------------------------------------------------------
# Registry — lido do NEON, que e a autoridade
# ---------------------------------------------------------------------------
def load_registry(
    neon_conn, marketplace_id: int
) -> tuple[dict[str, SellerAccount], list[str]]:
    """Le `marts.dim_seller_account` + `marts.dim_loja`.

    Devolve `(contas_ativas_por_external_id, problemas)`. `problemas` nao vazio
    significa registry AMBIGUO: a publicacao deve bloquear, nunca escolher uma
    das linhas.

    Os quatro `shop_id` da Shopee NAO estao no codigo. Eles vivem no registry, e
    e la que se adiciona ou remove uma conta.
    """
    with neon_conn.cursor() as cur:
        cur.execute(REGISTRY_SQL, {"marketplace_id": marketplace_id})
        linhas = list(cur.fetchall())

    contas: dict[str, SellerAccount] = {}
    problemas: list[str] = []
    vistos: dict[str, int] = {}

    for linha in linhas:
        ext = str(linha["external_seller_id"])
        vistos[ext] = vistos.get(ext, 0) + 1
        if vistos[ext] > 1:
            # A UNIQUE (marketplace_id, external_seller_id) deveria impedir
            # isto. Se acontecer, o schema mudou — bloquear e melhor que
            # escolher em silencio.
            problemas.append(f"external_seller_id duplicado: {ext}")
            continue
        if not linha["ativo"]:
            # Conta ou loja inativa nao entra no conjunto esperado. Ver
            # REGISTRY_SQL: a desativacao e por UPDATE, nao por DELETE.
            continue
        contas[ext] = SellerAccount(
            external_seller_id=ext,
            loja_id=int(linha["loja_id"]),
            brand_key=str(linha["brand_key"]),
            ativo=True,
        )

    # loja -> marca precisa ser 1:1 dentro do conjunto ativo.
    por_loja: dict[int, set[str]] = {}
    for conta in contas.values():
        por_loja.setdefault(conta.loja_id, set()).add(conta.brand_key)
    for loja_id, marcas in sorted(por_loja.items()):
        if len(marcas) > 1:
            problemas.append(
                f"loja_id {loja_id} resolve para mais de uma marca: {sorted(marcas)}"
            )

    return contas, problemas


# ---------------------------------------------------------------------------
# Leitura da fonte
# ---------------------------------------------------------------------------
def fetch_backlog(conn) -> list[dict]:
    """Pedidos Shopee aguardando expedicao. Conexao deve ser read-only."""
    with conn.cursor() as cur:
        cur.execute(SHOPEE_BACKLOG_SQL, {"statuses": list(SHOPEE_BACKLOG_STATUSES)})
        return [dict(row) for row in cur.fetchall()]


def fetch_watermarks(conn) -> list[SourceWatermark]:
    """Watermark por conta, usado como metadado de frescor."""
    with conn.cursor() as cur:
        cur.execute(SHOPEE_WATERMARK_SQL)
        return [
            SourceWatermark(
                external_seller_id=str(row["shop_id"]),
                shop_account=str(row["shop_account"]),
                max_ingested_at=row["max_ingested_at"],
            )
            for row in cur.fetchall()
        ]


def fetch_baselines(conn, effective_at: datetime) -> dict[str, tuple[int, float | None]]:
    """p50 de horas ate o pickup por conta: `{external_seller_id: (n, p50)}`."""
    _require_aware(effective_at, "effective_at")
    with conn.cursor() as cur:
        cur.execute(
            SHOPEE_BASELINE_SQL,
            {
                "effective_at": effective_at,
                "excluded_statuses": list(BASELINE_EXCLUDED_STATUSES),
            },
        )
        return {
            str(row["shop_id"]): (
                int(row["sample_size"]),
                float(row["p50_hours"]) if row["p50_hours"] is not None else None,
            )
            for row in cur.fetchall()
        }


def extract(
    conn,
    effective_at: datetime,
    expected_accounts: frozenset[str],
    *,
    registry_problems: list[str] | None = None,
) -> ExtractionResult:
    """Extracao completa, com SAUDE e TAMANHO separados.

    `expected_accounts` vem do REGISTRY (Neon), nunca da propria fonte. Se
    viesse da fonte, uma conta que sumisse do Data Mart apareceria como "conta
    nao esperada" em vez de "conta faltando" — e o sumico viraria silencio.

    A comparacao e de IGUALDADE DE CONJUNTOS, nao de continencia:

      registry ambiguo                       -> REGISTRY_AMBIGUOUS
      registry vazio                         -> SOURCE_UNAVAILABLE
      esperada ausente na fonte              -> ACCOUNT_MISSING
      observada sem cadastro                 -> UNEXPECTED_ACCOUNT
      conta sem carimbo de ingestao          -> WATERMARK_MISSING
      conjuntos iguais e watermarks validos  -> HEALTHY

    Erro de consulta NAO e classificado aqui: a excecao do driver sobe e o
    orquestrador falha, preservando a fila anterior.
    """
    _require_aware(effective_at, "effective_at")

    if registry_problems:
        return ExtractionResult(
            source_health=SourceHealth.REGISTRY_AMBIGUOUS,
            expected_accounts=expected_accounts,
            observed_accounts=frozenset(),
            account_watermarks={},
            backlog_rows=[],
            detail="; ".join(registry_problems),
        )

    if not expected_accounts:
        return ExtractionResult(
            source_health=SourceHealth.SOURCE_UNAVAILABLE,
            expected_accounts=frozenset(),
            observed_accounts=frozenset(),
            account_watermarks={},
            backlog_rows=[],
            detail="registry sem contas ativas para o canal",
        )

    watermarks = fetch_watermarks(conn)
    observadas = frozenset(w.external_seller_id for w in watermarks)
    carimbos = {w.external_seller_id: w.max_ingested_at for w in watermarks}
    nomes = {w.external_seller_id: w.shop_account for w in watermarks}

    def _resultado(health: SourceHealth, detalhe: str) -> ExtractionResult:
        return ExtractionResult(
            source_health=health,
            expected_accounts=expected_accounts,
            observed_accounts=observadas,
            account_watermarks=carimbos,
            backlog_rows=[],
            detail=detalhe,
            account_shop_names=nomes,
        )

    faltando = expected_accounts - observadas
    if faltando:
        return _resultado(
            SourceHealth.ACCOUNT_MISSING,
            f"contas esperadas ausentes na fonte: {sorted(faltando)}",
        )

    inesperadas = observadas - expected_accounts
    if inesperadas:
        # Loja nova aberta na Shopee e ainda nao cadastrada. Ignora-la deixaria
        # o backlog dela invisivel; publicar marca por texto e proibido.
        return _resultado(
            SourceHealth.UNEXPECTED_ACCOUNT,
            f"contas observadas sem cadastro no registry: {sorted(inesperadas)}",
        )

    sem_carimbo = sorted(c for c in expected_accounts if carimbos.get(c) is None)
    if sem_carimbo:
        return _resultado(
            SourceHealth.WATERMARK_MISSING,
            f"contas sem carimbo de ingestao: {sem_carimbo}",
        )

    # So aqui o backlog e lido. Zero linhas a partir deste ponto e fotografia
    # vazia legitima, nao ausencia de fonte.
    return ExtractionResult(
        source_health=SourceHealth.HEALTHY,
        expected_accounts=expected_accounts,
        observed_accounts=observadas,
        account_watermarks=carimbos,
        backlog_rows=fetch_backlog(conn),
        account_shop_names=nomes,
    )


def assert_no_pii_in_sql(sql: str, forbidden: frozenset[str]) -> None:
    """Barreira estrutural: nenhum termo de comprador no SQL."""
    baixo = sql.lower()
    achados = sorted(t for t in forbidden if t in baixo)
    if achados:
        raise AssertionError(
            f"SQL de expedicao contem termo(s) de PII: {', '.join(achados)}"
        )


# Barreira aplicada no import: se alguem colar uma coluna de comprador num dos
# SELECTs, o modulo deixa de importar e os testes falham na coleta.
for _sql in (
    SHOPEE_BACKLOG_SQL,
    SHOPEE_WATERMARK_SQL,
    SHOPEE_BASELINE_SQL,
    REGISTRY_SQL,
):
    from pipelines.expedicao.contract import PII_FORBIDDEN_TOKENS as _PII

    assert_no_pii_in_sql(_sql, _PII)

assert set(SHOPEE_SELECT_COLUMNS) == set(SHOPEE_ALLOWED_SOURCE_COLUMNS), (
    "SHOPEE_SELECT_COLUMNS divergiu da lista fechada do contrato"
)
