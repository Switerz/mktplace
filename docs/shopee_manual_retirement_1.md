# SHOPEE-MANUAL-RETIREMENT-1 — o que sai do export manual e o que fica

Data: 2026-10-16. Escopo: as quatro contas com aplicação Shopee — **apice, barbours, lescent, rituaria**.
A **kokeshi fica manual** (não tem aplicação Shopee cadastrada; o app é registrado por loja).
Nenhum export novo foi lido, nenhuma credencial foi usada ou renovada, nada foi escrito em produção.
Este documento traz só estrutura, regras e divergências percentuais — nenhum valor monetário.

## 1. Resposta curta

| pergunta | resposta |
|---|---|
| O que já podemos aposentar? | **Produtos mensais** das 4 contas: já servidos pela API desde o cutover de 13/10. Nada mais pode ser desligado hoje sem perder uma métrica servida. |
| O que pode entrar em shadow agora? | **Família de pedidos** da fato diária: pedidos, unidades, cancelados, concluídos, frete estimado e subtotal. Implementado neste PR (migration 027 + publicador + reconciliação), sem aplicar. |
| O que depende de prova ou de nova permissão? | **Ads** (endpoint existe e lista o tipo de app das 4 lojas; o grant por loja nunca foi provado), **taxas e repasse** (escrow autorizado em 3 contas, `apice` não determinado, não ingerido), **devoluções** (Returns API não ingerida). |
| O que continua manual por inexistência de API? | **Shop-stats**: GMV líquido da tela, visitantes, conversão e compradores (únicos, novos, recorrentes). Sem endpoint para app de loja. **UF regional**: a API mascara o endereço. |

## 2. Fontes manuais e o fluxo atual

`shopee_manual_refresh` (manual, sem agenda) roda, em ordem:

1. `daily_shopee_orders` — `Order.all*.xlsx` → `PATCH_SHOPEE_ORDERS_SQL` → `marts.fact_marketplace_daily_performance` (Neon), audit `shopee_daily`;
2. `daily_shopee_stats` — `*.shopee-shop-stats.*.xlsx` → `PATCH_SHOP_STATS_SQL`, audit `shopee-stats_daily`;
3. `daily_shopee_ads` — `Dados*.csv` → `PATCH_ADS_SQL`, audit `shopee-ads_daily`;
4. `sync_produtos_shopee` — PG local → `marts.fact_shopee_product_monthly` (`source='manual_export'`).

A fato diária **não tem coluna de procedência**: só os três patches manuais a escrevem. Os exports também foram
carregados no Data Mart (`raw/silver.*shopee*_export`, `stg_shopee_shop_stats`, `stg_shopee_ads`), mas essas tabelas
estão em `archive.*` e param em **24/08/2026**. A regional (`gold.marketplace_region_daily`) usa o export de pedidos.

## 3. O que a API entrega hoje (Airflow `goca-se/airflow`)

| capacidade | onde | estado |
|---|---|---|
| Pedidos (`order.get_order_list` / `get_order_detail`) | `shopee_orders_etl`, `0 */6 * * *` → `raw/silver.stg_shopee_orders`, `_order_items` | **ativo**, 4 contas, 31/12/2025 → hoje, carga às 12:02 UTC de 16/10 |
| Catálogo (`product.*`) | `shopee_catalog_etl`, `0 9 * * *` | ativo |
| Gold de produtos | `gold.shopee_product_daily` (dbt) | ativo; alimenta o cutover de produtos |
| Escrow (`payment.get_escrow_detail`) | `shopee_payment_capability_probe` (manual, pausado) | **só probe**; nenhuma tabela |
| Ads (`ads.*`) | `shopee_ads_capability_probe` — **só em `develop`**, nunca disparado | **só probe**, não promovido para `main` |
| AMS (`ams.*`) | `shopee_ams_capability_probe` em `main` (#1992) | probe; cobre só afiliados |
| Shop stats / Business Insights / Returns / Account health | — | nenhum código |

Credencial: Airflow Variable `SHOPEE_TOKENS` (access 4 h, refresh de uso único) e Connection `shopee__<marca>` por loja.
**Nunca** renovar token fora das DAGs: o refresh é de uso único por `shop_id` e derruba a ingestão de produção.

## 4. Documentação oficial (open.shopee.com, lida em 16/10/2026)

| rota | permissão listada | o que entrega | consequência |
|---|---|---|---|
| `v2.ads.get_all_cpc_ads_daily_performance` | Seller In House System, Marketing, Ads Service… | diário real: `expense`, `impression`, `clicks`, `direct_gmv`/`broad_gmv`, `direct_order`/`broad_order`, ROAS | Ads **tem** endpoint; o tipo de app das 4 lojas está na lista. Limites: janela ≤ 1 mês, histórico ≤ 6 meses, `start ≠ end`, data `DD-MM-AAAA`. Só CPC: campanhas **GMS** têm rota própria (`get_gms_campaign_performance`) |
| `v2.payment.get_escrow_list` / `_detail_batch` | Seller In House System, Accounting And Finance… | repasse por pedido: comissão, taxa de serviço, transação, frete | taxas e repasse **têm** endpoint |
| `v2.returns.get_return_list` | Seller In House System, Customer Service… | devoluções por pedido | devolução **tem** endpoint |
| `v2.principal.get_shop_sales_performance_detail` | **Brand Portal Service** (tipo *Principal*) | `sales`, `orders`, `unique_visitors`, conversão | é conta de **dono de marca**, não app de loja; `sales` **inclui** cancelados e devolvidos, sem a quebra → não reproduz o GMV líquido do shop-stats |
| `v2.account_health.get_shop_performance` | Seller In House System… | métricas de saúde (atraso, penalidade) | não é venda |
| `v2.ams.get_shop_performance` | Affiliate Marketing Solution Management | vendas **via afiliados** | recorte, não a loja |
| `v2.business_insights.get_marketing_hot_listing` | — | "Api does not exist" | — |

## 5. Matriz final — fonte manual → métrica → API → cobertura → divergência → ação

Divergência medida em 01/01–12/10/2026 (1.140 dias-marca, maior período comum), API − publicado sobre o publicado.
"Maduro" = jan–jun, em que o export foi tirado depois da maturação dos status.

### 5.1 `shopee_daily` — `Order.all*.xlsx`

| métrica (fato diária) | origem no arquivo | consumidor | API | divergência | estado |
|---|---|---|---|---|---|
| `orders` | `ID do pedido`, status ≠ `Cancelado`, dia de criação | `/overview`, `/brands`, `/trend`, `/canais`, `/qualidade` | `silver.stg_shopee_orders`, status ≠ `cancelled`, `created_date_brt` | **0,00%** maduro; até −3,8% jul–out por maturação | **READY_FOR_SHADOW** (implementado) |
| `units_sold` | `Quantidade` | não lida por endpoint roteado | `stg_shopee_order_items.quantity` | **0,00%** maduro, exceto **01/05**: o publicado tem o DOBRO (apice e barbours) — defeito do export | **READY_FOR_SHADOW** |
| `canceled_orders` | status = `Cancelado` | `/canais`, `/qualidade` | status = `cancelled` | **0,00%** maduro; +3,8% a +28% jul–out (cancelamento tardio) | **READY_FOR_SHADOW** |
| `delivered_orders` | status = `Concluído` | não roteada | status = `completed` | **0,00%** maduro; muito acima jul–out (o export congela antes da conclusão) | **READY_FOR_SHADOW** |
| `cancel_rate_pct` | derivada | `/canais`, `/qualidade` | derivada | segue as duas acima | **READY_FOR_SHADOW** |
| `seller_shipping_cost` | `Valor estimado do frete` | `/financeiro`, `/canais`, DRE frete (`NAO_CONFIRMADO`) | `estimated_shipping_fee` (100% preenchido) | **0,00%** maduro | **READY_FOR_SHADOW** |
| `avg_ticket` (base subtotal) | `Subtotal do produto` | não roteada para Shopee | Σ `item_total` | **0,00%** maduro (mesmo 01/05) | **READY_FOR_SHADOW** (`product_subtotal`) |
| `returned_orders` | `Status da Devolução / Reembolso` não vazio | `/qualidade` (taxa de devolução) | status `to_return` cobre ~5% do publicado; `returns.get_return_list` não ingerida | — | **PARTIAL**: ingerir Returns API (permissão listada; grant não provado) |
| `total_fees` | `Taxa de comissão líquida` + `Taxa de serviço líquida` | `/financeiro`, `/canais`, DRE taxas | escrow (`get_escrow_detail_batch`) | não ingerido | **PARTIAL**: escrow provado em barbours/lescent/rituaria, `apice` NÃO determinado (timeout), `currency` ausente em 2 contas |
| `total_settlement` | `Total global` (doc: "não é settlement") | `/financeiro` | `total_amount` diverge −10% a −15% e não é o mesmo conceito | — | **PARTIAL**: decisão de definição antes de qualquer fonte |
| `avg_fee_pct`, `avg_settlement_pct`, `shipping_pct_of_gmv` | derivadas | não roteadas para Shopee | derivadas | seguem os pais | seguem os pais |
| `unique_buyers` (fallback) | `Nome de usuário` | sobrescrito pelo shop-stats | `buyer_user_id` (pseudônimo por loja) | ver 5.2 | ver 5.2 |

Invariante de controle: **pedidos criados = ativos + cancelados** fecha em **0,00% nos 40 pares marca × mês**,
de janeiro a outubro. Nenhum pedido falta ou sobra de nenhum lado; a divergência de jul–out é só status amadurecendo.
O eixo de data casa no mesmo dia BRT (deslocar um dia leva a divergência a 27%).

### 5.2 `shopee-stats_daily` — `*.shopee-shop-stats.*.xlsx`

| métrica | origem | consumidor | API | estado |
|---|---|---|---|---|
| `gmv` = Vendas − Canceladas − Devolvidas | 3 colunas | **todas as telas** e receita da DRE | nenhuma para app de loja; BrandPortal exige conta Principal e não separa cancelado/devolvido | **NO_API_SOURCE**. Alternativa só por decisão de negócio: GMV de pedidos, +4,21% medido |
| `visitors`, `conversion_rate` | `Visitantes`, `Taxa de Conversão` | `/canais` | BrandPortal (`unique_visitors`), outra permissão e outro app | **NO_API_SOURCE** (app de loja) |
| `unique_buyers`, `new_buyers`, `repeat_buyers`, `repeat_buyer_rate_pct` | `# de compradores…` | `/`, `/canais` | derivável de pedidos, mas **−12% estável** em todos os meses (definição diferente), e novo/recorrente exige histórico anterior a 2026 | **NO_API_SOURCE** (como publicado) |

### 5.3 `shopee-ads_daily` — `Dados*.csv`

| métrica | origem | consumidor | API | divergência esperada | estado |
|---|---|---|---|---|---|
| `ad_spend`, `ad_revenue`, `ad_impressions`, `ad_clicks`, `roas`, `acos_pct`, `ctr_pct`, `cpc` | `Despesas`, `GMV`, `Impressões`, `Cliques` (período **rateado** por dia) | `/`, `/financeiro`, `/canais`, DRE Ads | `ads.get_all_cpc_ads_daily_performance` (+ `get_gms_campaign_performance`) | grão diário real × rateio; CPC × GMS; `broad_gmv` × `direct_gmv`; histórico ≤ 6 meses | **PARTIAL**: promover `shopee_ads_capability_probe` para `main` e disparar; só depois desenhar a ingestão |

### 5.4 Produtos mensais

| métrica | estado |
|---|---|
| `gmv`, `units_sold`, `completed_orders` | **APOSENTADO** para as 4 contas (cutover 13/10, `shopee_product_source_mode = api`) |
| `canceled_orders`, `unique_buyers`, `cancel_rate_pct` | NULL nas linhas `api` por contrato. ⚠️ `performance_service.get_produtos_shopee` converte `canceled_orders` NULL em 0 (`int(_f(...))`); corrigir em gate próprio |
| kokeshi | manual |

### 5.5 Regional (fora do `shopee_manual_refresh`, mas dependente do export)

| métrica | estado |
|---|---|
| UF, cidade | **NO_API_SOURCE**: `recipient_address` vem mascarado (`****`) em 100% dos pedidos da API |

## 6. O que este PR implementa

| arquivo | papel |
|---|---|
| `apps/api/alembic/versions/027_create_fact_shopee_daily_api_shadow.py` | tabela **própria** `marts.fact_shopee_daily_api_shadow` (dia × marca). CHECK de marca (sem kokeshi), `source='api'`, contagens não negativas, subconjuntos ≤ total, `<> 'NaN'`, `run_id` hexadecimal. **Não aplicada.** |
| `pipelines/sync_shopee_daily_api_shadow.py` | Data Mart (só leitura) → shadow. Dry-run por padrão; frescor por conta (≤ 13 h); janela de 90 dias até D-1, recalculada inteira; `is_partial` nos últimos 30 dias; guarda de colapso (< 80% aborta); NO-OP por conteúdo; transação única com lock consultivo e reconciliação pré-commit; recusa se a 027 não existir. |
| `pipelines/reconciliation/shopee_daily_api_vs_manual.py` | reconciliação **só leitura** API × publicado, com o invariante de pedidos criados e a classificação `exato` / `maturacao` / `defeito`. |

**Por que tabela própria e não `source` na fato diária:** a fato diária tem ~15 consumidores e nenhuma procedência.
Linhas `api` nela exigiriam um filtro em todos antes da primeira escrita; esquecer um dobraria o número naquela leitura.
Uma tabela que nenhum consumidor lê torna a dupla contagem impossível por construção (há teste que varre `apps/` atrás de
leitor).

Dry-run medido em 16/10 (só leitura, nada escrito): 1.152 linhas, 4 contas, 01/01–15/10, zero violação de contrato.

## 7. Próximos passos (cada um exige autorização)

1. Revisar e mesclar este PR.
2. `alembic upgrade 027` no Neon; dry-run e depois `--apply --desde 2026-01-01` do shadow.
3. Agendar o shadow (DAG ou orquestrador) e acompanhar a reconciliação por algumas semanas.
4. Decidir o cutover da família de pedidos: precisa de interruptor por métrica na fato diária ou de um consumidor que
   leia o shadow — gate próprio. O export de pedidos **só** pode ser desligado quando taxas, repasse e devoluções também
   tiverem fonte (ou forem retirados da tela por decisão).
5. Ads: promover o probe de `develop` para `main` (exige aprovação do time `Dados`) e disparar na janela segura de token.
6. Escrow: repetir o probe para `apice`; decidir a fonte de moeda; desenhar a ingestão.
7. Shop-stats: decisão de negócio — manter manual ou trocar a definição do GMV para a de pedidos (+4,21%).
