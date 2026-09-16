"""Gate EXP-1C — cria as duas tabelas de serving da Expedicao Shopee.

TRANSCRICAO DO CONTRATO JA VERSIONADO
--------------------------------------
Nada aqui e' novo: nomes fisicos, grao, chaves, colunas e dominios saem do
pacote `pipelines/expedicao/`, integrado em `main` pelo PR #2. Esta migration
apenas materializa o que aquele contrato ja declara.

    marts.expedicao_fila_atual   PK (channel, shop_account, marketplace_order_id)
    marts.expedicao_refresh_run  PK (channel, shop_account, snapshot_hour)

`marts.expedicao_alert_event` esta declarada no contrato como EVOLUCAO FUTURA e
NAO e' criada aqui.

A MARCA E' ATRIBUTO, NUNCA IDENTIDADE
--------------------------------------
A chave da fila espelha `pk_shopee_orders (shop_account, order_sn)`, que e'
UNIQUE na origem — medido em 30 dias: 29.369 linhas para 29.369 chaves e 29.369
`order_sn` distintos. `brand` fica FORA da PK de proposito: com a marca na
chave, corrigir a marca de uma conta no registry criaria uma linha nova e
deixaria a antiga orfa dentro da mesma fotografia. Como atributo, a correcao
atualiza a linha que ja' existe.

A mesma `order_sn` pode aparecer em contas diferentes sem colidir, porque
`shop_account` entra na chave.

POR QUE OS `CHECK` SAO BICONDICIONAIS
--------------------------------------
Os estados da fila nao sao independentes entre si — eles derivam uns dos outros
na transformacao. Um `CHECK` de uma perna so' (por exemplo "prazo nulo implica
status indisponivel") aceitaria a metade errada: status indisponivel com prazo
preenchido passaria. Como a transformacao garante as DUAS direcoes, o banco
tambem exige as duas, e qualquer divergencia entre codigo e dado vira erro de
escrita em vez de numero silenciosamente errado na tela.

`hours_open` e `hours_overdue` ganham guarda explicita contra `NaN`: em
PostgreSQL `'NaN'::numeric >= 0` e' VERDADEIRO, entao um teto numerico sozinho
nao barraria um NaN vindo de um float corrompido.

O RESUMO CODIFICA "stalled NAO E' SOMA"
----------------------------------------
`is_slow_vs_baseline` e `is_source_zombie` podem ser verdadeiros no MESMO
pedido. Logo `stalled_count` e' o tamanho da UNIAO, nunca a soma:

    GREATEST(slow, zombie) <= stalled <= slow + zombie

Esse intervalo e' exatamente o de uma uniao de dois conjuntos e vira `CHECK`.
Somar as duas contagens contaria a sobreposicao duas vezes.

As quatro categorias de prazo sao mutuamente exclusivas e somam
`backlog_count`. `over_48h_count`, `slow_count`, `zombie_count` e
`stalled_count` sao TRANSVERSAIS: entram no teto de `backlog_count`, nunca
naquela soma. Medicao de 15/09/2026 que sustenta isso: 933 aguardando =
101 + 149 + 588 + 95, com 307 acima de 48h — dos quais 206 ainda dentro do
prazo do marketplace.

FK DE MARCA CONTRA LINHA ORFA
------------------------------
`brand` referencia `marts.dim_loja(brand_key)` nas duas tabelas. A marca sempre
nasce do registry (`dim_seller_account` -> `dim_loja`), entao a FK apenas
transforma em invariante do banco o que o publisher ja' faz. Uma linha com marca
desconhecida passa a ser recusada na escrita.

ESTA MIGRATION NAO CADASTRA CONTA
----------------------------------
`marts.dim_seller_account` continua VAZIA (verificado por leitura read-only
antes de escrever este arquivo). Provisionar as contas e' um gate operacional
separado, e a Kokeshi fica de fora porque nao tem ingestao Shopee hoje. Nenhum
`INSERT` existe aqui.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None

SCHEMA = "marts"
FILA = "expedicao_fila_atual"
RESUMO = "expedicao_refresh_run"
FILA_Q = f"{SCHEMA}.{FILA}"
RESUMO_Q = f"{SCHEMA}.{RESUMO}"

#: Dominios do contrato versionado (`pipelines/expedicao/contract.py`). Ficam
#: como CHECK e nao como tipo ENUM do PostgreSQL: um tipo exigiria `ALTER TYPE`
#: a cada valor novo, e o dominio ainda vai crescer (o TikTok trara
#: `operational_2bd` quando houver calendario de dias uteis).
CHANNELS = ("shopee", "mercadolivre", "tiktokshop")
DEADLINE_SOURCES = ("marketplace_native", "operational_2bd", "unavailable")
DEADLINE_STATUSES = ("overdue", "due_within_24h", "on_time", "unavailable")
AGE_STATUSES = ("within_48h", "over_48h", "unknown")
FRESHNESS_STATUSES = ("fresh", "stale", "critical", "unknown")
TIMESTAMP_QUALITIES = ("verified", "assumed", "unknown")
RUN_STATUSES = ("success", "failed")

#: Contagens do resumo que nunca podem ser negativas.
CONTAGENS = (
    "backlog_count",
    "overdue_count",
    "due_within_24h_count",
    "on_time_count",
    "deadline_unavailable_count",
    "over_48h_count",
    "slow_count",
    "zombie_count",
    "stalled_count",
)

#: Contagens TRANSVERSAIS: limitadas por `backlog_count`, fora da soma das
#: quatro categorias de prazo.
TRANSVERSAIS = ("over_48h_count", "slow_count", "zombie_count", "stalled_count")


def _em_lista(coluna: str, valores) -> str:
    itens = ", ".join(f"'{v}'" for v in valores)
    return f"{coluna} IN ({itens})"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Estado atual da fila. Substituida por canal a cada refresh.
    # ------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE {FILA_Q} (
            channel                  VARCHAR(20)  NOT NULL,
            shop_account             VARCHAR(100) NOT NULL,
            marketplace_order_id     VARCHAR(64)  NOT NULL,
            brand                    VARCHAR(50)  NOT NULL,
            effective_at             TIMESTAMPTZ  NOT NULL,
            refresh_batch_id         VARCHAR(64)  NOT NULL,
            created_at               TIMESTAMPTZ,
            paid_at                  TIMESTAMPTZ,
            dispatch_deadline        TIMESTAMPTZ,
            deadline_source          VARCHAR(20)  NOT NULL,
            deadline_status          VARCHAR(20)  NOT NULL,
            operational_age_status   VARCHAR(20)  NOT NULL,
            is_slow_vs_baseline      BOOLEAN      NOT NULL,
            is_source_zombie         BOOLEAN      NOT NULL,
            is_stalled               BOOLEAN      NOT NULL,
            hours_open               NUMERIC(14,4),
            hours_overdue            NUMERIC(14,4),
            logistic_type            VARCHAR(30),
            carrier                  VARCHAR(120),
            source_ingested_at       TIMESTAMPTZ,
            source_freshness_status  VARCHAR(20)  NOT NULL,
            timestamp_quality        VARCHAR(20)  NOT NULL,
            CONSTRAINT pk_expedicao_fila_atual
                PRIMARY KEY (channel, shop_account, marketplace_order_id),
            CONSTRAINT fk_efa_brand
                FOREIGN KEY (brand) REFERENCES {SCHEMA}.dim_loja (brand_key)
        )
    """)

    # Dominios fechados.
    for coluna, valores in (
        ("channel", CHANNELS),
        ("deadline_source", DEADLINE_SOURCES),
        ("deadline_status", DEADLINE_STATUSES),
        ("operational_age_status", AGE_STATUSES),
        ("source_freshness_status", FRESHNESS_STATUSES),
        ("timestamp_quality", TIMESTAMP_QUALITIES),
    ):
        op.execute(
            f"ALTER TABLE {FILA_Q} ADD CONSTRAINT ck_efa_dominio_{coluna} "
            f"CHECK ({_em_lista(coluna, valores)})"
        )

    # Prazo ausente e status/origem "unavailable" sao a MESMA coisa, nos dois
    # sentidos. Sem a biconditional, `deadline_status='unavailable'` com prazo
    # preenchido passaria — e a tela mostraria "sem prazo" para um pedido que
    # tem prazo.
    op.execute(f"""
        ALTER TABLE {FILA_Q} ADD CONSTRAINT ck_efa_prazo_indisponivel CHECK (
            (dispatch_deadline IS NULL) = (deadline_status = 'unavailable')
        )
    """)
    op.execute(f"""
        ALTER TABLE {FILA_Q} ADD CONSTRAINT ck_efa_origem_do_prazo CHECK (
            (dispatch_deadline IS NULL) = (deadline_source = 'unavailable')
        )
    """)

    # Horas de estouro existem EXATAMENTE em `overdue`. `NULL` significa "nao
    # venceu" e `0` significaria "venceu agora" — sao coisas diferentes, e a
    # coluna precisa distinguir.
    op.execute(f"""
        ALTER TABLE {FILA_Q} ADD CONSTRAINT ck_efa_estouro_so_em_overdue CHECK (
            (deadline_status = 'overdue') = (hours_overdue IS NOT NULL)
        )
    """)

    # Idade desconhecida e ausencia de marco inicial sao a mesma coisa.
    op.execute(f"""
        ALTER TABLE {FILA_Q} ADD CONSTRAINT ck_efa_idade_desconhecida CHECK (
            (hours_open IS NULL) = (operational_age_status = 'unknown')
        )
    """)

    # Freshness desconhecido e ausencia de carimbo sao a mesma coisa.
    op.execute(f"""
        ALTER TABLE {FILA_Q} ADD CONSTRAINT ck_efa_frescor_desconhecido CHECK (
            (source_ingested_at IS NULL) = (source_freshness_status = 'unknown')
        )
    """)

    # `is_stalled` e' derivado: o OR das duas anomalias, nunca outro valor.
    op.execute(f"""
        ALTER TABLE {FILA_Q} ADD CONSTRAINT ck_efa_stalled_derivado CHECK (
            is_stalled = (is_slow_vs_baseline OR is_source_zombie)
        )
    """)

    # Duracao tem de ser FINITA. `NUMERIC` aceita tres valores que nao sao
    # numero: `NaN`, `Infinity` e `-Infinity` — os tres invalidos para "horas em
    # aberto" e "horas de estouro".
    #
    # Medido em PostgreSQL 16.14 antes de escrever este CHECK:
    #
    #     'NaN'::numeric >= 0        -> TRUE   (teto nao barra)
    #     'Infinity'::numeric >= 0   -> TRUE   (teto nao barra)
    #     '-Infinity'::numeric >= 0  -> FALSE
    #     'Infinity'  <> 'NaN'       -> TRUE   (guarda so' de NaN nao barra)
    #     '-Infinity' <> 'NaN'       -> TRUE   (idem)
    #
    # Ou seja: nem um teto numerico nem uma guarda so' de NaN bastam. Os tres
    # sao recusados EXPLICITAMENTE. Uma expressao de faixa
    # (`> '-Infinity' AND < 'Infinity'`) funcionaria por acidente — em NUMERIC,
    # `NaN > '-Infinity'` e' TRUE e `NaN < 'Infinity'` e' FALSE — mas depende de
    # uma ordenacao contraintuitiva e seria fragil de ler. A enumeracao diz o
    # que quer dizer.
    #
    # O valor invalido e' RECUSADO, nunca convertido para NULL ou zero: `NULL`
    # em `hours_open` significa "sem marco inicial" e zero significaria "aberto
    # agora". Silenciar um valor corrompido como um desses dois inventaria um
    # fato que a fonte nao sustenta.
    for coluna in ("hours_open", "hours_overdue"):
        op.execute(
            f"ALTER TABLE {FILA_Q} ADD CONSTRAINT ck_efa_{coluna}_finito CHECK ("
            f"{coluna} IS NULL OR ("
            f"{coluna} <> 'NaN'::numeric"
            f" AND {coluna} <> 'Infinity'::numeric"
            f" AND {coluna} <> '-Infinity'::numeric))"
        )

    # Fatos da FONTE Shopee, nao convencao interna: todas as colunas de data de
    # `raw.shopee_orders` sao `timestamp with time zone` (dai `verified`), e a
    # Shopee nao expoe modalidade logistica (dai `logistic_type` nulo). A coluna
    # existe para o ML, que distingue `cross_docking` de `fulfillment`.
    op.execute(f"""
        ALTER TABLE {FILA_Q} ADD CONSTRAINT ck_efa_shopee_timestamp CHECK (
            channel <> 'shopee' OR timestamp_quality = 'verified'
        )
    """)
    op.execute(f"""
        ALTER TABLE {FILA_Q} ADD CONSTRAINT ck_efa_shopee_sem_logistica CHECK (
            channel <> 'shopee' OR logistic_type IS NULL
        )
    """)

    # ------------------------------------------------------------------
    # 2. Resumo horario POR CONTA. Existe porque uma tabela de linhas nao
    #    representa "zero pedidos": com backlog vazio ela simplesmente nao tem
    #    linha, e "nao publicou" ficaria indistinguivel de "publicou uma
    #    fotografia vazia".
    # ------------------------------------------------------------------
    op.execute(f"""
        CREATE TABLE {RESUMO_Q} (
            channel                     VARCHAR(20)  NOT NULL,
            shop_account                VARCHAR(100) NOT NULL,
            snapshot_hour               TIMESTAMPTZ  NOT NULL,
            brand                       VARCHAR(50)  NOT NULL,
            refresh_batch_id            VARCHAR(64)  NOT NULL,
            observed_at                 TIMESTAMPTZ  NOT NULL,
            source_watermark_at         TIMESTAMPTZ,
            source_advanced             BOOLEAN      NOT NULL,
            backlog_count               INTEGER      NOT NULL,
            overdue_count               INTEGER      NOT NULL,
            due_within_24h_count        INTEGER      NOT NULL,
            on_time_count               INTEGER      NOT NULL,
            deadline_unavailable_count  INTEGER      NOT NULL,
            over_48h_count              INTEGER      NOT NULL,
            slow_count                  INTEGER      NOT NULL,
            zombie_count                INTEGER      NOT NULL,
            stalled_count               INTEGER      NOT NULL,
            run_status                  VARCHAR(20)  NOT NULL,
            ingested_at                 TIMESTAMPTZ  NOT NULL,
            CONSTRAINT pk_expedicao_refresh_run
                PRIMARY KEY (channel, shop_account, snapshot_hour),
            CONSTRAINT fk_err_brand
                FOREIGN KEY (brand) REFERENCES {SCHEMA}.dim_loja (brand_key)
        )
    """)

    for coluna, valores in (("channel", CHANNELS), ("run_status", RUN_STATUSES)):
        op.execute(
            f"ALTER TABLE {RESUMO_Q} ADD CONSTRAINT ck_err_dominio_{coluna} "
            f"CHECK ({_em_lista(coluna, valores)})"
        )

    for coluna in CONTAGENS:
        op.execute(
            f"ALTER TABLE {RESUMO_Q} ADD CONSTRAINT ck_err_{coluna}_nao_negativa "
            f"CHECK ({coluna} >= 0)"
        )

    # As quatro categorias de prazo sao mutuamente exclusivas e cobrem todo o
    # backlog. Esta e' a reconciliacao que a tela promete.
    op.execute(f"""
        ALTER TABLE {RESUMO_Q} ADD CONSTRAINT ck_err_categorias_somam_backlog CHECK (
            overdue_count + due_within_24h_count
          + on_time_count + deadline_unavailable_count = backlog_count
        )
    """)

    # Transversais: cabem no backlog, mas NAO entram naquela soma.
    for coluna in TRANSVERSAIS:
        op.execute(
            f"ALTER TABLE {RESUMO_Q} ADD CONSTRAINT ck_err_{coluna}_cabe_no_backlog "
            f"CHECK ({coluna} <= backlog_count)"
        )

    # `stalled` e' a UNIAO de slow e zombie, que podem coincidir no mesmo
    # pedido. O intervalo abaixo e' exatamente o de uma uniao de dois conjuntos
    # e impede tanto somar (contaria a sobreposicao duas vezes) quanto perder
    # linha.
    op.execute(f"""
        ALTER TABLE {RESUMO_Q} ADD CONSTRAINT ck_err_stalled_e_uniao CHECK (
            stalled_count >= GREATEST(slow_count, zombie_count)
        AND stalled_count <= slow_count + zombie_count
        )
    """)

    # `snapshot_hour` truncado na hora, em UTC. Sem o fuso explicito,
    # `date_trunc` usaria o TimeZone da sessao e a mesma execucao cairia em
    # horas diferentes conforme o worker.
    op.execute(f"""
        ALTER TABLE {RESUMO_Q} ADD CONSTRAINT ck_err_hora_truncada_em_utc CHECK (
            snapshot_hour
            = date_trunc('hour', snapshot_hour AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
        )
    """)

    # A observacao pertence a' hora que ela carimba.
    op.execute(f"""
        ALTER TABLE {RESUMO_Q} ADD CONSTRAINT ck_err_observacao_dentro_da_hora CHECK (
            observed_at >= snapshot_hour
        AND observed_at <  snapshot_hour + INTERVAL '1 hour'
        )
    """)

    # ------------------------------------------------------------------
    # 3. Indices. Um para cada acesso que o contrato REALMENTE documenta;
    #    nenhum para consulta hipotetica. A PK da fila lidera por `channel`,
    #    entao o `DELETE ... WHERE channel = %s` da substituicao ja' usa o
    #    prefixo dela e nao precisa de indice proprio.
    # ------------------------------------------------------------------
    # Tela operacional: fila de acao por severidade, filtrando marca.
    op.execute(
        f"CREATE INDEX idx_efa_acao ON {FILA_Q} "
        f"(channel, deadline_status, brand)"
    )
    # Agrupamento de parados — parcial: a maioria das linhas nao esta stalled.
    op.execute(
        f"CREATE INDEX idx_efa_stalled ON {FILA_Q} "
        f"(channel, brand) WHERE is_stalled"
    )
    # Cartao transversal de "acima de 48h" — parcial pelo mesmo motivo.
    op.execute(
        f"CREATE INDEX idx_efa_over_48h ON {FILA_Q} "
        f"(channel, brand) WHERE operational_age_status = 'over_48h'"
    )
    # Tendencia: evolucao horaria do backlog por marca. A PK lidera por
    # `shop_account`, que a tela de tendencia nao filtra.
    op.execute(
        f"CREATE INDEX idx_err_tendencia ON {RESUMO_Q} "
        f"(channel, brand, snapshot_hour DESC)"
    )


def downgrade() -> None:
    # Remove SOMENTE o que o upgrade criou, na ordem inversa. Nenhuma tabela de
    # outra frente e' tocada: a 016 do Full e a 017 do PMA permanecem intactas,
    # e `marts.dim_loja` (referenciada pelas FKs) nao pertence a esta migration.
    for indice in ("idx_err_tendencia", "idx_efa_over_48h",
                   "idx_efa_stalled", "idx_efa_acao"):
        op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.{indice}")
    op.execute(f"DROP TABLE IF EXISTS {RESUMO_Q}")
    op.execute(f"DROP TABLE IF EXISTS {FILA_Q}")
