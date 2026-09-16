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
| Faturamento bruto (GMV) | Soma dos valores brutos dos pedidos antes de descontos e taxas | `SUM(total_amount)` | dia/mÃªs/brand | âœ… `gmv` (ver §TikTok â€” GMV comercial) | âœ… `gmv` |
| Faturamento lÃ­quido | GMV menos taxas e descontos | `gmv - total_fees - descontos` | dia/brand | âœ… `total_settlement` | â“ (verificar ml_billing) |
| Pedidos | Contagem de pedidos | `COUNT(order_id)` | dia/brand | âœ… `orders` | âœ… `paid_orders` |
| Unidades vendidas | Soma de itens vendidos | `SUM(quantity)` | dia/brand | âœ… `items_sold` | âœ… `total_units` |
| Ticket mÃ©dio | GMV / pedidos, **da mesma populaÃ§Ã£o** | `gmv / orders` | dia/brand | âœ… `avg_ticket` (ver §TikTok â€” GMV comercial) | âœ… `avg_ticket` |
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

## TikTok â€” GMV comercial (Gate DQ-TK1, 2026-08-25)

Contrato vigente do headline TikTok. Substitui o Gate R2/R2.1.

**Headline** â€” `gmv` = `SUM(total_amount)` dos pedidos da **populaÃ§Ã£o comercial**
em `raw.tiktok_shop_orders`, dedup por `order_id`.

- **Inclui o frete pago pelo comprador.** DecisÃ£o do dono do nÃºmero: Ã© valor
  faturado, serÃ¡ descontado no DRE, mas compÃµe o bruto.
- **PopulaÃ§Ã£o comercial** (`COMMERCIAL_ORDER_STATUSES`): `COMPLETED`,
  `DELIVERED`, `IN_TRANSIT`, `AWAITING_COLLECTION`, `AWAITING_SHIPMENT`.
- **Fora** (`NON_COMMERCIAL_ORDER_STATUSES`): `UNPAID`, `CANCELLED`, `ON_HOLD`.
- `AWAITING_COLLECTION`/`AWAITING_SHIPMENT` entraram com prova dupla: 100% com
  `paid_at` preenchido em 01â€“24/08/2026 (3.876/3.876 e 4/4, contra 0/611 em
  `UNPAID`), e o contrato de status em `data_contracts.md` Â§2 que descreve
  `AWAITING_SHIPMENT` como "Pago, aguardando envio pelo seller".
- **Ressalva de `ON_HOLD`**: o dado mostra 4/4 com `paid_at` â€” a exclusÃ£o Ã©
  decisÃ£o de negÃ³cio, nÃ£o do dado. Volume imaterial (R$ 384,42 em 24 dias).
  Revisar se crescer.

**Pedidos e ticket** â€” `orders` Ã© a contagem da **mesma** populaÃ§Ã£o comercial;
`avg_ticket` = `gmv / orders` dessa mesma populaÃ§Ã£o. O relatÃ³rio de conteÃºdo da
Gold continua exposto como `content_orders` e **nunca** Ã© denominador do ticket
comercial â€” era o que o Gate R2.1 fazia, e em 24/08/2026 produziu um ticket de
R$ 8,64 contra os R$ 47 tÃ­picos.

**ResÃ­duo conhecido de 0,19%** â€” `total_amount` fica R$ 76.297,53 acima de
`sub_total + shipping_fee` em 01â€“24/08 (0,90% do GMV). `handling_fee` explica
R$ 60.269,26; a identidade `total_amount = sub_total + shipping_fee +
handling_fee` fecha em 146.861 de 149.784 pedidos (98,05%). Sobram
**R$ 16.028,27 (0,19% do GMV)** em 2.923 pedidos sem coluna que os explique.
Aceito como parte do total da plataforma. **NÃ£o ratear e nÃ£o explicar
artificialmente.**

**Mix de conteÃºdo tem base prÃ³pria** â€” `gmv_video + gmv_live + gmv_card` mede
atribuiÃ§Ã£o de conteÃºdo, nÃ£o pedido/dia, e **nÃ£o fecha com o headline** (em
ago/2026 ficou 0,65% a 4,75% acima, por marca). Shares usam a soma dos trÃªs
como denominador e se chamam "mix de conteÃºdo" â€” **nunca** "share das vendas
totais".

**Cancelamento: MEDIDO na Raw** â€” `canceled_orders` Ã© a contagem de
pedidos com status `CANCELLED` na Raw deduplicada por `order_id` (37.598 em
01â€”24/08/2026). Nunca lido da Gold, que grava 0 literal em 120/120 linhas
do mesmo perÃ­odo â€” um zero comprovadamente falso. `CANCELLED`
segue fora de `gmv` e de `orders`.

**Taxa de cancelamento** â€” `cancel_rate_pct` =
`canceled / (comercial + canceled) * 100`, arredondada em 2 casas. Mesmo
contrato jÃ¡ adotado pela Torre. Denominador zero => **NULL**: sem pedido
resolvido no dia nÃ£o existe taxa, e 0% seria uma afirmaÃ§Ã£o falsa.

**Devolvidos, reembolsados e taxa de problemas = NULL** â€” nÃ£o existe
nenhum status de devoluÃ§Ã£o ou reembolso em toda a histÃ³ria da Raw, e
`problem_rate` depende dos dois. NÃ£o sÃ£o dÃ­vida futura: sÃ£o
ausÃªncia de fonte hoje, e ausÃªncia nunca vira zero. DerivÃ¡-los apenas
do cancelamento mudaria a definiÃ§Ã£o dos indicadores.

**Dias recentes sÃ£o provisÃ³rios** â€” o status de um pedido amadurece por dias
(mediana 5,1 atÃ© `DELIVERED`, p90 8,3; estabiliza em ~8 dias). A janela recente
Ã© **reafirmada** a cada rodada pelo lookback de 10 dias
(`MIN_INCREMENTAL_LOOKBACK_DAYS`, piso no conector + `--days 10` no
orquestrador). Nunca reduzir. Em 24/08/2026, sÃ³ 37,5% dos pedidos do dia
estavam maduros.

**SÃ©rie histÃ³rica precisa ser recalculada integralmente** â€” a mudanÃ§a de
definiÃ§Ã£o eleva o GMV em **~+5,89%** (medido em 01â€“24/08/2026: 7.978.785,08 â†’
8.449.073,43). ComparaÃ§Ãµes mÃªs a mÃªs sÃ£o invÃ¡lidas atÃ© o backfill completo.

---

## TikTok â€” KPIs especÃ­ficos de conteÃºdo

| KPI | DefiniÃ§Ã£o | Coluna | DisponÃ­vel |
|---|---|---|---|
| GMV via vÃ­deo | Receita gerada por vÃ­deos | `gmv_video` | âœ… |
| GMV via live | Receita gerada em lives | `gmv_live` | âœ… |
| GMV via card | Receita via vitrine/card | `gmv_card` | âœ… |
| % mix de conteÃºdo vÃ­deo | `gmv_video / (gmv_video + gmv_live + gmv_card)` â€” **base prÃ³pria, nunca `gmv`** | `pct_gmv_video` | âœ… |
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
| Pedidos cancelados | Pedidos com status CANCELLED | COUNT(order_id) na Raw deduplicada | dia/brand | âœ… `canceled_orders` (medido na **Raw**, nao na Gold) | âœ… `cancelled_orders` |
| Taxa de cancelamento | Cancelados sobre os pedidos colocados que se resolveram | `canceled / (comercial + canceled) * 100`, 2 casas, NULL se denominador = 0 | dia/brand | âœ… `cancel_rate_pct` | âœ… `cancel_rate_pct` |
| Pedidos devolvidos | â€” | â€” | dia/brand | âŒ **NULL** - nenhum status de devolucao na Raw | â“ |
| Pedidos reembolsados | â€” | â€” | dia/brand | âŒ **NULL** - nenhum status de reembolso na Raw | â“ |
| Taxa de problemas | (cancelados + devolvidos + reembolsados) / total | â€” | dia/brand | âŒ **NULL** - depende de devolucao/reembolso, sem fonte | â“ |
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
| GMV | shop-stats XLSX | ✅ | ✅ Gerencial, Canais, Financeiro | `Vendas (BRL) - Vendas Canceladas - Vendas Devolvidas/Reembolsadas` (shop-stats é a fonte autoritativa do GMV desde o Gate R2.1; `Order.all*.xlsx` segue sendo fonte de Pedidos/Unidades) |
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

## Mercado Livre — Fulfillment (Gate FULL-1A, 2026-09-15)

Fonte unica: `marts.fact_ml_fulfillment_daily` e
`marts.fact_ml_fulfillment_listing_daily`. Contrato completo em
`docs/data_contracts.md` secao 8. **Nenhum KPI aqui mede estoque.**

| KPI | Formula | Grao | Ausencia |
|---|---|---|---|
| **`share_full_gmv`** (principal) | `paid_gmv(full) / paid_gmv(total)` | marca x dia | `NULL` se denominador 0 |
| `share_full_orders` | `paid_orders(full) / paid_orders(total)` | marca x dia | `NULL` se denominador 0 |
| `share_full_units` | `paid_units(full) / paid_units(total)` | marca x dia | `NULL` se denominador 0 |
| `cancellation_rate` | `cancelled_orders / eligible_orders` | marca x classe x dia | `NULL` se denominador 0 |
| `handling_avg` | `handling_seconds_sum / handling_sample_count` | **pedido** x classe | `NULL` se amostra 0 |
| `delivery_avg` | `delivery_seconds_sum / delivery_sample_count` | **pedido** x classe | `NULL` se amostra 0 |
| `listings_by_class` | `full_only` / `mixed` / `non_full_only` na janela | listing x janela | listing sem venda nao classifica |
| `migration_opportunity` | `paid_gmv` nao-Full por listing | listing x janela | listing so-Full nao e oportunidade |

Definicoes, filtros e limitacoes:

- **Competencia:** `date_created` do PEDIDO, para TODAS as metricas — inclusive
  `handling` e `delivery` (corrigido no FULL-1A-R; antes usavam a data do envio).
  Uma `ref_date`, uma coorte.
- **Denominador dos shares:** `full + non_full`. `unknown` fica FORA — nao e Full
  nem nao-Full, e conta-lo faria uma lacuna de dado parecer queda operacional.
- **`sample_count` mede CENSURA:** pedido ainda sem despacho ou sem entrega fica
  fora da amostra, nunca entra como tempo zero. Em agosto: 97,3% de cobertura de
  handling e 96,5% de delivery no Full.
- **Elegivel:** todos os status criados no dia (denominador do cancelamento).
- **Pago:** `status = 'paid'` (populacao de GMV e unidades). Frete fora.
- **Media nunca e' armazenada pronta:** a fato guarda soma e amostra, para que a
  reagregacao entre dias e marcas continue correta.
- **`NULL` nunca e' renderizado como zero.** Denominador zero significa "nao ha o
  que dividir", nao "zero de Full".
- **Refresh:** `manual_snapshot`. Sem Scheduler e sem Airflow neste gate.

Valores de referencia — agosto/2026, no grao publicado pela fato:

    share_full_gmv      80,61%      (reconcilia com a planilha, 8/8 marcas)
    share_full_orders   81,88%      OFICIAL (81,18% e grao de ENVIO, nao e KPI)
    share_full_units    82,05%      OFICIAL (81,98% e grao de ENVIO, nao e KPI)
    cancelamento Full       4,0798% (reconcilia: 4,07% truncado)
    cancelamento nao-Full   4,3594% (planilha mostra 4,26% — DIVERGENCIA ABERTA)
    handling            28,67 h Full x 71,83 h nao-Full   (coorte do pedido)
    entrega             2,65 d Full x 4,89 d nao-Full     (coorte do pedido)
    partially_refunded  21 Full + 16 nao-Full, em other_orders, fora do GMV
    listings            276 so-Full, 223 mistos, 90 so-nao-Full


## Mercado Livre — Full: superfície servida (Gate FULL-1D, 2026-09-16)

O Full do Mercado Livre deixou de ser apenas fato materializada e passou a ser
**tela**: `/full-ml`, no grupo Operações da navegação.

### O que a superfície é, e o que não é

Mede **modalidade logística do envio** — por onde o pedido saiu. **Não mede
estoque.** Não há disponibilidade, cobertura em dias nem ruptura, e a ausência é
decisão medida: o Gate FULL-0R provou correlação de −0,006 entre a variação
diária de `available_quantity` e as vendas em anúncios exclusivamente Full.

Também **não é expedição**: a frente de Expedição (migration 018) trata de outro
objeto e vive em outro lugar.

### As três classes

| classe | significado |
|---|---|
| `full` | `logistic_type = 'fulfillment'` |
| `non_full` | qualquer outra modalidade registrada — **não é sinônimo de cross-docking** |
| `unknown` | envio ausente ou `logistic_type` nulo |

`unknown` fica **fora do denominador** dos três shares: não é Full nem não-Full,
e contá-lo faria uma lacuna de dado parecer queda operacional. Continua somando
nos totais absolutos e aparece em alerta próprio na tela.

A composição real do `non_full` fica no bloco por tipo logístico, com o rótulo
bruto preservado — inclusive `xd_drop_off`, `self_service` e `drop_off`, extintos
em 2026.

### Métricas exibidas

Todas vêm prontas da API. **A tela não recalcula nada** — a única divisão feita
no cliente é o share **diário** da série, porque a API entrega o share agregado
do período, e ela usa a mesma regra do backend (denominador `full + non_full`).

`share_full_gmv` · `share_full_orders` · `share_full_units` · GMV, pedidos e
unidades por classe · `cancellation_rate` por classe · `handling` e `delivery`
com o tamanho da amostra · classificação de anúncios (só-Full / mistos /
só-não-Full) · maior GMV fora do Full por anúncio.

`sample_count` é **censura**, não decoração: pedido ainda sem despacho ou sem
entrega fica fora da amostra, nunca entra como tempo zero.

### Fonte, janela e limites

- Fonte: `marts.fact_ml_fulfillment_daily` e `..._listing_daily`, via
  `GET /api/v1/performance/ml-fulfillment`.
- Série publicada: **01/08/2025 em diante**.
- **Maio a julho de 2025 não são cobertos** e a tela diz isso: a fonte tem 1.330
  pedidos pagos sem item nesse intervalo, e publicá-los gravaria venda sem
  unidade.
- **Teto de 366 dias por consulta** (`MAX_RANGE_DAYS`, guardrail global da
  Torre). A fato publica mais de 400 dias: o histórico completo existe e precisa
  ser consultado em janelas. A tela barra a janela inválida **antes** da
  requisição, para o corpo técnico do 422 nunca chegar ao usuário.
- **D0 e futuro bloqueados**: a fato só publica até D−1.

### Estado operacional

`load_mode = manual_snapshot`, e a tela exibe isso em aviso permanente. **A
automação diária e o rebuild periódico pertencem à frente de DAG e ainda não
existem** — o dado só avança quando alguém executa o sync.

### Dinheiro chega como string

O backend serializa `Decimal` como **string** (`"4526767.38"`). Os tipos do
frontend refletem isso, e a conversão acontece num único ponto (`parseGmv`).
Tipar como número compilaria e produziria `NaN` em produção.

### Referência de agosto/2026

    share_full_gmv      80,61%
    share_full_orders   81,88%
    share_full_units    82,05%
    anúncios            271 só-Full · 222 mistos · 90 só-não-Full

Validados na tela contra a API local, com o Neon já publicado.


## Shopee - FBS: desempenho por pedido (Gate FULL-SH-1A-R, 2026-09-16)

Mede **por onde o pedido saiu**: fulfillment da Shopee (`fbs`) contra envio
pelo proprio vendedor (`seller`). A classe vem de `fulfillment_flag`,
**observada em cada pedido** - nunca da configuracao atual do anuncio.

### KPIs

| KPI | formula | denominador |
|---|---|---|
| `gross_gmv` | `SUM(item_total)` dos itens de pedidos nao cancelados | - |
| `share_fbs_gmv` | `gmv(fbs) / gmv(fbs+seller)` | so as duas classes |
| `share_fbs_orders` | `eligible(fbs) / eligible(fbs+seller)` | idem |
| `share_fbs_units` | `units(fbs) / units(fbs+seller)` | idem |
| `cancellation_rate` | `cancelled / created` | **todos os criados** |
| handling medio | `handling_seconds_sum / handling_sample_count` | amostra censurada |

Shares sao calculados em `Decimal`, **sem arredondamento** - quem formata
decide a precisao.

### Referencia de agosto/2026 (definicao ratificada)

| marca | GMV total | GMV FBS | share FBS |
|---|---:|---:|---:|
| barbours | 944.358,30 | 762.898,83 | 80,7849% |
| lescent | 320.670,05 | 192.639,90 | 60,0742% |
| rituaria | 413.010,35 | 235.269,61 | 56,9646% |
| apice | 275.234,00 | 0,00 | **0%** |
| kokeshi | - | - | **fora da cobertura** |

Os valores diferem das referencias do FULL-SH-0 em exatamente o GMV de
`to_return`, que a definicao ratificada **inclui** no bruto: +604,42 (apice),
+1.946,64 (barbours), +595,55 (lescent), +48,93 (rituaria). Removendo os
recortes, a identidade e **0,00 em todas as marcas**.

### Valores SUBSTITUIDOS - nao usar

Os numeros do FULL-SH-0 baseados em `total_amount` estao **obsoletos** e nao
devem continuar em documentacao, teste ou tela:

    barbours  GMV FBS 685.209,36  seller 169.702,59  share 80,15%   OBSOLETO
    lescent   GMV FBS 179.482,57  seller 126.792,77  share 58,60%   OBSOLETO
    rituaria  GMV FBS 214.282,35  seller 168.540,35  share 55,97%   OBSOLETO

`total_amount` mede **1,6% a 9,4% abaixo** do canonico da Torre.

### Limitacoes declaradas

- Serie comeca em **2026-01-01**.
- **Kokeshi fora da API** - a maior marca Shopee por volume nao aparece.
- **Sem entrega e sem devolucao** na API: handling vai ate a **coleta**.
- GMV e **bruto**, inclui `to_return` e `unpaid`.
- Total das quatro contas = "Shopee - cobertura API", nunca "Shopee total".


## Shopee - FBS: API de leitura (Gate FULL-SH-1C, 2026-09-16)

`GET /api/v1/performance/shopee-fbs`, **atras de `SHOPEE_FBS_ENABLED`
(default false)**.

### Formulas servidas

| KPI | formula |
|---|---|
| `gross_gmv` | SUM(gross_gmv) dos dias da janela |
| `share_fbs_gmv` | gmv(fbs) / gmv(fbs+seller), **apos** somar |
| `share_fbs_orders` | eligible(fbs) / eligible(fbs+seller) |
| `share_fbs_units` | units(fbs) / units(fbs+seller) |
| `cancellation_rate` | cancelled_orders / **created_orders** |
| handling medio | SUM(seconds_sum) / SUM(sample_count) |
| `coverage_ratio` | sample_count / eligible_orders |

### Quebras publicadas

`by_class` (fbs, seller) - `by_brand` - `by_account` (com watermark proprio
por conta) - `daily` (por dia e classe, **so' medidas aditivas**).

### Reconciliacao API x Neon - agosto/2026

| marca | GMV total | share FBS | to_return |
|---|---:|---:|---:|
| barbours | 944.358,30 | 0,807848917 | 1.946,64 |
| lescent | 320.670,05 | 0,600741791 | 595,55 |
| rituaria | 413.010,35 | 0,569645797 | 48,93 |
| apice | 275.234,00 | **0,0** | 604,42 |
| kokeshi | - | **fora da cobertura** | - |

52 verificacoes API x SQL, todas iguais. FBS + seller fecha GMV, pedidos e
unidades.

### Limitacoes declaradas no payload

- Cobertura PARCIAL: quatro contas; **Kokeshi fora da API**.
- Carga **manual**, sem DAG nem agenda.
- `closed_day`: D0 nao materializado, projetado nem fabricado.
- Serie a partir de **2026-01-01**.
- Handling vai ate' a **coleta**; sem entrega e sem devolucao.
- GMV e' **bruto**, inclui `to_return` e `unpaid`.
