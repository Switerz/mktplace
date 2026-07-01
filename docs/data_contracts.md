# Data Contracts â€” Torre de Controle GoBeautÃ©

VersÃ£o: 1.0 | Atualizado: 2026-06-16

---

## 1. Entidades canÃ´nicas

### dim_empresa

Grupos empresariais. No momento, toda operaÃ§Ã£o GoBeautÃ© estÃ¡ sob uma Ãºnica empresa-mÃ£e, mas o modelo suporta expansÃ£o.

| Campo | Tipo | ObrigatÃ³rio | DescriÃ§Ã£o |
|---|---|---|---|
| empresa_id | serial | âœ… | PK |
| nome_empresa | varchar(100) | âœ… | Nome de exibiÃ§Ã£o |
| nome_normalizado | varchar(100) | âœ… | Slug sem acento (ex: `gobeaute`) |
| ativo | boolean | âœ… | Default true |
| created_at | timestamptz | âœ… | |
| updated_at | timestamptz | âœ… | |

**Seed inicial:**
| empresa_id | nome_empresa | nome_normalizado |
|---|---|---|
| 1 | GoBeautÃ© | gobeaute |

---

### dim_loja

Cada brand/loja operacional. Mapeada diretamente do campo `brand` das tabelas do Data Mart.

| Campo | Tipo | ObrigatÃ³rio | DescriÃ§Ã£o |
|---|---|---|---|
| loja_id | serial | âœ… | PK |
| empresa_id | int | âœ… | FK dim_empresa |
| brand_key | varchar(50) | âœ… | Chave exata usada no Data Mart (ex: `kokeshi`) |
| nome_loja | varchar(100) | âœ… | Nome de exibiÃ§Ã£o (ex: `KOKESHI`) |
| nome_normalizado | varchar(100) | âœ… | Slug (ex: `kokeshi`) |
| ativo | boolean | âœ… | Default true |
| created_at | timestamptz | âœ… | |
| updated_at | timestamptz | âœ… | |

**Seed inicial (brands no escopo):**
| loja_id | empresa_id | brand_key | nome_loja | TikTok | ML |
|---|---|---|---|---|---|
| 1 | 1 | apice | ÃPICE | âœ… | âŒ |
| 2 | 1 | barbours | BARBOURS | âœ… | âœ… |
| 3 | 1 | kokeshi | KOKESHI | âœ… | âœ… |
| 4 | 1 | lescent | LESCENT | âœ… | âœ… |
| 5 | 1 | rituaria | RITUÃRIA | âœ… | âœ… (desde 2026-07-01) |

**Fora do escopo (nÃ£o incluir no seed):** `azbuy`, `gocase`

---

### dim_marketplace

| Campo | Tipo | ObrigatÃ³rio | DescriÃ§Ã£o |
|---|---|---|---|
| marketplace_id | serial | âœ… | PK |
| nome_marketplace | varchar(50) | âœ… | Ex: `TikTok Shop` |
| slug | varchar(20) | âœ… | Ex: `tiktok`, `mercadolivre` |
| ativo | boolean | âœ… | |

**Seed inicial:**
| marketplace_id | nome_marketplace | slug |
|---|---|---|
| 1 | TikTok Shop | tiktok |
| 2 | Mercado Livre | mercadolivre |
| 3 | Shopee | shopee |
| 4 | Magalu | magalu |
| 5 | Amazon | amazon |

Shopee (`marketplace_id = 3`) está ativa para ingestão via exports locais. Magalu e Amazon permanecem cadastrados mas inativos até integração.

---

### dim_seller_account

Conta de seller por marketplace. No TikTok, identificada por `shop_cipher`/`shop_name`. No ML, por `seller_id`.

| Campo | Tipo | ObrigatÃ³rio | DescriÃ§Ã£o |
|---|---|---|---|
| seller_account_id | serial | âœ… | PK |
| marketplace_id | int | âœ… | FK dim_marketplace |
| loja_id | int | âœ… | FK dim_loja |
| external_seller_id | varchar(100) | âœ… | ID na plataforma (seller_id ML / shop_cipher TikTok) |
| account_name | varchar(200) | âŒ | Nome da conta na plataforma |
| ativo | boolean | âœ… | |
| created_at | timestamptz | âœ… | |
| updated_at | timestamptz | âœ… | |

> **Nota**: Os `shop_cipher` do TikTok serÃ£o levantados via query nos dados reais na Sprint 3.

---

### dim_calendario

Tabela gerada (sem FK para outras tabelas). Cobre 2024â€“2027.

| Campo | Tipo | DescriÃ§Ã£o |
|---|---|---|
| date | date | PK |
| ano | int | |
| mes | int | 1â€“12 |
| mes_nome | varchar(20) | Janeiro, Fevereiro... |
| mes_abrev | varchar(3) | Jan, Fev... |
| semana_iso | int | |
| trimestre | int | 1â€“4 |
| dia_semana | int | 1=segunda, 7=domingo |
| dia_semana_nome | varchar(15) | |
| inicio_semana | date | Segunda-feira da semana |
| inicio_mes | date | Dia 1 do mÃªs |
| fim_mes | date | Ãšltimo dia do mÃªs |
| dias_no_mes | int | |
| is_weekend | boolean | |

---

### dim_status_pedido

| Campo | Tipo | DescriÃ§Ã£o |
|---|---|---|
| status_id | serial | PK |
| marketplace_id | int | FK dim_marketplace (null = canÃ´nico) |
| raw_status | varchar(50) | Valor original da plataforma |
| status_canonico | varchar(30) | Ver tabela de mapeamento abaixo |
| descricao | text | DescriÃ§Ã£o para exibiÃ§Ã£o |

---

### fact_marketplace_daily_performance â­ (MVP â€” tabela principal)

Alimentada das gold tables do Data Mart. Granularidade: `date Ã— loja_id Ã— marketplace_id`.

| Campo | Tipo | Fonte TikTok | Fonte ML |
|---|---|---|---|
| id | serial | â€” | â€” |
| date | date | `tiktok_brand_daily.date` | `ml_gestao_diaria.ref_date` |
| loja_id | int | via brand_key | via brand_key |
| marketplace_id | int | 1 | 2 |
| empresa_id | int | via loja_id | via loja_id |
| gmv | numeric | `gmv` | `gmv` |
| orders | bigint | `orders` | `paid_orders` |
| units_sold | bigint | `items_sold` | `total_units` |
| avg_ticket | numeric | `avg_ticket` | `avg_ticket` |
| unique_buyers | bigint | `customers` | `unique_buyers` |
| new_buyers | bigint | null | `new_buyers` |
| repeat_buyers | bigint | null | `repeat_buyers` |
| repeat_buyer_rate_pct | numeric | null | `repeat_buyer_rate_pct` |
| visitors | bigint | `visitors` | null |
| conversion_rate | numeric | `conversion_rate` | null |
| canceled_orders | bigint | `canceled` | `cancelled_orders` |
| returned_orders | bigint | `returned` | null |
| refunded_orders | bigint | `refunded` | null |
| problem_rate | numeric | `problem_rate` | null |
| cancel_rate_pct | numeric | null | `cancel_rate_pct` |
| ad_spend | numeric | null | `ad_spend` |
| ad_revenue | numeric | null | `ad_revenue` |
| ad_impressions | bigint | null | `ad_impressions` |
| ad_clicks | bigint | null | `ad_clicks` |
| roas | numeric | null | `roas` |
| acos_pct | numeric | null | `acos_pct` |
| ctr_pct | numeric | null | `ctr_pct` |
| cpc | numeric | null | `cpc` |
| gmv_video | numeric | `gmv_video` | null |
| gmv_live | numeric | `gmv_live` | null |
| gmv_card | numeric | `gmv_card` | null |
| total_settlement | numeric | `total_settlement` | null |
| total_fees | numeric | `total_fees` | null |
| avg_fee_pct | numeric | `avg_fee_pct` | null |
| avg_settlement_pct | numeric | `avg_settlement_pct` | null |
| avg_delivery_hours | numeric | `avg_delivery_hours` | null |
| avg_delivery_days | numeric | null | `avg_delivery_days` |
| seller_shipping_cost | numeric | null | `seller_shipping_cost` |
| shipping_pct_of_gmv | numeric | null | `shipping_pct_of_gmv` |
| delivered_orders | bigint | `delivered_orders` | `delivered_shipments` |
| target_revenue | numeric | null (da fact_goal_monthly) | null |
| target_attainment_pct | numeric | ðŸ”¶ calculado | ðŸ”¶ calculado |
| projected_month_revenue | numeric | ðŸ”¶ calculado | ðŸ”¶ calculado |
| data_quality_score | numeric | ðŸ”¶ calculado | ðŸ”¶ calculado |
| source_updated_at | timestamptz | timestamp do Ãºltimo sync | |
| ingested_at | timestamptz | timestamp de carga | |

> Campos `null` significam "fonte nÃ£o disponÃ­vel", nÃ£o zero. Frontend deve exibir como `â€”` ou `N/D`.

#### Semantica financeira de `total_settlement` / `total_fees` (documentado em 2026-07-01, ver auditoria em `docs/sections/financeiro_audit.md` secao 11)

| Canal | Sinal de `total_fees` | Tratamento na API | `total_settlement` — o que realmente e |
|---|---|---|---|
| TikTok | Negativo (debito) | `abs(total_fees)` antes de expor | Vem de `gold.tiktok_brand_daily`, que por sua vez reflete o subsistema de repasses (statements) do Data Mart. E um valor de repasse genuino. **Comprovado:** medido sobre uma base de "revenue" ~5,5% maior que o GMV comercial em mai/2026 (universos diferentes, verificado por SQL). **Inferencia forte, ainda nao comprovada pedido a pedido:** o repasse de um mes tambem pode incluir pedidos de outro mes — `raw.tiktok_shop_settlements` (que ligaria settlement a `order_id`) esta vazia nesta replica do Data Mart. Nao comparar `total_settlement / gmv` do mesmo mes como se fosse uma margem estavel — varia de 35% a 77% mes a mes. |
| Shopee | Positivo (custo) | Exposto direto, sem `abs()` | **Nao e settlement.** Vem da coluna "Total global" do export `Order.all*.xlsx` (`pipelines/connectors/shopee/_parser.py`) — e o valor total do pedido, nao um repasse liquido. Fica sempre perto de 90-100%+ do GMV independente da taxa real. Nao usar como indicador de margem/liquidez ate existir uma fonte real de repasse (relatorio de renda/income release da Shopee, hoje nao integrado). |
| ML | Sempre `NULL` | N/A | Campo nao existe no mart para ML. A comissao real do marketplace existe em `gold.ml_produto_pnl.marketplace_fee` (RDS, media ~16,5% da receita bruta) mas essa tabela e cumulativa por produto, **sem coluna de data** — nao deve ser somada a um mes especifico sem uma fonte com competencia temporal. |

---

### fact_goal_monthly

Metas mensais. Carregadas manualmente do XLSX na Sprint 7.

| Campo | Tipo | DescriÃ§Ã£o |
|---|---|---|
| goal_id | serial | PK |
| ref_month | date | Primeiro dia do mÃªs de referÃªncia |
| loja_id | int | FK dim_loja |
| marketplace_id | int | FK dim_marketplace (null = todos) |
| empresa_id | int | FK dim_empresa |
| metric_name | varchar(50) | Ex: `gmv`, `orders`, `conversion_rate` |
| target_value | numeric | Valor da meta |
| source | varchar(50) | Ex: `xlsx_2026`, `manual` |
| created_at | timestamptz | |
| updated_at | timestamptz | |

---

### audit.source_sync_run

Registra cada execuÃ§Ã£o de sync.

| Campo | Tipo | DescriÃ§Ã£o |
|---|---|---|
| sync_run_id | serial | PK |
| source_name | varchar(50) | Ex: `tiktok_brand_daily`, `ml_gestao_diaria` |
| marketplace_id | int | |
| loja_id | int | null = todas as lojas |
| started_at | timestamptz | |
| finished_at | timestamptz | |
| status | varchar(20) | `running`, `success`, `failed` |
| rows_extracted | int | |
| rows_loaded | int | |
| error_message | text | |
| source_min_date | date | PerÃ­odo mÃ­nimo extraÃ­do |
| source_max_date | date | PerÃ­odo mÃ¡ximo extraÃ­do |

---

## 2. Mapeamento de status canÃ´nico

### TikTok Shop â†’ CanÃ´nico

| raw_status (TikTok) | status_canonico | DescriÃ§Ã£o | Volume real |
|---|---|---|---|
| COMPLETED | delivered | Pedido entregue e finalizado | 1.141.634 |
| CANCELLED | cancelled | Pedido cancelado | 331.201 |
| DELIVERED | delivered | Entregue (ainda nÃ£o fechado) | 202.991 |
| UNPAID | pending | Aguardando pagamento | 86.764 |
| IN_TRANSIT | shipped | Em trÃ¢nsito para entrega | 76.672 |
| AWAITING_COLLECTION | shipped | Aguardando coleta pela transportadora | 57.112 |
| AWAITING_SHIPMENT | processing | Pago, aguardando envio pelo seller | 1.584 |
| ON_HOLD | on_hold | Pedido retido (fraude/revisÃ£o) | 115 |

### Mercado Livre â†’ CanÃ´nico

| raw_status (ML) | status_canonico | DescriÃ§Ã£o | Volume real |
|---|---|---|---|
| paid | paid | Pago e ativo | 207.884 |
| cancelled | cancelled | Cancelado | 11.540 |
| partially_refunded | returned | Reembolsado parcialmente | 142 |
| pending_cancel | cancelled | Cancelamento pendente de confirmaÃ§Ã£o | 4 |

### Status canÃ´nicos completos

| status_canonico | DescriÃ§Ã£o | TikTok | ML |
|---|---|---|---|
| pending | Aguardando pagamento | âœ… | âŒ |
| paid | Pago, processando | âŒ | âœ… |
| processing | Pago, preparando envio | âœ… | âŒ |
| shipped | Em trÃ¢nsito | âœ… | âŒ |
| delivered | Entregue | âœ… | âŒ (inferido via shipments) |
| cancelled | Cancelado | âœ… | âœ… |
| returned | Devolvido / reembolsado | âœ… | âœ… |
| on_hold | Retido | âœ… | âŒ |
| unknown | Status nÃ£o mapeado | fallback | fallback |

---

## 3. Disponibilidade de mÃ©tricas por marketplace

| MÃ©trica | TikTok | ML | Shopee |
|---|---|---|---|
| GMV diário | ✅ | ✅ | ✅ exports orders |
| Pedidos | ✅ | ✅ | ✅ exports orders |
| Unidades vendidas | ✅ | ✅ | ✅ exports orders |
| Ticket médio | ✅ | ✅ | 🔶 calculado |
| Visitantes | ✅ | ❌ | ✅ shop-stats |
| Taxa de conversão | ✅ | ❌ | ✅ shop-stats |
| Novos compradores | ❌ | ✅ | ✅ shop-stats |
| Taxa de recompra | âŒ | âœ… | âŒ |
| Cancelamentos | âœ… | âœ… | âŒ |
| DevoluÃ§Ãµes | âœ… | âŒ | âŒ |
| Tempo de entrega | âœ… (horas) | âœ… (dias) | âŒ |
| Investimento mídia | ❓ (investigar) | ✅ | ✅ ads CSV, média diária |
| ROAS | ❓ (investigar) | ✅ | 🔶 ads CSV |
| ACOS | âŒ (investigar) | âœ… | âŒ |
| GMV por vÃ­deo/live | âœ… | âŒ | âŒ |
| Taxas marketplace | âœ… | âŒ (investigar) | âŒ |
| Valor liquidado | âœ… | âŒ | âŒ |
| Ranking SKU | âœ… | âœ… | âŒ |
| Estoque | âŒ | âœ… (ml_item_stock) | âŒ |
| Metas mensais | âŒ (XLSX) | âŒ (XLSX) | âŒ |

---

## 4. ERD simplificado (Mermaid)

```mermaid
erDiagram
    dim_empresa ||--o{ dim_loja : "possui"
    dim_loja ||--o{ dim_seller_account : "tem conta em"
    dim_marketplace ||--o{ dim_seller_account : "hospeda"
    dim_marketplace ||--o{ dim_status_pedido : "define"

    dim_loja ||--o{ fact_marketplace_daily_performance : "produz"
    dim_marketplace ||--o{ fact_marketplace_daily_performance : "no canal"
    dim_empresa ||--o{ fact_marketplace_daily_performance : "agrega"
    dim_calendario ||--o{ fact_marketplace_daily_performance : "em"

    dim_loja ||--o{ fact_goal_monthly : "tem meta"
    dim_marketplace ||--o{ fact_goal_monthly : "por canal"
    dim_empresa ||--o{ fact_goal_monthly : "da empresa"

    dim_empresa {
        int empresa_id PK
        varchar nome_empresa
        varchar nome_normalizado
        boolean ativo
    }

    dim_loja {
        int loja_id PK
        int empresa_id FK
        varchar brand_key
        varchar nome_loja
        boolean ativo
    }

    dim_marketplace {
        int marketplace_id PK
        varchar nome_marketplace
        varchar slug
        boolean ativo
    }

    fact_marketplace_daily_performance {
        int id PK
        date date FK
        int loja_id FK
        int marketplace_id FK
        int empresa_id FK
        numeric gmv
        bigint orders
        bigint units_sold
        numeric avg_ticket
        numeric conversion_rate
        numeric ad_spend
        numeric roas
        bigint canceled_orders
        numeric target_attainment_pct
        numeric projected_month_revenue
    }

    fact_goal_monthly {
        int goal_id PK
        date ref_month
        int loja_id FK
        int marketplace_id FK
        varchar metric_name
        numeric target_value
    }
```

---

## 5. Regras de qualidade obrigatÃ³rias

1. **GMV nunca negativo**: `gmv >= 0` ou null.
2. **Data vÃ¡lida**: `date >= '2025-01-01'` e `date <= CURRENT_DATE + 1`.
3. **Brand no escopo**: apenas `apice`, `barbours`, `kokeshi`, `lescent`, `rituaria`.
4. **Sem duplicidade**: `UNIQUE(date, loja_id, marketplace_id)` em `fact_marketplace_daily_performance`.
5. **Null explÃ­cito**: mÃ©trica indisponÃ­vel = `null`, nunca `0` para evitar distorÃ§Ã£o de mÃ©dias.
6. **Fonte rastreÃ¡vel**: toda linha tem `source_updated_at` e `ingested_at`.

---

## 6. Campos obrigatÃ³rios vs opcionais por entidade

### fact_marketplace_daily_performance
**ObrigatÃ³rios** (nÃ£o podem ser null): `date`, `loja_id`, `marketplace_id`, `empresa_id`, `ingested_at`
**ObrigatÃ³rios por marketplace**:
- TikTok: `gmv`, `orders`, `units_sold`, `avg_ticket`
- ML: `gmv`, `paid_orders`, `total_units`, `avg_ticket`

**Opcionais** (podem ser null sem invalidar o registro): todos os demais campos, especialmente mÃ©tricas de mÃ­dia e conteÃºdo.

### Shopee — contrato atual via exports

A integração Shopee usa arquivos locais em `SHOPEE_DATA_PATH`, com subpasta por brand. O destino canônico é `marts.fact_marketplace_daily_performance` na granularidade `date × loja_id × marketplace_id`.

Fontes:
- `Order.all*.xlsx`: GMV, pedidos, unidades, compradores, cancelamentos, devoluções, liquidação, taxas e frete seller.
- `*.shopee-shop-stats.*.xlsx`: visitantes, conversão, novos compradores e recompra.
- `Dados*.csv`: mídia paga; como o export é agregado por período, o pipeline distribui totais como média diária.

Caveat: a Shopee ainda não tem API conectada; a fonte de verdade operacional nesta fase são os exports do Seller Center.
