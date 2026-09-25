"""Gate EXP-TK-OPS-1/2 — cria marts.expedicao_tiktok_dispatch_daily.

Base da LDR (Late Dispatch Rate) do TikTok Shop, no grao (data de pagamento,
marca). E' a unica tabela da Expedicao que NAO e' uma fotografia de fila:
Shopee e Mercado Livre respondem "quem esta parado agora", e esta responde
"a coorte que pagou no dia D cumpriu o prazo".

POR QUE TABELA NOVA E NAO `expedicao_fila_atual`
-------------------------------------------------
O grao e' incompativel. `expedicao_fila_atual` e' (channel, shop_account,
marketplace_order_id) — uma linha por PEDIDO aguardando. Aqui e' (channel,
paid_date, brand) — uma linha por COORTE. Espremer a serie na fila exigiria ou
publicar pedidos ja entregues na "fila atual" (mentira), ou perder a serie
historica, que e' justamente o pedido: "em quais dias o atraso comecou".
Shopee e ML ficam byte a byte intocados.

DOIS EVENTOS, PRAZOS DIFERENTES, BASES DIFERENTES
--------------------------------------------------
  despacho_* (RTS, etiqueta pronta)     -> vence em 1 dia util
  coleta_*   (TTS, transportadora)      -> vence em 2 dias uteis

Cada evento carrega o proprio vencimento, a propria base e a propria particao
de quatro contagens. As duas familias NUNCA se somam: o mesmo pedido aparece
uma vez em cada. Aplicar 2 dias uteis a etiqueta subestimava o atraso da
operacao — medido, a taxa de RTS passa de 0,06% para 0,61% com o prazo certo.

Os vencimentos diferem, entao as EXCLUSOES tambem diferem: um cancelamento
pode ser anterior ao prazo de coleta e posterior ao de despacho. Por isso
existem `despacho_base` e `coleta_base` separadas, e nao uma base unica.

DENOMINADOR
-----------
  base = pedidos_pagos_brutos - amostras_excluidas - cancelado_antes_sla

  - amostra gratis (`is_sample_order`) sai sempre: nao e' venda;
  - cancelado ANTES do vencimento sai: nao havia mais obrigacao de enviar;
  - cancelado DEPOIS do vencimento permanece: a obrigacao existia e nao foi
    cumprida;
  - cancelado SEM carimbo permanece. Medido: 21,6% dos cancelados nao tem
    linha no log de status, e exclui-los todos apagaria atraso real que
    ninguem poderia auditar. O impacto da escolha foi medido em 0,86 pp.

A leitura OFICIAL (LDR) agrega por `coleta_deadline`, nao por `paid_date`: o
denominador sao os pedidos cujo envio VENCE no periodo. A agregacao por
`paid_date` continua disponivel como "fluxo", que e' a visao que a gestao usa,
mas nunca e' apresentada como se fosse a LDR.

O PRAZO NAO E' O DO TIKTOK
---------------------------
A API `get_order_list` v202309 devolve SLA por pedido (`rts_sla_time` e
afins), mas a ingestao em `goca-se/airflow` nao mapeia nenhum desses campos —
zero ocorrencias em `ORDER_COLUMN_MAPPING`. As colunas de deadline aqui sao
RECONSTRUIDAS da politica de dias uteis.

A reconstrucao foi reconciliada contra a planilha da gestao (marca `barbours`,
01-23/09), a partir de um artefato VERSIONADO —
`docs/reconciliation/tiktok_ldr_planilha_gestor_2026-09-23.csv` — e nao de uma
consulta ad-hoc. Refazer com:

    python -m pipelines.reconciliation.tiktok_ldr_planilha

    regra                erro acumulado   erro medio   dias dentro de 1 pp
    uteis_com_feriado           7,1 pp       0,31 pp          23/23  <- vigente
    uteis_sem_feriado         137,7 pp       5,99 pp          19/23
    corridos                  573,8 pp      24,95 pp          14/23

Pedidos pagos batem em 21 dos 23 dias (total +1,68%). A planilha arredonda a
taxa para inteiro, entao 1 pp e' a melhor resolucao possivel da comparacao.

MATURACAO
---------
`*_is_mature` = o prazo daquele evento ja venceu. Coorte imatura tem taxa que
ainda pode subir; exibi-la como numero fechado (sobretudo como `0%`) afirma o
que ninguem sabe. O CHECK nao consegue impor isso (depende de `now()`), entao
a regra vive no publisher e na serving.

RETENCAO
--------
Sem DELETE por idade. A tabela e' pequena por construcao (~7 marcas x 365 dias
= ~2.500 linhas/ano) e a serie historica e' o produto. A janela publicada e'
reescrita inteira a cada execucao porque uma coorte antiga MUDA quando um
pedido dela e' finalmente coletado.
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
            channel                        VARCHAR(20)  NOT NULL,
            paid_date                      DATE         NOT NULL,
            brand                          VARCHAR(64)  NOT NULL,
            shop_name                      VARCHAR(128),

            -- Populacao bruta e o que sai dela.
            pedidos_pagos_brutos           INTEGER      NOT NULL,
            amostras_excluidas             INTEGER      NOT NULL,

            -- Evento 1: RTS (etiqueta), 1 dia util. Reconstruido, NAO e' o SLA
            -- do TikTok.
            despacho_deadline              DATE         NOT NULL,
            despacho_base                  INTEGER      NOT NULL,
            despacho_cancelado_antes_sla   INTEGER      NOT NULL,
            despacho_no_prazo              INTEGER      NOT NULL,
            despacho_atrasado              INTEGER      NOT NULL,
            despacho_pendente_vencido      INTEGER      NOT NULL,
            despacho_pendente_no_prazo     INTEGER      NOT NULL,
            despacho_is_mature             BOOLEAN      NOT NULL,

            -- Evento 2: TTS (coleta), 2 dias uteis.
            coleta_deadline                DATE         NOT NULL,
            coleta_base                    INTEGER      NOT NULL,
            coleta_cancelado_antes_sla     INTEGER      NOT NULL,
            coleta_no_prazo                INTEGER      NOT NULL,
            coleta_atrasada                INTEGER      NOT NULL,
            coleta_pendente_vencida        INTEGER      NOT NULL,
            coleta_pendente_no_prazo       INTEGER      NOT NULL,
            coleta_is_mature               BOOLEAN      NOT NULL,

            refresh_batch_id               UUID         NOT NULL,
            effective_at                   TIMESTAMPTZ  NOT NULL,
            source_watermark_at            TIMESTAMPTZ,

            CONSTRAINT pk_{TABELA} PRIMARY KEY (channel, paid_date, brand),

            -- Canal fechado: esta tabela e' do TikTok. Shopee e ML tem outra
            -- forma e outro grao; aceitar o valor deles aqui so' permitiria
            -- publicar lixo silenciosamente.
            CONSTRAINT ck_{TABELA}_channel
                CHECK (channel = 'tiktokshop'),

            CONSTRAINT ck_{TABELA}_nao_negativo
                CHECK (pedidos_pagos_brutos >= 0 AND amostras_excluidas >= 0
                   AND despacho_base >= 0 AND despacho_cancelado_antes_sla >= 0
                   AND despacho_no_prazo >= 0 AND despacho_atrasado >= 0
                   AND despacho_pendente_vencido >= 0
                   AND despacho_pendente_no_prazo >= 0
                   AND coleta_base >= 0 AND coleta_cancelado_antes_sla >= 0
                   AND coleta_no_prazo >= 0 AND coleta_atrasada >= 0
                   AND coleta_pendente_vencida >= 0
                   AND coleta_pendente_no_prazo >= 0),

            -- O fechamento do grao e' imposto pelo BANCO, nao so' pelo
            -- publisher: e' o unico ponto por onde toda escrita passa.
            CONSTRAINT ck_{TABELA}_despacho_fecha
                CHECK (despacho_no_prazo + despacho_atrasado
                     + despacho_pendente_vencido + despacho_pendente_no_prazo
                       = despacho_base),
            CONSTRAINT ck_{TABELA}_coleta_fecha
                CHECK (coleta_no_prazo + coleta_atrasada
                     + coleta_pendente_vencida + coleta_pendente_no_prazo
                       = coleta_base),

            -- A base de cada evento tem de ser exatamente a populacao bruta
            -- menos as exclusoes DAQUELE evento. Pega exclusao aplicada duas
            -- vezes e exclusao esquecida.
            CONSTRAINT ck_{TABELA}_despacho_base_bate
                CHECK (despacho_base
                       = pedidos_pagos_brutos - amostras_excluidas
                         - despacho_cancelado_antes_sla),
            CONSTRAINT ck_{TABELA}_coleta_base_bate
                CHECK (coleta_base
                       = pedidos_pagos_brutos - amostras_excluidas
                         - coleta_cancelado_antes_sla),

            -- Prazo tem de ser POSTERIOR ao pagamento; pega inversao de
            -- argumento e calendario de feriado corrompido antes de publicar.
            CONSTRAINT ck_{TABELA}_prazo_posterior
                CHECK (despacho_deadline > paid_date
                   AND coleta_deadline > paid_date),

            -- 1 dia util nunca vence depois de 2 dias uteis. Se isto quebrar,
            -- os prazos foram trocados entre os eventos.
            CONSTRAINT ck_{TABELA}_ordem_dos_prazos
                CHECK (despacho_deadline <= coleta_deadline)
        )
        """
    )

    # A leitura OFICIAL agrega por VENCIMENTO de coleta; a da gestao, por data
    # de pagamento. As duas precisam de indice, senao a tela paga varredura.
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{TABELA}_coleta_deadline
            ON marts.{TABELA} (coleta_deadline DESC)
        """
    )
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
