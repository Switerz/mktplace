"""Contrato do refresh de expedicao — nomes, dominios e limiares.

POR QUE ESTE MODULO EXISTE SEPARADO
-----------------------------------
O contrato precisa ser legivel e testavel sem abrir conexao. Extracao,
transformacao, publicacao e auditoria importam daqui; nenhum literal de dominio
e repetido nos outros modulos.

DECISOES JA FIXADAS (Gates EXP-0R .. EXP-1A-R2, com medicao)
------------------------------------------------------------
1. As dimensoes de estado sao ORTOGONAIS. Medimos 933 pedidos Shopee aguardando
   expedicao: 101 `overdue` e 307 `over_48h`. Como 206 deles estao acima de 48h
   e AINDA dentro do prazo do marketplace, um bucket unico e mutuamente
   exclusivo apagaria esses 206 da atencao operacional.

2. O limite comercial de 48h NAO depende do p50. `2 x p50` e anomalia
   estatistica auxiliar (`is_slow_vs_baseline`) e nunca pode ser apresentada
   como SLA.

3. `is_slow_vs_baseline` e `is_source_zombie` sao flags SEPARADAS e podem ser
   verdadeiras ao mesmo tempo. Por isso `stalled_count` NUNCA e a soma de
   `slow_count + zombie_count`: a sobreposicao seria contada duas vezes.

4. Nao ha corte temporal na fila. Medimos 628 pedidos ML ainda abertos com mais
   de 60 dias; uma janela de 60 dias os teria apagado silenciosamente.

5. Shopee tem `timestamp_quality = verified`: todas as colunas de data de
   `raw.shopee_orders` sao `timestamp with time zone`.

6. (EXP-1A-R) Toda execucao agendada recomputa e publica. As classificacoes
   dependem do RELOGIO; watermark e metadado de frescor, nunca condicao para
   pular.

7. (EXP-1A-R2) A identidade do pedido NAO inclui a marca. A chave e
   `(channel, shop_account, marketplace_order_id)` — espelha
   `pk_shopee_orders (shop_account, order_sn)`. Marca e ATRIBUTO resolvido pelo
   registry: corrigir a marca de uma conta nao pode criar um pedido novo.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

# ---------------------------------------------------------------------------
# Destino — nomes do contrato. Nenhuma tabela e criada por este pacote.
# ---------------------------------------------------------------------------
#: Estado atual. Grao/PK: (channel, shop_account, marketplace_order_id).
FILA_TABLE = "marts.expedicao_fila_atual"

#: Resumo HORARIO POR CONTA. Grao/PK: (channel, shop_account, snapshot_hour).
#: Uma execucao saudavel grava uma linha por conta ESPERADA, inclusive com
#: backlog zero — e assim que a fotografia vazia deixa prova.
RUN_TABLE = "marts.expedicao_refresh_run"

#: EVOLUCAO FUTURA, nao implementada neste gate.
#: `marts.expedicao_alert_event` — uma linha quando um pedido ENTRA ou SAI de
#: `overdue`, `over_48h`, `slow`, `zombie` ou `stalled`. Substitui a ideia de
#: copiar todos os pedidos a cada hora: registra transicao relevante, nao
#: fotografia repetida. Ver docs/expedicao_contrato.md.
ALERT_EVENT_TABLE_FUTURE = "marts.expedicao_alert_event"


class Channel(str, Enum):
    """Canal de venda. A publicacao e SEMPRE por canal (ver publisher)."""

    SHOPEE = "shopee"
    MERCADOLIVRE = "mercadolivre"
    TIKTOKSHOP = "tiktokshop"


#: `audit.source_sync_run.marketplace_id`. Confirmado em `marts.dim_marketplace`
#: (leitura read-only do Neon): 1=TikTok Shop, 2=Mercado Livre, 3=Shopee.
MARKETPLACE_ID = {
    Channel.TIKTOKSHOP: 1,
    Channel.MERCADOLIVRE: 2,
    Channel.SHOPEE: 3,
}


# ---------------------------------------------------------------------------
# Dominios de estado — ortogonais entre si
# ---------------------------------------------------------------------------
class DeadlineStatus(str, Enum):
    """Vencimento CONTRATUAL do marketplace. Autoridade maxima.

    `UNAVAILABLE` impede que ausencia de prazo vire falso positivo: prazo nulo
    jamais e rotulado `OVERDUE`.
    """

    OVERDUE = "overdue"
    DUE_WITHIN_24H = "due_within_24h"
    ON_TIME = "on_time"
    UNAVAILABLE = "unavailable"


class OperationalAgeStatus(str, Enum):
    """Idade operacional contra a regra de negocio de 48h.

    Independente de `DeadlineStatus`: um pedido pode estar dentro do prazo do
    marketplace e ainda assim exigir atencao por idade.
    """

    WITHIN_48H = "within_48h"
    OVER_48H = "over_48h"
    UNKNOWN = "unknown"


class FreshnessStatus(str, Enum):
    """Idade do DADO, nao do pedido. Medida por conta, nunca global."""

    FRESH = "fresh"
    STALE = "stale"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class TimestampQuality(str, Enum):
    """Confianca na convencao de fuso da FONTE."""

    VERIFIED = "verified"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


class RunStatus(str, Enum):
    """Desfecho da execucao, persistido no resumo horario."""

    SUCCESS = "success"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Limiares — todos explicitos, nenhum literal solto nos outros modulos
# ---------------------------------------------------------------------------
#: Regra de negocio: acima disto o pedido gera atencao operacional.
OPERATIONAL_AGE_LIMIT = timedelta(hours=48)

#: Janela do `DUE_WITHIN_24H`.
DUE_SOON_WINDOW = timedelta(hours=24)

#: Pedido ativo mais velho que isto e tratado como residuo da fonte.
SOURCE_ZOMBIE_AGE = timedelta(days=30)

#: Multiplicador do p50 para a anomalia estatistica. NAO e SLA.
SLOW_BASELINE_FACTOR = 2.0

#: Amostra minima para que um p50 possa sustentar `is_slow_vs_baseline`.
MIN_BASELINE_SAMPLE = 100

#: Freshness por conta.
FRESHNESS_FRESH_LIMIT = timedelta(hours=8)
FRESHNESS_STALE_LIMIT = timedelta(hours=24)


# ---------------------------------------------------------------------------
# Advisory lock — chave propria, validada contra as ja usadas no repo
# ---------------------------------------------------------------------------
#: Chaves ja em uso (varredura em `pipelines/`): 906_120_006 .. 914_120_014,
#: 912130013, 913_120_001, 913_120_041, 564738291056, 987654321123.
ADVISORY_LOCK_KEYS = {
    Channel.SHOPEE: 915_120_015,
    Channel.MERCADOLIVRE: 916_120_016,
    Channel.TIKTOKSHOP: 917_120_017,
}

KNOWN_FOREIGN_LOCK_KEYS = frozenset(
    {
        906_120_006, 907_120_007, 908_120_008, 909_120_009, 910_120_010,
        911_120_011, 912_120_012, 913_120_013, 914_120_014, 912130013,
        913_120_001, 913_120_041, 564738291056, 987654321123,
    }
)


# ---------------------------------------------------------------------------
# PII — barreira estrutural
# ---------------------------------------------------------------------------
#: Colunas que o SELECT da Shopee pode conter. Lista FECHADA: o teste compara
#: por igualdade, nao por continencia.
SHOPEE_ALLOWED_SOURCE_COLUMNS = frozenset(
    {
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
    }
)

#: Fragmentos proibidos em qualquer SQL ou modelo deste pacote.
PII_FORBIDDEN_TOKENS = frozenset(
    {
        "buyer", "cpf", "cnpj", "recipient", "phone", "telefone", "address",
        "endereco", "nome_contato", "client_name", "document", "documento",
        "dropshipper", "invoice_data", "package_list", "message_to_seller",
        "username", "email",
    }
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SellerAccount:
    """Conta resolvida pelo registry, nunca por inferencia de texto.

    Estrutura confirmada por leitura read-only do Neon:

        marts.dim_seller_account(seller_account_id PK, marketplace_id, loja_id,
            external_seller_id, account_name, ativo NOT NULL, ...)
            UNIQUE (marketplace_id, external_seller_id)

        marts.dim_loja(loja_id PK, empresa_id, brand_key UNIQUE, nome_loja,
            nome_normalizado, ativo NOT NULL, ...)

    A UNIQUE de `dim_seller_account` ja garante `external_seller_id` unico
    DENTRO do marketplace, e `brand_key` e UNIQUE em `dim_loja` — logo
    conta -> loja -> marca resolve 1:1 por construcao do schema.
    """

    external_seller_id: str
    loja_id: int
    brand_key: str
    ativo: bool = True


#: SQL do registry. `ativo` existe nas DUAS dimensoes: uma conta so entra no
#: conjunto esperado se a conta E a loja estiverem ativas. Desativar a conta
#: (`UPDATE ... SET ativo = false`) e como se remove uma conta do conjunto
#: esperado — nao se apaga a linha, para nao perder o historico da chave.
REGISTRY_SQL = """
SELECT
    sa.external_seller_id,
    sa.loja_id,
    l.brand_key,
    sa.ativo AND l.ativo AS ativo
FROM marts.dim_seller_account sa
JOIN marts.dim_loja l ON l.loja_id = sa.loja_id
WHERE sa.marketplace_id = %(marketplace_id)s
"""


@dataclass(frozen=True)
class SourceWatermark:
    """Watermark por conta. O maximo GLOBAL esconderia conta parada.

    Desde o EXP-1A-R e METADADO DE FRESCOR: descreve a idade do dado, mas NAO
    decide se a fotografia e publicada.

    Carrega `shop_account` porque as duas identidades da conta tem donos
    diferentes: `external_seller_id` (o `shop_id`) e a chave do REGISTRY, e
    `shop_account` e a chave da FONTE (`pk_shopee_orders`). O watermark e o
    unico ponto que ve as duas juntas, entao e ele que sustenta o resumo por
    conta.
    """

    external_seller_id: str
    shop_account: str
    max_ingested_at: datetime | None


class SourceHealth(str, Enum):
    """Saude da FONTE, independente do tamanho do backlog.

    A distincao existe porque `backlog == 0` e ambiguo: pode significar "tudo
    foi expedido" (fotografia vazia legitima) ou "a fonte sumiu" (nao se pode
    concluir nada). Tratar os dois igual apagaria a fila com base em silencio.

    `UNEXPECTED_ACCOUNT` existe porque conjunto esperado ser SUBCONJUNTO do
    observado nao basta: uma loja nova aberta na Shopee apareceria na API sem
    cadastro, e seus pedidos ou seriam ignorados (backlog invisivel) ou
    receberiam marca adivinhada. Os dois desfechos sao inaceitaveis, entao a
    publicacao inteira bloqueia ate a conta ser cadastrada.
    """

    HEALTHY = "healthy"
    ACCOUNT_MISSING = "account_missing"
    UNEXPECTED_ACCOUNT = "unexpected_account"
    REGISTRY_AMBIGUOUS = "registry_ambiguous"
    WATERMARK_MISSING = "watermark_missing"
    SOURCE_UNAVAILABLE = "source_unavailable"
    #: A conta EXISTE na fonte e tem carimbo, mas o carimbo esta FORA da coorte
    #: de confiabilidade: o extrator parou de rele-la.
    #:
    #: Existe por causa do Mercado Livre, e o motivo e' especifico. No Shopee a
    #: fonte parada ainda devolve as linhas do ultimo estado conhecido, e o
    #: alerta de frescor sinaliza o atraso. No ML o filtro de coorte REMOVE
    #: essas linhas — entao conta parada produziria `backlog = 0` e o publisher
    #: apagaria a fila anterior com base em silencio. E' exatamente o desfecho
    #: que `is_empty_photograph` existe para impedir.
    #:
    #: NAO e' persistido em lugar nenhum (a 018 nao guarda `source_health`),
    #: entao o valor novo nao exige migration.
    SOURCE_STALE = "source_stale"

    @property
    def can_publish(self) -> bool:
        """So `HEALTHY` autoriza publicacao (e, portanto, limpeza da fila)."""
        return self is SourceHealth.HEALTHY


@dataclass(frozen=True)
class ExtractionResult:
    """Resultado da extracao, com saude e tamanho SEPARADOS.

    `backlog_rows == []` nao e prova de nada sozinho: so significa fotografia
    vazia quando `source_health is HEALTHY`.
    """

    source_health: SourceHealth
    expected_accounts: frozenset[str]
    observed_accounts: frozenset[str]
    account_watermarks: dict[str, datetime | None]
    backlog_rows: list[dict]
    detail: str = ""
    #: `external_seller_id -> shop_account`, vindo do watermark. Necessario
    #: porque o resumo tem grao por `shop_account` e o registry so conhece o
    #: `external_seller_id`.
    account_shop_names: dict[str, str] | None = None
    #: Contagens do FUNIL de exclusao, por canal que tenha filtro alem do
    #: status. Existe porque exclusao silenciosa e indistinguivel de fonte
    #: vazia: o ML descarta Full e registro congelado, e quem le a fila precisa
    #: ver quantas linhas cada filtro tirou. Shopee nao preenche (nao filtra
    #: nada alem do status) e continua com `None`.
    diagnostics: dict[str, int] | None = None

    @property
    def backlog_count(self) -> int:
        return len(self.backlog_rows)

    @property
    def missing_accounts(self) -> frozenset[str]:
        """Esperadas e nao observadas."""
        return frozenset(self.expected_accounts - self.observed_accounts)

    @property
    def unexpected_accounts(self) -> frozenset[str]:
        """Observadas e nao cadastradas."""
        return frozenset(self.observed_accounts - self.expected_accounts)

    @property
    def is_empty_photograph(self) -> bool:
        """Fonte saudavel que observou zero pedidos pendentes."""
        return self.source_health.can_publish and not self.backlog_rows


class RegistryError(RuntimeError):
    """Conta presente na fonte e ausente do registry.

    Falha fechada: cair para o texto de `raw.shopee_orders.brand` publicaria uma
    marca adivinhada, que e exatamente o que o contrato proibe.
    """


class SourceUnhealthy(RuntimeError):
    """Fonte nao esta em condicao de sustentar uma publicacao.

    Falha fechada: preserva a fila anterior em vez de apaga-la com base numa
    leitura em que nao se pode confiar.
    """


# ---------------------------------------------------------------------------
# Mercado Livre (EXP-3B1) — o que NAO e igual a Shopee
# ---------------------------------------------------------------------------
#: Unidade operacional do ML e o SHIPMENT, nao o pedido. Medido em 17/09/2026:
#: 77 pedidos historicos tem mais de um shipment. Usar `order_id` como
#: identidade colidiria nesses casos e apagaria um dos despachos.
#:
#: `(brand, shipment_id)` e UNIQUE na fonte (0 duplicatas em 521.548 linhas) e
#: marca <-> seller_id e 1:1, entao (channel, shop_account, marketplace_order_id)
#: da 018 comporta o grao de shipment sem colisao e sem perda.
#: RESSALVA MEDIDA: `shipment_id` sozinho NAO e unico — repete entre marcas em
#: 117 casos. A marca (via shop_account) faz parte da identidade, nao e enfeite.
ML_OPERATIONAL_UNIT = "shipment"

#: ALLOWLIST POSITIVA das modalidades operadas pelo VENDEDOR.
#:
#: Nao e `!= 'fulfillment'`: uma modalidade nova do Mercado Livre entraria
#: sozinha na fila por negacao, sem ninguem decidir. Aqui ela fica de fora ate
#: ser classificada, e `unmapped_logistic_type_count` a torna visivel.
#:
#: Dominio real medido na fonte (17/09/2026, historico completo de 521.548
#: shipments): fulfillment 339.556, cross_docking 117.120, xd_drop_off 51.643,
#: self_service 9.927, drop_off 3.389. Nenhum outro valor, nenhum nulo.
ML_SELLER_MANAGED_LOGISTIC_TYPES = frozenset(
    {"cross_docking", "xd_drop_off", "drop_off", "self_service"}
)

#: FULL. Fica FORA da fila do vendedor: o estoque ja esta no armazem do Mercado
#: Livre e quem separa, embala e despacha e o proprio ML. Os substatus medidos
#: (`in_warehouse`, `in_packing_list`, `ready_to_pack`, `packed`) sao operacao
#: do armazem deles. Cobrar isso da expedicao do vendedor seria culpar a
#: operacao por trabalho que ela nao executa. Pertence a superficie de Full.
ML_FULFILLMENT_LOGISTIC_TYPE = "fulfillment"

#: Dominio FECHADO. Qualquer valor fora daqui bloqueia a publicacao.
ML_KNOWN_LOGISTIC_TYPES = ML_SELLER_MANAGED_LOGISTIC_TYPES | {
    ML_FULFILLMENT_LOGISTIC_TYPE
}

#: Status do shipment que ABRE a fila.
ML_BACKLOG_SHIPMENT_STATUS = "ready_to_ship"

#: Status do PEDIDO exigido. `paid` e o unico que representa venda viva.
ML_BACKLOG_ORDER_STATUS = "paid"

#: COORTE CONFIAVEL DA FONTE — derivada de medicao, nao escolhida.
#:
#: O extrator do ML nao vive neste repositorio (a ingestao e externa, via
#: Airflow), entao a janela NAO pode ser provada por codigo. O que pode ser
#: provado e o COMPORTAMENTO, linha a linha, por `extracted_at`.
#:
#: Medicao de 17/09/2026, nas quatro marcas:
#:   - shipments criados ha menos de 3 dias: pior atraso de releitura
#:     67,69h / 67,99h / 67,93h / 67,69h (mediana ~32h);
#:   - criados entre 3 e 7 dias: pior atraso 163,73h / 163,88h / 163,73h /
#:     163,80h — ou seja <= 6,83 dias, com dispersao de 0,15h entre marcas;
#:   - cobertura de releitura por idade do registro: 100% ate 7 dias,
#:     despencando para 2,5% na faixa de 7 a 15 dias e 0% acima disso.
#:
#: Logo: um registro NAO relido ha mais de 7 dias esta provadamente FORA da
#: janela ativa do extrator, e o estado gravado nele e uma fotografia velha —
#: nao evidencia de que o pedido continua parado na operacao.
#:
#: A margem e estreita: 168h de limiar contra 163,88h observados, ou seja 4,12h.
#: Um atraso do extrator empurra registros VIVOS para fora da coorte. O erro e
#: conservador (some da fila em vez de inventar backlog), e
#: `stale_source_record_count` mede exatamente quantos sairam.
ML_SOURCE_COHORT_MAX_AGE = timedelta(days=7)

#: CONVENCAO DE FUSO — POR COLUNA, nunca por tipo.
#:
#: `raw.ml_shipments` guarda DOIS RELOGIOS diferentes em colunas que tem o mesmo
#: tipo (`timestamp without time zone`). Inferir o fuso pelo tipo da coluna,
#: como o EXP-3B1 fazia, erra metade delas.
#:
#: Prova (EXP-3B2-P), ancorando cada coluna no `now()` do servidor, que e
#: `timestamptz` e portanto tem fuso conhecido:
#:
#:     now() - max(ml_shipments.extracted_at) = 0,144h  -> UTC
#:     now() - max(ml_orders.extracted_at)    = 0,232h  -> UTC
#:     now() - max(date_created)              = 4,250h  -> UTC-4
#:
#: O EXP-3A media `min(extracted_at - date_created) = 4,00h` e concluiu "a fonte
#: grava UTC-4". A medicao estava certa e a conclusao errada: a diferenca entre
#: DOIS carimbos nao fixa o fuso de NENHUM deles — e compativel com infinitos
#: pares. So uma ancora de fuso conhecido resolve.
#:
#: CARIMBO DE INGESTAO (`extracted_at`): relogio do job que escreve na tabela.
#: Ja esta em UTC. Aplicar offset nele joga o watermark 4h no FUTURO — foi o
#: que o EXP-3B2-P mediu: idade de watermark -3,79h, 12 linhas com
#: `source_ingested_at` futuro, 20 de 910 linhas entrando fora da coorte e a
#: deteccao de `SOURCE_STALE` atrasada em exatamente a margem declarada.
ML_INGESTION_TIMESTAMPS = frozenset({"extracted_at"})

#: Os APELIDOS sob os quais o carimbo de ingestao viaja fora do SELECT.
#:
#: O watermark sai do SQL como `max_extracted_at` e chega ao codigo como o
#: campo `SourceWatermark.max_ingested_at` — mesmo instante, tres nomes. A
#: barreira estrutural precisa conhecer os apelidos: sem isto ela protege a
#: coluna `extracted_at` e deixa o alias passar, e o watermark foi justamente
#: um dos tres pontos que tinham o defeito do EXP-3B1.
#:
#: Medido na revisao EXP-3B1-H1-R/V: trocar o normalizador do watermark para o
#: de negocio passava nos 75 testes sem nenhum reprovar.
ML_INGESTION_TIMESTAMP_ALIASES = frozenset({"max_extracted_at", "max_ingested_at"})

#: CARIMBO DE NEGOCIO (`date_created`, `date_ready_to_ship`, `order_created_at`):
#: vem da API do Mercado Livre, gravado naive em UTC-4.
#:
#: Continua sendo INFERENCIA MEDIDA, nao contrato oficial — por isso as linhas
#: do ML saem com `timestamp_quality = 'assumed'`, nunca `verified`.
#:
#: PERGUNTA ABERTA AO TIME: confirmar contra a documentacao oficial da API do
#: Mercado Livre se esses campos vem com offset -04:00 e se o extrator descarta
#: o fuso ao gravar.
ML_BUSINESS_TIMESTAMPS = frozenset(
    {"date_created", "date_ready_to_ship", "order_created_at"}
)

#: Offset dos carimbos de NEGOCIO. O nome diz a que se aplica de proposito: o
#: antigo `ML_SOURCE_UTC_OFFSET` sugeria "toda a fonte" e foi exatamente assim
#: que `extracted_at` acabou convertido junto.
ML_BUSINESS_UTC_OFFSET = timedelta(hours=-4)

#: Colunas que o SELECT do ML pode conter. Lista FECHADA, comparada por
#: igualdade. Nenhuma coluna de comprador, destinatario ou endereco.
ML_ALLOWED_SOURCE_COLUMNS = frozenset(
    {
        "seller_id",
        "brand",
        "shipment_id",
        "order_id",
        "shipment_status",
        "substatus",
        "logistic_type",
        "order_status",
        "order_created_at",
        "date_created",
        "date_ready_to_ship",
        "date_shipped",
        "date_cancelled",
        "tracking_method",
        "extracted_at",
    }
)
