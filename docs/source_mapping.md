# Source Mapping â€” Fontes de Dados

## Banco de origem: Data Mart

- **Host**: ver `.env` â†’ `DATAMART_HOST`
- **Banco**: Data Mart (PostgreSQL 17.9)
- **Metabase ID**: 43
- **Acesso**: read-only
- **Timezone**: America/Sao_Paulo

---

## TikTok Shop

### Cobertura
| Atributo | Valor |
|---|---|
| Brands/lojas | apice, azbuy, barbours, gocase, kokeshi, lescent, rituaria |
| PerÃ­odo (raw) | 2025-06-04 â†’ hoje (atualizaÃ§Ã£o contÃ­nua) |
| Total pedidos (raw) | ~1,9M |
| Status distintos | 8 |

### Tabelas relevantes

#### `raw.tiktok_shop_orders` â€” Pedidos brutos
Schema: `raw` | Linhas: ~1,9M | AtualizaÃ§Ã£o: contÃ­nua

| Coluna | Tipo | DescriÃ§Ã£o |
|---|---|---|
| id | int | PK interno |
| brand | varchar | Identificador da loja (ex: `kokeshi`) |
| order_id | varchar | ID externo TikTok |
| shop_name | varchar | Nome do shop no TikTok |
| shop_cipher | varchar | Identificador tÃ©cnico do shop |
| order_status | varchar | Status bruto TikTok |
| order_type | varchar | Tipo do pedido |
| is_cod | boolean | Pagamento na entrega |
| created_at | timestamp | Data de criaÃ§Ã£o do pedido |
| paid_at | timestamp | Data de pagamento |
| updated_at_tiktok | timestamp | Ãšltima atualizaÃ§Ã£o TikTok |
| currency | varchar | Moeda (BRL) |
| payment_method | varchar | MÃ©todo de pagamento |
| original_total_product_price | numeric | PreÃ§o cheio dos produtos |
| original_shipping_fee | numeric | Frete original |
| platform_discount | numeric | Desconto dado pela plataforma |
| seller_discount | numeric | Desconto dado pelo seller |
| shipping_fee | numeric | Frete cobrado do comprador |
| sub_total | numeric | Subtotal |
| total_amount | numeric | Valor total do pedido |
| cpf | varchar | CPF do comprador (dado sensÃ­vel) |
| extracted_at | timestamp | Data de extraÃ§Ã£o |
| updated_at | timestamp | Ãšltima atualizaÃ§Ã£o no DM |

#### `raw.tiktok_shop_line_items` â€” Itens dos pedidos
Schema: `api` | 26 colunas

#### `raw.tiktok_shop_payments` â€” Pagamentos
Schema: `raw` | 15 colunas

#### `raw.tiktok_shop_settlements` â€” LiquidaÃ§Ãµes financeiras
Schema: `raw` | 16 colunas

#### `raw.tiktok_analytics_products` â€” Analytics de produtos
Schema: `raw/api` | 45 colunas

#### `raw.tiktok_analytics_shop_hourly` â€” Analytics por hora
Schema: `raw/api` | 18 colunas

#### `gold.tiktok_brand_daily` â€” **Tabela principal para MVP** â­
Schema: `gold` | 68 colunas | Granularidade: dia Ã— brand

Colunas-chave:
| Coluna | Tipo | KPI |
|---|---|---|
| brand | varchar | Identificador da loja |
| date | date | Data de referÃªncia |
| gmv | numeric | Faturamento bruto |
| orders | bigint | Pedidos |
| items_sold | bigint | Unidades vendidas |
| customers | bigint | Compradores Ãºnicos |
| visitors | int | Visitantes |
| conversion_rate | numeric | Taxa de conversÃ£o |
| avg_ticket | numeric | Ticket mÃ©dio |
| gmv_video | numeric | GMV via vÃ­deo |
| gmv_live | numeric | GMV via live |
| gmv_card | numeric | GMV via card/vitrine |
| total_views | numeric | VisualizaÃ§Ãµes totais |
| total_settlement | numeric | Valor liquidado |
| total_fees | numeric | Taxas totais |
| avg_fee_pct | numeric | % taxa mÃ©dia |
| avg_settlement_pct | numeric | % liquidaÃ§Ã£o |
| canceled | bigint | Pedidos cancelados |
| refunded | bigint | Pedidos reembolsados |
| returned | bigint | Pedidos devolvidos |
| problem_rate | numeric | Taxa de problemas |
| avg_delivery_hours | numeric | Horas mÃ©dias de entrega |

#### `gold.tiktok_orders_daily` â€” Pedidos diÃ¡rios agregados
Schema: `gold` | 13 colunas

#### `gold.tiktok_product_daily` â€” Performance por produto/dia
Schema: `gold` | 41 colunas

#### `gold.tiktok_shop_hourly` â€” Dados horÃ¡rios
Schema: `gold` | 25 colunas

#### `gold.tiktok_settlements_summary` â€” Resumo de liquidaÃ§Ãµes
Schema: `gold` | 21 colunas

#### `gold.tiktok_creator_daily` â€” Performance de criadores
Schema: `gold` | 18 colunas

---

## Mercado Livre

### Cobertura
| Atributo | Valor |
|---|---|
| Brands/lojas | barbours, kokeshi, lescent, rituaria (incluida em 2026-07-01 — dados reais desde 2025-12-28) |
| PerÃ­odo (raw) | 2025-04-27 â†’ hoje (atualizaÃ§Ã£o contÃ­nua) |
| Total pedidos (raw) | ~219K |
| Status distintos | 4 |

### Tabelas relevantes

#### `raw.ml_orders` â€” Pedidos brutos
Schema: `raw` | Linhas: ~219K | AtualizaÃ§Ã£o: contÃ­nua

| Coluna | Tipo | DescriÃ§Ã£o |
|---|---|---|
| id | int | PK interno |
| brand | varchar | Identificador da loja |
| order_id | bigint | ID externo ML |
| pack_id | bigint | ID do pack (multi-item) |
| shipping_id | bigint | ID do envio |
| status | varchar | Status bruto ML |
| status_detail | text | Detalhe do status |
| buying_mode | varchar | Modo de compra |
| fulfilled | boolean | Fulfillment ML |
| total_amount | numeric | Valor total |
| paid_amount | numeric | Valor pago |
| shipping_cost | numeric | Custo de frete |
| buyer_id | bigint | ID do comprador |
| buyer_nickname | varchar | Nickname do comprador |
| seller_id | bigint | ID do seller ML |
| context_channel | varchar | Canal (marketplace, etc.) |
| context_site | varchar | Site (MLB) |
| taxes_amount | numeric | Impostos |
| date_created | timestamp | Data de criaÃ§Ã£o |
| date_closed | timestamp | Data de fechamento |
| last_updated_ml | timestamp | Ãšltima atualizaÃ§Ã£o ML |
| cancel_detail | jsonb | Detalhes do cancelamento |

#### `raw.ml_order_line_items` â€” Itens dos pedidos
Schema: `raw/api` | 39 colunas

#### `raw.ml_shipments` â€” Envios
Schema: `raw/api` | 76 colunas (mais rica: rastreamento, SLA, endereÃ§os)

#### `raw.ml_items` â€” AnÃºncios/produtos
Schema: `raw/api` | 44 colunas

#### `raw.ml_ads_campaigns` â€” Campanhas de anÃºncio
Schema: `raw/api` | 43 colunas

#### `raw.ml_ads_items` â€” Itens anunciados
Schema: `raw/api` | 61 colunas

#### `raw.ml_order_payments` â€” Pagamentos
Schema: `raw/api` | 42 colunas

#### `raw.ml_billing_info` â€” InformaÃ§Ãµes fiscais
Schema: `raw/api` | 21 colunas

#### `gold.ml_gestao_diaria` â€” **Tabela principal para MVP** â­
Schema: `gold` | 37 colunas | Granularidade: dia Ã— brand

| Coluna | Tipo | KPI |
|---|---|---|
| ref_date | date | Data de referÃªncia |
| brand | varchar | Identificador da loja |
| total_orders | bigint | Total de pedidos |
| paid_orders | bigint | Pedidos pagos |
| cancelled_orders | bigint | Pedidos cancelados |
| cancel_rate_pct | numeric | Taxa de cancelamento % |
| gmv | numeric | Faturamento bruto |
| avg_ticket | numeric | Ticket mÃ©dio |
| unique_buyers | bigint | Compradores Ãºnicos |
| new_buyers | bigint | Novos compradores |
| repeat_buyers | bigint | Compradores recorrentes |
| repeat_buyer_rate_pct | numeric | Taxa de recompra % |
| gmv_per_buyer | numeric | GMV por comprador |
| total_units | bigint | Unidades vendidas |
| unique_items_sold | bigint | Itens distintos vendidos |
| unique_skus_sold | bigint | SKUs distintos vendidos |
| units_per_order | numeric | Unidades por pedido |
| ad_clicks | bigint | Cliques em anÃºncios |
| ad_impressions | bigint | ImpressÃµes |
| ctr_pct | numeric | CTR % |
| ad_spend | numeric | Investimento em mÃ­dia |
| ad_revenue | numeric | Receita atribuÃ­da |
| roas | numeric | ROAS |
| acos_pct | numeric | ACOS % |
| cpc | numeric | CPC |
| total_shipments | bigint | Total de envios |
| delivered_shipments | bigint | Entregues |
| avg_delivery_days | numeric | Dias mÃ©dios de entrega |
| seller_shipping_cost | numeric | Custo de frete seller |
| shipping_pct_of_gmv | numeric | Frete como % do GMV |

#### `gold.ml_gestao_mensal` â€” Performance mensal
Schema: `gold` | 39 colunas | Granularidade: mÃªs (ref_month) Ã— brand
Inclui: MoM (gmv_mom_pct, orders_mom_pct, buyers_mom_pct)

#### `gold.ml_produto_diario` â€” Performance por produto/dia
Schema: `gold` | 22 colunas

#### `gold.ml_produto_pnl` â€” P&L por produto
Schema: `gold` | 40 colunas

#### `gold.ml_produto_ranking` â€” Ranking de produtos
Schema: `gold` | 24 colunas

#### `gold.ml_campaign_diaria` â€” Performance de campanhas/dia
Schema: `gold` | 24 colunas

#### `gold.ml_buyer_lifecycle` â€” Ciclo de vida do comprador
Schema: `gold` | 12 colunas

#### `gold.ml_cross_company_summary` â€” Resumo cross-brand
Schema: `gold` | 30 colunas

---

## Shopee

**Status**: integração via exports locais ativa. Banco destino: Neon.tech.

### Cobertura atual
| Atributo | Valor |
|---|---|
| Brands/lojas | apice, barbours, kokeshi, lescent, rituaria |
| Período dos exports | jan/2026 a jun/2026 nos arquivos disponíveis |
| Fonte orders | `shopee/{brand}/Order.all*.xlsx` |
| Fonte funil | `shopee/{brand}/*.shopee-shop-stats.*.xlsx` |
| Fonte ads | `shopee/{brand}/Dados*.csv` |
| Destino canônico | `marts.fact_marketplace_daily_performance` com `marketplace_id = 3` |
| Banco destino | Neon.tech — `ep-lively-frost-a6eg1wh2.us-west-2.aws.neon.tech` / `neondb` |


### Resultado do backfill no Neon em 2026-06-23

| Brand | Dias carregados | Periodo | GMV | Pedidos | Ads spend |
|---|---:|---|---:|---:|---:|
| apice | 170 | 2026-01-01 a 2026-06-19 | 1.431.615,21 | 16.883 | 101.665,60 |
| barbours | 170 | 2026-01-01 a 2026-06-19 | 8.570.056,05 | 91.557 | 319.795,70 |
| kokeshi | 171 | 2026-01-01 a 2026-06-20 | 9.024.949,43 | 179.391 | 600.658,83 |
| lescent | 170 | 2026-01-01 a 2026-06-19 | 663.146,10 | 10.372 | 58.770,20 |
| rituaria | 170 | 2026-01-01 a 2026-06-19 | 1.492.083,26 | 12.292 | 107.998,00 |

Auditoria de carga (`audit.source_sync_run`):
- `shopee_daily`: 755 extraídas / 755 carregadas, período 2026-01-01 a 2026-05-31.
- `shopee-stats_daily`: 755 extraídas / 755 carregadas, período 2026-01-01 a 2026-05-31.
- `shopee-ads_daily`: 851 extraídas / 851 carregadas, período 2026-01-01 a 2026-06-20.

### Regras atuais
- Orders são agregados em dois níveis: linhas de SKU → pedido → dia/brand.
- Pedidos cancelados são excluídos de GMV/pedidos ativos e contados em `canceled_orders`.
- `gmv` usa subtotal dos produtos ativos; `total_settlement`, taxas e frete seller vêm dos campos financeiros do export de pedidos.
- Shop-stats preenche visitantes, conversão, compradores novos/recorrentes quando o arquivo existir.
- Ads CSV não tem granularidade diária; o pipeline distribui os totais do período como média diária e documenta essa limitação operacional.

Caminhos futuros:
1. Validar reconciliação dos totais por brand/mês contra o Seller Center.
2. Automatizar extração via API oficial Shopee Open Platform.
3. Adicionar granularidade SKU/pedido em fase posterior.

---

## Mapeamento Brand â†’ Empresa (XLSX vs Banco)

| Brand (banco) | Nome exibiÃ§Ã£o | TikTok | ML | XLSX |
|---|---|---|---|---|
| apice | ÃPICE | âœ… | âŒ | âœ… |
| azbuy | AZBUY | âœ… | âŒ | âŒ (nÃ£o estÃ¡ no XLSX â€” investigar) |
| barbours | BARBOURS | âœ… | âœ… | âœ… |
| gocase | GOCASE | âœ… | âŒ | âŒ (nÃ£o estÃ¡ no XLSX â€” investigar) |
| kokeshi | KOKESHI | âœ… | âœ… | âœ… |
| lescent | LESCENT | âœ… | âœ… | âœ… |
| rituaria | RITUÃRIA | âœ… | âœ… (desde 2026-07-01) | âœ… |

---

## Perguntas em aberto

1. **azbuy e gocase** existem no TikTok mas nÃ£o no XLSX de metas. SÃ£o marcas novas? Adquiridas? Grupo externo?
2. **apice** nÃ£o tem dados no ML — confirmado como ausÃªncia real da fonte, nÃ£o gap de ingestÃ£o. **rituaria** tinha dados reais desde 2025-12-28 mas estava excluÃ­da por whitelist desatualizada — corrigido em 2026-07-01.
3. **ml.tiktok_shop_line_items** estÃ¡ no schema `api`, nÃ£o `raw`. Qual a diferenÃ§a entre `raw` e `api`? A `api` Ã© uma view ou tabela separada?
4. O XLSX menciona abas por loja (ÃPICE, BARBOURS, etc.) com metas mensais. Quem alimenta essas metas e como seria a ingestÃ£o futura?
5. Existe coluna de seller_account separada por loja no TikTok (shop_cipher) â€” como mapear para as brands?


