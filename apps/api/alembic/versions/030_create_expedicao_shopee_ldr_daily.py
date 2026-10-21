"""EXPEDICAO-SHOPEE-LDR-1 — LDR operacional da Shopee (coleta x prazo Shopee).

Cria duas tabelas, publicadas por `pipelines.expedicao.shopee_ldr_cli`:

  marts.expedicao_shopee_ldr_daily           (channel, deadline_date, shop_account)
  marts.expedicao_shopee_ldr_sem_prazo_daily (channel, paid_date, shop_account)
  marts.expedicao_shopee_fila_fluxo          (channel, shop_account, fluxo)

NAO E' A METRICA DE PENALIZACAO DA SHOPEE
-----------------------------------------
E' um indicador interno: entre os pedidos pagos e despachados pelo proprio
vendedor que VENCEM num dia (`ship_by_date` em Brasilia), quantos nao tinham
sido coletados (`pickup_done_time`) ate' o prazo. As regras, a medicao e o
vies estao em `pipelines/expedicao/shopee_ldr.py` e em
`docs/expedicao_contrato.md`.

POR QUE DUAS TABELAS
--------------------
O grao da taxa e' o DIA DE VENCIMENTO. Pedido sem `ship_by_date` nao tem dia de
vencimento, entao nao cabe nesse grao sem inventar um. Ele vai para a segunda
tabela, agrupado pelo dia de PAGAMENTO, e entra no calculo de cobertura.

O QUE O BANCO GARANTE (e nao so' o publisher)
----------------------------------------------
  * a particao fecha: no_prazo + atrasado + pendente_vencido
    + pendente_no_prazo = base;
  * a base e' exatamente pagos - FBS - cancelados sem coleta - enviados sem
    coleta (pega exclusao aplicada duas vezes e exclusao esquecida);
  * os subconjuntos de cancelamento nao excedem o total;
  * vencimento maduro nao tem pendente no prazo;
  * canal fechado em 'shopee'.

SEM RETENCAO
------------
~4 contas x 365 dias = ~1.500 linhas/ano. A janela de 35 dias para tras e 7
para a frente e' reescrita inteira a cada publicacao; fora dela, historico.

Esta migration NAO e' aplicada pelo gate que a cria.
"""

from alembic import op

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None

TABELA = "expedicao_shopee_ldr_daily"
TABELA_SP = "expedicao_shopee_ldr_sem_prazo_daily"
TABELA_FILA = "expedicao_shopee_fila_fluxo"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS marts.{TABELA} (
            channel                        VARCHAR(20)  NOT NULL,
            deadline_date                  DATE         NOT NULL,
            shop_account                   VARCHAR(64)  NOT NULL,
            shop_id                        VARCHAR(32)  NOT NULL,
            brand                          VARCHAR(64)  NOT NULL,

            -- Populacao: pagos com vencimento neste dia, qualquer fluxo.
            pedidos_pagos                  INTEGER      NOT NULL,
            -- Fora da taxa, cada um com contador proprio.
            excl_fbs                       INTEGER      NOT NULL,
            excl_cancelado_sem_coleta      INTEGER      NOT NULL,
            -- Subconjuntos informativos de excl_cancelado_sem_coleta.
            cancelado_vendedor_nao_enviou  INTEGER      NOT NULL,
            cancelado_motivo_desconhecido  INTEGER      NOT NULL,
            excl_enviado_sem_coleta        INTEGER      NOT NULL,

            -- A taxa: atraso = atrasado + pendente_vencido; LDR = atraso/base.
            base                           INTEGER      NOT NULL,
            no_prazo                       INTEGER      NOT NULL,
            atrasado                       INTEGER      NOT NULL,
            pendente_vencido               INTEGER      NOT NULL,
            pendente_no_prazo              INTEGER      NOT NULL,
            is_mature                      BOOLEAN      NOT NULL,

            refresh_batch_id               UUID         NOT NULL,
            effective_at                   TIMESTAMPTZ  NOT NULL,
            -- Ultima leitura da fonte DESTA conta. Nulo = conta sem leitura.
            source_watermark_at            TIMESTAMPTZ,

            CONSTRAINT pk_{TABELA} PRIMARY KEY (channel, deadline_date, shop_account),
            CONSTRAINT ck_{TABELA}_channel CHECK (channel = 'shopee'),
            CONSTRAINT ck_{TABELA}_nao_negativo CHECK (
                pedidos_pagos >= 0 AND excl_fbs >= 0
                AND excl_cancelado_sem_coleta >= 0
                AND cancelado_vendedor_nao_enviou >= 0
                AND cancelado_motivo_desconhecido >= 0
                AND excl_enviado_sem_coleta >= 0 AND base >= 0
                AND no_prazo >= 0 AND atrasado >= 0
                AND pendente_vencido >= 0 AND pendente_no_prazo >= 0),
            CONSTRAINT ck_{TABELA}_particao_fecha CHECK (
                no_prazo + atrasado + pendente_vencido + pendente_no_prazo = base),
            CONSTRAINT ck_{TABELA}_base_bate CHECK (
                base = pedidos_pagos - excl_fbs - excl_cancelado_sem_coleta
                       - excl_enviado_sem_coleta),
            CONSTRAINT ck_{TABELA}_subconjuntos_cancelamento CHECK (
                cancelado_vendedor_nao_enviou + cancelado_motivo_desconhecido
                  <= excl_cancelado_sem_coleta),
            CONSTRAINT ck_{TABELA}_madura_sem_pendente_no_prazo CHECK (
                NOT is_mature OR pendente_no_prazo = 0)
        )
        """
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABELA}_brand_deadline "
        f"ON marts.{TABELA} (brand, deadline_date DESC)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS marts.{TABELA_SP} (
            channel                    VARCHAR(20)  NOT NULL,
            paid_date                  DATE         NOT NULL,
            shop_account               VARCHAR(64)  NOT NULL,
            shop_id                    VARCHAR(32)  NOT NULL,
            brand                      VARCHAR(64)  NOT NULL,
            pedidos_pagos              INTEGER      NOT NULL,
            excl_fbs                   INTEGER      NOT NULL,
            excl_cancelado_sem_coleta  INTEGER      NOT NULL,
            -- Pedidos do vendedor, pagos, nao cancelados, SEM ship_by_date:
            -- reduzem a cobertura da taxa.
            sem_prazo                  INTEGER      NOT NULL,
            refresh_batch_id           UUID         NOT NULL,
            effective_at               TIMESTAMPTZ  NOT NULL,
            source_watermark_at        TIMESTAMPTZ,

            CONSTRAINT pk_{TABELA_SP} PRIMARY KEY (channel, paid_date, shop_account),
            CONSTRAINT ck_{TABELA_SP}_channel CHECK (channel = 'shopee'),
            CONSTRAINT ck_{TABELA_SP}_nao_negativo CHECK (
                pedidos_pagos >= 0 AND excl_fbs >= 0
                AND excl_cancelado_sem_coleta >= 0 AND sem_prazo >= 0),
            CONSTRAINT ck_{TABELA_SP}_fecha CHECK (
                pedidos_pagos = excl_fbs + excl_cancelado_sem_coleta + sem_prazo)
        )
        """
    )
    # Fila aberta por FLUXO (vendedor x FBS): fotografia do estado agora, com o
    # mesmo predicado e as mesmas regras da fila publicada. Existe para que o
    # "risco atual" da Shopee nao some FBS (despachado pela Shopee) ao atraso do
    # vendedor. Uma linha por conta e fluxo, inclusive com zero.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS marts.{TABELA_FILA} (
            channel                     VARCHAR(20)  NOT NULL,
            shop_account                VARCHAR(64)  NOT NULL,
            shop_id                     VARCHAR(32)  NOT NULL,
            brand                       VARCHAR(64)  NOT NULL,
            fluxo                       VARCHAR(16)  NOT NULL,
            backlog_count               INTEGER      NOT NULL,
            overdue_count               INTEGER      NOT NULL,
            due_within_24h_count        INTEGER      NOT NULL,
            on_time_count               INTEGER      NOT NULL,
            deadline_unavailable_count  INTEGER      NOT NULL,
            over_48h_count              INTEGER      NOT NULL,
            slow_count                  INTEGER      NOT NULL,
            zombie_count                INTEGER      NOT NULL,
            stalled_count               INTEGER      NOT NULL,
            refresh_batch_id            UUID         NOT NULL,
            effective_at                TIMESTAMPTZ  NOT NULL,
            source_watermark_at         TIMESTAMPTZ,

            CONSTRAINT pk_{TABELA_FILA} PRIMARY KEY (channel, shop_account, fluxo),
            CONSTRAINT ck_{TABELA_FILA}_channel CHECK (channel = 'shopee'),
            CONSTRAINT ck_{TABELA_FILA}_fluxo CHECK (fluxo IN ('vendedor', 'fbs')),
            CONSTRAINT ck_{TABELA_FILA}_nao_negativo CHECK (
                backlog_count >= 0 AND overdue_count >= 0
                AND due_within_24h_count >= 0 AND on_time_count >= 0
                AND deadline_unavailable_count >= 0 AND over_48h_count >= 0
                AND slow_count >= 0 AND zombie_count >= 0 AND stalled_count >= 0),
            -- Mesma particao da fila: as quatro situacoes de prazo fecham o backlog.
            CONSTRAINT ck_{TABELA_FILA}_particao_fecha CHECK (
                overdue_count + due_within_24h_count + on_time_count
                + deadline_unavailable_count = backlog_count),
            -- Dimensoes transversais nunca passam do backlog; travado e' o OR.
            CONSTRAINT ck_{TABELA_FILA}_transversais CHECK (
                over_48h_count <= backlog_count AND stalled_count <= backlog_count
                AND stalled_count <= slow_count + zombie_count
                AND stalled_count >= GREATEST(slow_count, zombie_count))
        )
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS marts.{TABELA_FILA}")
    op.execute(f"DROP TABLE IF EXISTS marts.{TABELA_SP}")
    op.execute(f"DROP TABLE IF EXISTS marts.{TABELA}")
