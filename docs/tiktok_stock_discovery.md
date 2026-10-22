# TIKTOK-STOCK-DISCOVERY: a Torre tem fonte confiável de estoque TikTok Shop?

> STOCK-DAILY-SHOPEE-TIKTOK-1, parte B. **Documento de decisão, sem implementação.** Nada aqui foi aplicado: não há migration, publicador, API nem tela de estoque TikTok. A produtização (seção i) só começa depois da validação no Seller Center e da confirmação com a operação de qual sistema sincroniza o saldo.

**Data da medição:** 21/10/2026, entre 19h25 e 19h50 BRT. Data Mart em réplica hot standby, com `replay_lag` de cerca de 2 min.
**Modo:** somente leitura. Sessões `readonly`, `statement_timeout=60s`, só SELECT. Nenhuma chamada à API do TikTok e nenhuma escrita.
**Código lido:** `goca-se/airflow` em `main` @ `9b97c62`, comparado com `origin/develop`, e `Switerz/mktplace` em `origin/main` @ `2dfe4b9`.

## Veredito: `TIKTOK_STOCK_SOURCE_VALID_WITH_CAVEATS`

**Como fonte, ela é sólida.** O desenho é bom e a captura está íntegra:
- série diária completa;
- grão único;
- raw, silver e gold idênticas;
- 100% dos SKUs vendidos cobertos;
- reservado comprometido coerente com os pedidos em aberto.

**O ponto fraco é o significado do número.** O saldo é o **saldo que o vendedor anuncia no TikTok**. Há evidência forte, mas inferida, de que ele espelha o **estoque físico compartilhado entre canais**, vindo do ERP ou de um hub. Kits aparecem com saldo derivado dos componentes. Por isso:
- **ruptura no TikTok** (SKU ativo com saldo 0) é confiável;
- **cobertura** é válida só com rótulo explícito: "saldo compartilhado ÷ demanda TikTok". Ela superestima os dias reais;
- **estoque parado e capital parado** (`is_dead_stock`, `stock_value`, soma de unidades) **não são confiáveis** e não devem ser publicados.

### Evidências decisivas

| # | Evidência | Número |
|---|---|---|
| 1 | Série sem buraco por marca | 7 marcas × 72/72 dias (11/08 a 21/10), 0 dia faltando |
| 2 | Grão único nas 5 relações | 0 duplicata em raw, raw_wh, silver, silver_wh e gold |
| 3 | Raw = silver = gold | 86.428/86.428 linhas, 0 divergência de quantidade; 330.745.178 unidades acumuladas nas três camadas |
| 4 | Gold reproduz `units_sold_28d` e as fórmulas | 0/25.892 divergências recalculando a partir de `stg_tiktok_analytics_skus`; 0 em `avg_daily_units_28d` e em `days_of_cover` |
| 5 | Cobertura dos SKUs vendidos | 726/726 SKUs com venda válida em 28 d (100%) têm linha de estoque em 20/10; 100% das unidades |
| 6 | Pedidos × gold (5 SKUs) | A média diária da gold fica 0,4% a 3,7% acima da recalculada pelos pedidos |
| 7 | Saldo cai mais que a venda TikTok | Barbours `10041`: −14.112 un em 7 d com 0 reposição, contra 2.077 vendidas no TikTok (6,8×). Kokeshi `40019`: −13.598 contra 3.734 (3,6×) |
| 8 | Kits espelham componente | 4 SKUs Kokeshi com exatamente 62.739 un (1 avulso + 3 kits); 383 SKUs em grupos de quantidade idêntica (≥100 un) em 20/10 |
| 9 | Cobertura implausível | Mediana de `days_of_cover` em 20/10 = **326 dias**; 347 SKUs acima de 365 d; média por marca (ativos SUFFICIENT) entre 459 e 11.162 dias |

---

## a. Tabelas existentes

As linhas da gold são as mesmas da raw (86.428). A diferença está em quem escreve e como: a raw recebe UPSERT da DAG, enquanto a silver e a gold são modelos incrementais do dbt.

| Relação | relkind | Linhas | Período | Natureza |
|---|---|---|---|---|
| `raw.tiktok_shop_inventory` | r | 86.428 | 11/08 a 21/10 (72 dias) | Série diária com `snapshot_date` na chave. O dia corrente é **reescrito** a cada run de 5 min. Dia passado congela no último run do dia. |
| `raw.tiktok_shop_inventory_warehouse` | r | 86.428 | idem | idem, no grão SKU × armazém |
| `silver.stg_tiktok_inventory` | r (dbt incremental) | 86.428 | idem | Limpeza, mais `is_active` e `has_quantity_mismatch` |
| `silver.stg_tiktok_inventory_warehouse` | r (dbt incremental) | 86.428 | idem | idem |
| `gold.fct_tiktok_inventory_daily` | r (dbt incremental, delete+insert, janela de 7 d por marca) | 86.428 | idem | Métricas: sellable, reserved, giro, cobertura, delta, status, dead stock |
| `gold.tiktok_inventory_daily` | **v** | (view) | idem | `SELECT * FROM gold.fct_tiktok_inventory_daily`: view de compatibilidade criada pelo post_hook `view_compat` |

O alias `fct_` existe em `main` e não em `develop`: `dbt/tiktok/models/gold/inventory/tiktok_inventory_daily.sql:1`.

**Colunas da raw de SKU:**
- identificação: `brand, snapshot_date, sku_id, product_id, seller_sku`;
- catálogo: `product_title, product_status, currency, sale_price`;
- saldos: `total_available_quantity, total_committed_quantity, campaign_reserved_quantity, creator_reserved_quantity, in_shop_quantity`;
- distribuição em JSONB: `campaign_inventory, creator_inventory`;
- controle: `warehouse_count, catalog_available_quantity, product_created_at, product_updated_at, fetched_at, updated_at`.

DDL em `src/tiktok/config/constants.py:868-915`. A raw de armazém tem `available_quantity, committed_quantity` por `warehouse_id` (`constants.py:917-938`).

**Pipeline produtor:** DAG `tiktok_shop_inventory_etl` (`dags/tiktok_shop_inventory_etl.py`):
- `schedule="*/5 * * * *"` (`:140`), `max_active_runs=1`, `catchup=False`;
- 1 task por loja via `.expand()` (`:360-362`);
- depois, `dbt_silver` e `dbt_gold` com as tags `inventory,silver` e `inventory,gold` (`:364-384`).

**Endpoints chamados** (`src/tiktok/config/settings.py:27-28`; `extract.py:8-18`):
1. `POST /product/202309/products/search`, com body `{"status":"ALL"}` (`extract.py:163`). Diz quais SKUs existem e traz título, status e preço. Página de 100 (`constants.py:833`).
2. `POST /product/202309/inventory/search`, com body `{"product_ids":[≤100]}` (`extract.py:303-308`). Traz as quantidades. Não tem paginação.

**O que é lido e mapeado** (`extract.py:419-451`):
- `skus[].total_available_quantity`;
- `skus[].total_committed_quantity`;
- `skus[].total_available_inventory_distribution.{campaign_inventory[], creator_inventory[], in_shop_inventory}` (`extract.py:330-371`);
- `skus[].warehouse_inventory[].{warehouse_id, available_quantity, committed_quantity}`;
- do catálogo, `skus[].inventory[].quantity` somado em `catalog_available_quantity` (`extract.py:105-125`).

As allowlists de gravação estão em `load.py:20-36`.

**O que é descartado:**
- nome do armazém: exigiria o escopo `seller.logistics` (`stg_tiktok_inventory_warehouse.sql:24-28`);
- quebra campanha/creator por armazém;
- qualquer histórico. A API só dá o saldo atual, não há backfill (`dags/...:18-24`).

**Loja excluída:** `azbuy` está em `_LOJAS_IGNORADAS` porque falta o escopo Product, erro 105005 (`dags/...:134`). Não afeta as marcas da Torre.

**Consumo no mktplace** (grep no repositório):
- `pipelines/channel_offer_sync.py:695-715` lê `silver.stg_tiktok_inventory` **só para preço, status e título** (PMA/ofertas). Nunca usa quantidade.
- `pipelines/ops/preflight.py:722` lista a mesma relação como fonte do PMA TikTok.
- `gold.tiktok_inventory_daily` aparece **apenas em comentários**, como origem dos parâmetros 28 d e 7 d reutilizados por Shopee e ML: `pipelines/sync_shopee_fbs_stock_daily.py:128,132` e `pipelines/transforms/ml_full_stock.py:29`.
- **Nenhum mart, API ou tela da Torre consome estoque TikTok hoje.**

## b. Significado de cada quantidade

As definições abaixo vêm dos comentários do código, que citam a documentação da API. **Não reconfirmei na documentação oficial do TikTok.**

| Campo | Significado | Evidência |
|---|---|---|
| `total_available_quantity` | Disponível para venda no SKU, somado em todos os armazéns. Exclui o comprometido. | `extract.py:13-18`; o código o trata como "fonte de verdade das quantidades" |
| `total_committed_quantity` | Unidades já reservadas por pedido de cliente ainda não expedido. Não vendáveis. | `extract.py:15-16`. Medido em 21/10: comprometido 2.018 / 1.737 / 4.941 / 2.099 / 301 contra 1.526 / 1.155 / 3.387 / 1.560 / 131 pedidos em `AWAITING_SHIPMENT/COLLECTION/ON_HOLD` (apice, barbours, kokeshi, lescent, rituaria): 1,3 a 2,3 un por pedido, coerente. O sentido "reservado por pedido" é **inferência coerente**, não prova. |
| `campaign_reserved_quantity` / `creator_reserved_quantity` | Parte do disponível alocada a campanha ou creator. Continua vendável, mas pelo canal reservado. | `extract.py:330-371`; DDL `constants.py:889-893` |
| `in_shop_quantity` | Disponível na vitrine depois das reservas | Identidade `campaign + creator + in_shop = total_available` **vale em 86.423/86.428 linhas (99,994%)**. As 5 exceções são de setembro, com diferença de 1 a 2 un. |
| `warehouse_inventory[].available/committed` | Mesmas grandezas por armazém | `Σ armazém = total do SKU` em 86.428/86.428 linhas, com 0 divergência e 0 órfão |
| `catalog_available_quantity` | Mesma grandeza pela rota `/products/search` | Divergência com `/inventory/search` de 0 a 8 SKUs por dia em cerca de 1.230, máximo de 5 un (20 e 21/10). É venda entre as duas chamadas, não captura parcial. |
| gold `sellable_quantity` | `coalesce(in_shop, total_available)` (`tiktok_inventory_daily.sql:188`) | 0 divergência contra a silver |
| gold `reserved_quantity` | `campaign + creator`. **Não inclui o comprometido.** (`:190-191`) | 0 divergência. **O nome engana:** "reserved" na gold é reserva de canal, não reserva de pedido. |

**Existe quantidade vendável real?** Existe: `total_available_quantity` (vendável em todos os canais do TikTok) ou `in_shop_quantity` (vitrine). Mas **é o saldo que o vendedor declara ao TikTok**, não uma contagem física feita pelo TikTok, porque não há FBT no BR. A pergunta "isso é o estoque físico do vendedor?" fica respondida na seção h.

## c. Grão e unicidade

**Grão:** (`brand`, `snapshot_date`, `sku_id`) e, para armazém, mais `warehouse_id`. `brand` é a loja (`conta_externa`). Testei:

```sql
SELECT count(*) - count(distinct (brand, snapshot_date, sku_id)) FROM <rel>;
```

Resultado: 0 em `raw`, `silver` e `gold`; 0 em `raw_wh` e `silver_wh` (com `warehouse_id`). Os testes dbt `unique_combination_of_columns` também existem (`silver/inventory/_models.yml:20-22`, `gold/inventory/_models.yml:9-12`).

- **Armazém:** exatamente **1 por marca** em toda a série. Os 7 `warehouse_id` são distintos, um por loja, e não existe linha `'unknown'`. É depósito do vendedor, o que confirma a hipótese anterior.
- **`sku_id` entre marcas:** 0 colisões.
- **`seller_sku` não é chave:** em 20/10 havia 100 `seller_sku` repetidos em mais de um `sku_id` (229 linhas). Exemplos: `Kit135` em 2 SKUs (um FREEZE e um ACTIVATE) e `40092` em 2. A chave correta é o `sku_id`, e o `seller_sku` serve só para ponte com o cadastro interno.

## d. Frequência, watermark e atraso

- **Cron:** `*/5`. Desde 25/08 há de 87 a 132 minutos distintos de escrita por dia. **Até 24/08 a DAG rodava 1×/dia**: entre 12/08 e 24/08 cada marca tem 1 só minuto de escrita.
- **Quebra semântica na série:** até 24/08 a foto era do **início do dia** (cerca de 03h BRT). Desde 25/08 é o **fim do dia**: a última mudança fica em mediana a 23,93 h depois da meia-noite BRT, ou seja, cerca de 23h55. Delta e comparação dia a dia atravessando 24→25/08 misturam as duas âncoras.
- **`snapshot_date`** = data BRT do relógio do run (`dags/...:285`).
- **`fetched_at` e `updated_at`** = `datetime.now()` ingênuo no container, que está em **UTC**. Medido: `max(fetched_at)` = 22:20 com o relógio em 22:25 UTC.
- **Atraso típico:** cerca de 5 min. Em 21/10 às 19h25 BRT, a última escrita era de 19h20 BRT.
- **Ressalva de frescor:** `bulk_upsert` tem `guard=True` (`src/tiktok/utils/db.py:105-150`). Linha sem mudança **não é regravada**, nem `fetched_at`. Assim, `max(updated_at)` mede a **última mudança**, não o último run bem-sucedido.
  - Marca de baixo giro parece "parada" estando saudável. Exemplo: Denavita em 15/10 tem 1 só escrita, às 00h01 BRT.
  - O que **é** observável: a primeira foto de cada dia sempre insere (chave nova), então `min(fetched_at)` de (marca, D) prova pelo menos um run bem-sucedido em D.
- **Buracos:** 0. Todas as 7 marcas têm 72/72 dias, e dentro da série de cada SKU também não há buraco (0 gaps por `lead()`).
- **Watermarks:**
  - silver: `updated_at > max(updated_at)` (`stg_tiktok_inventory.sql:28-31`);
  - gold: `max(snapshot_date)` **por marca** com janela de 7 d (`tiktok_inventory_daily.sql:38-41`).
- **Analytics** (`stg_tiktok_analytics_skus`): `max(date)` = 20/10 para todas as marcas (D−1).

## e. Cobertura por marca

Foto de 20/10 (dia fechado). Venda em `[22/09, 20/10)`: pedidos `silver.stg_tiktok_orders.created_at` (BRT ingênuo; `max(created_at)` = 19h25 com o relógio BRT em 19h29) sem `CANCELLED`/`UNPAID`, mais `silver.stg_tiktok_line_items`.

| Marca | SKUs com estoque | Produtos | SKUs ACTIVATE (21/10) | SKUs com saldo > 0 | SKUs vendidos 28 d | … com linha de estoque | Unid. (pedidos) cobertas | SKUs analytics 28 d com linha |
|---|---|---|---|---|---|---|---|---|
| apice | 248 | 240 | 167 | 137 | 159 | 159 (100%) | 42.000/42.000 | 160/160 |
| barbours | 328 | 325 | 163 | 190 | 159 | 159 (100%) | 36.299/36.299 | 160/160 |
| denavita | 83 | 78 | 47 | 74 | 31 | 31 (100%) | 548/548 | 28/28 |
| gocase | 140 | 42 | 132 | 76 | 107 | 107 (100%) | 21.191/21.191 | 107/107 |
| kokeshi | 209 | 197 | 145 | 126 | 126 | 126 (100%) | 92.292/92.292 | 126/126 |
| lescent | 118 | 93 | 85 | 104 | 71 | 71 (100%) | 30.710/30.710 | 70/70 |
| rituaria | 126 | 120 | 92 | 118 | 73 | 73 (100%) | 13.033/13.033 | 70/70 |

- **Catálogo:** `gold.map_tiktok_product_catalog`, com `last_seen` em 19/10. Bate 100% em produto: 934/934 `external_id` estão na foto de estoque de 20/10.
- **Kokeshi tem dado no TikTok.** O bloqueio de Kokeshi da Shopee (memória) não se aplica aqui.
- **Universo de status em 21/10:** 831 SKUs ACTIVATE de 1.253. Os demais (DELETED, FREEZE, SELLER_/PLATFORM_DEACTIVATED, DRAFT, FAILED) entram de propósito, porque a coleta usa `status=ALL` (`extract.py:40-44`).

## f. Reservado, indisponível, kit e ausência

- **NULL e 0:** a regra "NULL ≠ 0" está implementada:
  - `_int_or_none` em `extract.py:77-87`;
  - DDL sem DEFAULT 0;
  - silver sem `coalesce`.
  - Medido em toda a série: **0 NULL** em `total_available`, `total_committed` e `in_shop`. Os únicos NULL são 72 linhas de `product_status`/`catalog_available_quantity` de 1 SKU órfão da Rituaria (`1731951523265414200`, saldo 0, fora do catálogo).
- **SKU sem linha = desconhecido, não zero.**
  - SKU que está no catálogo mas não volta do `/inventory/search` não gera linha (`extract.py:389-392`).
  - SKU que sai do catálogo some da série. Medido: 9 SKUs sumiram, todos DELETED ou DRAFT (6 da Rituaria e 2 da Apice em 14/10, 1 da Gocase em 16/09).
  - O UPSERT **não apaga**: um SKU que some no meio do dia mantém a linha do run anterior daquele dia.
- **Reservado de pedido:** em `total_committed_quantity`, já fora do disponível.
- **Reservado de canal:** em `campaign_/creator_reserved_quantity`, dentro do disponível.
- **Indisponível:** não existe campo de indisponível, avariado ou em trânsito no `/inventory/search`. Não há equivalente ao `nao_disponivel`/`transfer` do ML.
- **Kit, combo e bundle:** a API **não marca kit**. Em 20/10, 599 SKUs parecem kit pelo `seller_sku`/título (kit/dupla/trio/combo), com 2,48 M das 4,28 M unidades.
  - Kits repetem exatamente a quantidade do componente (evidência 8). É **inferência forte** de que o saldo do kit é **calculado do componente** pelo ERP/hub.
  - Consequência: **somar unidades entre SKUs conta o mesmo físico várias vezes.**
  - A ponte para `is_kit` existe no mktplace (`gold.map_produto_codigo_gobeauty` / `dim_produto_gobeauty`, usados em `channel_offer_sync.py:718-735`), mas a memória registra nome errado em kits na `dim_produto`.

## g. Reconciliação de sanidade

- **raw → silver:** 86.428/86.428 com 0 divergência nos 7 campos (quantidades, status, preço). A camada de armazém também tem 0 divergência.
- **silver → gold:** 86.428/86.428 com 0 divergência em available, committed, sellable e reserved. A soma por marca em 20/10 é idêntica nas 3 camadas (ex.: Barbours 1.751.590; Kokeshi 1.321.906).
- **`units_sold_28d` da gold contra recálculo pela silver de analytics:** 0 divergência em 15.961 linhas de outubro fora da janela de reprocessamento e 0 em 9.931 dentro dela.
- **`avg_daily_units_28d` e `days_of_cover`:** 0 divergência contra a fórmula. Esses SKUs estão no topo de vendas, então o desvio pequeno não necessariamente vale para a cauda.

**Gold contra pedidos, 5 SKUs, 20/10:**

| SKU | Gold un. 28 d | Pedidos un. válidas | Δ | Cobertura gold | Cobertura por pedidos |
|---|---|---|---|---|---|
| kokeshi `40019` | 16.154 | 16.084 | +0,4% | 133,1 d | 133,7 d |
| kokeshi `KS03043` | 17.694 | 17.061 | +3,7% | 119,5 d | 123,9 d |
| kokeshi `40098` | 14.393 | 14.119 | +1,9% | 146,9 d | 149,7 d |
| rituaria `MLPREBIOTICA` | 4.693 | 4.612 | +1,8% | 113,2 d | 115,2 d |
| lescent `V10019` | 4.534 | 4.479 | +1,2% | 129,8 d | 131,4 d |

A gold usa **analytics** (`stg_tiktok_analytics_skus.units_sold`) e não pedidos (`tiktok_inventory_daily.sql:72-86`). A diferença sistemática para cima é esperada: `raw.tiktok_shop_line_items` tem `UNIQUE (order_id, sku_id)`, então duas unidades do mesmo SKU no mesmo pedido viram 1 linha, e a contagem por pedidos **subconta**.

**Inconsistência menor no código da gold:** o comentário em `tiktok_inventory_daily.sql:70-71` ("a foto é o saldo do início do dia") ficou falso desde a troca para `*/5` em 25/08. Hoje a foto é do fim do dia, então a venda de D já saiu do saldo mas não entra na janela `[D−28, D)`. O viés é pequeno, cerca de 1/28 da demanda.

## h. Dá para calcular ruptura, cobertura e estoque parado?

**O que a evidência mostra sobre o significado do saldo:**
- **O saldo cai bem mais que a venda TikTok.** Entre 12/10 e 19/10, sem nenhum dia de reposição:

  | SKU | Queda do saldo (7 d) | Venda TikTok (7 d) | Razão |
  |---|---|---|---|
  | barbours `10041` | 14.112 | 2.077 | 6,8× |
  | barbours `BB02083` | 2.927 | 1.548 | 1,9× |
  | rituaria `MLPREBIOTICA` | 2.304 | 1.146 | 2,0× |
  | lescent `V10019` | 2.442 | 1.419 | 1,7× |

  O saldo é consumido também por **outros canais**.
- **Kits com a mesma quantidade do componente** (seção f).
- **A ordem de grandeza bate com o Bling.** O saldo TikTok tem a mesma magnitude do saldo total do Bling da marca: Barbours `10041` está em 144.957 no TikTok contra 219.405 no Bling.
  - Ressalva forte: `raw.bling_estoque_saldos_*` está **parada desde 13 a 18/09**, por isso não serve como conciliação, só como ordem de grandeza.
  - A igualdade exata com o Bling de setembro é baixa (Barbours 90/319, Apice 50/243). Isso é esperado, dada a defasagem.
- **Conclusão:** é **inferência forte, não provada**, que o TikTok recebe o estoque físico compartilhado do vendedor, sincronizado por ERP ou hub. Falta confirmar com a operação qual sistema empurra o saldo.

**Ruptura: confiável no nível do anúncio.** `total_available_quantity = 0` em SKU ACTIVATE significa que o comprador não consegue comprar no TikTok. Em 20/10:

| Marca | Ativos com saldo 0 | … com venda em 28 d | Unidades vendidas em 28 d por esses SKUs |
|---|---|---|---|
| apice | 61 | 41 | 10.460 |
| barbours | 37 | 20 | 2.255 |
| gocase | 66 | 41 | 6.041 |
| kokeshi | 42 | 16 | 4.569 |
| lescent | 8 | 1 | 13 |
| rituaria | 4 | 0 | — |
| denavita | 3 | 2 | 4 |
| **Total** | | **121 candidatos** | |

Ressalva: o 0 pode ser decisão do vendedor (zerou o saldo de propósito) e não falta física.

**Cobertura: só com rótulo explícito.** O numerador é o saldo compartilhado e o denominador é a demanda **só do TikTok**, então a cobertura fica superestimada. A mediana é 326 d e a média de ativos SUFFICIENT vai de 459 a 11.162 d por marca. Como sinal de "baixo" (< 7 d) funciona: há 13 SKUs LOW e, se o saldo inteiro cobre menos de 7 dias só de TikTok, o risco é real. Já "excesso" e "dias até acabar" **não são fisicamente verdadeiros**. A cobertura real exige a demanda de todos os canais por produto interno, fora do escopo desta fonte.

**Estoque parado e capital parado: não publicar.**
- `is_dead_stock` (236 SKUs em 20/10) significa só "sem venda no TikTok". O SKU pode estar girando em ML e Shopee.
- Anúncios inativos com saldo somam **888.737 un**. É saldo de ERP espelhado, não capital parado no TikTok.
- `stock_value = saldo × preço` soma kits e componentes em dobro.

**Outras ressalvas:**
1. Quebra de âncora da foto em 25/08, de início para fim do dia.
2. O frescor por marca não é observável dentro do dia, por causa do guard do UPSERT.
3. `seller_sku` não é chave.
4. `azbuy` está fora.
5. O limite de 7 d é herdado e não foi ratificado. O de 90 d não tem origem documentada (já registrado na Shopee e no ML).

## i. Esboço da produtização (sem implementar)

Segue o padrão Shopee FBS e ML Full. O nome **não** leva "Full", porque não existe FBT no BR.

**Pré-requisito antes do apply:** validação manual no Seller Center, como foi feito na Shopee em 24/09. São 3 SKUs avulsos e 1 kit, comparando "Estoque disponível" na tela com `total_available_quantity`. Também é preciso confirmar com a operação qual sistema sincroniza o estoque e se ele é compartilhado.

### Mart (Neon, migration nova, aplicação como gate próprio)

**`marts.fact_tiktok_stock_daily`**
- PK: (`ref_date`, `brand`, `sku_id`).
- Colunas:
  - `product_id`, `seller_sku`, `product_status`, `is_active`;
  - `internal_is_kit` (ponte por `seller_sku` com o cadastro interno; NULL quando ambíguo);
  - `read_status ∈ {OK, SEM_LEITURA}`;
  - `disponivel`, `comprometido`, `vitrine`, `reservado_campanha`, `reservado_creator`;
  - `distribuicao_coerente` (bool, **sem CHECK bloqueante**, lição do ML: uma linha incoerente não derruba o dia);
  - `divergencia_catalogo` (bool);
  - `unidades_28d`, `analytics_dias_28d`, `media_diaria_28d`;
  - `cobertura_torre_dias` (NULL sem demanda);
  - `classificacao_torre ∈ {RUPTURA_CANDIDATA, BAIXO_CANDIDATO, SUFICIENTE, SEM_GIRO_CANDIDATO, SEM_DEMANDA_MEDIDA, INATIVO, SEM_LEITURA}`;
  - `escopo_saldo = 'SALDO_ANUNCIADO_COMPARTILHADO'` (constante, para nenhum consumidor ler como físico dedicado);
  - `foto_capturada_em` (= `max(fetched_at)` da marca/dia convertido de UTC), `source_run_id`, `published_at`.
- **Sem `EXCESSO_CANDIDATO`** enquanto o saldo for compartilhado.
- CHECKs:
  - quantidades ≥ 0 e `<> 'NaN'`;
  - `read_status='OK'` ⇔ quantidades NOT NULL;
  - cobertura exige `unidades_28d > 0`;
  - kit nunca é somado em unidades da marca.

**`marts.fact_tiktok_stock_brand_daily`**
- PK: (`ref_date`, `brand`).
- Contagens: SKUs, ativos, ativos sem saldo, ruptura com demanda, baixo, sem giro.
- `unidades_28d`, `analytics_dias_28d`, `skus_vendidos_28d_sem_linha` (guarda de cobertura; hoje 0).
- **Sem soma de unidades em estoque.**

### Publicador diário (`pipelines/sync_tiktok_stock_daily.py`, dry-run por padrão, `--apply`)

**Fonte:**
- `silver.stg_tiktok_inventory` na `ref_date = D−1 BRT`;
- `silver.stg_tiktok_analytics_skus` para a demanda;
- `gold.fct_tiktok_inventory_daily` só como **discriminador de reconciliação**. Os números da Torre são recalculados localmente, com classificação própria, como na Shopee e no ML.

**Gates** (falha → marca vai para `SEM_LEITURA` ou bloqueia, nunca vira zero):
1. **Foto de D−1 fechada por marca:** existe `min(fetched_at)` em (marca, D). A primeira foto do dia seguinte sempre insere, o que prova que a DAG estava viva na virada. `max(updated_at)` não serve como gate, por causa do guard.
2. **Contagem de SKUs de D−1** dentro de ±10% de D−2 por marca.
3. **Analytics:** `max(date) ≥ D−1` e `analytics_dias_28d ≥ 21`. Abaixo disso, demanda NULL e `SEM_DEMANDA_MEDIDA`.
4. **Marcas esperadas:** apice, barbours, kokeshi, lescent, rituaria; denavita e gocase opcionais. A lista é explícita, e marca ausente vira `sem_fotografia_no_dia`.
5. **Réplica:** checar `replay_lag` e comparar pela mesma réplica (memória "réplica atrasada = NO_OP falso").

**Escrita:**
- `pg_try_advisory_xact_lock(<chave nova>)`;
- DELETE + INSERT por `ref_date` numa transação;
- `audit.source_sync_run` com `source='tiktok_stock_daily'`;
- `sanitizar()` do erro (DSN e PII).

**Agendamento:** depois das 00h10 BRT, para a foto de D existir. Como a DAG roda a cada 5 min, a janela é folgada.

### API

`GET /performance/tiktok-estoque`, no mesmo molde de `/shopee-fbs-estoque` (`apps/api/app/routers/performance.py:959`):
- feature flag;
- filtros `marca[]` e `classificacao[]`, `limite`;
- payload com `ref_date`, `foto_capturada_em`, `limites_provisorios` e `escopo_saldo`;
- erros tipados `FatoInexistente` / `SemFotografiaPublicada`, como em `shopee_fbs_stock_service.py:395-446`.

### Tela

Bloco "Estoque TikTok (saldo anunciado)":
- **KPIs:** ativos sem saldo com demanda (ruptura), baixo < 7 d (provisório), sem giro.
- **Tabela por SKU** com saldo, comprometido, vitrine, média diária e cobertura rotulada.
- **Frescor visível** e estados de vazio, erro e "sem fotografia".
- **Sem R$ parado e sem soma de unidades.**
- Aviso fixo: "saldo compartilhado com outros canais; cobertura considera só a demanda do TikTok".
