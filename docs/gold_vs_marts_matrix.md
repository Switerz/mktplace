# Gold vs Marts — Matriz de Cobertura de Métricas

Atualizado: 2026-06-24
Validado contra: Neon real (`ep-lively-frost-a6eg1wh2.us-west-2.aws.neon.tech`)

Referência para decisões de migração de `gold.*` (RDS) para `marts.*` (Neon).
Cobre os 8 endpoints migrados para `performance_service.py` e os endpoints que permanecem em `gold_service.py`.

**Legenda:**
- ✅ Igual — campo equivalente, mesma semântica
- ⚠️ Proxy — campo existe no mart mas com diferença de cálculo ou cobertura parcial
- ❌ Ausente — sem equivalente no mart; endpoint permanece no gold_service

---

## 0. Estado real do Neon (2026-06-24)

| marketplace_id | Canal | Linhas | Período | conv_non_zero | avg_conv | GMV total |
|---|---|---|---|---|---|---|
| 1 | TikTok | 890 | 2025-12-26 → 2026-06-21 | 236 | 0.0285 (ratio) | 68.8M |
| 2 | ML | 539 | 2025-12-26 → 2026-06-23 | 0 | NULL | 14.4M |
| 3 | Shopee | 851 | 2026-01-01 → 2026-06-20 | 755 | 3.08 (pct) | 21.2M |

**Nota sobre `conversion_rate`:** campo tem escala inconsistente entre canais.
- TikTok: armazenado como ratio (0–1), ex: `0.031` → `_pct_from_source` multiplica por 100 → 3.1%
- Shopee: armazenado como percentagem (>1), ex: `3.51` → `_pct_from_source` retorna como-está → 3.51%
- ML: NULL — não populado no mart

`_pct_from_source(v)` na `performance_service.py` trata os dois casos correctamente com `abs(v) <= 1`.

---

## 1. Endpoints migrados para Neon (`performance_service.py`)

### 1.1 GMV e pedidos (`get_overview`, `get_brands`, `get_monthly`, `get_daily`, `get_pedidos`)

| Métrica | Fonte gold | Campo mart | Status | Evidência real |
|---|---|---|---|---|
| GMV diário | `gold.tiktok_brand_daily.gmv` / `gold.ml_gestao_diaria.gmv` | `marts.fact.gmv` | ✅ Igual | TikTok 68.8M, ML 14.4M, Shopee 21.2M no Neon |
| Pedidos pagos | `gold.*.paid_orders` | `marts.fact.orders` | ✅ Igual | Campo populado para todos os marketplaces |
| Pedidos cancelados | `gold.*.canceled_orders` | `marts.fact.canceled_orders` | ⚠️ Parcial | **TikTok: 0 em todos os meses** (dez/25–jun/26). ML e Shopee: não validado — ver secção 2 |
| Avg ticket | Calculado (gmv/orders) | `marts.fact.avg_ticket` | ✅ Igual | Campo existe no schema |
| MoM GMV | Calculado Python (2 meses) | Calculado Python | ✅ Igual | Lógica idêntica |
| `gpm` (TikTok) | `gold.tiktok_brand_daily.total_views` | ❌ Ausente | ❌ Ausente | **Confirmado:** coluna `total_views` não existe no mart |

### 1.2 Ads e financeiro (`get_financeiro`, `get_overview`)

| Métrica | Fonte gold | Campo mart | Status | Evidência real |
|---|---|---|---|---|
| `ad_spend`, `ad_revenue` | `gold.ml_gestao_diaria` / `gold.shopee` | `marts.fact.ad_spend`, `.ad_revenue` | ✅ Igual | Colunas existem no schema |
| `ad_clicks`, `ad_impressions` | `gold.ml_gestao_diaria` | `marts.fact.ad_clicks`, `.ad_impressions` | ✅ Igual | Colunas existem |
| `total_settlement`, `total_fees` | `gold.tiktok_brand_daily` | `marts.fact.total_settlement`, `.total_fees` | ✅ Igual | Colunas existem |
| `seller_shipping_cost` | `gold.ml_gestao_diaria` | `marts.fact.seller_shipping_cost` | ✅ Igual | Coluna existe |
| `cos_pct` | `total_fees / gmv` | Recalculado Python | ✅ Igual | |
| `impressions`, `page_views`, `ctr_pct` TikTok | `gold.v_channel_efficiency` | ❌ Ausente | ❌ Ausente | `ctr_pct` existe mas via `ad_clicks/ad_impressions`, não via `v_channel_efficiency` |

### 1.3 Canais e funil (`get_canais`)

| Métrica | Fonte gold | Campo mart | Status | Evidência real |
|---|---|---|---|---|
| `gmv_video`, `gmv_live`, `gmv_card` | `gold.tiktok_brand_daily` | `marts.fact.gmv_video/.live/.card` | ✅ Igual | Colunas existem no schema |
| `visitors` TikTok | `gold.tiktok_brand_daily.visitors` | `marts.fact.visitors` | ⚠️ Parcial | Mai/2026: 215,178 visitantes (TikTok). Mas nem todos os dias têm dados |
| `conversion_rate` TikTok | `gold.tiktok_brand_daily.conversion_rate` | `marts.fact.conversion_rate` | ⚠️ Parcial | **Mai/2026: 5/155 linhas não-zero. AVG=0.031 → 3.1%.** Cobertura parcial mas código correcto |
| `conversion_rate` ML | `gold.ml_gestao_mensal.conversion_rate` | `marts.fact.conversion_rate` | ❌ NULL | **Confirmado:** conv_non_zero=0 para ML em todos os períodos |
| `conversion_rate` Shopee | `shopee.conversion_rate` | `marts.fact.conversion_rate` | ✅ Igual | Mai/2026: AVG=3.51% (755/851 linhas não-zero) |
| `unique_buyers` TikTok | `gold.tiktok_brand_daily.customers` | `marts.fact.unique_buyers` | ✅ Igual | Mai/2026: 233,910 (soma diária) |
| `unique_buyers` ML | `gold.ml_gestao_mensal.unique_buyers` (deduplicado mês) | `marts.fact.unique_buyers` (soma diária) | ⚠️ Proxy | **Mai/2026: 43,841 soma diária** (93 linhas: 3 marcas × 31 dias). Gold deduplicava compradores por mês; mart acumula por dia/marca |
| `unique_buyers` Shopee | exports locais | `marts.fact.unique_buyers` | ✅ Igual | Mai/2026: 98,465 |
| `visitors` ML | `gold.ml_gestao_diaria.visitors` | `marts.fact.visitors` | ❌ NULL | Mai/2026: `visitors=NULL` para ML — não populado no mart |

### 1.4 Qualidade (`get_quality`)

| Métrica | Fonte gold | Campo mart | Status | Evidência real |
|---|---|---|---|---|
| `problem_rate` TikTok | `gold.tiktok_brand_daily.problem_rate` | `marts.fact.problem_rate` | ✅ Igual | Coluna existe no schema |
| `avg_delivery_hours` TikTok | `gold.tiktok_brand_daily.avg_delivery_hours` | `marts.fact.avg_delivery_hours` | ✅ Igual | Coluna existe |
| `tiktok_cancel_rate` | `gold.tiktok_brand_daily.canceled_orders` | `marts.fact.canceled_orders` | ✅ None correcto | **Confirmado:** `canceled_orders=0` para TikTok em TODOS os meses (dez/25–jun/26). Retornar `None` é o comportamento correcto. Não é falta de dados — o pipeline não popula este campo para TikTok |
| `ml_cancel_rate_pct` | `gold.ml_gestao_diaria.cancelled_orders / total` | `canceled / (orders + canceled)` | ✅ Igual | **Confirmado Neon mai/2026:** orders=45,432 · canceled=2,062 → cancel_rate=4.3% |
| `ml_not_delivered_rate_pct` | `gold.ml_gestao_mensal.not_delivered / total_shipments` | `(orders - delivered_orders) / orders` | ⚠️ Proxy | Unidades diferentes: gold usa shipments, mart usa orders. Divergência possível |
| `avg_delivery_days` ML | `gold.ml_gestao_diaria.avg_delivery_days` | `marts.fact.avg_delivery_days` | ✅ Igual | Coluna existe |
| `ml_unique_buyers` (qualidade) | `gold.ml_gestao_mensal.unique_buyers` (deduplicado) | soma diária do mart | ⚠️ Proxy | Mesma situação de `/canais` — ordem de grandeza correcta, sobreestima |
| `shopee_cancel_rate_pct`, `shopee_return_rate_pct` | exports locais | `marts.fact.canceled_orders`, `.returned_orders` | ✅ Igual | **Confirmado Neon mai/2026:** orders=91,157 · canceled=14,639 → cancel_rate=13.8% |

### 1.5 Cancel rate — denominador padronizado

Todos os cálculos em `performance_service.py` usam `canceled / (paid + canceled)`.
- `orders` no mart = pedidos pagos apenas
- `(orders + canceled_orders)` = total de tentativas
- TikTok: `canceled_orders=0` → `cancel_rate=None` (guard explícito no código)

---

## 2. Endpoints que permanecem em `gold_service.py` (RDS)

| Endpoint | Tabela gold usada | Razão de não migrar | Caminho para Neon |
|---|---|---|---|
| `/tempo-real` | `gold.tiktok_shop_hourly` | Mart é diário; sem tabela horária | Criar `marts.fact_tiktok_hourly` ou usar `raw.*` |
| `/brand-detail` | `gold.tiktok_brand_daily` (`total_views`, `active_videos`, `new_videos_posted`, demographics) | Colunas de conteúdo e audiência ausentes no mart | Adicionar colunas ao mart ou criar `marts.fact_tiktok_brand_content_daily` |
| `/brand-detail` (creators) | `gold.tiktok_creator_daily` | Granularidade por criador sem equivalente | Criar `marts.fact_tiktok_creator_daily` |
| `/produtos/ml` e `/summary` | `gold.ml_produto_ranking` | Sem equivalente no mart | Criar `marts.fact_ml_produto_ranking` |
| `/produtos/tiktok` | `gold.tiktok_product_daily` | Sem equivalente no mart | Criar `marts.fact_tiktok_product_daily` |
| `/inteligencia`, `/operacoes` | `gold.ml_cross_company_summary` e outros | Lógica multi-company específica | Avaliar após estabilizar MVP |

---

## 3. Métricas com impacto visível no dashboard

| Métrica | Endpoint | Retorno actual | Impacto real |
|---|---|---|---|
| `gpm` | `/brands` | `None` | Campo vazio — confirmado: `total_views` não existe no mart |
| `tiktok_cancel_rate` | `/quality` | `None` | **Correcto** — mart não tem cobertura confiável de cancelamento TikTok; retornar `None` evita exibir falso 0% |
| `ml_conversion_rate` | `/canais` | `None` | Confirmado: `conversion_rate=NULL` para ML em todos os períodos |
| `ml_unique_buyers` (deduplicado) | `/overview`, `/canais`, `/quality` | Soma diária (~43k em mai/2026) | Sobreestima vs. gold — ordem de grandeza correcta |
| `ml_not_delivered_rate_pct` | `/quality` | Proxy `orders - delivered` | Pode divergir da métrica logística do gold |

---

## 4. Ambiente local

`apps/api/.env` foi alinhado com o `.env` raiz — `DATABASE_URL` aponta para Neon.
Esse ficheiro é local e não deve ser commitado (está em `.gitignore`).

---

## 5. Validação executada no Neon (2026-06-24)

Query executada:
```sql
SELECT marketplace_id,
       DATE_TRUNC('month', date)::date AS mes,
       SUM(orders) AS total_orders,
       SUM(canceled_orders) AS total_canceled
FROM marts.fact_marketplace_daily_performance
GROUP BY 1, 2 ORDER BY 1, 2;
```

Resultados principais:

| Canal | Período | orders | canceled |
|---|---|---|---|
| TikTok | dez/25 – jun/26 (todos os meses) | — | **0** (sem cobertura) |
| ML | mai/2026 | 45,432 | 2,062 |
| Shopee | mai/2026 | 91,157 | 14,639 |

Conclusões:
- `tiktok_cancel_rate = None` — confirmado correcto. O mart não tem cobertura confiável de cancelamento TikTok; retornar `None` evita exibir falso 0%.
- `ml_cancel_rate` mai/2026: 2,062 / (45,432 + 2,062) = **4.3%** ✅
- `shopee_cancel_rate` mai/2026: 14,639 / (91,157 + 14,639) = **13.8%** ✅
