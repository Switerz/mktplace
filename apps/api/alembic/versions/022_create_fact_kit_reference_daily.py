"""Gate KITS-PMA-3 — cria marts.fact_kit_reference_daily.

Materializa a referencia de kit DERIVADA DOS COMPONENTES, calculada pelo
contrato de `app.services.pma_kit_bom` (gate KITS-MAP-2) no pipeline, que e'
quem alcanca o Data Mart. O Render/API nunca consulta o Data Mart: ele le' esta
fato no Neon, como le' todas as outras.

POR QUE TABELA PROPRIA, E NAO COLUNAS EM `fact_channel_offer_observation`
--------------------------------------------------------------------------
Foram as duas opcoes consideradas. A fotografia de ofertas perde nas quatro:

1. **Produtor.** `fact_channel_offer_observation` tem UM produtor
   (`channel_offer_publisher`), que apaga e reescreve o escopo inteiro numa
   transacao. Colunas de kit exigiriam um SEGUNDO produtor escrevendo nas
   mesmas linhas — por UPDATE, que quebra a atomicidade do republish — ou
   ensinariam o sync de ofertas a ler BOM do Data Mart, acoplando as duas
   frentes.

2. **Dependencia.** A referencia derivada depende de DOIS insumos: a fotografia
   da oferta E o snapshot B2B. Quando o snapshot muda e a fotografia nao, a
   referencia muda. Numa coluna aditiva isso obrigaria a reescrever a
   fotografia inteira para corrigir preco que nao e' dela.

3. **Densidade.** Na fotografia de 2026-09-25 sao 10 linhas com ponte
   promovivel em 1.916 ofertas. Quinze colunas nulas em 1.906 linhas descrevem
   a ausencia, nao o dado.

4. **Reversao.** Derrubar esta tabela nao toca a fotografia de ofertas. Uma
   coluna aditiva exigiria `ALTER` de volta numa tabela servida.

NAO E' — E NAO PODE SER — `fact_suggested_price_reference_snapshot`
--------------------------------------------------------------------
Aquela tabela representa a planilha B2B ORIGINAL, tem contrato proprio
(revisao 014) e um `snapshot_id` que identifica o arquivo importado. Um kit
derivado nao veio de arquivo nenhum: inserir ali faria `distinct_b2b_products`
contar produto que a Trade nunca cadastrou, e o `reference_row_id` de um kit
apontaria para uma linha que nao existe na planilha. Esta fato REFERENCIA o
snapshot (`reference_snapshot_id`) em vez de se misturar a ele.

A CHAVE IMPEDE APLICAR A REFERENCIA DE UMA OFERTA EM OUTRA
------------------------------------------------------------
PK `(marketplace, observed_date, offer_key)` — a mesma identidade da fotografia
de ofertas, onde `offer_key` ja' e' unico por `(observed_date, marketplace)`.
`brand`, `shop_account` e `seller_sku` viajam como colunas e o serving os
CONFERE no join: uma linha so' e' aplicada a oferta cujos quatro campos batem.
E `reference_snapshot_id` e' conferido contra o snapshot que a API esta
servindo naquele instante — referencia calculada sobre snapshot antigo e'
RECUSADA, nao exibida com o rotulo errado.

SO' PONTE PROVADA ENTRA
-----------------------
`bridge_status` admite exatamente `EXACT_DIRECT` e `EXACT_ALIAS`, por CHECK.
`CANDIDATE_REVIEW`, `AMBIGUOUS`, `CROSS_BRAND_CONFLICT` e `UNMAPPED` nao tem
como ser gravados nem por engano de publisher. O CHECK `ck_fkrd_mesma_marca`
exige `brand = kit_brand`: a ponte de outra marca e' recusada pelo BANCO, nao
so' pelo codigo que a calculou.

`status = 'blocked'` EXISTE DE PROPOSITO
-----------------------------------------
Ponte valida cujo componente nao tem referencia B2B e' gravada com
`kit_reference_amount` NULO e `reason` preenchido. Sao 4 ofertas hoje, todas
paradas em `KS02006` e `KS03022`. Elas NAO sao candidatas — a ponte e' exata —
e o serving nunca as usa como preco; existem para que a pergunta "por que este
kit nao tem preco" tenha resposta no proprio banco, em vez de exigir rodar o
diagnostico de novo.

O CHECK e' BICONDICIONAL nos dois sentidos: `resolved` exige valor e proibe
motivo; `blocked` exige motivo e proibe valor. Nao ha terceiro estado.

NaN NAO PASSA POR `> 0`
------------------------
`'NaN'::numeric > 0` e' FALSE, mas `'NaN'::numeric <> 0` e' TRUE, e um CHECK
mal escrito deixaria NaN entrar em coluna anulavel. Todas as colunas numericas
tem `<> 'NaN'` EXPLICITO, como na 014.
"""
from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None

SCHEMA = "marts"
TABELA = "fact_kit_reference_daily"
QUALIFICADA = f"{SCHEMA}.{TABELA}"

#: Os dois unicos status de ponte que podem ser materializados.
BRIDGE_STATUSES = ("EXACT_DIRECT", "EXACT_ALIAS")

#: Os dois unicos desfechos de uma linha publicada.
ROW_STATUSES = ("resolved", "blocked")

#: Faixas de desconto do contrato de kit, por UNIDADES.
DISCOUNTS = ("0", "0.05", "0.10", "0.15")


def _lista(valores) -> str:
    return ", ".join(f"'{v}'" for v in valores)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.execute(f"""
        CREATE TABLE {QUALIFICADA} (
            -- identidade da oferta: a MESMA de fact_channel_offer_observation
            marketplace              TEXT        NOT NULL,
            observed_date            DATE        NOT NULL,
            offer_key                TEXT        NOT NULL,
            brand                    TEXT        NOT NULL,
            shop_account             TEXT,
            seller_sku               TEXT,

            -- ponte provada ate' o kit Protheus
            kit_protheus_sku         TEXT        NOT NULL,
            kit_brand                TEXT        NOT NULL,
            bridge_status            TEXT        NOT NULL,
            bridge_method            TEXT        NOT NULL,

            -- procedencia dos DOIS insumos
            reference_snapshot_id    VARCHAR(64) NOT NULL,
            reference_captured_at    TIMESTAMPTZ,
            offer_batch_id           TEXT,

            -- composicao
            component_count          INTEGER        NOT NULL,
            total_units              NUMERIC(12,4)  NOT NULL,
            components               JSONB          NOT NULL,

            -- calculo
            components_base_amount   NUMERIC(14,2),
            discount_pct             NUMERIC(5,4)   NOT NULL,
            kit_reference_amount     NUMERIC(14,2),

            -- desfecho
            status                   TEXT        NOT NULL,
            reason                   TEXT,

            source_run_id            TEXT        NOT NULL,
            published_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

            PRIMARY KEY (marketplace, observed_date, offer_key),

            CONSTRAINT ck_fkrd_bridge_status
                CHECK (bridge_status IN ({_lista(BRIDGE_STATUSES)})),

            -- A ponte de outra marca e' recusada pelo BANCO, nao so' pelo
            -- codigo que a calculou.
            CONSTRAINT ck_fkrd_mesma_marca
                CHECK (brand = kit_brand),

            CONSTRAINT ck_fkrd_status
                CHECK (status IN ({_lista(ROW_STATUSES)})),

            -- Bicondicional nos DOIS sentidos. Nao ha terceiro estado.
            CONSTRAINT ck_fkrd_resolvido
                CHECK (
                    (status = 'resolved'
                     AND kit_reference_amount IS NOT NULL
                     AND kit_reference_amount > 0
                     AND kit_reference_amount <> 'NaN'
                     AND components_base_amount IS NOT NULL
                     AND components_base_amount > 0
                     AND components_base_amount <> 'NaN'
                     AND reason IS NULL)
                 OR (status = 'blocked'
                     AND kit_reference_amount IS NULL
                     AND components_base_amount IS NULL
                     AND reason IS NOT NULL)
                ),

            CONSTRAINT ck_fkrd_componentes
                CHECK (component_count > 0
                       AND total_units > 0
                       AND total_units <> 'NaN'
                       AND jsonb_typeof(components) = 'array'
                       AND jsonb_array_length(components) = component_count),

            CONSTRAINT ck_fkrd_desconto
                CHECK (discount_pct IN ({_lista(DISCOUNTS)})),

            CONSTRAINT ck_fkrd_textos
                CHECK (marketplace <> '' AND offer_key <> '' AND brand <> ''
                       AND kit_protheus_sku <> '' AND kit_brand <> ''
                       AND bridge_method <> '' AND reference_snapshot_id <> ''
                       AND source_run_id <> '')
        )
    """)

    # Caminho do serving: uma fotografia de um canal, com o snapshot conferido.
    op.execute(
        f"CREATE INDEX idx_fkrd_serving ON {QUALIFICADA} "
        f"(marketplace, observed_date, reference_snapshot_id) "
        f"WHERE status = 'resolved'"
    )
    # Caminho da auditoria: "o que este kit produziu ao longo do tempo".
    op.execute(
        f"CREATE INDEX idx_fkrd_kit ON {QUALIFICADA} "
        f"(kit_protheus_sku, observed_date DESC)"
    )

    op.execute(
        f"COMMENT ON TABLE {QUALIFICADA} IS "
        f"'Referencia de kit DERIVADA dos componentes (gate KITS-PMA-3). "
        f"Calculada por app.services.pma_kit_bom no pipeline. NAO e'' "
        f"referencia B2B: a B2B original vive em "
        f"marts.fact_suggested_price_reference_snapshot e esta tabela apenas a "
        f"referencia por reference_snapshot_id.'"
    )
    op.execute(
        f"COMMENT ON COLUMN {QUALIFICADA}.reference_snapshot_id IS "
        f"'Snapshot B2B usado no calculo. O serving RECUSA a linha quando "
        f"difere do snapshot que esta servindo — referencia calculada sobre "
        f"planilha antiga nao e'' exibida com o rotulo de hoje.'"
    )
    op.execute(
        f"COMMENT ON COLUMN {QUALIFICADA}.total_units IS "
        f"'SUM(qty_per_kit). E'' este valor, e nunca o numero de SKUs "
        f"distintos, que escolhe a faixa de desconto.'"
    )
    op.execute(
        f"COMMENT ON COLUMN {QUALIFICADA}.status IS "
        f"'resolved = todos os componentes resolvidos, valor publicavel. "
        f"blocked = ponte exata e composicao completa, mas algum componente "
        f"sem referencia B2B inequivoca; reason diz qual.'"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {QUALIFICADA}")
