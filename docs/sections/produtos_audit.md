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

### Bug 8 — `canceled_orders` subcontado em `fact_shopee_product_monthly`: ETL descarta grupos com só pedidos cancelados (ENCONTRADO em 2026-07-01; **RESOLVIDO em produção — local e Neon — em 2026-07-02/03 via Gates 1–4B; encerrado com QA final em 2026-07-03**)

**Severidade:** Média — GMV/`units_sold`/`completed_orders` não são afetados; `canceled_orders` e `cancel_rate_pct` ficam levemente subestimados para produtos com cancelamentos concentrados.

**Descoberto durante a reconciliação do Bug 7** (comparação marca×mês contra os 85 XLSX originais): GMV, `units_sold` e `completed_orders` batem exatamente em todas as 25 combinações marca×mês, mas `canceled_orders` fica sistematicamente abaixo do valor real do XLSX em 19 das 25 combinações — um total de ~84 pedidos cancelados "perdidos" no agregado (de milhares).

**Causa raiz** (`apps/api/etl/load_shopee_products.py`, função `_aggregate`):
```python
agg_completed = completed.groupby(grp_cols, dropna=False).agg(...)
agg_canceled  = canceled.groupby(grp_cols, dropna=False).agg(canceled_orders=("status", "count"))
result = agg_completed.merge(agg_canceled, on=grp_cols, how="left")
```
O merge é `left` a partir de `agg_completed` — qualquer grupo `(brand, ref_month, sku_ref, product_name, variation_name)` que tenha **somente** pedidos cancelados (zero "Concluído" naquele grupo) nunca aparece em `agg_completed`, então é descartado inteiro pelo `left` merge: seus pedidos cancelados nunca chegam a `fact_shopee_product_monthly`. Como esses grupos também não têm GMV (só pedidos cancelados), a perda não afeta receita — mas o pedido cancelado em si desaparece da contagem.

**Não corrigido na sessão de 2026-07-01** por instrução explícita ("não altere ETL, banco ou dados"). Recomendação registrada então: trocar para `outer` merge, reprocessar via `pipelines/reconciliation/` seguindo o mesmo padrão transacional do Bug 3/5, e validar contra os XLSX antes de substituir em produção.

**Gate 1 (2026-07-02) — código corrigido:** `apps/api/etl/load_shopee_products.py::_aggregate` trocou `how="left"` por `how="outer"`, com `fillna` em `gmv`/`units_sold`/`completed_orders`/`unique_buyers`/`canceled_orders`. Decisão explícita: `unique_buyers` de um grupo só-cancelado fica `0` (`nunique()` continua contando apenas compradores de pedidos concluídos). 4 testes novos em `apps/api/etl/tests/test_load_shopee_products_aggregate.py`. Commit `7bd0981`.

**Gate 2 (2026-07-02) — reconciliado em staging, SOMENTE PostgreSQL local, produção intocada:**

Script dedicado `pipelines/reconciliation/reconcile_bug8_canceled_only.py` (nunca referencia `DATABASE_URL`/`DATAMART_DATABASE_URL` — estruturalmente incapaz de conectar a Neon/RDS; exige `LOCAL_PG_URL` explícito e recusa qualquer host fora de `localhost`/`127.0.0.1`/`::1`) criou:
- Backup: `marts.fact_shopee_product_monthly_backup_bug8_20260702_150840` (2.431 linhas, cópia exata da tabela real).
- Staging: `marts.fact_shopee_product_monthly_staging_bug8_20260702_150840` (2.471 linhas, reprocessada dos 85 XLSX com o ETL corrigido).

**Resultado da reconciliação (25 combinações marca×mês):**
- `canceled_orders`: 53.515 → 53.599 (**+84**, exatamente o valor estimado na investigação original).
- Linhas do mart (grão real, pós soma de `variation_name` do Bug 5): **+40** — as 12 unidades de diferença vs. os 52 grupos identificados na granularidade fina (antes da soma de variação) se explicam por colisões com o fix do Bug 5 (ex.: `lescent` — a variação só-cancelada foi somada dentro de uma linha já existente em vez de aparecer como linha nova).
- GMV, `units_sold`, `completed_orders`: **diferença zero em todas as 25 combinações**.
- Sem chaves duplicadas, sem nulos em campos obrigatórios na staging.
- 100% das linhas novas/alteradas têm `gmv=0` → Pareto matematicamente inalterado (buckets dependem só de linhas com `gmv>0`, nenhuma delas mudou).
- Tabela real e Neon **não foram tocados** — nenhum `TRUNCATE`/`UPDATE` na produção, nenhuma conexão aberta com Neon.

Backup e staging preservados (não removidos) para inspeção/rollback manual.

**Gate 3 (2026-07-02) — swap aplicado, SOMENTE PostgreSQL local:**

`pipelines/reconciliation/swap_bug8_canceled_only.py` substituiu `marts.fact_shopee_product_monthly` (local) pelo conteúdo da staging do Gate 2, numa única transação (`LOCK` → `TRUNCATE` só da tabela real → `INSERT` com lista explícita de colunas → validação `EXCEPT` nos dois sentidos + agregados → `COMMIT` só se tudo idêntico). Usa os mesmos nomes fixos de backup/staging do Gate 2 (nunca descobertos dinamicamente) e a mesma guarda de host (`localhost`/`127.0.0.1`/`::1`, sem fallback de `LOCAL_PG_URL`, nunca referencia `DATABASE_URL`/`DATAMART_DATABASE_URL`).

**Pré-voo (antes do swap):** tabela real == backup do Gate 2 (`EXCEPT` 0/0 — sem drift) · staging reconferida: 2.471 linhas, GMV R$ 21.174.272,80, 53.599 cancelamentos, 0 duplicatas, 0 nulos.

**Resultado do swap** (tabela real local, pós-`COMMIT`):
- **2.471 linhas** (era 2.431).
- **53.599 cancelamentos** (era 53.515) — **+84 recuperados**, exatamente o valor projetado.
- GMV R$ 21.174.272,80, `units_sold` e `completed_orders`: **inalterados** em todas as 25 combinações marca×mês (diferença zero).
- Pareto: 0 linhas novas/alteradas com `gmv≠0` → buckets A/B/C/D matematicamente inalterados.
- Smoke test read-only (`get_produtos_shopee`/`get_produtos_shopee_summary` contra o Postgres local pós-swap, engine própria — nunca `app.database`/Neon): 3 combinações marca×mês testadas, `total` da tabela = `eligible_count` do summary em todas.
- Backup (`..._backup_bug8_20260702_150840`) e staging (`..._staging_bug8_20260702_150840`) **preservados**, não apagados.
- **Neon permanece intocado** — nenhuma conexão aberta com Neon em nenhuma fase do Gate 3 (nem preflight, nem swap, nem smoke test).

Testes: `pipelines/tests/test_swap_bug8_canceled_only.py` — preflight bloqueia objeto ausente/drift/staging divergente (linhas, GMV, duplicatas, nulos); `_swap` sempre faz rollback antes de qualquer falha chegar a `COMMIT`; `INSERT` comprovadamente usa lista explícita de colunas (nunca `SELECT *`); guardas estruturais confirmam ausência de leitura de `DATABASE_URL`/`DATAMART_DATABASE_URL` e nomes de backup/staging como constantes fixas (nunca descoberta dinâmica via `LIKE`/`ORDER BY ... DESC LIMIT`).

**Gate 4A.1 (2026-07-02) — diagnóstico read-only do Neon:**

`pipelines/reconciliation/diagnose_bug8_neon.py --diagnose` (primeiro script da série autorizado a abrir `DATABASE_URL`; nunca referencia `DATAMART_DATABASE_URL`; transação explicitamente read-only) confirmou Neon == backup local pré-fix: 2.431 linhas, 0 drift, 0 dados novos, nas 25 combinações marca×mês. Commit `7a5b6c3`.

**Gate 4A.2 (2026-07-02) — backup + staging criados no Neon:**

Modo `--prepare` do mesmo script (guardas: flag explícita + `I_UNDERSTAND_THIS_TOUCHES_NEON=1` + diagnóstico limpo recalculado imediatamente antes + **revalidação sob `SHARE LOCK`** contra o backup local pré-fix, fechando a janela de corrida entre diagnóstico e lock). Numa única transação criou e reconciliou:
- Backup Neon: `marts.fact_shopee_product_monthly_backup_bug8_neon_20260702_232445` (2.431 linhas, `EXCEPT` 0/0 vs. tabela real).
- Staging Neon: `marts.fact_shopee_product_monthly_staging_bug8_neon_20260702_232445` (2.471 linhas, copiada da staging local já validada no Gate 2 — 13 colunas explícitas, idêntica chave a chave, 0 duplicatas/nulos).

Tabela real do Neon não foi tocada neste gate. Commit do código: `54780d7`.

**Gate 4B (2026-07-02) — swap aplicado no Neon (COMMIT):**

`pipelines/reconciliation/swap_bug8_neon.py --swap-neon` (commit `ccd93fa`; guardas: flag + `I_UNDERSTAND_THIS_REPLACES_NEON_DATA=1` + diagnóstico limpo + nomes de backup/staging fixos + `SET LOCAL lock_timeout='10s'`/`statement_timeout='60s'` antes do `ACCESS EXCLUSIVE LOCK` + preflight completo sob o lock, incluindo verificação de FKs de terceiros). Transação única: `TRUNCATE` só da tabela real (sem `CASCADE`/`RESTART IDENTITY`) → `INSERT` com 13 colunas explícitas a partir da staging Neon → `EXCEPT` bidirecional 0/0 → agregados idênticos → `COMMIT`.

**Resultado final no Neon (produção):**

| Métrica | Antes (pré-fix) | Depois |
|---|---|---|
| Linhas | 2.431 | **2.471** (+40, todas com GMV zero) |
| `canceled_orders` | 53.515 | **53.599** (+84 recuperados) |
| GMV | R$ 21.174.272,80 | R$ 21.174.272,80 (inalterado) |
| `units_sold` | 337.448 | 337.448 (inalterado) |
| `completed_orders` | 329.588 | 329.588 (inalterado) |

Além das 40 linhas novas (grupos só-cancelados, `gmv=0`, fora do Pareto por construção), **12 linhas pré-existentes** mudaram apenas em `canceled_orders`/`variation_name` — variações só-canceladas somadas em linhas já existentes pela consolidação do Bug 5 (ex.: `lescent/N4`, `kokeshi/KV37A39`). Verificado explicitamente: GMV, `units_sold` e `completed_orders` idênticos ao backup em 100% das chaves comuns, nenhuma chave do backup sumiu → **Pareto matematicamente inalterado**.

**QA final de encerramento (2026-07-03, tudo read-only):**
- Neon: 17/17 checks (agregados exatos, `EXCEPT` real↔staging 0/0, 0 duplicatas/nulos, backup/staging intocados, 40 linhas gmv=0, 12 diffs só-cancelamento, 0 chaves perdidas).
- API pública (`https://mktplace-api.onrender.com`): **25/25 combinações marca×mês** em `/produtos/shopee` + `/produtos/shopee/summary` — HTTP 200, `eligible_count` = soma dos buckets, GMV dos buckets = `total_gmv`, nenhum produto de GMV zero no Pareto, filtros respeitados, `total_count` já refletindo as +40 linhas (ex.: apice jan/2026 `total_count=116`, `excluded_zero_gmv_count=5`).
- Reconciliação fonte: os 85 XLSX reprocessados em memória com o `_aggregate` corrigido batem com o Neon em GMV/unidades/concluídos/cancelados nas 25 combinações (divergência zero).

**Monitoramento futuro:** `pipelines/reconciliation/monitor_bug8_invariants.py` — read-only, valida **invariantes** (nunca os snapshots 2.471/53.599, que mudam com cargas futuras): duplicatas, nulos, métricas negativas, coerência das linhas só-canceladas (`gmv=0`, `cancel_rate=100`), consistência de `cancel_rate_pct`, e reconciliação de agregados por marca×mês contra os XLSX locais quando disponíveis (`canceled_orders` do Neon menor que o da fonte = assinatura de regressão ao left merge). Exit code 1 em divergência. Rodar após cada carga do ETL Shopee:
```bash
python -m pipelines.reconciliation.monitor_bug8_invariants            # completo
python -m pipelines.reconciliation.monitor_bug8_invariants --skip-source  # so invariantes do mart
```
Testes em `pipelines/tests/test_monitor_bug8_invariants.py` (conexões falsas). Não confundir com `diagnose_bug8_neon.py --diagnose`, que compara contra o snapshot pré-fix e **passou a divergir intencionalmente** após o Gate 4B — não usar como monitor.

**Política de retenção dos objetos de segurança (definida em 2026-07-03):**
- `marts.fact_shopee_product_monthly_backup_bug8_neon_20260702_232445` e `..._staging_bug8_neon_20260702_232445` (Neon), e `..._backup_bug8_20260702_150840`/`..._staging_bug8_20260702_150840` (PostgreSQL local) devem ser **preservados até ocorrer pelo menos uma carga real posterior do ETL Shopee validada com sucesso pelo monitor acima, mais 7 dias de observação**.
- Qualquer remoção futura exige autorização explícita — nenhum objeto foi apagado nesta sessão.

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
| `canceled_orders`/`cancel_rate_pct` Shopee | Confiável desde 2026-07-03 | Era subestimado (~84 pedidos) pelo `left` merge do ETL — corrigido no código e nos dados (local + Neon), validado contra os 85 XLSX (ver Bug 8, resolvido) |

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
| C16 | `apps/api/tests/test_performance_service_produtos.py`, `apps/api/tests/test_shopee_sku_consolidation.py` | Fase 1 do roadmap (fechamento de Produtos): último comentário remanescente que ainda descrevia a chave Shopee com 5 campos (incluindo `variation_name`) corrigido para os 4 campos reais; `test_cardinalidade_do_join_por_marca_e_mes` passa a checar também soma do GMV dos buckets = GMV elegível e "maior produto sempre no bucket A", por marca×mês, contra o Neon real. Nenhuma mudança de código de produção — apenas testes/docs | 2026-07-02 |
| C17 | `apps/api/etl/load_shopee_products.py`, `apps/api/etl/tests/test_load_shopee_products_aggregate.py` | Fase 2 (Bug 8), Gate 1: `_aggregate` troca `agg_completed.merge(agg_canceled, how="left")` por `how="outer"` com `fillna` — grupos só-cancelados deixam de ser descartados. `unique_buyers` desses grupos fica `0` por decisão explícita. Nenhum dado em produção alterado por este commit — só o código do ETL, que só tem efeito quando reexecutado | 2026-07-02 |
| C18 | `pipelines/reconciliation/reconcile_bug8_canceled_only.py` (novo), `pipelines/tests/test_reconcile_bug8_canceled_only.py` (novo) | Fase 2 (Bug 8), Gate 2: script de reconciliação SOMENTE PostgreSQL local (nunca referencia `DATABASE_URL`/`DATAMART_DATABASE_URL`; exige `LOCAL_PG_URL` explícito, sem fallback, e recusa qualquer host fora de `localhost`/`127.0.0.1`/`::1`). Criou backup + staging locais e reconciliou as 25 combinações marca×mês: `canceled_orders` +84, linhas do mart +40, GMV/units/completed com diferença zero, sem duplicatas/nulos, Pareto matematicamente inalterado. Tabela real e Neon não foram tocados. Swap (Gate 3) não executado | 2026-07-02 |
| C19 | `pipelines/reconciliation/swap_bug8_canceled_only.py` (novo), `pipelines/tests/test_swap_bug8_canceled_only.py` (novo) | Fase 2 (Bug 8), Gate 3: swap transacional da tabela real LOCAL a partir da staging do Gate 2 (`LOCK`+`TRUNCATE`+`INSERT` com colunas explícitas+`EXCEPT` bidirecional+agregados, `COMMIT` só se idêntico). Tabela real local passa a ter 2.471 linhas e 53.599 cancelamentos (+84). GMV/units/completed inalterados, Pareto inalterado. Backup/staging preservados. Neon não tocado — permanece com os dados antigos, sem os 84 cancelamentos recuperados, até o Gate 4 | 2026-07-02 |
| C20 | `pipelines/reconciliation/diagnose_bug8_neon.py` (novo), `pipelines/tests/test_diagnose_bug8_neon.py`, `pipelines/tests/test_prepare_bug8_neon.py` (novos) | Fase 2 (Bug 8), Gates 4A.1/4A.2: diagnóstico read-only Neon vs. backup local pré-fix (commit `7a5b6c3`) e criação transacional de backup+staging no Neon com revalidação sob `SHARE LOCK` contra condição de corrida (commit `54780d7`). Executado em 2026-07-02: Neon confirmado idêntico ao pré-fix, `..._backup_bug8_neon_20260702_232445` (2.431) e `..._staging_bug8_neon_20260702_232445` (2.471) criados e reconciliados. Tabela real do Neon não tocada | 2026-07-02 |
| C21 | `pipelines/reconciliation/swap_bug8_neon.py`, `pipelines/tests/test_swap_bug8_neon.py` (novos) | Fase 2 (Bug 8), Gate 4B (commit `ccd93fa`): swap da tabela real do NEON a partir da staging auditada — `SET LOCAL lock_timeout/statement_timeout` → `ACCESS EXCLUSIVE LOCK` → preflight sob lock (real==backup Neon, staging válida, sem FKs de terceiros) → `TRUNCATE`+`INSERT` explícito → `EXCEPT` 0/0 → `COMMIT`. Executado com sucesso em 2026-07-02: Neon em produção com 2.471 linhas / 53.599 cancelamentos, GMV/units/completed/Pareto inalterados. Backup/staging Neon preservados | 2026-07-02 |
| C22 | `pipelines/reconciliation/monitor_bug8_invariants.py`, `pipelines/tests/test_monitor_bug8_invariants.py` (novos), docs | Encerramento do Bug 8: QA final read-only (Neon 17/17 checks; API pública 25/25 combinações marca×mês; reconciliação fonte XLSX diff zero) + monitor reutilizável de invariantes pós-carga (read-only, sem snapshots hardcoded, detecta regressão ao left merge via `canceled_orders` menor que a fonte) + política de retenção de backup/staging documentada | 2026-07-03 |
| C23 | `apps/api/etl/load_shopee_products.py`, `apps/api/etl/tests/test_load_shopee_products_numeric.py` (novo) | Endurecimento preventivo de `_clean_numeric` (`Subtotal do produto` → `gmv`) e `_clean_int` (`Quantidade` → `units_sold`): mesma classe de limitação do bug de separador de milhar já corrigido em `pipelines/connectors/shopee/_parser.py` (ver `docs/runbook_shopee_raw.md` seção 11.5), agora com implementação LOCAL (`_parse_brl_float`/`_parse_qty_int`/`ShopeeNumericParseError`) fail-fast, exceções nunca encadeadas, formato US/`Infinity`/quantidade fracionária ou negativa rejeitados. Não importa o parser canônico de `pipelines/` — confirmado empiricamente que `pipelines` não está disponível no `.venv` real de `apps/api`. `main()` reestruturado em duas fases (`_prepare_all_brands`/`_write_prepared_brands`): toda validação/agregação de todas as marcas acontece em memória antes de qualquer `engine`/conexão/DDL ser criado — uma marca com dado inválido nunca mais toca o banco, provado por teste de fluxo real com engine fake (não apenas inspeção de código-fonte). Impacto histórico confirmado zero (mesmas colunas já auditadas, 0 células vazias/inválidas em 383.298 linhas); nenhum reprocessamento, nenhuma escrita em banco | 2026-07-04 |

---

## 8. Próximos passos de dados

1. **Consolidar as 4 constantes de whitelist ML duplicadas** em uma única fonte de verdade (ex.: `marts.dim_seller_account`).
2. **Expor data de atualização do ranking ML na UI.** Consultar `gold.ml_produto_ranking` por um campo `updated_at` ou similar e exibir no cabeçalho da tabela ML: "Ranking atualizado em: YYYY-MM-DD".
3. **Validar denominador de `problem_rate` TikTok.** Checar se `orders` em `gold.tiktok_product_daily` inclui ou exclui cancelados/devolvidos.
4. **Documentar thresholds de `ad_efficiency` e `revenue_velocity`** em `docs/kpi_dictionary.md`.
5. **Ativar o agendamento** de `pipelines/sync_produtos.py` e `pipelines/ingestion/daily_performance.py` no Windows Task Scheduler — comandos preparados em `docs/runbook_sync_produtos.md`, **não ativados** (requer nova autorização).
6. **Avaliar adicionar `variation_name` à chave única de `fact_shopee_product_monthly`** via migration, se a granularidade por variação for necessária na UI (ver Bug 5).
7. ~~**Corrigir o merge `left` de `_aggregate()` em `load_shopee_products.py`**~~ — **CONCLUÍDO em 2026-07-02/03** (Bug 8 resolvido via Gates 1–4B; monitor pós-carga em `pipelines/reconciliation/monitor_bug8_invariants.py`).
8. **Se houver demanda de negócio real para consolidar listings com título editado** (categoria B do Bug 7, 78 grupos), criar um arquivo de aliases explícito e versionado (`docs/data/shopee_sku_aliases.yaml` ou similar) com revisão manual por entrada — nunca gerado automaticamente por similaridade (ver Bug 9, item 3).

---

## 9. Status por tab (atualizado 2026-07-01)

| Tab | Dados em produção | Período | Status geral |
|---|---|---|---|
| ML | Sim (Neon, atualizado até 2026-07-01) | Ranking sem data explícita | Confiável, inclui `rituaria` (Bug 4 resolvido) — sem contexto temporal no ranking (Problema 5) |
| TikTok | Sim (Neon, atualizado até 2026-06-29) | Filtrado por mês | Confiável com ressalva de problem_rate |
| Shopee | Sim (Neon, corrigido) | jan–mai/2026 | Confiável para GMV/units/completed/**canceled** (validado contra os 85 XLSX originais, diff zero nas 25 combinações marca×mês em 2026-07-03) — identidade de produto usa chave estrita, sem consolidação automática (Bug 9); Bug 8 resolvido em produção |

---

## 10. Preço médio e margem do anúncio — Gate 1 (auditoria, 2026-07-13)

Pedido do gestor: "Colocar preço médio e margem do anúncio. Ter flag de data e escolha do período." Esta seção audita fonte, grão e disponibilidade **antes** de qualquer implementação. Nenhuma alteração de UI/API/banco foi feita nesta rodada — leitura de código e de documentação apenas.

### 10.1 Estado atual (mapeamento)

| Tab | Endpoints | Filtros suportados hoje | Grão | Fonte |
|---|---|---|---|---|
| Mercado Livre | `/produtos/ml`, `/produtos/ml/summary` | `brand`, `pareto_bucket`, `action_signal`, `product_status`, `revenue_velocity`, sort | Produto (`brand`+`item_id`) — **ranking acumulado, sem competência temporal** | `marts.fact_ml_produto_ranking` (snapshot de `gold.ml_produto_ranking`, RDS) |
| TikTok Shop | `/produtos/tiktok`, `/produtos/tiktok/summary` | `brand`, `ref_month`, `pareto_bucket`, sort | Produto/mês (`brand`+`product_id`, agregado de linhas diárias) | `marts.fact_tiktok_product_daily` (snapshot de `gold.tiktok_product_daily`, RDS) |
| Shopee | `/produtos/shopee`, `/produtos/shopee/summary` | `brand`, `ref_month`, `pareto_bucket`, sort | Produto/mês (`ref_month`+`brand`+`sku_ref_key`+`product_name`, chave estrita, sem consolidação — ver Bug 7/9) | `marts.fact_shopee_product_monthly` (Neon, ETL local) |

Confirmado em código (`apps/api/app/services/performance_service.py:1716-2275`, `apps/api/alembic/versions/004_create_product_tables.py`):

- Nenhum dos 3 marts de produto tem coluna de **custo/CMV**. Busca por `cmv|product_cost|cost_of_goods|unit_cost` no service e no schema não retornou nenhum resultado.
- `fact_shopee_product_monthly` e `fact_tiktok_product_daily` **não têm nenhuma coluna de ads/fee/frete a nível de produto** — só existem em GMV/unidades/pedidos/cancelamento (+ mix de canal e rating no caso TikTok).
- `fact_ml_produto_ranking` é a **única** das três com colunas de ads reais a nível de produto: `ad_spend`, `ad_roas`, `ad_acos_pct`, `days_advertised` (todas `NUMERIC`/`BIGINT`, tipadas desde a migration 004).
- `estimated_margin` (`NUMERIC(18,2)`) existe no schema e na query do ML desde o início, é copiado 1:1 de `gold.ml_produto_ranking.estimated_margin` (`pipelines/sync_produtos.py:278,343`) mas **nunca teve a fórmula documentada** — já sinalizado como proxy não confiável na seção 3/6 deste documento (2026-06-26) e reconfirmado agora: não há em nenhum lugar do repositório (services, pipelines, migrations, docs) o cálculo de origem, porque ele é gerado a montante, no schema `gold` do RDS, fora deste repositório.
- Na UI hoje (`MercadoLivreProductTable.tsx`, `TikTokProductTable.tsx`, `ShopeeProductTable.tsx`): ML mostra Receita/Unid./Pareto/Cancel./**ROAS**/Efic. Ads/Sinal — **não mostra `avg_price`, `ad_acos_pct`, `ad_spend` nem `estimated_margin`**, embora os 4 já cheguem da API. TikTok mostra GMV/Pedidos/Pareto/Canal/Prob.%/Rating — **sem preço médio** (não vem pronto da API, mas é calculável). Shopee **já mostra "Ticket Médio" (`avg_price`)**.

### 10.2 Preço médio — auditoria por marketplace

| Marketplace | Fórmula preferencial | Implementado? | Grão | Denominador zero/null | Confiável? | Limitações |
|---|---|---|---|---|---|---|
| Mercado Livre | `gross_revenue / units_sold` | Sim, no service (`performance_service.py:1995-1996`) — **calculado, não pré-armazenado** | Por anúncio (`item_id`) | `NULL` quando `units_sold = 0` (`CASE WHEN units_sold > 0`) | **Confiável** | Só chega até a API; não é exibido na tabela do frontend hoje |
| Shopee | `gmv / units_sold` | Sim (`performance_service.py:1743`) | Por linha do mart (produto+variação+SKU, chave estrita) | `NULL` quando `units_sold = 0` | **Confiável** | Se duas variações do mesmo produto têm preços muito diferentes, o preço médio da linha "produto" (quando variações são agregadas rio acima) pode mascarar dispersão — não investigado nesta rodada |
| TikTok Shop | `gmv / items_sold` | **Não implementado** — a query atual não expõe essa razão | Por produto/mês (se implementado) | Precisaria do mesmo guard `CASE WHEN items_sold > 0` | **Confiável, se implementado** — `gmv` e `items_sold` já existem na mesma agregação (`performance_service.py:2176-2180`), é adição de uma expressão, sem mudança de fonte | Nenhuma — é a mesma fórmula e os dois insumos já estão no `SELECT` |

**Veredito:** preço médio é seguro para os 3 marketplaces com a fórmula `receita/unidades`, sempre com guarda contra divisão por zero (`NULL`, nunca `0`). ML e Shopee já calculam; falta só (a) expor `avg_price` na tabela do frontend ML e (b) adicionar a expressão em TikTok. Nenhum dos dois é uma mudança de fonte de dado — é exposição/cálculo local sobre colunas já existentes.

### 10.3 "Margem do anúncio" — auditoria de componentes por marketplace

| Componente | Mercado Livre | TikTok Shop | Shopee |
|---|---|---|---|
| GMV/receita por produto | ✅ `gross_revenue` | ✅ `gmv` | ✅ `gmv` |
| Preço médio por produto | ✅ (calculável) | ✅ (calculável) | ✅ (implementado) |
| Unidades por produto | ✅ `units_sold` | ✅ `items_sold` | ✅ `units_sold` |
| Taxas/fees do marketplace por produto | ❌ (existe só lifetime/cumulativo, sem data — `gold.ml_produto_pnl.marketplace_fee`, ~16,5% da receita bruta lifetime, ver `docs/kpi_dictionary.md:269` e `financeiro_audit.md:11.4`) | ❌ (não existe no mart de produto; nem a nível de brand/dia há granularidade de produto) | ❌ (fees existem por marca/dia no financeiro, não por produto) |
| Frete seller por produto | ❌ | ❌ | ❌ |
| Ad spend por produto | ✅ `ad_spend` | ❌ (não existe no mart de produto) | ❌ (existe por marca/dia — `fact_marketplace_daily_performance` —, não por produto) |
| Ad revenue/ROAS/ACOS por produto | ✅ `ad_roas`, `ad_acos_pct` (real, já na API) | ❌ | ❌ |
| Descontos por produto | ❌ (não auditado nesta rodada, nenhuma menção encontrada) | ❌ | ❌ |
| Comissão de afiliados por produto | ❌ | ❌ | ❌ |
| CMV/custo do produto (SKU) | ❌ **Nenhum dos 3 marketplaces tem esse dado em qualquer mart do repositório** | ❌ | ❌ |
| Repasse/settlement por produto | ❌ | ❌ | ❌ |
| **Margem real possível?** | **Não** — falta CMV e a comissão real não tem competência mensal | **Não** — falta CMV e falta ads/fee por produto | **Não** — falta CMV e falta ads/fee por produto |

**Sobre `estimated_margin` (ML) especificamente:** é um valor pré-calculado no `gold` (RDS), copiado sem transformação pelo pipeline de sync. Sem acesso à lógica de origem (fora deste repositório, e não documentada em nenhum lugar acessível), **não deve ser exibido nem nomeado como "margem"** — reforça a decisão já registrada na seção 6 deste documento em 2026-06-26. Isso não mudou nesta auditoria.

**O que É honesto implementar agora, por marketplace:**

| Marketplace | Métrica honesta possível | Nome sugerido |
|---|---|---|
| Mercado Livre | ROAS e ACOS por produto (dado real, já na API, parcialmente na UI) | "ROAS do anúncio" / "ACOS do anúncio" (já é o nome de fato — só falta expor ACOS e `ad_spend` na tabela) |
| TikTok Shop | Nenhuma — não há nenhum componente de custo/ads por produto no mart atual | Bloquear qualquer indicador de "margem" ou "eficiência de ads" nesta aba até existir fonte |
| Shopee | Nenhuma — ads existe só por marca/dia, não por produto | Bloquear; se houver demanda, avaliar romper `fact_marketplace_daily_performance` (ads por marca/dia) por produto exigiria nova extração na origem, não é ajuste de exposição |

**Recomendação desta seção:** **Caminho C, restrito ao Mercado Livre; Caminho A (bloquear) para TikTok e Shopee.** Não implementar "margem" real nem `estimated_margin` em nenhum marketplace. Nenhuma nomenclatura com a palavra "margem" deve aparecer na UI enquanto não houver CMV — usar "ROAS"/"ACOS" (ML) e nada (TikTok/Shopee, até nova fonte).

### 10.4 Período/data — auditoria por marketplace

| Marketplace | `ref_month` | `date_from`/`date_to` | Granularidade diária | Granularidade mensal | Acumulado |
|---|---|---|---|---|---|
| Mercado Livre | ❌ Não suportado — `fact_ml_produto_ranking` é um ranking snapshot sem coluna de data | ❌ | ❌ | ❌ | ✅ **Único modo hoje** (`scope: "ranking_acumulado_atual"`) |
| TikTok Shop | ✅ Suportado (`year`/`month` na query) | ❌ Não suportado no endpoint de produtos (existe em `/daily`, não em `/produtos/tiktok`) | Fonte é diária (`fact_tiktok_product_daily`), mas a API só agrega por mês inteiro | ✅ | ❌ |
| Shopee | ✅ Suportado (`year`/`month`) | ❌ Não suportado | ❌ Fonte já é mensal (`fact_shopee_product_monthly`) — não há como refinar para dia | ✅ **Único grão da fonte** | ❌ |

Observações adicionais:
- O seletor de período do frontend (`AVAILABLE_MONTHS`, `apps/web/src/lib/mock-daily.ts:95-103`) é uma **lista fixa de 7 meses hardcoded** (Dez/25–Jun/26), não gerada dinamicamente a partir do mês atual — vai ficar desatualizada sem manutenção manual.
- ML não tem seletor de período no frontend hoje (`page.tsx:315-368`, sem `<PeriodSelector>` na aba ML) — condizente com a fonte (`ranking_acumulado_atual`), mas o usuário não vê nenhuma indicação de "desde quando" esse acumulado é — pendência já registrada na seção 8, item 2 ("expor data de atualização do ranking ML"), ainda não resolvida.
- Fingir granularidade diária para TikTok/Shopee na aba Produtos **não é recomendado**: a API de produtos só agrega por mês inteiro (mesmo a fonte TikTok sendo diária); daria a falsa impressão de que o usuário pode escolher qualquer intervalo.

**Proposta de contrato de período para a aba Produtos:**

1. **ML:** manter sem seletor de período (a fonte não tem competência temporal). Implementar a pendência já registrada: exibir "Ranking atualizado em: DD/MM/AAAA" no cabeçalho, usando o campo de atualização mais recente disponível em `gold.ml_produto_ranking` (a confirmar se existe `updated_at`/`refreshed_at`; `_ml_refreshed_at(db)` já existe no service e já é usado no summary — só falta renderizar no header da tabela, não no summary).
2. **TikTok/Shopee:** manter seletor mensal (`ref_month`), mas trocar a lista hardcoded `AVAILABLE_MONTHS` por uma lista gerada dinamicamente (últimos N meses a partir do mês corrente) — bug de manutenção, não de dado.
3. **Não adicionar `date_from`/`date_to` de intervalo livre** na aba Produtos para nenhum marketplace nesta fase — nenhuma das 3 fontes de produto suporta granularidade diária real na agregação atual (TikTok teria que reagregar a query, o que é possível mas não pedido explicitamente pelo gestor; Shopee e ML não têm a granularidade na fonte, ponto final).
4. **Estado de cobertura visível ao usuário:** cada tab já expõe (ou deveria expor) o texto de escopo atual (`scopeNote` em `ProductParetoSummary`) — usar esse mesmo padrão para deixar explícito "período não suportado" quando aplicável (hoje já existe para ML: "o Mercado Livre nao possui competência mensal na fonte atual, por isso não há seletor de período aqui").

### 10.5 Proposta de UX (para Gate 2, não implementar ainda)

**Filtros no topo (por tab):** marketplace (já existe via tabs) · marca (já existe) · período (já existe para TikTok/Shopee; N/A documentado para ML) · bucket Pareto (já existe) · para ML, manter também status/velocidade/sinal (já existem).

**Cards por marketplace (topo da tabela, hoje só há o resumo Pareto A/B/C/D):**
- GMV total do escopo filtrado (já calculado no summary via `total_gmv`);
- Produtos ativos = `eligible_count` (já existe);
- Preço médio do escopo (novo — média ponderada por GMV, não média simples de `avg_price` por linha, para não distorcer por produtos de baixo volume);
- % de produtos com ROAS calculável (ML apenas — `ad_spend IS NOT NULL`); para TikTok/Shopee, badge fixo "Eficiência de ads: dado indisponível nesta fonte";
- Alerta de dados incompletos quando `excluded_zero_gmv_count > 0` (já existe o dado, falta só exibir explicitamente como alerta, hoje é só uma nota de rodapé).

**Tabela (colunas por marketplace, incrementais ao que já existe):**
- ML: adicionar `avg_price`, `ad_acos_pct`, `ad_spend` às colunas já existentes (Receita, Unid., Pareto, Cancel., ROAS, Efic. Ads, Sinal). Não adicionar `estimated_margin`.
- TikTok: adicionar `avg_price` calculado (`gmv/items_sold`) à tabela existente.
- Shopee: nenhuma mudança de coluna — já mostra `avg_price`.

**Badges sugeridos (reaproveitando os já usados em Efic. Ads/pareto onde fizer sentido):**
- "preço alto/baixo" — evitar: sem um preço de referência/categoria para comparar, um badge relativo (ex.: p75 do próprio conjunto filtrado) seria mais honesto que "alto" em termos absolutos;
- "baixo giro" — já existe uma proxy (`revenue_velocity` no ML); não existe equivalente para TikTok/Shopee;
- "boa eficiência" — já existe (`ad_efficiency`, só ML);
- "margem indisponível" — usar em vez de qualquer valor de margem, nos 3 marketplaces;
- "dados parciais" — usar quando `ad_spend`/`ad_roas` forem `NULL` mas o produto tiver GMV > 0 (ML), e sempre para ads em TikTok/Shopee.

### 10.6 Recomendação final do Gate 1

**Caminho recomendado: B condicionado por marketplace (não é uma escolha única A/B/C/D — cada marketplace tem disponibilidade diferente).**

1. **Preço médio:** implementar para os 3 marketplaces agora (ML: expor `avg_price` já existente na API; TikTok: adicionar `gmv/items_sold` na query; Shopee: nenhuma mudança). Baixo risco, sem mudança de fonte.
2. **"Margem do anúncio":** **bloquear a palavra "margem" e o campo `estimated_margin` nos 3 marketplaces.** Para ML, implementar ROAS/ACOS por produto (dado real, já disponível, hoje só ROAS está na UI) — nomear como "ROAS do anúncio"/"ACOS do anúncio", nunca como "margem". Para TikTok e Shopee, não há nenhum componente de ads/custo por produto na fonte atual — não implementar nada nessa frente até existir uma nova extração (pendência de dado, não de UI).
3. **Período:** manter o contrato atual por marketplace (ML = acumulado sem seletor + expor data de atualização; TikTok/Shopee = seletor mensal existente, trocar a lista hardcoded por geração dinâmica). Não introduzir `date_from`/`date_to` de intervalo livre na aba Produtos nesta fase.
4. **Antes de qualquer Gate 2 de implementação:** validar com o gestor se "ROAS/ACOS do anúncio" (Mercado Livre apenas) atende a intenção de "margem do anúncio" do pedido original, já que margem real está bloqueada nos 3 marketplaces por falta de CMV.

**Pendência que seria necessária para desbloquear margem real:** um CMV por SKU (custo do produto) e uma fonte de comissão de marketplace com competência temporal (mensal/diária) para os 3 canais — hoje inexistente neste repositório em qualquer schema (`marts`, `gold` auditado via `source_mapping.md`, `data_contracts.md`). Isso é trabalho de nova fonte/pipeline (Caminho D), não de UI, e está fora do escopo deste Gate 1.

### 10.7 Gate 2 — implementação (2026-07-13)

Implementado exatamente conforme a recomendação da seção 10.6, com uma query read-only adicional ao RDS (`DATAMART_DATABASE_URL`, via `apps/api/.venv`) para confirmar dois pontos que ficaram em aberto no Gate 1.

**Query read-only executada (colunas e agregados apenas, nenhuma linha/PII impressa):**

| Pergunta do Gate 2 | Resposta confirmada |
|---|---|
| `gold.ml_produto_ranking` tem `updated_at`/`refreshed_at`/`snapshot_date`? | **Não** — 24 colunas, nenhuma delas é timestamp de atualização do snapshot (`first_sale`/`last_sale` são sobre a venda, não sobre a carga). |
| `gold.ml_produto_pnl` tem `updated_at`/`refreshed_at`/`snapshot_date`? | **Não** — 40 colunas; só existem `first_ad_date`/`last_ad_date` (sobre a campanha de ads, não sobre a carga do snapshot). |
| Origem/fórmula de `estimated_margin` | **Descoberta nesta rodada:** em `gold.ml_produto_pnl`, `estimated_margin = gross_revenue - marketplace_fee - ad_spend` de forma **exata** (diff médio e máximo = 0,00 em 1.659 linhas). É uma contribuição antes de CMV e frete, não uma margem líquida real. |
| `estimated_margin` é comparável entre `ml_produto_ranking` (a tabela que a API usa) e `ml_produto_pnl`? | **Não verificável com segurança**: `(brand, item_id)` **não é chave única** em nenhuma das duas tabelas (1.659 linhas / apenas 1.559 pares distintos em cada uma) — um JOIN direto gera fan-out (1.861 pares casados) e o diff deixa de ser confiável. Testado também dentro da própria `ml_produto_ranking` (sem JOIN): nenhuma combinação de `gross_revenue`/`ad_spend`/`price_spread_pct` reproduziu `estimated_margin` — o valor depende de `marketplace_fee`, que só existe em `ml_produto_pnl`. |
| `marketplace_fee` está populado? | Sim, 100% (1.659/1.659) em `ml_produto_pnl`; média ~18,75% da receita bruta nesta amostra (mesma ordem de grandeza dos ~16,5% já documentados na seção 11.4 do `financeiro_audit.md`) — **sem coluna de data**, confirma o achado do Gate 1: é lifetime/cumulativo, não pode ser atribuído a um mês específico. |

**Consequência prática (decisão mantida do Gate 1, agora com evidência adicional):** mesmo com a fórmula de `estimated_margin` agora conhecida, ela (a) depende de uma comissão lifetime sem competência temporal e (b) exclui CMV — continua sendo, na melhor hipótese, uma "contribuição estimada pré-CMV acumulada", nunca uma margem real. **Não foi implementado, não foi exposto na UI, e o nome "margem" não foi usado em nenhum lugar novo.** "Atualizado em" no header do ranking ML usa `MAX(refreshed_at)` do próprio mart `marts.fact_ml_produto_ranking` no Neon (carimbo de quando o pipeline de sync rodou pela última vez) — não do RDS, que não tem esse campo. Essa função (`_ml_refreshed_at`) e a exibição no frontend (`ProductParetoSummary` → `scopeNote`) já existiam antes deste Gate 2 e foram confirmadas corretas, não recriadas.

**Implementado:**

1. **Preço médio nos 3 marketplaces** — `avg_price` (receita/unidades, `NULL` quando denominador é zero):
   - ML: já existia na API (`performance_service.py:1996`), passou a ser exibido na tabela do frontend (`MercadoLivreProductTable.tsx`), coluna "Preço Médio".
   - TikTok: novo — `SUM(gmv)/SUM(items_sold)` adicionado à CTE `agg` de `get_produtos_tiktok`, ao schema `ProdutoTikTokRow` e à tabela do frontend.
   - Shopee: sem alteração (já existia e já era exibido).
2. **Preço médio ponderado no summary (cards)** — novo campo `avg_price_weighted` (receita elegível total / unidades elegíveis totais, **nunca** média simples de `avg_price` por linha) nos 3 endpoints `/summary`, via helper `_weighted_avg_price()`. Exibido como texto no `scopeNote` de cada aba (função pura `avgPriceNote()`, dirigida pela API, mesmo padrão de `zeroGmvNote`).
3. **ROAS/ACOS/Ad Spend no ML** — já existiam na API; passaram a ser exibidos na tabela (coluna "Eficiência Ads": ROAS em destaque + ACOS% e Ad Spend empilhados abaixo). `estimated_margin` **não** foi adicionado à UI nem ao rótulo da coluna.
4. **Nota fixa de margem indisponível** — função pura `marginUnavailableNote(tab)` em `produtos-tab-transition.ts`, exibida no `scopeNote` das 3 abas: para ML explica que ROAS/ACOS reflete eficiência de Ads (não margem); para TikTok/Shopee explica a ausência total de dado de ads/custo por produto.
5. **Lista de meses dinâmica (TikTok/Shopee)** — nova função pura `lastNMonths(count, today)`, substitui a lista hardcoded `AVAILABLE_MONTHS` na aba Produtos (mês atual primeiro, com sufixo "(atual)"); `PeriodSelector` ganhou uma prop opcional `months` (default preserva o comportamento antigo na página de marca, que não foi alterada).
6. **Não implementado (por decisão explícita):** `date_from`/`date_to` livre em Produtos; qualquer rótulo "margem" fora de `estimated_margin` (mantido só como campo técnico, não usado na UI); ROAS/ACOS/margem para TikTok/Shopee (sem fonte).

**Arquivos alterados:**
- Backend: `apps/api/app/schemas/performance.py`, `apps/api/app/services/performance_service.py`.
- Testes backend: `apps/api/tests/test_performance_service_produtos.py` (+11 testes novos: avg_price TikTok normal/zero, avg_price_weighted nos 3 summaries normal/zero, ROAS/ACOS preservados no schema ML, TikTok/Shopee sem margem/ads no schema, nenhum schema usa "margin" fora de `estimated_margin`), `apps/api/tests/test_produtos_determinism.py` (fixture `_tk_row` atualizada).
- Frontend: `apps/web/app/produtos/page.tsx`, `apps/web/src/components/MercadoLivreProductTable.tsx`, `apps/web/src/components/TikTokProductTable.tsx`, `apps/web/src/components/PeriodSelector.tsx`, `apps/web/src/lib/api-client.ts`, `apps/web/src/lib/produtos-tab-transition.ts`.
- Testes frontend: `apps/web/tests/produtos-tab-transition.test.ts` (+7 testes novos: `avgPriceNote`, `marginUnavailableNote`, `lastNMonths` incluindo virada de ano).

**Validação:** `pytest` completo (362 passed), `npm test` (174 passed), `npx tsc --noEmit` (sem erros), `npm run build` (sucesso), `python -m compileall app` (sem erros), smoke read-only dos 6 endpoints de produtos contra o Neon real (ML/TikTok/Shopee, list + summary, com e sem `ref_month`), `git diff --check` limpo, scan de segredos/PII no diff sem resultados.
