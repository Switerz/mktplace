"""Gate EXP-TK-OPS-1 — cria marts.expedicao_tiktok_dispatch_daily.

Serie diaria de atraso de despacho do TikTok Shop, no grao
(data de pagamento, marca). E' a unica tabela da Expedicao que NAO e' uma
fotografia de fila: Shopee e Mercado Livre respondem "quem esta parado agora",
e esta responde "a coorte que pagou no dia D cumpriu o prazo".

POR QUE TABELA NOVA E NAO `expedicao_fila_atual`
-------------------------------------------------
O grao e' incompativel. `expedicao_fila_atual` e' (channel, shop_account,
marketplace_order_id) — uma linha por PEDIDO aguardando. Aqui o grao e'
(channel, paid_date, brand) — uma linha por COORTE. Espremer a serie diaria na
fila exigiria ou publicar pedidos ja entregues na "fila atual" (mentira), ou
perder a serie historica (que e' justamente o pedido do gate: "em quais dias o
atraso comecou a crescer"). Shopee e ML ficam byte a byte intocados.

DOIS EVENTOS, DUAS FAMILIAS DE COLUNAS
---------------------------------------
Medido em 2026-09-24 contra a fonte real; ver `pipelines/expedicao/tiktok_daily.py`
para a prova completa.

  despacho_*  -> etiqueta criada pelo vendedor (`line_items.rts_time`).
                 100,0% preenchido em AWAITING_COLLECTION/IN_TRANSIT/DELIVERED/
                 COMPLETED e 0,0% em AWAITING_SHIPMENT/ON_HOLD. E' a propria
                 transicao para AWAITING_COLLECTION (mediana de diferenca 0,00h).
  coleta_*    -> retirada pela transportadora (log de status -> IN_TRANSIT).
                 Mediana de 44,93h DEPOIS do despacho. Presente em 99,94%.

As duas familias NUNCA se somam: o mesmo pedido e' contado uma vez em cada.
Publicar so' uma responderia a pergunta errada — na janela medida o despacho
ficou em ~0,03% e a coleta chegou a 88,9% num unico dia. A operacao etiquetou
no prazo; quem nao coletou foi a transportadora.

O PRAZO NAO E' O DO TIKTOK
---------------------------
`deadline_date` e' RECONSTRUIDO da regra de 2 dias uteis descrita pela gestao.
A API `get_order_list` v202309 devolve SLA por pedido (`rts_sla_time` e afins),
mas a ingestao em `goca-se/airflow` nao mapeia nenhum desses campos — zero
ocorrencias em `ORDER_COLUMN_MAPPING`. Enquanto o campo oficial nao existir na
base, esta coluna e' um proxy fiel da regra, NAO a medicao da penalizacao, e
todo consumidor precisa rotular isso.

MATURACAO
---------
`is_mature` = o prazo da coorte ja venceu. Coorte imatura tem taxa que ainda
pode subir; exibi-la como numero fechado (sobretudo como `0%`) afirma o que
ninguem sabe. A coorte de hoje quase sempre esta em 0% e quase nunca termina
em 0%. O CHECK nao consegue impor isso (depende de `now()`), entao a regra
vive no publisher e na serving.

RETENCAO
--------
Sem DELETE por idade nesta migration. A tabela e' pequena por construcao
(coortes x marcas: ~7 marcas x 365 dias = ~2.500 linhas/ano) e a serie
historica e' o produto. A janela publicada e' reescrita inteira a cada
execucao porque uma coorte antiga MUDA quando um pedido dela e' finalmente
coletado.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

TABELA = "expedicao_tiktok_dispatch_daily"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS marts.{TABELA} (
            channel                      VARCHAR(20)  NOT NULL,
            paid_date                    DATE         NOT NULL,
            brand                        VARCHAR(64)  NOT NULL,
            shop_name                    VARCHAR(128),

            -- Reconstruido da regra de 2 dias uteis, NAO e' o SLA do TikTok.
            deadline_date                DATE         NOT NULL,
            is_mature                    BOOLEAN      NOT NULL,

            -- Denominador. Cancelados ficam FORA dele e sao contados a parte:
            -- pedido cancelado nao tem obrigacao de despacho, e mante-lo no
            -- denominador deixaria um pico de cancelamento mascarar atraso.
            pedidos_pagos                INTEGER      NOT NULL,
            cancelados                   INTEGER      NOT NULL,

            -- Evento 1: etiqueta (rts_time). Particao exclusiva e exaustiva.
            despacho_no_prazo            INTEGER      NOT NULL,
            despacho_atrasado            INTEGER      NOT NULL,
            despacho_pendente_vencido    INTEGER      NOT NULL,
            despacho_pendente_no_prazo   INTEGER      NOT NULL,

            -- Evento 2: coleta (IN_TRANSIT). Particao exclusiva e exaustiva.
            coleta_no_prazo              INTEGER      NOT NULL,
            coleta_atrasada              INTEGER      NOT NULL,
            coleta_pendente_vencida      INTEGER      NOT NULL,
            coleta_pendente_no_prazo     INTEGER      NOT NULL,

            refresh_batch_id             UUID         NOT NULL,
            effective_at                 TIMESTAMPTZ  NOT NULL,
            source_watermark_at          TIMESTAMPTZ,

            CONSTRAINT pk_{TABELA} PRIMARY KEY (channel, paid_date, brand),

            -- Canal fechado: esta tabela e' do TikTok. Shopee e ML tem outra
            -- forma e outro grao; aceitar o valor deles aqui so' permitiria
            -- publicar lixo silenciosamente.
            CONSTRAINT ck_{TABELA}_channel
                CHECK (channel = 'tiktokshop'),

            CONSTRAINT ck_{TABELA}_nao_negativo
                CHECK (pedidos_pagos >= 0 AND cancelados >= 0
                   AND despacho_no_prazo >= 0 AND despacho_atrasado >= 0
                   AND despacho_pendente_vencido >= 0 AND despacho_pendente_no_prazo >= 0
                   AND coleta_no_prazo >= 0 AND coleta_atrasada >= 0
                   AND coleta_pendente_vencida >= 0 AND coleta_pendente_no_prazo >= 0),

            -- O fechamento do grao e' imposto pelo BANCO, nao so' pelo
            -- publisher: e' o unico ponto por onde toda escrita passa.
            CONSTRAINT ck_{TABELA}_despacho_fecha
                CHECK (despacho_no_prazo + despacho_atrasado
                     + despacho_pendente_vencido + despacho_pendente_no_prazo
                       = pedidos_pagos),
            CONSTRAINT ck_{TABELA}_coleta_fecha
                CHECK (coleta_no_prazo + coleta_atrasada
                     + coleta_pendente_vencida + coleta_pendente_no_prazo
                       = pedidos_pagos),

            -- Prazo tem de ser POSTERIOR ao pagamento. Pega inversao de
            -- argumento e calendario de feriado corrompido antes de publicar.
            CONSTRAINT ck_{TABELA}_prazo_posterior
                CHECK (deadline_date > paid_date)
        )
        """
    )

    # A tela sempre le por janela de datas e frequentemente filtra por marca.
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{TABELA}_paid_date
            ON marts.{TABELA} (paid_date DESC)
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{TABELA}_brand_paid_date
            ON marts.{TABELA} (brand, paid_date DESC)
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS marts.{TABELA} CASCADE")
