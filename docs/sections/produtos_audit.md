# Auditoria — Aba Produtos

Criado: 2026-06-26
Referência: Mai/2026
Endpoint: `GET /api/v1/performance/produtos/{ml|tiktok|shopee}`

---

## 1. Objetivo da aba

A aba Produtos deve responder, por marketplace e marca:

- Quais produtos geram mais GMV e qual o perfil de velocidade de receita;
- Quais têm ROAS alto/baixo e merecem escalar ou pausar ads;
- Quais têm taxa de cancelamento ou problema elevados;
- Para TikTok: de qual canal (vídeo, live, card) vem o GMV de cada produto;
- Para Shopee: desempenho por variação e SKU.

---

## 2. Componentes e endpoints

| Camada | Arquivo |
|---|---|
| Frontend | `apps/web/app/produtos/page.tsx` |
| API client | `apps/web/src/lib/api-client.ts` (`fetchProdutosML`, `fetchProdutosTikTok`, `fetchProdutosShopee`, `fetchProdutosMLSummary`) |
| Router | `apps/api/app/routers/performance.py` → `/produtos/ml`, `/produtos/ml/summary`, `/produtos/tiktok`, `/produtos/shopee` |
| Service | `apps/api/app/services/gold_service.py` → `get_produtos_ml`, `get_produtos_ml_summary`, `get_produtos_tiktok`, `get_produtos_shopee` |
| Schema | `apps/api/app/schemas/performance.py` (`ProdutoMLRow`, `ProdutoTikTokRow`, `ProdutoShopeeRow`, `ProdutosMLResponse`, `ProdutosTikTokResponse`, `ProdutosShopeeResponse`, `ProdutosMLSummaryResponse`) |

### Fontes de dados por aba

| Tab | Tabela fonte | Engine | Observação |
|---|---|---|---|
| Mercado Livre | `gold.ml_produto_ranking` | RDS (via `DATAMART_DATABASE_URL`) | Ranking estático — sem filtro de período |
| ML Summary (Pareto) | `gold.ml_produto_ranking` | RDS | Idem |
| TikTok | `gold.tiktok_product_daily` | RDS | Com filtro de período (year/month) |
| Shopee | `marts.fact_shopee_product_monthly` | Neon (via `DATABASE_URL`) | **Tabela ausente no Neon de produção** |

---

## 3. Inventário de métricas — campo a campo

### 3.1 Mercado Livre (`gold.ml_produto_ranking`)

| Campo | Definição de negócio | Fonte | Grain | Status | Risco |
|---|---|---|---|---|---|
| `gross_revenue` | GMV do produto no período do ranking | `gold.ml_produto_ranking.gross_revenue` | por produto | Confiável com ressalva | Período do ranking não exposto na UI — usuário não sabe a qual mês se refere |
| `units_sold` | Unidades vendidas | `gold.ml_produto_ranking.units_sold` | por produto | Confiável | — |
| `unique_buyers` | Compradores únicos | `gold.ml_produto_ranking.unique_buyers` | por produto | Confiável | — |
| `cancel_rate_pct` | Taxa de cancelamento | `gold.ml_produto_ranking.cancel_rate_pct` | por produto | Confiável | — |
| `pareto_bucket` | Classificação ABC (A/B/C/D) | Pré-calculado no gold | por produto | Confiável | Bucket muda a cada atualização do ranking — sem data de referência exposta |
| `revenue_velocity` | Velocidade de receita (high/medium/low/zero) | Pré-calculado no gold | por produto | Confiável | Definição do threshold não documentada |
| `ad_roas` | ROAS do produto (ad_revenue / ad_spend) | Pré-calculado no gold | por produto | Confiável | NULL quando sem ads |
| `ad_acos_pct` | ACOS (ad_spend / ad_revenue × 100) | Pré-calculado no gold | por produto | Confiável | NULL quando sem ads |
| `ad_efficiency` | Classificação de eficiência (star/efficient/marginal/inefficient/no_ads/no_return) | Pré-calculado no gold | por produto | Confiável | Thresholds não documentados neste audit |
| `action_signal` | Sinal de ação gerado pela lógica do pipeline | Pré-calculado no gold | por produto | Confiável | 6 valores fixos mapeados na UI |
| `estimated_margin` | Margem estimada do produto | Pré-calculado no gold | por produto | Proxy | Campo presente no schema mas **não exibido na UI**; cálculo desconhecido — não validar como número confiável sem checar a lógica do gold |
| `revenue_share_pct` | Participação no GMV total (%) | Pré-calculado no gold | por produto | Confiável | — |
| `product_status` | Status do produto (vende+anunciado / orgânico / gasta ads sem venda / inativo) | Pré-calculado no gold | por produto | Confiável | — |

**Problema principal: ausência de dimensão temporal no ranking ML.**
A tabela `gold.ml_produto_ranking` é um ranking sem data de corte exposta para o usuário. A UI do ML não tem `PeriodSelector`, ao contrário das abas TikTok e Shopee. O usuário não sabe a qual período os dados se referem. Isso pode induzir decisões com base em dados desatualizados.

### 3.2 TikTok (`gold.tiktok_product_daily`)

| Campo | Definição de negócio | Fonte | Grain | Status | Risco |
|---|---|---|---|---|---|
| `gmv` | GMV do produto no mês | `SUM(gmv)` por product_id | mensal/produto | Confiável | — |
| `orders` | Pedidos pagos | `SUM(orders)` | mensal/produto | Confiável | — |
| `items_sold` | Unidades vendidas | `SUM(items_sold)` | mensal/produto | Confiável | — |
| `pct_gmv_video` | % GMV via vídeo | `SUM(gmv_video)/SUM(gmv)×100` | mensal/produto | Confiável | Pode ser NULL se GMV=0 |
| `pct_gmv_live` | % GMV via live | `SUM(gmv_live)/SUM(gmv)×100` | mensal/produto | Confiável | Idem |
| `pct_gmv_card` | % GMV via product card | `SUM(gmv_product_card)/SUM(gmv)×100` | mensal/produto | Confiável | Os três juntos podem somar < 100% (existe "other"); o componente `AttributionBar` tem segmento cinza para o restante — correto |
| `problem_rate` | Taxa de problemas (cancelados+devolvidos+reembolsados) / (pedidos+cancelados+devolvidos+reembolsados) | Calculado Python | mensal/produto | Proxy | Denominador assume `orders` = pagos. Se a tabela gold já exclui cancelados de `orders`, o denominador pode estar subestimado |
| `rating_avg` | Média ponderada de avaliações | `SUM(rating_avg × total_ratings)/SUM(total_ratings)` | mensal/produto | Confiável | NULL quando sem avaliações |
| `total_ratings` | Total de avaliações no período | `SUM(total_ratings)` | mensal/produto | Confiável | — |

### 3.3 Shopee (`marts.fact_shopee_product_monthly`)

| Campo | Definição de negócio | Fonte | Grain | Status | Risco |
|---|---|---|---|---|---|
| `gmv` | GMV do produto/variação no mês | `marts.fact_shopee_product_monthly.gmv` | mensal/SKU | **Ausente em produção** | Tabela não existe no Neon remoto |
| `units_sold` | Unidades vendidas | idem | mensal/SKU | **Ausente em produção** | Idem |
| `completed_orders` → `orders` | Pedidos concluídos | idem | mensal/SKU | **Ausente em produção** | Idem |
| `cancel_rate_pct` | Taxa de cancelamento | idem | mensal/SKU | **Ausente em produção** | Idem |
| `avg_price` | Ticket médio (GMV/unidades) | idem | mensal/SKU | **Ausente em produção** | Idem |
| `variation_name` | Nome da variação do produto | idem | mensal/SKU | **Ausente em produção** | Idem |
| `sku_ref` | SKU de referência interno | idem | mensal/SKU | **Ausente em produção** | Idem |

---

## 4. Bugs e incoerências encontrados

### Bug 1 — Tabela `marts.fact_shopee_product_monthly` ausente no Neon de produção (RESOLVIDO em 2026-07-01)

**Severidade:** Alta — endpoint retornava erro 500 em produção

**O que acontecia:**
- O endpoint `/produtos/shopee` usa `get_db()` (Neon) e consulta `marts.fact_shopee_product_monthly`
- A tabela existia apenas no banco PostgreSQL portátil local, criado em 2026-06-23 como parte da Sprint Shopee
- O Neon de produção não tinha a tabela — a query lançava `UndefinedTable` → 500

**Status atual (confirmado por diagnóstico somente leitura em 2026-07-01):**
- Migration `004_create_product_tables.py` criou `fact_shopee_product_monthly`, `fact_ml_produto_ranking` e `fact_tiktok_product_daily` no Neon
- `pipelines/sync_produtos.py` popula as três tabelas a partir de PG local (Shopee) e RDS (ML/TikTok)
- Neon confirmado com 5.228 linhas em `fact_shopee_product_monthly` (idêntico ao PG local), 1.326 em `fact_ml_produto_ranking`, 170.806 em `fact_tiktok_product_daily`
- `apps/api/app/services/performance_service.py` já implementa `get_produtos_shopee/ml/tiktok` lendo exclusivamente do Neon
- **Este bug está resolvido.** Ver Bug 3 abaixo para o problema de qualidade de dados descoberto na mesma tabela.

### Bug 2 — SQL injection em `action_signal` (CORRIGIDO)

**Severidade:** Média — vetor de injeção SQL no endpoint `/produtos/ml`

**O que era:**
- `action_signal` era interpolado diretamente no SQL: `filters.append(f"action_signal = '{action_signal}'")`
- Parâmetro não validado no router (brand, pareto_bucket, product_status e revenue_velocity tinham whitelist; action_signal não)
- Um usuário mal-intencionado podia enviar `action_signal='; DROP TABLE gold.ml_produto_ranking; --` diretamente via URL

**Correção aplicada (commit desta sessão):**
- Adicionado `VALID_ML_ACTION_SIGNALS` com os 6 valores permitidos em `apps/api/app/routers/performance.py`
- Validação antes de chamar o service: `if action_signal and action_signal not in VALID_ML_ACTION_SIGNALS: raise HTTPException(422)`

### Bug 3 — `ref_month` futuro em `fact_shopee_product_monthly` (RESOLVIDO em 2026-07-01)

**Severidade:** Alta — ~42% das linhas da tabela (2.206 de 5.228, confirmado por `pipelines/reconciliation/check_sources_vs_neon.py --only integrity`) tinham `ref_month` em jul–dez/2026, impossível dado que os exports Shopee só cobrem jan–mai/2026 (não jun, como se supunha antes da investigação) e a data de hoje é 2026-07-01.

**Causa raiz:** `apps/api/etl/load_shopee_products.py` (linha do parse de `order_date`) usava:
```python
df["order_date"] = pd.to_datetime(df["order_date"], dayfirst=True, errors="coerce")
```
Os exports Shopee trazem "Data de criação do pedido" em formato ISO não-ambíguo (`"YYYY-MM-DD HH:MM"`, confirmado em 85/85 arquivos `.xlsx` de `shopee/*/Order.all*.xlsx`). Mesmo assim, `dayfirst=True` faz o parser (via dateutil) inverter dia/mês nesse formato sempre que o dia de origem é ≤ 12 — ex.: `"2026-01-12 08:54"` (12 de janeiro) é lido como `2026-12-01` (1º de dezembro). Como isso ocorre para qualquer pedido feito entre os dias 1 e 12 de qualquer mês real (jan–jun/2026), o resultado é uma distribuição quase uniforme de `ref_month` espalhada pelos 12 meses do ano — exatamente o padrão observado (403–460 linhas por mês, incluindo jul–dez/2026).

**Evidência (reproduzida e testada):**
- Teste automatizado: `apps/api/etl/tests/test_load_shopee_products_dates.py`
- Amostra real: `shopee/apice/Order.all.20260101_20260131.xlsx` — linha com raw string `"2026-01-12 08:54"` virava `Timestamp("2026-12-01 08:54:00")` com o parser antigo
- Todos os 85 arquivos `Order.all*.xlsx` no repositório usam o mesmo formato ISO — não há mistura de formatos que justificasse `dayfirst=True`

**Correção de código:**
```python
df["order_date"] = pd.to_datetime(df["order_date"], format="%Y-%m-%d %H:%M", errors="coerce")
```
em `apps/api/etl/load_shopee_products.py`.

**Correção de dados executada em 2026-07-01** via `pipelines/reconciliation/fix_shopee_product_dates.py` (backup timestamped em local e Neon → staging reprocessada dos 85 arquivos → validação cruzada contra `fact_marketplace_daily_performance` → substituição transacional local e Neon):

| Métrica | Antes (com bug) | Depois (corrigido) |
|---|---|---|
| Linhas | 5.228 | 2.431 |
| GMV total | R$ 8.773.954,36 | **R$ 21.174.272,80** |
| `ref_month` | jan–dez/2026 (25 grupos futuros) | jan–mai/2026 (0 futuros) |
| Local vs Neon | idêntico (ambos com o mesmo bug) | idêntico (ambos corrigidos) |

O GMV **aumentou** (não "conservou") porque o bug original não só invertia dia/mês — para pedidos com dia-do-mês entre 13 e 31, a inversão gerava um "mês" inválido (>12) e `dayfirst=True` descartava a linha como `NaT`. Confirmado em todos os 85 arquivos: **54.404 de 383.298 linhas de pedido (14,2%) eram descartadas silenciosamente**, nunca chegando a ser carregadas. A correção recupera esses pedidos. Validação cruzada: GMV corrigido = R$ 21.174.272,80 vs `fact_marketplace_daily_performance` (Shopee, mesmo período, fonte independente que sempre usou o parser correto) = R$ 21.181.850,05 — diferença de 0,04%, dentro do esperado pela diferença de metodologia (produtos conta só pedidos "Concluído"; diário conta todos exceto "Cancelado").

Backups preservados (não removidos): `marts.fact_shopee_product_monthly_backup_20260701_133049` no PostgreSQL local e no Neon.

### Bug 5 — Colisão de `variation_name` na chave única de `fact_shopee_product_monthly` (descoberto e corrigido em 2026-07-01, durante a correção do Bug 3)

**Severidade:** Alta — perda silenciosa de até 36,6% do GMV de uma marca (lescent)

Ao corrigir o Bug 3, a primeira tentativa de correção (upsert simples) revelou um segundo bug pré-existente: a chave única de `fact_shopee_product_monthly` é `(ref_month, brand, sku_ref_key, product_name)` — **não inclui `variation_name`**. Quando o mesmo `sku_ref`/produto tem mais de uma variação (cor, tamanho) no mesmo mês, as linhas colidem na chave única. O script original (`etl/load_shopee_products.py`) resolve a colisão fazendo upsert linha a linha — a última variação processada **sobrescreve silenciosamente** o GMV/unidades das anteriores.

Esse bug já existia em produção, mas ficava mascarado pelo Bug 3: como os pedidos de um mesmo produto ficavam espalhados por meses errados diferentes, colidiam com menos frequência no mesmo mês. Corrigido o parsing de data, as variações de um produto passaram a cair corretamente no mesmo mês — aumentando a colisão. Para `lescent`, isso causava perda de ~36,6% do GMV real (R$ 242.685 de R$ 663.031).

**Correção aplicada**: em vez de sobrescrever, `fix_shopee_product_dates.py` **soma** `gmv`/`units_sold`/`completed_orders`/`canceled_orders`/`unique_buyers` das linhas colidentes e recalcula `cancel_rate_pct`/`avg_price` a partir dos totais somados. `variation_name` passa a listar as variações combinadas (ex.: `"Preto; Branco"`) em vez de mostrar apenas a última. 129 colisões desse tipo foram encontradas e corrigidas nos 5 brands.

**Limitação conhecida**: `unique_buyers` somado entre variações pode contar 2x um comprador que comprou mais de uma variação do mesmo produto no mês — leve sobrestimativa, documentada aqui.

**Recomendação futura (não aplicada — fora do escopo aprovado)**: se a granularidade por variação for importante para a UI, adicionar `variation_name` à chave única via migration (`UNIQUE (ref_month, brand, sku_ref_key, product_name, variation_name)`).

### Bug 4 — `rituaria` ausente de todos os KPIs de Mercado Livre apesar de ter ~R$8M de GMV real na fonte (RESOLVIDO em 2026-07-01 — inclusão aprovada explicitamente)

**Severidade:** Alta — impacto financeiro direto no dashboard (GMV, pedidos, ROAS, ranking de produtos ML de uma marca inteira ausentes)

**O que acontecia:**
`pipelines/connectors/mercadolivre/connector.py` definia `BRANDS_IN_SCOPE = ("barbours", "kokeshi", "lescent")`, excluindo `rituaria` (e `apice`) da consulta a `gold.ml_gestao_diaria`. O mesmo whitelist estava duplicado em `gold_service.py` (`ML_BRANDS`), `performance_service.py` (`_ML_BRANDS`) e no router (`VALID_ML_BRANDS`), além do filtro de marca ML em `apps/web/app/produtos/page.tsx`.

**Evidência (query somente leitura direta no RDS, sem filtro de brand):**
```
rituaria: 186 dias de dados (2025-12-28 → 2026-07-01), GMV = R$ 8.027.817,35, pedidos = 78.167
apice:    0 linhas (confirma que apice de fato não vende no ML — filtro correto para essa marca, mantido)
```
`docs/architecture.md` registrava em 2026-06-16: *"rituaria existe no TikTok mas o pipeline de ML ainda não foi populado (...) Sem tratamento especial necessário — null nos campos ML."* Essa decisão estava desatualizada: a fonte já tinha dados de `rituaria` desde 2025-12-28, antes até da data dessa decisão.

**Correção aplicada (aprovação explícita do stakeholder em 2026-07-01):**
1. `rituaria` adicionada a `BRANDS_IN_SCOPE` (connector ML), `ML_BRANDS` (`gold_service.py`), `_ML_BRANDS` (`performance_service.py`), `VALID_ML_BRANDS` (router) e ao filtro `BrandML` em `apps/web/app/produtos/page.tsx`.
2. Backfill `daily_performance.py --source ml --mode backfill --days 190` (cobre 2025-12-23 → 2026-07-01): 758 linhas carregadas no Neon, incluindo 186 linhas de `rituaria`.
3. `sync_produtos.py --source ml`: 1.486 linhas em `fact_ml_produto_ranking` (era 1.326), incluindo 156 produtos de `rituaria`.
4. Reconciliação confirmou paridade exata RDS↔Neon mês a mês para `rituaria` (jan–jun/2026) e nenhuma perda nas demais marcas.

**Resultado**: `rituaria` passa a representar ~35% do GMV total de ML no Neon (R$ 7.961.372,89 de R$ 22.895.604,29, incluindo o dia parcial de 01/07).

**Pendência remanescente (não aplicada — fora do escopo aprovado)**: consolidar as 4 constantes de whitelist duplicadas em uma única fonte de verdade (ex.: `marts.dim_seller_account`, hoje não usado para filtrar).

### Problema 5 — ML sem dimensão temporal (design, não bug)

A aba ML lê `gold.ml_produto_ranking` que é um ranking sem data de corte exposta. A UI não tem `PeriodSelector` para ML, ao contrário de TikTok e Shopee. O usuário não sabe a qual período os dados se referem.

**Risco:** decisões tomadas com base em dados do mês passado sem o usuário saber.

**Recomendação:** Exibir na UI a data de atualização do ranking (campo `updated_at` ou similar no gold), ou adicionar um aviso "Ranking atualizado em: [data]".

---

## 5. Métricas confiáveis

**ML (quando RDS disponível):**
- gross_revenue, units_sold, unique_buyers, cancel_rate_pct
- pareto_bucket, revenue_velocity, product_status
- ad_roas, ad_acos_pct, ad_efficiency, action_signal
- revenue_share_pct

**TikTok (quando RDS disponível):**
- gmv, orders, items_sold
- pct_gmv_video, pct_gmv_live, pct_gmv_card (AttributionBar correta)
- rating_avg, total_ratings

**Shopee:** dados corrigidos no Neon desde 2026-07-01 (Bug 3 e Bug 5 resolvidos) — 2.431 linhas, GMV R$ 21.174.272,80, `ref_month` jan–mai/2026, validado contra fonte independente (diff 0,04%).

---

## 6. Métricas proxy ou ausentes

| Campo | Status | Risco |
|---|---|---|
| `problem_rate` TikTok | Proxy | Denominador pode estar errado se `orders` no gold já exclui problemas |
| `estimated_margin` ML | Ausente da UI / não validado | Campo no schema e na query mas não exibido; lógica de cálculo desconhecida |
| Período do ranking ML | Ausente da UI | Usuário não sabe a qual mês os dados se referem |
| `unique_buyers` Shopee (produtos com variação combinada) | Aproximação | Soma entre variações pode contar 2x um comprador que comprou >1 variação no mês (ver Bug 5) |

---

## 7. Correções aplicadas

| # | Arquivo | Mudança | Sessão |
|---|---|---|---|
| C1 | `apps/api/app/routers/performance.py` | Adicionado `VALID_ML_ACTION_SIGNALS` com 6 valores permitidos; validação antes de chamar `get_produtos_ml` | 2026-06-26 |
| C2 | `apps/api/alembic/versions/004_create_product_tables.py`, `pipelines/sync_produtos.py`, `apps/api/app/services/performance_service.py` | Migração de Produtos ML/TikTok/Shopee para Neon (resolve Bug 1) | 2026-06-26/2026-07-01 |
| C3 | `apps/api/etl/load_shopee_products.py` | Corrigido parsing de data (removido `dayfirst=True` incorreto em datas ISO) — causa raiz do Bug 3 | 2026-07-01 |
| C4 | `pipelines/sync_produtos.py` | Auditoria em `audit.source_sync_run`, rollback explícito em falha, validação de origem/destino, guarda de queda suspeita de linhas, brands lidas de `marts.dim_loja` | 2026-07-01 |
| C5 | `pipelines/reconciliation/fix_shopee_product_dates.py` (novo) | Correção transacional de dados: backup, staging, validação cruzada, substituição local+Neon (resolve Bug 3 e Bug 5) | 2026-07-01 |
| C6 | `pipelines/connectors/mercadolivre/connector.py`, `gold_service.py`, `performance_service.py`, `routers/performance.py`, `apps/web/app/produtos/page.tsx` | Inclusão de `rituaria` no escopo ML (resolve Bug 4) | 2026-07-01 |
| C7 | Backfill ML (758 linhas), incremental TikTok (70 linhas), sync Produtos ML (1.486) e TikTok (173.920) | Fecha o atraso de 8-10 dias no Neon e traz produtos da `rituaria` | 2026-07-01 |

---

## 8. Próximos passos de dados

1. **Consolidar as 4 constantes de whitelist ML duplicadas** em uma única fonte de verdade (ex.: `marts.dim_seller_account`).
2. **Expor data de atualização do ranking ML na UI.** Consultar `gold.ml_produto_ranking` por um campo `updated_at` ou similar e exibir no cabeçalho da tabela ML: "Ranking atualizado em: YYYY-MM-DD".
3. **Validar denominador de `problem_rate` TikTok.** Checar se `orders` em `gold.tiktok_product_daily` inclui ou exclui cancelados/devolvidos.
4. **Documentar thresholds de `ad_efficiency` e `revenue_velocity`** em `docs/kpi_dictionary.md`.
5. **Ativar o agendamento** de `pipelines/sync_produtos.py` e `pipelines/ingestion/daily_performance.py` no Windows Task Scheduler — comandos preparados em `docs/runbook_sync_produtos.md`, **não ativados** (requer nova autorização).
6. **Avaliar adicionar `variation_name` à chave única de `fact_shopee_product_monthly`** via migration, se a granularidade por variação for necessária na UI (ver Bug 5).

---

## 9. Status por tab (atualizado 2026-07-01)

| Tab | Dados em produção | Período | Status geral |
|---|---|---|---|
| ML | Sim (Neon, atualizado até 2026-07-01) | Ranking sem data explícita | Confiável, inclui `rituaria` (Bug 4 resolvido) — sem contexto temporal no ranking (Problema 5) |
| TikTok | Sim (Neon, atualizado até 2026-06-29) | Filtrado por mês | Confiável com ressalva de problem_rate |
| Shopee | Sim (Neon, corrigido) | jan–mai/2026 | Confiável — `ref_month` corrigido e validado contra fonte independente (Bug 3 e Bug 5 resolvidos) |
