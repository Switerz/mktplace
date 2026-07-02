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

### Bug 6 — `sku_ref_key` com mais de 1 linha por (ref_month, brand) em `fact_shopee_product_monthly` — reconciliado, não é duplicação espúria (2026-07-01)

**SUPERADO PARCIALMENTE pelo Bug 7 abaixo (mesmo dia):** a auditoria completa dos 115 grupos (não apenas as 3 amostras abaixo) encontrou 34 grupos onde o `sku_ref_key` está sendo reaproveitado em produtos comprovadamente distintos — consolidá-los por `sku_ref_key` como descrito aqui seria incorreto. A regra de identidade foi corrigida no Bug 7; este registro é mantido como está por rastreabilidade do que foi investigado e quando.

**Severidade:** Baixa (dado correto após consolidação em tempo de leitura; risco real era `unique_buyers` sobrestimado, ver abaixo).

**Investigação:** 115 grupos `(ref_month, brand, sku_ref_key)` com >1 linha no mart no momento da auditoria. Por instrução explícita, a soma do Neon **não foi aceita como prova** — a verificação voltou aos XLSX originais (`shopee/{brand}/Order.all*.xlsx`) linha a linha para 3 grupos-amostra (`apice/20587`, `apice/KIT074`, `apice/kit073`, jan/2026):

| sku_ref_key | Grafias de `product_name` na fonte | GMV por grafia (XLSX, "Concluído") | Soma bate com o Neon? |
|---|---|---|---|
| `20587` | "Leave-in Leave-in Antifrizz..." / "Leave-in Antifrizz..." | R$ 567,98 (10 un.) / R$ 170,70 (3 un.) | Sim, exato |
| `KIT074` | "...nutricao..." (sem acento) / "...nutrição..." com encoding corrompido no export | R$ 1.435,85 (16 un.) / R$ 359,60 (4 un., 1 cancelado) | Sim, exato |
| `kit073` | "...mascara..." / "...máscara..." | R$ 1.308,30 (7 un., 1 cancelado) / R$ 186,90 (1 un.) | Sim, exato |

**Causa raiz:** a própria Shopee grava o mesmo SKU/listing sob 2 grafias de `product_name` dentro do mesmo arquivo de export (typo com palavra duplicada, ou acento corrompido no encoding do export) — **nunca produtos distintos** nas amostras auditadas. Como o ETL agrega por `product_name` (entre outras colunas), essa variação textual fragmenta o mesmo SKU em >1 linha do mart. A leitura (`get_produtos_shopee`/`_summary` em `performance_service.py`) já soma essas linhas de volta por `sku_ref_key` — comportamento correto, confirmado contra a fonte, não apenas assumido a partir do próprio Neon.

**Limitação real encontrada:** `unique_buyers` estava sendo **somado** entre as linhas do grupo. Nos 3 grupos auditados não houve overlap de comprador entre as grafias (0 compradores em comum), mas nada garante isso nos outros 112 grupos ou em meses futuros — não há chave de comprador para deduplicar entre linhas do mart. **Corrigido**: quando o grupo tem mais de 1 linha, `unique_buyers` passa a ser `null` em vez de somado (`performance_service.get_produtos_shopee`). Mesmo critério já usado pelo próprio ETL para colisões de `variation_name` (Bug 5).

**Testes:** `apps/api/tests/test_shopee_sku_consolidation.py` (best-effort contra Neon real) — reconcilia GMV/units/orders somados contra a soma bruta do mart, e confirma `unique_buyers = null` sempre que há >1 linha no grupo.

---

### Bug 7 — `sku_ref_key` isolado NÃO é uma chave de identidade segura (bloqueador corrigido em 2026-07-01)

**REVERTIDO pelo Bug 9 abaixo (2026-07-02):** a consolidação automática por similaridade textual (`difflib`, limiar 0,85) descrita aqui foi **rejeitada** após revisão — decidir identidade de produto por heurística fuzzy em produção é um risco inaceitável, mesmo com um limiar calibrado empiricamente. `performance_service.py` não contém mais `_shopee_names_safe_to_merge`/`_shopee_aggregate_by_logical_key`; a chave em produção é a estrita descrita no Bug 9. A investigação e os 115 grupos classificados abaixo continuam válidos como *dado histórico* (a classificação em si não muda, só a decisão do que fazer com a categoria B).

**Severidade:** Alta — consolidar por `sku_ref_key` sem auditoria poderia misturar produtos genuinamente distintos.

**Problema:** o Bug 6 consolidava por `(brand, sku_ref_key)` assumindo que grupos com >1 linha eram sempre o mesmo produto com grafia diferente. Duas falhas nessa premissa:
1. `sku_ref_key` fica `''` (vazio) quando `sku_ref` está ausente na fonte — agrupar só por isso juntaria **todos** os produtos sem SKU de uma marca/mês numa única linha (não ocorre nos dados atuais — 0 linhas com `sku_ref_key` vazio em todos os 25 combos marca×mês auditados — mas o código antigo não tinha proteção alguma contra isso).
2. `sku_ref_key` não vazio pode estar sendo reaproveitado pelo vendedor em produtos **genuinamente diferentes**.

**Auditoria completa dos 115 grupos** (não apenas amostras — todo grupo `(ref_month, brand, sku_ref_key)` com >1 linha, 2026-07-01), classificados por similaridade textual dos `product_name` do grupo após normalizar (remove caracteres de encoding corrompido, remove acentos, lowercase):

| Categoria | Grupos | GMV combinado (soma das linhas do grupo) | Critério |
|---|---|---|---|
| A — Diferença apenas de encoding | 3 | R$ 4.056,60 | Nomes idênticos após normalizar (ratio = 1.0) |
| B — Diferença textual real, mesmo produto (seguro consolidar) | 78 | R$ 1.761.600,18 | Similaridade ≥ 0,85 — edição de título do mesmo anúncio |
| C — SKU reaproveitado em produtos aparentemente distintos (NÃO consolidar) | 34 | R$ 210.251,69 | Similaridade < 0,85 |
| **Total** | **115** | **R$ 1.975.908,47** | |

O limiar 0,85 foi calibrado pelo próprio vão nos dados: a população "mesmo anúncio" nunca fica abaixo de 0,86; a população confirmada "produto distinto" nunca fica acima de 0,82 — não há nenhum grupo observado entre 0,82 e 0,86.

**Exemplos da categoria C (NÃO consolidados — ficam em linhas separadas):**
- `lescent / LC03034` (mar/2026): "Perfume Deo Colônia Lescent NO 27 GOLDEN DUBAI 25ml Masculino" vs "Perfume Deo Colônia Lescent NO 30 PROVOCATEUR RIO 25ml Feminino" — duas fragrâncias diferentes, mesmo código de SKU.
- `kokeshi / 40091` (mai/2026, R$ 40.084,43 combinado): "Kit Colágeno Gota e Geleia" vs "Kit Colágeno Power" — duas composições de kit diferentes.
- `kokeshi / KV43A45` (jan-fev/2026): "Kit Kokeshi Sabonete Facial Pele de Porcelana Escolha Sérum" vs "Kit Sabonete e Sérum Skin Care Kokeshi Antiacne Anti-idade Antimanchas" (ratio 0,31) — os SKUs padrão `KVxxAyy` da Kokeshi parecem ser códigos de família/kit reaproveitados entre composições diferentes, não códigos de produto único.
- `barbours / BB02078` (mar/2026, ratio 0,821, abaixo do limiar): "Body Splash Masculino **Acqua** Homme Colônia..." vs "Body Splash Masculino **Ocean** Homme Desodorante..." — nomes de fragrância diferentes; mantido separado por segurança mesmo estando perto do limiar.

**Chave de identidade definitiva** (calculada em Python a cada requisição — `performance_service._shopee_aggregate_by_logical_key`, nunca gravada no mart):

```
CASE
  WHEN sku_ref_key não vazio E similaridade(nomes do grupo) >= 0.85
    THEN 'sku:' || sku_ref_key                              -- consolida, soma gmv/units/pedidos
  WHEN sku_ref_key não vazio E similaridade(nomes do grupo) < 0.85
    THEN 'name:' || product_name || '|variation:' || variation_name   -- SKU reaproveitado: NÃO consolida
  ELSE
    'name:' || product_name || '|variation:' || variation_name        -- sku vazio: NUNCA consolida
END
```

Não foi possível calcular a similaridade dentro do próprio SQL (Neon não tem `pg_trgm`/`fuzzystrmatch` instalado, e instalar extensão é uma alteração de banco fora do escopo aprovado) — por isso `get_produtos_shopee`/`_summary` buscam as linhas brutas do mart e agregam em Python a cada requisição, garantindo que a decisão de identidade sempre reflita o dado atual (nunca uma lista fixa de exceções que ficaria desatualizada).

**Efeito na Torre de Controle:** `total_gmv`/`units_sold`/`completed_orders`/`canceled_orders` agregados por marca/mês **não mudam** (a soma total independe de como as linhas são agrupadas). O que muda é `total_count` (mais produtos visíveis: os 34 grupos da categoria C passam a aparecer como 2+ linhas em vez de 1) e qual linha individual cai em qual bucket Pareto.

**Reconciliação por marca × mês (jan–mai/2026, 25 combinações) contra os XLSX originais:** GMV, `units_sold` e `completed_orders` batem **exatamente** com a soma bruta dos 85 arquivos `Order.all*.xlsx` em todas as 25 combinações (validado linha a linha via `openpyxl`, sem depender da soma do Neon). Maior produto de cada marca/mês cai sempre no bucket A (acumulado anterior = 0%), e a soma dos 4 buckets reconcilia exatamente com `total_gmv` nos 25 casos.

**Testes:** `apps/api/tests/test_shopee_logical_key.py` — cobre SKU vazio nunca mesclado, mesmo nome sem SKU tratado como identidades distintas, encoding-only mesclado, SKU reaproveitado mantido separado, `unique_buyers = null` quando >1 linha bruta é consolidada, e que tabela/summary/paginação usam exatamente a mesma chave (contagens reconciliam).

---

### Bug 8 — `canceled_orders` subcontado em `fact_shopee_product_monthly`: ETL descarta grupos com só pedidos cancelados (ENCONTRADO em 2026-07-01, NÃO CORRIGIDO — fora do escopo aprovado desta sessão)

**Severidade:** Média — GMV/`units_sold`/`completed_orders` não são afetados; `canceled_orders` e `cancel_rate_pct` ficam levemente subestimados para produtos com cancelamentos concentrados.

**Descoberto durante a reconciliação do Bug 7** (comparação marca×mês contra os 85 XLSX originais): GMV, `units_sold` e `completed_orders` batem exatamente em todas as 25 combinações marca×mês, mas `canceled_orders` fica sistematicamente abaixo do valor real do XLSX em 19 das 25 combinações — um total de ~84 pedidos cancelados "perdidos" no agregado (de milhares).

**Causa raiz** (`apps/api/etl/load_shopee_products.py`, função `_aggregate`):
```python
agg_completed = completed.groupby(grp_cols, dropna=False).agg(...)
agg_canceled  = canceled.groupby(grp_cols, dropna=False).agg(canceled_orders=("status", "count"))
result = agg_completed.merge(agg_canceled, on=grp_cols, how="left")
```
O merge é `left` a partir de `agg_completed` — qualquer grupo `(brand, ref_month, sku_ref, product_name, variation_name)` que tenha **somente** pedidos cancelados (zero "Concluído" naquele grupo) nunca aparece em `agg_completed`, então é descartado inteiro pelo `left` merge: seus pedidos cancelados nunca chegam a `fact_shopee_product_monthly`. Como esses grupos também não têm GMV (só pedidos cancelados), a perda não afeta receita — mas o pedido cancelado em si desaparece da contagem.

**Não corrigido nesta sessão** por instrução explícita ("não altere ETL, banco ou dados"). Recomendação futura: trocar para `outer` merge (ou `agg_completed.merge(agg_canceled, on=grp_cols, how="outer")` com `fillna(0)` em `gmv`/`units_sold`/`completed_orders`), reprocessar via `pipelines/reconciliation/` seguindo o mesmo padrão transacional do Bug 3/5 (backup + staging + validação cruzada), e validar contra os XLSX antes de substituir em produção.

---

### Bug 9 — Chave de identidade Shopee definitiva: SEM consolidação automática, mesmo com similaridade calibrada (2026-07-02)

**Contexto:** o Bug 7 propôs consolidar `sku_ref_key` duplicado quando os `product_name` do grupo passassem num limiar de similaridade textual (`difflib.SequenceMatcher >= 0.85`). Revisão explícita rejeitou essa abordagem: **decidir identidade de produto por heurística fuzzy dinâmica não deve ser usado como chave automática em produção**, mesmo com limiar calibrado por dados reais — o risco de um caso futuro cair do lado errado do limiar e juntar produtos distintos silenciosamente é inaceitável para uma métrica financeira.

**1. Busca por identificador estável nos 85 XLSX originais**

União completa das colunas (67 no total, amostrado no primeiro e último arquivo de cada marca — os 85 arquivos têm 65 colunas cada, mas o *template* difere: `apice` usa um template com 4 colunas próprias — `Tipo de pedido`, `CPF do Comprador`, `Desconto de Frete Aproximado`, `Returned quantity` — enquanto as outras 4 marcas usam um template mais novo com `Domestic Delivered Date`, `Data da Finalização do Cancelamento`, `Pedido FBS`, `Shopee Owned` no lugar; nenhuma dessas 8 colunas exclusivas de um template é um identificador de produto).

**Nenhuma coluna chamada "ID do produto", "ID do item", "ID do anúncio", "ID do modelo" ou "ID da variação" existe em nenhum dos dois templates.** Os únicos candidatos a identificador:

| Coluna | Presença | Preenchimento | Grão | Achado |
|---|---|---|---|---|
| `ID do pedido` | 85/85 arquivos, 100% das linhas | 100% | Pedido (não produto) | 17.255 de 361.101 valores se repetem (pedido com múltiplos itens) — confirma que é identificador de PEDIDO, não de produto/listing. Inútil para identidade de produto. |
| `Nº de referência do SKU principal` (já usado como `sku_ref`/`sku_ref_key`) | 85/85, 100% das linhas | 100% (383.298 de 383.298 linhas) | Definido pelo vendedor | Ver Bug 7: reaproveitado em produtos distintos em pelo menos 34 casos confirmados. |
| `Número de referência SKU` (coluna separada, NÃO usada pelo ETL) | 85/85, 100% das linhas | 100% (383.283 de 383.298 linhas, 99,996%) | Definido pelo vendedor | **Idêntica à coluna principal em 355.782 de 383.283 linhas preenchidas (92,8%)** — não é um identificador independente, é essencialmente um espelho da mesma referência de SKU. Nos ~6.500 casos em que difere (ex.: `apice/KIT072` → sku2 = `"1,4"`), o valor divergente parece ser lixo/erro de formatação, não um ID válido. **Não usável como identificador estável.** |

**Cardinalidade e estabilidade cruzada (todos os 85 arquivos, 383.298 linhas):**
- 84 valores distintos de `sku_ref_key` mapeiam para mais de 1 `product_name` diferente (correspondem aos 115 grupos `ref_month×brand×sku` duplicados do Bug 7, contando repetições entre meses uma única vez).
- 13 `product_name` mapeiam para mais de 1 `sku_ref_key` diferente. **Achado novo:** 11 desses 13 são o **mesmo SKU gravado com capitalização diferente** pela própria Shopee dentro do mesmo período (`KIT073`/`kit073`, `Kit111`/`KIT111`, `Kit116`/`Kit116`, `kit117`/`Kit117`, `Kit110`/`KIT110`, `Kit107`/`KIT107`, `Kit013`/`KIT013`, `KIT046`/`KIT047`) — ou seja, o mesmo listing real às vezes tem o SKU registrado com letras maiúsculas, às vezes minúsculas, entre pedidos diferentes do mesmo mês. Os outros 2 casos (`kokeshi`: `KS03043`/`20002` e `20001`/`KS03044`) têm códigos numericamente distintos para nomes de produto muito parecidos — possível re-emissão de SKU pelo vendedor, não confirmável sem mais contexto.

**Conclusão:** não existe identificador estável e confiável nos XLSX além do próprio `sku_ref_key` definido pelo vendedor — que já é sabidamente não confiável (reaproveitado entre produtos distintos, e agora também inconsistente em capitalização para o mesmo produto). **Nenhuma consolidação automática adicional foi proposta** (nem mesmo normalizar por capitalização) — isso também seria uma decisão automática de identidade, que a instrução desta correção pede para evitar.

**2. Chave definitiva adotada (sem similaridade, sem consolidação automática):**

```
(ref_month, brand, sku_ref_key, product_name)
```

Essa **é** a UNIQUE constraint real do mart — não uma aproximação dela. `variation_name` **não faz parte da chave**: é um atributo descritivo da linha, não um componente de identidade. Ele pode já ter sido consolidado ou sobrescrito rio acima pelo próprio ETL antes de chegar ao mart — colisões de `variation_name` sob o mesmo `sku_ref_key`+`product_name` são somadas na carga (Bug 5), e grupos com apenas pedidos cancelados podem ser descartados no merge (Bug 8) — então o valor de `variation_name` exibido é o que sobrou dessas decisões upstream, nunca recalculado pelo service. O `JOIN`/`GROUP BY` entre as CTEs internas de `get_produtos_shopee` usa exatamente `(brand, sku_ref_key, product_name)` — `ref_month` já é filtro de `WHERE`, então não precisa entrar na condição de junção.

Implementação em `get_produtos_shopee`/`get_produtos_shopee_summary` (`performance_service.py`) volta a ser SQL puro (sem agregação em Python) — cada linha de saída é exatamente 1 linha do mart, nenhuma soma entre linhas. `unique_buyers` deixa de precisar de tratamento especial (nunca mais é anulado pela API — é sempre o valor já calculado pelo próprio ETL).

**Consequência aceita:** o mesmo listing com título editado pelo vendedor durante o mês (ex.: `apice/20587` "Leave-in Leave-in..." vs "Leave-in...", ratio 0,93) ou com SKU gravado em capitalização diferente aparece como 2+ linhas separadas em vez de 1. Isso é intencional — preferimos dividir um produto a somar produtos distintos silenciosamente.

**Bug de implementação encontrado e corrigido durante os próprios testes de reconciliação:** a primeira versão da chave estrita incluía `variation_name` na cláusula `JOIN ... USING (...)` entre as CTEs `base` e `bucketed`. Como `variation_name` é frequentemente `NULL` e `NULL = NULL` nunca é verdadeiro em SQL, isso descartava silenciosamente ~metade das linhas do resultado (ex.: total de um mês caiu de 475 para 193 produtos). Corrigido removendo `variation_name` da condição de JOIN (mantendo-a apenas no SELECT e no ORDER BY, onde `NULLS LAST` já trata `NULL` corretamente) — os testes de reconciliação (`test_gmv_total_nao_muda_mesmo_sem_consolidacao`) pegaram o erro antes de qualquer uso real.

**3. Aliases explícitos auditados (opção avaliada, NÃO implementada):** não há demanda de negócio articulada para consolidar os 81 grupos da antiga categoria B/C (Bug 7) agora — a chave estrita já é aceitável e mais segura. Se essa necessidade surgir, o mecanismo recomendado é um arquivo versionado (ex.: `docs/data/shopee_sku_aliases.yaml`) com entradas manualmente revisadas, cada uma explicitando `sku_ref_key`, os `product_name` exatos consolidados, quem revisou e quando — nunca gerado automaticamente a partir de um score de similaridade. Nenhum arquivo desse tipo foi criado nesta sessão.

**4. Reconciliação com a chave definitiva (25 combinações marca×mês, jan–mai/2026):**
- GMV, `units_sold` e `completed_orders` continuam batendo **exatamente** com a soma bruta dos 85 XLSX (a chave estrita não muda totais, só o número de linhas — confirmado nas mesmas 25 combinações do Bug 7).
- Em todas as 25 combinações: soma dos 4 buckets Pareto = `total_gmv` exato; `eligible_count` do summary = `total` da tabela exato; maior produto de cada marca/mês sempre cai no bucket A.
- Contagem de produtos por marca/mês subiu em relação à versão com similaridade do Bug 7 nos casos que ela mesclava (categoria B): ex. `kokeshi` jan/2026 foi de 119 (com similaridade) para 144 (chave estrita) — os grupos antes consolidados voltam a aparecer como linhas separadas, uma por `product_name` bruto do mart.
- `sem_sku` (produtos sem `sku_ref`) = 0 em todas as 25 combinações no momento da auditoria — a proteção contra "juntar tudo numa linha" é defensiva, não corrige um problema hoje observável.

**5. Benchmark local (Neon somente leitura, 10 execuções após 2 de aquecimento):**

| Cenário | Mediana | Pior caso |
|---|---|---|
| `/produtos/shopee` todas as marcas, página 1 | 361,7 ms | 363,2 ms |
| `/produtos/shopee` todas as marcas, última página | 363,1 ms | 368,0 ms |
| `/produtos/shopee` uma marca, página 1 | 360,5 ms | 364,4 ms |
| `/produtos/shopee` uma marca, última página | 359,5 ms | 361,9 ms |
| `/produtos/shopee` bucket=A_top50 | 362,7 ms | 367,3 ms |
| `/produtos/shopee` bucket=D_tail | 363,8 ms | 366,4 ms |
| `/produtos/shopee/summary` todas as marcas | 360,9 ms | 364,3 ms |
| `/produtos/shopee/summary` uma marca | 359,7 ms | 365,7 ms |

Baseline de comparação (mesma sessão, mesma conexão Neon): `SELECT 1` puro = 179,8 ms (round-trip fixo de rede); `/produtos/ml/summary` já em produção = 546,5 ms; `/produtos/tiktok/summary` já em produção = 384,9 ms. Os números da Shopee (~360 ms, 2 round-trips de ~180 ms cada) estão **em linha com os endpoints ML/TikTok já existentes**, não pior — a latência é dominada pelo round-trip de rede até o Neon, não pela lógica da chave estrita. **Cache não foi adicionado** (não há evidência de necessidade; a latência é uma característica de infraestrutura compartilhada por toda a aba Produtos, não um problema introduzido por esta correção).

**Testes:** `apps/api/tests/test_shopee_sku_consolidation.py` — reescrito para a chave estrita: confirma que nenhum grupo duplicado é consolidado (mesmo os antigos casos "seguros" de encoding), que o GMV total não muda, que `unique_buyers` nunca é anulado pela API, e que total/summary/paginação/bucket reconciliam pela mesma chave.

---

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
| `unique_buyers` Shopee (produtos com variação combinada) | Aproximação (ETL) | Soma entre variações pode contar 2x um comprador que comprou >1 variação no mês (ver Bug 5) |
| `unique_buyers` Shopee (`sku_ref_key` com >1 linha no mart, ver Bug 6) | `null` (não estimado) | API não soma sem chave de comprador — corrigido em 2026-07-01, era somado antes |
| `canceled_orders`/`cancel_rate_pct` Shopee | Subestimado (~84 pedidos em jan–mai/2026) | ETL descarta grupos com só pedidos cancelados por usar `left` merge a partir de completados (ver Bug 8, não corrigido nesta sessão) |

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
| C8 | `apps/api/app/services/performance_service.py` (`_PARETO_BUCKET_CASE_SQL`) | Corrigida contradição entre comentário e SQL da fronteira Pareto: classificação passa a usar o acumulado de GMV **antes** de incluir o próprio produto (`cum_gmv - gmv`) com limiares estritos (`<` 50/80/95), não o acumulado depois (`<=`). Efeito: o maior produto do conjunto nunca cai em B só por representar >50% do GMV isolado | 2026-07-01 |
| C9 | `apps/api/app/services/performance_service.py` (`get_produtos_shopee`) | `unique_buyers` deixa de ser somado quando o grupo `sku_ref_key` consolida >1 linha do mart — vira `null` (ver Bug 6, revertido pelo C12) | 2026-07-01 |
| C10 | `apps/api/app/services/performance_service.py`, `app/schemas/performance.py`, `apps/web/src/lib/api-client.ts` | Summaries dos 3 canais passam a expor `total_count` (todos os produtos, inclusive GMV≤0), `eligible_count` (GMV>0, o que entra nos buckets) e `excluded_zero_gmv_count`; nota dinâmica na UI (`zeroGmvNote`) quando há produtos excluídos | 2026-07-01 |
| C11 | `apps/web/app/produtos/page.tsx`, `apps/web/src/lib/async-channel-state.ts` (novo) | Estados assíncronos por canal (tabela + summary, ML/TikTok/Shopee) passam a usar guarda de id de requisição + try/catch/finally: resposta obsoleta nunca sobrescreve estado mais recente, falha nunca deixa dados antigos exibidos, e "ao vivo" no cabeçalho reflete só o canal ativo (não mais um estado global compartilhado entre abas) | 2026-07-01 |
| C12 | `apps/api/app/services/performance_service.py` (`get_produtos_shopee`, `get_produtos_shopee_summary`) | **[SUBSTITUÍDO pelo C14]** Consolidação por SKU via similaridade textual (≥ 0,85) | 2026-07-01 |
| C13 | `apps/web/app/produtos/page.tsx`, `apps/web/src/lib/async-channel-state.ts` | Ao mudar marca, período, bucket ou aba (marketplace), os dados do canal são limpos (volta a `null`) antes de disparar a nova busca — mostra skeleton em vez de manter dados do filtro anterior visíveis durante o carregamento | 2026-07-01 |
| C14 | `apps/api/app/services/performance_service.py` (`get_produtos_shopee`, `get_produtos_shopee_summary`), `app/schemas/performance.py`, `apps/web/src/lib/api-client.ts` | Removida a consolidação por similaridade textual (C12/Bug 7); chave definitiva vira a UNIQUE constraint real do mart, `(ref_month, brand, sku_ref_key, product_name)` — `variation_name` é atributo descritivo, não parte da chave. SQL puro, sem agregação em Python, nenhuma consolidação automática entre linhas do mart (resolve Bug 9). Corrigido também um bug de `JOIN ... USING` com `variation_name` (coluna nula) que descartava ~metade das linhas — encontrado pelos próprios testes de reconciliação antes de qualquer uso real | 2026-07-02 |
| C15 | `apps/api/app/services/performance_service.py`, `app/schemas/performance.py`, `docs/sections/produtos_audit.md`, testes | Correção pré-commit: comentários/docstrings/testes que descreviam a chave Shopee como `(ref_month, brand, sku_ref_key, product_name, variation_name)` (5 campos) corrigidos para os 4 campos reais da UNIQUE constraint; adicionado teste de cardinalidade do JOIN por marca×mês | 2026-07-02 |

---

## 8. Próximos passos de dados

1. **Consolidar as 4 constantes de whitelist ML duplicadas** em uma única fonte de verdade (ex.: `marts.dim_seller_account`).
2. **Expor data de atualização do ranking ML na UI.** Consultar `gold.ml_produto_ranking` por um campo `updated_at` ou similar e exibir no cabeçalho da tabela ML: "Ranking atualizado em: YYYY-MM-DD".
3. **Validar denominador de `problem_rate` TikTok.** Checar se `orders` em `gold.tiktok_product_daily` inclui ou exclui cancelados/devolvidos.
4. **Documentar thresholds de `ad_efficiency` e `revenue_velocity`** em `docs/kpi_dictionary.md`.
5. **Ativar o agendamento** de `pipelines/sync_produtos.py` e `pipelines/ingestion/daily_performance.py` no Windows Task Scheduler — comandos preparados em `docs/runbook_sync_produtos.md`, **não ativados** (requer nova autorização).
6. **Avaliar adicionar `variation_name` à chave única de `fact_shopee_product_monthly`** via migration, se a granularidade por variação for necessária na UI (ver Bug 5).
7. **Corrigir o merge `left` de `_aggregate()` em `load_shopee_products.py`** para `outer` (com `fillna(0)`), reprocessar via pipeline de reconciliação transacional e validar contra XLSX antes de substituir em produção (ver Bug 8 — requer nova autorização, fora do escopo desta sessão).
8. **Se houver demanda de negócio real para consolidar listings com título editado** (categoria B do Bug 7, 78 grupos), criar um arquivo de aliases explícito e versionado (`docs/data/shopee_sku_aliases.yaml` ou similar) com revisão manual por entrada — nunca gerado automaticamente por similaridade (ver Bug 9, item 3).

---

## 9. Status por tab (atualizado 2026-07-01)

| Tab | Dados em produção | Período | Status geral |
|---|---|---|---|
| ML | Sim (Neon, atualizado até 2026-07-01) | Ranking sem data explícita | Confiável, inclui `rituaria` (Bug 4 resolvido) — sem contexto temporal no ranking (Problema 5) |
| TikTok | Sim (Neon, atualizado até 2026-06-29) | Filtrado por mês | Confiável com ressalva de problem_rate |
| Shopee | Sim (Neon, corrigido) | jan–mai/2026 | Confiável para GMV/units/completed (validado contra os 85 XLSX originais, diff zero) — identidade de produto usa chave estrita, sem consolidação automática (Bug 9); `canceled_orders` levemente subestimado (Bug 8, não corrigido) |
