# DicionÃ¡rio de KPIs â€” Torre de Controle GoBeautÃ©

Legenda de disponibilidade:
- âœ… disponÃ­vel diretamente da fonte
- ðŸ”¶ calculÃ¡vel por derivaÃ§Ã£o
- â“ precisa investigar
- âŒ indisponÃ­vel / nÃ£o mapeado

---

## Comercial

| KPI | DefiniÃ§Ã£o | FÃ³rmula | Granularidade | TikTok | ML |
|---|---|---|---|---|---|
| Faturamento bruto (GMV) | Soma dos valores brutos dos pedidos antes de descontos e taxas | `SUM(total_amount)` | dia/mÃªs/brand | âœ… `gmv` | âœ… `gmv` |
| Faturamento lÃ­quido | GMV menos taxas e descontos | `gmv - total_fees - descontos` | dia/brand | âœ… `total_settlement` | â“ (verificar ml_billing) |
| Pedidos | Contagem de pedidos | `COUNT(order_id)` | dia/brand | âœ… `orders` | âœ… `paid_orders` |
| Unidades vendidas | Soma de itens vendidos | `SUM(quantity)` | dia/brand | âœ… `items_sold` | âœ… `total_units` |
| Ticket mÃ©dio | GMV / pedidos | `gmv / orders` | dia/brand | âœ… `avg_ticket` | âœ… `avg_ticket` |
| Receita por SKU | GMV atribuÃ­do a cada SKU | join com gold.tiktok_product_daily / ml_produto_diario | dia/sku | âœ… | âœ… |
| Crescimento dia contra dia | VariaÃ§Ã£o % do GMV vs dia anterior | `(gmv_hoje - gmv_ontem) / gmv_ontem` | dia/brand | ðŸ”¶ | ðŸ”¶ |
| Crescimento MoM | VariaÃ§Ã£o % do GMV vs mÃªs anterior | `gmv_mom_pct` | mÃªs/brand | ðŸ”¶ | âœ… `gmv_mom_pct` |
| Atingimento de meta | Realizado / meta | `gmv / target_value` | mÃªs/brand | ðŸ”¶ (requer metas) | ðŸ”¶ (requer metas) |
| ProjeÃ§Ã£o fechamento mÃªs | Run-rate simples | `(gmv_acumulado / dias_decorridos) * dias_do_mes` | mÃªs/brand | ðŸ”¶ | ðŸ”¶ |

---

## Funil

| KPI | DefiniÃ§Ã£o | FÃ³rmula | Granularidade | TikTok | ML |
|---|---|---|---|---|---|
| Visitas | Visitantes Ãºnicos no perÃ­odo | â€” | dia/brand | âœ… `visitors` | â“ (verificar ml_item_visits) |
| Taxa de conversÃ£o | Pedidos / visitas | `orders / visitors` | dia/brand | âœ… `conversion_rate` | â“ |
| Compradores Ãºnicos | Compradores distintos | â€” | dia/brand | âœ… `customers` | âœ… `unique_buyers` |
| Novos compradores | Compradores em primeira compra | â€” | dia/brand | â“ | âœ… `new_buyers` |
| Taxa de recompra | Compradores recorrentes / total | â€” | dia/brand | â“ | âœ… `repeat_buyer_rate_pct` |
| GMV por comprador | GMV / compradores Ãºnicos | â€” | dia/brand | ðŸ”¶ | âœ… `gmv_per_buyer` |

---

## MÃ­dia

| KPI | DefiniÃ§Ã£o | FÃ³rmula | Granularidade | TikTok | ML |
|---|---|---|---|---|---|
| Investimento mÃ­dia | Total gasto em ads | â€” | dia/brand | â“ (verificar tiktok_analytics) | âœ… `ad_spend` |
| ROAS | Receita atribuÃ­da / investimento | `ad_revenue / ad_spend` | dia/brand | â“ | âœ… `roas` |
| ACOS | Investimento / receita atribuÃ­da % | `ad_spend / ad_revenue` | dia/brand | â“ | âœ… `acos_pct` |
| TACOS | Investimento / GMV total % | `ad_spend / gmv` | dia/brand | â“ | ðŸ”¶ |
| CPC | Custo por clique | `ad_spend / ad_clicks` | dia/brand | â“ | âœ… `cpc` |
| CTR | Cliques / impressÃµes % | `ad_clicks / ad_impressions` | dia/brand | â“ | âœ… `ctr_pct` |
| Receita atribuÃ­da | Receita de vendas influenciadas por ad | â€” | dia/brand | â“ | âœ… `ad_revenue` |
| Pedidos atribuÃ­dos | Pedidos de vendas via ad | â€” | dia/brand | â“ | âœ… `ad_units_sold` |
| % investimento s/ GMV | ad_spend / gmv | â€” | dia/brand | â“ | ðŸ”¶ |

---

## TikTok â€” KPIs especÃ­ficos de conteÃºdo

| KPI | DefiniÃ§Ã£o | Coluna | DisponÃ­vel |
|---|---|---|---|
| GMV via vÃ­deo | Receita gerada por vÃ­deos | `gmv_video` | âœ… |
| GMV via live | Receita gerada em lives | `gmv_live` | âœ… |
| GMV via card | Receita via vitrine/card | `gmv_card` | âœ… |
| % GMV vÃ­deo | gmv_video / gmv | `pct_gmv_video` | âœ… |
| VÃ­deos ativos | VÃ­deos com pelo menos 1 venda | `active_videos` | âœ… |
| Criadores ativos | Criadores com pelo menos 1 venda | `active_video_creators` | âœ… |
| GPM | GMV per 1000 views | `gpm` | âœ… |
| GMV por vÃ­deo | â€” | `gmv_per_video` | âœ… |
| GMV por criador | â€” | `gmv_per_creator` | âœ… |
| Lives | Total de lives | `total_lives` | âœ… |
| GMV por live | â€” | `gmv_per_live` | âœ… |
| VÃ­deos frescos | VÃ­deos postados hÃ¡ â‰¤ 7 dias | `fresh_videos` | âœ… |
| VÃ­deos evergreen | VÃ­deos com > 7 dias ainda gerando venda | `evergreen_videos` | âœ… |

---

## Operacional

| KPI | DefiniÃ§Ã£o | FÃ³rmula | Granularidade | TikTok | ML |
|---|---|---|---|---|---|
| Pedidos cancelados | â€” | â€” | dia/brand | âœ… `canceled` | âœ… `cancelled_orders` |
| Taxa de cancelamento | cancelados / total | â€” | dia/brand | ðŸ”¶ | âœ… `cancel_rate_pct` |
| Pedidos devolvidos | â€” | â€” | dia/brand | âœ… `returned` | â“ |
| Pedidos reembolsados | â€” | â€” | dia/brand | âœ… `refunded` | â“ |
| Taxa de problemas | (cancelados + devolvidos + reembolsados) / total | â€” | dia/brand | âœ… `problem_rate` | â“ |
| Tempo mÃ©dio entrega | Horas/dias mÃ©dios da criaÃ§Ã£o Ã  entrega | â€” | dia/brand | âœ… `avg_delivery_hours` | âœ… `avg_delivery_days` |
| Pedidos entregues | â€” | â€” | dia/brand | âœ… `delivered_orders` | âœ… `delivered_shipments` |
| Taxa nÃ£o entregue | â€” | â€” | dia/brand | â“ | âœ… `not_delivered_rate_pct` |

---

## Financeiro

| KPI | DefiniÃ§Ã£o | Granularidade | TikTok | ML |
|---|---|---|---|---|
| Taxas marketplace | Total de taxas cobradas | dia/brand | âœ… `total_fees` | â“ (verificar ml_billing_info) |
| % taxa | taxa / gmv | dia/brand | âœ… `avg_fee_pct` | â“ |
| Frete cobrado comprador | Frete pago pelo cliente | dia/brand | âœ… `original_shipping_fee` (raw) | â“ |
| Frete custo seller | Custo de frete para o seller | dia/brand | â“ | âœ… `seller_shipping_cost` |
| Frete % GMV | frete_custo / gmv | dia/brand | â“ | âœ… `shipping_pct_of_gmv` |
| Valor liquidado | Valor efetivamente recebido | dia/brand | âœ… `total_settlement` | â“ |
| % liquidaÃ§Ã£o | settlement / gmv | dia/brand | âœ… `avg_settlement_pct` | â“ |
| Margem contribuiÃ§Ã£o | Receita lÃ­quida - CMV | dia/brand/sku | â“ (sem custo de produto) | âœ… `ml_produto_pnl` (parcial) |

---

## CatÃ¡logo / Estoque

| KPI | DefiniÃ§Ã£o | Granularidade | TikTok | ML |
|---|---|---|---|---|
| SKUs ativos | SKUs com pelo menos 1 venda | dia/brand | âœ… (tiktok_product_daily) | âœ… `unique_skus_sold` |
| SKUs sem venda | SKUs no catÃ¡logo sem venda | â€” | â“ | â“ |
| Ranking de SKUs | Top N por GMV | dia/brand | âœ… (tiktok_product_daily) | âœ… (ml_produto_ranking) |
| PreÃ§o mÃ©dio | â€” | dia/sku | âœ… (tiktok_analytics_skus) | âœ… (ml_item_price_history) |
| Estoque disponÃ­vel | Qtd em estoque | â€” | â“ | âœ… (ml_item_stock_history) |

---

## Notas importantes

- **Zero â‰  null**: dados ausentes ou nÃ£o disponÃ­veis devem ser exibidos como `null`/`indisponÃ­vel`, nunca como zero, para nÃ£o distorcer mÃ©dias e agregaÃ§Ãµes.
- **Metas**: ainda nÃ£o estÃ£o no banco. SerÃ£o carregadas via loader do XLSX em sprint futura.
- **Shopee**: integração via exports locais em andamento. Orders e shop-stats têm granularidade diária; ads CSV é distribuído como média diária do período.
- **Compradores (`unique_buyers`/`customers`) — soma diária, não comprador único do intervalo**: `marts.fact_marketplace_daily_performance` guarda `unique_buyers` já deduplicado **dentro de cada dia**, mas os endpoints que agregam um intervalo (`/overview`, `/canais`, `/quality`) fazem `SUM(unique_buyers)` entre os dias do período. Um comprador que compra em 2 dias diferentes do mesmo mês é contado 2 vezes. O Neon não tem uma coluna de identidade do comprador para deduplicar de verdade entre dias (a Gold antiga do ML deduplicava mensalmente via `gold.ml_gestao_mensal`, mas essa lógica não foi portada). Consequência: `ml_unique_buyers`, `shopee_unique_buyers` e `tiktok_customers` (e as métricas derivadas por comprador, como `gmv_per_buyer`) são **estimativas por soma diária**, tendem a **sobrestimar** o comprador único real, e a UI precisa deixar isso explícito (rótulo "soma diária" nos KPIs e tabelas de Gerencial/Canais/Qualidade) em vez de apresentar como comprador único do período.

## Shopee — KPIs mapeados na fase atual

| KPI | Fonte | No banco | No dashboard | Caveat |
|---|---|---|---|---|
| GMV | `Order.all*.xlsx` | ✅ | ✅ Gerencial, Canais, Financeiro | subtotal dos pedidos ativos; cancelados excluídos |
| Pedidos | `Order.all*.xlsx` | ✅ | ✅ Gerencial, Qualidade | pedidos não cancelados |
| Unidades vendidas | `Order.all*.xlsx` | ✅ | ✅ Gerencial, Produtos | soma de quantidade nas linhas SKU ativas |
| Ticket médio | derivado | 🔶 | ✅ Produtos | `gmv / orders` |
| Compradores únicos | shop-stats XLSX | ✅ | ✅ Canais | por usuário comprador |
| Novos compradores | shop-stats XLSX | ✅ | ✅ Canais | |
| Recompra % | shop-stats XLSX | ✅ | ✅ Canais | |
| GMV por comprador | derivado | 🔶 | ✅ Canais | `gmv / unique_buyers` |
| Cancelamentos (n) | `Order.all*.xlsx` | ✅ | ✅ Qualidade | |
| Cancel% | derivado | ✅ | ✅ Qualidade | `canceled_orders / orders` |
| Devoluções (n) | `Order.all*.xlsx` | ✅ | ✅ Qualidade | baseado em status de devolução |
| Devol% | derivado | ✅ | ✅ Qualidade | `returned_orders / orders` |
| Taxas marketplace (R$) | `Order.all*.xlsx` | ✅ | ✅ Financeiro | comissão + taxa de serviço líquidas |
| Taxa % | derivado | ✅ | ✅ Financeiro | `fees / gmv` |
| Valor liquidado | `Order.all*.xlsx` | ✅ | ✅ Financeiro | `Total global` no export |
| Liquidação % | derivado | ✅ | ✅ Financeiro | `settlement / gmv` |
| Visitantes | shop-stats XLSX | ✅ | ✅ Canais | depende da disponibilidade mensal do arquivo |
| Conversão % | shop-stats XLSX | ✅ | ✅ Canais | percentual exportado pela Shopee |
| Ad spend | ads CSV | ✅ | ✅ Financeiro | média diária do período do CSV |
| ROAS | derivado | ✅ | ✅ Financeiro | `ad_revenue / ad_spend` — ad_revenue da Shopee é estimado |
| Frete seller (R$) | `Order.all*.xlsx` | ✅ | ✅ Financeiro | `seller_shipping_cost` |
| Ad impressions / CTR | ads CSV | ✅ | ❌ não exibido ainda | disponível em `fact_marketplace_daily_performance` |
