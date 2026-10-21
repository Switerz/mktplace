"""ML-FULL-STOCK-PRODUCTIZATION-1 — fatos do estoque Full do Mercado Livre.

NAO APLICADA. Aplicar e' gate proprio (`alembic upgrade 029` no Neon).
Reversivel por `alembic downgrade 027`, que derruba so' estas tres tabelas.
A migration nao insere dados.

CADEIA
------
`down_revision = "027"` (head de `main` em 20/10/2026). O PR #78 tambem parte
de 027 (revisao 028). Quem for mesclado DEPOIS ajusta o `down_revision` para
a head vigente antes do merge -- nunca dois heads em `main`.

GRAO E REGRAS (medidas em 16/10/2026)
-------------------------------------
- Grao fisico: (ref_date, brand, seller_id, inventory_id). Estoque NUNCA e'
  somado por anuncio: 74,5% das unidades Full vendidas sairam de inventarios
  compartilhados por mais de um anuncio.
- Quantidades vem do endpoint de INVENTARIO do ML (a fotografia do airflow em
  `raw.ml_full_inventory_*`). Produto/UP e' so' auditoria e nao entra aqui.
- `inventory_id` nao prova Full: `eh_full` vem de haver anuncio
  `logistic_type = fulfillment` ligado ao inventario. Inventario sem nenhum
  vira `FORA_DO_FULL` (contexto, fora da operacao).
- Ausencia nunca e' zero: leitura sem OK -> `SEM_LEITURA` com quantidades
  NULAS (CHECK). Cobertura nula sem demanda (CHECK).
- `transfer` e' movimento INTERNO do ML. Nao e' remessa do operador ao Full.
- Venda sem vinculo unico com um inventario NAO e' rateada: aparece no
  agregado por marca (`fact_ml_full_stock_brand_daily`).
- Limites de cobertura (7 / 90 dias) sao PROVISORIOS.

NaN: `'NaN'::numeric >= 0` e' TRUE no PostgreSQL; as faixas tem `<> 'NaN'`.
"""
from alembic import op

revision = "029"
down_revision = "027"
branch_labels = None
depends_on = None

INVENTARIO = "marts.fact_ml_full_stock_inventory_daily"
STATUS = "marts.fact_ml_full_stock_status_daily"
MARCA = "marts.fact_ml_full_stock_brand_daily"

MARCAS = ("barbours", "kokeshi", "lescent", "rituaria")
CLASSIFICACOES = (
    "RUPTURA_CANDIDATA", "BAIXO_CANDIDATO", "EXCESSO_CANDIDATO", "SEM_GIRO_CANDIDATO",
    "SUFICIENTE", "SEM_DEMANDA_MEDIDA", "SEM_LEITURA", "FORA_DO_FULL",
)
LEITURAS = ("OK", "NAO_ENCONTRADO", "CONTRATO_QUEBRADO")
VINCULOS = ("COM_VENDA", "SEM_VENDA_NA_JANELA")


def _lista(valores):
    return ", ".join(f"'{v}'" for v in valores)


def _upgrade_sql() -> list[str]:
    """Monta o DDL. Funcao (e nao lista no topo): a convencao do repo proibe
    chamada no nivel do modulo, porque Alembic importa todas as versions."""
    return [
    f"""
    CREATE TABLE {INVENTARIO} (
        ref_date              DATE         NOT NULL,   -- dia da FOTOGRAFIA (America/Sao_Paulo)
        brand                 TEXT         NOT NULL CHECK (brand IN ({_lista(MARCAS)})),
        seller_id             TEXT         NOT NULL,
        inventory_id          TEXT         NOT NULL,
        read_status           TEXT         NOT NULL CHECK (read_status IN ({_lista(LEITURAS)})),
        disponivel            BIGINT       CHECK (disponivel >= 0),
        nao_disponivel        BIGINT       CHECK (nao_disponivel >= 0),
        transfer              BIGINT       CHECK (transfer >= 0),
        total_coerente        BOOLEAN,
        -- soma do detalhe por status == nao_disponivel. FALSE = payload do ML
        -- incoerente: fica visivel por inventario, sem bloquear os demais.
        detalhe_coerente      BOOLEAN,
        eh_full               BOOLEAN      NOT NULL,
        anuncios              INTEGER      NOT NULL CHECK (anuncios >= 0),
        anuncios_ativos       INTEGER      NOT NULL CHECK (anuncios_ativos >= 0),
        anuncios_full         INTEGER      NOT NULL CHECK (anuncios_full >= 0),
        unidades_28d          BIGINT       NOT NULL CHECK (unidades_28d >= 0),
        media_diaria_28d      NUMERIC(14,2) NOT NULL CHECK (media_diaria_28d >= 0 AND media_diaria_28d <> 'NaN'),
        cobertura_torre_dias  NUMERIC(10,1) CHECK (cobertura_torre_dias >= 0 AND cobertura_torre_dias <> 'NaN'),
        classificacao_torre   TEXT         NOT NULL CHECK (classificacao_torre IN ({_lista(CLASSIFICACOES)})),
        vinculo_vendas        TEXT         NOT NULL CHECK (vinculo_vendas IN ({_lista(VINCULOS)})),
        source_run_id         TEXT         NOT NULL,
        published_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
        CONSTRAINT pk_fmlfsi PRIMARY KEY (ref_date, brand, seller_id, inventory_id),
        -- Leitura OK exige quantidades; qualquer outra as proibe (ausencia != zero).
        CONSTRAINT ck_fmlfsi_leitura_quantidade CHECK (
            (read_status = 'OK' AND disponivel IS NOT NULL AND nao_disponivel IS NOT NULL AND transfer IS NOT NULL)
            OR (read_status <> 'OK' AND disponivel IS NULL AND nao_disponivel IS NULL AND transfer IS NULL)),
        CONSTRAINT ck_fmlfsi_sem_leitura CHECK ((read_status <> 'OK') = (classificacao_torre = 'SEM_LEITURA')),
        CONSTRAINT ck_fmlfsi_fora_do_full CHECK (classificacao_torre <> 'FORA_DO_FULL' OR NOT eh_full),
        CONSTRAINT ck_fmlfsi_cobertura_exige_demanda CHECK (cobertura_torre_dias IS NULL OR unidades_28d > 0),
        -- SEM CHECK "transfer <= nao_disponivel" de proposito: a validacao em
        -- PostgreSQL real mostrou que UM payload incoerente derrubaria a
        -- publicacao do dia inteiro. A incoerencia vai em `detalhe_coerente`.
        CONSTRAINT ck_fmlfsi_detalhe_so_com_leitura CHECK ((read_status = 'OK') = (detalhe_coerente IS NOT NULL))
    )
    """,
    f"CREATE INDEX ix_fmlfsi_data ON {INVENTARIO} (ref_date DESC, brand)",
    f"""
    CREATE TABLE {STATUS} (
        ref_date      DATE   NOT NULL,
        brand         TEXT   NOT NULL CHECK (brand IN ({_lista(MARCAS)})),
        seller_id     TEXT   NOT NULL,
        inventory_id  TEXT   NOT NULL,
        status_raw    TEXT   NOT NULL,   -- nome cru do ML; desconhecido e' preservado
        quantidade    BIGINT NOT NULL CHECK (quantidade >= 0),
        source_run_id TEXT   NOT NULL,
        CONSTRAINT pk_fmlfss PRIMARY KEY (ref_date, brand, seller_id, inventory_id, status_raw)
    )
    """,
    f"""
    CREATE TABLE {MARCA} (
        ref_date                         DATE    NOT NULL,
        brand                            TEXT    NOT NULL CHECK (brand IN ({_lista(MARCAS)})),
        inventarios                      INTEGER NOT NULL CHECK (inventarios >= 0),
        inventarios_full_lidos           INTEGER NOT NULL CHECK (inventarios_full_lidos >= 0),
        inventarios_compartilhados       INTEGER NOT NULL CHECK (inventarios_compartilhados >= 0),
        -- NULL quando nenhum inventario Full foi lido: ausencia, nunca zero.
        disponivel_full                  BIGINT  CHECK (disponivel_full >= 0),
        nao_disponivel_full              BIGINT  CHECK (nao_disponivel_full >= 0),
        transfer_full                    BIGINT  CHECK (transfer_full >= 0),
        unidades_28d_alocadas            BIGINT  NOT NULL CHECK (unidades_28d_alocadas >= 0),
        unidades_28d_item_varios_inventarios BIGINT NOT NULL DEFAULT 0 CHECK (unidades_28d_item_varios_inventarios >= 0),
        unidades_28d_sem_inventory_id    BIGINT  NOT NULL DEFAULT 0 CHECK (unidades_28d_sem_inventory_id >= 0),
        unidades_28d_fora_da_ponte       BIGINT  NOT NULL DEFAULT 0 CHECK (unidades_28d_fora_da_ponte >= 0),
        cobertura_da_alocacao            NUMERIC(6,4) CHECK (cobertura_da_alocacao BETWEEN 0 AND 1
                                                            AND cobertura_da_alocacao <> 'NaN'),
        source_run_id                    TEXT    NOT NULL,
        published_at                     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT pk_fmlfsb PRIMARY KEY (ref_date, brand),
        CONSTRAINT ck_fmlfsb_disponivel_exige_leitura CHECK (
            (inventarios_full_lidos > 0) = (disponivel_full IS NOT NULL))
    )
    """,
    ]


DOWNGRADE_SQL = [f"DROP TABLE IF EXISTS {MARCA}", f"DROP TABLE IF EXISTS {STATUS}",
                 f"DROP TABLE IF EXISTS {INVENTARIO}"]


def upgrade() -> None:
    for sql in _upgrade_sql():
        op.execute(sql)


def downgrade() -> None:
    for sql in DOWNGRADE_SQL:
        op.execute(sql)
