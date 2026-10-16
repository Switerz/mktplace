# REGIONAL-SOURCE-RECOVERY-1 — fonte Shopee removida da visão regional

Medido em 16/10/2026, somente leitura no Data Mart (réplica). Nenhuma escrita
no Data Mart ou no Neon, nenhum deploy, nenhum export manual novo.

## Veredito

| Canal | Situação | O que este gate faz |
|---|---|---|
| **ML** | Gold parada em **28/09** por efeito colateral, sem defeito na própria fonte | **Recuperável.** O PR tira a Shopee do caminho crítico; o dry-run mostra 1.629 linhas de grão prontas (29/09–15/10) |
| **Shopee** | Gold parada em **24/08** (fim dos exports). A fonte foi arquivada em 29/09 | **Sem substituto equivalente.** Nenhuma fonte atual tem UF, município ou CEP do destinatário |
| **TikTok** | Sem cobertura regional desde sempre | Nada muda |

A tela não está "parada em 28/09" por um único motivo. São duas paradas
diferentes que aparecem juntas:

1. **Shopee, 24/08.** A Gold regional da Shopee sempre dependeu do export
   manual do Seller Center. O último arquivo cobre pedidos até 24/08 23:59
   (carga em 25/08 16:30). O arquivamento só tornou visível uma parada que já
   existia.
2. **ML, 28/09.** Em 29/09 19:37 UTC, `silver.stg_shopee_order_item_snapshots`
   foi movida para `archive.silver__stg_shopee_order_item_snapshots`
   (`archive._archive_log`, id 149, lote 2026-09-29, `destino_final=arquivar`).
   O `--incremental` consulta a fonte Shopee dentro da mesma transação do ML.
   A partir daí, toda execução sai com exit 4:
   `relation "silver.stg_shopee_order_item_snapshots" does not exist`. Os logs
   do runner registram isso em 03/10, 09/10, 13/10 e 16/10. A última execução
   com sucesso foi em 29/09 11:13.

## 1. Contrato atual da visão regional

Fonte: `pipelines/ingestion/gold_regional/loader.py`, serving
`marts.fact_marketplace_region_daily` (Neon), API
`apps/api/app/services/regioes_service.py`.

- **Grão:** `date × marketplace_id × loja_id × uf`. UF é uma das 27 siglas ou
  `XX`, que significa "não identificada" e nunca é NULL. A chave é UNIQUE.
- **Data:** data de criação do pedido. Para a Shopee é
  `order_created_at::date` (naive, já em BRT). Para o ML é
  `date_created::date`. O teto é D-1 em America/Sao_Paulo.
- **Marcas:** apice=1, barbours=2, kokeshi=3, lescent=4, rituaria=5.
- **Canais:** ML (2) e Shopee (3). TikTok (1) nunca tem linha, e a API o lista
  em `channels_sem_cobertura_regional`.

| Métrica | Shopee (export) | ML |
|---|---|---|
| `gmv` | `order_amount` com status `NOT ILIKE '%cancel%'` | `total_amount` com `status='paid'` |
| `orders` | pedidos não cancelados | pedidos `paid` |
| `units_sold` | `SUM(quantity)` dos não cancelados | sempre 0 (ausência, não zero) |
| `canceled_orders` | status `ILIKE '%cancel%'` | `status='cancelled'` |
| `returned_orders` | `return_refund_status IS NOT NULL` | sempre 0 |
| UF | `delivery_state` (nome) → sigla por mapa fixo | `raw.ml_shipments.receiver_state` sem `BR-` |
| `uf_eligible_orders` | **todos** os pedidos, cancelados incluídos | só os `paid` |
| frete | `buyer_paid`, `estimated` e `reverse` (o do vendedor fica NULL) | `sender_cost` resolvido |

A Shopee é deduplicada pelo `file_id` vencedor por `(brand, order_id)`, com
JOIN de volta para trazer todas as linhas de SKU.

## 2. Fontes atuais procuradas

| Fonte | Endereço? | Casa com pedido Shopee? | Veredito |
|---|---|---|---|
| `raw.shopee_orders.recipient_address` (API, airflow) | 9 chaves (state, city, zipcode, district, town, region, name, phone, full_address) | sim, é o próprio pedido | **100% `****`** nos 289.011 pedidos, nas 4 contas e nos 8 status, inclusive `READY_TO_SHIP` e `PROCESSED`. A máscara vem da plataforma: o repo airflow mediu o mesmo em 28/08 (`helpers.to_txt_desmascarado`) |
| `silver.stg_shopee_orders` / `stg_shopee_order_items` | nenhuma coluna geográfica | — | sem UF |
| `raw.shopee_orders.package_list` | só logística (canal, transportadora, peso) | — | sem UF |
| `raw.shopee_orders.invoice_data.access_key` (NF-e) | só a UF do **emitente** (35/31) | presente em ~85% dos pedidos de set | não traz o destinatário |
| `silver.intelipost_orders` | UF/cidade/CEP | **0** por `order_sn` e **0** por chave de NF-e (01–14/09) | não tem pedido Shopee |
| `gold.fct_gobeaute_prazo_pedidos` | UF/CEP | nenhum `numero_pedido` no formato de `order_sn` | não tem pedido Shopee |
| `raw.tlp_pedidos` | UF/CEP | 0, e só tem Denavita | descartado |
| `gold.tiny_orders_all_brands` | UF/CEP | ERP sem carga desde mai/2026 | descartado |
| `archive.silver__stg_shopee_order_item_snapshots` | `delivery_state`, `delivery_city` | é a fonte antiga | congelada em 24/08, sem atualização possível sem export manual |

## 3. Existe substituto com UF, município ou CEP?

**Não.** Nenhuma tabela do Data Mart liga um pedido Shopee posterior a 24/08 a
um endereço de destino.

## 4. Fonte antiga × candidata (API) em 01/01–24/08

A UF não tem como ser comparada porque a candidata não a possui. Nas métricas
não geográficas, contra a fonte antiga deduplicada:

- **Pedidos válidos** (API `is_sale` × export não cancelado): **exatos** de
  jan a abr nas 4 marcas. Ex.: Barbour's 3.694 / 11.112 / 30.500 / 27.255 e
  Ápice 626 / 1.008 / 2.564 / 5.475. Em mai–jun a diferença vai de 1 a 3
  pedidos. Em jul a diferença maior vem do status congelado no snapshot do
  export (ex.: Ápice 4.312 × 4.341).
- **GMV:** `total_amount` da API fica **0,02% a 11,7% abaixo** do
  `order_amount` do export (Lescent jan −0,02%, Rituaria jan −2,8%, Ápice abr
  −10,0%, Barbour's abr −11,7%). São definições diferentes, não perda. Trocar
  a fonte muda o número de GMV da tela e exige ratificação.
- **Cancelamento:** o silver da API usa status minúsculo (`cancelled`). Um
  filtro `= 'CANCELLED'` devolve zero cancelados e conta todos os pedidos como
  válidos. Barbour's mar: 36.919 = 30.500 + 6.419.
- **Kokeshi:** é a maior marca Shopee da Gold (408.027 pedidos em jan–ago) e
  **não tem conta na API**. Não há app registrado. Qualquer substituto via API
  perde a Kokeshi inteira.

## 5. Qualidade

- **Duplicidade:** 0 chaves duplicadas em `gold.fct_marketplace_region_daily`.
  A API tem 1 linha por `order_sn` nas 4 contas.
- **Endereço ausente:** a fonte antiga tinha 0 pedidos sem UF em jan–ago. Na
  API, 100% estão mascarados.
- **Pedido sem região:** na Shopee antiga, `XX` = 0. No ML, Barbour's tem
  65.609 de 163.942 pedidos em `XX`, um gap conhecido de nov/25–mar/26. A
  janela pendente (29/09–15/10) tem 100% de UF: 36.124 pedidos pagos.
- **Marca sem conta:** Kokeshi na Shopee API.
- **Ausência convertida em zero:** confirmado na API. `refreshed_at` é o
  `MAX(ingested_at)` de todos os canais pedidos, e
  `channels_sem_cobertura_regional` só conhece o TikTok. A Shopee aparece com 0
  em set e out sem aviso. **Depois que este PR rodar, o ML volta a ficar
  fresco e o `refreshed_at` passa a parecer saudável para "todos os canais"
  enquanto a Shopee segue em 0.** Ver o item 7.3.

## 6. O que o PR implementa

O PR não troca a fonte Shopee, porque não há equivalência. Ele faz só isto:

- Antes de ler a fonte Shopee, uma sonda
  `to_regclass('silver.stg_shopee_order_item_snapshots')`. Se a relação não
  existe, a Shopee sai da rodada e vira `source_unavailable`. O diagnose
  imprime `FONTE INDISPONIVEL`, a carga devolve
  `marketplaces_source_unavailable` e a CLI escreve `AVISO` em stderr. Nenhuma
  linha Shopee é inserida.
- O ML continua obrigatório. Ele não tem sonda, e a falta de `raw.ml_orders`
  segue derrubando a carga.
- Os modos exclusivos da Shopee (`--diagnose-shopee-window`, refresh e
  restore) continuam falhando de forma explícita. É o correto: a fonte deles
  não existe.

**Dry-run (`--diagnose`, só leitura, 16/10):**

```
teto superior INCLUSIVO: 2026-10-15
ml: max_date_gold=2026-09-28 max_date_source_eligible=2026-10-15 estimated_new_rows=1629 will_update=True
shopee: FONTE INDISPONIVEL (relacao ausente no banco) — fora da carga; Gold congelada em max_date_gold=2026-08-24
```

Com o código atual de produção, o mesmo comando falha com
`relation "silver.stg_shopee_order_item_snapshots" does not exist`.

## 7. Recuperar a Shopee: o que falta

### 7.1 Campos ausentes

UF, município e CEP do destinatário, por pedido Shopee. Também falta uma
definição de GMV ratificada para a fonte API (`total_amount` ≠
`order_amount`) e a conta Kokeshi.

### 7.2 Endpoint ou fonte necessária, por ordem de custo

1. **Shopee Open API, dados de etiqueta**
   (`v2.logistics.get_shipping_document_data_info`). O endereço do
   destinatário existe na etiqueta de envio. **Hipótese não provada.** Pode vir
   mascarado também, e provavelmente só existe na janela antes do envio, como
   o CPF (185/185 em `PROCESSED`, 0/707 em `COMPLETED`). A prova exige um probe
   pela DAG no repo airflow, disparado pelo Mário, em uma marca.
2. **XML da NF-e no emissor.** O destinatário (UF, município, CEP) é
   obrigatório na NF-e, e ~85% dos pedidos já trazem `access_key`. Falta
   identificar o sistema emissor dos CNPJs SP/MG e ingerir só os campos de
   destino.
3. O export manual do Seller Center está excluído por instrução.

### 7.3 Canais afetados

Só a Shopee, nas 5 marcas. A Kokeshi também fica sem API. O ML é recuperado
por este PR. O TikTok não muda.

### 7.4 Impacto na tela `/regioes`

- O mapa e o ranking por UF ficam sem Shopee depois de 24/08.
- O total "todos os canais" sai subestimado em set e out.
- O GMV Shopee por UF de set/out aparece como 0, sem aviso.
- O `refreshed_at` vai parecer fresco assim que o ML voltar.

### 7.5 Menor implementação para recuperar a visão

1. **Agora (este PR):** fazer o merge e atualizar o runner. O ML volta na
   próxima execução do `full_daily`. Isso é uma ação operacional e não foi
   executada.
2. **Antes ou junto do item 1, um PR pequeno de API.** Cada resposta de
   `/regioes/*` passa a trazer, por canal pedido, a última data carregada
   (`MAX(date)` por `marketplace_id`) e uma lista
   `channels_sem_carga_no_periodo` (Shopee quando `period.end > 24/08`). A tela
   mostra um chip "Shopee sem dado regional desde 24/08" em vez de zero. Sem
   isso, o item 1 piora a leitura da Shopee.
3. **Recuperar a Shopee:** provar o endpoint de 7.2.1. Se ele entregar a UF,
   gravar só `state`, `city` e `zipcode` na visita em `READY_TO_SHIP`, sem
   nome, telefone ou endereço, com preservação contra sobrescrita por máscara.
   Depois, um transform Gold a partir de `silver.stg_shopee_orders` (`is_sale`)
   e o GMV ratificado.

Alternativa descartada: carregar a Shopee API com `uf='XX'`. Os totais por
canal seriam preservados, mas o mapa continuaria vazio, o GMV mudaria de
definição sem ratificação e a `uf_fill_pct` de 0% só trocaria um zero
silencioso por um alerta permanente.
